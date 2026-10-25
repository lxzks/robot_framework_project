"""
test_cluster_e2e.py — 仪表盘整链联调（开发自测）
ECU(coolant=125 触发水温故障) + Dashboard(headless)
→ 验证仪表显示车速/档位/水温报警灯全部正确
→ 再验证 ADB 模拟器命令
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
# 用当前解释器（脚本由目标 venv 的 python 启动，天然正确、可移植）
PY = sys.executable
STATE = HERE / "dashboard_state.json"


def main() -> None:
    # 重置车机状态：保证 install/am start 测试从干净状态开始（测试隔离）
    (HERE / "adb_device_state.json").unlink(missing_ok=True)

    # 1. 启动 ECU：目标车速 80，水温 125（超过 120 阈值 → P0116）
    ecu = subprocess.Popen(
        [PY, str(HERE / "ecu_sim.py"), "--speed", "80", "--coolant", "125"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1)
    # 2. 启动仪表盘 headless
    dash = subprocess.Popen(
        [PY, str(HERE / "dashboard.py"), "--headless"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        print("等待仪表显示稳定（ECU 斜坡加速 + DTC 确认 2 个循环）...")
        time.sleep(12)

        snap = json.loads(STATE.read_text(encoding="utf-8"))
        print("\n== 仪表当前显示 ==")
        for k, v in snap.items():
            if k != "telltales_lit":
                print(f"  {k}: {v}")
        print(f"  telltales_lit: {snap['telltales_lit']}")

        assert 75 <= snap["speed_kmh"] <= 85, f"车速显示错误: {snap['speed_kmh']}"
        assert snap["gear"] == "D", f"档位应为 D: {snap['gear']}"
        assert snap["coolant_c"] >= 110, f"水温应逼近 125: {snap['coolant_c']}"
        assert "CoolantTempHigh" in snap["telltales_lit"], \
            f"水温报警灯应点亮: {snap['telltales_lit']}"
        print("\n[OK] 仪表显示全部正确（车速/档位/水温/报警灯）")

        # 3. 命令 ECU 水温恢复正常，验证报警灯熄灭 + DTC aging
        print("\n== ECU 水温恢复正常 90℃，观察报警灯熄灭/aging ==")
        ecu.stdin.write("coolant 90\n")
        ecu.stdin.flush()
        time.sleep(6)
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        print(f"  水温: {snap['coolant_c']}℃  报警灯: {snap['telltales_lit']}")
        assert "CoolantTempHigh" not in snap["telltales_lit"], "灯应已熄灭"
        print("[OK] 故障消失后报警灯熄灭（confirmed DTC 未满 40 循环，不会自愈）")

        # 4. 灯光测试例程：31 01 0301 → 全灯亮
        print("\n== (灯光测试例程在 RF 测试里验证，这里先跳过) ==")

        print("\n*** 仪表盘整链联调通过 ***")
    finally:
        ecu.terminate()
        dash.terminate()
        time.sleep(0.5)
        dash.kill()
        ecu.kill()

    # 5. ADB 模拟器测试
    print("\n== ADB 模拟器测试 ==")
    r = subprocess.run([PY, str(HERE / "adb_sim.py"), "devices"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print(r.stdout.strip())
    assert "VEHICLE2026AUTO" in r.stdout

    r = subprocess.run(
        [PY, str(HERE / "adb_sim.py"), "shell", "getprop", "ro.product.model"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print("getprop:", r.stdout.strip())
    assert "Cockpit" in r.stdout

    r = subprocess.run(
        [PY, str(HERE / "adb_sim.py"), "install", "-r",
         "com.vehicle.navi.apk"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print("install -r navi:", r.stdout.strip())
    assert "Success" in r.stdout

    r = subprocess.run(
        [PY, str(HERE / "adb_sim.py"), "shell", "am", "start",
         "-n", "com.vehicle.navi/.MapActivity"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print("am start navi:", r.stdout.strip())

    r = subprocess.run(
        [PY, str(HERE / "adb_sim.py"), "shell", "dumpsys", "window"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print("当前焦点:", r.stdout.strip())
    assert "com.vehicle.navi" in r.stdout
    print("\n[OK] ADB 模拟器全部命令正常")

    print("\n*** 仪表盘 + ADB 联调全部通过 ***")


if __name__ == "__main__":
    main()