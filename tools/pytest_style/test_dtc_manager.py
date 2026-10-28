"""dtc_manager 纯单元测试：DTC 状态机（pending → confirmed → aging → clear）。

关键：全程**纯内存**推进 `advance_cycle()`，不启动 ECU、不用 UDP 总线，
所以整个文件毫秒级跑完；对应的系统级验证在
tests/robot/03_dtc_workflow.robot 与 scenarios/04_dtc_lifecycle.robot。

信号字典要带齐 FAULT_CATALOG 里各 detect 函数用到的字段：
    coolant_temp / battery_voltage / o2_heater_fault
"""

from __future__ import annotations

import pytest

from dtc_manager import (
    BIT_CONFIRMED,
    BIT_PENDING,
    BIT_WARNING_INDICATOR,
    DtcManager,
)

NORMAL = {"coolant_temp": 90.0, "battery_voltage": 13.5, "o2_heater_fault": False}
FAULT_HOT = {"coolant_temp": 125.0, "battery_voltage": 13.5, "o2_heater_fault": False}


def test_pending_then_confirmed_by_threshold() -> None:
    """confirm_threshold=2：第 1 个故障循环 pending，第 2 个循环才 confirmed + 点灯。"""
    mgr = DtcManager(confirm_threshold=2)

    mgr.advance_cycle(FAULT_HOT)
    s1 = mgr.get_status("P0116")
    assert s1 & BIT_PENDING, "第 1 个故障循环应置 pending"
    assert not s1 & BIT_CONFIRMED, "第 1 个循环还不能确认（防误报）"

    mgr.advance_cycle(FAULT_HOT)
    s2 = mgr.get_status("P0116")
    assert s2 & BIT_CONFIRMED, "连续 2 个故障循环后应 confirmed"
    assert s2 & BIT_WARNING_INDICATOR, "confirmed 后应请求点亮警告灯"

    assert "P0116" in [r.dtc for r in mgr.list_dtcs(BIT_CONFIRMED)]
    assert "CoolantTempHigh" in mgr.telltales_to_light()


def test_aging_clears_confirmed_after_n_clean_cycles() -> None:
    """故障消失后 aging：连续 N 个无故障循环才自动清除 confirmed。"""
    mgr = DtcManager(confirm_threshold=2, aging_limit=3)
    mgr.advance_cycle(FAULT_HOT)
    mgr.advance_cycle(FAULT_HOT)                 # → confirmed

    # 第 1 个无故障循环：非保持型灯（水温）立即熄灭，但 confirmed 仍在（开始 aging）
    mgr.advance_cycle(NORMAL)
    s = mgr.get_status("P0116")
    assert s & BIT_CONFIRMED, "aging 未满前 confirmed 不能清"
    assert not s & BIT_WARNING_INDICATOR, "非保持型灯故障消失即灭"
    assert mgr.get_record("P0116").aging_counter == 1

    mgr.advance_cycle(NORMAL)                    # aging = 2
    assert mgr.get_status("P0116") & BIT_CONFIRMED
    mgr.advance_cycle(NORMAL)                    # aging = 3 → 清除
    s = mgr.get_status("P0116")
    assert not s & BIT_CONFIRMED
    assert mgr.get_record("P0116").aging_counter == 0


def test_clear_all_resets_records() -> None:
    """0x14 清除诊断信息：pending/confirmed 全部归零，灯全灭。"""
    mgr = DtcManager(confirm_threshold=2)
    mgr.advance_cycle(FAULT_HOT)
    mgr.advance_cycle(FAULT_HOT)
    assert mgr.clear_all() >= 1

    assert mgr.get_status("P0116") == 0
    assert mgr.list_dtcs(BIT_CONFIRMED) == []
    assert mgr.telltales_to_light() == []


def test_inject_unknown_dtc_raises() -> None:
    """注入 FAULT_CATALOG 之外的 DTC 必须报错（防止拼写错误静默通过）。"""
    mgr = DtcManager()
    with pytest.raises(ValueError):
        mgr.inject_fault("P9999")
