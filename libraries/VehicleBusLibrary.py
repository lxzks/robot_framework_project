"""
VehicleBusLibrary.py — Robot Framework 车载总线关键字库
=========================================================

把 python-can + cantools 的能力包装成 Robot Framework 关键字，
测试用例里就能像说话一样操作 CAN 总线：

    Send CAN Frame    0x100    881300805A330000
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed

RF 库技术要点：
    1. 用 @library 装饰器声明库（scope='GLOBAL' 全局单例）
    2. 每个 public 方法就是一个关键字，方法名自动转成"首字母大写空格分隔"形式
       （read_signal_from_bus → Read Signal From Bus）
    3. @keyword("自定义名") 可以显式指定关键字名
    4. 库实例持有 VirtualCanBus，suite 级复用；__del__/close 时释放
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

import cantools                                    # noqa: E402
from bus import VirtualCanBus                      # noqa: E402

DBC_PATH = Path(__file__).parent.parent / "simulators" / "dbc" / "vehicle.dbc"


class VehicleBusLibrary:
    """CAN 总线操作关键字集合"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, dbc_path: str = str(DBC_PATH), node_name: str = "RF-BUS") -> None:
        self._db = cantools.database.load_file(dbc_path)
        # echo_own=True（默认）：回环测试需要"发出去的帧自己能读回来"
        # （模拟 CAN 控制器自发自收；见 bus.py 的说明）
        self._bus = VirtualCanBus(node_name, echo_own=True)
        # 录制帧：(timestamp, arbitration_id, data_bytes, is_extended)
        self._recorder: list[tuple[float, int, bytes, bool]] = []
        self._recording = False
        console(f"[VehicleBusLibrary] 已连接虚拟总线，加载 DBC: {Path(dbc_path).name}")

    # ------------------------------------------------------------------ 生命周期
    @keyword("Close CAN Bus")
    def close_can_bus(self) -> None:
        """断开虚拟 CAN 总线连接（suite 结束时调用）"""
        self._bus.shutdown()

    # ------------------------------------------------------------------ 发送
    @keyword("Send CAN Frame")
    def send_can_frame(self, arbitration_id, data_hex: str) -> None:
        """发送一帧原始 CAN 报文。

        参数：
            arbitration_id    CAN ID，支持 '0x100' 或 '256'
            data_hex          数据场十六进制字符串，如 '881300805A330000'
        """
        fid = int(str(arbitration_id), 0)   # int(x, 0) 自动识别 0x 前缀
        data = bytes.fromhex(data_hex.replace(" ", ""))
        self._bus.send_frame(fid, data)
        info(f"发送 CAN 帧: ID={fid:#x} data={data.hex()}")

    @keyword("Send Signal Values")
    def send_signal_values(self, message_name: str, **signals) -> None:
        """按 DBC 信号名发送报文（自动编码，不用手算字节）。

        例（只给部分信号也行，未给的自动补默认值）：
            Send Signal Values    EngineData    VehicleSpeed=120
            Send Signal Values    EngineData    VehicleSpeed=120    EngineSpeed=3000

        两个关键处理：
        1. **补齐缺失信号**：cantools 要求报文内全部信号都提供，否则
           KeyError。自动生成的边界值用例每个测试点只关心一个信号，
           所以未提供的补"安全默认值"（物理 0；若 0 超范围则取最小值）。
        2. **越界值降级**：物理值超出 DBC 范围时改用 strict=False 编码
           —— 越界测试要验证的正是"ECU 收到超范围值时的行为"。
        """
        msg_def = self._db.get_message_by_name(message_name)
        values = {k: float(v) for k, v in signals.items()}
        for s in msg_def.signals:
            if s.name not in values:
                values[s.name] = (0.0 if s.minimum <= 0 <= s.maximum
                                  else float(s.minimum))
        try:
            data = self._db.encode_message(message_name, values)
        except Exception:
            data = self._db.encode_message(message_name, values, strict=False)
            info(f"⚠ {message_name} 值超出 DBC 范围，已按越界值发送: {signals}")
        self._bus.send_frame(msg_def.frame_id, data)
        # 清掉接收缓冲里本进程历史帧的回环副本：否则后续读回 echo 缓存为空时
        # 会读到旧副本，表现为"读回值错乱"（真实踩过的坑，见 bus.drain_rx）
        self._bus.drain_rx()
        info(f"发送 {message_name}({msg_def.frame_id:#x}): {signals} → {data.hex()}")

    @keyword("Read Frame And Verify Signals")
    def read_frame_and_verify_signals(self, message_name: str, **expected) -> dict:
        """读**一帧**报文并一次校验多个信号，返回解码结果。

        为什么需要它：组合测试里若"发 1 帧、按信号读 N 次"，第 2 次开始
        读的就是总线上别的帧（读串了）。语义上正确做法是——一次编码发送
        一帧，读回同一帧后校验其中的多个信号。
        """
        msg_def = self._db.get_message_by_name(message_name)
        msg = self._bus.recv_until(
            lambda m: m.arbitration_id == msg_def.frame_id, timeout=3.0)
        decoded = self._db.decode_message(
            message_name, msg.data, decode_choices=False)
        failures = []
        for name, exp in expected.items():
            actual = float(decoded[name])
            exp_f = float(exp)
            tolerance = max(abs(exp_f) * 0.02, 0.5)
            if abs(actual - exp_f) > tolerance:
                failures.append(f"{name} 期望 {exp_f:g} 实际 {actual:g}")
        if failures:
            raise AssertionError(f"{message_name} 组合校验失败: "
                                 + "; ".join(failures))
        info(f"{message_name} 组合校验通过: {expected}")
        return {k: float(v) for k, v in decoded.items()}

    # ------------------------------------------------------------------ 接收
    @keyword("Wait For CAN Frame")
    def wait_for_can_frame(self, arbitration_id, timeout: float = 3.0) -> str:
        """等待指定 ID 的报文到达，返回数据场十六进制字符串。"""
        fid = int(str(arbitration_id), 0)
        msg = self._bus.recv_until(
            lambda m: m.arbitration_id == fid, timeout=float(timeout))
        info(f"收到 CAN 帧: ID={fid:#x} data={msg.data.hex()}")
        return msg.data.hex()

    @keyword("Wait Until Signal In Range")
    def wait_until_signal_in_range(self, message_name: str, signal_name: str,
                                   low, high, timeout: float = 15.0) -> float:
        """轮询等待某信号值进入 [low, high] 区间，返回最终值。

        注意：不能只用 RF 的 Wait Until Keyword Succeeds 包 Read Signal ——
        它只检测"关键字是否抛异常"，读到一个超范围的值也算成功。
        带值域判断的等待必须做成一个独立关键字。
        """
        low_f, high_f = float(low), float(high)
        deadline = time.monotonic() + float(timeout)
        last: float | None = None
        while time.monotonic() < deadline:
            last = self.read_signal_from_bus(message_name, signal_name, timeout=2)
            if low_f <= last <= high_f:
                info(f"{message_name}.{signal_name}={last} 已进入 "
                     f"[{low_f}, {high_f}] ✓")
                return last
            time.sleep(0.2)
        raise AssertionError(
            f"{timeout}s 内 {message_name}.{signal_name}={last} "
            f"未进入 [{low_f}, {high_f}]")

    @keyword("Out Of Range Value Should Be Sendable")
    def out_of_range_value_should_be_sendable(self, message_name: str,
                                             signal_name: str,
                                             desc: str = "") -> None:
        """极值测试：把信号的**位域填满**（全 1 位模式）并发送。

        为什么手工构造字节而不用 cantools 编码：DBC 的位宽是固定的，
        超量程值（如 16 位信号要 16383.75 物理值）在编码阶段就会被
        范围校验拒绝 —— 物理上根本表达不出来，"发物理越界值"是伪命题。
        协议层能做的极限测试是：**直接构造数据场**（全 1 位 = 该信号可
        承载的最大原始值），验证 ECU/接收端收到"最大可能数据"时的行为。
        这也正是真实台架的做法：极限用例用原始字节构造，绕过 DBC 缩放。

        本实现按 Intel（小端）位序写入（本项目 DBC 全为 `@1+`）；
        Motorola 大端未实现（需要时按 DBC 规范的 sawtooth 位序扩展）。
        """
        msg_def = self._db.get_message_by_name(message_name)
        sig = msg_def.get_signal_by_name(signal_name)
        if sig.byte_order != "little_endian":
            raise AssertionError(
                f"{signal_name} 是 Motorola 大端信号，本关键字暂只支持 Intel")
        data = bytearray(msg_def.length)          # 全 0
        raw_max = (1 << sig.length) - 1
        # Intel 位序：bit i 位于 abs_bit = start + i（bit0 = 该字节最低位）
        for i in range(sig.length):
            if (raw_max >> i) & 1:
                abs_bit = sig.start + i
                data[abs_bit // 8] |= (1 << (abs_bit % 8))
        self._bus.send_frame(msg_def.frame_id, bytes(data))
        self._bus.drain_rx()
        got = self._bus.recv_until(
            lambda m: m.arbitration_id == msg_def.frame_id, timeout=3.0)
        if got.data != bytes(data):
            raise AssertionError(
                f"总线上读回的帧与发送不一致: {got.data.hex()} != {bytes(data).hex()}")
        info(f"极值 {signal_name} 全 1 位（{sig.length} bit，raw {raw_max}）"
             f"{desc} 已发送（{bytes(data).hex()}，帧已在总线上确认）")

    @keyword("Read Signal From Bus")
    def read_signal_from_bus(self, message_name: str, signal_name: str,
                             timeout: float = 3.0) -> float:
        """等待一帧指定报文并解出信号值（物理值，带单位和缩放）。

        例：${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
        """
        msg_def = self._db.get_message_by_name(message_name)
        msg = self._bus.recv_until(
            lambda m: m.arbitration_id == msg_def.frame_id, timeout=float(timeout))
        decoded = self._db.decode_message(
            message_name, msg.data, decode_choices=False)
        value = float(decoded[signal_name])
        info(f"{message_name}.{signal_name} = {value}")
        return value

    # ------------------------------------------------------------------ 录制
    @keyword("Start Bus Recorder")
    def start_bus_recorder(self) -> None:
        """开始录制总线上所有报文（用于事后分析，类似 CANoe 的 Trace）。

        **先清空接收缓冲**：socket 里往往积压着"开始录制之前"的帧
        （RF 不调用 recv 时帧会一直堆在缓冲里）。不清掉的话，录制结果里
        会混入历史帧，导致"IGN OFF 后还在发报文""睡眠后还有 13 帧"这类
        假失败 —— 本项目真实踩过。
        """
        self._recorder.clear()
        dropped = self._bus.drain_rx()
        self._recording = True
        if dropped:
            info(f"总线录制已开始（已清掉开始前积压的 {dropped} 帧）")
        else:
            info("总线录制已开始")

    @keyword("Stop Bus Recorder")
    def stop_bus_recorder(self) -> None:
        """停止录制"""
        self._recording = False
        info(f"总线录制已停止，共 {len(self._recorder)} 帧")

    @keyword("Get Recorded Frames")
    def get_recorded_frames(self) -> list:
        """返回录制的所有帧 [(timestamp, id_hex, data_hex), ...]（兼容旧断言）"""
        return [(ts, hex(fid), data.hex()) for ts, fid, data, _ext
                in self._recorder]

    @keyword("Recorded Frame Count")
    def recorded_frame_count(self) -> int:
        """录制到的帧数"""
        return len(self._recorder)

    @keyword("Export Bus Trace")
    def export_bus_trace(self, path: str) -> int:
        """把录制的帧导出成 Trace 文件（CANoe 兼容）。按扩展名选格式：
        `.asc` → Vector ASCII（可读文本，CANoe/CANalyzer 可直接打开）
        `.blf` → Vector 二进制（文件小，实车路试标准格式）

        典型用法（套件 teardown 里归档，失败时供分析）：
            Export Bus Trace    ${ROOT}/results/bus_trace.asc
        """
        from trace_tools import export_frames
        count = export_frames(list(self._recorder), path)
        info(f"已导出 {count} 帧 → {path}")
        return count

    @keyword("Trace Files Should Be Valid")
    def trace_files_should_be_valid(self, asc_path: str, blf_path: str,
                                    expected_count) -> None:
        """回读验证两类 Trace 文件（"测试的测试"）：

        - ASC 用自研解析器逐行校验（帧数 + 每行格式）
        - BLF 用 python-can 的 BLFReader 回读（工业库能读通 = 格式正确，
          这正是 CANoe 会做的事）
        并交叉核对：两种格式的 ID 序列应完全一致。
        """
        from trace_tools import verify_asc, verify_blf
        asc_frames = verify_asc(asc_path)
        blf_count = verify_blf(blf_path)
        expect = int(expected_count)
        if len(asc_frames) != expect:
            raise AssertionError(
                f"ASC 回读 {len(asc_frames)} 帧 ≠ 录制 {expect} 帧")
        if blf_count != expect:
            raise AssertionError(
                f"BLF 回读 {blf_count} 帧 ≠ 录制 {expect} 帧")
        # 交叉核对：录制器里的 ID 序列 与 ASC 解析出的 ID 序列一致
        rec_ids = [fid for _, fid, _, _ in self._recorder]
        asc_ids = [aid for _, aid, _, _ in asc_frames]
        if rec_ids != asc_ids:
            raise AssertionError("ASC 里的 ID 序列与录制不一致")
        info(f"Trace 回读校验通过：ASC {len(asc_frames)} 帧、"
             f"BLF {blf_count} 帧，ID 序列一致 ✓")

    @keyword("Recorded Frames Should Contain ID")
    def recorded_frames_should_contain_id(self, arbitration_id) -> None:
        """断言录制期间总线上出现过指定 ID 的报文"""
        fid = int(str(arbitration_id), 0)
        found = any(fid == r[1] for r in self._recorder)
        if not found:
            raise AssertionError(f"录制期间未出现 ID={fid:#x} 的报文")
        info(f"录制期间出现过 ID={fid:#x} ✓")

    @keyword("Recorded Frames Should Not Contain ID")
    def recorded_frames_should_not_contain_id(self, arbitration_id) -> None:
        """断言录制期间总线上**没有**出现指定 ID 的报文。

        电源管理测试的核心断言：进入睡眠后应用报文必须完全停止
        （验证"熄火后没有节点还在偷偷发报文"——这直接关系静态电流指标）。
        """
        fid = int(str(arbitration_id), 0)
        count = sum(1 for r in self._recorder if fid == r[1])
        if count:
            raise AssertionError(
                f"录制期间出现了 {count} 帧 ID={fid:#x}"
                f"（预期不该出现 —— 检查是否已进入睡眠/停发）")
        info(f"录制期间未出现 ID={fid:#x} ✓")

    @keyword("Get Last Frame Data For ID")
    def get_last_frame_data_for_id(self, arbitration_id) -> str:
        """取录制帧里最后一次出现的指定 ID 的数据场（hex 字符串）。

        用于分析 NM 报文的位定义，如：
            ${nm}=    Get Last Frame Data For ID    0x510
            ${ready}=    Evaluate    int(${nm}[2:4], 16) & 0x01
        """
        fid = int(str(arbitration_id), 0)
        matches = [r for r in self._recorder if r[1] == fid]
        if not matches:
            raise AssertionError(f"录制期间未出现 ID={fid:#x}，无法取数据")
        # 返回 hex 字符串（不是 bytes）—— 这样在 RF 里能直接做
        # Evaluate 的 int(${data}[2:4], 16) 位运算；返回 bytes 会因
        # 含 null 字节导致 Evaluate 报错（真实踩过的坑）
        return matches[-1][2].hex()

    @keyword("Period And Jitter Should Be Within")
    def period_and_jitter_should_be_within(self, arbitration_id,
                                           expected_period_ms,
                                           period_tolerance_ms: float = 15.0,
                                           jitter_tolerance_ms: float = 30.0
                                           ) -> dict:
        """从**已录制**的帧里分析指定 ID 的周期均值与抖动，断言在容差内。

        - 周期均值 = 相邻两帧时间差的平均值（ECU 该 100ms 发，实测就得接近 100ms）
        - 抖动 Jitter = 最大间隔 - 最小间隔（总线时序稳定性的核心指标）

        为什么这是真实工作必测项：周期是设计指标，ECU 跑偏了会导致
        依赖该信号的节点（如仪表、ABS）行为异常。台架上用 CANoe 看这个，
        我们用录制器自己算 —— 思路一致。

        用法：
            Period And Jitter Should Be Within    0x100    100    period_tolerance_ms=20
        """
        fid = int(str(arbitration_id), 0)
        stamps = sorted(ts for ts, aid, _d, _e in self._recorder if aid == fid)
        if len(stamps) < 3:
            raise AssertionError(
                f"ID={fid:#x} 录制到的帧太少（{len(stamps)} 帧），无法分析周期"
                f"—— 先 Start Bus Recorder + Drain Bus For 至少 2 秒")
        gaps_ms = [(stamps[i + 1] - stamps[i]) * 1000.0
                   for i in range(len(stamps) - 1)]
        avg = sum(gaps_ms) / len(gaps_ms)
        jitter = max(gaps_ms) - min(gaps_ms)
        exp = float(str(expected_period_ms))
        tol = float(period_tolerance_ms)
        jtol = float(jitter_tolerance_ms)
        if abs(avg - exp) > tol:
            raise AssertionError(
                f"ID={fid:#x} 周期均值 {avg:.1f}ms 偏离期望 {exp:.0f}ms"
                f"（容差 ±{tol:.0f}ms，共 {len(stamps)} 帧）")
        if jitter > jtol:
            raise AssertionError(
                f"ID={fid:#x} 周期抖动 {jitter:.1f}ms 超过容差 {jtol:.0f}ms"
                f"（周期均值 {avg:.1f}ms）—— 总线时序不稳，影响依赖该信号的节点")
        info(f"ID={fid:#x}: {len(stamps)} 帧, 周期均值 {avg:.1f}ms "
             f"(期望 {exp:.0f}ms), 抖动 {jitter:.1f}ms ✓")
        return {"frames": len(stamps), "avg_period_ms": round(avg, 2),
                "jitter_ms": round(jitter, 2)}

    # ------------------------------------------------------------------ 录制
    @keyword("Drain Bus For")
    def drain_bus_for(self, seconds=2.0) -> None:
        """持续收包 N 秒，把总线上的报文全部录下来（不挑 ID）。

        参数可写 2 或 2s（RF 的 timedelta 风格也兼容）。

        抓包策略：内层循环一次抽干 socket 缓冲（而非一次一帧）——
        否则 Python 单帧读取速度跟不上 10ms 周期的报文，Trace 会漏帧
        （实测单帧模式 2 秒只抓到 ~70 帧，批量抽干后 ~200 帧）。
        """
        duration = float(str(seconds).replace("s", ""))
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            for _ in range(100):          # 抽干缓冲
                msg = self._bus.recv_frame(timeout=0.001)
                if msg is None:
                    break
                self._recorder.append((time.time(), msg.arbitration_id,
                                       msg.data, msg.is_extended_id))
        info(f"已录制 {duration}s，累计 {len(self._recorder)} 帧")
