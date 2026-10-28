"""pytest 共享配置：路径注入 + 公共 fixture。

pytest 与 Robot Framework 的分工（详见本目录 README）：
    - pytest：**纯单元/逻辑测试** —— 毫秒级、不起仿真进程、不碰 UDP 总线；
    - Robot Framework：**系统级/仿真集成测试** —— 起 ECU/仪表/车机进程、走虚拟 CAN。

本文件只做三件事：
    1. 把项目根目录和 simulators/ 加进 sys.path（等价于根脚本里的
       `sys.path.insert(0, ROOT / "simulators")`，但 pytest 从 tools/ 目录
       收集测试，需要显式补上）；
    2. 提供 dbc_path / requirements_path / project_root 等 fixture，
       避免每个测试文件各写一遍路径；
    3. 不 import 任何会起进程的模块（ecu_sim 等）——保持单元测试纯净。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tools/pytest_style/conftest.py → parents[0]=pytest_style, [1]=tools, [2]=项目根
ROOT = Path(__file__).resolve().parents[2]
SIMULATORS = ROOT / "simulators"

for _p in (str(ROOT), str(SIMULATORS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_report_header(config: pytest.Config) -> str:
    """在 pytest 输出的头部标出项目根，方便排错（被 conftest 收集时执行）。"""
    return f"robot_framework pytest-style unit tests | project root: {ROOT}"


@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录（robot_framework/）。"""
    return ROOT


@pytest.fixture(scope="session")
def simulators_dir() -> Path:
    """仿真器目录（simulators/），uds_lib / dtc_manager 都在这里。"""
    return SIMULATORS


@pytest.fixture(scope="session")
def dbc_path() -> Path:
    """整车信号字典（5 条报文 / 35 个信号）。"""
    return SIMULATORS / "dbc" / "vehicle.dbc"


@pytest.fixture(scope="session")
def requirements_path() -> Path:
    """需求清单 YAML（需求追溯的源头）。"""
    return ROOT / "requirements" / "requirements.yaml"
