"""
cluster_canvas.py — 仪表 HMI 离屏渲染器（视觉自动化测试的"被测画面"）
=====================================================================

为什么需要它
------------
`dashboard.py --headless` 把"显示状态"变成 JSON，`DashboardLibrary` 断言 JSON 字段
（状态外置路线）。但真实 HMI 测试还有另一条路线：**截图 + 像素比对**——它验证的
不是"状态字段对不对"，而是"**画出来的像素对不对**"（布局错位、配色错误、警告灯
没画出来、指针卡住……这些 bug 状态字段里看不出来）。

本模块用 PySide6 的 QImage/QPainter **离屏渲染**一块 800x480 的确定性仪表画面，
作为视觉自动化模块（`libraries/VisionLibrary.py` + `tests/robot/09_hmi_vision.robot`）
的"被测对象"：

    显示状态 dict ──render_state()──► QImage ──save──► PNG ──比对/识别──► 断言

三条设计铁律（决定测试是否可复现）
----------------------------------
1. **确定性**：固定 800x480、固定配色、固定布局，不读时间、不用随机数、不依赖
   系统字体（画面里**一个文字都没有**，信息面板用色块/三角形表达）。
   同样的输入状态 → 同样的像素（同一平台逐位相同）。
2. **离屏**：模块导入前就设置 ``QT_QPA_PLATFORM=offscreen``，不弹窗口，
   Windows / Linux CI 都能跑（CI 的 ubuntu 需要 libgl1/libegl1）。
3. **可查询的几何常量**：警告灯区域 `TELLTALE_REGIONS`、车速表参数
   `SPEED_GAUGE` 都是公开常量，测试按名字查坐标，不写死魔法数字。

为什么不画文字
--------------
离屏环境（尤其 CI 容器）没有字体目录，`QFont` 会报警告并 fallback 到默认字体，
不同机器字形/字宽不同 → 基准图必然漂移。所以本项目刻意**只用矩形/圆/圆弧/指针/
三角形/色块**表达信息——这恰好也是车载 HUD/图形化仪表的主流画法。真实项目里要验
文字，应该用 OCR 或可访问性树（见 docs/25）。

金标准图（golden）怎么来
------------------------
`simulators/golden/` 里的 PNG **必须由本渲染器生成**，命令见文件末尾 main()：

    .\\.venv\\Scripts\\python.exe simulators/cluster_canvas.py \
        --preset default --out simulators/golden/cluster_default.png

状态 dict 字段（与 simulators/dashboard_state.json 一致，缺省自动补默认值）
---------------------------------------------------------------------------
    speed / speed_kmh      float   车速 km/h        默认 0
    rpm                    float   转速 rpm          默认 800
    coolant / coolant_c    float   水温 ℃            默认 90
    fuel / fuel_pct        float   油量 %            默认 60
    gear                   str/int 档位 P/R/N/D/S/L  默认 "P"
    telltales              dict    12 灯 {名字: 0/1}  默认全灭
    telltales_lit          list    点亮的灯名字列表   默认 []
    trip                   dict    {"km": ..., "time_s": ...}
    media                  dict    {"volume": 0~100, "track_index": ...}
    navi                   dict    {"turn": 0~7, "distance_m": ...}
    （hvac/phone 等其余字段原样保留，本渲染器不画，但 normalize 不会丢）

等价关系：``{"telltales_lit": ["CoolantTempHigh"]}`` 与
``{"telltales": {"CoolantTempHigh": 1}}`` 渲染结果完全一致。

颜色 / 区域常量一览（测试库与文档都引用这里）
----------------------------------------------
    背景 #0d1117  面板 #161b22 / #1c2330  边框 #2a3140
    指针 #ff9500（橙，仪表内唯一橙色，角度识别按颜色找它）
    进度弧 #58a6ff（蓝）  水温条 #d29922  油量条 #3fb950
    警告灯 点亮 #ff3b30（红） / 熄灭 #2a3140（灰）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 关键：必须在导入 PySide6 之前设置离屏平台插件，否则 Windows 会尝试开真窗口
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt               # noqa: E402
from PySide6.QtGui import (QColor, QImage, QPainter, QPen,   # noqa: E402
                           QPolygonF)
from PySide6.QtWidgets import QApplication                   # noqa: E402

# ===========================================================================
# 画布与配色常量
# ===========================================================================
CANVAS_WIDTH = 800
CANVAS_HEIGHT = 480

COLORS = {
    "bg": "#0d1117",            # 画布背景（对齐 cluster_gui.py 深色座舱主题）
    "panel": "#161b22",         # 面板底
    "panel2": "#1c2330",        # 面板亮
    "border": "#2a3140",        # 边框 / 未激活元素
    "text": "#e6edf3",          # 主色（刻度）
    "dim": "#8b949e",           # 次色（次要刻度）
    "accent": "#58a6ff",        # 主色（进度弧 / 转速条 / 档位块）
    "green": "#3fb950",         # 油量条 / 导航箭头
    "orange": "#d29922",        # 水温条
    "red": "#f85149",           # 语义红（本渲染器未用于装饰，避免干扰灯检测）
    "needle": "#ff9500",        # 指针专用橙：仪表内唯一该颜色 → 角度识别依据
    "telltale_lit": "#ff3b30",  # 警告灯点亮色（统一红，见下）
    "telltale_off": "#2a3140",  # 警告灯熄灭色（灰）
}

# 12 个警告灯（名字与 simulators/dashboard.py 的 TELLTALE_UI 完全一致）
TELLTALE_NAMES = [
    "MilWarning", "OilPressureLow", "CoolantTempHigh", "BatteryLow",
    "AbsFault", "AirbagFault", "SeatbeltWarning",
    "TurnLeft", "TurnRight", "HighBeam", "LowBeam", "ParkBrake",
]

# 警告灯网格：右侧面板底部 2 行 x 6 列（位置固定可查）
TELLTALE_GRID = {
    "x": 496, "y": 352,      # 面板左上角
    "cols": 6, "rows": 2,
    "cell_w": 48, "cell_h": 56,
    "radius": 11,            # 圆点半径（像素）
    "region_pad": 2,         # 区域检测框比圆点外扩 2px（吸收抗锯齿边）
}


def _build_telltale_regions() -> dict[str, tuple[int, int, int, int]]:
    """根据网格参数生成 名称 -> (x, y, w, h) 的检测区域（不含文字，坐标可查）。"""
    regions: dict[str, tuple[int, int, int, int]] = {}
    r = TELLTALE_GRID["radius"]
    pad = TELLTALE_GRID["region_pad"]
    side = 2 * (r + pad)
    for index, name in enumerate(TELLTALE_NAMES):
        row, col = divmod(index, TELLTALE_GRID["cols"])
        cx = TELLTALE_GRID["x"] + TELLTALE_GRID["cell_w"] // 2 \
            + col * TELLTALE_GRID["cell_w"]
        cy = TELLTALE_GRID["y"] + TELLTALE_GRID["cell_h"] // 2 \
            + row * TELLTALE_GRID["cell_h"]
        regions[name] = (cx - r - pad, cy - r - pad, side, side)
    return regions


# 名称 -> (x, y, w, h)：警告灯在画面上的检测区域（视觉断言按此裁图）
TELLTALE_REGIONS: dict[str, tuple[int, int, int, int]] = _build_telltale_regions()

# 车速表几何：0~240 km/h 映射到 135° 起、顺时针 270°（下方留缺口）
SPEED_GAUGE = {
    "center": (248.0, 240.0),
    "vmin": 0.0,
    "vmax": 240.0,
    "start_angle_deg": 135.0,   # 屏幕坐标系（y 向下）：0 = 正右，顺时针为正
    "sweep_deg": 270.0,
    "radius": 170.0,            # 进度弧半径
    "ring_width": 14.0,         # 进度弧环宽
    "needle_length": 140.0,     # 指针长度（从圆心算）
    "needle_width": 8.0,        # 指针粗细
    "hub_radius": 12.0,         # 中心轴盖半径
    "tick_count": 12,           # 主刻度分段数（13 个刻度点）
    "tick_inner_radius": 182.0,
    "tick_outer_radius": 196.0,
}

# 右侧三个信息面板（x, y, w, h）
PANEL_RECTS = {
    "bars": (496, 16, 288, 136),      # 转速 / 水温 / 油量 三条
    "gear": (496, 160, 288, 120),     # 档位块
    "info": (496, 288, 288, 56),      # 媒体音量块 + 导航箭头 + 小计里程条
    "telltales": (496, 352, 288, 112),
}

# 仪表内几何：左侧车速表面板
SPEED_PANEL_RECT = (16, 16, 464, 448)

# 档位布局：3 列 x 2 行，P R N / D S L（无文字，用"第几个块亮"表达档位）
GEAR_LAYOUT = {"order": ["P", "R", "N", "D", "S", "L"],
               "x": 568, "y": 174, "size": 40, "gap": 12, "cols": 3}

# 指针颜色匹配容差（角度识别时判定"这是指针像素"）
NEEDLE_COLOR_TOLERANCE = 24

# 默认显示状态：所有字段的缺省值（与 dashboard_state.json 的语义一致）
DEFAULT_STATE = {
    "speed": 0.0,
    "rpm": 800.0,
    "coolant": 90.0,
    "fuel": 60.0,
    "gear": "P",
    "telltales": {},
    "trip": {"km": 0.0, "time_s": 0.0},
    "media": {"volume": 0, "track_index": 0},
    "navi": {"turn": 0, "distance_m": 0},
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_state(state: dict | None) -> dict:
    """把任意来源的显示状态归一化成本渲染器需要的字段（缺省补默认值）。

    同时兼容两套字段命名：
      · dashboard_state.json 的快照命名：speed_kmh / coolant_c / fuel_pct /
        telltales_lit（点亮的灯名列表）
      · 测试里手写的教学命名：speed / coolant / fuel / telltales（{名字: 0/1}）
    两者渲染结果完全一致 —— 这就是"数据契约"的容错层。
    """
    raw: dict = dict(state or {})

    def pick(*keys, default=None):
        for key in keys:
            if key in raw and raw[key] is not None:
                return raw[key]
        return default

    gear_raw = pick("gear", default=DEFAULT_STATE["gear"])
    gear = _gear_name(gear_raw)

    # 警告灯：默认全灭；支持 telltales{名字:0/1} 与 telltales_lit[名字...] 两种写法
    telltales = {name: 0 for name in TELLTALE_NAMES}
    raw_telltales = raw.get("telltales") or {}
    if isinstance(raw_telltales, dict):
        for name in TELLTALE_NAMES:
            if name in raw_telltales:
                telltales[name] = 1 if raw_telltales[name] else 0
    for name in raw.get("telltales_lit") or []:
        if name in telltales:
            telltales[name] = 1

    trip_raw = raw.get("trip") or {}
    media_raw = raw.get("media") or {}
    navi_raw = raw.get("navi") or {}

    return {
        "speed": _to_float(pick("speed", "speed_kmh", default=0.0), 0.0),
        "rpm": _to_float(pick("rpm", default=DEFAULT_STATE["rpm"]), 800.0),
        "coolant": _to_float(pick("coolant", "coolant_c", "coolant_temp",
                                  default=DEFAULT_STATE["coolant"]), 90.0),
        "fuel": _to_float(pick("fuel", "fuel_pct", default=DEFAULT_STATE["fuel"]),
                          60.0),
        "gear": gear,
        "gear_index": GEAR_LAYOUT["order"].index(gear)
        if gear in GEAR_LAYOUT["order"] else 0,
        "telltales": telltales,
        "trip": {
            "km": _to_float(trip_raw.get("km", 0.0), 0.0),
            "time_s": _to_float(trip_raw.get("time_s", 0.0), 0.0),
        },
        "media": {
            "volume": int(_to_float(media_raw.get("volume", 0), 0)),
            "track_index": int(_to_float(media_raw.get("track_index", 0), 0)),
        },
        "navi": {
            "turn": int(_to_float(navi_raw.get("turn", 0), 0)),
            "distance_m": int(_to_float(navi_raw.get("distance_m", 0), 0)),
        },
    }


def _gear_name(gear) -> str:
    """档位统一成字符串：支持 'D' / 3（dashboard.py 的 0~5 编码）。"""
    if isinstance(gear, str):
        value = gear.strip().upper()
        if value in GEAR_LAYOUT["order"]:
            return value
        if value.isdigit():
            gear = int(value)
        else:
            return DEFAULT_STATE["gear"]
    try:
        index = int(gear)
    except (TypeError, ValueError):
        return DEFAULT_STATE["gear"]
    if 0 <= index < len(GEAR_LAYOUT["order"]):
        return GEAR_LAYOUT["order"][index]
    return DEFAULT_STATE["gear"]


def load_state(path: str | Path) -> dict:
    """读取 dashboard_state.json 这类状态文件（原子写场景下带少量重试）。"""
    state_path = Path(path)
    if not state_path.exists():
        raise FileNotFoundError(f"状态文件不存在: {state_path}")
    last_error: Exception | None = None
    for _ in range(5):
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:   # 撞上半写文件时重试
            last_error = exc
            time.sleep(0.02)
    raise ValueError(f"状态文件读取失败（可能正被写入）: {state_path}: {last_error}")


# ===========================================================================
# Qt 应用单例
# ===========================================================================
_QT_APP: QApplication | None = None


def ensure_qt_app() -> QApplication:
    """懒创建 QApplication（离屏）。

    为什么必须懒创建：Robot Framework 进程里只在第一次渲染/读图时初始化 Qt；
    重复调用复用单例，避免 "QApplication instance already exists" 崩溃。
    offscreen 平台的 QApplication 不创建窗口、不依赖显示服务器。
    """
    global _QT_APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setApplicationName("cluster_canvas")
    _QT_APP = app          # type: ignore[assignment]  # instance() 返回 QCoreApplication
    return app            # type: ignore[return-value]


# ===========================================================================
# 绘制小工具
# ===========================================================================
def _q(color: str) -> QColor:
    return QColor(color)


def _rect(rect: tuple[int, int, int, int]) -> QRectF:
    x, y, w, h = rect
    return QRectF(float(x), float(y), float(w), float(h))


def _draw_panel(painter: QPainter, rect: tuple[int, int, int, int],
                radius: float = 14.0) -> None:
    painter.setPen(QPen(_q(COLORS["border"]), 2))
    painter.setBrush(_q(COLORS["panel"]))
    painter.drawRoundedRect(_rect(rect), radius, radius)


def _screen_dir(angle_deg: float) -> tuple[float, float]:
    """屏幕坐标系方向向量（y 向下，角度顺时针增长）。"""
    rad = math.radians(angle_deg)
    return math.cos(rad), math.sin(rad)


def _gauge_screen_angle(speed: float) -> float:
    """车速 → 指针的屏幕角度（0 刻度 135°，满量程 405°=45°）。"""
    gauge = SPEED_GAUGE
    frac = _clamp((speed - gauge["vmin"]) / (gauge["vmax"] - gauge["vmin"]),
                  0.0, 1.0)
    return gauge["start_angle_deg"] + gauge["sweep_deg"] * frac


# ===========================================================================
# 画面元素：车速表（圆弧 + 刻度 + 指针）
# ===========================================================================
def _draw_speed_gauge(painter: QPainter, state: dict) -> None:
    gauge = SPEED_GAUGE
    cx, cy = gauge["center"]
    radius = gauge["radius"]

    # 表盘外圈（细边框）与表盘底
    painter.setPen(QPen(_q(COLORS["border"]), 2))
    painter.setBrush(_q(COLORS["panel2"]))
    painter.drawEllipse(QPointF(cx, cy), radius + 34, radius + 34)

    # 进度弧底槽：135° 起、顺时针 270°
    # Qt 的角度是"0°=3 点钟方向、逆时针为正"，与屏幕 y 向下坐标系相反，
    # 所以屏幕角度 θ 对应 Qt 角度 -θ；顺时针扫 = 负 span。
    arc_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
    start_qt = -gauge["start_angle_deg"]
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_q(COLORS["border"]), gauge["ring_width"],
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    painter.drawArc(arc_rect, int(round(start_qt * 16)),
                    int(round(-gauge["sweep_deg"] * 16)))

    # 当前车速的进度弧（蓝）
    frac = _clamp((state["speed"] - gauge["vmin"])
                  / (gauge["vmax"] - gauge["vmin"]), 0.0, 1.0)
    if frac > 0.0:
        painter.setPen(QPen(_q(COLORS["accent"]), gauge["ring_width"],
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawArc(arc_rect, int(round(start_qt * 16)),
                        int(round(-gauge["sweep_deg"] * frac * 16)))

    # 刻度线：0,20,...,240 共 13 个点；偶数为主刻度（粗、亮）
    tick_count = gauge["tick_count"]
    for i in range(tick_count + 1):
        angle = gauge["start_angle_deg"] + gauge["sweep_deg"] * i / tick_count
        dx, dy = _screen_dir(angle)
        major = (i % 2 == 0)
        pen = QPen(_q(COLORS["text"] if major else COLORS["dim"]),
                   3.0 if major else 1.5)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(cx + dx * gauge["tick_inner_radius"],
                    cy + dy * gauge["tick_inner_radius"]),
            QPointF(cx + dx * gauge["tick_outer_radius"],
                    cy + dy * gauge["tick_outer_radius"]))

    # 指针（橙）：角度由车速决定，长度/粗细固定
    angle = _gauge_screen_angle(state["speed"])
    dx, dy = _screen_dir(angle)
    needle_pen = QPen(_q(COLORS["needle"]), gauge["needle_width"],
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(needle_pen)
    painter.drawLine(QPointF(cx, cy),
                     QPointF(cx + dx * gauge["needle_length"],
                             cy + dy * gauge["needle_length"]))
    # 中心轴盖（同指针色；角度识别时会按半径过滤掉它）
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_q(COLORS["needle"]))
    painter.drawEllipse(QPointF(cx, cy), gauge["hub_radius"],
                        gauge["hub_radius"])


# ===========================================================================
# 画面元素：转速 / 水温 / 油量三条
# ===========================================================================
def _draw_bars(painter: QPainter, state: dict) -> None:
    x, y, w, _h = PANEL_RECTS["bars"]
    bar_x = x + 44
    bar_w = w - 64
    bar_h = 14
    rows = [
        # (纵向中心, 占比, 颜色)
        (y + 40, _clamp(state["rpm"] / 8000.0, 0.0, 1.0), COLORS["accent"]),
        (y + 84, _clamp((state["coolant"] - 40.0) / 90.0, 0.0, 1.0),
         COLORS["orange"]),
        (y + 112, _clamp(state["fuel"] / 100.0, 0.0, 1.0), COLORS["green"]),
    ]
    for row_y, frac, color in rows:
        top = row_y - bar_h / 2
        # 左侧方形"图标"（无文字，用色块占位）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_q(color))
        painter.drawRoundedRect(QRectF(x + 20, top, 14, bar_h), 3, 3)
        # 底槽
        painter.setBrush(_q(COLORS["border"]))
        painter.drawRoundedRect(QRectF(bar_x, top, bar_w, bar_h), 4, 4)
        # 填充
        if frac > 0.0:
            painter.setBrush(_q(color))
            painter.drawRoundedRect(
                QRectF(bar_x, top, max(bar_w * frac, 6.0), bar_h), 4, 4)


# ===========================================================================
# 画面元素：档位块（3x2，亮的那块表示档位；无文字）
# ===========================================================================
def _draw_gear_block(painter: QPainter, state: dict) -> None:
    layout = GEAR_LAYOUT
    size = layout["size"]
    gap = layout["gap"]
    active = state["gear_index"]
    for index, _name in enumerate(layout["order"]):
        row, col = divmod(index, layout["cols"])
        bx = layout["x"] + col * (size + gap)
        by = layout["y"] + row * (size + gap)
        rect = QRectF(bx, by, size, size)
        if index == active:
            painter.setPen(QPen(_q(COLORS["accent"]), 2))
            painter.setBrush(_q(COLORS["accent"]))
        else:
            painter.setPen(QPen(_q(COLORS["border"]), 2))
            painter.setBrush(_q(COLORS["panel2"]))
        painter.drawRoundedRect(rect, 8, 8)


# ===========================================================================
# 画面元素：信息面板（媒体音量块 + 导航箭头 + 小计里程条）
# ===========================================================================
def _draw_info_panel(painter: QPainter, state: dict) -> None:
    x, y, w, _h = PANEL_RECTS["info"]

    # 媒体音量：8 个方块，亮起的块数 = 音量比例
    blocks = 8
    lit = int(_clamp(state["media"]["volume"], 0, 100) * blocks // 100)
    block_w = 20
    block_h = 20
    start_x = x + 16
    for i in range(blocks):
        bx = start_x + i * (block_w + 6)
        rect = QRectF(bx, y + 8, block_w, block_h)
        if i < lit:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_q(COLORS["accent"]))
        else:
            painter.setPen(QPen(_q(COLORS["border"]), 1))
            painter.setBrush(_q(COLORS["panel2"]))
        painter.drawRoundedRect(rect, 3, 3)

    # 导航转向箭头（三角形，方向由 navi.turn 决定；无文字）
    _draw_navi_arrow(painter, x + w - 34, y + 18, 12.0,
                     state["navi"]["turn"])

    # 小计里程条：50km 满格
    trip_frac = _clamp(state["trip"]["km"] / 50.0, 0.0, 1.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_q(COLORS["border"]))
    painter.drawRoundedRect(QRectF(start_x, y + 40, w - 52, 6), 3, 3)
    if trip_frac > 0.0:
        painter.setBrush(_q(COLORS["green"]))
        painter.drawRoundedRect(
            QRectF(start_x, y + 40, max((w - 52) * trip_frac, 4.0), 6), 3, 3)


def _draw_navi_arrow(painter: QPainter, cx: float, cy: float, size: float,
                     turn: int) -> None:
    """画一个三角形表示导航方向（0 无 / 1 左 / 2 右 / 3 直行 / 4 偏左 /
    5 偏右 / 6 掉头 / 7 到达），不同方向用旋转实现。"""
    painter.save()
    if turn == 0:
        painter.setPen(QPen(_q(COLORS["border"]), 2))
        painter.setBrush(_q(COLORS["panel2"]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx, cy - size), QPointF(cx + size, cy),
            QPointF(cx, cy + size), QPointF(cx - size, cy)]))
        painter.restore()
        return
    if turn == 7:                      # 到达：绿色圆角方块
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_q(COLORS["green"]))
        painter.drawRoundedRect(
            QRectF(cx - size, cy - size, size * 2, size * 2), 5, 5)
        painter.restore()
        return
    rotation = {1: -90, 2: 90, 3: 0, 4: -45, 5: 45, 6: 180}.get(turn, 0)
    painter.translate(cx, cy)
    painter.rotate(rotation)
    painter.setPen(QPen(_q(COLORS["green"]), 1))
    painter.setBrush(_q(COLORS["green"]))
    painter.drawPolygon(QPolygonF([
        QPointF(0, -size),
        QPointF(size * 0.8, size * 0.75),
        QPointF(-size * 0.8, size * 0.75)]))
    painter.restore()


# ===========================================================================
# 画面元素：12 个警告灯圆点（位置固定、点亮红/熄灭灰）
# ===========================================================================
def _draw_telltales(painter: QPainter, state: dict) -> None:
    grid = TELLTALE_GRID
    radius = grid["radius"]
    for name, (rx, ry, rw, rh) in TELLTALE_REGIONS.items():
        # 区域 = 圆点外接方框；圆心由区域反推（区域是圆心对称的）
        cx = rx + rw / 2.0
        cy = ry + rh / 2.0
        lit = bool(state["telltales"].get(name, 0))
        painter.setPen(QPen(_q(COLORS["border"]), 2))
        painter.setBrush(_q(COLORS["telltale_lit"] if lit
                            else COLORS["telltale_off"]))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)


# ===========================================================================
# 对外 API
# ===========================================================================
def render_state(state: dict | None = None) -> QImage:
    """把"显示状态 dict"渲染成 800x480 的 QImage（确定性、离屏）。

    参数
        state: 显示状态 dict（字段见模块 docstring），也接受 dashboard_state.json
               的路径（str/Path）或 None（全部用默认值）。

    返回
        QImage（Format_RGB32，800x480）。同一输入在同一平台逐位一致。
    """
    ensure_qt_app()
    if isinstance(state, (str, Path)):
        state = load_state(state)
    normalized = normalize_state(state)

    image = QImage(CANVAS_WIDTH, CANVAS_HEIGHT, QImage.Format.Format_RGB32)
    image.fill(_q(COLORS["bg"]))

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 左侧：车速表面板
        _draw_panel(painter, SPEED_PANEL_RECT, radius=18)
        _draw_speed_gauge(painter, normalized)

        # 右侧：三个信息面板 + 警告灯面板
        _draw_panel(painter, PANEL_RECTS["bars"], radius=12)
        _draw_panel(painter, PANEL_RECTS["gear"], radius=12)
        _draw_panel(painter, PANEL_RECTS["info"], radius=12)
        _draw_panel(painter, PANEL_RECTS["telltales"], radius=12)

        _draw_bars(painter, normalized)
        _draw_gear_block(painter, normalized)
        _draw_info_panel(painter, normalized)
        _draw_telltales(painter, normalized)
    finally:
        painter.end()

    # 固定设备像素比：防止 HighDPI 机器上被 Qt 放大
    image.setDevicePixelRatio(1.0)
    return image


def render_state_to_png(state: dict | None, path: str | Path) -> Path:
    """渲染状态并保存成 PNG（自动创建父目录），返回实际写入路径。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = render_state(state)
    if not image.save(str(output), "PNG"):
        raise OSError(f"PNG 保存失败: {output}")
    return output


def measure_needle_angle(image: QImage, gauge: str = "speed") -> float:
    """从截图中识别车速指针角度，返回"相对 0 刻度扫过的角度"（0 ~ 270°）。

    算法（纯像素、无机器学习）：
      1. 在表盘区域内找"指针橙"像素（颜色容差 NEEDLE_COLOR_TOLERANCE）；
      2. 过滤掉中心轴盖（半径 < 40px 的像素）——否则轴盖会把方向拉向 0；
      3. 取离圆心最远的一圈像素（指针尖端，半径差 6px 以内）做**向量平均**，
         得到指针方向，抗单像素噪声；
      4. 屏幕角度 → 仪表扫过角度：sweep = (screen_deg - 135) % 360。

    为什么要这么绕：如果按"最远点"单点算，抗锯齿会让尖端出现一两个离群
    像素，角度抖动可达 2~3°；取最远一圈做平均后实测误差 < 0.5°。
    """
    ensure_qt_app()
    if gauge != "speed":
        raise ValueError(f"不支持的仪表: {gauge}（当前只实现了 speed 车速表）")
    if image.isNull():
        raise ValueError("传入的 QImage 为空，无法识别指针角度")

    rgb = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = rgb.width(), rgb.height()
    raw = bytes(rgb.constBits())
    bytes_per_line = rgb.bytesPerLine()

    target = _q(COLORS["needle"])
    tol = NEEDLE_COLOR_TOLERANCE
    cx, cy = SPEED_GAUGE["center"]
    reach = int(SPEED_GAUGE["tick_outer_radius"]) + 10
    x0 = max(0, int(cx) - reach)
    x1 = min(width, int(cx) + reach + 1)
    y0 = max(0, int(cy) - reach)
    y1 = min(height, int(cy) + reach + 1)

    candidates: list[tuple[float, float, float]] = []     # (dx, dy, radius²)
    for py in range(y0, y1):
        row = py * bytes_per_line
        for px in range(x0, x1):
            index = row + px * 4
            blue = raw[index]
            green = raw[index + 1]
            red = raw[index + 2]
            if (abs(red - target.red()) <= tol
                    and abs(green - target.green()) <= tol
                    and abs(blue - target.blue()) <= tol):
                dx = px - cx
                dy = py - cy
                radius_sq = dx * dx + dy * dy
                if radius_sq >= 40.0 * 40.0:      # 排除中心轴盖
                    candidates.append((dx, dy, radius_sq))

    if not candidates:
        raise ValueError(
            "在画面中找不到指针（#ff9500 橙色像素）—— "
            "请确认截图确实由 cluster_canvas.render_state() 生成")

    max_radius_sq = max(item[2] for item in candidates)
    # 尖端带：离最远点半径 6px 以内（指针 RoundCap 末端是一小段圆弧）
    band_sq = max(max_radius_sq - (2 * math.sqrt(max_radius_sq) * 6 - 36), 0.0)
    tip_points = [(dx, dy) for dx, dy, radius_sq in candidates
                  if radius_sq >= band_sq]
    sum_dx = sum(dx for dx, _dy in tip_points)
    sum_dy = sum(dy for _dx, dy in tip_points)
    screen_deg = math.degrees(math.atan2(sum_dy, sum_dx))
    sweep = (screen_deg - SPEED_GAUGE["start_angle_deg"]) % 360.0
    return round(sweep, 2)


# ===========================================================================
# 预设状态：生成 golden 基准图 / 命令行演示用
# ===========================================================================
PRESET_STATES: dict[str, dict] = {
    # 默认状态：0 km/h、灯全灭、P 档 —— 视觉基准图的"静止基线"
    "default": {},
    # 60 km/h 行驶 + 水温过高报警（车速 60、水温 121℃、D 档、续航 45%）
    "60kmh_alarm": {
        "speed": 60.0,
        "rpm": 4800.0,
        "coolant": 121.0,
        "fuel": 45.0,
        "gear": "D",
        "telltales": {"CoolantTempHigh": 1},
        "trip": {"km": 12.3, "time_s": 600.0},
        "media": {"volume": 45, "track_index": 1},
        "navi": {"turn": 3, "distance_m": 800},
    },
    # 全灯点亮（灯光测试 / 上电自检的典型画面）
    "all_telltales": {
        "speed": 120.0,
        "rpm": 3200.0,
        "coolant": 105.0,
        "fuel": 72.0,
        "gear": "S",
        "telltales": {name: 1 for name in TELLTALE_NAMES},
        "trip": {"km": 30.0, "time_s": 1800.0},
        "media": {"volume": 80, "track_index": 2},
        "navi": {"turn": 1, "distance_m": 300},
    },
}


def main(argv: list[str] | None = None) -> int:
    """命令行入口：生成基准图或渲染任意状态文件。

    用法（项目根目录）：
        .\\.venv\\Scripts\\python.exe simulators/cluster_canvas.py \\
            --preset default --out simulators/golden/cluster_default.png
        .\\.venv\\Scripts\\python.exe simulators/cluster_canvas.py \\
            --state simulators/dashboard_state.json --out results/vision/live.png
    """
    parser = argparse.ArgumentParser(description="仪表 HMI 离屏渲染器")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--preset", choices=sorted(PRESET_STATES),
                        default="default", help="使用预设状态（默认 default）")
    source.add_argument("--state", help="状态 JSON 文件路径（如 dashboard_state.json）")
    parser.add_argument("--out", required=True, help="输出 PNG 路径")
    args = parser.parse_args(argv)

    state: dict | None
    if args.state:
        state = load_state(args.state)
    else:
        state = PRESET_STATES[args.preset]
    output = render_state_to_png(state, args.out)
    angle = measure_needle_angle(render_state(state))
    print(f"[cluster_canvas] 已渲染 {CANVAS_WIDTH}x{CANVAS_HEIGHT} -> {output}")
    print(f"[cluster_canvas] 指针角度识别值: {angle:.2f}° "
          f"（车速 {normalize_state(state)['speed']:.1f} km/h）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
