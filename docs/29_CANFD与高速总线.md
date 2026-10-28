# 29 · CAN FD 与高速总线

> 经典 CAN 每帧最多 8 字节，这是 1986 年的设计。到了 ADAS/智能座舱时代，一帧要装
> 64 字节的点云摘要、融合结果、OTA 元数据——于是有了 **CAN FD（Flexible Data-rate）**：
> 数据段扩到 64 字节，数据段还能切到 5~8Mbps 高速率。
>
> 本章讲清三件事：**CAN FD 比经典 CAN 多了什么、DLC 0~15 怎么映射、BRS/ESI 是什么**；
> 然后逐条精讲 `tests/robot/12_canfd.robot` 的 10 个用例（64 字节收发、DLC 映射、
> 标志位、浮点/位图/对象列表解码、丢帧检测、错误注入、FD/经典边界）；最后把
> "仿真里学的"映射回 CANoe 接真实 FD 台架的测试工作。
>
> 前置：02 章（CAN 基础）、07 章（CAN 仿真测试实战的"仿真器→库→套件"套路）、
> 22 章（能力地图里 CAN FD 的位置）、29 章之前建议先读 23 章（LIN 模块的独立仿真思路）。

---

## 📖 零基础读法建议（先看这个，能省你半天）

CAN FD 的难点不在"概念"，而在**一堆细节差异**：DLC 不是字节数、BRS/ESI 两个新标志、
CRC 从 15 bit 变 17/21 bit、填充规则变了。按优先级分配精力：

| 标记 | 章节 | 为什么这个优先级 |
|---|---|---|
| 🟢 **必读** | **29.3 CAN FD 帧结构**（尤其 29.3.1 DLC 映射、29.3.2 BRS/ESI）、**29.4 DBC 如何描述 FD**、**29.6 逐条讲 12 套件** | 你每天工作都要用：看抓包、解 64 字节大帧、判断帧为什么"不是 FD" |
| 🟡 **了解即可** | 29.1 为什么需要 FD、29.2 FD vs 经典 CAN 差异表、29.7 错误注入与边界 | 面试常问"CAN FD 和 CAN 的区别"；错误注入是拉开水平的地方 |
| ⚪ **可以跳过** | 29.8 真实项目工具（CANoe 采样点配置）、29.9 扩展练习 | 入职上真实台架时再回来看，会更有感觉 |

> 💡 **为什么可以跳过位定时细节**：测试工程师日常是"抓帧、解信号、验行为"，不是配
> CAN 控制器寄存器。你只需要记住：**BRS=1 时数据段提速；采样点（sample point）通常配
> 75~80%；>1Mbps 的 FD 需要收发器支持 + 发送延迟补偿（TDC）**。
>
> **本章的验收标准只有一条**：给你一帧 `DLC=15、BRS=1` 的 64 字节帧，你能说出
> "数据长度 64 字节、数据段高速、这是 FD 帧"；再给你 `DLC=9`，你能说出"12 字节"。

---

## 29.1 为什么需要 CAN FD（经典 CAN 的 8 字节天花板）

经典 CAN 的两个物理限制：

1. **数据段最多 8 字节**——一帧大报文只能拆成很多帧发，协议开销（仲裁/CRC/ACK）占比高；
2. **位速率固定**——整帧（含数据段）都是 500kbps，数据段没法提速。

对 ADAS 域控来说这是灾难：一个障碍物列表（4 个目标 × x/y/距离）用经典 CAN 要拆成
十几帧，周期一到总线就"堵车"。CAN FD 用两个改动解决：

| 改动 | 效果 |
|---|---|
| 数据段扩展到 **64 字节** | 一帧装下 4 个障碍物（3×float32×4 = 48 字节）还有余量 |
| **BRS（位速率切换）** | 仲裁段保持 500kbps（兼容老节点、保证仲裁），数据段切到 5Mbps |
| **更大的 CRC（17/21 bit）** | 64 字节数据需要更强的错误检测 |

**带宽收益（数量级感受）**：

```
经典 CAN 8 字节帧  @500kbps：一帧约 130~150us → 有效载荷吞吐 ≈ 30~40 KB/s
CAN FD 64 字节帧   @500k/5Mbps（BRS=1）：一帧约 150~200us → 有效载荷 ≈ 300+ KB/s
                                                          ↑ 约 8~10 倍
```

> ⚠️ 上面的数字是**量级估算**（取决于 ID/数据长度/填充位/采样点），不要当精确值背。
> 真实总线负载率要用 CANoe 的 Bus Statistics 或本项目 `trace` 分析工具实测。

**典型场景**：ADAS 感知结果、域控间大数据、OTA 元数据、电池 BMS 汇总、
线控底盘的冗余状态快照。**车身域**（门窗/座椅）通常还是经典 CAN 或 LIN——成本优先。

---

## 29.2 CAN FD vs 经典 CAN：差异表（面试必背）

| 维度 | 经典 CAN | CAN FD | 测试要注意什么 |
|---|---|---|---|
| 数据段长度 | 0~8 字节 | 0~64 字节 | 断言不能用"固定 8 字节"的思维 |
| DLC 编码 | 0~8 = 字节数；9~15 非法 | 0~8 = 字节数，**9~15 = 12/16/20/24/32/48/64** | DLC 必须查映射表，不能直接当长度 |
| 位速率 | 整帧固定（500k/250k） | 仲裁段慢速 + **数据段可切换高速（BRS）** | 抓包工具要看 BRS 标记；时间戳解释会变 |
| BRS | 不存在 | 0=不切换 / 1=数据段提速 | 测试要验证 BRS 位传递 |
| ESI | 不存在 | 0=error-active / 1=error-passive | 反映发送节点健康状态，可做降级依据 |
| CRC | 15 bit | **17 bit（≤16 字节）/ 21 bit（>16 字节）** | 硬件自动算；注入 CRC 错误验证接收侧 |
| 填充位 | 固定填充（每 4 bit 后） | 固定填充 + 动态填充（CRC 区） | 位时间计算更复杂，工具自动处理 |
| 远程帧 | 支持 | **不支持** | 别用 RTR 思维写 FD 用例 |
| 标准 | ISO 11898-1 | ISO 11898-1:2015（ISO FD） | 还有非 ISO 的"Bosch FD 1.0"，两者不兼容 |

**一句话总结**：CAN FD = 经典 CAN 的**数据段扩展 + 数据段提速 + 更强 CRC**，
仲裁机制/ID 优先级/错误帧这些"经典 CAN 的骨架"全部保留——所以会 CAN 就会一半 FD。

---

## 29.3 CAN FD 帧结构（核心）

```
                  一帧 CAN FD 报文（标准帧）
┌─────┬───────────────┬────────────────────────────┬──────────────────┬─────┬─────┐
│ SOF │   仲裁段       │        控制段               │     数据段        │ CRC │ ACK │
│1bit │ 11bit ID      │ IDE | FDF | res | BRS | ESI │  0~64 字节       │17/21│2bit │
│     │               │  DLC(4bit)                  │ （BRS=1 时高速）  │ bit │     │
└─────┴───────────────┴────────────────────────────┴──────────────────┴─────┴─────┘
        └──── 仲裁段/控制段：500kbps 慢速（保证和老 CAN 竞争不破坏）────┘
                                  └──── BRS=1：数据段切 5Mbps ────┘
```

逐字段看测试要知道什么：

| 字段 | 经典 CAN | CAN FD | 测试关注点 |
|---|---|---|---|
| **FDF**（FD Format） | 0 | 1 | 区分 FD/经典帧；FD 帧不能发远程帧 |
| **DLC** | 0~8 | 0~15（9~15 映射大长度） | **最常考**：DLC ≠ 字节数 |
| **BRS** | — | 0/1 | 数据段是否提速；CANoe 里显示在帧详情中 |
| **ESI** | — | 0/1 | 发送节点是否 error-passive（健康度） |
| 数据段 | ≤8B | ≤64B | 64 字节大帧的解析、float32 信号 |
| CRC | 15 bit | 17/21 bit | 硬件算；测试通过注入错误验证接收处理 |

### 29.3.1 DLC 映射：为什么 9~15 不是"9~15 字节"

CAN 控制器里 DLC 只有 **4 bit**（0~15）。经典 CAN 用 0~8 表示字节数，9~15 空着。
CAN FD 把空着的 9~15 "压缩编码"成更大的长度：

```
DLC : 0  1  2  3  4  5  6  7  8   9   10  11  12  13  14  15
字节: 0  1  2  3  4  5  6  7  8   12  16  20  24  32  48  64
                                  └──────── FD 扩展 ────────┘
```

**为什么是 12/16/20/24/32/48/64 而不是 9~15？** 因为这些长度对总线时序/填充位更友好，
且覆盖了典型报文大小（12 字节状态、20 字节坐标、64 字节整帧）。**记忆口诀**：
8 之后全是 4 的倍数，最后三个是 32/48/64。

测试里必须实现/验证这张表（用例②逐项验证 0~15）。本项目在
`simulators/canfd_sim.py` 与 `libraries/CanFdLibrary.py` 各实现一份
`DLC_TO_LENGTH`（独立实现、交叉校验），`DLC Should Map To Length` 关键字直接暴露给用例。

### 29.3.2 BRS 与 ESI：CAN FD 新增的两个标志

**BRS（Bit Rate Switch，位速率切换）**

- BRS=0：整帧都按仲裁段速率（如 500kbps）发——和经典 CAN 一样慢；
- BRS=1：仲裁段 500kbps，**数据段切到 5Mbps**（速率由控制器配置），传完数据段再切回。

```
 ID 仲裁(慢) ──▶ BRS=1 ──▶ DATA 64B @5Mbps（快）──▶ CRC/ACK(慢)
```

**测试关注**：数据段高速时，ECU 的收发器、采样点、发送延迟补偿（TDC）必须配对；
BRS 位传丢（或接收方不支持）会导致 CRC 错、帧丢失。所以"BRS 标志传递"是 FD 功能测试的必测项。

**ESI（Error State Indicator，错误状态指示）**

- ESI=0：发送节点 error-active（健康）；
- ESI=1：发送节点 error-passive（已累计较多错误，进入降级状态）。

**测试关注**：接收方可以根据 ESI 决定是否信任该节点的数据/是否降级。故障注入后，
验证 ESI 是否如实反映节点状态，是功能安全相关测试点。

> 💡 本项目仿真器在 JSON 帧里用 `"brs": true/false`、`"esi": true/false` 表达这两个标志；
> 测试库用 `BRS Flag Should Be` / `ESI Flag Should Be` 断言。真实 CANoe 抓包里，
> 这两个标志显示在帧的 Detail 窗口，也可以作为过滤列。

### 29.3.3 CRC17/21 与填充位（了解即可）

数据越长，CRC 位宽越大：**≤16 字节用 CRC17，>16 字节用 CRC21**（还有 4 个固定填充位）。
FD 的位填充规则也变了：CRC 区用固定填充、数据区用动态填充，导致"一帧到底占多少 bit"
不能简单按经典 CAN 公式算。

对测试工程师的含义：
- CRC 由硬件算，**自动化测试不手动算 CRC**（除了注入错误场景）；
- 总线负载/时间估算要用 CANoe 的统计，不要用"47+8×DLC"这类经典 CAN 简化公式套 FD；
- 错误注入盒/仿真器注入 CRC 错误时，接收端应能通过硬件错误帧识别（本项目用 `crc_valid` 标记模拟）。

---

## 29.4 DBC 如何描述 CAN FD（数据字典的新字段）

真实项目里，FD 报文同样用 **DBC** 描述。与经典 DBC 相比多了这些关键要素
（本项目文件：`simulators/dbc/vehicle_fd.dbc`）：

**① 报文长度可以写 64**

```
BO_ 1024 VehicleStatusFD: 64 ADAS
```

**② float32 信号用 `SIG_VALTYPE_` 声明**

DBC 的信号只描述"原始位段 + 缩放"，表达不了 IEEE-754 浮点。于是 DBC 规范用
`SIG_VALTYPE_` 声明特殊编码：

```
 SG_ VehicleSpeedFD : 0|32@1- (1,0) [0|65535] "km/h" CLUSTER,TESTER
 ...
SIG_VALTYPE_ 1024 VehicleSpeedFD : 1;      /* 1 = 32-bit IEEE float, 2 = double */
```

**③ 用属性标明"这是 FD 帧、开 BRS"**

```
BA_DEF_ BO_ "VFrameFormat" ENUM "...","StandardCAN_FD","ExtendedCAN_FD";
BA_DEF_ BO_ "CANFD_BRS" ENUM "0","1";
BA_ "VFrameFormat" BO_ 1024 14;          /* 14 = StandardCAN_FD */
BA_ "CANFD_BRS" BO_ 1024 1;              /* 1 = BRS 开启 */
```

> 💡 不同工具（CANdb++/CANoe/cantools）对属性名的支持略有差异，但**语义一致**：
> 看一个 DBC 是不是 FD 数据库，先找 `VFrameFormat`/`CANFD_BRS`/`SIG_VALTYPE_`。
> 本项目 DBC 用标准语法写，可用 `cantools` 解析验证（开发时已用它交叉验证布局）。

**本项目 DBC 的 3 个 FD 报文**：

| ID | 名称 | 长度 | 发送者 | 周期 | 内容 |
|---|---|---|---|---|---|
| `0x400` | VehicleStatusFD | 64 | ADAS | 20ms | 车速 float32、转速 uint16、档位、12 灯位图、时间戳、电压/加速度/里程 float32 |
| `0x401` | ObjectList | 64 | ADAS | 50ms | 4 个障碍物 × (x, y, distance) 三个 float32 + 障碍物数量 |
| `0x402` | ControlCommand | 64 | TESTER | 按需 | 测试端发送的变长帧（8/12/16/20/24/32/48/64 字节），仿真器校验并回显 |

---

## 29.5 本项目实现：独立仿真进程 + 组播双向帧流

### 29.5.1 架构图

```
┌───────────────────────────────────┐    UDP 组播 233.255.0.3:30003      ┌────────────────────────────┐
│ simulators/canfd_sim.py           │ ─────────────────────────────────▶ │ libraries/CanFdLibrary.py  │
│  （ADAS 域控 = 被测节点）          │   JSON 帧：seq/id/data_hex/dlc/     │  （RF 测试端）              │
│  · 0x400 VehicleStatusFD  20ms    │   is_fd/brs/esi/ts + counter        │  · 组播收帧 + 去重          │
│  · 0x401 ObjectList       50ms    │                                     │  · DBC 手工解码（struct）    │
│  · 0x402 ControlCommand  回显      │ ◀───────────────────────────────── │  · 发送测试帧 / 回声匹配     │
└───────────────────────────────────┘   测试帧 origin=tester（双向组播）  └────────────────────────────┘
                                              │
                                 TCP 127.0.0.1:30004 控制口（JSON 行）
                                 set_signal / set_object / inject_error / silence / errors / stats / quit
                                              │
                                 tests/robot/12_canfd.robot（10 用例）
```

**与经典 CAN 模块（simulators/bus.py）的关键区别**：

| | 经典 CAN 模块 | CAN FD 模块 |
|---|---|---|
| 端口 | UDP 组播 `233.255.0.1:30000` | UDP 组播 `233.255.0.3:30003`（严格错开） |
| 帧模型 | python-can 的 CAN 2.0 报文 | 自定义 JSON（含 dlc/is_fd/brs/esi/counter） |
| 控制通道 | 无（测试端直接发帧） | TCP 30004（设置信号/注入错误/静默） |
| 节点角色 | ECU + 仪表 + 测试端多节点 | ADAS 域控节点 + 测试端 |
| 数据长度 | 0~8 字节 | 0~64 字节 |

**端口可配置**（避免和同事/CI 抢端口）：

```bat
set CANFD_BUS_PORT=31003
set CANFD_CTRL_PORT=31004
.venv\Scripts\python.exe -m robot --outputdir results/dev_canfd tests\robot\12_canfd.robot
```

### 29.5.2 文件清单与职责

| 文件 | 角色 | 关键内容 |
|---|---|---|
| `simulators/canfd_sim.py` | 被仿真 CAN FD 总线（独立进程） | 20ms/50ms 周期调度、DLC 映射、BRS/ESI、错误注入、组播收发、TCP 控制口 |
| `simulators/dbc/vehicle_fd.dbc` | CAN FD 数据库 | 3 个 64 字节报文 + float32 信号 + VFrameFormat/CANFD_BRS 属性 |
| `libraries/CanFdLibrary.py` | Robot Framework 关键字库 | 组播收帧去重、发送闭环、手工解码、标志/长度断言、错误注入控制 |
| `tests/robot/12_canfd.robot` | 套件 12：10 个用例 | 正向 8 + 负向 2；4 个需求标签 |
| `docs/29_CANFD与高速总线.md` | 本章 | 你现在读的这份 |

### 29.5.3 帧 JSON 格式（仿真器的"抓包流"）

```json
{"kind":"frame", "seq":123, "id":1024, "data_hex":"…", "dlc":15,
 "is_fd":true, "brs":true, "esi":false, "ts":1793.12,
 "counter":45, "crc_valid":true, "crc":"A1B2C3D4",
 "valid":true, "error":null, "origin":"adas", "echo_of":null}
```

| 字段 | 含义 | 测试用途 |
|---|---|---|
| `seq` | 仿真器全局帧序号（只增不减） | `since_seq` 标记、序列连续性 |
| `id` | 帧 ID（0x400/0x401/0x402） | 过滤 |
| `dlc` / `data_hex` | DLC 与十六进制数据 | 长度/DLC 映射、信号解码 |
| `is_fd` | 是否 FD 帧 | FD/经典边界用例 |
| `brs` / `esi` | 位速率切换 / 错误状态指示 | 用例③④ |
| `counter` | **同一 ID 的发送计数器** | 丢帧检测（用例⑧） |
| `crc_valid` | CRC 是否正确（注入错误时 false） | 错误处理（用例⑨） |
| `valid` | 帧结构是否合法（长度不符时 false） | 负向识别（用例⑨） |
| `origin` | `adas`=仿真节点发出，`tester`=测试端发出 | 库端过滤回环副本 |
| `echo_of` | 回显帧对应的测试端发送序号 | `Wait For FD Frame ... tester_seq=` 闭环 |

### 29.5.4 三个关键设计（也是三个真实坑）

**① 双向组播 + "回显"防自证**

测试端 send 出去的帧，本机回环会立刻回到测试端自己的接收缓存。如果库直接读它，
就变成"读自己发的帧"——断言恒真（项目里以前踩过"发送→读回自证"的坑）。
本模块的设计：
- 测试端发出的帧带 `origin:"tester"` 与 `tester_seq`；
- 仿真器收到后**校验**，合法才按自己的 `seq` 重新发一帧 `origin:"adas"`、带 `echo_of`；
- 测试库**只收 `origin:"adas"` 的帧**，`Wait For FD Frame ... tester_seq=${sent}`
  只接受对应 `echo_of` 的回显。

这样断言的是"帧经过仿真节点校验后被重新发上总线"，而不是自己发自己收。

**② 发送计数器（counter）做丢帧检测**

ADAS 安全类报文常带"发送计数器"：每帧 +1，接收方发现跳号就知道总线上丢了帧。
本项目仿真器每发一帧同 ID 报文 counter +1；`Inject FD Error sequence_loss` 会**消耗**
一个 counter 但不上总线 → 下一帧 counter 跳 2。测试库
`FD Frame Counter Should Advance` 发现同样 ID 的 counter 不是 +1 就报"计数器跳号"。
这是 CAN FD 测试里**最实战**的丢帧检测方法之一。

**③ 组播双绑定 + 端口隔离**

测试库和仿真器要**同时**绑定 `30003` 才能各收一份组播（`SO_REUSEADDR` + `IP_ADD_MEMBERSHIP`）。
这也是为什么两个进程能"同时在同一条总线上"。端口选了 `233.255.0.3:30003`，
与经典 CAN 的 `233.255.0.1:30000` 完全隔离——用例⑩用 `Get FD Bus Info` 断言
`bus_port != classic_can_port` 来证明"两套总线互不干扰"。

**④ 定时精度（与 LIN 模块同一个 Windows 坑）**

调度器用 `time.sleep()` 而不是 `threading.Event.wait(短超时)`：后者在 Windows 上会被
系统定时器量化成 ~15.6ms，导致 20ms/50ms 周期抖动。详见 23 章 23.5.4 的实测记录。

---

## 29.6 逐条讲 12 套件（`tests/robot/12_canfd.robot`）

### 29.6.0 套件骨架

```robotframework
*** Settings ***
Documentation    套件 12：CAN FD 高速总线测试
Library          ../../libraries/CanFdLibrary.py
Library          String
Suite Setup      Start CanFD Simulation
Suite Teardown   Stop CanFD Simulation
Test Setup       清空 FD 接收缓存
```

三个要点：

1. **不 import `resources/common.resource`** —— CAN FD 模块与 ECU/仪表盘/经典 CAN
   完全独立，是最小可运行闭环（和 23 章 LIN 模块同一种模块化套路）。
2. **Suite Setup/Teardown 自管进程** —— `Start CanFD Simulation` 懒启动子进程并等
   控制口就绪；Teardown 里 `quit → wait → terminate → kill` 兜底回收。
3. **Test Setup 清缓存** —— 用例之间不共享接收缓存（用例隔离），避免"读到上个用例的帧"。

### 29.6.1 用例 1：64 字节 FD 帧应完整收发

```robotframework
64 字节 FD 帧应完整收发
    [Tags]    REQ-CANFD-001
    ${data}=    Evaluate    ''.join(f'{index:02x}' for index in range(64))
    ${sent}=    Send FD Frame    ${ID_CONTROL}    ${data}    brs=${True}    esi=${False}
    ${frame}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent}
    FD Frame Length Should Be    ${frame}    64
    Should Be Equal As Integers    ${frame}[dlc]    15
    Should Be True    ${frame}[is_fd]
    Should Be Equal    ${frame}[data_hex]    ${data}
```

**测什么**：64 字节数据 + DLC=15 + FD 标志 + 数据逐字节一致——这是 CAN FD 的"基本盘"。

**设计亮点**：
- `tester_seq=${sent}` 等的是仿真器**校验后回显**的那帧（不是自己发的回环副本），
  防止"发送→读回自证"的恒真断言；
- 数据用 64 个递增字节（0x00~0x3f），一旦错位/截断立刻能看出来；
- 同时断言 DLC=15（不是"长度 64 就够了"——DLC 映射错也会导致真实 ECU 解析失败）。

### 29.6.2 用例 2：DLC 0 到 15 应映射到 CAN FD 规定长度

```robotframework
    FOR    ${dlc}    ${length}    IN ENUMERATE    @{DLC_LENGTHS}
        DLC Should Map To Length    ${dlc}    ${length}
    END
    FOR    ${dlc}    ${length}    IN ENUMERATE    @{DLC_LENGTHS}
        IF    ${length} >= 8
            ${sent}=    Send FD Frame    ${ID_CONTROL}    'cc' * ${length}
            ${frame}=   Wait For FD Frame    ${ID_CONTROL}    tester_seq=${sent}
            FD Frame Length Should Be    ${frame}    ${length}
            Should Be Equal As Integers    ${frame}[dlc]    ${dlc}
        END
    END
```

**测什么**：① 映射表逐项（0→0、1→1、…、8→8、9→12、…、15→64）；
② 实际发 8/12/16/20/24/32/48/64 字节，验证 DLC 与数据长度双向一致。

**真实工作对应**：FD 报文配置错误（DLC 与数据长度不匹配）是最常见的集成问题之一——
发送方按 64 字节发、接收方 DBC 配 12 字节，解析必然错位。

### 29.6.3 用例 3：BRS 标志应随 FD 帧正确传递

```robotframework
    ${sent_on}=    Send FD Frame    ${ID_CONTROL}    ${data}    brs=${True}
    ${frame_on}=   Wait For FD Frame    ${ID_CONTROL}    tester_seq=${sent_on}
    BRS Flag Should Be    ${frame_on}    True
    ${sent_off}=   Send FD Frame    ${ID_CONTROL}    ${data}    brs=${False}
    BRS Flag Should Be    ${frame_off}    False
```

**测什么**：BRS=1（数据段提速）与 BRS=0 两种帧的标志传递；并抽查 ADAS 周期帧 0x400 默认 BRS=1。

**真实工作对应**：BRS 配错 = 数据段没提速（带宽不达标）或提速后收发器/采样点不匹配（CRC 错）。
CANoe 里 BRS 显示在帧详情，也可作为列过滤。

### 29.6.4 用例 4：ESI 标志应随 FD 帧正确传递

```robotframework
    ${sent_error}=    Send FD Frame    ${ID_CONTROL}    ${data}    dlc=10    esi=${True}
    ${frame_error}=   Wait For FD Frame    ${ID_CONTROL}    tester_seq=${sent_error}
    ESI Flag Should Be    ${frame_error}    True
```

**测什么**：ESI=1（发送节点 error-passive）/ 0 的传递；正常周期帧 ESI=0。

**真实工作对应**：功能安全场景下，接收方可能根据 ESI 降级（不信任该节点数据）。
故障注入后 ESI 是否如实置位，是"故障降级链路"的验证点。

### 29.6.5 用例 5：float32 信号解码精度应符合 IEEE-754 单精度

```robotframework
    ${marker}=    Set FD Signal    speed_kmh=123.456    engine_rpm=3456    gear=3
    ${frame}=     Wait For FD Frame    ${ID_STATUS}    since_seq=${marker}
    ${signals}=   Decode FD Signals    ${frame}
    ${single}=    Evaluate    __import__('struct').unpack('<f', __import__('struct').pack('<f', 123.456))[0]
    Should Be Equal As Numbers    ${signals}[VehicleSpeedFD]    ${single}    precision=6
```

**测什么**：按 DBC 的 `SIG_VALTYPE_=1`（IEEE-754 float32）解码 float 信号，与测试端用
`struct` **独立**算出的单精度值逐位一致；并验证解码结果与 123.456 的偏差 < 0.001。

**为什么强调"独立算"**：如果测试库和被测实现用同一个函数算 float，实现错了也发现不了。
测试端用 `struct.pack/unpack` 单独复算，才叫"验证"。

**单精度常识**：`123.456` 存成 float32 后是 `123.45600128173828`——差值 ~1.3e-6。
**float32 只有约 7 位十进制有效精度**，别拿 float64 的精度要求它（这是新人常见误解）。

### 29.6.6 用例 6：12 灯位图应逐灯正确解码

```robotframework
    ${marker}=    Set FD Signal    telltale_bitmap=0xAAA
    ${signals}=   Decode FD Signals    ${frame}
    FOR    ${index}    ${name}    IN ENUMERATE    @{TELLTALE_NAMES}
        Should Be Equal As Integers    ${signals}[${name}]    ${TELLTALE_BITS}[${index}]
    END
    ${hex}=       Set Variable    ${frame}[data_hex]
    ${bitmap_hex}=    Get Substring    ${hex}    16    20
    Should Be Equal    ${bitmap_hex}    aa0a
```

**测什么**：0x400 的 byte8~9 是 12 个警告灯的位图。设置 0xAAA（奇数位亮），
12 个灯名全部逐位断言；再从原始数据 hex 字符串截取 bitmap 两字节（小端 → `aa 0a`），
同时验证**位图编码与物理位序**。

**真实工作对应**：位图/枚举信号最常见的 bug 是"字节序反了/bit 偏移错一位"，
表现为"灯亮错一个"。逐灯断言 + 原始字节断言，两个视角互相印证。

### 29.6.7 用例 7：对象列表 4 个障碍物应正确解码

**测什么**：0x401 一帧 64 字节装 4 个障碍物，每个 3 个 float32（x/y/distance）。
依次设置 4 组坐标，等待更新后的帧，断言 12 个浮点信号 + ObjectCount=4。

**设计亮点**：这是 CAN FD "一帧装更多数据"的实际价值演示——经典 CAN 需要多帧才能
传完的 4 个目标，FD 一帧搞定。多信号同帧解码验证了偏移计算（第 n 个障碍物
offset = n×12 字节）的正确性。

### 29.6.8 用例 8：序列号与发送计数器应连续且能发现丢帧

```robotframework
    ${f1}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ${f2}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ${f3}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    FD Frame Counter Should Advance    ${f1}    ${f2}    ${f3}
    ...
    ${marker}=    Inject FD Error    sequence_loss    ${ID_STATUS}    count=1
    ${after}=     Wait For FD Frame    ${ID_STATUS}    since_seq=${marker}
    Run Keyword And Expect Error    *计数器*
    ...    FD Frame Counter Should Advance    ${before}    ${after}
```

**测什么**：① 正常连续帧：seq 严格递增、同 ID 的 counter 逐帧 +1；
② 注入 `sequence_loss`（消耗 counter 但不上总线）后，counter 跳号必须被关键字识别。

**真实工作对应**：安全相关报文常用计数器（E2E 保护的前身）检测丢帧/重放。
"数据看起来正常但 counter 跳了"正是总线丢帧的典型现场——只断言数据内容会漏掉这类故障。

### 29.6.9 用例 9：长度不符与 CRC 错误应被拒绝并识别

**测什么**（三个负向场景）：

1. **注入 length_mismatch**：帧的 data 长度与 DLC 映射不符 → 帧 `valid=false`，
   `FD Frame Length Should Be` 必须报"长度不符"（`Run Keyword And Expect Error` 证明灵敏度）；
2. **注入 crc_error**：帧 `crc_valid=false`（接收侧应能识别并丢弃）；
3. **测试端发非法帧**：64 字节数据 + DLC=8 → 仿真器**拒绝**（不回显，等待超时），
   并写入错误日志 `length_mismatch`。

**设计亮点**：既验证"被测节点发出的错误帧能被测试发现"，又验证"测试端发的错帧会被
被测节点拒绝"——两个方向的输入校验都覆盖。负数场景用 `Run Keyword And Expect Error` + 超时下界，
证明断言不是恒真。

### 29.6.10 用例 10：FD 与经典 CAN 应互不干扰

**测什么**：
1. `Get FD Bus Info` 断言 `bus_port=30003`、`bus_group=233.255.0.3`，且
   **不等于** 经典 CAN 端口 `30000`（端口隔离）；
2. `FD Bus Should Be Silent`：暂停周期发送后 0 帧，关键字内部自动恢复（静默测试能力）；
3. 发一帧经典 CAN 帧（8 字节、`is_fd=false`）：回显仍是经典帧，
   `Classic CAN Frame Should Not Be Decoded As FD` 验证它**不能**被 FD 解码。

**真实工作对应**：新老总线共存是真实项目的常态（FD 域控 + 经典 CAN 车身）。
"FD 帧和经典帧混在同一个工具里"时，最怕的是**把经典帧按 FD 解析**（DLC/长度语义完全不同）。
本用例把边界钉死。

### 29.6.11 需求标签覆盖

| 需求 ID | 含义 | 覆盖用例 |
|---|---|---|
| **REQ-CANFD-001** | 64 字节收发 | 用例 1、8、10 |
| **REQ-CANFD-002** | DLC 映射 | 用例 2 |
| **REQ-CANFD-003** | BRS/ESI 标志 | 用例 3、4 |
| **REQ-CANFD-004** | FD 信号解码与错误处理 | 用例 5、6、7、9 |

10 个用例、4 条需求全部被引用。按需求跑：`robot --include REQ-CANFD-002 tests/robot/12_canfd.robot`。

---

## 29.7 错误注入与边界（负向测试怎么设计）

### 29.7.1 三种注入方式

| 注入 | 仿真器行为 | 测试端怎么观测 | 对应用例 |
|---|---|---|---|
| `crc_error` | 帧照发，CRC 字段被改坏（`crc_valid=false`） | 读帧断言 `crc_valid=false` | 用例 9 |
| `length_mismatch` | DLC 不变、data 截短（`valid=false`、`error=length_mismatch`） | `FD Frame Length Should Be` 报"长度不符" | 用例 9 |
| `sequence_loss` | counter 照常 +1 但不上总线（下一帧跳号） | `FD Frame Counter Should Advance` 报"计数器跳号" | 用例 8 |

**注入语法的通用套路**（与 LIN 模块一致）：

```robotframework
${marker}=    Inject FD Error    length_mismatch    ${ID_STATUS}    count=1
${frame}=     Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker}
```

`Inject FD Error` 返回 `since_seq` 标记，保证等到的是"注入之后"的帧，
不会先读到注入前的正常帧（这是消除 flaky 的关键细节）。

### 29.7.2 为什么负向用例要写成 `Run Keyword And Expect Error`

正向断言（`FD Frame Length Should Be` 通过）不能证明"坏帧会被抓住"。
如果关键字实现成 `return frame`（不检查），正向用例照样全绿——这就是**恒真断言**。

负向写法：

```robotframework
Run Keyword And Expect Error    *长度不符*    FD Frame Length Should Be    ${bad}    64
```

含义：**"调用这个关键字，必须抛出匹配 `*长度不符*` 的错误"**。关键字真检查才通过。

### 29.7.3 输入校验也要测

除了"被测节点发错帧"，还要测"测试端/其他节点发错帧，被测节点是否拒绝"：
- 用例 9 发送 64 字节数据 + DLC=8 的非法帧 → 仿真器拒绝（不回显）、错误日志记录；
- 用例 10 发送经典 CAN 帧 → 确认它不会被 FD 逻辑错误处理。

真实 ECU 收到非法 DLC/长度时的行为（忽略/Ack 错误/复位）是协议一致性测试的一部分。

---

## 29.8 真实项目：接 CANoe 测 CAN FD

### 29.8.1 工具映射

| 本项目 | 真实工作对应物 | 你要会的事 |
|---|---|---|
| `simulators/canfd_sim.py`（ADAS 节点仿真） | 真实 ADAS 域控 / CANoe FD 仿真节点 | 配 FD 通道：仲裁速率 500k、数据速率 2M/5M、采样点 75~80% |
| UDP 组播 JSON 帧流 | CANoe Trace 窗口 / VN1630/VN5640 接口盒抓包 | 看 ID/DLC/BRS/ESI、过滤帧、看 64 字节数据 |
| `simulators/dbc/vehicle_fd.dbc` | 标准 CAN FD DBC（CANdb++ 编辑 / CANoe 加载） | 看 `SIG_VALTYPE_`、`VFrameFormat`、`CANFD_BRS` |
| `Set FD Signal / Send FD Frame` | CANoe 的 IG（Interactive Generator）/ CAPL 脚本发帧 | 用 IG 模块发 64 字节 FD 帧、置 BRS/ESI |
| `Inject FD Error` | 故障注入盒 / 线束改线 / CANoe 错误帧注入 | 注入 CRC 错、长度错、丢帧 |
| `Decode FD Signals` | CANoe Trace 的符号解码 / cantools | 从 DBC 解 float32/位图信号 |

### 29.8.2 CAN FD 台架配置清单（真实项目必看）

1. **速率**：仲裁段（500kbps）+ 数据段（2M/5Mbps，取决于 ECU 支持）；
2. **采样点**：常用 75%~80%（仲裁段和数据段可以分别配）；配错会偶发 CRC 错；
3. **SJW/位定时**：数据段高速时抖动窗口更小，需要精确配置；
4. **TDC（Transmitter Delay Compensation）**：数据段 >1Mbps 时通常必须开启/配置；
5. **收发器**：确认是 FD 收发器（经典 CAN 收发器无法跑 5Mbps 数据段）；
6. **ESI/BRS 支持**：老节点可能不支持 FD，混挂时要确认网关/节点的 FD 兼容性。

**测试顺序**（拿到一个支持 FD 的域控后）：
1. 冒烟：周期 FD 帧是否按 DLC/长度发出（BRS 位对不对）；
2. 数据：每个信号解码（float32/位图/枚举/大对象列表）；
3. 协议：DLC 边界、BRS/ESI、CRC 错误、帧类型边界（FD/经典）；
4. 时序：周期/抖动、大帧对总线负载的影响；
5. 容错：非法长度、丢帧（counter 跳号）、错误帧恢复；
6. 组网：FD 与经典 CAN 共存（网关路由、混合总线）。

### 29.8.3 本仿真器的边界（诚实说明）

本模块是**教学级 FD 语义仿真**，不是真实 CAN FD 控制器：
- 不模拟位定时、采样点、CRC17/21 硬件计算、填充位、错误帧；
- BRS/ESI 只作为帧字段传递，不模拟真实速率切换；
- 用 JSON + UDP 组播代替物理差分信号。

**能学的**：DLC 映射、64 字节帧的解析、BRS/ESI 语义、DBC 如何描述 FD、
错误注入与丢帧检测、FD/经典边界——这些**测试设计能力**与真实台架完全一致。
**不能学的**：示波器测眼图、采样点调试、真实总线负载——这些必须上真实硬件。

---

## 29.9 扩展练习（自己动手）

1. **加一个 FD 报文**：给 ADAS 节点加 `0x403 RadarTargets`（64 字节，8 个目标 × (距离 uint16, 角度 int16, 速度 int16)）。需要改哪些文件？
   提示：`simulators/canfd_sim.py` 的编码函数/调度、`simulators/dbc/vehicle_fd.dbc`、`libraries/CanFdLibrary.py` 的 `Decode FD Signals`，以及套件加一条解码用例。
2. **DLC 边界负向**：写一条用例，用 `Send Raw FD Frame` 发 `dlc=15` 但只给 32 字节数据，断言仿真器拒绝且错误日志含 `length_mismatch`。
3. **总线负载估算**：录 2 秒 FD 帧，按"每帧 bit 数 ≈ 47 + 数据长度×8（数据段按高速折算）"估算负载率，和经典 CAN 的估算公式对比差异。
4. **ESI 降级联动**：注入连续 CRC 错误让发送节点"状态变差"，扩展仿真器增加"错误计数→ESI 置位"逻辑，再写用例断言 ESI 变化（体会"机制先有，测试再有"）。
5. **与经典 CAN 模块联动**：写一个场景，经典 CAN 的 0x100 车速与 FD 的 0x400 车速一致性比对（跨总线信号一致性）。
6. **真实 DBC 交叉验证**：用项目已安装的 `cantools` 加载 `simulators/dbc/vehicle_fd.dbc`，对比 `Decode FD Signals` 的解码结果（学习"工具解码 vs 手工解码"的一致性验证）。

---

## 29.10 本章自检

1. CAN FD 相比经典 CAN 的四个核心变化是什么？分别解决什么问题？
2. DLC=9、10、15 分别对应多少字节？为什么 DLC 不能直接当字节数用？
3. BRS=1 时哪个阶段提速？为什么仲裁段不能提速？
4. ESI 是什么？它和"故障降级"有什么关系？
5. DBC 里怎么表示 float32 信号、怎么标记一个报文是 FD 帧 + 开 BRS？
6. 为什么测试端要等"仿真器回显（echo_of）"而不是直接读自己发出去的回环帧？
7. 发送计数器（counter）怎么做丢帧检测？举一个能失败的断言写法。
8. 仿真器注入 `length_mismatch` 后，`FD Frame Length Should Be` 应该怎么失败？
9. FD 和经典 CAN 共存时，最容易出的测试漏洞是什么？
10. 本仿真器与真实 CAN FD 硬件的差距在哪？哪些能力可以平移、哪些必须上真实台架？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | ①**数据段 8→64 字节**（大报文一帧传）；②**BRS 数据段提速**（带宽）；③**更强 CRC（17/21）**（大数据错误检测）；④**DLC 9~15 扩展编码**（长度压缩表示）。仲裁/ID 优先级等经典骨架保留 |
| 2 | 9→**12**、10→**16**、15→**64**。DLC 只有 4 bit，经典 CAN 只用到 0~8，FD 把 9~15 压缩编码成更大长度，所以"看 DLC 必须查映射表" |
| 3 | **数据段**提速（5/8Mbps）。仲裁段必须保持慢速：①要和经典 CAN 节点共存（老节点按 500k 听）；②速度越高、有效传输距离越短，仲裁需要覆盖整个总线长度 |
| 4 | Error State Indicator：发送节点是否进入 **error-passive**。接收方可据此降级/不信任该节点数据；是故障管理的输入之一 |
| 5 | `SG_ 信号 : 位段 (scale,offset) …` + `SIG_VALTYPE_ <msgid> <sig> : 1;`（1=float32，2=double）；FD 标记：`BA_ "VFrameFormat" BO_ <id> 14;`（StandardCAN_FD）+ `BA_ "CANFD_BRS" BO_ <id> 1;` |
| 6 | 本机组播回环会把测试端自己发的帧送回自己，直接读就是"自己发自己收"的恒真断言。仿真器校验后以 `echo_of` 回显，测试端等 `tester_seq` 匹配的 `origin=adas` 帧，才是真正"上过总线"的帧 |
| 7 | 仿真器每帧同 ID counter +1；`sequence_loss` 消耗 counter 不上总线 → 下一帧跳号。断言：`Run Keyword And Expect Error    *计数器*    FD Frame Counter Should Advance    ${before}    ${after}` |
| 8 | 帧被标记 `valid=false` 且 data 长度与 DLC 映射不一致 → 关键字抛含 **"长度不符"** 的 AssertionError（用 `Run Keyword And Expect Error    *长度不符*` 证明灵敏度） |
| 9 | 把经典 CAN 帧（≤8 字节、DLC≤8、无 BRS）按 FD 解析（比如用 64 字节布局/FLAG 解码）→ 数据错位或误判。必须有帧类型校验 + 边界用例 |
| 10 | 仿真器不模拟位定时/采样点/CRC17-21 硬件/填充位/真实速率切换。可平移：DLC 映射、64 字节解析、BRS/ESI 语义、DBC、错误注入与丢帧检测、FD/经典边界；必须上真实台架：示波器/眼图、采样点、TDC、真实总线负载/EMC |

---

**上一章** ← [28 · 功能安全与 ASPICE 测试过程](28_功能安全与ASPICE测试过程.md)　|　**下一章** → [30 · SOME/IP 与 SOA 服务测试](30_SOMEIP与SOA服务测试.md)

> 📌 Wave2 扩展篇导航：28 功能安全/ASPICE → **29 CAN FD（本章）** → [30 SOME/IP](30_SOMEIP与SOA服务测试.md) → 31 功能寻址/OBD-II → 32 功能安全机制验证 → 33 工程工具链 → 34 追踪分析
