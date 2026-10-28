"""
lin_sim.py — LIN 2.x 教学版总线仿真器（独立进程）
=================================================

为什么需要单独的 LIN 仿真器？
--------------------------------
本项目已有虚拟 **CAN** 总线（233.255.0.1:30000，见 simulators/bus.py），
但 LIN 和 CAN 是两种完全不同的协议：

    CAN：多主、双线差分、靠仲裁抢总线、500kbps
    LIN：单主多从、单线 12V、**主机轮询从机才许回答**、≤20kbps

LIN 的"从机不许主动说话"这一条决定了它不能用 CAN 的"谁都能广播"模型
来模拟。所以本模块**不复用 CAN 虚拟总线**，而是做一个独立的 LIN 仿真进程：

    ┌──────────────────────────┐      UDP 127.0.0.1:30001       ┌────────────────────┐
    │  lin_sim.py（被仿真 LIN  │ ─────────────────────────────▶ │  LinLibrary.py     │
    │  总线：1 主机 + 4 从机） │   每帧一个 JSON（等价于示波器  │  （Robot Framework │
    │                          │   上抓到的一条 LIN 帧）        │   测试端 = 主机侧）│
    │                          │ ◀───────────────────────────── │                    │
    └──────────────────────────┘      TCP 127.0.0.1:30002       └────────────────────┘
                                     JSON 行控制命令（设置从机
                                     信号 / 主请求 / 注入错误）

  - **帧路径（UDP 单向下行）**：仿真器按调度表把总线上每一帧以 JSON 行
    发给测试库。真实台架上这一步是 CANoe/LIN 分析仪抓包；本仿真等价于
    "抓包流"。
  - **控制路径（TCP 请求-响应）**：测试库向仿真器下发命令，扮演真实
    LIN 主节点的行为（发主请求、改从机信号、注入总线故障）。
  - 两个端口都可用环境变量覆盖：LIN_UDP_PORT / LIN_CTRL_PORT，
    避免和同事的进程/CI 打架。

LIN 2.x 帧结构（本项目等价模拟的字段）
--------------------------------------
真实 LIN 一帧长这样（详见 docs/23_LIN总线与车身控制测试.md 的 ASCII 图）::

    BREAK(≥13 bit 显性) | SYNC(0x55) | PID(6 bit ID + 2 bit 奇偶) | DATA(1~8B) | CHECKSUM(1B)

本仿真器把每条帧模拟成下面这个 JSON（"等价模拟"指的是字段语义一致，
而不是真的在物理线上发 0/1）：

    {"kind":"frame", "seq":123, "ts":..., "break":true, "sync":85,
     "id":16, "pid":80, "data":[150], "checksum":193,
     "checksum_type":"enhanced", "checksum_valid":true,
     "pid_parity_valid":true, "direction":"slave_response", ...}

调度表（Schedule Table）
------------------------
LIN 主机按"调度表"轮询：每 **10ms** 发一个报头（header），从机收到和自己
ID 匹配的报头才填数据。本仿真调度表 4 个槽位，轮一圈 40ms::

    槽位 0 → 0x10 雨量传感器    （1 字节，增强校验和）
    槽位 1 → 0x11 雨刷状态      （1 字节，增强校验和）
    槽位 2 → 0x12 座椅位置      （2 字节，增强校验和）
    槽位 3 → 0x3C 主请求帧      （8 字节，经典校验和 —— LIN 2.x 规定主请求帧用经典算法）

睡眠/唤醒
---------
- 发 `send_master_request` 且 data[0]=0x00 → 先发一帧 0x3C（睡眠命令帧），
  然后停止调度（总线上没有任何帧 = 总线静默）。
- 发 `send_master_request` 且 data[0]=0x01，或 `wakeup` 命令 → 恢复调度。
  `wakeup` 会额外发一个 wakeup_pulse 事件（真实 LIN 是主机拉低总线
  250us~5ms 的"唤醒脉冲"）。

两种校验和（教学中必须会手算）
------------------------------
- 经典（classic，LIN 1.x / LIN 2.x 的主请求帧）：只累加数据字节；
- 增强（enhanced，LIN 2.x 其余帧）：PID + 数据字节一起累加（含进位回卷）。
最后取反码。本文件实现了两种算法，并可注入错误，供测试验证校验和检查
是真检查（负向用例）。

控制命令（JSON 一行一条，响应也是 JSON 一行）
---------------------------------------------
    {"cmd":"ping"}
    {"cmd":"set_slave","id":16,"signals":{"rain_intensity":75}}
    {"cmd":"send_master_request","data":[0,0,0,0,0,0,0,0]}
    {"cmd":"send_header","id":16}
    {"cmd":"inject_error","kind":"checksum|no_response|bad_parity","id":16,"count":1}
    {"cmd":"wakeup"}
    {"cmd":"stats"}
    {"cmd":"quit"}

运行方式（一般由 libraries/LinLibrary.py 懒启动，无需手工跑）::

    .venv\\Scripts\\python.exe simulators/lin_sim.py
    # 换端口：
    set LIN_UDP_PORT=31001 && set LIN_CTRL_PORT=31002 && python simulators/lin_sim.py
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
from collections import defaultdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 端口约定（必须与 libraries/LinLibrary.py 保持一致；均可用环境变量覆盖）
# ---------------------------------------------------------------------------
DEFAULT_UDP_PORT = int(os.environ.get("LIN_UDP_PORT", "30001"))
DEFAULT_CTRL_PORT = int(os.environ.get("LIN_CTRL_PORT", "30002"))
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_CTRL_HOST = "127.0.0.1"

# 调度表：每 10ms 轮询一个报文，4 个报文轮一圈 = 40ms
SLOT_SECONDS = 0.010
SCHEDULE_IDS: List[int] = [0x10, 0x11, 0x12, 0x3C]
SCHEDULE_TEXT = "0x10 雨量传感器 → 0x11 雨刷状态 → 0x12 座椅位置 → 0x3C 主请求帧"

# 每个报文的数据长度 & 校验和类型（LIN 2.x：主请求帧 0x3C 用经典校验和）
FRAME_LENGTH = {0x10: 1, 0x11: 1, 0x12: 2, 0x3C: 8}
FRAME_CHECKSUM = {0x10: "enhanced", 0x11: "enhanced", 0x12: "enhanced", 0x3C: "classic"}
FRAME_NAMES = {
    0x10: "RainSensorStatus",
    0x11: "WiperStatus",
    0x12: "SeatPosition",
    0x3C: "MasterRequest",
}
FRAME_DIRECTION = {0x3C: "master_request"}   # 其余视为从机应答

# ---------------------------------------------------------------------------
# 信号定义（与 simulators/ldf/vehicle.ldf 一一对应 —— 改一处必须同步改另一处）
# start: 信号起始 bit（LIN 是小端位序，bit0 = 首字节最低位）
# scale: 物理值 = 原始值 × scale + offset
# ---------------------------------------------------------------------------
SIGNAL_DEFS: Dict[int, Dict[str, Dict[str, Any]]] = {
    0x10: {
        "rain_intensity": {
            "start": 0, "length": 8, "scale": 0.5, "offset": 0.0,
            "minimum": 0.0, "maximum": 100.0, "unit": "%",
            "values": None,
            "comment": "雨量强度：原始值 0~200，物理值 0~100%（1 LSB = 0.5%）",
        },
    },
    0x11: {
        "wiper_state": {
            "start": 0, "length": 2, "scale": 1.0, "offset": 0.0,
            "minimum": 0.0, "maximum": 3.0, "unit": "",
            "values": {0: "OFF", 1: "INTERMITTENT", 2: "LOW", 3: "HIGH"},
            "comment": "雨刷档位：0=关 1=间歇 2=低速 3=高速",
        },
        "wiper_speed": {
            "start": 2, "length": 6, "scale": 100.0, "offset": 0.0,
            "minimum": 0.0, "maximum": 6300.0, "unit": "rpm",
            "values": None,
            "comment": "雨刷电机转速：原始值 ×100 rpm（6 bit 最大 6300rpm）",
        },
    },
    0x12: {
        "seat_slide": {
            "start": 0, "length": 8, "scale": 1.0, "offset": 0.0,
            "minimum": 0.0, "maximum": 255.0, "unit": "mm",
            "values": None,
            "comment": "座椅前后滑轨位置：0~255mm（1 LSB = 1mm）",
        },
        "seat_backrest": {
            "start": 8, "length": 8, "scale": 0.5, "offset": 0.0,
            "minimum": 0.0, "maximum": 127.5, "unit": "deg",
            "values": None,
            "comment": "座椅靠背角度：原始值 ×0.5°，0~127.5°（教学简化量程）",
        },
    },
    0x3C: {
        "master_cmd": {
            "start": 0, "length": 8, "scale": 1.0, "offset": 0.0,
            "minimum": 0.0, "maximum": 255.0, "unit": "",
            "values": {0: "SLEEP", 1: "WAKEUP"},
            "comment": "主请求命令字：0=睡眠 1=唤醒（其余值保留）",
        },
    },
}

# 默认从机信号原始值：上电全 0（雨量 0%、雨刷关、座椅 0mm/0°）
DEFAULT_RAW = {0x10: 0, 0x11: 0, 0x12: 0}
# 默认主请求数据：首字节 1（WAKEUP）表示"保持清醒"，否则一上电就睡眠了
DEFAULT_MASTER_DATA = [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

log = logging.getLogger("lin.sim")


# ---------------------------------------------------------------------------
# LIN 协议算法（测试库 libraries/LinLibrary.py 里有独立实现，用于交叉校验）
# ---------------------------------------------------------------------------
def calc_pid(frame_id: int) -> int:
    """由 6 bit 帧 ID 计算带 2 bit 奇偶校验的 PID（Protected Identifier）。

    公式（LIN 2.x 规范）::

        b0..b5 = ID 的 6 个 bit
        P0 = b0 ^ b1 ^ b2 ^ b4          （偶校验位）
        P1 = !(b1 ^ b3 ^ b4 ^ b5)       （奇校验位）
        PID = ID | (P0 << 6) | (P1 << 7)

    算例：0x3C = 111100b（b0=0,b1=0,b2=1,b3=1,b4=1,b5=1）
          P0 = 0^0^1^1 = 0，P1 = !(0^1^1^1) = 0 → PID = 0x3C
          （所以经典主请求帧的 PID 恰好等于 ID）
    """
    fid = frame_id & 0x3F
    b = [(fid >> i) & 1 for i in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 ^ (b[1] ^ b[3] ^ b[4] ^ b[5])
    return fid | (p0 << 6) | (p1 << 7)


def _fold_sum(raw_sum: int) -> int:
    """LIN 校验和先把累加和的高位进位"回卷"加到低 8 位，再取反。"""
    while raw_sum > 0xFF:
        raw_sum = (raw_sum & 0xFF) + (raw_sum >> 8)
    return (~raw_sum) & 0xFF


def classic_checksum(data: bytes) -> int:
    """经典校验和：只累加数据字节（LIN 1.x；LIN 2.x 的主请求帧 0x3C 用）。"""
    return _fold_sum(sum(data))


def enhanced_checksum(pid: int, data: bytes) -> int:
    """增强校验和：PID + 数据字节一起累加（LIN 2.x 的普通帧用）。"""
    return _fold_sum(pid + sum(data))


# ---------------------------------------------------------------------------
# 仿真器主体
# ---------------------------------------------------------------------------
class LinSimulator:
    """一台被仿真的 LIN 总线：1 个主节点（调度器）+ 若干从节点（信号存储）。"""

    def __init__(self, udp_port: int = DEFAULT_UDP_PORT,
                 ctrl_port: int = DEFAULT_CTRL_PORT) -> None:
        self.udp_port = udp_port
        self.ctrl_port = ctrl_port

        self._lock = threading.RLock()
        self._stop = threading.Event()

        # ---- 总线状态 ----
        self.seq = 0                      # 已发送帧序号（只增不减，测试端用它做 since 标记）
        self.skipped = 0                  # 因 no_response 注入而"没发出去"的帧数
        self.sleeping = False
        self.slot = 0
        self.cycle = 0                    # 调度表轮次（每发完一圈 +1）
        self.started_at = time.time()
        self.per_id_count: Dict[int, int] = defaultdict(int)
        self.injections: List[Dict[str, Any]] = []   # 待生效的错误注入任务
        self.total_injected = 0
        self.sleep_commands = 0
        self.wakeups = 0
        self.master_data = list(DEFAULT_MASTER_DATA)

        # 从机信号内存：帧 ID → 字节数组（从机"填"进帧的数据）
        self.slave_raw: Dict[int, bytearray] = {
            fid: bytearray(FRAME_LENGTH[fid]) for fid in FRAME_LENGTH if fid != 0x3C
        }
        for fid, raw in DEFAULT_RAW.items():
            if raw:
                self.slave_raw[fid][0] = raw & 0xFF

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._server: Optional[socket.socket] = None
        self._sched_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 基础
    def _send_line(self, payload: Dict[str, Any]) -> None:
        """把一条 JSON 消息发给 UDP 帧端口（帧或事件）。"""
        try:
            self._udp.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             (DEFAULT_UDP_HOST, self.udp_port))
        except OSError as exc:  # pragma: no cover - 只有对端 socket 异常才发生
            log.warning("UDP 发送失败（帧端口 %s）：%s", self.udp_port, exc)

    def _event(self, event: str, **fields: Any) -> None:
        """发一条总线事件（睡眠/唤醒/从机无响应...），测试库单独归档。"""
        payload: Dict[str, Any] = {
            "kind": "event", "event": event, "ts": time.time(),
            "seq": self.seq, "sleeping": self.sleeping,
        }
        payload.update(fields)
        self._send_line(payload)
        log.debug("EVENT %s %s", event, fields)

    # ------------------------------------------------------------------ 调度器
    def _scheduler_loop(self) -> None:
        """调度表主循环：每 10ms 一个槽位，轮询 4 个报文。

        定时精度说明（Windows 踩坑记录）：
        这里用 ``time.sleep()`` 而不是 ``threading.Event.wait(timeout)``。
        Python 3.11+ 在 Windows 上把 ``time.sleep()`` 实现成了高精度
        （waitable timer，误差亚毫秒），而 ``Event.wait(0.01)`` 走的是
        内核同步对象的毫秒级等待，实测会被系统定时器量化成 ~15.6ms，
        于是"等晚了→下一槽补发"形成 52ms/26ms 交替的周期性抖动。
        测试用例①专门验证这个 10ms 时基，所以调度器必须用高精度 sleep。
        """
        schedule = list(SCHEDULE_IDS)
        next_tick = time.monotonic()
        while not self._stop.is_set():
            if self.sleeping:
                # 睡眠中：不发帧，等唤醒（5ms 轮询，CPU 占用可忽略）
                time.sleep(0.005)
                next_tick = time.monotonic()
                continue
            now = time.monotonic()
            if now < next_tick:
                time.sleep(next_tick - now)
                continue
            if now - next_tick > 0.10:
                # 被长时间挂起（如调试断点/机器卡顿）后不要"补发"一串旧帧，
                # 直接把时基对齐到当前时刻，避免测试看到异常突发。
                next_tick = now
            frame_id = schedule[self.slot]
            self.slot = (self.slot + 1) % len(schedule)
            if self.slot == 0:
                self.cycle += 1
            try:
                self._emit_frame(frame_id)
            except Exception:  # noqa: BLE001 - 单帧异常不应杀死仿真进程
                log.exception("发送 LIN 帧 0x%02X 失败", frame_id)
            next_tick += SLOT_SECONDS
        log.info("调度器已退出")

    # ------------------------------------------------------------------ 帧构造/发送
    def _frame_data(self, frame_id: int) -> bytes:
        if frame_id == 0x3C:
            return bytes(self.master_data)
        return bytes(self.slave_raw[frame_id])

    def _take_injection(self, frame_id: int) -> Optional[Dict[str, Any]]:
        """取出一个即将作用在 frame_id 上的错误注入任务（没有则 None）。

        语义：注入任务排队，只有"目标帧 ID 匹配"（或没指定 ID）时才消耗，
        所以测试注入后可以放心等待目标帧，不会被别的帧"吃掉"注入。
        """
        for i, job in enumerate(self.injections):
            if job["id"] is None or job["id"] == frame_id:
                job["remaining"] -= 1
                if job["remaining"] <= 0:
                    self.injections.pop(i)
                return job
        return None

    def _emit_frame(self, frame_id: int, reason: str = "schedule",
                    trigger: str = "schedule") -> Optional[Dict[str, Any]]:
        """构造并发送一帧 LIN 报文（等价模拟 break/sync/PID/data/checksum）。"""
        with self._lock:
            job = self._take_injection(frame_id)
            if job is not None and job["kind"] == "no_response":
                # 从机"装死"：报头发出去了，但从机不应答 → 总线上没有该帧。
                self.skipped += 1
                self._event("no_response", id=frame_id, token=job.get("token"))
                log.info("注入生效：0x%02X 从机无响应（跳过本帧）", frame_id)
                return None

            data = self._frame_data(frame_id)
            pid = calc_pid(frame_id)
            checksum_type = FRAME_CHECKSUM[frame_id]
            checksum = (classic_checksum(data) if checksum_type == "classic"
                        else enhanced_checksum(pid, data))
            checksum_valid = True
            pid_parity_valid = True
            injected = None

            if job is not None and job["kind"] == "checksum":
                # 校验和错误：把正确校验和翻转成错误值，帧照样发到线上。
                checksum = checksum ^ 0x5A
                checksum_valid = False
                injected = "checksum"
                log.info("注入生效：0x%02X 校验和错误（线上 0x%02X）",
                         frame_id, checksum)
            elif job is not None and job["kind"] == "bad_parity":
                # PID 奇偶位错误：接收方算出的 PID 对不上，应把整帧判为无效。
                pid = pid ^ 0x40          # 翻转 P0 奇偶位
                pid_parity_valid = False
                injected = "bad_parity"
                log.info("注入生效：0x%02X PID 奇偶校验错误（线上 PID 0x%02X）",
                         frame_id, pid)

            self.seq += 1
            self.per_id_count[frame_id] += 1
            frame = {
                "kind": "frame",
                "seq": self.seq,
                "ts": time.time(),
                "break": True,                 # 等价模拟：同步间隔场（≥13 bit 显性）
                "sync": 0x55,                  # 同步字节固定 0x55
                "id": frame_id,
                "name": FRAME_NAMES[frame_id],
                "pid": pid,
                "data": list(data),
                "length": len(data),
                "checksum": checksum,
                "checksum_type": checksum_type,
                "checksum_valid": checksum_valid,
                "pid_parity_valid": pid_parity_valid,
                "direction": FRAME_DIRECTION.get(frame_id, "slave_response"),
                "cycle": self.cycle,
                "slot": self.slot,
                "reason": reason,              # schedule / header / master_request / wakeup
                "trigger": trigger,
                "sleeping": self.sleeping,
                "injected": injected,
            }
            self._send_line(frame)
            log.debug("TX seq=%d id=0x%02X pid=0x%02X data=%s %s",
                      self.seq, frame_id, pid, data.hex(), checksum_type)
            return frame

    # ------------------------------------------------------------------ 睡眠/唤醒
    def _enter_sleep(self) -> None:
        with self._lock:
            if not self.sleeping:
                self.sleeping = True
                self.sleep_commands += 1
                self._event("sleep")
                log.info("总线进入睡眠（调度停止）")

    def _leave_sleep(self, event: str = "wakeup") -> None:
        with self._lock:
            if self.sleeping:
                self.sleeping = False
                self.wakeups += 1
                self._event(event)
                log.info("总线被唤醒（调度恢复）")

    # ------------------------------------------------------------------ 信号编解码
    @staticmethod
    def _coerce_number(value: Any, defn: Dict[str, Any]) -> float:
        """把 RF 传来的字符串/数字/枚举名转成物理值（float）。"""
        values = defn.get("values") or {}
        if isinstance(value, str):
            text = value.strip()
            for raw, label in values.items():
                if str(label).lower() == text.lower():
                    return float(raw)
        return float(value)

    @staticmethod
    def _write_bits(data: bytearray, start: int, length: int, raw: int) -> None:
        """把原始值按 LIN 小端位序写进数据场（bit0 = 首字节最低位）。"""
        for i in range(length):
            abs_bit = start + i
            byte_idx, bit_idx = divmod(abs_bit, 8)
            if (raw >> i) & 1:
                data[byte_idx] |= (1 << bit_idx)
            else:
                data[byte_idx] &= ~(1 << bit_idx) & 0xFF

    def set_slave_signals(self, frame_id: int, signals: Dict[str, Any]) -> Dict[str, Any]:
        """设置从机信号（等价于"用 LDF 编码后写进从机 RAM"）。

        返回：{"since_seq": 当前帧序号, "data": 编码后的字节, "signals": 物理值}
        测试端拿 since_seq 做标记，只等待"设置之后"发出的帧，避免读到旧值。
        """
        if frame_id not in SIGNAL_DEFS:
            raise ValueError(f"未知 LIN 帧 ID 0x{frame_id:02X}，"
                             f"可选：{[f'0x{x:02X}' for x in SIGNAL_DEFS]}")
        if frame_id == 0x3C:
            raise ValueError("0x3C 是主请求帧，请用 send_master_request 命令"
                             "（或 Send LIN Master Request 关键字）")
        if not isinstance(signals, dict) or not signals:
            raise ValueError("signals 不能为空，例：{\"rain_intensity\": 75}")

        with self._lock:
            data = self.slave_raw[frame_id]
            snapshot: Dict[str, float] = {}
            for name, value in signals.items():
                defn = SIGNAL_DEFS[frame_id].get(name)
                if defn is None:
                    raise ValueError(
                        f"帧 0x{frame_id:02X} 没有信号 {name!r}，"
                        f"可选：{list(SIGNAL_DEFS[frame_id])}")
                phys = self._coerce_number(value, defn)
                lo, hi = defn.get("minimum"), defn.get("maximum")
                if lo is not None and phys < lo - 1e-9:
                    raise ValueError(f"{name}={phys} 低于最小值 {lo}{defn['unit']}")
                if hi is not None and phys > hi + 1e-9:
                    raise ValueError(f"{name}={phys} 高于最大值 {hi}{defn['unit']}")
                raw = int(round((phys - defn["offset"]) / defn["scale"]))
                max_raw = (1 << defn["length"]) - 1
                if not 0 <= raw <= max_raw:
                    raise ValueError(
                        f"{name}={phys} 编码后原始值 {raw} 超出 {defn['length']} bit "
                        f"范围 0~{max_raw}")
                self._write_bits(data, defn["start"], defn["length"], raw)
                snapshot[name] = phys
            return {
                "ok": True,
                "id": frame_id,
                "since_seq": self.seq,
                "data": list(data),
                "signals": snapshot,
            }

    # ------------------------------------------------------------------ 命令分发
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
                return {"ok": True, "pid": os.getpid(), "udp_port": self.udp_port,
                        "ctrl_port": self.ctrl_port, "sleeping": self.sleeping,
                        "since_seq": self.seq}
            if cmd == "stats":
                return self._stats_response()
            if cmd == "set_slave":
                return self.set_slave_signals(self._parse_id(req.get("id")),
                                              req.get("signals") or {})
            if cmd == "set_slave_raw":
                return self._set_slave_raw(req)
            if cmd == "send_master_request":
                return self._cmd_master_request(req)
            if cmd == "send_header":
                return self._cmd_send_header(req)
            if cmd == "inject_error":
                return self._cmd_inject(req)
            if cmd == "wakeup":
                return self._cmd_wakeup(req)
            if cmd == "quit":
                return {"ok": True, "stopping": True}
            return {"ok": False, "error": f"未知命令 {cmd!r}"}
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _parse_id(value: Any) -> int:
        if value is None:
            raise ValueError("缺少帧 id 参数")
        if isinstance(value, int):
            return value
        return int(str(value), 0)

    def _cmd_master_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """主节点发 0x3C 主请求帧：设置 8 字节数据 → 立即发一帧 → sleep/wake 判定。"""
        data = req.get("data")
        if not isinstance(data, list):
            raise ValueError("data 必须是字节数组，例：[0,0,0,0,0,0,0,0]")
        if len(data) > 8:
            raise ValueError(f"主请求帧最多 8 字节，收到 {len(data)}")
        values = [int(x) & 0xFF for x in data] + [0] * (8 - len(data))
        with self._lock:
            self.master_data = values
            first = values[0]
            if first == 0x00:
                cmd_text = "SLEEP"
            elif first == 0x01:
                cmd_text = "WAKEUP"
            else:
                cmd_text = "RESERVED"
            self._event("master_request", data=values, cmd_text=cmd_text)
            frame = self._emit_frame(0x3C, reason="master_request", trigger="control")
            if first == 0x00:
                self._enter_sleep()
            elif first == 0x01:
                self._leave_sleep("wakeup")
            return {"ok": True, "since_seq": self.seq,
                    "cmd_text": cmd_text, "frame": frame, "sleeping": self.sleeping}

    def _cmd_send_header(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """主节点发一个报头（header）→ 对应从机立即应答一帧。

        真实 LIN 里主节点发 header 是从机应答的触发条件；本仿真把"下一个
        调度槽"提前执行，等价于手工触发一次轮询（CANoe 里也有这种手动触发）。
        """
        frame_id = self._parse_id(req.get("id"))
        if frame_id not in FRAME_LENGTH:
            raise ValueError(f"未知 LIN 帧 ID 0x{frame_id:02X}，"
                             f"可选：{[f'0x{x:02X}' for x in FRAME_LENGTH]}")
        with self._lock:
            # 睡眠中发报头 = 唤醒总线（真实 LIN 主节点就是用报头/唤醒脉冲叫醒从机）
            if self.sleeping:
                self._leave_sleep("wakeup")
            frame = self._emit_frame(frame_id, reason="header", trigger="control")
            return {"ok": True, "since_seq": self.seq, "frame": frame,
                    "sleeping": self.sleeping}

    def _cmd_wakeup(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """唤醒脉冲：真实 LIN 是主机把总线拉低 250us~5ms；仿真等价发一条事件。"""
        with self._lock:
            was_sleeping = self.sleeping
            self.sleeping = False
            if was_sleeping:
                self.wakeups += 1
            master = list(self.master_data)
            master[0] = 0x01
            self.master_data = master
            self._event("wakeup_pulse", was_sleeping=was_sleeping,
                        pulse_ms=2.0)
            log.info("发送唤醒脉冲（was_sleeping=%s）", was_sleeping)
            return {"ok": True, "since_seq": self.seq, "was_sleeping": was_sleeping,
                    "sleeping": False}

    def _cmd_inject(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """排队一个错误注入任务（checksum / no_response / bad_parity）。"""
        kind = req.get("kind")
        if kind not in ("checksum", "no_response", "bad_parity"):
            raise ValueError(f"未知注入类型 {kind!r}，"
                             f"可选：checksum / no_response / bad_parity")
        frame_id = req.get("id")
        fid = None if frame_id is None else self._parse_id(frame_id)
        if fid is not None and fid not in FRAME_LENGTH:
            raise ValueError(f"未知 LIN 帧 ID 0x{fid:02X}，"
                             f"可选：{[f'0x{x:02X}' for x in FRAME_LENGTH]}")
        count = int(req.get("count", 1))
        if count <= 0:
            raise ValueError(f"count 必须为正整数，收到 {count}")
        with self._lock:
            token = self.total_injected + 1
            self.total_injected += 1
            self.injections.append({
                "kind": kind, "id": fid, "remaining": count,
                "count": count, "token": token,
            })
            log.info("排队错误注入 #%d：kind=%s target=%s count=%d",
                     token, kind,
                     "任意帧" if fid is None else f"0x{fid:02X}", count)
            return {"ok": True, "kind": kind, "id": fid, "count": count,
                    "token": token, "since_seq": self.seq,
                    "pending": len(self.injections)}

    def _set_slave_raw(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """直接写从机数据场字节（调试/极端测试用，绕过信号编码）。"""
        frame_id = self._parse_id(req.get("id"))
        if frame_id not in self.slave_raw:
            raise ValueError(f"0x{frame_id:02X} 不是从机应答帧")
        data = req.get("data")
        if not isinstance(data, list) or len(data) != FRAME_LENGTH[frame_id]:
            raise ValueError(f"0x{frame_id:02X} 需要 {FRAME_LENGTH[frame_id]} 字节数据")
        with self._lock:
            self.slave_raw[frame_id] = bytearray(int(x) & 0xFF for x in data)
            return {"ok": True, "id": frame_id,
                    "data": list(self.slave_raw[frame_id]),
                    "since_seq": self.seq}

    def _stats_response(self) -> Dict[str, Any]:
        with self._lock:
            per_id = {f"0x{fid:02X}": self.per_id_count.get(fid, 0)
                      for fid in FRAME_LENGTH}
            return {
                "ok": True,
                "pid": os.getpid(),
                "uptime_s": round(time.time() - self.started_at, 3),
                "seq": self.seq,
                "emitted": self.seq,
                "skipped": self.skipped,
                "sleeping": self.sleeping,
                "sleep_commands": self.sleep_commands,
                "wakeups": self.wakeups,
                "cycle": self.cycle,
                "slot": self.slot,
                "per_id": per_id,
                "pending_injections": len(self.injections),
                "total_injected": self.total_injected,
                "master_data": list(self.master_data),
                "slave_data": {f"0x{fid:02X}": list(raw)
                               for fid, raw in self.slave_raw.items()},
                "udp_port": self.udp_port,
                "ctrl_port": self.ctrl_port,
            }

    # ------------------------------------------------------------------ 服务端
    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(10.0)
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    log.debug("CTRL <<< %s", line.decode("utf-8", "replace"))
                    resp = self._dispatch(line)
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n")
                                 .encode("utf-8"))
                    if resp.get("stopping"):
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
        """启动仿真器，阻塞直到收到 quit / Ctrl+C。返回进程退出码。"""
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind((DEFAULT_CTRL_HOST, self.ctrl_port))
            self._server.listen(8)
        except OSError as exc:
            print(
                f"[lin_sim] [失败] LIN 控制端口 {DEFAULT_CTRL_HOST}:{self.ctrl_port} "
                f"绑定失败：{exc}\n"
                f"[lin_sim]   常见原因：已有一个 lin_sim 在运行（残留进程），或端口被占用。\n"
                f"[lin_sim]   排查：netstat -ano | findstr :{self.ctrl_port}\n"
                f"[lin_sim]   换端口：set LIN_CTRL_PORT=31002 后重跑（测试库读同一环境变量）",
                file=sys.stderr, flush=True)
            self._udp.close()
            return 2

        print("[lin_sim] LIN 2.x 仿真器已启动", flush=True)
        print(f"[lin_sim]   UDP 帧端口   : {DEFAULT_UDP_HOST}:{self.udp_port}", flush=True)
        print(f"[lin_sim]   TCP 控制端口 : {DEFAULT_CTRL_HOST}:{self.ctrl_port}", flush=True)
        print(f"[lin_sim]   调度表       : {SCHEDULE_TEXT}（每槽 {SLOT_SECONDS * 1000:.0f}ms）",
              flush=True)

        self._sched_thread = threading.Thread(
            target=self._scheduler_loop, name="lin-scheduler", daemon=True)
        self._sched_thread.start()
        try:
            self._serve()
        except KeyboardInterrupt:  # pragma: no cover - 仅手工 Ctrl+C
            print("[lin_sim] 收到 Ctrl+C，退出", flush=True)
        finally:
            self._stop.set()
            if self._sched_thread is not None:
                self._sched_thread.join(timeout=2.0)
            try:
                self._server.close()
            except OSError:
                pass
            self._udp.close()
            print("[lin_sim] 已退出", flush=True)
        return 0


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LIN 2.x 教学版总线仿真器（UDP 发帧 + TCP 控制）",
    )
    parser.add_argument("--udp-port", type=int,
                        default=int(os.environ.get("LIN_UDP_PORT", "30001")),
                        help="UDP 帧端口（默认 30001，或环境变量 LIN_UDP_PORT）")
    parser.add_argument("--ctrl-port", type=int,
                        default=int(os.environ.get("LIN_CTRL_PORT", "30002")),
                        help="TCP 控制端口（默认 30002，或环境变量 LIN_CTRL_PORT）")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出 DEBUG 日志")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[lin_sim] %(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    sim = LinSimulator(udp_port=args.udp_port, ctrl_port=args.ctrl_port)
    return sim.run()


if __name__ == "__main__":
    sys.exit(main())
