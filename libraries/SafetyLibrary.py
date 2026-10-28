"""
SafetyLibrary.py — Robot Framework 功能安全机制验证关键字库
================================================================

把「功能安全安全机制」的验证能力包装成 RF 关键字，用来测 ``simulators/safety_ecu.py``
这个独立安全节点：

    E2E 端到端保护      Send Safety Frame / Inject E2E Error / E2E Error Counter Should Be
                        E2E Counter Should Advance / E2E Crc Should Be Valid
    看门狗              Inject Heartbeat Stop / Resume Heartbeat / Watchdog Should Trigger Degradation
    降级与安全状态      Safety Status Should Be / Safety Output Should Be Safe Value /
                        Safety Output Should Be Invalid / Clear Fault / Fault Flag Should Be
    进程管理            Start Safety Node / Stop Safety Node / Reset Safety Node

**独立验证原则（本库最重要的设计约束）**：
    安全机制不能"自己证明自己" —— 测试端不能 import 被测节点（``safety_ecu.py``）的
    CRC 函数或校验逻辑，否则被测实现有 bug 时测试也会跟着"通过"（两份一样的错误实现
    互相自证）。因此：

    1. 本文件**刻意重复实现**了 CRC-8/SAE-J1850（`crc8_sae_j1850`），不 import 被测代码；
    2. 用公开**校验值**自证实现正确：``crc8_sae_j1850(b"123456789") == 0x4B``
       （CRC-8/SAE-J1850 的标准 check value，见关键字 `Crc8 Should Be`）；
    3. 做**双向交叉验证**：测试端算的 CRC 被节点接受（发正常帧）、节点发回的安全帧
       被测试端独立重算（`E2E Crc Should Be Valid`）；
    4. 做**mutation 负向验证**：坏 CRC 帧必须被判失败（`Run Keyword And Expect Error`），
       证明校验不是"恒真"。

**连接方式**：安全节点是独立进程，通过 UDP 127.0.0.1:30600（可用环境变量
``SAFETY_UDP_PORT`` 覆盖）与本库做 JSON 请求/响应。端口可配、进程由套件 Teardown 回收。

典型用法（见 ``tests/robot/15_safety_mechanisms.robot``）::

    Suite Setup      Start Safety Node
    Suite Teardown   Stop Safety Node
    Test Setup       Reset Safety Node

    Send Safety Frame    steering_angle_deg=15.0
    Safety Status Should Be    NORMAL

    Inject E2E Error    crc
    E2E Error Counter Should Be    crc    1

    Inject Heartbeat Stop
    Watchdog Should Trigger Degradation
    Safety Output Should Be Invalid
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

ROOT = Path(__file__).resolve().parent.parent
SAFETY_SCRIPT = ROOT / "simulators" / "safety_ecu.py"
RESULTS_DIR = ROOT / "results"
DEFAULT_UDP_PORT = 30600

# ---------------------------------------------------------------------------
# E2E 帧协议定义（教学版 AUTOSAR E2E Profile 1）
#   本文件只共享"协议定义"（ID/长度/计数器范围），**不共享实现**（CRC/校验逻辑）。
#   byte 0..1  dataId (uint16 BE)  0x5A01=控制命令帧  0x5A02=安全输出帧
#   byte 2     counter 0..14 循环
#   byte 3..4  data: 转向角 int16 BE（0.1° 单位）
#   byte 5     status: 0=正常 1=安全值 2=无效
#   byte 6     fault_level: 0/1/2
#   byte 7     CRC-8/SAE-J1850（覆盖 byte0..6）
# ---------------------------------------------------------------------------
CONTROL_DATA_ID = 0x5A01
OUTPUT_DATA_ID = 0x5A02
FRAME_LENGTH = 8
COUNTER_MOD = 15
DEFAULT_WATCHDOG_TIMEOUT_MS = 300

STATES = ("NORMAL", "DEGRADED", "SAFE_STATE")
FAULT_FLAG_NAMES = ("E2E_CRC_ERROR", "E2E_COUNTER_ERROR", "E2E_REPLAY", "WATCHDOG")


def crc8_sae_j1850(data: bytes) -> int:
    """CRC-8/SAE-J1850（AUTOSAR E2E Profile 1 使用的 CRC）。

    **这是测试端的独立实现**：刻意与 ``simulators/safety_ecu.py`` 的同名函数
    重复实现，不 import 被测代码 —— 安全机制验证必须独立（理由见模块 docstring）。

    参数：poly=0x1D、init=0xFF、xorout=0xFF、不反射（MSB first）。
    公开校验值：``crc8_sae_j1850(b"123456789") == 0x4B``。
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0xFF


class SafetyLibrary:
    """功能安全机制验证关键字集合（SUITE scope：一个套件一个安全节点进程）。"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, port=None) -> None:
        self._port = int(port if port is not None
                         else os.environ.get("SAFETY_UDP_PORT", DEFAULT_UDP_PORT))
        self._sock: socket.socket | None = None
        self._proc: subprocess.Popen | None = None
        self._log_handle = None
        self._stdout_path: Path | None = None
        self._watchdog_timeout_ms = DEFAULT_WATCHDOG_TIMEOUT_MS
        # 发送端（测试仪）自己的 E2E 计数器状态：真实发送端也要维护 0~14 计数器
        self._tx_counter = 0
        self._last_normal_frame: str | None = None
        self._last_status: dict | None = None
        info(f"[SafetyLibrary] 已加载：安全节点 UDP 127.0.0.1:{self._port}"
             f"（进程在 Start Safety Node 时启动）")

    # ==================================================================
    # 一、进程与连接管理
    # ==================================================================
    @keyword("Start Safety Node")
    def start_safety_node(self, watchdog_timeout_ms=DEFAULT_WATCHDOG_TIMEOUT_MS,
                          wait_timeout=10.0) -> None:
        """拉起独立安全节点进程并等待其就绪，然后复位到干净状态。

        参数：
            watchdog_timeout_ms  看门狗超时阈值（默认 300ms，对应需求"心跳停止
                                 超过 300ms 进入 SAFE_STATE"）
            wait_timeout         等待节点就绪的最长时间（秒）

        实现要点：
            - 用 ``sys.executable``（当前跑 robot 的 Python）启动子进程，**不硬编码
              解释器路径** —— 换机器/CI 都能跑；
            - 子进程 stdout/stderr 重定向到 ``results/safety_ecu_stdout.log``，
              节点日志不污染 RF 控制台，但失败时能直接查看；
            - 就绪判定用 ``ping`` 命令轮询（不是 sleep 猜时间）；
            - 启动后立刻 ``reset`` 一次：保证用例从 NORMAL/计数器 0 开始。
        """
        if self._proc is not None and self._proc.poll() is None:
            info("安全节点已在运行，复用现有进程")
            return
        if not SAFETY_SCRIPT.exists():
            raise AssertionError(f"找不到安全节点脚本：{SAFETY_SCRIPT}")
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._stdout_path = RESULTS_DIR / "safety_ecu_stdout.log"
        env = os.environ.copy()
        env["SAFETY_UDP_PORT"] = str(self._port)
        self._log_handle = open(self._stdout_path, "w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, str(SAFETY_SCRIPT),
             "--port", str(self._port),
             "--watchdog-timeout-ms", str(int(watchdog_timeout_ms))],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        self._watchdog_timeout_ms = int(watchdog_timeout_ms)
        info(f"已启动安全节点进程：PID={self._proc.pid} "
             f"端口={self._port} 看门狗={self._watchdog_timeout_ms}ms")

        deadline = time.monotonic() + float(wait_timeout)
        while True:
            if self._proc.poll() is not None:
                raise AssertionError(
                    "安全节点进程启动后立即退出（exit code "
                    f"{self._proc.returncode}），节点日志：\n{self._log_tail()}")
            try:
                self._request({"cmd": "ping"}, timeout=0.3)
                break
            except AssertionError:
                if time.monotonic() > deadline:
                    raise AssertionError(
                        f"安全节点 {wait_timeout}s 内未就绪（端口 {self._port} 可能被占用），"
                        f"节点日志：\n{self._log_tail()}")
                time.sleep(0.1)
        self._request({"cmd": "reset"})
        self._tx_counter = 0
        self._last_normal_frame = None
        self._last_status = None
        console(f"[SafetyLibrary] 安全节点已就绪：127.0.0.1:{self._port}"
                f"（PID {self._proc.pid}，看门狗 {self._watchdog_timeout_ms}ms）")

    @keyword("Stop Safety Node")
    def stop_safety_node(self) -> None:
        """发送 quit 并回收安全节点进程（套件 Teardown 调用）。

        回收顺序：先发 quit 让节点优雅退出（把统计/日志 flush 完）→ 等 3s →
        仍在就杀（terminate→kill）→ 关闭 socket 与日志句柄。
        **进程必须回收干净**：否则下个套件/下次运行会因端口占用找不到节点。
        """
        if self._proc is not None:
            try:
                self._request({"cmd": "quit"}, timeout=0.5, expect_ok=False)
            except AssertionError:
                pass
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=3.0)
            info(f"安全节点进程已回收（exit code {self._proc.returncode}）")
            self._proc = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @keyword("Reset Safety Node")
    def reset_safety_node(self) -> None:
        """全量复位安全节点（状态 NORMAL、计数器归零、心跳恢复），用于用例隔离。

        为什么需要：安全节点是被多个用例共享的**有状态**系统（降级后要恢复、
        计数器要连续），不做隔离就会出现"单跑能过、连跑挂"的经典问题。
        本关键字在 Test Setup/Teardown 里调用，保证每个用例从同一初始状态开始。
        """
        status = self._request({"cmd": "reset"})
        self._tx_counter = 0
        self._last_normal_frame = None
        self._last_status = status
        if status.get("state") != "NORMAL":
            raise AssertionError(f"复位后状态异常：{status.get('state')}（{status}）")
        info("安全节点已复位：NORMAL / 计数器=0 / 心跳已恢复 / 故障标志清零")

    def _log_tail(self, lines: int = 20) -> str:
        """取节点日志末尾若干行（启动失败时给排查线索）。"""
        if self._stdout_path is None or not self._stdout_path.exists():
            return "(没有节点日志)"
        try:
            text = self._stdout_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return "(读取节点日志失败)"

    # ==================================================================
    # 二、UDP 通信内部工具
    # ==================================================================
    def _ensure_socket(self) -> None:
        """懒建 UDP 客户端 socket（绑定本机随机端口，超时 3s）。

        Windows 特有坑：向"还没有进程监听"的 UDP 端口发包会收到 ICMP 端口不可达，
        之后 recvfrom 可能抛 ``ConnectionResetError(10054)`` 而不是 timeout。
        Python 的 ``socket.ioctl`` 不支持 SIO_UDP_CONNRESET 开关，因此在 _request
        里把该异常翻译成"节点未就绪"，由 Start Safety Node 的轮询重试兜住。
        """
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3.0)
        self._sock = sock

    def _request(self, payload: dict, timeout: float = 3.0,
                 expect_ok: bool = True) -> dict:
        """发一条 JSON 命令并等响应（同步请求/响应，一条一条来）。"""
        self._ensure_socket()
        cmd = payload.get("cmd", "?")
        try:
            self._sock.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              ("127.0.0.1", self._port))
        except OSError as exc:
            raise AssertionError(
                f"发送命令 {cmd} 到安全节点失败（127.0.0.1:{self._port}）：{exc}"
                f" —— 节点进程是否在运行？") from exc
        deadline = time.monotonic() + float(timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"安全节点 {timeout}s 内没有响应命令 {cmd}"
                    f"（127.0.0.1:{self._port}）—— 检查节点进程/端口/防火墙")
            self._sock.settimeout(remaining)
            try:
                raw, _addr = self._sock.recvfrom(65535)
            except socket.timeout as exc:
                raise AssertionError(
                    f"安全节点 {timeout}s 内没有响应命令 {cmd}"
                    f"（127.0.0.1:{self._port}）") from exc
            except ConnectionResetError as exc:
                raise AssertionError(
                    f"安全节点未就绪（命令 {cmd}）：{self._port} 端口没有进程监听"
                    f"（节点可能尚未启动或已退出）") from exc
            except OSError as exc:
                raise AssertionError(
                    f"与安全节点通信失败（命令 {cmd}）：{exc}") from exc
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if expect_ok and not response.get("ok", False):
                raise AssertionError(f"安全节点返回错误：{response}")
            return response

    # ==================================================================
    # 三、E2E 发送（正常帧 / 故障注入）
    # ==================================================================
    @keyword("Send Safety Frame")
    def send_safety_frame(self, steering_angle_deg=0.0, status=0) -> dict:
        """构造并发送一帧**合法** E2E 安全报文（CRC 与计数器都正确），返回节点处理结果。

        参数：
            steering_angle_deg  转向角（度，0.1° 精度；节点会把它透传为输出）
            status              数据区状态字节（0=正常）

        返回 dict：``{"accepted": bool, "reason": str, "counter": int,
        "state": str, "fault_level": int, "frame": hex, "sent_counter": int, ...}``

        实现说明：测试端（发送方）自己维护 0~14 的 E2E 计数器，每发一帧 +1；
        CRC 由本库**独立实现**计算 —— 节点用它的实现校验，形成双向交叉验证。
        """
        response = self._send_valid_frame(steering_angle_deg, status=status)
        info(f"发送 E2E 安全帧：counter={response['sent_counter']} "
             f"angle={float(steering_angle_deg):.1f}° crc=0x{response['crc']:02X} "
             f"→ 节点判定 {response['reason']}（accepted={response['accepted']}，"
             f"state={response['state']}）")
        return response

    def _send_valid_frame(self, steering_angle_deg=0.0, status=0) -> dict:
        """内部：发一帧合法 E2E 帧（不打印逐帧日志，供批量/自动恢复场景用）。"""
        counter = self._tx_counter
        frame = self._build_frame(CONTROL_DATA_ID, counter, steering_angle_deg,
                                  status=status)
        response = self._request({"cmd": "e2e_frame", "frame": frame.hex()})
        self._tx_counter = (counter + 1) % COUNTER_MOD
        self._last_normal_frame = frame.hex()
        response.update({
            "frame": frame.hex(),
            "sent_counter": counter,
            "crc": frame[-1],
            "steering_angle_deg": float(steering_angle_deg),
        })
        return response

    @keyword("Inject E2E Error")
    def inject_e2e_error(self, kind="crc", jump=5) -> dict:
        """向安全节点注入一条**非法** E2E 报文，返回节点判定结果（用于负向验证）。

        参数：
            kind  ``crc``      CRC 错误帧（计数器正常，CRC 字节取反）
                  ``counter``  计数器跳变帧（跳过 jump-1 帧，delta=jump）
                  ``replay``   重放帧（把上一帧正常报文原样再发一次，counter 重复）
            jump  counter 注入时的跳变步长（delta，默认 5 → 丢失 4 帧，超过阈值 3）

        返回 dict 同 `Send Safety Frame`，另有 ``kind`` 字段。

        为什么故障注入要"构造非法帧"而不是改节点配置：真实安全机制的价值就在于
        **面对坏数据时的行为**；注入到通信链路上才是真正端到端的验证（这也是
        HIL 台架上用 CANoe/CAPL 发错误帧的同一思路）。
        """
        kind_normalized = str(kind).strip().lower()
        if kind_normalized in ("crc", "crc_error"):
            counter = self._tx_counter
            frame = self._build_frame(CONTROL_DATA_ID, counter, 0.0)
            # CRC 字节取反：保证与正确 CRC 不同，模拟传输位翻转
            bad_frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
            response = self._request({"cmd": "e2e_frame", "frame": bad_frame.hex()})
            self._tx_counter = (counter + 1) % COUNTER_MOD
            response.update({"frame": bad_frame.hex(), "sent_counter": counter,
                             "crc": bad_frame[-1], "kind": "crc"})
        elif kind_normalized in ("counter", "counter_jump", "jump"):
            step = int(jump)
            if not 2 <= step < COUNTER_MOD:
                raise AssertionError(
                    f"jump 必须在 2..{COUNTER_MOD - 1} 之间（delta 步长），收到 {jump}")
            # 上一帧正常 counter 为 _tx_counter-1；跳变后 delta = step
            counter = (self._tx_counter + step - 1) % COUNTER_MOD
            frame = self._build_frame(CONTROL_DATA_ID, counter, 0.0)
            response = self._request({"cmd": "e2e_frame", "frame": frame.hex()})
            self._tx_counter = (counter + 1) % COUNTER_MOD
            response.update({"frame": frame.hex(), "sent_counter": counter,
                             "crc": frame[-1], "kind": "counter", "jump": step})
        elif kind_normalized in ("replay", "duplicate"):
            if self._last_normal_frame is None:
                raise AssertionError("还没有发送过正常帧，无法重放")
            response = self._request({"cmd": "e2e_frame",
                                      "frame": self._last_normal_frame})
            # 重放不推进计数器（它本来就是"旧帧"）
            response.update({"frame": self._last_normal_frame,
                             "sent_counter": None, "crc": None, "kind": "replay"})
        else:
            raise AssertionError(
                f"未知注入类型 {kind}（支持 crc / counter / replay）")
        info(f"注入 E2E 错误：kind={response['kind']} "
             f"frame={response['frame']} → 节点判定 "
             f"accepted={response['accepted']} reason={response['reason']} "
             f"error_streak={response.get('error_streak')}")
        return response

    @staticmethod
    def _build_frame(data_id: int, counter: int, steering_angle_deg=0.0,
                     status: int = 0, fault_level: int = 0) -> bytes:
        """按 E2E Profile 1 打包 8 字节安全帧（测试端独立实现 CRC）。"""
        raw = max(-32768, min(32767, int(round(float(steering_angle_deg) * 10.0))))
        header = (
            int(data_id).to_bytes(2, "big")
            + bytes([int(counter) % COUNTER_MOD])
            + raw.to_bytes(2, "big", signed=True)
            + bytes([int(status) & 0xFF, int(fault_level) & 0xFF])
        )
        return header + bytes([crc8_sae_j1850(header)])

    # ==================================================================
    # 四、状态读取与断言
    # ==================================================================
    @keyword("Read Safety Status")
    def read_safety_status(self) -> dict:
        """读取安全节点完整状态，返回 dict（结构见 docs/32 的关键字说明）。

        关键字段::

            {'state': 'NORMAL'|'DEGRADED'|'SAFE_STATE',
             'fault_level': 0|1|2,
             'fault_flags': {'E2E_CRC_ERROR': bool, 'E2E_COUNTER_ERROR': bool,
                             'E2E_REPLAY': bool, 'WATCHDOG': bool},
             'output': {'valid': bool, 'steering_angle_deg': float|None,
                        'status': 0|1|2, 'mode': 'NORMAL'|'SAFE_VALUE'|'INVALID'},
             'e2e': {'crc_errors': int, 'counter_errors': int, 'replays': int,
                     'lost_frames': int, 'last_rx_counter': int,
                     'last_tx_frame': '<hex>'（节点发出的安全状态帧，供独立验 CRC）,
                     ...},
             'watchdog': {'timeout_ms', 'reaction_ms', 'last_heartbeat_age_ms', ...},
             'config': {...}}
        """
        self._last_status = self._request({"cmd": "status"})
        return self._last_status

    @keyword("Safety Status Should Be")
    def safety_status_should_be(self, expected_state,
                                timeout: float = 2.0) -> float:
        """轮询断言安全节点状态机处于期望状态（NORMAL/DEGRADED/SAFE_STATE）。

        参数：
            expected_state  NORMAL / DEGRADED / SAFE_STATE
            timeout         轮询超时（秒）—— 降级/看门狗触发是**异步**的，
                            必须给节点时间反应（不是 sleep 猜，而是轮询观测）

        返回：从开始轮询到观测到期望状态的耗时（毫秒）。
        """
        expected = str(expected_state).strip().upper()
        if expected not in STATES:
            raise AssertionError(f"未知状态 {expected_state}，可选 {STATES}")
        started = time.monotonic()
        deadline = started + float(timeout)
        last: dict = {}
        while True:
            last = self.read_safety_status()
            if last.get("state") == expected:
                elapsed_ms = (time.monotonic() - started) * 1000.0
                info(f"状态机 = {expected}（观测耗时 {elapsed_ms:.0f}ms，"
                     f"fault_level={last.get('fault_level')}，"
                     f"error_streak={last.get('e2e', {}).get('error_streak')}）")
                return round(elapsed_ms, 1)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        raise AssertionError(
            f"{timeout}s 内状态不是 {expected}，实际 {last.get('state')}"
            f"（fault_level={last.get('fault_level')}，"
            f"fault_flags={last.get('fault_flags')}，"
            f"error_streak={last.get('e2e', {}).get('error_streak')}，"
            f"watchdog={last.get('watchdog')}）")

    @keyword("Fault Flag Should Be")
    def fault_flag_should_be(self, flag, expected=True) -> None:
        """断言某故障标志的当前值（DTC 风格的 fault flag）。

        参数：
            flag      E2E_CRC_ERROR / E2E_COUNTER_ERROR / E2E_REPLAY / WATCHDOG
            expected  True（故障激活）/ False（故障已清除）
        """
        name = str(flag).strip().upper()
        if name not in FAULT_FLAG_NAMES:
            raise AssertionError(f"未知故障标志 {flag}，可选 {FAULT_FLAG_NAMES}")
        expected_bool = self._as_bool(expected)
        status = self.read_safety_status()
        actual = bool(status.get("fault_flags", {}).get(name, False))
        if actual != expected_bool:
            raise AssertionError(
                f"故障标志 {name} = {actual}，期望 {expected_bool}"
                f"（当前状态 {status.get('state')}，全部标志 {status.get('fault_flags')}）")
        info(f"故障标志 {name} = {actual} ✓")

    @keyword("Safety Output Should Be Safe Value")
    def safety_output_should_be_safe_value(self) -> None:
        """断言安全节点输出为"安全值"：转向角回 0、输出仍有效、模式 SAFE_VALUE。

        这是 DEGRADED 状态的核心行为：检测到故障后，执行器不能乱动，
        必须输出**预定义的安全值**（本项目约定转向角 0 = 回正）。
        """
        status = self.read_safety_status()
        output = status.get("output", {})
        if not output.get("valid"):
            raise AssertionError(f"期望安全值输出（valid=True），实际输出无效：{output}")
        angle = output.get("steering_angle_deg")
        if angle is None or abs(float(angle) - 0.0) > 1e-9:
            raise AssertionError(
                f"期望安全值（转向角=0.0°），实际 {angle}（输出 {output}）")
        if output.get("mode") != "SAFE_VALUE":
            raise AssertionError(f"期望输出模式 SAFE_VALUE，实际 {output.get('mode')}")
        info(f"输出为安全值：转向角 {angle}°（valid={output['valid']}，"
             f"mode={output['mode']}，fault_level={status.get('fault_level')}）")

    @keyword("Safety Output Should Be Invalid")
    def safety_output_should_be_invalid(self) -> None:
        """断言安全节点输出**无效**（SAFE_STATE 的核心行为：不再向执行器给值）。"""
        status = self.read_safety_status()
        output = status.get("output", {})
        if output.get("valid"):
            raise AssertionError(f"期望输出无效（valid=False），实际 {output}")
        if output.get("steering_angle_deg") is not None:
            raise AssertionError(
                f"输出无效时转向角应为 None，实际 {output.get('steering_angle_deg')}")
        if output.get("mode") != "INVALID":
            raise AssertionError(f"期望输出模式 INVALID，实际 {output.get('mode')}")
        info(f"输出无效（mode=INVALID，fault_level={status.get('fault_level')}）✓")

    @keyword("Clear Fault")
    def clear_fault(self) -> dict:
        """清除故障标志与降级状态（手动恢复路径），返回清故障后的状态。

        模拟真实诊断仪的 **0x14 ClearDiagnosticInformation**：故障源消失后，
        需要显式清除故障记忆，系统才回到 NORMAL（本项目同时支持"连续 N 帧
        合法数据自动恢复"，两条恢复路径都测，见用例 6）。
        """
        status = self._request({"cmd": "clear_fault"})
        info(f"已清除故障标志：state={status.get('state')}，"
             f"fault_level={status.get('fault_level')}，"
             f"fault_flags={status.get('fault_flags')}")
        return status

    # ==================================================================
    # 五、E2E 计数器 / 错误计数 / 独立 CRC 验证
    # ==================================================================
    @keyword("E2E Counter Should Advance")
    def e2e_counter_should_advance(self, count=16) -> list:
        """连续发送 count 帧合法报文，验证 E2E 计数器逐帧 +1、14 后回 0。

        参数：
            count  连续发送帧数（默认 16：覆盖 0~14 一整圈 + 回绕到 0）

        返回：节点确认的计数器序列（list[int]），如 ``[0,1,...,14,0]``。

        E2E 计数器的意义：接收端靠它发现**丢帧**(delta>1)、**重复/重放**(delta=0)；
        计数器不按规则走 = 通信序列不可信，安全机制必须能识别。
        """
        total = int(count)
        if total < 2:
            raise AssertionError(f"count 必须 ≥2，收到 {count}")
        sequence: list[int] = []
        start = self._tx_counter
        for _ in range(total):
            response = self._send_valid_frame(steering_angle_deg=0.0)
            sent = response["sent_counter"]
            if not response.get("accepted"):
                raise AssertionError(
                    f"计数器验证期间发送的合法帧被拒绝：{response}")
            if int(response.get("counter", -1)) != int(sent):
                raise AssertionError(
                    f"节点记录的接收计数器 {response.get('counter')} ≠ 发送的 {sent}"
                    f"（{response}）")
            sequence.append(int(sent))
        expected = [(start + i) % COUNTER_MOD for i in range(total)]
        if sequence != expected:
            raise AssertionError(f"计数器序列错误：{sequence} ≠ 期望 {expected}")
        wrapped = any(sequence[i] > sequence[i + 1] for i in range(len(sequence) - 1))
        if total >= COUNTER_MOD and not wrapped:
            raise AssertionError(f"{total} 帧应出现 14→0 回绕，实际序列 {sequence}")
        info(f"E2E 计数器递增/循环验证通过（{total} 帧）：{sequence}")
        return sequence

    @keyword("E2E Error Counter Should Be")
    def e2e_error_counter_should_be(self, kind, expected) -> None:
        """断言某一类 E2E 错误计数等于期望值。

        参数：
            kind      crc（CRC 错误）/ counter（计数器跳变）/ replay（重放）/
                      lost（累计丢失帧数）/ total（错误总数）/ bad（坏帧）
            expected  期望计数（整数）
        """
        mapping = {
            "crc": ("crc_errors", "CRC 错误"),
            "crc_error": ("crc_errors", "CRC 错误"),
            "counter": ("counter_errors", "计数器跳变错误"),
            "counter_error": ("counter_errors", "计数器跳变错误"),
            "replay": ("replays", "重放帧"),
            "replays": ("replays", "重放帧"),
            "lost": ("lost_frames", "累计丢失帧"),
            "lost_frames": ("lost_frames", "累计丢失帧"),
            "total": ("total_errors", "E2E 错误总数"),
            "bad": ("bad_frames", "非法格式帧"),
        }
        key = str(kind).strip().lower()
        if key not in mapping:
            raise AssertionError(
                f"未知错误类型 {kind}，可选 {sorted(set(mapping))}")
        field, label = mapping[key]
        status = self.read_safety_status()
        actual = int(status.get("e2e", {}).get(field, -1))
        target = int(expected)
        if actual != target:
            raise AssertionError(
                f"{label}计数 = {actual}，期望 {target}"
                f"（e2e 统计 {status.get('e2e')}）")
        info(f"{label}计数 = {actual} ✓")

    @keyword("E2E Crc Should Be Valid")
    def e2e_crc_should_be_valid(self, frame_hex,
                                expected_data_id=None) -> dict:
        """用**测试端独立实现**重算帧 CRC 并校验，返回解码后的帧字段。

        参数：
            frame_hex           8 字节 E2E 帧（十六进制，可带空格）
            expected_data_id    可选的期望 dataId（如 0x5A02 安全输出帧）

        返回 dict：``{'data_id', 'counter', 'steering_angle_deg', 'status',
        'fault_level', 'crc'}``

        **这是 REQ-SAFE-005（独立验证）的核心关键字**：若直接 import 被测节点的
        校验函数，被测实现有 bug 时测试也会"通过"；本关键字用本库独立实现的
        CRC 重算，并与帧内 CRC 比对。失败信息固定包含 "CRC 校验失败"
        （便于用 ``Run Keyword And Expect Error    *CRC 校验失败*`` 做 mutation 验证）。
        """
        try:
            frame = bytes.fromhex(str(frame_hex).replace(" ", ""))
        except ValueError as exc:
            raise AssertionError(f"帧十六进制非法：{frame_hex}（{exc}）") from exc
        if len(frame) != FRAME_LENGTH:
            raise AssertionError(
                f"E2E 帧长度应为 {FRAME_LENGTH} 字节，实际 {len(frame)}：{frame.hex()}")
        calculated = crc8_sae_j1850(frame[0:7])
        received = frame[7]
        if calculated != received:
            raise AssertionError(
                f"CRC 校验失败：帧内 CRC=0x{received:02X}，测试端独立重算="
                f"0x{calculated:02X}（帧 {frame.hex()}）")
        data_id = int.from_bytes(frame[0:2], "big")
        counter = frame[2]
        raw_angle = int.from_bytes(frame[3:5], "big", signed=True)
        decoded = {
            "data_id": f"0x{data_id:04X}",
            "data_id_raw": data_id,
            "counter": counter,
            "steering_angle_deg": raw_angle / 10.0,
            "status": frame[5],
            "fault_level": frame[6],
            "crc": f"0x{calculated:02X}",
        }
        if counter >= COUNTER_MOD:
            raise AssertionError(
                f"counter={counter} 超出 E2E Profile 1 合法范围 0..{COUNTER_MOD - 1}"
                f"（帧 {frame.hex()}）")
        if expected_data_id is not None:
            expected = int(str(expected_data_id), 0)
            if data_id != expected:
                raise AssertionError(
                    f"帧 dataId=0x{data_id:04X} ≠ 期望 0x{expected:04X}")
        info(f"E2E 帧独立 CRC 重算通过：{frame.hex()} CRC=0x{calculated:02X} "
             f"dataId=0x{data_id:04X} counter={counter} "
             f"angle={decoded['steering_angle_deg']}° fault_level={frame[6]}")
        return decoded

    @keyword("Crc8 Should Be")
    def crc8_should_be(self, data_hex, expected) -> int:
        """断言测试端独立 CRC 实现对给定数据的计算结果（用于对照公开 check value）。

        标准 check value：CRC-8/SAE-J1850 对 ASCII ``"123456789"``（十六进制
        ``313233343536373839``）的计算结果为 **0x4B**。这条断言证明测试端的
        CRC 实现本身没有写错 —— 否则"独立重算"也只是另一个错误实现。
        """
        try:
            data = bytes.fromhex(str(data_hex).replace(" ", ""))
        except ValueError as exc:
            raise AssertionError(f"数据十六进制非法：{data_hex}") from exc
        expected_int = int(str(expected), 0)
        actual = crc8_sae_j1850(data)
        if actual != expected_int:
            raise AssertionError(
                f"CRC 计算结果 0x{actual:02X} ≠ 期望 0x{expected_int:02X}"
                f"（数据 {data.hex()}）—— 测试端独立 CRC 实现可能有误")
        info(f"独立 CRC 实现对照通过：crc8({data.hex()}) = 0x{actual:02X}")
        return actual

    # ==================================================================
    # 六、看门狗注入与验证
    # ==================================================================
    @keyword("Inject Heartbeat Stop")
    def inject_heartbeat_stop(self) -> dict:
        """注入"内部任务卡死"：停止安全节点的心跳任务。

        真实场景对应两种故障：① 任务卡死（死循环/阻塞）；② 任务跑飞不再喂狗。
        测试端无法直接让被测任务卡死时，通常用**停止喂狗**等价模拟（HIL 台架上
        还有"冻结任务/断点"等手段）。停止后看门狗应在阈值（300ms）后触发 SAFE_STATE。
        """
        status = self._request({"cmd": "stop_heartbeat"})
        info(f"已注入心跳停止：heartbeat_enabled="
             f"{status.get('watchdog', {}).get('heartbeat_enabled')}，"
             f"看门狗阈值 {status.get('watchdog', {}).get('timeout_ms')}ms")
        return status

    @keyword("Resume Heartbeat")
    def resume_heartbeat(self) -> dict:
        """恢复心跳（注入"任务恢复"），安全节点应自动退出 SAFE_STATE 回到 NORMAL。"""
        status = self._request({"cmd": "resume_heartbeat"})
        info(f"已恢复心跳：heartbeat_enabled="
             f"{status.get('watchdog', {}).get('heartbeat_enabled')}")
        return status

    @keyword("Watchdog Should Trigger Degradation")
    def watchdog_should_trigger_degradation(self, timeout: float = 2.0) -> float:
        """等待并断言看门狗已触发：状态 SAFE_STATE、fault_level=2、WATCHDOG 标志置位。

        参数：
            timeout  等待超时（秒）

        返回：节点自报的**看门狗反应时间**（毫秒，= 触发时心跳年龄）。
        断言反应时间 ≥ 阈值×0.8：太早说明触发条件不对（比如把心跳超时判定写错）。
        正常情况下应在 300~400ms 之间（阈值 300ms + 巡检/线程调度）。
        """
        started = time.monotonic()
        deadline = started + float(timeout)
        status: dict = {}
        while True:
            status = self.read_safety_status()
            if status.get("state") == "SAFE_STATE":
                break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"{timeout}s 内看门狗未触发 SAFE_STATE（当前 state="
                    f"{status.get('state')}，心跳年龄 "
                    f"{status.get('watchdog', {}).get('last_heartbeat_age_ms')}ms，"
                    f"阈值 {status.get('watchdog', {}).get('timeout_ms')}ms）")
            time.sleep(0.02)
        reaction_ms = status.get("watchdog", {}).get("reaction_ms")
        timeout_ms = float(status.get("watchdog", {}).get("timeout_ms") or 0)
        if reaction_ms is None:
            raise AssertionError(f"看门狗已触发但没有记录反应时间：{status.get('watchdog')}")
        if float(reaction_ms) < timeout_ms * 0.8:
            raise AssertionError(
                f"看门狗反应时间 {reaction_ms}ms 早于阈值 {timeout_ms}ms 的 80%"
                f" —— 触发条件可能写错了")
        if int(status.get("fault_level", -1)) != 2:
            raise AssertionError(
                f"SAFE_STATE 的 fault_level 应为 2，实际 {status.get('fault_level')}")
        if not status.get("fault_flags", {}).get("WATCHDOG"):
            raise AssertionError(
                f"SAFE_STATE 应置位 WATCHDOG 故障标志，实际 {status.get('fault_flags')}")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        info(f"看门狗按预期触发：节点反应 {reaction_ms}ms（阈值 {timeout_ms:.0f}ms），"
             f"注入后 {elapsed_ms:.0f}ms 观测到 SAFE_STATE")
        return float(reaction_ms)

    # ==================================================================
    # 内部工具
    # ==================================================================
    @staticmethod
    def _as_bool(value) -> bool:
        """把 RF 传入的 True/False（也可能是字符串 'True'/'0'）归一成 bool。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "y", "on"):
            return True
        if text in ("false", "0", "no", "n", "off", ""):
            return False
        raise AssertionError(f"无法把 {value!r} 解析成布尔值")
