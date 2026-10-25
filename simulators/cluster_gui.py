"""
cluster_gui.py — 多界面座舱仪表盘（Instrument Cluster GUI）
=============================================================

真实车载仪表盘远不止"两块表"——它是一块**多界面座舱屏**：
按方向盘按键/tab 切换，常见界面包括：

    主页(仪表) / 行程电脑 / 媒体 / 导航 / 电话 / 设置 / 车辆信息

本模块实现全部 7 个界面 + 开机启动画面 + 转向灯闪烁动画。
显示数据全部来自 CAN 总线（ClusterDisplay 模型），与 headless 模式
共用同一模型 —— GUI 看到的和 RF 断言的是同一份数据。

界面索引：
    0 Main 主页      1 Trip 行程     2 Media 媒体    3 Navi 导航
    4 Phone 电话     5 Settings 设置  6 VehicleInfo 车辆信息

运行：
    python dashboard.py          # GUI 模式（不带 --headless）
"""

from __future__ import annotations

import logging
import math
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("cluster_gui")

from PySide6.QtCore import (QPointF, QRectF, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QPainter, QPen)
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QProgressBar,
                               QPushButton, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).parent))
from dashboard import (STATE_FILE, TELLTALE_UI, ClusterDisplay,  # noqa: E402
                       _atomic_write_json)
from ivi_data import (                                     # noqa: E402
    CONTACTS, TRACKS, TURN_NAMES,
)

# ---------------------------------------------------------------------------
# 主题色（深色座舱风格）
# ---------------------------------------------------------------------------
C_BG = "#0d1117"          # 背景
C_PANEL = "#161b22"       # 面板
C_PANEL2 = "#1c2330"      # 面板亮
C_BORDER = "#2a3140"      # 边框
C_TEXT = "#e6edf3"        # 主文字
C_TEXT_DIM = "#8b949e"    # 次文字
C_ACCENT = "#58a6ff"      # 主色（蓝）
C_GREEN = "#3fb950"       # 绿
C_ORANGE = "#d29922"      # 橙
C_RED = "#f85149"         # 红

STYLE_PANEL = (f"background-color: {C_PANEL}; border: 1px solid {C_BORDER}; "
               f"border-radius: 14px;")
STYLE_TAB_ACTIVE = (f"background-color: {C_ACCENT}; color: #ffffff; "
                    f"border: none; border-radius: 10px; font-size: 14px; "
                    f"padding: 8px 0; font-weight: bold;")
STYLE_TAB = (f"background-color: {C_PANEL2}; color: {C_TEXT_DIM}; "
             f"border: 1px solid {C_BORDER}; border-radius: 10px; "
             f"font-size: 14px; padding: 8px 0;")
STYLE_BTN = (f"background-color: {C_PANEL2}; color: {C_TEXT}; "
             f"border: 1px solid {C_BORDER}; border-radius: 10px; "
             f"font-size: 13px; padding: 6px 14px;")


# ===========================================================================
# 启动画面
# ===========================================================================
class BootScreen(QWidget):
    """开机启动画面：真实仪表上电有 boot 动画（约 1~2 秒）"""

    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(f"background-color: {C_BG};")
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        logo = QLabel("VISTEON")
        logo.setStyleSheet(f"color: {C_TEXT}; font-size: 42px; "
                           f"font-weight: bold; letter-spacing: 8px;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("数字座舱仿真 · robot_framework")
        sub.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 14px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedWidth(360)
        self.bar.setStyleSheet(
            f"QProgressBar {{background-color: {C_PANEL}; border: 1px solid "
            f"{C_BORDER}; border-radius: 6px; height: 10px; text-align: center;}}"
            f"QProgressBar::chunk {{background-color: {C_ACCENT}; "
            f"border-radius: 5px;}}")
        self._value = 0

        root.addStretch(2)
        root.addWidget(logo)
        root.addSpacing(10)
        root.addWidget(sub)
        root.addSpacing(30)
        root.addWidget(self.bar, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addStretch(3)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._value = 0
        self._timer.start(40)   # 40ms × 50 = 2 秒

    def _tick(self) -> None:
        self._value += 4
        self.bar.setValue(self._value)
        if self._value >= 100:
            self._timer.stop()
            self.finished.emit()


# ===========================================================================
# 通用组件
# ===========================================================================
class GaugeWidget(QWidget):
    """指针式圆形表盘（车速/转速）"""

    def __init__(self, title: str, unit: str, vmin: float, vmax: float,
                 major: int, red_from: float | None = None) -> None:
        super().__init__()
        self.title, self.unit = title, unit
        self.vmin, self.vmax, self.major = vmin, vmax, major
        self.red_from = red_from
        self.value = vmin
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_value(self, v: float) -> None:
        self.value = max(self.vmin, min(self.vmax, v))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        cx, cy, r = self.width() / 2, self.height() / 2, side / 2 - 26

        # 表盘底
        p.setPen(QPen(QColor(C_BORDER), 2))
        p.setBrush(QColor(C_PANEL))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        # 刻度
        for i in range(self.major + 1):
            v = self.vmin + (self.vmax - self.vmin) * i / self.major
            ang = math.radians(135 + 270 * i / self.major)
            x1, y1 = cx + (r - 14) * math.cos(ang), cy + (r - 14) * math.sin(ang)
            x2, y2 = cx + (r - 30) * math.cos(ang), cy + (r - 30) * math.sin(ang)
            in_red = self.red_from is not None and v >= self.red_from
            p.setPen(QPen(QColor(C_RED) if in_red else QColor(C_TEXT), 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            tx, ty = cx + (r - 46) * math.cos(ang), cy + (r - 46) * math.sin(ang)
            p.setPen(QColor(C_RED) if in_red else QColor(C_TEXT))
            p.setFont(QFont("Arial", max(10, int(side / 30))))
            p.drawText(QRectF(tx - 24, ty - 12, 48, 24),
                       Qt.AlignmentFlag.AlignCenter, f"{v:.0f}")

        # 指针
        frac = (self.value - self.vmin) / (self.vmax - self.vmin)
        ang = math.radians(135 + 270 * frac)
        in_red = self.red_from is not None and self.value >= self.red_from
        needle_color = QColor(C_RED) if in_red else QColor(C_ACCENT)
        p.setPen(QPen(needle_color, 3))
        p.drawLine(QPointF(cx, cy),
                   QPointF(cx + (r - 36) * math.cos(ang),
                           cy + (r - 36) * math.sin(ang)))
        p.setBrush(needle_color)
        p.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))

        # 标题与数值
        p.setPen(QColor(C_TEXT_DIM))
        p.setFont(QFont("Arial", 11))
        p.drawText(QRectF(cx - 70, cy + r - 50, 140, 22),
                   Qt.AlignmentFlag.AlignCenter, self.title)
        p.setPen(QColor(C_RED) if in_red else QColor(C_TEXT))
        p.setFont(QFont("Arial", 17, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 80, cy + r - 28, 160, 26),
                   Qt.AlignmentFlag.AlignCenter, f"{self.value:.0f} {self.unit}")
        p.end()


class TurnArrowWidget(QWidget):
    """导航转向箭头（自绘：左转/右转/直行/偏左/偏右/掉头/到达）"""

    def __init__(self) -> None:
        super().__init__()
        self.turn = 0
        self.setMinimumSize(200, 200)

    def set_turn(self, turn: int) -> None:
        self.turn = turn
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        cx, cy, s = self.width() / 2, self.height() / 2, side / 3
        p.setBrush(QColor(C_PANEL2))
        p.setPen(QPen(QColor(C_BORDER), 1))
        p.drawEllipse(QRectF(cx - s * 1.3, cy - s * 1.3,
                             s * 2.6, s * 2.6))
        if self.turn == 0:
            p.setPen(QColor(C_TEXT_DIM))
            p.setFont(QFont("Arial", 14))
            p.drawText(QRectF(cx - s, cy - 12, 2 * s, 24),
                       Qt.AlignmentFlag.AlignCenter, "导航未开启")
            p.end()
            return

        pen = QPen(QColor(C_ACCENT), int(s / 8), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        def arrow(dx: float, dy: float, head: float) -> None:
            """从 (0,0) 画箭头到 (dx,dy)，head 为箭头张角(弧度)"""
            ang = math.atan2(dy, dx)
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx, cy + dy))
            for sign in (1, -1):
                ha = ang + math.pi + sign * head
                p.drawLine(QPointF(cx + dx, cy + dy),
                           QPointF(cx + dx + s / 2.2 * math.cos(ha),
                                   cy + dy + s / 2.2 * math.sin(ha)))

        if self.turn == 1:      # 左转
            arrow(-s, 0, math.radians(140))
        elif self.turn == 2:    # 右转
            arrow(s, 0, math.radians(140))
        elif self.turn == 3:    # 直行
            arrow(0, -s, math.radians(140))
        elif self.turn == 4:    # 偏左
            arrow(-s * 0.7, -s * 0.7, math.radians(150))
        elif self.turn == 5:    # 偏右
            arrow(s * 0.7, -s * 0.7, math.radians(150))
        elif self.turn == 6:    # 掉头
            pen2 = QPen(QColor(C_ORANGE), int(s / 8))
            p.setPen(pen2)
            p.drawArc(QRectF(cx - s, cy - s, 2 * s, 2 * s), 45 * 16, 270 * 16)
            arrow(s, 0, math.radians(140))
        elif self.turn == 7:    # 到达
            p.setBrush(QColor(C_GREEN))
            p.setPen(QPen(QColor(C_GREEN), 2))
            p.drawEllipse(QRectF(cx - s * 0.5, cy - s * 0.5, s, s))
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Arial", int(s / 3), QFont.Weight.Bold))
            p.drawText(QRectF(cx - s * 0.5, cy - s * 0.5, s, s),
                       Qt.AlignmentFlag.AlignCenter, "到")
        p.end()


def info_card(title: str, value: str, color: str = C_TEXT) -> QFrame:
    """一张信息卡片（行程电脑/车辆信息用）"""
    card = QFrame()
    card.setStyleSheet(STYLE_PANEL)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    t = QLabel(title)
    t.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; border: none;")
    v = QLabel(value)
    v.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: bold; "
                    f"border: none;")
    lay.addWidget(t)
    lay.addWidget(v)
    return card


# ===========================================================================
# 七个界面
# ===========================================================================
class MainPage(QWidget):
    """主页：车速表 + 转速表 + 档位 + 小信息 + 警告灯"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 6)

        top = QHBoxLayout()
        self.speed_gauge = GaugeWidget("车速 km/h", "km/h", 0, 240, 12)
        self.rpm_gauge = GaugeWidget("转速 rpm", "rpm", 0, 8000, 8,
                                     red_from=6500)
        top.addWidget(self.speed_gauge)
        top.addWidget(self.rpm_gauge)

        # 中央信息列：档位 + 小信息
        mid = QHBoxLayout()
        self.gear_label = QLabel("P")
        self.gear_label.setStyleSheet(
            f"color: {C_ACCENT}; font-size: 52px; font-weight: bold;")
        self.gear_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gear_label.setFixedWidth(110)
        self.info_label = QLabel()
        self.info_label.setStyleSheet(
            f"color: {C_TEXT}; font-size: 15px; line-height: 1.7;")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        mid.addWidget(self.gear_label)
        mid.addWidget(self.info_label, 1)

        # 警告灯
        self.telltale_labels: dict[str, QLabel] = {}
        lights = QGridLayout()
        for idx, (name, (cn, color)) in enumerate(TELLTALE_UI.items()):
            lbl = QLabel(cn)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(30)
            lbl.setStyleSheet(
                f"color: {C_TEXT_DIM}; background-color: {C_PANEL}; "
                f"border: 1px solid {C_BORDER}; border-radius: 8px; "
                f"font-size: 11px;")
            self.telltale_labels[name] = lbl
            lights.addWidget(lbl, idx // 4, idx % 4)

        root.addLayout(top, 5)
        root.addLayout(mid, 1)
        root.addLayout(lights, 1)

    def refresh(self, snap: dict) -> None:
        self.speed_gauge.set_value(snap["speed_kmh"])
        self.rpm_gauge.set_value(snap["rpm"])
        self.gear_label.setText(snap["gear"])
        hvac = snap["hvac"]
        self.info_label.setText(
            f"总里程  {snap['odometer_km']} km\n"
            f"水温    {snap['coolant_c']} ℃\n"
            f"油量    {snap['fuel_pct']} %\n"
            f"电压    {snap['battery_v']} V\n"
            f"车外    {hvac['outdoor_temp_c']} ℃")
        lit = set(snap["telltales_lit"])
        self._current_lit = lit
        for name, lbl in self.telltale_labels.items():
            cn, color = TELLTALE_UI[name]
            if name in lit:
                lbl.setStyleSheet(
                    f"color: #ffffff; background-color: {color}; "
                    f"border: 1px solid {color}; border-radius: 8px; "
                    f"font-size: 11px; font-weight: bold;")
            else:
                lbl.setStyleSheet(
                    f"color: {C_TEXT_DIM}; background-color: {C_PANEL}; "
                    f"border: 1px solid {C_BORDER}; border-radius: 8px; "
                    f"font-size: 11px;")


class TripPage(QWidget):
    """行程电脑：小计里程/时间/均速/油耗/续航"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)

        title = QLabel("行程电脑 TRIP")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {
            "trip_km": info_card("小计里程", "-- km"),
            "trip_time": info_card("行驶时间", "--"),
            "avg_speed": info_card("平均车速", "-- km/h"),
            "avg_fuel": info_card("平均油耗", "-- L/100km"),
            "inst_fuel": info_card("瞬时油耗", "-- L/100km"),
            "range": info_card("续航里程", "-- km", C_GREEN),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid, 1)

        btn = QPushButton("重置小计（视觉演示）")
        btn.setStyleSheet(STYLE_BTN)
        btn.setFixedWidth(200)
        root.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self, snap: dict) -> None:
        trip = snap["trip"]
        m, s = divmod(int(trip["time_s"]), 60)
        h, m = divmod(m, 60)
        self.cards["trip_km"].findChildren(QLabel)[1].setText(
            f"{trip['km']:.2f} km")
        self.cards["trip_time"].findChildren(QLabel)[1].setText(
            f"{h:02d}:{m:02d}:{s:02d}")
        self.cards["avg_speed"].findChildren(QLabel)[1].setText(
            f"{trip['avg_speed_kmh']:.1f} km/h")
        self.cards["avg_fuel"].findChildren(QLabel)[1].setText(
            f"{trip['avg_fuel_lph']:.1f} L/100km")
        self.cards["inst_fuel"].findChildren(QLabel)[1].setText(
            f"{trip['inst_fuel_lph']:.1f} L/100km")
        self.cards["range"].findChildren(QLabel)[1].setText(
            f"{trip['range_km']:.0f} km")


class MediaPage(QWidget):
    """媒体：专辑封面占位 + 歌名/歌手 + 进度 + 播放控制（视觉）+ 音量"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)

        title = QLabel("媒体 MEDIA")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        # 专辑封面（色块 + 音符）
        cover = QFrame()
        cover.setFixedSize(220, 220)
        cover.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {C_ACCENT}, stop:1 #a371f7); border-radius: 16px;")
        cover_lay = QVBoxLayout(cover)
        note = QLabel("♪")
        note.setStyleSheet("color: rgba(255,255,255,0.9); font-size: 84px; "
                           "border: none;")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lay.addWidget(note)
        root.addWidget(cover, alignment=Qt.AlignmentFlag.AlignCenter)

        self.track_label = QLabel("--")
        self.track_label.setStyleSheet(f"color: {C_TEXT}; font-size: 24px; "
                                       f"font-weight: bold; border: none;")
        self.track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label = QLabel("--")
        self.artist_label.setStyleSheet(f"color: {C_TEXT_DIM}; "
                                        f"font-size: 15px; border: none;")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.track_label)
        root.addWidget(self.artist_label)
        root.addSpacing(14)

        # 进度条（按 0x400 的 MediaStatus + 本地计时模拟）
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(8)
        self.progress.setStyleSheet(
            f"QProgressBar {{background-color: {C_PANEL2}; border: none; "
            f"border-radius: 4px;}}"
            f"QProgressBar::chunk {{background-color: {C_ACCENT}; "
            f"border-radius: 4px;}}")
        root.addWidget(self.progress)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; "
                                      f"border: none;")
        root.addWidget(self.time_label)

        # 播放控制（仪表上通常只读，按钮做视觉演示）
        ctrl = QHBoxLayout()
        for txt in ("⏮ 上一首", "⏸ 暂停", "⏭ 下一首"):
            b = QPushButton(txt)
            b.setStyleSheet(STYLE_BTN)
            b.setFixedHeight(40)
            ctrl.addWidget(b)
        root.addLayout(ctrl)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("音量"))
        vol_row.itemAt(0).widget().setStyleSheet(
            f"color: {C_TEXT_DIM}; border: none;")
        self.vol_bar = QProgressBar()
        self.vol_bar.setRange(0, 100)
        self.vol_bar.setFixedHeight(6)
        self.vol_bar.setStyleSheet(
            f"QProgressBar {{background-color: {C_PANEL2}; border: none; "
            f"border-radius: 3px;}}"
            f"QProgressBar::chunk {{background-color: {C_GREEN}; "
            f"border-radius: 3px;}}")
        vol_row.addWidget(self.vol_bar, 1)
        root.addLayout(vol_row)

        self._elapsed = 0        # 本地播放计时（秒）
        self._duration = 0
        self._last_status = ""

    def refresh(self, snap: dict) -> None:
        media = snap["media"]
        self.track_label.setText(media["track"])
        self.artist_label.setText(f"{media['artist']}  ·  "
                                  f"#{media['track_index'] + 1}")
        self.vol_bar.setValue(media["volume"])
        self._duration = dict(
            (t[0], t[2]) for t in TRACKS).get(media["track"], 200)
        # 切歌时重置计时
        if media["status"] != self._last_status:
            if media["status"] in ("播放", "切歌"):
                self._elapsed = 0
            self._last_status = media["status"]
        if media["status"] == "播放":
            self._elapsed += 0.5   # 500ms 刷新周期近似
        self._elapsed = min(self._elapsed, self._duration)
        pct = int(self._elapsed / max(1, self._duration) * 100)
        self.progress.setValue(pct)
        self.time_label.setText(
            f"{int(self._elapsed) // 60}:{int(self._elapsed) % 60:02d} / "
            f"{self._duration // 60}:{self._duration % 60:02d}   "
            f"[{media['status']}]")


class NaviPage(QWidget):
    """导航：转向箭头 + 剩余距离 + 街道 + ETA"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("导航 NAVIGATION")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        self.arrow = TurnArrowWidget()
        root.addWidget(self.arrow, 5)

        self.dist_label = QLabel("-- m")
        self.dist_label.setStyleSheet(
            f"color: {C_ACCENT}; font-size: 44px; font-weight: bold; "
            f"border: none;")
        self.dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.dist_label, 1)

        self.street_label = QLabel("--")
        self.street_label.setStyleSheet(f"color: {C_TEXT}; font-size: 20px; "
                                        f"border: none;")
        self.street_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.street_label)

        self.eta_label = QLabel("预计 -- 到达")
        self.eta_label.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 14px; "
                                     f"border: none;")
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.eta_label)

    def refresh(self, snap: dict) -> None:
        navi = snap["navi"]
        self.arrow.set_turn(
            {v: k for k, v in TURN_NAMES.items() if v}.get(navi["turn"], 0))
        d = navi["distance_m"]
        if d >= 1000:
            self.dist_label.setText(f"{d / 1000:.1f} km")
        else:
            self.dist_label.setText(f"{d} m")
        self.street_label.setText(navi["street"])
        # ETA：按当前车速估（静止按 40km/h）
        speed = max(5, snap["speed_kmh"] or 40)
        eta_s = d / 1000 / speed * 3600
        m, s = divmod(int(eta_s), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            self.eta_label.setText(f"预计 {h} 小时 {m} 分到达")
        else:
            self.eta_label.setText(f"预计 {m} 分 {s:02d} 秒到达")


class PhonePage(QWidget):
    """电话：来电卡片 + 联系人列表"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("电话 PHONE")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        # 来电/通话卡片
        card = QFrame()
        card.setStyleSheet(STYLE_PANEL)
        card_lay = QVBoxLayout(card)
        self.call_status = QLabel("无通话")
        self.call_status.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 14px; "
                                       f"border: none;")
        self.caller_name = QLabel("--")
        self.caller_name.setStyleSheet(f"color: {C_TEXT}; font-size: 30px; "
                                       f"font-weight: bold; border: none;")
        self.caller_num = QLabel("")
        self.caller_num.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 15px; "
                                      f"border: none;")
        card_lay.addWidget(self.call_status)
        card_lay.addWidget(self.caller_name)
        card_lay.addWidget(self.caller_num)

        btns = QHBoxLayout()
        self.answer = QPushButton("📞 接听")
        self.hangup = QPushButton("📵 挂断")
        self.answer.setFixedHeight(42)
        self.hangup.setFixedHeight(42)
        btns.addWidget(self.answer)
        btns.addWidget(self.hangup)
        card_lay.addLayout(btns)
        root.addWidget(card)

        # 联系人列表
        list_title = QLabel("联系人")
        list_title.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px; "
                                 f"border: none;")
        root.addWidget(list_title)
        for name, num in CONTACTS:
            row = QFrame()
            row.setStyleSheet(STYLE_PANEL)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 6, 12, 6)
            n = QLabel(name)
            n.setStyleSheet(f"color: {C_TEXT}; font-size: 14px; border: none;")
            p = QLabel(num)
            p.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px; "
                            f"border: none;")
            rl.addWidget(n)
            rl.addStretch(1)
            rl.addWidget(p)
            root.addWidget(row)
        root.addStretch(1)

    def refresh(self, snap: dict) -> None:
        phone = snap["phone"]
        status = phone["call_status"]
        self.call_status.setText(status or "无通话")
        self.caller_name.setText(phone["caller"] if status else "--")
        active = bool(status)
        color = C_GREEN if status == "通话中" else (
            C_ORANGE if status == "来电" else C_TEXT_DIM)
        self.call_status.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold; "
            f"border: none;")
        self.answer.setEnabled(status == "来电")
        self.hangup.setEnabled(active)


class SettingsPage(QWidget):
    """设置（视觉演示为主）"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("设置 SETTINGS")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        items = [
            ("单位", "km/h · ℃"),
            ("语言", "中文（简体）"),
            ("主题", "深色（座舱）"),
            ("仪表亮度", "■■■■■□□□□□ 自动"),
            ("超速提醒", "120 km/h（视觉演示）"),
            ("关于", "robot_framework v1.0 · PySide6 仿真"),
        ]
        for k, v in items:
            row = QFrame()
            row.setStyleSheet(STYLE_PANEL)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 10, 14, 10)
            a = QLabel(k)
            a.setStyleSheet(f"color: {C_TEXT}; font-size: 14px; border: none;")
            b = QLabel(v)
            b.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px; "
                            f"border: none;")
            rl.addWidget(a)
            rl.addStretch(1)
            rl.addWidget(b)
            root.addWidget(row)
        root.addStretch(1)

    def refresh(self, snap: dict) -> None:
        """设置页为静态展示，无需刷新（占位保持接口一致）"""


class VehicleInfoPage(QWidget):
    """车辆信息：警告摘要 + 关键参数"""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("车辆信息 VEHICLE INFO")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 18px; "
                            f"font-weight: bold;")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {
            "coolant": info_card("发动机水温", "-- ℃"),
            "battery": info_card("蓄电池电压", "-- V"),
            "outdoor": info_card("车外温度", "-- ℃"),
            "hvac": info_card("空调设定", "-- ℃"),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid)

        warn_title = QLabel("警告摘要")
        warn_title.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px; "
                                 f"border: none;")
        root.addWidget(warn_title)
        self.warn_label = QLabel("无警告")
        self.warn_label.setStyleSheet(
            f"color: {C_GREEN}; font-size: 15px; border: none;")
        self.warn_label.setWordWrap(True)
        root.addWidget(self.warn_label)
        root.addStretch(1)

    def refresh(self, snap: dict) -> None:
        self.cards["coolant"].findChildren(QLabel)[1].setText(
            f"{snap['coolant_c']} ℃")
        self.cards["battery"].findChildren(QLabel)[1].setText(
            f"{snap['battery_v']} V")
        self.cards["outdoor"].findChildren(QLabel)[1].setText(
            f"{snap['hvac']['outdoor_temp_c']} ℃")
        self.cards["hvac"].findChildren(QLabel)[1].setText(
            f"{'开' if snap['hvac']['ac_on'] else '关'} · "
            f"{snap['hvac']['set_temp_c']} ℃")
        lit = snap["telltales_lit"]
        if not lit:
            self.warn_label.setText("无警告，系统正常")
            self.warn_label.setStyleSheet(
                f"color: {C_GREEN}; font-size: 15px; border: none;")
        else:
            names = [TELLTALE_UI[n][0] for n in lit]
            self.warn_label.setText("⚠ " + "、".join(names))
            self.warn_label.setStyleSheet(
                f"color: {C_RED}; font-size: 15px; border: none;")


# ===========================================================================
# 主窗口
# ===========================================================================
PAGES = ["主页", "行程", "媒体", "导航", "电话"]


class ClusterMainWindow(QMainWindow):
    def __init__(self, bus, display, db) -> None:
        super().__init__()
        self.display = display
        self.db = db
        self.setWindowTitle("座舱仪表盘模拟器 · robot_framework")
        self.setStyleSheet(f"background-color: {C_BG};")
        self.resize(1000, 780)

        self.stack = QStackedWidget()
        self.pages = [
            MainPage(), TripPage(), MediaPage(), NaviPage(),
            PhonePage(), SettingsPage(), VehicleInfoPage(),
        ]
        for p in self.pages:
            self.stack.addWidget(p)

        # ---- 顶部：标题 + 设置/车辆信息入口 ----
        header = QHBoxLayout()
        title = QLabel("VISTEON COCKPIT")
        title.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 13px; "
                            f"letter-spacing: 3px;")
        header.addWidget(title)
        header.addStretch(1)
        for idx, name in ((5, "⚙ 设置"), (6, "🚗 车辆信息")):
            b = QPushButton(name)
            b.setStyleSheet(STYLE_BTN)
            b.setFixedWidth(110)
            b.clicked.connect(lambda _=False, i=idx: self.goto(i))
            header.addWidget(b)

        # ---- 底部 tab ----
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self.tab_buttons: list[QPushButton] = []
        for idx, name in enumerate(PAGES):
            b = QPushButton(name)
            b.setStyleSheet(STYLE_TAB)
            b.clicked.connect(lambda _=False, i=idx: self.goto(i))
            self.tab_buttons.append(b)
            tabs.addWidget(b)
        self._highlight_tab(0)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 10)
        root.addLayout(header)
        root.addWidget(self.stack, 1)
        root.addLayout(tabs)
        self.setCentralWidget(central)

        # ---- CAN 接收线程 ----
        self.reader = CanReader(bus)
        self.reader.frame_received.connect(self.on_frame)
        self.reader.start()

        # ---- 转向灯闪烁定时器（真实车约 1.5Hz：亮 333ms / 灭 333ms）----
        self._blink_on = False
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink_tick)
        self.blink_timer.start(333)

        # ---- 刷新定时器（推进 trip 累计 + 刷新当前页）----
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_tick)
        self.refresh_timer.start(500)
        self._last_tick = time.monotonic()

    # ------------------------------------------------------------------
    def goto(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index < len(PAGES):
            self._highlight_tab(index)

    def _highlight_tab(self, active: int) -> None:
        for i, b in enumerate(self.tab_buttons):
            b.setStyleSheet(STYLE_TAB_ACTIVE if i == active else STYLE_TAB)

    # ------------------------------------------------------------------
    def on_frame(self, msg) -> None:
        self.display.update_from_can(self.db, msg)

    def _refresh_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.display.advance(dt)
        snap = self.display.snapshot()
        page = self.stack.currentWidget()
        page.refresh(snap)
        # GUI 模式也周期落盘显示状态 —— 和 headless 同一数据契约，
        # 联动可观测（外部读状态文件就知道仪表屏上显示了什么）
        try:
            _atomic_write_json(STATE_FILE, snap)
        except Exception as exc:  # noqa: BLE001
            log.warning("GUI 状态落盘失败: %s", exc)

    def _blink_tick(self) -> None:
        """转向灯闪烁（333ms 半周期）：灯应亮时按节拍亮/灭交替，
        灯不应亮时保持熄灭。真实车约 1.5Hz。"""
        self._blink_on = not self._blink_on
        main_page: MainPage = self.pages[0]
        lit = set(self.display.snapshot()["telltales_lit"])
        for name in ("TurnLeft", "TurnRight"):
            lbl = main_page.telltale_labels[name]
            _cn, color = TELLTALE_UI[name]
            # 只有"灯应亮"且处于亮的半周期才点亮；否则一律熄灭样式
            if name in lit and self._blink_on:
                lbl.setStyleSheet(
                    f"color: #ffffff; background-color: {color}; "
                    f"border: 1px solid {color}; border-radius: 8px; "
                    f"font-size: 11px; font-weight: bold;")
            else:
                lbl.setStyleSheet(
                    f"color: {C_TEXT_DIM}; background-color: {C_PANEL}; "
                    f"border: 1px solid {C_BORDER}; border-radius: 8px; "
                    f"font-size: 11px;")

    def closeEvent(self, event) -> None:
        self.reader.requestInterruption()
        self.reader.wait(1000)     # 等 CAN 接收线程真正退出，避免泄漏
        super().closeEvent(event)


class CanReader(QThread):
    """CAN 接收线程：收到报文发信号给 UI 线程"""

    frame_received = Signal(object)

    def __init__(self, bus) -> None:
        super().__init__()
        self._bus = bus

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                msg = self._bus.recv_frame(timeout=0.05)
            except (OSError, ValueError):
                break
            if msg is not None:
                self.frame_received.emit(msg)


# ===========================================================================
# 入口
# ===========================================================================
def run_cluster_gui(bus, display: ClusterDisplay, db) -> None:
    app = QApplication(sys.argv)

    boot = BootScreen()
    boot.resize(640, 400)
    boot.show()

    win = ClusterMainWindow(bus, display, db)
    boot.finished.connect(lambda: (boot.close(), win.show()))
    boot.start()

    exit_code = app.exec()
    sys.exit(exit_code)
