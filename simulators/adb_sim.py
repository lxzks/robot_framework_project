"""
adb_sim.py — Android 设备模拟器（adb 命令行 mock）
===================================================

解决学习痛点：想学车载 Android（Android Automotive）自动化，
但没有安卓真机、也不想装几个 G 的 Android Studio 模拟器。

本文件模拟一台"安卓车机"，把 adb 命令行的常用子命令全部实现，
状态持久化在 adb_device_state.json（与 android_gui.py 共享，
GUI 上的点击等价于 am start 等命令）。

用法（和真 adb 几乎一样）：
    python adb_sim.py devices
    python adb_sim.py shell getprop ro.product.model
    python adb_sim.py shell dumpsys battery
    python adb_sim.py install media.apk
    python adb_sim.py shell am start -n com.vehicle.media/.MainActivity
    python adb_sim.py shell dumpsys window
    python adb_sim.py logcat -d

内置"车机应用"（模拟出厂预装）：
    com.vehicle.launcher   车机桌面（开机默认界面）
    com.vehicle.media      音乐媒体
    com.vehicle.settings   车机设置
    com.vehicle.navi       导航（需 install 后出现）
    com.vehicle.phone      电话
    com.vehicle.hvac       空调

Robot Framework 的 AdbLibrary 会通过 subprocess 调用本脚本，
接口与真实 adb 完全一致 —— 将来换真机时，只需把命令前缀从
"python adb_sim.py" 换成 "adb"，测试代码一行不用改。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

STATE_FILE = Path(__file__).parent / "adb_device_state.json"

# IVI 座舱状态段：android_gui.py（车机 GUI）与仪表盘共享。
# GUI 上的每一次操作都改这里，同时由 GUI 广播到 CAN 总线（0x400/0x500）。
DEFAULT_IVI = {
    "media_status": 0,        # 0 stop 1 play 2 pause
    "track_index": 0,
    "volume": 40,
    "call_status": 0,         # 0 none 1 incoming 2 active
    "caller_index": 0,
    "navi_active": 0,
    "navi_turn": 0,
    "navi_distance": 0,       # m
    "street_index": 0,
    "ac_on": 1,
    "hvac_temp": 24,
    "fan_level": 3,
    "seat_heat": 0,
    "recirculate": 0,
    "defrost": 0,
}

DEFAULT_STATE = {
    "device": {
        "serial": "VEHICLE2026AUTO",
        "model": "Visteon Cockpit P7",
        "brand": "Visteon",
        "android_version": "14",
        "api_level": 34,
        "build": "VWH.2026.10",
    },
    "props": {
        "ro.product.model": "Visteon Cockpit P7",
        "ro.product.brand": "Visteon",
        "ro.build.version.release": "14",
        "ro.build.version.sdk": "34",
        "ro.car.vehicle_type": "passenger",
        "persist.sys.timezone": "Asia/Shanghai",
    },
    "battery": {"level": 78, "status": "discharging", "temperature": 285},
    "packages": [
        {"name": "com.vehicle.launcher", "version": "1.0.0", "system": True},
        {"name": "com.vehicle.media", "version": "2.3.1", "system": True},
        {"name": "com.vehicle.navi", "version": "3.0.5", "system": True},
        {"name": "com.vehicle.phone", "version": "1.4.0", "system": True},
        {"name": "com.vehicle.hvac", "version": "1.1.2", "system": True},
        {"name": "com.vehicle.settings", "version": "4.1.0", "system": True},
    ],
    "current_focus": "com.vehicle.launcher/.HomeActivity",
    "ivi": dict(DEFAULT_IVI),
    "logs": [],
}


def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # 兼容旧状态文件：补齐缺失的段
        for key, value in DEFAULT_STATE.items():
            state.setdefault(key, value)
        for key, value in DEFAULT_IVI.items():
            state["ivi"].setdefault(key, value)
        return state
    return json.loads(json.dumps(DEFAULT_STATE))  # deep copy


def _cleanup_stale_tmp() -> None:
    """清理被强杀留下的 tmp（原因见 dashboard.py 同名函数）"""
    for stale in Path(__file__).parent.glob("tmp*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass


def save_state(state: dict) -> None:
    """原子写：先写临时文件再替换，防止 GUI/命令行动态读到写一半的 JSON。

    Windows 特有竞态：os.replace 需要目标文件的 DELETE 权限，而读取方
    open() 默认不共享 DELETE —— 写入方让步重试（真实工程做法）。
    """
    state["ivi"].update({k: v for k, v in DEFAULT_IVI.items()
                         if k not in state["ivi"]})
    for attempt in range(8):
        fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent),
                                   suffix=".tmp", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
            return
        except PermissionError:
            Path(tmp).unlink(missing_ok=True)
            time.sleep(0.02 * (attempt + 1))
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise


def log(state: dict, tag: str, msg: str) -> None:
    entry = {"ts": time.strftime("%m-%d %H:%M:%S"), "tag": tag, "msg": msg}
    state["logs"].append(entry)
    state["logs"] = state["logs"][-100:]


# ---------------------------------------------------------------------------
# 命令分发
# ---------------------------------------------------------------------------
def cmd_devices(_state: dict) -> str:
    return (
        "List of devices attached\n"
        f"{_state['device']['serial']}\tdevice\n"
    )


def cmd_shell(state: dict, args: list[str]) -> str:
    if not args:
        return ""
    sub = args[0]
    if sub == "getprop":
        if len(args) == 1:
            return "".join(f"[{k}]: [{v}]\n" for k, v in state["props"].items())
        key = args[1]
        return state["props"].get(key, "")
    if sub == "setprop":
        state["props"][args[1]] = args[2]
        save_state(state)
        return ""
    if sub == "dumpsys":
        if len(args) > 1 and args[1] == "battery":
            b = state["battery"]
            return (f"Current Battery Service state:\n"
                    f"  level: {b['level']}\n  status: {b['status']}\n"
                    f"  temperature: {b['temperature']}\n")
        if len(args) > 1 and args[1] == "window":
            return (f"  mCurrentFocus=Window{{"
                    f"{state['current_focus']}}}\n"
                    f"  mFocusedApp=ActivityRecord{{"
                    f"{state['current_focus']}}}\n")
        return "DUMP OF SERVICE: " + " ".join(args[1:]) + "\n"
    if sub == "am" and len(args) >= 2:
        if args[1] == "start":
            # am start -n <pkg>/<activity>
            target = args[-1]
            names = [p["name"] for p in state["packages"]]
            pkg = target.split("/")[0]
            if pkg in names:
                state["current_focus"] = target
                log(state, "ActivityManager", f"Starting: {target}")
                save_state(state)
                return (f"Starting: Intent {{ cmp={target} }}\n")
            return (f"Error: Activity not started, unable to resolve Intent {{ "
                    f"comp={target} }}\n")
        if args[1] == "force-stop":
            log(state, "ActivityManager", f"Force stopping {args[2]}")
            save_state(state)
            return ""
        if args[1] == "broadcast":
            log(state, "ActivityManager", f"Broadcast: {' '.join(args[2:])}")
            save_state(state)
            return "Broadcast completed: result=0\n"
    if sub == "input":
        # input tap x y / input keyevent <code> / input swipe ...
        log(state, "Input", f"input {' '.join(args[1:])}")
        # 真实设备语义：HOME(3) 回桌面，BACK(4) 返回
        if len(args) >= 3 and args[1] == "keyevent":
            if args[2] == "3":
                state["current_focus"] = "com.vehicle.launcher/.HomeActivity"
        save_state(state)
        return ""
    if sub == "wm":
        if args[1] == "size":
            return "Physical size: 1920x1080\n"
        if args[1] == "density":
            return "Physical density: 240\n"
    if sub == "settings":
        # settings get/put system screen_brightness 128
        if args[1] == "get":
            return state["props"].get("persist.sys." + args[3], "null") \
                if len(args) > 3 else "null"
    if sub == "reboot":
        log(state, "System", "reboot")
        state["current_focus"] = "com.vehicle.launcher/.HomeActivity"
        save_state(state)
        return ""
    return f"adb_sim: 未实现的 shell 命令: {' '.join(args)}\n"


def cmd_install(state: dict, args: list[str]) -> str:
    """install [-r] <apk>：-r 为覆盖安装（真实 adb 语义，测试可重复）"""
    reinstall = False
    apks = []
    for a in args:
        if a == "-r":
            reinstall = True
        else:
            apks.append(a)
    apk = apks[0]
    pkg = apk.replace(".apk", "")
    existing = any(p["name"] == pkg for p in state["packages"])
    if existing and not reinstall:
        return "Failure [INSTALL_FAILED_ALREADY_EXISTS]\n"
    if existing:
        for p in state["packages"]:
            if p["name"] == pkg:
                p["version"] = "1.0.1"
    else:
        state["packages"].append({"name": pkg, "version": "1.0.0", "system": False})
    log(state, "PackageManager", f"Installed {pkg}")
    save_state(state)
    return "Success\n"


def cmd_uninstall(state: dict, args: list[str]) -> str:
    pkg = args[0]
    state["packages"] = [p for p in state["packages"] if p["name"] != pkg]
    if state["current_focus"].startswith(pkg):
        state["current_focus"] = "com.vehicle.launcher/.HomeActivity"
    log(state, "PackageManager", f"Uninstalled {pkg}")
    save_state(state)
    return "Success\n"


def cmd_logcat(state: dict, args: list[str]) -> str:
    lines = [f"{e['ts']} {e['tag']}: {e['msg']}" for e in state["logs"]]
    if args and args[0] == "-c":
        state["logs"] = []
        save_state(state)
        return ""
    if not lines:
        return "（无日志，先执行一些操作试试）\n"
    return "\n".join(lines) + "\n"


def cmd_pull(_state: dict, args: list[str]) -> str:
    return f"1 file pulled, {len(args[0])} bytes. (模拟)\n"


def cmd_push(_state: dict, args: list[str]) -> str:
    return f"1 file pushed. (模拟)\n"


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    state = load_state()
    cmd, rest = argv[0], argv[1:]
    out = {
        "devices": cmd_devices,
        "shell": lambda s: cmd_shell(s, rest),
        "install": lambda s: cmd_install(s, rest),
        "uninstall": lambda s: cmd_uninstall(s, rest),
        "logcat": lambda s: cmd_logcat(s, rest),
        "pull": cmd_pull,
        "push": cmd_push,
    }.get(cmd)
    if cmd is None:
        print(f"adb_sim: 未知命令 {cmd}")
        sys.exit(1)
    print(out(state))


if __name__ == "__main__":
    main()
