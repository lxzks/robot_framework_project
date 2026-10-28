# 36 · ODX/OTX 数据库驱动测试

> 前 35 章里，诊断请求都是**手写 hex**：`22 F1 90` 读 VIN、`10 03` 进扩展会话。
> 这在教学项目里没问题，但真实项目的诊断定义有**几千个 DID/服务/DTC**，还要
> 区分车型/配置（变体）——手写 hex 的测试根本维护不动。
>
> 工业界的答案是"**数据库 + 测试序列**"：**ODX（ISO 22901）** 把诊断数据
> （DID/服务/DTC/编码规则）定义成单一事实源；**OTX（ISO 13209）** 把测试序列
> 定义成可交换的 XML。测试脚本只写"读 VIN"、"进扩展会话"，由工具按数据库
> 生成请求、按定义解码响应。
>
> 本章讲清三件事：**ODX 里有什么、OTX 序列怎么执行、数据库与 ECU 版本不一致
> 时会怎样**；然后逐条讲 `tests/robot/18_odx_database.robot` 的 9 个用例；
> 最后映射到 CANoe/ODX Studio/vTESTstudio 的真实工作流。
>
> 前置：03 章（UDS 协议）、08 章（UDS 诊断测试实战）、31 章（DID/功能寻址等
> 进阶服务）、35 章（OTA 会新增 F195/F1A3/F186 版本类 DID，本章的数据库已收录）。

---

## 📖 零基础读法建议（先看这个，能省你半天）

| 标记 | 章节 | 为什么这个优先级 |
|---|---|---|
| 🟢 **必读** | **36.2 ODX 结构与核心元素**、**36.4 OTX 测试序列**、**36.6 逐条讲 18 套件** | 你每天工作都要用：读 ODX、按 DID 名生成请求、跑/改 OTX 序列 |
| 🟡 **了解即可** | 36.1 为什么用数据库、36.3 数据库与 ECU 一致性、36.7 负向测试 | 面试常问"数据驱动测试怎么落地"；一致性是版本管理的核心 |
| ⚪ **可以跳过** | 36.8 真实项目工具（ODX Studio/CANoe 配置细节）、36.9 扩展练习 | 入职用真实工具链时再回看 |

> 💡 **为什么可以跳过工具细节**：不同 OEM 用的工具链差异很大（Vector、dSPACE、
> Elektrobit、各家的 ODX 编辑器），但**核心概念完全一样**：DIAG-LAYER 定义 ECU、
> DATA-IDENTIFIER 定义 DID、SERVICE 定义服务、OTX 定义测试序列。
> 学会概念，换工具只是换界面。
>
> **本章的验收标准只有一条**：给你一份 ODX，你能说出"VIN 是哪个 DID、多长、
> 怎么解码"；给你一份 OTX，你能说出"第几步在测什么、失败时能不能定位到步骤"。

---

## 36.1 为什么工程要用"诊断数据库"（手写 hex 的四个痛点）

### 36.1.1 手写 hex 在真实项目里会烂掉

本项目 `tests/robot/02` 可以写 `Read DID As ASCII    F190`，因为 DID 只有几个。
真实项目里：

| 痛点 | 手写 hex 的后果 |
|---|---|
| **数量大** | 一个 ECU 几百个 DID、几十个服务、上百个 DTC；测试脚本里到处是魔数 |
| **多车型/多配置** | 同一功能在不同车型 DID 不同（变体管理）→ 每个车型复制一套脚本 |
| **版本漂移** | ECU 软件升级后 DID 长度/编码变了，脚本里的硬编码不会自动跟着变 |
| **评审与追溯** | 审核问"DID F1A0 的测试覆盖在哪"，只能 grep 十六进制字符串 |

### 36.1.2 数据库驱动测试长什么样

```
           手写 hex 的方式                         数据库驱动的方式
  ┌──────────────────────────────┐      ┌──────────────────────────────────┐
  │ Send UDS Request   22 F1 90  │      │ Read DID By Name    VIN          │
  │ ${data}= Read DID As ASCII   │      │ （读 ODX：VIN=F190，17B ASCII）   │
  │   F190                       │      │ 自动生成 22 F1 90、自动解 ASCII   │
  └──────────────────────────────┘      └──────────────────────────────────┘
        魔数散落在用例里                      数据库是唯一事实源，用例只写语义
```

**核心收益**：
1. **单一事实源**：DID 的 ID/长度/编码只在 ODX 里定义一次；
2. **可移植**：换车型只换 ODX/变体选择，用例不动；
3. **可校验**：数据库与 ECU 版本不一致时，测试会**明确报错**（而不是悄悄错位解码）；
4. **可交换**：OEM 发 PDX/ODX 给 Tier1，测试工具导入即用（不用人工翻译）。

### 36.1.3 ODX / OTX / CDD / PDX 的关系（一句话版）

| 名词 | 全称 | 是什么 | 本项目 |
|---|---|---|---|
| **ODX** | Open Diagnostic data eXchange（ISO 22901） | 诊断**数据**格式：DID/服务/DTC/编码/变体 | `simulators/diag/vehicle.odx.xml`（教学子集） |
| **OTX** | Open Test sequence eXchange（ISO 13209） | 测试**序列**格式：请求/断言/流程控制 | `simulators/diag/vehicle.otx.xml`（教学子集） |
| **CDD** | CANdela Diagnostic Description（Vector 私有） | 另一种诊断描述格式（工具内部） | 无（讲解用） |
| **PDX** | ODX 打包格式（zip） | 把多个 ODX 文件打包成交付物 | 无（讲解用） |

---

## 36.2 ODX 结构与核心元素（重点）

### 36.2.1 真实 ODX 的分层结构（了解）

真实 ODX 是带命名空间的 XML，核心是**分层 DIAG-LAYER**：

```
ODX
├── DIAG-LAYER 层级（从通用到具体，可以继承/引用）
│   ├── PROTOCOL                 ← 协议层（UDS/KWP/ISO 15031）
│   ├── FUNCTIONAL-GROUP         ← 功能组（如"排放相关"）
│   ├── BASE-VARIANT             ← 基础变体（一个平台共用）
│   └── ECU-VARIANT              ← 具体 ECU 变体（车型/配置）
│         ├── DATA-IDENTIFIERS   ← DID 定义
│         ├── DTCS               ← DTC 定义
│         ├── SERVICES           ← 服务定义（请求/响应参数）
│         ├── SINGLE-ECU-JOB / DIAG-COMMS（通信参数）
│         └── COMPARAMS          ← 通信/时序/子功能常量
├── COMPU-METHODS                ← 物理值↔原始值的换算规则（缩放/枚举）
├── DIAG-CODED-TYPES             ← 编码类型（字节序/长度/对齐）
└── PHYSICAL-TYPES               ← 物理量类型（单位/范围）
```

> 真实 ODX 的难点在**变体继承**和**引用**：ECU-VARIANT 可以继承 BASE-VARIANT，
> 只覆盖差异部分。测试工具负责"展开继承、选出当前变体"，测试脚本只面对
> 展开后的"有效数据"——本项目教学版直接省略继承，简化成"一个 ECU 一行定义"。

### 36.2.2 本项目教学版 ODX 的元素（要会读）

```
ODX
├── FILE-INFO                 ← 文件元信息（文件名/版本/格式/描述）
├── DIAG-LAYER
│   └── ECU-VARIANT short_name="CLUSTER_ECU"
│         ├── LONG-NAME            ← 数字仪表 ECU
│         ├── DIAG-ADDRESS 0x7E0   ← 诊断请求地址（Tester → ECU）
│         ├── RESPONSE-ADDRESS 0x7E8 ← 响应地址（ECU → Tester）
│         ├── FUNCTIONAL-ADDRESS 0x7DF ← 功能寻址地址
│         └── PROTOCOL UDS_ISO_14229
├── DATA-IDENTIFIERS     ← 10 个 DID（本章核心）
│   └── DATA-IDENTIFIER
│         ├── SHORT-NAME   VIN
│         ├── ID           F190
│         ├── ENCODING     ASCII
│         ├── LENGTH       17
│         ├── READ-ONLY    true
│         └── DESCRIPTION  ...
├── DTCS                 ← 5 个 DTC（P0116/P0117/P0562/P0563/P0135）
├── SERVICES             ← 17 个 UDS 服务（与 ECU handler 对齐）
└── COMPARAMS            ← 10 个常量（会话/安全访问/P2/CLEAR_DTC_GROUP…）
```

**教学设计的关键点**：文件的每一类元素都对应一个**测试自动化会用到的问题**：

| ODX 元素 | 解答的问题 | 测试关键字 |
|---|---|---|
| `ECU-VARIANT` / 地址 | 发给哪个 ID？ | `Get ECU Info` |
| `DATA-IDENTIFIER` | DID 是什么、多长、怎么解码？ | `DID Request Bytes` / `Decode DID Response` |
| `DTCS` | 故障码字典 | `List DTCs` / `DTC Name` |
| `SERVICES` | 服务请求/正响应结构 | `Service Request Template` |
| `COMPARAMS` | 会话值/子功能/清码组/超时 | `Comparam` / `Generate Request From ODX` |

### 36.2.3 编码（ENCODING）：DID 数据的"解码规则"

ODX 里每个 DID 都要说明"这串字节怎么变成人能看懂的值"。本项目教学版用简化
`ENCODING`，真实 ODX 用 `DIAG-CODED-TYPE` + `COMPU-METHOD`（缩放/枚举/位域）：

| ENCODING | 含义 | 例子 | 真实 ODX 对应 |
|---|---|---|---|
| `ASCII` | 字节流按 ASCII 解码 | VIN `4C53…` → `LSVAM4180NC000001` | DIAG-CODED-TYPE（字符串）+ COMPU-METHOD |
| `HEX` | 按十六进制展示 | F1B0 `0e000000` | PHYSICAL-TYPE（十六进制） |
| `UINT8` | 无符号整数（本项目预留） | — | COMPU-METHOD 线性换算 |

**解码要校验长度**：`Decode DID Response` 会断言"响应数据长度 == ODX 的 LENGTH"。
这不是多此一举——**数据库与 ECU 版本不一致时，长度错位是第一症状**（见 36.3）。

---

## 36.3 数据库与 ECU 的一致性（版本管理的核心）

### 36.3.1 为什么不一致会出问题

ODX 是**交付物**，与 ECU 软件版本一一对应。真实项目里常见的"数据库事故"：

| 场景 | 现象 | 测试能否发现 |
|---|---|---|
| ECU 升级了，ODX 没更新 | 新 DID 不在库里（读不到/不敢测） | 覆盖率下降，靠评审发现 |
| ODX 超前 ECU（新库老 ECU） | 按新库发请求 → 老 ECU 回 NRC 0x31 | 能（读 DID 失败） |
| 长度/编码改了没同步 | 解码错位/乱码 | **能**（本库长度强校验会报错） |
| 服务的 SID 集合变了 | 数据库里有 ECU 不支持的服务 | 能（`ODX Should Be Valid` 检查 SID 集合） |

**本项目的做法**：
- `ODX Should Be Valid` 检查 **ODX 服务集合 == ECU 实际支持的 17 个 SID**；
- 用例②逐 DID 读取并解码，**数据库里每个 DID 都必须在 ECU 上读得到且长度/格式正确**；
- 用例④检查 ECU 报出的 DTC 必须都在 ODX 字典里。

### 36.3.2 本项目当前的真实 DID 清单（10 个）

| DID | 名称 | 编码 | 长度 | 典型值 | 说明 |
|---|---|---|---|---|---|
| `F187` | PartNumber 零件号 | ASCII | 14 | `HW-IC-2026-001` | 只读 |
| `F188` | SoftwareVersion 软件版本 | ASCII | 16 | `SW-CLUSTER-3.2.1` | 可写（0x2E 演示） |
| `F189` | SerialNumber 序列号 | ASCII | 17 | `ECU-2026-0918-007` | 只读 |
| `F18A` | Supplier 供应商 | ASCII | 7 | `VISTEON` | 只读 |
| `F190` | VIN 车辆识别码 | ASCII | 17 | `LSVAM4180NC000001` | 20 字节响应走 ISO-TP 多帧 |
| `F1A0` | ApplicationVersion 应用版本 | ASCII | 6 | `V1.0.0` | 可写 |
| `F195` | ApplicationSoftwareVersion 软件版本 | ASCII | 6 | `v1.0.0` | **35 章 OTA 模块新增**；刷写成功后 + 复位变 v1.1.0 |
| `F1A3` | BootloaderVersion | ASCII | 8 | `BL-1.0.0` | 35 章新增；升级前核对 |
| `F186` | HardwareVersion 硬件版本 | ASCII | 13 | `HW-VISTEON-P7` | 35 章新增；软硬件兼容性核对 |
| `F1B0` | TestStimulus 测试激励 | HEX | 4 | `0e000000` | 工程模式；写需安全解锁 |

> 📌 **两个诚实的说明**：
> 1. 行业常见的 **F191（硬件号）本项目 ECU 未实现**，所以 ODX 不收录——
>    "数据库只写 ECU 真有的"比"抄一份全的"更符合一致性原则；
> 2. **F1A1/F1A2 是 OTA 刷写内部暂存 DID**（目标版本/期望 CRC），属于 Bootloader
>    流程细节，真实项目通常由刷写规范而非诊断数据库描述，故不纳入本 ODX。
>
> 这两个取舍本身就是在演示 ODX 管理的边界：**数据库描述"对外诊断接口"，
> 不是简单罗列所有可读内存**。

---

## 36.4 OTX 测试序列（ISO 13209）

### 36.4.1 OTX 是什么

有了 ODX（数据），还需要一种格式描述"**怎么测**"：第一步发什么、断言什么、
失败怎么办。OTX 就是测试序列的 XML 标准，通常由 CANoe/OTX Studio 编辑，
可以被测试执行器直接运行（不用翻译成代码）。

```
ODX（数据）  +  OTX（序列）  ──▶  测试执行器（CANoe / 本项目的 OdxLibrary）
                                     ↓
                              发请求、断言响应、记录步骤结果
```

### 36.4.2 本项目教学版 OTX（5 步）

```xml
<otx:test-sequence xmlns:otx="http://iso.org/otx/1.0" short-name="CLUSTER_BasicCheck" version="1.0.0">
  <otx:step id="1" type="request" name="进入扩展会话" service="0x10" subfunction="0x03"/>
  <otx:step id="2" type="request" name="读取 VIN" service="0x22" did="F190"/>
  <otx:step id="3" type="expect"  name="校验 VIN 格式" service="0x22" did="F190"
            encoding="ASCII" length="17" pattern="^LSVAM4180NC[0-9]{6}$"/>
  <otx:step id="4" type="request" name="读取软件版本" service="0x22" did="F188"/>
  <otx:step id="5" type="expect"  name="校验软件版本格式" service="0x22" did="F188"
            encoding="ASCII" length="16" pattern="^SW-CLUSTER-[0-9]+[.][0-9]+[.][0-9]+$"/>
</otx:test-sequence>
```

**步骤语义**：

| type | 含义 | 属性 |
|---|---|---|
| `request` | 发一条请求，必须正响应 | `service`（SID）、`subfunction`（子功能）、`did`（DID 名/ID） |
| `expect` | 断言**上一条响应** | `did`（校验响应 DID）、`encoding`、`length`、`pattern`（正则）、`expected`（精确值） |

**步骤级可观测性**（OTX 相比手写脚本的最大优势）：
```
[OTX] 第 5 步「校验软件版本格式」失败：ASCII 不匹配正则 '^BAD-VERSION$'，实际 'SW-CLUSTER-3.2.1'（已完成 4 步）
```
—— 失败直接定位到"第几步、步骤名、期望什么、实际什么"，前 4 步结果仍可查看。

### 36.4.3 真实 OTX 的复杂度（了解）

ISO 13209 的完整 OTX 是图灵完备的测试语言：变量、参数、循环、分支、异常处理、
库调用、与 ODX 联动。本项目只实现了**顺序 request/expect 子集**——够演示
"数据驱动 + 步骤级定位"，但不覆盖流程控制。真实项目里复杂测试序列通常在
OTX Studio/vTESTstudio 里编辑并调用现成的 OTX 库函数。

---

## 36.5 本项目实现

### 36.5.1 架构图

```
 simulators/diag/vehicle.odx.xml        simulators/diag/vehicle.otx.xml
 （诊断数据：DID/DTC/服务/COMPARAM）    （测试序列：5 步 request/expect）
                 │                                   │
                 ▼                                   ▼
        ┌──────────────────────────────────────────────────┐
        │ libraries/OdxLibrary.py（xml.etree 解析）          │
        │  · Load ODX/Load OTX：解析 + 结构校验（失败不污染） │
        │  · DID Request Bytes / Decode DID Response        │
        │  · Run OTX Sequence：逐步骤执行，失败定位到步骤名   │
        └───────────────────────┬──────────────────────────┘
                                │ 复用项目已有虚拟 CAN
                                ▼
        VirtualCanBus（233.255.0.1:30000） + uds_client.DiagnosticTester
                                │ 0x7E0 请求 → 0x7E8 响应
                                ▼
                    simulators/ecu_sim.py（被测 ECU，17 个服务 / 10 个 DID）
                                ▲
                 tests/robot/18_odx_database.robot（Resource common.resource
                 起 ECU 环境；Suite Setup 加载 ODX/OTX；9 个用例）
```

### 36.5.2 文件清单与职责

| 文件 | 角色 | 关键内容 |
|---|---|---|
| `simulators/diag/vehicle.odx.xml` | 诊断数据库（教学版 ODX 子集） | ECU 变体/地址、10 DID、5 DTC、17 服务、10 COMPARAM |
| `simulators/diag/vehicle.otx.xml` | 测试序列（教学版 OTX 子集） | 5 步 request/expect（会话→VIN→断言→版本→断言） |
| `libraries/OdxLibrary.py` | Robot Framework 关键字库 | xml.etree 解析、数据驱动请求/解码、OTX 执行器、响应串扰防护 |
| `tests/robot/18_odx_database.robot` | 套件 18：9 个用例 | 解析/一致性/按名读/清码/服务/OTX 正+负/坏库负向 |
| `docs/36_ODX与OTX数据库驱动测试.md` | 本章 | 你现在读的这份 |

### 36.5.3 关键字清单（24 个）

| 分组 | 关键字 | 作用 |
|---|---|---|
| **ODX 加载/查询** | `Load ODX` | 加载并校验数据库（失败不替换当前库） |
| | `ODX Should Be Valid` | 结构校验 + 服务 SID 与 ECU 一致性 |
| | `Get ECU Info` | ECU 变体/诊断地址/响应地址 |
| | `List Data Identifiers` / `List DTCs` / `List Services` / `List Service SIDs` | 列表查询 |
| **数据驱动请求/解码** | `DID Request Bytes` | 按 DID 名/ID 生成 `22 xx xx` |
| | `Decode DID Response` | 按 ODX 定义解码（ASCII/HEX/UINT8）+ 长度强校验 |
| | `DTC Name` / `Service Request Template` / `Comparam` | 字典查询 |
| | `Generate Request From ODX` | read_did / enter_session / clear_dtc / tester_present |
| | `Read DID By Name` / `Enter Session By Name` / `Clear All DTCs` | 生成请求 + 执行 + 断言正响应（一步到位） |
| **执行/生命周期** | `Send ODX Request` | 发任意请求，返回结构化响应 |
| | `Close Odx Tester` | 关闭诊断仪/总线（Teardown） |
| **OTX** | `Load OTX` / `List OTX Steps` | 加载/查看序列 |
| | `Run OTX Sequence` | 逐步骤执行；支持 `expect_overrides` 负向注入 |
| | `OTX Step Should Have Passed` / `OTX Summary` / `Get OTX Results` | 步骤级断言/摘要/结果 |

### 36.5.4 四个关键设计（含一个真实踩坑）

**① 加载即校验，失败不污染**

`Load ODX` 先完整解析+校验（根元素、DIAG-LAYER、DID 唯一、DTC 编号、SID 集合…），
**全部通过才替换当前数据库**。坏文件报清晰错误（用例⑨），当前库保持可用——
和真实工具"打开坏库失败后仍用原库"一致。

**② 按 ODX 生成请求、按 ODX 解码响应**

用例里不出现 `22 F1 90` 这样的魔数（除非演示对比）：
```robotframework
${request}=    Generate Request From ODX    read_did    VIN      # → 22F190
${response}=   Send ODX Request    ${request}
${vin}=        Decode DID Response    VIN    ${response}        # → LSVAM4180NC000001
```
`Decode DID Response` 会校验"响应长度 == ODX LENGTH"，长度不符直接报错并提示
"数据库与 ECU 版本可能不一致"。

**③ OTX 步骤级执行与失败定位**

`Run OTX Sequence` 逐步执行并记录每步 `{id, name, type, status, elapsed_ms, message}`；
失败时抛 `[OTX] 第 N 步「步骤名」失败：…`，并保留前面已执行步骤的结果
（`Get OTX Results` 可查）。用例⑧用 `expect_overrides` 注入坏期望，专门验证这条：
**第 5 步失败、前 4 步 PASS、错误消息含步骤名**。

**④ 真实踩坑：多网卡组播的"迟到重复响应"**

开发本章时遇到一次典型的 flaky（第 2 次连跑才暴露）：

- 现象：用例②连续读 10 个 DID 都正常，但用例③ `Read DID By Name VIN` 偶尔
  拿到 `F1B0`（TestStimulus）的数据，报"数据长度不符"；
- 根因：虚拟 CAN 是多网卡 UDP 组播，**同一帧可能从不同网卡回环出多份副本**，
  极少数副本会晚于 `simulators/bus.py` 的 5ms 去重窗口到达。连续读不同 DID 时，响应 SID
  都是 `0x62`，`uds_client.send_request` 只按 SID 匹配 → 迟到的上一帧响应被
  误当成本次响应；
- 修复（`libraries/OdxLibrary.py` 内）：`Send ODX Request` 增加**响应 DID 二次校验**
  —— 0x22 请求的响应 payload 前 2 字节必须等于请求的 DID；不符就重试（最多 3 次，
  每次 `tester.send_request` 都会清空收件箱）。修复后连跑稳定通过；
- 教训：**"响应 SID 匹配"不等于"响应对应本次请求"**。真实项目里多诊断仪
  （CANoe + 自研脚本）并发时也有同类串扰，必须做请求标识校验（DID/echo/序列号）。

> 这段踩坑写进教程不是凑字数——**flaky 的根因分析**正是车载测试工程师的核心能力：
> 不能"重跑一次过了就算了"。

---

## 36.6 逐条讲 18 套件（`tests/robot/18_odx_database.robot`）

### 36.6.0 套件骨架

```robotframework
*** Settings ***
Documentation    套件 18：ODX/OTX 诊断数据库驱动测试
Resource         ../../resources/common.resource
Library          ../../libraries/OdxLibrary.py
Suite Setup      启动环境并加载诊断数据库
Suite Teardown   关闭 ODX 与车辆环境
Test Setup       回到默认诊断会话
```

三个要点：

1. **Resource common.resource**：本套件需要真实 ECU（诊断链路），由公共资源拉起
   ECU+headless 仪表盘；ODX/OTX 只是"数据/序列"，执行时还是要发到 ECU。
2. **Suite Setup 加载两个数据库**：ODX 解析/OTX 解析不依赖 ECU，但先加载好，
   用例里直接查询；OTX 文件在加载时完成结构校验。
3. **Test Setup 回默认会话 + Teardown 逆序回收**：前一个用例可能停在扩展会话；
   Teardown 先 `Close Odx Tester`（释放本库自己的总线连接）再 `Stop Vehicle
   Environment`，避免总线句柄泄漏告警。

### 36.6.1 用例 1：ODX 数据库应能加载并通过结构校验

```robotframework
${summary}=    Load ODX    ${ODX_PATH}
Should Be Equal    ${summary}[ecu]    CLUSTER_ECU
Should Be Equal As Integers    ${summary}[diag_address]    0x7E0
Should Be Equal As Integers    ${summary}[did_count]    10
Should Be Equal As Integers    ${summary}[dtc_count]    5
Should Be Equal As Integers    ${summary}[service_count]    17
${checks}=    ODX Should Be Valid
```

**测什么**：数据库能被解析成"测试工具能用的结构"：ECU 变体/地址、10 DID、
5 DTC、17 服务；交叉校验（DID 唯一、DTC 编号合法、服务集合 == ECU 的 17 个 SID）
通过。这是所有数据驱动用例的入口——**库都加载不了，后面全白搭**。

### 36.6.2 用例 2：DID 列表应与 ECU 实际 DID 一致

**测什么**：数据库里 10 个 DID **逐个**按 ODX 生成请求、发给 ECU、按 ODX 定义解码；
解码值/长度必须与数据库一致。这是"数据库 ↔ ECU 一致性"最直接的验证。

**设计亮点**：
- 用例**不写死 22 F1 90**，请求由 `Generate Request From ODX` 生成；
- 版本类 DID（F195/F1A3/F186）会随 OTA 刷写/软件升级变化，用**格式断言**
  （正则/前缀）而不是写死值；固定 DID（零件号/VIN…）断言精确值；
- 长度强校验在 `Decode DID Response` 内部完成——服务端/数据库任一方长度改错，
  用例立即失败。

### 36.6.3 用例 3：按名称读 DID 应正确解码

```robotframework
${vin}=    Read DID By Name    VIN
Should Be Equal    ${vin}    LSVAM4180NC000001
Length Should Be    ${vin}    17
${software}=    Read DID By Name    SoftwareVersion
Should Match Regexp    ${software}    ^SW-CLUSTER-[0-9]+[.][0-9]+[.][0-9]+$
```

**测什么**：最贴近真实测试写法的"一步到位"关键字：名字 → 请求 → 响应 → 解码。
包括 17 字节 VIN（**响应 20 字节，必须走 ISO-TP 多帧重组**）、软件版本正则、
Bootloader/硬件版本格式。

**真实工作对应**：CANoe 里配置好 ODX 后，测试脚本/OTX 里也是"按 DID 名读写"；
长度/编码由工具保证。这个用例就是那个体验的最小复刻。

### 36.6.4 用例 4：DTC 列表应与 ECU 故障目录一致

**测什么**：ODX 的 DTC 编号集合 == ECU 的 FAULT_CATALOG（P0116/P0117/P0562/
P0563/P0135）；`DTC Name P0562` 能查到中文名称；并且 **ECU 当前报出的 DTC
必须都在 ODX 字典里**（数据库是诊断字典，报出字典外的 DTC = 典型版本不一致缺陷）。

**真实工作对应**：售后诊断仪读出的 DTC 必须能映射成用户/技师可读的故障描述，
这个映射就来自诊断数据库。DTC 字典漏一个，4S 店就"查无此码"。

### 36.6.5 用例 5：按 ODX 生成清 DTC 请求并执行

```robotframework
${request}=    Generate Request From ODX    clear_dtc     # → 14FFFFFF
${response}=   Send ODX Request    ${request}
${count}=      Read DTC Count                       # → 0
${cleared}=    Clear All DTCs
```

**测什么**：清码请求不是手写的 `14 FF FF FF`，而是由 ODX 的 COMPARAM
`CLEAR_DTC_GROUP=0xFFFFFF` 生成；执行后正响应、DTC 数量归零。
演示"**同一个语义，数据库改一处、所有测试跟着变**"。

### 36.6.6 用例 6：服务模板应与 ECU 支持的 17 个 SID 一致

**测什么**：
1. `List Service SIDs` 的集合 == 17 个预期 SID（10/11/14/19/22/27/28/2E/2F/31/34/36/37/3D/3E/85/23）；
2. 每个服务模板都有名称/请求描述/正响应描述；
3. 抽查 `0x22` 模板是 ReadDataByIdentifier；
4. 未知 SID `0x99` 的边界行为：
   - **默认会话** → NRC `0x7F`（会话限制优先）；
   - **扩展会话** → NRC `0x11`（服务不存在）。

**设计亮点**：不只对比"数据库清单 == 常量清单"（那可能两边一起错），还从
ECU 实际行为侧抽查未知 SID 的两种负响应——把"支持集合"钉成真实边界，
也顺带讲清 UDS 的"会话限制优先于服务支持判断"的顺序。

### 36.6.7 用例 7：OTX 测试序列应逐步执行通过

```robotframework
${summary}=    Run OTX Sequence    ${OTX_PATH}
Should Be Equal As Integers    ${summary}[passed]    5
FOR    ${step_id}    IN RANGE    1    6
    OTX Step Should Have Passed    ${step_id}
END
```

**测什么**：OTX 5 步全部通过（进扩展会话→读 VIN→校验 VIN→读软件版本→校验版本
格式），并且**步骤级结果可见**：`Get OTX Results` 能看到每步 `{id,name,type,status}`
和耗时；`OTX Summary` 给出通过/失败计数。

**真实工作对应**：CANoe/vTESTstudio 执行 OTX 测试序列后，报告里就是按步骤
展示 through/failed；失败时直接指出第几步，不需要人肉翻日志。

### 36.6.8 用例 8：OTX 坏期望应在第 5 步失败并报出步骤名（负向）

```robotframework
${overrides}=    Create Dictionary    5=^BAD-VERSION$
${error}=    Run Keyword And Expect Error    *第 5 步*
...    Run OTX Sequence    ${OTX_PATH}    expect_overrides=${overrides}
Should Contain    ${error}    校验软件版本格式
```

**测什么**（负向，证明断言有效）：
1. 把第 5 步"软件版本格式"的期望正则覆盖成 `^BAD-VERSION$`；
2. 执行器必须在**第 5 步**失败；
3. 错误消息必须包含**步骤名**；
4. 前 4 步仍标记 PASS、第 5 步 FAIL（`Get OTX Results` 逐步骤验证）；
5. `OTX Step Should Have Passed 5` 也必须失败。

**设计亮点**：OTX 的价值不只是"能跑"，而是"失败能定位"。这个用例用
`Run Keyword And Expect Error` 精确匹配"第 5 步 + 步骤名"，如果执行器把
失败信息糊成"断言失败"，用例就会挂——**负向测试验证的是工具能力本身**。

### 36.6.9 用例 9：损坏或缺字段的 ODX 应清晰报错（负向）

**测什么**：三类坏数据库都要给出可定位的清晰错误：
1. XML 损坏（标签未闭合）→ `XML 解析失败`；
2. 缺 `DIAG-LAYER` → `缺少 <DIAG-LAYER>`；
3. 文件不存在 → `文件不存在`。

并且**失败不污染**：连续加载三个坏文件后，再用默认数据库加载仍完整可用
（10 个 DID、结构校验通过）。

**真实工作对应**：诊断数据库是交付物，收到 OEM 的坏 PDX/ODX 时，工具必须
"拒绝加载 + 说清楚哪里错"，而不是加载一半、后续测试带病运行。

### 36.6.10 需求标签覆盖

| 需求 ID | 含义 | 覆盖用例 |
|---|---|---|
| **REQ-DIAG-023** | ODX 数据库解析 | 用例 1、2、4、6、9 |
| **REQ-DIAG-024** | 数据库驱动请求/响应 | 用例 2、3、5 |
| **REQ-DIAG-025** | OTX 测试序列执行 | 用例 7、8 |

9 个用例、3 条需求全部被引用。按需求跑：
`robot --include REQ-DIAG-025 tests/robot/18_odx_database.robot`。

---

## 36.7 负向测试：数据库/序列的"坏输入"清单

| 负向场景 | 怎么造 | 期望行为 | 用例 |
|---|---|---|---|
| ODX XML 损坏 | 写一个标签未闭合的文件 | 抛"XML 解析失败"，不污染当前库 | ⑨ |
| ODX 缺 DIAG-LAYER | 只写 `<ODX><FILE-INFO/></ODX>` | 抛"缺少 `<DIAG-LAYER>`" | ⑨ |
| ODX 路径不存在 | 指向不存在的文件 | 抛"文件不存在" | ⑨ |
| 数据库超前于 ECU | 读 ODX 里 ECU 未实现的 DID | 正响应失败（NRC 0x31）/长度不符 | ②（当前全部一致，靠校验兜底） |
| OTX 期望不匹配 | `expect_overrides` 覆盖第 5 步正则 | 第 5 步失败、报步骤名、前序步骤保留 | ⑧ |
| 未知服务 SID | 发 `99` | 默认会话 0x7F / 扩展会话 0x11 | ⑥ |

**为什么负向这么重要**：数据驱动测试最怕"数据库错了测试还全绿"。
- 长度强校验（②）抓"数据库/ECU 不一致"；
- SID 集合校验（①⑥）抓"服务清单漂移"；
- 坏库负向（⑨）抓"解析失败还继续跑"；
- 坏期望负向（⑧）抓"OTX 执行器把失败吞掉"。

---

## 36.8 真实项目：ODX/OTX 工具链与工作流

### 36.8.1 工具映射

| 本项目 | 真实工作对应物 | 你要会的事 |
|---|---|---|
| `simulators/diag/vehicle.odx.xml` | 标准 ODX/PDX（ISO 22901） | 用 ODX Studio/CANoe 导入、选 ECU 变体、看 DID/服务 |
| `simulators/diag/vehicle.otx.xml` | 标准 OTX（ISO 13209）序列 | 用 vTESTstudio/OTX Studio 编辑/执行测试序列 |
| `libraries/OdxLibrary.py`（xml.etree 解析） | CANoe/vTESTstudio 的诊断数据库引擎 | 知道工具"按 DID 名生成请求"的原理，能用 API 做同样的事 |
| `Run OTX Sequence` | CANoe 的 Test Module / vTESTstudio 执行 | 看报告里的步骤级结果、失败定位 |
| `Send ODX Request` | CANoe Diagnostic Console | 手工发请求、看响应，辅助探索 |
| `Get ECU Info` | ODX 的 ECU 变体选择 | 多车型/多配置下选对变体 |

### 36.8.2 ODX 版本 A~D（一句话）

| 版本 | 新增能力 |
|---|---|
| **ODX 2.0 A** | 基础诊断数据（DID/服务/DTC） |
| **ODX 2.0 B** | 增加 ECU 配置/变体编码 |
| **ODX 2.0 C** | 增加功能诊断（functional group）、刷写数据描述 |
| **ODX 2.2 D** | 增加 ODX-F（flash）、安全/访问控制细化（各 OEM 实际用 B/C/D 混合） |

> 测试工程师不需要背版本号，只需要知道：**版本越高，描述越全**；拿到 ODX
> 先看 `FILE-INFO` 里的版本和 OEM 规范要求。

### 36.8.3 真实工作流（Tier1 测试视角）

```
 OEM 发来 PDX/ODX（诊断数据库）+ 需求/诊断规范
        │
        ▼
 导入 CANoe/ODX Studio → 选 ECU 变体 → 生成诊断请求/测试序列（OTX）
        │
        ▼
 台架/HIL 执行：按 DID 读、按服务发、按 OTX 步骤跑 → 报告（步骤级）
        │
        ▼
 ECU 软件升级 → 数据库版本更新 → 差异评审（新增/修改了哪些 DID/服务/DTC）
        │
        ▼
 回归测试（变化项 + 受影响项）→ 更新追溯矩阵
```

**版本管理的关键动作**（真实项目必做）：
1. 每个 ECU 软件版本绑定一个 ODX 版本（基线）；
2. 升级前对比新旧 ODX 的 DID/服务/DTC 差异，评估回归范围；
3. 测试报告里记录"用的哪个 ODX 版本"（否则结果不可复现）。

### 36.8.4 本仿真器的边界（诚实说明）

- **非标准 ODX**：教学版用简化标签，没有命名空间/变体继承/COMPU-METHOD，
  不能直接喂给 CANoe；真实 ODX 是严格符合 ISO 22901 的 XML。
- **OTX 子集**：只有顺序 request/expect，没有变量/循环/分支/库函数。
- **通信**：复用项目虚拟 CAN（模拟），不是真实 ECU/台架。

**可平移的能力**：ODX 元素语义（DID/服务/DTC/COMPARAM）、数据驱动请求/解码、
数据库与 ECU 一致性校验、OTX 步骤级执行与失败定位、负向测试设计——这些在
真实工具链里**一模一样**。

---

## 36.9 扩展练习（自己动手）

1. **体验"数据库超前于 ECU"**：给 `simulators/diag/vehicle.odx.xml` 增加一个 ECU 未实现的 DID
   （如 F191），再跑用例②——观察它如何失败（NRC 0x31 或长度不符）。然后撤掉
   这条 DID：这就是"数据库必须与 ECU 版本一致"的现场教学。
2. **扩展 OTX**：给序列加两步（回到默认会话 `10 01` + 断言会话响应），
   体会"步骤数变了，测试用例不用改"（`Run OTX Sequence` 自动按 XML 执行）。
3. **用 COMPARAM 做时序断言**：读 ODX 的 `P2_MAX_MS=50`，写一条"响应时间 ≤ P2"
   的用例（结合 `Measure Response Time` 关键字），体会"时序参数也来自数据库"。
4. **解码规则扩展**：给 ODX 增加一个 `UINT8` 编码的 DID（如警告灯掩码），
   扩展 `Decode DID Response` 的 UINT8 分支，再写用例验证。
5. **与真实工具交叉验证**：用项目已安装的 `cantools` 加载其他 DBC；如果有条件，
   用 CANoe Demo 打开标准 ODX 示例，对照本章的元素语义。
6. **响应串扰实验**：把 `Send ODX Request` 的 DID 校验重试逻辑临时关掉，
   连跑多次复现"读 VIN 拿到别的 DID"的 flaky，再恢复——深刻理解 36.5.4④。

---

## 36.10 本章自检

1. 手写 hex 的诊断测试在真实项目里有哪四个痛点？ODX 分别怎么解决？
2. ODX 的 DIAG-LAYER 有哪几层？ECU-VARIANT 里至少要有哪些信息？
3. 一个 DID 定义包含哪些要素？`ENCODING=ASCII` + `LENGTH=17` 意味着什么？
4. 为什么 `Decode DID Response` 要做长度强校验？长度不符通常说明什么问题？
5. OTX 的 `request` 步骤和 `expect` 步骤分别做什么？`expect` 断言的是哪条响应？
6. OTX 相比手写脚本，最大的可维护性优势是什么？失败时能提供什么信息？
7. 数据库与 ECU 版本不一致时，本项目哪几个关键字/用例能发现？
8. 为什么"响应 SID 匹配"不等于"响应对应本次请求"？本库如何修复这个 flaky？
9. 真实项目中 ODX 和 OTX 分别由谁维护、测试工程师要怎么用？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | ①**数量大**→数据库统一管理；②**多车型/变体**→换 ODX/选变体即可；③**版本漂移**→数据库与 ECU 版本绑定、校验暴露不一致；④**评审追溯**→按名字引用、可追溯。手写 hex 的魔数会散落且易错 |
| 2 | 分层：PROTOCOL / FUNCTIONAL-GROUP / BASE-VARIANT / ECU-VARIANT（+ECU-SHARED-DATA）；ECU-VARIANT 至少要有名称、诊断地址/响应地址、协议（本项目还有功能寻址地址） |
| 3 | 要素：SHORT-NAME、ID、ENCODING、LENGTH（真实 ODX 还有 DIAG-CODED-TYPE/COMPU-METHOD/取值范围/读写权限等）。ASCII+17 = 17 字节 ASCII 文本（VIN），响应需多帧重组 |
| 4 | 长度是数据库与 ECU 的"契约"；不符通常说明**数据库版本与 ECU 软件版本不一致**（或编码规则错），继续解码会错位/乱码 → 必须早失败 |
| 5 | request 发请求（必须正响应）；expect 断言**上一条**响应的 DID/长度/正则/精确值。expect 依赖前一条 request 的响应 |
| 6 | 序列用 XML 描述、可交换/可工具编辑；失败能定位到**第几步 + 步骤名 + 期望/实际**，且保留前序步骤结果（步骤级可观测性） |
| 7 | `ODX Should Be Valid`（SID 集合不一致）、`Decode DID Response`（长度不符）、用例②逐个读 DID、用例④ DTC 字典包含性、`Read DID By Name` 失败 |
| 8 | 多网卡 UDP 组播会产生**迟到重复响应**；连续读不同 DID 时响应 SID 都是 0x62，只按 SID 匹配会把上一帧误当本次响应。本库校验"响应 payload 前两字节 == 请求 DID"，不符重试（最多 3 次） |
| 9 | ODX 通常由 OEM/Tier1 的诊断/系统团队维护并随 ECU 软件版本发布（PDX）；OTX 由测试团队/工具链维护。测试工程师：选对变体与版本、按名字用 DID/服务、跑/改 OTX 序列、升级时评审数据库差异并评估回归 |

---

**上一章** ← [35 · OTA 与 Bootloader 全链路](35_OTA与Bootloader全链路.md)　|　**下一章** → [37 · Appium 与真机 UI 自动化](37_Appium与真机UI自动化.md)

> 📌 Wave3 扩展篇导航：34 Trace 分析 → [35 OTA/Bootloader](35_OTA与Bootloader全链路.md) → **36 ODX/OTX（本章）** → [37 Appium](37_Appium与真机UI自动化.md) → [38 HIL 台架](38_HIL台架与电源IO验证.md) → [39 能力自检](39_能力自检与下一步学习路线.md)
