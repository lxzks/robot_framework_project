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
import zlib
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
from bus import VirtualCanBus                                    # noqa: E402
from dtc_manager import (                                        # noqa: E402
    BIT_CONFIRMED, DtcManager, FAULT_CATALOG, status_to_names,
)
from uds_lib import (                                            # noqa: E402
    SID_CLEAR_DTC, SID_COMMUNICATION_CONTROL, SID_CONTROL_DTC_SETTING,
    SID_DIAGNOSTIC_SESSION_CONTROL, SID_ECU_RESET, SID_IO_CONTROL,
    SID_READ_DID, SID_READ_DTC, SID_REQUEST_DOWNLOAD,
    SID_ROUTINE_CONTROL, SID_SECURITY_ACCESS, SID_TESTER_PRESENT,
    SID_TRANSFER_DATA, SID_TRANSFER_EXIT, SID_WRITE_DID,
    SESSION_DEFAULT, SESSION_EXTENDED, SESSION_PROGRAMMING,
    IsoTpReceiver, IsoTpSender,
    build_flow_control, negative_response,
    positive_response, string_to_dtc,
)

log = logging.getLogger("ecu_sim")

# ---------------------------------------------------------------------------
# CAN ID 定义（诊断 ID 按惯例：测试仪→ECU 用 0x7E0，ECU→测试仪用 0x7E8）
# ---------------------------------------------------------------------------
ID_ENGINE_DATA = 0x100
ID_CLUSTER_DATA = 0x200
ID_TELLTALE = 0x300
ID_UDS_REQUEST = 0x7E0     # Tester → ECU（物理寻址）
ID_UDS_FUNCTIONAL_REQUEST = 0x7DF   # Tester → 所有 ECU（功能寻址，OBD-II 也走这里）
ID_UDS_RESPONSE = 0x7E8    # ECU → Tester（无论物理/功能寻址，响应都发物理响应 ID）
ID_NM = 0x510              # 网络管理报文（NM PDU，不走 DBC，同真实项目）
ID_POWER_MODE = 0x600      # 电源模式广播（真实由 BCM 发，本仿真由测试/BCM 扮演）

# ---------------------------------------------------------------------------
# 诊断进阶服务（本文件按 UDS 服务号实现，不放进 uds_lib 常量表，避免动公共库）
# ---------------------------------------------------------------------------
SID_READ_MEMORY_BY_ADDRESS = 0x23    # ReadMemoryByAddress
SID_WRITE_MEMORY_BY_ADDRESS = 0x3D   # WriteMemoryByAddress
OBD_SIDS = {0x01, 0x03, 0x04, 0x09}  # ISO 15031 常用 mode（仅功能寻址 0x7DF 生效）

# 0x2F 执行器控制：DID 0x3001 = 仪表警告灯模块（12 个灯，2 字节掩码）
IO_CONTROL_DID = 0x3001
# 灯名 → TelltaleStatus(DBC) 里的信号位序。三处清单（ECU/DBC/仪表）必须一致。
TELLTALE_BIT_LAYOUT = [
    ("MilWarning", 0), ("OilPressureLow", 1), ("CoolantTempHigh", 2),
    ("BatteryLow", 3), ("AbsFault", 4), ("AirbagFault", 5),
    ("SeatbeltWarning", 6), ("TurnLeft", 8), ("TurnRight", 9),
    ("HighBeam", 10), ("LowBeam", 11), ("ParkBrake", 12),
]

# 0x23/0x3D 内存映射教学区：0x08000000 起 1KB（工程/产线读写内存的教学替身）
MEMORY_AREA_BASE = 0x08000000
MEMORY_AREA_SIZE = 0x400

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
    # ---- OTA / Bootloader 版本管理（Wave3 新增）----
    0xF195: b"v1.0.0",                 # 应用软件版本号（刷写成功 + 复位后更新为包版本）
    0xF1A3: b"BL-1.0.0",               # Bootloader 版本
    0xF186: b"HW-VISTEON-P7",          # 硬件版本
    # OTA 刷写暂存（0x2E 可写，仅刷写流程内部使用）：
    0xF1A1: b"v1.0.0",                 # 目标版本暂存：31 01 FF00 成功后由 0x11 复位激活
    0xF1A2: bytes(4),                  # 期望 CRC32（31 01 FF00 完整性校验用，0=不校验）
    # 测试激励控制（工程模式 DID，台架/产线用）：4 字节
    #   byte0 = 供电电压(V)  byte1 = 丢帧率(%)
    #   byte2 = 报文停发位图(bit0=0x100 bit1=0x200 bit2=0x300)
    #   byte3 = 预编程检查用"模拟车速"(km/h, 0=用真实车速；自动化测试用它验证
    #           "车速非 0 拒绝刷写"分支，台架无法真的让车跑起来)
    0xF1B0: bytes([14, 0, 0, 0]),
}
WRITABLE_DIDS = {0xF188, 0xF1A0, 0xF1B0, 0xF1A1, 0xF1A2}  # 允许 0x2E 写的 DID

# 安全访问算法（教学简化版）：key = seed XOR 0x5A5A5A5A
# 真实 OEM 算法复杂得多（CRC/查表/时间窗），但流程完全一样
SECURITY_XOR_KEY = 0x5A5A5A5A

# 安全访问防爆破参数：连续 3 次错误密钥 → 锁定 5 秒
# （真实 ECU 通常还有指数退避/永久锁定+售后解锁流程，教学版取最小闭环）
SECURITY_MAX_ATTEMPTS = 3
SECURITY_LOCKOUT_SECONDS = 5.0


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
        # ---- 安全访问（0x27）状态机：防绕过 / 防重放 / 防爆破 ----
        #   _seed: 当前挑战种子；None 表示"尚未请求种子"（此时 27 02 必须拒绝）
        #   _failed_key_attempts: 连续错误密钥次数（成功或锁定到期后清零）
        #   _security_lock_until: 锁定截止时刻（time.monotonic），0 表示未锁定
        self._seed: bytes | None = None
        self._failed_key_attempts = 0
        self._security_lock_until = 0.0
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

        # ---- 0x2F InputOutputControlByIdentifier：警告灯执行器状态 ----
        #   _io_control_state: 最近一次 shortTerm/longTerm 控制的 12 位灯掩码
        #   _io_control_mode:  最近一次控制子功能（00/01/02/03），便于调试
        self._io_control_state = 0
        self._io_control_mode: int | None = None
        # ---- 0x23/0x3D 内存映射教学区：0x08000000 起 1KB ----
        # 前 17 字节映射 VIN（与 DID 0xF190 一致），其余为可读写 RAM。
        # 真实 ECU 这里是 Flash/EEPROM 驱动；教学版用 bytearray 替代。
        self._memory_area = bytearray(MEMORY_AREA_SIZE)
        _vin = DID_DATABASE[0xF190]
        self._memory_area[:len(_vin)] = _vin

        # ---- OTA / Bootloader 状态机（Wave3 新增）----
        #   _app_version:     当前运行的 APP 版本（成功刷写 + 0x11 复位后才更新）
        #   _active_slot:     A/B 分区当前激活槽（A=旧镜像，B=新镜像）
        #   _pending_activation: 31 01 FF00 通过后登记的"待激活"版本（复位生效）
        #   _expected_crc:    本轮下载期望 CRC32（由 0x2E 写 F1A2 预置；None=只校验长度）
        #   _precheck_speed:  预编程条件检查用的模拟车速（0=用真实车速）
        self._app_version: bytes = bytes(DID_DATABASE[0xF195])
        self._active_slot = "A"
        self._pending_activation: dict | None = None
        self._expected_crc: int | None = None
        self._precheck_speed = 0

        # ---- 多帧接收状态（0x7E0/0x7DF 上收到 TP 分片时暂存）----
        self._tp_rx = IsoTpReceiver()
        self._tp_functional = False   # 当前正在重组的请求是否来自功能寻址 0x7DF
        self._awaiting_fc = False    # 是否在等测试仪的流控帧
        self._pending_frames: list[bytes] = []

        self._running = False

    # ==================================================================
    # 一、正常通信：周期报文
    # ==================================================================
    def _send_engine_data(self) -> None:
        # 钳位到 DBC 声明范围：EngineSpeed [0|16000]，即车速上限 200 km/h。
        # 否则 `speed 250` 这类输入会让 cantools 抛 EncodeError，直接打死
        # 主循环（run() 里已兜底，但这里先保证发得出合法帧）。
        rpm = min(16000.0, max(800.0, self.speed_actual * 80))  # 怠速 800，约 80rpm/(km/h)
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
        扩展会话：+ 0x2E 写（还需安全解锁）/ 0x2F 执行器控制 / 0x23 读内存
                  / 0x3D 写内存（后三者同样需安全解锁）/ 0x31 例程 / 0x28 / 0x85
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
        # 写 DID / 执行器控制 / 内存读写：扩展会话之上还必须有安全解锁
        if sid in (SID_WRITE_DID, SID_IO_CONTROL,
                   SID_READ_MEMORY_BY_ADDRESS,
                   SID_WRITE_MEMORY_BY_ADDRESS) and not self.security_unlocked:
            return 0x33  # securityAccessDenied
        return None

    def handle_uds_request(self, data: bytes, functional: bool = False) -> None:
        """处理一条完整 UDS 请求（已重组为单块数据），回复 0x7E8。

        functional=True 表示请求来自**功能寻址 0x7DF**：
          - OBD-II 服务（0x01/0x03/0x04/0x09）走 ISO 15031 分支；
          - 其它请求仍按普通 UDS 处理（真实 ECU 也允许 0x3E/0x22 等功能寻址）。
        响应一律发 0x7E8（物理响应 ID）—— 真实 ECU 同样如此：
        功能寻址只有"请求"是广播的，"响应"必须带发送方身份（物理 ID）。
        """
        if len(data) < 1:
            return
        sid = data[0]
        params = data[1:]
        log.info("UDS 请求(%s): SID=%#04x params=%s",
                 "功能寻址" if functional else "物理寻址", sid, params.hex())
        if functional and sid in OBD_SIDS:
            self._svc_obd(sid, params)
            return

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
            SID_IO_CONTROL: self._svc_io_control,
            SID_READ_MEMORY_BY_ADDRESS: self._svc_read_memory,
            SID_WRITE_MEMORY_BY_ADDRESS: self._svc_write_memory,
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
        self._seed = None                # 会话切换后旧种子作废（防跨会话重放）
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
        # ---- OTA/Bootloader：A/B 分区激活 ----
        # 刷写成功（31 01 FF00 通过）只把新版本登记为"待激活"；
        # 复位后 Bootloader 才真正切到新槽 → 版本号更新。
        # 这样"刷写失败/中途断电"天然回滚到旧版本（旧槽从未被破坏）。
        if self._pending_activation is not None:
            new_version = self._pending_activation.get("version")
            if new_version:
                DID_DATABASE[0xF195] = bytes(new_version)
                self._app_version = bytes(new_version)
                self._active_slot = self._pending_activation.get(
                    "slot", self._active_slot)
                log.warning("OTA 激活新版本 %s（当前 A/B 槽=%s）",
                            new_version.decode(errors="replace"),
                            self._active_slot)
            self._pending_activation = None
        # 复位同时清安全访问状态：种子作废、爆破计数/锁定归零
        # （真实 ECU 复位后安全访问必须从头来）
        self._seed = None
        self._failed_key_attempts = 0
        self._security_lock_until = 0.0
        # 刷写相关的临时输入也必须清（避免上次 OTA 的激励污染下一次刷写检查）
        self._expected_crc = None
        self._precheck_speed = 0

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
        """0x27 SecurityAccess：种子-密钥握手（含防绕过/防重放/防爆破）。

        安全设计要点（真实 ECU 也遵循同样的原则，教学版只简化算法）：
        - **必须先取种子**：27 02 前没发 27 01 → NRC 0x24 requestSequenceError。
          （历史漏洞：旧实现用 `getattr(self, "_seed", b"\x00"*4)`，即
          未请求种子时默认 seed=0x00000000 → 期望密钥恒为 0x5A5A5A5A，
          攻击者直接发 `27 02 5A5A5A5A` 就能解锁。本测试模块用"漏洞回归"用例锁死它。）
        - **种子一次性**：校验成功后立即清空 `_seed`，同一 key 不能重放。
        - **nonce**：每次 27 01 都生成新的随机种子 → 旧密钥在新种子上必然失效。
        - **防爆破**：连续 3 次错误密钥 → 锁定 5 秒，期间任何安全访问都回
          NRC 0x36 exceedNumberOfAttempts（锁定到期后计数归零、允许重试）。
        """
        now = time.monotonic()
        if now < self._security_lock_until:
            # 锁定中：不消耗也不校验，直接拒绝（避免继续爆破）
            self._send_response(negative_response(sid, 0x36))
            return
        if self._security_lock_until and now >= self._security_lock_until:
            # 锁定到期：计数归零，允许重新尝试
            log.info("安全访问锁定到期，计数归零")
            self._security_lock_until = 0.0
            self._failed_key_attempts = 0

        if not p:
            self._send_response(negative_response(sid, 0x13))
            return
        sub = p[0] & 0x7F        # 最高位是 suppressPosRspMsgIndicationBit
        if sub == 0x01:          # ---- 请求种子 ----
            if len(p) != 1:      # 27 01 不接受额外参数（超长 → 0x13）
                self._send_response(negative_response(sid, 0x13))
                return
            seed = random.randbytes(4)   # nonce：每次都是新随机种子
            self._seed = seed
            self._send_response(positive_response(sid, bytes([0x01]) + seed))
            log.info("安全访问：下发新种子 %s", seed.hex())
        elif sub == 0x02:        # ---- 发送密钥 ----
            if len(p) != 5:      # subFunction(1) + key(4)，截断/超长 → 0x13
                self._send_response(negative_response(sid, 0x13))
                return
            if self._seed is None:
                # 未请求种子就直接发密钥 → 序列错误（修复 27 02 绕过漏洞）
                log.warning("安全访问：27 02 在未请求种子的情况下到达 → NRC 0x24")
                self._send_response(negative_response(sid, 0x24))
                return
            expected = compute_key(self._seed)
            key = p[1:5]
            if key == expected:
                self.security_unlocked = True
                self._seed = None            # 一次性：成功后立即作废
                self._failed_key_attempts = 0
                self._send_response(positive_response(sid, bytes([0x02])))
                log.info("安全访问：解锁成功")
            else:
                self._failed_key_attempts += 1
                log.warning("安全访问：密钥错误（连续第 %d 次）",
                            self._failed_key_attempts)
                if self._failed_key_attempts >= SECURITY_MAX_ATTEMPTS:
                    self._security_lock_until = now + SECURITY_LOCKOUT_SECONDS
                    self._seed = None
                    log.warning("安全访问：连续失败 %d 次，锁定 %.0fs（NRC 0x36）",
                                self._failed_key_attempts, SECURITY_LOCKOUT_SECONDS)
                    self._send_response(negative_response(sid, 0x36))
                else:
                    self._send_response(negative_response(sid, 0x35))
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
        if did == 0xF1A2 and len(value) != 4:   # 期望 CRC32 必须 4 字节
            self._send_response(negative_response(sid, 0x13))
            return
        DID_DATABASE[did] = bytes(value)
        if did == 0xF1B0:
            self._apply_test_stimulus(value)
        elif did == 0xF1A2:
            self._expected_crc = int.from_bytes(value, "big")
            log.info("OTA 期望 CRC32 = %#010x", self._expected_crc)
        elif did == 0xF1A1:
            log.info("OTA 目标版本暂存 = %s", value.decode(errors="replace"))
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
        # byte3 = 预编程条件检查用的"模拟车速"（0=不模拟，用真实车速）。
        # 自动化测试没法真让车跑起来，用它验证"车速非 0 → 拒绝刷写"分支。
        self._precheck_speed = int(v[3])
        log.warning("测试激励已应用: 电压=%.1fV 丢帧率=%d%% 停发位图=%#04x 模拟车速=%d",
                    self.battery_voltage, self.drop_rate, self.suppressed_mask,
                    self._precheck_speed)

    def _should_send(self, bit: int) -> bool:
        """按测试激励决定某条周期报文是否发送"""
        if self.suppressed_mask & (1 << bit):
            return False
        if self.drop_rate > 0 and random.randint(1, 100) <= self.drop_rate:
            return False
        return True

    # ---------------------------------------------------------------- 0x31
    def _preprogramming_problems(self) -> list:
        """预编程条件检查（0x31 01 0203）的判定逻辑，返回"不满足项"清单。

        真实 Bootloader 进入编程前要求：车速为 0、驻车、蓄电池电压正常、
        无关键故障、无进行中的下载……教学版取两条最容易自动化的：
          - 车速 = 0（自动化测试可用工程 DID F1B0 byte3 模拟非 0，见
            `_apply_test_stimulus`；台架无法真的让车跑起来）
          - 蓄电池电压 10.5~15.5V（可用 F1B0 byte0 注入欠压/过压）
          - 不能有未完成的下载会话
        """
        problems: list = []
        speed = self._precheck_speed if self._precheck_speed > 0 \
            else self.speed_actual
        if abs(speed) > 0.5:
            problems.append(f"车速非 0（{speed:.1f} km/h）")
        if not 10.5 <= self.battery_voltage <= 15.5:
            problems.append(f"电压异常（{self.battery_voltage:.1f}V）")
        if self.flash_state is not None and not self.flash_state.get("exited"):
            problems.append("存在未完成的下载会话")
        return problems

    def _svc_routine_control(self, sid: int, p: bytes) -> None:
        if len(p) < 3:
            self._send_response(negative_response(sid, 0x13))
            return
        sub, rid = p[0], (p[1] << 8) | p[2]
        if sub == 0x01:  # 启动例程
            if rid == 0x0200:     # 自检例程：立即完成
                self.routines[rid] = {"result": 0x00, "done": True}
                self._send_response(positive_response(sid, bytes([0x01, 0x02, 0x00, 0x00])))
            elif rid == 0x0203:   # 预编程条件检查（刷写前：车速=0、电压正常…）
                problems = self._preprogramming_problems()
                if problems:
                    log.warning("预编程条件不满足，拒绝刷写: %s",
                                "；".join(problems))
                    # ISO 14229：进入编程会话的前提条件不满足 → 0x22
                    self._send_response(negative_response(sid, 0x22))
                    return
                self.routines[rid] = {"result": 0x00, "done": True}
                self._send_response(positive_response(
                    sid, bytes([0x01, 0x02, 0x03, 0x00])))
                log.info("预编程条件检查通过（车速=0、电压=%.1fV）",
                         self.battery_voltage)
            elif rid == 0x0202:   # 编程依赖检查（编程会话内、请求下载前）
                if self.flash_state is not None and not self.flash_state.get("exited"):
                    self._send_response(negative_response(sid, 0x24))
                    return
                self.routines[rid] = {"result": 0x00, "done": True}
                self._send_response(positive_response(
                    sid, bytes([0x01, 0x02, 0x02, 0x00])))
                log.info("编程依赖检查通过")
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
            elif rid == 0xFF00:   # 检查编程完整性（刷写收尾：长度 + CRC32 双重校验）
                if self.flash_state is None:
                    self._send_response(negative_response(sid, 0x24))  # requestSequenceError
                    return
                received = self.flash_state["received"]
                expected = self.flash_state["size"]
                payload_data = bytes(self.flash_state.get("data", b""))
                ok = (received == expected)
                detail = f"长度 {received}/{expected}"
                # 若刷写流程通过 0x2E 写 F1A2 预置了期望 CRC32，则做第二道校验。
                # 真实 ECU 在这里比对 Flash 校验和；教学版用 zlib.crc32。
                if ok and self._expected_crc is not None:
                    crc = zlib.crc32(payload_data) & 0xFFFFFFFF
                    ok = (crc == self._expected_crc)
                    detail += f"，CRC32 {crc:#010x}/{self._expected_crc:#010x}"
                self.flash_state = None      # 校验后清状态
                self._expected_crc = None    # 期望 CRC 一次性使用
                if ok:
                    # 刷写成功：登记"待激活"版本，等 0x11 复位才切 A/B 槽
                    target = DID_DATABASE.get(0xF1A1, self._app_version)
                    self._pending_activation = {
                        "version": bytes(target),
                        "slot": "B" if self._active_slot == "A" else "A",
                    }
                else:
                    self._pending_activation = None   # 失败：保持旧版本（回滚）
                result = 0x00 if ok else 0x01
                self._send_response(positive_response(
                    sid, bytes([0x01, 0xFF, 0x00, result])))
                log.info("编程完整性校验: %s → %s", detail,
                         "通过（复位后激活新版本）" if ok else "失败（保持旧版本）")
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
        if self.flash_state is not None and not self.flash_state.get("exited"):
            # 上一次刷写还没退出传输就又要下载 → 序列错误。
            # 注意：0x37 之后 flash_state 仍保留（31 01 FF00 完整性校验还要用
            # 已收字节数/校验和），但允许新的 0x34 覆盖它 —— 真实 ECU 支持
            # 分区块连续下载（App → Cal → …），不能因为"上一段已 exit"就拒绝。
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
            # 保存已收数据：31 01 FF00 要用它做 CRC32 完整性校验
            "data": bytearray(),
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
        self.flash_state["data"].extend(payload)
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

    # ---------------------------------------------------------------- 0x2F
    def _svc_io_control(self, sid: int, p: bytes) -> None:
        """0x2F InputOutputControlByIdentifier：控制 ECU 的"虚拟执行器"。

        真实用途（产线/EOL 与台架）：
          - 不拆件就能验证执行器驱动通路（灯、电机、继电器）
          - 产线终检"点灯测试"、售后诊断"强制动作"
          - 台架上绕过传感器直接给定执行器值（如把水温表强制打到 120℃）

        本项目执行器：DID 0x3001 = 仪表警告灯模块（12 个灯）。
        请求：``2F <DID 2B> <control 1B> <mask 2B>``
          control：00 returnControlToECU / 01 resetToDefault
                   02 shortTermAdjustment / 03 longTermAdjustment
          mask   ：12 个警告灯的位图（位序与 DBC TelltaleStatus 一致）
        正响应：``6F <DID 2B> <controlState 2B>``（控制后的实际灯掩码）

        权限：扩展/编程会话 + 安全解锁（默认会话回 0x7F、未解锁回 0x33）。
        """
        if len(p) < 3:
            self._send_response(negative_response(sid, 0x13))
            return
        did = (p[0] << 8) | p[1]
        control = p[2]
        if did != IO_CONTROL_DID:
            self._send_response(negative_response(sid, 0x31))
            return
        if control not in (0x00, 0x01, 0x02, 0x03):
            self._send_response(negative_response(sid, 0x12))
            return
        if control in (0x02, 0x03):      # shortTermAdjustment / longTermAdjustment
            if len(p) < 5:
                self._send_response(negative_response(sid, 0x13))
                return
            mask = ((p[3] << 8) | p[4]) & 0x1FFF
            self._io_control_mode = control
            self._io_control_state = mask
            self._apply_io_telltale_mask(mask)
            state = mask
            log.info("0x2F 执行器控制: DID=%#06x control=%#04x mask=%#06x",
                     did, control, mask)
        else:                            # 00 returnControlToECU / 01 resetToDefault
            self._io_control_mode = control
            self._io_control_state = 0
            self.telltale_override = {}   # 交还控制权：0x300 回到 DTC 驱动
            state = self._current_telltale_mask()
            log.info("0x2F 交还控制权: DID=%#06x control=%#04x", did, control)
        # 正响应 6F + DID + controlState（2 字节掩码）
        self._send_response(positive_response(
            sid, bytes([p[0], p[1], (state >> 8) & 0xFF, state & 0xFF])))

    def _apply_io_telltale_mask(self, mask: int) -> None:
        """把 12 位灯掩码映射成 0x300 TelltaleStatus 的信号覆盖字典。

        `_send_telltale` 会用 `signals.update(self.telltale_override)` 覆盖
        DTC 驱动的灯状态 —— 这就是"执行器强制"的落地点。
        """
        self.telltale_override = {
            name: (mask >> bit) & 1 for name, bit in TELLTALE_BIT_LAYOUT
        }

    def _current_telltale_mask(self) -> int:
        """当前由 DTC 请求点亮的灯掩码（0x2F 交还控制权时用于回显）。"""
        lights = set(self.dtc.telltales_to_light())
        mask = 0
        for name, bit in TELLTALE_BIT_LAYOUT:
            if name in lights:
                mask |= 1 << bit
        return mask

    # ---------------------------------------------------------------- 0x23/0x3D
    def _parse_alfid(self, p: bytes) -> tuple[int, int, int] | None:
        """解析 ALFID（addressAndLengthFormatIdentifier）。

        编码（与 0x34 一致）：高 4 位 = memorySize 的字节数，低 4 位 = address 的字节数。
        返回 ``(address, size, data_offset)``；格式非法返回 None。
        """
        if len(p) < 2:
            return None
        alfi = p[0]
        addr_len = alfi & 0x0F
        size_len = (alfi >> 4) & 0x0F
        if addr_len not in (1, 2, 3, 4) or size_len not in (1, 2, 3, 4):
            return None
        if len(p) < 1 + addr_len + size_len:
            return None
        address = int.from_bytes(p[1:1 + addr_len], "big")
        size = int.from_bytes(p[1 + addr_len:1 + addr_len + size_len], "big")
        return address, size, 1 + addr_len + size_len

    def _memory_range_ok(self, address: int, size: int) -> bool:
        return (size > 0
                and MEMORY_AREA_BASE <= address
                and address + size <= MEMORY_AREA_BASE + MEMORY_AREA_SIZE)

    def _svc_read_memory(self, sid: int, p: bytes) -> None:
        """0x23 ReadMemoryByAddress：读内存映射教学区。

        请求：``23 <ALFID> <address N> <size M>``；正响应：``63 + 数据``。
        真实用途：工程模式读标定/变量、产线读序列号；**风险**：可被用来
        读取内存中的密钥/标定数据，所以真实 ECU 需要安全解锁 + 会话限制。
        """
        parsed = self._parse_alfid(p)
        if parsed is None:
            self._send_response(negative_response(sid, 0x13))
            return
        address, size, _data_off = parsed
        if not self._memory_range_ok(address, size):
            self._send_response(negative_response(sid, 0x31))
            return
        offset = address - MEMORY_AREA_BASE
        data = bytes(self._memory_area[offset:offset + size])
        log.info("0x23 读内存: 地址 %#010x 长度 %d", address, size)
        self._send_response(positive_response(sid, data))

    def _svc_write_memory(self, sid: int, p: bytes) -> None:
        """0x3D WriteMemoryByAddress：写内存映射教学区。

        请求：``3D <ALFID> <address N> <size M> <数据 M 字节>``；正响应：``7D``。
        真实用途：产线写入配置/标定、Bootloader 刷写；**风险极高**
        （可篡改标定/绕过保护），真实项目通常只在编程会话 + 安全解锁下开放。
        """
        parsed = self._parse_alfid(p)
        if parsed is None:
            self._send_response(negative_response(sid, 0x13))
            return
        address, size, data_off = parsed
        if len(p) - data_off != size:
            self._send_response(negative_response(sid, 0x13))
            return
        if not self._memory_range_ok(address, size):
            self._send_response(negative_response(sid, 0x31))
            return
        offset = address - MEMORY_AREA_BASE
        self._memory_area[offset:offset + size] = p[data_off:data_off + size]
        log.info("0x3D 写内存: 地址 %#010x 长度 %d", address, size)
        self._send_response(positive_response(sid))

    # ---------------------------------------------------------------- OBD-II
    def _svc_obd(self, sid: int, p: bytes) -> None:
        """OBD-II 服务（ISO 15031 / SAE J1979），只处理功能寻址 0x7DF 的请求。

        法规要求（排放相关）OBD 扫描仪不进入厂家诊断会话就能直接读数据，
        所以 OBD 分支**不做会话/权限检查**（只支持 4 个最常用的 mode）：
          0x01 当前数据（PID 05 水温 / 0C 转速 / 0D 车速）
          0x03 读已确认 DTC（43 + 数量 + 2 字节 DTC）
          0x04 清 DTC（44）
          0x09 PID 02 读 VIN（49 02 01 + 17 字节）
        """
        if sid == 0x01:
            pid = p[0] if p else None
            data = self._obd_current_data(pid)
            if data is None:
                self._send_response(negative_response(sid, 0x31))
                return
            self._send_response(positive_response(sid, bytes([pid]) + data))
        elif sid == 0x03:
            confirmed = [rec for rec in self.dtc.list_dtcs()
                         if rec.status & BIT_CONFIRMED]
            body = bytearray([len(confirmed) & 0xFF])
            for rec in confirmed:
                b1, b2, _b3 = string_to_dtc(rec.dtc)
                body.extend([b1, b2])
            log.info("OBD mode03: 已确认 DTC %d 条", len(confirmed))
            self._send_response(positive_response(sid, bytes(body)))
        elif sid == 0x04:
            count = self.dtc.clear_all()
            log.info("OBD mode04: 清 DTC（%d 条）", count)
            self._send_response(positive_response(sid))
        elif sid == 0x09:
            if not p or p[0] != 0x02:
                self._send_response(negative_response(sid, 0x12))
                return
            vin = DID_DATABASE[0xF190]
            # 49 02 01 + VIN 17 字节 = 20 字节 → ISO-TP 多帧响应
            self._send_response(positive_response(sid, bytes([0x02, 0x01]) + vin))
        else:
            self._send_response(negative_response(sid, 0x12))

    def _obd_current_data(self, pid: int | None) -> bytes | None:
        """OBD mode 0x01 当前数据，返回 2 字节数据场（A,B）；未知 PID → None。

        换算公式（SAE J1979）：
          PID 0x0C 转速：rpm = ((A*256)+B)/4        本项目模型 rpm = speed*80（钳位）
          PID 0x0D 车速：km/h = A                   本项目直接用 speed_actual
          PID 0x05 水温：℃ = A - 40                 本项目用 coolant_temp
        """
        if pid == 0x0C:
            rpm = min(16000.0, max(800.0, self.speed_actual * 80))
            raw = int(round(rpm * 4)) & 0xFFFF
            return bytes([(raw >> 8) & 0xFF, raw & 0xFF])
        if pid == 0x0D:
            speed = max(0, min(255, int(round(self.speed_actual))))
            return bytes([speed, 0x00])
        if pid == 0x05:
            temp = max(0, min(255, int(round(self.coolant_temp)) + 40))
            return bytes([temp, 0x00])
        return None

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
    # 五、总线监听线程：收 0x7E0（物理）/0x7DF（功能）请求，处理 TP 分片
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
            # 线程级兜底：任何单帧处理异常都只记录并继续，绝不静默退出。
            # 否则现象是"周期报文还在发、诊断请求石沉大海"，极难排查。
            try:
                self._handle_bus_frame(msg)
            except Exception:  # noqa: BLE001
                log.exception("总线帧处理异常（监听线程继续运行）")
                self._tp_rx = IsoTpReceiver()

    def _handle_bus_frame(self, msg) -> None:
        """处理一帧总线报文（由 _bus_listener 逐帧调用，异常由调用方兜底）。"""
        # 忽略自己发送帧的回环副本（模拟环境特有，见 SELF_TX_IDS 说明）
        if msg.arbitration_id in SELF_TX_IDS:
            return
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
            return
        # 功能寻址 0x7DF：诊断仪向"所有 ECU"广播的请求（OBD-II 扫描仪也走这里）。
        # 本 ECU 同时监听物理地址 0x7E0 与功能地址 0x7DF，响应统一发 0x7E8。
        if msg.arbitration_id not in (ID_UDS_REQUEST, ID_UDS_FUNCTIONAL_REQUEST) \
                or not msg.data:
            return
        functional = msg.arbitration_id == ID_UDS_FUNCTIONAL_REQUEST
        pci = msg.data[0]
        if pci & 0xF0 == 0x00:      # 单帧 SF：整条请求在一帧里
            length = pci & 0x0F
            request = bytes(msg.data[1 : 1 + length])
            self._handle_request_safely(request, functional)
            self.last_tester_request_time = time.monotonic()
        elif pci & 0xF0 == 0x10:    # 首帧 FF：立即回流控帧
            total = ((pci & 0x0F) << 8) | msg.data[1]
            self._tp_rx = IsoTpReceiver()
            self._tp_functional = functional   # 记住请求是功能寻址还是物理寻址
            try:
                self._tp_rx.feed(bytes(msg.data))
            except ValueError as exc:      # 非法首帧：重置状态机，不杀线程
                log.warning("收到非法首帧（已重置 TP 状态机）: %s", exc)
                self._tp_rx = IsoTpReceiver()
                return
            # FC 是 ECU→Tester 方向，必须发到响应 ID 0x7E8（不是 0x7E0！）
            # BS=0（不限量）与接收端实现一致：本 ECU 的 IsoTpReceiver
            # 一次收完不做分块确认。**声明的流控参数必须与实际实现能力
            # 一致** —— 若声明 BS=8 却不在收满 8 帧后回新 FC，发送方会
            # 一直等流控帧直到超时（本项目真实踩过的坑）。
            self.bus.send_frame(ID_UDS_RESPONSE, build_flow_control(0, 0))
            log.info("收到多帧请求 FF，总长 %d 字节，已回 FC", total)
        elif pci & 0xF0 == 0x20:    # 连续帧 CF
            # UDP 组播会丢包/重复/乱序 → 序号错乱时 TP 状态机抛 ValueError。
            # 必须捕获并重置状态机：否则一帧丢失就会永久杀死诊断监听线程
            # （现象：周期报文还在发、所有诊断请求石沉大海，真实踩过的坑）。
            try:
                done, data = self._tp_rx.feed(bytes(msg.data))
            except ValueError as exc:
                log.warning("TP 连续帧异常（已重置 TP 状态机）: %s", exc)
                self._tp_rx = IsoTpReceiver()
                return
            if done and data:
                self._handle_request_safely(data, self._tp_functional)
                self.last_tester_request_time = time.monotonic()

    def _handle_request_safely(self, request: bytes,
                               functional: bool = False) -> None:
        """处理请求并兜住所有异常。

        为什么必须兜：诊断处理逻辑里任何未捕获异常（如一个 NameError）
        都会让**整个监听线程静默退出** —— 现象是"周期报文还在发，但所有
        诊断请求石沉大海"，极难排查（本项目真实踩过：刷写服务引用了未
        导入的 SID 常量）。线程级异常必须记录并继续。
        """
        try:
            self.handle_uds_request(request, functional=functional)
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
                    self._seed = None        # 超时回退后旧种子也作废

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
                # 单轮兜底：编码/发送类异常（如某信号超出 DBC 范围）不能让
                # 整个 ECU 进程退出 —— 否则表现为"进程没了但没人知道原因"。
                try:
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
                except Exception:  # noqa: BLE001
                    log.exception("ECU 主循环单轮异常（继续运行）")
                    time.sleep(0.05)
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
            print(f"[OK] 已强制注入 {dtc}: {FAULT_CATALOG[dtc]['name']}"
                  f"（信号判决被绕过，unject 可撤销）")
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
