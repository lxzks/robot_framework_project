"""uds_lib 纯单元测试：DTC 编解码 / ISO-TP 拆帧与重组 / 负响应格式。

对照关系（pytest ↔ Robot Framework）：
    本文件测的是 uds_lib.py 里的**纯函数**——给输入、断言输出，不启动任何进程；
    对应的系统级验证在 tests/robot/02_uds_diagnostic.robot（真实走 CAN 总线）。

设计原则：
    1. 只 import 纯函数（uds_lib 没有模块级副作用），毫秒级完成；
    2. 断言具体字节值，不用"看起来差不多"的模糊断言（避免恒真测试）；
    3. 异常路径也要测（非法 DTC、ISO-TP 序号错乱）。
"""

from __future__ import annotations

import pytest

import uds_lib


# ---------------------------------------------------------------------------
# DTC ↔ 3 字节编码（SAE J2012 的 BCD 规则）
# ---------------------------------------------------------------------------
def test_dtc_known_encoding() -> None:
    """P0116 是教程里出现最多的故障码，编码必须是 01 16 00。"""
    assert uds_lib.string_to_dtc("P0116") == (0x01, 0x16, 0x00)
    assert uds_lib.dtc_to_string(0x01, 0x16, 0x00) == "P0116"


@pytest.mark.parametrize("code", ["P0116", "C1234", "B0001", "U3FFF"])
def test_dtc_string_bytes_roundtrip(code: str) -> None:
    """字符串 → 3 字节 → 字符串 往返必须相等（覆盖 P/C/B/U 四种前缀）。"""
    b1, b2, b3 = uds_lib.string_to_dtc(code)
    assert (b1, b2, b3) == uds_lib.string_to_dtc(code)
    assert uds_lib.dtc_to_string(b1, b2, b3) == code
    # 第三个字节是保留字节，恒为 0
    assert b3 == 0x00


def test_dtc_invalid_input_raises() -> None:
    """非法 DTC 必须报错，而不是安静地返回垃圾数据。"""
    with pytest.raises((ValueError, KeyError)):
        uds_lib.string_to_dtc("X1234")      # 非法前缀
    with pytest.raises(ValueError):
        uds_lib.string_to_dtc("P011")       # 位数不足
    with pytest.raises(ValueError):
        uds_lib.string_to_dtc("PZZZZ")      # 非十六进制


# ---------------------------------------------------------------------------
# 负响应格式：7F <原SID> <NRC>
# ---------------------------------------------------------------------------
def test_negative_response_format() -> None:
    raw = uds_lib.negative_response(0x22, 0x31)
    assert raw == bytes([0x7F, 0x22, 0x31])
    resp = uds_lib.parse_response(raw)
    assert resp.positive is False
    assert resp.sid == 0x7F
    assert resp.nrc == 0x31
    assert "NRC=0x31" in resp.describe()


def test_parse_positive_response() -> None:
    resp = uds_lib.parse_response(bytes([0x62, 0xF1, 0x87]))
    assert resp.positive is True
    assert resp.sid == 0x62
    assert resp.nrc is None


# ---------------------------------------------------------------------------
# ISO-TP（ISO 15765-2）：拆帧 ↔ 重组往返
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("size", [7, 20, 64])
def test_isotp_split_and_reassemble(size: int) -> None:
    """7 字节单帧、20 字节（1 FF + 2 CF）、64 字节（1 FF + 9 CF）都要能往返。"""
    data = bytes(range(size))
    frames = uds_lib.IsoTpSender.split_frames(data)

    receiver = uds_lib.IsoTpReceiver()
    reassembled = None
    for frame in frames:
        done, payload = receiver.feed(frame)
        if done:
            reassembled = payload
            break

    assert reassembled == data
    # 帧的 PCI 类型断言：≤7 字节走 SF，>7 字节走 FF
    assert (frames[0][0] >> 4 == 0x1) if size > 7 else (frames[0][0] == size)


def test_isotp_sequence_error_raises() -> None:
    """连续帧序号错乱必须报错（对应真实 CAN TP 的丢帧/乱序保护）。"""
    data = bytes(range(20))
    frames = uds_lib.IsoTpSender.split_frames(data)
    receiver = uds_lib.IsoTpReceiver()
    receiver.feed(frames[0])                     # FF
    wrong_seq_cf = bytes([0x22]) + b"\x00" * 7   # 期望 0x21，实际 0x22
    with pytest.raises(ValueError):
        receiver.feed(wrong_seq_cf)


def test_flow_control_frame() -> None:
    assert uds_lib.build_flow_control(8, 0) == bytes([0x30, 0x08, 0x00])
