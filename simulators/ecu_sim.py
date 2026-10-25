"""
ecu_sim.py — 动力域 ECU 模拟器（Powertrain ECU Simulator）
============================================================

这是整个仿真系统的"车辆大脑"，一个进程模拟一个真实 ECU，同时做三件事：

    1. 【正常通信】周期发送 CAN 报文（0x100 发动机数据 / 0x200 仪表数据 / 0x300 警告灯）
    2. 【诊断服务】监听 0x7E0 上的 UDS 请求，按 ISO 14229 语义响应（0x7E8 回复）
    3. 【故障管理】内置 DTC 状态机，水温超 120℃ 自动报 P0116 并请求点亮水温灯

运行方式：
    python ecu_sim.py                    # 默认参数启动
    python ecu_sim.py --speed 80         # 初始车速 80 km/h
    python ecu_sim.py --coolant 125      # 初始水温 125℃（直接触发故障）

启动后进入交互控制台，支持命令：
    speed 80        设定目标车速（带加减速斜坡，模拟真实车辆）
    coolant 125     直接设定水温
    inject P0135    手动注入氧传感器故障（MIL 灯亮）
    unject P0135    撤销注入
    dtc             打印所有 DTC 状态
    status          打印当前信号模型
    quit            退出

UDS 服务实现清单（请求 → 响应示例）：
    10 03                进入扩展会话           → 50 03 00 32 01 F4
    10 01                回默认会话             → 50 01 00 32 01 F4
    11 01                ECU 硬复位             → 51 01
    14 FF FF FF          清除所有 DTC           → 54
    19 01                读 DTC 数量            → 59 01 FF 00 02
    19 02 08             按掩码读 DTC           → 59 02 ... (多帧)
    22 F1 90             读 VIN (17字节)        → 62 F1 90 ... (多帧，演示 ISO-TP)
    22 F1 87             读零件号               → 62 F1 87 ...
    27 01                请求种子               → 67 01 <4字节种子>
    27 02 <密钥>          发送密钥               → 67 02（成功）/ 7F 27 35（错误）
    2E F1 A0 <版本>      写软件版本（需安全解锁） → 6E F1 A0
    31 01 02 00          启动自检例程           → 71 01 02 00
    31 01 03 01          启动灯光测试例程       → 71 01 03 01
    3E 00                诊断仪保活             → 7E 00
    85 02                暂停 DTC 检测          → C5 02
    85 01                恢复 DTC 检测          → C5 01
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import threading
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
from bus import VirtualCanBus                                    # noqa: E402
from dtc_manager import (                                        # noqa: E402
    BIT_CONFIRMED, BIT_PENDING, DtcManager, FAULT_CATALOG,
    status_to_names,
)
from uds_lib import (                                            # noqa: E402
    SID_CLEAR_DTC, SID_COMMUNICATION_CONTROL, SID_CONTROL_DTC_SETTING,
    SID_DIAGNOSTIC_SESSION_CONTROL, SID_ECU_RESET, SID_IO_CONTROL,
    SID_READ_DID, SID_READ_DTC, SID_REQUEST_DOWNLOAD,
    SID_ROUTINE_CONTROL, SID_SECURITY_ACCESS, SID_TESTER_PRESENT,
    SID_TRANSFER_DATA, SID_TRANSFER_EXIT, SID_WRITE_DID,
    SESSION_DEFAULT, SESSION_EXTENDED, SESSION_PROGRAMMING,
    IsoTpReceiver, IsoTpSender,
    build_flow_control, negative_response, parse_response,
    positive_response, string_to_dtc,
)

log = logging.getLogger("ecu_sim")

# ---------------------------------------------------------------------------
# CAN ID 定义（诊断 ID 按惯例：测试仪→ECU 用 0x7E0，ECU→测试仪用 0x7E8）
# ---------------------------------------------------------------------------
ID_ENGINE_DATA = 0x100
ID_CLUSTER_DATA = 0x200
ID_TELLTALE = 0x300
ID_UDS_REQUEST = 0x7E0     # Tester → ECU
ID_UDS_RESPONSE = 0x7E8    # ECU → Tester
ID_NM = 0x510              # 网络管理报文（NM PDU，不走 DBC，同真实项目）
ID_POWER_MODE = 0x600      # 电源模式广播（真实由 BCM 发，本仿真由测试/BCM 扮演）

# 本 ECU 自己发送的报文 ID 集合。为什么要这个：UDP 组播环境下
# python-can 的 receive_own_messages 过滤不生效（该接口不支持），
# 导致**自己发的帧会被自己收到**。若不忽略，"总线静默判定"会被
# 自己的 NM 发送不断刷新 → 永远进不了睡眠（真实踩过的坑：
# 现象是 IGN OFF 后应用报文停了，但 NM 一直发、总线上永远有帧）。
SELF_TX_IDS = {0x100, 0x200, 0x300, ID_NM, ID_UDS_RESPONSE}

# ---------------------------------------------------------------------------
# DID 数据字典（真实 ECU 里这叫 DID database，由 ODX/CDD 文件定义）
# ---------------------------------------------------------------------------
DID_DATABASE = {
    0xF187: b"HW-IC-2026-001",                 # 零件号（硬件）
    0xF188: b"SW-CLUSTER-3.2.1",               # 软件版本（可写，演示 0x2E）
    0xF189: b"ECU-2026-0918-007",              # 序列号
    0xF18A: b"VISTEON",                        # 供应商
    0xF190: b"LSVAM4180NC000001",              # VIN 码 17 字节（演示多帧响应）
    0xF1A0: b"V1.0.0",                         # 应用版本（可写）
    # 测试激励控制（工程模式 DID，台架/产线用）：4 字节
    #   byte0 = 供电电压(V)  byte1 = 丢帧率(%)
    #   byte2 = 报文停发位图(bit0=0x100 bit1=0x200 bit2=0x300)  byte3 = 保留
    0xF1B0: bytes([14, 0, 0, 0]),
}
WRITABLE_DIDS = {0xF188, 0xF1A0, 0xF1B0}       # 允许 0x2E 写的 DID

# 安全访问算法（教学简化版）：key = seed XOR 0x5A5A5A5A
# 真实 OEM 算法复杂得多（CRC/查表/时间窗），但流程完全一样
SECURITY_XOR_KEY = 0x5A5A5A5A


def compute_key(seed: bytes) -> bytes:
    assert len(seed) == 4
    seed_int = int.from_bytes(seed, "big")
    return (seed_int ^ SECURITY_XOR_KEY).to_bytes(4, "big")


class PowertrainEcu:
    """动力域 ECU：正常通信 + 诊断 + 故障管理三合一"""

    def __init__(self, bus: VirtualCanBus) -> None:
        self.bus = bus
        self.dtc = DtcManager()
        self.db = None  # DBC 由 run() 里加载（ecu_sim 与 simulators 同目录）
        import cantools
        self.db = cantools.database.load_file(
            str(Path(__file__).parent / "dbc" / "vehicle.dbc")
        )

        # ---- 信号模型（车辆"物理状态"）----
        self.speed_actual = 0.0       # 当前车速
        self.speed_target = 0.0       # 目标车速（斜坡逼近）
        self.coolant_temp = 90.0
        self.coolant_target = 90.0
        self.fuel_level = 60.0
        self.odometer = 12345         # km
        self.battery_voltage = 13.8
        self.gear = 0                 # 0=P 3=D

        # ---- 诊断状态 ----
        self.session = SESSION_DEFAULT
        self.security_unlocked = False
        self.last_tester_request_time = time.monotonic()  # S3 定时器
        self.dtc_detection_enabled = True
        self.routines: dict[int, dict] = {}   # 例程状态
        # 0x28 通信控制：周期报文发送/接收开关（刷写与台架信号测试用）
        self.comm_tx_enabled = True
        self.comm_rx_enabled = True
        # 刷写状态（0x34 请求下载时创建，0x37 退出/校验后清空）
        self.flash_state: dict | None = None
        # 测试激励（由工程模式 DID F1B0 控制，见 _svc_write_did）
        self.drop_rate = 0          # 周期报文丢帧率 %（模拟总线质量下降）
        self.suppressed_mask = 0    # 报文停发位图（模拟信号超时/丢失）
        # 电源模式与网络管理（NM）
        #   power_mode: 0=OFF 1=ACC 2=IGN 3=CRANK（真实由 BCM 通过 CAN 广播）
        #   nm_state:   normal（正常工作）/ sleep（已休眠，只等唤醒）
        # 睡眠条件：IGN OFF + 总线静默超过 SLEEP_DELAY_SECONDS
        # （真实项目这个时长通常几十秒~几分钟；仿真缩短到 2 秒便于测试）
        self.power_mode = 2
        self.nm_state = "normal"
        self._last_bus_activity = time.monotonic()
        self.SLEEP_DELAY_SECONDS = 2.0
        self.telltale_override: dict[str, int] = {}  # 灯光测试例程用

        # ---- 多帧接收状态（0x7E0 上收到 TP 分片时暂存）----
        self._tp_rx = IsoTpReceiver()
        self._awaiting_fc = False    # 是否在等测试仪的流控帧
        self._pending_frames: list[bytes] = []

        self._running = False

    # ==================================================================
    # 一、正常通信：周期报文
    # ==================================================================
    def _send_engine_data(self) -> None:
        rpm = max(800, self.speed_actual * 80)     # 怠速 800，每 km/h 约 80 rpm
        data = self.db.encode_message("EngineData", {
            "VehicleSpeed": self.speed_actual,
            "EngineSpeed": rpm,
            "CoolantTemp": self.coolant_temp,
            "ThrottlePosition": min(100, self.speed_actual / 1.2),
        })
        self.bus.send_frame(ID_ENGINE_DATA, data)

    def _send_cluster_data(self) -> None:
        self.gear = 0 if self.speed_actual < 1 else 3
        self.odometer += self.speed_actual * 0.2 / 3600  # 200ms 周期累积
        data = self.db.encode_message("ClusterData", {
            "GearPosition": self.gear,
            "FuelLevel": self.fuel_level,
            "Odometer": int(self.odometer),
            "BatteryVoltage": self.battery_voltage,
        })
        self.bus.send_frame(ID_CLUSTER_DATA, data)

    def _send_telltale(self) -> None:
        """发送警告灯状态：DTC 请求的灯 + 手动注入的灯 + 例程覆盖的灯。

        注意：cantools 的 encode_message 要求报文内所有信号都显式提供值
        （strict=False 也不会用 0 填充缺失信号），所以先铺一层全 0 再置 1。
        """
        msg = self.db.get_message_by_name("TelltaleStatus")
        signals = {s.name: 0 for s in msg.signals}
        for light in self.dtc.telltales_to_light():
            signals[light] = 1
        signals.update(self.telltale_override)
        data = self.db.encode_message("TelltaleStatus", signals)
        self.bus.send_frame(ID_TELLTALE, data)

    def _send_nm(self, sleep_ready: int) -> None:
        """发送网络管理报文（NM PDU，0x510）—— 电源管理的关键机制。

        NM 报文的典型内容（AUTOSAR NM 简化版）：
          byte0 = 源节点 ID（本 ECU = 0x01）
          byte1 = 睡眠就绪标志（1 = 本节点已收尾，可以睡了）
          byte2 = 网络状态（0=正常）

        为什么需要 NM：总线上的节点不能想睡就睡—— 必须确认**所有**节点
        都准备好（都置了睡眠就绪位），且总线已静默，才能一起进睡眠。
        否则某个还在工作的节点（如正在写 Flash 的 ECU）会被睡着的邻居
        拖死。测试要验证的正是这套协商逻辑。
        """
        self.bus.send_frame(ID_NM, bytes([0x01, sleep_ready & 1, 0x00]))

    # ==================================================================
    # 二、信号模型推进（车辆"物理仿真"）
    # ==================================================================
    def _physics_tick(self, dt: float) -> None:
        # 车速斜坡：每秒 ±8 km/h 加减速
        if self.speed_actual < self.speed_target:
            self.speed_actual = min(self.speed_target, self.speed_actual + 8 * dt)
        elif self.speed_actual > self.speed_target:
            self.speed_actual = max(self.speed_target, self.speed_actual - 12 * dt)
        # 水温一阶惯性：朝目标温度缓慢逼近（教学用 4s 时间常数，
        # 真实发动机约几分钟；想让故障快速复现就调小它）
        self.coolant_temp += (self.coolant_target - self.coolant_temp) * (dt / 4.0)
        # 水温有波动
        self.coolant_temp += random.uniform(-0.05, 0.05)

    # ==================================================================
    # 三、诊断服务处理（核心中的核心）
    # ==================================================================
    def _session_active(self) -> int:
        return self.session

    def _check_session_for_service(self, sid: int) -> int | None:
        """返回 None=允许，否则返回 NRC。教学版权限模型：
        默认会话：只读类服务（0x10/0x3E/0x11/0x19/0x22/0x14）
        扩展会话：+ 0x2E 写（还需安全解锁）/ 0x31 例程 / 0x28 / 0x85
        编程会话：+ 刷写三件套 0x34/0x36/0x37（还需安全解锁）
        """
        if sid in (SID_DIAGNOSTIC_SESSION_CONTROL, SID_TESTER_PRESENT, SID_ECU_RESET):
            return None
        if sid in (SID_READ_DID, SID_READ_DTC, SID_CLEAR_DTC):
            return None  # 简化：默认会话也允许读（很多 OEM 确实如此）

        # 刷写服务：必须编程会话 + 安全解锁（两层权限，缺一不可）
        if sid in (SID_REQUEST_DOWNLOAD, SID_TRANSFER_DATA, SID_TRANSFER_EXIT):
            if self.session != SESSION_PROGRAMMING:
                return 0x7F   # serviceNotSupportedInActiveSession
            if not self.security_unlocked:
                return 0x33   # securityAccessDenied
            return None

        if self.session == SESSION_DEFAULT:
            return 0x7F  # serviceNotSupportedInActiveSession
        if sid == SID_WRITE_DID and not self.security_unlocked:
            return 0x33  # securityAccessDenied
        return None

    def handle_uds_request(self, data: bytes) -> None:
        """处理一条完整 UDS 请求（已重组为单块数据），回复 0x7E8。"""
        if len(data) < 1:
            return
        sid = data[0]
        params = data[1:]
        log.info("UDS 请求: SID=%#04x params=%s", sid, params.hex())

        nrc = self._check_session_for_service(sid)
        if nrc is not None:
            self._send_response(negative_response(sid, nrc))
            return

        handler = {
            SID_DIAGNOSTIC_SESSION_CONTROL: self._svc_session_control,
            SID_ECU_RESET: self._svc_ecu_reset,
            SID_CLEAR_DTC: self._svc_clear_dtc,
            SID_READ_DTC: self._svc_read_dtc,
            SID_READ_DID: self._svc_read_did,
            SID_SECURITY_ACCESS: self._svc_security_access,
            SID_WRITE_DID: self._svc_write_did,
            SID_ROUTINE_CONTROL: self._svc_routine_control,
            SID_TESTER_PRESENT: self._svc_tester_present,
            SID_CONTROL_DTC_SETTING: self._svc_control_dtc_setting,
            SID_COMMUNICATION_CONTROL: self._svc_communication_control,
            SID_REQUEST_DOWNLOAD: self._svc_request_download,
            SID_TRANSFER_DATA: self._svc_transfer_data,
            SID_TRANSFER_EXIT: self._svc_request_transfer_exit,
        }.get(sid)

        if handler is None:
            self._send_response(negative_response(sid, 0x11))  # serviceNotSupported
            return
        handler(sid, params)

    # ---------------------------------------------------------------- 0x10
    def _svc_session_control(self, sid: int, p: bytes) -> None:
        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0] & 0x7F
        if sub not in (SESSION_DEFAULT, SESSION_EXTENDED, 0x02):
            self._send_response(negative_response(sid, 0x12))
            return
        self.session = sub
        self.security_unlocked = False   # 切会话必须重新解锁（安全设计常识）
        # P2 = 50ms(0x0032), P2* = 5000ms(0x01F4)：诊断仪等待响应的超时参数
        self._send_response(positive_response(sid, bytes([sub, 0x00, 0x32, 0x01, 0xF4])))

    # ---------------------------------------------------------------- 0x11
    def _svc_ecu_reset(self, sid: int, p: bytes) -> None:
        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        reset_type = p[0] & 0x7F
        self._send_response(positive_response(sid, bytes([reset_type])))
        # 模拟复位效果：会话回默认、安全锁定、**刷写状态清零**
        # （真实 ECU 复位后刷写序列必须重来 —— 这也让测试可以用
        # 0x11 01 做刷写用例之间的隔离清理）
        self.session = SESSION_DEFAULT
        self.security_unlocked = False
        self.flash_state = None

    # ---------------------------------------------------------------- 0x14
    def _svc_clear_dtc(self, sid: int, p: bytes) -> None:
        if len(p) < 3:
            self._send_response(negative_response(sid, 0x13))
            return
        group = int.from_bytes(p[:3], "big")
        if group == 0xFFFFFF:
            count = self.dtc.clear_all()
        else:
            dtc_str = f"{group:06X}"  # 简化处理
            count = 1 if self.dtc.clear_dtc(dtc_str) else 0
        log.info("清除 DTC: group=%#x, 实际清除 %d 个", group, count)
        self._send_response(positive_response(sid))  # 54

    # ---------------------------------------------------------------- 0x19
    def _svc_read_dtc(self, sid: int, p: bytes) -> None:
        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0]
        if sub == 0x01:  # 报告 DTC 数量
            records = self.dtc.list_dtcs()
            payload = bytes([0x01, 0xFF, len(records)])
            self._send_response(positive_response(sid, payload))
        elif sub == 0x02:  # 按状态掩码报告 DTC + 状态
            mask = p[1] if len(p) > 1 else 0xFF
            records = self.dtc.list_dtcs(mask)
            body = bytearray([0x02, 0xFF])
            for rec in records:
                b1, b2, b3 = string_to_dtc(rec.dtc)
                body.extend([b1, b2, b3, rec.status])
            self._send_response(positive_response(sid, bytes(body)))
        elif sub == 0x03:  # 报告 DTC 快照（冻结帧）
            records = self.dtc.list_dtcs()
            body = bytearray([0x03])
            for rec in records:
                if rec.snapshot:
                    b1, b2, b3 = string_to_dtc(rec.dtc)
                    body.extend([b1, b2, b3, 0x01, 0x00])  # DTC + 记录号
                    sig = rec.snapshot["signals"]
                    # 快照 DID 0x9001: 车速
                    speed = int(sig.get("speed", 0))
                    body.extend([0x90, 0x01, speed >> 8, speed & 0xFF])
            self._send_response(positive_response(sid, bytes(body)))
        else:
            self._send_response(negative_response(sid, 0x12))

    # ---------------------------------------------------------------- 0x22
    def _svc_read_did(self, sid: int, p: bytes) -> None:
        if len(p) < 2:
            self._send_response(negative_response(sid, 0x13))
            return
        did = (p[0] << 8) | p[1]
        value = DID_DATABASE.get(did)
        if value is None:
            self._send_response(negative_response(sid, 0x31))  # requestOutOfRange
            return
        self._send_response(positive_response(sid, bytes([p[0], p[1]]) + value))

    # ---------------------------------------------------------------- 0x27
    def _svc_security_access(self, sid: int, p: bytes) -> None:
        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0]
        if sub == 0x01:  # 请求种子
            seed = random.randbytes(4)
            self._seed = seed
            self._send_response(positive_response(sid, bytes([0x01]) + seed))
        elif sub == 0x02:  # 发送密钥
            key = p[1:5]
            if len(key) != 4:
                self._send_response(negative_response(sid, 0x13))
                return
            expected = compute_key(getattr(self, "_seed", b"\x00" * 4))
            if key == expected:
                self.security_unlocked = True
                self._send_response(positive_response(sid, bytes([0x02])))
            else:
                self._send_response(negative_response(sid, 0x35))  # invalidKey
        else:
            self._send_response(negative_response(sid, 0x12))

    # ---------------------------------------------------------------- 0x2E
    def _svc_write_did(self, sid: int, p: bytes) -> None:
        if len(p) < 3:
            self._send_response(negative_response(sid, 0x13))
            return
        did = (p[0] << 8) | p[1]
        value = p[2:]
        if did not in WRITABLE_DIDS:
            self._send_response(negative_response(sid, 0x31))
            return
        DID_DATABASE[did] = bytes(value)
        if did == 0xF1B0:
            self._apply_test_stimulus(value)
        else:
            log.info("写入 DID %#06x = %s", did, value.decode(errors="replace"))
        self._send_response(positive_response(sid, bytes([p[0], p[1]])))

    def _apply_test_stimulus(self, value: bytes) -> None:
        """应用测试激励（工程模式 DID F1B0）。

        真实项目里 ECU 常有"工程模式/开发模式 DID"用于注入测试条件
        （台架与产线用），本 DID 模拟三类激励：
          - 供电电压：触发 P0562(<10.5V) / P0563(>15.5V) 故障
          - 丢帧率：模拟总线质量下降（鲁棒性验证）
          - 报文停发：模拟某信号源失效（降级策略验证）
        注意：写这个 DID 同样需要扩展会话 + 安全解锁（权限模型不放松）。
        """
        v = list(value) + [0] * 4
        self.battery_voltage = float(v[0]) if v[0] else self.battery_voltage
        self.drop_rate = max(0, min(100, v[1]))
        self.suppressed_mask = v[2]
        log.warning("测试激励已应用: 电压=%.1fV 丢帧率=%d%% 停发位图=%#04x",
                    self.battery_voltage, self.drop_rate, self.suppressed_mask)

    def _should_send(self, bit: int) -> bool:
        """按测试激励决定某条周期报文是否发送"""
        if self.suppressed_mask & (1 << bit):
            return False
        if self.drop_rate > 0 and random.randint(1, 100) <= self.drop_rate:
            return False
        return True

    # ---------------------------------------------------------------- 0x31
    def _svc_routine_control(self, sid: int, p: bytes) -> None:
        if len(p) < 3:
            self._send_response(negative_response(sid, 0x13))
            return
        sub, rid = p[0], (p[1] << 8) | p[2]
        if sub == 0x01:  # 启动例程
            if rid == 0x0200:     # 自检例程：立即完成
                self.routines[rid] = {"result": 0x00, "done": True}
                self._send_response(positive_response(sid, bytes([0x01, 0x02, 0x00, 0x00])))
            elif rid == 0x0301:   # 灯光测试：点亮所有警告灯
                # 注意：必须与仪表/DBC 的灯清单**完全一致**（12 个）。
                # 这里曾漏过 SeatbeltWarning → 灯光自检只亮 11 个灯，
                # 是"灯清单三处不同步"的典型缺陷（ECU/仪表/DBC 各有一份清单）
                self.routines[rid] = {"result": 0x00, "done": False}
                self.telltale_override = {
                    "MilWarning": 1, "OilPressureLow": 1, "CoolantTempHigh": 1,
                    "BatteryLow": 1, "AbsFault": 1, "AirbagFault": 1,
                    "SeatbeltWarning": 1,
                    "TurnLeft": 1, "TurnRight": 1, "HighBeam": 1, "LowBeam": 1,
                    "ParkBrake": 1,
                }
                # 持续 4 秒（原 2 秒）：给自动化测试留出"仪表收报文 + 写状态
                # 文件 + 测试读到"的完整窗口，否则用例要靠极限时序才能通过
                threading.Timer(4.0, self._finish_light_test).start()
                self._send_response(positive_response(sid, bytes([0x01, 0x03, 0x01])))
            elif rid == 0xFF00:   # 检查编程完整性（刷写收尾：校验和比对）
                if self.flash_state is None:
                    self._send_response(negative_response(sid, 0x24))  # requestSequenceError
                    return
                received = self.flash_state["received"]
                expected = self.flash_state["size"]
                ok = (received == expected)
                # 真实 ECU 这里会比对 Flash 校验和；教学版比对"收到字节数
                # 与声明大小是否一致"（长度错 = 完整性失败）
                self.flash_state = None      # 校验后清状态
                result = 0x00 if ok else 0x01
                self._send_response(positive_response(
                    sid, bytes([0x01, 0xFF, 0x00, result])))
                log.info("编程完整性校验: 收到 %d 字节 / 声明 %d 字节 → %s",
                         received, expected, "通过" if ok else "失败")
            else:
                self._send_response(negative_response(sid, 0x31))
        elif sub == 0x03:  # 请求例程结果
            info = self.routines.get(rid)
            if info is None:
                self._send_response(negative_response(sid, 0x31))
                return
            payload = bytes([0x03, p[1], p[2], info["result"]])
            self._send_response(positive_response(sid, payload))
        else:
            self._send_response(negative_response(sid, 0x12))

    # ---------------------------------------------------------------- 0x34/0x36/0x37 刷写
    def _svc_request_download(self, sid: int, p: bytes) -> None:
        """0x34 RequestDownload：请求下载（刷写第一步）。

        请求载荷（ISO 14229）：
            dataFormatIdentifier(1)              数据格式（压缩/加密标志，0=无）
            addressAndLengthFormatIdentifier(1)  高 4 位=size 字节数, 低 4 位=addr 字节数
            memoryAddress(N)                     目标地址（大端）
            memorySize(M)                        要写入的字节数（大端）
        正响应：74 + lengthFormatIdentifier(0x20) + maxBlockLength(2B)（大端）
        """
        if self.flash_state is not None:
            # 上一次刷写没收尾就又请求下载 → 序列错误
            self._send_response(negative_response(sid, 0x24))
            return
        if len(p) < 10:
            self._send_response(negative_response(sid, 0x13))
            return
        alfi = p[1]
        addr_len = alfi & 0x0F
        size_len = (alfi >> 4) & 0x0F
        if addr_len != 4 or size_len != 4 or len(p) < 2 + addr_len + size_len:
            self._send_response(negative_response(sid, 0x13))
            return
        address = int.from_bytes(p[2:2 + addr_len], "big")
        size = int.from_bytes(p[2 + addr_len:2 + addr_len + size_len], "big")
        # 目标地址范围校验（教学：限制在 App 分区）
        if not (0x08000000 <= address) or size == 0 or address + size > 0x08100000:
            self._send_response(negative_response(sid, 0x31))  # requestOutOfRange
            return
        self.flash_state = {
            "address": address, "size": size, "received": 0,
            "checksum": 0, "next_block": 1, "exited": False,
        }
        max_block = 1024
        self._send_response(positive_response(
            sid, bytes([0x20]) + max_block.to_bytes(2, "big")))
        log.info("请求下载: 地址 %#010x 大小 %d 字节", address, size)

    def _svc_transfer_data(self, sid: int, p: bytes) -> None:
        """0x36 TransferData：分块传输数据。

        请求：blockSequenceCounter(1) + data(≤ maxBlockLength)
        正响应：76 + blockSequenceCounter
        块序号必须严格递增（1,2,...,0xFF,0x00,1...）—— 序号错乱是真实
        刷写最常见的失败，ECU 必须能识别（NRC 0x73）。
        """
        if self.flash_state is None:
            self._send_response(negative_response(sid, 0x24))
            return
        if len(p) < 2:
            self._send_response(negative_response(sid, 0x13))
            return
        block = p[0]
        payload = p[1:]
        if block != self.flash_state["next_block"]:
            self._send_response(negative_response(sid, 0x73))  # wrongBlockSequenceCounter
            log.warning("块序号错乱: 收到 %d 期望 %d", block,
                        self.flash_state["next_block"])
            return
        if self.flash_state["received"] + len(payload) > self.flash_state["size"]:
            self._send_response(negative_response(sid, 0x13))  # 超出声明大小
            return
        self.flash_state["checksum"] = (
            self.flash_state["checksum"] + sum(payload)) & 0xFFFFFFFF
        self.flash_state["received"] += len(payload)
        self.flash_state["next_block"] = (block + 1) & 0xFF
        self._send_response(positive_response(sid, bytes([block])))

    def _svc_request_transfer_exit(self, sid: int, p: bytes) -> None:
        """0x37 RequestTransferExit：退出传输（刷写第三步）。"""
        if self.flash_state is None:
            self._send_response(negative_response(sid, 0x24))
            return
        self.flash_state["exited"] = True
        self._send_response(positive_response(sid, b""))
        log.info("退出传输: 累计收到 %d 字节，校验和 %#x",
                 self.flash_state["received"], self.flash_state["checksum"])

    def _finish_light_test(self) -> None:
        self.telltale_override = {}
        if 0x0301 in self.routines:
            self.routines[0x0301]["done"] = True

    # ---------------------------------------------------------------- 0x3E
    def _svc_tester_present(self, sid: int, p: bytes) -> None:
        sub = p[0] if p else 0x00
        if sub & 0x80:    # 抑制正响应：不回
            return
        self._send_response(positive_response(sid, bytes([sub & 0x7F])))

    # ---------------------------------------------------------------- 0x85
    def _svc_control_dtc_setting(self, sid: int, p: bytes) -> None:
        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0] & 0x7F
        if sub == 0x01:
            self.dtc_detection_enabled = True
        elif sub == 0x02:
            self.dtc_detection_enabled = False
        else:
            self._send_response(negative_response(sid, 0x12))
            return
        self._send_response(positive_response(sid, bytes([sub])))

    # ---------------------------------------------------------------- 0x28
    def _svc_communication_control(self, sid: int, p: bytes) -> None:
        """通信控制（ISO 14229 0x28）：控制正常报文的收/发。

        真实用途：
          - 刷写前必须关闭正常通信（避免总线被应用报文干扰）
          - **台架信号测试**：暂停 ECU 周期报文，独占总线注入测试值
            （否则测试发的帧和 ECU 周期帧会混在一起，读回的是谁的说不清）

        子功能：
          0x00 enableRxAndTx          恢复收发
          0x01 enableRxAndDisableTx   只停发送（保留诊断接收）
          0x03 disableRxAndTx         停收停发（刷写场景）
        注意：诊断响应（0x7E8）不受本开关影响 —— 诊断通信独立于
        应用通信，这是真实 ECU 的设计（否则关了通信就没法恢复）。
        """
        if len(p) < 1:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0] & 0x7F
        if sub == 0x00:
            self.comm_tx_enabled = True
            self.comm_rx_enabled = True
        elif sub == 0x01:
            self.comm_tx_enabled = False
            self.comm_rx_enabled = True
        elif sub == 0x03:
            self.comm_tx_enabled = False
            self.comm_rx_enabled = False
        else:
            self._send_response(negative_response(sid, 0x12))  # subFunctionNotSupported
            return
        self._send_response(positive_response(sid, bytes([sub]) + p[1:2]))
        log.info("通信控制: sub=%#04x → 周期发送=%s, 接收=%s",
                 sub, self.comm_tx_enabled, self.comm_rx_enabled)

    # ==================================================================
    # 四、响应发送（含 ISO-TP 多帧）
    # ==================================================================
    def _send_response(self, data: bytes) -> None:
        """把 UDS 响应发到 0x7E8。≤7 字节单帧，否则走 ISO-TP 多帧。"""
        if len(data) <= 7:
            self.bus.send_frame(ID_UDS_RESPONSE, bytes([len(data)]) + data)
            return
        frames = IsoTpSender.split_frames(data)
        # 首帧
        self.bus.send_frame(ID_UDS_RESPONSE, frames[0])
        # 等测试仪的流控帧 FC（最多 3 秒）
        try:
            fc = self.bus.recv_until(
                lambda m: m.arbitration_id == ID_UDS_REQUEST
                and len(m.data) >= 1 and (m.data[0] & 0xF0) == 0x30,
                timeout=3.0,
            )
            block_size = fc.data[1]
            st_min = fc.data[2] / 1000.0
        except TimeoutError:
            log.warning("等流控帧超时，放弃多帧发送")
            return
        # 按块大小发连续帧
        sent = 0
        for f in frames[1:]:
            self.bus.send_frame(ID_UDS_RESPONSE, f)
            sent += 1
            if block_size and sent % block_size == 0 and sent < len(frames) - 1:
                # 发满一块，再等下一个 FC
                try:
                    self.bus.recv_until(
                        lambda m: m.arbitration_id == ID_UDS_REQUEST
                        and (m.data[0] & 0xF0) == 0x30,
                        timeout=3.0,
                    )
                except TimeoutError:
                    return
            if st_min:
                time.sleep(st_min)

    # ==================================================================
    # 五、总线监听线程：收 0x7E0 请求，处理 TP 分片
    # ==================================================================
    def _bus_listener(self) -> None:
        while self._running:
            try:
                msg = self.bus.recv_frame(timeout=0.1)
            except (OSError, ValueError):
                # 总线关闭（进程退出）时 recv 会抛错，正常退出线程
                break
            if msg is None:
                continue
            # 忽略自己发送帧的回环副本（模拟环境特有，见 SELF_TX_IDS 说明）
            if msg.arbitration_id in SELF_TX_IDS:
                continue
            # ---- 网络管理：任何**其他节点**的总线活动都刷新静默计时 ----
            self._last_bus_activity = time.monotonic()
            if self.nm_state == "sleep":
                self.nm_state = "normal"
                self.power_mode = 2          # 简化：唤醒即视为恢复上电（真实还需等 IGN）
                log.warning("检测到 CAN 活动，节点从睡眠唤醒")
            # ---- 电源模式广播（真实由 BCM 发，本仿真测试脚本可扮演）----
            if msg.arbitration_id == ID_POWER_MODE and msg.data:
                mode = msg.data[0]
                if mode != self.power_mode:
                    log.warning("电源模式变更: %d → %d", self.power_mode, mode)
                self.power_mode = mode
                continue
            if msg.arbitration_id != ID_UDS_REQUEST or not msg.data:
                continue
            pci = msg.data[0]
            if pci & 0xF0 == 0x00:      # 单帧 SF：整条请求在一帧里
                length = pci & 0x0F
                request = bytes(msg.data[1 : 1 + length])
                self._handle_request_safely(request)
                self.last_tester_request_time = time.monotonic()
            elif pci & 0xF0 == 0x10:    # 首帧 FF：立即回流控帧
                total = ((pci & 0x0F) << 8) | msg.data[1]
                self._tp_rx = IsoTpReceiver()
                self._tp_rx.feed(bytes(msg.data))
                # FC 是 ECU→Tester 方向，必须发到响应 ID 0x7E8（不是 0x7E0！）
                # BS=0（不限量）与接收端实现一致：本 ECU 的 IsoTpReceiver
                # 一次收完不做分块确认。**声明的流控参数必须与实际实现能力
                # 一致** —— 若声明 BS=8 却不在收满 8 帧后回新 FC，发送方会
                # 一直等流控帧直到超时（本项目真实踩过的坑）。
                self.bus.send_frame(ID_UDS_RESPONSE, build_flow_control(0, 0))
                log.info("收到多帧请求 FF，总长 %d 字节，已回 FC", total)
            elif pci & 0xF0 == 0x20:    # 连续帧 CF
                done, data = self._tp_rx.feed(bytes(msg.data))
                if done and data:
                    self._handle_request_safely(data)
                    self.last_tester_request_time = time.monotonic()

    def _handle_request_safely(self, request: bytes) -> None:
        """处理请求并兜住所有异常。

        为什么必须兜：诊断处理逻辑里任何未捕获异常（如一个 NameError）
        都会让**整个监听线程静默退出** —— 现象是"周期报文还在发，但所有
        诊断请求石沉大海"，极难排查（本项目真实踩过：刷写服务引用了未
        导入的 SID 常量）。线程级异常必须记录并继续。
        """
        try:
            self.handle_uds_request(request)
        except Exception:  # noqa: BLE001
            log.exception("处理 UDS 请求异常（监听线程继续运行）: %s",
                          request.hex())

    # ==================================================================
    # 六、S3 定时器：扩展会话 5 秒无请求自动回默认会话
    # ==================================================================
    def _session_timer(self) -> None:
        while self._running:
            time.sleep(0.5)
            if self.session != SESSION_DEFAULT:
                if time.monotonic() - self.last_tester_request_time > 5.0:
                    log.info("S3 超时（5s 无 TesterPresent），回默认会话")
                    self.session = SESSION_DEFAULT
                    self.security_unlocked = False

    # ==================================================================
    # 七、主循环
    # ==================================================================
    def run(self) -> None:
        log.info("启动总线监听线程与会话定时器")
        self._running = True
        threading.Thread(target=self._bus_listener, daemon=True).start()
        threading.Thread(target=self._session_timer, daemon=True).start()

        print("=" * 62)
        print(" 动力域 ECU 模拟器已启动")
        print(" 周期报文: 0x100(100ms) 0x200(200ms) 0x300(500ms)")
        print(" 诊断监听: 0x7E0 请求 → 0x7E8 响应")
        print(" 控制命令: status | dtc | speed <n> | coolant <n> "
              "| inject <DTC> | unject <DTC> | help | quit")
        print("=" * 62)

        last_tick = time.monotonic()
        t_100 = t_200 = t_500 = t_nm = 0.0
        log.info("ECU 主循环运行中")
        try:
            while self._running:
                now = time.monotonic()
                dt = now - last_tick
                last_tick = now
                self._physics_tick(dt)

                t_100 += dt
                t_200 += dt
                t_500 += dt
                t_nm += dt
                # 0x28 通信控制：暂停期间不发周期报文（恢复后立即补发一轮）
                if not self.comm_tx_enabled:
                    t_100 = t_200 = t_500 = 0
                    time.sleep(0.01)
                    continue
                # ---- 已休眠：完全静默，只等总线活动唤醒 ----
                if self.nm_state == "sleep":
                    t_100 = t_200 = t_500 = t_nm = 0
                    time.sleep(0.01)
                    continue
                # ---- 电源未 ON（IGN OFF/ACC）：停发应用报文，只发 NM ----
                if self.power_mode != 2:
                    if t_nm >= 0.5:
                        t_nm = 0
                        # IGN OFF 时置睡眠就绪位，向网络声明"我这边可以睡了"
                        self._send_nm(sleep_ready=1 if self.power_mode == 0 else 0)
                    if (self.power_mode == 0
                            and time.monotonic() - self._last_bus_activity
                            >= self.SLEEP_DELAY_SECONDS):
                        self.nm_state = "sleep"
                        log.warning("总线静默 %.1fs，进入睡眠（停止 NM 发送）",
                                    self.SLEEP_DELAY_SECONDS)
                    t_100 = t_200 = t_500 = 0
                    time.sleep(0.01)
                    continue
                if t_100 >= 0.1:
                    t_100 = 0
                    if self._should_send(0):        # bit0 = EngineData(0x100)
                        self._send_engine_data()
                if t_200 >= 0.2:
                    t_200 = 0
                    if self._should_send(1):        # bit1 = ClusterData(0x200)
                        self._send_cluster_data()
                if t_500 >= 0.5:
                    t_500 = 0
                    if self.dtc_detection_enabled:
                        self.dtc.advance_cycle({
                            "coolant_temp": self.coolant_temp,
                            "battery_voltage": self.battery_voltage,
                            "speed": self.speed_actual,
                            "o2_heater_fault": getattr(self, "_o2_fault", False),
                        })
                    self._send_telltale()
                time.sleep(0.01)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            print("\nECU 模拟器退出")

    # ------------------------------------------------------------------
    def console(self) -> None:
        """交互控制台（单独线程运行）。

        注意两点：
        1. 循环条件必须是 while True，不能依赖 self._running ——
           console 线程启动时 run() 还没把 _running 置 True，依赖它会导致
           线程直接退出、永远读不到命令（经典初始化竞态）。
        2. 每条命令都包异常 + 参数校验 —— 用户敲错命令（少参数/非法数字）
           不能让控制台线程崩溃（否则后续所有命令都"没反应"）。
        """
        while True:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                continue
            try:
                if not self._handle_command(line):
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR] 命令执行失败: {exc}（输入 help 查看用法）")

    def _handle_command(self, line: str) -> bool:
        """处理一条控制台命令。返回 False 表示退出控制台。"""
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("quit", "exit"):
            self._running = False
            return False

        if cmd == "help":
            print(" 可用命令：")
            print("   status              查看当前车辆状态")
            print("   dtc                 查看 DTC 列表与状态位")
            print("   speed <km/h>        设置目标车速（如 speed 80）")
            print("   coolant <℃>         设置目标水温（>120 触发故障，如 coolant 125）")
            print("   inject <DTC>        注入故障（如 inject P0135）")
            print("   unject <DTC>        撤销故障注入")
            print("   quit                退出")
            return True

        if cmd == "speed":
            if not args:
                print("[ERR] 用法: speed <km/h>，例如 speed 80")
                return True
            self.speed_target = float(args[0])
            print(f"[OK] 目标车速 → {self.speed_target} km/h")
            return True

        if cmd == "coolant":
            if not args:
                print("[ERR] 用法: coolant <℃>，例如 coolant 125")
                return True
            self.coolant_target = float(args[0])
            print(f"[OK] 目标水温 → {self.coolant_target} ℃")
            return True

        if cmd == "inject":
            if not args:
                print(f"[ERR] 用法: inject <DTC>，可选: {list(FAULT_CATALOG)}")
                return True
            dtc = args[0].upper()
            if dtc not in FAULT_CATALOG:
                print(f"[ERR] 未知 DTC {dtc}，可选: {list(FAULT_CATALOG)}")
                return True
            if dtc == "P0135":
                self._o2_fault = True
            self.dtc.inject_fault(dtc, {"speed": self.speed_actual})
            print(f"[OK] 已注入 {dtc}: {FAULT_CATALOG[dtc]['name']}")
            return True

        if cmd == "unject":
            if not args:
                print(f"[ERR] 用法: unject <DTC>，可选: {list(FAULT_CATALOG)}")
                return True
            dtc = args[0].upper()
            if dtc == "P0135":
                self._o2_fault = False
            self.dtc.clear_fault_injection(dtc)
            print(f"[OK] 已撤销 {dtc}")
            return True

        if cmd == "dtc":
            print(f"cycle={self.dtc.cycle_count} session={self.session:#04x} "
                  f"unlocked={self.security_unlocked}")
            records = self.dtc.list_dtcs()
            for rec in records:
                print(f"  {rec.dtc}: status={rec.status:#04x} "
                      f"[{', '.join(status_to_names(rec.status)) or '无'}] "
                      f"aging={rec.aging_counter}")
            if not records:
                print("  （无 DTC 记录）")
            return True

        if cmd == "status":
            print(f"speed={self.speed_actual:.1f}km/h(target {self.speed_target}) "
                  f"coolant={self.coolant_temp:.1f}℃ fuel={self.fuel_level}% "
                  f"gear={self.gear} odo={self.odometer:.0f}km "
                  f"batt={self.battery_voltage}V")
            return True

        print(f"[ERR] 未知命令 {cmd}（输入 help 查看用法）")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="动力域 ECU 模拟器")
    parser.add_argument("--speed", type=float, default=0.0, help="初始目标车速")
    parser.add_argument("--coolant", type=float, default=90.0, help="初始目标水温")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(name)s %(message)s",
                        stream=sys.stdout)
    # 日志走 stdout：CI/RF 环境通常只重定向 stdout，走 stderr 会丢日志
    # （本项目真实踩过：ECU 监听线程静默崩溃却查不到任何线索）
    # echo_own=False：ECU 不需要回读自己发的帧；更重要的是——睡眠判定依赖
    # "总线静默"，若统计到自己的发送就永远静默不了（真实踩过的坑）
    with VirtualCanBus("POWERTRAIN-ECU", echo_own=False) as bus:
        ecu = PowertrainEcu(bus)
        ecu.speed_target = args.speed
        ecu.coolant_target = args.coolant
        threading.Thread(target=ecu.console, daemon=True).start()
        ecu.run()


if __name__ == "__main__":
    main()
