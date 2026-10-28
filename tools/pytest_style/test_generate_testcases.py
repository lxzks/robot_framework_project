"""generate_testcases.py 纯逻辑测试：DBC 解析、BVA 测试点、pairwise 组合。

注意：这里**不写任何文件**（不调用 main()），只测纯函数，避免污染
tests/robot/generated/；生成文件的端到端验证由 CI 里的 generate_testcases.py
+ Robot Framework 套件完成。
"""

from __future__ import annotations

import itertools

import pytest

import generate_testcases


@pytest.fixture(scope="module")
def signals(dbc_path):
    """整个 DBC 的信号模型（缓存一次，模块内复用）。"""
    return generate_testcases.collect_signals(dbc_path)


def test_dbc_loads_all_messages_and_signals(signals: list[dict]) -> None:
    """DBC 解析结果：5 条报文 / 35 个信号（与项目文档一致）。"""
    messages = {s["message"] for s in signals}
    assert messages == {
        "EngineData", "ClusterData", "TelltaleStatus", "MediaPhoneData", "NaviHvacData",
    }
    assert len(signals) == 35


def test_signal_model_has_required_keys(signals: list[dict]) -> None:
    """信号模型字段齐全（生成器消费这些字段生成用例）。"""
    required = {
        "message", "message_id", "name", "minimum", "maximum",
        "scale", "offset", "is_float", "choices", "length", "senders",
    }
    for sig in signals:
        assert required <= set(sig), f"{sig.get('name')} 缺字段: {required - set(sig)}"


def test_numeric_bva_points(signals: list[dict]) -> None:
    """数值信号 BVA：含最小值/最大值 + 越界点，且物理范围单调有效。"""
    vehicle_speed = next(s for s in signals if s["name"] == "VehicleSpeed")
    points = generate_testcases.numeric_test_points(vehicle_speed)
    descriptions = [desc for desc, _ in points]

    assert len(points) >= 5
    assert "最小值" in descriptions and "最大值" in descriptions
    assert any("越界" in desc for desc in descriptions), "BVA 必须包含越界点"
    # 越界点必须真的越界（lo-step < lo，hi+step > hi）
    values = dict(points)
    lo, hi = vehicle_speed["minimum"], vehicle_speed["maximum"]
    assert values["越界(低于最小值)"] < lo
    assert values["越界(高于最大值)"] > hi


def test_enum_equivalence_points(signals: list[dict]) -> None:
    """枚举信号 → 每个取值一条等价类（GearPosition 有 6 档）。"""
    gear = next(s for s in signals if s["name"] == "GearPosition")
    assert gear["choices"], "GearPosition 应为枚举信号"
    points = generate_testcases.enum_test_points(gear)

    assert len(points) == len(gear["choices"]) == 6
    assert [value for _, value in points] == sorted(gear["choices"])


def test_pairwise_covers_all_pairs() -> None:
    """pairwise 生成的行集必须覆盖所有"两两取值组合"，且行数少于全组合。"""
    params = [("A", [0, 1, 2]), ("B", [0, 1]), ("C", [0, 1])]
    rows = generate_testcases.pairwise(params)

    assert 0 < len(rows) < 3 * 2 * 2, "应比全组合 12 行更少（pairwise 的意义）"

    for p1, p2 in itertools.combinations([name for name, _ in params], 2):
        values = dict(params)
        for v1 in values[p1]:
            for v2 in values[p2]:
                assert any(
                    row[p1] == v1 and row[p2] == v2 for row in rows
                ), f"未覆盖组合 {p1}={v1}, {p2}={v2}"


def test_pick_pairwise_signals_same_message(signals: list[dict]) -> None:
    """pairwise 只能选同一报文内的数值信号（跨报文组合在总线上无法一次编码）。"""
    target_msg, picked = generate_testcases.pick_pairwise_signals(signals, 3)

    assert target_msg == "EngineData"          # ECU 发出的数值信号最多的报文
    assert 2 <= len(picked) <= 3
    for sig in picked:
        assert sig["message"] == target_msg
        assert sig["length"] > 1               # 位信号不做 pairwise
        assert not sig["choices"]              # 枚举信号不参与


def test_generate_robot_returns_valid_robot_text(signals: list[dict]) -> None:
    """生成器拼出的 .robot 文本包含关键结构（不落盘，只检查字符串）。"""
    content = generate_testcases.generate_robot(signals, pairwise_count=3)

    assert "*** Settings ***" in content
    assert "*** Test Cases ***" in content
    assert "*** Keywords ***" in content
    assert "[Template]" in content
    assert "REQ-CAN-011" in content            # 需求追溯 Tag
    assert "Out Of Range Value Should Be Sendable" in content
