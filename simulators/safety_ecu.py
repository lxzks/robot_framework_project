"""
safety_ecu.py — 功能安全节点模拟器（E2E Profile 1 + 看门狗 + 降级/安全状态）
================================================================================

这是「**功能安全机制验证**」模块的被测对象（DUT）：一个独立的**安全节点**进程，
用最少的代码把真实 ECU 里三类安全机制跑起来，让测试端可以**注入故障**并**观测反应**：

    1. E2E 端到端保护（教学版 AUTOSAR E2E Profile 1）
       安全报文 8 字节 = dataId(2) + counter(1) + data(4) + CRC8(1)；
       接收端逐帧校验 CRC / 计数器序列，统计 CRC 错、计数器跳变、重放；
    2. 看门狗（Watchdog）
       节点内部"任务心跳"每 50ms 更新一次；超过 300ms 没更新 → 判定任务卡死 →
       进入 SAFE_STATE 并上报 WATCHDOG 故障；心跳恢复 → 自动恢复；
    3. 降级 / 安全状态（Degraded / Safe State）
       NORMAL    正常输出（把收到的转向角安全值透传给下游）
       DEGRADED  连续 3 条 E2E 错误 或 计数器跳变超阈值 → 输出"安全值"（转向角=0）
                 + 故障标志，fault_level=1
       SAFE_STATE 心跳停止 → 输出无效 + fault_level=2

为什么把安全节点做成**独立进程 + 独立 UDP 现场总线**（127.0.0.1:30600）：
    - 故障注入需要"发送任意非法帧"（CRC 错/计数器跳变/重放），独立通道让测试端
      可以精确构造字节，不受真实 CAN 驱动"只能发合法帧"的限制；
    - 套件因此**不依赖 ECU/虚拟 CAN**，可以独立进 CI（见 docs/32 的边界说明）；
    - 真实项目中 E2E 帧跑在 CAN/CAN FD 上，本文件只把"传输层"换成 UDP，
      帧格式、状态机、错误统计算法完全一致（8 字节正好放进一帧经典 CAN）。

通信协议（JSON over UDP，请求/响应一一对应）::

    {"cmd": "ping"}                        → {"ok": true, "ready": true, ...}
    {"cmd": "status"}                      → 完整状态（状态机/输出/E2E 统计/看门狗）
    {"cmd": "e2e_frame", "frame": "<hex>"} → 校验一帧：accepted/reason/state/错误统计
    {"cmd": "stop_heartbeat"}              → 停止心跳（注入"任务卡死"）
    {"cmd": "resume_heartbeat"}            → 恢复心跳（注入"任务恢复"）
    {"cmd": "clear_fault"}                 → 清除故障标志/降级状态
    {"cmd": "reset"}                       → 全量复位（测试用例隔离用）
    {"cmd": "quit"}                        → 退出进程

启动方式（一般由 libraries/SafetyLibrary.py 拉起，也可以手动跑）::

    .\\.venv\\Scripts\\python.exe simulators\\safety_ecu.py
    .\\.venv\\Scripts\\python.exe simulators\\safety_ecu.py --port 30600 --watchdog-timeout-ms 300

端口可用环境变量 ``SAFETY_UDP_PORT`` 或 ``--port`` 覆盖（默认 30600）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time

log = logging.getLogger("safety_ecu")

# ---------------------------------------------------------------------------
# 通信参数
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
DEFAULT_UDP_PORT = int(os.environ.get("SAFETY_UDP_PORT", "30600"))

# ---------------------------------------------------------------------------
# E2E 帧格式（教学版 AUTOSAR E2E Profile 1，8 字节，正好一帧经典 CAN 的数据场）
#   byte 0..1  dataId  (uint16，大端)  0x5A01=控制命令帧（测试端→节点）
#                                       0x5A02=安全输出帧（节点→测试端）
#   byte 2     counter (本项目约定 0..14 循环；15 视为非法值)
#   byte 3..4  data    (int16，大端，转向角，单位 0.1°，如 150 = 15.0°)
#   byte 5     status  (0=正常输出，1=安全值，2=输出无效)
#   byte 6     fault   (fault_level：0/1/2)
#   byte 7     crc8    (CRC-8/SAE-J1850，覆盖 byte0..6)
# ---------------------------------------------------------------------------
CONTROL_DATA_ID = 0x5A01        # 测试端发给安全节点的"转向角指令"安全帧
OUTPUT_DATA_ID = 0x5A02         # 安全节点输出的"安全状态"帧（供测试端独立校验）
FRAME_LENGTH = 8
COUNTER_MOD = 15                # 本项目 E2E 约定：4bit counter 只用 0..14 循环（15 视为非法值）
COUNTER_JUMP_THRESHOLD = 3      # delta > 3（丢 4 帧以上）→ 判定"跳变超阈值"→ 立即降级
ERROR_THRESHOLD = 3             # 连续 3 条 E2E 校验失败 → DEGRADED
RECOVERY_VALID_FRAMES = 3       # 连续 3 条完全合法帧 → 自动恢复 NORMAL

# 安全值：降级时输出给下游的"安全转向角"（回正 / 保持 0，避免执行器乱动）
SAFE_STEERING_ANGLE_DEG = 0.0

# ---------------------------------------------------------------------------
# 看门狗 / 定时参数
# ---------------------------------------------------------------------------
DEFAULT_WATCHDOG_TIMEOUT_MS = 300
HEARTBEAT_INTERVAL_S = 0.05     # 内部任务心跳周期 50ms
MAINTENANCE_INTERVAL_S = 0.02   # 看门狗巡检 + 输出帧发送的节拍 20ms
TX_INTERVAL_S = 0.05            # 节点每 50ms 发一帧 E2E 安全状态帧
WATCHDOG_RECOVER_RATIO = 0.5    # 心跳年龄 < 阈值×0.5 才认为"心跳真的恢复了"

STATES = ("NORMAL", "DEGRADED", "SAFE_STATE")
FAULT_FLAGS = ("E2E_CRC_ERROR", "E2E_COUNTER_ERROR", "E2E_REPLAY", "WATCHDOG")


# ===========================================================================
# CRC-8 / SAE J1850（AUTOSAR E2E Profile 1 使用的 CRC 算法）
# ===========================================================================
def crc8_sae_j1850(data: bytes) -> int:
    """CRC-8/SAE-J1850：多项式 0x1D、初值 0xFF、输出异或 0xFF、不反射。

    参数（与 docs/32 的 CRC 参数表一致，可对照公开 check 值验证）：
        poly   = 0x1D  (x^8 + x^4 + x^3 + x^2 + 1)
        init   = 0xFF
        xorout = 0xFF
        refin/refout = False（不反射，MSB first）

    公开校验值：``crc8_sae_j1850(b"123456789") == 0x4B`` —— 单元测试会用它
    证明本实现（以及测试端的独立实现）没有把算法写错。

    注意：``libraries/SafetyLibrary.py`` 里有一份**刻意重复**的独立实现，
    测试端不会 import 本文件的函数 —— 安全机制不能"自己证明自己"（见 docs/32）。
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


class SafetyNode:
    """安全节点：E2E 接收校验 + 看门狗 + 降级状态机 + E2E 状态帧发送。"""

    def __init__(self, port: int = DEFAULT_UDP_PORT,
                 watchdog_timeout_ms: int = DEFAULT_WATCHDOG_TIMEOUT_MS) -> None:
        self.port = int(port)
        self.watchdog_timeout_s = float(watchdog_timeout_ms) / 1000.0
        self._lock = threading.RLock()
        self._running = True
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((HOST, self.port))
        except OSError as exc:
            raise SystemExit(
                f"[safety_ecu] 绑定 {HOST}:{self.port} 失败：{exc}\n"
                f"  端口被占用？换端口：--port 30601 或设置环境变量 SAFETY_UDP_PORT。")
        self._sock.settimeout(0.1)

        self._reset_state()
        log.info("安全节点已启动：%s:%d，watchdog=%.0fms，E2E 帧=%d 字节 "
                 "(control=%#06x, output=%#06x)",
                 HOST, self.port, self.watchdog_timeout_s * 1000,
                 FRAME_LENGTH, CONTROL_DATA_ID, OUTPUT_DATA_ID)

    # ------------------------------------------------------------------
    # 状态复位（初始化 / 测试隔离）
    # ------------------------------------------------------------------
    def _reset_state(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._started_at = now
            # 状态机
            self._state = "NORMAL"
            self._fault_level = 0
            self._fault_flags = {flag: False for flag in FAULT_FLAGS}
            self._degraded_active = False
            self._watchdog_fault_active = False
            # E2E 接收统计
            self._rx_total = 0
            self._rx_valid = 0
            self._crc_errors = 0
            self._counter_errors = 0
            self._replays = 0
            self._lost_frames = 0
            self._bad_frames = 0
            self._error_streak = 0
            self._recovery_streak = 0
            self._degraded_count = 0
            self._recover_count = 0
            # 计数器：从 14 开始 → 第一帧期望 counter=0（delta = (0-14)%15 = 1）
            self._last_rx_counter = COUNTER_MOD - 1
            # 输出（安全节点透传给下游执行器的转向角）
            self._last_valid_angle_deg = 0.0
            self._output_valid = True
            self._output_angle_deg = 0.0
            self._output_mode = "NORMAL"
            # E2E 发送（安全状态帧）
            self._tx_counter = 0
            self._tx_frames = 0
            self._last_tx_frame: str | None = None
            self._last_tx_time = 0.0
            # 看门狗
            self._heartbeat_enabled = True
            self._heartbeat_count = 0
            self._last_heartbeat = now
            self._watchdog_trips = 0
            self._watchdog_reaction_ms: float | None = None
        log.info("安全状态已复位（NORMAL，计数器 0，心跳恢复）")

    # ------------------------------------------------------------------
    # 线程 1：内部任务心跳（看门狗的被监控对象）
    # ------------------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                if self._heartbeat_enabled:
                    self._heartbeat_count += 1
                    self._last_heartbeat = time.monotonic()
            self._stop_event.wait(HEARTBEAT_INTERVAL_S)

    # ------------------------------------------------------------------
    # 线程 2：看门狗巡检 + 周期发送 E2E 安全状态帧
    # ------------------------------------------------------------------
    def _maintenance_loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._lock:
                self._watchdog_tick(now)
                if now - self._last_tx_time >= TX_INTERVAL_S:
                    self._send_e2e_output_frame(now)
            self._stop_event.wait(MAINTENANCE_INTERVAL_S)

    def _watchdog_tick(self, now: float) -> None:
        """看门狗核心：心跳年龄超过阈值 → SAFE_STATE；心跳恢复 → 自动恢复。"""
        age = now - self._last_heartbeat
        if self._watchdog_fault_active:
            if age < self.watchdog_timeout_s * WATCHDOG_RECOVER_RATIO:
                self._watchdog_fault_active = False
                self._fault_flags["WATCHDOG"] = False
                self._recover_count += 1
                self._recompute_state()
                log.warning("看门狗恢复：心跳恢复正常（年龄 %.0fms）→ 状态 %s",
                            age * 1000, self._state)
        elif age > self.watchdog_timeout_s:
            self._watchdog_fault_active = True
            self._fault_flags["WATCHDOG"] = True
            self._watchdog_trips += 1
            self._watchdog_reaction_ms = round(age * 1000.0, 1)
            self._recompute_state()
            log.warning("看门狗触发：心跳已 %.0fms 未更新（阈值 %.0fms）→ SAFE_STATE",
                        age * 1000, self.watchdog_timeout_s * 1000)

    def _send_e2e_output_frame(self, now: float) -> None:
        """把当前输出/故障等级打包成 E2E 保护的安全状态帧（供测试端独立验 CRC）。"""
        if self._output_angle_deg is None:
            raw = 0
        else:
            raw = max(-32768, min(32767, int(round(self._output_angle_deg * 10.0))))
        status_byte = {"NORMAL": 0, "SAFE_VALUE": 1, "INVALID": 2}[self._output_mode]
        header = (
            OUTPUT_DATA_ID.to_bytes(2, "big")
            + bytes([self._tx_counter & 0xFF])
            + raw.to_bytes(2, "big", signed=True)
            + bytes([status_byte, self._fault_level & 0xFF])
        )
        frame = header + bytes([crc8_sae_j1850(header)])
        self._tx_counter = (self._tx_counter + 1) % COUNTER_MOD
        self._tx_frames += 1
        self._last_tx_frame = frame.hex()
        self._last_tx_time = now

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _recompute_state(self) -> None:
        """按优先级推导状态机输出：SAFE_STATE > DEGRADED > NORMAL。"""
        if self._watchdog_fault_active:
            self._state = "SAFE_STATE"
            self._fault_level = 2
            self._output_valid = False
            self._output_angle_deg = None
            self._output_mode = "INVALID"
        elif self._degraded_active:
            self._state = "DEGRADED"
            self._fault_level = 1
            self._output_valid = True
            self._output_angle_deg = SAFE_STEERING_ANGLE_DEG   # 安全值：转向角回 0
            self._output_mode = "SAFE_VALUE"
        else:
            self._state = "NORMAL"
            self._fault_level = 0
            self._output_valid = True
            self._output_angle_deg = self._last_valid_angle_deg
            self._output_mode = "NORMAL"

    def _may_degrade(self, fault_flag: str, immediate: bool = False) -> None:
        """连续错误达阈值（或严重故障）→ 进入 DEGRADED + 安全值 + 故障标志。"""
        if self._degraded_active:
            return
        if immediate or self._error_streak >= ERROR_THRESHOLD:
            self._degraded_active = True
            self._degraded_count += 1
            self._fault_flags[fault_flag] = True
            self._recovery_streak = 0
            self._recompute_state()
            log.warning("进入 DEGRADED：%s（连续错误 %d 条%s）→ 输出安全值",
                        fault_flag, self._error_streak,
                        "，计数器跳变超阈值" if immediate else "")

    def _recover_from_degraded(self) -> None:
        """连续 N 条合法帧 → 自动退出 DEGRADED，清 E2E 故障标志。"""
        self._degraded_active = False
        for flag in FAULT_FLAGS:
            if flag != "WATCHDOG":
                self._fault_flags[flag] = False
        self._error_streak = 0
        self._recovery_streak = 0
        self._recover_count += 1
        self._recompute_state()
        log.warning("E2E 恢复：连续 %d 条合法帧 → 状态 %s",
                    RECOVERY_VALID_FRAMES, self._state)

    # ------------------------------------------------------------------
    # E2E 接收校验（核心）
    # ------------------------------------------------------------------
    def _process_e2e_frame(self, frame_hex: str) -> dict:
        """校验一帧 E2E 安全报文，更新统计/状态，返回处理结果。"""
        with self._lock:
            result = {
                "ok": True,
                "accepted": False,
                "reason": "",
                "lost_frames": 0,
                "counter": self._last_rx_counter,
                "state": self._state,
                "fault_level": self._fault_level,
                "error_streak": self._error_streak,
            }
            try:
                frame = bytes.fromhex(str(frame_hex).replace(" ", ""))
            except ValueError:
                self._bad_frames += 1
                result["reason"] = "bad_hex"
                return result
            if len(frame) != FRAME_LENGTH:
                self._bad_frames += 1
                result["reason"] = "bad_length"
                return result
            data_id = int.from_bytes(frame[0:2], "big")
            if data_id != CONTROL_DATA_ID:
                self._bad_frames += 1
                result["reason"] = "bad_data_id"
                return result

            counter = frame[2]
            if counter >= COUNTER_MOD:          # 15 是 E2E Profile 1 的非法计数值
                self._counter_errors += 1
                self._error_streak += 1
                self._recovery_streak = 0
                result["reason"] = "bad_counter"
                self._may_degrade("E2E_COUNTER_ERROR")
                self._fill_result(result)
                return result

            crc_ok = frame[7] == crc8_sae_j1850(frame[0:7])
            delta = (counter - self._last_rx_counter) % COUNTER_MOD
            # 教学简化：格式合法的帧都推进接收计数器（这样连续注入 CRC 错后
            # 仍能恢复序列）；只有 CRC 与序列都正确的帧才算"有效数据"。
            self._last_rx_counter = counter
            self._rx_total += 1

            if not crc_ok:
                self._crc_errors += 1
                self._error_streak += 1
                self._recovery_streak = 0
                result["reason"] = "crc_error"
                self._may_degrade("E2E_CRC_ERROR")
            elif delta == 0:                     # 重复帧 / 重放攻击
                self._replays += 1
                self._error_streak += 1
                self._recovery_streak = 0
                result["reason"] = "replay"
                self._may_degrade("E2E_REPLAY")
            elif delta >= 2:
                lost = delta - 1
                self._lost_frames += lost
                result["lost_frames"] = lost
                if delta > COUNTER_JUMP_THRESHOLD:
                    # 丢 4 帧以上：序列严重异常 → 立即降级（不等连续 3 次）
                    self._counter_errors += 1
                    self._error_streak += 1
                    self._recovery_streak = 0
                    result["reason"] = "counter_jump"
                    self._may_degrade("E2E_COUNTER_ERROR", immediate=True)
                else:
                    # 丢 1~2 帧：记录丢失但数据仍可用（可容忍的偶发丢帧）；
                    # 注意这不算"完全合法帧"，不推进自动恢复计数。
                    self._accept_valid_data(frame)
                    self._error_streak = 0
                    self._recovery_streak = 0
                    result["accepted"] = True
                    result["reason"] = "lost_frames"
            else:                                # delta == 1：正常递增
                self._accept_valid_data(frame)
                self._rx_valid += 1
                self._error_streak = 0
                result["accepted"] = True
                result["reason"] = "ok"
                if self._degraded_active:
                    self._recovery_streak += 1
                    if self._recovery_streak >= RECOVERY_VALID_FRAMES:
                        self._recover_from_degraded()
            self._fill_result(result)
            return result

    def _accept_valid_data(self, frame: bytes) -> None:
        """接收一帧合法数据：记住最新转向角，NORMAL 时透传为输出。"""
        raw = int.from_bytes(frame[3:5], "big", signed=True)
        self._last_valid_angle_deg = raw / 10.0
        self._recompute_state()

    def _fill_result(self, result: dict) -> None:
        result["counter"] = self._last_rx_counter
        result["state"] = self._state
        result["fault_level"] = self._fault_level
        result["error_streak"] = self._error_streak

    # ------------------------------------------------------------------
    # 状态查询 / 命令处理
    # ------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            total_errors = self._crc_errors + self._counter_errors + self._replays
            return {
                "ok": True,
                "state": self._state,
                "fault_level": self._fault_level,
                "fault_flags": dict(self._fault_flags),
                "output": {
                    "valid": self._output_valid,
                    "steering_angle_deg": self._output_angle_deg,
                    "status": {"NORMAL": 0, "SAFE_VALUE": 1, "INVALID": 2}[self._output_mode],
                    "mode": self._output_mode,
                },
                "e2e": {
                    "rx_total": self._rx_total,
                    "rx_valid": self._rx_valid,
                    "crc_errors": self._crc_errors,
                    "counter_errors": self._counter_errors,
                    "replays": self._replays,
                    "lost_frames": self._lost_frames,
                    "bad_frames": self._bad_frames,
                    "total_errors": total_errors,
                    "error_streak": self._error_streak,
                    "last_rx_counter": self._last_rx_counter,
                    "degraded_count": self._degraded_count,
                    "recover_count": self._recover_count,
                    "tx_frames": self._tx_frames,
                    "tx_counter": self._tx_counter,
                    "last_tx_frame": self._last_tx_frame,
                },
                "watchdog": {
                    "heartbeat_enabled": self._heartbeat_enabled,
                    "heartbeat_count": self._heartbeat_count,
                    "timeout_ms": round(self.watchdog_timeout_s * 1000.0, 1),
                    "reaction_ms": self._watchdog_reaction_ms,
                    "trips": self._watchdog_trips,
                    "last_heartbeat_age_ms": round((now - self._last_heartbeat) * 1000.0, 1),
                },
                "config": {
                    "counter_mod": COUNTER_MOD,
                    "counter_jump_threshold": COUNTER_JUMP_THRESHOLD,
                    "error_threshold": ERROR_THRESHOLD,
                    "recovery_valid_frames": RECOVERY_VALID_FRAMES,
                    "control_data_id": f"{CONTROL_DATA_ID:#06x}",
                    "output_data_id": f"{OUTPUT_DATA_ID:#06x}",
                },
                "uptime_s": round(now - self._started_at, 2),
            }

    def _handle(self, request: dict) -> dict:
        """处理一条 JSON 请求，返回响应 dict。"""
        cmd = str(request.get("cmd", "")).lower()
        if cmd in ("ping", "hello"):
            return {"ok": True, "ready": True, "port": self.port,
                    "control_data_id": f"{CONTROL_DATA_ID:#06x}",
                    "output_data_id": f"{OUTPUT_DATA_ID:#06x}"}
        if cmd == "status":
            return self.status()
        if cmd == "e2e_frame":
            return self._process_e2e_frame(str(request.get("frame", "")))
        if cmd == "stop_heartbeat":
            with self._lock:
                self._heartbeat_enabled = False
                log.warning("注入：内部任务心跳已停止（看门狗将在 %.0fms 后触发）",
                            self.watchdog_timeout_s * 1000)
            return self.status()
        if cmd == "resume_heartbeat":
            with self._lock:
                self._heartbeat_enabled = True
                self._last_heartbeat = time.monotonic()   # 任务恢复后心跳立即刷新
                log.warning("注入：内部任务心跳已恢复")
            return self.status()
        if cmd == "clear_fault":
            with self._lock:
                self._degraded_active = False
                self._watchdog_fault_active = False
                self._fault_flags = {flag: False for flag in FAULT_FLAGS}
                self._error_streak = 0
                self._recovery_streak = 0
                self._recompute_state()
                log.warning("手动清故障：状态回 %s（历史错误计数保留）", self._state)
            return self.status()
        if cmd == "reset":
            self._reset_state()
            return self.status()
        if cmd == "quit":
            self._running = False
            return {"ok": True, "bye": True}
        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    # ------------------------------------------------------------------
    # UDP 服务主循环 / 生命周期
    # ------------------------------------------------------------------
    def _dispatch(self, raw: bytes) -> dict:
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"bad json: {exc}"}
        if not isinstance(request, dict):
            return {"ok": False, "error": "request must be a JSON object"}
        try:
            return self._handle(request)
        except Exception as exc:  # noqa: BLE001 —— 单条命令异常不能打死节点
            log.exception("命令处理异常：%s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def run(self) -> None:
        for target, name in ((self._heartbeat_loop, "heartbeat"),
                             (self._maintenance_loop, "maintenance")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        print("=" * 62)
        print(f" 功能安全节点已启动   UDP {HOST}:{self.port}")
        print(f" E2E: control={CONTROL_DATA_ID:#06x} output={OUTPUT_DATA_ID:#06x} "
              f"frame={FRAME_LENGTH}B counter=0..{COUNTER_MOD - 1}")
        print(f" 看门狗: 心跳 {HEARTBEAT_INTERVAL_S * 1000:.0f}ms / "
              f"阈值 {self.watchdog_timeout_s * 1000:.0f}ms → SAFE_STATE")
        print("=" * 62)
        try:
            while self._running:
                try:
                    raw, addr = self._sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                response = self._dispatch(raw)
                try:
                    self._sock.sendto(
                        json.dumps(response, ensure_ascii=False).encode("utf-8"),
                        addr)
                except OSError:
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._running = False
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        try:
            self._sock.close()
        except OSError:
            pass
        log.info("安全节点已退出")


def main() -> None:
    parser = argparse.ArgumentParser(description="功能安全节点模拟器（E2E/看门狗/降级）")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT,
                        help=f"UDP 端口（默认 {DEFAULT_UDP_PORT}，也可用 SAFETY_UDP_PORT）")
    parser.add_argument("--watchdog-timeout-ms", type=int,
                        default=DEFAULT_WATCHDOG_TIMEOUT_MS,
                        help="看门狗超时阈值（毫秒，默认 300）")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    node = SafetyNode(port=args.port,
                      watchdog_timeout_ms=args.watchdog_timeout_ms)
    node.run()


if __name__ == "__main__":
    main()
