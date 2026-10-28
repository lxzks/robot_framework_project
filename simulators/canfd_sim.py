"""
canfd_sim.py — CAN FD 高速总线教学仿真器（独立进程）
====================================================

为什么需要单独的 CAN FD 仿真器？
--------------------------------
经典 CAN（本项目 233.255.0.1:30000，见 simulators/bus.py）每帧最多 8 字节、
固定位速率。CAN FD（Flexible Data-rate）把**数据段**扩到最长 64 字节，并在
数据段切到更高速率（BRS=1 时最高 5~8Mbps）——ADAS 点云摘要、OTA 元数据、
大块传感器融合结果都靠它传。

经典 CAN 的 python-can UDP 组播模型表达不了 FD 的三个关键新特性：
    ① 数据长度不再是 0~8，而是 DLC 0~15 → 0/1/.../8/12/16/20/24/32/48/64
    ② BRS（Bit Rate Switch）：仲裁段慢速、数据段高速
    ③ ESI（Error State Indicator）：发送节点是否处于 error-passive
所以本模块**不复用**经典 CAN 虚拟总线，而是独立仿真：

    ┌──────────────────────────────┐    UDP 组播 233.255.0.3:30003     ┌──────────────────────────┐
    │ simulators/canfd_sim.py      │ ────────────────────────────────▶ │ libraries/CanFdLibrary.py │
    │  （ADAS 域控 = 被测节点）     │   JSON 帧：seq/id/data_hex/dlc/   │  （RF 测试端）            │
    │  · 0x400 VehicleStatusFD 20ms│   is_fd/brs/esi/ts + counter      │  · 组播收帧 + 本地校验     │
    │  · 0x401 ObjectList    50ms  │                                    │  · 手工解码 float32/位图   │
    │  · 0x402 ControlCommand 回显 │ ◀──────────────────────────────── │  · 发送测试帧（组播）      │
    └──────────────────────────────┘   测试帧（origin=tester）         └──────────────────────────┘
                                                 │
                                    TCP 127.0.0.1:30004 控制口（JSON 行，一问一答）
                                    set_signal / inject_error / silence / stats / quit ...

端口约定（可用环境变量覆盖，避免和同事/CI 抢端口）：
    CANFD_BUS_PORT   默认 30003（组播帧流；与经典 CAN 的 30000 严格错开）
    CANFD_CTRL_PORT  默认 30004（本机 TCP 控制口）
    CANFD_BUS_GROUP  默认 233.255.0.3（组播组；一般不用改）

CAN FD 帧结构（等价模拟）
-------------------------
真实 CAN FD 一帧：`SOF | 仲裁段 | 控制段(DLC+IDE+FDF+BRS+ESI) | 数据段(0~64B) | CRC(17/21bit) | ACK | EOF`
本仿真用 JSON 表达同一语义：

    {"kind":"frame", "seq":123, "id":1024, "data_hex":"...", "dlc":15,
     "is_fd":true, "brs":true, "esi":false, "ts":..., "counter":45,
     "crc_valid":true, "crc":"0x1A2B3C", "valid":true, "error":null,
     "origin":"adas", "echo_of":null}

  - seq      ：仿真器发出的全局帧序号（只增不减，测试端用来做 since 标记）
  - counter  ：**同一 ID 的发送计数器**（丢帧检测用：正常每帧 +1，跳号=丢帧）
  - crc_valid：CRC 是否正确（真实 CAN FD 由硬件算 CRC17/21；本仿真用简化 CRC32 展示）
  - valid    ：帧结构是否合法（注入"长度不符"时 valid=false）
  - origin   ：adas=仿真节点发出的帧；tester=测试端发上总线的帧（库端过滤掉）

DLC 映射表（CAN FD 的"长度压缩编码"，必须会背）
------------------------------------------------
    DLC : 0 1 2 3 4 5 6 7 8 9  10 11 12 13 14 15
    字节: 0 1 2 3 4 5 6 7 8 12 16 20 24 32 48 64
DLC 9~15 在经典 CAN 里是非法值；CAN FD 用它们表达 12/16/20/24/32/48/64 字节。

错误注入（教学版故障注入，负向测试用）
--------------------------------------
    crc_error        CRC 字段被改坏（crc_valid=false）
    length_mismatch  帧的 data 长度与 DLC 映射长度不一致（valid=false，error="length_mismatch"）
    sequence_loss    该帧"丢帧"：counter 照常 +1 但不上总线（下一帧 counter 跳号，可被测试发现）
测试端还可以故意发"长度不符"的帧，仿真器会**拒绝并记入错误日志**（不做回显），
用于验证接收侧输入校验。

运行方式（一般由 libraries/CanFdLibrary.py 懒启动）::

    .venv\\Scripts\\python.exe simulators/canfd_sim.py
    set CANFD_BUS_PORT=31003 && set CANFD_CTRL_PORT=31004 && python simulators/canfd_sim.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
import zlib
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# 端口/组播约定（库端读同一组环境变量）
# ---------------------------------------------------------------------------
DEFAULT_BUS_GROUP = os.environ.get("CANFD_BUS_GROUP", "233.255.0.3")
DEFAULT_BUS_PORT = int(os.environ.get("CANFD_BUS_PORT", "30003"))
DEFAULT_CTRL_PORT = int(os.environ.get("CANFD_CTRL_PORT", "30004"))

# CAN FD 的 DLC → 数据长度映射（0~8 与经典 CAN 相同，9~15 是 FD 扩展）
DLC_TO_LENGTH: Dict[int, int] = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
    9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
}
LENGTH_TO_DLC: Dict[int, int] = {length: dlc for dlc, length in DLC_TO_LENGTH.items()}

# 周期报文（ADAS 域控节点）
ID_VEHICLE_STATUS = 0x400
ID_OBJECT_LIST = 0x401
ID_CONTROL_COMMAND = 0x402
PERIOD_STATUS_S = 0.020          # 0x400 每 20ms 一帧
PERIOD_OBJECTS_S = 0.050         # 0x401 每 50ms 一帧
TICK_S = 0.005                   # 调度器基础节拍（5ms），保证 20/50ms 都能整除

# 12 个警告灯（顺序与经典 CAN 0x300 TelltaleStatus 一致，方便对比学习）
TELLTALE_NAMES = [
    "MilWarning", "OilPressureLow", "CoolantTempHigh", "BatteryLow",
    "AbsFault", "AirbagFault", "SeatbeltWarning", "TurnLeft",
    "TurnRight", "HighBeam", "LowBeam", "ParkBrake",
]

# 默认信号值（可被 set_signal 控制）
DEFAULT_SIGNALS: Dict[str, Any] = {
    "speed_kmh": 88.5,
    "engine_rpm": 2400,
    "gear": 3,                   # 3=D 档（0=P 1=R 2=N 3=D 4=S 5=L）
    "vehicle_mode": 1,           # 1=DRIVING
    "telltale_bitmap": 0x000,    # 12 位灯位图，0=全灭
    "battery_voltage": 13.8,
    "accel": 0.02,
    "odometer": 12345.6,
}
DEFAULT_OBJECTS: List[Dict[str, float]] = [
    {"x": 10.0, "y": 0.5, "distance": 10.01},
    {"x": 20.0, "y": -1.5, "distance": 20.06},
    {"x": 35.0, "y": 3.0, "distance": 35.13},
    {"x": 50.0, "y": -0.2, "distance": 50.00},
]

log = logging.getLogger("canfd.sim")


# ---------------------------------------------------------------------------
# 协议辅助：DLC 映射 / 报文编码 / CRC
# ---------------------------------------------------------------------------
def dlc_to_length(dlc: int) -> int:
    """查询 DLC 对应的数据长度；非法 DLC 抛 ValueError。"""
    if dlc not in DLC_TO_LENGTH:
        raise ValueError(f"非法 DLC {dlc}（CAN FD 只允许 0~15）")
    return DLC_TO_LENGTH[dlc]


def length_to_dlc(length: int) -> int:
    """把"数据长度"反查成 DLC；CAN FD 只支持特定长度集合。"""
    if length not in LENGTH_TO_DLC:
        legal = ", ".join(str(item) for item in sorted(LENGTH_TO_DLC))
        raise ValueError(f"CAN FD 不支持 {length} 字节数据（合法长度：{legal}）")
    return LENGTH_TO_DLC[length]


def compute_crc(data: bytes) -> str:
    """教学版 CRC：用标准库 zlib.crc32 代表 CAN FD 的 CRC17/21 字段。

    真实 CAN FD 的 CRC 长度随数据长度变化（≤16 字节用 CRC17，>16 用 CRC21），
    由硬件计算。本项目只需要一个"可独立复算、能注入错误"的校验值，
    因此固定用 CRC32；测试端不依赖它做业务断言，只用于注入/展示。
    """
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


# ---------------------------------------------------------------------------
# CAN FD 报文的"数据场编码"函数（与 simulators/dbc/vehicle_fd.dbc 一致）
# ---------------------------------------------------------------------------
def _put_float32(buffer: bytearray, offset: int, value: float) -> None:
    """在指定字节偏移写入 IEEE-754 单精度 float（小端，对应 DBC 的 @1 形式）。"""
    buffer[offset:offset + 4] = struct.pack("<f", float(value))


def _put_uint16(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset:offset + 2] = struct.pack("<H", int(value) & 0xFFFF)


def _put_uint32(buffer: bytearray, offset: int, value: int) -> None:
    buffer[offset:offset + 4] = struct.pack("<I", int(value) & 0xFFFFFFFF)


class CanFdSimulator:
    """一台被仿真的 CAN FD 总线：ADAS 域控节点 + 测试端注入通道。"""

    def __init__(self, bus_group: str = DEFAULT_BUS_GROUP,
                 bus_port: int = DEFAULT_BUS_PORT,
                 ctrl_port: int = DEFAULT_CTRL_PORT) -> None:
        self.bus_group = bus_group
        self.bus_port = bus_port
        self.ctrl_port = ctrl_port

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.started_at = time.time()

        # ---- 总线状态 ----
        self.seq = 0                            # 全局帧序号（只增不减）
        self.counter: Dict[int, int] = defaultdict(int)   # 每个 ID 的发送计数器
        self.per_id_count: Dict[int, int] = defaultdict(int)
        self.echo_count = 0                     # 回显的测试帧数量
        self.rejected_count = 0                 # 被拒绝的非法测试帧数量
        self.injected_count = 0
        self.silent = False                     # True=暂停周期发送（FD Bus Should Be Silent）
        self.signals = dict(DEFAULT_SIGNALS)
        self.objects = [dict(item) for item in DEFAULT_OBJECTS]
        self.injections: List[Dict[str, Any]] = []
        self.error_log: Deque[Dict[str, Any]] = deque(maxlen=100)

        # ---- socket ----
        self._sock: Optional[socket.socket] = None
        self._server: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._sched_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 组播
    def _open_bus_socket(self) -> socket.socket:
        """打开"既能发组播、又能收组播"的 UDP socket。

        Windows/Linux 通用套路：
            1. SO_REUSEADDR 允许测试库和仿真器**同时**绑定 30003（组播的
               正常用法：多个监听者各收一份）；
            2. bind(("", port)) 绑通配地址（不能 bind 组播地址，Windows 报 10049）；
            3. IP_ADD_MEMBERSHIP 加入组播组，收该组所有帧；
            4. IP_MULTICAST_LOOP=1：本机自己发的帧也回环一份（测试库依赖它收 echo）。
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.bus_port))
        mreq = struct.pack("4s4s", socket.inet_aton(self.bus_group),
                           socket.inet_aton("0.0.0.0"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.settimeout(0.2)
        return sock

    def _send_line(self, payload: Dict[str, Any]) -> None:
        """把一条 JSON 帧发到组播组（等价于 CAN FD 控制器把帧发上总线）。"""
        assert self._sock is not None
        try:
            self._sock.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              (self.bus_group, self.bus_port))
        except OSError as exc:  # pragma: no cover
            log.warning("组播发送失败（%s:%s）：%s", self.bus_group, self.bus_port, exc)

    # ------------------------------------------------------------------ 数据编码
    def _build_vehicle_status(self) -> bytes:
        """0x400 VehicleStatusFD：64 字节，字段布局见 vehicle_fd.dbc。"""
        data = bytearray(64)
        _put_float32(data, 0, self.signals["speed_kmh"])
        _put_uint16(data, 4, self.signals["engine_rpm"])
        # GearPosition = byte6 bit0~3，VehicleMode = byte6 bit4~7（与 vehicle_fd.dbc 的
        # 48|4@1+ 和 52|4@1+ 两个信号位布局一致）
        gear = int(self.signals["gear"]) & 0x0F
        mode = int(self.signals["vehicle_mode"]) & 0x0F
        data[6] = gear | (mode << 4)
        # 12 个警告灯位图：bit0~bit11 对应 TELLTALE_NAMES（byte 8~9，小端）
        _put_uint16(data, 8, int(self.signals["telltale_bitmap"]) & 0x0FFF)
        # 时间戳 uint32（毫秒，滚动，用于验证"帧在持续刷新"）
        _put_uint32(data, 10, int(time.time() * 1000) & 0xFFFFFFFF)
        _put_float32(data, 14, self.signals["battery_voltage"])
        _put_float32(data, 18, self.signals["accel"])
        _put_float32(data, 22, self.signals["odometer"])
        # 其余字节填固定图案，模拟"大报文里的保留区/原始数据"
        for i in range(26, 64):
            data[i] = (i * 7) & 0xFF
        return bytes(data)

    def _build_object_list(self) -> bytes:
        """0x401 ObjectList：64 字节，4 个障碍物 × (x,y,distance) 三个 float32。"""
        data = bytearray(64)
        for index, obj in enumerate(self.objects[:4]):
            base = index * 12
            _put_float32(data, base + 0, obj["x"])
            _put_float32(data, base + 4, obj["y"])
            _put_float32(data, base + 8, obj["distance"])
        data[48] = len(self.objects) & 0xFF      # ObjectCount
        for i in range(49, 64):
            data[i] = 0xEE                       # 保留区图案
        return bytes(data)

    # ------------------------------------------------------------------ 发送
    def _take_injection(self, frame_id: int) -> Optional[Dict[str, Any]]:
        """取出一个作用于该帧 ID 的注入任务（没有则 None）。"""
        for index, job in enumerate(self.injections):
            if job["id"] is None or job["id"] == frame_id:
                job["remaining"] -= 1
                if job["remaining"] <= 0:
                    self.injections.pop(index)
                return job
        return None

    def _emit(self, frame_id: int, data: bytes, *, brs: bool, esi: bool,
              is_fd: bool = True, echo_of: Optional[int] = None,
              reason: str = "schedule") -> Optional[Dict[str, Any]]:
        """发出（或按注入规则丢弃）一帧 CAN FD 报文。调用方需持有 self._lock。"""
        self.counter[frame_id] += 1
        counter = self.counter[frame_id]
        job = self._take_injection(frame_id)
        if job is not None and job["kind"] == "sequence_loss":
            # 丢帧：计数器已经 +1（真实 ECU 的发送计数器不受总线丢帧影响），
            # 下一帧的 counter 会出现"跳号"——测试端据此检测丢帧。
            self.injected_count += 1
            log.info("注入生效：0x%03X 丢帧（counter=%d 被消耗、不发总线）",
                     frame_id, counter)
            return None

        dlc = length_to_dlc(len(data))
        payload = bytearray(data)
        valid = True
        error = None
        crc_valid = True
        if job is not None and job["kind"] == "length_mismatch":
            # 保持 dlc 不变，但把数据截短 → 长度与 DLC 映射不符（结构非法）
            cut = max(1, len(payload) - 12)
            payload = payload[:cut]
            valid = False
            error = "length_mismatch"
            self.injected_count += 1
            log.info("注入生效：0x%03X 长度不符（DLC=%d 应为 %d 字节，实际 %d 字节）",
                     frame_id, dlc, DLC_TO_LENGTH[dlc], len(payload))
        # CRC 按"最终上总线的数据"计算（先做长度注入，再算 CRC）
        crc = compute_crc(bytes(payload))
        if job is not None and job["kind"] == "crc_error":
            crc = f"{int(crc, 16) ^ 0x5A5A5A5A:08X}"
            crc_valid = False
            self.injected_count += 1
            log.info("注入生效：0x%03X CRC 错误", frame_id)

        self.seq += 1
        self.per_id_count[frame_id] += 1
        frame = {
            "kind": "frame",
            "seq": self.seq,
            "id": frame_id,
            "data_hex": bytes(payload).hex(),
            "dlc": dlc,
            "is_fd": bool(is_fd),
            "brs": bool(brs),
            "esi": bool(esi),
            "ts": time.time(),
            "counter": counter,
            "crc": crc,
            "crc_valid": crc_valid,
            "valid": valid,
            "error": error,
            "origin": "adas",
            "echo_of": echo_of,
            "reason": reason,
            "periodic": echo_of is None,
        }
        self._send_line(frame)
        log.debug("TX seq=%d id=0x%03X dlc=%d len=%d brs=%s esi=%s %s",
                  self.seq, frame_id, dlc, len(payload), brs, esi,
                  "valid" if valid else f"INVALID({error})")
        return frame

    # ------------------------------------------------------------------ 调度器
    def _scheduler_loop(self) -> None:
        """周期发送 0x400（20ms）与 0x401（50ms）。

        Windows 上定时精度坑见 23 章 LIN 模块：threading.Event.wait 的短超时
        会被系统定时器量化，导致周期抖动；这里同样用 time.sleep()（3.11+
        在 Windows 用高精度 waitable timer）。
        """
        next_tick = time.monotonic()
        tick = 0
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(next_tick - now)
                continue
            if now - next_tick > 0.10:
                next_tick = now
            tick += 1
            try:
                with self._lock:
                    if not self.silent:
                        if tick % 4 == 0:          # 每 4×5ms = 20ms
                            self._emit(ID_VEHICLE_STATUS,
                                       self._build_vehicle_status(),
                                       brs=True, esi=False, reason="schedule")
                        if tick % 10 == 0:         # 每 10×5ms = 50ms
                            self._emit(ID_OBJECT_LIST,
                                       self._build_object_list(),
                                       brs=True, esi=False, reason="schedule")
            except Exception:  # noqa: BLE001 - 单帧异常不杀死仿真进程
                log.exception("周期发送失败（tick=%d）", tick)
            next_tick += TICK_S
        log.info("调度器已退出")

    # ------------------------------------------------------------------ 接收测试端帧
    def _validate_tester_frame(self, message: Dict[str, Any]) -> tuple[bytes, int, bool, bool, bool, Optional[str]]:
        """校验测试端发来的帧；返回 (data, dlc, is_fd, brs, esi, error)。

        error 非 None 表示该帧**被拒绝**（不做回显），同时计入错误日志。
        这是真实的"接收侧输入校验"：总线上的非法帧不能被节点接受。
        """
        frame_id = message.get("id")
        dlc = message.get("dlc")
        is_fd = bool(message.get("is_fd", True))
        brs = bool(message.get("brs", False))
        esi = bool(message.get("esi", False))
        data_hex = str(message.get("data_hex", ""))
        try:
            frame_id = int(frame_id)
            dlc = int(dlc)
        except (TypeError, ValueError):
            return b"", 0, is_fd, brs, esi, "bad_field_type"
        if not 0 <= frame_id <= 0x7FF:
            return b"", dlc, is_fd, brs, esi, "bad_frame_id"
        if not 0 <= dlc <= 15:
            return b"", dlc, is_fd, brs, esi, "bad_dlc"
        try:
            data = bytes.fromhex(data_hex.replace(" ", ""))
        except ValueError:
            return b"", dlc, is_fd, brs, esi, "bad_hex"
        expected_length = DLC_TO_LENGTH[dlc]
        if len(data) != expected_length:
            return data, dlc, is_fd, brs, esi, (
                f"length_mismatch（DLC={dlc} 要求 {expected_length} 字节，实际 {len(data)} 字节）")
        if not is_fd and dlc > 8:
            return data, dlc, is_fd, brs, esi, (
                f"classic_dlc_overflow（经典 CAN 帧 DLC 最大 8，收到 {dlc}）")
        if not is_fd and len(data) > 8:
            return data, dlc, is_fd, brs, esi, (
                f"classic_length_overflow（经典 CAN 最多 8 字节，收到 {len(data)}）")
        if not is_fd and brs:
            return data, dlc, is_fd, brs, esi, "brs_on_classic（经典 CAN 没有 BRS 位）"
        return data, dlc, is_fd, brs, esi, None

    def _handle_tester_frame(self, message: Dict[str, Any]) -> None:
        """处理测试端发上总线的帧：非法→拒绝并记日志；合法→回显到总线。"""
        data, dlc, is_fd, brs, esi, error = self._validate_tester_frame(message)
        try:
            frame_id = int(message.get("id") or 0)
        except (TypeError, ValueError):
            frame_id = -1
        echo_of = message.get("tester_seq")
        with self._lock:
            if error is not None:
                self.rejected_count += 1
                self.error_log.append({
                    "ts": time.time(),
                    "error": error.split("（")[0],
                    "detail": error,
                    "id": frame_id,
                    "dlc": message.get("dlc"),
                    "data_bytes": len(data),
                })
                log.warning("拒绝非法测试帧：%s", error)
                return
            self.echo_count += 1
            # 回显：仿真器重新按自己的 seq 发一遍（测试端读到的是"总线上的帧"，
            # 而不是自己发出去的那份——避免自证式断言）。
            self._emit(frame_id, data, brs=brs, esi=esi, is_fd=is_fd,
                       echo_of=int(echo_of) if echo_of is not None else None,
                       reason="tester_echo")

    def _rx_loop(self) -> None:
        """收组播数据报：只处理 origin=tester 的帧（自己发的 adas 帧忽略）。"""
        assert self._sock is not None
        sock = self._sock
        while not self._stop.is_set():
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
            if message.get("origin") != "tester":
                continue            # 组播回环会收到自己发的帧，忽略
            try:
                self._handle_tester_frame(message)
            except Exception:  # noqa: BLE001
                log.exception("处理测试帧失败")

    # ------------------------------------------------------------------ 控制命令
    def _dispatch(self, line: bytes) -> Dict[str, Any]:
        try:
            req = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"控制命令不是合法 JSON：{exc}"}
        if not isinstance(req, dict):
            return {"ok": False, "error": "控制命令必须是 JSON 对象"}
        cmd = req.get("cmd", "")
        try:
            if cmd == "ping":
                return {"ok": True, "pid": os.getpid(), "seq": self.seq,
                        "bus_port": self.bus_port, "bus_group": self.bus_group,
                        "ctrl_port": self.ctrl_port}
            if cmd == "stats":
                return self._stats_response()
            if cmd == "set_signal":
                return self._cmd_set_signal(req)
            if cmd == "set_object":
                return self._cmd_set_object(req)
            if cmd == "inject_error":
                return self._cmd_inject(req)
            if cmd == "silence":
                return self._cmd_silence(req)
            if cmd == "errors":
                return self._cmd_errors(req)
            if cmd == "quit":
                return {"ok": True, "stopping": True}
            return {"ok": False, "error": f"未知命令 {cmd!r}"}
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _parse_id(value: Any) -> int:
        if value is None:
            raise ValueError("缺少帧 id 参数")
        return int(value) if isinstance(value, int) else int(str(value), 0)

    def _cmd_set_signal(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """设置 ADAS 节点信号（下一帧周期报文生效）。"""
        signals = req.get("signals")
        if not isinstance(signals, dict) or not signals:
            raise ValueError('signals 不能为空，例：{"speed_kmh": 123.456}')
        allowed = set(DEFAULT_SIGNALS)
        with self._lock:
            for name, value in signals.items():
                if name not in allowed:
                    raise ValueError(f"未知信号 {name!r}，可选：{sorted(allowed)}")
                if name == "telltale_bitmap":
                    self.signals[name] = int(str(value), 0) & 0x0FFF
                elif name in ("engine_rpm", "gear", "vehicle_mode"):
                    self.signals[name] = int(str(value), 0)
                else:
                    self.signals[name] = float(value)
            return {"ok": True, "since_seq": self.seq,
                    "signals": dict(self.signals)}

    def _cmd_set_object(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """设置某个障碍物（index=1~4）的 x/y/distance。"""
        index = int(req.get("index", 0))
        if not 1 <= index <= 4:
            raise ValueError(f"障碍物 index 必须在 1~4，收到 {index}")
        with self._lock:
            obj = self.objects[index - 1]
            for name in ("x", "y", "distance"):
                if name in req:
                    obj[name] = float(req[name])
            return {"ok": True, "since_seq": self.seq,
                    "index": index, "object": dict(obj)}

    def _cmd_inject(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """排队错误注入任务（crc_error / length_mismatch / sequence_loss）。"""
        kind = req.get("kind")
        if kind not in ("crc_error", "length_mismatch", "sequence_loss"):
            raise ValueError(f"未知注入类型 {kind!r}，"
                             f"可选：crc_error / length_mismatch / sequence_loss")
        frame_id = req.get("id")
        fid = None if frame_id in (None, "") else self._parse_id(frame_id)
        if fid is not None and not 0 <= fid <= 0x7FF:
            raise ValueError(f"非法帧 ID {fid}")
        count = int(req.get("count", 1))
        if count <= 0:
            raise ValueError(f"count 必须为正整数，收到 {count}")
        with self._lock:
            self.injections.append({"kind": kind, "id": fid,
                                    "remaining": count, "count": count})
            log.info("排队错误注入：kind=%s id=%s count=%d",
                     kind, "任意帧" if fid is None else f"0x{fid:03X}", count)
            return {"ok": True, "kind": kind, "id": fid, "count": count,
                    "since_seq": self.seq, "pending": len(self.injections)}

    def _cmd_silence(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """暂停/恢复周期发送（模拟总线上暂时没有 FD 帧）。"""
        enabled = bool(req.get("enabled", True))
        with self._lock:
            self.silent = enabled
            log.info("周期发送%s", "已暂停（静默）" if enabled else "已恢复")
            return {"ok": True, "silent": self.silent, "since_seq": self.seq}

    def _cmd_errors(self, req: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            errors = list(self.error_log)
            if bool(req.get("clear", False)):
                self.error_log.clear()
            return {"ok": True, "count": len(errors), "errors": errors}

    def _stats_response(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "pid": os.getpid(),
                "uptime_s": round(time.time() - self.started_at, 3),
                "seq": self.seq,
                "emitted": self.seq,
                "echo_count": self.echo_count,
                "rejected_count": self.rejected_count,
                "injected_count": self.injected_count,
                "silent": self.silent,
                "pending_injections": len(self.injections),
                "per_id": {f"0x{fid:03X}": count
                           for fid, count in sorted(self.per_id_count.items())},
                "counters": {f"0x{fid:03X}": value
                             for fid, value in sorted(self.counter.items())},
                "bus_port": self.bus_port,
                "bus_group": self.bus_group,
                "ctrl_port": self.ctrl_port,
                "signals": dict(self.signals),
                "objects": [dict(item) for item in self.objects],
                "last_errors": list(self.error_log)[-5:],
            }

    # ------------------------------------------------------------------ 服务端
    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(10.0)
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    response = self._dispatch(line)
                    conn.sendall((json.dumps(response, ensure_ascii=False) + "\n")
                                 .encode("utf-8"))
                    if response.get("stopping"):
                        self._stop.set()
                        return
        except (ConnectionError, socket.timeout, OSError) as exc:
            log.debug("控制连接结束：%s", exc)

    def _serve(self) -> None:
        assert self._server is not None
        self._server.settimeout(0.3)
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            log.debug("控制连接来自 %s", addr)
            with conn:
                self._handle_client(conn)
        log.info("控制服务已退出")

    def run(self) -> int:
        """启动仿真器，阻塞到收到 quit / Ctrl+C。返回退出码。"""
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("127.0.0.1", self.ctrl_port))
            self._server.listen(8)
        except OSError as exc:
            print(
                f"[canfd_sim] [失败] TCP 控制端口 127.0.0.1:{self.ctrl_port} 绑定失败：{exc}\n"
                f"[canfd_sim]   常见原因：已有一个 canfd_sim 在运行（残留进程）或端口被占用。\n"
                f"[canfd_sim]   排查：netstat -ano | findstr :{self.ctrl_port}\n"
                f"[canfd_sim]   换端口：set CANFD_CTRL_PORT=31004（测试库读同一环境变量）",
                file=sys.stderr, flush=True)
            return 2
        try:
            self._sock = self._open_bus_socket()
        except OSError as exc:
            print(
                f"[canfd_sim] [失败] UDP 组播端口 {self.bus_group}:{self.bus_port} 绑定失败：{exc}\n"
                f"[canfd_sim]   排查：netstat -ano | findstr :{self.bus_port}\n"
                f"[canfd_sim]   换端口：set CANFD_BUS_PORT=31003（测试库读同一环境变量）",
                file=sys.stderr, flush=True)
            self._server.close()
            return 2

        print("[canfd_sim] CAN FD 教学仿真器已启动", flush=True)
        print(f"[canfd_sim]   UDP 组播帧 : {self.bus_group}:{self.bus_port}", flush=True)
        print(f"[canfd_sim]   TCP 控制口 : 127.0.0.1:{self.ctrl_port}", flush=True)
        print(f"[canfd_sim]   周期报文   : 0x400 VehicleStatusFD(20ms) / "
              f"0x401 ObjectList(50ms)，0x402 回显测试帧", flush=True)

        self._rx_thread = threading.Thread(target=self._rx_loop,
                                           name="canfd-rx", daemon=True)
        self._sched_thread = threading.Thread(target=self._scheduler_loop,
                                              name="canfd-scheduler", daemon=True)
        self._rx_thread.start()
        self._sched_thread.start()
        try:
            self._serve()
        except KeyboardInterrupt:  # pragma: no cover
            print("[canfd_sim] 收到 Ctrl+C，退出", flush=True)
        finally:
            self._stop.set()
            for thread in (self._rx_thread, self._sched_thread):
                if thread is not None:
                    thread.join(timeout=2.0)
            try:
                self._server.close()
            except OSError:
                pass
            try:
                if self._sock is not None:
                    self._sock.close()
            except OSError:
                pass
            print("[canfd_sim] 已退出", flush=True)
        return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAN FD 教学仿真器（UDP 组播发帧 + TCP 控制）")
    parser.add_argument("--bus-port", type=int,
                        default=int(os.environ.get("CANFD_BUS_PORT", "30003")),
                        help="UDP 组播端口（默认 30003 / CANFD_BUS_PORT）")
    parser.add_argument("--ctrl-port", type=int,
                        default=int(os.environ.get("CANFD_CTRL_PORT", "30004")),
                        help="TCP 控制端口（默认 30004 / CANFD_CTRL_PORT）")
    parser.add_argument("--group", default=os.environ.get("CANFD_BUS_GROUP", "233.255.0.3"),
                        help="组播组（默认 233.255.0.3）")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出 DEBUG 日志")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[canfd_sim] %(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    simulator = CanFdSimulator(bus_group=args.group, bus_port=args.bus_port,
                               ctrl_port=args.ctrl_port)
    return simulator.run()


if __name__ == "__main__":
    sys.exit(main())
