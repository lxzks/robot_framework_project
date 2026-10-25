"""
test_ivi_e2e.py — 座舱 IVI 联动联调（开发自测）
android_gui(offscreen) + dashboard(headless) + ecu_sim
验证：
  1. android_gui 周期广播 0x400/0x500 到虚拟总线
  2. headless 仪表收到并更新 media/navi/hvac 显示（数据契约一致）
  3. adb 命令 am start 改变焦点（状态文件共享）
  4. 仪表的 trip 行程电脑累计
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
# 用当前解释器（脚本由目标 venv 的 python 启动，天然正确、可移植）
PY = sys.executable
STATE = HERE / "adb_device_state.json"
DASH_STATE = HERE / "dashboard_state.json"

sys.path.insert(0, str(HERE))
from bus import VirtualCanBus                                    # noqa: E402


def dash_snap() -> dict:
    """读仪表显示状态（headless 数据契约）"""
    return json.loads(DASH_STATE.read_text(encoding="utf-8"))


def wait_until(cond, timeout: float = 10.0, interval: float = 0.3,
               desc: str = "") -> object:
    """轮询等到条件返回真值；超时抛 AssertionError。

    替代固定 sleep —— 广播周期/启动耗时不确定时，轮询是唯一可靠做法。
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = cond()
            if last:
                return last
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    raise AssertionError(f"{timeout}s 内未满足条件: {desc}（最后状态: {last}）")


def main() -> None:
    # 清理旧状态
    STATE.unlink(missing_ok=True)
    DASH_STATE.unlink(missing_ok=True)

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"     # 无显示环境跑 Qt GUI

    ecu = subprocess.Popen(
        [PY, str(HERE / "ecu_sim.py"), "--speed", "60"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    agui = subprocess.Popen(
        [PY, str(HERE / "android_gui.py")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=env)
    dash = subprocess.Popen(
        [PY, str(HERE / "dashboard.py"), "--headless"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # 等三个进程就绪：轮询状态文件出现且新鲜（比固定 sleep 可靠，
        # 高负载机器上 4 秒可能不够 —— 真实踩过的偶发失败）
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if DASH_STATE.exists():
                try:
                    snap0 = json.loads(DASH_STATE.read_text(encoding="utf-8"))
                    if snap0.get("updated_at", 0) > 0:
                        ready = True
                        break
                except Exception:  # noqa: BLE001
                    pass
        assert ready, "仪表状态文件未就绪（20 秒内）"

        print("== 1. 虚拟总线上应出现 0x400/0x500（车机广播） ==")
        seen_400 = seen_500 = False
        with VirtualCanBus("IVI-VERIFY") as bus:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                msg = bus.recv_frame(timeout=0.2)
                if msg is None:
                    continue
                if msg.arbitration_id == 0x400:
                    seen_400 = True
                elif msg.arbitration_id == 0x500:
                    seen_500 = True
        assert seen_400, "总线上没收到 0x400 MediaPhoneData"
        assert seen_500, "总线上没收到 0x500 NaviHvacData"
        print("   OK: 0x400 / 0x500 周期广播确认")

        print("\n== 2. 仪表显示应包含 IVI 信息（两块屏数据一致） ==")
        # 轮询等车速上升 + trip 累计（ECU 斜坡加速需要时间，比 sleep 可靠）
        snap = wait_until(lambda: (lambda s: s if (s["speed_kmh"] > 30
                                                   and s["trip"]["km"] > 0)
                                   else None)(dash_snap()),
                          desc="车速上升且 trip 累计")
        print(f"   media: {snap['media']}")
        print(f"   navi:  {snap['navi']}")
        print(f"   hvac:  {snap['hvac']}")
        print(f"   trip:  {snap['trip']}")
        assert snap["hvac"]["ac_on"] is True, "默认空调应开"
        assert snap["hvac"]["set_temp_c"] == 24, "默认温度应 24℃"
        print("   OK: 仪表 IVI 显示 + trip 累计正常")

        print("\n== 3. adb 命令切换应用焦点（状态文件共享） ==")
        r = subprocess.run(
            [PY, str(HERE / "adb_sim.py"), "shell", "am", "start",
             "-n", "com.vehicle.media/.MainActivity"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        print("  ", r.stdout.strip())
        state = json.loads(STATE.read_text(encoding="utf-8"))
        assert state["current_focus"] == "com.vehicle.media/.MainActivity"
        print("   OK: 焦点已切换（android_gui 轮询后同步切界面）")

        print("\n== 4. 通过状态文件模拟「点导航」→ 仪表导航段更新 ==")
        state["ivi"]["navi_active"] = 1
        state["ivi"]["navi_distance"] = 1200
        state["ivi"]["navi_turn"] = 2      # 右转
        state["ivi"]["street_index"] = 1
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        # 轮询等仪表导航段更新（车机广播 500ms 周期，比 sleep 可靠）
        snap = wait_until(lambda: (lambda s: s if s["navi"]["distance_m"] > 0
                                   else None)(dash_snap()),
                          desc="仪表导航距离出现")
        print(f"   仪表 navi 显示: {snap['navi']}")
        # 注意：android_gui 的 _navi_tick 每秒会重算转向提示并递减距离，
        # 所以不断言精确转向，验证"合法转向 + 距离递减 + 街道正确"
        assert snap["navi"]["turn"] in ["左转", "右转", "直行"], \
            f"转向显示错: {snap['navi']}"
        assert snap["navi"]["distance_m"] <= 1200, "距离不应超过起点"
        assert snap["navi"]["street"] == "解放大道路"
        print("   OK: 车机操作 → CAN → 仪表显示，链路通（转向/距离/街道）")

        print("\n*** IVI 联动联调全部通过 ***")
    finally:
        ecu.kill()
        agui.kill()
        dash.kill()
        out, _ = agui.communicate()
        if "Traceback" in out:
            print("--- android_gui 输出(异常) ---")
            print(out[-1500:])


if __name__ == "__main__":
    main()