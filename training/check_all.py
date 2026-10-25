"""
check_all.py — 实训判分器：一键验证所有练习的参考答案
=========================================================

作用：跑一遍全部 6 个练习的 solution.robot，确认参考答案都能通过。
用途：
    1. 自学者：写完自己的答案后，用它对照"参考答案确实是对的"
    2. 环境自检：跑不通说明环境有问题（而不是你的脚本有问题）
    3. 维护：改了仿真环境后，回归验证练习没被改坏

用法：
    python training/check_all.py              # 跑全部
    python training/check_all.py --level L6   # 只跑某个练习
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ROOT / "training"

LEVELS = [
    ("L1_first_case", "L1 第一个脚本（读+断言）"),
    ("L2_wait_and_assert", "L2 轮询等待"),
    ("L3_diagnostic_basics", "L3 诊断三步"),
    ("L4_negative_test", "L4 负向测试"),
    ("L5_data_driven", "L5 数据驱动"),
    ("L6_full_cycle", "L6 完整闭环"),
]


def run_one(level: str, name: str) -> tuple[bool, str]:
    """跑一个练习的 solution，返回 (是否通过, 摘要)"""
    target = TRAINING / level / "solution.robot"
    if not target.exists():
        return False, "参考答案不存在"
    out_dir = ROOT / "results" / "training" / level
    proc = subprocess.run(
        [sys.executable, "-m", "robot", "--outputdir", str(out_dir),
         "--report", "NONE", "--log", "NONE", str(target)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        cwd=str(ROOT),
    )
    # 从 robot 输出里抓最后一行统计
    summary = ""
    for line in reversed(proc.stdout.splitlines()):
        if "tests," in line and ("passed" in line or "failed" in line):
            summary = line.strip()
            break
    ok = proc.returncode == 0
    return ok, summary or "（未解析到统计）"


def main() -> int:
    parser = argparse.ArgumentParser(description="实训判分器")
    parser.add_argument("--level", default=None, help="只跑某个练习（如 L6）")
    args = parser.parse_args()

    targets = [lv for lv in LEVELS
               if args.level is None or lv[0].startswith(args.level)]
    if not targets:
        print(f"没有匹配 '{args.level}' 的练习")
        return 1

    print("=" * 66)
    print(" 车载自动化脚本实训 · 参考答案验证")
    print("=" * 66)

    results = []
    for level, label in targets:
        print(f"\n▶ {label} ...")
        ok, summary = run_one(level, label)
        mark = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {mark}  {summary}")
        results.append((label, ok, summary))

    passed = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 66)
    print(f" 结果：{passed}/{len(results)} 个练习的参考答案通过")
    if passed != len(results):
        print(" 失败的练习请检查环境（先跑 python run_tests.py 看主套件是否正常）")
        for label, ok, summary in results:
            if not ok:
                print(f"   - {label}: {summary}")
    else:
        print(" 环境正常，可以开始做题了 —— 打开 training/L1_first_case/README.md")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
