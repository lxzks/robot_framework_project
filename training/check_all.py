"""
check_all.py — 实训自检器：验证 12 个练习的参考答案 + 提示你练习的 TODO 进度
==========================================================================

作用：跑一遍全部 12 个练习的 solution.robot，确认参考答案都能通过。
用途：
    1. 环境自检：跑不通说明环境有问题（而不是你的脚本有问题）
    2. 对照：写完自己的答案后，用它确认"参考答案确实是对的"
    3. 维护：改了仿真环境后，回归验证练习没被改坏

**注意**：它验证的是参考答案（solution.robot），不是你的答案；你的练习要跑
exercise.robot 看 PASS/FAIL（脚本末尾会提示每个练习还剩多少 TODO）。

用法：
    python training/check_all.py               # 跑全部 12 个
    python training/check_all.py --level L6    # 只跑某个练习
"""

from __future__ import annotations

import argparse
import os
import re
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
    ("L7_lin_signals", "L7 LIN 车身信号"),
    ("L8_doip_diag", "L8 DoIP 以太网诊断"),
    ("L9_vision_assert", "L9 HMI 视觉断言"),
    ("L10_security_fuzz", "L10 信息安全"),
    ("L11_ota_flash", "L11 OTA 刷写"),
    ("L12_appium_ui", "L12 Appium UI"),
]


def count_todos(level: str) -> int:
    """统计 exercise.robot 里还剩多少个 TODO 标记（用于提示你的练习进度）"""
    exercise = TRAINING / level / "exercise.robot"
    if not exercise.exists():
        return 0
    text = exercise.read_text(encoding="utf-8")
    return len(re.findall(r"TODO", text))


def run_one(level: str, name: str) -> tuple[bool, str]:
    """跑一个练习的 solution，返回 (是否通过, 摘要)"""
    target = TRAINING / level / "solution.robot"
    if not target.exists():
        return False, "参考答案不存在"
    out_dir = ROOT / "results" / "training" / level
    # 统一子进程编码：robot 的 stdout 在 Windows 下默认是本地代码页（GBK），
    # 而父进程可能被 `python -X utf8` / UTF-8 终端改成 UTF-8 —— 不统一就会
    # 在读取子进程输出时 UnicodeDecodeError（真实踩过）。这里两边都钉死 UTF-8。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-m", "robot", "--outputdir", str(out_dir),
         "--report", "NONE", "--log", "NONE", str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, cwd=str(ROOT), env=env,
    )
    # 从 robot 输出里抓最后一行统计
    summary = ""
    for line in reversed((proc.stdout or "").splitlines()):
        if "tests," in line and ("passed" in line or "failed" in line):
            summary = line.strip()
            break
    ok = proc.returncode == 0
    return ok, summary or "（未解析到统计）"


def _safe_console() -> None:
    """避免 Windows GBK 控制台下非 GBK 字符（如 ✅）导致 UnicodeEncodeError。

    只把 errors 改成 replace（不改 encoding，防止中文在 cmd 里乱码），
    真正打印时也不使用 emoji —— 保证 `python training/check_all.py` 在任何终端都能跑完。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001  某些重定向流不支持 reconfigure
            pass


def main() -> int:
    _safe_console()
    parser = argparse.ArgumentParser(
        description="实训自检器（验证参考答案；不是给你的练习判分）")
    parser.add_argument("--level", default=None, help="只跑某个练习（如 L6）")
    args = parser.parse_args()

    targets = [lv for lv in LEVELS
               if args.level is None or lv[0].startswith(args.level)]
    if not targets:
        print(f"没有匹配 '{args.level}' 的练习")
        return 1

    print("=" * 66)
    print(" 车载自动化脚本实训 · 参考答案验证（12 个练习）")
    print("=" * 66)

    results = []
    for level, label in targets:
        print(f"\n[{label}] ...")
        ok, summary = run_one(level, label)
        mark = "PASS" if ok else "FAIL"
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

    print("\n 你的练习（exercise.robot）进度（本工具不判你的答案）：")
    for level, label in targets:
        todos = count_todos(level)
        if todos:
            print(f"   · {label}: 还剩 {todos} 个 TODO")
        else:
            print(f"   · {label}: TODO 已补完，去跑自己的练习看 PASS/FAIL")
        print(f"     python -m robot --outputdir results/training "
              f"training/{level}/exercise.robot")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
