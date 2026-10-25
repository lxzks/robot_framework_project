"""
generate_testcases.py — 测试用例自动生成器（DBC → 可执行用例）
================================================================

**这是本项目和 "APIGen"（API 测试生成）同一套思想的座舱版**：
从"接口规范"自动生成测试用例 —— APIGen 从 OpenAPI 规范生成 API 用例，
本工具从 DBC（CAN 信号数据库）生成总线信号用例。

三步生成流水线：

    1. 解析 DBC：提取每个报文的每个信号（类型/范围/缩放/单位/值表）
    2. 生成测试点：
       - 数值信号 → 边界值分析（BVA）：min-1 / min / min+1 / mid / max-1 / max / max+1
         （min-1 与 max+1 是**越界值**，验证 ECU 是否拒绝或钳位）
       - 枚举信号 → 等价类：值表每个取值各一条
    3. 组合优化：
       - 单信号用例（每个边界值一条，Template 数据驱动）
       - **多信号 pairwise 组合**（3 信号 × 6 值 = 216 组合 → pairwise ~20 组），
         覆盖所有"两两取值组合"而不必测全组合

产出：一个可直接 `robot` 运行的 .robot 文件 + 一份生成摘要。

用法：
    python generate_testcases.py                          # 生成到 tests/robot/generated/
    python generate_testcases.py --message EngineData      # 只生成指定报文
    python generate_testcases.py --pairwise-signals 3      # 指定 pairwise 参与信号数
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "simulators"))

import cantools                                        # noqa: E402

DBC_PATH = ROOT / "simulators" / "dbc" / "vehicle.dbc"


# ===========================================================================
# 1. 解析 DBC → 信号模型
# ===========================================================================
def collect_signals(dbc_path: Path, only_message: str | None = None) -> list[dict]:
    """提取所有报文的信号定义（供生成器消费）"""
    db = cantools.database.load_file(str(dbc_path))
    out = []
    for msg in db.messages:
        if only_message and msg.name != only_message:
            continue
        for sig in msg.signals:
            out.append({
                "message": msg.name,
                "message_id": msg.frame_id,
                "name": sig.name,
                "minimum": sig.minimum,
                "maximum": sig.maximum,
                "unit": sig.unit or "",
                "scale": sig.scale,
                "offset": sig.offset,
                "is_float": sig.is_float,
                "choices": dict(sig.choices) if sig.choices else None,
                "length": sig.length,
                "senders": list(msg.senders) if msg.senders else [],
            })
    return out


def numeric_test_points(sig: dict) -> list[tuple[str, float]]:
    """边界值分析（BVA）：7 点法。

    min-1 / max+1 是**越界值** —— 用来验证 ECU 对超范围输入的
    处理（拒绝 / 钳位 / 报错），这是边界测试的核心价值。
    """
    lo, hi = float(sig["minimum"]), float(sig["maximum"])
    step = max(float(sig["scale"]), (hi - lo) / 100 if hi > lo else 1.0)
    if not sig["is_float"]:
        step = max(1.0, round(step))
    points = [
        ("最小值", lo),
        ("最小值+1LSB", min(lo + step, hi)),
        ("典型值(中点)", (lo + hi) / 2),
        ("最大值-1LSB", max(hi - step, lo)),
        ("最大值", hi),
        ("越界(低于最小值)", lo - step),
        ("越界(高于最大值)", hi + step),
    ]
    # 去重（范围极小的信号可能出现重复点）
    seen, out = set(), []
    for desc, v in points:
        key = round(v, 6)
        if key not in seen:
            seen.add(key)
            out.append((desc, v))
    return out


def enum_test_points(sig: dict) -> list[tuple[str, int]]:
    """枚举信号 → 等价类：值表每个取值一条"""
    return [(f"{name}({value})", value)
            for value, name in sorted(sig["choices"].items())]


# ===========================================================================
# 2. pairwise 组合（两两覆盖）
# ===========================================================================
def pairwise(parameters: list[tuple[str, list]]) -> list[dict]:
    """生成覆盖所有"两两取值组合"的最小行集（贪心算法）。

    直觉：N 个参数各 M 个值 → 全组合 M^N 爆炸；
    但实际上大部分缺陷由**两参数交互**触发，所以只需保证
    "任取两个参数的任一取值组合都至少出现一次"。

    实现（贪心扩展）：
      1. 枚举所有参数对的取值组合 = 待覆盖集合
      2. 每轮：选一个未被覆盖的组合作为种子的"骨架"，
         逐个参数扩展（选择能覆盖最多未覆盖对的值）
    """
    names = [n for n, _ in parameters]
    values = {n: vs for n, vs in parameters}

    # 待覆盖：{(p1, v1, p2, v2), ...}
    uncovered: set[tuple] = set()
    for p1, p2 in itertools.combinations(names, 2):
        for v1 in values[p1]:
            for v2 in values[p2]:
                uncovered.add((p1, v1, p2, v2))

    rows: list[dict] = []
    while uncovered:
        # 种子：取第一个未覆盖组合
        seed = next(iter(uncovered))
        row = {seed[0]: seed[1], seed[2]: seed[3]}
        # 逐参数补齐
        for name in names:
            if name in row:
                continue
            best_val, best_gain = None, -1
            for v in values[name]:
                # 该值能覆盖多少条"含本参数与其他已定参数的"未覆盖组合
                gain = 0
                for other, ov in row.items():
                    for pair in ((name, v, other, ov), (other, ov, name, v)):
                        if pair in uncovered:
                            gain += 1
                if gain > best_gain:
                    best_val, best_gain = v, gain
            row[name] = best_val
        rows.append(row)
        # 移除本行覆盖的组合
        for p1, p2 in itertools.combinations(names, 2):
            for pair in ((p1, row[p1], p2, row[p2]),
                         (p2, row[p2], p1, row[p1])):
                uncovered.discard(pair)
    return rows


# ===========================================================================
# 3. 生成 .robot 用例文件
# ===========================================================================
def _fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def pick_pairwise_signals(signals: list[dict], count: int) -> tuple[str, list[dict]]:
    """选参与 pairwise 的信号：取"数值信号最多的报文"的前 N 个。

    两条策略：
    1. 限定同一报文 —— 跨报文的信号组合在总线上是分帧发送的，
       组合断言没有意义（发送时序不可控）；同报文内的信号才是一次编码的。
    2. 排除位信号（length == 1）—— 它的取值范围只有 0/1，
       "取 min/mid/max 三个代表值"退化成无意义的 0.5；
       位信号更适合放在边界值用例里（覆盖 0 和 1 即可）。
    """
    by_msg: dict[str, list[dict]] = {}
    for sig in signals:
        by_msg.setdefault(sig["message"], []).append(sig)

    def usable(sigs: list[dict]) -> list[dict]:
        return [s for s in sigs
                if not s["choices"] and s["length"] > 1]

    def is_ecu_message(sigs: list[dict]) -> bool:
        # 优先测 ECU 发出的报文（车身网关注入的 IVI 报文不是本测试对象）
        return bool(sigs) and "ECU" in sigs[0].get("senders", [])

    # 排序键：ECU 报文优先，其次数值信号数量多
    msg_name, sigs = max(
        by_msg.items(),
        key=lambda kv: (is_ecu_message(kv[1]), len(usable(kv[1]))),
        default=(None, []))
    return msg_name, usable(sigs)[:count]


def generate_robot(signals: list[dict], pairwise_count: int = 3) -> str:
    """生成可执行的 .robot 文件内容"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts = [f"""*** Settings ***
Documentation    自动生成的信号测试用例（由 generate_testcases.py 于 {now} 生成）
...              **不要手工修改本文件** —— 改生成器脚本后重新生成。
...              生成策略：
...              1. 数值信号 → 边界值分析（BVA）：min-1/min/min+1/中点/max-1/max/max+1
...                 其中 min-1 与 max+1 为越界值，验证 ECU 对超范围输入的处理
...              2. 枚举信号 → 等价类：值表每个取值
...              3. 多信号 → pairwise 两两组合覆盖（避免全组合爆炸）
...              每条用例都是"发送 → 读回 → 比对"闭环，可直接 robot 运行。
...              需求追溯：[Tags] 里的 REQ-xxx 指向 requirements/requirements.yaml
...              （REQ-CAN-011 = 信号编解码正确性；生成的用例在 Suite Setup
...               用 0x28 暂停 ECU 周期报文，故也关联 REQ-DIAG-014）
Resource         ../../../resources/common.resource
Suite Setup      环境就绪并暂停 ECU 周期报文
Suite Teardown   恢复 ECU 周期报文并关闭环境

*** Test Cases ***
"""]

    # ---- 边界值用例（按报文集中的模板）----
    by_msg: dict[str, list[dict]] = {}
    for sig in signals:
        by_msg.setdefault(sig["message"], []).append(sig)

    for msg_name, sigs in by_msg.items():
        rows, oor_rows = [], []
        for sig in sigs:
            if sig["choices"]:
                for desc, val in enum_test_points(sig):
                    rows.append(f"    {msg_name}    {sig['name']}    {val}    {desc}")
            else:
                for desc, val in numeric_test_points(sig):
                    if "越界" in desc:
                        # 越界点 → 极值用例（发满量程原始值；物理越界值
                        # 因位宽固定无法编码，见库关键字文档）
                        oor_rows.append(
                            f"    {msg_name}    {sig['name']}    "
                            f"满量程极值({sig['length']}bit)")
                    else:
                        rows.append(
                            f"    {msg_name}    {sig['name']}    "
                            f"{_fmt_value(val)}    {desc}")
        parts.append(
            f"{msg_name} 信号边界值/等价类测试\n"
            f"    [Documentation]    自动生成：{len(rows)} 个测试点"
            f"（{len(sigs)} 个信号）—— 发送后从总线读回并断言一致\n"
            f"    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014\n"
            f"    [Template]    信号应正确收发\n"
            + "\n".join(rows) + "\n")
        if oor_rows:
            parts.append(
                f"{msg_name} 极值可发送性测试\n"
                f"    [Documentation]    自动生成：{len(oor_rows)} 个极值测试点"
                f" —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据"
                f"时协议层正常（物理越界值因位宽固定无法编码）\n"
                f"    [Tags]    generated    extreme    REQ-CAN-011\n"
                f"    [Template]    Out Of Range Value Should Be Sendable\n"
                + "\n".join(oor_rows) + "\n")

    # ---- pairwise 组合用例（同一报文的数值信号，避免跨报文组合）
    target_msg_name, multi_sig = pick_pairwise_signals(signals, pairwise_count)
    if len(multi_sig) >= 2:
        # 每个信号取 3 个代表值（min/mid/max）做组合。
        # 键名必须用**短名**（VehicleSpeed）—— RF 的 kwargs 语法不支持
        # "EngineData.VehicleSpeed=0" 这种带点的键（真实踩过的坑）
        params = []
        for s in multi_sig:
            lo, hi = float(s["minimum"]), float(s["maximum"])
            # 整数信号的中间值必须取整：127.5 编码后 round 成 128，
            # 读回 128 ≠ 127.5，断言必挂（真实踩过的坑）
            mid = ((lo + hi) / 2) if s["is_float"] else round((lo + hi) / 2)
            params.append((s["name"], [lo, mid, hi]))
        combo_rows = pairwise(params)
        # 每行都必须带报文名：RF 的 [Template] 只接受一个关键字名，
        # 不能写成 "[Template] 关键字 参数"（会报 accepts only one value）
        rows = "\n".join(
            f"    {target_msg_name}    " +
            "    ".join(f"{k}={_fmt_value(v)}" for k, v in r.items())
            for r in combo_rows)
        parts.append(
            f"{target_msg_name} 多信号 pairwise 组合测试\n"
            f"    [Documentation]    自动生成：pairwise 两两覆盖"
            f"（{len(multi_sig)} 信号 × 3 值 = 全组合 "
            f"{3 ** len(multi_sig)} 组 → 优化为 {len(combo_rows)} 组）\n"
            f"    [Tags]    generated    pairwise    REQ-CAN-011\n"
            f"    [Template]    信号组合应正确收发\n"
            + rows + "\n")

    # ---- 关键字 ----
    parts.append("""*** Keywords ***
环境就绪并暂停 ECU 周期报文
    [Documentation]    启动环境 → 进扩展会话 → 28 01 01 暂停 ECU 周期报文。
    ...               必须暂停：ECU 也在周期发同一报文，不暂停的话测试注入
    ...               的值与 ECU 的周期帧混在总线上，读回结果不可信。
    Start Vehicle Environment
    Enter Extended Session
    Suspend ECU Periodic Messages

恢复 ECU 周期报文并关闭环境
    [Documentation]    28 00 恢复周期报文 → 关闭环境。
    ...               S3 定时器 5 秒会踢回默认会话，所以恢复前要重新进扩展会话。
    Run Keyword And Ignore Error    Enter Extended Session
    Run Keyword And Ignore Error    Resume ECU Periodic Messages
    Stop Vehicle Environment

信号应正确收发
    [Documentation]    发一个信号值 → 从总线读回 → 断言一致（单信号闭环）
    [Arguments]    ${msg}    ${sig}    ${value}    ${desc}
    Send Signal Values    ${msg}    ${sig}=${value}
    ${actual}=    Read Signal From Bus    ${msg}    ${sig}
    ${tolerance}=    Evaluate    max(abs(float(${value})) * 0.02, 0.5)
    ${diff}=    Evaluate    abs(float(${actual}) - float(${value}))
    Should Be True    ${diff} <= ${tolerance}
    ...    msg=${msg}.${sig} 期望 ${value} 实际 ${actual}（${desc}）

信号组合应正确收发
    [Documentation]    一次发同一报文的多个信号 → 读回**同一帧**并校验全部
    ...               （一次编码发一帧，一帧里含多信号；不能"发 1 帧读 N 次"）
    ...               [Template] 每行格式：报文名    短信号名=值    ...
    [Arguments]    ${msg}    &{signals}
    Send Signal Values    ${msg}    &{signals}
    Read Frame And Verify Signals    ${msg}    &{signals}
""")
    return "\n".join(parts)


# ===========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="从 DBC 生成 RF 测试用例")
    parser.add_argument("--dbc", default=str(DBC_PATH))
    parser.add_argument("--message", default=None, help="只生成指定报文")
    parser.add_argument("--pairwise-signals", type=int, default=3,
                        help="参与 pairwise 组合的信号数（默认 3）")
    parser.add_argument("--output", default=str(
        ROOT / "tests" / "robot" / "generated" / "generated_signals.robot"))
    args = parser.parse_args()

    signals = collect_signals(Path(args.dbc), args.message)
    if not signals:
        print("没有解析到信号，请检查 --dbc / --message")
        sys.exit(1)

    content = generate_robot(signals, args.pairwise_signals)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")

    # 摘要
    numeric = [s for s in signals if not s["choices"]]
    enums = [s for s in signals if s["choices"]]
    bva_cases = sum(len(numeric_test_points(s)) if not s["choices"]
                    else len(enum_test_points(s)) for s in signals)
    _msg, multi = pick_pairwise_signals(signals, args.pairwise_signals)
    combo_n = (len(pairwise([(s["name"], [0, 1, 2]) for s in multi]))
               if len(multi) >= 2 else 0)  # 组数只与参数/取值个数有关

    print(f"生成完成: {out}")
    print(f"  信号 {len(signals)} 个（数值 {len(numeric)} / 枚举 {len(enums)}）")
    print(f"  边界值/等价类测试点: {bva_cases}")
    if combo_n:
        print(f"  pairwise 组合: {len(multi)} 信号 × 3 值 = "
              f"全组合 {3 ** len(multi)} 组 → 优化为 {combo_n} 组")
    print()
    print("运行生成用例:")
    print(f"  python -m robot --outputdir results {out}")


if __name__ == "__main__":
    main()
