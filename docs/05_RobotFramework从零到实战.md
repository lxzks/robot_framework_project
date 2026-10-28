# 05 · Robot Framework 从零到实战

> Robot Framework（RF）是**关键字驱动**的通用测试框架。Python 生态、ATDD（验收测试驱动开发）友好、报告开箱即用——这些让它成为测试开发面试的高频加分项。
>
> 本章目标：零基础到能独立读写本项目的 **6 个主测试套件**（01~06）+ 1 个**自动生成套件**（`tests/robot/generated/generated_signals.robot`，由 `generate_testcases.py` 从 DBC 生成，勿手改），并理解"为什么这样组织"。

---

## 5.1 RF 是什么：用"说话"写测试

传统 pytest 用代码写测试：

```python
def test_read_vin():
    vin = read_did(0xF190)
    assert vin == "LSVAM4180NC000001"
```

RF 用**关键字（keyword）**写：

```robotframework
*** Test Cases ***
读取VIN码应正确
    ${vin}=    Read DID As ASCII    F190
    Should Be Equal    ${vin}    LSVAM4180NC000001
```

一眼就能读懂——**这就是关键字驱动的价值：用例即文档**。产品经理、测试主管都能看懂用例在验证什么。

### 5.1.1 RF 生态定位

| 框架 | 风格 | 强项 | 弱项 |
|---|---|---|---|
| **Robot Framework** | 关键字驱动 | 用例可读性、报告、非程序员友好、库生态（Selenium/Appium/Requests...） | 复杂逻辑写起来啰嗦、调试体验一般 |
| pytest | 代码驱动 | 灵活、插件生态、开发者熟悉 | 用例=代码，可读性靠命名 |
| unittest | 代码驱动 | 标准库、OO 风格 | 啰嗦、老化 |

**车载系统验证为什么常用 RF/类似工具**：用例数量巨大（几千条）、经常要按规格书逐条核对、需要给客户/审核方看报告。关键字驱动 + 自带 HTML 报告正好对口。

---

## 5.2 一个最小 RF 项目长什么样

RF 的**数据文件**是纯文本（`.robot` 或 `.resource`），分四个区域：

```robotframework
*** Settings ***
Documentation    套件说明
Resource        ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Variables ***
${TIMEOUT}      10

*** Test Cases ***
第一个用例
    [Documentation]    用例说明
    [Tags]    smoke
    Log    你好，Robot Framework

*** Keywords ***
自定义关键字
    [Arguments]    ${name}
    Log    Hello ${name}
```

- **Settings**：引入资源、库，设置 setup/teardown
- **Variables**：变量定义
- **Test Cases**：用例列表
- **Keywords**：用户关键字（把多个步骤打包复用）

---

## 5.3 语法速成（30 分钟版）

### 5.3.1 数据表格：分隔符是灵魂

RF 用例由"表格"构成，**两个以上空格**（或 `|` 管道符）分隔单元格：

```robotframework
Send CAN Frame    0x100    881300805A330000
```

- 第一列是关键字名，后面是参数；
- 行首缩进 4 空格 = 上一行的延续；
- `...` 表示上一行续行。

**推荐用管道符对齐**（可读性最好，VS Code 的 RF 插件会自动格式化）：

```robotframework
| Send CAN Frame | 0x100 | 881300805A330000 |
```

### 5.3.2 变量

```robotframework
*** Variables ***
${SPEED}        60              # 标量
@{SIGNALS}      speed    rpm    # 列表
&{DICT}          key=value      # 字典

*** Test Cases ***
变量示例
    ${speed}=    Get Displayed Speed          # 赋值（= 两边空格可有可无）
    Log    当前车速 ${speed} km/h
    Should Be True    ${speed} > 50
```

注意：**RF 里所有参数默认都是字符串**。数值比较用 `Should Be Equal As Numbers`，或让库返回原生类型（本项目库都做了类型转换，`Get Displayed Speed` 直接返回 float）。

### 5.3.3 流程控制

```robotframework
    FOR    ${i}    IN RANGE    5
        Log    第 ${i} 次
    END

    FOR    ${item}    IN    @{SIGNALS}
        Log    ${item}
    END

    IF    ${speed} > 100
        Log    超速了
    ELSE IF    ${speed} > 60
        Log    快速路
    ELSE
        Log    慢速
    END

    WHILE    ${retry} < 3
        ${ok}=    试一次
        IF    ${ok}    BREAK
    END
```

> 踩坑记录（本项目真实经历）：RF 的 inline IF（`IF $cond    Keyword`）**分支里不能有赋值**，且 `FOR ${a} ${b} IN @{list}` 对 list of tuple 的解包行为和预期不同。写复杂逻辑时优先用块式 FOR/IF + 中间变量，或用 Python 库实现（"复杂逻辑下沉到库"是 RF 的最佳实践）。

### 5.3.4 等待与重试

测试车载系统，"等待"是刚需（信号要几十毫秒才刷新、故障要点亮要几秒）。RF 提供：

```robotframework
Wait Until Keyword Succeeds    10x    1s    Telltale Should Be Lit    CoolantTempHigh
# 含义：最多重试 10 次，每次间隔 1s，直到关键字成功
```

> **大坑**：`Wait Until Keyword Succeeds` 只检测"关键字是否抛异常"，**不检测返回值**！用 `Wait Until Keyword Succeeds ... Read Signal ...` 等待信号进范围是**无效**的（读到超范围值也算成功）。正确做法是把值域判断做进关键字里——本项目的 `Wait Until Signal In Range` 就是为此而生（见 `libraries/VehicleBusLibrary.py`）。

### 5.3.5 断言

| 关键字 | 用途 |
|---|---|
| `Should Be Equal a b` | 字符串相等 |
| `Should Be Equal As Numbers a b` | 数值相等 |
| `Should Be True ${cond}` | 布尔条件 |
| `Should Be Empty ${list}` | 空列表/字符串 |
| `Should Contain ${text} ${sub}` | 包含 |
| `Run Keyword And Expect Error 模式 关键字 args` | **预期关键字失败**（测负响应必备） |

最后一个是诊断测试神器：`Run Keyword And Expect Error *没等到* Send UDS Request 3E 80`（验证 ECU 静默不回复）。

### 5.3.6 数据驱动测试（Template）

```robotframework
*** Test Cases ***
读取各DID
    [Template]    读取DID并校验
    F187    HW-IC-2026-001
    F189    ECU-2026-0918-007
    F18A    VISTEON
```

一个用例模板 × N 组数据 = N 条执行记录，报告里每条独立统计。

---

## 5.4 用户关键字与资源文件

### 5.4.1 用户关键字：用例的"函数"

```robotframework
*** Keywords ***
进入扩展会话并解锁
    [Documentation]    组合多个步骤，供多个用例复用
    Enter Extended Session
    Security Unlock
```

**什么时候该抽关键字？** 三条判断：
1. 同样两步以上在 3 个以上用例出现 → 抽；
2. 用例里有超过 5 行连续步骤 → 按业务语义拆；
3. 用例里有复杂逻辑（循环/条件/计算）→ **下沉到 Python 库**。

### 5.4.2 资源文件（.resource）

存放跨套件共享的关键字和变量。本项目 `resources/common.resource` 就是范例：所有套件共享"启动/关闭车辆环境"。

```robotframework
# 01_can_signals.robot 里这样引入：
Resource    ../../resources/common.resource
```

路径规则（**血的教训**）：资源文件里的相对路径基于**该资源文件所在目录**；用例文件里的相对路径基于**该 .robot 文件所在目录**。本项目第一版就在这栽过（`${CURDIR}/../..` 多退一级）。

---

## 5.5 自定义关键字库：RF 接 Python 的桥

这是"测试开发"和"测试执行"的分水岭。本项目 4 个库（`libraries/` 目录）都是标准写法：

```python
# libraries/VehicleBusLibrary.py（简化）
from robot.api.deco import keyword

class VehicleBusLibrary:
    ROBOT_LIBRARY_SCOPE = "SUITE"      # 每个套件一个实例
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, node_name: str = "RF-BUS") -> None:
        self._bus = VirtualCanBus(node_name)   # 构造时连总线

    @keyword("Send CAN Frame")               # 显式指定关键字名
    def send_can_frame(self, arbitration_id, data_hex: str) -> None:
        fid = int(str(arbitration_id), 0)     # "0x100" 或 "256" 都认
        data = bytes.fromhex(data_hex.replace(" ", ""))
        self._bus.send_frame(fid, data)
```

要点：

| 机制 | 说明 |
|---|---|
| 类名 `XxxLibrary` | RF 约定后缀，引用时可写全路径或短名 |
| `ROBOT_LIBRARY_SCOPE` | `GLOBAL`（全程一个实例）/ `SUITE`（每套件一个）/ `TEST`（每用例一个）。**持有总线连接、必须在 teardown 释放的库用 SUITE**（本项目 VehicleBus/Diagnostic）；**只读状态文件、无连接的库用 GLOBAL 更省开销**（本项目 Dashboard/Adb）。关键不是选哪个，而是 start/stop 必须成对，且不能让下一个套件复用已关闭的资源（本项目踩过） |
| `@keyword("名字")` | 显式命名；不写则方法名 `send_can_frame` 自动变成 `Send Can Frame` |
| 参数默认值 | RF 调用时可不传 |
| 返回值 | 直接 return，RF 用 `${var}=    Keyword` 接收 |
| 抛出异常 | 抛 `AssertionError` 即用例失败；错误信息会进报告 |

**动态库 vs 静态库**：以上是静态 API（靠类型注解/反射）。复杂场景（参数带默认对象等）可用 `robotlibcore.PythonLibCore` 基类写动态库——本项目依赖里装了 `robotframework-pythonlibcore`，进阶可以看它的文档。

---

## 5.6 运行与报告

```bash
# 基本运行
python -m robot tests/robot/

# 指定输出目录、只跑某个套件/用例
python -m robot --outputdir results tests/robot/02_uds_diagnostic.robot
python -m robot --test "读取*" tests/robot/     # 按用例名过滤（支持通配）

# 常用选项
--include smoke        # 只跑带 smoke 标签的
--exclude slow         # 排除慢的
--variable PYTHON:/path/to/python   # 覆盖变量
--loglevel DEBUG       # 详细日志
```

**输出三件套**（在 `--outputdir` 目录）：

| 文件 | 内容 |
|---|---|
| `output.xml` | 机器可读全量结果（CI 解析用） |
| `log.html` | **最重要**：每个关键字的调用、参数、返回值、日志，逐步可点开 |
| `report.html` | 概览：通过率、耗时、趋势 |

**调试技巧**：用例失败 → 打开 log.html → 点开失败步骤 → 能看到每个关键字的入参和库打印的 `info()` 日志。本项目所有关键字的关键路径都打了 `info()`，报告里可直接看到"请求 22f187 → 62f187..."。

---

## 5.7 本项目的 RF 架构解读

```
tests/robot/01_can_signals.robot      ← 套件 = 一个测试领域
    │  Resource: common.resource       ← 共享资源（环境管理+库加载）
    │  Library: VehicleBusLibrary...   ← 经 resource 引入，全套件可用
    │  Suite Setup: Start Vehicle Environment speed=60
    │      └─ Process 库启动 ecu_sim + dashboard（子进程）
    │      └─ Wait Until ECU Should Respond（就绪检查）
    │  Test Cases × 8                  ← 用例 = 一个验证点
    │  Suite Teardown: Stop Vehicle Environment
    │      └─ Terminate Process + Wait For Process（防残留）
    └─ libraries/                      ← 4 个自定义库 = 4 类系统能力
           VehicleBusLibrary   → CAN 总线（发帧/读信号/录制）
           DiagnosticLibrary   → UDS 诊断（会话/安全/DID/DTC/例程）
           DashboardLibrary    → 仪表断言（读显示状态/等灯亮灭）
           AdbLibrary          → Android（设备/应用/输入/状态）
```

**设计思想**（面试可展开）：
1. **用例只描述"做什么验证"，不关心"怎么连总线"** —— 连接细节全在库里；
2. **环境生命周期由 suite setup/teardown 托管** —— 一条命令全自动，CI 友好；
3. **断言语义化** —— `Telltale Should Be Lit` 比 `assert state["telltales_lit"].count(...)` 更接近业务语言；
4. **复杂逻辑下沉** —— ISO-TP 重组、种子密钥计算都在库里，用例层零算法。

---

## 5.8 本章自检

1. RF 相比 pytest 的核心差异？系统验证岗为什么偏好关键字驱动？
2. `ROBOT_LIBRARY_SCOPE` 三种取值的区别？持有总线连接的库该用哪个？为什么？
3. `Wait Until Keyword Succeeds` 的正确用法和经典误用？
4. 资源文件里的相对路径基于哪个目录？用例文件呢？
5. 设计一个用例："验证扩展会话+解锁后才能写 DID，未解锁返回 NRC 0x33"——用本项目的关键字写出来。

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | RF 的核心差异是**关键字驱动 + 表格语法**（用例读起来像自然语言，非技术人员也能评审）；pytest 是**代码驱动**。System Validation 岗偏好前者，因为**测试规范要交给客户/主管评审**，可读性就是价值 |
| 2 | `ROBOT_LIBRARY_SCOPE` 三值：**TEST**（每个用例重建）/ **SUITE**（每个套件重建）/ **GLOBAL**（全程一个）。持总线连接、要在套件 teardown 释放的库用 **SUITE**（VehicleBus/Diagnostic）；只读状态文件的库用 **GLOBAL**（Dashboard/Adb）——重点是 start/stop 成对、别让下一套件复用已关闭资源（本项目两种都用） |
| 3 | 正确用法：被等待的关键字**自己会断言**（如 `DTC List Should Contain`）；经典误用：包 `Read Signal From Bus`（它读到什么值都算成功 → 等待立即结束）—— 详见 15 章模式 2 |
| 4 | **资源文件里的相对路径基于该资源文件所在目录**（`${CURDIR}`）；**用例文件里的相对路径基于用例文件所在目录** —— 这是最容易搞混的一点 |
| 5 | 三步走：`Enter Extended Session` → `Security Unlock` → `Write DID`（正响应）；负向版：只进会话不解锁 → 发 `2E ...` → `Response Should Be Negative With NRC    0x33` |

---

**上一章：[04 · DTC 详解](04_DTC详解.md)** ｜ **下一章：[06 · 环境搭建与项目结构](06_环境搭建与项目结构.md)**
