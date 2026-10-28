"""
AdbLibrary.py — Robot Framework ADB / Android 关键字库
=======================================================

封装 adb 命令行（本项目用 simulators/adb_sim.py 模拟一台安卓车机）。

为什么用 subprocess 封装 adb 而不是调 Python 库？
    —— 因为 adb 命令是车载 Android 测试的行业标准接口。
    用 subprocess 封装后，将来换真机/模拟器时，只要把
    ADB_PREFIX 从 "python adb_sim.py" 换成 "adb"，测试代码零改动。
    这正是"测试资产可移植"的设计。

覆盖的高频场景：
    设备管理：devices / getprop / reboot
    应用管理：install / uninstall / 启动 Activity / force-stop
    UI 交互：input tap / input swipe / keyevent
    状态查询：dumpsys battery / dumpsys window / wm size
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))
from adb_sim import load_state, log as adb_log, save_state  # noqa: E402


def resolve_adb_prefix(env: dict | None = None) -> list[str]:
    """解析 adb 命令前缀（纯函数，便于单元测试）。

    默认用项目内的 ``simulators/adb_sim.py`` 模拟车机；
    **设置环境变量 ``ADB_BIN`` 即可切到真实 adb**（测试代码零改动）::

        # PowerShell（单设备）
        $env:ADB_BIN="adb"; python run_tests.py
        # 多设备时可通过 -s 指定序列号（会按空格拆分）
        $env:ADB_BIN="adb -s 1234567890ABCDEF"

    也要以装在有空格路径里时，可直接写全路径并加引号（shlex 解析）。
    """
    e = os.environ if env is None else env
    raw = (e.get("ADB_BIN") or "").strip()
    if raw:
        return shlex.split(raw)
    return [
        sys.executable,
        str(Path(__file__).parent.parent / "simulators" / "adb_sim.py"),
    ]


# adb 命令前缀：默认指向模拟器；接真机时设置环境变量 ADB_BIN（见上）
ADB_PREFIX = resolve_adb_prefix()


class AdbLibrary:
    """ADB / Android 设备操作关键字集合"""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self) -> None:
        console(f"[AdbLibrary] adb 命令前缀: {' '.join(ADB_PREFIX)}")

    # ------------------------------------------------------------------ 内部
    def _run_adb(self, *args: str, check: bool = False) -> str:
        cmd = ADB_PREFIX + list(args)
        # stdin=DEVNULL 是必须的：Windows 下子进程若继承测试框架的 stdin
        # 管道句柄（可能被其他仿真进程持有），会导致进程挂起。
        # 自动化调外部命令的通用最佳实践：显式指定 stdin。
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=15)
        if check and proc.returncode != 0:
            raise AssertionError(
                f"adb 命令失败: {' '.join(args)}\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        info(f"$ {' '.join(args)}\n{proc.stdout.strip()}")
        return proc.stdout

    # ------------------------------------------------------------------ 设备
    @keyword("Adb Devices")
    def adb_devices(self) -> str:
        """列出连接的设备（adb devices）"""
        return self._run_adb("devices")

    @keyword("Device Should Be Connected")
    def device_should_be_connected(self, serial: str = "") -> None:
        """断言有设备在线（可指定序列号）"""
        out = self.adb_devices()
        lines = [ln for ln in out.splitlines()[1:] if ln.strip()]
        if not lines:
            raise AssertionError("没有已连接的设备")
        if serial and serial not in out:
            raise AssertionError(f"设备 {serial} 不在列表:\n{out}")
        info(f"设备在线 ✓: {lines}")

    @keyword("Get Prop")
    def get_prop(self, key: str) -> str:
        """读取系统属性（adb shell getprop <key>）"""
        return self._run_adb("shell", "getprop", key).strip()

    # ------------------------------------------------------------------ 应用
    @keyword("Install APK")
    def install_apk(self, apk_name: str) -> None:
        """安装应用（adb install -r <apk>，-r 覆盖安装，测试可重复）。
        模拟器上 apk 名即包名。"""
        out = self._run_adb("install", "-r", apk_name)
        if "Success" not in out:
            raise AssertionError(f"安装失败: {out}")

    @keyword("Uninstall Package")
    def uninstall_package(self, package: str) -> None:
        """卸载应用（adb uninstall <package>）"""
        self._run_adb("uninstall", package)

    @keyword("Start Activity")
    def start_activity(self, component: str) -> None:
        """启动 Activity（adb shell am start -n <pkg>/<activity>）"""
        out = self._run_adb("shell", "am", "start", "-n", component)
        if "Error" in out:
            raise AssertionError(f"启动失败: {out}")

    @keyword("Force Stop Package")
    def force_stop_package(self, package: str) -> None:
        """强杀应用（adb shell am force-stop <package>）"""
        self._run_adb("shell", "am", "force-stop", package)

    # ------------------------------------------------------------------ UI
    @keyword("Tap")
    def tap(self, x: int, y: int) -> None:
        """点击屏幕坐标（adb shell input tap x y）"""
        self._run_adb("shell", "input", "tap", str(x), str(y))

    @keyword("Swipe")
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """滑动（adb shell input swipe）"""
        self._run_adb("shell", "input", "swipe",
                      str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    @keyword("Send Keyevent")
    def send_keyevent(self, keycode: int) -> None:
        """发送按键（adb shell input keyevent <code>），如 26=电源 3=HOME"""
        self._run_adb("shell", "input", "keyevent", str(keycode))

    # ------------------------------------------------------------------ 状态断言
    @keyword("Current Focus Should Contain")
    def current_focus_should_contain(self, package: str) -> None:
        """断言当前前台界面包含指定包名（dumpsys window 解析）"""
        out = self._run_adb("shell", "dumpsys", "window")
        if package not in out:
            raise AssertionError(f"当前界面不包含 {package}:\n{out}")
        info(f"当前前台: {package} ✓")

    @keyword("Battery Level Should Be Greater Than")
    def battery_level_should_be_greater_than(self, level: int) -> None:
        """断言电量大于指定值（dumpsys battery 解析）"""
        out = self._run_adb("shell", "dumpsys", "battery")
        current = None
        for line in out.splitlines():
            if "level:" in line:
                current = int(line.split(":")[1].strip())
        if current is None or current <= int(level):
            raise AssertionError(f"电量 {current} 不大于 {level}")
        info(f"电量 {current}% ✓")

    # ------------------------------------------------------------------ 车机侧状态（模拟 GUI 操作）
    @keyword("Set Headunit Navi State")
    def set_headunit_navi_state(self, active: int, distance_m: int,
                                street_index: int, turn: int = 3) -> None:
        """模拟在安卓车机上操作导航：写 ivi 状态（等价点"开始导航"）。

        车机 GUI（android_gui.py）轮询到后会广播 CAN 0x500，仪表随即显示。
        参数：
            active          1=导航中 0=未导航
            distance_m      剩余距离（米）
            street_index    街道名索引（0 起，见 ivi_data.STREETS）
            turn            转向提示 0无 1左 2右 3直行 7到达
        """
        state = load_state()
        state["ivi"].update(navi_active=int(active),
                            navi_distance=int(distance_m),
                            street_index=int(street_index),
                            navi_turn=int(turn))
        adb_log(state, "IVI", f"navi set: active={active} "
                              f"dist={distance_m} street={street_index}")
        save_state(state)
        info(f"车机导航状态已设置: 距离 {distance_m}m")

    @keyword("Set Headunit Media State")
    def set_headunit_media_state(self, track_index: int, status: int = 1,
                                 volume: int | None = None) -> None:
        """模拟在安卓车机上操作媒体：写 ivi 状态（等价点歌/播放）。

        参数：
            track_index    曲目索引（0 起，见 ivi_data.TRACKS）
            status         0=停止 1=播放 2=暂停
            volume         音量 0-100（None 表示不变）
        """
        state = load_state()
        state["ivi"].update(track_index=int(track_index),
                            media_status=int(status))
        if volume is not None:
            state["ivi"]["volume"] = int(volume)
        adb_log(state, "IVI", f"media set: track={track_index} "
                              f"status={status}")
        save_state(state)
        info(f"车机媒体状态已设置: 曲目索引 {track_index}")

    @keyword("Set Headunit Hvac State")
    def set_headunit_hvac_state(self, temp: int, ac_on: int = 1,
                                fan_level: int | None = None) -> None:
        """模拟在安卓车机上调空调：写 ivi 状态。"""
        state = load_state()
        state["ivi"].update(hvac_temp=int(temp), ac_on=int(ac_on))
        if fan_level is not None:
            state["ivi"]["fan_level"] = int(fan_level)
        adb_log(state, "IVI", f"hvac set: temp={temp} ac={ac_on}")
        save_state(state)
        info(f"车机空调状态已设置: {temp}℃ AC={ac_on}")

    @keyword("Get Headunit IVI State")
    def get_headunit_ivi_state(self) -> dict:
        """读取车机 ivi 状态字典（断言两块屏一致时用）"""
        return load_state()["ivi"]

    # ------------------------------------------------------------------ 车机 → CAN 广播
    @keyword("Broadcast Headunit State To Bus")
    def broadcast_headunit_state_to_bus(self) -> None:
        """把车机当前 IVI 状态立即广播到 CAN 总线（0x400/0x500）。

        为什么需要它：正常架构里由 android_gui（车机常驻进程）周期广播，
        但自动化测试不能依赖"子进程定时器还活着"这种隐式前提。
        用例里显式调用本关键字，保证"状态已写 → 总线已推 → 仪表已显示"
        的确定性链路（测试设计原则：消除隐式时间依赖）。
        """
        global _IVI_BACKEND
        if _IVI_BACKEND is None:
            from ivi_backend import IviBackend
            _IVI_BACKEND = IviBackend(node_name="RF-IVI")
        _IVI_BACKEND.broadcast_can()
        info("车机 IVI 状态已广播到 CAN（0x400/0x500）")

    @keyword("Close Headunit Backend")
    def close_headunit_backend(self) -> None:
        """关闭模块级 IVI 广播器持有的虚拟总线（套件结束时调用）。

        为什么需要：`Broadcast Headunit State To Bus` 会懒加载一个进程级单例
        `IviBackend`，它持有一个 VirtualCanBus。过去从不释放 → 测试进程结束
        时报 "UdpMulticastBus was not properly shut down"（句柄泄漏）。
        """
        global _IVI_BACKEND
        if _IVI_BACKEND is None:
            return
        try:
            _IVI_BACKEND.shutdown()
            info("车机 IVI 广播器总线已释放")
        finally:
            _IVI_BACKEND = None


# 模块级单例：RF 进程内的 IVI 广播器（lazy 初始化）
_IVI_BACKEND = None
