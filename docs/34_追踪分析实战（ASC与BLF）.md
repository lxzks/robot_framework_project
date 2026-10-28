# 34 · 追踪分析实战：从 ASC/BLF 里还原失败现场

> 测试跑完只看到 `PASS/FAIL` 是不够的。**失败现场的证据在 Trace 里**——
> 真实 Tier1 团队里，测试工程师大量时间在做同一件事：
> **拿到 Trace（ASC/BLF）→ 算总线负载 → 看周期/抖动 → 找丢帧/异常 ID → 写进缺陷单**。
>
> [07 章](07_CAN仿真测试实战.md)讲的是"怎么把总线录下来、导出成 CANoe 兼容格式"；
> 本章讲另一半：**怎么把 Trace 读回来做统计和异常检测**，并把分析结果变成证据。
>
> ```bash
> # 本模块三件套（全部新增，不改现有文件）
> simulators/asc_analyzer.py          # Trace 解析器 + 总线负载/周期抖动/异常检测 + HTML/CSV 报告 + CLI
> libraries/TraceAnalysisLibrary.py   # 15 个 RF 关键字（分析/断言/报告/合成夹具）
> tests/robot/16_trace_analysis.robot # 9 个用例（9/9 全绿，连跑两次一致，约 4 秒）
>
> # 跑本章套件（会起 ECU/仪表并真实录制 2 秒总——和 CAN 测试一样需要独占虚拟总线）
> .\.venv\Scripts\python.exe -m robot --outputdir results/dev_trace tests\robot\16_trace_analysis.robot
>
> # 命令行直接分析任意 ASC/BLF（不需要 RF）
> .\.venv\Scripts\python.exe simulators/asc_analyzer.py --input results\dev_trace\bus_recording.asc --out results\dev_trace\report.html
> ```

---

## 📖 零基础读法建议（先看这个）

| 优先级 | 小节 | 说明 |
|---|---|---|
| 🟢 **必读** | 34.1 / 34.2 | 为什么分析 Trace 是测试工程师的日常、ASC/BLF 到底长什么样 |
| 🟢 **必读** | 34.3 / 34.4 | 总线负载、周期、抖动三个核心指标怎么算（面试高频） |
| 🟢 **必读** | 34.5 / 34.7 | 异常检测规则 + 9 条用例逐条讲 |
| 🟡 了解 | 34.6 | 本项目实现（函数/关键字表）；会改统计口径时回来看 |
| 🟡 了解 | 34.8 | 真实项目工作流：录制→分析→缺陷单附 Trace→CI 门禁 |
| ⚪ 可跳过 | 34.2.2 的 ASC 列对齐细节 | 自己解析文本时再对照 |

**动手顺序**：先跑 16 套件看 9/9 全绿 → 打开 `results/dev_trace/bus_recording.asc` 用文本编辑器看原始行 →
命令行跑一次 `asc_analyzer.py` 看统计输出 → 最后做 34.9 自查题。

---

## 34.1 为什么测试工程师 70% 的时间在分析 Trace

先看一个真实感的场景：

> **缺陷单**：车辆行驶中仪表偶发短暂黑屏/指针归零，约 0.5 秒后恢复，复现概率 1/100。
>
> 没有 Trace 的时候：只能一遍遍复现、猜"是不是总线丢帧""是不是仪表进程卡了"。
> 有 Trace 的时候：把故障时刻前后的 ASC 拖进 CANoe，看 **0x100（车速）在那一刻是不是断了**：
> 正常 100ms 一帧，故障段出现一个 **600ms 的空档** → 立刻定位为"ECU 侧 6 帧丢失/总线仲裁异常"，
> 而不是"仪表渲染 bug"。再把这段 Trace + 分析报告附进缺陷单，开发 5 分钟就能看懂。

这就是 Trace 分析的价值：**把"偶发"变成"可回放的现场"**。它服务的环节贯穿测试全流程：

| 阶段 | Trace 用来干什么 | 本章对应能力 |
|---|---|---|
| 调试（Debug） | 用例失败了，看当时总线上到底发了什么 | 解析帧、按 ID 过滤、看数据 |
| 时序验证（Timing） | 周期/抖动是否满足设计；有没有丢帧 | 周期统计、抖动、异常检测 |
| 性能/健康度（Health） | 总线负载是否接近饱和；是否有异常 ID | 总线负载计算、未知 ID 检测 |
| 缺陷取证（Evidence） | 失败现场打包：Trace + 分析报告 + 截图 | HTML/CSV 报告导出 |
| 回归门禁（CI） | 每次跑完自动检查负载/抖动有没有退化 | CLI + 报告归档为 CI artifact |

> **面试话术**："我做的 Trace 工具链是闭环的：**录制→导出 CANoe 兼容格式→回读分析→异常检测→报告归档**。
> 它不是'录了个包'，而是能让失败可回放、让时序指标进 CI 门禁。"

---

## 34.2 ASC / BLF 格式与 CANoe 的关系

### 34.2.1 两种格式的分工

| 格式 | 类型 | 谁能打开 | 优点 | 缺点 | 用途 |
|---|---|---|---|---|---|
| **ASC** | Vector ASCII 文本 | CANoe / CANalyzer / 任何文本编辑器 | 可读、可 grep、可 diff、可 code review | 体积大（纯文本） | 小数据量归档、排故、教学 |
| **BLF** | Vector 二进制 | CANoe / CANalyzer / python-can | 体积小、支持长时间录制（实车路试标准） | 只能工具打开 | 大数据量、实车路试、自动化流水线 |

**关键认知**：ASC/BLF 是**行业通用交换格式**。你的测试工具产出这两种文件，才能进真实团队的工作流
（工程师用 CANoe 打开回放）；反过来，CANoe 在现场录的 Trace 你也能用 Python 分析——
本章的解析器两种格式都吃。

### 34.2.2 ASC 文件逐列看

本项目 `simulators/trace_tools.py` 导出的 ASC（真实录制的节选）：

```
date Wed Oct 28 09:58:24 2026               ← 录制日期（CANoe 头部）
base hex  timestamps absolute               ← 十六进制 ID + 时间戳模式
internal events logged
// version 1.0.0 (robot_framework trace_tools)
Begin Triggerblock Wed Oct 28 09:58:24 2026 ← 触发块开始
   0.000000 Start of measurement            ← 事件行（不是数据帧）
     0.000000 1  100             Rx   d 8 44 02 80 0C 82 0C 00 00
     0.104071 1  100             Rx   d 8 97 02 80 0C 82 0E 00 00
     0.104219 1  200             Rx   d 8 93 09 00 39 30 00 00 8A
     0.291983 1  300             Rx   d 2 00 00
End TriggerBlock
```

数据帧行的列含义：

| 列 | 示例 | 含义 |
|---|---|---|
| 1 | `0.104071` | 时间戳（秒，6 位小数） |
| 2 | `1` | 通道号 |
| 3 | `100` / `123x` | 仲裁 ID（十六进制；`x` 后缀 = 扩展帧 29 位） |
| 4 | `Rx` | 方向（Rx 接收 / Tx 发送） |
| 5 | `d` | 帧类型（d = 数据帧） |
| 6 | `8` | DLC（数据长度） |
| 7… | `44 02 80 …` | 数据字节（十六进制） |

> ⚠️ **本项目的 ASC 有一个"如实说明"的细节**：头部写的是
> `base hex  timestamps absolute`，但 `trace_tools.export_asc()` 实际写入的是
> **相对测量起点的时间**（第一帧 = `0.000000`），而不是 Unix 绝对时间。
> 这是导出器为了 CANoe 可读性做的简化——**分析器必须兼容它**：周期/抖动只看
> 相邻帧时间差，与时间起点无关；报告里再统一把时间轴归一化到 0。
> （真实 CANoe 的 "absolute" ASC 时间戳量级会大得多；解析器两种都吃。）

### 34.2.3 BLF：交给成熟库，不手写二进制

BLF 是 Vector 私有二进制格式（文件头魔数 `LOGG`），**手写字节必错**。本项目用
python-can 的 `can.BLFWriter` / `can.BLFReader` 读写——这也是行业标准做法：

```python
# 导出（trace_tools.export_blf 内部）
with can.BLFWriter(str(path), channel=1) as writer:
    for ts, aid, data, ext in frames:
        writer.on_message_received(can.Message(timestamp=ts, arbitration_id=aid,
                                               data=data, is_extended_id=ext))

# 回读（asc_analyzer.parse_blf 内部）
with can.BLFReader(str(path)) as reader:
    for message in reader:      # message.timestamp / arbitration_id / data / is_rx ...
        ...
```

`trace_tools.py` 里有一条很好的工程习惯：**"测试的测试"**——导出后用 BLFReader 回读校验
（`Trace Files Should Be Valid`），因为 Trace 打不开/内容缺失在真实项目里是致命问题。
本章的 `16` 套件更进一步：**同一份录制同时导出 ASC 与 BLF，断言两种格式解析结果完全一致**。

---

## 34.3 如何从 Trace 算总线负载

### 34.3.1 一帧到底占多少位

CAN 标准数据帧在总线上的位数（含所有帧开销，不含位填充）：

```
SOF(1) + 仲裁场(11) + RTR(1) + IDE(1) + r0(1) + DLC(4)
      + 数据场(8×DLC) + CRC(15) + CRC 分隔(1) + ACK(1) + ACK 分隔(1)
      + EOF(7) + IFS(3)
= 47 + 8 × DLC
```

| 帧类型 | 固定位数 | 公式 |
|---|---|---|
| 标准帧（11 位 ID） | 47 | `47 + 8×DLC` |
| 扩展帧（29 位 ID） | 67 | `67 + 8×DLC` |
| **位填充余量** | — | × **1.2**（最坏情况填充位可让帧长增加约 20%） |

### 34.3.2 负载率公式

```
总线负载(%) = Σ(每帧位数 × 1.2) / (比特率 × 记录时长) × 100
```

**算一例**（本章套件**某次** 2 秒录制的样例，每次运行会有 ±1 帧/几 ms 波动）：

- 0x100：20 帧 × 8 字节 → 每帧 (47+64)×1.2 = 133.2 位
- 0x200：10 帧 × 8 字节 → 133.2 位
- 0x300：4 帧 × 2 字节 → (47+16)×1.2 = 75.6 位
- 总位数 ≈ 20×133.2 + 10×133.2 + 4×75.6 ≈ **4298 位**
- 时长 1.969s、比特率 500 kbps → 负载 = 4298 / (500000×1.969) × 100 ≈ **0.4366%**

与 `asc_analyzer.compute_bus_load()` 的输出 `0.4365%` 一致（舍入差）。**这个量级说明教学仿真总线非常空闲**；
真实整车动力 CAN 常见 30%~60%，舒适 CAN 常见 10%~30%。

### 34.3.3 为什么负载要进测试门禁

- 负载过高 → 高优先级报文延迟、低优先级报文可能被"饿死"（极端场景下丢帧）；
- 负载变化 → 新功能悄悄加了周期报文（"就加一条 10ms 报文"是最常见的负载炸弹）；
- **门禁姿势**：CI 里每次跑完分析 Trace，负载超过阈值（如 50%）就让流水线失败，
  并在报告里记录数值趋势（本章的 `Bus Load Should Be Below` + CSV 报告就是最小实现）。

---

## 34.4 周期与抖动分析：均值正常不代表没问题

### 34.4.1 三个指标

给定某 ID 的时间戳序列 `t0, t1, t2, ...`，相邻间隔 `g_i = t_{i+1} - t_i`：

| 指标 | 公式 | 说明 |
|---|---|---|
| 周期均值 | `mean(g)` | 平均多久发一帧（设计值，如 0x100 应 100ms） |
| 周期标准差 | `std(g)` | 间隔的离散程度（越小越稳） |
| **抖动 Jitter** | `max(g) - min(g)` | 最大间隔与最小间隔之差——**时序稳定性的直接指标** |

### 34.4.2 为什么"均值正常"会骗人

假设某 ECU 设计 100ms 发一帧，某次测试录到间隔：

```
100, 100, 100, 160, 100, 40, 100, 100, 100, 100   → 均值恰好 = 100ms！
```

均值完美，但那两帧一个晚了 60ms、一个早了 60ms——接收方（如 ABS）的判断逻辑
可能已经出问题。**所以周期断言必须配抖动断言**：

```robotframework
Period Should Be Near     trace.asc    0x100    expected_ms=100    tolerance_ms=25
Jitter Should Be Within   trace.asc    0x100    max_jitter_ms=100
```

### 34.4.3 本项目实测数字（2 秒录制，500 kbps）

| ID | 帧数 | 周期均值 | 标准差 | 间隔范围 | 抖动 |
|---|---|---|---|---|---|
| 0x100 车速 | 20 | **103.640 ms** | 0.497 ms | [102.883, 104.524] ms | **1.641 ms** |
| 0x200 仪表 | 10 | **207.325 ms** | — | — | **2.967 ms** |
| 0x300 警告灯 | 4 | **507.655 ms** | — | — | **2.335 ms** |

> **为什么 0x100 实测 103.7ms 而不是 100ms？** 因为仿真 ECU 的主循环是"10ms 睡一次、
> 累计到 ≥100ms 才发"的软件调度（`t_100 >= 0.1`），实际周期 ≈ 100 + 调度粒度；
> 真实 ECU 用硬件定时器，周期精度通常在 ±1% 以内。**Trace 分析的价值之一就是
> 暴露"设计值 vs 实测值"的差距**——所以用例的容差按接收方超时阈值定（25ms），
> 而不是写死 100ms 精确相等（那会让用例永远红）。

> 📌 **关于数字波动**：录制类 Trace 每次运行的帧数/周期均值/抖动都会有**小幅波动**
> （录制窗口对齐、进程调度、虚拟总线多网卡回环去重时机）。本章所有数字都是**某次运行的
> 样例**，用例里断言的是**范围/容差**（帧数下限、周期 ±25ms、抖动上限、负载区间），
> 不是精确值——这正是"录制型测试"和"确定性单元测试"的区别：**统计型断言必须留裕度**。

---

## 34.5 异常检测：丢帧 / 间隔异常 / 未知 ID

`detect_anomalies(frames, expected_periods={0x100: 0.1, 0x200: 0.2, 0x300: 0.5}, tolerance=0.5)`
用三条规则覆盖现场最常见的问题：

| 类型 | 判定规则 | 现场含义 | 严重度 |
|---|---|---|---|
| **missing_frame**（丢帧） | 相邻间隔 > 期望周期 × (1+tolerance) | ECU/总线丢帧、节点卡死、仲裁失败；估算丢帧数 = `round(间隔/周期) - 1` | high |
| **interval_too_short**（间隔过短） | 相邻间隔 < 期望周期 × (1−tolerance) | 重复帧、突发发送、时钟源异常 | medium |
| **unknown_id**（未知 ID） | 出现不在期望周期清单里的 ID | 新节点上线、报文配错、诊断/网络管理报文混入应用 Trace | medium |

**为什么用"容差比例"而不是"±N ms"**：容差应该跟着期望周期缩放——
100ms 报文 ±50ms 和 500ms 报文 ±50ms 的严重程度完全不同。默认 `tolerance=0.5`（±50%）
是教学取值；真实项目按协议规范（如 ±10%~20%）收紧。

**负向用例怎么证明检测不是恒真**：用 `Write Synthetic Trace` 手工构造"已知坏"的 Trace：

```robotframework
# 0x100 在 0.2s → 0.4s 之间缺了一帧（期望 100ms，实际间隔 200ms）
# 注意：Evaluate 的 Python 表达式必须写在**一行**里（RF 的 ... 续行是"追加参数"，不是拼接字符串）
${frames}=    Evaluate    [[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"], [0.2, 0x100, "1122334455667788"], [0.4, 0x100, "1122334455667788"], [0.5, 0x100, "1122334455667788"]]
Write Synthetic Trace    missing.asc    ${frames}
Anomalies Should Be Detected    missing.asc    anomaly_type=missing_frame
```

实测：检测到 1 条 `missing_frame`，间隔 200ms，估算丢 1 帧。**这条用例通过，才说明检测算法
真的在算间隔，而不是永远返回"无异常"。**

---

## 34.6 本项目实现

### 34.6.1 数据流全景

```
ecu_sim.py 周期报文 ──► 虚拟 CAN 总线（UDP 组播）
                              │
              VehicleBusLibrary.Start Bus Recorder（录制器）
                              │
                  trace_tools.export_asc / export_blf（CANoe 兼容格式）
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
        bus_recording.asc       bus_recording.blf     ← 同一份录制的两种格式
                    └─────────┬─────────┘
                              ▼
            asc_analyzer.parse_trace / analyze（解析 + 统计 + 异常检测）
                              ▼
     TraceAnalysisLibrary 15 个 RF 关键字（断言 + 报告 + 合成夹具）
                              ▼
            results/dev_trace/trace_report.html / .csv（缺陷证据包）
```

### 34.6.2 `simulators/asc_analyzer.py` 函数表

| 函数 | 作用 |
|---|---|
| `parse_asc(path)` / `parse_asc_with_meta(path)` | 解析 ASC 文本，返回 `list[Frame]`；`with_meta` 额外返回行数/跳过行/坏行/时间戳模式 |
| `parse_blf(path)` / `parse_blf_with_meta(path)` | 用 `can.BLFReader` 解析 BLF |
| `parse_trace(path)` | **自动识别** ASC/BLF（扩展名 → 魔数 `LOGG` → 文本） |
| `normalize_time(frames)` | 时间轴归一化到 0 起点（ASC 相对时间 / BLF 绝对时间统一口径） |
| `time_range(frames)` | 返回 (起, 止, 时长秒) |
| `frame_bits(frame)` | 单帧位数估算（标准 47+8×DLC / 扩展 67+8×DLC，×1.2 位填充余量） |
| `compute_bus_load(frames, bitrate=500000, duration=None)` | 总线负载率（%） |
| `id_stats(frames)` | 按 ID 汇总：帧数/DLC/速率/周期均值/标准差/极值/抖动 |
| `period_stats(frames, arbitration_id)` | 单 ID 周期统计（mean/std/min/max/jitter，单位 ms） |
| `detect_anomalies(frames, expected_periods=..., tolerance=0.5)` | 异常检测（丢帧/间隔过短/未知 ID） |
| `build_report(frames, bitrate=...)` | 汇总成报告 dict（分析/导出共用） |
| `analyze(path, bitrate=500000)` | 一步到位：解析 + 报告（空/坏文件抛清晰 ValueError） |
| `export_csv(report, path)` / `export_html(report, path)` / `export_report(report, path)` | 教学版报告导出（按扩展名选格式） |
| `Frame` dataclass | 统一帧表示：timestamp / arbitration_id / data / is_extended / channel / is_rx |
| `main()` | CLI：`--input` / `--out` / `--bitrate` |

**关键常量**（库/文档/CLI 共用一份，不写两套）：

| 常量 | 值 | 含义 |
|---|---|---|
| `DEFAULT_BITRATE` | `500_000` | 本项目虚拟总线比特率 |
| `EXPECTED_PERIODS` | `{0x100: 0.1, 0x200: 0.2, 0x300: 0.5}` | 三条应用报文的期望周期（秒） |
| `STUFFING_OVERHEAD` | `1.2` | 位填充余量（20%） |
| `BASE_FRAME_BITS` / `EXTENDED_FRAME_BITS` | `47` / `67` | 帧固定开销位数 |
| `DEFAULT_ANOMALY_TOLERANCE` | `0.5` | 间隔偏离期望周期 ±50% 触发异常 |
| `BLF_MAGIC` | `b"LOGG"` | BLF 文件魔数（自动识别用） |

### 34.6.3 CLI 实测输出

```text
$ .\.venv\Scripts\python.exe simulators/asc_analyzer.py --input results\dev_trace\bus_recording.asc --out results\dev_trace\report.html
====================================================================
 Trace 分析: results\dev_trace\bus_recording.asc
====================================================================
 格式: asc   帧数: 34   时长: 1.969s
 唯一 ID: 3   总线负载: 0.4365%（500000 bps）
    0x100  帧数  20  周期均值 103.640ms  抖动 1.641ms
    0x200  帧数  10  周期均值 207.325ms  抖动 2.967ms
    0x300  帧数   4  周期均值 507.655ms  抖动 2.335ms
 异常: 0 条
[asc_analyzer] 报告已导出: results\dev_trace\report.html
```

### 34.6.4 `libraries/TraceAnalysisLibrary.py` 关键字表（15 个）

| # | 关键字 | 回答什么问题 | 返回 |
|---|---|---|---|
| 1 | `Analyze Trace File` | 这份 Trace 整体长什么样（格式/帧数/ID/负载/异常） | 报告 dict |
| 2 | `Trace Frame Count Should Be` | 帧数对不对（精确值 / 下限 / 上限） | 实际帧数 |
| 3 | `Trace Should Contain ID` | 某条报文在不在（且至少 N 帧） | 帧数 |
| 4 | `Get Trace Bus Load` | 总线负载率是多少 | 百分比 |
| 5 | `Bus Load Should Be Below` | 负载是否低于门禁阈值 | 百分比 |
| 6 | `ID Stats Should Report` | 某 ID（或全部）的帧数/速率/周期/抖动 | 统计 dict |
| 7 | `Period Should Be Near` | 周期均值是否接近设计值（±容差） | 均值 ms |
| 8 | `Jitter Should Be Within` | 抖动是否在允许范围 | 抖动 ms |
| 9 | `Anomalies Should Be Detected` | 有没有检出指定类型/ID 的异常 | 异常列表 |
| 10 | `Anomaly Count Should Be` | 异常数量对不对（可先按类型/ID 过滤） | 条数 |
| 11 | `Export Analysis Report` | 导出 HTML/CSV 证据报告 | 报告路径 |
| 12 | `Generate Trace From Bus Recorder` | 独立录一段总线并生成 Trace（不依赖别的录制器） | Trace 路径 |
| 13 | `Trace Time Range Should Be` | Trace 时间跨度是否对（归档完整性） | 时长 s |
| 14 | `Compare Two Traces` | 两份 Trace 的 ID 集合/帧数/周期是否一致（ASC vs BLF、两次 run） | 差异摘要 dict |
| 15 | `Write Synthetic Trace` | 造"已知坏 Trace"夹具（负向用例用） | Trace 路径 |

> 设计说明：库内部对解析结果做了**缓存**（按文件 mtime+size 失效），同一份 Trace 被
> 9 条用例反复断言也只解析一次；`Analyze Trace File` / `Export Analysis Report` 会强制重解析，
> 保证读到的是当前文件内容。

---

## 34.7 逐条讲 `tests/robot/16_trace_analysis.robot`（9 条用例）

**套件骨架**：本套件需要真实总线数据，所以用 `Resource ../../resources/common.resource`
起 ECU/仪表；Suite Setup 只录一次（2 秒）并导出两种格式，用例只做分析，避免用例之间
抢录制造成顺序依赖。全套约 **4.1 秒**（含环境启动与 2 秒录制）。

```robotframework
*** Settings ***
Resource         ../../resources/common.resource
Library          ../../libraries/TraceAnalysisLibrary.py
Suite Setup      启动环境并录制导出 Trace     # 起 ECU → 录 2s → 导出 ASC + BLF
Suite Teardown   Stop Vehicle Environment
```

| # | 用例 | 需求标签 | 断言层次 |
|---|---|---|---|
| 1 | 项目导出的 ASC 应能解析且帧数时长合理 | REQ-TRACE-001 | 解析 + 完整性 |
| 2 | ASC 应包含三条周期报文 | REQ-TRACE-001 | 存在性 |
| 3 | 总线负载应在合理范围 | REQ-TRACE-002 | 负载计算 |
| 4 | 0x100 周期与抖动应满足设计值 | REQ-TRACE-003 | 时序指标 |
| 5 | 缺帧的 Trace 应被检测为丢帧异常 | REQ-TRACE-003 | 异常检测（负向） |
| 6 | 未知 ID 应被检测出来 | REQ-TRACE-003 | 异常检测（负向） |
| 7 | ASC 与 BLF 两种格式应解析出一致结果 | REQ-TRACE-001 | 格式一致性 |
| 8 | 分析报告应能导出 HTML 与 CSV 且内容非空 | REQ-TRACE-002 | 证据产出 |
| 9 | 空文件或坏格式应给出清晰报错 | REQ-TRACE-001 | 健壮性 |

### 34.7.1 用例 1：ASC 解析 + 帧数/时长合理

```robotframework
项目导出的 ASC 应能解析且帧数时长合理
    [Tags]    REQ-TRACE-001
    ${report}=    Analyze Trace File    ${ASC_TRACE}
    Should Be Equal    ${report}[format]    asc
    Trace Frame Count Should Be    ${ASC_TRACE}    minimum=20
    Trace Time Range Should Be    ${ASC_TRACE}    expected_seconds=2.0    tolerance_seconds=0.6
```

**测什么**："录制→导出→分析"链路能跑通。录制 2 秒实测得到 **34 帧 / 1.969 秒**（首末帧不落在
录制边界上，所以跨度略小于 2 秒，容差 0.6s）。

### 34.7.2 用例 2：三条周期报文都存在

```robotframework
    Trace Should Contain ID    ${ASC_TRACE}    0x100    min_frames=5
    Trace Should Contain ID    ${ASC_TRACE}    0x200    min_frames=3
    Trace Should Contain ID    ${ASC_TRACE}    0x300    min_frames=2
```

**测什么**：帧数下限按各自周期推算（2 秒：0x100 约 20 帧、0x200 约 10 帧、0x300 约 4 帧）。
实测出现 20 / 10 / 4 帧。**用"下限"而不是"精确值"**：录制窗口对齐方式会让帧数有 ±1 的浮动。

### 34.7.3 用例 3：总线负载

```robotframework
    ${load}=    Get Trace Bus Load    ${ASC_TRACE}
    Should Be True    0 < ${load} < 50    msg=总线负载 ${load}% 不合理
    Bus Load Should Be Below    ${ASC_TRACE}    max_percent=50
    Should Be True    ${load} < 5    msg=教学仿真环境总线负载应远低于 5%（实测 ${load}%）
```

**测什么**：负载计算既不是恒零（`>0`），也远未饱和（`<5%`，实测 **0.4365%**）。
真实项目门禁常设 30%~50%；这里保留 50% 的"硬上限断言"作教学，再补一条更严的 <5%
体现"仿真环境本应很空闲"。

### 34.7.4 用例 4：周期与抖动

```robotframework
    ID Stats Should Report    ${ASC_TRACE}    0x100    min_frames=10
    Period Should Be Near    ${ASC_TRACE}    0x100    expected_ms=100    tolerance_ms=25
    Jitter Should Be Within    ${ASC_TRACE}    0x100    max_jitter_ms=100
    Period Should Be Near    ${ASC_TRACE}    0x200    expected_ms=200    tolerance_ms=50
```

**测什么**：0x100 实测周期均值 **103.640ms**（仿真 ECU 的 10ms 调度粒度导致略偏大）、
抖动 **1.641ms**；0x200 实测 **207.325ms**。`ID Stats Should Report` 同时把完整统计
（帧数/DLC/速率/标准差/极值/抖动）打进 RF 日志——失败时不用重跑就能看到数字。

### 34.7.5 用例 5：缺帧必须被检测（负向/灵敏度）

```robotframework
    # 完整列表见 16 套件；核心是 0.2s → 0.4s 缺一帧（Evaluate 表达式必须一行写完）
    ${frames}=    Evaluate    [[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"], [0.2, 0x100, "1122334455667788"], [0.4, 0x100, "1122334455667788"], [0.5, 0x100, "1122334455667788"]]
    Write Synthetic Trace    ${MISSING_TRACE}    ${frames}
    ${anomalies}=    Anomalies Should Be Detected    ${MISSING_TRACE}
    ...    anomaly_type=missing_frame    arbitration_id=0x100
    Anomaly Count Should Be    ${MISSING_TRACE}    expected=1    anomaly_type=missing_frame
    ${gap}=    Set Variable    ${anomalies}[0][actual_gap_ms]
    Should Be True    ${gap} > 150        # 丢帧间隔应约 200ms
    ${missing}=    Set Variable    ${anomalies}[0][missing_estimated]
    Should Be Equal As Integers    ${missing}    1
```

**测什么**：**"测试的测试"**——手工构造已知缺一帧的 Trace，检测器必须报
`missing_frame`、实际间隔 200ms、估算丢 1 帧。如果检测算法写错（比如容差设 10 倍、
或根本没遍历间隔），这条会红。**负向用例证明异常检测不是恒真。**

### 34.7.6 用例 6：未知 ID 必须被检测

```robotframework
    # 合成 Trace：0x100 正常 100ms + 混入 0x123（不在期望清单里）
    ${frames}=    Evaluate    [[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"], [0.2, 0x100, "1122334455667788"], [0.05, 0x123, "AABB"]]
    Write Synthetic Trace    ${UNKNOWN_TRACE}    ${frames}
    ${anomalies}=    Anomalies Should Be Detected    ${UNKNOWN_TRACE}    anomaly_type=unknown_id
    Should Be Equal    ${id_hex}    0x123
```

**测什么**：出现期望周期清单之外的 ID 必须被点名。对应真实场景："新节点上线没通知"、
"报文配错 ID"、"诊断/网络管理报文混进应用 Trace"。**注意是"按 ID 报一条"而不是"每帧报一条"**
（同一未知 ID 出现 100 帧不该刷 100 条异常）。

### 34.7.7 用例 7：ASC 与 BLF 解析一致

```robotframework
    ${asc_report}=    Analyze Trace File    ${ASC_TRACE}
    ${blf_report}=    Analyze Trace File    ${BLF_TRACE}
    Should Be Equal    ${blf_report}[format]    blf
    Should Be Equal As Integers    ${asc_report}[frames]    ${blf_report}[frames]
    Compare Two Traces    ${ASC_TRACE}    ${BLF_TRACE}
```

**测什么**：同一份录制导出两种格式，解析结果必须等价：**34/34 帧、ID 集合一致、
周期均值最大差异 0.001ms**。真实价值：ASC 给人看、BLF 给工具跑，如果两种格式分析结论不同，
说明工具链有 bug（时间戳精度/方向/扩展帧标志等）。这也是对 `trace_tools` 导出器的交叉验证。

### 34.7.8 用例 8：报告导出（证据包）

```robotframework
    ${html}=    Export Analysis Report    ${ASC_TRACE}    ${TRACE_OUT}/trace_report.html
    ${csv}=     Export Analysis Report    ${ASC_TRACE}    ${TRACE_OUT}/trace_report.csv
    Should Contain    ${html_content}    总线负载
    Should Contain    ${csv_content}    0x100
```

**测什么**：失败现场证据包 = **Trace + 分析报告**。HTML（约 3KB）含总览卡片、ID 统计表、
异常表（异常行红/黄高亮），可直接贴缺陷单；CSV（约 0.7KB）Excel 可直接打开（utf-8-sig
防中文乱码）。真实 CI 里这两个文件进 artifact，失败单直接引用。

### 34.7.9 用例 9：空文件/坏格式的清晰报错

```robotframework
    Create File    ${EMPTY_TRACE}
    Run Keyword And Expect Error    *没有解析到任何数据帧*    Analyze Trace File    ${EMPTY_TRACE}
    Create File    ${GARBAGE_TRACE}    this is not a can trace file\n123 456\n
    Run Keyword And Expect Error    *没有解析到任何数据帧*    Analyze Trace File    ${GARBAGE_TRACE}
```

**测什么**：解析器健壮性。空文件、非 Trace 文本都不能抛 Python 堆栈，必须报
"没有解析到任何数据帧（空文件/格式不支持/内容损坏）"+ 行数统计。**错误信息质量也是被测对象**：
真实排故时，一句能看懂的报错能省半小时。

### 34.7.10 实测结果

```
==============================================================================
16 Trace Analysis :: 套件 16：Trace 分析（ASC/BLF 解析 + 总线负载 + 周期抖动 + 异常检测）
==============================================================================
项目导出的 ASC 应能解析且帧数时长合理                          | PASS |
ASC 应包含三条周期报文                                          | PASS |
总线负载应在合理范围                                            | PASS |
0x100 周期与抖动应满足设计值                                    | PASS |
缺帧的 Trace 应被检测为丢帧异常                                 | PASS |
未知 ID 应被检测出来                                            | PASS |
ASC 与 BLF 两种格式应解析出一致结果                             | PASS |
分析报告应能导出 HTML 与 CSV 且内容非空                          | PASS |
空文件或坏格式应给出清晰报错                                    | PASS |
------------------------------------------------------------------------------
9 tests, 9 passed, 0 failed    （连跑两次均 9/9；单次约 4.1~4.2 秒，含环境启动+2s 录制）
```

---

## 34.8 真实项目工作流

### 34.8.1 录制 → 分析 → 缺陷单（标准动作）

```
① 测试失败
   ↓
② 保留现场：Trace（ASC/BLF）+ 失败截图 + 诊断日志 一起归档（CI artifact / 本地 results/）
   ↓
③ 用本模块分析（或 CANoe 打开回放）：
     asc_analyzer.py 算负载/周期/抖动/异常 → HTML/CSV 报告
   ↓
④ 写缺陷单：
     现象 + 复现步骤 + 期望/实际 + Trace 文件 + 分析报告 + 具体帧号/时间点
   ↓
⑤ 开发拿着 Trace 在 CANoe 里逐帧回放 → 定位是 ECU 发送问题还是接收方问题
```

> **加分细节**：缺陷单里写"0x100 在 12.345s 处间隔 603ms，疑似连续丢 6 帧（正常 100ms）"
> 比写"仪表黑屏"有价值一百倍——**可复现、可定位、可回归验证**。

### 34.8.2 工具对照：本项目 vs 商业工具

| 能力 | 商业工具 | 本项目等价物 |
|---|---|---|
| 录制 Trace | CANoe / CANalyzer / VN 硬件 | `VehicleBusLibrary.Start Bus Recorder` / `Generate Trace From Bus Recorder` |
| 导出 ASC/BLF | CANoe 保存 | `trace_tools.export_asc / export_blf` |
| Trace 回放/过滤 | CANoe Trace 窗口 | `asc_analyzer.parse_trace` + 按 ID 统计 |
| 总线统计 | CANoe Statistics 窗口 | `compute_bus_load` / `id_stats` |
| 周期/抖动分析 | CANoe 图形化统计 | `period_stats` / `Period Should Be Near` / `Jitter Should Be Within` |
| 异常检测 | CANoe 触发条件 + 人工看 | `detect_anomalies`（丢帧/未知 ID 规则化） |
| 报告导出 | CANoe 报告 / 自研平台 | `export_html` / `export_csv` |
| 自动化门禁 | CI 集成商业工具 | CLI + RF 断言，天然进 CI |

### 34.8.3 进 CI 门禁的三条建议

1. **归档一切**：每次跑都保存 Trace 与分析报告（artifact），失败时无需重新复现；
2. **指标趋势化**：把总线负载/周期均值/抖动写进报告（CSV），版本间对比看退化；
3. **阈值门禁化**：负载/抖动超阈值让流水线失败——但不能拍脑袋设阈值，
   要按**协议规范 + 接收方超时裕度**推导（如 100ms 报文允许 ±20%，则抖动 < 40ms）。

### 34.8.4 局限与边界（如实说明）

- 本项目的解析器是**教学版**：覆盖 Vector ASC 常见数据帧行与 BLF 数据帧；真实 CANoe
  文件可能有 CAN FD 帧、错误帧、远程帧、GPS/以太网事件等扩展行（未实现，遇到会跳过/计数）；
- 总线负载是**位估算**（47+8×DLC ×1.2），不是 CANoe 的实测位时间统计，用于趋势/门禁足够，
  不能替代硬件级测量；
- 异常检测是**规则型**（间隔阈值/未知 ID），不做 AUTOSAR E2E、网络管理状态机、丢帧序列号分析——
  真实项目要按协议扩展规则（例如结合 E2E 计数器判断"应发未发"）。

---

## 34.9 本章自检

1. ASC 和 BLF 各适合什么场景？为什么 "ASC 给人看、BLF 给工具跑"？
2. 一个标准 CAN 数据帧占多少位？8 字节数据的 0x100 报文的位数怎么算？为什么要乘 1.2？
3. 总线负载率公式是什么？为什么负载要进 CI 门禁？
4. 周期均值正常但抖动很大，可能造成什么后果？抖动怎么定义？
5. 丢帧异常怎么从时间戳判断？"估算丢帧数"怎么算？
6. 为什么要用合成 Trace 做负向用例？它证明了什么？
7. 真实项目里，一份完整的"失败现场证据包"应包含什么？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | ASC 文本可读、可 grep/diff/code review，体积大；BLF 二进制体积小、支持长时间录制，是实车路试标准。可读性 vs 体积/性能的分工 |
| 2 | 标准帧 **47 + 8×DLC** 位（SOF 1 + 仲裁 11 + 控制 + CRC 15 + ACK + EOF 7 + IFS 3）；8 字节 = 47+64 = 111 位。位填充最坏约 +20% → ×1.2 |
| 3 | 负载 = Σ(每帧位数×1.2) / (比特率×时长) × 100。高负载会导致高优先级报文延迟/低优先级饥饿，新功能加报文是常见"负载炸弹"，所以必须门禁 |
| 4 | 接收方可能超时/误判（如 ABS 判断延迟）。抖动 = **max(间隔) − min(间隔)**；均值会被"迟到的帧"和"早到的帧"互相抵消 |
| 5 | 相邻帧间隔 > 期望周期×(1+容差) 判丢帧；估算丢帧数 = round(间隔/周期) − 1（例：200ms/100ms → 丢 1 帧） |
| 6 | 负向用例证明异常检测**不是恒真**（不会总是返回"无异常"）；合成已知坏数据 → 必须报指定类型异常，才能说明检测规则真的生效 |
| 7 | Trace（ASC/BLF）+ 分析报告（HTML/CSV）+ 失败截图 + 诊断日志 + 复现步骤/时间点；核心是"可回放、可定位、可回归" |

---

**上一章**：[33 · 工程工具链实战](33_工程工具链实战.md)　|　**下一章**：[35 · OTA 与 Bootloader 全链路](35_OTA与Bootloader全链路.md)

> 📌 扩展篇导航：[22 全景](22_车载自动化测试全景与能力地图.md) → [23 LIN](23_LIN总线与车身控制测试.md) → [24 DoIP](24_车载以太网与DoIP诊断.md) → [25 HMI 视觉](25_HMI视觉自动化测试.md) → [26 非功能](26_非功能测试实战.md) → [27 信息安全](27_信息安全测试入门.md) → [28 功能安全/ASPICE](28_功能安全与ASPICE测试过程.md) → 29 CAN FD → 30 SOME/IP → 31 功能寻址与 OBD-II → 32 安全机制 → [33 工程工具链](33_工程工具链实战.md) → **34 追踪分析（本章）** → [35 OTA/Bootloader](35_OTA与Bootloader全链路.md) → [36 ODX/OTX](36_ODX与OTX数据库驱动测试.md) → [37 Appium](37_Appium与真机UI自动化.md) → [38 HIL](38_HIL台架与电源IO验证.md) → [39 能力自检](39_能力自检与下一步学习路线.md)
>
> 章节目录与读法见 [00 · 学习路线图](00_学习路线图.md)；Trace 导出详见 [07 · CAN 仿真测试实战](07_CAN仿真测试实战.md)；CI 与交付链见 [13 章](13_测试工程化与CI.md)、[20 章](20_测试规范与验证交付物.md)。


