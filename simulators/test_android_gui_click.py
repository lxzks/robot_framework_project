"""
test_android_gui_click.py — 安卓车机 GUI 点击全覆盖测试（开发自测）
====================================================================

offscreen 模式下 QPushButton.click() 会真实发射 clicked 信号、
走完整的连接逻辑 —— 正好用来验证"每个按钮点了有没有反应"。

覆盖：桌面图标 / 底部导航 / 媒体控制 / 导航控制 / 电话 / 空调。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication, QPushButton          # noqa: E402

from adb_sim import STATE_FILE, load_state                       # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> None:
    STATE_FILE.unlink(missing_ok=True)
    app = QApplication(sys.argv)
    from android_gui import AndroidMainWindow, APPS

    win = AndroidMainWindow()
    ivi = lambda: load_state()["ivi"]                        # noqa: E731

    print("== 1. 桌面图标点击 → 切页 + 焦点变化 ==")
    for pkg in ["com.vehicle.navi", "com.vehicle.media",
                "com.vehicle.phone", "com.vehicle.hvac",
                "com.vehicle.settings"]:
        # 先回桌面
        win.stack.setCurrentIndex(0)
        # 找到桌面对应按钮并点击
        launcher = win.pages[0]
        clicked = False
        for b in launcher.findChildren(QPushButton):
            if b.text() == f"{APPS[pkg][1]}\n{APPS[pkg][2]}":
                b.click()
                clicked = True
                break
        check(f"点击 {APPS[pkg][2]} 图标切页", clicked and
              win.stack.currentIndex() == APPS[pkg][0],
              f"clicked={clicked} page={win.stack.currentIndex()}")
        check(f"点击 {APPS[pkg][2]} 后焦点变化",
              load_state()["current_focus"].startswith(pkg))

    print("== 2. 底部导航键 ==")
    win.btn_home.click()
    check("Home 键回桌面", win.stack.currentIndex() == 0)
    win.btn_back.click()
    check("桌面按 Back 停留桌面", win.stack.currentIndex() == 0)
    win.btn_recent.click()   # 仅记日志，不崩即可
    check("多任务键不崩溃", True)

    print("== 3. 媒体页按钮 ==")
    win.stack.setCurrentIndex(2)   # Media
    media = win.pages[2]
    track0 = ivi()["track_index"]
    media.btn_next.click()
    check("下一首切歌", ivi()["track_index"] == (track0 + 1) % 6)
    media.btn_prev.click()
    check("上一首回切", ivi()["track_index"] == track0)
    st0 = ivi()["media_status"]
    media.btn_play.click()
    check("播放/暂停切换", ivi()["media_status"] != st0)
    vol0 = ivi()["volume"]
    media.btn_vol_up.click()
    check("音量+", ivi()["volume"] == min(100, vol0 + 10))
    # 点播放列表第 3 首
    btns = [b for b in media.findChildren(QPushButton)
            if b.text().startswith("3.")]
    if btns:
        btns[0].click()
        check("点列表第3首", ivi()["track_index"] == 2)

    print("== 4. 导航页按钮 ==")
    win.stack.setCurrentIndex(1)   # Navi
    navi = win.pages[1]
    navi.btn_start.click()
    check("开始导航", ivi()["navi_active"] == 1 and ivi()["navi_distance"] > 0)
    dest_btns = [b for b in navi.findChildren(QPushButton)
                 if b.text().startswith("📍")]
    if dest_btns:
        dest_btns[2].click()
        check("选第3个目的地", ivi()["street_index"] == 2
              and ivi()["navi_active"] == 1)
    navi.btn_stop.click()
    check("结束导航", ivi()["navi_active"] == 0)

    print("== 5. 电话页按钮 ==")
    win.stack.setCurrentIndex(3)   # Phone
    phone = win.pages[3]
    call_btns = [b for b in phone.findChildren(QPushButton)
                 if b.text().startswith("👤")]
    if call_btns:
        call_btns[1].click()
        check("呼叫第2个联系人", ivi()["call_status"] == 1
              and ivi()["caller_index"] == 1)
        phone.btn_hangup.click()
        check("挂断", ivi()["call_status"] == 0)

    print("== 6. 空调页按钮 ==")
    win.stack.setCurrentIndex(4)   # Hvac
    hvac = win.pages[4]
    t0 = ivi()["hvac_temp"]
    hvac.btn_temp_up.click()
    check("温度+", ivi()["hvac_temp"] == min(32, t0 + 1))
    hvac.btn_temp_down.click()
    hvac.btn_temp_down.click()
    check("温度- -", ivi()["hvac_temp"] == max(16, t0 - 1))
    ac0 = ivi()["ac_on"]
    sw = hvac.switch_btns["ac_on"]
    sw.click()
    check("A/C 开关切换", ivi()["ac_on"] != ac0)
    sw.click()   # 还原

    print("== 7. 页面刷新可见性（按钮文本/标签是否随状态变） ==")
    win.stack.setCurrentIndex(2)
    media.btn_play.click()
    play_text = media.btn_play.text()
    ivi_state = ivi()["media_status"]
    check("播放按钮文本与状态联动",
          (play_text == "⏸") == (ivi_state == 1),
          f"text={play_text} status={ivi_state}")

    win.backend.shutdown()
    print(f"\n点击测试: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
