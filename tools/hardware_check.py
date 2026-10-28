"""tools/hardware_check.py — 仿真 ⇄ 真实硬件 配置检查 / 连通性探针
=================================================================

用途：确认"当前命令会连仿真还是真实硬件"，以及真实硬件配置是否可用。

    python tools/hardware_check.py              # 检查 CAN + ADB 配置（默认）
    python tools/hardware_check.py --can        # 只检查 CAN
    python tools/hardware_check.py --adb        # 只检查 ADB
    python tools/hardware_check.py --json       # 机器可读输出（CI/脚本用）

判定逻辑：
- **没有设置环境变量** → 使用项目内置仿真（UDP 组播虚拟 CAN / adb_sim），
  打印"仿真模式"，退出码 0（正常学习状态）。
- **设置了真实硬件环境变量**（CAN_INTERFACE / CAN_CHANNEL / CAN_BITRATE
  或 ADB_BIN）→ 真正尝试打开接口/执行 adb version：
  成功退出码 0；失败退出码 2，并打印排查清单。

环境变量（与 simulators/bus.py、libraries/AdbLibrary.py 保持一致）：
    CAN_INTERFACE   udp_multicast（默认）| slcan | socketcan | pcan | vector | ...
    CAN_CHANNEL     仿真=组播地址；真实硬件=COM4 / can0 / PCAN_USBBUS1 ...
    CAN_BITRATE     仲裁段波特率，默认 500000
    ADB_BIN         真实 adb 命令（默认用 simulators/adb_sim.py），可带 -s 序列号
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "simulators"))
sys.path.insert(0, str(ROOT / "libraries"))

from bus import build_can_kwargs, resolve_bus_config  # noqa: E402
from AdbLibrary import resolve_adb_prefix  # noqa: E402


def collect_config(env: dict | None = None) -> dict:
    """收集当前生效的总线/ADB 配置（纯函数，便于单元测试）。"""
    can_cfg = resolve_bus_config()
    adb_prefix = resolve_adb_prefix(env=env)
    is_sim_adb = (len(adb_prefix) >= 2
                  and adb_prefix[0].endswith("python.exe")
                  and "adb_sim.py" in adb_prefix[1])
    return {
        "can": can_cfg,
        "adb": {
            "prefix": adb_prefix,
            "is_simulated": is_sim_adb,
        },
    }


def probe_can(cfg: dict, timeout_s: float = 3.0) -> dict:
    """尝试打开一次 CAN 总线并立即关闭；返回 {ok, mode, detail}。"""
    if cfg["is_simulated"]:
        return {"ok": True, "mode": "simulated",
                "detail": "CAN_INTERFACE 未设置 → 使用 UDP 组播虚拟总线（仿真）"}
    try:
        import can  # 延迟导入：仿真环境下也不强依赖
        bus = can.Bus(**build_can_kwargs(cfg))
        bus.shutdown()
        return {"ok": True, "mode": "real",
                "detail": f"已打开真实 CAN: interface={cfg['interface']} "
                          f"channel={cfg['channel']} bitrate={cfg['bitrate']}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": "real",
                "detail": f"打开真实 CAN 失败: {type(exc).__name__}: {exc}",
                "hint": "检查：①接线/驱动 ②CAN_INTERFACE 名称是否 python-can 支持 "
                        "③CAN_CHANNEL（COM4/can0/PCAN_USBBUS1…）④CAN_BITRATE 与整车一致"
                        "（本项目默认 500k）"}


def probe_adb(cfg: dict, timeout_s: float = 10.0) -> dict:
    """执行 adb version（或 adb_sim 自检）；返回 {ok, mode, detail}。"""
    prefix = cfg["prefix"]
    if cfg["is_simulated"]:
        return {"ok": True, "mode": "simulated",
                "detail": "ADB_BIN 未设置 → 使用项目内置 adb_sim.py（仿真车机）"}
    try:
        proc = subprocess.run(prefix + ["version"], capture_output=True,
                              text=True, timeout=timeout_s,
                              stdin=subprocess.DEVNULL)
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        first = out[0] if out else ""
        if proc.returncode == 0:
            return {"ok": True, "mode": "real",
                    "detail": f"adb 可用: {first}"}
        return {"ok": False, "mode": "real",
                "detail": f"adb version 退出码 {proc.returncode}: {first}",
                "hint": "检查 ADB_BIN 路径、设备是否插好、adb 是否被安全软件拦截"}
    except FileNotFoundError:
        return {"ok": False, "mode": "real",
                "detail": f"找不到 adb 命令: {prefix}",
                "hint": "把 Android platform-tools 加入 PATH，或 ADB_BIN 写全路径"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "mode": "real",
                "detail": f"adb version 超时（>{timeout_s}s）",
                "hint": "常见原因：adb server 卡死 → 先运行 `adb kill-server` 再重试"}


def main() -> int:
    parser = argparse.ArgumentParser(description="仿真 ⇄ 真实硬件 配置检查/探针")
    parser.add_argument("--can", action="store_true", help="只检查 CAN")
    parser.add_argument("--adb", action="store_true", help="只检查 ADB")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    check_can = args.can or not args.adb
    check_adb = args.adb or not args.can

    cfg = collect_config()
    results = {}
    if check_can:
        results["can"] = {"config": cfg["can"], **probe_can(cfg["can"])}
    if check_adb:
        results["adb"] = {"config": cfg["adb"], **probe_adb(cfg["adb"])}

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=" * 62)
        print(" 仿真 / 真实硬件 配置检查")
        print("=" * 62)
        for name, label in (("can", "CAN 总线"), ("adb", "ADB 设备")):
            if name not in results:
                continue
            r = results[name]
            mode = "仿真" if r["mode"] == "simulated" else "真实硬件"
            print(f"\n[{label}]  模式={mode}  {'OK' if r['ok'] else 'FAIL'}")
            print(f"  {r['detail']}")
            if r.get("hint"):
                print(f"  ↳ {r['hint']}")

    all_ok = all(r["ok"] for r in results.values())
    real_used = any(r["mode"] == "real" for r in results.values())
    print()
    if not all_ok:
        print("结果：真实硬件配置不可用（见上方提示）。修复前请先用仿真模式。")
        return 2
    if real_used:
        print("结果：真实硬件连通性检查通过 ✅")
    else:
        print("结果：当前为纯仿真模式（不需要真实硬件，属正常学习状态）✅")
        print("切换真实硬件见 docs/33 章「真实硬件接入」或 README 对应小节。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
