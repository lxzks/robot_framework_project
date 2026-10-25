"""
ivi_backend.py — 车机 IVI 后端（不依赖 Qt）
=============================================

把"安卓车机"的核心逻辑从 GUI 里抽出来：
    - 状态管理（读写 adb_device_state.json，与 adb_sim.py 共享）
    - CAN 广播（0x400 媒体/电话/空调 + 0x500 导航/空调细节）
    - 应用启动（等价 am start）、应用内命令（媒体/导航/电话/空调）

三个使用者：
    - android_gui.py      GUI 模式（定时器驱动本后端）
    - android_gui.py      headless 模式（循环驱动本后端）
    - libraries/AdbLibrary.py  Robot Framework 用例（显式驱动，
                              测试不依赖长生命周期子进程的定时器，
                              确定性更强）
"""

from __future__ import annotations

import logging
from pathlib import Path

import cantools

import sys
sys.path.insert(0, str(Path(__file__).parent))
from adb_sim import (STATE_FILE, load_state, log as adb_log,  # noqa: E402
                     save_state)
from bus import VirtualCanBus                                   # noqa: E402
from ivi_data import TRACKS                                     # noqa: E402

log = logging.getLogger("ivi_backend")

ID_MEDIA_PHONE = 0x400
ID_NAVI_HVAC = 0x500
POLL_INTERVAL = 0.3     # 状态文件轮询（秒）
CAN_INTERVAL = 0.5      # CAN 广播周期（秒）
NAVI_INTERVAL = 1.0     # 导航距离推进周期（秒）

# 应用注册表：包名 → (界面索引, 图标, 中文名, Activity)
APPS = {
    "com.vehicle.launcher": (0, "🏠", "桌面", ".HomeActivity"),
    "com.vehicle.navi": (1, "🧭", "导航", ".MapActivity"),
    "com.vehicle.media": (2, "🎵", "媒体", ".MainActivity"),
    "com.vehicle.phone": (3, "📞", "电话", ".DialerActivity"),
    "com.vehicle.hvac": (4, "❄", "空调", ".HvacActivity"),
    "com.vehicle.settings": (5, "⚙", "设置", ".SettingsActivity"),
}


class IviBackend:
    """车机 IVI 核心逻辑（无 Qt 依赖）"""

    def __init__(self, node_name: str = "IVI-HEADUNIT") -> None:
        self.db = cantools.database.load_file(
            str(Path(__file__).parent / "dbc" / "vehicle.dbc"))
        self.bus = VirtualCanBus(node_name)
        self._last_focus = ""
        self._ensure_state_file()

    def _ensure_state_file(self) -> None:
        state = load_state()
        if not STATE_FILE.exists():
            # 上电初始化：首次启动落一份初始状态（与 adb 命令行共享）
            save_state(state)
            adb_log(state, "System", "headunit boot, state initialized")
            save_state(state)

    # ------------------------------------------------------------------
    def broadcast_can(self) -> None:
        """把 IVI 状态广播到 CAN：0x400 媒体/电话/空调 + 0x500 导航/空调"""
        state = load_state()
        ivi = state["ivi"]
        d1 = self.db.encode_message("MediaPhoneData", {
            "MediaStatus": ivi["media_status"],
            "TrackIndex": ivi["track_index"],
            "Volume": ivi["volume"],
            "CallStatus": ivi["call_status"],
            "CallerIndex": ivi["caller_index"],
            "OutdoorTemp": 25,            # 教学固定值
            "AcOn": ivi["ac_on"],
            "HvacTemp": ivi["hvac_temp"],
        })
        self.bus.send_frame(ID_MEDIA_PHONE, d1)
        d2 = self.db.encode_message("NaviHvacData", {
            "NaviTurn": ivi["navi_turn"],
            "NaviDistance": ivi["navi_distance"],
            "StreetIndex": ivi["street_index"],
            "FanLevel": ivi["fan_level"],
            "SeatHeat": ivi["seat_heat"],
            "Recirculate": ivi["recirculate"],
            "Defrost": ivi["defrost"],
        })
        self.bus.send_frame(ID_NAVI_HVAC, d2)
        log.info("CAN 广播 track=%s navi=%s/%s", ivi["track_index"],
                 ivi["navi_active"], ivi["navi_distance"])

    # ------------------------------------------------------------------
    def poll_state(self) -> tuple[bool, dict]:
        """轮询状态文件。返回 (焦点是否变化, 状态字典)。

        焦点变化由 adb 命令行（am start）或 GUI 点击产生。
        """
        state = load_state()
        focus = state["current_focus"]
        changed = focus != self._last_focus
        if changed:
            log.info("焦点变化: %s", focus)
            self._last_focus = focus
        return changed, state

    # ------------------------------------------------------------------
    def navi_tick(self) -> None:
        """导航中：距离按 40km/h 递减（简化行驶模型）"""
        state = load_state()
        ivi = state["ivi"]
        if not ivi["navi_active"]:
            return
        ivi["navi_distance"] = max(0, ivi["navi_distance"] - 11)  # 40km/h≈11m/s
        if ivi["navi_distance"] == 0:
            ivi["navi_turn"] = 7   # 到达
            ivi["navi_active"] = 0
        else:
            # 每 500m 换一次转向提示（演示）
            ivi["navi_turn"] = 1 + (ivi["navi_distance"] // 500) % 3
        save_state(state)

    # ------------------------------------------------------------------
    def set_focus(self, package: str) -> None:
        """启动应用（等价 am start）"""
        state = load_state()
        if not any(p["name"] == package for p in state["packages"]):
            adb_log(state, "PackageManager", f"Not installed: {package}")
            save_state(state)
            return
        state["current_focus"] = f"{package}/{APPS[package][3]}"
        adb_log(state, "ActivityManager", f"Starting: {package}")
        save_state(state)
        self._last_focus = state["current_focus"]

    def command(self, command: str) -> None:
        """应用内命令（媒体/导航/电话/空调）——GUI 按钮与测试共用"""
        state = load_state()
        ivi = state["ivi"]
        if command == "media_toggle":
            ivi["media_status"] = 0 if ivi["media_status"] == 1 else 1
        elif command == "media_next":
            ivi["track_index"] = (ivi["track_index"] + 1) % len(TRACKS)
            ivi["media_status"] = 1
        elif command == "media_prev":
            ivi["track_index"] = (ivi["track_index"] - 1) % len(TRACKS)
            ivi["media_status"] = 1
        elif command.startswith("play_track:"):
            ivi["track_index"] = int(command.split(":")[1])
            ivi["media_status"] = 1
        elif command == "vol_up":
            ivi["volume"] = min(100, ivi["volume"] + 10)
        elif command == "vol_down":
            ivi["volume"] = max(0, ivi["volume"] - 10)
        elif command == "navi_start":
            ivi["navi_active"] = 1
            ivi["navi_distance"] = 5000       # 默认 5km 演示路线
            ivi["navi_turn"] = 3              # 直行
            ivi["street_index"] = 0
        elif command == "navi_stop":
            ivi["navi_active"] = 0
            ivi["navi_distance"] = 0
        elif command.startswith("navi_dest:"):
            idx = int(command.split(":")[1])
            ivi["street_index"] = idx
            ivi["navi_active"] = 1
            ivi["navi_distance"] = 3000 + idx * 800
            ivi["navi_turn"] = 1 + (idx % 3)  # 左/右/直行 轮换
        elif command.startswith("call:"):
            ivi["caller_index"] = int(command.split(":")[1])
            ivi["call_status"] = 1            # 来电
        elif command == "call_end":
            ivi["call_status"] = 0
        elif command == "temp_up":
            ivi["hvac_temp"] = min(32, ivi["hvac_temp"] + 1)
        elif command == "temp_down":
            ivi["hvac_temp"] = max(16, ivi["hvac_temp"] - 1)
        elif command.startswith("toggle:"):
            key = command.split(":")[1]
            ivi[key] = 0 if ivi[key] else 1
        adb_log(state, "IVI", command)
        save_state(state)

    def auto_answer(self) -> None:
        """来电自动接听（GUI 的模拟）"""
        state = load_state()
        if state["ivi"]["call_status"] == 1:
            state["ivi"]["call_status"] = 2     # 接听 → 通话中
            adb_log(state, "Telecom", "call answered")
            save_state(state)

    def shutdown(self) -> None:
        self.bus.shutdown()
