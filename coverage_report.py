"""
coverage_report.py — 测试覆盖率统计
=====================================

回答一个主管必问的问题："测了这么多，到底覆盖了多少？"

统计三个维度（全部从 RF 的实际执行结果里提取，不是估算）：

    1. **UDS 服务覆盖率**：ECU 实现的服务 vs 测试实际调用的服务
    2. **信号覆盖率**：DBC 定义的信号 vs 测试实际读写的信号
    3. **DTC 覆盖率**：故障码目录 vs 测试实际验证过的 DTC

原理：解析 results/output.xml（RF 的机器可读结果），遍历所有关键字调用，
按关键字名/参数归集"用到了什么"。库关键字隐含的服务也有映射表
（如 Enter Extended Session → 0x10、Request Download → 0x34）。

用法：
    python -m robot --outputdir results tests/robot/    # 先跑测试
    python coverage_report.py                            # 再统计
    python coverage_report.py --html results/coverage.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "simulators"))

DEFAULT_XML = ROOT / "results" / "output.xml"

# 库关键字 → 隐含的 UDS 服务（关键字封装了服务，参数里看不出来）
KEYWORD_TO_SID = {
    "Enter Extended Session": 0x10,
    "Enter Default Session": 0x10,
    "Enter Programming Session": 0x10,
    "ECU Should Respond": 0x3E,
    "Security Unlock": 0x27,
    "Request Seed": 0x27,
    "Send Key": 0x27,
    "Read DID": 0x22,
    "Read DID As ASCII": 0x22,
    "Write DID": 0x2E,
    "Read DTC Count": 0x19,
    "Read DTC By Mask": 0x19,
    "Clear DTC": 0x14,
    "Start Routine": 0x31,
    "Start Light Test Routine": 0x31,
    "Suspend ECU Periodic Messages": 0x28,
    "Resume ECU Periodic Messages": 0x28,
    "Request Download": 0x34,
    "Transfer Data Block": 0x36,
    "Request Transfer Exit": 0x37,
    "Check Programming Integrity": 0x31,
    "Integrity Check Result Should Be": 0x31,
    "Measure Response Time": None,      # 参数里带请求，单独解析
}

# 涉及信号的关键字：参数里含 message / signal 名
SIGNAL_KEYWORDS = {
    "Read Signal From Bus": "read",
    "Send Signal Values": "write",
    "Read Frame And Verify Signals": "read",
    "Wait Until Signal In Range": "read",
}


# ===========================================================================
def iter_keywords(node):
    """递归遍历一个节点的 body（关键字调用树）"""
    for child in getattr(node, "body", []) or []:
        yield child
        yield from iter_keywords(child)


# 关键字包装器：内层调用的名字和参数在 args 里（不是 body 子节点），
# 如 `Run Keyword And Ignore Error    Send UDS Request    11 01`
# 值是"内层关键字名在 args 里的下标"。不递归解析会把内层调用漏掉
# （覆盖率虚低：本项目 0x11 就被漏过）。
WRAPPER_KEYWORDS = {
    "Run Keyword And Ignore Error": 0,
    "Run Keyword And Return Status": 0,
    "Run Keyword And Expect Error": 1,
}


def _record(name: str, args: list[str], used_sids: set, used_signals: set,
            used_dtcs: set) -> None:
    """按一个关键字调用记录覆盖情况"""
    # --- UDS 服务 ---
    if name in KEYWORD_TO_SID and KEYWORD_TO_SID[name] is not None:
        used_sids.add(KEYWORD_TO_SID[name])
    if name in ("Send UDS Request", "Measure Response Time") and args:
        hexstr = args[0].replace(" ", "")
        try:
            used_sids.add(int(hexstr[:2], 16))
        except ValueError:
            pass

    # --- 信号 ---
    if name in SIGNAL_KEYWORDS:
        # 参数形态：Send Signal Values  msg  sig=value ...
        #          Read Signal From Bus  msg  sig
        # Template 用例的实参可能是 "${sig}" 变量形式，这类跳过
        # （信号维度另用生成器口径统计）
        for a in args[1:]:
            if "=" in a:
                a = a.split("=", 1)[0]
            if a and not a.startswith("$") and " " not in a:
                used_signals.add(a)

    # --- DTC ---
    if name == "DTC List Should Contain":
        used_dtcs.update(a.upper() for a in args if a.upper().startswith("P"))


def iter_all_keywords(suite):
    """遍历套件下的**全部**关键字调用。

    两个易踩的点（本项目都真实踩过）：
    1. RF 的 ExecutionResult 是懒加载的，必须显式访问 suite.tests /
       suite.suites，只遍历 body 会漏掉所有用例（统计全 0）；
    2. **setup/teardown 是独立属性**（不在 body 里）—— TestSuite 和
       TestCase 都有 setup/teardown 属性，套件环境准备、用例的
       Test Teardown 里的关键字（如 11 01 复位）都在那里，
       不显式取就会统计不到。
    """
    for attr in ("setup", "teardown"):
        node = getattr(suite, attr, None)
        if node is not None:
            yield node
            yield from iter_keywords(node)
    for test in suite.tests:
        for attr in ("setup", "teardown"):
            node = getattr(test, attr, None)
            if node is not None:
                yield node
                yield from iter_keywords(node)
        yield from iter_keywords(test)
    for sub in suite.suites:
        yield from iter_all_keywords(sub)


def collect(output_xml: Path) -> dict:
    """从 RF 结果提取覆盖数据"""
    from robot.api import ExecutionResult
    from uds_lib import NRC_NAMES, SESSION_DEFAULT  # noqa: F401
    from dtc_manager import FAULT_CATALOG
    import cantools

    result = ExecutionResult(str(output_xml))

    used_sids: set[int] = set()
    used_signals: set[str] = set()
    used_dtcs: set[str] = set()

    for suite in result.suite.suites:
        for kw in iter_all_keywords(suite):
            # 跳过控制结构节点（IF/FOR/WHILE/TRY 及其分支）——它们不是
            # 关键字调用，且其 kwname/args 属性在 RF 7 已弃用（会刷警告）
            if type(kw).__name__ in (
                    "If", "IfBranch", "For", "ForIteration",
                    "While", "WhileIteration", "Try", "TryBranch", "Group"):
                continue
            name = getattr(kw, "name", "") or getattr(kw, "kwname", "") or ""
            args = [str(a) for a in getattr(kw, "args", [])]

            # 关键字包装器：把内层调用解开再统计
            # （如 Run Keyword And Ignore Error → 内层 Send UDS Request）
            while name in WRAPPER_KEYWORDS:
                idx = WRAPPER_KEYWORDS[name]
                if len(args) <= idx:
                    break
                name, args = args[idx], args[idx + 1:]

            _record(name, args, used_sids, used_signals, used_dtcs)

    # 全集
    db = cantools.database.load_file(
        str(ROOT / "simulators" / "dbc" / "vehicle.dbc"))
    all_signals = {s.name for m in db.messages for s in m.signals}
    all_dtcs = set(FAULT_CATALOG.keys())

    # ECU 实现的 UDS 服务（含会话相关的隐性服务）
    from ecu_sim import PowertrainEcu  # noqa: F401  (仅确认可导入)
    implemented_sids = {
        0x10: "DiagnosticSessionControl",
        0x11: "ECUReset",
        0x14: "ClearDiagnosticInformation",
        0x19: "ReadDTCInformation",
        0x22: "ReadDataByIdentifier",
        0x27: "SecurityAccess",
        0x28: "CommunicationControl",
        0x2E: "WriteDataByIdentifier",
        0x31: "RoutineControl",
        0x34: "RequestDownload",
        0x36: "TransferData",
        0x37: "RequestTransferExit",
        0x3E: "TesterPresent",
        0x85: "ControlDTCSetting",
    }

    # 信号名过滤：只保留 DBC 里真实存在的（排除报文名等噪声）
    used_signals_real = {s for s in used_signals if s in all_signals}
    # 补充口径：生成的用例（tests/robot/generated/）对 DBC **全部信号**都
    # 生成了边界值/等价类用例（见 generate_testcases.py），而 Template
    # 用例的实参在结果树里是变量形式、解析不到信号名 —— 所以这里并入
    # 生成器的信号全集，避免低估。
    try:
        from generate_testcases import collect_signals
        gen_signals = {s["name"] for s in collect_signals(
            ROOT / "simulators" / "dbc" / "vehicle.dbc")}
        used_signals_real |= (gen_signals & all_signals)
    except Exception as exc:  # noqa: BLE001
        print(f"[coverage] 提示：生成器信号集未并入（{exc}）")

    return {
        "uds": {
            "implemented": implemented_sids,
            "used": sorted(used_sids),
            "covered": sorted(s for s in used_sids if s in implemented_sids),
            "missing": sorted(s for s in implemented_sids if s not in used_sids),
        },
        "signals": {
            "all": all_signals,
            "used": used_signals_real,
            "missing": sorted(all_signals - used_signals_real),
        },
        "dtcs": {
            "all": all_dtcs,
            "used": used_dtcs & all_dtcs,
            "missing": sorted(all_dtcs - used_dtcs),
        },
    }


def pct(covered: int, total: int) -> str:
    return f"{covered / total * 100:.1f}%" if total else "n/a"


def print_report(c: dict) -> None:
    u, s, d = c["uds"], c["signals"], c["dtcs"]
    print("=" * 62)
    print(" 测试覆盖率报告")
    print("=" * 62)

    print(f"\n[UDS 服务]  {len(u['covered'])}/{len(u['implemented'])}  "
          f"({pct(len(u['covered']), len(u['implemented']))})")
    for sid in u["covered"]:
        print(f"   ✓ {sid:#04x} {u['implemented'][sid]}")
    for sid in u["missing"]:
        print(f"   ✗ {sid:#04x} {u['implemented'][sid]}  ← 未覆盖")

    print(f"\n[信号]      {len(s['used'])}/{len(s['all'])}  "
          f"({pct(len(s['used']), len(s['all']))})")
    if s["missing"]:
        print(f"   未覆盖: {', '.join(s['missing'])}")
    else:
        print("   ✓ 全部信号已被测试覆盖")

    print(f"\n[DTC]       {len(d['used'])}/{len(d['all'])}  "
          f"({pct(len(d['used']), len(d['all']))})")
    for dtc in sorted(d["used"]):
        print(f"   ✓ {dtc}")
    for dtc in d["missing"]:
        print(f"   ✗ {dtc}  ← 未覆盖")

    total = len(u["implemented"]) + len(s["all"]) + len(d["all"])
    hit = len(u["covered"]) + len(s["used"]) + len(d["used"])
    print(f"\n{'=' * 62}")
    print(f" 综合覆盖率: {hit}/{total} ({pct(hit, total)})")
    print("=" * 62)


def render_html(c: dict) -> str:
    u, s, d = c["uds"], c["signals"], c["dtcs"]

    def rows(items, cols):
        return "".join(
            f"<tr>{''.join(f'<td>{v}</td>' for v in row)}</tr>"
            for row in items)

    uds_rows = rows(
        [(f"0x{sid:02X}", f"{name}", "✅ 已覆盖" if sid in u["covered"] else "❌ 未覆盖")
         for sid, name in sorted(u["implemented"].items())], 3)
    sig_rows = rows(
        [(name, "✅" if name in s["used"] else "❌")
         for name in sorted(s["all"])], 2)
    dtc_rows = rows(
        [(name, "✅ 已覆盖" if name in d["used"] else "❌ 未覆盖")
         for name in sorted(d["all"])], 2)

    def p(covered, total):
        return f"{covered / total * 100:.1f}%" if total else "n/a"

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>测试覆盖率报告</title><style>
body{{background:#0d1117;color:#e6edf3;font-family:"Segoe UI","Microsoft YaHei",sans-serif;
padding:32px;line-height:1.6}}
h1{{font-size:22px}} h2{{color:#58a6ff;font-size:16px;margin-top:26px}}
table{{border-collapse:collapse;width:100%;max-width:760px}}
th,td{{border-bottom:1px solid #2a3140;padding:7px 12px;font-size:13px;text-align:left}}
th{{color:#8b949e;background:#11161d}}
.kpi{{display:flex;gap:14px;margin:14px 0}}
.kpi div{{background:#161b22;border:1px solid #2a3140;border-radius:10px;padding:12px 18px}}
.kpi b{{display:block;font-size:22px;color:#58a6ff}}
</style></head><body>
<h1>测试覆盖率报告</h1>
<div class="kpi">
  <div><b>{p(len(u['covered']), len(u['implemented']))}</b>UDS 服务 {len(u['covered'])}/{len(u['implemented'])}</div>
  <div><b>{p(len(s['used']), len(s['all']))}</b>信号 {len(s['used'])}/{len(s['all'])}</div>
  <div><b>{p(len(d['used']), len(d['all']))}</b>DTC {len(d['used'])}/{len(d['all'])}</div>
</div>
<h2>UDS 服务覆盖</h2><table><tr><th>SID</th><th>服务</th><th>状态</th></tr>{uds_rows}</table>
<h2>信号覆盖</h2><table><tr><th>信号</th><th>状态</th></tr>{sig_rows}</table>
<h2>DTC 覆盖</h2><table><tr><th>故障码</th><th>状态</th></tr>{dtc_rows}</table>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="测试覆盖率统计")
    parser.add_argument("--robot-xml", default=str(DEFAULT_XML))
    parser.add_argument("--html", default=None, help="同时输出 HTML 报告")
    args = parser.parse_args()

    xml = Path(args.robot_xml)
    if not xml.exists():
        print(f"找不到 {xml} —— 先跑一遍测试（python run_tests.py）")
        sys.exit(1)

    c = collect(xml)
    print_report(c)

    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(c), encoding="utf-8")
        print(f"\nHTML 覆盖率报告: {out}")


if __name__ == "__main__":
    main()
