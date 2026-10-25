"""
test_spec_check.py — 测试规范一致性校验
==========================================

**为什么需要它**：测试规范（Test Spec）是交付物，里面的引用必须真实：
    - `requirement: REQ-XXX` 指向的需求必须存在于 requirements.yaml
    - `automation: 文件::用例名` 指向的用例必须真的存在
一旦需求改了编号、用例改了名字，规范就会"指着不存在的东西"——
审核时这是硬伤。真实项目里这类一致性检查通常做成 CI 的一个门禁。

三类检查：
    ① 规范里的需求 ID 是否都存在（含 related_requirements）
    ② 规范引用的自动化用例是否真的存在（文件 + 用例名）
    ③ 反向检查：规范里的需求是否都有自动化覆盖（提示未自动化项）

用法：
    python test_spec_check.py
    python test_spec_check.py --spec deliverables/test_spec_ic_alarm.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DEFAULT_SPEC = ROOT / "deliverables" / "test_spec_ic_alarm.yaml"
REQ_FILE = ROOT / "requirements" / "requirements.yaml"


def load_yaml(p: Path) -> dict:
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def collect_case_names(robot_file: Path) -> set[str]:
    """取 .robot 文件里的用例名（*** Test Cases *** 段内、无缩进行）"""
    names: set[str] = set()
    in_tc = False
    for line in robot_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("***"):
            in_tc = "test cases" in line.lower()
            continue
        if in_tc and line and not line[0].isspace() and line.strip():
            names.add(line.strip())
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="测试规范一致性校验")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    args = parser.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"找不到测试规范: {spec_path}")
        sys.exit(1)

    spec = load_yaml(spec_path)
    known_reqs = {r["id"] for r in load_yaml(REQ_FILE)["requirements"]}

    print("=" * 72)
    print(f" 测试规范校验：{spec['spec']['id']} {spec['spec']['title']}")
    print("=" * 72)

    errors: list[str] = []
    warnings: list[str] = []
    cases = spec.get("test_cases", [])
    reqs_in_spec: set[str] = set()

    print(f"\n测试项 {len(cases)} 个：")
    for tc in cases:
        tid = tc.get("id", "?")
        req = tc.get("requirement")
        related = tc.get("related_requirements", []) or []
        auto = tc.get("automation")

        # ① 需求引用校验
        for rid in ([req] if req else []) + list(related):
            if rid:
                reqs_in_spec.add(rid)
                if rid not in known_reqs:
                    errors.append(f"{tid}: 引用了不存在的需求 {rid}")
        if not req:
            warnings.append(f"{tid}: 未关联需求（可能不该出现在本 Spec 内）")

        # ② 自动化引用校验
        auto_state = "手工"
        if auto and "::" in auto:
            rel, case_name = auto.split("::", 1)
            robot_path = ROOT / rel.strip()
            if not robot_path.exists():
                errors.append(f"{tid}: 自动化文件不存在 -> {rel}")
            else:
                names = collect_case_names(robot_path)
                if case_name.strip() not in names:
                    errors.append(
                        f"{tid}: 用例名不存在于 {rel} -> 「{case_name.strip()}」")
                else:
                    auto_state = "已自动化 ✓"
        elif auto:
            errors.append(f"{tid}: automation 格式应为 文件::用例名 -> {auto}")

        print(f"  [{tid}] {tc.get('title','')[:34]:<36} "
              f"{req or '(无需求)':<14} {auto_state}")

    # ③ 反向：规范外的需求（本 Spec 未覆盖的）
    covered = sorted(reqs_in_spec)
    print(f"\n本规范关联需求 {len(covered)} 条：{', '.join(covered)}")

    print("\n" + "-" * 72)
    if errors:
        print(f"❌ 发现 {len(errors)} 处**一致性错误**（必须修复）：")
        for e in errors:
            print(f"   · {e}")
    else:
        print("✅ 一致性检查通过：所有需求引用与自动化引用均真实存在")

    if warnings:
        print(f"\n⚠ {len(warnings)} 条提示：")
        for w in warnings:
            print(f"   · {w}")
    print("=" * 72)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
