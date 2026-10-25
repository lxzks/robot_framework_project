"""
trace_tools.py — 总线 Trace 导出（CANoe / CANalyzer 兼容格式）
================================================================

测试失败时最需要什么？**总线 Trace**——看"当时总线上到底发生了什么"。

本模块把录制的报文导出成工业界通用格式：
    - **ASC**（Vector ASCII Logging）：纯文本，CANoe/CANalyzer 可直接打开，
      也能用文本工具 diff/搜索，适合归档和 code review；
    - **BLF**（Binary Logging Format）：Vector 二进制格式，文件小、
      支持大数据量，是实车路试的标准格式。

面试价值：能说"我的测试工具产出的 Trace 可以直接在 CANoe 里回放分析"，
比只说"我录了报文"有说服力得多 —— 因为格式兼容意味着工具链能进真实流程。

ASC 格式（节选）：
    date Mon Oct 13 04:00:00 2026
    base hex  timestamps absolute
    Begin Triggerblock Mon Oct 13 04:00:00 2026
       0.000000 1  100             Rx   d 8 C4 09 80 0C 82 33 00 00
       0.100000 1  200             Rx   d 8 03 3C 78 0D 80 0E 00 00
    End TriggerBlock
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

# 录制帧的统一格式：(timestamp, arbitration_id, data_bytes, is_extended)
FrameTuple = tuple[float, int, bytes, bool]


def _normalize(frames) -> list[FrameTuple]:
    """兼容三种输入：三元组/四元组/(ts, id_hex, data_hex)（RF 库的格式）"""
    out: list[FrameTuple] = []
    for f in frames:
        if len(f) >= 4:
            ts, aid, data, ext = f[0], f[1], f[2], f[3]
        else:
            ts, aid, data = f[0], f[1], f[2]
            ext = False
        if isinstance(aid, str):
            aid = int(aid, 0)
        if isinstance(data, str):
            data = bytes.fromhex(data.replace(" ", ""))
        out.append((float(ts), int(aid), bytes(data), bool(ext)))
    return out


def export_asc(frames, path, channel: int = 1) -> int:
    """导出 ASC（Vector ASCII 格式，CANoe 可直接打开）。返回帧数。

    frames 支持：(ts, id, data, is_extended) 或 (ts, id, data) 或
    (ts, id_hex, data_hex)（Robot Framework 库的录制格式）。
    """
    recs = _normalize(frames)
    if not recs:
        raise ValueError("没有可导出的帧（录制器是空的？）")

    # 时间戳相对化：ASC 通常以测量开始为 0 点
    t0 = recs[0][0]
    now_str = datetime.fromtimestamp(time.time()).strftime(
        "%a %b %d %I:%M:%S %Y")

    lines = [
        f"date {now_str}",
        "base hex  timestamps absolute",
        "internal events logged",
        "// version 1.0.0 (robot_framework trace_tools)",
        f"Begin Triggerblock {now_str}",
        "   0.000000 Start of measurement",
    ]
    for ts, aid, data, ext in recs:
        rel = ts - t0
        id_str = f"{aid:X}" + ("x" if ext else "")
        hex_bytes = " ".join(f"{b:02X}" for b in data)
        # 列对齐（CANoe 风格）：ID 左对齐占 15 字符
        lines.append(
            f"   {rel:10.6f} {channel}  {id_str:<15} Rx   d {len(data)} "
            f"{hex_bytes}")
    lines.append("End TriggerBlock")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(recs)


def export_blf(frames, path) -> int:
    """导出 BLF（Vector 二进制格式）。返回帧数。

    用 python-can 的 BLFWriter 实现 —— 这保证了格式正确性
    （BLF 是二进制格式，手写字节必错，必须用成熟库）。
    """
    import can

    recs = _normalize(frames)
    if not recs:
        raise ValueError("没有可导出的帧（录制器是空的？）")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with can.BLFWriter(str(out), channel=1) as writer:
        for ts, aid, data, ext in recs:
            writer.on_message_received(can.Message(
                timestamp=ts,
                arbitration_id=aid,
                data=data,
                is_extended_id=ext,
            ))
    return len(recs)


def export_frames(frames, path) -> int:
    """按扩展名自动选择格式：.asc → ASC，.blf → BLF"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".asc":
        return export_asc(frames, p)
    if ext == ".blf":
        return export_blf(frames, p)
    raise ValueError(f"不支持的 Trace 格式 {ext}（用 .asc 或 .blf）")


# ===========================================================================
# 回读验证（"测试的测试"）：导出器写错了在真实工作里很致命
# （Trace 打不开 / 内容缺失 → 现场问题无法回溯）
# ===========================================================================
def verify_asc(path) -> list[tuple[float, int, int, bytes]]:
    """解析 ASC 文件，返回 [(rel_ts, id, dlc, data)]。

    只解析数据帧行（形如 `   0.000000 1  100   Rx   d 8 C4 09 ...`），
    忽略头部/事件行。列序：ts, channel, id, Rx/Tx, d, dlc, data...
    解析失败抛出 ValueError。
    """
    out: list[tuple[float, int, int, bytes]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith(("date ", "base ", "internal ", "//",
                                  "Begin ", "End ")):
            continue
        parts = s.split()
        # 数据帧行：ts channel id Rx d dlc data...(>=7 段)
        # 事件行（如 "0.000000 Start of measurement"）只有 3 段
        if len(parts) < 7 or parts[3] not in ("Rx", "Tx"):
            continue
        try:
            ts = float(parts[0])
            arb = parts[2]
            ext = arb.endswith(("x", "X"))
            aid = int(arb.rstrip("xX"), 16)
            dlc = int(parts[5])
            data = bytes(int(b, 16) for b in parts[6:6 + dlc])
        except (ValueError, IndexError) as exc:
            raise ValueError(f"ASC 行解析失败: {line!r} ({exc})") from exc
        out.append((ts, aid, dlc, data))
    return out


def verify_blf(path) -> int:
    """用 python-can 的 BLFReader 回读，返回帧数（能读通即格式正确）。

    用工业库回读是 BLF 正确性的硬证据 —— 这正是 CANoe 会做的事。
    """
    import can
    count = 0
    with can.BLFReader(str(path)) as reader:
        for _msg in reader:
            count += 1
    return count
