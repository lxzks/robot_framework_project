"""
TraceAnalysisLibrary.py — Robot Framework Trace 分析关键字库
============================================================

定位：把 `simulators/asc_analyzer.py` 的 Trace 分析能力包装成 RF 关键字，
让用例能像 CANoe 的 Trace 窗口那样"回放、统计、找异常、出报告"。

    测试失败 → 拿到 ASC/BLF → 算负载/周期/抖动 → 检测丢帧/未知 ID → 导出报告

和视觉库（VisionLibrary）一样，本库只依赖**文件**，不关心 Trace 从哪来：
项目录制器导出的、CANoe 保存的、实车路测拷回来的 ASC/BLF 都能分析。

关键字一览（15 个）
-------------------
    解析/统计: Analyze Trace File / Trace Frame Count Should Be / Trace Should Contain ID
    负载:      Get Trace Bus Load / Bus Load Should Be Below
    ID 统计:   ID Stats Should Report
    周期:      Period Should Be Near / Jitter Should Be Within
    异常:      Anomalies Should Be Detected / Anomaly Count Should Be
    报告:      Export Analysis Report
    录制:      Generate Trace From Bus Recorder
    时间:      Trace Time Range Should Be
    对比:      Compare Two Traces
    夹具:      Write Synthetic Trace（负向用例造"缺帧/未知 ID"文件用）

典型用法
--------
    *** Settings ***
    Resource    ../../resources/common.resource     # 起 ECU + 提供 Start Bus Recorder
    Library     ../../libraries/TraceAnalysisLibrary.py

    录制并分析
        Start Bus Recorder
        Drain Bus For    2
        Stop Bus Recorder
        Export Bus Trace    results/bus.asc
        Analyze Trace File    results/bus.asc
        Period Should Be Near    results/bus.asc    0x100    expected_ms=100
        Anomalies Should Be Detected    results/bus.asc    anomaly_type=missing_frame
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

# 复用 simulators/ 里的分析器与总线（不重复实现解析/统计逻辑）
ROOT = Path(__file__).resolve().parent.parent
SIMULATORS_DIR = ROOT / "simulators"
if str(SIMULATORS_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATORS_DIR))

import asc_analyzer as analyzer                                 # noqa: E402

#: 默认比特率（与 asc_analyzer / 任务 12 非功能模块同口径）
DEFAULT_BITRATE = analyzer.DEFAULT_BITRATE
#: 三条应用报文的期望周期（复用分析器常量，避免两处定义漂移）
EXPECTED_PERIODS = analyzer.EXPECTED_PERIODS


class TraceAnalysisLibrary:
    """Trace（ASC/BLF）分析关键字集合。

    设计原则：
      · 每个关键字吃"文件路径"，内部缓存解析结果（同一文件只解析一次）；
        文件被改写（mtime/size 变化）时自动重新解析，不读到旧数据；
      · 断言失败信息带**实测数字**（帧数/周期/抖动/异常明细），可直接贴缺陷单；
      · 空文件/坏格式/AST 不存在都转成 AssertionError —— 用例报错必须能看懂。
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, bitrate: int = DEFAULT_BITRATE) -> None:
        self._bitrate = int(bitrate)
        # 缓存: {文件绝对路径: {"mtime","size","report"}}
        self._cache: dict[str, dict] = {}
        console(f"[TraceAnalysisLibrary] 比特率 {self._bitrate} bps，"
                f"期望周期 {', '.join(f'{k:#x}={v * 1000:.0f}ms' for k, v in EXPECTED_PERIODS.items())}")

    # ==================================================================
    # 内部工具
    # ==================================================================
    def _resolve(self, trace_path) -> Path:
        path = Path(str(trace_path))
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path

    def _analysis(self, trace_path, force: bool = False) -> dict:
        """解析/复用 Trace 分析报告（缓存按 mtime+size 失效）。"""
        path = self._resolve(trace_path)
        if not path.exists():
            raise AssertionError(
                f"Trace 文件不存在: {path} —— 先录制并导出 Trace"
                f"（Start Bus Recorder → Stop Bus Recorder → Export Bus Trace）")
        stat = path.stat()
        key = str(path)
        cached = self._cache.get(key)
        if (not force and cached is not None
                and cached["mtime"] == stat.st_mtime_ns
                and cached["size"] == stat.st_size):
            return cached["report"]
        try:
            report = analyzer.analyze(path, bitrate=self._bitrate)
        except FileNotFoundError as exc:
            raise AssertionError(str(exc)) from None
        except ValueError as exc:
            # 空文件/坏格式/无法识别 → 清晰报错（用例用 Run Keyword And Expect Error 锁死它）
            raise AssertionError(str(exc)) from None
        except OSError as exc:
            raise AssertionError(f"Trace 文件读取失败: {path}: {exc}") from None
        self._cache[key] = {"mtime": stat.st_mtime_ns, "size": stat.st_size,
                            "report": report}
        return report

    @staticmethod
    def _id_hex(arbitration_id) -> str:
        return f"0x{analyzer.coerce_id(arbitration_id):X}"

    @staticmethod
    def _opt_int(value) -> int | None:
        """RF 可选的整数参数（省略时是 None，用字符串也兼容）。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.upper() == "NONE":
            return None
        return int(text)

    def _id_summary(self, report: dict, arbitration_id) -> dict:
        id_hex = self._id_hex(arbitration_id)
        summary = report["ids"].get(id_hex)
        if summary is None:
            raise AssertionError(
                f"Trace 里没有 ID {id_hex}（实际 ID: "
                f"{', '.join(sorted(report['ids'])) or '无'}）")
        return summary

    @staticmethod
    def _filter_anomalies(anomalies: list[dict], anomaly_type=None,
                          arbitration_id=None) -> list[dict]:
        result = list(anomalies)
        if anomaly_type is not None:
            wanted = str(anomaly_type).strip()
            result = [item for item in result if item["type"] == wanted]
        if arbitration_id is not None:
            wanted_id = analyzer.coerce_id(arbitration_id)
            result = [item for item in result
                      if item["arbitration_id"] == wanted_id]
        return result

    def _count_should_match(self, actual: int, keyword: str,
                            expected=None, minimum=None, maximum=None,
                            detail: str = "") -> int:
        """expected/minimum/maximum 三选一的通用数量断言。"""
        expected = self._opt_int(expected)
        minimum = self._opt_int(minimum)
        maximum = self._opt_int(maximum)
        if expected is None and minimum is None and maximum is None:
            raise AssertionError(
                f"{keyword} 需要 expected / minimum / maximum 至少一个参数")
        problems = []
        if expected is not None and actual != expected:
            problems.append(f"期望等于 {expected}")
        if minimum is not None and actual < minimum:
            problems.append(f"期望 ≥ {minimum}")
        if maximum is not None and actual > maximum:
            problems.append(f"期望 ≤ {maximum}")
        if problems:
            raise AssertionError(
                f"{keyword} 断言失败：实际 {actual}，{'、'.join(problems)}"
                f"{('；' + detail) if detail else ''}")
        info(f"{keyword} 通过：实际 {actual}"
             f"{('（' + detail + '）') if detail else ''}")
        return actual

    # ==================================================================
    # 解析 / 统计
    # ==================================================================
    @keyword("Analyze Trace File")
    def analyze_trace_file(self, trace_path) -> dict:
        """解析 Trace 文件（ASC/BLF 自动识别）并输出分析摘要，返回报告 dict。

        返回的 report dict 关键字段：
            format / frames / unique_ids / duration_s / bitrate /
            bus_load_percent / ids{ID: 统计} / anomalies[列表] / anomaly_count

        适合做"整份 Trace 体检"：后面所有断言关键字都基于同一份分析结果。
        """
        report = self._analysis(trace_path, force=True)
        info(f"Trace 分析: {report['source']}")
        info(f"  格式={report['format']} 帧数={report['frames']} "
             f"时长={report['duration_s']:.3f}s 唯一ID={report['unique_ids']} "
             f"总线负载={report['bus_load_percent']}%")
        for id_hex, summary in report["ids"].items():
            info(f"  {id_hex}: {summary['count']} 帧, "
                 f"周期均值 {summary['period_mean_ms']}ms, "
                 f"抖动 {summary['jitter_ms']}ms")
        info(f"  异常 {report['anomaly_count']} 条")
        return report

    @keyword("Trace Frame Count Should Be")
    def trace_frame_count_should_be(self, trace_path, expected=None,
                                    minimum=None, maximum=None) -> int:
        """断言 Trace 的帧数（支持精确值/下限/上限三种写法）。

        Arguments:
        - ``trace_path``：ASC/BLF 文件路径
        - ``expected``：精确帧数（``Trace Frame Count Should Be  trace.asc  expected=34``）
        - ``minimum`` / ``maximum``：范围断言（录制类 Trace 帧数会浮动，推荐用 range）

        返回实际帧数。
        """
        report = self._analysis(trace_path)
        return self._count_should_match(
            report["frames"], "帧数", expected=expected,
            minimum=minimum, maximum=maximum,
            detail=f"文件 {Path(str(trace_path)).name}")

    @keyword("Trace Should Contain ID")
    def trace_should_contain_id(self, trace_path, arbitration_id,
                                min_frames=1) -> int:
        """断言 Trace 里出现了指定 CAN ID（且至少 min_frames 帧），返回帧数。

        Arguments:
        - ``arbitration_id``：支持 ``0x100`` / ``256`` 两种写法
        - ``min_frames``：至少出现多少帧（默认 1，周期报文建议 ≥ 3）
        """
        report = self._analysis(trace_path)
        summary = self._id_summary(report, arbitration_id)
        required = self._opt_int(min_frames) or 1
        if summary["count"] < required:
            raise AssertionError(
                f"Trace 里 ID {self._id_hex(arbitration_id)} 只出现 "
                f"{summary['count']} 帧，少于要求的 {required} 帧")
        info(f"Trace 含 ID {self._id_hex(arbitration_id)}："
             f"{summary['count']} 帧 ✓")
        return summary["count"]

    # ==================================================================
    # 总线负载
    # ==================================================================
    @keyword("Get Trace Bus Load")
    def get_trace_bus_load(self, trace_path) -> float:
        """返回 Trace 的总线负载率（百分比，如 0.44 表示 0.44%）。

        口径：每帧位数 ≈ (47 + 8×DLC) × 1.2（含 20% 位填充余量），
        再除以 比特率 × 记录时长。详见 asc_analyzer.compute_bus_load()。
        """
        report = self._analysis(trace_path)
        load = report["bus_load_percent"]
        if load is None:
            raise AssertionError("Trace 帧数/时长不足，无法计算总线负载")
        info(f"总线负载 {load:.4f}%（比特率 {report['bitrate']} bps，"
             f"时长 {report['duration_s']:.3f}s）")
        return float(load)

    @keyword("Bus Load Should Be Below")
    def bus_load_should_be_below(self, trace_path, max_percent=50.0) -> float:
        """断言总线负载率低于上限（默认 50%，教学仿真环境实测约 0.4%~0.6%）。

        Arguments:
        - ``max_percent``：负载上限百分比。真实项目按车型/总线设计规范设
          （常见舒适 CAN 30%/动力 CAN 50% 以内）。
        """
        report = self._analysis(trace_path)
        load = report["bus_load_percent"]
        if load is None:
            raise AssertionError("Trace 帧数/时长不足，无法计算总线负载")
        limit = float(max_percent)
        if load > limit:
            raise AssertionError(
                f"总线负载 {load:.4f}% 超过上限 {limit:.2f}%"
                f"（{report['frames']} 帧 / {report['duration_s']:.3f}s / "
                f"{report['bitrate']} bps）")
        info(f"总线负载 {load:.4f}% ≤ 上限 {limit:.2f}% ✓")
        return float(load)

    # ==================================================================
    # ID 统计 / 周期 / 抖动
    # ==================================================================
    @keyword("ID Stats Should Report")
    def id_stats_should_report(self, trace_path, arbitration_id=None,
                               min_frames=3) -> dict:
        """输出/断言 ID 统计（帧数、速率、周期均值/标准差/极值/抖动）。

        Arguments:
        - ``arbitration_id``：给定时只报告/断言该 ID；省略则报告全部 ID
        - ``min_frames``：若指定 ID，要求至少有这么多帧（默认 3，周期统计才有意义）

        返回：指定 ID 时为该 ID 的统计 dict；省略时为 {ID: 统计} 全量 dict。
        """
        report = self._analysis(trace_path)
        if arbitration_id is not None:
            summary = self._id_summary(report, arbitration_id)
            required = self._opt_int(min_frames) or 1
            if summary["count"] < required:
                raise AssertionError(
                    f"ID {self._id_hex(arbitration_id)} 只有 "
                    f"{summary['count']} 帧（至少需要 {required} 帧才能做周期统计）")
            info(f"{self._id_hex(arbitration_id)} 统计: "
                 f"{summary['count']} 帧, DLC={summary['dlc']}, "
                 f"速率={summary['rate_hz']}Hz, "
                 f"周期均值={summary['period_mean_ms']}ms, "
                 f"标准差={summary['period_std_ms']}ms, "
                 f"范围=[{summary['period_min_ms']}, "
                 f"{summary['period_max_ms']}]ms, "
                 f"抖动={summary['jitter_ms']}ms")
            return summary
        if report["unique_ids"] == 0:
            raise AssertionError(f"Trace 里没有任何帧: {trace_path}")
        for id_hex, summary in report["ids"].items():
            info(f"{id_hex}: {summary['count']} 帧, "
                 f"速率={summary['rate_hz']}Hz, "
                 f"周期均值={summary['period_mean_ms']}ms, "
                 f"抖动={summary['jitter_ms']}ms")
        return report["ids"]

    @keyword("Period Should Be Near")
    def period_should_be_near(self, trace_path, arbitration_id, expected_ms,
                              tolerance_ms=15.0) -> float:
        """断言指定 ID 的**周期均值**接近期望值（毫秒），返回实测周期均值。

        Arguments:
        - ``arbitration_id``：如 ``0x100``
        - ``expected_ms``：期望周期，如车速报文 100
        - ``tolerance_ms``：允许偏差（默认 ±15ms；本项目 0x100 实测约 100±3ms）

        为什么断言"均值 ± 容差"而不是精确值：仿真/台架的调度抖动是客观存在的，
        容差要按"接收方超时阈值"倒推（如 100ms 报文的接收超时一般是 2~3 倍周期）。
        """
        report = self._analysis(trace_path)
        summary = self._id_summary(report, arbitration_id)
        mean = summary["period_mean_ms"]
        if mean is None:
            raise AssertionError(
                f"ID {self._id_hex(arbitration_id)} 帧数不足，无法计算周期")
        expected = float(expected_ms)
        tolerance = float(tolerance_ms)
        if abs(mean - expected) > tolerance:
            raise AssertionError(
                f"ID {self._id_hex(arbitration_id)} 周期均值 {mean:.3f}ms "
                f"偏离期望 {expected:.1f}ms 超过 ±{tolerance:.1f}ms"
                f"（{summary['count']} 帧，范围 "
                f"[{summary['period_min_ms']}, {summary['period_max_ms']}]ms，"
                f"抖动 {summary['jitter_ms']}ms）")
        info(f"ID {self._id_hex(arbitration_id)} 周期均值 {mean:.3f}ms ≈ "
             f"期望 {expected:.1f}ms（容差 ±{tolerance:.1f}ms）✓")
        return float(mean)

    @keyword("Jitter Should Be Within")
    def jitter_should_be_within(self, trace_path, arbitration_id,
                                max_jitter_ms=50.0) -> float:
        """断言指定 ID 的**抖动**（最大间隔 - 最小间隔）不超过上限，返回抖动值。

        Arguments:
        - ``max_jitter_ms``：抖动上限（默认 50ms）

        为什么单独测抖动：周期均值很容易"看起来正常"——几帧晚到 30ms、几帧早到
        30ms，均值仍是 100ms，但接收方可能已经超时/丢帧。抖动才是时序稳定性的
        直接指标（真实项目用 CANoe 的 Statistics 窗口看同一数据）。
        """
        report = self._analysis(trace_path)
        summary = self._id_summary(report, arbitration_id)
        jitter = summary["jitter_ms"]
        if jitter is None:
            raise AssertionError(
                f"ID {self._id_hex(arbitration_id)} 帧数不足，无法计算抖动")
        limit = float(max_jitter_ms)
        if jitter > limit:
            raise AssertionError(
                f"ID {self._id_hex(arbitration_id)} 抖动 {jitter:.3f}ms "
                f"超过上限 {limit:.1f}ms（周期范围 "
                f"[{summary['period_min_ms']}, {summary['period_max_ms']}]ms，"
                f"均值 {summary['period_mean_ms']}ms）")
        info(f"ID {self._id_hex(arbitration_id)} 抖动 {jitter:.3f}ms ≤ "
             f"{limit:.1f}ms ✓")
        return float(jitter)

    # ==================================================================
    # 异常检测
    # ==================================================================
    @keyword("Anomalies Should Be Detected")
    def anomalies_should_be_detected(self, trace_path, anomaly_type=None,
                                     arbitration_id=None) -> list:
        """断言 Trace 里检出了异常（可指定类型/ID），返回异常列表。

        Arguments:
        - ``anomaly_type``：``missing_frame``（丢帧）/ ``interval_too_short``
          （间隔过短）/ ``unknown_id``（未知 ID）；省略则任意异常都算
        - ``arbitration_id``：只看指定 ID

        典型用法（负向用例）：
            Anomalies Should Be Detected    missing.asc    anomaly_type=missing_frame
            Anomalies Should Be Detected    unknown.asc    anomaly_type=unknown_id
        """
        report = self._analysis(trace_path)
        selected = self._filter_anomalies(report["anomalies"],
                                          anomaly_type=anomaly_type,
                                          arbitration_id=arbitration_id)
        if not selected:
            scope = (f"类型={anomaly_type or '任意'}"
                     f"，ID={self._id_hex(arbitration_id) if arbitration_id is not None else '任意'}")
            raise AssertionError(
                f"期望检测到异常，但 {Path(str(trace_path)).name} 里"
                f"没有符合条件的异常（{scope}；全量异常 "
                f"{report['anomaly_count']} 条）")
        info(f"检测到 {len(selected)} 条异常（类型={anomaly_type or '任意'}）:")
        for anomaly in selected[:5]:
            info(f"  [{anomaly['type']}] {anomaly['detail']}")
        return selected

    @keyword("Anomaly Count Should Be")
    def anomaly_count_should_be(self, trace_path, expected=None, minimum=None,
                                maximum=None, anomaly_type=None,
                                arbitration_id=None) -> int:
        """断言异常数量（精确值/下限/上限三选一），返回实际条数。

        可先用 ``anomaly_type`` / ``arbitration_id`` 过滤，再对数量做断言。
        例：``Anomaly Count Should Be  missing.asc  expected=1  anomaly_type=missing_frame``
        """
        report = self._analysis(trace_path)
        selected = self._filter_anomalies(report["anomalies"],
                                          anomaly_type=anomaly_type,
                                          arbitration_id=arbitration_id)
        return self._count_should_match(
            len(selected), "异常数", expected=expected,
            minimum=minimum, maximum=maximum,
            detail=f"类型={anomaly_type or '任意'}，"
                   f"ID={self._id_hex(arbitration_id) if arbitration_id is not None else '任意'}")

    # ==================================================================
    # 报告
    # ==================================================================
    @keyword("Export Analysis Report")
    def export_analysis_report(self, trace_path, output_path) -> str:
        """把 Trace 分析报告导出成 HTML 或 CSV（按输出扩展名自动选格式），返回路径。

        HTML：单文件、内联样式，可直接进缺陷单/存档（含总览卡片 + ID 统计表 +
        异常表 + 红色/黄色高亮）。
        CSV：Excel 可直接打开（utf-8-sig 防中文乱码）。

        真实项目里这份报告就是"失败现场证据包"的一部分：Trace + 分析报告 + 截图。
        """
        report = self._analysis(trace_path, force=True)
        output = self._resolve(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = analyzer.export_report(report, output)
        except ValueError as exc:
            raise AssertionError(
                f"报告导出失败: {exc}") from None
        if not result.exists() or result.stat().st_size == 0:
            raise AssertionError(f"报告导出后文件为空: {result}")
        info(f"分析报告已导出: {result}（{result.stat().st_size} 字节，"
             f"帧数 {report['frames']}，异常 {report['anomaly_count']} 条）")
        return str(result)

    # ==================================================================
    # 录制 / 时间范围 / 对比 / 夹具
    # ==================================================================
    @keyword("Generate Trace From Bus Recorder")
    def generate_trace_from_bus_recorder(self, output_path, duration=2.0,
                                         channel=1) -> str:
        """从虚拟总线**录制** N 秒并直接生成 Trace 文件（.asc/.blf），返回路径。

        什么时候用：做独立分析脚本时（不经过 VehicleBusLibrary 的录制器）——
        本关键字自带一个临时总线连接，录完立刻导出，适合"环境已由别的进程
        启动、只想抓一段现场"的场景。
        套件里更推荐用现有录制器：
            Start Bus Recorder → Drain Bus For → Stop Bus Recorder → Export Bus Trace

        参数:
        - ``output_path``：输出文件（扩展名决定格式：.asc / .blf）
        - ``duration``：录制秒数（默认 2.0，支持 ``2s`` 写法）
        """
        from bus import VirtualCanBus            # 延迟导入：分析纯文件时无需总线
        from trace_tools import export_frames

        seconds = float(str(duration).replace("s", ""))
        if seconds <= 0:
            raise AssertionError(f"录制时长必须 > 0，收到 {duration}")
        output = self._resolve(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        records: list[tuple] = []
        last_seen: dict[tuple, float] = {}
        with VirtualCanBus("TRACE-ANALYZER") as bus:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                message = bus.recv_frame(timeout=0.05)
                if message is None:
                    continue
                now = time.monotonic()
                key = (message.arbitration_id, bytes(message.data))
                previous = last_seen.get(key)
                last_seen[key] = now
                # 多网卡机器上 UDP 组播会重复回环同一帧：同 (ID, 数据) 5ms 内去重
                if previous is not None and (now - previous) * 1000.0 < 5.0:
                    continue
                records.append((time.time(), message.arbitration_id,
                                bytes(message.data),
                                bool(message.is_extended_id)))
        if not records:
            raise AssertionError(
                f"{seconds}s 内总线上没有帧 —— 先启动仿真环境（ecu_sim.py），"
                "再调用本关键字录制")
        count = export_frames(records, output)
        info(f"已录制 {seconds}s / {count} 帧 → {output}")
        return str(output)

    @keyword("Trace Time Range Should Be")
    def trace_time_range_should_be(self, trace_path, expected_seconds,
                                   tolerance_seconds=0.5) -> float:
        """断言 Trace 的时间跨度（最后一帧 - 第一帧）接近期望秒数，返回实测跨度。

        录制 2 秒的 Trace 实测跨度通常略小于 2 秒（首末帧不正好落在边界），
        所以默认容差 0.5s；对"归档是否完整/是否录了一半"这类问题足够敏感。
        """
        report = self._analysis(trace_path)
        duration = float(report["duration_s"])
        expected = float(expected_seconds)
        tolerance = float(tolerance_seconds)
        if abs(duration - expected) > tolerance:
            raise AssertionError(
                f"Trace 时间跨度 {duration:.3f}s 偏离期望 {expected:.1f}s "
                f"超过 ±{tolerance:.1f}s（{report['frames']} 帧）")
        info(f"Trace 时间跨度 {duration:.3f}s ≈ 期望 {expected:.1f}s ✓")
        return duration

    @keyword("Compare Two Traces")
    def compare_two_traces(self, trace_a, trace_b, count_tolerance_pct=5.0,
                           period_tolerance_ms=10.0) -> dict:
        """对比两份 Trace：ID 集合、帧数、各 ID 周期均值是否一致，返回差异摘要。

        典型用途：**同一次录制的 ASC 与 BLF 交叉验证**（两种格式应解析出相同结果）、
        两次回归 run 的一致性对比（帧数波动 <5%）。

        Arguments:
        - ``count_tolerance_pct``：两份 Trace 帧数允许的相对差（默认 5%）
        - ``period_tolerance_ms``：同一 ID 周期均值允许差（默认 10ms）
        """
        report_a = self._analysis(trace_a)
        report_b = self._analysis(trace_b)
        ids_a = set(report_a["ids"])
        ids_b = set(report_b["ids"])
        if ids_a != ids_b:
            raise AssertionError(
                f"两份 Trace 的 ID 集合不一致：A={sorted(ids_a)} vs "
                f"B={sorted(ids_b)}")
        frames_a, frames_b = report_a["frames"], report_b["frames"]
        largest = max(frames_a, frames_b, 1)
        count_diff_pct = abs(frames_a - frames_b) / largest * 100.0
        if count_diff_pct > float(count_tolerance_pct):
            raise AssertionError(
                f"两份 Trace 帧数差异 {count_diff_pct:.2f}% 超过 "
                f"{float(count_tolerance_pct):.2f}%（A={frames_a}, B={frames_b}）")
        period_deltas: dict[str, float] = {}
        for id_hex in sorted(ids_a):
            mean_a = report_a["ids"][id_hex]["period_mean_ms"]
            mean_b = report_b["ids"][id_hex]["period_mean_ms"]
            if mean_a is None or mean_b is None:
                continue
            delta = abs(mean_a - mean_b)
            period_deltas[id_hex] = round(delta, 3)
            if delta > float(period_tolerance_ms):
                raise AssertionError(
                    f"ID {id_hex} 周期均值差异 {delta:.3f}ms 超过 "
                    f"{float(period_tolerance_ms):.1f}ms"
                    f"（A={mean_a}ms, B={mean_b}ms）")
        duration_delta = abs(float(report_a["duration_s"])
                             - float(report_b["duration_s"]))
        summary = {
            "ids": sorted(ids_a),
            "frame_count_a": frames_a,
            "frame_count_b": frames_b,
            "count_diff_pct": round(count_diff_pct, 3),
            "period_deltas_ms": period_deltas,
            "duration_delta_s": round(duration_delta, 6),
        }
        info(f"两份 Trace 对比通过：ID 集合一致 {sorted(ids_a)}，"
             f"帧数 {frames_a}/{frames_b}（差异 {count_diff_pct:.2f}%），"
             f"周期均值最大差异 "
             f"{max(period_deltas.values()) if period_deltas else 0:.3f}ms")
        return summary

    @keyword("Write Synthetic Trace")
    def write_synthetic_trace(self, output_path, frames, channel=1) -> str:
        """写一份合成 ASC 文件（测试夹具），返回路径。

        用途：**负向用例需要"已知的坏 Trace"**——手工构造缺帧/未知 ID 的场景，
        验证异常检测算法真的能抓到（而不是只会分析"正常录制"）。

        Arguments:
        - ``frames``：帧列表，每项 ``[时间戳秒, ID, 数据hex]`` 或
          ``[时间戳秒, ID, 数据hex, 是否扩展帧]``，如
          ``[[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"]]``
        - ``channel``：通道号（默认 1）

        输出格式与 `simulators/trace_tools.py` 的 ASC 完全一致（CANoe 可打开）。
        """
        if not frames:
            raise AssertionError("合成 Trace 需要至少一帧")
        parsed: list[tuple[float, int, bytes, bool]] = []
        for index, item in enumerate(frames):
            try:
                timestamp = float(item[0])
                arbitration_id = analyzer.coerce_id(item[1])
                raw_data = item[2]
                is_extended = bool(item[3]) if len(item) > 3 else False
            except (TypeError, ValueError, IndexError) as exc:
                raise AssertionError(
                    f"第 {index + 1} 帧格式错误（应形如 "
                    f"[0.0, 0x100, \"11223344\"]）: {item!r}: {exc}") from None
            if isinstance(raw_data, (bytes, bytearray)):
                data = bytes(raw_data)
            else:
                text = str(raw_data).replace(" ", "")
                try:
                    data = bytes.fromhex(text)
                except ValueError as exc:
                    raise AssertionError(
                        f"第 {index + 1} 帧数据不是合法十六进制: {raw_data!r}") from None
            parsed.append((timestamp, arbitration_id, data, is_extended))
        parsed.sort(key=lambda record: record[0])
        origin = parsed[0][0]

        stamp = datetime.now().strftime(
            "%a %b %d %I:%M:%S %Y")
        lines = [
            f"date {stamp}",
            "base hex  timestamps absolute",
            "internal events logged",
            "// synthetic trace (TraceAnalysisLibrary.write_synthetic_trace)",
            f"Begin Triggerblock {stamp}",
        ]
        for timestamp, arbitration_id, data, is_extended in parsed:
            id_text = f"{arbitration_id:X}" + ("x" if is_extended else "")
            hex_bytes = " ".join(f"{byte:02X}" for byte in data)
            lines.append(
                f"   {timestamp - origin:10.6f} {int(channel)}  {id_text:<15}"
                f" Rx   d {len(data)} {hex_bytes}")
        lines.append("End TriggerBlock")
        output = self._resolve(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        info(f"已生成合成 Trace: {output}（{len(parsed)} 帧）")
        return str(output)

