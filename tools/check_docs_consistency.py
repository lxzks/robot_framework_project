"""tools/check_docs_consistency.py — 文档数字一致性检查（CI 门禁）
==================================================================

为什么需要：本项目的内容规模（用例数/需求数/教程篇数/关键字库/训练练习/
pytest 单测数）会被 README、docs/00、KNOWN_ISSUES、GIT_GUIDE、project_brief
等 **十几处** 引用；每次加模块都要人肉同步，历史上已多次漂移。

本脚本：
1. 从仓库真实文件**现场统计**这些数字：
   - 主套件/场景的 Robot 用例数（解析 .robot）
   - requirements.yaml 需求条数
   - docs/*.md 教程篇数
   - libraries/*.py 关键字库个数
   - training/L* 训练练习个数
   - tools/pytest_style 里的 pytest 单元测试个数
2. 检查"权威文档"里是否包含正确数字（缺失即失败）。
3. 扫描全仓 Markdown 是否残留**过期数字黑名单**（例如旧版本用例数/旧耗时）。

用法：
    python tools/check_docs_consistency.py            # 检查
    python tools/check_docs_consistency.py --verbose  # 打印全部统计
退出码：0=一致；1=发现不一致/过期数字。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 过期数字黑名单：这些字符串一旦出现在文档里，说明有人还在用旧口径。
# 新增规则时尽量写"带单位的完整短语"，避免误伤（例如 111 这种裸数字不列）。
STALE_PATTERNS = [
    r"116\s*个",
    r"116\s*用例",
    r"165\s*个",
    r"238\s*个",
    r"238\s*用例",
    r"160\s*主套件",
    r"111\s*主套件",
    r"108\s*条需求",
    r"86\s*条需求",
    r"(?<!\d)35\s*篇",
    r"14\s*个关键字库",
    r"10\s*个练习",
    r"301\.9\s*s",
    r"358\.8\s*s",
    r"379\.5\s*s",
    r"6\.5\s*分钟",
]

MD_GLOBS = [
    "README.md",
    "GIT_GUIDE.md",
    "KNOWN_ISSUES.md",
    "docs/*.md",
    "deliverables/*.md",
    "start_here/*.md",
    "training/README.md",
    "training/cheatsheet.md",
]


def count_rf_tests(path: Path) -> int:
    """统计一个 .robot 文件里的用例数（Test Cases 段内、列 0 非空行）。"""
    tests = 0
    in_tc = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if re.match(r"^\*{3,}\s*(Test Cases|测试用例)\s*\*{3,}\s*$", line, re.I):
            in_tc = True
            continue
        if re.match(r"^\*{3,}", line):
            in_tc = False
            continue
        if in_tc and line.strip() and not line.lstrip().startswith("#") \
                and raw[0] not in (" ", "\t"):
            tests += 1
    return tests


def count_pytest_tests() -> int:
    n = 0
    for p in (ROOT / "tools" / "pytest_style").glob("test_*.py"):
        n += len(re.findall(r"^\s*def test_", p.read_text(encoding="utf-8"), re.M))
    return n


def collect_metrics(root: Path = ROOT) -> dict:
    """现场统计项目真实数字（纯函数，供单元测试与检查共用）。"""
    main_files = sorted((root / "tests" / "robot").rglob("*.robot"))
    scen_files = sorted((root / "scenarios").glob("*.robot"))
    main_tests = sum(count_rf_tests(p) for p in main_files)
    scen_tests = sum(count_rf_tests(p) for p in scen_files)

    req_text = (root / "requirements" / "requirements.yaml").read_text(
        encoding="utf-8")
    reqs = len(re.findall(r"^\s*-\s*id:\s*REQ-", req_text, re.M))

    docs = len(list((root / "docs").glob("*.md")))
    libs = len(list((root / "libraries").glob("*.py")))
    levels = len([d for d in (root / "training").glob("L*")
                  if d.is_dir() and (d / "solution.robot").exists()])

    return {
        "main_tests": main_tests,
        "scenario_tests": scen_tests,
        "total_tests": main_tests + scen_tests,
        "main_files": len(main_files),
        "scenario_files": len(scen_files),
        "requirements": reqs,
        "docs": docs,
        "libraries": libs,
        "training_levels": levels,
        "pytest_tests": count_pytest_tests(),
    }


def collect_md_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for pattern in MD_GLOBS:
        files.extend(sorted(root.glob(pattern)))
    # 去重（同一文件可能被多个 pattern 命中）
    return sorted(set(files))


def find_stale_numbers(root: Path = ROOT) -> list[tuple[str, int, str, str]]:
    """返回 [(相对路径, 行号, 命中规则, 该行文本)]。"""
    hits: list[tuple[str, int, str, str]] = []
    compiled = [(p, re.compile(p)) for p in STALE_PATTERNS]
    for f in collect_md_files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pattern, rx in compiled:
                if rx.search(line):
                    rel = str(f.relative_to(root))
                    hits.append((rel, i, pattern, line.strip()[:160]))
    return hits


def check_required_phrases(m: dict, root: Path = ROOT) -> list[str]:
    """检查权威文档里是否包含正确数字；返回问题列表。"""
    problems: list[str] = []
    required = {
        "README.md": [
            f"{m['total_tests']} 个",
            f"{m['requirements']} 条",
            f"{m['docs']} 篇",
            f"{m['libraries']} 个",
            "12 个" if m["training_levels"] == 12 else f"{m['training_levels']} 个",
        ],
        "docs/00_学习路线图.md": [
            f"{m['total_tests']}",
            f"{m['requirements']} 条",
            f"{m['docs']} 篇",
            f"{m['training_levels']} 个",
        ],
        "KNOWN_ISSUES.md": [
            f"{m['total_tests']} 条",
            f"{m['requirements']} 条",
            f"{m['docs']} 篇",
        ],
        "GIT_GUIDE.md": [
            f"{m['total_tests']} 用例",
        ],
    }
    for rel, phrases in required.items():
        p = root / rel
        if not p.exists():
            problems.append(f"缺文件: {rel}")
            continue
        text = p.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel} 缺少正确数字短语: {phrase!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="文档数字一致性检查")
    parser.add_argument("--verbose", action="store_true", help="打印全部统计")
    parser.add_argument("--allow-stale", default="",
                        help="逗号分隔的允许出现的过期规则（如某章要讲历史）")
    args = parser.parse_args()

    m = collect_metrics()
    if args.verbose:
        print("现场统计：")
        for k, v in m.items():
            print(f"  {k:16} = {v}")

    problems = check_required_phrases(m)
    allow = {x.strip() for x in args.allow_stale.split(",") if x.strip()}
    stale = [h for h in find_stale_numbers() if h[2] not in allow]

    print("=" * 62)
    print(" 文档数字一致性检查")
    print("=" * 62)
    print(f"用例: 主套件 {m['main_tests']} + 场景 {m['scenario_tests']}"
          f" = {m['total_tests']}；需求 {m['requirements']}；教程 {m['docs']} 篇；"
          f"库 {m['libraries']}；训练 {m['training_levels']}；"
          f"pytest {m['pytest_tests']}")

    if problems:
        print(f"\n❌ 权威文档数字缺失/不一致 {len(problems)} 处：")
        for p in problems:
            print("   -", p)
    if stale:
        print(f"\n❌ 发现过期数字黑名单命中 {len(stale)} 处：")
        for rel, line_no, pattern, text in stale[:50]:
            print(f"   - {rel}:{line_no} [{pattern}] {text}")

    if problems or stale:
        return 1
    print("\n✅ 全部一致：权威数字存在，且全仓无过期数字。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

