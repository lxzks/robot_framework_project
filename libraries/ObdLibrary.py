"""
ObdLibrary.py — 功能寻址 / 新 UDS 服务 / OBD-II 测试库
=========================================================

解决三个"进阶诊断"测试需求（都基于同一根虚拟 CAN 总线）：

1. **功能寻址（Functional Addressing，0x7DF）**
   诊断仪把请求广播给总线上**所有** ECU（不是点对点），常用于 OBD-II
   扫描仪、以及"让所有 ECU 一起进会话/发 TesterPresent"。响应仍然由各 ECU
   发到自己的**物理响应 ID**（本项目 ECU = 0x7E8）——功能寻址只广播请求。
   本库自己用 `VirtualCanBus` 直接收发（现有 DiagnosticLibrary 只支持物理
   寻址 0x7E0，按 task 要求不改它），因此这里带一个最小 ISO-TP 收发实现。

2. **新增 UDS 服务**
   - `0x2F InputOutputControlByIdentifier`：强制控制执行器（本项目 DID 0x3001
     = 仪表 12 个警告灯的掩码）。关键字 `IOControl Send`。
   - `0x23 ReadMemoryByAddress` / `0x3D WriteMemoryByAddress`：按地址读写
     内存（教学区 0x08000000 起 1KB）。关键字 `Read/Write Memory By Address`。

3. **OBD-II（ISO 15031 / SAE J1979）**
   法规要求的排放诊断，走功能寻址 0x7DF，不需要厂家会话/解锁：
   - `01 <PID>` 当前数据（PID 05 水温 / 0C 转速 / 0D 车速）
   - `03` 读已确认 DTC、`04` 清 DTC、`09 02` 读 VIN

用法（套件里）::

    Resource    ../../resources/common.resource
    Library     ../../libraries/ObdLibrary.py

    Suite Setup     Start Vehicle Environment    speed=60
    Suite Setup     Open OBD Channel
    Suite Teardown  关闭 OBD 环境

    ${raw}=    Send Functional UDS Request    3E 00
    ${rpm}=    OBD Read RPM
    ${data}=   Read Memory By Address    0x08000000    4

技术说明：
- ROBOT_LIBRARY_SCOPE = "SUITE"：一个套件一个实例（一根总线 + 同步收发）。
- 该库的关键字名与 DiagnosticLibrary 有部分重合（`Response Should Be Positive`），
  Robot Framework 在套件里要求用 `ObdLibrary.` 前缀消歧；套件中已按此写法。
- 所有 `Send *` 关键字只发不收断言（返回原始响应 hex），断言类关键字单独调用，
  这样负向用例可以精确断言 NRC。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                                   # noqa: E402
from uds_lib import (                                           # noqa: E402
    IsoTpReceiver, IsoTpSender, dtc_to_string, parse_response,
)

# 诊断寻址 ID（与 simulators/ecu_sim.py 保持一致）
ID_PHYSICAL_REQUEST = 0x7E0     # 物理寻址：点对点请求
ID_FUNCTIONAL_REQUEST = 0x7DF   # 功能寻址：广播请求（OBD-II 也走这里）
ID_RESPONSE = 0x7E8             # 本 ECU 的响应 ID（功能/物理寻址都一样）

# 内存映射教学区（与 ECU 一致：0x08000000 起 1KB）
MEMORY_AREA_BASE = 0x08000000
MEMORY_AREA_SIZE = 0x400


def _hex_int(value) -> int:
    """把 '0x3001' / '3001'（十六进制语义）转成 int。"""
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return int(text, 16)


def _num(value) -> int:
    """把 '0x0C' / '12' 转成 int（支持十六进制前缀，否则按十进制）。"""
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


class ObdLibrary:
    """功能寻址 / 0x2F / 0x23·0x3D / OBD-II 关键字库"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, node_name: str = "RF-OBD") -> None:
        self._node_name = node_name
        self._bus: VirtualCanBus | None = None
        self._last_raw: bytes = b""
        self._last_response = None
        self._last_response_id: int | None = None

    # ==================================================================
    # 一、通道生命周期
    # ==================================================================
    @keyword("Open OBD Channel")
    def open_obd_channel(self) -> None:
        """打开 OBD/功能寻址测试通道（复用 VirtualCanBus 虚拟 CAN 总线）。

        echo_own=False：不需要回读自己发的请求帧（只关心 ECU 响应）。
        """
        if self._bus is not None:
            info("OBD 通道已打开（复用现有连接）")
            return
        self._bus = VirtualCanBus(self._node_name, echo_own=False)
        console(f"[ObdLibrary] OBD/功能寻址通道已就绪（{self._node_name}）")

    @keyword("Close OBD Channel")
    def close_obd_channel(self) -> None:
        """关闭通道、释放虚拟总线（套件 Teardown 调用）。"""
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
            info("OBD 通道已关闭")

    def _ensure_channel(self) -> None:
        if self._bus is None:
            self.open_obd_channel()

    # ==================================================================
    # 二、最小 ISO-TP 收发（功能寻址 + 物理寻址共用）
    # ==================================================================
    def _send_and_wait(self, request: bytes, functional: bool = False,
                       timeout: float = 3.0, retries: int = 1) -> bytes:
        """发一条 UDS 请求并等完整响应（自动处理 ISO-TP 多帧）。

        为什么自己实现：DiagnosticTester 只发物理寻址 0x7E0；功能寻址要求
        同一套 TP 能力但换发送 ID。这里保持"同步收发"（单线程，无收包队列），
        对测试场景（一问一答）足够，且行为确定。

        关于重试：虚拟总线是 UDP 组播，**没有真实 CAN 的帧重传**，高负载下
        偶发丢一帧就会整条请求超时（真实台架用 TP 层 N_Cr/N_As 超时重发）。
        这里对**超时**做一次幂等重试，避免偶发网络丢帧被误判成产品缺陷；
        真正的功能性错误（NRC/数据错）不会被重试掩盖。
        """
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._attempt_send_and_wait(request, functional, timeout)
            except TimeoutError as exc:
                last_err = exc
                if attempt < retries:
                    info(f"⚠ 请求超时（{exc}），重试一次（UDP 组播无重传，偶发丢帧）")
                    if self._bus is not None:
                        self._bus.drain_rx()   # 清掉上一轮残留后再重试
        raise TimeoutError(
            f"{timeout}s 内没等到 UDS 响应（已重试 {retries} 次）: {last_err}")

    def _attempt_send_and_wait(self, request: bytes, functional: bool,
                               timeout: float) -> bytes:
        """`_send_and_wait` 的单次实现（不含重试逻辑）。"""
        self._ensure_channel()
        assert self._bus is not None
        tx_id = ID_FUNCTIONAL_REQUEST if functional else ID_PHYSICAL_REQUEST
        self._bus.drain_rx()      # 清掉上一轮残留（周期报文/迟到响应）

        # ---- 发请求（>7 字节走 FF + CF，并等 ECU 的 FC）----
        if len(request) <= 7:
            self._bus.send_frame(tx_id, bytes([len(request)]) + request)
        else:
            frames = IsoTpSender.split_frames(request)
            self._bus.send_frame(tx_id, frames[0])
            fc = self._wait_fc_frame(timeout)
            block_size = fc.data[1] if len(fc.data) > 1 else 0
            sent = 0
            for frame in frames[1:]:
                self._bus.send_frame(tx_id, frame)
                sent += 1
                if block_size and sent % block_size == 0 \
                        and sent < len(frames) - 1:
                    self._wait_fc_frame(timeout)

        # ---- 收响应（重组多帧；按请求 SID 过滤串扰帧）----
        raw = self._receive_response(timeout, expected_sid=request[0])
        self._last_raw = raw
        self._last_response = parse_response(raw)
        return raw

    def _wait_fc_frame(self, timeout: float = 3.0):
        """等 ECU 的流控帧（PCI=0x30，发在 0x7E8）。"""
        assert self._bus is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._bus.recv_frame(timeout=0.05)
            if msg is None:
                continue
            if msg.arbitration_id != ID_RESPONSE or not msg.data:
                continue
            if msg.data[0] & 0xF0 == 0x30:      # 流控帧
                return msg
        raise TimeoutError(f"{timeout}s 内没等到 ECU 的流控帧(FC)")

    @staticmethod
    def _response_matches(payload, expected_sid: int) -> bool:
        """按 SID 判断一条响应是否属于本次请求（防同套件多诊断仪串扰）。

        正响应：SID+0x40；负响应：0x7F + 请求 SID + NRC。
        """
        if not payload:
            return False
        resp_sid = payload[0]
        if resp_sid == ((expected_sid + 0x40) & 0xFF):
            return True
        return (resp_sid == 0x7F and len(payload) >= 2
                and payload[1] == expected_sid)

    def _receive_response(self, timeout: float = 3.0,
                          expected_sid: int | None = None) -> bytes:
        """同步收一条完整 UDS 响应；多帧响应时主动给 ECU 回流控帧。

        `expected_sid` 不为 None 时只接受属于该请求的响应（SID+0x40 或
        7F+SID），跳过其它诊断仪实例的迟到响应帧——同一套件里
        DiagnosticLibrary 与 ObdLibrary 都监听 0x7E8，不过滤会偶发串扰。
        """
        assert self._bus is not None
        rx = IsoTpReceiver()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
            msg = self._bus.recv_frame(timeout=min(0.05, remaining))
            if msg is None or msg.arbitration_id != ID_RESPONSE or not msg.data:
                continue
            data = bytes(msg.data)
            pci = data[0]
            self._last_response_id = msg.arbitration_id
            if pci & 0xF0 == 0x00:             # 单帧
                payload = data[1:1 + (pci & 0x0F)]
                if expected_sid is not None and \
                        not self._response_matches(payload, expected_sid):
                    continue
                return payload
            if pci & 0xF0 == 0x10:             # 首帧：回流控帧并开始重组
                first = data[2:2 + 6]
                if expected_sid is not None and \
                        not self._response_matches(first, expected_sid):
                    continue                   # 不是本次请求的响应，跳过
                rx = IsoTpReceiver()
                try:
                    rx.feed(data)
                except ValueError:
                    rx = IsoTpReceiver()
                    continue
                # ECU 等 FC 的接收 ID 固定是物理请求 ID 0x7E0
                self._bus.send_frame(ID_PHYSICAL_REQUEST,
                                     bytes([0x30, 0x00, 0x00]))
                continue
            if pci & 0xF0 == 0x20:             # 连续帧
                try:
                    done, full = rx.feed(data)
                except ValueError:
                    rx = IsoTpReceiver()
                    continue
                if done and full is not None:
                    if expected_sid is not None and \
                            not self._response_matches(full, expected_sid):
                        rx = IsoTpReceiver()
                        continue
                    return full
        raise TimeoutError(f"{timeout}s 内没等到 UDS 响应")

    # ==================================================================
    # 三、发送 / 断言基础关键字
    # ==================================================================
    @keyword("Send Functional UDS Request")
    def send_functional_uds_request(self, request_hex: str) -> str:
        """向功能寻址 0x7DF 发一条 UDS 请求，返回完整响应 hex。

        请求会广播给总线上所有 ECU；本库只收本 ECU 的响应（0x7E8）。
        例：``Send Functional UDS Request    3E 00``
        """
        req = bytes.fromhex(request_hex.replace(" ", ""))
        raw = self._send_and_wait(req, functional=True)
        info(f"功能寻址请求 {req.hex()} → 响应 {raw.hex()}")
        return raw.hex()

    @keyword("Send Physical UDS Request")
    def send_physical_uds_request(self, request_hex: str) -> str:
        """向物理寻址 0x7E0 发一条 UDS 请求，返回完整响应 hex。

        与 DiagnosticLibrary 的 `Send UDS Request` 等价，但收发在本库内完成，
        便于在同一测试里交替使用"功能寻址/物理寻址"。
        """
        req = bytes.fromhex(request_hex.replace(" ", ""))
        raw = self._send_and_wait(req, functional=False)
        info(f"物理寻址请求 {req.hex()} → 响应 {raw.hex()}")
        return raw.hex()

    @keyword("Response Should Be Positive")
    def response_should_be_positive(self) -> None:
        """断言上一条响应为正响应。"""
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if not self._last_response.positive:
            raise AssertionError(
                f"期望正响应，实际: {self._last_response.describe()}")

    @keyword("Response Should Be Negative With NRC")
    def response_should_be_negative_with_nrc(self, nrc) -> None:
        """断言上一条响应为指定 NRC 的负响应（nrc 支持 '0x31' / 49）。"""
        nrc_int = int(str(nrc), 0)
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if self._last_response.positive:
            raise AssertionError(
                f"期望负响应 NRC={nrc_int:#04x}，实际: "
                f"{self._last_response.describe()}")
        if self._last_response.nrc != nrc_int:
            raise AssertionError(
                f"期望 NRC={nrc_int:#04x}，实际: {self._last_response.describe()}")

    @keyword("Response Should Come From")
    def response_should_come_from(self, response_id) -> None:
        """断言响应来自指定 CAN ID（功能寻址用例验证"响应仍发 0x7E8"）。"""
        expect = int(str(response_id), 0)
        if self._last_response_id != expect:
            raise AssertionError(
                f"响应来自 {self._last_response_id!r}，期望 {expect:#x}")
        info(f"响应来自 {expect:#x} ✓")

    @keyword("Last Response Payload Hex")
    def last_response_payload_hex(self) -> str:
        """上一条响应中"去掉响应 SID"之后的 payload（hex）。"""
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        return self._last_response.payload.hex()

    # ==================================================================
    # 四、0x2F 执行器控制
    # ==================================================================
    @keyword("IOControl Send")
    def iocontrol_send(self, did, control, mask=0) -> str:
        """发 0x2F InputOutputControlByIdentifier，返回响应 hex（不做断言）。

        参数：
            did      数据标识符（本项目 0x3001 = 仪表 12 个警告灯执行器）
            control  00 returnControlToECU / 01 resetToDefault
                     02 shortTermAdjustment / 03 longTermAdjustment
            mask     12 位警告灯掩码（位序与 DBC TelltaleStatus 一致）

        请求格式：``2F <DID 2B> <control 1B> <mask 2B>``
        正响应  ：``6F <DID 2B> <controlState 2B>``
        """
        did_int = _hex_int(did)
        control_int = _num(control)
        mask_int = _num(mask) & 0x1FFF
        req = bytes([
            0x2F,
            (did_int >> 8) & 0xFF, did_int & 0xFF,
            control_int,
            (mask_int >> 8) & 0xFF, mask_int & 0xFF,
        ])
        raw = self._send_and_wait(req, functional=False)
        info(f"0x2F IOControl DID={did_int:#06x} control={control_int:#04x} "
             f"mask={mask_int:#06x} → {raw.hex()}")
        return raw.hex()

    # ==================================================================
    # 五、0x23 / 0x3D 内存读写
    # ==================================================================
    @keyword("Read Memory By Address")
    def read_memory_by_address(self, address, size) -> str:
        """0x23 ReadMemoryByAddress：按地址读内存，返回数据 hex。

        请求：``23 <ALFID> <address 4B> <size N>``（ALFID 高 4 位=size 字节数，
        低 4 位=地址字节数；本库地址固定 4 字节，size ≤255 用 0x14，否则 0x24）。
        正响应：``63 + 数据``。
        """
        addr = _hex_int(address) if not isinstance(address, int) else address
        length = int(size)
        size_len = 1 if length <= 0xFF else 2
        alfi = (size_len << 4) | 0x04
        req = (bytes([0x23, alfi]) + addr.to_bytes(4, "big")
               + length.to_bytes(size_len, "big"))
        raw = self._send_and_wait(req, functional=False)
        self._last_raw = raw
        resp = parse_response(raw)
        if not resp.positive:
            raise AssertionError(
                f"0x23 读内存失败（{addr:#010x}, {length}B）: {resp.describe()}")
        info(f"0x23 读内存 {addr:#010x} {length}B → {resp.payload.hex()}")
        return resp.payload.hex()

    @keyword("Write Memory By Address")
    def write_memory_by_address(self, address, data_hex: str) -> str:
        """0x3D WriteMemoryByAddress：按地址写内存，返回响应 hex（7d）。

        请求：``3D <ALFID> <address 4B> <size N> <数据>``。
        写超过 7 字节时走 ISO-TP 多帧（本库自动处理）。
        """
        addr = _hex_int(address) if not isinstance(address, int) else address
        data = bytes.fromhex(data_hex.replace(" ", ""))
        size_len = 1 if len(data) <= 0xFF else 2
        alfi = (size_len << 4) | 0x04
        req = (bytes([0x3D, alfi]) + addr.to_bytes(4, "big")
               + len(data).to_bytes(size_len, "big") + data)
        raw = self._send_and_wait(req, functional=False)
        info(f"0x3D 写内存 {addr:#010x} {len(data)}B → {raw.hex()}")
        return raw.hex()

    # ==================================================================
    # 六、OBD-II（功能寻址 0x7DF）
    # ==================================================================
    @keyword("OBD Read PID")
    def obd_read_pid(self, pid) -> str:
        """OBD mode 0x01 读当前数据：发 ``01 <PID>``，返回数据场 hex（A,B）。

        正响应格式：``41 <PID> <A> <B>``；本关键字校验 PID 回显后返回 ``AABB``。
        """
        pid_int = _num(pid)
        raw = self._send_and_wait(bytes([0x01, pid_int]), functional=True)
        self._last_raw = raw
        resp = parse_response(raw)
        if not resp.positive:
            raise AssertionError(
                f"OBD PID {pid_int:#04x} 读取失败: {resp.describe()}")
        payload = resp.payload
        if not payload or payload[0] != pid_int:
            raise AssertionError(f"OBD 响应的 PID 回显异常: {payload.hex()}")
        data = payload[1:]
        info(f"OBD PID {pid_int:#04x} → {data.hex()}")
        return data.hex()

    @keyword("OBD Decode RPM")
    def obd_decode_rpm(self, data_hex: str) -> float:
        """按 SAE J1979 解码转速：rpm = ((A*256)+B)/4。"""
        data = bytes.fromhex(data_hex.replace(" ", ""))
        if len(data) < 2:
            raise AssertionError(f"PID 0C 数据至少 2 字节: {data_hex!r}")
        return ((data[0] * 256) + data[1]) / 4.0

    @keyword("OBD Decode Speed")
    def obd_decode_speed(self, data_hex: str) -> int:
        """按 SAE J1979 解码车速：km/h = A。"""
        data = bytes.fromhex(data_hex.replace(" ", ""))
        if len(data) < 1:
            raise AssertionError(f"PID 0D 数据至少 1 字节: {data_hex!r}")
        return data[0]

    @keyword("OBD Decode Coolant")
    def obd_decode_coolant(self, data_hex: str) -> int:
        """按 SAE J1979 解码水温：℃ = A - 40。"""
        data = bytes.fromhex(data_hex.replace(" ", ""))
        if len(data) < 1:
            raise AssertionError(f"PID 05 数据至少 1 字节: {data_hex!r}")
        return data[0] - 40

    @keyword("OBD Read RPM")
    def obd_read_rpm(self) -> float:
        """读发动机转速（PID 0x0C），返回 rpm。"""
        return self.obd_decode_rpm(self.obd_read_pid(0x0C))

    @keyword("OBD Read Speed")
    def obd_read_speed(self) -> int:
        """读车速（PID 0x0D），返回 km/h。"""
        return self.obd_decode_speed(self.obd_read_pid(0x0D))

    @keyword("OBD Read Coolant")
    def obd_read_coolant(self) -> int:
        """读冷却液温度（PID 0x05），返回 ℃。"""
        return self.obd_decode_coolant(self.obd_read_pid(0x05))

    @keyword("OBD Read DTCs")
    def obd_read_dtcs(self) -> list:
        """OBD mode 0x03：读已确认 DTC，返回 DTC 字符串列表。

        正响应：``43 <数量> <DTC1 2B> <DTC2 2B> ...``（OBD 用 2 字节 DTC 编码，
        与 UDS 3 字节编码的前 2 字节一致）。
        """
        raw = self._send_and_wait(bytes([0x03]), functional=True)
        self._last_raw = raw
        resp = parse_response(raw)
        if not resp.positive:
            raise AssertionError(f"OBD 读 DTC 失败: {resp.describe()}")
        payload = resp.payload
        if not payload:
            raise AssertionError("OBD 0x03 响应缺少数量字节")
        count = payload[0]
        dtcs = []
        idx = 1
        while idx + 1 < len(payload):
            dtcs.append(dtc_to_string(payload[idx], payload[idx + 1], 0))
            idx += 2
        if count != len(dtcs):
            raise AssertionError(
                f"OBD 0x03 数量字节 {count} 与实际 DTC 数 {len(dtcs)} 不一致")
        info(f"OBD mode03 已确认 DTC: {dtcs}")
        return dtcs

    @keyword("OBD Clear DTCs")
    def obd_clear_dtcs(self) -> str:
        """OBD mode 0x04：清除 DTC，返回响应 hex（期望 44）。"""
        raw = self._send_and_wait(bytes([0x04]), functional=True)
        self._last_raw = raw
        info(f"OBD mode04 清 DTC → {raw.hex()}")
        return raw.hex()

    @keyword("OBD Read VIN")
    def obd_read_vin(self) -> str:
        """OBD mode 0x09 PID 0x02：读 VIN（17 字节，走 ISO-TP 多帧响应）。

        正响应：``49 02 01 <VIN 17B>``；返回 VIN 字符串。
        """
        raw = self._send_and_wait(bytes([0x09, 0x02]), functional=True)
        self._last_raw = raw
        resp = parse_response(raw)
        if not resp.positive:
            raise AssertionError(f"OBD 读 VIN 失败: {resp.describe()}")
        payload = resp.payload
        if len(payload) < 3 or payload[0] != 0x02 or payload[1] != 0x01:
            raise AssertionError(
                f"OBD 0x09 02 响应头异常: {payload.hex()}")
        vin = payload[2:]
        if len(vin) != 17:
            raise AssertionError(f"VIN 长度应为 17，实际 {len(vin)}: {vin!r}")
        text = vin.decode("ascii", errors="replace")
        info(f"OBD 读 VIN = {text}")
        return text

    @keyword("OBD Response Should Be")
    def obd_response_should_be(self, expected_hex: str) -> None:
        """断言上一条响应（完整 hex）等于期望值（忽略大小写/空格）。"""
        expect = expected_hex.replace(" ", "").lower()
        actual = self._last_raw.hex()
        if actual != expect:
            raise AssertionError(f"期望响应 {expect}，实际 {actual}")
        info(f"OBD 响应 = {actual} ✓")

    @keyword("OBD Response Should Start With")
    def obd_response_should_start_with(self, expected_prefix_hex: str) -> None:
        """断言上一条响应的前缀（用于"服务+数据"类断言）。"""
        expect = expected_prefix_hex.replace(" ", "").lower()
        actual = self._last_raw.hex()
        if not actual.startswith(expect):
            raise AssertionError(f"期望响应以 {expect} 开头，实际 {actual}")
        info(f"OBD 响应前缀 = {expect} ✓")
