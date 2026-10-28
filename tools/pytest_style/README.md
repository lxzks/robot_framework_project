# tools/pytest_style —— pytest 单元测试（与 Robot Framework 互补）

> 一句话：**pytest 负责"单元/逻辑"测试，Robot Framework 负责"系统级/仿真"测试。**
> 本目录是 [docs/33 · 工程工具链实战](../../docs/33_工程工具链实战.md) 的配套代码。

---

## 1. 为什么要有这一层？

本项目的系统测试（`tests/robot/` + `scenarios/`，共 165 个用例）用 Robot Framework 写，
它们会**拉起 ECU/仪表/车机仿真进程、走 UDP 组播虚拟总线**——这正是系统测试该干的事，
但代价是：一条用例动辄几百毫秒到几秒，全量约 5 分钟。

写代码时更常发生的是另一类问题：

| 问题类型 | 例子 | 用哪个工具 |
|---|---|---|
| **纯函数算错了** | DTC 的 BCD 编解码、ISO-TP 拆帧/重组、pairwise 组合 | ✅ pytest（毫秒级） |
| **数据文件写坏了** | requirements.yaml 需求 ID 重复 / 格式错 / 字段缺失 | ✅ pytest |
| **模块间集成/协议行为** | 真 ECU 收到 0x22 返回什么、故障注入后灯亮不亮 | 🤖 Robot Framework |
| **端到端业务流程** | 车机点歌 → CAN 广播 → 仪表同步显示 | 🤖 Robot Framework |

**分工不是重复**：pytest 层跑得极快，适合 pre-commit / CI 第一道门禁；
RF 层覆盖真实系统语义，适合全量回归。两层都过，才说明"代码对 + 系统对"。

---

## 2. 快速运行

```bash
# 在项目根目录执行（使用项目 .venv 的解释器）
.\.venv\Scripts\python.exe -m pytest tools/pytest_style -q
```

本机实测（2026-10，Python 3.14.5 / pytest 9.2.0）：

```
30 passed, 1 warning in 0.45s
```

> ⚠️ 那个 warning 是本机沙箱拒绝写 `.pytest_cache` 导致的缓存告警，与测试结果无关；
> CI（Linux）不会出现。pytest 不在 `requirements.txt` 里，CI 步骤里单独 `pip install pytest`。
> 本机若不想看到缓存告警，可加 `-p no:cacheprovider`：
> `.\.venv\Scripts\python.exe -m pytest tools/pytest_style -q -p no:cacheprovider`

---

## 3. 目录与覆盖内容

```
tools/pytest_style/
├── conftest.py                    # sys.path 注入（项目根 + simulators/）+ 公共 fixture
├── test_uds_lib.py                # DTC 编解码往返、ISO-TP 拆帧/重组（7/20/64B）、负响应格式
├── test_dtc_manager.py            # DTC 状态机 pending→confirmed→aging→clear（纯内存）
├── test_requirements_yaml.py      # 需求清单可解析、ID 唯一/格式/字段/枚举值
├── test_generate_testcases.py     # DBC 解析（5 报文/35 信号）、BVA 点、pairwise 两两覆盖
└── README.md                      # 本文件
```

对应关系（pytest ↔ RF）：

| pytest 文件 | 测什么 | RF 侧的等价/补充 |
|---|---|---|
| `test_uds_lib.py` | UDS 编解码与 CAN TP 纯函数 | `tests/robot/02_uds_diagnostic.robot`（真实总线收发） |
| `test_dtc_manager.py` | DTC 状态机内存模型 | `tests/robot/03_dtc_workflow.robot` + `scenarios/04` |
| `test_requirements_yaml.py` | 需求追溯源头的**数据质量** | `test_spec_check.py` / `trace_matrix.py` 的前置保障 |
| `test_generate_testcases.py` | 用例生成器的纯逻辑 | CI 里 `generate_testcases.py` + generated 套件 |

---

## 4. 设计原则（新增测试时请遵守）

1. **不起仿真进程**：不 import `ecu_sim`/`dashboard`，不碰 UDP 组播（30000 端口）；
   `test_generate_testcases.py` 只调纯函数，**不写** `tests/robot/generated/` 文件。
2. **毫秒级**：单文件 < 1s；本目录整体 < 5s（CI 作为快速失败门禁）。
3. **断言具体**：断言字节值/状态位/精确集合，不写"范围看起来对"的恒真断言。
4. **路径用 fixture**：`dbc_path` / `requirements_path` / `project_root` 由 `conftest.py` 提供。
5. **不加新依赖**：只依赖 pytest + 项目已有依赖（cantools/PyYAML）。

---

## 5. CI 中的位置

`.github/workflows/ci.yml` 的 test job：

1. 安装依赖
2. **`python -m pytest tools/pytest_style -q`** ← 本目录（秒级，失败快速反馈）
3. `test_spec_check.py` → `generate_testcases.py` → `run_tests.py --report`（RF 全量 ~5 分钟）
4. 上传 `results/` 产物

并且 test job 跑 Python 版本矩阵 `['3.12', '3.14']`（`fail-fast: false`），
确保"本地能跑（3.14）"和"CI 主版本（3.12）"都绿。
