"""
dtc_manager.py — DTC 故障码状态机
===================================

DTC = Diagnostic Trouble Code，仪表盘上那个"检查发动机"灯背后的东西。

一个 DTC 从"故障发生"到"点亮警告灯"到"自动消失"，要经过一套状态机：
    pending（疑似） → confirmed（确认） → aged out（老化治愈）/ clear（人工清除）

每个 DTC 带 1 字节"状态位"（ISO 14229 定义 8 个 bit）：

    bit0 testFailed                     本次检测是否失败（当前周期）
    bit1 testFailedThisOperationCycle   本操作周期内是否失败过
    bit2 pendingDTC                     疑似故障（未确认）
    bit3 confirmedDTC                   已确认故障（警告灯点亮的依据）
    bit4 testNotCompletedSinceLastClear 上次清除以来检测未跑完
    bit5 testFailedSinceLastClear      上次清除以来失败过
    bit6 testNotCompletedThisOperationCycle 本周期检测未完成
    bit7 warningIndicatorRequested     请求点亮警告灯（Telltale）

真实 ECU 里这套状态由诊断监控器（diagnostic monitor）驱动；
本项目用"运行循环(operation cycle)"模拟：调用 advance_cycle() 一次 =  ECU 跑一轮自检。

典型时序（教学简化版，和主流 OEM 标定一致）：
    循环1: 注入故障 → testFailed=1, pendingDTC=1
    循环2: 故障仍在 → confirmedDTC=1（警告灯点亮）
    循环3+: 故障消失 → testFailed=0，开始 aging 计数
    40 个无故障循环 → confirmedDTC 自动清除（aging/healing）
    任意时刻 0x14 服务 → 全部清除
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 状态位掩码
# ---------------------------------------------------------------------------
BIT_TEST_FAILED = 0x01
BIT_TEST_FAILED_THIS_CYCLE = 0x02
BIT_PENDING = 0x04
BIT_CONFIRMED = 0x08
BIT_TEST_NOT_COMPLETED_SINCE_CLEAR = 0x10
BIT_TEST_FAILED_SINCE_CLEAR = 0x20
BIT_TEST_NOT_COMPLETED_THIS_CYCLE = 0x40
BIT_WARNING_INDICATOR = 0x80

STATUS_BIT_NAMES = [
    (0x01, "testFailed"),
    (0x02, "testFailedThisOperationCycle"),
    (0x04, "pendingDTC"),
    (0x08, "confirmedDTC"),
    (0x10, "testNotCompletedSinceLastClear"),
    (0x20, "testFailedSinceLastClear"),
    (0x40, "testNotCompletedThisOperationCycle"),
    (0x80, "warningIndicatorRequested"),
]


def status_to_names(status: int) -> List[str]:
    return [name for mask, name in STATUS_BIT_NAMES if status & mask]


# ---------------------------------------------------------------------------
# 故障"物理模型"：本项目模拟的故障类型
# ---------------------------------------------------------------------------
# 每个故障 = 一个 DTC + 一个"检测函数" + 一个对应警告灯
# 检测函数输入当前信号模型，输出 True=故障存在
FAULT_CATALOG: Dict[str, Dict] = {
    "P0116": {
        "name": "发动机冷却液温度传感器电路范围/性能故障",
        "detect": lambda sig: sig["coolant_temp"] > 120.0,
        "telltale": "CoolantTempHigh",     # 水温高报警灯
        "description": "冷却液温度超过 120℃ 阈值（正常 80~105℃）",
    },
    "P0117": {
        "name": "发动机冷却液温度传感器电路低电位",
        "detect": lambda sig: sig["coolant_temp"] < -30.0,
        "telltale": "CoolantTempHigh",
        "description": "冷却液温度低于 -30℃（传感器对地短路）",
    },
    "P0562": {
        "name": "系统电压过低",
        "detect": lambda sig: sig["battery_voltage"] < 10.5,
        "telltale": "BatteryLow",          # 蓄电池报警灯
        "description": "蓄电池电压低于 10.5V（正常 12.6~14.8V）",
    },
    "P0563": {
        "name": "系统电压过高",
        "detect": lambda sig: sig["battery_voltage"] > 15.5,
        "telltale": "BatteryLow",
        "description": "蓄电池电压高于 15.5V（发电机调节器失效，会烧负载）",
    },
    "P0135": {
        "name": "氧传感器加热器电路故障",
        "detect": lambda sig: sig.get("o2_heater_fault", False),
        "telltale": "MilWarning",          # MIL 发动机故障灯
        "latch": True,                     # MIL 是保持型：故障消失也不灭，
        "description": "氧传感器加热器开路（手动注入）",  # 要等 DTC 清除/aging 完成
    },
}


@dataclass
class DtcRecord:
    """一个 DTC 的完整状态"""
    dtc: str                              # 如 "P0116"
    status: int = 0                       # 状态位字节
    aging_counter: int = 0                # 无故障循环计数（用于自动清除）
    first_seen: Optional[float] = None
    snapshot: Optional[Dict] = None       # 故障确认时的快照（冻结帧）

    def set_bit(self, mask: int) -> None:
        self.status |= mask

    def clear_bit(self, mask: int) -> None:
        self.status &= ~mask

    @property
    def is_pending(self) -> bool:
        return bool(self.status & BIT_PENDING)

    @property
    def is_confirmed(self) -> bool:
        return bool(self.status & BIT_CONFIRMED)


@dataclass
class DtcManager:
    """管理一个 ECU 上所有 DTC 的状态机。

    confirm_threshold: 连续多少个故障循环才把 pending 升为 confirmed
    aging_limit: 连续多少个无故障循环自动清除 confirmed
    """

    confirm_threshold: int = 2
    aging_limit: int = 40
    _records: Dict[str, DtcRecord] = field(default_factory=dict)
    cycle_count: int = 0

    # ------------------------------------------------------------------ 注入
    def inject_fault(self, dtc: str, signals: Optional[Dict] = None) -> None:
        """手动注入一个故障（教学/测试用，真实 ECU 靠传感器异常自然触发）。"""
        if dtc not in FAULT_CATALOG:
            raise ValueError(f"未知 DTC {dtc}，可选: {list(FAULT_CATALOG)}")
        rec = self._records.setdefault(dtc, DtcRecord(dtc=dtc))
        if rec.first_seen is None:
            rec.first_seen = time.time()
        rec.snapshot = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "signals": dict(signals or {}),
        }

    def clear_fault_injection(self, dtc: str) -> None:
        """撤销手动注入。"""
        if dtc in FAULT_CATALOG:
            FAULT_CATALOG[dtc]["detect"]  # noqa: 只是文档提示
        # 简化：置一个标志，让下次 advance_cycle 时该故障"消失"
        self._records.get(dtc, DtcRecord(dtc=dtc)).snapshot = None

    # ------------------------------------------------------------------ 主循环
    def advance_cycle(self, signals: Dict) -> Dict[str, bool]:
        """ECU 跑一轮诊断自检。返回 {dtc: 故障是否存在}。"""
        self.cycle_count += 1
        active: Dict[str, bool] = {}

        for dtc, spec in FAULT_CATALOG.items():
            fault_present = spec["detect"](signals)
            active[dtc] = fault_present
            rec = self._records.setdefault(dtc, DtcRecord(dtc=dtc))

            if fault_present:
                rec.set_bit(BIT_TEST_FAILED)
                rec.set_bit(BIT_TEST_FAILED_THIS_CYCLE)
                rec.set_bit(BIT_TEST_FAILED_SINCE_CLEAR)
                rec.clear_bit(BIT_TEST_NOT_COMPLETED_THIS_CYCLE)
                rec.clear_bit(BIT_TEST_NOT_COMPLETED_SINCE_CLEAR)
                if rec.is_pending:
                    # 已 pending：累加确认计数（挂在 aging_counter 反面不好，单独字段）
                    rec.__dict__["_pending_cycles"] = rec.__dict__.get("_pending_cycles", 0) + 1
                    if rec.__dict__["_pending_cycles"] >= self.confirm_threshold:
                        rec.set_bit(BIT_CONFIRMED)
                        rec.set_bit(BIT_WARNING_INDICATOR)   # 确认 → 请求点灯
                else:
                    # 第一次见：置 pending
                    rec.set_bit(BIT_PENDING)
                    rec.__dict__["_pending_cycles"] = 1
                rec.aging_counter = 0
            else:
                # 本周期无故障
                rec.clear_bit(BIT_TEST_FAILED)
                # 灭灯策略：非保持型灯（水温/电压）故障消失即灭；
                # 保持型灯（MIL）要等 confirmed 被 aging 清除或人工 0x14 清除
                if not FAULT_CATALOG[dtc].get("latch", False):
                    rec.clear_bit(BIT_WARNING_INDICATOR)
                if not rec.is_confirmed and rec.is_pending:
                    # pending 期间故障消失 → 直接撤销 pending（防抖）
                    rec.clear_bit(BIT_PENDING)
                    rec.__dict__["_pending_cycles"] = 0
                if rec.is_confirmed:
                    # confirmed 后故障消失 → 开始 aging 计数
                    rec.aging_counter += 1
                    if rec.aging_counter >= self.aging_limit:
                        rec.clear_bit(BIT_CONFIRMED)
                        rec.clear_bit(BIT_WARNING_INDICATOR)
                        rec.clear_bit(BIT_TEST_FAILED_SINCE_CLEAR)
                        rec.aging_counter = 0
                        rec.__dict__["_pending_cycles"] = 0
        return active

    # ------------------------------------------------------------------ 查询
    def get_status(self, dtc: str) -> int:
        return self._records.get(dtc, DtcRecord(dtc=dtc)).status

    def get_record(self, dtc: str) -> DtcRecord:
        return self._records.setdefault(dtc, DtcRecord(dtc=dtc))

    def list_dtcs(self, mask: int = 0xFF) -> List[DtcRecord]:
        """按状态掩码过滤。mask=0x08 → 只列 confirmed 的。"""
        return [r for r in self._records.values() if r.status & mask]

    def get_snapshot(self, dtc: str) -> Optional[Dict]:
        return self._records.get(dtc, DtcRecord(dtc=dtc)).snapshot

    # ------------------------------------------------------------------ 清除
    def clear_all(self) -> int:
        """0x14 清除诊断信息：所有 pending/confirmed 全部归零，aging 清零。"""
        count = len([r for r in self._records.values() if r.status & (BIT_PENDING | BIT_CONFIRMED)])
        for rec in self._records.values():
            rec.status = 0
            rec.aging_counter = 0
            rec.__dict__["_pending_cycles"] = 0
            rec.first_seen = None
        return count

    def clear_dtc(self, dtc: str) -> bool:
        """清除单个 DTC（0x14 带 groupOfDTC 参数时）。"""
        if dtc in self._records and self._records[dtc].status:
            self._records[dtc].status = 0
            self._records[dtc].aging_counter = 0
            self._records[dtc].__dict__["_pending_cycles"] = 0
            return True
        return False

    # ------------------------------------------------------------------ 辅助
    def telltales_to_light(self) -> List[str]:
        """当前应该点亮的警告灯列表（仪表盘据此点灯）。"""
        lights: List[str] = []
        for dtc, rec in self._records.items():
            if rec.status & BIT_WARNING_INDICATOR:
                light = FAULT_CATALOG[dtc]["telltale"]
                if light not in lights:
                    lights.append(light)
        return lights


if __name__ == "__main__":
    # 自测：完整走一遍 DTC 生命周期
    mgr = DtcManager()
    signals = {"coolant_temp": 90.0, "battery_voltage": 13.5}

    print("== 阶段1：正常驾驶 3 个循环 ==")
    for _ in range(3):
        mgr.advance_cycle(signals)
    print("P0116 状态:", hex(mgr.get_status("P0116")))

    print("\n== 阶段2：水温飙到 125℃，注入故障 ==")
    signals["coolant_temp"] = 125.0
    mgr.inject_fault("P0116", signals)
    for i in range(3):
        mgr.advance_cycle(signals)
        print(f"  循环{i+1}: status={hex(mgr.get_status('P0116'))} "
              f"({status_to_names(mgr.get_status('P0116'))}) 应点灯={mgr.telltales_to_light()}")

    print("\n== 阶段3：水温恢复正常，看 aging 自愈 ==")
    signals["coolant_temp"] = 90.0
    for i in range(3):
        mgr.advance_cycle(signals)
    print("  3 循环后 confirmed 仍在吗:", mgr.get_status("P0116") & BIT_CONFIRMED != 0,
          "(aging_counter =", mgr.get_record("P0116").aging_counter, ")")

    print("\n== 阶段4：0x14 清除全部 DTC ==")
    mgr.inject_fault("P0562", signals)   # 再造一个电压故障
    signals["battery_voltage"] = 9.0
    for _ in range(3):
        mgr.advance_cycle(signals)
    print("  清除前 confirmed 列表:", [r.dtc for r in mgr.list_dtcs(BIT_CONFIRMED)])
    print("  清除数量:", mgr.clear_all())
    print("  清除后 P0116 状态:", hex(mgr.get_status("P0116")),
          " 应点灯:", mgr.telltales_to_light())
