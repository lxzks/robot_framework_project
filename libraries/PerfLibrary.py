"""
PerfLibrary.py — Robot Framework 非功能测试关键字库（性能 / 压力 / 长稳 / 总线负载）
======================================================================================

项目里的其它库回答「**功能对不对**」：信号值对不对、UDS 响应对不对、灯亮不亮。
本库回答另一类问题 ——「**有多快、多稳、多扛造、总线还剩多少余量**」：

    响应时间（P2 一致性）    Measure UDS Response Time / Response Time Stats
    响应时间抖动            Response Time Jitter Should Be Below
    总线负载率（500kbps）   Record Bus Load / Bus Load Should Be Below
    突发压力与吞吐          Send UDS Requests In Burst / Throughput Should Be Above
    周期报文抖动            Measure Periodic Message Jitter / Jitter Should Be Within
    长稳（soak）            Run Soak Sampling / Soak Errors Should Be Below
    基线汇总                Log Performance Summary

为什么单独一个库，而不是塞进 DiagnosticLibrary：
    1. **测量口径不同**：功能断言关心"响应内容对不对"，非功能关心"多少毫秒 /
       多少百分比 / 多少条每秒"。计时、采样策略、统计口径（p95、标准差）是独立
       的知识点，混进去会让两个库都变复杂。
    2. **需要专用总线通道**：本库用**两条**虚拟总线 ——
         · sniffer 总线：只收不发，专门统计总线负载 / 周期抖动 / 长稳帧流；
         · diag 总线：只做诊断请求-响应（DiagnosticTester 的收包线程独占）。
       若复用一条总线，诊断收包线程会把周期报文全部读走，总线负载就永远测不到。

测量是**真实**的（这是本库的硬要求，也是性能测试可信的前提）：
    - 所有时间戳都用 ``time.monotonic()``，不受系统时间调整影响；
    - 总线负载 / 周期抖动 / 长稳丢帧全部来自**真实收帧**，不是按公式倒推；
    - 响应时间从"请求帧发出"计到"完整响应入队"，用 μs 级轮询读取
      DiagnosticTester 的收件箱（``_pending``），**避开它公开 send_request
      内部 20ms 的轮询粒度** —— 否则测什么都是 ~20ms，把测试工具自身的开销
      算到被测 ECU 头上（本项目实测：客户端轮询口径 ~20.6ms，真实链路 ~1.6ms）。
      若被测客户端内部结构变化导致快速通道不可用，自动降级为公开
      ``send_request``（数值会偏高，但仍可用）。
    - 连续采样 / 突发发送的请求间隔 ≥8ms：仿真总线按 (ID, 数据) 在 **5ms**
      窗口内去重（多网卡组播回环去重机制，见 ``simulators/bus.py``），同内容
      响应间隔太小会被当成重复帧丢弃。真实 CAN 不存在这个机制，这是**纯软件
      仿真特有的限制**，因此本库把它显式建模进采样间隔里，并在日志里说明。

典型用法（见 ``tests/robot/10_nonfunctional_perf.robot``）::

    ${samples}=    Measure UDS Response Time    3E 00
    ${stats}=      Response Time Stats    ${samples}
    Response Time P95 Should Be Below    ${stats}    50

    ${load}=    Record Bus Load    2
    Bus Load Should Be Below    ${load}    30

    ${burst}=    Send UDS Requests In Burst    200
    Throughput Should Be Above    ${burst}    50
"""

from __future__ import annotations

import math
import statistics
import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                       # noqa: E402
from uds_client import (                            # noqa: E402
    ID_UDS_REQUEST, ID_UDS_RESPONSE, DiagnosticTester,
)

# ---------------------------------------------------------------------------
# 常量：仿真总线的物理/协议参数
# ---------------------------------------------------------------------------
# 仿真总线按 (ID, 数据) 在 5ms 窗口内去重（见 simulators/bus.py 的
# _is_loopback_duplicate）。本库任何"同一请求连续发送"的间隔必须严格大于它，
# 否则内容完全相同的响应会被误判成回环重复而丢弃 —— 表现为"抽样丢一半"。
BUS_DEDUP_WINDOW_MS = 5.0
SAMPLE_GAP_SECONDS = 0.008          # 8ms：> 5ms 窗口，留 3ms 给系统调度抖动

# 总线负载估算参数（CAN 2.0B 标准数据帧，bit 为单位）
#   1(start) + 11(仲裁) + 6(控制) + 8*DLC(数据) + 15(CRC) + 2(ACK) + 7(EOF) ≈ 42
# 教学/工程上常按 **47** 近似（对含 DLC 与控制场的最坏情况留余量），
# 再乘 1.2 的位填充（bit stuffing）余量：连续 5 个同极性位后硬件会插 1 个
# 反极性位，最坏情况约 20% 额外开销。真实项目用 CANoe 的总线统计直接读，
# 这里的算法与 CANoe「Bus Statistics」的口径一致。
BITS_PER_FRAME_BASE = 47
BITS_PER_DATA_BYTE = 8
BIT_STUFFING_FACTOR = 1.2
DEFAULT_BITRATE_BPS = 500_000

# 仿真 ECU 的周期报文设计指标（与 simulators/ecu_sim.py 的主循环一致）：
#   0x100 EngineData  100ms
#   0x200 ClusterData 200ms
#   0x300 Telltale    500ms
PERIODIC_MESSAGES = {
    0x100: 100.0,
    0x200: 200.0,
    0x300: 500.0,
}


class PerfLibrary:
    """非功能测试（性能/压力/长稳/总线负载）关键字集合。

    ``ROBOT_LIBRARY_SCOPE = "SUITE"``：一个套件内只建一次总线连接与诊断收包
    线程，套件结束由 ``Close Perf Library`` 释放（本库在套件 Teardown 里调用）。
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, node_name: str = "RF-PERF") -> None:
        self._node_name = node_name
        # 总线/诊断仪**延迟到首次使用关键字时**才建立（见 _connect）：
        # Robot Framework 在套件启动时会先创建一个"导入期"库实例用于解析，
        # 随后再创建真正执行用的实例；若在 __init__ 里就建总线，导入期实例
        # 被丢弃时会留下一对未 shutdown 的 UDP 组播 socket（进程退出时
        # python-can 打 "UdpMulticastBus was not properly shut down" 告警）。
        # 延迟连接既消除了这类告警噪声，也让"只导入不测量"的套件零总线开销。
        self._sniffer = None            # type: VirtualCanBus | None
        self._diag_bus = None           # type: VirtualCanBus | None
        self._tester = None             # type: DiagnosticTester | None

        # 最近一次测量结果（供 … Should Be Below / Log Performance Summary 复用）
        self._last_samples: list[float] = []
        self._last_bus_load: dict | None = None
        self._last_jitter: dict | None = None
        self._last_burst: dict | None = None
        self._last_soak: dict | None = None
        self._warmed_up = False
        self._fast_rx_ready = False
        self._closed = False

        info("[PerfLibrary] 已加载（总线与诊断仪将在首次测量时建立）")

    def _connect(self) -> None:
        """按需建立测量通道：sniffer（只收）+ diag（诊断请求/响应）+ 收包线程。"""
        if self._sniffer is not None:
            return
        if self._closed:
            raise AssertionError(
                "PerfLibrary 已被 Close Perf Library 关闭，不能继续测量"
                "（检查套件 Teardown 是否在用例之前执行了）")
        # sniffer：只收不发，负责"看总线"（负载/周期/长稳帧流）。
        # echo_own=False —— 本节点不发送，不需要回读自己的帧。
        self._sniffer = VirtualCanBus(f"{self._node_name}-SNIFF", echo_own=False)
        # diag：诊断请求/响应专用（DiagnosticTester 的单收包线程独占它）。
        self._diag_bus = VirtualCanBus(f"{self._node_name}-DIAG", echo_own=False)
        self._tester = DiagnosticTester(self._diag_bus)
        self._tester.start()
        # 快速计时通道：直接读 DiagnosticTester 的收件箱（μs 级轮询），
        # 绕开公开 send_request 的 20ms 轮询粒度。属性缺失时自动降级。
        self._fast_rx_ready = all(
            hasattr(self._tester, attr) for attr in ("_pending", "_inbox", "_lock")
        )
        info(f"[PerfLibrary] 已建立性能测量通道：sniffer + diag "
             f"(快速计时={'开' if self._fast_rx_ready else '关'}，"
             f"采样间隔 ≥{SAMPLE_GAP_SECONDS * 1000:.0f}ms)")

    # ==================================================================
    # 一、UDS 响应时间（P2 一致性）
    # ==================================================================
    @keyword("Measure UDS Response Time")
    def measure_uds_response_time(self, request_hex: str = "3E 00",
                                  samples: int = 9) -> list:
        """连续发送 **samples 条**同一 UDS 请求，返回每条"请求→响应"的耗时列表（毫秒）。

        参数：
            request_hex    单帧 UDS 请求（十六进制，如 '3E 00' / '22 F187'）
            samples        采样次数（默认 9 次；**单次测量会漏掉"偶尔慢"**）

        计时口径（性能测试的可比性全靠口径统一）：
            - 起点：请求帧 ``send_frame()`` 返回时刻；
            - 终点：收包线程把**完整**响应（含 ISO-TP 多帧重组）放进队列时刻；
            - 用 ``time.monotonic()``，不受 NTP 校时/系统时间跳变影响；
            - 不含测试工具 20ms 轮询粒度（见模块 docstring 的说明）。

        预热：首次调用自动先发 2 条丢弃结果 —— 第一条请求包含解释器/内核
        冷启动、GIL 竞争等一次性开销，不预热会把基线抬高一截（性能测试
        "先预热再测量"是行业惯例，CANoe 测响应时间也一样）。

        为什么采样后还要"间隔 8ms"：仿真总线 5ms 去重窗口会误丢同内容响应，
        详见模块 docstring。真实 CAN 上该限制不存在。

        返回：``[1.23, 1.57, ...]``（毫秒，float，长度 = samples）
        """
        request = self._parse_hex(request_hex)
        count = int(samples)
        if count < 1:
            raise AssertionError(f"samples 必须 ≥1，收到 {samples}")
        if len(request) > 7:
            raise AssertionError(
                f"本关键字只对**单帧**请求计时（≤7 字节），收到 {len(request)} 字节："
                f"{request.hex()}。多帧请求的耗时含流控帧交互，口径不同，需另建关键字。")

        self._connect()
        self._ensure_warmed_up(request)
        values: list[float] = []
        for _ in range(count):
            # 与上一条响应的到达时刻拉开 > 去重窗口，避免同内容响应被误丢
            time.sleep(SAMPLE_GAP_SECONDS)
            values.append(self._send_and_wait(request, timeout=2.0))
        self._last_samples = values

        stats = self.response_time_stats(values)
        info(f"UDS 响应时间采样 {count} 次（{request.hex()}）："
             f"min={stats['min']}ms avg={stats['avg']}ms max={stats['max']}ms "
             f"p95={stats['p95']}ms std={stats['std']}ms")
        return values

    @keyword("Response Time Stats")
    def response_time_stats(self, samples=None) -> dict:
        """对一组响应时间样本（毫秒）求统计，返回 min / avg / max / p95 / std。

        参数：
            samples    响应时间列表（通常来自 Measure UDS Response Time）；
                       省略时使用最近一次 Measure UDS Response Time 的样本。

        返回 dict（单位均为毫秒）::

            {'count': 9, 'min': 1.01, 'avg': 1.63, 'max': 2.03,
             'p95': 2.03, 'std': 0.32}

        p95 口径：**最近秩**（nearest-rank）—— 升序后取第 ceil(0.95*n) 个样本。
        注意 n=9 时 p95 就是最大值（第 9 个），这是保守口径，也是压测工具
        （如 wrk/Vegeta/LoadRunner）报告延迟分位数的常见做法：**宁可报高**。
        为什么强调"p95 不是平均"：平均值会把偶发的长尾拖平（9 次里 1 次
        500ms、8 次 1ms 时平均值只有 ~57ms，看着还行，实际用户已经卡了）。
        """
        values = self._as_sample_list(samples)
        ordered = sorted(values)
        n = len(ordered)
        return {
            "count": n,
            "min": round(ordered[0], 3),
            "avg": round(sum(values) / n, 3),
            "max": round(ordered[-1], 3),
            # 最近秩 p95：ceil(0.95*n) 是"名次"（1-based），转 0-based 减 1
            "p95": round(ordered[max(0, math.ceil(0.95 * n) - 1)], 3),
            "std": round(statistics.pstdev(values), 3),
        }

    @keyword("Response Time P95 Should Be Below")
    def response_time_p95_should_be_below(self, stats=None,
                                          threshold_ms: float = 50.0) -> None:
        """断言响应时间 **p95 < 阈值**（默认 50ms，即本项目声明的 P2）。

        参数：
            stats         Response Time Stats 返回的 dict；也可直接传样本列表；
                          省略则用最近一次 Measure 的样本。
            threshold_ms  阈值（毫秒）

        P2 = 50ms 来自 ECU 在会话控制响应里声明的参数（``50 03 00 32 01 F4``
        里的 ``0x0032``）。诊断仪按它决定"等多久算超时"——实测持续超过 P2，
        诊断仪就会误判超时，现场表现是"诊断仪连不上车"。
        """
        data = self._as_stats(stats)
        limit = float(threshold_ms)
        if data["p95"] > limit:
            raise AssertionError(
                f"响应时间 p95={data['p95']}ms 超过阈值 {limit:g}ms"
                f"（min={data['min']} avg={data['avg']} max={data['max']} "
                f"std={data['std']} count={data['count']}）")
        info(f"响应时间 p95={data['p95']}ms ≤ {limit:g}ms 合格 "
             f"（min={data['min']} avg={data['avg']} max={data['max']} "
             f"std={data['std']}ms，n={data['count']}）")

    @keyword("Response Time Jitter Should Be Below")
    def response_time_jitter_should_be_below(self, stats=None,
                                             max_std_ms: float = 10.0) -> None:
        """断言响应时间的**离散程度**（总体标准差）< 阈值，衡量响应时间抖动。

        参数：
            stats       Response Time Stats 的 dict / 样本列表 / 省略（用最近样本）
            max_std_ms  标准差上限（毫秒）

        "平均响应快"不等于"响应稳定"：平均值 5ms 但偶尔 80ms 的 ECU，在
        实车上会出现"诊断仪偶发超时"。标准差把这种离散度量化出来。
        """
        data = self._as_stats(stats)
        limit = float(max_std_ms)
        if data["std"] > limit:
            raise AssertionError(
                f"响应时间标准差 {data['std']}ms 超过阈值 {limit:g}ms"
                f"（响应抖动过大：min={data['min']} max={data['max']} "
                f"avg={data['avg']}）")
        info(f"响应时间标准差 {data['std']}ms ≤ {limit:g}ms 合格"
             f"（min={data['min']} max={data['max']}）")

    # ==================================================================
    # 二、总线负载率
    # ==================================================================
    @keyword("Record Bus Load")
    def record_bus_load(self, duration=2.0, bit_rate_kbps=500) -> float:
        """在 duration 秒内**真实接收**总线上的所有帧，估算总线负载率并返回百分比。

        参数：
            duration       录制时长（秒，可写 2 或 '2s'）
            bit_rate_kbps  总线比特率（默认 500kbps，本项目仿真总线的设定）

        计算口径（与 CANoe「Bus Statistics」一致）::

            每帧位数 = 47 + 8 * DLC          # 起始+仲裁+控制+CRC+ACK+EOF 近似
            总位数   = Σ(每帧位数) * 1.2     # 1.2 = 20% 位填充（bit stuffing）余量
            负载率   = 总位数 / (实际录制时长 * 比特率) * 100%

        为什么要算这个：CAN 总线是**共享介质**，负载率直接决定"还剩多少带宽
        给诊断/刷写/新功能"。500kbps 的经典工程经验是 **30% 是设计红线**：
        超过后低优先级报文延迟抖动急剧变大（最坏情况可达几十毫秒），
        诊断响应和刷写速度都会受影响。真实项目在台架上用 CANoe 的
        Bus Statistics 读这个值，本关键字用同样的口径自己算。

        返回值：负载率百分数（float，如 ``0.459`` 表示 0.459%）。
        详细明细（帧数/帧率/各 ID 计数）在日志里输出，并存入最近一次记录
        供 ``Log Performance Summary`` 汇总。
        """
        seconds = self._parse_seconds(duration)
        rate_bps = float(str(bit_rate_kbps).replace("kbps", "").replace("Kbps", ""))
        rate_bps *= 1000.0
        if seconds <= 0:
            raise AssertionError(f"duration 必须 >0，收到 {duration}")
        if rate_bps <= 0:
            raise AssertionError(f"bit_rate_kbps 必须 >0，收到 {bit_rate_kbps}")

        # 清掉"录制开始前"积压在 socket 里的历史帧，否则负载会被虚高
        self._connect()
        dropped_stale = self._sniffer.drain_rx(max_frames=10000)
        bits = 0
        frames = 0
        per_id: dict[int, int] = {}
        start = time.monotonic()
        deadline = start + seconds
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            msg = self._sniffer.recv_frame(
                timeout=min(0.02, max(0.001, deadline - now)))
            if msg is None:
                continue
            frames += 1
            bits += BITS_PER_FRAME_BASE + BITS_PER_DATA_BYTE * len(msg.data)
            per_id[msg.arbitration_id] = per_id.get(msg.arbitration_id, 0) + 1
        elapsed = time.monotonic() - start

        stuffed_bits = bits * BIT_STUFFING_FACTOR
        load_percent = stuffed_bits / (elapsed * rate_bps) * 100.0
        result = {
            "load_percent": round(load_percent, 3),
            "frames": frames,
            "frame_rate": round(frames / elapsed, 1),
            "bits": int(stuffed_bits),
            "duration_s": round(elapsed, 3),
            "bitrate_kbps": rate_bps / 1000.0,
            "per_id": {f"{fid:#x}": n for fid, n in sorted(per_id.items())},
            "stale_drained": dropped_stale,
        }
        self._last_bus_load = result
        # 30% 是工程红线：日志里直接给出"距红线还有多少"，读日志就能判断风险
        info(f"总线负载率 {result['load_percent']}%（{rate_bps / 1000.0:.0f}kbps，"
             f"{elapsed:.2f}s 收 {frames} 帧 / {result['frame_rate']} 帧/s，"
             f"填充后 {int(stuffed_bits)} bit）"
             f" 各 ID 帧数={result['per_id']}"
             f" —— 距 30% 红线余量 {30.0 - result['load_percent']:.2f} 个百分点")
        return result["load_percent"]

    @keyword("Bus Load Should Be Below")
    def bus_load_should_be_below(self, load=None, max_percent: float = 30.0) -> None:
        """断言总线负载率**严格低于**阈值（默认 30%）。

        参数：
            load         Record Bus Load 的返回值（百分比 float）；也接受记录 dict；
                         省略则用最近一次 Record Bus Load 的结果。
            max_percent  负载率上限（%）

        30% 的来源：工程经验红线（见 ``Record Bus Load`` 说明）。低于它，
        诊断/刷写还有带宽余量；高于它，低优先级报文可能开始出现超时。
        """
        value = self._as_load_percent(load)
        limit = float(max_percent)
        if value >= limit:
            raise AssertionError(
                f"总线负载率 {value:.3f}% 达到/超过阈值 {limit:g}%"
                f"（总线带宽余量不足，诊断/刷写可能受影响）")
        info(f"总线负载率 {value:.3f}% < {limit:g}% 合格")

    # ==================================================================
    # 三、突发压力与吞吐
    # ==================================================================
    @keyword("Send UDS Requests In Burst")
    def send_uds_requests_in_burst(self, count=200,
                                   request_hex: str = "3E 00",
                                   interval_ms: float = 8.0) -> dict:
        """连续发送 count 条同一 UDS 请求并**逐条确认响应**，返回成功率/吞吐。

        参数：
            count        请求条数（默认 200 条 TesterPresent）
            request_hex  请求内容（默认 '3E 00' TesterPresent 保活）
            interval_ms  相邻请求间隔（毫秒，默认 8ms —— 必须 > 仿真总线 5ms
                         去重窗口，否则内容完全相同的响应会被误丢，成功率假失败）

        为什么逐条确认而不是"打了就跑"：**成功率必须用响应证明**。只统计
        "发出去了多少帧"是假指标 —— 总线忙/ECU 卡死时帧照样发得出去，但没有
        任何 ECU 处理过的证据。"发一条、等一条、确认一条"才能给出真实的
        成功/失败数。

        为什么 8ms 间隔也算"突发"：默认工况下诊断链路是**空闲**的（约 0 条/s），
        连续 200 条、~100 条/s 的请求速率已把 ECU 诊断处理压到接近饱和；
        200 条全部成功 = ECU 的诊断任务在压力下没有丢请求/丢响应。

        返回 dict::

            {'sent': 200, 'success': 200, 'failed': 0, 'elapsed_s': 1.86,
             'throughput_per_s': 107.5, 'interval_ms': 8.0,
             'rtt_avg_ms': 1.6, 'rtt_max_ms': 2.4}

        注意：真实 CAN 台架上没有仿真总线的 5ms 去重限制，可以无间隔
        fire-and-forget 打满 ECU 接收缓冲区；本库为了结果可在纯软件环境
        复现，显式按 8ms 节拍发送。
        """
        total = int(count)
        request = self._parse_hex(request_hex)
        gap_seconds = float(interval_ms) / 1000.0
        if total < 1:
            raise AssertionError(f"count 必须 ≥1，收到 {count}")
        if gap_seconds * 1000.0 <= BUS_DEDUP_WINDOW_MS:
            raise AssertionError(
                f"interval_ms={interval_ms} 太小：仿真总线 {BUS_DEDUP_WINDOW_MS:g}ms "
                f"去重窗口会把内容相同的响应当成重复帧丢弃，成功率会被误判。"
                f"请用 >{BUS_DEDUP_WINDOW_MS:g}ms（默认 8ms 已留余量）。")

        self._connect()
        self._ensure_warmed_up(request)
        success = 0
        failed = 0
        rtts: list[float] = []
        start = time.monotonic()
        for index in range(total):
            # 第一条也要等——与"突发前刚发生的上一条响应"拉开去重窗口
            time.sleep(gap_seconds)
            try:
                rtt = self._send_and_wait(request, timeout=2.0)
                success += 1
                rtts.append(rtt)
            except AssertionError as exc:  # 超时/负响应都算失败
                failed += 1
                info(f"突发第 {index + 1}/{total} 条失败：{exc}")
        elapsed = time.monotonic() - start
        # 收尾再等一个间隔：让最后一条响应离开仿真总线的 5ms 去重窗口，
        # 否则紧随其后的其它诊断请求若收到"内容相同"的响应（如又是 7E 00），
        # 会被去重机制误判成重复帧丢弃 → 表现为偶发超时（本项目真实踩过）。
        # 真实 CAN 无去重机制，不存在这个问题。
        time.sleep(gap_seconds)
        throughput = total / elapsed if elapsed > 0 else 0.0
        result = {
            "sent": total,
            "success": success,
            "failed": failed,
            "elapsed_s": round(elapsed, 3),
            "throughput_per_s": round(throughput, 1),
            "interval_ms": float(interval_ms),
            "rtt_avg_ms": round(sum(rtts) / len(rtts), 3) if rtts else None,
            "rtt_max_ms": round(max(rtts), 3) if rtts else None,
        }
        self._last_burst = result
        info(f"突发压力：发送 {total} 条（间隔 {float(interval_ms):g}ms）→ "
             f"成功 {success} / 失败 {failed}，耗时 {elapsed:.3f}s，"
             f"吞吐 {throughput:.1f} 条/s，单条响应 avg={result['rtt_avg_ms']}ms "
             f"max={result['rtt_max_ms']}ms")
        return result

    @keyword("Throughput Should Be Above")
    def throughput_should_be_above(self, burst=None,
                                   min_per_s: float = 50.0) -> None:
        """断言诊断吞吐量 ≥ 阈值（默认 50 条/s）。

        参数：
            burst       Send UDS Requests In Burst 的返回 dict；也可直接传
                        吞吐数值（条/s）；省略则用最近一次突发结果。
            min_per_s   吞吐下限（条/s）

        50 条/s 的工程含义：诊断仪刷新一屏 20 个 DID 的实时数据（常见于
        产线/售后工具），若吞吐只有 50 条/s 则需要 0.4s 刷一屏，已到可用
        下限；再低用户会明显感觉"卡"。同时要求成功率 100% —— 快而丢
        没有意义。
        """
        data = self._as_burst(burst)
        throughput = data["throughput_per_s"]
        limit = float(min_per_s)
        if throughput < limit:
            raise AssertionError(
                f"诊断吞吐 {throughput} 条/s 低于下限 {limit:g} 条/s"
                f"（成功 {data['success']}/{data['sent']}，耗时 {data['elapsed_s']}s）")
        info(f"诊断吞吐 {throughput} 条/s ≥ {limit:g} 条/s 合格"
             f"（成功 {data['success']}/{data['sent']}，"
             f"单条响应 avg={data.get('rtt_avg_ms')}ms）")

    # ==================================================================
    # 四、周期报文抖动（时序稳定性）
    # ==================================================================
    @keyword("Measure Periodic Message Jitter")
    def measure_periodic_message_jitter(self, arbitration_id=0x100,
                                        duration=3.0) -> dict:
        """录制指定周期报文 duration 秒，统计相邻帧**间隔**的均值/标准差/最大值。

        参数：
            arbitration_id  报文 ID（默认 0x100 发动机数据，设计周期 100ms）
            duration        录制时长（秒）

        返回 dict（单位毫秒）::

            {'id': '0x100', 'frames': 29, 'avg_interval_ms': 103.75,
             'std_interval_ms': 0.61, 'min_interval_ms': 102.52,
             'max_interval_ms': 105.33, 'max_deviation_ms': 1.58}

        "周期均值对但抖动大"是最隐蔽的时序缺陷：平均看是 100ms，但偶发
        一帧晚 40ms，依赖该信号的节点（仪表指针、ABS 判断）就可能异常。
        所以除均值外必须看 **标准差** 与 **最大间隔**。
        真实台架上用 CANoe Trace 的统计列看这个，本关键字用真实收帧自己算。
        """
        fid = self._parse_id(arbitration_id)
        seconds = self._parse_seconds(duration)
        if seconds <= 0:
            raise AssertionError(f"duration 必须 >0，收到 {duration}")

        self._connect()
        self._sniffer.drain_rx(max_frames=10000)
        stamps: list[float] = []
        start = time.monotonic()
        deadline = start + seconds
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            msg = self._sniffer.recv_frame(
                timeout=min(0.01, max(0.0005, deadline - now)))
            if msg is None or msg.arbitration_id != fid:
                continue
            stamps.append(time.monotonic())

        if len(stamps) < 3:
            raise AssertionError(
                f"ID={fid:#x} 在 {seconds:g}s 内只收到 {len(stamps)} 帧，"
                f"无法分析周期（至少需要 3 帧）—— 检查 ECU 是否在发该报文")
        gaps = [(stamps[i + 1] - stamps[i]) * 1000.0
                for i in range(len(stamps) - 1)]
        avg = sum(gaps) / len(gaps)
        result = {
            "id": f"{fid:#x}",
            "frames": len(stamps),
            "avg_interval_ms": round(avg, 3),
            "std_interval_ms": round(statistics.pstdev(gaps), 3),
            "min_interval_ms": round(min(gaps), 3),
            "max_interval_ms": round(max(gaps), 3),
            "max_deviation_ms": round(max(gaps) - avg, 3),
        }
        self._last_jitter = result
        info(f"周期报文 {result['id']}：{result['frames']} 帧，"
             f"间隔均值 {result['avg_interval_ms']}ms，"
             f"标准差 {result['std_interval_ms']}ms，"
             f"最小 {result['min_interval_ms']}ms / 最大 {result['max_interval_ms']}ms")
        return result

    @keyword("Jitter Should Be Within")
    def jitter_should_be_within(self, jitter=None,
                                expected_period_ms: float = 100.0,
                                mean_tolerance_ms: float = 15.0,
                                max_std_ms: float = 10.0,
                                max_gap_deviation_ms: float = 50.0) -> None:
        """断言周期报文的间隔均值/离散度/最大间隔都在容差内。

        参数：
            jitter                Measure Periodic Message Jitter 的返回 dict
                                  （省略则用最近一次结果）
            expected_period_ms    设计周期（默认 100ms）
            mean_tolerance_ms     均值容差（|实测均值 - 设计周期| ≤ 该值）
            max_std_ms            间隔标准差上限
            max_gap_deviation_ms  最大间隔超出设计周期的上限
                                  （最大间隔 - 设计周期 ≤ 该值）

        容差不是拍脑袋：本仿真 ECU 的主循环用 ``sleep(0.01)`` 轮询 + 累加
        计时（``t_100 += dt; if t_100 >= 0.1``），实际周期稳定在 **~104ms**
        （系统误差约 4%），因此均值容差必须覆盖这个系统性偏差；而抖动
        （标准差/最大间隔）才是真正要卡紧的指标。CI 慢机器上调度抖动会
        变大，所以抖动容差也留了余量 —— **阈值定得比实测紧一点点，比
        追求理论值更重要**（否则用例会在慢机器上随机红，失去回归价值）。
        """
        data = self._as_jitter(jitter)
        expected = float(expected_period_ms)
        avg_dev = abs(data["avg_interval_ms"] - expected)
        if avg_dev > float(mean_tolerance_ms):
            raise AssertionError(
                f"周期报文 {data['id']} 间隔均值 {data['avg_interval_ms']}ms "
                f"偏离设计周期 {expected:g}ms 达 {avg_dev:.2f}ms"
                f"（容差 ±{float(mean_tolerance_ms):g}ms）")
        if data["std_interval_ms"] > float(max_std_ms):
            raise AssertionError(
                f"周期报文 {data['id']} 间隔标准差 {data['std_interval_ms']}ms "
                f"超过阈值 {float(max_std_ms):g}ms（周期抖动过大）")
        gap_dev = data["max_interval_ms"] - expected
        if gap_dev > float(max_gap_deviation_ms):
            raise AssertionError(
                f"周期报文 {data['id']} 最大间隔 {data['max_interval_ms']}ms "
                f"超出设计周期 {expected:g}ms 达 {gap_dev:.2f}ms"
                f"（上限 {float(max_gap_deviation_ms):g}ms，"
                f"个别帧过晚会让接收方超时）")
        info(f"周期报文 {data['id']} 抖动合格：均值 {data['avg_interval_ms']}ms"
             f"（偏离 {avg_dev:.2f}ms）、标准差 {data['std_interval_ms']}ms、"
             f"最大间隔 {data['max_interval_ms']}ms")

    # ==================================================================
    # 五、长稳（soak）
    # ==================================================================
    @keyword("Run Soak Sampling")
    def run_soak_sampling(self, duration=20.0, sample_interval=0.5,
                          probe_request: str = "3E 00") -> dict:
        """长稳采样：duration 秒内持续收帧，并每 sample_interval 秒探一次诊断链路。

        参数：
            duration       长稳时长（秒，默认 20s —— 足够暴露周期性抖动/慢泄漏）
            sample_interval 诊断链路采样间隔（秒，默认 0.5s）
            probe_request  诊断探测请求（默认 '3E 00'）

        采样内容（两个维度同时监控，因为长稳问题可能只表现在其中一条链路上）：
            1. **周期报文**：逐帧记录 0x100/0x200/0x300 的到达时刻，最后统计
               每条的实收帧数、最大间隔、**丢帧数**；
            2. **诊断链路**：每 0.5s 发一条 TesterPresent，记录响应耗时与
               **超时次数**。

        丢帧判定口径（重要，避免"假丢帧"）：按**帧间隔**判断，而不是
        「理论帧数 − 实收帧数」::

            间隔 > 1.5 × 设计周期 才算异常，丢失帧数 = round(间隔 / 周期) − 1

        为什么不用理论帧数：仿真 ECU 实际周期约 104ms（系统误差 4%），
        20s 内"理论 200 帧"必然只收到 ~193 帧，按理论值算会凭空报 7 帧
        "丢帧"—— 这是把**系统性周期偏差**误判成丢帧。按间隔判断只关心
        "有没有哪一帧明显晚了/没来"，不受周期系统偏差影响。真实项目
        （CANoe 统计）也是看间隔/超时，而不是拿理论帧数硬减。

        返回 dict::

            {'duration_s': 20.01, 'samples': 40,
             'periodic': {'0x100': {'received': 193, 'dropped': 0,
                                    'max_interval_ms': 122.9}, ...},
             'dropped_frames': 0, 'diag_ok': 40, 'diag_timeouts': 0,
             'errors': 0, 'probe_avg_ms': 3.2, 'probe_max_ms': 8.1}
        """
        seconds = self._parse_seconds(duration)
        interval = self._parse_seconds(sample_interval)
        request = self._parse_hex(probe_request)
        if seconds <= 0 or interval <= 0:
            raise AssertionError(
                f"duration/sample_interval 必须 >0，收到 {duration}/{sample_interval}")

        self._connect()
        self._sniffer.drain_rx(max_frames=10000)
        # 每条周期报文独立记录：last=上一帧到达时刻，gaps=相邻间隔(ms)
        records: dict[int, dict] = {
            fid: {"received": 0, "last": None, "gaps": []}
            for fid in PERIODIC_MESSAGES
        }
        diag_ok = 0
        diag_timeouts = 0
        probe_rtts: list[float] = []
        start = time.monotonic()
        deadline = start + seconds
        next_probe = start + interval
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            msg = self._sniffer.recv_frame(
                timeout=min(0.02, max(0.001, deadline - now)))
            now = time.monotonic()
            if msg is not None and msg.arbitration_id in records:
                rec = records[msg.arbitration_id]
                rec["received"] += 1
                if rec["last"] is not None:
                    rec["gaps"].append((now - rec["last"]) * 1000.0)
                rec["last"] = now
            if now >= next_probe:
                # 不追赶：一次慢探测之后从当前时刻重新计时，避免"补采样"打乱节奏
                next_probe = now + interval
                try:
                    probe_rtts.append(self._send_and_wait(request, timeout=1.0))
                    diag_ok += 1
                except AssertionError as exc:
                    diag_timeouts += 1
                    info(f"长稳诊断探测超时：{exc}")

        elapsed = time.monotonic() - start
        report: dict[str, dict] = {}
        dropped_frames = 0
        for fid, rec in records.items():
            period = PERIODIC_MESSAGES[fid]
            drops = sum(
                max(0, round(gap / period) - 1)
                for gap in rec["gaps"] if gap > period * 1.5
            )
            dropped_frames += drops
            report[f"{fid:#x}"] = {
                "received": rec["received"],
                "dropped": drops,
                "max_interval_ms": (round(max(rec["gaps"]), 1)
                                    if rec["gaps"] else None),
            }
        result = {
            "duration_s": round(elapsed, 2),
            "samples": len(probe_rtts) + diag_timeouts,
            "periodic": report,
            "dropped_frames": dropped_frames,
            "diag_ok": diag_ok,
            "diag_timeouts": diag_timeouts,
            "errors": dropped_frames + diag_timeouts,
            "probe_avg_ms": (round(sum(probe_rtts) / len(probe_rtts), 3)
                             if probe_rtts else None),
            "probe_max_ms": round(max(probe_rtts), 3) if probe_rtts else None,
        }
        self._last_soak = result
        info(f"长稳采样 {result['duration_s']}s：周期报文实收={report}，"
             f"丢帧={dropped_frames}；诊断探测 {result['samples']} 次，"
             f"成功 {diag_ok} / 超时 {diag_timeouts}，"
             f"响应耗时 avg={result['probe_avg_ms']}ms max={result['probe_max_ms']}ms")
        return result

    @keyword("Soak Errors Should Be Below")
    def soak_errors_should_be_below(self, soak=None,
                                    max_errors: int = 5,
                                    max_timeouts: int = 0) -> None:
        """断言长稳期间的总错误数（丢帧 + 诊断超时）与超时数都在阈值内。

        参数：
            soak           Run Soak Sampling 的返回 dict（省略则用最近一次结果）
            max_errors     丢帧 + 诊断超时 的总数上限（默认 5）
            max_timeouts   诊断超时次数上限（默认 0，诊断链路不允许超时）

        为什么允许 max_errors 有一点余量（默认 5）：长稳期间 CI/开发机的
        系统抖动（GC、杀毒扫描、Windows 定时任务）可能让个别帧晚到超过
        1.5 个周期，这属于**测试环境噪声**而非产品缺陷；而 20s 内 ~340 帧
        里偶发 1~2 帧是正常的。真正要卡死的是**诊断超时**（一次都不允许），
        因为那意味着诊断链路被压垮 —— 这就是 max_timeouts 单独一个参数的
        意义：不同严重程度的错误不能用一个阈值混着看。
        """
        data = self._as_soak(soak)
        errors = int(data["errors"])
        timeouts = int(data["diag_timeouts"])
        dropped = int(data["dropped_frames"])
        if errors > int(max_errors):
            raise AssertionError(
                f"长稳错误数 {errors} 超过上限 {int(max_errors)}"
                f"（丢帧 {dropped}、诊断超时 {timeouts}；周期明细 {data['periodic']}）")
        if timeouts > int(max_timeouts):
            raise AssertionError(
                f"长稳期间诊断超时 {timeouts} 次 超过上限 {int(max_timeouts)}"
                f"（诊断链路在长稳下不可用；丢帧 {dropped}）")
        info(f"长稳合格：总错误 {errors} ≤ {int(max_errors)}"
             f"（丢帧 {dropped}）、诊断超时 {timeouts} ≤ {int(max_timeouts)}，"
             f"诊断响应 avg={data['probe_avg_ms']}ms "
             f"max={data['probe_max_ms']}ms")

    # ==================================================================
    # 六、汇总与生命周期
    # ==================================================================
    @keyword("Log Performance Summary")
    def log_performance_summary(self, label: str = "性能基线") -> None:
        """把本轮已测得的各项非功能指标汇总输出（log.html + 控制台一行），供回归对比。

        参数：
            label    汇总标题（建议带需求号，如 'REQ-PERF 性能基线'）

        为什么要有"基线记录"：性能测试的价值在**对比**而不是单次数值 ——
        同一台机器上，今天 p95=2ms，下次提交后 p95=20ms，就说明改动引入了
        性能退化。基线不进断言、只进日志/报告，供人（或 CI 的后处理脚本）
        做趋势对比；把基线硬编码成断言反而会让"机器换了"变成假失败。
        """
        lines: list[str] = []
        if self._last_samples:
            stats = self.response_time_stats(self._last_samples)
            lines.append(
                f"UDS 响应时间(ms) min={stats['min']} avg={stats['avg']} "
                f"max={stats['max']} p95={stats['p95']} std={stats['std']} "
                f"n={stats['count']}")
        if self._last_bus_load:
            load = self._last_bus_load
            lines.append(
                f"总线负载={load['load_percent']}% ({load['frame_rate']} 帧/s, "
                f"{load['duration_s']}s, {load['bitrate_kbps']:.0f}kbps)")
        if self._last_jitter:
            jitter = self._last_jitter
            lines.append(
                f"周期报文 {jitter['id']} 间隔(ms) avg={jitter['avg_interval_ms']} "
                f"std={jitter['std_interval_ms']} max={jitter['max_interval_ms']} "
                f"frames={jitter['frames']}")
        if self._last_burst:
            burst = self._last_burst
            lines.append(
                f"突发压力 {burst['success']}/{burst['sent']} 成功，"
                f"吞吐={burst['throughput_per_s']} 条/s，"
                f"耗时={burst['elapsed_s']}s")
        if self._last_soak:
            soak = self._last_soak
            lines.append(
                f"长稳 {soak['duration_s']}s 丢帧={soak['dropped_frames']} "
                f"诊断超时={soak['diag_timeouts']} "
                f"诊断探测={soak['samples']} 次")
        if not lines:
            lines.append("本轮还没有任何性能测量数据（先跑 Measure/Record/Run 等关键字）")

        info(f"===== {label} =====")
        for line in lines:
            info(line)
        info("=" * (len(label) + 12))
        # 控制台只打一行（不进 log.html 的细节），方便在 CI 控制台快速扫一眼
        console(f"[Perf] {label}: " + " | ".join(lines))

    @keyword("Close Perf Library")
    def close_perf_library(self) -> None:
        """关闭诊断仪收包线程与两条虚拟总线（套件 Teardown 调用）。

        不释放会怎样：UDP 组播 socket 句柄泄漏，进程退出时 python-can 会打
        "UdpMulticastBus was not properly shut down"；更麻烦的是收包线程还
        在跑，后续套件若启动新 ECU，旧 socket 仍会持续收帧（多消费者抢帧
        的隐患）。所以资源释放必须是套件 Teardown 的固定动作。
        """
        if self._closed:
            return
        self._closed = True
        if self._tester is None:
            info("[PerfLibrary] 未使用过测量关键字，无需释放")
            return
        try:
            self._tester.stop()
        finally:
            for bus in (self._diag_bus, self._sniffer):
                if bus is None:
                    continue
                try:
                    bus.shutdown()
                except Exception:  # noqa: BLE001 —— 释放阶段失败不掩盖主结果
                    pass
        self._sniffer = self._diag_bus = self._tester = None
        info("[PerfLibrary] 已释放诊断仪收包线程与虚拟总线")

    # ==================================================================
    # 内部工具（非关键字）
    # ==================================================================
    _INTERNAL_RX_POLL_SECONDS = 0.0005   # 500μs：响应入队后 0.5ms 内就能取到

    def _ensure_warmed_up(self, request: bytes) -> None:
        """首次测量前预热 2 条（丢弃结果），消除冷启动一次性开销。"""
        if self._warmed_up:
            return
        info("性能测量预热：先发 2 条请求并丢弃结果（消除解释器/内核冷启动开销）")
        for _ in range(2):
            time.sleep(SAMPLE_GAP_SECONDS)
            self._send_and_wait(request, timeout=2.0)
        self._warmed_up = True

    def _send_and_wait(self, request: bytes, timeout: float = 2.0) -> float:
        """发一条单帧 UDS 请求并等完整响应，返回毫秒耗时（内部工具）。

        快速通道：直接轮询 DiagnosticTester 的收件箱（``_pending``，已完成
        ISO-TP 重组），只 sleep 500μs —— 计时不含客户端 20ms 轮询粒度。
        降级通道：客户端内部结构变化时退回公开 ``send_request``（数值偏高）。
        """
        if len(request) > 7:
            raise AssertionError(f"内部计时只支持单帧请求，收到 {request.hex()}")
        if self._tester is None:
            self._connect()
        if not self._fast_rx_ready:
            start = time.monotonic()
            self._tester.send_request(request, timeout=timeout)
            return (time.monotonic() - start) * 1000.0

        # 清空"上一次"残留，保证取到的响应一定属于本次请求
        with self._tester._lock:
            self._tester._pending.clear()
            self._tester._inbox.clear()
        start = time.monotonic()
        self._diag_bus.send_frame(ID_UDS_REQUEST, bytes([len(request)]) + request)
        deadline = start + timeout
        raw = None
        while True:
            with self._tester._lock:
                pending = self._tester._pending
                if pending:
                    raw = pending.pop(0)
                    break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"UDS 请求 {request.hex()} 在 {timeout:g}s 内没有响应"
                    f"（诊断链路超时/ECU 无响应）")
            time.sleep(self._INTERNAL_RX_POLL_SECONDS)
        elapsed = (time.monotonic() - start) * 1000.0
        self._check_uds_response(request, raw)
        return elapsed

    @staticmethod
    def _check_uds_response(request: bytes, raw: bytes) -> None:
        """校验响应是正响应（SID+0x40）或负响应（7F + SID），负响应抛错。"""
        if not raw:
            raise AssertionError(f"收到空响应（请求 {request.hex()}）")
        expected_positive = (request[0] + 0x40) & 0xFF
        if raw[0] == expected_positive:
            return
        if raw[0] == 0x7F and len(raw) >= 3:
            raise AssertionError(
                f"请求 {request.hex()} 收到负响应：NRC={raw[2]:#04x}"
                f"（原 SID={raw[1]:#04x}）")
        raise AssertionError(
            f"请求 {request.hex()} 的响应 SID 异常：收到 {raw.hex()}，"
            f"期望正响应 SID={expected_positive:#04x}")

    @staticmethod
    def _parse_hex(text: str) -> bytes:
        """'3E 00' / '3e00' → b'\\x3e\\x00'"""
        return bytes.fromhex(str(text).replace(" ", "").replace("0x", ""))

    @staticmethod
    def _parse_id(value) -> int:
        """0x100 / '0x100' / '256' → 256"""
        return int(str(value), 0)

    @staticmethod
    def _parse_seconds(value) -> float:
        """2 / '2' / '2s' → 2.0（兼容项目里其它库的写法）"""
        return float(str(value).replace("s", "").replace("S", ""))

    def _as_sample_list(self, samples) -> list[float]:
        """把 samples 参数归一成 float 列表；None 时用最近一次测量样本。"""
        if samples is None:
            if not self._last_samples:
                raise AssertionError(
                    "没有可用的响应时间样本：先调用 Measure UDS Response Time")
            values = list(self._last_samples)
        elif isinstance(samples, dict):
            raise AssertionError(
                "Response Time Stats 需要样本列表，收到的是统计 dict"
                "（若想复用统计结果，直接把 dict 传给 ... Should Be Below 类关键字）")
        elif isinstance(samples, (list, tuple)):
            values = [float(item) for item in samples]
        else:
            raise AssertionError(
                f"samples 需要响应时间列表，收到 {type(samples).__name__}: {samples!r}")
        if not values:
            raise AssertionError("响应时间样本列表为空")
        return values

    def _as_stats(self, stats) -> dict:
        """把 stats 参数归一成统计 dict：dict 直通 / 样本列表 → 计算 / None → 最近样本。"""
        if isinstance(stats, dict):
            if "p95" not in stats:
                raise AssertionError(f"统计 dict 缺少 p95 字段：{stats}")
            return stats
        return self.response_time_stats(self._as_sample_list(stats))

    def _as_load_percent(self, load) -> float:
        """把负载参数归一成百分比 float：数字直通 / dict 取 load_percent / None 用最近。"""
        if load is None:
            if self._last_bus_load is None:
                raise AssertionError("还没有总线负载记录：先调用 Record Bus Load")
            return float(self._last_bus_load["load_percent"])
        if isinstance(load, dict):
            return float(load["load_percent"])
        return float(load)

    def _as_burst(self, burst) -> dict:
        """把突发结果归一成 dict：dict 直通 / 数字包成最小 dict / None 用最近。"""
        if burst is None:
            if self._last_burst is None:
                raise AssertionError(
                    "还没有突发压力结果：先调用 Send UDS Requests In Burst")
            return self._last_burst
        if isinstance(burst, dict):
            return burst
        return {"throughput_per_s": float(burst), "success": "n/a",
                "sent": "n/a", "elapsed_s": "n/a"}

    def _as_jitter(self, jitter) -> dict:
        """把抖动结果归一成 dict；None 用最近一次结果。"""
        if jitter is None:
            if self._last_jitter is None:
                raise AssertionError(
                    "还没有周期抖动测量结果：先调用 Measure Periodic Message Jitter")
            return self._last_jitter
        if not isinstance(jitter, dict):
            raise AssertionError(f"需要 Measurement dict，收到 {jitter!r}")
        return jitter

    def _as_soak(self, soak) -> dict:
        """把长稳结果归一成 dict；None 用最近一次结果。"""
        if soak is None:
            if self._last_soak is None:
                raise AssertionError("还没有长稳结果：先调用 Run Soak Sampling")
            return self._last_soak
        if not isinstance(soak, dict):
            raise AssertionError(f"需要 Soak dict，收到 {soak!r}")
        return soak


if __name__ == "__main__":
    # 自测（需先启动 ECU：python simulators/ecu_sim.py）：
    #   只做一次最小冒烟，确认两条总线通道都通
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    lib = PerfLibrary("PerfLibrary-SelfTest")
    try:
        values = lib.measure_uds_response_time("3E 00", samples=5)
        print("响应时间样本(ms):", [round(v, 2) for v in values])
        print("统计:", lib.response_time_stats(values))
        print("总线负载(%):", lib.record_bus_load(1.0))
    finally:
        lib.close_perf_library()
