"""
CanFdLibrary.py — Robot Framework CAN FD 高速总线关键字库
=========================================================

把"被仿真的 CAN FD 总线"（simulators/canfd_sim.py，独立进程）包装成
Robot Framework 关键字，测试用例里就能像说话一样验证 CAN FD：

    Start CanFD Simulation
    ${marker}=    Set FD Signal    speed_kmh=123.456
    ${frame}=     Wait For FD Frame    0x400    since_seq=${marker}
    ${signals}=   Decode FD Signals    ${frame}
    BRS Flag Should Be    ${frame}    True

架构
----
    simulators/canfd_sim.py（ADAS 域控节点 = 被测对象）
        UDP 组播 233.255.0.3:30003  ──▶  帧流（JSON 行：id/data_hex/dlc/is_fd/brs/esi...）
                                    ◀──  测试端发上总线的 FD 帧（origin=tester）
        TCP 127.0.0.1:30004          ──▶  控制命令（设置信号/注入错误/静默/统计）
    每组帧都带 seq（全局帧序号）与 counter（同 ID 发送计数器），测试端用它们
    做"只等操作之后的新帧"和"丢帧检测"——这是消除 flaky 和验证序列完整性的关键。

为什么不复用经典 CAN 的 VirtualCanBus？
    CAN FD 的 DLC 9~15、BRS、ESI、64 字节数据段在 python-can 的经典 CAN
    模型里表达不了；本模块用原始 UDP socket（标准库）直接做组播，节点与
    测试端都能双向收发，语义上更接近"一条 FD 总线上的两个节点"。

CAN FD 的 DLC → 长度映射（本库独立实现一份，逐项可查）
-------------------------------------------------------
    DLC : 0 1 2 3 4 5 6 7 8 9  10 11 12 13 14 15
    字节: 0 1 2 3 4 5 6 7 8 12 16 20 24 32 48 64
`DLC Should Map To Length` 关键字就是把这张表暴露给测试用例（用例②逐项验证）。

解码为什么手工写？
    真实项目用 DBC/LDF 解析库（cantools、CANoe）自动解码；教学项目为了
    不新增依赖，用 struct 按 simulators/dbc/vehicle_fd.dbc 的布局手写解码。
    布局与 DBC 一一对应：float32 用 `<f`、uint16 用 `<H`、灯位图按 bit 取。
    注意：DBC 里的 float 信号用 `SIG_VALTYPE_ <id> <signal> : 1;` 声明，
    DBC 本身只是文本描述，真正的解码逻辑必须在工具/库中实现。

端口约定（与仿真器读同一组环境变量）
------------------------------------
    CANFD_BUS_PORT   默认 30003（UDP 组播帧流；经典 CAN 用 30000，严格错开）
    CANFD_CTRL_PORT  默认 30004（TCP 控制口）
    CANFD_BUS_GROUP  默认 233.255.0.3

本库是 SUITE scope：每个套件一个实例，Suite Setup 启动仿真器、Teardown 回收。
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from robot.api.deco import keyword
from robot.api.logger import console, info

# ---------------------------------------------------------------------------
# 路径 / 端口 / 协议常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SIM_SCRIPT = ROOT / "simulators" / "canfd_sim.py"
DBC_PATH = ROOT / "simulators" / "dbc" / "vehicle_fd.dbc"
SIM_LOG = ROOT / "results" / "canfd_sim_stdout.log"

DEFAULT_BUS_GROUP = "233.255.0.3"
DEFAULT_BUS_PORT = 30003
DEFAULT_CTRL_PORT = 30004
CLASSIC_CAN_PORT = 30000                 # 经典 CAN 虚拟总线端口（用于端口隔离断言）

# CAN FD DLC → 数据长度映射（独立实现，和仿真器互相交叉校验）
DLC_TO_LENGTH: Dict[int, int] = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
    9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
}
LENGTH_TO_DLC: Dict[int, int] = {length: dlc for dlc, length in DLC_TO_LENGTH.items()}
LEGAL_FD_LENGTHS = ", ".join(str(item) for item in sorted(LENGTH_TO_DLC))

# 帧 ID：ADAS 域控周期帧 + 测试端控制帧
ID_VEHICLE_STATUS = 0x400
ID_OBJECT_LIST = 0x401
ID_CONTROL_COMMAND = 0x402

# 12 个警告灯（顺序与 vehicle_fd.dbc / 经典 CAN 0x300 一致）
TELLTALE_NAMES = [
    "MilWarning", "OilPressureLow", "CoolantTempHigh", "BatteryLow",
    "AbsFault", "AirbagFault", "SeatbeltWarning", "TurnLeft",
    "TurnRight", "HighBeam", "LowBeam", "ParkBrake",
]


# ---------------------------------------------------------------------------
# 小工具：字节/布尔/ID 解析
# ---------------------------------------------------------------------------
def _to_bool(value: Any, name: str = "flag") -> bool:
    """把 RF 传来的布尔写法规范成 bool（支持 True/False/1/0/yes/no）。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise AssertionError(f"[CANFD] {name} 应为布尔值（True/False），收到 {value!r}")


def _to_bytes(data: Any) -> bytes:
    """把 RF 传来的数据规范成 bytes：支持 'AABB' / 'AA BB' / ['AA','BB'] / [1,2]。"""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, (list, tuple)):
        values = []
        for item in data:
            if isinstance(item, str):
                values.append(int(item, 16))
            else:
                values.append(int(item))
        return bytes(value & 0xFF for value in values)
    text = str(data).replace(",", " ").replace("0x", " ").strip()
    tokens = [token for token in text.split() if token]
    if not tokens:
        return b""
    if len(tokens) == 1:
        chunk = tokens[0]
        if len(chunk) % 2 != 0:
            raise AssertionError(f"[CANFD] 十六进制字符串长度必须为偶数：{data!r}")
        return bytes.fromhex(chunk)
    return bytes(int(token, 16) for token in tokens)


def _to_int(value: Any, name: str = "value") -> int:
    """解析整数（支持 '0x10' / 16 / '16'）。"""
    try:
        if isinstance(value, int):
            return value
        return int(str(value).strip(), 0)
    except ValueError as exc:
        raise AssertionError(f"[CANFD] 无法把 {name}={value!r} 解析成整数") from exc


class CanFdLibrary:
    """CAN FD 总线操作关键字集合（组播收帧 + 控制口 + DBC 手工解码）。"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(
        self,
        bus_port: Optional[int] = None,
        ctrl_port: Optional[int] = None,
        group: Optional[str] = None,
        startup_timeout: float = 10.0,
    ) -> None:
        """端口优先级：显式参数 > 环境变量 > 默认值。

        换端口跑（避免与他人抢 30003/30004）::

            set CANFD_BUS_PORT=31003
            set CANFD_CTRL_PORT=31004
            robot tests/robot/12_canfd.robot
        """
        self._bus_port = int(bus_port or os.environ.get("CANFD_BUS_PORT", DEFAULT_BUS_PORT))
        self._ctrl_port = int(ctrl_port or os.environ.get("CANFD_CTRL_PORT", DEFAULT_CTRL_PORT))
        self._bus_group = str(group or os.environ.get("CANFD_BUS_GROUP", DEFAULT_BUS_GROUP))
        self._startup_timeout = float(startup_timeout)
        if not DBC_PATH.exists():
            raise AssertionError(f"[CANFD] 找不到 DBC 文件：{DBC_PATH}")

        # 子进程 / socket / 接收线程
        self._proc: Optional[subprocess.Popen] = None
        self._log_file: Optional[Any] = None
        self._sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()

        # 接收缓存
        self._pending: Deque[Dict[str, Any]] = deque()
        self._history: Deque[Dict[str, Any]] = deque(maxlen=20000)
        self._seen_seq: Deque[int] = deque()
        self._seen_set: set = set()
        self._last_seq = 0
        self._tester_seq = 0

        info(f"[CanFdLibrary] CAN FD 端口约定：组播 {self._bus_group}:{self._bus_port}，"
             f"控制 {self._ctrl_port}（经典 CAN 端口 {CLASSIC_CAN_PORT} 不占用）")

    # ==================================================================
    # 生命周期
    # ==================================================================
    @keyword("Start CanFD Simulation")
    def start_canfd_simulation(self, timeout: float = 10.0) -> str:
        """启动 CAN FD 仿真器子进程并等待控制口就绪（幂等/可复用）。

        子进程用当前 Python 解释器（sys.executable）启动 simulators/canfd_sim.py，
        实现"懒启动 + 套件级复用"：重复调用不会重复起进程；进程异常时抛
        AssertionError 并附上子进程日志末尾（端口占用/脚本报错一目了然）。
        """
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._command({"cmd": "ping"}, timeout=1.5)
                info("[CanFdLibrary] CAN FD 仿真器已在运行，复用当前进程")
                return "reused"
            except AssertionError:
                self._cleanup_process()

        self._start_receiver()
        self._spawn_simulator()
        self._wait_until_ready(float(timeout))
        self._command({"cmd": "silence", "enabled": False}, timeout=3.0)
        self.clear_fd_frame_buffer()
        console(f"[CanFdLibrary] CAN FD 仿真器就绪：组播 {self._bus_group}:{self._bus_port} / "
                f"控制 {self._ctrl_port}，日志 {SIM_LOG.name}")
        return "started"

    @keyword("Stop CanFD Simulation")
    def stop_canfd_simulation(self) -> None:
        """停止仿真器（Suite Teardown 调用；幂等，从未启动也安全）。

        回收顺序：quit → wait(5s) → terminate → wait(3s) → kill 兜底 →
        关组播 socket、停接收线程。顺序错了会留孤儿进程占着 30003/30004。
        """
        if (self._proc is None and self._sock is None
                and self._rx_thread is None):
            return
        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    self._command({"cmd": "quit"}, timeout=2.0)
            except AssertionError:
                pass
            try:
                self._proc.wait(timeout=5.0)
                info("[CanFdLibrary] CAN FD 仿真器已优雅退出")
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        info("[CanFdLibrary] ⚠ CAN FD 仿真器未能回收（kill 后仍超时）")
                info("[CanFdLibrary] CAN FD 仿真器被强制回收（terminate/kill）")
        self._cleanup_process()
        self._stop_receiver()
        console("[CanFdLibrary] CAN FD 仿真器已停止")

    def _ensure_started(self) -> None:
        if (self._proc is None or self._proc.poll() is not None
                or not self._running):
            self.start_canfd_simulation()

    def _spawn_simulator(self) -> None:
        if not SIM_SCRIPT.exists():
            raise AssertionError(f"[CANFD] 找不到仿真器脚本：{SIM_SCRIPT}")
        SIM_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(SIM_LOG, "wb")
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        cmd = [
            sys.executable, "-X", "utf8", str(SIM_SCRIPT),
            "--bus-port", str(self._bus_port),
            "--ctrl-port", str(self._ctrl_port),
            "--group", self._bus_group,
        ]
        info(f"[CanFdLibrary] 启动 CAN FD 仿真器：{' '.join(cmd)}")
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=self._log_file, stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            raise AssertionError(f"[CANFD] 启动仿真器失败：{exc}") from exc

    def _wait_until_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                # 进程退出：可能是控制口被"残留的旧仿真器"占用 → 尝试复用它
                try:
                    self._command({"cmd": "ping"}, timeout=1.0)
                    info("[CanFdLibrary] 检测到已有 CAN FD 仿真器实例，复用")
                    return
                except AssertionError:
                    raise AssertionError(
                        f"[CANFD] CAN FD 仿真器启动失败（退出码 {self._proc.returncode}）。\n"
                        f"子进程日志（{SIM_LOG}）末尾：\n{self._read_log_tail()}\n"
                        f"常见原因：控制端口 {self._ctrl_port} 被占用（残留进程）→ "
                        f"netstat -ano | findstr :{self._ctrl_port} 排查，"
                        f"或设置环境变量 CANFD_BUS_PORT/CANFD_CTRL_PORT 换端口。"
                    ) from None
            try:
                self._command({"cmd": "ping"}, timeout=1.5)
                return
            except AssertionError as exc:
                last_error = str(exc)
                time.sleep(0.05)
        raise AssertionError(
            f"[CANFD] {timeout}s 内仿真器控制端口未就绪（127.0.0.1:{self._ctrl_port}）。\n"
            f"最后一次错误：{last_error}\n子进程日志末尾：\n{self._read_log_tail()}")

    def _read_log_tail(self, lines: int = 15) -> str:
        try:
            if self._log_file is not None:
                self._log_file.flush()
            text = SIM_LOG.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.strip().splitlines()[-lines:]) or "(日志为空)"
        except OSError as exc:
            return f"(读取日志失败: {exc})"

    def _cleanup_process(self) -> None:
        self._proc = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    # ==================================================================
    # 底层通信：TCP 控制 + UDP 组播接收
    # ==================================================================
    def _command(self, payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """向控制口发一条 JSON 命令并解析响应（一次命令一个短连接）。"""
        text = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            with socket.create_connection(("127.0.0.1", self._ctrl_port),
                                          timeout=timeout) as conn:
                conn.sendall(text.encode("utf-8"))
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise AssertionError(
                            f"[CANFD] 控制口关闭了连接（命令 {payload.get('cmd')} 未响应）")
                    buffer += chunk
        except OSError as exc:
            raise AssertionError(
                f"[CANFD] 无法连接仿真器控制口 127.0.0.1:{self._ctrl_port}：{exc}\n"
                f"提示：仿真器可能未启动/已崩溃；检查 {SIM_LOG}，"
                f"或确认端口是否被残留进程占用。") from exc
        line = buffer.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"[CANFD] 控制响应不是合法 JSON：{line!r}") from exc
        if not response.get("ok"):
            raise AssertionError(
                f"[CANFD] 仿真器拒绝命令 {payload.get('cmd')!r}："
                f"{response.get('error', '未知错误')}")
        return response

    def _start_receiver(self) -> None:
        """绑定 UDP 组播端口并启动接收线程。

        关键：测试库和仿真器**同时**绑定 30003（组播的合法用法），
        因此必须 SO_REUSEADDR；收端只保留 origin=adas 的帧（仿真器发出），
        测试端自己发上总线的 origin=tester 帧会被过滤掉，避免"读到自己的帧"造成自证。
        """
        self._stop_receiver()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", self._bus_port))
        except OSError as exc:
            sock.close()
            raise AssertionError(
                f"[CANFD] UDP 组播端口 {self._bus_port} 绑定失败：{exc}\n"
                f"常见原因：上一次测试的 canfd_sim.py/库残留、或另一个进程占用。\n"
                f"排查：netstat -ano | findstr :{self._bus_port}；"
                f"换端口：set CANFD_BUS_PORT=31003（控制口一起换 CANFD_CTRL_PORT=31004）。"
            ) from exc
        try:
            mreq = struct.pack("4s4s",
                               socket.inet_aton(self._bus_group),
                               socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        except OSError as exc:
            sock.close()
            raise AssertionError(
                f"[CANFD] 加入组播组 {self._bus_group} 失败：{exc}（检查网卡/防火墙）"
            ) from exc
        sock.settimeout(0.2)
        self._sock = sock
        self._running = True
        self._rx_thread = threading.Thread(target=self._receive_loop,
                                           name="canfd-rx", daemon=True)
        self._rx_thread.start()

    def _stop_receiver(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._rx_thread is not None and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        self._rx_thread = None

    def _receive_loop(self) -> None:
        sock = self._sock
        assert sock is not None
        while self._running:
            try:
                raw, _addr = sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict) or message.get("kind") != "frame":
                continue
            if message.get("origin") != "adas":
                continue            # 过滤测试端自己发上总线的回环副本
            seq = int(message.get("seq", 0))
            with self._lock:
                if seq in self._seen_set:
                    continue        # 多网卡组播回环重复副本去重
                self._seen_seq.append(seq)
                self._seen_set.add(seq)
                while len(self._seen_seq) > 512:
                    self._seen_set.discard(self._seen_seq.popleft())
                self._last_seq = max(self._last_seq, seq)
                message["_recv_time"] = time.monotonic()
                self._pending.append(message)
                self._history.append(message)

    # ==================================================================
    # 帧读取 / 缓存
    # ==================================================================
    @keyword("Read FD Frame")
    def read_fd_frame(self, frame_id: Optional[Any] = None,
                      timeout: float = 2.0) -> Dict[str, Any]:
        """从接收缓存读下一帧 FD 报文（可指定 ID），超时抛 AssertionError。

        返回帧字典，核心字段::

            {"seq": 123, "id": 1024, "data_hex": "…", "dlc": 15, "is_fd": true,
             "brs": true, "esi": false, "counter": 45, "valid": true,
             "crc_valid": true, "origin": "adas", …}
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        return self._wait_frame(fid, float(timeout), None, None)

    @keyword("Wait For FD Frame")
    def wait_for_fd_frame(self, frame_id: Optional[Any] = None,
                          timeout: float = 3.0,
                          since_seq: Optional[Any] = None,
                          tester_seq: Optional[Any] = None) -> Dict[str, Any]:
        """等待一帧 FD 报文；超时抛 AssertionError（消息含"未等到 FD 帧"）。

        参数：
            frame_id   帧 ID（0x400/1024/"0x400"）；省略=任意帧
            since_seq  只接受 seq 大于该标记的帧（传 Set FD Signal / Inject FD Error /
                       Send FD Frame 返回的标记，确保等到"操作之后"的新帧）
            tester_seq 只接受 echo_of 等于该值的帧（等待"自己刚发出去的那帧"的
                       总线回显，用于 Send FD Frame → Wait For FD Frame 闭环）

        超时消息可用 `Run Keyword And Expect Error    *未等到 FD 帧*` 精确匹配。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        marker = None if since_seq in (None, "") else _to_int(since_seq, "since_seq")
        echo = None if tester_seq in (None, "") else _to_int(tester_seq, "tester_seq")
        return self._wait_frame(fid, float(timeout), marker, echo)

    def _wait_frame(self, frame_id: Optional[int], timeout: float,
                    since_seq: Optional[int],
                    tester_seq: Optional[int]) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                for index, frame in enumerate(self._pending):
                    if frame_id is not None and int(frame["id"]) != frame_id:
                        continue
                    if since_seq is not None and int(frame["seq"]) <= since_seq:
                        continue
                    if tester_seq is not None and int(frame.get("echo_of") or -1) != tester_seq:
                        continue
                    del self._pending[index]
                    return frame
                pending = len(self._pending)
                last_seq = self._last_seq
            if time.monotonic() >= deadline:
                target = "任意帧" if frame_id is None else f"0x{frame_id:03X}"
                extra = ""
                if since_seq is not None:
                    extra += f"，since_seq={since_seq}"
                if tester_seq is not None:
                    extra += f"，tester_seq={tester_seq}"
                raise AssertionError(
                    f"[CANFD] {timeout}s 内未等到 FD 帧 {target}{extra}"
                    f"（接收缓存 {pending} 帧，在线最新 seq={last_seq}）"
                    f"—— 检查仿真器是否在发该帧/是否被静默/是否被拒绝（非法帧不会回显）")
            time.sleep(0.002)

    @keyword("Clear FD Frame Buffer")
    def clear_fd_frame_buffer(self) -> None:
        """清空已接收帧缓存（每个用例开头调用，保证用例隔离）。"""
        with self._lock:
            self._pending.clear()
            self._history.clear()
            self._seen_seq.clear()
            self._seen_set.clear()
            self._last_seq = 0
        info("CAN FD 接收缓存已清空")

    @keyword("Get FD Frames")
    def get_fd_frames(self, frame_id: Optional[Any] = None) -> List[Dict[str, Any]]:
        """返回接收历史中的帧列表（可按 ID 过滤）。"""
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        with self._lock:
            frames = list(self._history)
        return frames if fid is None else [f for f in frames if int(f["id"]) == fid]

    @keyword("FD Frame Count Should Be")
    def fd_frame_count_should_be(self, frame_id: Any, expected_count: Any,
                                 tolerance: Any = 0) -> int:
        """断言接收历史中指定 ID 的帧数（可带容差），用于频率/丢帧类验证。"""
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        expected = int(expected_count)
        tol = int(tolerance)
        with self._lock:
            count = sum(1 for frame in self._history if int(frame["id"]) == fid)
        if abs(count - expected) > tol:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 收到 {count} 帧，期望 {expected}±{tol} 帧")
        info(f"0x{fid:03X} 帧数 {count}（期望 {expected}±{tol}）✓")
        return count

    # ==================================================================
    # 发送
    # ==================================================================
    @keyword("Send FD Frame")
    def send_fd_frame(self, frame_id: Any, data: Any, brs: Any = True,
                      esi: Any = False, is_fd: Any = True,
                      dlc: Optional[Any] = None) -> int:
        """把一帧 FD 报文发上总线（组播），返回**测试端发送序号**。

        - data 支持 `"AA BB"` / `"AABB"` / `[170, 187]` / `${bytes}`；
        - brs/esi 为 CAN FD 标志位（数据段高速 / 错误状态指示）；
        - dlc 省略时由数据长度自动查表；显式给 dlc 时会做一致性校验；
        - 返回值 tester_seq：用于 `Wait For FD Frame ... tester_seq=${sent}`
          等待"这帧被仿真器回显到总线上"的版本（真正的收发闭环，而不是自己读自己）。

        合法数据长度：0~8、12、16、20、24、32、48、64（CAN FD 的 DLC 9~15）。
        想做非法帧负向测试请用 Send Raw FD Frame（本关键字拒绝不一致的帧）。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        if fid is None:
            raise AssertionError("[CANFD] Send FD Frame 必须指定帧 ID")
        payload = _to_bytes(data)
        fd = _to_bool(is_fd, "is_fd")
        brs_flag = _to_bool(brs, "brs")
        esi_flag = _to_bool(esi, "esi")
        if not fd:
            if len(payload) > 8:
                raise AssertionError(
                    f"[CANFD] 经典 CAN 帧最多 8 字节，收到 {len(payload)} 字节"
                    f"（要做 FD 帧请保持 is_fd=True）")
            if brs_flag:
                raise AssertionError("[CANFD] 经典 CAN 帧没有 BRS 位，is_fd=False 时 brs 必须为 False")
        if dlc is None:
            if len(payload) not in LENGTH_TO_DLC:
                raise AssertionError(
                    f"[CANFD] {len(payload)} 字节不是 CAN FD 合法长度（合法：{LEGAL_FD_LENGTHS}）")
            dlc_value = LENGTH_TO_DLC[len(payload)]
        else:
            dlc_value = _to_int(dlc, "dlc")
            if not 0 <= dlc_value <= 15:
                raise AssertionError(f"[CANFD] DLC 必须在 0~15，收到 {dlc_value}")
            expected = DLC_TO_LENGTH[dlc_value]
            if len(payload) != expected:
                raise AssertionError(
                    f"[CANFD] 数据长度与 DLC 不符：DLC={dlc_value} 要求 {expected} 字节，"
                    f"实际 {len(payload)} 字节（负向测试请用 Send Raw FD Frame）")
        return self._send_tester_frame(fid, payload, dlc_value, fd, brs_flag, esi_flag)

    @keyword("Send Raw FD Frame")
    def send_raw_fd_frame(self, frame_id: Any, data: Any, dlc: Any,
                          is_fd: Any = True, brs: Any = True,
                          esi: Any = False) -> int:
        """**不做本地校验**地把帧发上总线（负向测试专用：长度不符/非法 DLC 等）。

        仿真器收到后会校验：非法帧被拒绝、不回显、并记入错误日志
        （`Get FD Errors` 可查）。返回测试端发送序号。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        if fid is None:
            raise AssertionError("[CANFD] Send Raw FD Frame 必须指定帧 ID")
        payload = _to_bytes(data)
        return self._send_tester_frame(fid, payload, _to_int(dlc, "dlc"),
                                       _to_bool(is_fd, "is_fd"),
                                       _to_bool(brs, "brs"),
                                       _to_bool(esi, "esi"))

    def _send_tester_frame(self, frame_id: int, payload: bytes, dlc: int,
                           is_fd: bool, brs: bool, esi: bool) -> int:
        with self._lock:
            self._tester_seq += 1
            tester_seq = self._tester_seq
        message = {
            "kind": "frame",
            "origin": "tester",
            "tester_seq": tester_seq,
            "id": frame_id,
            "data_hex": payload.hex(),
            "dlc": dlc,
            "is_fd": is_fd,
            "brs": brs,
            "esi": esi,
            "ts": time.time(),
        }
        assert self._sock is not None
        self._sock.sendto(json.dumps(message, ensure_ascii=False).encode("utf-8"),
                          (self._bus_group, self._bus_port))
        info(f"发送 FD 帧 0x{frame_id:03X}：{len(payload)} 字节 DLC={dlc} "
             f"BRS={brs} ESI={esi} FD={is_fd}（tester_seq={tester_seq}）")
        return tester_seq

    # ==================================================================
    # 仿真器控制：设置信号 / 注入错误 / 静默 / 统计
    # ==================================================================
    @keyword("Set FD Signal")
    def set_fd_signal(self, signals: Optional[Dict[str, Any]] = None,
                      **kwargs: Any) -> int:
        """设置 ADAS 域控信号（下一帧 0x400 周期报文生效），返回 since_seq 标记。

        写法（RF 关键字参数）::

            ${m}=    Set FD Signal    speed_kmh=123.456    engine_rpm=3456
            ${m}=    Set FD Signal    telltale_bitmap=0x0A5

        可设置：speed_kmh / engine_rpm / gear / vehicle_mode / telltale_bitmap /
        battery_voltage / accel / odometer（对应 vehicle_fd.dbc 的 VehicleStatusFD）。
        """
        self._ensure_started()
        payload: Dict[str, Any] = dict(signals or {})
        payload.update(kwargs)
        if not payload:
            raise AssertionError("[CANFD] 至少给一个信号，例：speed_kmh=123.456")
        response = self._command({"cmd": "set_signal", "signals": payload})
        info(f"设置 ADAS 信号：{payload} → since_seq={response.get('since_seq')}")
        return int(response["since_seq"])

    @keyword("Set FD Object")
    def set_fd_object(self, index: Any, x: Any = None, y: Any = None,
                      distance: Any = None) -> int:
        """设置第 index（1~4）个障碍物的 x/y/distance（下一帧 0x401 生效）。

        返回 since_seq 标记，配合 `Wait For FD Frame    0x401    since_seq=${m}`
        等待更新后的对象列表帧。
        """
        self._ensure_started()
        command: Dict[str, Any] = {"cmd": "set_object", "index": _to_int(index, "index")}
        for name, value in (("x", x), ("y", y), ("distance", distance)):
            if value is not None and value != "":
                command[name] = float(value)
        response = self._command(command)
        info(f"设置障碍物 {index}: x={x} y={y} distance={distance} "
             f"→ since_seq={response.get('since_seq')}")
        return int(response["since_seq"])

    @keyword("Inject FD Error")
    def inject_fd_error(self, kind: str, frame_id: Optional[Any] = None,
                        count: Any = 1) -> int:
        """向 ADAS 节点注入发送错误（负向测试核心工具），返回 since_seq 标记。

        kind：
            crc_error        下一帧 CRC 被改坏（帧内 crc_valid=false）
            length_mismatch  下一帧数据被截短，长度与 DLC 不符（valid=false）
            sequence_loss    下一帧"丢帧"：counter 照常 +1 但不发总线，
                             下一帧 counter 跳号 → FD Frame Counter Should Advance 可检出

        frame_id 省略=任意帧；count=连续影响帧数（默认 1）。
        """
        self._ensure_started()
        fid = None if frame_id in (None, "") else self._parse_frame_id(frame_id)
        response = self._command({
            "cmd": "inject_error", "kind": kind, "id": fid, "count": int(count)})
        info(f"注入 FD 错误：{kind} @ {'任意帧' if fid is None else f'0x{fid:03X}'}"
             f" × {count}（since_seq={response.get('since_seq')}）")
        return int(response["since_seq"])

    @keyword("Get FD Errors")
    def get_fd_errors(self, clear: Any = False) -> List[Dict[str, Any]]:
        """读取仿真器"非法帧/错误"日志（被拒绝的测试帧、注入事件等）。

        用例里通常先 `Get FD Errors    clear=True` 清空，再制造非法帧，
        最后断言日志里有对应错误（证明接收侧真的做了校验）。
        """
        self._ensure_started()
        response = self._command({"cmd": "errors",
                                  "clear": _to_bool(clear, "clear")})
        return list(response.get("errors", []))

    @keyword("Get FD Statistics")
    def get_fd_statistics(self) -> Dict[str, Any]:
        """读取仿真器统计（seq、每 ID 帧数、回显数、拒绝数、注入数、静默状态等）。"""
        self._ensure_started()
        return self._command({"cmd": "stats"})

    @keyword("Get FD Bus Info")
    def get_fd_bus_info(self) -> Dict[str, Any]:
        """返回端口/组播信息（用于 FD 与经典 CAN 的端口隔离断言）。

        返回：{"bus_port", "bus_group", "ctrl_port", "classic_can_port", "protocol"}
        其中 classic_can_port=30000 是本项目经典 CAN 的固定端口 —— 本模块不占用它。
        """
        self._ensure_started()
        stats = self._command({"cmd": "stats"})
        return {
            "bus_port": int(stats["bus_port"]),
            "bus_group": str(stats["bus_group"]),
            "ctrl_port": int(stats["ctrl_port"]),
            "classic_can_port": CLASSIC_CAN_PORT,
            "protocol": "CAN FD",
        }

    @keyword("FD Bus Should Be Silent")
    def fd_bus_should_be_silent(self, duration: float = 0.5) -> None:
        """断言接下来 duration 秒 FD 总线上没有任何帧（暂停节点周期发送）。

        实现：先让仿真器暂停周期发送（silence=true），监听一段时间确认 0 帧，
        结束时（**即使失败**）自动恢复发送，不污染后续用例。
        """
        self._ensure_started()
        quiet_seconds = float(duration)
        self._command({"cmd": "silence", "enabled": True})
        try:
            start = time.monotonic()
            info(f"开始监听 FD 总线静默 {quiet_seconds}s ...")
            time.sleep(quiet_seconds)
            with self._lock:
                offenders = [frame for frame in self._history
                             if frame.get("_recv_time", 0.0) >= start]
            if offenders:
                sample = ", ".join(
                    f"0x{int(frame['id']):03X}(seq={frame.get('seq')})"
                    for frame in offenders[:6])
                raise AssertionError(
                    f"[CANFD] 期望 FD 总线静默 {quiet_seconds}s，但收到 {len(offenders)} 帧："
                    f"{sample}")
            info(f"FD 总线静默 {quiet_seconds}s 验证通过（0 帧）✓")
        finally:
            try:
                self._command({"cmd": "silence", "enabled": False})
            except AssertionError:
                info("[CanFdLibrary] ⚠ 恢复 FD 周期发送失败（仿真器可能已退出）")

    # ==================================================================
    # 帧属性断言
    # ==================================================================
    @keyword("FD Frame Length Should Be")
    def fd_frame_length_should_be(self, frame: Dict[str, Any],
                                  expected_length: Any) -> int:
        """断言 FD 帧的数据长度正确（含 DLC 映射一致性 + 结构有效性校验）。

        帧被注入"长度不符"后：帧内 valid=false、data 长度与 DLC 映射不一致，
        本关键字会抛出包含 **"长度不符"** 的 AssertionError（负向用例可匹配）。
        """
        frame = self._require_frame(frame, "FD Frame Length Should Be")
        fid = int(frame["id"])
        expected = _to_int(expected_length, "expected_length")
        data = _to_bytes(frame.get("data_hex", ""))
        actual = len(data)
        dlc = int(frame.get("dlc", -1))
        if frame.get("valid") is False:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 长度不符：{frame.get('error')} "
                f"（DLC={dlc}，数据 {actual} 字节）—— 帧结构无效，不能用于业务解码")
        if dlc not in DLC_TO_LENGTH:
            raise AssertionError(f"[CANFD] 帧 0x{fid:03X} 的 DLC={dlc} 非法（0~15）")
        mapped = DLC_TO_LENGTH[dlc]
        if actual != mapped:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 长度不符：DLC={dlc} 应映射 {mapped} 字节，"
                f"实际数据 {actual} 字节")
        if actual != expected:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 长度不符：期望 {expected} 字节，实际 {actual} 字节")
        info(f"0x{fid:03X} 长度 {actual} 字节（DLC={dlc}）验证通过 ✓")
        return actual

    @keyword("DLC Should Map To Length")
    def dlc_should_map_to_length(self, dlc: Any, expected_length: Any) -> int:
        """断言 CAN FD 的 DLC 映射正确（用例②逐项验证 0~15）。"""
        dlc_value = _to_int(dlc, "dlc")
        expected = _to_int(expected_length, "expected_length")
        if not 0 <= dlc_value <= 15:
            raise AssertionError(f"[CANFD] DLC 必须在 0~15，收到 {dlc_value}")
        actual = DLC_TO_LENGTH[dlc_value]
        if actual != expected:
            raise AssertionError(
                f"[CANFD] DLC {dlc_value} 应映射 {expected} 字节，实际映射 {actual} 字节")
        return actual

    @keyword("BRS Flag Should Be")
    def brs_flag_should_be(self, frame: Dict[str, Any], expected: Any) -> bool:
        """断言 FD 帧的 BRS（Bit Rate Switch）标志等于期望值。"""
        frame = self._require_frame(frame, "BRS Flag Should Be")
        expected_bool = _to_bool(expected, "expected")
        actual = bool(frame.get("brs"))
        if actual != expected_bool:
            raise AssertionError(
                f"[CANFD] 帧 0x{int(frame['id']):03X} BRS 标志期望 {expected_bool}，"
                f"实际 {actual}（BRS=1 表示数据段切高速率，CAN FD 的核心特性）")
        info(f"0x{int(frame['id']):03X} BRS={actual} 验证通过 ✓")
        return actual

    @keyword("ESI Flag Should Be")
    def esi_flag_should_be(self, frame: Dict[str, Any], expected: Any) -> bool:
        """断言 FD 帧的 ESI（Error State Indicator）标志等于期望值。"""
        frame = self._require_frame(frame, "ESI Flag Should Be")
        expected_bool = _to_bool(expected, "expected")
        actual = bool(frame.get("esi"))
        if actual != expected_bool:
            raise AssertionError(
                f"[CANFD] 帧 0x{int(frame['id']):03X} ESI 标志期望 {expected_bool}，"
                f"实际 {actual}（ESI=1 表示发送节点处于 error-passive 状态）")
        info(f"0x{int(frame['id']):03X} ESI={actual} 验证通过 ✓")
        return actual

    @keyword("FD Frame Counter Should Advance")
    def fd_frame_counter_should_advance(self, *frames: Dict[str, Any]) -> List[int]:
        """断言多帧的**序列号与发送计数器连续**（检测丢帧/乱序）。

        规则：
            ① 传入帧的 seq 必须严格递增（全局帧序号不重复、不倒序）；
            ② 同一 ID 的帧，counter 必须逐帧 +1（CAN FD 的安全报文常用
               "发送计数器"识别丢帧；跳号 = 总线上丢了帧）。

        参数至少 2 帧。正常情况返回各帧 counter 列表；发现跳号/倒序则抛
        AssertionError（消息含 **"计数器"**，负向用例可匹配）。
        """
        if len(frames) < 2:
            raise AssertionError(
                "[CANFD] FD Frame Counter Should Advance 至少需要 2 帧参数"
                "（用 Read/Wait For FD Frame 连续读帧后传入）")
        normalized: List[Dict[str, Any]] = []
        for frame in frames:
            if not isinstance(frame, dict) or "seq" not in frame:
                raise AssertionError("[CANFD] 参数必须是 Wait For FD Frame 返回的帧字典")
            normalized.append(frame)
        for index in range(1, len(normalized)):
            previous = int(normalized[index - 1]["seq"])
            current = int(normalized[index]["seq"])
            if current <= previous:
                raise AssertionError(
                    f"[CANFD] 帧序列不连续：seq {previous} → {current}（必须严格递增，"
                    f"乱序/重复说明接收或总线异常）")
        last_counter: Dict[int, int] = {}
        counters: List[int] = []
        for frame in normalized:
            fid = int(frame["id"])
            counter = int(frame.get("counter", -1))
            counters.append(counter)
            if fid in last_counter:
                expected = last_counter[fid] + 1
                if counter != expected:
                    raise AssertionError(
                        f"[CANFD] 帧 0x{fid:03X} 发送计数器跳号：上一帧 {last_counter[fid]}，"
                        f"本帧 {counter}（期望 {expected}）—— 总线上丢了 {counter - expected} 帧")
            last_counter[fid] = counter
        info(f"帧序列/计数器连续：seq={[int(f['seq']) for f in normalized]}，"
             f"counter={counters} ✓")
        return counters

    # ==================================================================
    # 信号解码（按 vehicle_fd.dbc 手工实现）
    # ==================================================================
    @keyword("Decode FD Signals")
    def decode_fd_signals(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        """按 vehicle_fd.dbc 把一帧 CAN FD 数据解码成信号字典（浮点/整数/位图）。

        支持：
            0x400 VehicleStatusFD：VehicleSpeedFD(float32)、EngineSpeed(uint16)、
                 GearPosition(4bit)、VehicleMode(4bit)、12 个警告灯位图、
                 TimestampMs(uint32)、BatteryVoltageFD/LongitudinalAccel/OdometerFD(float32)
            0x401 ObjectList：4 个障碍物 × (x, y, distance) 共 12 个 float32 + ObjectCount
        其他帧/经典帧会抛出清晰错误。

        float 信号按 IEEE-754 单精度解码（`<f`，与 DBC 的 SIG_VALTYPE_=1 对应）；
        枚举信号返回数值（0=P/1=R/…），与 cantools 的 decode_choices=False 口径一致。
        """
        frame = self._require_frame(frame, "Decode FD Signals")
        fid = int(frame["id"])
        if frame.get("is_fd") is False:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 是经典 CAN 帧（is_fd=false），"
                f"不是 CAN FD 帧，不能按 FD 信号表解码")
        if frame.get("valid") is False:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 结构无效（{frame.get('error')}），无法解码")
        dlc = int(frame.get("dlc", -1))
        if dlc not in DLC_TO_LENGTH:
            raise AssertionError(f"[CANFD] 帧 0x{fid:03X} DLC={dlc} 非法")
        data = _to_bytes(frame.get("data_hex", ""))
        expected_length = DLC_TO_LENGTH[dlc]
        if len(data) != expected_length:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 长度不符：DLC={dlc} 要求 {expected_length} 字节，"
                f"实际 {len(data)} 字节，不能解码")

        signals: Dict[str, Any] = {}
        if fid == ID_VEHICLE_STATUS:
            signals["VehicleSpeedFD"] = struct.unpack_from("<f", data, 0)[0]
            signals["EngineSpeed"] = struct.unpack_from("<H", data, 4)[0]
            signals["GearPosition"] = data[6] & 0x0F
            signals["VehicleMode"] = (data[6] >> 4) & 0x0F
            bitmap = struct.unpack_from("<H", data, 8)[0] & 0x0FFF
            for bit, name in enumerate(TELLTALE_NAMES):
                signals[name] = (bitmap >> bit) & 0x01
            signals["TimestampMs"] = struct.unpack_from("<I", data, 10)[0]
            signals["BatteryVoltageFD"] = struct.unpack_from("<f", data, 14)[0]
            signals["LongitudinalAccel"] = struct.unpack_from("<f", data, 18)[0]
            signals["OdometerFD"] = struct.unpack_from("<f", data, 22)[0]
        elif fid == ID_OBJECT_LIST:
            for index in range(4):
                base = index * 12
                signals[f"Obj{index + 1}X"] = struct.unpack_from("<f", data, base)[0]
                signals[f"Obj{index + 1}Y"] = struct.unpack_from("<f", data, base + 4)[0]
                signals[f"Obj{index + 1}Distance"] = struct.unpack_from("<f", data, base + 8)[0]
            signals["ObjectCount"] = data[48]
        else:
            raise AssertionError(
                f"[CANFD] vehicle_fd.dbc 中没有帧 0x{fid:03X} 的信号定义，"
                f"已知：0x400 VehicleStatusFD / 0x401 ObjectList / 0x402 ControlCommand（仅原始字节）")
        info(f"DBC 解码 0x{fid:03X}（{len(data)} 字节）→ {signals}")
        return signals

    @keyword("Classic CAN Frame Should Not Be Decoded As FD")
    def classic_can_frame_should_not_be_decoded_as_fd(
            self, frame: Dict[str, Any]) -> str:
        """断言"经典 CAN 帧不能被当作 FD 帧解码"（FD/经典共存边界验证）。

        实现：确认帧 is_fd=false 后，尝试按 FD 信号解码；解码必须失败
        （抛"经典 CAN 帧不是 CAN FD 帧"）。如果解码竟然成功，说明库错误地
        把经典帧当 FD 处理，本关键字报错。返回实际错误消息便于日志审查。
        """
        frame = self._require_frame(frame, "Classic CAN Frame Should Not Be Decoded As FD")
        fid = int(frame["id"])
        if frame.get("is_fd") is not False:
            raise AssertionError(
                f"[CANFD] 帧 0x{fid:03X} 的 is_fd={frame.get('is_fd')}，"
                f"不是经典 CAN 帧（本关键字只用于验证 FD/经典边界）")
        try:
            self.decode_fd_signals(frame)
        except AssertionError as exc:
            message = str(exc)
            if "经典 CAN" in message or "不是 CAN FD" in message:
                info(f"经典 CAN 帧 0x{fid:03X} 被正确拒绝按 FD 解码：{message} ✓")
                return message
            raise
        raise AssertionError(
            f"[CANFD] 经典 CAN 帧 0x{fid:03X} 不应能按 FD 信号表解码，但解码却成功了"
            f"—— 说明 FD 解码入口缺少帧类型校验")

    # ==================================================================
    # 内部工具
    # ==================================================================
    @staticmethod
    def _parse_frame_id(value: Any) -> Optional[int]:
        if value is None:
            return None
        text = str(value).strip()
        if text.lower() in ("", "none", "any", "*"):
            return None
        try:
            fid = int(text, 0)
        except ValueError as exc:
            raise AssertionError(
                f"[CANFD] 无法解析帧 ID {value!r}（用 0x400/1024 这样的写法）") from exc
        if not 0 <= fid <= 0x7FF:
            raise AssertionError(f"[CANFD] 标准帧 ID 必须在 0x000~0x7FF，收到 {fid:#x}")
        return fid

    @staticmethod
    def _require_frame(frame: Any, keyword_name: str) -> Dict[str, Any]:
        if not isinstance(frame, dict) or "id" not in frame:
            raise AssertionError(
                f"[CANFD] {keyword_name} 需要帧字典参数（Wait For FD Frame 的返回值），"
                f"收到 {type(frame).__name__}")
        return frame

    def __del__(self) -> None:  # pragma: no cover - 兜底
        try:
            self.stop_canfd_simulation()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    # 手工自测：python libraries/CanFdLibrary.py
    library = CanFdLibrary()
    library.start_canfd_simulation()
    try:
        marker = library.set_fd_signal(speed_kmh=123.456)
        frame = library.wait_for_fd_frame(0x400, timeout=2.0, since_seq=marker)
        print("帧:", {key: frame[key] for key in ("seq", "id", "dlc", "brs", "esi")})
        print("信号:", library.decode_fd_signals(frame))
        print("总线信息:", library.get_fd_bus_info())
    finally:
        library.stop_canfd_simulation()
