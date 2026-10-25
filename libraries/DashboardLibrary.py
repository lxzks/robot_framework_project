"""
DashboardLibrary.py — Robot Framework 仪表盘(HMI)断言关键字库
================================================================

读取 headless 仪表盘写入的 dashboard_state.json，验证"屏幕上显示了什么"。

核心思想（HMI 自动化测试的本质）：
    真实车上验证仪表盘靠眼睛；自动化测试必须把"显示状态"变成可断言的
    数据。headless 仪表盘每 100ms 把当前显示快照写入 JSON 文件，
    本库读取该文件做断言 —— 这就是量产 HMI 测试工具（如 seeTest、
    Testin）的工作原理：把 UI 状态变成数据契约。

用法（需先启动 dashboard.py --headless）：
    ${speed}=    Get Displayed Speed
    Should Be Equal As Numbers    ${speed}    80
    Telltale CoolantTempHigh Should Be Lit
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

STATE_FILE = Path(__file__).parent.parent / "simulators" / "dashboard_state.json"

# 状态文件 freshness 阈值：超过 2 秒没更新认为仪表盘卡死/退出
MAX_STALE_SECONDS = 2.0


class DashboardLibrary:
    """仪表盘显示状态断言关键字集合"""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, state_file: str = str(STATE_FILE)) -> None:
        self._state_file = Path(state_file)
        console(f"[DashboardLibrary] 监控状态文件: {self._state_file.name}")

    # ------------------------------------------------------------------ 内部
    def _read_state(self) -> dict:
        if not self._state_file.exists():
            raise AssertionError(
                f"状态文件 {self._state_file} 不存在 —— "
                "请先启动: python simulators/dashboard.py --headless")
        # 防御性重试：高频读写共享文件时，读瞬间可能撞上
        # ① 半写文件（JSONDecodeError）② Windows 文件锁（PermissionError）。
        # 写入方已做原子写+重试，读取方也重试，双保险。
        import json as _json
        last_err: Exception | None = None
        for _ in range(5):
            try:
                return _json.loads(self._state_file.read_text(encoding="utf-8"))
            except (_json.JSONDecodeError, PermissionError, OSError) as exc:
                last_err = exc
                time.sleep(0.02)
        raise AssertionError(f"状态文件读取失败（可能正被写入）: {last_err}")

    def _fresh_state(self, allow_stale_retry: bool = False) -> dict:
        """读取状态并检查新鲜度。

        allow_stale_retry=True 时（断言类关键字的轮询语义），
        读到过期状态不抛错、返回原始数据，由调用方的轮询循环继续等 ——
        这样即使偶发读到别的写入者的旧快照，也不会炸断整个轮询。
        """
        state = self._read_state()
        age = time.time() - state.get("updated_at", 0)
        if age > MAX_STALE_SECONDS and not allow_stale_retry:
            raise AssertionError(
                f"仪表盘状态已过期 {age:.1f}s（阈值 {MAX_STALE_SECONDS}s），"
                "headless 仪表盘可能已退出")
        return state

    # ------------------------------------------------------------------ 就绪
    @keyword("Dashboard Should Be Ready")
    def dashboard_should_be_ready(self, max_age: float = 1.5) -> None:
        """断言仪表盘已就绪：状态文件存在且新鲜（刚写过）。

        Suite Setup 里等待用 —— 注意"文件存在"不等于"就绪"，
        headless 仪表盘收到第一帧 CAN 报文前写出的快照是初始值。
        """
        state = self._read_state()
        age = time.time() - state.get("updated_at", 0)
        if age > float(max_age):
            raise AssertionError(f"仪表盘未就绪：状态 {age:.1f}s 未更新")
        info(f"仪表盘就绪（{age:.2f}s 前刚更新）✓")

    # ------------------------------------------------------------------ 读取
    @keyword("Get Displayed Speed")
    def get_displayed_speed(self) -> float:
        """获取仪表显示的车速（km/h）"""
        return self._fresh_state()["speed_kmh"]

    @keyword("Get Displayed Gear")
    def get_displayed_gear(self) -> str:
        """获取仪表显示的档位（P/R/N/D/S/L）"""
        return self._fresh_state()["gear"]

    @keyword("Get Displayed Coolant")
    def get_displayed_coolant(self) -> float:
        """获取仪表显示的水温（℃）"""
        return self._fresh_state()["coolant_c"]

    @keyword("Get Displayed Odometer")
    def get_displayed_odometer(self) -> int:
        """获取仪表显示的里程（km）"""
        return self._fresh_state()["odometer_km"]

    @keyword("Get Lit Telltales")
    def get_lit_telltales(self) -> list:
        """获取当前点亮的警告灯列表"""
        return self._fresh_state()["telltales_lit"]

    # ------------------------------------------------------------------ IVI 座舱信息
    @keyword("Get Displayed Media Track")
    def get_displayed_media_track(self) -> str:
        """获取仪表显示媒体界面的当前歌名"""
        return self._fresh_state()["media"]["track"]

    @keyword("Get Displayed Media Artist")
    def get_displayed_media_artist(self) -> str:
        """获取仪表显示媒体界面的歌手"""
        return self._fresh_state()["media"]["artist"]

    @keyword("Get Displayed Navi Turn")
    def get_displayed_navi_turn(self) -> str:
        """获取仪表显示导航界面的转向提示"""
        return self._fresh_state()["navi"]["turn"]

    @keyword("Get Displayed Navi Distance")
    def get_displayed_navi_distance(self) -> int:
        """获取仪表显示导航界面的剩余距离（米）"""
        return self._fresh_state()["navi"]["distance_m"]

    @keyword("Get Displayed Hvac Temp")
    def get_displayed_hvac_temp(self) -> int:
        """获取仪表显示的空调设定温度（℃）"""
        return self._fresh_state()["hvac"]["set_temp_c"]

    @keyword("Get Displayed Trip Km")
    def get_displayed_trip_km(self) -> float:
        """获取行程电脑的小计里程（km）"""
        return self._fresh_state()["trip"]["km"]

    @keyword("Media Track Should Be")
    def media_track_should_be(self, name: str, timeout: float = 5.0) -> None:
        """断言仪表媒体界面显示指定歌名（轮询等待）"""
        deadline = time.monotonic() + float(timeout)
        last = ""
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["media"]["track"]
            if last == name:
                info(f"媒体显示 {name} ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内媒体未显示 {name}（当前: {last}）")

    @keyword("Navi Turn Should Be One Of")
    def navi_turn_should_be_one_of(self, *turns, timeout: float = 5.0) -> None:
        """断言仪表导航转向提示属于指定集合（车机会周期重算转向，不能精确断言）"""
        allowed = set(turns)
        deadline = time.monotonic() + float(timeout)
        last = ""
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["navi"]["turn"]
            if last in allowed:
                info(f"导航转向 {last} ✓")
                return
            time.sleep(0.1)
        raise AssertionError(
            f"{timeout}s 内导航转向 {last} 不在 {allowed} 中")

    @keyword("Navi Distance Should Be Below")
    def navi_distance_should_be_below(self, max_m: int,
                                      timeout: float = 5.0) -> None:
        """断言仪表导航距离已更新且低于指定值（轮询等待）。

        注意：不能用 Wait Until Keyword Succeeds 包 Get Displayed
        Navi Distance —— 距离为 0 时该关键字不抛错，"等待"会立即结束。
        带值域判断的等待必须做成独立轮询关键字。
        """
        deadline = time.monotonic() + float(timeout)
        last = -1
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["navi"]["distance_m"]
            if 0 < last <= int(max_m):
                info(f"导航距离 {last}m（< {max_m}m）✓")
                return
            time.sleep(0.1)
        raise AssertionError(
            f"{timeout}s 内导航距离 {last} 未进入 (0, {max_m}]")

    @keyword("Hvac Temp Should Be")
    def hvac_temp_should_be(self, temp: int, timeout: float = 5.0) -> None:
        """断言仪表显示的空调设定温度"""
        deadline = time.monotonic() + float(timeout)
        last = -1
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["hvac"]["set_temp_c"]
            if last == int(temp):
                info(f"空调温度 {temp}℃ ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内空调温度未显示 {temp}℃（当前: {last}）")

    @keyword("Trip Should Be Accumulating")
    def trip_should_be_accumulating(self, timeout: float = 10.0) -> None:
        """断言行程电脑在小计里程累计（车辆行驶中）"""
        deadline = time.monotonic() + float(timeout)
        last = -1.0
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["trip"]["km"]
            if last > 0:
                info(f"小计里程已累计 {last} km ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内小计里程仍为 {last}")

    # ------------------------------------------------------------------ 断言
    @keyword("Telltale Should Be Lit")
    def telltale_should_be_lit(self, name: str, timeout: float = 5.0) -> None:
        """断言指定警告灯在 timeout 秒内点亮（轮询等待）"""
        deadline = time.monotonic() + float(timeout)
        last: list = []
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["telltales_lit"]
            if name in last:
                info(f"警告灯 {name} 已点亮 ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内 {name} 未点亮（当前点亮: {last}）")

    @keyword("Telltale Should Be Off")
    def telltale_should_be_off(self, name: str, timeout: float = 5.0) -> None:
        """断言指定警告灯在 timeout 秒内熄灭"""
        deadline = time.monotonic() + float(timeout)
        last: list = []
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["telltales_lit"]
            if name not in last:
                info(f"警告灯 {name} 已熄灭 ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内 {name} 未熄灭（当前点亮: {last}）")

    @keyword("All Telltales Should Be Lit")
    def all_telltales_should_be_lit(self, timeout: float = 5.0) -> None:
        """断言所有 12 个警告灯全亮（灯光测试例程的验证点）"""
        expected = {
            "MilWarning", "OilPressureLow", "CoolantTempHigh", "BatteryLow",
            "AbsFault", "AirbagFault", "SeatbeltWarning", "TurnLeft",
            "TurnRight", "HighBeam", "LowBeam", "ParkBrake",
        }
        deadline = time.monotonic() + float(timeout)
        last: list = []
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["telltales_lit"]
            if expected.issubset(set(last)):
                info("全部 12 个警告灯点亮 ✓")
                return
            time.sleep(0.1)
        missing = expected - set(last)
        raise AssertionError(f"灯光测试未全部点亮，缺失: {missing}")

    @keyword("All Telltales Should Be Off")
    def all_telltales_should_be_off(self, timeout: float = 8.0) -> None:
        """断言所有警告灯熄灭（灯光测试例程 2 秒后恢复）"""
        deadline = time.monotonic() + float(timeout)
        last: list = []
        while time.monotonic() < deadline:
            last = self._fresh_state(allow_stale_retry=True)["telltales_lit"]
            if not last:
                info("全部警告灯已熄灭 ✓")
                return
            time.sleep(0.1)
        raise AssertionError(f"{timeout}s 内仍有灯亮着: {last}")
