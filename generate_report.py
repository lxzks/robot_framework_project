"""
generate_report.py — 测试报告自动化（Tier1 交付物形态）
=========================================================

把三类产物合成一份可交付的 HTML 报告：

    1. Robot Framework 执行结果（results/output.xml）
       → 通过率、套件明细、失败用例详情
    2. 总线 Trace 分析（results/bus_trace.asc）
       → 每个 CAN ID 的帧数、周期均值、**周期抖动（Jitter）**、DLC
       （周期抖动是总线健康度的核心指标，真实台架排故必看）
    3. 产物清单（log.html / 仪表快照 / Trace 文件链接）

为什么需要它：Tier1 的测试交付不是"跑完了"，而是一份能给别人看的
报告 —— 测试主管/客户看结论，开发拿着 Trace 复现问题。

用法：
    python -m robot --outputdir results tests/robot/     # 先跑测试
    python generate_report.py                            # 再生成报告
    python generate_report.py --output results/summary.html
"""

from __future__ import annotations

import argparse
import html
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "simulators"))


# ===========================================================================
# 数据采集
# ===========================================================================
def collect_robot_stats(output_xml: Path) -> dict:
    """读 RF 结果：总数/通过/失败/耗时 + 套件明细 + 失败用例"""
    from robot.api import ExecutionResult
    if not output_xml.exists():
        return {"available": False, "reason": f"{output_xml.name} 不存在"}

    result = ExecutionResult(str(output_xml))
    stats = result.statistics
    total = stats.total
    suites = []
    for suite in result.suite.suites:
        s = suite.statistics
        suites.append({
            "name": suite.name,
            "doc": (suite.doc or "").split("\n")[0][:80],
            "total": s.total,
            "passed": s.passed,
            "failed": s.failed,
            "elapsed_s": round(suite.elapsedtime / 1000, 1),
            "status": suite.status,
        })

    failures = []

    def walk(suite) -> None:
        for test in suite.tests:
            if test.status == "FAIL":
                msg = ""
                for child in getattr(test, "body", []):
                    if getattr(child, "status", "") == "FAIL":
                        msg = getattr(child, "message", "") or ""
                        break
                failures.append({
                    "suite": suite.name,
                    "name": test.name,
                    "message": (msg or "（无消息）").split("\n")[0][:200],
                })
        for sub in suite.suites:
            walk(sub)

    walk(result.suite)
    return {
        "available": True,
        "total": total.total,
        "passed": total.passed,
        "failed": total.failed,
        "pass_rate": round(total.passed / total.total * 100, 1) if total.total else 0,
        "elapsed_s": round(result.suite.elapsedtime / 1000, 1),
        "suites": suites,
        "failures": failures,
    }


def analyze_trace(asc_path: Path) -> dict:
    """从 ASC Trace 分析总线活动：每 ID 的帧数/周期/抖动/DLC"""
    from trace_tools import verify_asc
    if not asc_path.exists():
        return {"available": False, "reason": f"{asc_path.name} 不存在"}

    frames = verify_asc(asc_path)
    if not frames:
        return {"available": False, "reason": "Trace 为空"}

    by_id: dict[int, list[tuple[float, int, bytes]]] = {}
    for ts, aid, dlc, data in frames:
        by_id.setdefault(aid, []).append((ts, dlc, data))

    msgs = []
    for aid, recs in sorted(by_id.items()):
        gaps_ms = [round((recs[i + 1][0] - recs[i][0]) * 1000, 2)
                   for i in range(len(recs) - 1)]
        period_avg = round(statistics.mean(gaps_ms), 2) if gaps_ms else 0.0
        jitter = round(max(gaps_ms) - min(gaps_ms), 2) if len(gaps_ms) > 1 else 0.0
        msgs.append({
            "id": f"0x{aid:X}",
            "count": len(recs),
            "dlc": recs[0][1],
            "period_ms": period_avg,
            "jitter_ms": jitter,
            "last_data": recs[-1][2].hex().upper(),
        })

    span_s = round(frames[-1][0] - frames[0][0], 2)
    return {
        "available": True,
        "frame_total": len(frames),
        "span_s": span_s,
        "id_count": len(by_id),
        "messages": msgs,
    }


def collect_requirement_metrics(output_xml: Path) -> dict:
    """需求维度的验证指标（复用 trace_matrix 的追溯逻辑，避免两套实现）。

    这是 System Validation 报告区别于普通测试报告的地方：
    不只报"用例通过率"，还要报"**需求覆盖率**"和未覆盖需求清单。
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from trace_matrix import (REQ_FILE, build_matrix, collect_tests,
                                  load_requirements)
        m = build_matrix(load_requirements(REQ_FILE), collect_tests(output_xml))
        return {
            "available": True,
            "total": m["total_reqs"],
            "covered": len(m["covered"]),
            "uncovered": [e["req"] for e in m["uncovered"]],
            "orphans": len(m["orphans"]),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}


# ===========================================================================
# HTML 渲染
# ===========================================================================
def _esc(s) -> str:
    return html.escape(str(s))


def render_html(robot: dict, trace: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 需求维度指标（System Validation 关注点）----
    rm = collect_requirement_metrics(Path(str(ROOT / "results" / "output.xml")))
    if rm["available"]:
        cov = rm["covered"] / rm["total"] * 100 if rm["total"] else 0
        req_card = (f'<div class="card"><div class="k">需求覆盖率</div>'
                    f'<div class="v">{cov:.1f}%</div></div>')
        if rm["uncovered"]:
            items = "".join(
                f"<li>[{u['id']}] {u['title']}（{u.get('priority','-')}）</li>"
                for u in rm["uncovered"])
            req_section = (f'<h2>未覆盖需求（{len(rm["uncovered"])}）</h2>'
                           f'<p class="warn">这些需求目前没有对应用例 —— 需决定"补测试"'
                           f'还是"标记为不适用"（追溯矩阵结果）</p>'
                           f'<ul class="fails">{items}</ul>')
        else:
            req_section = '<h2>未覆盖需求</h2><p class="ok">全部需求均已被测试覆盖 🎉</p>'
    else:
        req_card = ''
        req_section = (f'<h2>需求追溯</h2><p class="warn">不可用：'
                       f'{rm.get("reason", "")}</p>')

    # ---- 概览卡片 ----
    if robot["available"]:
        ok = robot["passed"] == robot["total"]
        verdict = "PASS" if ok else "FAIL"
        verdict_color = "#3fb950" if ok else "#f85149"
        cards = f"""
        <div class="cards">
          <div class="card"><div class="k">用例总数</div><div class="v">{robot['total']}</div></div>
          <div class="card"><div class="k">通过</div><div class="v ok">{robot['passed']}</div></div>
          <div class="card"><div class="k">失败</div><div class="v {'bad' if robot['failed'] else ''}">{robot['failed']}</div></div>
          <div class="card"><div class="k">通过率</div><div class="v">{robot['pass_rate']}%</div></div>
          <div class="card"><div class="k">总耗时</div><div class="v">{robot['elapsed_s']}s</div></div>
          <div class="card"><div class="k">结论</div><div class="v" style="color:{verdict_color}">{verdict}</div></div>
          {req_card}
        </div>"""
    else:
        cards = f"<p class='warn'>Robot 结果不可用：{_esc(robot['reason'])}</p>"

    # ---- 套件明细 ----
    suite_rows = ""
    if robot["available"]:
        for s in robot["suites"]:
            color = "#3fb950" if s["failed"] == 0 else "#f85149"
            suite_rows += f"""
            <tr>
              <td>{_esc(s['name'])}</td>
              <td class="mono">{_esc(s['doc'])}</td>
              <td class="num">{s['total']}</td>
              <td class="num ok">{s['passed']}</td>
              <td class="num {'bad' if s['failed'] else ''}">{s['failed']}</td>
              <td class="num">{s['elapsed_s']}s</td>
              <td style="color:{color};font-weight:600">{s['status']}</td>
            </tr>"""

    # ---- 失败详情 ----
    fail_html = ""
    if robot["available"] and robot["failures"]:
        rows = "".join(
            f"<li><b>{_esc(f['name'])}</b> <span class='dim'>（{_esc(f['suite'])}）</span>"
            f"<br><span class='mono bad'>{_esc(f['message'])}</span></li>"
            for f in robot["failures"])
        fail_html = f"<h2>失败用例（{len(robot['failures'])}）</h2><ul class='fails'>{rows}</ul>"
    elif robot["available"]:
        fail_html = "<h2>失败用例</h2><p class='ok'>无失败用例 🎉</p>"

    # ---- 总线活动 ----
    if trace["available"]:
        t_rows = ""
        for m in trace["messages"]:
            jitter_cls = "bad" if m["jitter_ms"] > 20 else ""
            t_rows += f"""
            <tr>
              <td class="mono">{m['id']}</td>
              <td class="num">{m['count']}</td>
              <td class="num">{m['dlc']}</td>
              <td class="num">{m['period_ms']} ms</td>
              <td class="num {jitter_cls}">{m['jitter_ms']} ms</td>
              <td class="mono dim">{_esc(m['last_data'])}</td>
            </tr>"""
        trace_html = f"""
        <h2>总线活动分析 <span class="dim">（{trace['frame_total']} 帧 / {trace['span_s']}s / {trace['id_count']} 个 ID）</span></h2>
        <p class="dim">周期抖动（Jitter）= 相邻帧间隔的最大差 —— 总线健康度核心指标，&gt;20ms 标红示警。</p>
        <table>
          <thead><tr><th>CAN ID</th><th>帧数</th><th>DLC</th><th>周期均值</th><th>抖动</th><th>末帧数据</th></tr></thead>
          <tbody>{t_rows}</tbody>
        </table>"""
    else:
        trace_html = f"<h2>总线活动分析</h2><p class='warn'>Trace 不可用：{_esc(trace['reason'])}</p>"

    # ---- 产物清单 ----
    artifacts = []
    for name, p in [
        ("Robot 报告 (report.html)", ROOT / "results" / "report.html"),
        ("Robot 日志 (log.html)", ROOT / "results" / "log.html"),
        ("原始结果 (output.xml)", ROOT / "results" / "output.xml"),
        ("总线 Trace (ASC, CANoe 可打开)", ROOT / "results" / "bus_trace.asc"),
        ("总线 Trace (BLF)", ROOT / "results" / "bus_trace.blf"),
        ("仪表显示快照 (JSON)", ROOT / "simulators" / "dashboard_state.json"),
    ]:
        exists = p.exists()
        mark = "✅" if exists else "—"
        size = f"{p.stat().st_size / 1024:.1f} KB" if exists else ""
        artifacts.append(
            f"<li>{mark} {_esc(name)} <span class='dim mono'>{_esc(p.name)} {size}</span></li>")
    artifacts_html = f"<h2>产物清单</h2><ul>{''.join(artifacts)}</ul>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>车载自动化测试报告 · {now}</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --border:#2a3140; --text:#e6edf3; --dim:#8b949e;
           --ok:#3fb950; --bad:#f85149; --accent:#58a6ff; }}
  * {{ box-sizing: border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",sans-serif;
         margin:0; padding:32px; line-height:1.6; }}
  h1 {{ font-size:24px; margin:0 0 4px; }}
  h2 {{ font-size:17px; margin:28px 0 10px; color:var(--accent); }}
  .sub {{ color:var(--dim); font-size:13px; margin-bottom:22px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }}
  .card .k {{ color:var(--dim); font-size:12px; }}
  .card .v {{ font-size:24px; font-weight:700; margin-top:2px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border-radius:10px; overflow:hidden; }}
  th, td {{ padding:9px 12px; border-bottom:1px solid var(--border); font-size:13px; text-align:left; }}
  th {{ color:var(--dim); font-weight:600; background:#11161d; }}
  tr:last-child td {{ border-bottom:none; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .mono {{ font-family:Consolas,monospace; font-size:12px; }}
  .dim {{ color:var(--dim); }}
  .ok {{ color:var(--ok); }}
  .bad {{ color:var(--bad); }}
  .warn {{ color:#d29922; }}
  ul {{ padding-left:20px; }}
  .fails li {{ margin-bottom:8px; }}
  footer {{ margin-top:32px; color:var(--dim); font-size:12px; border-top:1px solid var(--border); padding-top:12px; }}
</style>
</head>
<body>
  <h1>车载自动化测试报告</h1>
  <div class="sub">生成时间 {now} · 项目 robot_framework（动力域 ECU + 座舱仪表 + 安卓车机 仿真座舱）</div>
  {cards}
  <h2>套件明细</h2>
  <table>
    <thead><tr><th>套件</th><th>说明</th><th class="num">用例</th><th class="num">通过</th>
    <th class="num">失败</th><th class="num">耗时</th><th>结果</th></tr></thead>
    <tbody>{suite_rows}</tbody>
  </table>
  {fail_html}
  {req_section}
  {trace_html}
  {artifacts_html}
  <footer>由 generate_report.py 生成 —— 对应 Tier1 测试交付物形态：结论给人看，Trace 给开发用。</footer>
</body>
</html>"""


# ===========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="生成测试交付报告")
    parser.add_argument("--output", default=str(ROOT / "results" / "test_summary.html"),
                        help="报告输出路径")
    parser.add_argument("--robot-xml", default=str(ROOT / "results" / "output.xml"))
    parser.add_argument("--trace", default=str(ROOT / "results" / "bus_trace.asc"))
    args = parser.parse_args()

    robot = collect_robot_stats(Path(args.robot_xml))
    trace = analyze_trace(Path(args.trace))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(robot, trace), encoding="utf-8")

    if robot["available"]:
        print(f"报告已生成: {out}")
        print(f"  用例 {robot['passed']}/{robot['total']} 通过"
              f"（{robot['pass_rate']}%），耗时 {robot['elapsed_s']}s")
    else:
        print(f"报告已生成（Robot 结果缺失）: {out}")
    if trace["available"]:
        print(f"  总线分析：{trace['frame_total']} 帧 / {trace['id_count']} 个 ID")


if __name__ == "__main__":
    main()
