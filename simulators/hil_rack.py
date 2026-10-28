"""
hil_rack.py — HIL 台架 IO 板模拟器（KL30/KL15 电源 + DI/DO/继电器 + 故障注入）
================================================================================

这是「HIL 台架抽象与电源/IO 验证」模块的**被测台架**（模拟 IO 板 + 程控电源），
一个独立进程，通过 **TCP 127.0.0.1:9090**（env ``HIL_PORT`` 或 ``--port`` 可覆盖）
用 **JSON 行协议**接受测试端的 IO 操作命令，并返回完整台架状态。

它模拟真实 HIL 台架（dSPACE / Vector VT System / 自研 IO 板）最常用的三类能力：

    1. 电源通道（KL30 / KL15）
       KL30 = 常电（默认 12.6V 满电电压，0~18V 可调）；KL15 = 点火开关（ON/OFF）。
       支持欠压 9V / 过压 16V / 掉电 0V / 电源抖动（PWM 式脉冲掉电）；
       上电时序必须是 **KL30 先于 KL15**、下电时序 **KL15 先于 KL30**（真实台架要
       模拟"ECU 先上电自检、再收到点火信号"的顺序，否则 ECU 行为不可信）。
    2. 数字输入 DI（4 路，激励车身开关）
       DOOR_OPEN 车门 / SEATBELT_FASTENED 安全带 / BRAKE_PRESSED 刹车 / GEAR_P 档位。
       真实台架通过 VT 板卡/继电器给 ECU 硬线输入，这里用状态位模拟。
    3. 数字输出 DO + 继电器（4 + 2 路，读回执行器状态）
       DO：MAIN_RELAY / INDICATOR_LAMP / FAN / SPARE（写后回读，验证执行器输出）；
       RELAY：ECU_POWER（断 ECU 供电做硬件断电重启）/ LOAD_BANK（负载切换）。
    4. 电流限制模拟
       默认负载电流 0.8A；设定 >5A 时 over_current=True 并累计过流事件，
       模拟"台架程控电源的电流钳制/保护"（真实台架会直接限流/断电）。

为什么把台架做成独立进程 + 抽象协议（教学重点）：
    测试脚本**不应该绑死某一家的台架**。本文件是"仿真后端"，``libraries/HilLibrary.py``
    通过 ``HIL_BACKEND=sim|real`` 选择后端；接真实台架时只实现 real 后端的
    ``command()``（映射到 dSPACE/Vector 的 API、SCPI、Modbus…），用例一行不改。

通信协议（JSON 行，请求/响应一一对应）::

    {"cmd": "ping"}                              → {"ok": true, "backend": "simulated", ...}
    {"cmd": "status"}                            → {"ok": true, "state": {...完整状态...}}
    {"cmd": "power_on",  "delay_ms": 100}        → KL30_ON → (delay) → KL15_ON，记录时序
    {"cmd": "power_off", "delay_ms": 100}        → KL15_OFF → (delay) → KL30_OFF，记录时序
    {"cmd": "set_kl30",  "voltage": 12.6}        → 设定 KL30 电压（0~18V）
    {"cmd": "inject",    "kind": "undervoltage"|"overvoltage"|"power_loss"}
    {"cmd": "power_cycle", "off_ms": 300}        → 断 ECU 供电 → 恢复 → 状态清空
    {"cmd": "set_di",    "name": "DOOR_OPEN", "value": true}
    {"cmd": "set_do",    "name": "INDICATOR_LAMP", "value": true}
    {"cmd": "set_relay", "name": "ECU_POWER", "state": true}
    {"cmd": "set_current", "amps": 6.5}          → >5A 置 over_current 标记
    {"cmd": "inject_power_glitch", "pulses": 5, "duration_ms": 120, "interval_ms": 20}
    {"cmd": "reset"}                             → 全量复位（用例隔离用）
    {"cmd": "quit"}                              → 退出进程

启动方式（一般由 libraries/HilLibrary.py 拉起，也可手动跑）::

    .\\.venv\\Scripts\\python.exe simulators\\hil_rack.py
    .\\.venv\\Scripts\\python.exe simulators\\hil_rack.py --port 9090
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socketserver
import sys
import threading
import time

log = logging.getLogger("hil_rack")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("HIL_PORT", "9090"))
DEFAULT_BACKEND = "simulated"

# ---------------------------------------------------------------------------
# 台架硬件定义（真实项目里这张表来自"IO 映射表/线束表"，见 docs/38）
# ---------------------------------------------------------------------------
KL30_NOMINAL_V = 12.6       # 12V 蓄电池满电静止电压（发动机未启动）
KL30_MIN_V = 0.0
KL30_MAX_V = 18.0
KL15_DELAY_MS = 100         # 默认 KL30 → KL15 间隔

UNDERVOLTAGE_V = 9.0        # 欠压注入（真实项目常测 9V/10.5V 冷启动边界）
OVERVOLTAGE_V = 16.0        # 过压注入（发电机调节器失效场景）
POWER_LOSS_V = 0.0

CURRENT_LIMIT_A = 5.0       # 台架程控电源电流上限（教学值）
NOMINAL_CURRENT_A = 0.8

# DI：车身开关激励（真实台架给 ECU 硬线输入）
DI_CHANNELS = {
    "DOOR_OPEN": False,             # 车门开
    "SEATBELT_FASTENED": True,      # 安全带已系（默认系上）
    "BRAKE_PRESSED": False,         # 刹车踩下
    "GEAR_P": True,                 # 档位 P
}
# DO：执行器/继电器输出（真实台架读回 ECU 驱动输出或直接驱动执行器）
DO_CHANNELS = {
    "MAIN_RELAY": False,            # 主继电器
    "INDICATOR_LAMP": False,        # 指示灯
    "FAN": False,                   # 风扇
    "SPARE": False,                 # 备用
}
# 继电器：ECU_POWER 断开 = 硬件断电重启（模拟真实台架拔保险丝/断 KL30）
RELAY_CHANNELS = {
    "ECU_POWER": True,              # 默认闭合：ECU 有电
    "LOAD_BANK": False,             # 负载切换继电器
}


class RackState:
    """台架状态机：所有 IO / 电源状态与操作记录（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------
    # 复位
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """回到出厂默认状态：12.6V、KL15 OFF、DI/DO 默认、继电器闭合、无故障。"""
        with self._lock:
            now = time.monotonic()
            self._started_at = now
            # 电源
            self._kl30_voltage = KL30_NOMINAL_V
            self._kl15 = False
            self._fault_type = "NONE"        # NONE / UNDERVOLTAGE / OVERVOLTAGE / POWER_LOSS
            self._last_sequence: list[dict] = []
            # IO
            self._di = dict(DI_CHANNELS)
            self._do = dict(DO_CHANNELS)
            self._relays = dict(RELAY_CHANNELS)
            # 电流
            self._current_a = NOMINAL_CURRENT_A
            self._peak_current_a = NOMINAL_CURRENT_A
            self._over_current = False
            # 电源抖动
            self._glitch_count = 0
            self._last_glitch: dict | None = None
            # 统计
            self._stats = {
                "power_on_count": 0,
                "power_off_count": 0,
                "power_cycle_count": 0,
                "over_current_events": 0,
                "glitch_count": 0,
                "commands_total": 0,
            }
            self._last_command = ""
        log.info("台架状态已复位（%.1fV / KL15 OFF / 继电器闭环）", KL30_NOMINAL_V)

    # ------------------------------------------------------------------
    # 状态快照
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """返回完整台架状态 JSON（测试断言的全部依据）。"""
        with self._lock:
            now = time.monotonic()
            return {
                "ok": True,
                "backend": DEFAULT_BACKEND,
                "kl30": {
                    "voltage": round(self._kl30_voltage, 2),
                    "nominal_v": KL30_NOMINAL_V,
                    "min_v": KL30_MIN_V,
                    "max_v": KL30_MAX_V,
                },
                "kl15": self._kl15,
                "power": {
                    "state": "ON" if (self._kl15 and self._kl30_voltage > 6.0) else "OFF",
                    "fault_type": self._fault_type,
                    "fault_active": self._fault_type != "NONE",
                    "ecu_powered": self._relays["ECU_POWER"] and self._kl30_voltage > 6.0,
                    "last_sequence": list(self._last_sequence),
                },
                "di": dict(self._di),
                "do": dict(self._do),
                "relays": dict(self._relays),
                "current": {
                    "amps": round(self._current_a, 2),
                    "peak_amps": round(self._peak_current_a, 2),
                    "limit_a": CURRENT_LIMIT_A,
                    "over_current": self._over_current,
                },
                "glitch": {
                    "count": self._glitch_count,
                    "last": dict(self._last_glitch) if self._last_glitch else None,
                },
                "stats": dict(self._stats),
                "backend": DEFAULT_BACKEND,
                "last_command": self._last_command,
                "uptime_s": round(now - self._started_at, 3),
                "timestamp": round(now, 3),
            }

    # ------------------------------------------------------------------
    # 电源操作
    # ------------------------------------------------------------------
    def power_on(self, delay_ms: int = KL15_DELAY_MS) -> dict:
        """上电时序：KL30 先上电 → 等 delay_ms → KL15 ON（记录每步相对时间）。"""
        delay = max(0, int(delay_ms)) / 1000.0
        with self._lock:
            # KL30 先就位：恢复到标称电压并闭合 ECU 供电继电器（上电前的电源准备）
            self._kl30_voltage = KL30_NOMINAL_V
            self._fault_type = "NONE"
            self._relays["ECU_POWER"] = True
            if self._kl15:
                # 已经点火：为了可测时序，先回到"KL15 OFF"再按顺序上电
                self._kl15 = False
                time.sleep(0.01)
            start = time.monotonic()
            self._last_sequence = [
                {"step": "KL30_ON", "voltage": round(self._kl30_voltage, 2),
                 "offset_ms": 0.0},
            ]
            time.sleep(delay)
            self._kl15 = True
            self._last_sequence.append({
                "step": "KL15_ON",
                "voltage": round(self._kl30_voltage, 2),
                "offset_ms": round((time.monotonic() - start) * 1000.0, 1),
            })
            self._stats["power_on_count"] += 1
            log.info("上电时序执行：KL30(%.2fV) → %.0fms → KL15 ON",
                     self._kl30_voltage, delay * 1000)
            return self.snapshot()

    def power_off(self, delay_ms: int = KL15_DELAY_MS) -> dict:
        """下电时序：KL15 OFF → 等 delay_ms → KL30 OFF（记录每步相对时间）。"""
        delay = max(0, int(delay_ms)) / 1000.0
        with self._lock:
            start = time.monotonic()
            self._last_sequence = [{"step": "KL15_OFF", "offset_ms": 0.0}]
            self._kl15 = False
            time.sleep(delay)
            self._kl30_voltage = POWER_LOSS_V
            self._fault_type = "POWER_OFF"
            self._last_sequence.append({
                "step": "KL30_OFF",
                "voltage": 0.0,
                "offset_ms": round((time.monotonic() - start) * 1000.0, 1),
            })
            self._stats["power_off_count"] += 1
            log.info("下电时序执行：KL15 OFF → %.0fms → KL30 OFF", delay * 1000)
            return self.snapshot()

    def set_kl30(self, voltage: float) -> dict:
        """设定 KL30 电压（0~18V）；恢复正常电压时自动清故障标记。"""
        value = round(float(voltage), 2)
        if not KL30_MIN_V <= value <= KL30_MAX_V:
            raise ValueError(f"KL30 电压 {value}V 超出范围 {KL30_MIN_V}~{KL30_MAX_V}V")
        with self._lock:
            self._kl30_voltage = value
            if value >= 6.0:
                self._fault_type = "NONE"
            elif value == 0.0:
                self._fault_type = "POWER_LOSS"
            log.info("KL30 电压设定为 %.2fV", value)
            return self.snapshot()

    def inject_fault(self, kind: str) -> dict:
        """电源故障注入：undervoltage(9V) / overvoltage(16V) / power_loss(0V)。"""
        with self._lock:
            kind_lower = str(kind).strip().lower()
            if kind_lower == "undervoltage":
                self._kl30_voltage = UNDERVOLTAGE_V
                self._fault_type = "UNDERVOLTAGE"
            elif kind_lower == "overvoltage":
                self._kl30_voltage = OVERVOLTAGE_V
                self._fault_type = "OVERVOLTAGE"
            elif kind_lower == "power_loss":
                self._kl30_voltage = POWER_LOSS_V
                self._fault_type = "POWER_LOSS"
            else:
                raise ValueError(
                    f"未知电源故障 {kind}（支持 undervoltage/overvoltage/power_loss）")
            log.warning("注入电源故障：%s → KL30=%.2fV", kind_lower.upper(),
                        self._kl30_voltage)
            return self.snapshot()

    def power_cycle(self, off_ms: int = 300) -> dict:
        """硬件断电重启：断开 ECU 供电 → off_ms → 恢复供电并把瞬态状态清空。"""
        off_seconds = max(0, int(off_ms)) / 1000.0
        with self._lock:
            self._stats["power_cycle_count"] += 1
            self._last_sequence = [{"step": "ECU_POWER_OFF", "offset_ms": 0.0}]
            self._relays["ECU_POWER"] = False
            self._kl15 = False
            time.sleep(off_seconds)
            # 恢复供电：电压回到标称、清故障/瞬态 IO/电流/抖动（"状态清空"）
            self._kl30_voltage = KL30_NOMINAL_V
            self._fault_type = "NONE"
            self._di = dict(DI_CHANNELS)
            self._do = dict(DO_CHANNELS)
            self._relays["ECU_POWER"] = True
            self._current_a = NOMINAL_CURRENT_A
            self._over_current = False
            self._last_glitch = None
            self._last_sequence.append({
                "step": "ECU_POWER_ON",
                "voltage": KL30_NOMINAL_V,
                "offset_ms": round(off_seconds * 1000.0, 1),
            })
            log.warning("Power Cycle 完成：断电 %.0fms → 恢复 %.1fV、状态已清空",
                        off_seconds * 1000, KL30_NOMINAL_V)
            return self.snapshot()

    # ------------------------------------------------------------------
    # IO / 继电器 / 电流
    # ------------------------------------------------------------------
    def set_di(self, name: str, value) -> dict:
        with self._lock:
            key = self._check_channel(name, DI_CHANNELS, "DI")
            self._di[key] = _as_bool(value)
            log.info("DI 激励：%s = %s", key, self._di[key])
            return self.snapshot()

    def set_do(self, name: str, value) -> dict:
        with self._lock:
            key = self._check_channel(name, DO_CHANNELS, "DO")
            self._do[key] = _as_bool(value)
            log.info("DO 输出：%s = %s", key, self._do[key])
            return self.snapshot()

    def set_relay(self, name: str, state) -> dict:
        with self._lock:
            key = self._check_channel(name, RELAY_CHANNELS, "继电器")
            self._relays[key] = _as_bool(state)
            if key == "ECU_POWER" and not self._relays[key]:
                self._kl15 = False
            log.info("继电器：%s = %s", key, self._relays[key])
            return self.snapshot()

    def set_current(self, amps: float) -> dict:
        with self._lock:
            value = round(max(0.0, float(amps)), 2)
            was_over = self._over_current
            self._over_current = value > CURRENT_LIMIT_A
            self._current_a = value
            self._peak_current_a = max(self._peak_current_a, value)
            if self._over_current and not was_over:
                self._stats["over_current_events"] += 1
                log.warning("过流！负载电流 %.2fA > 限值 %.1fA", value, CURRENT_LIMIT_A)
            return self.snapshot()

    # ------------------------------------------------------------------
    # 电源抖动（PWM 式脉冲掉电，模拟接触器抖动/线束虚接）
    # ------------------------------------------------------------------
    def inject_power_glitch(self, pulses: int = 5, duration_ms: int = 120,
                            interval_ms: int = 20) -> dict:
        """模拟 PWM 式电源抖动：连续 pulses 个"掉电 duration_ms + 恢复 interval_ms"。

        真实台架用程控电源按波形输出（或继电器高频通断），ECU 可能复位/降级；
        本仿真记录波形参数与脉宽，并把电压"恢复后"的状态返回给测试端。
        """
        pulse_count = int(pulses)
        duration = int(duration_ms)
        interval = int(interval_ms)
        if not 1 <= pulse_count <= 50:
            raise ValueError(f"pulses 必须在 1~50，收到 {pulses}")
        if not 1 <= duration <= 1000:
            raise ValueError(f"duration_ms 必须在 1~1000，收到 {duration_ms}")
        if not 1 <= interval <= 1000:
            raise ValueError(f"interval_ms 必须在 1~1000，收到 {interval_ms}")
        with self._lock:
            previous_voltage = self._kl30_voltage
            self._glitch_count += 1
            self._stats["glitch_count"] += 1
            total_ms = pulse_count * (duration + interval)
            self._last_glitch = {
                "pulses": pulse_count,
                "duration_ms": duration,
                "interval_ms": interval,
                "total_ms": total_ms,
                "dip_voltage": 0.0,
                "restored_voltage": round(previous_voltage, 2),
            }
            # 抖动结束：电压恢复到注入前的值（真实台架波形结束后回到标称电压）
            self._kl30_voltage = previous_voltage
            log.warning("注入电源抖动：%d 个脉冲（掉电 %dms + 恢复 %dms，共 %dms）",
                        pulse_count, duration, interval, total_ms)
            return self.snapshot()

    @staticmethod
    def _check_channel(name: str, channels: dict, kind: str) -> str:
        key = str(name).strip().upper()
        if key not in channels:
            raise ValueError(
                f"未知 {kind} 通道 {name}，可选 {sorted(channels)}")
        return key

    # ------------------------------------------------------------------
    # 命令分发
    # ------------------------------------------------------------------
    def handle(self, request: dict) -> dict:
        """处理一条 JSON 命令，返回 {"ok": true, "state": {...}} 或 {"ok": false, "error": ...}。"""
        with self._lock:
            self._stats["commands_total"] += 1
        cmd = str(request.get("cmd", "")).strip().lower()
        self._last_command = cmd
        try:
            if cmd in ("ping", "hello"):
                return {"ok": True, "backend": DEFAULT_BACKEND,
                        "kl30_nominal_v": KL30_NOMINAL_V,
                        "current_limit_a": CURRENT_LIMIT_A}
            if cmd == "status":
                return {"ok": True, "state": self.snapshot()}
            if cmd == "power_on":
                return {"ok": True, "state": self.power_on(request.get("delay_ms", KL15_DELAY_MS))}
            if cmd == "power_off":
                return {"ok": True, "state": self.power_off(request.get("delay_ms", KL15_DELAY_MS))}
            if cmd == "set_kl30":
                return {"ok": True, "state": self.set_kl30(request.get("voltage"))}
            if cmd == "inject":
                return {"ok": True, "state": self.inject_fault(request.get("kind", ""))}
            if cmd == "power_cycle":
                return {"ok": True, "state": self.power_cycle(request.get("off_ms", 300))}
            if cmd == "set_di":
                return {"ok": True, "state": self.set_di(request.get("name", ""),
                                                         request.get("value", False))}
            if cmd == "set_do":
                return {"ok": True, "state": self.set_do(request.get("name", ""),
                                                         request.get("value", False))}
            if cmd == "set_relay":
                return {"ok": True, "state": self.set_relay(request.get("name", ""),
                                                            request.get("state", False))}
            if cmd == "set_current":
                return {"ok": True, "state": self.set_current(request.get("amps", 0.0))}
            if cmd == "inject_power_glitch":
                return {"ok": True, "state": self.inject_power_glitch(
                    request.get("pulses", 5),
                    request.get("duration_ms", 120),
                    request.get("interval_ms", 20))}
            if cmd == "reset":
                self.reset()
                return {"ok": True, "state": self.snapshot()}
            if cmd == "quit":
                return {"ok": True, "bye": True}
            return {"ok": False, "error": f"未知命令: {cmd}"}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 —— 单条命令异常不能打死台架
            log.exception("命令处理异常：%s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _as_bool(value) -> bool:
    """把 JSON 里的 true/false（或 0/1、字符串）归一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no", ""):
        return False
    raise ValueError(f"无法解析为布尔值：{value!r}")


class RackRequestHandler(socketserver.StreamRequestHandler):
    """每个 TCP 连接一个处理器：按 JSON 行请求-响应。"""

    def handle(self) -> None:  # noqa: C901 —— 协议简单，保持单函数可读
        rack: RackState = self.server.rack        # type: ignore[attr-defined]
        while True:
            try:
                line = self.rfile.readline()
            except OSError:
                break
            if not line:
                break
            request: dict = {}
            try:
                request = json.loads(line.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                response = {"ok": False, "error": f"bad request: {exc}"}
                request = {}
            else:
                response = rack.handle(request)
            try:
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
            except OSError:
                break
            if request.get("cmd", "").strip().lower() == "quit":
                self.server.stop_event.set()          # type: ignore[attr-defined]
                break
        log.debug("客户端连接断开")


class RackTCPserver(socketserver.ThreadingTCPServer):
    """支持多连接（测试端可能开多条连接/重连），允许端口复用。"""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, rack: RackState) -> None:
        super().__init__(address, handler)
        self.rack = rack
        self.stop_event = threading.Event()


def main() -> None:
    parser = argparse.ArgumentParser(description="HIL 台架 IO 板模拟器（电源/DI/DO/继电器）")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP 端口（默认 {DEFAULT_PORT}，也可用 HIL_PORT）")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    rack = RackState()
    try:
        server = RackTCPserver((args.host, args.port), RackRequestHandler, rack)
    except OSError as exc:
        raise SystemExit(
            f"[hil_rack] 绑定 {args.host}:{args.port} 失败：{exc}\n"
            f"  端口被占用？换端口：--port 9091 或设置环境变量 HIL_PORT。")

    serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
    serve_thread.start()
    print("=" * 62)
    print(f" HIL 台架模拟器已启动   TCP {args.host}:{args.port}")
    print(f" KL30={KL30_NOMINAL_V}V（0~{KL30_MAX_V}V）/ KL15 点火 / DI {len(DI_CHANNELS)} 路 / "
          f"DO {len(DO_CHANNELS)} 路 / 继电器 {len(RELAY_CHANNELS)} 路")
    print(f" 电流限值 {CURRENT_LIMIT_A}A / 上电时序 KL30→KL15 / 下电时序 KL15→KL30")
    print("=" * 62)
    try:
        while not server.stop_event.wait(0.2):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        serve_thread.join(timeout=1.0)
        log.info("台架模拟器已退出")


if __name__ == "__main__":
    main()
