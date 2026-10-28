"""
trace_matrix.py — 需求追溯矩阵（Requirements Traceability Matrix, RTM）
=======================================================================

**这是 System Validation 岗位的核心交付物之一。**

在 Tier1，测试不是"想到什么测什么"：每条用例都要能追溯到一条需求，
反过来每条需求都要有验证手段。客户/审核会问三个问题：

    1. 这条需求怎么验证的？（需求 → 用例）
    2. 这条用例在验什么需求？（用例 → 需求）
    3. 哪些需求还没被验证？（覆盖缺口）

本工具自动回答这三个问题：

    需求清单 (requirements/requirements.yaml)
            │  用例用 [Tags] REQ-XXX 关联
            ▼
    Robot Framework 执行结果 (results/output.xml)
            │
            ▼
    追溯矩阵：需求 ↔ 用例 ↔ 结果
      ├─ 需求覆盖率（有多少需求有对应用例）
      ├─ 未覆盖需求清单 ← 交给测试主管决定"补测试"还是"标记不适用"
      ├─ 孤儿用例（没有关联需求的用例）← 反查漏写需求或冗余用例
      └─ 按需求的通过情况 ← 需求维度的质量视图（而不是用例维度）

用法：
    python -m robot --outputdir results tests/robot/ scenarios/    # 先跑测试
    python trace_matrix.py                                        # 生成矩阵
    python trace_matrix.py --html results/trace_matrix.html        # 另存 HTML
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path


def _force_utf8_stdio() -> None:
    """Windows 下 stdout 被重定向时默认 GBK，打印 ✓/✅ 会 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


_force_utf8_stdio()

ROOT = Path(__file__).parent
REQ_FILE = ROOT / "requirements" / "requirements.yaml"
DEFAULT_XML = ROOT / "results" / "output.xml"

# 需求 ID 约定：REQ-<域>-<序号>（与用例 [Tags] 里的写法一致）
REQ_PATTERN = re.compile(r"^REQ-[A-Z]+-\d{3}$")


# ===========================================================================
# 1. 读取需求清单
# ===========================================================================
def load_requirements(path: Path) -> list[dict]:
    """读需求清单（SRS 摘录）。校验 ID 唯一性与格式"""
    import yaml
    if not path.exists():
        print(f"找不到需求清单: {path}")
        sys.exit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    reqs = data.get("requirements", [])
    seen = set()
    for r in reqs:
        rid = r.get("id", "")
        if not REQ_PATTERN.match(rid):
            print(f"⚠ 需求 ID 格式不符（应形如 REQ-CAN-001）: {rid}")
        if rid in seen:
            print(f"⚠ 需求 ID 重复: {rid}")
        seen.add(rid)
    return reqs


# ===========================================================================
# 2. 读取执行结果里的用例与标签
# ===========================================================================
def iter_tests(suite):
    """递归取所有用例，返回 (用例所在的直接套件, 用例)。

    注意 RF 结果树是懒加载的，必须显式访问 tests。
    """
    for test in suite.tests:
        yield suite, test
    for sub in suite.suites:
        yield from iter_tests(sub)


def collect_tests(xml_path: Path) -> list[dict]:
    """从 RF 结果里提取每个用例的名字/套件/标签/状态。

    必须遍历**根套件本身**：单套件运行（如 `robot tests/robot/01_can_signals.robot`）
    时用例直接挂在根套件上，只遍历 `result.suite.suites` 会得到 0 用例、
    需求覆盖率误报 0%。套件名取用例所在的**直接**父套件（01 Can Signals 等），
    这样 RTM 里仍能按文件区分用例来源。
    """
    from robot.api import ExecutionResult
    if not xml_path.exists():
        print(f"找不到执行结果: {xml_path} —— 先跑一遍测试")
        sys.exit(1)

    result = ExecutionResult(str(xml_path))
    out = []
    for parent, test in iter_tests(result.suite):
        out.append({
            "name": test.name,
            "suite": parent.name,
            "tags": set(test.tags or []),
            "status": test.status,          # PASS / FAIL / SKIP
            "elapsed_ms": test.elapsedtime,
        })
    return out


# ===========================================================================
# 3. 建立追溯关系
# ===========================================================================
def build_matrix(reqs: list[dict], tests: list[dict]) -> dict:
    """建立 需求 ↔ 用例 双向映射并统计"""
    by_req: dict[str, list[dict]] = {r["id"]: [] for r in reqs}
    orphans: list[dict] = []

    for t in tests:
        linked = [tag for tag in t["tags"] if REQ_PATTERN.match(str(tag))]
        if not linked:
            orphans.append(t)
            continue
        for rid in linked:
            by_req.setdefault(rid, []).append(t)

    covered, uncovered = [], []
    for r in reqs:
        cases = by_req.get(r["id"], [])
        passed = sum(1 for c in cases if c["status"] == "PASS")
        failed = sum(1 for c in cases if c["status"] == "FAIL")
        entry = {
            "req": r,
            "cases": cases,
            "passed": passed,
            "failed": failed,
            "covered": len(cases) > 0,
        }
        (covered if entry["covered"] else uncovered).append(entry)

    # 引用不存在的需求的 tag（写错了）
    known = {r["id"] for r in reqs}
    dangling = sorted({rid for rid in by_req if rid not in known})

    return {
        "total_reqs": len(reqs),
        "covered": covered,
        "uncovered": uncovered,
        "by_req": by_req,
        "orphans": orphans,
        "dangling": dangling,
        "total_tests": len(tests),
    }


# ===========================================================================
# 4. 输出
# ===========================================================================
def pct(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "n/a"


def print_report(m: dict) -> None:
    print("=" * 78)
    print(" 需求追溯矩阵（Requirements Traceability Matrix）")
    print("=" * 78)
    n_ok = len(m["covered"])
    print(f"\n 需求总数 {m['total_reqs']}   已覆盖 {n_ok} ({pct(n_ok, m['total_reqs'])})"
          f"   未覆盖 {len(m['uncovered'])}")
    print(f" 用例总数 {m['total_tests']}   孤儿用例 {len(m['orphans'])}"
          f"（没有关联任何需求）")

    print("\n【已覆盖需求】")
    for e in m["covered"]:
        r = e["req"]
        mark = "✅" if e["failed"] == 0 else "❌"
        detail = f"{len(e['cases'])} 用例"
        if e["failed"]:
            detail += f"（{e['failed']} 失败）"
        print(f"  {mark} [{r['id']}] {r['title'][:30]:<32} "
              f"{r.get('priority', '-'):<7} {detail}")

    if m["uncovered"]:
        print(f"\n【未覆盖需求 {len(m['uncovered'])} 条】← 需决定：补测试 或 标记为不适用")
        for e in m["uncovered"]:
            r = e["req"]
            note = f"  ({r['note']})" if r.get("note") else ""
            print(f"  ✗ [{r['id']}] {r['title'][:34]:<36} {r.get('priority','-'):<7}{note}")

    if m["orphans"]:
        print(f"\n【孤儿用例 {len(m['orphans'])} 条】← 检查：漏写需求？还是冗余用例？")
        for t in m["orphans"][:15]:
            print(f"  · {t['name'][:56]}  [{t['suite'][:24]}]")
        if len(m["orphans"]) > 15:
            print(f"  ... 其余 {len(m['orphans']) - 15} 条")

    if m["dangling"]:
        print(f"\n【无效引用】用例引用了不存在的需求 ID: {', '.join(m['dangling'])}")

    total_cases = sum(len(e["cases"]) for e in m["covered"])
    total_failed = sum(e["failed"] for e in m["covered"])
    print("\n" + "=" * 78)
    print(f" 验证指标：需求覆盖率 {pct(n_ok, m['total_reqs'])}"
          f" | 需求-用例关联关系 {total_cases} 条"
          f"（{m['total_tests']} 个用例，一个用例可关联多条需求）"
          f" | 需求维度失败 {total_failed} 条")
    print("=" * 78)


def render_html(m: dict) -> str:
    def esc(s) -> str:
        return html.escape(str(s))

    rows = ""
    for e in m["covered"] + m["uncovered"]:
        r = e["req"]
        if not e["covered"]:
            status, color = "未覆盖", "#d29922"
        elif e["failed"]:
            status, color = f"{e['failed']} 条失败", "#f85149"
        else:
            status, color = "全部通过", "#3fb950"
        case_list = "<br>".join(
            f"· {esc(c['name'])}" + ("" if c["status"] == "PASS" else f" <b>[{c['status']}]</b>")
            for c in e["cases"]) or "—"
        rows += f"""<tr>
          <td class="mono">{esc(r['id'])}</td>
          <td>{esc(r['title'])}<div class="dim">{esc(r.get('description','')[:80])}</div></td>
          <td>{esc(r.get('source',''))}</td>
          <td>{esc(r.get('priority',''))}</td>
          <td>{esc(r.get('method',''))}</td>
          <td class="num">{len(e['cases'])}</td>
          <td style="color:{color};font-weight:600">{status}</td>
          <td class="dim">{case_list}</td>
        </tr>"""

    orphan_rows = "".join(
        f"<tr><td>{esc(t['name'])}</td><td class='dim'>{esc(t['suite'])}</td>"
        f"<td>{t['status']}</td></tr>" for t in m["orphans"])

    n_ok = len(m["covered"])
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>需求追溯矩阵</title><style>
body{{background:#0d1117;color:#e6edf3;font-family:"Segoe UI","Microsoft YaHei",sans-serif;padding:30px;line-height:1.6}}
h1{{font-size:22px}} h2{{color:#58a6ff;font-size:16px;margin-top:26px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
th,td{{border-bottom:1px solid #2a3140;padding:7px 10px;text-align:left;vertical-align:top}}
th{{color:#8b949e;background:#11161d;position:sticky;top:0}}
.mono{{font-family:Consolas,monospace}} .dim{{color:#8b949e;font-size:11.5px}}
.num{{text-align:right}}
.kpi{{display:flex;gap:14px;margin:14px 0}}
.kpi div{{background:#161b22;border:1px solid #2a3140;border-radius:10px;padding:12px 18px}}
.kpi b{{display:block;font-size:22px;color:#58a6ff}}
</style></head><body>
<h1>需求追溯矩阵（RTM）</h1>
<div class="dim">项目：{esc(m.get('project',''))} · 生成自 requirements.yaml + Robot Framework 执行结果</div>
<div class="kpi">
  <div><b>{pct(n_ok, m['total_reqs'])}</b>需求覆盖率 {n_ok}/{m['total_reqs']}</div>
  <div><b>{len(m['uncovered'])}</b>未覆盖需求</div>
  <div><b>{m['total_tests']}</b>用例总数</div>
  <div><b>{len(m['orphans'])}</b>孤儿用例</div>
</div>
<h2>需求 ↔ 用例 ↔ 结果</h2>
<table><tr><th>需求 ID</th><th>需求</th><th>来源</th><th>优先级</th><th>方法</th>
<th>用例数</th><th>状态</th><th>关联用例</th></tr>{rows}</table>
<h2>孤儿用例（未关联需求）</h2>
<table><tr><th>用例</th><th>套件</th><th>状态</th></tr>{orphan_rows or '<tr><td colspan="3">无</td></tr>'}</table>
</body></html>"""


# ===========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="需求追溯矩阵生成器")
    parser.add_argument("--requirements", default=str(REQ_FILE))
    parser.add_argument("--robot-xml", default=str(DEFAULT_XML))
    parser.add_argument("--html", default=str(ROOT / "results" / "trace_matrix.html"))
    args = parser.parse_args()

    reqs = load_requirements(Path(args.requirements))
    tests = collect_tests(Path(args.robot_xml))
    m = build_matrix(reqs, tests)
    print_report(m)

    out = Path(args.html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(m), encoding="utf-8")
    print(f"\nHTML 追溯矩阵: {out}")


if __name__ == "__main__":
    main()
