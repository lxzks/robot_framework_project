"""requirements.yaml 数据质量测试：可解析、ID 唯一、格式正确、字段齐全。

为什么这类"数据文件"也要写测试？
    需求清单是需求追溯（trace_matrix.py）的源头。ID 重复/格式写错/字段缺失
    会让追溯矩阵**静默失真**（比如出现两行同 ID、或某条需求没有 source）。
    这类检查几毫秒就能跑完，属于"性价比最高的防回归"。

不依赖仿真进程，也不需要 output.xml。
"""

from __future__ import annotations

import re

import pytest
import yaml

# ID 约定：REQ-<域，大写字母>-<三位序号>，例如 REQ-CAN-001 / REQ-SEC-005
ID_PATTERN = re.compile(r"^REQ-[A-Z]+-\d{3}$")
REQUIRED_FIELDS = ("id", "title", "description", "source", "priority", "method", "verify")


def _load(requirements_path):
    return yaml.safe_load(requirements_path.read_text(encoding="utf-8"))


def _requirements(requirements_path):
    return _load(requirements_path)["requirements"]


def test_yaml_parses_and_has_requirements(requirements_path) -> None:
    """YAML 必须可解析，且顶层结构里有非空的 requirements 列表。"""
    data = _load(requirements_path)
    assert isinstance(data, dict)
    assert data.get("project"), "缺少 project 字段"
    reqs = data.get("requirements")
    assert isinstance(reqs, list) and reqs, "requirements 不能为空"


def test_yaml_and_regex_counts_agree(requirements_path) -> None:
    """两套独立口径（yaml 解析 vs 正则数 `- id:` 行）必须一致——防解析/格式异常。"""
    reqs = _requirements(requirements_path)
    text = requirements_path.read_text(encoding="utf-8")
    regex_ids = re.findall(r"^\s*-\s*id:\s*(\S+)", text, re.M)

    assert [r["id"] for r in reqs] == regex_ids


def test_requirement_ids_unique(requirements_path) -> None:
    """需求 ID 必须唯一（重复会导致追溯矩阵把两条需求合成一条）。"""
    ids = [r["id"] for r in _requirements(requirements_path)]
    duplicates = {rid for rid in ids if ids.count(rid) > 1}
    assert duplicates == set()


def test_requirement_id_format(requirements_path) -> None:
    """所有 ID 必须符合 REQ-<域>-<三位> 约定（trace_matrix 用正则解析 Tag）。"""
    bad = [r["id"] for r in _requirements(requirements_path)
           if not ID_PATTERN.match(str(r["id"]))]
    assert bad == []


def test_required_fields_present(requirements_path) -> None:
    """每条需求都必须有可追溯字段（source 用于审核，verify 用于验证方法）。"""
    for req in _requirements(requirements_path):
        missing = [f for f in REQUIRED_FIELDS if not req.get(f)]
        assert not missing, f"{req.get('id')} 缺少字段: {missing}"


def test_priority_and_method_enums(requirements_path) -> None:
    """priority / method 只允许约定值（ISO 术语：Test/Analysis/Inspection/Demonstration）。"""
    allowed_priority = {"High", "Medium", "Low"}
    allowed_method = {"Test", "Analysis", "Inspection", "Demonstration"}

    for req in _requirements(requirements_path):
        assert req["priority"] in allowed_priority, f"{req['id']} priority 非法"
        assert req["method"] in allowed_method, f"{req['id']} method 非法"
