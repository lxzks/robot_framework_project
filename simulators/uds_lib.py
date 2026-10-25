"""
uds_lib.py — UDS (ISO 14229) 协议编解码 + ISO-TP 多帧传输
============================================================

UDS = Unified Diagnostic Services，车载诊断的"通用语言"。
诊断仪（Tester）发请求，ECU 回响应，一问一答。

请求格式（单帧，CAN 8 字节数据场内放得下时）：
    [服务号 SID][参数 0][参数 1]...
例如读取 DID 0xF187（零件号）：
    [22][F1][87]

响应格式：
    正响应：[SID + 0x40][参数...]      例：[62][F1][A5][...]
    负响应：[7F][原 SID][NRC]          例：[7F][22][31]  (requestOutOfRange)

超过 7 字节的数据（比如读一长串 DTC 列表）不能塞进单帧 CAN，
需要 ISO 15765-2 传输协议（俗称 CAN TP）拆成多帧：
    首帧 FF:  [1x][长度低字节] + 前 6 字节数据
    连续帧 CF:[2x] + 后续 7 字节（x = 帧序号 0~15 循环）
    流控帧 FC:[30][块大小][STmin] —— 接收方告诉发送方"你能连续发几帧、帧间隔多久"

本模块把上面这些全部封装成函数，供 ECU 模拟器和测试代码共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 服务号 SID（Service Identifier）—— UDS 的"动词表"
# ---------------------------------------------------------------------------
SID_DIAGNOSTIC_SESSION_CONTROL = 0x10   # 诊断会话控制
SID_ECU_RESET = 0x11                    # ECU 复位
SID_CLEAR_DTC = 0x14                    # 清除故障码
SID_READ_DTC = 0x19                     # 读取故障码
SID_READ_DID = 0x22                     # 读数据标识符（按 ID 读一块数据）
SID_SECURITY_ACCESS = 0x27              # 安全访问（种子-密钥握手）
SID_COMMUNICATION_CONTROL = 0x28        # 通信控制（关正常报文收发）
SID_WRITE_DID = 0x2E                    # 写数据标识符
SID_ROUTINE_CONTROL = 0x31              # 例程控制（跑内置自检/动作）
SID_REQUEST_DOWNLOAD = 0x34             # 请求下载（刷写第一步）
SID_TRANSFER_DATA = 0x36                # 传输数据（刷写）
SID_TRANSFER_EXIT = 0x37                # 退出传输
SID_TESTER_PRESENT = 0x3E               # 诊断仪在线保活
SID_CONTROL_DTC_SETTING = 0x85          # 控制 DTC 更新（暂停/恢复故障检测）
SID_IO_CONTROL = 0x2F                   # IO 控制（强制执行器动作）

# 正响应 = 请求 SID + 0x40
def positive_response_id(sid: int) -> int:
    return sid + 0x40


# ---------------------------------------------------------------------------
# 负响应码 NRC（Negative Response Code）—— ECU 说"不"的理由
# ---------------------------------------------------------------------------
NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceived-ResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}
# 最常用的三个，单独起别名方便测试断言
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_CONDITIONS_NOT_CORRECT = 0x22


def nrc_name(code: int) -> str:
    return NRC_NAMES.get(code, f"unknownNRC({code:#x})")


# ---------------------------------------------------------------------------
# 诊断子功能（Sub-function）
# ---------------------------------------------------------------------------
SESSION_DEFAULT = 0x01     # 默认会话：上电就在这，功能最少
SESSION_PROGRAMMING = 0x02 # 编程会话：刷写用
SESSION_EXTENDED = 0x03    # 扩展会话：开放大部分诊断功能（读 DTC 等）

RESET_HARD = 0x01          # 硬复位
RESET_KEY_OFF_ON = 0x02    # 钥匙关-开
RESET_SOFT = 0x03          # 软复位

SUBFUNC_SUPPORTED_BIT = 0x80  # 抑制正响应位：请求里置 1，ECU 不回复（静默执行）


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class UdsRequest:
    """一次 UDS 请求"""
    sid: int
    payload: bytes = b""          # sid 之后的参数
    suppress_response: bool = False  # 是否抑制正响应（sub-function 最高位置 1）

    def to_bytes(self) -> bytes:
        return bytes([self.sid]) + self.payload


@dataclass
class UdsResponse:
    """一次 UDS 响应（正/负）"""
    positive: bool
    sid: int                      # 正响应：SID+0x40；负响应：0x7F
    payload: bytes = b""          # 正响应：SID+0x40 之后的参数；负响应：[原SID, NRC]

    @property
    def nrc(self) -> Optional[int]:
        if self.positive:
            return None
        return self.payload[1]

    def describe(self) -> str:
        if self.positive:
            return f"正响应 SID={self.sid:#04x} payload={self.payload.hex()}"
        return f"负响应 NRC={self.nrc:#04x}({nrc_name(self.nrc)})"


# ---------------------------------------------------------------------------
# 编解码 helpers
# ---------------------------------------------------------------------------
def build_request(sid: int, payload: bytes = b"", suppress_response: bool = False) -> bytes:
    """构造请求字节流。suppress_response=True 时把 sub-function 的 0x80 位置 1。"""
    payload = bytearray(payload)
    if suppress_response and payload:
        payload[0] |= SUBFUNC_SUPPORTED_BIT
    return bytes([sid]) + bytes(payload)


def parse_response(data: bytes) -> UdsResponse:
    """解析 ECU 返回的数据（单帧形式，data 为去掉 PCI 字节后的内容）。"""
    if len(data) < 1:
        raise ValueError("空响应")
    if data[0] == 0x7F:
        if len(data) < 3:
            raise ValueError(f"负响应长度不足: {data.hex()}")
        return UdsResponse(positive=False, sid=0x7F, payload=bytes(data[1:]))
    return UdsResponse(positive=True, sid=data[0], payload=bytes(data[1:]))


def negative_response(sid: int, nrc: int) -> bytes:
    """构造负响应：7F <原SID> <NRC>"""
    return bytes([0x7F, sid, nrc])


def positive_response(sid: int, payload: bytes = b"") -> bytes:
    """构造正响应：<SID+0x40> <参数>"""
    return bytes([positive_response_id(sid)]) + payload


# ---------------------------------------------------------------------------
# ISO 15765-2 (CAN TP) 多帧传输 —— 状态机版，收发双方共用
# ---------------------------------------------------------------------------
@dataclass
class IsoTpSender:
    """把一段长数据拆成 FF + N个CF 发出去。

    用法::
        frames = IsoTpSender.split_frames(long_data)
        # 然后按顺序、配合接收方的流控帧发出去（简化版：直接连着发）
    """
    block_size: int = 8        # 流控帧里的块大小 BS（一次允许多少个 CF）
    st_min_ms: int = 0         # 帧间最小间隔（毫秒）

    @staticmethod
    def split_frames(data: bytes) -> List[bytes]:
        """把 ≤4095 字节的数据拆成 ISO-TP 帧序列（每帧是 PCI+数据的完整 CAN 载荷）。"""
        if len(data) <= 7:
            return [bytes([len(data)]) + data]           # 单帧 SF
        frames: List[bytes] = []
        # 首帧 FF: [1x][len_lo] + 数据前 6 字节
        total = len(data)
        ff = bytes([0x10 | ((total >> 8) & 0x0F), total & 0xFF]) + data[:6]
        frames.append(ff)
        rest = data[6:]
        seq = 1
        while rest:
            chunk = rest[:7]
            rest = rest[7:]
            frames.append(bytes([0x20 | (seq & 0x0F)]) + chunk)  # 连续帧 CF
            seq = (seq + 1) & 0x0F
        return frames


@dataclass
class IsoTpReceiver:
    """把收到的 FF + CF 重新拼成完整数据。

    用法::
        r = IsoTpReceiver()
        for frame in frames:            # 每来一帧 CAN 数据（含 PCI），喂进来
            done, data = r.feed(frame)
            if done: ...
    """
    _buf: bytearray = field(default_factory=bytearray)
    _expect_seq: int = 1
    _total: Optional[int] = None
    _started: bool = False

    def feed(self, frame: bytes) -> Tuple[bool, Optional[bytes]]:
        """喂入一帧（含 PCI 字节）。返回 (是否收完整, 完整数据 or None)。"""
        if not frame:
            return False, None
        pci = frame[0]
        if pci & 0xF0 == 0x00:                       # 单帧 SF
            length = pci & 0x0F
            return True, bytes(frame[1 : 1 + length])
        if pci & 0xF0 in (0x10,):                    # 首帧 FF
            self._total = ((pci & 0x0F) << 8) | frame[1]
            self._buf = bytearray(frame[2:8])
            self._expect_seq = 1
            self._started = True
            return False, None
        if pci & 0xF0 == 0x20:                       # 连续帧 CF
            if not self._started:
                raise ValueError("收到 CF 但还没收到 FF")
            seq = pci & 0x0F
            if seq != self._expect_seq:
                raise ValueError(f"CF 序号错乱: 期望 {self._expect_seq} 实际 {seq}")
            self._buf.extend(frame[1:8])
            self._expect_seq = (self._expect_seq + 1) & 0x0F
            if self._total is not None and len(self._buf) >= self._total:
                return True, bytes(self._buf[: self._total])
            return False, None
        raise ValueError(f"未知 PCI 字节 {pci:#04x}")


def build_flow_control(block_size: int = 8, st_min_ms: int = 0) -> bytes:
    """构造流控帧 FC: 30 <BS> <STmin>"""
    return bytes([0x30, block_size, st_min_ms])


# ---------------------------------------------------------------------------
# DTC 工具（3 字节 BCD 故障码 ↔ 字符串）
# ---------------------------------------------------------------------------
def dtc_to_string(b1: int, b2: int, b3: int) -> str:
    """3 字节 DTC → 字符串，如 0x01 0x16 0x00 → 'P0116'。

    编码规则（SAE J2012 / ISO 15031-6）：
        第一个字节 bit7-6：首字母 00=P 01=C 10=B 11=U
        第一个字节 bit5-4：数字1（BCD）
        第一个字节 bit3-0：数字2（BCD）
        第二个字节 bit7-4：数字3（BCD）
        第二个字节 bit3-0：数字4（BCD）
        第三个字节：保留（全 0）
    例：P0116 → byte1=(00<<6)|(0<<4)|1=0x01, byte2=(1<<4)|6=0x16 → 01 16 00
    """
    first = (b1 >> 6) & 0x03
    d1 = (b1 >> 4) & 0x03
    d2 = b1 & 0x0F
    d3 = (b2 >> 4) & 0x0F
    d4 = b2 & 0x0F
    prefix = {0: "P", 1: "C", 2: "B", 3: "U"}[first]
    return prefix + f"{d1:X}{d2:X}{d3:X}{d4:X}"


def string_to_dtc(s: str) -> Tuple[int, int, int]:
    """'P0116' → (0x01, 0x16, 0x00)"""
    prefix_map = {"P": 0, "C": 1, "B": 2, "U": 3}
    p = prefix_map[s[0].upper()]
    digits = s[1:].upper()
    if len(digits) != 4 or not all(c in "0123456789ABCDEF" for c in digits):
        raise ValueError(f"DTC 后 4 位必须是十六进制数字: {s}")
    b1 = (p << 6) | ((int(digits[0], 16) & 0x03) << 4) | (int(digits[1], 16) & 0x0F)
    b2 = ((int(digits[2], 16) & 0x0F) << 4) | (int(digits[3], 16) & 0x0F)
    return (b1, b2, 0x00)


if __name__ == "__main__":
    # 自测：所有核心函数跑一遍
    print("== UDS 编解码自测 ==")
    print("请求 22 F1 87 →", build_request(0x22, bytes([0xF1, 0x87])).hex())
    print("负响应 7F 22 31 →", negative_response(0x22, 0x31).hex(),
          "=", nrc_name(0x31))
    resp = parse_response(bytes([0x62, 0xF1, 0x87, 0x41, 0x42]))
    print("解析正响应:", resp.describe())
    resp2 = parse_response(negative_response(0x22, 0x33))
    print("解析负响应:", resp2.describe())

    print("\n== ISO-TP 多帧自测 ==")
    long_data = bytes(range(20))          # 20 字节数据 → 1 FF + 2 CF
    frames = IsoTpSender.split_frames(long_data)
    for f in frames:
        print("  帧:", f.hex())
    rx = IsoTpReceiver()
    out = None
    for f in frames:
        done, out = rx.feed(f)
        if done:
            break
    assert out == long_data, "ISO-TP 重组失败!"
    print("重组还原成功:", out.hex())

    print("\n== DTC 编码自测 ==")
    print("P0116 →", tuple(hex(x) for x in string_to_dtc("P0116")))
    print("0x011600 →", dtc_to_string(*string_to_dtc("P0116")))
