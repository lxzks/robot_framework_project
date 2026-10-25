"""
run_tests.py — 统一测试入口（本地 / CI 通用）
================================================

解决一个具体问题：RF 通过 Process 库启动仿真子进程（ecu_sim、dashboard、
android_gui），需要知道"用哪个 Python 解释器"。硬编码路径在 CI 上必挂，
所以统一在这里注入：

    python run_tests.py                    # 跑全部套件（含生成的用例）
    python run_tests.py --suite 02         # 只跑某个套件（按名字过滤）
    python run_tests.py --generate         # 先重新生成用例再跑
    python run_tests.py --report           # 跑完顺便出交付报告 + 覆盖率

内部做的事：
    1. 用**当前解释器**（sys.executable）作为子进程解释器注入 RF 变量 PYTHON
    2. 可选先跑用例生成器（DBC → 边界值/pairwise 用例）
    3. 跑 robot，把 stdout/stderr 透传
    4. 可选生成测试报告（generate_report.py）、覆盖率（coverage_report.py）
       与需求追溯矩阵（trace_matrix.py）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TESTS_DIR = ROOT / "tests" / "robot"
SCENARIOS_DIR = ROOT / "scenarios"
RESULTS = ROOT / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description="robot_framework 统一测试入口")
    parser.add_argument("--suite", default=None,
                        help="只跑名字含该字符串的套件（如 02 / flash）")
    parser.add_argument("--tag", default=None, help="只跑带该标签的用例")
    parser.add_argument("--generate", action="store_true",
                        help="先重新生成用例（DBC → 边界值/pairwise）")
    parser.add_argument("--report", action="store_true",
                        help="跑完生成交付报告和覆盖率报告")
    parser.add_argument("--robot-args", default="",
                        help="透传给 robot 的额外参数")
    args = parser.parse_args()

    py = sys.executable
    print(f"[run_tests] 解释器: {py}")
    print(f"[run_tests] 工作目录: {ROOT}")

    # 1. 可选：重新生成用例
    if args.generate:
        print("\n[run_tests] 生成测试用例 ...")
        rc = subprocess.call([py, str(ROOT / "generate_testcases.py")])
        if rc != 0:
            print("[run_tests] 用例生成失败，中止")
            return rc

    # 2. 组装 robot 命令
    # 默认跑"主套件 + 场景"两部分（场景是项目交付的一部分，
    # 漏掉会导致需求追溯/覆盖率统计不完整 —— 真实踩过）
    default_targets = [TESTS_DIR, SCENARIOS_DIR]
    targets = [str(t) for t in default_targets]
    if args.suite:
        candidates = [p for base in default_targets
                      for p in base.rglob("*.robot")
                      if args.suite.lower() in p.name.lower()
                      or args.suite.lower() in str(p.parent.name).lower()]
        if not candidates:
            print(f"[run_tests] 没有匹配 '{args.suite}' 的套件")
            return 1
        targets = [str(p) for p in candidates]

    cmd = [py, "-m", "robot",
           "--outputdir", str(RESULTS),
           # 注入子进程解释器（common.resource 里的 ${PYTHON}）
           "--variable", f"PYTHON:{py}",
           "--consolewidth", "120"]
    if args.tag:
        cmd += ["--include", args.tag]
    if args.robot_args:
        cmd += args.robot_args.split()
    # 默认生成 report/log（CI 归档需要）
    cmd += ["--report", "report.html", "--log", "log.html"]
    cmd += targets

    print(f"\n[run_tests] {' '.join(cmd)}\n" + "-" * 70)
    # 环境变量兜底：即使有人直接调 robot，%{PYTHON_EXE=python} 也能取到
    env = dict(os.environ, PYTHON_EXE=py)
    rc = subprocess.call(cmd, env=env)

    # 3. 可选：报告与覆盖率
    if args.report:
        print("\n[run_tests] 生成交付报告 ...")
        subprocess.call([py, str(ROOT / "generate_report.py")], env=env)
        print("\n[run_tests] 生成覆盖率报告 ...")
        subprocess.call([py, str(ROOT / "coverage_report.py")], env=env)
        print("\n[run_tests] 生成需求追溯矩阵（RTM）...")
        subprocess.call([py, str(ROOT / "trace_matrix.py")], env=env)

    print(f"\n[run_tests] robot 退出码: {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
