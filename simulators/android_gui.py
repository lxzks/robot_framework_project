"""
android_gui.py — 安卓车机中控（Android Automotive 风格）
================================================================

模拟车机中控大屏：状态栏 + 桌面 + 6 个应用界面 + 底部导航栏。
所有操作（点图标/播音乐/开导航/打电话/调空调）都会：
    1. 写入 adb_device_state.json（与 adb_sim.py 共享 —— 命令行
       am start 后这里也会同步切换界面，反之亦然）；
    2. 广播 CAN 报文 0x400（媒体/电话/空调）和 0x500（导航/空调细节）
       —— 仪表盘随即显示同样的信息（两块屏联动）。

这对应真实座舱架构：车机 SoC（安卓）通过 CAN 把"要在仪表上显示的
信息"推给仪表控制器；测试时两块屏用同一数据源，天然一致。

双模式（与 dashboard.py 设计一致）：
    python android_gui.py             # GUI 模式：给人看
    python android_gui.py --headless  # 无界面：CAN 广播 + 状态轮询照常，
                                      # 供 Robot Framework / CI 使用
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# 行缓冲：进程被 kill 时日志不丢（调试 CI/无头模式必备）
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 文件日志：无头模式下定位"进程是否活着、定时器是否还在跑"
logging.basicConfig(
    filename=str(Path(__file__).parent / "android_gui.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)
log = logging.getLogger("android_gui")

sys.path.insert(0, str(Path(__file__).parent))
from adb_sim import load_state, log as adb_log, save_state  # noqa: E402
from ivi_backend import (APPS, CAN_INTERVAL, IviBackend,  # noqa: E402
                         NAVI_INTERVAL, POLL_INTERVAL)
from ivi_data import (CONTACTS, STREETS, TRACKS,           # noqa: E402
                      contact_info, street_name, track_info)

# ===========================================================================
# Headless 模式（无 Qt）：供 RF / CI 使用
# ===========================================================================
def run_headless() -> None:
    backend = IviBackend()
    print("[HEADLESS] 安卓车机运行中：CAN 广播 + 状态轮询（无界面）")
    print("[HEADLESS] 按 Ctrl+C 退出")
    last_can = last_navi = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            # 每一步都包异常：单步失败不能拖死整个循环（生产级健壮性）
            try:
                backend.poll_state()
            except Exception as exc:  # noqa: BLE001
                log.exception("poll_state 失败: %s", exc)
            if now - last_can >= CAN_INTERVAL:
                last_can = now
                try:
                    backend.broadcast_can()
                except Exception as exc:  # noqa: BLE001
                    log.exception("broadcast 失败: %s", exc)
            if now - last_navi >= NAVI_INTERVAL:
                last_navi = now
                try:
                    backend.navi_tick()
                except Exception as exc:  # noqa: BLE001
                    log.exception("navi_tick 失败: %s", exc)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        backend.shutdown()
    print("[HEADLESS] 退出")


# ===========================================================================
# GUI 模式（PySide6）
# ===========================================================================
# 应用注册表：包名 → (界面索引, 图标, 中文名, Activity)
APPS = {
    "com.vehicle.launcher": (0, "🏠", "桌面", ".HomeActivity"),
    "com.vehicle.navi": (1, "🧭", "导航", ".MapActivity"),
    "com.vehicle.media": (2, "🎵", "媒体", ".MainActivity"),
    "com.vehicle.phone": (3, "📞", "电话", ".DialerActivity"),
    "com.vehicle.hvac": (4, "❄", "空调", ".HvacActivity"),
    "com.vehicle.settings": (5, "⚙", "设置", ".SettingsActivity"),
}

A_BG = "#101418"
A_PANEL = "#1a2027"
A_PANEL2 = "#222b35"
A_BORDER = "#2d3844"
A_TEXT = "#e8eef5"
A_DIM = "#8a97a6"
A_ACCENT = "#4fc3f7"
A_GREEN = "#66bb6a"
A_ORANGE = "#ffa726"
A_RED = "#ef5350"


def panel_style() -> str:
    return (f"background-color: {A_PANEL}; border: 1px solid {A_BORDER}; "
            f"border-radius: 14px;")


def btn_style(color: str = A_PANEL2, text: str = A_TEXT) -> str:
    return (f"background-color: {color}; color: {text}; "
            f"border: 1px solid {A_BORDER}; border-radius: 10px; "
            f"font-size: 14px; padding: 8px 16px;")


def app_button(icon: str, name: str):
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(f"{icon}\n{name}")
    b.setFixedSize(120, 110)
    b.setStyleSheet(
        f"QPushButton {{background-color: {A_PANEL}; color: {A_TEXT}; "
        f"border: 1px solid {A_BORDER}; border-radius: 16px; "
        f"font-size: 14px;}}"
        f"QPushButton:pressed {{background-color: {A_ACCENT}; "
        f"color: #000000;}}")
    return b


# ---------------------------------------------------------------------------
# 六个界面（纯 Qt，刷新数据来自 backend）
# ---------------------------------------------------------------------------
from PySide6.QtCore import Qt, QTimer                        # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout,  # noqa: E402
                               QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)


class LauncherPage(QWidget):
    """车机桌面：应用图标网格（点击 = am start）"""

    def __init__(self, on_launch) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)

        self.clock_label = QLabel(time.strftime("%H:%M"))
        self.clock_label.setStyleSheet(f"color: {A_TEXT}; font-size: 56px; "
                                       f"font-weight: bold; border: none;")
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        date = QLabel(time.strftime("%m月%d日"))
        date.setStyleSheet(f"color: {A_DIM}; font-size: 16px; border: none;")
        date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.clock_label)
        root.addWidget(date)
        root.addStretch(1)

        grid = QGridLayout()
        grid.setSpacing(24)
        order = ["com.vehicle.navi", "com.vehicle.media", "com.vehicle.phone",
                 "com.vehicle.hvac", "com.vehicle.settings"]
        for i, pkg in enumerate(order):
            _, icon, name, _ = APPS[pkg]
            b = app_button(icon, name)
            b.clicked.connect(lambda _=False, p=pkg: on_launch(p))
            grid.addWidget(b, i // 3, i % 3)
        root.addLayout(grid)
        root.addStretch(1)

        hint = QLabel("点击图标启动应用（等价于 adb shell am start）")
        hint.setStyleSheet(f"color: {A_DIM}; font-size: 12px; border: none;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)


class MediaPage(QWidget):
    """媒体应用：歌单/播放控制/音量"""

    def __init__(self, on_command) -> None:
        super().__init__()
        self.on_command = on_command
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        title = QLabel("🎵 音乐")
        title.setStyleSheet(f"color: {A_TEXT}; font-size: 20px; "
                            f"font-weight: bold; border: none;")
        root.addWidget(title)

        self.cover = QLabel("♪")
        self.cover.setFixedSize(180, 180)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {A_ACCENT}, stop:1 #7e57c2); border-radius: 18px; "
            f"color: rgba(255,255,255,0.92); font-size: 72px;")
        root.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignCenter)

        self.track_label = QLabel("--")
        self.track_label.setStyleSheet(f"color: {A_TEXT}; font-size: 22px; "
                                       f"font-weight: bold; border: none;")
        self.track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label = QLabel("--")
        self.artist_label.setStyleSheet(f"color: {A_DIM}; font-size: 14px; "
                                        f"border: none;")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.track_label)
        root.addWidget(self.artist_label)
        root.addSpacing(10)

        ctrl = QHBoxLayout()
        self.btn_prev = QPushButton("⏮")
        self.btn_play = QPushButton("▶")
        self.btn_next = QPushButton("⏭")
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.setFixedSize(64, 48)
            b.setStyleSheet(btn_style())
            ctrl.addWidget(b)
        root.addLayout(ctrl)
        self.btn_prev.clicked.connect(lambda: self.on_command("media_prev"))
        self.btn_play.clicked.connect(lambda: self.on_command("media_toggle"))
        self.btn_next.clicked.connect(lambda: self.on_command("media_next"))

        vol = QHBoxLayout()
        vol_lbl = QLabel("音量")
        vol_lbl.setStyleSheet(f"color: {A_DIM}; border: none; font-size: 13px;")
        vol.addWidget(vol_lbl)
        self.btn_vol_down = QPushButton("－")
        self.btn_vol_up = QPushButton("＋")
        for b in (self.btn_vol_down, self.btn_vol_up):
            b.setFixedSize(48, 36)
            b.setStyleSheet(btn_style())
            vol.addWidget(b)
        self.btn_vol_down.clicked.connect(lambda: self.on_command("vol_down"))
        self.btn_vol_up.clicked.connect(lambda: self.on_command("vol_up"))
        vol.addStretch(1)
        root.addLayout(vol)
        root.addStretch(1)

        list_title = QLabel("播放列表")
        list_title.setStyleSheet(f"color: {A_DIM}; font-size: 13px; "
                                 f"border: none;")
        root.addWidget(list_title)
        for i, (name, artist, _dur) in enumerate(TRACKS):
            row = QPushButton(f"{i + 1}. {name} — {artist}")
            row.setStyleSheet(f"QPushButton {{text-align: left; {btn_style()}}}")
            row.clicked.connect(
                lambda _=False, idx=i: self.on_command(f"play_track:{idx}"))
            root.addWidget(row)

    def refresh(self, ivi: dict) -> None:
        name, artist, _ = track_info(ivi["track_index"])
        status = {0: "已停止", 1: "播放中", 2: "已暂停"}[ivi["media_status"]]
        self.track_label.setText(name)
        self.btn_play.setText("⏸" if ivi["media_status"] == 1 else "▶")
        self.artist_label.setText(f"{artist} · {status} · 音量 "
                                  f"{ivi['volume']}")


class NaviPage(QWidget):
    """导航应用：目的地选择 + 转向/距离/ETA（距离随行驶递减）"""

    def __init__(self, on_command) -> None:
        super().__init__()
        self.on_command = on_command
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        title = QLabel("🧭 导航")
        title.setStyleSheet(f"color: {A_TEXT}; font-size: 20px; "
                            f"font-weight: bold; border: none;")
        root.addWidget(title)

        self.status_label = QLabel("未开始导航")
        self.status_label.setStyleSheet(f"color: {A_DIM}; font-size: 15px; "
                                        f"border: none;")
        root.addWidget(self.status_label)

        self.dist_label = QLabel("--")
        self.dist_label.setStyleSheet(
            f"color: {A_ACCENT}; font-size: 46px; font-weight: bold; "
            f"border: none;")
        self.dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.dist_label)
        self.turn_label = QLabel("")
        self.turn_label.setStyleSheet(f"color: {A_TEXT}; font-size: 18px; "
                                      f"border: none;")
        self.turn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.turn_label)

        ctrl = QHBoxLayout()
        self.btn_start = QPushButton("开始导航")
        self.btn_stop = QPushButton("结束导航")
        for b in (self.btn_start, self.btn_stop):
            b.setFixedHeight(42)
            b.setStyleSheet(btn_style())
            ctrl.addWidget(b)
        self.btn_start.clicked.connect(lambda: self.on_command("navi_start"))
        self.btn_stop.clicked.connect(lambda: self.on_command("navi_stop"))
        root.addLayout(ctrl)

        dest_title = QLabel("选择目的地")
        dest_title.setStyleSheet(f"color: {A_DIM}; font-size: 13px; "
                                 f"border: none;")
        root.addWidget(dest_title)
        for i, street in enumerate(STREETS):
            row = QPushButton(f"📍 {street}")
            row.setStyleSheet(
                f"QPushButton {{text-align: left; {btn_style()}}}")
            row.clicked.connect(
                lambda _=False, idx=i: self.on_command(f"navi_dest:{idx}"))
            root.addWidget(row)
        root.addStretch(1)

    def refresh(self, ivi: dict) -> None:
        if not ivi["navi_active"]:
            self.status_label.setText("未开始导航")
            self.dist_label.setText("--")
            self.turn_label.setText("")
            return
        d = ivi["navi_distance"]
        self.dist_label.setText(f"{d / 1000:.1f} km" if d >= 1000
                                else f"{d} m")
        turn_names = ["", "左转", "右转", "直行", "偏左", "偏右", "掉头", "到达"]
        self.turn_label.setText(
            f"{turn_names[ivi['navi_turn']]} 进入 "
            f"{street_name(ivi['street_index'])}")
        self.status_label.setText("导航中")


class PhonePage(QWidget):
    """电话应用：联系人 + 拨号 + 通话状态"""

    def __init__(self, on_command) -> None:
        super().__init__()
        self.on_command = on_command
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        title = QLabel("📞 电话")
        title.setStyleSheet(f"color: {A_TEXT}; font-size: 20px; "
                            f"font-weight: bold; border: none;")
        root.addWidget(title)

        self.call_card = QFrame()
        self.call_card.setStyleSheet(panel_style())
        cc = QVBoxLayout(self.call_card)
        self.call_status = QLabel("空闲")
        self.call_status.setStyleSheet(f"color: {A_DIM}; font-size: 14px; "
                                       f"border: none;")
        self.caller = QLabel("--")
        self.caller.setStyleSheet(f"color: {A_TEXT}; font-size: 26px; "
                                  f"font-weight: bold; border: none;")
        cc.addWidget(self.call_status)
        cc.addWidget(self.caller)
        btns = QHBoxLayout()
        self.btn_hangup = QPushButton("📵 挂断")
        self.btn_hangup.setStyleSheet(btn_style(A_RED, "#ffffff"))
        self.btn_hangup.clicked.connect(lambda: self.on_command("call_end"))
        btns.addWidget(self.btn_hangup)
        cc.addLayout(btns)
        root.addWidget(self.call_card)

        list_title = QLabel("联系人（点击呼叫）")
        list_title.setStyleSheet(f"color: {A_DIM}; font-size: 13px; "
                                 f"border: none;")
        root.addWidget(list_title)
        for i, (name, num) in enumerate(CONTACTS):
            row = QPushButton(f"👤 {name}    {num}")
            row.setStyleSheet(
                f"QPushButton {{text-align: left; {btn_style()}}}")
            row.clicked.connect(
                lambda _=False, idx=i: self.on_command(f"call:{idx}"))
            root.addWidget(row)
        root.addStretch(1)

    def refresh(self, ivi: dict) -> None:
        status = ivi["call_status"]
        if status == 0:
            self.call_status.setText("空闲")
            self.caller.setText("--")
            self.call_status.setStyleSheet(
                f"color: {A_DIM}; font-size: 14px; border: none;")
            self.btn_hangup.setEnabled(False)
        elif status == 1:
            self.call_status.setText("来电…")
            self.caller.setText(contact_info(ivi["caller_index"])[0])
            self.call_status.setStyleSheet(
                f"color: {A_ORANGE}; font-size: 14px; font-weight: bold; "
                f"border: none;")
            self.btn_hangup.setEnabled(True)
        else:
            self.call_status.setText("通话中")
            self.caller.setText(contact_info(ivi["caller_index"])[0])
            self.call_status.setStyleSheet(
                f"color: {A_GREEN}; font-size: 14px; font-weight: bold; "
                f"border: none;")
            self.btn_hangup.setEnabled(True)


class HvacPage(QWidget):
    """空调应用：温度/风量/AC/座椅加热/内循环/除霜"""

    def __init__(self, on_command) -> None:
        super().__init__()
        self.on_command = on_command
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        title = QLabel("❄ 空调")
        title.setStyleSheet(f"color: {A_TEXT}; font-size: 20px; "
                            f"font-weight: bold; border: none;")
        root.addWidget(title)

        temp_row = QHBoxLayout()
        self.btn_temp_down = QPushButton("－")
        self.temp_label = QLabel("24℃")
        self.temp_label.setStyleSheet(
            f"color: {A_ACCENT}; font-size: 44px; font-weight: bold; "
            f"border: none;")
        self.temp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_temp_up = QPushButton("＋")
        for b in (self.btn_temp_down, self.btn_temp_up):
            b.setFixedSize(56, 56)
            b.setStyleSheet(btn_style())
        self.btn_temp_down.clicked.connect(
            lambda: self.on_command("temp_down"))
        self.btn_temp_up.clicked.connect(lambda: self.on_command("temp_up"))
        temp_row.addStretch(1)
        temp_row.addWidget(self.btn_temp_down)
        temp_row.addWidget(self.temp_label)
        temp_row.addWidget(self.btn_temp_up)
        temp_row.addStretch(1)
        root.addLayout(temp_row)

        fan_row = QHBoxLayout()
        fan_lbl = QLabel("风量")
        fan_lbl.setStyleSheet(f"color: {A_DIM}; border: none;")
        fan_row.addWidget(fan_lbl)
        self.fan_bars = []
        for _i in range(7):
            bar = QFrame()
            bar.setFixedSize(26, 30)
            bar.setStyleSheet(
                f"background-color: {A_PANEL2}; border: 1px solid "
                f"{A_BORDER}; border-radius: 4px;")
            self.fan_bars.append(bar)
            fan_row.addWidget(bar)
        fan_row.addStretch(1)
        root.addLayout(fan_row)

        switches = QGridLayout()
        switches.setSpacing(10)
        self.switch_btns: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate([
                ("ac_on", "A/C"), ("seat_heat", "座椅加热"),
                ("recirculate", "内循环"), ("defrost", "除霜")]):
            b = QPushButton(label)
            b.setFixedHeight(44)
            b.setStyleSheet(btn_style())
            b.clicked.connect(
                lambda _=False, k=key: self.on_command(f"toggle:{k}"))
            self.switch_btns[key] = b
            switches.addWidget(b, i // 2, i % 2)
        root.addLayout(switches)
        root.addStretch(1)

    def refresh(self, ivi: dict) -> None:
        self.temp_label.setText(f"{ivi['hvac_temp']}℃")
        for i, bar in enumerate(self.fan_bars):
            on = i < ivi["fan_level"]
            bar.setStyleSheet(
                f"background-color: {'#4fc3f7' if on else A_PANEL2}; "
                f"border: 1px solid {A_BORDER}; border-radius: 4px;")
        for key, b in self.switch_btns.items():
            on = bool(ivi[key])
            b.setStyleSheet(btn_style(A_ACCENT, "#000000") if on
                            else btn_style())


class SettingsPage(QWidget):
    """设置：读 getprop 值展示"""

    def __init__(self, props: dict) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("⚙ 设置")
        title.setStyleSheet(f"color: {A_TEXT}; font-size: 20px; "
                            f"font-weight: bold; border: none;")
        root.addWidget(title)
        items = [
            ("机型", props.get("ro.product.model", "--")),
            ("品牌", props.get("ro.product.brand", "--")),
            ("安卓版本", props.get("ro.build.version.release", "--")),
            ("API Level", props.get("ro.build.version.sdk", "--")),
            ("车辆类型", props.get("ro.car.vehicle_type", "--")),
            ("时区", props.get("persist.sys.timezone", "--")),
        ]
        for k, v in items:
            row = QFrame()
            row.setStyleSheet(panel_style())
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 8, 14, 8)
            a = QLabel(k)
            a.setStyleSheet(f"color: {A_TEXT}; font-size: 14px; border: none;")
            b = QLabel(str(v))
            b.setStyleSheet(f"color: {A_DIM}; font-size: 13px; border: none;")
            rl.addWidget(a)
            rl.addStretch(1)
            rl.addWidget(b)
            root.addWidget(row)
        root.addStretch(1)

    def refresh(self, ivi: dict) -> None:
        """设置页为静态展示，无需刷新（占位保持接口一致）"""


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class AndroidMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("安卓车机模拟器 · robot_framework")
        self.setStyleSheet(f"background-color: {A_BG};")
        self.resize(860, 1000)

        self.backend = IviBackend()
        state = load_state()
        self.pages = [
            LauncherPage(self.launch_app),
            NaviPage(self.on_command),
            MediaPage(self.on_command),
            PhonePage(self.on_command),
            HvacPage(self.on_command),
            SettingsPage(state["props"]),
        ]
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)

        # ---- 顶部状态栏 ----
        status = QHBoxLayout()
        status.setContentsMargins(16, 8, 16, 8)
        self.clock_label = QLabel(time.strftime("%H:%M"))
        self.clock_label.setStyleSheet(f"color: {A_TEXT}; font-size: 15px; "
                                       f"font-weight: bold; border: none;")
        icons = QLabel("📶  🛰  🔗  🎵")
        icons.setStyleSheet(f"color: {A_TEXT}; font-size: 13px; border: none;")
        self.batt_label = QLabel("🔋 78%")
        self.batt_label.setStyleSheet(f"color: {A_TEXT}; font-size: 13px; "
                                      f"border: none;")
        status.addWidget(self.clock_label)
        status.addStretch(1)
        status.addWidget(icons)
        status.addWidget(self.batt_label)

        # ---- 底部导航栏（Android 三大金刚键）----
        nav = QHBoxLayout()
        nav.setContentsMargins(16, 6, 16, 10)
        self.btn_back = QPushButton("◀")
        self.btn_home = QPushButton("●")
        self.btn_recent = QPushButton("■")
        for b in (self.btn_back, self.btn_home, self.btn_recent):
            b.setFixedSize(72, 44)
            b.setStyleSheet(btn_style())
            nav.addWidget(b)
        self.btn_back.clicked.connect(self.on_back)
        self.btn_home.clicked.connect(self.on_home)
        self.btn_recent.clicked.connect(self.on_recent)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(status)
        root.addWidget(self.stack, 1)
        root.addLayout(nav)
        self.setCentralWidget(central)

        # ---- 定时器驱动 backend（与 headless 完全相同的周期）----
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(int(POLL_INTERVAL * 1000))

        self.can_timer = QTimer(self)
        self.can_timer.timeout.connect(self.backend.broadcast_can)
        self.can_timer.start(int(CAN_INTERVAL * 1000))

        self.navi_timer = QTimer(self)
        self.navi_timer.timeout.connect(self.backend.navi_tick)
        self.navi_timer.start(int(NAVI_INTERVAL * 1000))

        self._answer_timer = None
        # 初始进入 adb 状态指定的界面
        changed, st = self.backend.poll_state()
        self._sync_focus(st["current_focus"], force=True)
        self._refresh_pages(st)

    # ------------------------------------------------------------------
    def launch_app(self, package: str) -> None:
        """启动应用（等价 am start）并立即切页。

        注意：不能依赖 _sync_focus 的"焦点变化"判断 —— set_focus 内部
        已把 _last_focus 更新成新焦点，再走 _sync_focus 会因"焦点没变"
        而直接 return，界面不切换（真实踩过的坑：点了图标没反应）。
        这里直接按 APPS 注册表切页，最简单确定。
        """
        self.backend.set_focus(package)
        if package in APPS:
            self.stack.setCurrentIndex(APPS[package][0])
        self._refresh_pages(load_state())

    def on_back(self) -> None:
        if self.stack.currentIndex() != 0:
            self.launch_app("com.vehicle.launcher")
        else:
            state = load_state()
            adb_log(state, "Input", "keyevent 4 (BACK)")
            save_state(state)

    def on_home(self) -> None:
        self.launch_app("com.vehicle.launcher")

    def on_recent(self) -> None:
        state = load_state()
        adb_log(state, "Input", "keyevent 187 (RECENT_APPS)")
        save_state(state)

    # ------------------------------------------------------------------
    def on_command(self, command: str) -> None:
        self.backend.command(command)
        if command.startswith("call:"):
            # 2 秒后自动接听（模拟车主接电话）。
            # 先停掉旧定时器：连续点多个联系人时避免多个 timer 并存
            if self._answer_timer is not None:
                self._answer_timer.stop()
            self._answer_timer = QTimer(self)
            self._answer_timer.setSingleShot(True)
            self._answer_timer.timeout.connect(self.backend.auto_answer)
            self._answer_timer.timeout.connect(
                lambda: self._refresh_pages(load_state()))
            self._answer_timer.start(2000)
        self._refresh_pages(load_state())

    # ------------------------------------------------------------------
    def _poll(self) -> None:
        try:
            changed, state = self.backend.poll_state()
            if changed:
                self._sync_focus(state["current_focus"])
            self._refresh_pages(state)
            self.clock_label.setText(time.strftime("%H:%M"))
            self.batt_label.setText(f"🔋 {state['battery']['level']}%")
        except Exception as exc:  # noqa: BLE001
            log.exception("GUI 轮询失败: %s", exc)

    def _sync_focus(self, focus: str, force: bool = False) -> None:
        """按 current_focus 切换界面（adb am start → GUI 同步）"""
        if focus == self.backend._last_focus and not force:
            return
        self.backend._last_focus = focus
        pkg = focus.split("/")[0]
        if pkg in APPS:
            self.stack.setCurrentIndex(APPS[pkg][0])

    def _refresh_pages(self, state: dict) -> None:
        ivi = state["ivi"]
        self.pages[0].clock_label.setText(time.strftime("%H:%M"))
        self.pages[1].refresh(ivi)   # Navi
        self.pages[2].refresh(ivi)   # Media
        self.pages[3].refresh(ivi)   # Phone
        self.pages[4].refresh(ivi)   # Hvac

    def closeEvent(self, event) -> None:
        self.backend.shutdown()
        super().closeEvent(event)


def run_gui() -> None:
    app = QApplication(sys.argv)
    win = AndroidMainWindow()
    win.show()
    Path(__file__).with_name("android_gui.pid").write_text(str(os.getpid()))
    log.info("android_gui GUI 启动 pid=%s", os.getpid())
    sys.exit(app.exec())


def main() -> None:
    parser = argparse.ArgumentParser(description="安卓车机模拟器")
    parser.add_argument("--headless", action="store_true",
                        help="无界面模式：CAN 广播 + 状态轮询（供 RF/CI）")
    args = parser.parse_args()
    if args.headless:
        run_headless()
    else:
        run_gui()


if __name__ == "__main__":
    main()
