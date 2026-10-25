"""
DiagnosticLibrary.py — Robot Framework UDS 诊断关键字库
========================================================

把 UDS 诊断仪（uds_client.DiagnosticTester）包装成 RF 关键字。
测试用例里就能这样写诊断测试：

    Enter Extended Session
    Security Unlock
    ${vin}=    Read DID    F190
    ${count}=    Read DTC Count
    Response Should Be Negative With NRC    33

设计要点：
    - 每个关键字内部都通过 send_request 完成"发请求→等响应"，
      响应自动处理 ISO-TP 多帧重组
    - 断言类关键字（... Should Be ...）失败时自动抛 AssertionError，
      RF 会标记用例失败并附上错误信息
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                       # noqa: E402
from uds_client import (                           # noqa: E402
    SECURITY_XOR_KEY, DiagnosticTester,
)
from uds_lib import dtc_to_string  # noqa: E402


class DiagnosticLibrary:
    """UDS 诊断操作关键字集合"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, node_name: str = "RF-TESTER") -> None:
        # echo_own=False：诊断仪不需要回读自己发的请求帧（只要 0x7E8 响应），
        # 关掉可让接收循环专心处理 ECU 响应
        self._bus = VirtualCanBus(node_name, echo_own=False)
        self._tester = DiagnosticTester(self._bus)
        self._tester.start()
        self._last_response = None
        console("[DiagnosticLibrary] 诊断仪已就绪")

    # ------------------------------------------------------------------ 基础
    @keyword("Send UDS Request")
    def send_uds_request(self, request_hex: str) -> str:
        """发送裸 UDS 请求（十六进制），返回响应描述文本。

        例：Send UDS Request    10 03
        """
        req = bytes.fromhex(request_hex.replace(" ", ""))
        ok, resp = self._tester.send_request(req)
        self._last_response = resp
        info(f"请求 {req.hex()} → {resp.describe()}")
        return resp.describe()

    @keyword("ECU Should Respond")
    def ecu_should_respond(self) -> None:
        """断言 ECU 在线：发 TesterPresent 并期望正响应。

        Suite Setup 的环境就绪检查用 —— 比"看进程起来"更可靠，
        直接验证诊断链路通。
        """
        self.send_uds_request("3E 00")
        self.response_should_be_positive()

    @keyword("Close Diagnostic Tester")
    def close_diagnostic_tester(self) -> None:
        """断开诊断仪（suite 结束时调用）"""
        self._tester.stop()
        self._bus.shutdown()

    # ------------------------------------------------------------------ 断言
    @keyword("Response Should Be Positive")
    def response_should_be_positive(self) -> None:
        """断言上一条 UDS 响应是正响应"""
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if not self._last_response.positive:
            raise AssertionError(f"期望正响应，实际: {self._last_response.describe()}")
        info("正响应 ✓")

    @keyword("Response Should Be Negative With NRC")
    def response_should_be_negative_with_nrc(self, nrc) -> None:
        """断言上一条响应是指定 NRC 的负响应。nrc 支持 '0x33' 或 '51'"""
        nrc_int = int(str(nrc), 0)
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if self._last_response.positive:
            raise AssertionError(f"期望负响应，实际: {self._last_response.describe()}")
        if self._last_response.nrc != nrc_int:
            raise AssertionError(
                f"期望 NRC={nrc_int:#04x}，实际: {self._last_response.describe()}")
        info(f"NRC={nrc_int:#04x} ✓")

    # ------------------------------------------------------------------ 会话
    @keyword("Enter Extended Session")
    def enter_extended_session(self) -> None:
        """进入扩展诊断会话（10 03）—— 开放写/例程等高级功能"""
        self.send_uds_request("10 03")
        self.response_should_be_positive()

    @keyword("Enter Default Session")
    def enter_default_session(self) -> None:
        """回到默认诊断会话（10 01）"""
        self.send_uds_request("10 01")
        self.response_should_be_positive()

    @keyword("Tester Present")
    def tester_present(self) -> None:
        """发送 TesterPresent（3E 00）保活，防止 S3 超时回默认会话"""
        self.send_uds_request("3E 00")
        self.response_should_be_positive()

    # ------------------------------------------------------------------ 安全访问
    @keyword("Request Seed")
    def request_seed(self) -> str:
        """请求安全访问种子（27 01），返回 4 字节种子的 hex"""
        self.send_uds_request("27 01")
        self.response_should_be_positive()
        seed = self._last_response.payload[1:5]
        return seed.hex()

    @keyword("Send Key")
    def send_key(self, key_hex: str) -> None:
        """发送密钥（27 02）"""
        self.send_uds_request(f"27 02 {key_hex}")

    @keyword("Security Unlock")
    def security_unlock(self) -> None:
        """自动完成种子-密钥握手（27 01 + 27 02）。

        本项目的密钥算法：key = seed XOR 5A5A5A5A（教学算法，
        真实 OEM 算法保密，但流程完全一致）。
        """
        seed = bytes.fromhex(self.request_seed())          # 27 01
        key = (int.from_bytes(seed, "big") ^ SECURITY_XOR_KEY).to_bytes(4, "big")
        info(f"种子 {seed.hex()} → 密钥 {key.hex()}")
        self.send_key(key.hex())                           # 27 02
        self.response_should_be_positive()                 # 67 02 = 解锁成功

    # ------------------------------------------------------------------ DID
    @keyword("Read DID")
    def read_did(self, did: str) -> str:
        """读数据标识符（22），返回数据的 hex 字符串。

        例：${data}=    Read DID    F190
        """
        self.send_uds_request(f"22 {did}")
        self.response_should_be_positive()
        data = self._last_response.payload[2:]
        return data.hex()

    @keyword("Read DID As ASCII")
    def read_did_as_ascii(self, did: str) -> str:
        """读 DID 并按 ASCII 解码（VIN、零件号等文本型 DID 用）"""
        hex_data = self.read_did(did)
        return bytes.fromhex(hex_data).decode("ascii", errors="replace")

    @keyword("Write DID")
    def write_did(self, did: str, value_hex: str) -> None:
        """写数据标识符（2E），需要先进扩展会话 + 安全解锁"""
        self.send_uds_request(f"2E {did} {value_hex}")
        self.response_should_be_positive()

    # ------------------------------------------------------------------ DTC
    @keyword("Read DTC Count")
    def read_dtc_count(self) -> int:
        """读取 DTC 总数（19 01）"""
        self.send_uds_request("19 01")
        self.response_should_be_positive()
        count = self._last_response.payload[2]
        return count

    @keyword("Read DTC By Mask")
    def read_dtc_by_mask(self, mask: str = "08") -> list:
        """按状态掩码读 DTC 列表（19 02），返回 [(dtc, status_hex), ...]

        mask=08 → 只读 confirmed；mask=FF → 全部
        """
        self.send_uds_request(f"19 02 {mask}")
        self.response_should_be_positive()
        body = self._last_response.payload[1:]   # 去掉 sub-function 回显
        result = []
        i = 1
        while i + 4 <= len(body):
            dtc = dtc_to_string(body[i], body[i + 1], body[i + 2])
            status = body[i + 3]
            result.append((dtc, f"{status:#04x}"))
            i += 4
        return result

    @keyword("DTC List Should Contain")
    def dtc_list_should_contain(self, dtc: str) -> None:
        """断言当前 confirmed DTC 列表里包含指定故障码"""
        dtcs = self.read_dtc_by_mask("08")
        names = [d for d, _ in dtcs]
        if dtc not in names:
            raise AssertionError(f"DTC 列表 {names} 中不包含 {dtc}")
        info(f"确认 {dtc} 在 DTC 列表中 ✓")

    @keyword("Clear DTC")
    def clear_dtc(self) -> None:
        """清除全部 DTC（14 FF FF FF）"""
        self.send_uds_request("14 FF FF FF")
        self.response_should_be_positive()

    # ------------------------------------------------------------------ 例程
    @keyword("Start Routine")
    def start_routine(self, routine_id: str) -> None:
        """启动例程（31 01），如 Start Routine    0200（自检）"""
        self.send_uds_request(f"31 01 {routine_id}")
        self.response_should_be_positive()

    @keyword("Start Light Test Routine")
    def start_light_test_routine(self) -> None:
        """启动仪表灯光测试例程（31 01 0301）：点亮所有警告灯 2 秒"""
        self.send_uds_request("31 01 0301")
        self.response_should_be_positive()

    # ------------------------------------------------------------------ 通信控制（0x28）
    @keyword("Suspend ECU Periodic Messages")
    def suspend_ecu_periodic_messages(self) -> None:
        """暂停 ECU 周期报文（28 01 01：只停发送，保留诊断接收）。

        台架信号测试的关键前置：ECU 每 100ms 也在发同一报文，若不暂停，
        测试注入的值会与 ECU 的周期帧混在总线上，读回"到底是谁的"说不清。
        真实台架测试与刷写流程都用这条服务独占总线。
        """
        self.send_uds_request("28 01 01")
        self.response_should_be_positive()
        info("ECU 周期报文已暂停（28 01 01）")

    @keyword("Resume ECU Periodic Messages")
    def resume_ecu_periodic_messages(self) -> None:
        """恢复 ECU 周期报文（28 00）"""
        self.send_uds_request("28 00")
        self.response_should_be_positive()
        info("ECU 周期报文已恢复（28 00）")

    # ------------------------------------------------------------------ 测试激励（工程模式 DID）
    @keyword("Set Test Stimulus")
    def set_test_stimulus(self, voltage=None, drop_rate=None,
                          suppress_mask=None) -> None:
        """设置测试激励（工程模式 DID F1B0）—— 故障注入的核心手段。

        参数（不传的保持上次设定）：
            voltage        供电电压(V)：<10.5 → 触发 P0562；>15.5 → 触发 P0563
            drop_rate      周期报文丢帧率(%)：模拟总线质量下降
            suppress_mask  报文停发位图：bit0=0x100 bit1=0x200 bit2=0x300
                           （模拟某信号源失效 → 验证接收方降级策略）

        前置：必须先 Enter Extended Session + Security Unlock（工程模式 DID
        同样受权限模型保护 —— 这是真实 ECU 的做法，不能因为是"测试用途"
        就放松权限）。
        """
        cur = getattr(self, "_stimulus", {"voltage": 14, "drop_rate": 0,
                                         "suppress_mask": 0})
        if voltage is not None:
            cur["voltage"] = int(voltage)
        if drop_rate is not None:
            cur["drop_rate"] = int(drop_rate)
        if suppress_mask is not None:
            cur["suppress_mask"] = int(suppress_mask)
        self._stimulus = cur
        payload = bytes([cur["voltage"], cur["drop_rate"],
                         cur["suppress_mask"], 0])
        self.send_uds_request("2E F1B0 " + payload.hex())
        self.response_should_be_positive()
        info(f"测试激励已设置: 电压={cur['voltage']}V 丢帧率={cur['drop_rate']}% "
             f"停发位图={cur['suppress_mask']:#04x}")

    @keyword("Clear Test Stimulus")
    def clear_test_stimulus(self) -> None:
        """清除全部测试激励，恢复正常工况（电压 14V / 无丢帧 / 不停发）"""
        self._stimulus = {"voltage": 14, "drop_rate": 0, "suppress_mask": 0}
        payload = bytes([14, 0, 0, 0])
        self.send_uds_request("2E F1B0 " + payload.hex())
        self.response_should_be_positive()
        info("测试激励已清除（恢复正常工况）")

    # ------------------------------------------------------------------ 刷写（0x34/0x36/0x37）
    @keyword("Enter Programming Session")
    def enter_programming_session(self) -> None:
        """进入编程会话（10 02）—— 刷写的前置条件"""
        self.send_uds_request("10 02")
        self.response_should_be_positive()

    @keyword("Request Download")
    def request_download(self, address: str = "08000000", size: int = 128) -> int:
        """请求下载（0x34）。

        参数：目标地址（十六进制字符串）、要写入的字节数。
        正响应 74 20 xx xx → 返回 maxBlockLength（单块最大字节数）。
        """
        addr = int(address, 16)
        # dataFormatIdentifier=00, addressAndLengthFormatIdentifier=44
        # （高 4 位=size 字节数 4，低 4 位=addr 字节数 4）
        payload = bytes([0x00, 0x44]) + \
            addr.to_bytes(4, "big") + int(size).to_bytes(4, "big")
        resp = self.send_uds_request("34 " + payload.hex())
        self.response_should_be_positive()
        # 从响应里解出 maxBlockLength：正响应 payload = 20 <hi> <lo>
        data = self._last_response.payload
        max_block = int.from_bytes(data[1:3], "big")
        info(f"请求下载成功: 地址 {address} 大小 {size} 字节，"
             f"单块上限 {max_block} 字节")
        return max_block

    @keyword("Transfer Data Block")
    def transfer_data_block(self, block_counter: int, data_hex: str) -> None:
        """传输数据块（0x36）：块序号 + 数据（十六进制）"""
        block = int(block_counter) & 0xFF
        self.send_uds_request(f"36 {block:02x} {data_hex}")
        self.response_should_be_positive()

    @keyword("Request Transfer Exit")
    def request_transfer_exit(self) -> None:
        """退出传输（0x37）"""
        self.send_uds_request("37")
        self.response_should_be_positive()

    @keyword("Check Programming Integrity")
    def check_programming_integrity(self) -> None:
        """检查编程完整性（31 01 FF00）：校验和/长度比对，结果 00=通过"""
        self.send_uds_request("31 01 FF00")
        self.response_should_be_positive()

    @keyword("Integrity Check Result Should Be")
    def integrity_check_result_should_be(self, result: int) -> None:
        """断言完整性校验结果（31 01 FF00 响应最后 1 字节：00=通过 01=失败）"""
        data = self._last_response.payload
        actual = data[-1] if data else None
        if actual != int(result):
            raise AssertionError(
                f"完整性校验结果 {actual} ≠ 期望 {result}"
                f"（0x00=通过，0x01=失败）")
        info(f"完整性校验结果 = {actual} ✓")

    @keyword("Measure Response Time")
    def measure_response_time(self, request_hex: str) -> float:
        """测量"请求→响应"往返耗时（毫秒），用于 P2 时序一致性验证。

        P2 = ECU 声明的最长响应时间（本项目 50ms）。实测耗时若持续大于
        P2，诊断仪会误判超时 —— 这是协议一致性测试的必测项。
        """
        t0 = time.monotonic()
        self.send_uds_request(request_hex)
        elapsed = (time.monotonic() - t0) * 1000.0
        info(f"响应耗时 {elapsed:.1f} ms")
        return elapsed
