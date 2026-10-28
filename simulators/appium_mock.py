"""
appium_mock.py — Appium Server 的 W3C WebDriver 教学 mock（HTTP 127.0.0.1:4723）
================================================================================

为什么需要它
------------
Appium 是车载 HMI/车机 UI 自动化的行业标准：它把 **W3C WebDriver 协议**翻译成
Android 的 uiautomator2 操作（找元素/点击/取文本/截图）。真机上你看到的是：

    Appium 客户端（脚本）──HTTP JSON──► Appium Server ──► uiautomator2 ──► 车机

想学这套东西，但本机没有安卓车机、也不想装几个 G 的 Android Studio。
所以本模块用**标准库 http.server** 实现 Appium Server 的 W3C 协议子集，
后端接到本项目现有的安卓车机模拟状态（`simulators/adb_device_state.json`，
由 `simulators/adb_sim.py` 维护；文件不存在时用默认状态）：

    测试脚本 ──HTTP JSON──► appium_mock.py ──► adb_device_state.json（6 个车机界面）

**换真机 = 只改 server URL + capabilities**：`libraries/AppiumLibrary.py` 是标准
urllib W3C 客户端，把 `http://127.0.0.1:XXXX` 换成真 Appium 的地址即可（见 docs/37）。

实现的 W3C 端点（子集）
----------------------
    GET    /status                                  服务就绪探测
    POST   /session                                 创建会话（capabilities → sessionId）
    GET    /session/{id}/source                     uiautomator XML 页面结构
    GET    /session/{id}/screenshot                 截图（PNG，base64 放在 value 里）
    POST   /session/{id}/element                    查找元素（id / accessibility id / xpath）
    POST   /session/{id}/element/{eid}/click        点击元素
    GET    /session/{id}/element/{eid}/text         读取元素文本
    POST   /session/{id}/actions                    W3C Actions（tap / swipe）
    GET    /session/{id}/appium/device/current_activity   当前 Activity
    POST   /session/{id}/appium/device/start_activity     启动 Activity（切换界面）
    GET    /session/{id}/appium/mock/actions        本 mock 专用：已记录的操作（调试用）
    DELETE /session/{id}                            删除会话
    未实现端点 → 404 + W3C JSON error

设计约束（教学版的"真"与"假"）
------------------------------
* **协议是真的**：W3C 请求/响应格式（`{"value": ...}`、错误对象、element key
  `element-6066-11e4-a52e-4f735466cecf`）都按规范写，客户端代码可直接指向真 Appium；
* **页面树是 mock 的**：XML 由 `adb_device_state.json` 的 `current_focus` + `ivi`
  实时生成，6 个车机界面（桌面/媒体/导航/电话/空调/设置）与 `android_gui.py` 一致；
* **截图是画出来的**：真实 Appium 截的是设备屏幕；mock 用**纯标准库**生成确定性
  PNG（渐变 + 色块 + 伪纹理；不用字体，跨机一致）。它的价值是打通"截图→保存→
  断言"链路，而不是像素级还原 GUI；
* **操作会落盘**：click/tap 会写回 `adb_device_state.json`（`logs` 里留 Appium 记录），
  所以"点击开始导航 → 状态变化"这种闭环可以真实验证。

运行
----
    .\\.venv\\Scripts\\python.exe simulators/appium_mock.py            # 默认 127.0.0.1:4723
    .\\.venv\\Scripts\\python.exe simulators/appium_mock.py --port 4799
    # 端口也可用环境变量 APPIUM_PORT 覆盖

    # 手工探活（另一个终端）：
    curl http://127.0.0.1:4723/status
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import sys
import threading
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree

# 复用现有安卓车机状态文件（adb_sim 维护，android_gui 也读写它）
SIMULATORS_DIR = Path(__file__).resolve().parent
if str(SIMULATORS_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATORS_DIR))
import adb_sim                                                # noqa: E402
from ivi_data import CONTACTS, STREETS, TRACKS    # noqa: E402

# ---------------------------------------------------------------------------
# 常量：W3C 协议 / 车机界面注册表
# ---------------------------------------------------------------------------
DEFAULT_PORT = int(os.environ.get("APPIUM_PORT", "4723"))
DEFAULT_HOST = "127.0.0.1"
#: W3C 标准元素 key（客户端用它从 find element 响应里取 elementId）
W3C_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
#: 手机屏幕逻辑分辨率（与 adb_sim 的 `wm size` 一致：1920x1080）
SCREEN_W, SCREEN_H = 1920, 1080
#: 显示状态文件（adb_sim 的状态文件，缺失时用默认值）
STATE_FILE = adb_sim.STATE_FILE

#: 6 个车机界面：包名 → (中文名, Activity)
APP_ACTIVITIES: dict[str, tuple[str, str]] = {
    "com.vehicle.launcher": ("桌面", ".HomeActivity"),
    "com.vehicle.media": ("媒体", ".MainActivity"),
    "com.vehicle.navi": ("导航", ".MapActivity"),
    "com.vehicle.phone": ("电话", ".DialerActivity"),
    "com.vehicle.hvac": ("空调", ".HvacActivity"),
    "com.vehicle.settings": ("设置", ".SettingsActivity"),
}
#: 桌面图标顺序（与 android_gui.py 的 LauncherPage 一致）
LAUNCHER_ORDER = ["com.vehicle.navi", "com.vehicle.media", "com.vehicle.phone",
                  "com.vehicle.hvac", "com.vehicle.settings"]


# ===========================================================================
# UI 树：节点表示 + XML 序列化（等价 uiautomator dump）
# ===========================================================================
def _bounds(x: int, y: int, x2: int, y2: int) -> str:
    """uiautomator 的 bounds 字符串格式：[x,y][x2,y2]"""
    return f"[{x},{y}][{x2},{y2}]"


def node(text: str = "", resource_id: str = "", class_name: str = "android.widget.TextView",
         content_desc: str = "", bounds: tuple[int, int, int, int] = (0, 0, 0, 0),
         clickable: bool = False, package: str = "", action: dict | None = None,
         children: list[dict] | None = None) -> dict:
    """构造一个 uiautomator 节点（attributes + 子节点 + mock 内部 action）。"""
    return {
        "attrs": {
            "index": 0,
            "text": text,
            "resource-id": resource_id,
            "class": class_name,
            "package": package,
            "content-desc": content_desc,
            "checkable": "false",
            "checked": "false",
            "clickable": "true" if clickable else "false",
            "enabled": "true",
            "focusable": "true" if clickable else "false",
            "focused": "false",
            "scrollable": "false",
            "long-clickable": "false",
            "password": "false",
            "selected": "false",
            "bounds": _bounds(*bounds),
        },
        "children": children or [],
        "action": action,
    }


def flatten_nodes(root: dict) -> list[dict]:
    """深度优先摊平 UI 树（查找元素用）。"""
    result = [root]
    for child in root["children"]:
        result.extend(flatten_nodes(child))
    return result


def serialize_tree(root: dict, package: str, activity: str) -> str:
    """把节点树序列化成 uiautomator XML 字符串（含 package/activity 属性）。"""

    def to_element(item: dict) -> ElementTree.Element:
        attrs = {key: str(value) for key, value in item["attrs"].items()}
        element = ElementTree.Element("node", attrs)
        for child in item["children"]:
            element.append(to_element(child))
        return element

    element = to_element(root)
    # 顶层补 package/activity：uiautomator 本身不带 activity，这里加给测试断言用
    element.set("package", package)
    element.set("activity", activity)
    body = ElementTree.tostring(element, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
            f'<hierarchy rotation="0">\n{body}\n</hierarchy>')


# ===========================================================================
# 6 个车机界面的 UI 树构建（数据来自 adb_device_state.json）
# ===========================================================================
def _screen_frame(package: str, children: list[dict],
                  state: dict) -> dict:
    """公共外壳：状态栏 + 内容节点 + 底部导航栏。"""
    battery = state.get("battery", {}).get("level", 78)
    status_bar = node(text=f"{battery}%",
                      resource_id="com.android.systemui:id/battery",
                      bounds=(40, 20, 240, 60), package="com.android.systemui")
    bottom = [
        node(text="◀", resource_id="com.android.systemui:id/back",
             content_desc="返回", class_name="android.widget.Button",
             bounds=(40, 1000, 200, 1070), clickable=True,
             package="com.android.systemui", action={"type": "home"}),
        node(text="●", resource_id="com.android.systemui:id/home",
             content_desc="主屏", class_name="android.widget.Button",
             bounds=(220, 1000, 380, 1070), clickable=True,
             package="com.android.systemui", action={"type": "home"}),
        node(text="■", resource_id="com.android.systemui:id/recent",
             content_desc="最近任务", class_name="android.widget.Button",
             bounds=(400, 1000, 560, 1070), clickable=True,
             package="com.android.systemui", action={"type": "home"}),
    ]
    return node(class_name="android.widget.FrameLayout", package=package,
                bounds=(0, 0, SCREEN_W, SCREEN_H),
                children=[status_bar, *children, *bottom])


def _title(text: str, package: str, bounds=(60, 100, 1200, 180)) -> dict:
    return node(text=text, resource_id=f"{package}:id/title",
                class_name="android.widget.TextView", bounds=bounds,
                package=package)


def build_launcher_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.launcher", ".HomeActivity"
    children = [_title("桌面", package)]
    positions = [(480, 300), (810, 300), (1140, 300), (480, 560), (810, 560)]
    for index, app_package in enumerate(LAUNCHER_ORDER):
        name = APP_ACTIVITIES[app_package][0]
        app_activity = APP_ACTIVITIES[app_package][1]
        x, y = positions[index]
        children.append(node(
            text=name,
            resource_id=f"com.vehicle.launcher:id/app_{app_package.split('.')[-1]}",
            class_name="android.widget.Button",
            content_desc=f"打开{name}",
            bounds=(x, y, x + 300, y + 200), clickable=True,
            package=package,
            action={"type": "launch", "package": app_package,
                    "activity": app_activity}))
    children.append(node(
        text="点击图标启动应用（等价于 adb shell am start）",
        resource_id="com.vehicle.launcher:id/hint",
        bounds=(360, 830, 1560, 890), package=package))
    return _screen_frame(package, children, state), package, activity


def build_media_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.media", ".MainActivity"
    ivi = state["ivi"]
    track_index = int(ivi.get("track_index", 0)) % len(TRACKS)
    name, artist, _duration = TRACKS[track_index]
    status = int(ivi.get("media_status", 0))
    children = [
        _title("音乐", package),
        node(text=name, resource_id="com.vehicle.media:id/track_name",
             bounds=(60, 210, 1200, 280), package=package),
        node(text=artist, resource_id="com.vehicle.media:id/artist",
             bounds=(60, 290, 900, 350), package=package),
        node(text="上一首", resource_id="com.vehicle.media:id/btn_prev",
             class_name="android.widget.Button", bounds=(100, 420, 260, 500),
             clickable=True, package=package,
             action={"type": "media_step", "delta": -1}),
        node(text="暂停" if status == 1 else "播放",
             resource_id="com.vehicle.media:id/btn_play",
             class_name="android.widget.Button", bounds=(280, 420, 440, 500),
             clickable=True, package=package, action={"type": "media_toggle"}),
        node(text="下一首", resource_id="com.vehicle.media:id/btn_next",
             class_name="android.widget.Button", bounds=(460, 420, 620, 500),
             clickable=True, package=package,
             action={"type": "media_step", "delta": 1}),
        node(text=f"音量 {int(ivi.get('volume', 40))}%",
             resource_id="com.vehicle.media:id/volume",
             bounds=(680, 430, 980, 490), package=package),
        node(text="播放列表", resource_id="com.vehicle.media:id/list_title",
             bounds=(60, 540, 600, 600), package=package),
    ]
    for index, (track_name, track_artist, _duration) in enumerate(TRACKS):
        children.append(node(
            text=f"{index + 1}. {track_name} — {track_artist}",
            resource_id=f"com.vehicle.media:id/track_{index}",
            class_name="android.widget.Button",
            bounds=(60, 610 + index * 55, 1100, 655 + index * 55),
            clickable=True, package=package,
            action={"type": "track", "index": index}))
    return _screen_frame(package, children, state), package, activity


def build_navi_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.navi", ".MapActivity"
    ivi = state["ivi"]
    active = int(ivi.get("navi_active", 0))
    distance = int(ivi.get("navi_distance", 0))
    street_index = int(ivi.get("street_index", 0))
    children = [
        _title("导航", package),
        node(text="导航中" if active else "未开始导航",
             resource_id="com.vehicle.navi:id/status",
             bounds=(60, 180, 900, 260), package=package),
        node(text=f"剩余 {distance} 米 · {STREETS[street_index % len(STREETS)]}"
                  f" · 直行",
             resource_id="com.vehicle.navi:id/detail",
             bounds=(60, 270, 1500, 330), package=package),
        node(text="开始导航", resource_id="com.vehicle.navi:id/btn_start",
             class_name="android.widget.Button", bounds=(100, 380, 360, 460),
             clickable=True, package=package, action={"type": "navi_start"}),
        node(text="结束导航", resource_id="com.vehicle.navi:id/btn_stop",
             class_name="android.widget.Button", bounds=(400, 380, 660, 460),
             clickable=True, package=package, action={"type": "navi_stop"}),
        node(text="选择目的地", resource_id="com.vehicle.navi:id/dest_title",
             bounds=(60, 520, 600, 580), package=package),
    ]
    for index, street in enumerate(STREETS):
        children.append(node(
            text=street, resource_id=f"com.vehicle.navi:id/dest_{index}",
            class_name="android.widget.Button",
            bounds=(60, 590 + index * 55, 700, 635 + index * 55),
            clickable=True, package=package,
            action={"type": "navi_dest", "index": index}))
    return _screen_frame(package, children, state), package, activity


def build_phone_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.phone", ".DialerActivity"
    ivi = state["ivi"]
    call_status = int(ivi.get("call_status", 0))
    caller = CONTACTS[int(ivi.get("caller_index", 0)) % len(CONTACTS)][0]
    status_text = {0: "空闲", 1: "来电", 2: "通话中"}.get(call_status, "空闲")
    children = [
        _title("电话", package),
        node(text=status_text, resource_id="com.vehicle.phone:id/call_status",
             bounds=(60, 180, 900, 260), package=package),
        node(text=caller, resource_id="com.vehicle.phone:id/caller",
             bounds=(60, 280, 900, 340), package=package),
        node(text="挂断", resource_id="com.vehicle.phone:id/btn_hangup",
             class_name="android.widget.Button", bounds=(100, 360, 360, 440),
             clickable=True, package=package, action={"type": "hangup"}),
        node(text="联系人（点击呼叫）",
             resource_id="com.vehicle.phone:id/contacts_title",
             bounds=(60, 500, 800, 560), package=package),
    ]
    for index, (name, number) in enumerate(CONTACTS):
        children.append(node(
            text=f"{name} {number}",
            resource_id=f"com.vehicle.phone:id/contact_{index}",
            class_name="android.widget.Button",
            bounds=(60, 570 + index * 55, 1000, 615 + index * 55),
            clickable=True, package=package,
            action={"type": "call", "index": index}))
    return _screen_frame(package, children, state), package, activity


def build_hvac_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.hvac", ".HvacActivity"
    ivi = state["ivi"]
    temp = int(ivi.get("hvac_temp", 24))
    fan = int(ivi.get("fan_level", 3))
    children = [
        _title("空调", package),
        node(text=f"{temp}℃", resource_id="com.vehicle.hvac:id/temp",
             bounds=(720, 200, 1200, 340), package=package),
        node(text="－", resource_id="com.vehicle.hvac:id/temp_down",
             class_name="android.widget.Button", bounds=(500, 380, 700, 480),
             clickable=True, package=package,
             action={"type": "hvac_temp", "delta": -1}),
        node(text="＋", resource_id="com.vehicle.hvac:id/temp_up",
             class_name="android.widget.Button", bounds=(1100, 380, 1300, 480),
             clickable=True, package=package,
             action={"type": "hvac_temp", "delta": 1}),
        node(text=f"风量 {fan}", resource_id="com.vehicle.hvac:id/fan",
             bounds=(60, 560, 600, 620), package=package),
    ]
    for index in range(1, 5):
        children.append(node(
            text=f"风量{index}",
            resource_id=f"com.vehicle.hvac:id/fan_{index - 1}",
            class_name="android.widget.Button",
            bounds=(60 + (index - 1) * 220, 640, 240 + (index - 1) * 220, 720),
            clickable=True, package=package,
            action={"type": "fan", "level": index}))
    return _screen_frame(package, children, state), package, activity


def build_settings_page(state: dict) -> tuple[dict, str, str]:
    package, activity = "com.vehicle.settings", ".SettingsActivity"
    children = [_title("设置", package)]
    for index, (key, value) in enumerate(state.get("props", {}).items()):
        children.append(node(
            text=f"{key}: {value}",
            resource_id=f"com.vehicle.settings:id/setting_{index}",
            bounds=(60, 220 + index * 70, 1500, 280 + index * 70),
            package=package))
    return _screen_frame(package, children, state), package, activity


PAGE_BUILDERS = {
    "com.vehicle.launcher": build_launcher_page,
    "com.vehicle.media": build_media_page,
    "com.vehicle.navi": build_navi_page,
    "com.vehicle.phone": build_phone_page,
    "com.vehicle.hvac": build_hvac_page,
    "com.vehicle.settings": build_settings_page,
}


def build_current_tree(state: dict) -> tuple[dict, str, str]:
    """按状态里的 current_focus 构建当前 UI 树，返回 (tree, package, activity)。"""
    focus = str(state.get("current_focus") or "com.vehicle.launcher/.HomeActivity")
    package, _, activity = focus.partition("/")
    if package not in PAGE_BUILDERS:                 # 未知界面统一落到桌面
        package = "com.vehicle.launcher"
        activity = ".HomeActivity"
    activity = activity or APP_ACTIVITIES[package][1]
    return PAGE_BUILDERS[package](state)


def build_current_page(state: dict) -> tuple[str, str, str]:
    """按状态里的 current_focus 构建当前页面 XML，返回 (xml, package, activity)。"""
    tree, package, activity = build_current_tree(state)
    return serialize_tree(tree, package, activity), package, activity


# ===========================================================================
# 元素查找（三种定位策略）
# ===========================================================================
def _match_resource_id(node_rid: str, wanted: str) -> bool:
    if not node_rid or not wanted:
        return False
    if node_rid == wanted:
        return True
    # 允许省略包名：传 "btn_play" 也能命中 "com.vehicle.media:id/btn_play"
    return node_rid.endswith(":id/" + wanted) or node_rid.endswith("/" + wanted)


_XPATH_ATTR_PATTERN = re.compile(
    r"@(text|resource-id|content-desc)\s*=\s*['\"]([^'\"]+)['\"]")


def _match_xpath(attrs: dict, xpath: str) -> bool:
    """极简 xpath：支持 //*[@text='X'] / //*[@resource-id='X'] 等属性等值匹配。

    真实 Appium 用的是完整 XPath 1.0（能写层级/位置/函数）；教学 mock 只实现
    "按属性精确匹配一个元素"这个 90% 用例都在用的子集（见 docs/37 解析）。
    """
    matches = _XPATH_ATTR_PATTERN.findall(xpath)
    if not matches:
        return False
    return all(str(attrs.get(attr, "")) == value for attr, value in matches)


def find_node(root: dict, using: str, value: str) -> dict | None:
    """在 UI 树里按定位策略查找元素，返回节点或 None。"""
    strategy = (using or "").strip().lower()
    for item in flatten_nodes(root):
        attrs = item["attrs"]
        if strategy == "id":
            if _match_resource_id(attrs["resource-id"], value):
                return item
        elif strategy in ("accessibility id", "accessibility_id"):
            if attrs["content-desc"] == value:
                return item
        elif strategy == "xpath":
            if _match_xpath(attrs, value):
                return item
        elif strategy == "text":
            if attrs["text"] == value:
                return item
        elif strategy == "class name":
            if attrs["class"] == value:
                return item
        else:
            raise ValueError(f"mock 不支持的定位策略: {using}")
    return None


# ===========================================================================
# 操作语义：点击后改状态文件（与 android_gui 的交互等价）
# ===========================================================================
def apply_action(state: dict, action: dict) -> str:
    """执行一个 mock 操作，修改车机状态并返回人类可读的描述。"""
    kind = action.get("type")
    ivi = state["ivi"]
    if kind == "launch":
        package = action["package"]
        activity = action["activity"]
        state["current_focus"] = f"{package}/{activity}"
        return f"am start -n {package}/{activity}"
    if kind == "home":
        state["current_focus"] = "com.vehicle.launcher/.HomeActivity"
        return "keyevent HOME → launcher"
    if kind == "navi_start":
        ivi.update(navi_active=1, navi_turn=3, navi_distance=1500,
                   street_index=0)
        return "click 开始导航 → navi_active=1"
    if kind == "navi_stop":
        ivi.update(navi_active=0, navi_turn=0, navi_distance=0)
        return "click 结束导航 → navi_active=0"
    if kind == "navi_dest":
        index = int(action["index"])
        ivi.update(navi_active=1, street_index=index, navi_turn=3,
                   navi_distance=2500)
        return f"click 目的地 #{index} → navi_active=1"
    if kind == "media_toggle":
        ivi["media_status"] = 2 if int(ivi.get("media_status", 0)) == 1 else 1
        return f"click 播放/暂停 → media_status={ivi['media_status']}"
    if kind == "media_step":
        delta = int(action["delta"])
        ivi["track_index"] = (int(ivi.get("track_index", 0)) + delta) % len(TRACKS)
        ivi["media_status"] = 1
        return f"click 切歌 → track_index={ivi['track_index']}"
    if kind == "track":
        ivi["track_index"] = int(action["index"])
        ivi["media_status"] = 1
        return f"click 曲目 → track_index={ivi['track_index']}"
    if kind == "vol":
        ivi["volume"] = max(0, min(100, int(ivi.get("volume", 40))
                                   + int(action["delta"])))
        return f"click 音量 → volume={ivi['volume']}"
    if kind == "call":
        ivi["call_status"] = 2
        ivi["caller_index"] = int(action["index"])
        return f"click 联系人 → call_status=2"
    if kind == "hangup":
        ivi["call_status"] = 0
        return "click 挂断 → call_status=0"
    if kind == "hvac_temp":
        ivi["hvac_temp"] = max(16, min(30, int(ivi.get("hvac_temp", 24))
                                       + int(action["delta"])))
        return f"click 温度 → hvac_temp={ivi['hvac_temp']}"
    if kind == "fan":
        ivi["fan_level"] = int(action["level"])
        return f"click 风量 → fan_level={ivi['fan_level']}"
    return f"mock 未实现的操作: {kind}"


# ===========================================================================
# 截图：纯标准库生成确定性 PNG（不依赖字体/显示环境）
# ===========================================================================
#: 6 个界面的主题色（RGB），让截图能区分界面（真实 Appium 截的是车机屏幕）
PAGE_COLORS = {
    "com.vehicle.launcher": (32, 44, 58),
    "com.vehicle.media": (48, 80, 112),
    "com.vehicle.navi": (36, 112, 84),
    "com.vehicle.phone": (112, 64, 96),
    "com.vehicle.hvac": (112, 96, 48),
    "com.vehicle.settings": (72, 72, 88),
}


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def make_screenshot_png(state: dict) -> bytes:
    """生成一张 480x800 的确定性 PNG（渐变 + 界面主题色块 + 伪纹理）。

    为什么不用 PySide6 渲染：mock 截图只用于验证"截图链路"，
    纯标准库生成跨平台 100% 一致、无字体依赖、无需 Qt 初始化。
    """
    focus = str(state.get("current_focus") or "com.vehicle.launcher/.HomeActivity")
    package = focus.partition("/")[0]
    base = PAGE_COLORS.get(package, (32, 44, 58))
    width, height = 480, 800
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                # 每行 filter type 0
        for x in range(width):
            if y < 60:                               # 顶部状态栏
                raw.extend((base[0] + 40, base[1] + 40, base[2] + 40))
            elif 300 <= y < 380:                     # 主题色块（界面标识）
                raw.extend(base)
            elif 520 <= y < 620 and x < 240:         # 伪纹理区（保证 PNG 体积可断言）
                raw.extend(((x * 31 + y * 17) & 0xFF,
                            (x * 17 + y * 29) & 0xFF,
                            (x * 13 + y * 7) & 0xFF))
            else:                                    # 背景渐变
                raw.extend((min(255, base[0] + x // 6),
                            min(255, base[1] + y // 10),
                            min(255, base[2] + (x + y) // 12)))
    compressed = zlib.compress(bytes(raw), level=6)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b""))


# ===========================================================================
# W3C WebDriver 会话/元素存储
# ===========================================================================
class AppiumError(Exception):
    """W3C 错误（HTTP 状态码 + error code + message）。"""

    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


class AppiumMockServer(ThreadingHTTPServer):
    """HTTP 服务 + 会话存储（练习版 Appium Server 的"状态机"）。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, AppiumMockHandler)
        self.sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0
        print(f"[appium_mock] 就绪: http://{address[0]}:{address[1]}"
              f"（状态文件: {STATE_FILE}）", flush=True)

    # ------------------------------------------------------------------ 状态
    @staticmethod
    def load_state() -> dict:
        return adb_sim.load_state()

    @staticmethod
    def save_state(state: dict) -> None:
        adb_sim.save_state(state)

    # ------------------------------------------------------------------ 会话
    def _new_session(self, capabilities: dict) -> dict:
        merged = dict(capabilities)
        platform = str(merged.get("platformName") or "Android")
        if platform.lower() != "android":
            raise AppiumError(500, "session not created",
                              f"不支持的 platformName: {platform}（mock 只支持 Android）")
        app_package = merged.get("appPackage")
        if app_package:
            app_activity = merged.get("appActivity") or \
                APP_ACTIVITIES.get(str(app_package), (None, ".MainActivity"))[1]
            state = self.load_state()
            state["current_focus"] = f"{app_package}/{app_activity}"
            adb_sim.log(state, "Appium", f"session 启动 {app_package}/{app_activity}")
            self.save_state(state)
        session_id = f"appium-mock-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._counter += 1
            self.sessions[session_id] = {
                "capabilities": merged,
                "elements": {},
                "actions": [],
                "next_element": 1,
            }
        print(f"[appium_mock] 创建会话 {session_id} caps={merged}", flush=True)
        return {"sessionId": session_id, "capabilities": merged}

    def _session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise AppiumError(404, "invalid session id",
                              f"invalid session id: {session_id}"
                              f"（会话不存在或已 DELETE）")
        return session

    def _record_action(self, session: dict, action: dict) -> None:
        session["actions"].append(action)
        state = self.load_state()
        adb_sim.log(state, "Appium", json.dumps(action, ensure_ascii=False))
        self.save_state(state)

    # ------------------------------------------------------------------ 路由
    def dispatch(self, method: str, parts: list[str], body: dict) -> object:
        if parts and parts[0] == "wd" and len(parts) > 1 and parts[1] == "hub":
            parts = parts[2:]                    # 兼容 Appium 1 的 /wd/hub 前缀
        if method == "GET" and parts == ["status"]:
            return {"ready": True, "build": "appium-mock 1.0",
                    "message": "教学版 Appium Server（W3C WebDriver 子集）"}
        if method == "POST" and parts == ["session"]:
            capabilities = self._parse_capabilities(body)
            return self._new_session(capabilities)
        if len(parts) >= 2 and parts[0] == "session":
            session_id = parts[1]
            if method == "DELETE" and len(parts) == 2:
                self._session(session_id)
                with self._lock:
                    self.sessions.pop(session_id, None)
                print(f"[appium_mock] 删除会话 {session_id}", flush=True)
                return None
            session = self._session(session_id)
            return self._dispatch_session(method, parts[2:], session, body)
        raise AppiumError(404, "unknown command",
                          f"mock 未实现的端点: {method} /{'/'.join(parts)}")

    def _dispatch_session(self, method: str, parts: list[str],
                          session: dict, body: dict) -> object:
        if method == "GET" and parts == ["source"]:
            state = self.load_state()
            xml, _package, _activity = build_current_page(state)
            return xml
        if method == "GET" and parts == ["screenshot"]:
            state = self.load_state()
            return base64.b64encode(make_screenshot_png(state)).decode("ascii")
        if method == "POST" and parts == ["element"]:
            return self._find_element(session, body)
        if method == "POST" and parts == ["actions"]:
            return self._perform_actions(session, body)
        if method == "POST" and len(parts) == 3 and parts[0] == "element" \
                and parts[2] == "click":
            return self._click_element(session, parts[1])
        if method == "GET" and len(parts) == 3 and parts[0] == "element" \
                and parts[2] == "text":
            spec = self._element(session, parts[1])
            return spec["text"]
        if method == "GET" and len(parts) == 3 and parts[0] == "element" \
                and parts[2] == "displayed":
            self._element(session, parts[1])
            return True
        if method == "GET" and parts == ["appium", "device", "current_activity"]:
            state = self.load_state()
            return state["current_focus"]
        if method == "POST" and parts == ["appium", "device", "start_activity"]:
            return self._start_activity(body)
        if method == "GET" and parts == ["appium", "mock", "actions"]:
            return list(session["actions"])
        raise AppiumError(404, "unknown command",
                          f"mock 未实现的 session 端点: {method} "
                          f"/{'/'.join(parts)}")

    # ------------------------------------------------------------------ 具体动作
    @staticmethod
    def _parse_capabilities(body: dict) -> dict:
        capabilities = body.get("capabilities") or {}
        always = capabilities.get("alwaysMatch") or {}
        first_match = (capabilities.get("firstMatch") or [{}])[0] or {}
        legacy = body.get("desiredCapabilities") or {}
        return {**legacy, **first_match, **always}

    def _find_element(self, session: dict, body: dict) -> dict:
        using = body.get("using", "")
        value = body.get("value", "")
        state = self.load_state()
        tree, package, activity = build_current_tree(state)
        found = find_node(tree, using, value)
        if found is None:
            raise AppiumError(
                404, "no such element",
                f"An element could not be located using {using}={value!r} "
                f"(当前界面 {package}/{activity})")
        with self._lock:
            element_id = f"element-{uuid.uuid4().hex[:10]}"
            session["elements"][element_id] = {
                "action": found["action"],
                "resource_id": found["attrs"]["resource-id"],
                "text": found["attrs"]["text"],
            }
        return {W3C_ELEMENT_KEY: element_id, "ELEMENT": element_id}

    def _element(self, session: dict, element_id: str) -> dict:
        spec = session["elements"].get(element_id)
        if spec is None:
            raise AppiumError(404, "no such element",
                              f"stale element reference: {element_id}")
        return spec

    def _click_element(self, session: dict, element_id: str) -> None:
        spec = self._element(session, element_id)
        if not spec.get("action"):
            raise AppiumError(400, "element not interactable",
                              f"元素不可点击: {spec.get('resource_id') or element_id}")
        state = self.load_state()
        description = apply_action(state, spec["action"])
        adb_sim.log(state, "Appium", f"click {spec['action']}: {description}")
        self.save_state(state)
        self._record_action(session, {
            "type": "click",
            "element_id": element_id,
            "resource_id": spec.get("resource_id", ""),
            "description": description,
        })

    def _perform_actions(self, session: dict, body: dict) -> dict:
        """解析 W3C actions：pointerMove ×N + pointerDown/Up → tap 或 swipe。"""
        points: list[tuple[int, int]] = []
        has_down = has_up = False
        for sequence in body.get("actions") or []:
            for action in sequence.get("actions") or []:
                action_type = action.get("type")
                if action_type == "pointerMove" and action.get("x") is not None:
                    points.append((int(action["x"]), int(action["y"])))
                elif action_type == "pointerDown":
                    has_down = True
                elif action_type == "pointerUp":
                    has_up = True
        if not points or not (has_down and has_up):
            raise AppiumError(400, "invalid argument",
                              "actions 里需要 pointerMove + pointerDown + pointerUp")
        if len(points) >= 2 and points[0] != points[-1]:
            record = {"type": "swipe", "x": points[0][0], "y": points[0][1],
                      "x2": points[-1][0], "y2": points[-1][1]}
        else:
            record = {"type": "tap", "x": points[0][0], "y": points[0][1]}
        state = self.load_state()
        adb_sim.log(state, "Input",
                    f"input {record['type']} " +
                    " ".join(str(value) for key, value in record.items()
                             if key != "type"))
        self.save_state(state)
        self._record_action(session, record)
        return record

    def _start_activity(self, body: dict) -> None:
        package = body.get("appPackage") or body.get("app_package")
        activity = body.get("appActivity") or body.get("app_activity") \
            or (APP_ACTIVITIES.get(package, (None, ".MainActivity"))[1])
        state = self.load_state()
        installed = {item["name"] for item in state.get("packages", [])}
        if package not in installed:
            raise AppiumError(404, "activity not started",
                              f"应用未安装: {package}")
        state["current_focus"] = f"{package}/{activity}"
        adb_sim.log(state, "ActivityManager",
                    f"Appium start_activity: {package}/{activity}")
        self.save_state(state)
        return None


# ===========================================================================
# HTTP 层
# ===========================================================================
class AppiumMockHandler(BaseHTTPRequestHandler):
    """极薄 HTTP 层：解析路由 → server.dispatch → 统一 W3C JSON 响应。"""

    server_version = "AppiumMock/1.0"

    def log_message(self, fmt, *args) -> None:      # noqa: D401
        print(f"[appium_mock] {self.address_string()} {fmt % args}", flush=True)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppiumError(400, "invalid argument",
                              f"请求体不是合法 JSON: {exc}") from exc

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        try:
            body = self._read_json_body() if method in ("POST", "PUT") else {}
            parts = [part for part in self.path.split("?")[0].split("/") if part]
            value = self.server.dispatch(method, parts, body)     # type: ignore[attr-defined]
            self._send(200, {"value": value})
        except AppiumError as exc:
            self._send(exc.status, {"value": {"error": exc.error,
                                              "message": exc.message,
                                              "stacktrace": ""}})
        except ValueError as exc:                     # 不支持的定位策略等
            self._send(400, {"value": {"error": "invalid argument",
                                       "message": str(exc), "stacktrace": ""}})
        except Exception as exc:                      # noqa: BLE001
            self._send(500, {"value": {"error": "unknown error",
                                       "message": str(exc), "stacktrace": ""}})

    def do_GET(self) -> None:                         # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:                        # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:                      # noqa: N802
        self._handle("DELETE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Appium Server 教学 mock")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"监听端口（默认 {DEFAULT_PORT}，也可用 APPIUM_PORT 环境变量）")
    args = parser.parse_args(argv)
    try:
        server = AppiumMockServer((args.host, args.port))
    except OSError as exc:
        print(f"[appium_mock] 启动失败（端口 {args.port} 被占用？）: {exc}",
              file=sys.stderr, flush=True)
        return 2
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[appium_mock] 退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

