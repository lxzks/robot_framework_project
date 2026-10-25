"""
判分器：检查 practice.py 的 8 道题完成情况
=============================================

用法：
    python python_practice/check.py                 # 检查 practice.py
    python python_practice/check.py 答案文件.py      # 检查别的文件（比如你自己写的）

每题独立检查，一个失败不影响其他题。全部通过后可以看 answers.md 对照思路。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(path: Path):
    """从任意路径加载 Python 模块。

    ⚠️ 必须先把模块注册到 sys.modules 再 exec_module —— 否则文件里的
    @dataclass 会崩（dataclass 内部靠 sys.modules 查找模块）。
    这是动态导入的一个经典坑。
    """
    mod_name = "practice_under_test"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod          # ← 关键这一行
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


class Checker:
    def __init__(self):
        self.results: list[tuple[str, str, bool, str]] = []

    def check(self, pid: str, title: str, fn):
        """执行一个检查函数 fn() -> (ok, detail)"""
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.results.append((pid, title, ok, detail))

    def report(self) -> int:
        print("\n" + "=" * 62)
        print(" 检查结果")
        print("=" * 62)
        passed = 0
        for pid, title, ok, detail in self.results:
            mark = "✓" if ok else "✗"
            print(f"  [{pid}] {title:<34} {mark}")
            if not ok:
                print(f"        ↳ {detail}")
            passed += ok
        total = len(self.results)
        print("-" * 62)
        print(f"  通过 {passed}/{total}" + ("   🎉 全部完成！" if passed == total else ""))
        print("=" * 62)
        if passed < total:
            print("\n  提示：只补 TODO 不够的话，看每题 docstring 里的【提示】和【验收】。")
            print("  全部通过后再看 answers.md 对照写法。")
        return 0 if passed == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Python 练习判分器")
    parser.add_argument("target", nargs="?", default=str(Path(__file__).parent / "practice.py"))
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"找不到文件: {target}")
        sys.exit(1)

    print(f"检查文件: {target}")
    try:
        m = load_module(target)
    except Exception:
        print("❌ 文件本身有语法错误，无法导入：\n")
        traceback.print_exc()
        sys.exit(1)

    c = Checker()

    # ---- P1 ----
    def p1():
        if not hasattr(m, "calc_rpm"):
            return False, "没有 calc_rpm 函数"
        cases = [(60, 4800), (5, 800), (100, 8000), (0, 800)]
        for sp, expect in cases:
            got = m.calc_rpm(sp)
            if got is None:
                return False, "calc_rpm 返回了 None（TODO 还没写）"
            if abs(float(got) - expect) > 1e-6:
                return False, f"calc_rpm({sp}) 得到 {got}，期望 {expect}"
        return True, ""
    c.check("P1", "写函数：算转速", p1)

    # ---- P2 ----
    def p2():
        if not hasattr(m, "read_json_field"):
            return False, "没有 read_json_field 函数"
        # 不存在的文件
        got = m.read_json_field(ROOT / "绝对不存在的文件.json", "x", "默认值")
        if got != "默认值":
            return False, f"不存在的文件应返回默认值，实际 {got!r}"
        # 存在的文件（临时造一个）
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump({"speed_kmh": 60.0, "nested": {"a": 1}}, f)
            tmp = Path(f.name)
        try:
            got = m.read_json_field(tmp, "speed_kmh")
            if got != 60.0:
                return False, f"读字段得到 {got!r}，期望 60.0"
            got = m.read_json_field(tmp, "不存在字段", "dflt")
            if got != "dflt":
                return False, f"字段不存在应返回默认值，实际 {got!r}"
        finally:
            tmp.unlink(missing_ok=True)
        return True, ""
    c.check("P2", "pathlib 读 JSON 字段", p2)

    # ---- P3 ----
    def p3():
        if not hasattr(m, "DtcRecord"):
            return False, "没有 DtcRecord 类"
        r = m.DtcRecord("P0116")
        if getattr(r, "dtc", None) != "P0116":
            return False, f"dtc 字段不对：{getattr(r, 'dtc', '<缺失>')!r}"
        if getattr(r, "status", None) != 0:
            return False, f"status 默认值应 0，实际 {getattr(r, 'status', '<缺失>')!r}"
        if getattr(r, "count", None) != 0:
            return False, f"count 默认值应 0，实际 {getattr(r, 'count', '<缺失>')!r}"
        if getattr(r, "snapshot", "X") is not None:
            return False, f"snapshot 默认值应 None，实际 {getattr(r, 'snapshot', '<缺失>')!r}"
        # 能改
        r.status = 8
        if r.status != 8:
            return False, "字段不可写"
        return True, ""
    c.check("P3", "dataclass 定义 DTC 记录", p3)

    # ---- P4 ----
    def p4():
        if not hasattr(m, "safe_read_text"):
            return False, "没有 safe_read_text 函数"
        got = m.safe_read_text(ROOT / "绝对不存在的文件.txt")
        if got != "":
            return False, f"不存在的文件应返回 ''，实际 {got!r}"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write("hello 车电")
            tmp = Path(f.name)
        try:
            got = m.safe_read_text(tmp)
            if got != "hello 车电":
                return False, f"读文件内容不对：{got!r}"
        finally:
            tmp.unlink(missing_ok=True)
        return True, ""
    c.check("P4", "try/except 保护读文件", p4)

    # ---- P5 ----
    def p5():
        if not hasattr(m, "lit_telltales"):
            return False, "没有 lit_telltales 函数"
        got = m.lit_telltales({"MilWarning": 0, "CoolantTempHigh": 1, "TurnLeft": 1})
        if list(got) != ["CoolantTempHigh", "TurnLeft"]:
            return False, f"结果不对：{got!r}（应只含值为 1 的灯名）"
        got = m.lit_telltales({})
        if list(got) != []:
            return False, f"空字典应返回 []，实际 {got!r}"
        got = m.lit_telltales({"A": 0, "B": 0})
        if list(got) != []:
            return False, f"全为 0 时应返回 []，实际 {got!r}"
        return True, ""
    c.check("P5", "列表推导取点亮的灯", p5)

    # ---- P6 ----
    def p6():
        if not hasattr(m, "group_by_requirement"):
            return False, "没有 group_by_requirement 函数"
        tests = [("REQ-A", "用例1"), ("REQ-A", "用例2"), ("REQ-B", "用例3")]
        got = m.group_by_requirement(tests)
        expect = {"REQ-A": ["用例1", "用例2"], "REQ-B": ["用例3"]}
        if got != expect:
            return False, f"分组结果不对：{got!r}"
        if m.group_by_requirement([]) != {}:
            return False, "空输入应返回 {}"
        return True, ""
    c.check("P6", "setdefault 分组统计", p6)

    # ---- P7 ----
    def p7():
        if not hasattr(m, "build_fault_rules"):
            return False, "没有 build_fault_rules 函数"
        rules = m.build_fault_rules()
        if not isinstance(rules, dict) or "P0116" not in rules or "P0562" not in rules:
            return False, f"规则表结构不对：{rules!r}"
        hit = [k for k, v in rules.items()
               if v["detect"]({"coolant": 125, "voltage": 13.8})]
        if "P0116" not in hit:
            return False, f"水温 125 应命中 P0116，实际命中 {hit}"
        hit = [k for k, v in rules.items()
               if v["detect"]({"coolant": 90, "voltage": 9.0})]
        if "P0562" not in hit:
            return False, f"电压 9V 应命中 P0562，实际命中 {hit}"
        hit = [k for k, v in rules.items()
               if v["detect"]({"coolant": 90, "voltage": 13.8})]
        if hit:
            return False, f"正常状态不该命中任何故障，实际命中 {hit}"
        return True, ""
    c.check("P7", "lambda 构造故障规则表", p7)

    # ---- P8 ----
    def p8():
        if not hasattr(m, "simulate_periodic"):
            return False, "没有 simulate_periodic 函数"
        calls = []
        n = m.simulate_periodic(1.0, 100, lambda: calls.append(1))
        if not isinstance(n, int):
            return False, f"应返回整数次数，实际 {type(n).__name__}"
        if not (9 <= n <= 11):
            return False, f"1 秒 / 100ms 应约 10 次，实际 {n}"
        if len(calls) != n:
            return False, f"返回 {n} 但实际调用 {len(calls)} 次，不一致"
        return True, ""
    c.check("P8", "周期任务循环（dt 累加）", p8)

    sys.exit(c.report())


if __name__ == "__main__":
    main()
