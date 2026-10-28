"""
HilLibrary.py — Robot Framework HIL 台架与电源/IO 验证关键字库
================================================================

把「HIL 台架 IO 板 + 程控电源」的访问封装成 RF 关键字，并**抽象出后端接口**
（教学重点：测试脚本不要绑死某一家的台架）：

    Start HIL Rack / Stop HIL Rack / Reset Rack    台架进程生命周期（用例隔离）
    Power On Sequence / Power Off Sequence          上下电时序（KL30→KL15 / KL15→KL30）
    Set Kl30 Voltage / Get Kl30 Voltage / Kl30 Voltage Should Be
    Inject Under Voltage / Inject Over Voltage / Inject Power Loss / Inject Power Glitch
    Power Cycle                                     硬件断电重启（继电器断开-恢复）
    Set Digital Input / Get Digital Input           DI 激励（车门/安全带/刹车/档位）
    Set Digital Output / Get Digital Output         DO 读写（主继电器/指示灯/风扇）
    Set Relay                                       控制 ECU 供电通断等继电器
    Set Load Current / Over Current Should Be Flagged  电流与过流保护
    Read Rack State / Rack State Should Contain     完整状态读回与断言
    Backend Should Be                               后端抽象：simulated / real

**后端抽象（本库最重要的设计）**：

    HIL_BACKEND=sim   （默认）→ SimulatedBackend：拉起 ``simulators/hil_rack.py`` 子进程，
                        通过 TCP 127.0.0.1:9090 的 JSON 行协议做 IO 操作；
    HIL_BACKEND=real          → RealBackend：**明确报"未实现 / 需接真实台架"**。
                        接 dSPACE / Vector VT System / 自研 IO 板时，只需要实现
                        ``RealBackend.command()``：把统一的 JSON 命令映射成台架
                        SDK / SCPI / Modbus / CANoe COM 调用，**测试用例一行不改**。

    没有抽象层会怎样：脚本里到处写 ``vt7001.set_voltage(...)`` / ``ds.set_io(...)``，
    换一家台架就要重写所有用例。抽象层把"用例想做什么"（设电压/上电/读 DI）和
    "台架怎么做"（厂商 API）彻底分开 —— 这就是 HIL 自动化中最常见的可移植性设计。

连接方式：TCP ``127.0.0.1:9090``（端口可用 env ``HIL_PORT`` 或构造参数覆盖），
JSON 行协议请求/响应。台架进程由 ``Start HIL Rack`` 拉起、``Stop HIL Rack`` 回收，
stdout 重定向到 ``results/hil_rack_stdout.log``。

典型用法（见 ``tests/robot/20_hil_rack.robot``）::

    Suite Setup      Start HIL Rack
    Suite Teardown   Stop HIL Rack
    Test Setup       Reset Rack

    ${state}=    Power On Sequence    delay_ms=100
    Kl30 Voltage Should Be    12.6
    Set Digital Input    DOOR_OPEN    True
    Rack State Should Contain    di.DOOR_OPEN    True
    Power Cycle
    Backend Should Be    simulated
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

ROOT = Path(__file__).resolve().parent.parent
HIL_SCRIPT = ROOT / "simulators" / "hil_rack.py"
RESULTS_DIR = ROOT / "results"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9090
KL30_NOMINAL_V = 12.6
CURRENT_LIMIT_A = 5.0

FAIL_BACKEND = "HIL_BACKEND 只支持 sim|real（当前 {value}）"

# real 后端的统一提示：既说明"没实现"，也给出"怎么接真实台架"的下一步
REAL_BACKEND_MESSAGE = (
    "real 后端未实现：需接真实台架（dSPACE SCALEXIO / Vector VT System / 自研 IO 板）。\n"
    "  替换点：实现 libraries/HilLibrary.py 的 RealBackend.command()，把统一命令\n"
    "  （set_kl30/power_on/power_cycle/set_di/set_do/set_relay/...）映射到台架 SDK、\n"
    "  SCPI 或 Modbus；再用 HIL_BACKEND=real + HIL_RACK_ENDPOINT 指向真实台架即可，\n"
    "  测试脚本与用例不需要改动。当前为纯软件仿真环境，请使用 HIL_BACKEND=sim。"
)


# ===========================================================================
# 后端抽象：所有台架操作都走这三个方法（connect / command / close）
# ===========================================================================
class HilBackend(ABC):
    """台架后端接口：测试关键字只依赖这个抽象，不依赖具体台架厂商。"""

    name: str = "abstract"

    @abstractmethod
    def connect(self) -> None:
        """建立与台架的连接（真实台架=初始化 SDK/打开 TCP；仿真=拉起子进程）。"""

    @abstractmethod
    def command(self, payload: dict, timeout: float = 5.0) -> dict:
        """执行一条台架命令并返回响应 dict（统一 JSON 语义）。"""

    @abstractmethod
    def close(self) -> None:
        """释放连接/进程资源。"""


class SimulatedBackend(HilBackend):
    """仿真台架后端：拉起 ``simulators/hil_rack.py`` 子进程，走 TCP JSON 行协议。"""

    name = "simulated"

    def __init__(self, port: int = DEFAULT_PORT, wait_timeout: float = 10.0) -> None:
        self._port = int(port)
        self._wait_timeout = float(wait_timeout)
        self._sock: socket.socket | None = None
        self._buf = b""
        self._proc: subprocess.Popen | None = None
        self._log_handle = None
        self._stdout_path = RESULTS_DIR / "hil_rack_stdout.log"

    # ------------------------------------------------------------------
    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _terminate_process(self, grace: float = 3.0) -> None:
        """回收台架子进程（terminate → wait → kill）并关闭日志句柄。

        启动失败路径也必须调用：否则"进程活着但 TCP 一直没就绪"的超时路径
        会把 hil_rack.py 留在后台占着 9090（交叉审查发现的资源泄漏）。
        """
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    info("[HilLibrary] ⚠ 台架模拟器进程未能回收（kill 后仍超时）")
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def connect(self) -> None:
        if self._sock is not None:
            return
        if not HIL_SCRIPT.exists():
            raise AssertionError(f"找不到台架模拟器脚本：{HIL_SCRIPT}")
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(self._stdout_path, "w", encoding="utf-8")
        env = os.environ.copy()
        env["HIL_PORT"] = str(self._port)
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(HIL_SCRIPT), "--port", str(self._port)],
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + self._wait_timeout
            while True:
                if self._proc.poll() is not None:
                    raise AssertionError(
                        "台架模拟器进程启动后立即退出（exit code "
                        f"{self._proc.returncode}），日志：\n{self._log_tail()}")
                try:
                    sock = socket.create_connection((DEFAULT_HOST, self._port), timeout=1.0)
                    self._sock = sock
                    self._sock.settimeout(5.0)
                    self._buf = b""
                    if self.command({"cmd": "ping"}, timeout=2.0).get("ok"):
                        return
                except (OSError, AssertionError):
                    self._close_socket()
                    if time.monotonic() > deadline:
                        raise AssertionError(
                            f"台架模拟器 {self._wait_timeout:g}s 内未就绪"
                            f"（端口 {self._port} 可能被占用），日志：\n{self._log_tail()}")
                    time.sleep(0.1)
        except BaseException:
            # 启动失败：连接/子进程/日志句柄全部回收（不把问题留给下一个套件）
            self._close_socket()
            self._terminate_process()
            raise

    def command(self, payload: dict, timeout: float = 5.0) -> dict:
        if self._sock is None:
            raise AssertionError("台架连接未建立（先 Start HIL Rack）")
        try:
            self._sock.settimeout(timeout)
            self._sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            while b"\n" not in self._buf:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise AssertionError("台架连接被关闭（台架进程是否已退出？）")
                self._buf += chunk
        except socket.timeout as exc:
            raise AssertionError(
                f"台架 {timeout:g}s 内没有响应命令 {payload.get('cmd')}") from exc
        except OSError as exc:
            raise AssertionError(f"与台架通信失败：{exc}") from exc
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssertionError(f"台架响应不是合法 JSON：{line!r}") from exc
        if not response.get("ok", False):
            raise AssertionError(f"台架命令 {payload.get('cmd')} 失败：{response.get('error')}")
        return response

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.settimeout(0.5)
                self._sock.sendall(b'{"cmd": "quit"}\n')
            except OSError:
                pass
        self._close_socket()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                pass
        self._terminate_process()

    def _log_tail(self, lines: int = 20) -> str:
        try:
            text = self._stdout_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return "(没有台架日志)"


class RealBackend(HilBackend):
    """真实台架后端（**替换点 / 教学占位**）：接 dSPACE、Vector VT System、自研 IO 板。

    真实项目里的实现思路（伪代码）::

        class RealBackend(HilBackend):
            def connect(self):
                self._vt = VTSystem(...)        # 或 dSPACE API / 自研 IO 板 SDK
                self._psu = SCPI电源(f"HIL_RACK_ENDPOINT")   # 程控电源
            def command(self, payload):
                cmd = payload["cmd"]
                if cmd == "set_kl30":  self._psu.set_voltage(payload["voltage"])
                elif cmd == "set_di":   self._vt.set_di(payload["name"], payload["value"])
                ...
                return {"ok": True, "state": self._snapshot()}

    只要实现 ``command()``，本库全部关键字（以及所有用例）就能直接跑在真实台架上。
    """

    name = "real"

    def connect(self) -> None:
        raise AssertionError(REAL_BACKEND_MESSAGE)

    def command(self, payload: dict, timeout: float = 5.0) -> dict:
        raise AssertionError(REAL_BACKEND_MESSAGE)

    def close(self) -> None:
        return


class HilLibrary:
    """HIL 台架与电源/IO 验证关键字集合（SUITE scope：一个套件一个台架连接）。"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, port=None) -> None:
        self._port = int(port if port is not None
                         else os.environ.get("HIL_PORT", DEFAULT_PORT))
        self._backend: HilBackend | None = None
        self._last_state: dict | None = None
        info(f"[HilLibrary] 已加载：台架 TCP {DEFAULT_HOST}:{self._port}"
             f"（HIL_BACKEND={self._backend_env_name()}）")

    # ==================================================================
    # 一、后端抽象与生命周期
    # ==================================================================
    @staticmethod
    def _backend_env_name() -> str:
        """解析 env HIL_BACKEND → 'simulated' | 'real'（非法值给出清晰报错）。"""
        raw = os.environ.get("HIL_BACKEND", "sim").strip().lower()
        if raw in ("sim", "simulated", "mock", ""):
            return "simulated"
        if raw in ("real", "hardware", "hw"):
            return "real"
        raise AssertionError(FAIL_BACKEND.format(value=raw))

    def _make_backend(self) -> HilBackend:
        name = self._backend_env_name()
        if name == "real":
            return RealBackend()
        return SimulatedBackend(self._port)

    @keyword("Start HIL Rack")
    def start_hil_rack(self, wait_timeout: float = 10.0) -> None:
        """启动 HIL 台架（仿真后端 = 拉起子进程；real 后端 = 明确报"需接真实台架"）。

        参数：
            wait_timeout  等待台架就绪的最长时间（秒）

        设计说明（替换点教学）：
            本关键字先按 ``HIL_BACKEND`` 构造后端。若环境选择 real，``RealBackend.connect()``
            会抛出带"需接真实台架"的清晰错误 —— **在测试套件层面就暴露"环境没接台架"**，
            而不是跑到一半某个 IO 关键字莫名失败。切换真实台架只需要实现 RealBackend，
            本关键字与所有用例不用改。
        """
        backend = self._make_backend()
        if backend.name == "real":
            backend.connect()           # 必然抛出"需接真实台架"的指导性错误
        if self._backend is not None:
            info("台架已在运行，复用现有连接")
            return
        backend.connect()               # type: ignore[attr-defined]
        self._backend = backend
        self.reset_rack()
        console(f"[HilLibrary] HIL 台架已就绪：{backend.name} 后端 @ {DEFAULT_HOST}:{self._port}")

    @keyword("Stop HIL Rack")
    def stop_hil_rack(self) -> None:
        """停止台架并回收进程/连接（套件 Teardown 调用）。

        必须回收干净：否则下次运行端口 9090 被占用，台架起不来；残留进程还会让
        "读到的状态"来自上一次运行（状态污染）。
        """
        if self._backend is None:
            info("台架未启动，无需停止")
            return
        name = self._backend.name
        self._backend.close()
        self._backend = None
        self._last_state = None
        info(f"HIL 台架已停止并回收（{name} 后端）")

    @keyword("Backend Should Be")
    def backend_should_be(self, expected="simulated") -> None:
        """断言当前台架后端类型（simulated / real），并验证后端抽象替换点。

        参数：
            expected  期望后端：``simulated``（默认，纯软件仿真）或 ``real``（真实台架）

        行为（这是 REQ-HIL-004 的核心）：
            - env ``HIL_BACKEND=sim``   → 当前为 simulated，断言通过；
            - env ``HIL_BACKEND=real``  → **直接报错"real 后端未实现：需接真实台架"**，
              给出真实台架的接入位置（RealBackend.command）与下一步，而不是静默失败。

        为什么"切到 real 必须明确报错"：如果用假数据继续"通过"，测试会给出**虚假的安全感**；
        台架没接就该红灯，这是 HIL 测试最基本的诚实性。
        """
        actual = self._backend_env_name()
        if actual == "real":
            raise AssertionError(REAL_BACKEND_MESSAGE)
        expected_norm = str(expected).strip().lower()
        if expected_norm in ("sim", "simulated", "mock"):
            expected_norm = "simulated"
        if expected_norm not in ("simulated", "real"):
            raise AssertionError(
                f"Backend Should Be 只接受 simulated/real，收到 {expected}")
        if expected_norm != actual:
            raise AssertionError(
                f"当前后端为 {actual}，期望 {expected_norm}（通过 env HIL_BACKEND 选择）")
        info(f"台架后端 = {actual} ✓（替换点：实现 RealBackend.command() 即可接真实台架）")

    @keyword("Reset Rack")
    def reset_rack(self) -> None:
        """复位台架到默认状态（12.6V / KL15 OFF / 无故障 / IO 默认），用于用例隔离。

        台架是**有状态**的（电压/IO/故障/计数器），不隔离会出现"单跑能过、连跑挂"。
        """
        response = self._command({"cmd": "reset"})
        self._last_state = response.get("state")
        info("台架已复位：12.6V / KL15 OFF / 继电器闭合 / 无故障 / DI/DO 默认")

    # ==================================================================
    # 二、电源操作与上下电时序
    # ==================================================================
    @keyword("Power On Sequence")
    def power_on_sequence(self, delay_ms=100) -> dict:
        """执行上电时序并断言顺序正确：**KL30 先上电 → delay_ms → KL15 ON**。

        参数：
            delay_ms  KL30 与 KL15 之间的间隔（毫秒，默认 100）

        返回：完整台架状态 dict（``power.last_sequence`` 里带每步相对时间 offset_ms）

        为什么顺序重要：真实 ECU 要求先有常电（KL30）完成上电自检/初始化，再收到点火
        信号（KL15）才进入工作模式；顺序反了 ECU 可能不进工作状态、DTC 记录异常。
        **只断言"最终电压 12.6V、KL15 ON"是不够的** —— 要证明"谁先谁后"。
        """
        delay = float(delay_ms)
        response = self._command({"cmd": "power_on", "delay_ms": int(delay)})
        state = self._store_state(response)
        steps = state.get("power", {}).get("last_sequence", [])
        actual = [step.get("step") for step in steps]
        expected = ["KL30_ON", "KL15_ON"]
        if actual != expected:
            raise AssertionError(
                f"上电时序错误：期望 {expected}，实际 {actual}（完整记录 {steps}）")
        offset = float(steps[1].get("offset_ms", 0.0))
        if offset < delay * 0.6:
            raise AssertionError(
                f"KL30→KL15 间隔 {offset:g}ms 过短（设定 {delay:g}ms 的 60% 以下），"
                f"时序可能没有真正等待（记录 {steps}）")
        if offset > delay + 2000:
            raise AssertionError(
                f"KL30→KL15 间隔 {offset:g}ms 过长（设定 {delay:g}ms），台架响应可能异常")
        if not state.get("kl15"):
            raise AssertionError(f"上电后 KL15 应为 ON：{state.get('kl15')}")
        if abs(float(state.get("kl30", {}).get("voltage", 0)) - KL30_NOMINAL_V) > 0.05:
            raise AssertionError(
                f"上电后 KL30 电压应为 {KL30_NOMINAL_V}V，实际 {state.get('kl30')}")
        info(f"上电时序正确：KL30_ON → {offset:g}ms → KL15_ON"
             f"（KL30={state['kl30']['voltage']}V，KL15={state['kl15']}）")
        return state

    @keyword("Power Off Sequence")
    def power_off_sequence(self, delay_ms=100) -> dict:
        """执行下电时序并断言顺序正确：**KL15 OFF → delay_ms → KL30 OFF**。

        下电顺序反了（先断 KL30 再关 KL15）在真实 ECU 上可能产生"电压跌落中丢帧/
        异常 DTC"，所以同样要验证"谁先谁后"，而不是只看最终都断电。
        """
        delay = float(delay_ms)
        response = self._command({"cmd": "power_off", "delay_ms": int(delay)})
        state = self._store_state(response)
        steps = state.get("power", {}).get("last_sequence", [])
        actual = [step.get("step") for step in steps]
        expected = ["KL15_OFF", "KL30_OFF"]
        if actual != expected:
            raise AssertionError(
                f"下电时序错误：期望 {expected}，实际 {actual}（完整记录 {steps}）")
        offset = float(steps[1].get("offset_ms", 0.0))
        if offset < delay * 0.6:
            raise AssertionError(
                f"KL15→KL30 间隔 {offset:g}ms 过短（设定 {delay:g}ms 的 60% 以下）")
        if state.get("kl15"):
            raise AssertionError(f"下电后 KL15 应为 OFF：{state.get('kl15')}")
        if abs(float(state.get("kl30", {}).get("voltage", 1.0))) > 0.05:
            raise AssertionError(
                f"KL30 OFF 后电压应为 0V，实际 {state.get('kl30')}")
        info(f"下电时序正确：KL15_OFF → {offset:g}ms → KL30_OFF")
        return state

    @keyword("Set Kl30 Voltage")
    def set_kl30_voltage(self, voltage) -> float:
        """设定 KL30 常电电压（0~18V），返回读回电压。

        参数：
            voltage  目标电压（V，如 12.6 / 9.0 / 16.0 / 0）
        """
        response = self._command({"cmd": "set_kl30", "voltage": float(voltage)})
        state = self._store_state(response)
        actual = float(state["kl30"]["voltage"])
        info(f"KL30 电压设定为 {actual}V")
        return actual

    @keyword("Get Kl30 Voltage")
    def get_kl30_voltage(self) -> float:
        """读取当前 KL30 电压（V）。"""
        state = self.read_rack_state()
        voltage = float(state["kl30"]["voltage"])
        info(f"KL30 当前电压 {voltage}V")
        return voltage

    @keyword("Kl30 Voltage Should Be")
    def kl30_voltage_should_be(self, expected, tolerance=0.05) -> None:
        """断言 KL30 电压等于期望值（默认容差 ±0.05V），返回实测值。

        参数：
            expected   期望电压（V）
            tolerance  容差（V）
        """
        actual = self.get_kl30_voltage()
        target = float(expected)
        limit = float(tolerance)
        if abs(actual - target) > limit:
            raise AssertionError(
                f"KL30 电压 {actual:g}V ≠ 期望 {target:g}V（容差 ±{limit:g}V）")
        info(f"KL30 电压 {actual:g}V = 期望 {target:g}V ✓")

    # ==================================================================
    # 三、电源故障注入
    # ==================================================================
    @keyword("Inject Under Voltage")
    def inject_under_voltage(self) -> dict:
        """注入欠压：KL30 降到 9.0V（发动机启动/冷启动边界工况），返回台架状态。

        真实影响：ECU 可能记录 P0562（系统电压低）、关闭大功率负载、仪表提示；
        本模块只验证"台架能施加并读回该电压"（ECU 侧验证需真 ECU，见 docs/38 边界表）。
        """
        state = self._store_state(self._command({"cmd": "inject", "kind": "undervoltage"}))
        info(f"已注入欠压：KL30={state['kl30']['voltage']}V，"
             f"fault_type={state['power']['fault_type']}")
        return state

    @keyword("Inject Over Voltage")
    def inject_over_voltage(self) -> dict:
        """注入过压：KL30 升到 16.0V（发电机调节器失效/跨接启动工况），返回台架状态。"""
        state = self._store_state(self._command({"cmd": "inject", "kind": "overvoltage"}))
        info(f"已注入过压：KL30={state['kl30']['voltage']}V，"
             f"fault_type={state['power']['fault_type']}")
        return state

    @keyword("Inject Power Loss")
    def inject_power_loss(self) -> dict:
        """注入掉电：KL30 降到 0V（蓄电池断开/保险丝熔断），返回台架状态。"""
        state = self._store_state(self._command({"cmd": "inject", "kind": "power_loss"}))
        info(f"已注入掉电：KL30={state['kl30']['voltage']}V，"
             f"fault_type={state['power']['fault_type']}")
        return state

    @keyword("Inject Power Glitch")
    def inject_power_glitch(self, pulses=5, duration_ms=120, interval_ms=20) -> dict:
        """注入 PWM 式电源抖动（掉电脉冲串），返回台架状态。

        参数：
            pulses       掉电脉冲个数（默认 5）
            duration_ms  每个脉冲掉电时长（ms）
            interval_ms  脉冲之间恢复时长（ms）

        真实场景：线束虚接/继电器触点抖动/接触器弹跳 → 电压瞬间跌到 0 再恢复。
        测试要验证 ECU 在电压抖动下不误动作/能恢复（真 ECU 验证见 docs/38 边界表）；
        本模块验证"台架能施加抖动波形且恢复后电压正常"。
        """
        response = self._command({
            "cmd": "inject_power_glitch",
            "pulses": int(pulses),
            "duration_ms": int(duration_ms),
            "interval_ms": int(interval_ms),
        })
        state = self._store_state(response)
        last = (state.get("glitch") or {}).get("last") or {}
        if int(last.get("pulses", -1)) != int(pulses):
            raise AssertionError(
                f"电源抖动未按预期记录：期望 {pulses} 个脉冲，台架记录 {last}")
        info(f"已注入电源抖动：{last['pulses']} 脉冲 × 掉电 {last['duration_ms']}ms + "
             f"恢复 {last['interval_ms']}ms（总 {last['total_ms']}ms），"
             f"恢复后 KL30={state['kl30']['voltage']}V")
        return state

    @keyword("Power Cycle")
    def power_cycle(self, off_ms=300) -> dict:
        """硬件断电重启：断开 ECU 供电继电器 → off_ms → 恢复供电并清空瞬态状态。

        参数：
            off_ms  断电时长（毫秒，默认 300）

        返回：恢复后的完整台架状态。

        为什么要 Power Cycle：软件复位（0x11）模拟不了"真断电"——真实台架上拔 KL30
        会让 ECU 完全重新初始化（RAM 丢失、继电器复位、DTC 重新计算）。很多"只在冷启动
        出现"的问题只有硬件断电重启才能复现。断言要点：恢复 12.6V、KL15 OFF（重新点火
        前不该 ON）、故障/IO/电流状态清空。
        """
        response = self._command({"cmd": "power_cycle", "off_ms": int(off_ms)})
        state = self._store_state(response)
        info(f"Power Cycle 完成（断电 {off_ms}ms）：恢复 {state['kl30']['voltage']}V，"
             f"KL15={state['kl15']}，累计 {state['stats']['power_cycle_count']} 次")
        return state

    # ==================================================================
    # 四、DI / DO / 继电器 / 电流
    # ==================================================================
    @keyword("Set Digital Input")
    def set_digital_input(self, name, value) -> dict:
        """设置数字输入 DI（模拟车身开关硬线激励）。

        通道：DOOR_OPEN（车门开）/ SEATBELT_FASTENED（安全带已系）/
              BRAKE_PRESSED（刹车踩下）/ GEAR_P（档位 P）。
        """
        response = self._command({"cmd": "set_di", "name": name,
                                  "value": self._as_bool(value)})
        state = self._store_state(response)
        info(f"DI 激励：{str(name).upper()} = {state['di'][str(name).upper()]}")
        return state

    @keyword("Get Digital Input")
    def get_digital_input(self, name) -> bool:
        """读取数字输入 DI 的当前值（bool）。"""
        state = self.read_rack_state()
        key = str(name).strip().upper()
        if key not in state.get("di", {}):
            raise AssertionError(
                f"未知 DI 通道 {name}，可选 {sorted(state.get('di', {}))}")
        value = bool(state["di"][key])
        info(f"DI {key} = {value}")
        return value

    @keyword("Set Digital Output")
    def set_digital_output(self, name, value) -> dict:
        """设置数字输出 DO（主继电器/指示灯/风扇/备用），返回台架状态。

        真实台架中 DO 可能是 ECU 驱动的输出（测试读回），也可能是测试端直接驱动
        的执行器；本模块用"可写可回读"模拟后者的验证方式。
        """
        response = self._command({"cmd": "set_do", "name": name,
                                  "value": self._as_bool(value)})
        state = self._store_state(response)
        info(f"DO 输出：{str(name).upper()} = {state['do'][str(name).upper()]}")
        return state

    @keyword("Get Digital Output")
    def get_digital_output(self, name) -> bool:
        """读取数字输出 DO 的当前值（bool），用于验证"写下去的值读回来一致"。"""
        state = self.read_rack_state()
        key = str(name).strip().upper()
        if key not in state.get("do", {}):
            raise AssertionError(
                f"未知 DO 通道 {name}，可选 {sorted(state.get('do', {}))}")
        value = bool(state["do"][key])
        info(f"DO {key} = {value}")
        return value

    @keyword("Set Relay")
    def set_relay(self, name, state) -> dict:
        """控制继电器通断，返回台架状态。

        通道：ECU_POWER（ECU 供电，断开=硬件断电）/ LOAD_BANK（负载切换）。

        用 ECU_POWER 继电器可以做"硬件断电重启"；真实台架上这就是程控电源/保险丝
        盒的远程通断，是电源管理测试的常用手段。
        """
        response = self._command({"cmd": "set_relay", "name": name,
                                  "state": self._as_bool(state)})
        rack_state = self._store_state(response)
        info(f"继电器 {str(name).upper()} = {rack_state['relays'][str(name).upper()]}")
        return rack_state

    @keyword("Set Load Current")
    def set_load_current(self, amps) -> dict:
        """设定台架负载电流（A），超过 5A 时置过流标记，返回台架状态。

        真实台架用程控电源/电子负载控制电流；过流保护是电源测试的重要安全项
        （真实 ECU 可能因过流被台架切断供电）。
        """
        response = self._command({"cmd": "set_current", "amps": float(amps)})
        state = self._store_state(response)
        current = state["current"]
        info(f"负载电流设定 {current['amps']}A"
             f"（限值 {current['limit_a']}A，over_current={current['over_current']}）")
        return state

    @keyword("Over Current Should Be Flagged")
    def over_current_should_be_flagged(self, expected=True) -> None:
        """断言过流标记 over_current 与期望一致（默认 True），并输出电流/限值。

        参数：
            expected  True（应置位）/ False（应清除）
        """
        state = self.read_rack_state()
        current = state.get("current", {})
        actual = bool(current.get("over_current"))
        expected_bool = self._as_bool(expected)
        if actual != expected_bool:
            raise AssertionError(
                f"过流标记 over_current={actual}，期望 {expected_bool}"
                f"（负载电流 {current.get('amps')}A / 限值 {current.get('limit_a')}A）")
        info(f"过流标记 over_current={actual} ✓"
             f"（电流 {current.get('amps')}A，限值 {current.get('limit_a')}A，"
             f"峰值 {current.get('peak_amps')}A，累计过流事件 "
             f"{state.get('stats', {}).get('over_current_events')} 次）")

    # ==================================================================
    # 五、状态读取与断言
    # ==================================================================
    @keyword("Read Rack State")
    def read_rack_state(self) -> dict:
        """读取完整台架状态 dict（电压/KL15/DI/DO/继电器/电流/故障/统计）。

        关键结构::

            {'kl30': {'voltage': 12.6, 'nominal_v': 12.6, ...},
             'kl15': True,
             'power': {'state': 'ON', 'fault_type': 'NONE', 'fault_active': False,
                       'ecu_powered': True, 'last_sequence': [{'step','offset_ms'}, ...]},
             'di': {...}, 'do': {...}, 'relays': {...},
             'current': {'amps': 0.8, 'limit_a': 5.0, 'over_current': False, ...},
             'glitch': {'count': 0, 'last': {...}},
             'stats': {...}, 'backend': 'simulated'}
        """
        response = self._command({"cmd": "status"})
        return self._store_state(response)

    @keyword("Rack State Should Contain")
    def rack_state_should_contain(self, key_path, expected) -> None:
        """断言台架状态的某个字段等于期望值（支持点号路径）。

        参数：
            key_path  字段路径，如 ``kl15`` / ``kl30.voltage`` / ``power.fault_type`` /
                      ``do.MAIN_RELAY`` / ``relays.ECU_POWER`` / ``current.over_current``
            expected  期望值（bool/数字/字符串自动按类型比较）
        """
        state = self.read_rack_state()
        node = state
        parts = str(key_path).split(".")
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                raise AssertionError(
                    f"台架状态里没有字段 {key_path}（在 {'.'.join(parts[:parts.index(part) + 1])} 处找不到 {part}）")
            node = node[part]
        if not self._values_equal(node, expected):
            raise AssertionError(
                f"台架状态 {key_path} = {node!r}，期望 {expected!r}"
                f"（完整状态 {state}）")
        info(f"台架状态 {key_path} = {node!r} ✓")

    # ==================================================================
    # 内部工具
    # ==================================================================
    def _command(self, payload: dict) -> dict:
        """统一执行台架命令（自动检查后端是否已启动）。"""
        if self._backend is None:
            raise AssertionError(
                "台架尚未启动：先调用 Start HIL Rack（套件 Suite Setup）")
        return self._backend.command(payload)

    def _store_state(self, response: dict) -> dict:
        state = response.get("state")
        if not isinstance(state, dict):
            raise AssertionError(f"台架响应缺少 state 字段：{response}")
        self._last_state = state
        return state

    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("true", "1", "on", "yes"):
            return True
        if text in ("false", "0", "off", "no", ""):
            return False
        raise AssertionError(f"无法把 {value!r} 解析成布尔值")

    @staticmethod
    def _values_equal(actual, expected) -> bool:
        """按类型比较：bool → 布尔语义；数字 → 容差比较；其它 → 字符串比较。"""
        if isinstance(actual, bool):
            return HilLibrary._as_bool(expected) == actual
        if isinstance(actual, (int, float)):
            try:
                return abs(float(actual) - float(expected)) <= 1e-6
            except (TypeError, ValueError):
                return False
        return str(actual) == str(expected)
