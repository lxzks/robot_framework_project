"""
start_cockpit.py — 一键启动完整座舱（三个进程）
================================================

一条命令拉起整个仿真座舱：

    python start_cockpit.py                       # 默认：车速0 水温90
    python start_cockpit.py --speed 60            # 车速 60
    python start_cockpit.py --speed 60 --coolant 125   # 直接触发水温故障

启动的三个进程（各自独立窗口）：
    1. ecu_sim.py      动力域 ECU（车辆本体，无窗口，控制台在日志里）
    2. dashboard.py    座舱仪表 GUI（7 个界面，tab 切换）
    3. android_gui.py  安卓车机 GUI（6 个界面，点图标=am start）

三者通过虚拟 CAN 总线（UDP 组播）实时联动：
    车机点歌/开导航/调空调 ──CAN 0x400/0x500──► 仪表媒体/导航界面同步显示
    ECU 水温超限 ──CAN 0x300──► 仪表水温报警灯亮

停止：在本窗口按 Ctrl+C，三个进程会全部关闭。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
SIM = ROOT / "simulators"


def find_python() -> str:
    """按优先级找 Python 解释器：项目 .venv → sys.executable"""
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",       # Windows 项目 venv
        ROOT / ".venv" / "bin" / "python",               # Linux/macOS
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def main() -> None:
    parser = argparse.ArgumentParser(description="一键启动座舱仿真")
    parser.add_argument("--speed", type=float, default=0.0,
                        help="ECU 初始目标车速（km/h）")
    parser.add_argument("--coolant", type=float, default=90.0,
                        help="ECU 初始目标水温（℃），>120 触发水温故障")
    args = parser.parse_args()

    py = find_python()
    print(f"Python: {py}")
    print("=" * 62)
    print(" 正在启动座舱仿真（三个进程）...")
    print("  1. 动力域 ECU（ecu_sim）—— 车辆本体（独立窗口，可敲命令）")
    print("  2. 座舱仪表（dashboard）—— 7 界面，底部 tab 切换")
    print("  3. 安卓车机（android_gui）—— 桌面/媒体/导航/电话/空调")
    print("=" * 62)
    print(" 联动演示：")
    print("  · 在安卓车机点《晴天》→ 切到仪表'媒体' tab 看同步显示")
    print("  · 在安卓车机点'开始导航'→ 切到仪表'导航' tab 看转向/距离")
    print("  · 在安卓车机调空调温度 → 仪表'车辆信息'页看同步")
    print("  · 水温超过 120℃ 时仪表水温灯亮（--coolant 125 可直接触发）")
    print()
    print(" ECU 控制台可用命令（在 ECU 那个窗口里敲）：")
    print("   status | dtc | speed 80 | coolant 125 | inject P0135 | unject P0135 | quit")
    print("=" * 62)
    print(" 停止：在本窗口按 Ctrl+C（三个进程一起关）")
    print()

    # stdin=DEVNULL：防止仿真进程持有本窗口 stdin 导致后续操作异常
    # Windows 上每个进程分配独立控制台窗口，便于观察日志/交互；
    # Linux/macOS 就在当前终端跑
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

    # ECU 需要真实 stdin（它有交互控制台：coolant/dt/inject 等命令），
    # 所以 stdin 不设 DEVNULL —— 独立控制台窗口里可以敲命令玩故障注入
    procs = [
        subprocess.Popen(
            [py, str(SIM / "ecu_sim.py"),
             "--speed", str(args.speed), "--coolant", str(args.coolant)],
            creationflags=creationflags),
        subprocess.Popen(
            [py, str(SIM / "dashboard.py")],
            stdin=subprocess.DEVNULL, creationflags=creationflags),
        subprocess.Popen(
            [py, str(SIM / "android_gui.py")],
            stdin=subprocess.DEVNULL, creationflags=creationflags),
    ]
    names = ["ecu_sim", "dashboard", "android_gui"]
    for name, p in zip(names, procs):
        print(f"  [启动] {name} (PID {p.pid})")

    try:
        # 主线程等待：任一进程退出则全部结束（座舱是一个整体）
        while True:
            for name, p in zip(names, procs):
                if p.poll() is not None:
                    print(f"\n [退出] {name} 已关闭（code={p.returncode}），"
                          "正在关闭其余进程...")
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在关闭座舱仿真...")
        for name, p in zip(names, procs):
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for name, p in zip(names, procs):
            if p.poll() is None:
                p.kill()
        print("座舱仿真已全部关闭。")


if __name__ == "__main__":
    main()
