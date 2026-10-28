"""
LinLibrary.py — Robot Framework LIN 总线关键字库
================================================

把 "被仿真的 LIN 总线"（simulators/lin_sim.py，独立进程）包装成
Robot Framework 关键字，测试用例里就能像说话一样验证 LIN：

    Start LIN Simulation
    ${marker}=    Set LIN Slave Signal    0x10    rain_intensity=75
    ${frame}=     Wait For LIN Frame      0x10    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal       ${frame}    rain_intensity    75
    LIN Checksum Should Be Valid          ${frame}

架构（与 CAN 模块 bus.py 的对比）
----------------------------------
本项目 CAN 用 python-can 的 UDP 组播直连（多节点"微信群"）；LIN 是
**主从**总线，必须有一个"主节点"按调度表轮询，所以拆成两个进程：

    lin_sim.py（被测 LIN 总线：主节点调度器 + 从节点）  ←→  LinLibrary.py（测试端）
        UDP 127.0.0.1:30001  帧流（每帧一条 JSON，等价抓包）  ──▶  接收线程缓存
        TCP 127.0.0.1:30002  控制命令（JSON 行，一问一答）    ◀──  每个关键字发一条

为什么不用 CAN 的 233.255.0.1:30000？
    LIN 从机"不许主动说话"，CAN 的广播模型模拟不了主从调度；
    而且 LIN 测试关注的是"主机轮询 → 从机应答 → 校验和/时序"，需要
    一个按时基主动发帧的调度器，只能独立进程实现。

关键字的"标记（marker）"约定（重要，避免读到旧帧）
---------------------------------------------------
仿真器每发一帧都带 **只增不减的 seq**。`Set LIN Slave Signal` /
`Inject LIN Error` / `Send LIN Master Request` 等"改变总线行为"的关键字
都会返回当前 seq 作为标记。之后 `Wait For LIN Frame ... since_seq=${marker}`
只接受 **seq 更大** 的帧 —— 这样绝不会把"设置之前就缓存在接收队列里的
旧帧"误当成"设置之后的帧"，是消除 flaky 的关键设计（RF 测试里非常常见
的"读到旧数据"问题）。

两种校验和（本库独立实现一遍，用于交叉校验仿真器）
---------------------------------------------------
- 经典 classic：只累加数据字节（LIN 1.x / LIN 2.x 的 0x3C 主请求帧）
- 增强 enhanced：PID + 数据字节（LIN 2.x 普通帧）
累加和进位回卷后取反码。库里的 `LIN Checksum Should Be Valid` 不复用
仿真器的代码，而是**自己重算一遍**与帧里的 checksum 比对 —— 测试的
"测"才有独立性（否则仿真器算错两边一起错，用例恒真）。

LDF 解码
--------
`Decode LIN Frame By LDF` 读取 simulators/ldf/vehicle.ldf（教学版 LDF 子集，
JSON 载体），按 signal 的 start_bit/length/scale/offset 把原始字节还原成
物理值。真实项目里这一步由 CANoe/LDF Explorer 完成，测试调用现成 API；
本库手写了一遍，方便你看清"信号解码"到底发生了什么。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from robot.api.deco import keyword
from robot.api.logger import console, info

# ---------------------------------------------------------------------------
# 路径 / 端口约定（simulators/lin_sim.py 侧读同样的环境变量）
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SIM_SCRIPT = ROOT / "simulators" / "lin_sim.py"
DEFAULT_LDF = ROOT / "simulators" / "ldf" / "vehicle.ldf"
SIM_LOG = ROOT / "results" / "lin_sim_stdout.log"

UDP_HOST = "127.0.0.1"
CTRL_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 30001
DEFAULT_CTRL_PORT = 30002

# 调度表（必须与 lin_sim.py 的 SCHEDULE_IDS 一致；用于时序分析）
SCHEDULE_IDS: List[int] = [0x10, 0x11, 0x12, 0x3C]


# ---------------------------------------------------------------------------
# LIN 协议算法（独立于仿真器的第二实现 —— 测试端用来交叉校验）
# ---------------------------------------------------------------------------
def calc_pid(frame_id: int) -> int:
    """由 6 bit 帧 ID 计算带 2 bit 奇偶校验的 PID（Protected Identifier）。

    P0 = b0 ^ b1 ^ b2 ^ b4，P1 = !(b1 ^ b3 ^ b4 ^ b5)，
    PID = ID | (P0 << 6) | (P1 << 7)。
    """
    fid = frame_id & 0x3F
    b = [(fid >> i) & 1 for i in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 ^ (b[1] ^ b[3] ^ b[4] ^ b[5])
    return fid | (p0 << 6) | (p1 << 7)


def _fold_sum(raw_sum: int) -> int:
    """LIN 校验和：累加和的高位进位回卷到低 8 位，再取反码。"""
    while raw_sum > 0xFF:
        raw_sum = (raw_sum & 0xFF) + (raw_sum >> 8)
    return (~raw_sum) & 0xFF


def classic_checksum(data: bytes) -> int:
    """经典校验和：只累加数据字节。"""
    return _fold_sum(sum(data))


def enhanced_checksum(pid: int, data: bytes) -> int:
    """增强校验和：PID + 数据字节一起累加。"""
    return _fold_sum(pid + sum(data))


# ---------------------------------------------------------------------------
# 关键字库
# ---------------------------------------------------------------------------
class LinLibrary:
    """LIN 总线操作关键字集合（仿真器进程 + UDP 帧接收 + LDF 解码）。"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(
        self,
        udp_port: Optional[int] = None,
        ctrl_port: Optional[int] = None,
        ldf_path: Optional[str] = None,
        startup_timeout: float = 10.0,
    ) -> None:
        """参数都可省略：默认端口 30001/30002，默认 LDF 是 vehicle.ldf。

        端口优先级：显式参数 > 环境变量 LIN_UDP_PORT/LIN_CTRL_PORT > 默认值。
        换端口时**环境变量最方便**（测试进程和仿真子进程读同一份）::

            set LIN_UDP_PORT=31001
            set LIN_CTRL_PORT=31002
            robot tests/robot/08_lin_bus.robot
        """
        self._udp_port = int(udp_port or os.environ.get("LIN_UDP_PORT", DEFAULT_UDP_PORT))
        self._ctrl_port = int(ctrl_port or os.environ.get("LIN_CTRL_PORT", DEFAULT_CTRL_PORT))
        self._startup_timeout = float(startup_timeout)

        self._ldf_path = Path(ldf_path) if ldf_path else DEFAULT_LDF
        self._ldf: Dict[str, Any] = self._load_ldf(self._ldf_path)

        # 子进程 / socket / 接收线程
        self._proc: Optional[subprocess.Popen] = None
        self._log_file: Optional[Any] = None
        self._udp_sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()

        # 接收缓存：
        #   _pending —— 还没被任何 Read/Wait 关键字取走的帧（FIFO）
        #   _history —— 本套件收到过的全部帧（用于统计/静默断言）
        #   _events  —— 总线事件（sleep / wakeup_pulse / no_response ...）
        self._pending: Deque[Dict[str, Any]] = deque()
        self._history: Deque[Dict[str, Any]] = deque(maxlen=20000)
        self._events: Deque[Dict[str, Any]] = deque(maxlen=2000)
        self._last_seq = 0

        info(f"[LinLibrary] LIN 端口约定：UDP 帧 {UDP_HOST}:{self._udp_port}，"
             f"TCP 控制 {CTRL_HOST}:{self._ctrl_port}；LDF={self._ldf_path.name}")

    # ==================================================================
    # 生命周期
    # ==================================================================
    @keyword("Start LIN Simulation")
    def start_lin_simulation(self, timeout: float = 10.0) -> str:
        """启动 LIN 仿真器子进程并等待控制端口就绪（懒启动，重复调用安全）。

        Suite Setup 调用一次即可；如果套件里第一个操作关键字先被调用，
        库也会自动懒启动（`_ensure_started`），不会因为漏写 Setup 而失败。

        失败时抛 AssertionError，消息里带子进程日志末尾，方便定位
        "端口被占用/脚本报错/依赖缺失"三类问题。
        """
        # 已经启动且在跑：直接复用（多套件场景/重复调用）
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._command({"cmd": "ping"}, timeout=1.5)
                info("[LinLibrary] LIN 仿真器已在运行，复用当前进程")
                return "reused"
            except AssertionError:
                self._cleanup_process()   # 进程在但控制口不通 → 清理后重启

        self._start_receiver()
        self._spawn_simulator()
        self._wait_until_ready(float(timeout))
        # 清场：唤醒总线 + 清空接收缓存（上一轮残留/启动瞬间的帧都不算数）
        self._command({"cmd": "wakeup"}, timeout=3.0)
        self.clear_lin_frame_buffer()
        console(f"[LinLibrary] LIN 仿真器就绪：UDP {self._udp_port} / "
                f"TCP {self._ctrl_port}，日志 {SIM_LOG.name}")
        return "started"

    @keyword("Stop LIN Simulation")
    def stop_lin_simulation(self) -> None:
        """停止 LIN 仿真器（Suite Teardown 调用；可重复调用/从未启动也安全）。

        回收顺序（顺序错了会留孤儿进程，占着端口让下一次跑必挂）：
            1. 发 quit 命令让仿真器自己优雅退出；
            2. `Popen.wait()` 等进程真正结束；
            3. 超时则 terminate → wait → kill 兜底；
            4. 最后关 UDP 接收 socket、停接收线程。
        """
        if (self._proc is None and self._udp_sock is None
                and self._rx_thread is None):
            return                            # 从未启动/已停止：静默返回（幂等）
        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    self._command({"cmd": "quit"}, timeout=2.0)
            except AssertionError:
                pass                          # 控制口已不可用，走 terminate 兜底
            try:
                self._proc.wait(timeout=5.0)
                info("[LinLibrary] LIN 仿真器已优雅退出")
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        info("[LinLibrary] ⚠ LIN 仿真器进程未能回收（kill 后仍超时）")
                info("[LinLibrary] LIN 仿真器被强制回收（terminate/kill）")
        self._cleanup_process()
        self._stop_receiver()
        console("[LinLibrary] LIN 仿真器已停止")

    def _ensure_started(self) -> None:
        """懒启动：任何操作类关键字发现仿真器没跑就自动拉起。"""
        if (self._proc is None or self._proc.poll() is not None
                or not self._running):
            self.start_lin_simulation()

    def _spawn_simulator(self) -> None:
        """用当前 Python 解释器拉起 simulators/lin_sim.py。"""
        if not SIM_SCRIPT.exists():
            raise AssertionError(f"[LIN] 找不到仿真器脚本：{SIM_SCRIPT}")
        SIM_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(SIM_LOG, "wb")
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        cmd = [
            sys.executable, "-X", "utf8", str(SIM_SCRIPT),
            "--udp-port", str(self._udp_port),
            "--ctrl-port", str(self._ctrl_port),
        ]
        info(f"[LinLibrary] 启动 LIN 仿真器命令：{' '.join(cmd)}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,       # Windows 下不继承 stdin，避免挂起
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
        except OSError as exc:
            raise AssertionError(f"[LIN] 启动仿真器失败：{exc}") from exc

    def _wait_until_ready(self, timeout: float) -> None:
        """轮询控制端口直到 ping 通；进程先死则带上日志末尾报清晰错误。"""
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                # 进程已经退出：先试一次控制口（可能是别的残留实例占着端口），
                # 连不上就报错，并把日志末尾贴出来。
                try:
                    self._command({"cmd": "ping"}, timeout=1.0)
                    info("[LinLibrary] 检测到已有 LIN 仿真器实例，复用")
                    return
                except AssertionError:
                    tail = self._read_log_tail()
                    raise AssertionError(
                        f"[LIN] LIN 仿真器启动失败（退出码 {self._proc.returncode}）。\n"
                        f"子进程日志（{SIM_LOG}）末尾：\n{tail}\n"
                        f"常见原因：控制端口 {self._ctrl_port} 被占用（残留进程）→ "
                        f"用 netstat -ano | findstr :{self._ctrl_port} 排查，"
                        f"或设置环境变量 LIN_CTRL_PORT/LIN_UDP_PORT 换端口。"
                    ) from None
            try:
                self._command({"cmd": "ping"}, timeout=1.5)
                return
            except AssertionError as exc:
                last_error = str(exc)
                time.sleep(0.05)
        raise AssertionError(
            f"[LIN] {timeout}s 内 LIN 仿真器控制端口未就绪（{CTRL_HOST}:{self._ctrl_port}）。\n"
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
        """清空进程相关资源（不负责终止进程，由调用方决定）。"""
        self._proc = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    # ==================================================================
    # 底层通信：TCP 控制 + UDP 接收
    # ==================================================================
    def _command(self, payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """向仿真器控制端口发一条 JSON 命令，返回解析后的响应字典。

        一次命令一个 TCP 连接（命令频率很低，几十微秒级开销，换来实现简单
        且没有"连接半死"状态需要维护 —— 教学项目里可维护性优先）。
        """
        text = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            with socket.create_connection((CTRL_HOST, self._ctrl_port),
                                          timeout=timeout) as conn:
                conn.sendall(text.encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise AssertionError(
                            f"[LIN] 控制端口关闭了连接（命令 {payload.get('cmd')} 未收到响应）")
                    buf += chunk
        except OSError as exc:
            raise AssertionError(
                f"[LIN] 无法连接 LIN 仿真器控制端口 {CTRL_HOST}:{self._ctrl_port}：{exc}\n"
                f"提示：仿真器可能没启动/已崩溃；检查 {SIM_LOG} 日志，"
                f"或确认端口是否被残留进程占用。") from exc

        line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"[LIN] 控制响应不是合法 JSON：{line!r}") from exc
        if not resp.get("ok"):
            raise AssertionError(
                f"[LIN] 仿真器拒绝命令 {payload.get('cmd')!r}："
                f"{resp.get('error', '未知错误')}")
        return resp

    def _start_receiver(self) -> None:
        """绑定 UDP 帧端口并启动接收线程（先于仿真器启动，避免丢启动帧）。"""
        self._stop_receiver()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((UDP_HOST, self._udp_port))
        except OSError as exc:
            sock.close()
            raise AssertionError(
                f"[LIN] UDP 帧端口 {UDP_HOST}:{self._udp_port} 绑定失败：{exc}\n"
                f"常见原因：上一次测试的 lin_sim.py 残留/另一个测试进程在用同一端口。\n"
                f"排查：netstat -ano | findstr :{self._udp_port}；"
                f"换端口：set LIN_UDP_PORT=31001（控制端口也一起换 LIN_CTRL_PORT=31002）。"
            ) from exc
        sock.settimeout(0.2)
        self._udp_sock = sock
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._receive_loop, name="lin-rx", daemon=True)
        self._rx_thread.start()

    def _stop_receiver(self) -> None:
        self._running = False
        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except OSError:
                pass
            self._udp_sock = None
        if self._rx_thread is not None and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        self._rx_thread = None

    def _receive_loop(self) -> None:
        """接收线程：UDP 报文 → JSON → 帧进 _pending/_history，事件进 _events。"""
        sock = self._udp_sock
        assert sock is not None
        while self._running:
            try:
                raw, _addr = sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break                     # socket 被关闭（正常停止）
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            msg["_recv_time"] = time.monotonic()
            with self._lock:
                if msg.get("kind") == "frame":
                    seq = int(msg.get("seq", 0))
                    self._last_seq = max(self._last_seq, seq)
                    self._pending.append(msg)
                    self._history.append(msg)
                else:
                    self._events.append(msg)

    # ==================================================================
    # 帧读取 / 计数
    # ==================================================================
    @keyword("Read LIN Frame")
    def read_lin_frame(self, frame_id: Optional[Any] = None,
                       timeout: float = 2.0) -> Dict[str, Any]:
        """从接收缓存读**下一帧**（可指定帧 ID），阻塞等待直到超时。

        与 `Wait For LIN Frame` 的区别：
        - Read 是"取缓存下一帧"（最贴近真实 LIN 分析仪 read() 的语义）；
        - Wait For 支持 `since_seq` 标记只等"某操作之后发出的新帧"，
          是写测试时更常用的安全写法（不会读到旧帧）。

        返回帧字典（字段示例见 docs/23 章）::

            {"id":16, "pid":80, "data":[150], "checksum":..., "checksum_type":"enhanced",
             "checksum_valid":true, "pid_parity_valid":true, "seq":..., ...}
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        return self._wait_frame(fid, float(timeout), since_seq=None)

    @keyword("Wait For LIN Frame")
    def wait_for_lin_frame(self, frame_id: Optional[Any] = None,
                           timeout: float = 3.0,
                           since_seq: Optional[Any] = None) -> Dict[str, Any]:
        """等待一帧指定 ID 的 LIN 报文；超时抛 AssertionError（消息含原因）。

        参数：
            frame_id   帧 ID，如 0x10 / "0x10" / 16；省略或 any = 任意帧
            timeout    超时秒数
            since_seq  只接受 seq **大于** 该标记的帧。传 Set LIN Slave Signal /
                       Inject LIN Error / Send LIN Master Request / Wake Up LIN Bus
                       的返回值，即可确保等待的是"操作之后"的新帧。

        超时消息以 "未等到帧" 结尾（负向用例可用
        `Run Keyword And Expect Error    *未等到帧*` 精确断言）。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        marker = None if since_seq in (None, "") else int(str(since_seq), 0)
        return self._wait_frame(fid, float(timeout), since_seq=marker)

    def _wait_frame(self, frame_id: Optional[int], timeout: float,
                    since_seq: Optional[int]) -> Dict[str, Any]:
        """从 _pending 里按条件取帧（取走并从缓存删除），超时报错。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                for index, frame in enumerate(self._pending):
                    if frame_id is not None and int(frame["id"]) != frame_id:
                        continue
                    if since_seq is not None and int(frame["seq"]) <= since_seq:
                        continue
                    del self._pending[index]
                    return frame
                pending_count = len(self._pending)
                last_seq = self._last_seq
            if time.monotonic() >= deadline:
                target = "任意帧" if frame_id is None else f"0x{frame_id:02X}"
                extra = (f"，since_seq={since_seq}" if since_seq is not None else "")
                raise AssertionError(
                    f"[LIN] {timeout}s 内未等到帧 {target} 的报文{extra}"
                    f"（接收缓存 {pending_count} 帧，在线最新 seq={last_seq}）"
                    f"—— 检查仿真器是否在发该帧/是否已进入睡眠/是否注入了 no_response")
            time.sleep(0.002)

    @keyword("LIN Frame Count Should Be")
    def lin_frame_count_should_be(self, frame_id: Any, expected_count: Any,
                                  tolerance: Any = 0) -> int:
        """断言"自上次清空缓存以来"收到的指定 ID 帧数等于期望值（可带容差）。

        典型用法（测周期报文数量）::

            Clear LIN Frame Buffer
            Sleep    0.5s
            LIN Frame Count Should Be    0x10    12    tolerance=2

        为什么带 tolerance：0.5s / 40ms 周期 = 12.5 帧，边界上必然有 ±1
        的量子化误差；真实总线测试同样用"期望 ± 容差"而不是精确相等。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        expected = int(expected_count)
        tol = int(tolerance)
        count = self.get_lin_frame_count(fid)
        if abs(count - expected) > tol:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} 收到 {count} 帧，期望 {expected}±{tol} 帧"
                f"（自上次 Clear LIN Frame Buffer 起；检查调度是否正常/是否睡眠）")
        info(f"0x{fid:02X} 帧数 {count}（期望 {expected}±{tol}）✓")
        return count

    @keyword("Get LIN Frame Count")
    def get_lin_frame_count(self, frame_id: Optional[Any] = None) -> int:
        """返回接收历史中指定 ID 的帧数（清空缓存后重新计数）。"""
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        with self._lock:
            if fid is None:
                return len(self._history)
            return sum(1 for frame in self._history if int(frame["id"]) == fid)

    @keyword("Clear LIN Frame Buffer")
    def clear_lin_frame_buffer(self) -> None:
        """清空已接收帧缓存 + 统计历史 + 事件缓存（每个用例开头调用，保证隔离）。

        注意：它**不影响仿真器**的从机信号/睡眠状态 —— 那是总线状态，
        由 `Wake Up LIN Bus` 等关键字负责复位（套件的 Test Setup 两件事都做）。
        """
        with self._lock:
            self._pending.clear()
            self._history.clear()
            self._events.clear()
            self._last_seq = 0
        info("LIN 接收缓存已清空")

    @keyword("Get LIN Frames")
    def get_lin_frames(self, frame_id: Optional[Any] = None) -> List[Dict[str, Any]]:
        """返回接收历史里的帧列表（按到达顺序；可过滤 ID）——供自定义分析用。"""
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        with self._lock:
            frames = list(self._history)
        if fid is None:
            return frames
        return [frame for frame in frames if int(frame["id"]) == fid]

    @keyword("Get LIN Events")
    def get_lin_events(self, event_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回总线事件列表（sleep / wakeup_pulse / master_request / no_response）。

        用途：验证"唤醒脉冲真的发过"这类**控制行为**（帧本身看不出唤醒事件）。
        """
        self._ensure_started()
        with self._lock:
            events = list(self._events)
        if event_name:
            return [event for event in events if event.get("event") == event_name]
        return events

    # ==================================================================
    # 主节点行为：设置从机信号 / 发主请求 / 发报头 / 注入错误
    # ==================================================================
    @keyword("Set LIN Slave Signal")
    def set_lin_slave_signal(self, frame_id: Any, signals: Optional[Dict[str, Any]] = None,
                             **kwargs: Any) -> int:
        """设置从节点信号值（等价于台架上"把从机信号拉到某个值"）。

        两种写法都行（RF 的 **kwargs 语法更自然）::

            ${m}=    Set LIN Slave Signal    0x10    rain_intensity=75
            ${m}=    Set LIN Slave Signal    0x12    seat_slide=200    seat_backrest=45.0
            ${m}=    Set LIN Slave Signal    0x10    ${{ {'rain_intensity': 75} }}   # 很少用

        返回值是**当前帧序号标记（since_seq）**：紧跟着
        `Wait For LIN Frame    ${id}    since_seq=${m}` 就能等到"设置之后"
        的第一帧，绝不会读到设置前的旧值。

        数值会按 LDF 的 scale/offset 编码成原始位；越界（如雨量 120%）直接
        报错 —— 测试要的是"设置非法值应被拒绝"，而不是静默截断。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        if fid is None:
            raise AssertionError("[LIN] Set LIN Slave Signal 必须指定帧 ID")
        payload: Dict[str, Any] = dict(signals or {})
        payload.update(kwargs)
        if not payload:
            raise AssertionError("[LIN] 至少给一个信号，例：rain_intensity=75")
        resp = self._command({"cmd": "set_slave", "id": fid, "signals": payload})
        info(f"设置从机信号 0x{fid:02X}: {payload} → data={resp.get('data')}"
             f"（since_seq={resp.get('since_seq')}）")
        return int(resp["since_seq"])

    @keyword("Send LIN Master Request")
    def send_lin_master_request(self, data: Any) -> int:
        """发 0x3C 主请求帧（8 字节），并按首字节执行睡眠/唤醒语义。

        LIN 的睡眠/唤醒就是主节点发的**主请求帧**：
            data[0] = 0x00 → 睡眠命令：本帧照发，然后调度停止（总线静默）
            data[0] = 0x01 → 唤醒请求：总线保持/恢复清醒

        参数 data 支持 `[0,0,0,0,0,0,0,0]` 或十六进制字符串 `"00 00 00 00 00 00 00 00"`
        （不足 8 字节自动补 0）。

        返回 since_seq 标记（之后等帧请带上）。
        """
        self._ensure_started()
        payload = self._normalize_master_data(data)
        resp = self._command({"cmd": "send_master_request", "data": payload})
        info(f"发送 0x3C 主请求: {payload} → {resp.get('cmd_text')}"
             f"（sleeping={resp.get('sleeping')}, since_seq={resp.get('since_seq')}）")
        return int(resp["since_seq"])

    @keyword("Send LIN Header")
    def send_lin_header(self, frame_id: Any) -> Dict[str, Any]:
        """手动发一个 LIN 报头（header），令对应从机立即应答一帧并返回该帧。

        真实 LIN：主节点发 break+sync+PID（报头），匹配 ID 的从机才填数据。
        本关键字等价于"手工触发一次轮询"（CANoe 里也有这种手动触发），
        返回的是仿真器构造并已发到帧流里的完整帧字典。
        """
        self._ensure_started()
        fid = self._parse_frame_id(frame_id)
        if fid is None:
            raise AssertionError("[LIN] Send LIN Header 必须指定帧 ID")
        resp = self._command({"cmd": "send_header", "id": fid})
        frame = resp.get("frame")
        if not frame:
            raise AssertionError(f"[LIN] 发送报头 0x{fid:02X} 后未收到从机应答帧")
        info(f"发送报头 0x{fid:02X} → 从机应答 data={frame.get('data')} "
             f"checksum={frame.get('checksum')}")
        return frame

    @keyword("Inject LIN Error")
    def inject_lin_error(self, kind: str, frame_id: Optional[Any] = None,
                         count: Any = 1) -> int:
        """向 LIN 总线注入错误（负向测试的核心工具）。

        kind 取值（与 simulators/lin_sim.py 的 inject_error 命令一一对应）：
            checksum      校验和错误：帧照发，但 checksum 被翻转成错误值
                          （用于验证接收端**真的在校验**，而不是恒真）
            no_response   从机无响应：目标帧被跳过不发（用于验证超时处理）
            bad_parity    PID 奇偶校验错误：PID 的 P0 位被翻转
                          （接收端应把整帧判为无效）

        frame_id 省略 = 下一个任意帧（一般建议指定，避免误伤别的 ID）。
        count = 连续影响多少帧（默认 1；no_response 注入可给 5 制造足够长的空档）。

        返回 since_seq 标记：注入生效在"标记之后"的帧上。
        """
        self._ensure_started()
        fid = None if frame_id in (None, "") else self._parse_frame_id(frame_id)
        resp = self._command({
            "cmd": "inject_error", "kind": kind,
            "id": fid, "count": int(count),
        })
        target = "任意帧" if fid is None else f"0x{fid:02X}"
        info(f"注入 LIN 错误：{kind} @ {target} × {count}"
             f"（since_seq={resp.get('since_seq')}，排队任务数={resp.get('pending')}）")
        return int(resp["since_seq"])

    @keyword("Wake Up LIN Bus")
    def wake_up_lin_bus(self) -> int:
        """发送 LIN 唤醒脉冲，让总线从睡眠恢复调度。

        真实 LIN 睡眠后，主节点把总线拉低 250us~5ms 作为"唤醒脉冲"；
        本仿真等价发一条 wakeup_pulse 事件（可用 `Get LIN Events` 验证）
        并恢复调度。返回 since_seq 标记。
        """
        self._ensure_started()
        resp = self._command({"cmd": "wakeup"})
        info(f"发送 LIN 唤醒脉冲（was_sleeping={resp.get('was_sleeping')}，"
             f"since_seq={resp.get('since_seq')}）")
        return int(resp["since_seq"])

    # ==================================================================
    # 总线状态断言
    # ==================================================================
    @keyword("LIN Bus Should Be Silent")
    def lin_bus_should_be_silent(self, duration: float = 0.5) -> None:
        """断言接下来 duration 秒内**总线上没有任何帧**（睡眠/静默测试用）。

        判定依据是"接收线程在这段时间里有没有收到新帧"，不是"缓存里有没有"，
        所以不需要先清缓存也不会被历史帧误判（但用例里建议先 Sleep 一小段
        让命令生效，再调用本关键字）。
        """
        self._ensure_started()
        quiet_seconds = float(duration)
        start = time.monotonic()
        info(f"开始监听 LIN 总线静默 {quiet_seconds}s ...")
        time.sleep(quiet_seconds)
        with self._lock:
            offenders = [frame for frame in self._history
                         if frame.get("_recv_time", 0.0) >= start]
        if offenders:
            sample = ", ".join(
                f"0x{int(frame['id']):02X}(seq={frame.get('seq')})"
                for frame in offenders[:6])
            raise AssertionError(
                f"[LIN] 期望总线静默 {quiet_seconds}s，但收到 {len(offenders)} 帧："
                f"{sample} —— 检查是否真的进入睡眠（Send LIN Master Request 首字节 0x00）")
        info(f"总线静默 {quiet_seconds}s 验证通过（0 帧）✓")

    @keyword("LIN Frame Should Contain Signal")
    def lin_frame_should_contain_signal(self, frame_or_id: Any, signal_name: str,
                                        expected: Any, tolerance: float = 0.01,
                                        timeout: float = 2.0) -> float:
        """断言某帧里某个信号的物理值符合期望（按 LDF 解码）。

        frame_or_id 可以是 `Wait For LIN Frame` 返回的帧字典，也可以直接给帧 ID
        （给 ID 时本关键字会自己等下一帧）::

            ${frame}=    Wait For LIN Frame    0x10    since_seq=${m}
            LIN Frame Should Contain Signal    ${frame}    rain_intensity    75    tolerance=0.5
            LIN Frame Should Contain Signal    0x12    seat_slide    200

        tolerance：缩放后的物理值比较容差（默认 0.01；0.5% 的雨量信号给 0.5）。
        """
        frame = self._resolve_frame(frame_or_id, timeout)
        fid = int(frame["id"])
        defn = self._frame_definition(fid)
        signal_defs = {sig["name"]: sig for sig in defn.get("signals", [])}
        if signal_name not in signal_defs:
            raise AssertionError(
                f"[LIN] LDF 中帧 0x{fid:02X} 没有信号 {signal_name!r}，"
                f"已知信号：{list(signal_defs)}")
        actual = self._decode_frame(frame)["values"].get(signal_name)
        signal_def = signal_defs[signal_name]
        expected_value: Any = expected
        values = signal_def.get("values") or {}
        if values and isinstance(expected, str):
            # 允许按枚举名断言："wiper_state=LOW"
            reverse = {str(label).lower(): int(raw) for raw, label in values.items()}
            key = expected.strip().lower()
            if key in reverse:
                expected_value = float(reverse[key])
            else:
                raise AssertionError(
                    f"[LIN] 信号 {signal_name} 的枚举值 {expected!r} 不存在，"
                    f"可选：{list(values.values())}")
        expected_float = float(expected_value)
        if abs(float(actual) - expected_float) > float(tolerance):
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} 信号 {signal_name} 期望 {expected_float:g}"
                f" 实际 {float(actual):g}（容差 ±{float(tolerance):g}，"
                f"data={frame.get('data')}）")
        info(f"0x{fid:02X}.{signal_name} = {float(actual):g} ≈ 期望 {expected_float:g} ✓")
        return float(actual)

    @keyword("LIN Checksum Should Be Valid")
    def lin_checksum_should_be_valid(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        """断言一帧 LIN 报文的校验和正确（**测试端独立重算**，不是看标志位）。

        参数是 `Wait For LIN Frame` 返回的帧字典。本关键字：
            1. 先用帧内 pid/data 按 checksum_type（classic/enhanced）重算校验和；
            2. 与帧里的 checksum 逐字节比对；
            3. 若帧带 checksum_valid=false（注入错误）也直接失败，消息说明原因。

        失败消息含"校验和不匹配/校验和无效"关键词，负向用例可精确匹配::

            Run Keyword And Expect Error    *校验和*    LIN Checksum Should Be Valid    ${frame}
        """
        frame = self._require_frame_dict(frame, "LIN Checksum Should Be Valid")
        fid = int(frame["id"])
        data = bytes(int(byte) & 0xFF for byte in frame["data"])
        pid = int(frame["pid"])
        checksum_type = frame.get("checksum_type") or "enhanced"
        actual = int(frame["checksum"])
        if frame.get("checksum_valid") is False:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} 校验和无效（checksum_valid=false，"
                f"通常是 inject_error kind=checksum 注入的结果）："
                f"线上 0x{actual:02X}，data={data.hex()}")
        expected = (classic_checksum(data) if checksum_type == "classic"
                    else enhanced_checksum(pid, data))
        if expected != actual:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} 校验和不匹配：帧内 0x{actual:02X}，"
                f"按 {checksum_type} 算法重算应为 0x{expected:02X}"
                f"（pid=0x{pid:02X}, data={data.hex()}）")
        info(f"0x{fid:02X} {checksum_type} 校验和 0x{actual:02X} 验证通过 ✓")
        return frame

    @keyword("LIN PID Should Be Valid")
    def lin_pid_should_be_valid(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        """断言一帧 LIN 报文的 PID（含 2 bit 奇偶校验）正确。

        PID = ID + P0/P1 两个奇偶位；接收端用 PID 判断帧 ID 是否可信。
        注入 `bad_parity` 后本关键字应失败（负向用例用它证明灵敏度）::

            Run Keyword And Expect Error    *PID*    LIN PID Should Be Valid    ${frame}
        """
        frame = self._require_frame_dict(frame, "LIN PID Should Be Valid")
        fid = int(frame["id"])
        actual = int(frame["pid"])
        expected = calc_pid(fid)
        if frame.get("pid_parity_valid") is False:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} PID 奇偶校验无效（pid_parity_valid=false，"
                f"通常是 inject_error kind=bad_parity 注入的结果）："
                f"线上 PID=0x{actual:02X}，正确值应为 0x{expected:02X}")
        if actual != expected:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} PID 奇偶校验不匹配：帧内 0x{actual:02X}，"
                f"按 LIN 2.x 公式重算应为 0x{expected:02X}")
        info(f"0x{fid:02X} PID=0x{actual:02X}（奇偶校验）验证通过 ✓")
        return frame

    # ==================================================================
    # 时序统计
    # ==================================================================
    @keyword("Get LIN Statistics")
    def get_lin_statistics(self) -> Dict[str, Any]:
        """读取仿真器内部统计（emitted/skipped/sleeping/唤醒次数/每 ID 帧数等）。"""
        self._ensure_started()
        return self._command({"cmd": "stats"})

    @keyword("Get LIN Schedule Statistics")
    def get_lin_schedule_statistics(self) -> Dict[str, Any]:
        """基于**已收到的帧**分析调度时序，返回统计字典（写自定义断言用）。

        返回结构::

            {"frames": 200,
             "duration_s": 2.0,
             "gap_avg_ms": 10.0, "gap_min_ms":..., "gap_max_ms":...,
             "per_id": {"0x10": {"count": 50, "period_avg_ms": 40.0,
                                 "period_min_ms":..., "period_max_ms":...,
                                 "jitter_ms": 0.9}, ...}}

        周期/抖动定义（与 CANOE 的 Trace 分析口径一致）：
        - 周期 = 同一 ID 相邻两帧的到达间隔；
        - 抖动 jitter = 该 ID 周期的 (max - min)。
        """
        self._ensure_started()
        with self._lock:
            frames = list(self._history)
        result: Dict[str, Any] = {
            "frames": len(frames), "duration_s": 0.0,
            "gap_avg_ms": 0.0, "gap_min_ms": 0.0, "gap_max_ms": 0.0,
            "per_id": {},
        }
        if not frames:
            return result
        frames.sort(key=lambda frame: int(frame.get("seq", 0)))
        result["duration_s"] = round(
            float(frames[-1]["_recv_time"]) - float(frames[0]["_recv_time"]), 4)
        gaps = [(float(b["_recv_time"]) - float(a["_recv_time"])) * 1000.0
                for a, b in zip(frames, frames[1:])]
        if gaps:
            result.update({
                "gap_avg_ms": round(sum(gaps) / len(gaps), 3),
                "gap_min_ms": round(min(gaps), 3),
                "gap_max_ms": round(max(gaps), 3),
            })
        by_id: Dict[int, List[Dict[str, Any]]] = {}
        for frame in frames:
            by_id.setdefault(int(frame["id"]), []).append(frame)
        for fid, group in sorted(by_id.items()):
            stamps = [float(frame["_recv_time"]) for frame in group]
            periods = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
            entry: Dict[str, Any] = {"count": len(group)}
            if periods:
                entry.update({
                    "period_avg_ms": round(sum(periods) / len(periods), 3),
                    "period_min_ms": round(min(periods), 3),
                    "period_max_ms": round(max(periods), 3),
                    "jitter_ms": round(max(periods) - min(periods), 3),
                })
            result["per_id"][f"0x{fid:02X}"] = entry
        return result

    @keyword("LIN Schedule Timing Should Be Within")
    def lin_schedule_timing_should_be_within(
        self,
        expected_slot_ms: float = 10.0,
        slot_tolerance_ms: float = 4.0,
        period_tolerance_ms: float = 6.0,
        jitter_tolerance_ms: float = 15.0,
        min_frames: int = 3,
    ) -> Dict[str, Any]:
        """断言 LIN 调度表的时基与周期符合设计（用例①的核心断言）。

        默认设计：调度表每 10ms 一个槽位，4 个报文轮一圈 → 每个报文
        周期 40ms。关键字会从已收到的帧里算：
            - 全网相邻帧间隔均值 ≈ expected_slot_ms（± slot_tolerance_ms）
            - 每个报文周期均值 ≈ expected_slot_ms × 4（± period_tolerance_ms）
            - 每个报文抖动 jitter = max-min（≤ jitter_tolerance_ms）

        失败消息会指出具体是哪个帧 ID、实测值多少、容差多少——真实台架
        上"周期漂移"缺陷就靠这类断言暴露。
        """
        self._ensure_started()
        slot = float(expected_slot_ms)
        slot_tol = float(slot_tolerance_ms)
        period_tol = float(period_tolerance_ms)
        jitter_tol = float(jitter_tolerance_ms)
        min_frames = int(min_frames)
        expected_period = slot * len(SCHEDULE_IDS)
        stats = self.get_lin_schedule_statistics()

        if stats["frames"] < len(SCHEDULE_IDS) * min_frames:
            raise AssertionError(
                f"[LIN] 采样帧数不足：{stats['frames']} 帧，"
                f"至少需要 {len(SCHEDULE_IDS) * min_frames} 帧（先 Clear + Sleep 采一段）")

        gap_avg = float(stats["gap_avg_ms"])
        if abs(gap_avg - slot) > slot_tol:
            raise AssertionError(
                f"[LIN] 调度时基异常：相邻帧平均间隔 {gap_avg:.2f}ms，"
                f"期望 {slot:.1f}±{slot_tol:.1f}ms（调度表每槽 {slot:.0f}ms）")

        for fid in SCHEDULE_IDS:
            key = f"0x{fid:02X}"
            entry = stats["per_id"].get(key)
            if entry is None or entry.get("count", 0) < min_frames:
                raise AssertionError(
                    f"[LIN] 调度表报文 0x{fid:02X} 采样不足："
                    f"{0 if entry is None else entry.get('count')} 帧 < {min_frames}")
            avg = float(entry["period_avg_ms"])
            if abs(avg - expected_period) > period_tol:
                raise AssertionError(
                    f"[LIN] 报文 0x{fid:02X} 周期均值 {avg:.2f}ms 偏离期望 "
                    f"{expected_period:.1f}ms（容差 ±{period_tol:.1f}ms，"
                    f"共 {entry['count']} 帧）—— 调度表时基/轮询顺序可能有问题")
            jitter = float(entry["jitter_ms"])
            if jitter > jitter_tol:
                raise AssertionError(
                    f"[LIN] 报文 0x{fid:02X} 周期抖动 {jitter:.2f}ms 超过容差 "
                    f"{jitter_tol:.1f}ms（周期均值 {avg:.2f}ms）—— 时基不稳")
        info(f"LIN 调度时序验证通过：相邻帧 {gap_avg:.2f}ms（期望 {slot:.0f}ms），"
             f"4 报文周期 ≈ {expected_period:.0f}ms，抖动均 ≤ {jitter_tol:.0f}ms ✓")
        return stats

    # ==================================================================
    # LDF
    # ==================================================================
    @keyword("Set LDF File")
    def set_ldf_file(self, ldf_path: str) -> str:
        """切换 LDF 数据库文件（默认用 simulators/ldf/vehicle.ldf）。

        传相对路径时按项目根目录解析。文件不存在/JSON 结构不合法会抛清晰
        错误（模拟真实工作里"数据库文件路径配错"的现场）。
        """
        candidate = Path(str(ldf_path))
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if not candidate.exists():
            raise AssertionError(
                f"[LIN] LDF 文件不存在：{candidate}（当前工作目录 {Path.cwd()}）")
        self._ldf = self._load_ldf(candidate)      # 先校验，成功后才替换
        self._ldf_path = candidate
        info(f"LDF 已切换：{candidate}（{len(self._ldf.get('frames', []))} 个帧定义）")
        return str(candidate)

    @keyword("Decode LIN Frame By LDF")
    def decode_lin_frame_by_ldf(self, frame: Any, data: Optional[Any] = None,
                                decode_choices: bool = False) -> Dict[str, Any]:
        """按 LDF 把一帧 LIN 报文的原始数据解码成物理值（返回信号名→值字典）。

        参数 frame 可以是帧字典（`Wait For LIN Frame` 的返回值），也可以是帧 ID
        （此时需再给 data，如 `"C8 5A"` 或 [200, 90]）::

            ${decoded}=    Decode LIN Frame By LDF    ${frame}
            ${seat}=       Decode LIN Frame By LDF    0x12    200,90

        decode_choices=False（默认）时枚举信号返回**原始数值**（与 cantools 的
        decode_choices=False 行为一致；本项目 DBC 解码也统一这么用）；
        True 时返回枚举文本（如 wiper_state=2 → "LOW"）。
        """
        if isinstance(frame, dict):
            fid = int(frame["id"])
            raw_data = list(frame["data"])
        else:
            fid = self._parse_frame_id(frame)
            if fid is None or data is None:
                raise AssertionError(
                    "[LIN] 传帧字典，或传帧 ID + data 两参数，例："
                    "Decode LIN Frame By LDF    0x12    200,90")
            raw_data = self._normalize_bytes(data)
        defn = self._frame_definition(fid)
        expected_len = int(defn.get("length", len(raw_data)))
        if len(raw_data) < expected_len:
            raise AssertionError(
                f"[LIN] 帧 0x{fid:02X} 数据长度 {len(raw_data)} 字节，"
                f"LDF 要求 {expected_len} 字节")
        payload = bytes(int(byte) & 0xFF for byte in raw_data[:expected_len])
        decoded_full = self._decode_frame({"id": fid, "data": raw_data,
                                           "pid": calc_pid(fid)})
        values = decoded_full["values"]
        labels = decoded_full["labels"]
        if decode_choices:
            result = dict(labels)
        else:
            result = dict(values)
        info(f"LDF 解码 0x{fid:02X}（{defn.get('name')}）data={payload.hex()} → {result}")
        return result

    # ==================================================================
    # 内部工具：ID/字节/帧字典/LDF 解码
    # ==================================================================
    @staticmethod
    def _parse_frame_id(value: Any) -> Optional[int]:
        """解析帧 ID：支持 0x10 / "0x10" / 16 / None（None=任意帧）。"""
        if value is None:
            return None
        text = str(value).strip()
        if text.lower() in ("", "none", "any", "*"):
            return None
        try:
            fid = int(text, 0)
        except ValueError as exc:
            raise AssertionError(f"[LIN] 无法解析帧 ID {value!r}（用 0x10/16 这样的写法）") from exc
        if not 0x00 <= fid <= 0x3F:
            raise AssertionError(f"[LIN] LIN 帧 ID 必须在 0x00~0x3F，收到 {fid:#x}")
        return fid

    @staticmethod
    def _normalize_master_data(data: Any) -> List[int]:
        """把 RF 传来的主请求数据规范成 8 字节列表（支持 list / hex 字符串）。"""
        if isinstance(data, str):
            text = data.replace(",", " ").replace("0x", " ").strip()
            tokens = [token for token in text.split() if token]
            try:
                # 兼容 "0000000000000000" / "00 00 00 ..." / "0,0,0" 三种写法
                if len(tokens) == 1 and tokens[0]:
                    chunk = tokens[0]
                    values = [int(chunk[i:i + 2], 16)
                              for i in range(0, len(chunk), 2)]
                else:
                    values = [int(token, 16) for token in tokens]
            except ValueError as exc:
                raise AssertionError(f"[LIN] 主请求数据 {data!r} 不是合法十六进制") from exc
        elif isinstance(data, (list, tuple)):
            try:
                values = [int(value) for value in data]
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"[LIN] 主请求数据 {data!r} 含非整数") from exc
        else:
            raise AssertionError(
                f"[LIN] 主请求数据应为列表或十六进制字符串，收到 {type(data).__name__}")
        if len(values) > 8:
            raise AssertionError(f"[LIN] 主请求帧最多 8 字节，收到 {len(values)} 字节")
        values = [value & 0xFF for value in values]
        return values + [0] * (8 - len(values))

    @staticmethod
    def _normalize_bytes(data: Any) -> List[int]:
        """字节数据规范化：list[int] / "C8 5A" / "C85A" / (200,90)。"""
        if isinstance(data, str):
            text = data.replace(",", " ").replace("0x", " ").strip()
            tokens = [token for token in text.split() if token]
            if len(tokens) == 1 and tokens[0]:
                chunk = tokens[0]
                return [int(chunk[i:i + 2], 16) for i in range(0, len(chunk), 2)]
            return [int(token, 16) for token in tokens]
        if isinstance(data, (list, tuple)):
            return [int(value) & 0xFF for value in data]
        raise AssertionError(f"[LIN] 无法解析字节数据 {data!r}")

    def _require_frame_dict(self, frame: Any, keyword_name: str) -> Dict[str, Any]:
        if not isinstance(frame, dict) or "id" not in frame:
            raise AssertionError(
                f"[LIN] {keyword_name} 需要帧字典参数（Wait For LIN Frame 的返回值），"
                f"收到 {type(frame).__name__}")
        return frame

    def _resolve_frame(self, frame_or_id: Any, timeout: float) -> Dict[str, Any]:
        """帧字典直接用；帧 ID 则等一帧新的。"""
        if isinstance(frame_or_id, dict):
            return frame_or_id
        return self._wait_frame(self._parse_frame_id(frame_or_id), float(timeout),
                                since_seq=None)

    def _load_ldf(self, path: Path) -> Dict[str, Any]:
        """加载/校验教学版 LDF（JSON）：必须有 frames 列表且每帧有 id/signals。"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AssertionError(f"[LIN] 读取 LDF 失败：{path}：{exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"[LIN] LDF 不是合法 JSON：{path}：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
            raise AssertionError(
                f"[LIN] LDF 结构不合法：{path} 缺少 frames 列表"
                f"（教学版 LDF 是 JSON，见文件头 _comment 说明）")
        for frame in data["frames"]:
            if "id" not in frame or not isinstance(frame.get("signals"), list):
                raise AssertionError(
                    f"[LIN] LDF 帧定义不完整（需要 id + signals 列表）：{frame}")
        info(f"[LinLibrary] 已加载 LDF：{path.name}，"
             f"帧数={len(data['frames'])}，"
             f"帧 ID={[hex(frame['id']) for frame in data['frames']]}")
        return data

    def _frame_definition(self, frame_id: int) -> Dict[str, Any]:
        for frame in self._ldf.get("frames", []):
            if int(frame["id"]) == int(frame_id):
                return frame
        known = [f"0x{int(frame['id']):02X}" for frame in self._ldf.get("frames", [])]
        raise AssertionError(
            f"[LIN] LDF（{self._ldf_path.name}）中不存在帧 0x{frame_id:02X}，"
            f"已知帧：{known} —— 检查帧 ID 是否写错，或 Set LDF File 换数据库")

    @staticmethod
    def _read_raw_bits(data: bytes, start_bit: int, length: int) -> int:
        """按 LIN 小端位序取原始位：bit0 = 首字节最低位（与 LDF 约定一致）。"""
        raw = 0
        for i in range(length):
            abs_bit = start_bit + i
            byte_index, bit_index = divmod(abs_bit, 8)
            if byte_index >= len(data):
                break
            if (data[byte_index] >> bit_index) & 1:
                raw |= (1 << i)
        return raw

    def _decode_frame(self, frame: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """按 LDF 解码一帧，返回 {"values": {信号: 物理值}, "labels": {信号: 枚举名}}。"""
        fid = int(frame["id"])
        defn = self._frame_definition(fid)
        data = bytes(int(byte) & 0xFF for byte in frame.get("data", []))
        values: Dict[str, Any] = {}
        labels: Dict[str, Any] = {}
        for signal in defn.get("signals", []):
            raw = self._read_raw_bits(data, int(signal["start_bit"]),
                                      int(signal["length"]))
            scale = float(signal.get("scale", 1) or 1)
            offset = float(signal.get("offset", 0) or 0)
            physical = round(raw * scale + offset, 6)
            values[signal["name"]] = physical
            enum_values = signal.get("values") or {}
            labels[signal["name"]] = enum_values.get(str(raw), str(physical))
        return {"values": values, "labels": labels}

    # ==================================================================
    # 杂项
    # ==================================================================
    def __del__(self) -> None:  # pragma: no cover - 兜底回收
        """对象销毁时兜底回收子进程（正常由 Suite Teardown 调 Stop）。"""
        try:
            self.stop_lin_simulation()
        except Exception:  # noqa: BLE001 - 解释器退出阶段不抛异常
            pass


if __name__ == "__main__":
    # 手工自测：python libraries/LinLibrary.py（不经过 Robot，打印一帧看看）
    library = LinLibrary()
    library.start_lin_simulation()
    try:
        marker = library.set_lin_slave_signal(0x10, rain_intensity=75)  # type: ignore
        frame = library.wait_for_lin_frame(0x10, timeout=2.0, since_seq=marker)  # type: ignore
        print("收到帧:", frame)
        library.lin_checksum_should_be_valid(frame)  # type: ignore
        print("LDF 解码:", library.decode_lin_frame_by_ldf(frame))  # type: ignore
        print("调度统计:", library.get_lin_schedule_statistics())  # type: ignore
    finally:
        library.stop_lin_simulation()
