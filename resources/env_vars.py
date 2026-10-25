"""
env_vars.py — RF 变量文件（把环境信息暴露给测试）
====================================================

解决的问题：测试要通过 Process 库启动仿真子进程（ecu_sim / dashboard /
android_gui），必须知道"用哪个 Python 解释器"。

    - 硬编码路径 → 换机器、上 CI 必挂；
    - 只用环境变量 → 直接 `python -m robot` 跑时取不到，落到 PATH 里的
      python（可能没有依赖）→ 子进程起不来、所有用例失败（真实踩过）。

**为什么用 Variables 文件而不是 Library**：库的模块级常量要通过
`${库名.变量名}` 访问（如 ${EnvLib.PYTHON_EXE}）；而 RF 的 **Variables
文件**里的模块级常量直接成为全局变量，可以写 `${PYTHON_EXE}`。
于是它永远等于"当前正在跑测试的解释器"，无论谁怎么启动 robot 都对。

用法（common.resource 里）：
    Variables        env_vars.py
    ${PYTHON}        ${PYTHON_EXE}
"""

from __future__ import annotations

import sys
from pathlib import Path

# 当前解释器（运行 robot 的那个 python）
PYTHON_EXE = sys.executable
# 项目根目录（供用例拼接路径）
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
