"""
asc_analyzer.py — Trace 分析器（ASC / BLF 解析 + 总线负载 + 周期抖动 + 异常检测）
================================================================================

为什么需要它
------------
测试跑完只看到"PASS/FAIL"是不够的：**失败现场的证据在 Trace 里**。
真实工作里测试工程师大量时间在做同一件事：

    拿到 Trace（ASC/BLF）→ 算总线负载 → 看周期/抖动 → 找丢帧/异常 ID → 写进缺陷单

`simulators/trace_tools.py` 负责"录制→导出"（CANoe/CANalyzer 兼容格式），
本模块负责另一半"**导入→分析**"——就像 CANoe 的 Trace 窗口 + 统计功能：

    Trace 文件 ──parse_asc()/parse_blf()──► Frame 列表
        ├── compute_bus_load()   总线负载率（%）
        ├── id_stats()           每个 ID 的帧数/速率/周期/抖动
        ├── period_stats()       单个 ID 的周期统计（均值/标准差/极值/抖动）
        ├── detect_anomalies()   丢帧 / 间隔异常 / 未知 ID
        └── export_csv()/export_html()   教学版分析报告 + CLI

支持的格式
----------
* **ASC**（Vector ASCII Logging，纯文本）：CANoe / CANalyzer 可直接打开，
  也能用 grep/diff。本项目 `trace_tools.export_asc()` 导出的就是这种格式。
* **BLF**（Vector Binary Logging Format）：二进制，文件小、实车路测标准格式。
  用 python-can 的 `can.BLFReader` 读（不手写二进制解析）。

> ⚠️ 已知格式细节（如实说明，不改 trace_tools.py）：项目导出的 ASC 头写的是
> `base hex  timestamps absolute`，但 `export_asc()` 实际写的是**相对测量起点的
> 时间**（第一帧 = 0.000000），不是 Unix 绝对时间。两种都兼容——因为周期/抖动
> 分析只用**相邻帧时间差**，与时间起点无关；分析报告会把时间轴归一化到 0 起点。
> 同理 BLF 里的时间戳是 Unix 绝对时间（~1.7e9），`normalize_time()` 也会归一化。

总线负载怎么算（和任务 12 的非功能模块同一口径）
------------------------------------------------
一个标准 CAN 数据帧的位数 ≈ **47 + 8 × DLC**：
    SOF 1 + 仲裁场 11 + RTR 1 + IDE 1 + r0 1 + DLC 4
    + 数据 8×DLC + CRC 15 + CRC 分隔 1 + ACK 1 + ACK 分隔 1 + EOF 7 + IFS 3 = 47 位
扩展帧（29 位 ID）≈ **67 + 8 × DLC**。
再乘 **20% 位填充余量**（STAFF：最坏情况下填充位可让帧长增加约 20%）：

    总线负载(%) = Σ(每帧位数 × 1.2) / (比特率 × 记录时长) × 100

这只是工程近似（真实 CANoe 按实测位时间统计），但用于"负载趋势/门禁"足够。

异常检测思路（教学版规则）
--------------------------
1. **丢帧 missing_frame**：相邻两帧间隔 > 期望周期 × (1 + 容差)
   → 估算丢帧数 = round(间隔 / 周期) - 1
2. **间隔异常 interval_too_short**：间隔 < 期望周期 × (1 - 容差)
   → 重复帧 / 突发发送 / 时钟抖动过大
3. **未知 ID unknown_id**：出现不在"期望周期清单"里的 ID
   → 新节点上线 / 报文配错 / 诊断/网络管理报文混入（真实项目先看是不是预期内）

CLI
---
    .\\.venv\\Scripts\\python.exe simulators/asc_analyzer.py \
        --input results/dev_trace/bus_recording.asc \
        --out results/dev_trace/report.html --bitrate 500000
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 默认参数（集中常量，库/CLI/文档都引用这里）
# ---------------------------------------------------------------------------
#: 本项目虚拟总线的比特率（经典 CAN 500 kbps）
DEFAULT_BITRATE = 500_000
#: 三条应用报文的期望周期（秒）：车速 100ms / 仪表 200ms / 警告灯 500ms
EXPECTED_PERIODS: dict[int, float] = {0x100: 0.1, 0x200: 0.2, 0x300: 0.5}
#: 位填充余量（最坏情况约 20%）
STUFFING_OVERHEAD = 1.2
#: 标准帧固定位数（不含数据场）与扩展帧固定位数
BASE_FRAME_BITS = 47
EXTENDED_FRAME_BITS = 67
#: 异常检测默认容差（间隔偏离期望周期 ±50% 触发）
DEFAULT_ANOMALY_TOLERANCE = 0.5
#: BLF 文件魔数（Vector "LOGG"）
BLF_MAGIC = b"LOGG"


# ===========================================================================
# 数据结构
# ===========================================================================
@dataclass(frozen=True)
class Frame:
    """一帧 CAN 报文（ASC/BLF 解析后的统一表示）。

    字段:
        timestamp      时间戳（秒，保留文件里的原始值）
        arbitration_id 仲裁 ID（整数，如 0x100）
        data           数据场（bytes）
        is_extended    是否扩展帧（29 位 ID）
        channel        通道号（ASC 第 2 列）
        is_rx          接收（True）/ 发送（False）方向
    """

    timestamp: float
    arbitration_id: int
    data: bytes = b""
    is_extended: bool = False
    channel: int = 1
    is_rx: bool = True

    @property
    def dlc(self) -> int:
        return len(self.data)

    @property
    def id_hex(self) -> str:
        return f"0x{self.arbitration_id:X}"


def _id_hex(arbitration_id: int) -> str:
    return f"0x{arbitration_id:X}"


def coerce_id(value) -> int:
    """把 0x100 / '0x100' / 256 / '100' 统一成整数 ID。"""
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 0)
    return int(value)


def _read_text(path: Path) -> str:
    """读取文本（CANoe 的 ASC 可能是 UTF-8 或 Latin-1，都兼容）。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# ===========================================================================
# 解析：ASC / BLF / 自动识别
# ===========================================================================
def parse_asc(path) -> list[Frame]:
    """解析 Vector ASC 文本文件，返回帧列表（按时间排序）。

    兼容本项目 `simulators/trace_tools.py` 导出的格式：
        date ...
        base hex  timestamps absolute
        Begin Triggerblock ...
           0.000000 1  100             Rx   d 8 C4 09 80 0C 82 33 00 00
        End TriggerBlock

    健壮性：头部/事件行跳过；坏数据行只计数不抛错（parse_asc_with_meta 可取到计数）。
    """
    frames, _meta = parse_asc_with_meta(path)
    return frames


def parse_asc_with_meta(path) -> tuple[list[Frame], dict]:
    """解析 ASC 并返回 (帧列表, 解析元信息)。

    元信息 dict: {format, lines, bad_lines, skipped_lines, timestamp_mode}
    —— 失败时 Trace 分析报告里会显示"跳过/坏行"数，对应真实排故时
    "Trace 是不是录漏了/写坏了"的第一判断。
    """
    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace 文件不存在: {trace_path}")
    text = _read_text(trace_path)
    frames: list[Frame] = []
    meta = {"format": "asc", "lines": 0, "bad_lines": 0,
            "skipped_lines": 0, "timestamp_mode": "absolute"}
    clock = 0.0

    for raw_line in text.splitlines():
        meta["lines"] += 1
        line = raw_line.strip()
        if not line:
            meta["skipped_lines"] += 1
            continue
        lower = line.lower()
        if lower.startswith(("date ", "base ", "internal ", "//",
                             "begin ", "end ")):
            # 头部行：记录时间戳模式（absolute / relative）
            if lower.startswith("base "):
                if "relative" in lower:
                    meta["timestamp_mode"] = "relative"
                elif "absolute" in lower:
                    meta["timestamp_mode"] = "absolute"
            meta["skipped_lines"] += 1
            continue

        parts = line.split()
        # 数据帧行：ts channel id Rx/Tx d dlc data...（至少 7 段）
        # 事件行（如 "0.000000 Start of measurement"）段数不足，直接跳过
        if len(parts) < 7 or parts[3] not in ("Rx", "Tx") or parts[4] != "d":
            meta["skipped_lines"] += 1
            continue
        try:
            timestamp = float(parts[0])
            channel = int(parts[1])
            id_token = parts[2]
            is_extended = id_token.lower().endswith("x")
            arbitration_id = int(id_token.rstrip("xX"), 16)
            dlc = int(parts[5])
            if not 0 <= dlc <= 64:
                raise ValueError(f"非法 DLC: {dlc}")
            data = bytes(int(token, 16) for token in parts[6:6 + dlc])
            if len(data) != dlc:
                raise ValueError(f"数据字节不足: {len(data)}/{dlc}")
        except (ValueError, IndexError):
            meta["bad_lines"] += 1          # 坏行跳过，不让整份 Trace 解析失败
            continue

        if meta["timestamp_mode"] == "relative":
            # 相对模式：每行时间戳是"相对上一帧"的增量 → 累计成绝对时间轴
            clock += timestamp
            timestamp = clock
        frames.append(Frame(timestamp=timestamp,
                            arbitration_id=arbitration_id,
                            data=data,
                            is_extended=is_extended,
                            channel=channel,
                            is_rx=(parts[3] == "Rx")))

    frames.sort(key=lambda frame: frame.timestamp)
    return frames, meta


def parse_blf(path) -> list[Frame]:
    """解析 Vector BLF 二进制文件（python-can 的 BLFReader），返回帧列表。"""
    frames, _meta = parse_blf_with_meta(path)
    return frames


def parse_blf_with_meta(path) -> tuple[list[Frame], dict]:
    """解析 BLF 并返回 (帧列表, 元信息)。坏文件抛 ValueError（带原因）。"""
    import can

    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace 文件不存在: {trace_path}")
    frames: list[Frame] = []
    try:
        with can.BLFReader(str(trace_path)) as reader:
            for message in reader:
                frames.append(Frame(
                    timestamp=float(message.timestamp),
                    arbitration_id=int(message.arbitration_id),
                    data=bytes(message.data),
                    is_extended=bool(message.is_extended_id),
                    channel=1,
                    is_rx=bool(getattr(message, "is_rx", True)),
                ))
    except Exception as exc:  # noqa: BLE001 —— python-can 的异常类型不止一种
        raise ValueError(
            f"BLF 解析失败（文件损坏或不是 BLF 格式）: {trace_path}: {exc}") from exc
    frames.sort(key=lambda frame: frame.timestamp)
    meta = {"format": "blf", "lines": len(frames), "bad_lines": 0,
            "skipped_lines": 0, "timestamp_mode": "absolute"}
    return frames, meta


def parse_trace(path) -> tuple[list[Frame], dict]:
    """自动识别 ASC/BLF 并解析，返回 (帧列表, 元信息)。

    识别顺序：扩展名 → 文件魔数（BLF 以 b"LOGG" 开头）→ 文本 ASC。
    """
    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace 文件不存在: {trace_path}")
    suffix = trace_path.suffix.lower()
    if suffix == ".blf":
        return parse_blf_with_meta(trace_path)
    if suffix == ".asc":
        return parse_asc_with_meta(trace_path)
    # 扩展名不可靠时看魔数（真实项目常见 .txt/.log 后缀的导出文件）
    head = trace_path.read_bytes()[:8]
    if head.startswith(BLF_MAGIC):
        return parse_blf_with_meta(trace_path)
    return parse_asc_with_meta(trace_path)


# ===========================================================================
# 基础统计
# ===========================================================================
def normalize_time(frames: list[Frame]) -> list[Frame]:
    """把时间轴归一化到 0 起点（第一帧 = 0.0）。

    为什么需要：ASC 导出的是"相对测量起点"、BLF 里是"Unix 绝对时间"，
    两种文件的时间戳量级差 9 个数量级；归一化后所有分析（时长/负载/异常时间）
    口径一致，报告里也更好读。
    """
    if not frames:
        return []
    origin = frames[0].timestamp
    return [Frame(timestamp=frame.timestamp - origin,
                  arbitration_id=frame.arbitration_id,
                  data=frame.data,
                  is_extended=frame.is_extended,
                  channel=frame.channel,
                  is_rx=frame.is_rx)
            for frame in frames]


def time_range(frames: list[Frame]) -> tuple[float, float, float]:
    """返回 (起始时间, 结束时间, 时长秒)。空 Trace 返回 (0, 0, 0)。"""
    if not frames:
        return 0.0, 0.0, 0.0
    start = min(frame.timestamp for frame in frames)
    end = max(frame.timestamp for frame in frames)
    return start, end, end - start


def frame_bits(frame: Frame) -> float:
    """单帧在总线上占用的位数估算（含 20% 位填充余量）。"""
    fixed = EXTENDED_FRAME_BITS if frame.is_extended else BASE_FRAME_BITS
    return (fixed + 8 * frame.dlc) * STUFFING_OVERHEAD


def compute_bus_load(frames: list[Frame], bitrate: int = DEFAULT_BITRATE,
                     duration: float | None = None) -> float:
    """计算总线负载率（百分比，如 0.43 表示 0.43%）。

    参数:
        frames   帧列表（建议先 normalize_time，负载与时间起点无关）
        bitrate  比特率 bps，默认 500 kbps
        duration 统计时长（秒）；缺省用"最后一帧 - 第一帧"的时间跨度

    公式: Σ(每帧位数) / (bitrate × duration) × 100
    """
    if not frames:
        raise ValueError("Trace 里没有帧，无法计算总线负载")
    if int(bitrate) <= 0:
        raise ValueError(f"比特率必须 > 0，收到 {bitrate}")
    span = float(duration) if duration is not None else time_range(frames)[2]
    if span <= 0:
        raise ValueError("记录时长必须 > 0（至少两帧且时间戳不同）"
                         "—— 帧太少无法计算总线负载")
    total_bits = sum(frame_bits(frame) for frame in frames)
    return total_bits / (int(bitrate) * span) * 100.0


def _group_by_id(frames: list[Frame]) -> dict[int, list[Frame]]:
    groups: dict[int, list[Frame]] = {}
    for frame in frames:
        groups.setdefault(frame.arbitration_id, []).append(frame)
    for group in groups.values():
        group.sort(key=lambda item: item.timestamp)
    return groups


def _period_summary(stamps: list[float]) -> dict:
    """由时间戳序列算周期统计（毫秒）。帧数 < 2 时周期字段为 None。"""
    if len(stamps) < 2:
        return {"count": len(stamps), "period_mean_ms": None,
                "period_std_ms": None, "period_min_ms": None,
                "period_max_ms": None, "jitter_ms": None}
    gaps_ms = [(stamps[i + 1] - stamps[i]) * 1000.0
               for i in range(len(stamps) - 1)]
    return {
        "count": len(stamps),
        "period_mean_ms": round(statistics.fmean(gaps_ms), 3),
        "period_std_ms": round(statistics.pstdev(gaps_ms), 3),
        "period_min_ms": round(min(gaps_ms), 3),
        "period_max_ms": round(max(gaps_ms), 3),
        "jitter_ms": round(max(gaps_ms) - min(gaps_ms), 3),
    }


def id_stats(frames: list[Frame]) -> dict:
    """按 ID 汇总统计：帧数、速率、DLC、周期均值/标准差/极值/抖动。

    返回:
        {
          "total_frames": 34, "unique_ids": 3, "duration_s": 1.9,
          "ids": {
            "0x100": {"count": 20, "dlc": 8, "rate_hz": 10.5,
                      "period_mean_ms": 100.1, "period_std_ms": 0.8,
                      "period_min_ms": 99.0, "period_max_ms": 102.0,
                      "jitter_ms": 3.0},
            ...
          }
        }
    """
    total_frames = len(frames)
    if not frames:
        return {"total_frames": 0, "unique_ids": 0, "duration_s": 0.0,
                "ids": {}}
    duration = time_range(frames)[2]
    groups = _group_by_id(frames)
    ids: dict[str, dict] = {}
    for arbitration_id in sorted(groups):
        group = groups[arbitration_id]
        summary = _period_summary([frame.timestamp for frame in group])
        count = summary.pop("count")
        dlc = group[0].dlc
        ids[_id_hex(arbitration_id)] = {
            "count": count,
            "dlc": dlc,
            "rate_hz": round(count / duration, 2) if duration > 0 else None,
            **summary,
        }
    return {
        "total_frames": total_frames,
        "unique_ids": len(groups),
        "duration_s": round(duration, 6),
        "ids": ids,
    }


def period_stats(frames: list[Frame], arbitration_id) -> dict:
    """单个 ID 的周期统计（单位：毫秒）。

    返回 dict 至少包含: count / mean / std / min / max / jitter
    （jitter = 最大间隔 - 最小间隔，是总线时序稳定性的核心指标）。
    """
    target = coerce_id(arbitration_id)
    stamps = sorted(frame.timestamp for frame in frames
                    if frame.arbitration_id == target)
    if len(stamps) < 2:
        raise ValueError(
            f"ID {_id_hex(target)} 只有 {len(stamps)} 帧，无法计算周期"
            f"（至少需要 2 帧）")
    gaps_ms = [(stamps[i + 1] - stamps[i]) * 1000.0
               for i in range(len(stamps) - 1)]
    return {
        "arbitration_id": target,
        "id_hex": _id_hex(target),
        "count": len(stamps),
        "mean": round(statistics.fmean(gaps_ms), 3),
        "std": round(statistics.pstdev(gaps_ms), 3),
        "min": round(min(gaps_ms), 3),
        "max": round(max(gaps_ms), 3),
        "jitter": round(max(gaps_ms) - min(gaps_ms), 3),
    }


# ===========================================================================
# 异常检测
# ===========================================================================
def detect_anomalies(frames: list[Frame],
                     expected_periods: dict | None = None,
                     tolerance: float = DEFAULT_ANOMALY_TOLERANCE) -> list[dict]:
    """检测 Trace 异常：丢帧 / 间隔异常 / 未知 ID。

    Arguments:
        frames           帧列表（建议先 normalize_time，异常时间更好读）
        expected_periods 期望周期表 {ID: 周期秒}，缺省用本项目的三条应用报文
        tolerance        间隔偏离期望周期的容差比例（0.5 = ±50%）

    Returns:
        异常 dict 列表（按时间排序），每个含:
        {type, arbitration_id, id_hex, time_s, detail, severity, ...}
        type 取值: missing_frame / interval_too_short / unknown_id
    """
    expected_raw = expected_periods if expected_periods is not None \
        else EXPECTED_PERIODS
    expected = {coerce_id(key): float(value)
                for key, value in expected_raw.items()}
    anomalies: list[dict] = []
    groups = _group_by_id(frames)

    # ---- 未知 ID：出现但不在期望清单里 ----
    for arbitration_id in sorted(groups):
        if arbitration_id in expected:
            continue
        group = groups[arbitration_id]
        anomalies.append({
            "type": "unknown_id",
            "arbitration_id": arbitration_id,
            "id_hex": _id_hex(arbitration_id),
            "time_s": round(group[0].timestamp, 6),
            "actual_gap_ms": None,
            "expected_period_ms": None,
            "missing_estimated": 0,
            "detail": f"未知 ID {_id_hex(arbitration_id)}：出现 {len(group)} 帧，"
                      f"不在期望周期清单 "
                      f"{sorted(_id_hex(key) for key in expected)} 中",
            "severity": "medium",
        })

    # ---- 间隔异常：丢帧 / 间隔过短 ----
    for arbitration_id in sorted(expected):
        period = expected[arbitration_id]
        if period <= 0:
            continue
        group = groups.get(arbitration_id)
        if not group or len(group) < 2:
            continue
        stamps = [frame.timestamp for frame in group]
        for previous, current in zip(stamps, stamps[1:]):
            gap = current - previous
            if gap > period * (1.0 + tolerance):
                missing = max(1, int(round(gap / period)) - 1)
                anomalies.append({
                    "type": "missing_frame",
                    "arbitration_id": arbitration_id,
                    "id_hex": _id_hex(arbitration_id),
                    "time_s": round(previous, 6),
                    "actual_gap_ms": round(gap * 1000.0, 3),
                    "expected_period_ms": round(period * 1000.0, 3),
                    "missing_estimated": missing,
                    "detail": f"{_id_hex(arbitration_id)} 在 "
                              f"{previous:.3f}s 后间隔 {gap * 1000:.1f}ms，"
                              f"期望 {period * 1000:.0f}ms —— 疑似丢 {missing} 帧",
                    "severity": "high",
                })
            elif gap < period * (1.0 - tolerance):
                anomalies.append({
                    "type": "interval_too_short",
                    "arbitration_id": arbitration_id,
                    "id_hex": _id_hex(arbitration_id),
                    "time_s": round(previous, 6),
                    "actual_gap_ms": round(gap * 1000.0, 3),
                    "expected_period_ms": round(period * 1000.0, 3),
                    "missing_estimated": 0,
                    "detail": f"{_id_hex(arbitration_id)} 间隔 "
                              f"{gap * 1000:.1f}ms，明显短于期望 "
                              f"{period * 1000:.0f}ms（疑似重复帧/突发）",
                    "severity": "medium",
                })

    anomalies.sort(key=lambda item: (item["time_s"], item["type"]))
    return anomalies


# ===========================================================================
# 分析报告
# ===========================================================================
def build_report(frames: list[Frame], bitrate: int = DEFAULT_BITRATE,
                 source: str | None = None, meta: dict | None = None) -> dict:
    """把一帧列表汇总成完整分析报告 dict（所有导出/CLI 共用）。"""
    if not frames:
        raise ValueError("Trace 里没有帧，无法生成分析报告")
    normalized = normalize_time(sorted(frames, key=lambda item: item.timestamp))
    duration = time_range(normalized)[2]
    stats = id_stats(normalized)
    anomalies = detect_anomalies(normalized)
    bus_load: float | None
    try:
        bus_load = round(compute_bus_load(normalized, bitrate=bitrate,
                                          duration=duration), 4)
    except ValueError:
        bus_load = None                 # 只有一帧等极端情况：负载无法计算
    return {
        "source": str(source) if source is not None else "",
        "format": (meta or {}).get("format", ""),
        "frames": len(normalized),
        "unique_ids": stats["unique_ids"],
        "duration_s": round(duration, 6),
        "bitrate": int(bitrate),
        "bus_load_percent": bus_load,
        "ids": stats["ids"],
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "parse_meta": meta or {},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def analyze(path, bitrate: int = DEFAULT_BITRATE) -> dict:
    """一步到位：解析 Trace 文件（自动识别 ASC/BLF）→ 生成分析报告。

    空文件/无法识别/内容损坏时抛 ValueError（消息里说明原因与解析统计）。
    """
    frames, meta = parse_trace(path)
    if not frames:
        raise ValueError(
            f"Trace 文件里没有解析到任何数据帧（空文件/格式不支持/内容损坏）: "
            f"{path}（共 {meta.get('lines', 0)} 行，跳过 "
            f"{meta.get('skipped_lines', 0)} 行，坏行 "
            f"{meta.get('bad_lines', 0)} 行）")
    return build_report(frames, bitrate=bitrate, source=str(path), meta=meta)


# ===========================================================================
# 报告导出（教学版：真实项目用 CANoe/CANalyzer 或自研平台出报告）
# ===========================================================================
def export_csv(report: dict, path) -> Path:
    """把分析报告导出成 CSV（Excel 可直接打开，utf-8-sig 防中文乱码）。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["# Trace 分析报告（教学版）"])
        writer.writerow(["源文件", report.get("source", "")])
        writer.writerow(["格式", report.get("format", "")])
        writer.writerow(["生成时间", report.get("generated_at", "")])
        writer.writerow(["帧数", report.get("frames", 0)])
        writer.writerow(["唯一 ID 数", report.get("unique_ids", 0)])
        writer.writerow(["记录时长(s)", report.get("duration_s", 0)])
        writer.writerow(["比特率(bps)", report.get("bitrate", 0)])
        writer.writerow(["总线负载(%)", report.get("bus_load_percent")])
        writer.writerow(["异常数", report.get("anomaly_count", 0)])
        writer.writerow([])
        writer.writerow(["ID", "帧数", "DLC", "速率(Hz)", "周期均值(ms)",
                         "周期标准差(ms)", "周期最小(ms)", "周期最大(ms)",
                         "抖动(ms)"])
        for id_hex, summary in report.get("ids", {}).items():
            writer.writerow([
                id_hex, summary.get("count"), summary.get("dlc"),
                summary.get("rate_hz"), summary.get("period_mean_ms"),
                summary.get("period_std_ms"), summary.get("period_min_ms"),
                summary.get("period_max_ms"), summary.get("jitter_ms"),
            ])
        writer.writerow([])
        writer.writerow(["异常类型", "ID", "时间(s)", "实际间隔(ms)",
                         "期望周期(ms)", "疑似丢帧", "说明"])
        for anomaly in report.get("anomalies", []):
            writer.writerow([
                anomaly.get("type"), anomaly.get("id_hex"),
                anomaly.get("time_s"), anomaly.get("actual_gap_ms"),
                anomaly.get("expected_period_ms"),
                anomaly.get("missing_estimated"), anomaly.get("detail"),
            ])
    return output


def export_html(report: dict, path) -> Path:
    """把分析报告导出成 HTML（单文件、内联样式，可直接发缺陷单/存档）。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    def escape(value) -> str:
        text = "" if value is None else str(value)
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    id_rows = "\n".join(
        f"<tr><td>{escape(id_hex)}</td>"
        f"<td>{escape(summary.get('count'))}</td>"
        f"<td>{escape(summary.get('dlc'))}</td>"
        f"<td>{escape(summary.get('rate_hz'))}</td>"
        f"<td>{escape(summary.get('period_mean_ms'))}</td>"
        f"<td>{escape(summary.get('period_std_ms'))}</td>"
        f"<td>{escape(summary.get('period_min_ms'))}</td>"
        f"<td>{escape(summary.get('period_max_ms'))}</td>"
        f"<td>{escape(summary.get('jitter_ms'))}</td></tr>"
        for id_hex, summary in report.get("ids", {}).items())

    anomaly_rows = "\n".join(
        f"<tr class='{escape(anomaly.get('severity', 'medium'))}'>"
        f"<td>{escape(anomaly.get('type'))}</td>"
        f"<td>{escape(anomaly.get('id_hex'))}</td>"
        f"<td>{escape(anomaly.get('time_s'))}</td>"
        f"<td>{escape(anomaly.get('actual_gap_ms'))}</td>"
        f"<td>{escape(anomaly.get('expected_period_ms'))}</td>"
        f"<td>{escape(anomaly.get('detail'))}</td></tr>"
        for anomaly in report.get("anomalies", []))
    if not anomaly_rows:
        anomaly_rows = ("<tr><td colspan='6'>未检测到异常"
                        "（对录制 Trace 来说这是好消息）</td></tr>")

    bus_load = report.get("bus_load_percent")
    bus_load_text = "无法计算" if bus_load is None else f"{bus_load}%"
    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Trace 分析报告</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
         margin: 32px; color: #1f2328; background: #f6f8fa; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .meta {{ color: #57606a; font-size: 13px; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
          padding: 12px 18px; min-width: 150px; }}
  .card .label {{ color: #57606a; font-size: 12px; }}
  .card .value {{ font-size: 22px; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
          margin-bottom: 24px; font-size: 13px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }}
  th {{ background: #eaeef2; }}
  tr.high td {{ background: #ffebe9; }}
  tr.medium td {{ background: #fff8c5; }}
  .note {{ color: #57606a; font-size: 12px; }}
</style>
</head>
<body>
<h1>Trace 分析报告（教学版）</h1>
<div class="meta">源文件: {escape(report.get("source", ""))} ｜
格式: {escape(report.get("format", ""))} ｜
生成时间: {escape(report.get("generated_at", ""))}</div>
<div class="cards">
  <div class="card"><div class="label">总帧数</div>
    <div class="value">{escape(report.get("frames", 0))}</div></div>
  <div class="card"><div class="label">唯一 ID</div>
    <div class="value">{escape(report.get("unique_ids", 0))}</div></div>
  <div class="card"><div class="label">记录时长</div>
    <div class="value">{escape(report.get("duration_s", 0))} s</div></div>
  <div class="card"><div class="label">总线负载</div>
    <div class="value">{escape(bus_load_text)}</div></div>
  <div class="card"><div class="label">异常数</div>
    <div class="value">{escape(report.get("anomaly_count", 0))}</div></div>
</div>
<h2>各 ID 统计</h2>
<table>
  <tr><th>ID</th><th>帧数</th><th>DLC</th><th>速率(Hz)</th>
      <th>周期均值(ms)</th><th>周期标准差(ms)</th><th>周期最小(ms)</th>
      <th>周期最大(ms)</th><th>抖动(ms)</th></tr>
{id_rows}
</table>
<h2>异常检测（丢帧 / 间隔异常 / 未知 ID）</h2>
<table>
  <tr><th>类型</th><th>ID</th><th>时间(s)</th><th>实际间隔(ms)</th>
      <th>期望周期(ms)</th><th>说明</th></tr>
{anomaly_rows}
</table>
<p class="note">教学版报告：真实项目通常由 CANoe/CANalyzer 或测试平台自动生成，
并作为失败证据附在缺陷单里。本报告字段与 asc_analyzer 的函数口径一致。</p>
</body>
</html>
"""
    output.write_text(html_text, encoding="utf-8")
    return output


def export_report(report: dict, path) -> Path:
    """按扩展名选择报告格式：.csv → CSV，.html/.htm → HTML。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return export_csv(report, path)
    if suffix in (".html", ".htm"):
        return export_html(report, path)
    raise ValueError(f"不支持的报告格式 {suffix}（用 .html 或 .csv）")


# ===========================================================================
# CLI
# ===========================================================================
def main(argv: list[str] | None = None) -> int:
    """命令行入口：分析一份 Trace，打印统计，可导出 HTML/CSV 报告。

    用法（项目根目录）：
        .\\.venv\\Scripts\\python.exe simulators/asc_analyzer.py ^
            --input results/dev_trace/bus_recording.asc ^
            --out results/dev_trace/trace_report.html --bitrate 500000
    """
    parser = argparse.ArgumentParser(description="Trace 分析器（ASC/BLF）")
    parser.add_argument("--input", required=True,
                        help="Trace 文件路径（.asc 或 .blf，自动识别）")
    parser.add_argument("--out", help="报告输出路径（.html 或 .csv）")
    parser.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE,
                        help=f"比特率 bps（默认 {DEFAULT_BITRATE}）")
    args = parser.parse_args(argv)

    report = analyze(args.input, bitrate=args.bitrate)
    bus_load = report["bus_load_percent"]
    print("=" * 68)
    print(f" Trace 分析: {report['source']}")
    print("=" * 68)
    print(f" 格式: {report['format']}   帧数: {report['frames']}   "
          f"时长: {report['duration_s']:.3f}s")
    print(f" 唯一 ID: {report['unique_ids']}   "
          f"总线负载: {'n/a' if bus_load is None else f'{bus_load:.4f}%'}"
          f"（{report['bitrate']} bps）")
    for id_hex, summary in report["ids"].items():
        print(f"   {id_hex:>6}  帧数 {summary['count']:>3}  "
              f"周期均值 {summary['period_mean_ms']}ms  "
              f"抖动 {summary['jitter_ms']}ms")
    print(f" 异常: {report['anomaly_count']} 条")
    for anomaly in report["anomalies"]:
        print(f"   [{anomaly['type']}] {anomaly['detail']}")
    if args.out:
        output = export_report(report, args.out)
        print(f"[asc_analyzer] 报告已导出: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

