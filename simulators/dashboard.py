"""
dashboard.py — 数字仪表盘（Instrument Cluster）模拟器
======================================================

模拟真实车载仪表盘的**显示逻辑**：
    收 CAN 信号(0x100/0x200/0x300) → 解析 → 驱动表盘指针/数字/警告灯

两种运行模式：
    1. GUI 模式（默认）：PySide6 画出指针式车速表+转速表+警告灯，给人看
    2. Headless 模式（--headless）：无窗口，把当前显示状态周期性写入
       dashboard_state.json，供 Robot Framework 测试读取断言

为什么需要 headless：CI/自动化测试环境没有屏幕，"显示得对不对"
不能靠眼睛看，必须把显示状态变成可读取的数据 —— 这正是 HMI 自动化测试的
核心思想：把"屏幕上看到的"变成"可断言的字段"。

运行：
    python dashboard.py              # GUI 模式
    python dashboard.py --headless   # 无界面，写状态文件
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bus import VirtualCanBus                      # noqa: E402
from ivi_data import (                             # noqa: E402
    TURN_NAMES, call_status_name, contact_info,
    media_status_name, street_name, track_info,
)
import cantools                                    # noqa: E402

log = logging.getLogger("dashboard")

ID_ENGINE_DATA = 0x100
ID_CLUSTER_DATA = 0x200
ID_TELLTALE = 0x300
ID_MEDIA_PHONE = 0x400      # 座舱 IVI：媒体/电话/空调/室外温度
ID_NAVI_HVAC = 0x500        # 座舱 IVI：导航/空调细节

STATE_FILE = Path(__file__).parent / "dashboard_state.json"

# 警告灯显示配置：信号名 → (中文名, 颜色)
TELLTALE_UI = {
    "MilWarning":        ("MIL 发动机故障", "#ff3b30"),
    "OilPressureLow":    ("机油压力低", "#ff9500"),
    "CoolantTempHigh":   ("水温过高", "#ff3b30"),
    "BatteryLow":        ("蓄电池故障", "#ffcc00"),
    "AbsFault":          ("ABS 故障", "#ffcc00"),
    "AirbagFault":       ("安全气囊故障", "#ff3b30"),
    "SeatbeltWarning":   ("请系安全带", "#ff3b30"),
    "TurnLeft":          ("左转向灯", "#34c759"),
    "TurnRight":         ("右转向灯", "#34c759"),
    "HighBeam":          ("远光灯", "#007aff"),
    "LowBeam":           ("近光灯", "#34c759"),
    "ParkBrake":         ("驻车制动", "#ff3b30"),
}
GEAR_NAMES = {0: "P", 1: "R", 2: "N", 3: "D", 4: "S", 5: "L"}


class ClusterDisplay:
    """仪表盘的"显示模型"：纯数据，不依赖任何 UI 框架。

    GUI 和 headless 共用这一个模型 —— 显示逻辑只写一遍。
    """

    def __init__(self) -> None:
        self.speed = 0.0          # km/h
        self.rpm = 800.0
        self.coolant = 90.0       # ℃
        self.throttle = 0.0       # %
        self.gear = 0
        self.fuel = 60.0          # %
        self.odometer = 0         # km
        self.battery = 13.8       # V
        self.telltales: dict[str, int] = {name: 0 for name in TELLTALE_UI}
        # ---- IVI 座舱信息（0x400/0x500，由车机侧推送）----
        self.media_status = 0     # 0 stop 1 play 2 pause
        self.track_index = 0
        self.volume = 30
        self.call_status = 0      # 0 none 1 incoming 2 active
        self.caller_index = 0
        self.outdoor_temp = 25.0  # ℃
        self.ac_on = 0
        self.hvac_temp = 24
        self.navi_turn = 0        # 0 none ... 7 arrive
        self.navi_distance = 0    # m
        self.street_index = 0
        self.fan_level = 0
        self.seat_heat = 0
        self.recirculate = 0
        self.defrost = 0
        self.updated_at = 0.0
        # ---- 行程电脑（trip）本地累计，由 advance(dt) 推进 ----
        self.trip_km = 0.0
        self.trip_time_s = 0.0
        self._trip_speed_sum = 0.0
        self._trip_samples = 0

    def advance(self, dt: float) -> None:
        """推进行程电脑累计（headless 循环 / GUI 定时器周期性调用）。

        只有车速 > 1 km/h 才累计（和真实车"P 档不计小计"一致）。
        """
        if self.speed > 1.0:
            self.trip_km += self.speed * dt / 3600.0
            self.trip_time_s += dt
            self._trip_speed_sum += self.speed
            self._trip_samples += 1

    @property
    def trip_avg_speed(self) -> float:
        if not self._trip_samples:
            return 0.0
        return self._trip_speed_sum / self._trip_samples

    # ------------------------------------------------------------------
    def update_from_can(self, db: cantools.database.Database, msg) -> None:
        """收到一帧 CAN 报文，更新显示模型。

        注意：cantools 解码枚举信号（DBC 里带 VAL_ 的）返回 NamedSignalValue
        对象（如 GearPosition.D），它是 int 子类但不可哈希 —— 直接拿它做
        dict 键会抛 TypeError。所以这里统一转成原生 int/float。
        """
        try:
            # decode_choices=False：枚举信号直接返回原始数值，
            # 不返回 NamedSignalValue 对象（它不可哈希也不能 int()，
            # 是 HMI 代码里的著名坑）
            if msg.arbitration_id == ID_ENGINE_DATA:
                d = db.decode_message("EngineData", msg.data, decode_choices=False)
                self.speed = float(d["VehicleSpeed"])
                self.rpm = float(d["EngineSpeed"])
                self.coolant = float(d["CoolantTemp"])
                self.throttle = float(d["ThrottlePosition"])
            elif msg.arbitration_id == ID_CLUSTER_DATA:
                d = db.decode_message("ClusterData", msg.data, decode_choices=False)
                self.gear = int(d["GearPosition"])
                self.fuel = float(d["FuelLevel"])
                self.odometer = int(d["Odometer"])
                self.battery = float(d["BatteryVoltage"])
            elif msg.arbitration_id == ID_TELLTALE:
                d = db.decode_message("TelltaleStatus", msg.data, decode_choices=False)
                for name in self.telltales:
                    self.telltales[name] = int(d.get(name, 0))
            elif msg.arbitration_id == ID_MEDIA_PHONE:
                d = db.decode_message("MediaPhoneData", msg.data, decode_choices=False)
                self.media_status = int(d["MediaStatus"])
                self.track_index = int(d["TrackIndex"])
                self.volume = int(d["Volume"])
                self.call_status = int(d["CallStatus"])
                self.caller_index = int(d["CallerIndex"])
                self.outdoor_temp = float(d["OutdoorTemp"])
                self.ac_on = int(d["AcOn"])
                self.hvac_temp = int(d["HvacTemp"])
            elif msg.arbitration_id == ID_NAVI_HVAC:
                d = db.decode_message("NaviHvacData", msg.data, decode_choices=False)
                self.navi_turn = int(d["NaviTurn"])
                self.navi_distance = int(d["NaviDistance"])
                self.street_index = int(d["StreetIndex"])
                self.fan_level = int(d["FanLevel"])
                self.seat_heat = int(d["SeatHeat"])
                self.recirculate = int(d["Recirculate"])
                self.defrost = int(d["Defrost"])
            self.updated_at = time.time()
        except Exception as exc:  # noqa: BLE001
            log.warning("解码 %#x 失败: %s", msg.arbitration_id, exc)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """当前显示状态的字典形式 —— headless 模式和 RF 测试的数据契约"""
        return {
            "speed_kmh": round(self.speed, 1),
            "rpm": round(self.rpm, 0),
            "coolant_c": round(self.coolant, 1),
            "throttle_pct": round(self.throttle, 1),
            "gear": GEAR_NAMES.get(self.gear, "?"),
            "fuel_pct": round(self.fuel, 1),
            "odometer_km": self.odometer,
            "battery_v": round(self.battery, 1),
            "telltales_lit": sorted(
                [name for name, v in self.telltales.items() if v]
            ),
            # ---- IVI 座舱信息（仪表显示的媒体/导航/电话）----
            "media": {
                "status": media_status_name(self.media_status),
                "track_index": self.track_index,
                "track": track_info(self.track_index)[0],
                "artist": track_info(self.track_index)[1],
                "volume": self.volume,
            },
            "phone": {
                "call_status": call_status_name(self.call_status),
                "caller": contact_info(self.caller_index)[0],
            },
            "navi": {
                "turn": TURN_NAMES.get(self.navi_turn, ""),
                "distance_m": self.navi_distance,
                "street": street_name(self.street_index),
            },
            "hvac": {
                "ac_on": bool(self.ac_on),
                "set_temp_c": self.hvac_temp,
                "fan_level": self.fan_level,
                "seat_heat": self.seat_heat,
                "recirculate": bool(self.recirculate),
                "defrost": bool(self.defrost),
                "outdoor_temp_c": round(self.outdoor_temp, 1),
            },
            "trip": {
                "km": round(self.trip_km, 2),
                "time_s": round(self.trip_time_s, 1),
                "avg_speed_kmh": round(self.trip_avg_speed, 1),
                "avg_fuel_lph": 7.5,                      # 教学模型：固定均值
                "inst_fuel_lph": round(6.0 + self.speed / 18, 1),
                "range_km": round(self.fuel / 100 * 520),  # 满箱续航 520km
            },
            "updated_at": self.updated_at,
        }


# ===========================================================================
# Headless 模式：无 GUI，写状态文件
# ===========================================================================
def _atomic_write_json(path: Path, data: dict, retries: int = 8) -> None:
    """原子写 JSON：先写临时文件再 os.replace。

    防两种竞态：
    1. 读取方撞上"truncate 后、内容写完前"的半写文件 → JSONDecodeError；
    2. Windows 特有：os.replace 需要目标文件的 DELETE 权限，而读取方
       open() 默认不共享 DELETE → PermissionError。
    对 2 的解法：写入方让步重试（真实工程做法，读取方不该为写方改模式）。
    """
    import os
    import tempfile
    for attempt in range(retries):
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows 文件锁竞争：目标正被读取，稍后重试
            Path(tmp).unlink(missing_ok=True)
            time.sleep(0.02 * (attempt + 1))
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
    log.warning("原子写 %s 重试 %d 次仍被锁，放弃本轮", path.name, retries)


def _cleanup_stale_tmp() -> None:
    """清理上次被强杀（Ctrl+C / terminate）留下的临时文件。

    为什么需要：原子写用 tempfile.mkstemp 生成 tmp，正常路径会 os.replace 掉；
    但如果进程正好在"写完 tmp、还没 replace"时被强杀，tmp 就留在目录里。
    测试的 Teardown 每次都 terminate 进程 → 跑几十次就攒一堆 tmp，
    新人打开 simulators/ 会看到几十个莫名其妙的文件（本项目真实出现过 40 个）。
    启动时清一次，成本极低。
    """
    for stale in Path(__file__).parent.glob("tmp*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass       # 可能被别的进程占用，跳过就好


def run_headless(bus: VirtualCanBus, display: ClusterDisplay,
                 db: cantools.database.Database, interval: float = 0.1) -> None:
    _cleanup_stale_tmp()
    print(f"[HEADLESS] 仪表盘运行中，状态每 {interval}s 写入 {STATE_FILE.name}")
    print("[HEADLESS] 按 Ctrl+C 退出")
    try:
        last_advance = time.monotonic()
        while True:
            # 每轮把 socket 缓冲里积压的帧全部抽干再写状态，
            # 避免"一轮只收一帧"导致 0x200/0x300 被饿死
            for _ in range(50):
                msg = bus.recv_frame(timeout=0.001)
                if msg is None:
                    break
                display.update_from_can(db, msg)
            now = time.monotonic()
            display.advance(now - last_advance)   # 推进行程电脑累计
            last_advance = now
            try:
                _atomic_write_json(STATE_FILE, display.snapshot())
            except Exception as exc:  # noqa: BLE001
                # 单轮写失败不能杀死整个循环（Windows 文件锁等瞬时错误）
                log.warning("状态写入失败（下轮重试）: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    print("[HEADLESS] 退出")


# ===========================================================================
# GUI 模式：多界面座舱仪表（见 cluster_gui.py）
# ===========================================================================
def run_gui(bus: VirtualCanBus, display: ClusterDisplay,
            db: cantools.database.Database) -> None:
    """GUI 模式已升级为多界面座舱仪表（cluster_gui.py），此处仅作转接。"""
    from cluster_gui import run_cluster_gui
    run_cluster_gui(bus, display, db)


def main() -> None:
    parser = argparse.ArgumentParser(description="数字仪表盘模拟器")
    parser.add_argument("--headless", action="store_true",
                        help="无界面模式，写状态文件（供自动化测试）")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(name)s %(message)s")
    db = cantools.database.load_file(
        str(Path(__file__).parent / "dbc" / "vehicle.dbc"))
    display = ClusterDisplay()

    with VirtualCanBus("CLUSTER") as bus:
        if args.headless:
            run_headless(bus, display, db)
        else:
            run_gui(bus, display, db)


if __name__ == "__main__":
    main()
