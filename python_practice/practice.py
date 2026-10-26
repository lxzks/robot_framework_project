"""
Python 能力练习（8 题，从"改一行"到"写一个小工具"）
================================================================

**怎么用**：
  1. 打开本文件，从 P1 开始，把每个 `# TODO` 补完
  2. 每补完一题就存盘，然后跑：
         python python_practice/check.py
     它会告诉你哪几题过了、哪几题还不行
  3. 全部通过后，再看 answers.md 对照思路

**重要**：这 8 题都是本项目**真实代码里用到的技能**，
不是"为了练习而练习"的典型算法题。每题都标了【项目里对应什么】。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# ===========================================================================
# P1 · 写一个函数（基础）
# ===========================================================================
def calc_rpm(speed_kmh: float) -> float:
    """按本项目的简化模型算转速：转速 = max(800, 车速 × 80)

    【项目里对应】ecu_sim.py 的 _send_engine_data 里计算 EngineSpeed
    【提示】max() 是 Python 内置函数，max(a, b) 返回较大的那个
    【验收】calc_rpm(60) == 4800；calc_rpm(5) == 800（低速时不低于怠速）
    """
    # TODO: 在这里返回计算结果（把下面这行 pass 删掉）
    pass


# ===========================================================================
# P2 · pathlib 读 JSON 字段（项目里 26 个文件都在用）
# ===========================================================================
def read_json_field(path, field_name: str, default=None):
    """读一个 JSON 文件，取出指定字段；文件不存在或字段不存在时返回 default

    【项目里对应】所有测试关键字读 dashboard_state.json 的方式
    【提示】
        p = Path(path)
        if not p.exists(): return default
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get(field_name, default)
    【验收】read_json_field("simulators/dashboard_state.json", "speed_kmh") 返回数字或 None，
            read_json_field("不存在的文件.json", "x", "默认值") == "默认值"
    """
    # TODO
    pass


# ===========================================================================
# P3 · dataclass 定义数据结构（项目里 2 个文件在用）
# ===========================================================================
@dataclass
class DtcRecord:
    """一个 DTC 记录（模仿 simulators/dtc_manager.py 的 DtcRecord）

    【要求】定义 4 个字段：
        dtc: str                        （必填，如 "P0116"）
        status: int = 0                 （状态字节，默认 0）
        count: int = 0                  （发生次数，默认 0）
        snapshot: dict | None = None    （快照，默认 None）
    【验收】DtcRecord("P0116") 能创建；.status 是 0；.snapshot 是 None
    """
    # TODO: 在这里写 4 个字段（注意有默认值的要放在没默认值的后面）
    pass


# ===========================================================================
# P4 · try/except 保护（项目里 16 个文件在用）
# ===========================================================================
def safe_read_text(path) -> str:
    """读文件内容；文件不存在 / 读失败时返回空字符串 ""，**不能崩**

    【项目里对应】所有"尽力而为"的清理逻辑
    【提示】try: ... except (OSError, ValueError): return ""
    【验收】不存在的文件 → ""；存在的文件 → 内容
    """
    # TODO
    pass


# ===========================================================================
# P5 · 列表推导（项目里 26 个文件在用）
# ===========================================================================
def lit_telltales(telltale_map: dict) -> list:
    """仪表内部把每个灯存成"灯名 → 0/1"，这里要取出值为 1 的灯名

    【输入示例】{"MilWarning": 0, "CoolantTempHigh": 1, "TurnLeft": 1}
    【输出示例】["CoolantTempHigh", "TurnLeft"]
    【项目里对应】dashboard.py 里 self.telltales 就是这种结构，
                 对外暴露时才转成 telltales_lit 列表
    【提示】**这题正是列表推导的典型场景**：
            [name for name, v in telltale_map.items() if v]
            从右往左读：遍历键值对 → 筛出 v 为真 → 收集 name
    【验收】传入 {"A": 1, "B": 0, "C": 1} → ["A", "C"]；
            传入 {} → []；传入 {"A": 0} → []
    """
    # TODO
    pass


# ===========================================================================
# P6 · 分组统计（setdefault / dict 推导）
# ===========================================================================
def group_by_requirement(tests: list[tuple[str, str]]) -> dict[str, list[str]]:
    """把 [(需求ID, 用例名), ...] 按需求分组

    【输入】[("REQ-CAN-001", "用例A"), ("REQ-CAN-001", "用例B"), ("REQ-DTC-002", "用例C")]
    【输出】{"REQ-CAN-001": ["用例A", "用例B"], "REQ-DTC-002": ["用例C"]}
    【项目里对应】trace_matrix.py 建立"需求 → 用例列表"的映射
    【提示】用 d.setdefault(rid, []).append(case)
    【验收】见 docstring 的输入输出
    """
    # TODO
    pass


# ===========================================================================
# P7 · lambda 当"规则表"（项目里 10 个文件在用）
# ===========================================================================
def build_fault_rules() -> dict:
    """构建一个"故障判定规则表"（模仿 dtc_manager.py 的 FAULT_CATALOG）

    【要求】返回这样的字典：
        {
          "P0116": {"name": "水温过高", "detect": lambda sig: sig["coolant"] > 120},
          "P0562": {"name": "电压过低", "detect": lambda sig: sig["voltage"] < 10.5},
        }
    【输入】signals 字典的键是 "coolant" / "voltage"
    【项目里对应】dtc_manager.py 的 FAULT_CATALOG（加故障只加一行）
    【验收】check.py 会用 {"coolant": 125, "voltage": 13.8} 去跑，应该命中 P0116
    """
    # TODO
    pass


# ===========================================================================
# P8 · 周期任务循环（dt 累加，项目主循环的核心）
# ===========================================================================
def simulate_periodic(duration_s: float, period_ms: float,
                      on_send: Callable[[], None]) -> int:
    """模拟"每 period_ms 毫秒执行一次"，持续 duration_s 秒，返回执行次数

    【要求】
      - 用 time.monotonic() 计时（不是 time.sleep(period) 简单循环）
      - 用"累加 dt，够了就发"的模式
      - 为了不真的等太久，每一步用 time.sleep(min(0.01, ...)) 推进
    【项目里对应】ecu_sim.py 的 run() 主循环（t_100 >= 0.1 那段）
    【验收】period_ms=100, duration_s=1 → 大约 10 次（允许 9~11）
    【思路提示】（不给你完整代码，自己拼）
        1. 用 time.monotonic() 记两个时刻：
              last    —— 上一轮循环的时刻
              deadline —— 什么时候该停（= 开始时刻 + duration_s）
        2. 循环条件：现在还早于 deadline 就继续
        3. 每轮做三件事：
              a. 先 time.sleep(0.005) 模拟"干了点别的活"（也让 CPU 歇一下）
              b. 算 dt = 现在 - last，然后把 last 更新成现在
              c. 把 dt 累加到计时器 t；如果 t >= 周期（毫秒要除以 1000），
                 就把 t 清零、调用 on_send()、次数 +1
        4. 循环结束返回次数
    【注意】清零要写 t = 0（不要写 t -= 周期），否则误差会累积
    """
    # TODO
    pass


# ===========================================================================
if __name__ == "__main__":
    print("这是练习模板，不是用来直接跑的。")
    print("请补完 TODO 后运行：python python_practice/check.py")
