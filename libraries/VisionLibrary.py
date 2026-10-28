"""
VisionLibrary.py — HMI 视觉自动化测试关键字库（截图基准比对 / 指针角度 / 警告灯）
================================================================================

库的定位
--------
`DashboardLibrary` 走"状态外置"路线：断言 dashboard_state.json 里的字段。
本库走**视觉路线**：把 HMI 画面渲染成 PNG，然后用**像素**回答三个问题：

    1. 画面对不对？        —— 整图 / 区域与黄金图（golden）比对
    2. 指针指哪？          —— 按指针颜色识别角度（0~270°）
    3. 警告灯亮没亮？      —— 按区域红色占比判定 lit / off

被测画面由 `simulators/cluster_canvas.py` 离屏渲染（确定性、无字体、不弹窗）。
所有关键字**只依赖 PNG 文件**，不关心截图从哪来 —— 所以真实项目里把
"渲染器截图"换成"真屏截图 / adb screencap / Appium screenshot / 视频抽帧"，
断言层一行不用改（见 docs/25 章"真实项目怎么接"）。

纯 PySide6 实现
---------------
图像读取/比较/差异图全部用 QImage 的原生像素缓冲（constBits + 切片比较），
**不依赖 Pillow / numpy / OpenCV** —— 少一个依赖就少一个 CI 环境坑。

关键常量（本文件集中管理，文档同步）
------------------------------------
    DEFAULT_TOLERANCE            整图/区域比对默认容差 = 2%（跨平台抗锯齿差异余量）
    PIXEL_COLOR_TOLERANCE        单像素通道差异 ≤ 32 视为"同色"（抗锯齿边缘）
    TELLTALE_LIT_MIN_RATIO       红色占比 ≥ 25% 判定点亮
    TELLTALE_OFF_MAX_RATIO       红色占比 ≤ 5%  判定熄灭
    NEEDLE_ANGLE_TOLERANCE_DEG   指针角度默认容差 = 2°
警告灯的**区域坐标**来自 `cluster_canvas.TELLTALE_REGIONS`（渲染器与断言库同一份
几何常量，避免"库和画面各写一套坐标"的经典烂账）。

用法示例
--------
    *** Settings ***
    Library    ../../libraries/VisionLibrary.py

    *** Test Cases ***
    默认状态整图应与黄金图一致
        ${png}=    Render Cluster State    ${{ {}}}    ${OUT}/default.png
        Compare With Golden    ${png}    cluster_default.png

    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/60.png
    ${angle}=    Get Needle Angle    ${OUT}/60.png
    Needle Angle Should Be Near    ${OUT}/60.png    67.5    tolerance=2.0
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

# 把 simulators/ 加进 sys.path，直接复用渲染器（不复制一份几何/配色常量）
ROOT = Path(__file__).resolve().parent.parent
SIMULATORS_DIR = ROOT / "simulators"
if str(SIMULATORS_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATORS_DIR))

import cluster_canvas as canvas                                    # noqa: E402
from PySide6.QtCore import QRect                                   # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen           # noqa: E402

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
DEFAULT_GOLDEN_DIR = ROOT / "simulators" / "golden"
DEFAULT_OUTPUT_DIR = ROOT / "results" / "vision"
DEFAULT_DIFF_DIR = DEFAULT_OUTPUT_DIR / "diffs"
LIVE_STATE_FILE = ROOT / "simulators" / "dashboard_state.json"

# ---------------------------------------------------------------------------
# 比对/识别阈值（集中成常量，改这里即可调灵敏度；文档逐条解释）
# ---------------------------------------------------------------------------
#: 整图/区域差异比例容差。2% 看起来宽，但这是为了覆盖"同一份 Qt 代码在
#: Windows / Linux 上抗锯齿舍入的亚像素差异"；真正的 GUI 缺陷（指针角度错、
#: 灯不亮、布局整体偏移）差异远大于 2%（实测：车速 240 vs 0 差异 3.6%）。
DEFAULT_TOLERANCE = 0.02
#: 单像素比较时每个通道允许的差（0~255）。抗锯齿边缘相邻像素混合比例略有不同，
#: 用 32 把这类"视觉上无差异"的像素判为相同，避免误报。
PIXEL_COLOR_TOLERANCE = 32
#: 判定警告灯"点亮"的最低红色占比（红灯圆点约占检测区域的 48%~56%）。
TELLTALE_LIT_MIN_RATIO = 0.25
#: 判定警告灯"熄灭"的最高红色占比（灰灯区域红色占比为 0）。
TELLTALE_OFF_MAX_RATIO = 0.05
#: "红色像素"判定：亮度下限 + R 通道相对 G/B 的最小差值。
TELLTALE_RED_MIN_BRIGHTNESS = 120
TELLTALE_RED_MIN_GAP = 60
#: 指针角度断言默认容差（度）。
NEEDLE_ANGLE_TOLERANCE_DEG = 2.0
#: 差异图高亮色：纯红（与警告灯的红 #ff3b30 区分开，便于非空白检测）。
DIFF_HIGHLIGHT = QColor(255, 0, 0)


class VisionLibrary:
    """HMI 视觉自动化关键字集合（截图基准比对 + 图形识别）。

    设计原则：
      · 每个关键字都**自己加载图片**，不在内存里缓存 QImage —— 测试用例之间
        无状态泄漏，顺序无关；
      · 失败信息必须能"直接定位"：差异比例、像素数、区域坐标、差异图路径全带上；
      · 比对失败自动保存差异图（红框高亮），失败现场不丢。
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, golden_dir: str = str(DEFAULT_GOLDEN_DIR),
                 output_dir: str = str(DEFAULT_OUTPUT_DIR)) -> None:
        self._golden_dir = Path(golden_dir)
        self._output_dir = Path(output_dir)
        self._diff_dir = self._output_dir / "diffs"
        console(f"[VisionLibrary] 黄金图目录: {self._golden_dir}")
        console(f"[VisionLibrary] 运行产物目录: {self._output_dir}")

    # ==================================================================
    # 内部工具
    # ==================================================================
    @staticmethod
    def _ensure_parent(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    def _as_path(self, value, what: str = "文件") -> Path:
        """把 RF 传进来的字符串路径转成 Path（统一 resolve 便于报错定位）。"""
        path = Path(str(value))
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path

    def _load_image(self, image_path, what: str = "截图") -> QImage:
        """加载图片并统一成 Format_RGB32（比对/取色都基于这个格式）。

        注意 PySide6 生命周期坑：`bytes(img.constBits())` 必须在 img 变量仍存活时
        调用，不能写成 `bytes(QImage(path).constBits())` —— 临时对象被回收后
        内存视图悬空，会直接段错误（本项目踩过）。
        """
        path = self._as_path(image_path)
        if not path.exists():
            raise AssertionError(
                f"{what}不存在: {path} —— 先渲染截图（Render Cluster State / "
                f"Capture Cluster Screenshot）或检查路径")
        source = QImage(str(path))
        if source.isNull():
            raise AssertionError(
                f"{what}无法读取（文件损坏或不是图片）: {path}")
        image = source.convertToFormat(QImage.Format.Format_RGB32)
        if image.isNull():
            raise AssertionError(f"{what}格式转换失败: {path}")
        return image

    def _resolve_golden(self, golden) -> Path:
        """黄金图参数支持三种写法：绝对路径 / 相对项目根路径 / 纯文件名。"""
        raw = str(golden)
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        for base in (ROOT, self._golden_dir):
            path = base / raw
            if path.exists():
                return path
        searched = f"{ROOT / raw} / {self._golden_dir / raw}"
        raise AssertionError(
            f"基准图不存在: {raw}（已查找: {searched}）—— "
            "首次建立基准请用 Save Golden / Update Golden 生成并提交到 git")

    @staticmethod
    def _pixels(image: QImage) -> tuple[bytes, int]:
        """取 QImage 的原始像素字节与行跨距（Format_RGB32：BGRA，每像素 4 字节）。"""
        data = bytes(image.constBits())
        return data, image.bytesPerLine()

    @staticmethod
    def _diff_indices(data_a: bytes, data_b: bytes, width: int, height: int,
                      bytes_per_line: int,
                      color_tolerance: int) -> list[int]:
        """逐像素比较，返回"有显著差异"的像素线性下标（y*width + x）。

        先做整行 bytes 相等快速通道：绝大多数像素完全一致的场景（黄金图自检、
        跨平台渲染）几乎零成本。单像素判定 = 任一通道差 > color_tolerance，
        这样抗锯齿边缘的细微混合差异不会被误判成缺陷。
        """
        if data_a == data_b:
            return []
        stride = width * 4
        indices: list[int] = []
        for y in range(height):
            row = y * bytes_per_line
            row_a = data_a[row:row + stride]
            if row_a == data_b[row:row + stride]:
                continue
            base = y * width
            for x in range(width):
                i = row + x * 4
                if (abs(data_a[i] - data_b[i]) > color_tolerance
                        or abs(data_a[i + 1] - data_b[i + 1]) > color_tolerance
                        or abs(data_a[i + 2] - data_b[i + 2]) > color_tolerance):
                    indices.append(base + x)
        return indices

    def _compare_images(self, image_a: QImage, image_b: QImage,
                        color_tolerance: int) -> tuple[list[int], int]:
        """比较两张同尺寸图，返回 (差异像素下标列表, 总像素数)。"""
        if image_a.size() != image_b.size():
            raise AssertionError(
                f"两张图尺寸不一致，无法比对: {image_a.width()}x{image_a.height()} "
                f"vs {image_b.width()}x{image_b.height()}")
        data_a, bpl_a = self._pixels(image_a)
        data_b, bpl_b = self._pixels(image_b)
        if bpl_a != bpl_b:
            raise AssertionError(f"两张图行跨距不一致: {bpl_a} vs {bpl_b}")
        indices = self._diff_indices(data_a, data_b, image_a.width(),
                                     image_a.height(), bpl_a, color_tolerance)
        return indices, image_a.width() * image_a.height()

    def _write_diff_image(self, actual: QImage, indexes: list[int],
                          output_path: Path) -> Path:
        """生成差异图：底图 = 实际截图，差异像素覆盖纯红 + 红框圈出差异区域。"""
        width, height = actual.width(), actual.height()
        data, bytes_per_line = self._pixels(actual)
        buffer = bytearray(data)
        stride = width * 4
        min_x, min_y = width, height
        max_x = max_y = -1
        for index in indexes:
            y, x = divmod(index, width)
            offset = y * bytes_per_line + x * 4
            # Format_RGB32 小端内存序 = B, G, R, A → 纯红 = (0, 0, 255, 255)
            buffer[offset:offset + 4] = b"\x00\x00\xff\xff"
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)

        raw = bytes(buffer)                       # 必须先持引用，防 QImage 悬空
        diff_image = QImage(raw, width, height, bytes_per_line,
                            QImage.Format.Format_RGB32).copy()
        if indexes:
            painter = QPainter(diff_image)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QPen(DIFF_HIGHLIGHT, 3))
                painter.setBrush(QColor(0, 0, 0, 0))
                painter.drawRect(QRect(min_x - 2, min_y - 2,
                                       max_x - min_x + 5, max_y - min_y + 5))
            finally:
                painter.end()
        self._ensure_parent(output_path)
        if not diff_image.save(str(output_path), "PNG"):
            raise AssertionError(f"差异图保存失败: {output_path}")
        return output_path

    def _red_ratio(self, image: QImage, region: tuple[int, int, int, int]) -> float:
        """统计区域内"红色像素"占比（警告灯 lit/off 判据）。"""
        x, y, w, h = region
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            raise AssertionError(f"非法的检测区域: {region}")
        if x + w > image.width() or y + h > image.height():
            raise AssertionError(
                f"检测区域 {region} 超出图片范围 "
                f"({image.width()}x{image.height()})")
        crop = image.copy(QRect(x, y, w, h))
        data = bytes(crop.constBits())
        bytes_per_line = crop.bytesPerLine()
        red = 0
        for row_y in range(h):
            row = row_y * bytes_per_line
            for col_x in range(w):
                i = row + col_x * 4
                blue = data[i]
                green = data[i + 1]
                red_channel = data[i + 2]
                if (red_channel >= TELLTALE_RED_MIN_BRIGHTNESS
                        and red_channel - green >= TELLTALE_RED_MIN_GAP
                        and red_channel - blue >= TELLTALE_RED_MIN_GAP):
                    red += 1
        return red / float(w * h)

    def _telltale_region(self, telltale: str) -> tuple[int, int, int, int]:
        if telltale not in canvas.TELLTALE_REGIONS:
            raise AssertionError(
                f"未知警告灯: {telltale} —— 可选: "
                f"{', '.join(sorted(canvas.TELLTALE_REGIONS))}")
        return canvas.TELLTALE_REGIONS[telltale]

    # ==================================================================
    # 截图生成
    # ==================================================================
    @keyword("Render Cluster State")
    def render_cluster_state(self, state, output_path) -> str:
        """把显示状态渲染成仪表截图 PNG，返回截图路径。

        Arguments:
        - ``state``：显示状态。两种写法都支持：
            ① dict（测试里直接构造，如 ``{"speed": 60, "telltales": {"CoolantTempHigh": 1}}``）
            ② dashboard_state.json 这类状态文件路径（headless 仿真写出的快照）
        - ``output_path``：截图输出路径（目录不存在会自动创建）。

        渲染是**确定性**的：同一状态在同一平台渲染出的像素逐位相同
        （可直接做"渲染两次像素完全一致"的确定性用例）。
        """
        try:
            path = canvas.render_state_to_png(state, output_path)
        except FileNotFoundError as exc:
            raise AssertionError(
                f"{exc} —— state 参数可传 dict 或状态 JSON 文件路径") from None
        except (ValueError, TypeError, OSError) as exc:
            raise AssertionError(f"渲染截图失败: {exc}") from None
        info(f"已渲染仪表截图: {path}")
        return str(path)

    @keyword("Capture Cluster Screenshot")
    def capture_cluster_screenshot(self, state=None, output_path=None) -> str:
        """抓取一帧仪表截图（本环境 = 调离屏渲染器渲染），返回 PNG 路径。

        Arguments:
        - ``state``：显示状态（dict / 状态 JSON 路径）。缺省时若 headless 仪表盘
          正在写 ``simulators/dashboard_state.json`` 就读它，否则用默认状态。
        - ``output_path``：输出路径，缺省 ``results/vision/capture.png``。

        **真实项目怎么扩展**：这个关键字是"截图来源"的唯一入口，真实台架上把它
        换成
            · Qt 仪表：``QScreen.grabWindow()`` / ``grabToImage()``
            · 安卓车机：``adb exec-out screencap -p > shot.png``
            · Appium：``driver.get_screenshot_as_file()``
            · 视频流：FFmpeg 抽帧
        后续 Compare With Golden / Get Needle Angle / Telltale Region ... 全部只认
        PNG 文件，一行断言都不用改 —— 这就是"截图来源"与"视觉断言"解耦的价值。
        """
        if state is None:
            if LIVE_STATE_FILE.exists():
                state = LIVE_STATE_FILE
                info(f"Capture: 使用 headless 仪表盘状态文件 {LIVE_STATE_FILE.name}")
            else:
                state = {}
                info("Capture: 未发现 dashboard_state.json，使用默认显示状态")
        target = Path(output_path) if output_path else \
            self._output_dir / "capture.png"
        return self.render_cluster_state(state, target)

    # ==================================================================
    # 黄金图管理
    # ==================================================================
    def _golden_destination(self, golden) -> Path:
        """黄金图参数：纯文件名 → golden 目录；含目录的路径 → 原样使用。"""
        path = Path(str(golden))
        if path.parent and str(path.parent) not in (".", ""):
            if not path.is_absolute():
                path = (ROOT / path).resolve()
            return path
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        return self._golden_dir / path

    @keyword("Save Golden")
    def save_golden(self, image_path, golden_name) -> str:
        """把一张截图保存为黄金图（基准图），返回保存后的路径。

        Arguments:
        - ``image_path``：已渲染/截图的 PNG 路径
        - ``golden_name``：黄金图文件名（如 ``cluster_default.png``，可省略 .png），
          也可传包含目录的完整相对路径做临时基准图（测试用）。

        注意：黄金图是**要提交进 git 的测试资产**，不是运行产物；日常新增/修改
        基准走本关键字或 ``Update Golden``，并在 code review 里看 diff。
        """
        source = self._as_path(image_path)
        self._load_image(source)             # 校验确实是可读图片
        destination = self._golden_destination(golden_name)
        self._ensure_parent(destination)
        shutil.copyfile(source, destination)
        info(f"黄金图已保存: {destination}")
        return str(destination)

    @keyword("Update Golden")
    def update_golden(self, image_path, golden_name) -> str:
        """显式更新黄金图（基准图管理入口），返回更新后的路径。

        什么时候用：
          - HMI 界面**按需求**改版（布局/配色变了，且人工确认新画面对）；
          - 首次为某个状态建立基准图。
        **不能**在测试失败时自动更新 —— 那会把 bug 固化成"预期"（视觉测试
        最大的反模式）。所以更新必须显式调用、人工复核、随代码一起提交。
        """
        destination = self.save_golden(image_path, golden_name)
        info(f"⚠ 黄金图已更新: {destination} —— 请人工复核新旧差异并提交 git")
        return destination

    # ==================================================================
    # 比对
    # ==================================================================
    @keyword("Compare With Golden")
    def compare_with_golden(self, image_path, golden,
                            tolerance: float = DEFAULT_TOLERANCE,
                            color_tolerance: int = PIXEL_COLOR_TOLERANCE,
                            diff_path=None) -> float:
        """整图与黄金图比对：差异比例在容差内通过，否则报错并保存差异图。

        Arguments:
        - ``image_path``：实际截图
        - ``golden``：黄金图文件名（如 ``cluster_default.png``）或路径
        - ``tolerance``：允许的差异比例，默认 2%（见 DEFAULT_TOLERANCE 注释）
        - ``color_tolerance``：单像素通道容差，默认 32
        - ``diff_path``：差异图输出路径，缺省存到 ``results/vision/diffs/``

        返回实际差异比例（0.0 ~ 1.0 的浮点数，便于用例做排序/阈值断言）。
        失败时抛 AssertionError，消息里带差异比例、差异像素数与差异图路径。
        """
        actual = self._load_image(image_path, "实际截图")
        golden_image = self._load_image(self._resolve_golden(golden), "黄金图")
        indexes, total = self._compare_images(actual, golden_image,
                                              int(color_tolerance))
        ratio = len(indexes) / total
        if ratio > float(tolerance):
            target = Path(str(diff_path)) if diff_path else \
                self._diff_dir / self._auto_diff_name(image_path, golden)
            self._write_diff_image(actual, indexes, target)
            raise AssertionError(
                f"整图与黄金图不一致：差异比例 {ratio:.4%} > 容差 "
                f"{float(tolerance):.2%}（{len(indexes)}/{total} 像素不同）; "
                f"差异图已保存: {target}")
        info(f"整图比对通过：差异比例 {ratio:.4%} ≤ 容差 {float(tolerance):.2%}")
        return ratio

    @keyword("Compare Region With Golden")
    def compare_region_with_golden(self, image_path, golden, x, y, w, h,
                                   tolerance: float = DEFAULT_TOLERANCE,
                                   color_tolerance: int = PIXEL_COLOR_TOLERANCE,
                                   diff_path=None) -> float:
        """只比对图片的指定区域（兴趣区 ROI），其余区域变化不影响结果。

        Arguments:
        - ``image_path`` / ``golden``：同 Compare With Golden
        - ``x, y, w, h``：区域坐标与大小（如警告灯区域，可从 Get Telltale Region 查）
        - ``tolerance / color_tolerance / diff_path``：同上

        典型用途：只验证"警告灯区域没变"，而车速/转速变化引起的表盘变化不参与
        比对 —— 区域比对是抑制无关变化、提高稳定性的第一手段。
        """
        actual = self._load_image(image_path, "实际截图")
        golden_image = self._load_image(self._resolve_golden(golden), "黄金图")
        region = (int(x), int(y), int(w), int(h))
        actual_roi = self._crop(actual, region, "实际截图")
        golden_roi = self._crop(golden_image, region, "黄金图")
        indexes, total = self._compare_images(actual_roi, golden_roi,
                                              int(color_tolerance))
        ratio = len(indexes) / total
        if ratio > float(tolerance):
            target = Path(str(diff_path)) if diff_path else \
                self._diff_dir / self._auto_diff_name(image_path, golden,
                                                      suffix="_region")
            self._write_diff_image(actual_roi, indexes, target)
            raise AssertionError(
                f"区域 {region} 与黄金图不一致：差异比例 {ratio:.4%} > 容差 "
                f"{float(tolerance):.2%}（{len(indexes)}/{total} 像素不同）; "
                f"差异图已保存: {target}")
        info(f"区域 {region} 比对通过：差异比例 {ratio:.4%}")
        return ratio

    def _crop(self, image: QImage, region: tuple[int, int, int, int],
              what: str) -> QImage:
        x, y, w, h = region
        if x < 0 or y < 0 or w <= 0 or h <= 0 or \
                x + w > image.width() or y + h > image.height():
            raise AssertionError(
                f"区域 {region} 超出{what}范围 ({image.width()}x{image.height()})")
        return image.copy(QRect(x, y, w, h))

    def _auto_diff_name(self, image_path, golden, suffix: str = "") -> str:
        actual_stem = Path(str(image_path)).stem
        golden_stem = Path(str(golden)).stem
        return f"{actual_stem}__vs__{golden_stem}{suffix}_diff.png"

    @keyword("Image Diff Ratio")
    def image_diff_ratio(self, image_a, image_b,
                         color_tolerance: int = PIXEL_COLOR_TOLERANCE) -> float:
        """计算两张图的差异比例（0.0 = 完全相同，1.0 = 全图都不同）。

        Arguments:
        - ``image_a`` / ``image_b``：两张同尺寸 PNG
        - ``color_tolerance``：单像素通道容差；传 0 就是**严格逐像素相等**
          （确定性用例用它证明"渲染两次一模一样"）

        本关键字不抛错（除了尺寸不一致/文件不存在），适合做"差异排序""灵敏度"
        这类需要拿到数值自己断言的场景。
        """
        image_a_obj = self._load_image(image_a, "图片 A")
        image_b_obj = self._load_image(image_b, "图片 B")
        indexes, total = self._compare_images(image_a_obj, image_b_obj,
                                              int(color_tolerance))
        return len(indexes) / total

    @keyword("Images Should Differ")
    def images_should_differ(self, image_a, image_b, min_ratio: float = 0.001,
                             color_tolerance: int = PIXEL_COLOR_TOLERANCE) -> float:
        """断言两张图**确实有差异**（差异比例 ≥ min_ratio）。

        专门用来防视觉测试最大的坑：**比对恒真**。如果差分算法写错（比如把图片
        缩成 1x1 再比、或容差设成 1.0），所有用例都会"通过"却什么都没验证。
        mutation 用例（故意改状态→必须比对失败）就是靠本关键字证明灵敏度。
        """
        ratio = self.image_diff_ratio(image_a, image_b, color_tolerance)
        if ratio < float(min_ratio):
            raise AssertionError(
                f"两张图几乎相同：差异比例 {ratio:.4%} < 最小可接受 "
                f"{float(min_ratio):.2%} —— 比对可能已失去灵敏度（恒真）")
        info(f"差异灵敏度检查通过：差异比例 {ratio:.4%} ≥ {float(min_ratio):.2%}")
        return ratio

    @keyword("Save Diff Image")
    def save_diff_image(self, image_path, golden, diff_path=None) -> str:
        """生成并保存差异图（红色高亮差异像素 + 红框标差异范围），返回路径。

        Arguments:
        - ``image_path``：实际截图
        - ``golden``：黄金图
        - ``diff_path``：输出路径；缺省 ``results/vision/diffs/`` 下自动命名

        差异图是失败现场的第一手证据：进 CI artifact 或贴进缺陷单。
        """
        actual = self._load_image(image_path, "实际截图")
        golden_image = self._load_image(self._resolve_golden(golden), "黄金图")
        indexes, total = self._compare_images(actual, golden_image,
                                              PIXEL_COLOR_TOLERANCE)
        target = Path(str(diff_path)) if diff_path else \
            self._diff_dir / self._auto_diff_name(image_path, golden)
        self._write_diff_image(actual, indexes, target)
        info(f"差异图已保存: {target}（{len(indexes)}/{total} 像素不同）")
        return str(target)

    # ==================================================================
    # 警告灯区域识别
    # ==================================================================
    @keyword("Get Telltale Region")
    def get_telltale_region(self, telltale: str) -> list[int]:
        """查询指定警告灯的检测区域 ``[x, y, w, h]``（几何常量来自渲染器）。

        视觉用例按名字取坐标，不写死魔法数字 —— 渲染器改了布局，测试自动跟随。
        """
        x, y, w, h = self._telltale_region(telltale)
        return [x, y, w, h]

    @keyword("Telltale Region Should Be Lit")
    def telltale_region_should_be_lit(self, image_path, telltale: str,
                                      min_ratio: float = TELLTALE_LIT_MIN_RATIO) -> float:
        """断言指定警告灯区域**已点亮**：红色像素占比 ≥ min_ratio。

        Arguments:
        - ``image_path``：仪表截图
        - ``telltale``：警告灯名（与 DBC 信号名一致，如 ``CoolantTempHigh``）
        - ``min_ratio``：红色占比阈值，默认 25%（红灯圆点实际约 48%）

        返回实测红色占比，便于日志/调阈值。
        """
        region = self._telltale_region(telltale)
        image = self._load_image(image_path, "仪表截图")
        ratio = self._red_ratio(image, region)
        if ratio < float(min_ratio):
            raise AssertionError(
                f"警告灯 {telltale} 区域 {region} 红色占比 {ratio:.2%} < 点亮阈值 "
                f"{float(min_ratio):.2%} —— 灯应亮但没有亮")
        info(f"警告灯 {telltale} 已点亮（红色占比 {ratio:.2%}）")
        return ratio

    @keyword("Telltale Region Should Be Off")
    def telltale_region_should_be_off(self, image_path, telltale: str,
                                      max_ratio: float = TELLTALE_OFF_MAX_RATIO) -> float:
        """断言指定警告灯区域**已熄灭**：红色像素占比 ≤ max_ratio。

        与 Should Be Lit 配对使用：同一个区域"亮→断言 lit，灭→断言 off"，
        负向断言才能证明识别逻辑不是恒真。
        """
        region = self._telltale_region(telltale)
        image = self._load_image(image_path, "仪表截图")
        ratio = self._red_ratio(image, region)
        if ratio > float(max_ratio):
            raise AssertionError(
                f"警告灯 {telltale} 区域 {region} 红色占比 {ratio:.2%} > 熄灭阈值 "
                f"{float(max_ratio):.2%} —— 灯应灭但还亮着")
        info(f"警告灯 {telltale} 已熄灭（红色占比 {ratio:.2%}）")
        return ratio

    # ==================================================================
    # 指针角度识别
    # ==================================================================
    @keyword("Get Needle Angle")
    def get_needle_angle(self, image_path) -> float:
        """识别车速指针角度，返回相对 0 刻度的扫过角度（0 ~ 270°）。

        算法：按指针专属橙色找像素 → 排除中心轴盖 → 取指针尖端一圈像素做
        向量平均 → 换算成仪表扫过角度（0=表盘下限，270=上限）。
        只认像素颜色，不依赖任何"状态字段"—— 真实项目里这就是你看图的自动化版本。
        """
        image = self._load_image(image_path, "仪表截图")
        try:
            angle = canvas.measure_needle_angle(image)
        except ValueError as exc:
            raise AssertionError(str(exc)) from None
        info(f"识别到指针角度: {angle:.2f}°")
        return float(angle)

    @keyword("Needle Angle Should Be Near")
    def needle_angle_should_be_near(self, image_path, expected_angle: float,
                                    tolerance: float = NEEDLE_ANGLE_TOLERANCE_DEG) -> float:
        """断言指针角度在期望值 ± tolerance 内（默认 2°）。

        Arguments:
        - ``image_path``：仪表截图
        - ``expected_angle``：期望扫过角度；由车速换算：``speed / 240 * 270``
        - ``tolerance``：角度容差（度）

        为什么容差是 2° 而不是 0：像素级角度识别的量化误差约 0.3~0.5°，
        再叠加抗锯齿，留 2° 既能抓住"指针卡死/接错信号"这类真实缺陷，
        又不会被 1px 的渲染差异搞成 flaky。
        """
        actual = self.get_needle_angle(image_path)
        delta = abs(actual - float(expected_angle))
        if delta > float(tolerance):
            raise AssertionError(
                f"指针角度 {actual:.2f}° 偏离期望 {float(expected_angle):.2f}° "
                f"超过容差 ±{float(tolerance):.1f}°（实际偏差 {delta:.2f}°）")
        info(f"指针角度 {actual:.2f}° ≈ 期望 {float(expected_angle):.2f}°"
             f"（偏差 {delta:.2f}° ≤ ±{float(tolerance):.1f}°）")
        return actual

    # ==================================================================
    # 工具
    # ==================================================================
    @keyword("Count Red Pixels")
    def count_red_pixels(self, image_path) -> int:
        """统计图片中"接近纯红"的像素数（差异图高亮的检测手段）。

        判定条件：R ≥ 200 且 G ≤ 60 且 B ≤ 60。差异图把差异像素涂成纯红
        ``#ff0000``，而仪表本身的警告灯红是 ``#ff3b30``（G=59）—— 阈值区分开，
        所以"差异图里红色像素数 > 0"就等价于"差异图不是空白/确实有高亮"。
        """
        image = self._load_image(image_path, "图片")
        data, bytes_per_line = self._pixels(image)
        count = 0
        for y in range(image.height()):
            row = y * bytes_per_line
            for x in range(image.width()):
                i = row + x * 4
                if data[i] <= 60 and data[i + 1] <= 60 and data[i + 2] >= 200:
                    count += 1
        return count
