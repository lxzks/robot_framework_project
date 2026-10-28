"""
AppiumLibrary.py — Robot Framework Appium / W3C WebDriver 关键字库
==================================================================

定位：用**标准库 urllib + json** 手写一个 Appium 客户端（W3C WebDriver 协议），
配合 `simulators/appium_mock.py`（Appium Server 教学 mock）完成车机 UI 自动化闭环：

    测试用例 ──► AppiumLibrary（HTTP JSON）──► Appium Server ──► uiautomator2 ──► 车机

**为什么手写 JSON over HTTP 而不是装 Appium-Python-Client**：
  1. 车载项目常见"现场只有浏览器/受限 Python 环境"的约束，标准库客户端零依赖；
  2. 手写一遍才会真懂 W3C 协议：session/capabilities/element key/actions 是什么；
  3. **换真机只改 server URL + capabilities**（见 docs/37）——接口与真实 Appium 一致。

协议要点（本库实现）
--------------------
* POST /session 请求体：`{"capabilities": {"alwaysMatch": {...}, "firstMatch": [{}]}}`
* 所有响应统一是 `{"value": ...}`；错误是 `{"value": {"error": ..., "message": ...}}`
* find element 返回 `{"element-6066-11e4-a52e-4f735466cecf": "<elementId>"}`
  （这个长字符串是 W3C 规定的元素 key，`ELEMENT` 是旧 JSONWP 兼容 key）
* W3C Actions：`{"actions":[{"type":"pointer","id":"finger1","actions":[...]}]}`，
  tap = pointerMove + pointerDown + pointerUp；swipe = 两个 pointerMove + down/up
* 截图：GET /screenshot 返回 base64 编码的 PNG（真实 Appium 返回设备屏幕）

关键字（18 个）
---------------
    服务:   Start Appium Server / Stop Appium Server
    会话:   Create Session / Delete Session
    查找:   Find Element / Element Should Exist / Element Should Not Exist
    操作:   Click Element / Get Element Text / Element Text Should Be
            Tap Coordinate / Swipe
    源码:   Get Page Source / Page Source Should Contain / Switch To Package
    其他:   Get Current Activity / Get Screenshot / Get Recorded Actions

接真实 Appium（三步，用例代码零改动）
------------------------------------
1. 启动真 Appium Server（uiautomator2 driver），如 `appium --port 4723`；
2. 设置 `APPIUM_SERVER_URL=http://127.0.0.1:4723`（或 `Start Appium Server` 前
   设库参数 `server_url=`）——本库探测到已有服务会直接复用，不再启 mock；
3. 把 `Create Session` 的 capabilities 换成真机参数（platformName/deviceName/
   appPackage/appActivity/uiautomator2ServerInstallTimeout 等）。
"""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

# ---------------------------------------------------------------------------
# 路径/默认参数
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SIMULATORS_DIR = ROOT / "simulators"
MOCK_SCRIPT = SIMULATORS_DIR / "appium_mock.py"
DEFAULT_PORT = int(os.environ.get("APPIUM_PORT", "4723"))
DEFAULT_SERVER_URL = os.environ.get(
    "APPIUM_SERVER_URL", f"http://127.0.0.1:{DEFAULT_PORT}")
#: mock 子进程日志（gitignored 的 results/ 下；真机场景不产生）
SERVER_LOG = ROOT / "results" / "appium_mock_server.log"
#: 默认截图输出
DEFAULT_SCREENSHOT = ROOT / "results" / "appium" / "screenshot.png"
#: W3C 元素 key（规范固定字符串）
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
#: 截图 PNG 文件头
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AppiumLibrary:
    """Appium（W3C WebDriver）UI 自动化关键字集合。

    两类运行模式：
      * **mock 模式**（默认）：`Start Appium Server` 拉起 `simulators/appium_mock.py`，
        后端是 `adb_device_state.json`，不需要安卓设备/ECU，适合 CI 与学习；
      * **真机模式**：先启动真实 Appium Server，把 `APPIUM_SERVER_URL` 指过去，
        `Start Appium Server` 探测到服务后直接复用（不会重复启动）。
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, server_url: str = DEFAULT_SERVER_URL,
                 timeout: float = 10.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = float(timeout)
        self._process: subprocess.Popen | None = None
        self._log_handle = None
        self._session_counter = 0
        console(f"[AppiumLibrary] server={self._server_url} "
                f"（未启动时 Start Appium Server 会自动拉起 mock）")

    # ==================================================================
    # 内部：HTTP / 子进程
    # ==================================================================
    def _http(self, method: str, path: str, body: dict | None = None):
        """发一个 W3C WebDriver HTTP 请求，返回响应里的 ``value``。"""
        url = f"{self._server_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") \
            if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error = json.loads(raw).get("value", {})
            except json.JSONDecodeError:
                error = {"error": "http error", "message": raw}
            raise AssertionError(
                f"Appium 请求失败: {method} {path} → HTTP {exc.code} "
                f"[{error.get('error', '?')}] {error.get('message', raw)}") from None
        except urllib.error.URLError as exc:
            raise AssertionError(
                f"无法连接 Appium Server {self._server_url}"
                f"（先调用 Start Appium Server）: {exc.reason}") from None
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"Appium 响应不是合法 JSON: {method} {path}: {exc}") from None
        if not isinstance(payload, dict) or "value" not in payload:
            raise AssertionError(
                f"Appium 响应不符合 W3C 格式（缺 value 字段）: {payload!r}")
        return payload["value"]

    def _probe(self, timeout: float = 2.0) -> bool:
        """探测 server 是否就绪（GET /status）。"""
        url = f"{self._server_url}/status"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("value", {}).get("ready"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return False

    def _log_tail(self, lines: int = 12) -> str:
        if not SERVER_LOG.exists():
            return "（无日志）"
        content = SERVER_LOG.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:])

    # ==================================================================
    # 服务生命周期
    # ==================================================================
    def _terminate_mock(self, grace: float = 5.0) -> None:
        """回收 mock 子进程（terminate → wait → kill）并关闭日志句柄。

        启动失败路径也必须调用它：否则"进程还活着但 /status 一直没就绪"的
        超时路径会把 mock 进程留在后台占着 4723（交叉审查发现的资源泄漏）。
        """
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    info("[AppiumLibrary] ⚠ Appium mock 未能回收（kill 后仍超时）")
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    @keyword("Start Appium Server")
    def start_appium_server(self, port=None, server_url=None) -> str:
        """启动 Appium Server（已有服务则复用），返回 server URL。

        Arguments:
        - ``port``：mock server 监听端口（默认 4723，可用环境变量 APPIUM_PORT 覆盖）
        - ``server_url``：直接指定已存在的 Appium Server 地址（真机场景常用）

        逻辑：
            ① 先探测目标地址是否已有 server 就绪 → 有就复用（**真机模式**）；
            ② 没有则拉起 `simulators/appium_mock.py` 子进程（**mock 模式**），
               并轮询 /status 直到就绪；
            ③ 启动失败（端口被占用/脚本异常）时报错并附 mock 日志尾部，方便定位；
               同时回收子进程/日志句柄，不留孤儿进程占端口。
        """
        if server_url:
            self._server_url = str(server_url).rstrip("/")
        if port:
            self._server_url = f"http://127.0.0.1:{int(port)}"
        if self._probe():
            info(f"检测到已有 Appium Server，直接复用: {self._server_url}")
            return self._server_url

        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = SERVER_LOG.open("w", encoding="utf-8")
        env = os.environ.copy()
        env["APPIUM_PORT"] = self._server_url.rsplit(":", 1)[-1]
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"      # 子进程控制台中文用 UTF-8
        port_value = self._server_url.rsplit(":", 1)[-1]
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(MOCK_SCRIPT), "--port", port_value],
                stdout=self._log_handle, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env, cwd=str(ROOT))
            info(f"已启动 Appium mock 子进程 (pid={self._process.pid})，"
                 f"日志: {SERVER_LOG}")

            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise AssertionError(
                        f"Appium mock 启动失败（退出码 {self._process.returncode}）——"
                        f"端口 {port_value} 可能被占用。日志尾部:\n{self._log_tail()}")
                if self._probe():
                    info(f"Appium Server 就绪: {self._server_url}")
                    return self._server_url
                time.sleep(0.05)
            raise AssertionError(
                f"{self._timeout}s 内 Appium Server 未就绪: {self._server_url}\n"
                f"日志尾部:\n{self._log_tail()}")
        except BaseException:
            self._terminate_mock()
            raise

    @keyword("Stop Appium Server")
    def stop_appium_server(self) -> None:
        """停止由本库启动的 mock 子进程（外部复用的真 Appium 不归本库管理）。

        回收策略：terminate → 等 5s → 仍存活则 kill。**测试 Teardown 必须调用**，
        否则残留进程会占着 4723 端口，下一个套件启动 mock 时失败。
        """
        process = self._process
        if process is None:
            info("没有由本库启动的 Appium 进程（外部 server 不处理）")
        self._terminate_mock()
        if process is not None:
            info(f"Appium mock 子进程已停止 (pid={process.pid}, "
                 f"exit={process.returncode})")

    # ==================================================================
    # 会话
    # ==================================================================
    @keyword("Create Session")
    def create_session(self, platform_name: str = "Android",
                       device_name: str = "VEHICLE2026AUTO",
                       app_package: str | None = None,
                       app_activity: str | None = None, **extra) -> str:
        """创建 Appium 会话（W3C capabilities），返回 sessionId。

        Arguments:
        - ``platform_name``：平台名（Android；mock 只支持 Android）
        - ``device_name``：设备名/序列号（对应 `adb devices` 的 serial）
        - ``app_package`` / ``app_activity``：要启动的应用（mock 会切到该界面；
          真机上等价于 Appium 启动 app）
        - ``**extra``：其余 capabilities 原样透传，如
          ``automationName=UiAutomator2    noReset=True``

        W3C 请求体格式：
            {"capabilities": {"alwaysMatch": {...}, "firstMatch": [{}]}}
        """
        capabilities = {"platformName": platform_name}
        if device_name:
            capabilities["deviceName"] = device_name
        if app_package:
            capabilities["appPackage"] = app_package
        if app_activity:
            capabilities["appActivity"] = app_activity
        capabilities.update(extra)
        payload = self._http("POST", "/session", {
            "capabilities": {"alwaysMatch": capabilities, "firstMatch": [{}]}})
        session_id = payload.get("sessionId") if isinstance(payload, dict) else None
        if not session_id:
            raise AssertionError(f"创建会话失败（响应里没有 sessionId）: {payload!r}")
        self._session_counter += 1
        info(f"Appium 会话已创建: {session_id}（capabilities={capabilities}）")
        return str(session_id)

    @keyword("Delete Session")
    def delete_session(self, session_id: str) -> None:
        """删除会话（DELETE /session/{id}）。之后再用该 session 操作会报
        invalid session id —— 这本身也是一条要测的边界。"""
        self._http("DELETE", f"/session/{session_id}")
        info(f"Appium 会话已删除: {session_id}")

    # ==================================================================
    # 元素查找
    # ==================================================================
    @keyword("Find Element")
    def find_element(self, session_id: str, using: str, value: str) -> str:
        """查找元素并返回 elementId，找不到抛错（no such element）。

        Arguments:
        - ``using``：定位策略
            * ``id``：resource-id，如 ``com.vehicle.navi:id/btn_start``
              （mock 也接受省略包名的 ``btn_start``）
            * ``accessibility id``：content-desc（无障碍描述）
            * ``xpath``：如 ``//*[@text='开始导航']``
              （mock 只实现"属性等值匹配"子集；真 Appium 支持完整 XPath 1.0）
        - ``value``：定位值

        真实项目定位策略选择：**resource-id 最稳**（开发预留测试 id）＞
        accessibility id（顺带做无障碍）＞ xpath（易碎，界面层级一变就挂）。
        """
        payload = self._http("POST", f"/session/{session_id}/element",
                             {"using": using, "value": value})
        element_id = None
        if isinstance(payload, dict):
            element_id = payload.get(ELEMENT_KEY) or payload.get("ELEMENT")
        if not element_id:
            raise AssertionError(
                f"查找元素响应异常（缺 W3C element key）: using={using} "
                f"value={value!r} payload={payload!r}")
        info(f"找到元素 [{using}={value}] → {element_id}")
        return str(element_id)

    @keyword("Element Should Exist")
    def element_should_exist(self, session_id: str, using: str, value: str) -> str:
        """断言元素存在（找到则返回 elementId，找不到让 Find Element 抛错）。"""
        element_id = self.find_element(session_id, using, value)
        info(f"元素存在 ✓: [{using}={value}]")
        return element_id

    @keyword("Element Should Not Exist")
    def element_should_not_exist(self, session_id: str, using: str,
                                 value: str) -> None:
        """断言元素**不存在**（负向用例：验证界面上没有不该出现的控件）。

        实现：调用 Find Element，如果它抛错（no such element）则说明不存在 → 通过；
        如果找到了反而要失败。注意不能用 `Wait Until Keyword Succeeds` 包
        Find Element —— 那个语义是"等它出现"，正好反了。
        """
        try:
            element_id = self.find_element(session_id, using, value)
        except AssertionError as exc:
            if "no such element" in str(exc):
                info(f"元素不存在 ✓: [{using}={value}]")
                return
            raise
        raise AssertionError(
            f"元素不应存在但被找到了: [{using}={value}] → {element_id}")

    # ==================================================================
    # 元素操作
    # ==================================================================
    @keyword("Click Element")
    def click_element(self, session_id: str, element_id: str) -> None:
        """点击元素（POST /element/{eid}/click）。

        mock 后端会把点击语义落盘到 `adb_device_state.json`：
        点桌面图标 = am start 启动应用；点"开始导航" = navi_active=1 ……
        所以"点击 → 界面/状态变化"可以真实断言（不是假点击）。
        """
        self._http("POST", f"/session/{session_id}/element/{element_id}/click")
        info(f"已点击元素: {element_id}")

    @keyword("Get Element Text")
    def get_element_text(self, session_id: str, element_id: str) -> str:
        """读取元素文本（GET /element/{eid}/text）。"""
        text = self._http("GET", f"/session/{session_id}/element/{element_id}/text")
        text = "" if text is None else str(text)
        info(f"元素文本: {text!r}")
        return text

    @keyword("Element Text Should Be")
    def element_text_should_be(self, session_id: str, element_id: str,
                               expected: str) -> str:
        """断言元素文本等于期望值，返回实际文本。"""
        actual = self.get_element_text(session_id, element_id)
        if actual != str(expected):
            raise AssertionError(
                f"元素文本不符: 期望 {expected!r}，实际 {actual!r}")
        info(f"元素文本正确: {actual!r} ✓")
        return actual

    # ==================================================================
    # W3C Actions：tap / swipe
    # ==================================================================
    @keyword("Tap Coordinate")
    def tap_coordinate(self, session_id: str, x, y) -> dict:
        """按坐标点击（W3C Actions：pointerMove → pointerDown → pointerUp）。

        API 21+ 的 uiautomator2 不再支持旧的 /touch/perform，**坐标级操作必须走
        W3C Actions**（真机与 mock 一致）。返回本次操作记录 dict：
        ``{"type": "tap", "x": 960, "y": 540}``（mock 的 /actions 响应会回带记录）。
        """
        body = {"actions": [{
            "type": "pointer", "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0,
                 "x": int(x), "y": int(y)},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ]}]}
        self._http("POST", f"/session/{session_id}/actions", body)
        record = {"type": "tap", "x": int(x), "y": int(y)}
        info(f"坐标点击 ({x}, {y}) ✓")
        return record

    @keyword("Swipe")
    def swipe(self, session_id: str, x1, y1, x2, y2, duration_ms=300) -> dict:
        """滑动（W3C Actions 简化版：起点 down → 移到终点 → up）。

        真实 Appium 的 swipe 也是用 Actions 拼出来的（或旧 /touch/perform，
        已废弃）。返回操作记录 dict。
        """
        body = {"actions": [{
            "type": "pointer", "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0,
                 "x": int(x1), "y": int(y1)},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": int(duration_ms),
                 "x": int(x2), "y": int(y2)},
                {"type": "pointerUp", "button": 0},
            ]}]}
        self._http("POST", f"/session/{session_id}/actions", body)
        record = {"type": "swipe", "x": int(x1), "y": int(y1),
                  "x2": int(x2), "y2": int(y2)}
        info(f"滑动 ({x1}, {y1}) → ({x2}, {y2})，{duration_ms}ms ✓")
        return record

    # ==================================================================
    # 页面源码 / 界面切换
    # ==================================================================
    @keyword("Get Page Source")
    def get_page_source(self, session_id: str) -> str:
        """获取当前页面源码（uiautomator XML），返回 XML 字符串。

        源码里包含每个控件的 text/resource-id/content-desc/bounds/clickable，
        以及根节点的 package/activity —— 这是"不点按也能断言界面状态"的基础。
        """
        source = self._http("GET", f"/session/{session_id}/source")
        source = "" if source is None else str(source)
        info(f"页面源码长度: {len(source)} 字符")
        return source

    @keyword("Page Source Should Contain")
    def page_source_should_contain(self, session_id: str, *expected) -> str:
        """断言页面源码包含一个或多个子串（界面状态断言的主力）。

        例：
            Page Source Should Contain    ${SESSION}    .MainActivity    音乐
        """
        if not expected:
            raise AssertionError("Page Source Should Contain 至少需要一个期望子串")
        source = self.get_page_source(session_id)
        missing = [str(item) for item in expected if str(item) not in source]
        if missing:
            raise AssertionError(
                f"页面源码缺少期望内容: {missing}（源码长度 {len(source)}，"
                f"头部: {source[:300]!r}）")
        info(f"页面源码包含 {[str(item) for item in expected]} ✓")
        return source

    @keyword("Switch To Package")
    def switch_to_package(self, session_id: str, package: str,
                          activity: str = "") -> str:
        """切换到指定包/Activity（等价 Appium 的 start_activity），返回当前 Activity。

        用途：不依赖"先回桌面再点图标"的导航路径，直接把被测界面拉起来
        （真实项目里跨应用场景常用 am start 拉起目标页面）。

        实现：POST /session/{id}/appium/device/start_activity，再取一次
        page source 断言 package（和 activity，若给定）确实出现。
        """
        body = {"appPackage": package}
        if activity:
            body["appActivity"] = activity
        self._http("POST", f"/session/{session_id}/appium/device/start_activity",
                   body)
        source = self.get_page_source(session_id)
        if str(package) not in source:
            raise AssertionError(
                f"切换界面失败：页面源码里没有包名 {package}")
        if activity and str(activity) not in source:
            raise AssertionError(
                f"切换界面失败：页面源码里没有 Activity {activity}")
        current = self.get_current_activity(session_id)
        info(f"已切换到 {current} ✓")
        return current

    @keyword("Get Current Activity")
    def get_current_activity(self, session_id: str) -> str:
        """获取当前前台 Activity（GET /appium/device/current_activity）。

        真实 Appium（uiautomator2 driver）提供同名端点；也可用
        ``adb shell dumpsys window | grep mCurrentFocus`` 等价获取。
        """
        activity = self._http(
            "GET", f"/session/{session_id}/appium/device/current_activity")
        activity = "" if activity is None else str(activity)
        info(f"当前 Activity: {activity}")
        return activity

    # ==================================================================
    # 截图
    # ==================================================================
    @keyword("Get Screenshot")
    def get_screenshot(self, session_id: str, output_path=None) -> str:
        """截图并保存为 PNG，返回文件路径（GET /screenshot → base64 PNG）。

        Arguments:
        - ``output_path``：保存路径（缺省 ``results/appium/screenshot.png``）

        本关键字同时校验：PNG 文件头、宽高 > 0、文件非空 —— 真正的"截图成功"
        不是"命令没报错"，而是"拿到了一张可解析的图"。
        """
        target = Path(str(output_path)) if output_path else DEFAULT_SCREENSHOT
        if not target.is_absolute():
            target = (ROOT / target).resolve()
        payload = self._http("GET", f"/session/{session_id}/screenshot")
        if not payload:
            raise AssertionError("截图响应为空（value 缺失）")
        try:
            png = base64.b64decode(str(payload))
        except Exception as exc:                       # noqa: BLE001
            raise AssertionError(f"截图 base64 解码失败: {exc}") from None
        if not png.startswith(PNG_SIGNATURE):
            raise AssertionError(f"截图不是合法 PNG（头部 {png[:8]!r}）")
        if len(png) < 100:
            raise AssertionError(f"截图文件过小（{len(png)} 字节）")
        width, height = struct.unpack(">II", png[16:24])   # IHDR 里的宽高
        if width <= 0 or height <= 0:
            raise AssertionError(f"截图尺寸非法: {width}x{height}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        info(f"截图已保存: {target}（{len(png)} 字节，{width}x{height}）")
        return str(target)

    # ==================================================================
    # mock 调试端点
    # ==================================================================
    @keyword("Get Recorded Actions")
    def get_recorded_actions(self, session_id: str) -> list:
        """读取 mock server 记录的操作列表（**mock 专用调试端点**）。

        Real Appium 没有这个端点：真实项目里验证"点击事件真的发生了"靠
        logcat（`adb logcat -d | grep Input`）、页面变化或业务状态断言。
        本关键字存在是为了让教学用例能直接证明"tap 被服务端收到了"。
        """
        actions = self._http(
            "GET", f"/session/{session_id}/appium/mock/actions")
        actions = list(actions or [])
        info(f"mock 已记录 {len(actions)} 个操作: "
             f"{[item.get('type') for item in actions]}")
        return actions
