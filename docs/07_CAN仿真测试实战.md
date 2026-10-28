# 07 · CAN 仿真测试实战：01 套件逐行精讲

> 本章把 `tests/robot/01_can_signals.robot` 的 8 个用例逐个拆开：测什么、怎么测、为什么这样设计、对应真实工作的哪部分。
>
> 前置：先按 06 章跑通环境，并启动 `ecu_sim.py --speed 60` 手动观察总线。

---

## 7.0 套件骨架

```robotframework
*** Settings ***
Documentation    套件 01：CAN 总线信号测试
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
...
```

三个要点：

1. **`Resource` 引入公共资源** —— 库（VehicleBus/Diagnostic/Dashboard）和环境管理关键字都从这里来；
2. **`Suite Setup Start Vehicle Environment speed=60`** —— 每个套件自动拉起 ECU（目标车速 60）和 headless 仪表盘，teardown 自动回收。**一条 `robot` 命令，零手动操作**；
3. **speed=60 的参数化** —— 同一个环境启动关键字，不同套件传不同初始工况（03 套件传 `coolant=125` 造故障）。

---

## 7.1 用例 1：总线应同时存在三条周期报文

```robotframework
总线应同时存在三条周期报文
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    Recorded Frames Should Contain ID    0x100
    Recorded Frames Should Contain ID    0x200
    Recorded Frames Should Contain ID    0x300
```

**测什么**：ECU 是否在按设计周期发送全部报文（100ms/0x100、200ms/0x200、500ms/0x300）。

**真实工作对应**：这是**总线符合性测试（bus compliance）**的第一项——用 CANoe 录 Trace，核对每个报文的周期、ID、DLC。报文丢失/周期漂移是常见的实车问题（EMC 干扰、bus-off、任务超时）。

**设计亮点——录制器模式**：
- `Start Bus Recorder` / `Drain Bus For 2` / `Stop Bus Recorder`：录 2 秒总线上**所有**报文到内部列表；
- `Recorded Frames Should Contain ID`：断言录制期间出现过指定 ID。

这对应 CANoe 的 Trace + 过滤。**录制-过滤-断言**是总线测试的通用范式，比"收到一帧看看值"更适合做周期性验证。

**举一反三**：可以扩展成"报文周期抖动检查"——录 10 秒，统计同一 ID 相邻帧间隔，断言 max-min < 5ms。试试自己写这个用例（提示：`Get Recorded Frames` 返回 `[(ts, id_hex, data_hex), ...]`，用 Python 算）。

---

## 7.2 用例 2：车速信号应在合理范围且接近设定值

```robotframework
车速信号应在合理范围且接近设定值
    Wait Until Signal In Range    EngineData    VehicleSpeed    50    70    timeout=20
```

**测什么**：ECU 按 60 km/h 目标启动，经过加减速斜坡后，车速信号应稳定在 60 附近（允许 ±10 容差）。

**`Wait Until Signal In Range` 是关键**（05 章讲过 `Wait Until Keyword Succeeds` 的陷阱）：它在库内部轮询，**直到值进入 [50, 70] 或超时**。为什么必须这样？ECU 从 0 加速到 60 需要 ~7.5 秒（斜坡 8 km/h/s），测试不能假设"读一次就到 60"。

**为什么断言范围而不是精确值？**
- 物理仿真有噪声（`random.uniform(-0.05, 0.05)`）和斜坡残余；
- 真实系统测试也永远用范围/容差断言——精确等于某个浮点数是不现实的（想想 0.1+0.2≠0.3）。

**真实工作对应**：信号矩阵（signal matrix）验证——"给定工况 X，信号 Y 应在范围 Z"。

---

## 7.3 用例 3：转速信号应与车速联动

```robotframework
转速信号应与车速联动
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    ${rpm}=    Read Signal From Bus    EngineData    EngineSpeed
    ${expected}=    Evaluate    max(800, ${speed} * 80)
    Should Be True    abs(${rpm} - ${expected}) < 300
    ...    msg=转速 ${rpm} 与理论值 ${expected} 偏差过大
```

**测什么**：**信号间的一致性（cross-signal consistency）**——本项目模型是 `rpm = max(800, speed × 80)`（怠速 800，每 km/h 对应 80 rpm）。分别读两个信号，验证它们满足模型关系。

**这是很有价值的一类测试**：单个信号正常不代表系统正常，**信号之间的关系**才是功能正确性的体现。例如"车速 > 0 时档位不能是 P"、"打转向灯时对应 bit 置 1"。

**注意读取时序**：两次 `Read Signal From Bus` 之间车还在加速（~100ms 差 0.8 km/h → 转速差 64 rpm），所以容差给到 300。

**举一反三**：加一个用例验证"档位与车速一致"：speed<1 → gear=0(P)，speed≥1 → gear=3(D)。（联动断言的思路见本章用例 3；仪表侧全 12 灯的矩阵校验在 `scenarios/02_telltale_matrix.robot`。）

---

## 7.4 用例 4：水温信号初始应在正常工作区间

```robotframework
水温信号初始应在正常工作区间
    ${coolant}=    Read Signal From Bus    EngineData    CoolantTemp
    Should Be True    80 <= ${coolant} <= 105
    ...    msg=水温 ${coolant} 不在 80~105℃ 正常区间
```

**测什么**：冷启动后水温应处于正常工作区间（本项目模型：从 90℃ 一阶惯性趋向目标）。

**为什么这个用例存在？** 它示范**边界/范围类断言**的最简形式。真实场景："充电时电池温度应在 0~45℃"、"燃油量不应在 1 秒内跳变 > 5%"。

**注意 `Read Signal From Bus` 的语义**：它**阻塞等待一帧指定报文**（默认 3s 超时）再解码返回。总线报文本就是周期刷新的，所以每次调用拿到的是"当前最新值"。

---

## 7.5 用例 5：档位应随车速从 P 切换到 D

```robotframework
档位应随车速从 P 切换到 D
    Wait Until Signal In Range    ClusterData    GearPosition    3    3    timeout=20
```

**测什么**：车速 > 1 km/h 后，档位信号应从 P(0) 变为 D(3)。

**Enum 信号的测试写法**：`Wait Until Signal In Range ... 3 3`（区间退化为单值）。真实场景档位是字符串（P/R/N/D），可以扩展库支持字符串值表断言，或断言"gear != P"。

---

## 7.6 用例 6：里程信号应随行驶累积

```robotframework
里程信号应随行驶累积
    ${odo1}=    Read Signal From Bus    ClusterData    Odometer
    Sleep    1s
    ${odo2}=    Read Signal From Bus    ClusterData    Odometer
    Should Be True    ${odo2} >= ${odo1}
    ...    msg=里程应单调不减: ${odo1} → ${odo2}
```

**测什么**：里程（odometer）是**累积量**：speed > 0 时应单调递增，熄火后保持不变。

**单调性测试**是累积量/计数器的通用验证模式（里程、油耗、循环计数、故障次数）。断言 `>=` 而不是 `>`——同一 200ms 周期内可能读到同一帧。

**举一反三**：还可以验证"精度"——`odo2 - odo1` 应约等于 `speed × (1/1800) km`（200ms 周期：speed × 0.2/3600）。超差说明累积系数错了。

---

## 7.7 用例 7：正常状态下所有警告灯报文应为熄灭

```robotframework
正常状态下所有警告灯报文应为熄灭
    ${raw}=    Wait For CAN Frame    0x300
    ${lit}=    Read Signal From Bus    TelltaleStatus    MilWarning
    Should Be Equal As Integers    ${lit}    0    msg=正常状态下 MIL 不应点亮
```

**测什么**：无故障时，TelltaleStatus 报文的警告灯 bit 全 0。

> ⚠️ 注意：**本用例只断言了 `MilWarning` 一位**；12 个灯的全量矩阵校验在 `scenarios/02_telltale_matrix.robot`（`All Telltales Should Be Off` / 自检后全灭）。真实项目里位图类报文必须做全矩阵，"只测一个 bit"是常见漏测模式——可以把它当扩展练习补全（FOR 循环遍历 12 个信号名逐个断言）。

**两个 API 的配合**：
- `Wait For CAN Frame 0x300`：等一帧指定 ID 的报文，返回 **hex 数据**（如 `0000`）——用于"看原始字节"的场景；
- `Read Signal From Bus TelltaleStatus MilWarning`：等同一帧并解码出**信号值**——用于"看物理值"的场景。

真实排故时这两个视角都要有：hex 看协议层（位对不对），信号值看应用层（灯该不该亮）。

**`Should Be Equal As Integers`**：RF 的字符串相等很严格（"0" vs 0），断言数值必须用 As Numbers 版本。库返回的是原生 int，这里再包一层更稳。

---

## 7.8 用例 8：录制总线并导出 Trace 文件（CANoe 兼容）

```robotframework
录制总线并导出 Trace 文件（CANoe 兼容）
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    ${n}=    Recorded Frame Count
    Should Be True    ${n} > 10    msg=2 秒内应录到多帧周期报文，实际 ${n}
    ${asc_n}=    Export Bus Trace    ${ROOT}/results/bus_trace.asc
    ${blf_n}=    Export Bus Trace    ${ROOT}/results/bus_trace.blf
    Should Be Equal As Integers    ${asc_n}    ${n}
    Should Be Equal As Integers    ${blf_n}    ${n}
    Trace Files Should Be Valid
    ...    ${ROOT}/results/bus_trace.asc    ${ROOT}/results/bus_trace.blf    ${n}
```

**测什么**：录 2 秒总线 → 导出 **ASC**（Vector 文本格式，可读、可 diff，适合归档）和 **BLF**（Vector 二进制格式，体积小、适合长时间录制）→ **回读校验**两种格式都能被解析、帧数与录制数一致。

**为什么值得单列一个用例**：自动化失败时，光有断言消息不够——"总线上到底发生了什么"才是定位证据。把 Trace 作为失败附件归档（本项目 suite teardown 还会导出 `results/bus_trace_last.asc`），开发可以拿它直接用 CANoe 打开分析时间线。这对应真实 Tier1 交付物里的 **Trace/日志留存要求**，也是"提缺陷必须带证据"的演练。

**真实工作对应**：CANoe 的 Trace 窗口 + "Save as ASC/BLF"；行业惯例 ASC 用于评审/归档、BLF 用于长时录制。对应关键字 `Export Bus Trace` / `Trace Files Should Be Valid`（见 `libraries/VehicleBusLibrary.py`）。

---

## 7.9 本章小结：CAN 信号测试的方法论

8 个用例覆盖了 5 类测试模式：

| 模式 | 用例 | 本质 |
|---|---|---|
| **存在性/周期性** | 用例 1 | 报文在不在、按不按周期来 |
| **范围/边界** | 用例 2、4、5 | 物理值在不在合理区间 |
| **一致性/联动** | 用例 3、6 | 信号之间的关系对不对 |
| **状态** | 用例 7 | 位图/枚举状态对不对 |
| **可观测性/Trace 归档** | 用例 8 | 失败后留证、可回放、可交给开发分析 |

把这 5 类模式套到任何车载信号上，就是一套完整的信号验证策略。**这也是写测试用例时的通用检查清单**——每加一个信号，问自己：存在性/范围/联动/状态/可留证，各有没有用例覆盖？

---

## 7.10 扩展练习（自己动手）

1. **周期抖动检查**：**已实现，可直接对照**——`Period And Jitter Should Be Within    0x100    100    period_tolerance_ms=20`（`scenarios/08_bus_health_monitor.robot` 在用；扩展目标是改成 200ms/500ms 再解释"为什么去重窗口是 5ms"）。
2. **水温升温曲线**：ECU 启动 `--coolant 125`，每 0.5s 读一次水温，记录到达 120℃ 的时间，断言 < 12s（模型约 8s，见 09 章时间线；提示：套件里用 FOR 循环 + `Sleep 0.5s`，收集值到列表）。
3. **报文长度校验**：扩展库加关键字 `Recorded Frames Should Have DLC 8`，验证 0x100/0x200 都是 8 字节。

---

## 7.11 本章自检

1. 01 套件的 8 条用例分别验证什么？哪一条最能体现"联动断言"的价值，为什么？
2. 为什么车速断言用 50~70 的范围，而不是 `Should Be Equal ${speed} 60`？读出两个动态值时容差应该怎么定？
3. `Wait Until Signal In Range` 与 `Wait Until Keyword Succeeds` 都能"重试"，语义差在哪？为什么库要专门提供前者？
4. 录制器模式（`Start Bus Recorder` → `Drain Bus For` → `Stop Bus Recorder` → `Recorded Frames Should Contain ID`）验证的是哪一类需求？为什么"录 2 秒再断言"比"收到一帧就断言"更适合周期报文？
5. 用例 8 把总线导出成 ASC/BLF 并用 `Trace Files Should Be Valid` 回读校验——这一步在真实缺陷处理中解决什么问题？
6. 如果 ECU 只发 0x100/0x200、不发 0x300，哪条用例会失败？失败信息能直接指向"哪个报文缺失"吗？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | 8 条 = 3 条周期报文存在性 / 车速范围 / 转速-车速联动 / 水温范围 / 档位随车速 / 里程单调 / 灯位图全 0 / Trace 导出。价值最高的是第 3 条：单看转速在"合理范围"发现不了模型系数写错（×60 还是 ×80），**联动断言**能立刻抓住 |
| 2 | 车速是**动态量**，且两次读取之间车还在加速（100ms 差约 0.8km/h → 转速差约 64rpm）；精确相等必然 flaky。容差按"物理过程 + 采样间隔"估（本项目给 ±10km/h、转速 ±300rpm），真实系统按规格容差 |
| 3 | `Wait Until Keyword Succeeds` 只判断"被等待的关键字有没有抛异常"，而 `Read Signal From Bus` 读到什么值都算成功 → 用它等范围是**无效等待**。`Wait Until Signal In Range` 把"值域判断"做进被等待的关键字里，超时仍未进范围才失败 |
| 4 | 验证"周期报文按设计 ID/周期发送"（总线符合性）。周期报文是持续行为，单帧只能证明"曾经发过一次"；录一个时间窗口再断言能同时覆盖**存在性 + 周期**，也是 CANoe "Trace + 过滤"的等价做法 |
| 5 | Trace 是失败现场证据：ASC 可读/可 diff、BLF 体积小适合长录；回读校验保证"导出的文件真的能被解析"（不是空文件/格式错），失败时能附给开发用 CANoe 复现时间线 |
| 6 | 第 1 条（三条周期报文应同时存在）会失败，`Recorded Frames Should Contain ID 0x300` 的失败信息直接指向缺失的 ID——这正是把"哪条报文缺失"编码进断言/关键字的价值 |

---

**上一章：[06 · 环境搭建与项目结构](06_环境搭建与项目结构.md)** ｜ **下一章：[08 · UDS 诊断测试实战](08_UDS诊断测试实战.md)**
