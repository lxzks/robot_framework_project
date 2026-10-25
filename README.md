# 车载自动化测试学习项目（robot_framework）

纯软件仿真的车载自动化测试系统：**模拟动力域 ECU + 数字仪表盘 + 安卓车机**，基于 **Robot Framework** 编写 **116 个**自动化测试用例（主套件 62 + 场景 54），覆盖 CAN 信号、UDS 诊断、DTC 故障管理、HMI 断言、ADB 设备操作、故障注入与电源管理。

> 为零基础学习车载自动化测试而建。详细教程见 [`docs/`](docs/)（共 22 篇，从 [`00_学习路线图.md`](docs/00_学习路线图.md) 的 4 周计划开始）。
>
> 🚀 **完全零基础（没开过车、没写过测试）？先走** → [`start_here/`](start_here/README.md)（7 天，每天 1-2 小时）
> 🐍 **想练 Python？** → [`python_practice/`](python_practice/README.md)（8 题 + 自动判分）
> 📄 **要投简历/面试？** → [`deliverables/project_brief.md`](deliverables/project_brief.md)（项目说明书 + 简历描述）
> 🎤 **准备面试追问？** → [`docs/12 章 12.3 节`](docs/12_术语表与面试高频题.md)（针对本项目的 20 道追问）

---

## 系统组成

```
┌─────────────────────────────────────────────────────────────┐
│  Robot Framework 测试层（tests/robot/ 7 套件 62 用例           │
│    01 CAN 信号 │ 02 UDS 诊断 │ 03 DTC+HMI 闭环 │ 04 ADB      │
│    05 座舱 IVI 双屏一致性                                    │
├─────────────────────────────────────────────────────────────┤
│  自定义关键字库（libraries/）                                  │
│    VehicleBusLibrary │ DiagnosticLibrary │                  │
│    DashboardLibrary  │ AdbLibrary（含车机状态广播）           │
├─────────────────────────────────────────────────────────────┤
│  车辆仿真（simulators/，多进程 + UDP 组播虚拟 CAN 总线）        │
│    ecu_sim.py       动力域 ECU（周期报文 + 14 个 UDS 服务      │
│                     + DTC 状态机 + 交互控制台）               │
│    dashboard.py     仪表入口：GUI 多界面 / --headless 双模式   │
│    cluster_gui.py   多界面座舱仪表（启动画面+7 界面+转向灯闪烁）│
│    android_gui.py   安卓车机中控（GUI 6 界面 / --headless）    │
│    ivi_backend.py   车机 IVI 后端（无 Qt：状态+CAN 广播）      │
│    uds_client.py    UDS 诊断仪命令行（模拟 CANoe 诊断控制台）   │
│    adb_sim.py       安卓车机模拟器（adb 命令 mock，GUI 共享状态）│
│    uds_lib.py       UDS 编解码 + ISO-TP 多帧                  │
│    dtc_manager.py   DTC 状态机（pending/confirmed/aging）      │
│    bus.py           python-can 虚拟总线封装                   │
│    ivi_data.py      歌单/联系人/街道字典（CAN 传索引 GUI 查表）│
│    dbc/vehicle.dbc  整车信号字典（5 条报文）                   │
│    test_*_e2e.py    三个端到端联调自测（UDS/仪表/IVI）        │
│    test_android_gui_click.py  GUI 点击全覆盖自测（27 项断言）  │
├─────────────────────────────────────────────────────────────┤
│  一键启动（项目根）                                            │
│    start_cockpit.py   拉起 ECU+仪表+车机 三个进程             │
│    start_cockpit.bat  Windows 双击入口                       │
└─────────────────────────────────────────────────────────────┘
```

### 开发自测（改代码后跑一遍）

```bash
python run_tests.py                 # 统一入口：跑全部 116 个用例
python run_tests.py --suite 06      # 只跑刷写套件
python run_tests.py --generate --report   # 生成用例 → 跑 → 出报告+覆盖率

python simulators/test_uds_e2e.py           # UDS 诊断全流程（9 步）
python simulators/test_cluster_e2e.py       # 仪表+ADB 整链
python simulators/test_ivi_e2e.py           # 座舱 IVI 联动
python simulators/test_android_gui_click.py # 车机 GUI 点击全覆盖（27 项）
```

## 测试工程化工具链

| 工具 | 作用 | 用法 |
|---|---|---|
| `run_tests.py` | 统一入口（本地/CI 一致，自动注入解释器） | `python run_tests.py --report` |
| `generate_testcases.py` | **用例自动生成**：DBC → 边界值(BVA) + 枚举等价类 + pairwise 组合 | `python generate_testcases.py` |
| `generate_report.py` | **交付报告**：RF 结果 + 总线 Trace 分析（周期/抖动）+ 产物清单 | `python generate_report.py` |
| `coverage_report.py` | **覆盖率**：UDS 服务 / 信号 / DTC 三维 | `python coverage_report.py --html` |
| `trace_matrix.py` | **需求追溯矩阵**：需求↔用例↔结果、需求覆盖率、孤儿用例 | `python trace_matrix.py` |
| `link_requirements.py` | 给用例打需求标签（建立追溯关系，支持 `--dry-run`） | `python link_requirements.py --dry-run` |
| `test_spec_check.py` | **测试规范一致性校验**（需求 ID 与自动化引用是否存在，CI 门禁） | `python test_spec_check.py` |
| `simulators/trace_tools.py` | **Trace 导出**：ASC（CANoe 可直接打开）/ BLF，含回读校验 | RF 关键字 `Export Bus Trace` |
| `.github/workflows/ci.yml` | CI：push/PR 自动跑 + nightly 凌晨 2 点完整跑并归档 | 推送到 GitHub 即生效 |

**产物示例**：`results/test_summary.html`（交付报告，含需求覆盖率）、
`results/coverage.html`（覆盖率）、`results/trace_matrix.html`（需求追溯矩阵）、
`results/bus_trace.asc`（CANoe 兼容 Trace，可直接用 CANoe/CANalyzer 打开分析）。

## 快速开始

```bash
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2. 一键启动完整座舱（三个进程，一条命令）
python start_cockpit.py                                # 默认工况
python start_cockpit.py --speed 60 --coolant 125       # 车速60 + 水温故障
# Windows 也可以双击 start_cockpit.bat

# 3. 或分终端手动启动（各自独立窗口）
python simulators/ecu_sim.py --speed 60 --coolant 125  # 终端1：车辆
python simulators/dashboard.py                         # 终端2：座舱仪表（7 界面）
python simulators/android_gui.py                       # 终端3：安卓车机（6 界面）

# 4. 一键自动化（自动拉起环境 → 116 个用例 → 自动回收）
python -m robot --outputdir results tests/robot/
```

预期输出：`37 tests, 37 passed, 0 failed`。

## 三屏联动（本项目最好玩的部分）

三个进程通过虚拟 CAN 总线组成一个**迷你座舱**， GUI 操作实时联动：

| 你在安卓车机上 | 座舱仪表同步 |
|---|---|
| 点《晴天》播放 | “媒体” tab 显示歌名/歌手/进度/音量 |
| 点“开始导航” | “导航” tab 显示转向箭头/剩余距离/街道/ETA |
| 调空调温度 | 主页显示室外温度，“车辆信息”页显示空调状态 |
| 打电话给联系人 | “电话” tab 显示来电卡片 |
| （ECU 水温超 120℃） | 水温报警灯亮 + DTC 记录（诊断仪可读） |

反过来，`adb shell am start -n com.vehicle.media/.MainActivity` 也会让车机 GUI 自己跳到媒体界面——**命令行和界面看到的是同一台设备**。

## 测试覆盖

| 套件 | 用例数 | 覆盖内容 |
|---|---|---|
| 01_can_signals | 7 | 周期报文完整性、信号范围、信号联动、档位逻辑、里程累积、警告灯状态 |
| 02_uds_diagnostic | 11 | 会话控制、ISO-TP 多帧、安全访问（种子/密钥/锁定）、DID 读写、NRC 错误处理、例程、抑制响应 |
| 03_dtc_workflow | 7 | 故障注入→pending→confirmed→点灯→快照→清除→恢复 全生命周期 |
| 04_adb_android | 6 | 设备检测、系统属性、安装/启动/卸载、输入模拟、焦点断言、电量查询 |
| 05_ivi_cockpit | 6 | 座舱双屏一致：车机状态→CAN 0x400/0x500→仪表显示（媒体/导航/空调/trip） |
| 06_uds_flash_conformance | 11 | **UDS 刷写流程**（0x34/0x36/0x37 + 完整性校验）+ **协议一致性**（权限模型/错误处理/时序） |
| generated（自动生成） | 11 | 由 DBC 自动生成：31 个数值信号的边界值 + 枚举等价类 + pairwise 组合 |

## 座舱双屏联动

```
安卓车机（android_gui / ivi_backend）
    │  点应用/播歌/开导航/调空调 → 写 adb_device_state.json
    │  （adb 命令行 am start 改焦点，GUI 轮询同步切界面）
    ▼
CAN 广播 0x400（媒体/电话/空调）+ 0x500（导航/空调细节）
    ▼
仪表盘（dashboard / cluster_gui）显示同样的信息
```

两块屏共用同一数据源（状态文件 + CAN），测试断言"车机操作 → 仪表显示"链路 —— 这就是量产座舱域**多屏一致性测试**的核心思路。

## 目录结构

```
├── docs/             # 22 篇教程（00 路线图 → 21 Python 补强）
├── simulators/       # 车辆仿真（车辆侧进程）
├── libraries/        # RF 自定义关键字库（4 个）
├── tests/robot/      # RF 测试套件（4 个）
├── resources/        # 公共资源（环境生命周期管理）
└── results/          # RF 测试输出（output.xml / log.html / report.html）
```

## 关键设计

- **多进程仿真**：ECU/仪表/诊断仪是独立进程，用 python-can 的 UDP 组播接口组成"虚拟 CAN 总线"（`233.255.0.1:30000`），进程间通信语义与真实总线广播一致；
- **HMI 状态外置**：headless 仪表盘把显示模型序列化为 JSON，测试断言数据而非像素；
- **协议完整实现**：ISO-TP 多帧（FF/CF/FC 状态机）、DTC 八状态位、S3 会话定时器、MIL 灯 latch 策略；
- **环境即代码**：套件 setup/teardown 自动拉起/回收仿真进程，一条命令完成全部测试，CI 友好。

## 教程导航

| 文档 | 内容 |
|---|---|
| [00 学习路线图](docs/00_学习路线图.md) | **4 周冲刺计划、五层能力模型、22 篇导航** |
| [01 车载自动化全景](docs/01_车载自动化全景.md) | 伟世通业务、仪表盘原理、测试类型 |
| [02 车载网络基础](docs/02_车载网络基础.md) | CAN 物理层/位仲裁/DBC、LIN/DoIP |
| [03 UDS 协议详解](docs/03_UDS协议详解.md) | 14 个服务、NRC、会话、ISO-TP |
| [04 DTC 详解](docs/04_DTC详解.md) | 故障码结构、状态位、生命周期 |
| [05 Robot Framework](docs/05_RobotFramework从零到实战.md) | 语法、数据驱动、自定义库 |
| [06 环境搭建](docs/06_环境搭建与项目结构.md) | 架构、文件地图、10 个排坑记录 |
| [07-09 实战精讲](docs/07_CAN仿真测试实战.md) | CAN/UDS/HMI 套件逐行讲解 |
| [10 ADB/Android](docs/10_ADB与Android车载基础.md) | adb 命令族、AAOS、安卓车机 GUI |
| [11 综合实战](docs/11_综合实战项目.md) | 4 个递进任务、面试话术 |
| [12 术语与面试](docs/12_术语表与面试高频题.md) | 200+ 术语、33 道高频题 |
| **[13 测试工程化与 CI](docs/13_测试工程化与CI.md)** | Trace 归档/交付报告/用例生成/覆盖率/CI |
| **[14 从零写第一个车载用例](docs/14_从零写第一个车载用例.md)** | 手把手，0 起点必读 |
| **[15 车载脚本模式库 20 式](docs/15_车载脚本模式库.md)** | 20 个必会写法（最实用） |
| **[16 调试技能](docs/16_调试技能.md)** | 跑不通怎么查 |
| **[17 从需求到脚本](docs/17_从需求到脚本.md)** | 一句话需求变用例 |
| **[18 零基础常识速成](docs/18_零基础常识速成.md)** | **18.0 汽车与仪表基础（三张图，没开过车必读）+ ECU/总线/诊断/行业黑话** |
| **[实训练习 training/](training/README.md)** | **6 个动手练习（模板+答案+判分器）** |
| **[19 十大真实场景脚本实战](docs/19_十二个场景脚本实战.md)** | **12 个可跑场景（信号/权限/时序/刷写/总线/故障注入/电源管理）** |
| **[20 测试规范与验证交付物](docs/20_测试规范与验证交付物.md)** | **需求追溯矩阵 + 测试规范 + 验证报告（System Validation 工作流）** |
| **[21 Python 补强](docs/21_Python补强.md)** | **读懂本项目代码的 Python 特性（装饰器/dataclass/线程/lambda…）+ 实战拆解** |
| **[场景脚本 scenarios/](docs/19_十二个场景脚本实战.md)** | **54 个真实工作风格用例，可直接跑** |
| **[需求与交付物 requirements/ deliverables/](docs/20_测试规范与验证交付物.md)** | **63 条需求 + 测试规范/验证报告/缺陷报告模板** |

## 接真实硬件

| 目标 | 改动 |
|---|---|
| 真实 CAN（CANable/CANalyst） | `simulators/bus.py` 的 `interface` 改 `serial`/`socketcan` |
| 真实安卓设备 | `libraries/AdbLibrary.py` 的 `ADB_PREFIX` 改 `["adb"]` |

## 已知边界（教学简化）

- 安全访问算法是 XOR 教学版（真实 OEM 算法保密但流程一致）；
- UDS 多帧未实现 0x78 responsePending 等待；
- 未实现 ISO-TP 错误恢复（序号错乱直接抛错）；
- 整车功能寻址（0x7DF）未实现，仅单 ECU 物理寻址。
