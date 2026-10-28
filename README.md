# 车载自动化测试学习项目（robot_framework）

[![车载自动化测试 CI](https://github.com/lxzks/robot_framework_project/actions/workflows/ci.yml/badge.svg)](https://github.com/lxzks/robot_framework_project/actions)

纯软件仿真的车载自动化测试系统：**模拟动力域 ECU + 数字仪表盘 + 安卓车机**，基于 **Robot Framework** 编写 **276 个**自动化测试用例（主套件 198 + 场景 78），覆盖 CAN/CAN FD/LIN/DoIP/SOME-IP 通信、UDS 诊断、OBD-II、OTA/Bootloader、ODX/OTX、Appium 真机 UI、HIL 台架、DTC 故障管理、HMI 与视觉断言、ADB 设备操作、非功能、信息安全、功能安全机制、Trace 分析、故障注入与电源管理。

> 为零基础学习车载自动化测试而建。详细教程见 [`docs/`](docs/)（共 40 篇，从 [`00_学习路线图.md`](docs/00_学习路线图.md) 的 4 周计划开始）。
>
> 🚀 **完全零基础（没开过车、没写过测试）？先走** → [`start_here/`](start_here/README.md)（7 天，每天 1-2 小时）
> 🐍 **想练 Python？** → [`python_practice/`](python_practice/README.md)（8 题 + 自动判分）
> 📄 **要投简历/面试？** → [`deliverables/project_brief.md`](deliverables/project_brief.md)（项目说明书 + 简历描述）
> 🎤 **要现场演示？** → [`deliverables/demo_script.md`](deliverables/demo_script.md)（15 分钟演示脚本，含追问与应急预案）
> 🎤 **准备面试追问？** → [`docs/12 章 12.3 节`](docs/12_术语表与面试高频题.md)（针对本项目的 20 道追问）

---

## 系统组成

```
┌─────────────────────────────────────────────────────────────┐
│  Robot Framework 测试层（tests/robot/ 21 套件 198 用例      │
│    01 CAN 信号 │ 02 UDS 诊断 │ 03 DTC+HMI │ 04 ADB          │
│    05 座舱 IVI 双屏 │ 06 UDS 刷写/协议一致性                │
│    07 DoIP 诊断 │ 08 LIN 总线 │ 09 HMI 视觉                 │
│    10 非功能测试 │ 11 信息安全                              │
│    12 CAN FD │ 13 SOME/IP │ 14 诊断进阶(OBD)                │
│    15 安全机制 │ 16 Trace 分析                              │
│    17 OTA/Bootloader │ 18 ODX/OTX                           │
│    19 Appium 真机 UI │ 20 HIL 台架                          │
├─────────────────────────────────────────────────────────────┤
│  自定义关键字库（libraries/ 18 个）                         │
│    VehicleBus │ Diagnostic │ Dashboard │ Adb                │
│    Lin │ Doip │ Vision │ Perf │ Security                    │
│    CanFd │ SomeIp │ Safety │ Obd │ TraceAnalysis            │
│    Ota │ Odx │ Appium │ Hil                                 │
├─────────────────────────────────────────────────────────────┤
│  车辆仿真（simulators/，多进程 + UDP 组播虚拟 CAN 总线）    │
│    ecu_sim.py       动力域 ECU（周期报文 + 17 个 UDS 服务   │
│                     + DTC 状态机 + 交互控制台）             │
│    dashboard.py     仪表入口：GUI 多界面 / --headless 双模式│
│    cluster_gui.py   多界面座舱仪表（启动画面+7 界面+闪烁）  │
│    android_gui.py   安卓车机中控（GUI 6 界面 / --headless） │
│    ivi_backend.py   车机 IVI 后端（无 Qt：状态+CAN 广播）   │
│    uds_client.py    UDS 诊断仪命令行（模拟 CANoe）          │
│    adb_sim.py       安卓车机模拟器（adb 命令 mock）         │
│    uds_lib.py       UDS 编解码 + ISO-TP 多帧                │
│    dtc_manager.py   DTC 状态机（pending/confirmed/aging）   │
│    bus.py           python-can 虚拟总线封装                 │
│    ivi_data.py      歌单/联系人/街道字典（CAN 传索引查表）  │
│    dbc/vehicle.dbc  整车信号字典（5 条报文）                │
│    lin_sim.py + ldf/vehicle.ldf  LIN 主从总线（08 套件）    │
│    doip_gateway.py  DoIP 网关（以太网⇄CAN，07 套件）        │
│    canfd_sim.py     CAN FD 帧/位速率切换仿真（12 套件）     │
│    someip_service.py  SOME/IP 服务发现/方法调用（13 套件）  │
│    safety_ecu.py    安全机制（E2E/看门狗/降级，15 套件）    │
│    asc_analyzer.py  ASC/BLF 追踪分析（16 套件）             │
│    ota_server.py    OTA 云端/Bootloader 状态机（17 套件）   │
│    diag/*.xml       ODX/OTX 诊断数据库样本（18 套件）       │
│    appium_mock.py   Appium WebDriver mock（19 套件）        │
│    hil_rack.py      HIL 台架（电源/IO 仿真，20 套件）       │
│    cluster_canvas.py 视觉渲染 + golden/ 基准图（09 套件）   │
│    test_*_e2e.py    三个端到端联调自测（UDS/仪表/IVI）      │
│    test_android_gui_click.py  GUI 点击全覆盖自测（27 项）   │
├─────────────────────────────────────────────────────────────┤
│  一键启动（项目根）                                         │
│    start_cockpit.py   拉起 ECU+仪表+车机 三个进程           │
│    start_cockpit.bat  Windows 双击入口                      │
└─────────────────────────────────────────────────────────────┘
```

### 开发自测（改代码后跑一遍）

```bash
python run_tests.py                 # 统一入口：跑全部 276 个用例
python run_tests.py --suite 06      # 按文件名/目录名过滤：命中 tests/robot/06 + scenarios/06（同编号文件）
python run_tests.py --suite 17      # Wave3：17 OTA（18 ODX / 19 Appium / 20 HIL 同理，只命中主套件）
python run_tests.py --generate --report   # 生成用例 → 跑 → 出报告+覆盖率
# 只跑单个文件的精确写法（避免同编号场景一起跑）：
# python -m robot --outputdir results tests/robot/06_uds_flash_conformance.robot

python simulators/test_uds_e2e.py           # UDS 诊断全流程（10 步）
python simulators/test_cluster_e2e.py       # 仪表+ADB 整链
python simulators/test_ivi_e2e.py           # 座舱 IVI 联动
python simulators/test_android_gui_click.py # 车机 GUI 点击全覆盖（27 项）

python tools/hardware_check.py             # 仿真/真实硬件 配置检查（接硬件、演示前先跑）
python tools/check_docs_consistency.py     # 文档数字一致性检查（改数字/加模块后跑；CI 门禁）
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
| `tools/pytest_style/` | **pytest 单元测试**（30 个，毫秒级，与 RF 系统测试互补） | `.\.venv\Scripts\python.exe -m pytest tools/pytest_style -q` |
| `Dockerfile` / `.dockerignore` | **Docker 化测试环境**（python:3.12-slim，一条命令复现） | `docker build -t vehicle-test . && docker run --rm vehicle-test` |
| `tools/hardware_check.py` | **真实硬件探针**：检查 CAN 接口/ADB 配置，打印仿真/真实模式与排查清单 | `python tools/hardware_check.py` |
| `tools/check_docs_consistency.py` | **文档数字一致性门禁**：现场统计用例/需求/教程/训练数并查过期数字 | `python tools/check_docs_consistency.py` |
| `.github/workflows/ci.yml` | CI：Python **3.12/3.14 矩阵** + PR 冒烟快速反馈 + 文档数字门禁 + push/nightly 全量与归档 | 推送到 GitHub 即生效 |

**产物示例**：`results/test_summary.html`（交付报告，含需求覆盖率）、
`results/coverage.html`（覆盖率）、`results/trace_matrix.html`（需求追溯矩阵）、
`results/bus_trace.asc`（CANoe 兼容 Trace，可直接用 CANoe/CANalyzer 打开分析）。

## 快速开始

> 推荐用虚拟环境（项目里 `.venv/` 已被 .gitignore 忽略，clone 后自己建）。Windows PowerShell 激活命令是 `.venv\Scripts\Activate.ps1`，cmd 是 `.venv\Scripts\activate.bat`，Linux/macOS 是 `source .venv/bin/activate`。

```bash
# 1. 建虚拟环境（只需一次）+ 安装依赖（Python 3.10+；本机 3.14.5 / CI 3.12 验证）
python -m venv .venv
.venv\Scripts\activate            # Windows cmd；PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 一键启动完整座舱（三个进程，一条命令）
python start_cockpit.py                                # 默认工况
python start_cockpit.py --speed 60 --coolant 125       # 车速60 + 水温故障
# Windows 也可以双击 start_cockpit.bat

# 3. 或分终端手动启动（各自独立窗口）
python simulators/ecu_sim.py --speed 60 --coolant 125  # 终端1：车辆
python simulators/dashboard.py                         # 终端2：座舱仪表（7 界面）
python simulators/android_gui.py                       # 终端3：安卓车机（6 界面）

# 4. 一键跑全部自动化（自动拉起环境 → 276 个用例 → 自动回收）
python run_tests.py                # 276 个用例（tests/robot 198 + scenarios 78）
python run_tests.py --report       # 跑完再生成 results/ 下的交付报告/覆盖率/追溯矩阵
```

预期输出：`276 tests, 276 passed, 0 failed`（本机全量约 6 分钟/359~380s（多次实测）；综合覆盖率 96.5%，需求追溯 124/124）。

> 只跑主套件（198 个用例）：`python -m robot --outputdir results tests/robot/`
> 按名字过滤跑某个模块：`python run_tests.py --suite 17`（命中 tests/robot/17；06/07 等同编号场景也会一起命中）

## 常见报错（新手先看这里）

| 报错 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'yaml'` | 没装 PyYAML（换环境/重装后常见） | `pip install -r requirements.txt`（或 `pip install "PyYAML>=6.0"`） |
| `'python' 不是内部或外部命令` / `python: command not found` | Python 没加进 PATH，或本机用的是 `py` 启动器 | 重装 Python 并勾选 Add to PATH；或改用 `py -m robot ...` / 写解释器全路径 |
| `ModuleNotFoundError: No module named 'PySide6'` | 跑了 GUI 模式但没装 Qt 依赖 | 装依赖 `pip install -r requirements.txt`；只想跑测试/无桌面环境时用 headless：`python simulators/dashboard.py --headless` |
| 测试大量超时 / `TimeoutError ... 没等到期望的报文` | UDP 组播被防火墙/安全软件拦截，或 30000 端口/残留进程占用 | 放行 Python 的 UDP 组播（`233.255.0.1:30000`）；结束残留 Python 进程、删 `simulators/*state.json` 后重跑 |
| `UnicodeEncodeError: 'gbk' codec ...` | Windows 控制台默认 GBK，脚本/Robot 输出含 ✅✓ 等符号 | 运行前 `chcp 65001`，或设 `PYTHONIOENCODING=utf-8`（cmd 用 `set`，PowerShell 用 `$env:`） |

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
| 01_can_signals | 8 | 周期报文完整性、信号范围、信号联动、档位逻辑、里程累积、警告灯状态 |
| 02_uds_diagnostic | 13 | 会话控制、ISO-TP 多帧、安全访问（种子/密钥/锁定）、DID 读写、NRC 错误处理、例程、抑制响应 |
| 03_dtc_workflow | 7 | 故障注入→pending→confirmed→点灯→快照→清除→恢复 全生命周期 |
| 04_adb_android | 6 | 设备检测、系统属性、安装/启动/卸载、输入模拟、焦点断言、电量查询 |
| 05_ivi_cockpit | 6 | 座舱双屏一致：车机状态→CAN 0x400/0x500→仪表显示（媒体/导航/空调/trip） |
| 06_uds_flash_conformance | 11 | **UDS 刷写流程**（0x34/0x36/0x37 + 完整性校验）+ **协议一致性**（权限模型/错误处理/时序） |
| 07_doip_diagnostics | 10 | **DoIP 诊断**（ISO 13400）：车辆识别/路由激活/诊断透传（以太网 ⇄ CAN） |
| 08_lin_bus | 10 | **LIN 2.x 车身总线**：帧结构/PID/两种校验和/调度表/睡眠唤醒（主从 + LDF） |
| 09_hmi_vision | 10 | **HMI 视觉测试**：截图基准比对/区域断言/指针角度/警告灯识别 + mutation 用例自检 |
| 10_nonfunctional_perf | 9 | **非功能测试**：响应时间/总线负载/吞吐/抖动/长稳（真实测量，不伪造数字） |
| 11_security | 10 | **信息安全**：种子-密钥/未授权拒绝/重放防护/爆破锁止/模糊测试 |
| 12_canfd | 10 | **CAN FD**：帧格式/位速率切换/DLC>8/CRC 与总线负载 |
| 13_someip | 10 | **SOME/IP / SOA**：服务发现(SD)/方法调用/事件组/序列化 |
| 14_functional_addressing_obd | 10 | **功能寻址与 OBD-II**：0x7DF 广播/0x2F·0x23·0x3D/OBD 服务 |
| 15_safety_mechanisms | 10 | **功能安全机制**：E2E 保护/看门狗/程序流/失效降级 |
| 16_trace_analysis | 9 | **Trace 分析**：ASC/BLF 解析、周期/抖动、失败现场还原 |
| 17_ota_bootloader | 10 | **OTA 与 Bootloader**：A/B 分区、CRC32 完整性、回滚、刷写失败恢复 |
| 18_odx_database | 9 | **ODX/OTX 数据库驱动**：诊断描述解析、参数化请求/响应校验 |
| 19_appium | 9 | **Appium 真机 UI**：W3C WebDriver、capabilities、定位器、手势与断言（mock 后端） |
| 20_hil_rack | 10 | **HIL 台架**：电源/IGN/电压、IO 验证、故障注入（仿真台架） |
| generated（自动生成） | 11 | 由 DBC 自动生成：31 个数值信号的边界值 + 枚举等价类 + pairwise 组合 |
| **合计** | **198** | 另有 `scenarios/` 16 个真实场景 78 个用例 → 全量 276，见 `python run_tests.py` |

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
├── docs/             # 40 篇教程（00 路线图 → 39 能力自检与学习路线）
├── start_here/       # 7 天零基础主线（day1 → day7）+ 毕业任务参考答案
├── python_practice/  # 8 道 Python 练习 + 自动判分
├── training/         # 12 个渐进实训（L1→L12，题目/模板/答案/判分器）
├── tools/            # 工程工具链：pytest 单测 / 真实硬件探针 / 文档数字校验
│   ├── pytest_style/ #   pytest 单元测试（30 个，毫秒级，与 RF 系统测试互补）
│   ├── hardware_check.py         #   仿真⇄真实硬件 配置检查
│   └── check_docs_consistency.py #   文档数字一致性（CI 门禁）
├── simulators/       # 车辆仿真（ECU/仪表/车机/总线/DBC + LIN/DoIP/视觉/CAN FD/SOME-IP/安全/Trace/OTA/Appium/HIL）
├── libraries/        # RF 自定义关键字库（18 个：总线/诊断/仪表/ADB/LIN/DoIP/视觉/性能/安全/CAN FD/SOME-IP/安全机制/OBD/Trace/OTA/ODX/Appium/HIL）
├── tests/robot/      # RF 主套件（20 个手写套件 + 1 自动生成 = 21 个 .robot，198 用例）
├── scenarios/        # 16 个真实工作风格场景（78 用例）
├── resources/        # 公共资源（环境生命周期管理）
├── requirements/     # 124 条需求清单（requirements.yaml）
├── deliverables/     # 项目说明书/测试规范/验证报告/缺陷报告模板/15 分钟演示脚本
├── results/          # 测试产物（gitignore，运行后生成）
├── Dockerfile        # 容器化测试环境（python:3.12-slim）
└── .github/          # CI 工作流（Python 3.12/3.14 矩阵 + pytest + RF 全量）
```

## 关键设计

- **多进程仿真**：ECU/仪表/诊断仪是独立进程，用 python-can 的 UDP 组播接口组成"虚拟 CAN 总线"（`233.255.0.1:30000`），进程间通信语义与真实总线广播一致；
- **HMI 状态外置**：headless 仪表盘把显示模型序列化为 JSON，测试断言数据而非像素；
- **协议完整实现**：ISO-TP 多帧（FF/CF/FC 状态机）、DTC 八状态位、S3 会话定时器、MIL 灯 latch 策略；
- **环境即代码**：套件 setup/teardown 自动拉起/回收仿真进程，一条命令完成全部测试，CI 友好；
- **Wave2 能力面**：CAN FD（12）/ SOME/IP（13）/ OBD-II 功能寻址（14）/ 功能安全机制 E2E（15）/ Trace 分析（16）；
- **Wave3 能力面**：OTA 与 Bootloader 全链路（17）/ ODX/OTX 数据库驱动（18）/ Appium 真机 UI（19）/ HIL 台架（20）；工程侧 = pytest 单元测试 + Docker + CI（3.12/3.14 矩阵）。

## 教程导航

| 文档 | 内容 |
|---|---|
| [00 学习路线图](docs/00_学习路线图.md) | **4 周冲刺计划、五层能力模型、40 篇导航** |
| [01 车载自动化全景](docs/01_车载自动化全景.md) | 伟世通业务、仪表盘原理、测试类型 |
| [02 车载网络基础](docs/02_车载网络基础.md) | CAN 物理层/位仲裁/DBC、LIN/DoIP |
| [03 UDS 协议详解](docs/03_UDS协议详解.md) | UDS 核心服务（协议篇）、NRC、会话、ISO-TP |
| [04 DTC 详解](docs/04_DTC详解.md) | 故障码结构、状态位、生命周期 |
| [05 Robot Framework](docs/05_RobotFramework从零到实战.md) | 语法、数据驱动、自定义库 |
| [06 环境搭建](docs/06_环境搭建与项目结构.md) | 架构、文件地图、21 个排坑记录 |
| [07-09 实战精讲](docs/07_CAN仿真测试实战.md) | CAN/UDS/HMI 套件逐行讲解 |
| [10 ADB/Android](docs/10_ADB与Android车载基础.md) | adb 命令族、AAOS、安卓车机 GUI |
| [11 综合实战](docs/11_综合实战项目.md) | 4 个递进任务、面试话术 |
| [12 术语与面试](docs/12_术语表与面试高频题.md) | 80 条术语、33 道高频题 + 20 道项目追问 |
| **[13 测试工程化与 CI](docs/13_测试工程化与CI.md)** | Trace 归档/交付报告/用例生成/覆盖率/CI |
| **[14 从零写第一个车载用例](docs/14_从零写第一个车载用例.md)** | 手把手，0 起点必读 |
| **[15 车载脚本模式库 20 式](docs/15_车载脚本模式库.md)** | 20 个必会写法（最实用） |
| **[16 调试技能](docs/16_调试技能.md)** | 跑不通怎么查 |
| **[17 从需求到脚本](docs/17_从需求到脚本.md)** | 一句话需求变用例 |
| **[18 零基础常识速成](docs/18_零基础常识速成.md)** | **18.0 汽车与仪表基础（三张图，没开过车必读）+ ECU/总线/诊断/行业黑话** |
| **[实训练习 training/](training/README.md)** | **12 个动手练习（L1~L12，模板+答案+判分器）** |
| **[19 十六个场景脚本实战](docs/19_十六个场景脚本实战.md)** | **16 个场景脚本（12 核心 + 4 扩展）/ 78 个可跑用例（信号/权限/时序/刷写/总线/故障注入/电源管理 + LIN/DoIP/视觉/性能扩展）** |
| **[20 测试规范与验证交付物](docs/20_测试规范与验证交付物.md)** | **需求追溯矩阵 + 测试规范 + 验证报告（System Validation 工作流）** |
| **[21 Python 补强](docs/21_Python补强.md)** | **读懂本项目代码的 Python 特性（装饰器/dataclass/线程/lambda…）+ 实战拆解** |
| **[22 车载自动化测试全景](docs/22_车载自动化测试全景与能力地图.md)** | **完整能力地图 + 能力矩阵（每项能力怎么练）+ 学习路径 v2 + 项目扩展路线** |
| **[23 LIN 总线与车身控制测试](docs/23_LIN总线与车身控制测试.md)** | **LIN 2.x 帧/调度表/LDF/两种校验和/睡眠唤醒 + 可跑模块 `08_lin_bus`** |
| **[24 车载以太网与 DoIP 诊断](docs/24_车载以太网与DoIP诊断.md)** | **ISO 13400 DoIP 头/车辆识别/路由激活/诊断透传 + 可跑模块 `07_doip_diagnostics`** |
| **[25 HMI 视觉自动化测试](docs/25_HMI视觉自动化测试.md)** | **截图基准比对/区域断言/指针角度/警告灯识别/差异图 + 可跑模块 `09_hmi_vision`** |
| **[26 非功能测试实战](docs/26_非功能测试实战.md)** | **性能/压力/长稳/总线负载（真实测量）+ 可跑模块 `10_nonfunctional_perf`** |
| **[27 信息安全测试入门](docs/27_信息安全测试入门.md)** | **UDS 安全访问/重放/爆破防护/模糊测试 + 可跑模块 `11_security`** |
| **[28 功能安全与 ASPICE](docs/28_功能安全与ASPICE测试过程.md)** | **ISO 26262 安全机制怎么测 + ASPICE 证据链（流程与标准课）** |
| **[29 CAN FD 与高速总线](docs/29_CANFD与高速总线.md)** | **CAN FD 帧格式/位速率切换/DLC>8/CRC + 可跑模块 `12_canfd`** |
| **[30 SOME/IP 与 SOA 服务测试](docs/30_SOMEIP与SOA服务测试.md)** | **面向服务通信：服务发现/方法调用/事件组 + 可跑模块 `13_someip`** |
| **[31 功能寻址与 OBD-II 诊断](docs/31_功能寻址与OBDII诊断.md)** | **0x7DF 广播/0x2F·0x23·0x3D/OBD 服务 + 可跑模块 `14_functional_addressing_obd`** |
| **[32 功能安全机制验证](docs/32_功能安全机制验证.md)** | **E2E 保护/看门狗/程序流/失效降级 + 可跑模块 `15_safety_mechanisms`** |
| **[33 工程工具链实战](docs/33_工程工具链实战.md)** | **pytest 单元测试 / Docker / CI 矩阵 / CANoe·Appium·HIL 对接** |
| **[34 追踪分析实战（ASC/BLF）](docs/34_追踪分析实战（ASC与BLF）.md)** | **从 Trace 还原失败现场 + 可跑模块 `16_trace_analysis`** |
| **[35 OTA 与 Bootloader 全链路](docs/35_OTA与Bootloader全链路.md)** | **云端→车端→ECU 刷写、A/B 回滚、CRC 完整性 + 可跑模块 `17_ota_bootloader`** |
| **[36 ODX/OTX 数据库驱动测试](docs/36_ODX与OTX数据库驱动测试.md)** | **ODX/OTX 诊断数据库解析 + 参数化诊断用例 + 可跑模块 `18_odx_database`** |
| **[37 Appium 与真机 UI 自动化](docs/37_Appium与真机UI自动化.md)** | **W3C WebDriver/capabilities/定位器 + 可跑模块 `19_appium`（mock 后端）** |
| **[38 HIL 台架与电源/IO 验证](docs/38_HIL台架与电源IO验证.md)** | **台架电源/IGN/IO/故障注入（仿真台架）+ 可跑模块 `20_hil_rack`** |
| **[39 能力自检与下一步学习路线](docs/39_能力自检与下一步学习路线.md)** | **40 篇知识地图 + 能力自检 + 入职后学习路线** |
| **[场景脚本 scenarios/](docs/19_十六个场景脚本实战.md)** | **78 个真实工作风格用例（十六个场景脚本：12 核心 + 4 扩展），可直接跑** |
| **[需求与交付物 requirements/ deliverables/](docs/20_测试规范与验证交付物.md)** | **124 条需求 + 测试规范/验证报告/缺陷报告模板** |
| **[deliverables/demo_script.md](deliverables/demo_script.md)** | **15 分钟面试/汇报演示脚本：3 终端布局 + 一条完整链路 + 追问答案 + 应急预案** |

## 接真实硬件（可选）

项目默认纯仿真；**不修改任何代码**，只用环境变量即可切到真实硬件：

| 目标 | 环境变量示例 | 说明 |
|---|---|---|
| 真实 CAN（CANable/USB-CAN，slcan） | `CAN_INTERFACE=slcan` `CAN_CHANNEL=COM4` `CAN_BITRATE=500000` | 需 `pip install pyserial`（slcan 后端依赖） |
| 真实 CAN（Linux socketcan） | `CAN_INTERFACE=socketcan` `CAN_CHANNEL=can0` | 先 `sudo ip link set can0 up type can bitrate 500000` |
| 真实 CAN（PCAN/Vector） | `CAN_INTERFACE=pcan`（或 `vector`）+ 对应通道 | 需装厂商驱动/python-can 插件 |
| 真实安卓设备 | `ADB_BIN=adb`（多设备 `ADB_BIN="adb -s 序列号"`） | 真机需开 USB 调试 |

```powershell
python tools/hardware_check.py     # 先检查配置：仿真/真实模式、接口能否打开、adb 是否可用
$env:CAN_INTERFACE="slcan"; $env:CAN_CHANNEL="COM4"   # 设置后运行任何测试命令即走真实总线
python run_tests.py --tag smoke    # 建议先跑冒烟，再跑全量
Remove-Item Env:CAN_INTERFACE      # 删除即切回仿真
```

> ⚠️ 诚实声明：接入层与探针已就绪，但作者没有真实 CAN 硬件/真车机可验证，实际接线、驱动、波特率与整车一致性问题需你在真机上确认。原理与替换点见 [docs/33 §33.11](docs/33_工程工具链实战.md)。

## 已知边界（教学简化）

- 安全访问算法是 XOR 教学版（真实 OEM 算法保密但流程一致）；
- UDS 多帧未实现 0x78 responsePending 等待；
- 未实现 ISO-TP 错误恢复（序号错乱直接抛错，但已加异常兜底，不会杀死诊断线程）；
- 整车功能寻址（0x7DF）仅在套件 14 做教学级验证（真实网关路由/多 ECU 响应的复杂度简化）；
- 扩展模块（LIN/DoIP/视觉/性能/安全）也有教学简化点，见 `docs/23~27` 与 KNOWN_ISSUES；
- Wave2 扩展模块（CAN FD/SOME-IP/OBD/功能安全/Trace 分析）的简化点见 `docs/29~34` 与 KNOWN_ISSUES；
- Wave3 扩展模块（OTA/ODX/Appium/HIL）的简化点见 `docs/35~38` 与 KNOWN_ISSUES，主要是：OTA 的 A/B 分区是语义模拟、完整性校验用 CRC32 而非真实签名、Appium 走 mock WebDriver、HIL 是仿真台架而非真实 dSPACE/VT 硬件。

> 完整清单（协议简化 / 测试有效性限制 / 工程待改进 / 教程可补项）见
> **[KNOWN_ISSUES.md](KNOWN_ISSUES.md)**（2026-10-28 体检产出，面试时可主动披露）。

## License

[Apache License 2.0](LICENSE)。
