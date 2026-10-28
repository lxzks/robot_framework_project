# 24 · 车载以太网与 DoIP 诊断：让 UDS 跑在网线上

> 前面 03/08 章，UDS 一直跑在 CAN 上（0x7E0/0x7E8 + ISO-TP 多帧）。
> 真实工作里还有另一条路：**诊断报文走以太网**——这就是 DoIP
> （Diagnostics over IP，ISO 13400）。4S 店的远程诊断、产线终检、
> OTA 刷写，走的都是它。
>
> 好消息是：**UDS 的服务、NRC、会话逻辑一个都没变**，变的只是"运输层"。
> 本章会把 CAN 上的 ECU 接到一条"假以太网"上，亲手跑通
> `Tester ──TCP 13400──> DoIP 网关 ──虚拟 CAN──> ECU` 的完整链路。
>
> 学习方法：先跑一遍套件（24.9 的命令），再打开
> `simulators/doip_gateway.py` 边读边对照本章的报文表。

---

## 📖 零基础读法建议

本章是**扩展篇**（22~25 章）的"通信最后一公里"，不需要一次啃完：

| 标记 | 章节 | 为什么这个优先级 |
|---|---|---|
| 🟢 **必读** | **24.2 DoIP 通用头**、**24.5 路由激活**、**24.9 本项目实现** | 这是"以太网诊断在测试什么"的核心：看头、看激活、看透传 |
| 🟡 **第二轮再读** | 24.1 为什么车载以太网、24.3 payload 表、24.6 诊断透传、24.7 连接保活、24.10 逐条讲套件 | 面试会问、联调时会用；有 CAN 基础后理解很快 |
| ⚪ **当字典查** | **24.8 协议错误映射表**、24.11 教学简化点 | 抓包对不上号时回来查；别背 |

> **一句话抓重点**：DoIP = **8 字节通用头 + 4 件事**
> （车辆识别 / 路由激活 / 诊断报文 / 连接保活）。
> UDS 还是那个 UDS，你 03 章学的服务号、NRC 全部照用。

---

## 24.1 为什么车载以太网：CAN 不够用了吗

CAN 很好，但它的上限很低：经典 CAN 只有 **1 Mbps**，一帧最多 8 字节。
一个 1080P 摄像头每秒的数据量，够 CAN 总线忙半天。所以现代车是**分层**的：

| 网络 | 速率 | 拓扑 | 典型用途 |
|---|---|---|---|
| CAN / CAN FD | 0.5~2 Mbps（FD 最高 8 Mbps） | 总线（广播） | 动力、底盘、车身——实时控制 |
| 车载以太网 100BASE-T1 | 100 Mbps | 交换机（点对点） | 域控主干、ADAS、IVI |
| 车载以太网 1000BASE-T1 | 1 Gbps | 交换机 | 中央计算、雷达/摄像头 |
| **DoIP** | 跑在以太网上 | TCP/UDP | **诊断 over IP**（远程诊断/OTA） |

> **不是替代，是分层**：以太网做"大数据主干"，CAN 做"实时控制末梢"。
> 一个中央网关同时挂着两条网，DoIP 网关就是"CAN ⇄ 以太网"的翻译官。

对测试工程师来说，这带来三个新东西：

1. **连接方式变了**：不再是"听 0x7E0"，而是"连 TCP 13400"。
2. **诊断入口变多**：以太网口暴露给了诊断仪（也暴露给了攻击者），所以
   DoIP 有**路由激活**这种"上车许可"机制。
3. **协议测试面变大**：CAN 上你只需要懂 UDS；DoIP 上你还得会测
   **头格式、握手、异常报文处置**——这正是 `tests/robot/07_doip_diagnostics.robot` 的内容。

---

## 24.2 DoIP 通用头：8 个字节定乾坤

DoIP 的每一条报文（不管是车辆识别还是诊断）都以这个头开始：

```
+---------------------------------------------------------------+
| protocol_version | inverse_version | payload_type | payload_length | payload |
|      1 B         |       1 B       |     2 B      |      4 B       |   N B   |
|     0x02         |      0xFD       |   big-endian |  big-endian    |         |
+---------------------------------------------------------------+
```

| 字段 | 长度 | 本仿真取值 | 说明 |
|---|---|---|---|
| protocol_version | 1 B | `0x02` | ISO 13400-2 版本号。本教学版只支持 0x02 |
| inverse_version | 1 B | `0xFD` | protocol_version **按位取反**（`~0x02 = 0xFD`），接收方用它 0 成本判断"这 8 字节到底是不是 DoIP 头" |
| payload_type | 2 B | 见 24.3 | 大端（网络字节序）：`00 01` 表示车辆识别请求 |
| payload_length | 4 B | 0~65536 | payload 字节数，**不含头** |
| payload | N B | — | 具体内容由 payload_type 决定 |

**记忆抓手**：头里没有"校验和"字段。DoIP 靠 **TCP 的可靠传输** + inverse 版本对
来保证可靠性；这也解释了为什么长度字段必须严格校验（见 24.8）。

Python 里解析/组包就一行（网关源码用的就是这个）：

```python
import struct
version, inverse, payload_type, payload_length = struct.unpack(">BBHI", header)
header = struct.pack(">BBHI", 0x02, 0xFD, payload_type, len(payload))
# ">" = 大端；B=1字节  B=1字节  H=2字节  I=4字节
```

### 24.2.1 三个能直接背下来的 hex 例子

```text
车辆识别请求（UDP，payload 空）：
02 FD 00 01 00 00 00 00
└┬─┘ └┬─┘ └─┬─┘ └──┬──┘
0x02 0xFD 0001      0 字节 payload

路由激活响应（TCP，payload 13 字节）：
02 FD 00 06 00 00 00 0D | 0E 00 0E 80 10 00 00 00 00 00 00 00 00
                          └tester┘ └entity┘ code  reserved  OEM

诊断请求（TCP，0x8001，里面装 UDS `10 03`）：
02 FD 80 01 00 00 00 06 | 0E 00 00 01 10 03
                          └ SA ─┘ └ TA ─┘ └ UDS
```

> ⚠️ **一个容易踩的坑**：`payload_length` 是 4 字节大端，不是 1 字节。
> 写负向用例构造"长度不符"时（24.8、用例⑨），改错字节位置会得到完全不同的
> 结果。套件里用的是 `02fd00010000000a02`——头声明 10 字节，实际只跟 1 字节。

> 📌 **版本差异（当字典查）**：`protocol_version` 的取值是"哪一版标准"的代号——
> `0x01` = ISO 13400-2:2010、`0x02` = ISO 13400-2:2012、`0x03` 起对应 2019 版
> （新增 TLS/认证等能力）。**通用头在所有版本里都是 8 字节**，没有"后续版本加长头"。
> ISO 13400-2 还允许 Tester 在"不知道车辆协议版本"时用 `0xFF` 发车辆识别请求，
> 本教学版只支持 `0x02`（见 24.11 简化点）。

---

## 24.3 payload type 与 payload 结构表

同一个 8 字节头，payload_type 决定"这条报文是什么"。本仿真支持的**教学子集**：

### 24.3.1 车辆识别 / 实体管理（UDP 或 TCP）

| payload_type | 名称 | 方向 | payload 结构 |
|---|---|---|---|
| `0x0000` | Generic DoIP header NACK | 实体→Tester | `code`(1B) |
| `0x0001` | 车辆识别请求 | Tester→实体 | 空 |
| `0x0002` | 车辆识别请求（带 EID） | Tester→实体 | `EID`(6B) |
| `0x0003` | 车辆识别请求（带 VIN） | Tester→实体 | `VIN`(17B) |
| `0x0004` | 车辆识别响应 / 车辆公告 | 实体→Tester | `VIN`(17B)+`逻辑地址`(2B)+`EID`(6B)+`GID`(6B)+`further action`(1B)=**32B** |
| `0x0005` | 路由激活请求 | Tester→实体 | `tester 地址`(2B)+`activation type`(1B)+`reserved`(4B)=7B |
| `0x0006` | 路由激活响应 | 实体→Tester | `tester 地址`(2B)+`实体地址`(2B)+`code`(1B)+`reserved`(4B)+`OEM`(4B)=13B |
| `0x0007` | alive check 请求 | Tester→实体 | 空 |
| `0x0008` | alive check 响应 | 实体→Tester | `源地址`(2B) |

### 24.3.2 诊断报文（TCP）

| payload_type | 名称 | 方向 | payload 结构 |
|---|---|---|---|
| `0x8001` | 诊断报文 | Tester→ECU / ECU→Tester | `源地址`(2B)+`目标地址`(2B)+`UDS 数据`(N B) |
| `0x8002` | 诊断报文肯定确认 | 实体→Tester | `源地址`(2B)+`目标地址`(2B)+`ack code`(1B，0x00=成功) |
| `0x8003` | 诊断报文否定确认 | 实体→Tester | `源地址`(2B)+`目标地址`(2B)+`NACK code`(1B) |

> **两个细节**：
> ① 上表的"源/目标地址"是 **DoIP 逻辑地址**（2 字节），不是 IP 地址！
> 逻辑地址是诊断世界里的"门牌号"，靠它做路由（本仿真：Tester=0x0E00、
> 网关实体=0x0E80、ECU=0x0001）。
> ② 0x8002/0x8003 里的 SA/TA **回显原诊断报文的 SA/TA**，Tester 靠它把
> "确认"和"请求"配对（教学版按此实现；真实 OEM 可能有差异，断言应以
> **ack code** 为准）。

### 24.3.3 响应码速查（协议错误怎么表示）

| 类型 | code | 含义 | 本仿真处置 |
|---|---|---|---|
| 通用头 NACK `0x0000` | `0x00` | incorrect pattern format | inverse 不符 / 协议版本不支持 → **回完关闭连接** |
|  | `0x01` | unknown payload type | 未知 payload type → 回完**保持连接** |
|  | `0x02` | message too large | payload_length 超 64KB → 回完关闭 |
|  | `0x04` | invalid payload length | 声明长度收不齐 / 类型长度不符 → 收不齐时关闭 |
| 路由激活 `0x0006` code | `0x10` | **routing successfully activated** | 成功 |
|  | `0x00` | unknown source address | tester 地址不在 0x0E00~0x0EFF |
|  | `0x02` | SA different from already activated | 同连接换 tester 地址 |
|  | `0x06` | unsupported routing activation type | 只支持 type 0x00 |
| 诊断 NACK `0x8003` | `0x06` | **target unreachable** | **路由未激活就发诊断** / ECU 无响应超时 |
|  | `0x02` | invalid source address | SA 与已激活 tester 不符 |
|  | `0x03` | unknown target address | 目标 ECU 不是 0x0001 |

> 这些 code 就是 `07` 套件负向用例断言的"预期值"。
> **协议测试的及格线不是"用例过了"，而是"你能说出每个 code 为什么是这个值"。**

---

## 24.4 车辆识别：Tester 怎么知道"我面前是哪台车"

诊断仪插上以太网口，只知道一个 IP/端口，并不知道这台车的 VIN、逻辑地址。
ISO 13400 用 **UDP** 做这件事（UDP 无连接，适合"广播问一圈"）：

```text
Tester                                        DoIP 实体（网关）
  │                                                │
  │  UDP 0x0001 车辆识别请求（payload 空）           │
  │ ──────────────────────────────────────────────► │
  │                                                │
  │  UDP 0x0004 车辆识别响应（32 字节）              │
  │ ◄────────────────────────────────────────────── │
  │  VIN(17) + 逻辑地址(2) + EID(6) + GID(6) + FA(1) │
```

响应 payload 拆开看（这 32 字节本章要能背下来）：

| 偏移 | 长度 | 字段 | 本仿真值 | 说明 |
|---|---|---|---|---|
| 0 | 17 | VIN | `LSVAM4180NC000001` | 车辆识别码，必须和 ECU 的 DID F190 一致 |
| 17 | 2 | 逻辑地址 | `0x0E80` | DoIP 实体地址，后续路由激活要用 |
| 19 | 6 | EID | `001122334455` | 实体标识（Entity ID），真实项目前 3 字节常是 OUI |
| 25 | 6 | GID | `000000000000` | 组标识（Group ID），0 = 不分组 |
| 31 | 1 | further action | `0x00` | 0x00 = 无需后续动作 |

> **测试关注点**：VIN 必须与 ECU 侧一致（同一台车）、逻辑地址必须与后续
> 路由激活响应里的实体地址一致、长度必须是 32 字节。
> 本仿真还可以发 `0x0002`（带 EID）和 `0x0003`（带 VIN）的"精确查找"，
> 但套件只用最基础的 `0x0001`——真实项目里多台车广播应答时才需要带条件查。

**用 RF 怎么写**（用例①）：

```robotframework
${vin}=    DoIP Vehicle Identification
Should Be Equal    ${vin}    LSVAM4180NC000001
Length Should Be    ${vin}    17
DoIP Vehicle Logical Address Should Be    0x0E80
DoIP Vehicle EID Should Be    001122334455
```

---

## 24.5 路由激活：以太网诊断的"上车许可"

CAN 是物理隔离的，插上 OBD 口才有可能发诊断；**以太网口可能暴露在
整车网络甚至公网上**，任何能连 TCP 13400 的节点都能发报文。所以 DoIP
规定：**TCP 连上不等于能诊断，必须先做路由激活（routing activation）**。

```text
Tester                                              DoIP 实体（网关）
  │  TCP 三次握手（13400）                              │
  │ ◄────────────────────────────────────────────────► │
  │                                                    │
  │  0x0005 路由激活请求                                │
  │  SA=0x0E00 + activation type=0x00 + reserved       │
  │ ──────────────────────────────────────────────────►│  ① 认 tester 地址
  │                                                    │  ② 登记"这个 socket 允许诊断"
  │  0x0006 路由激活响应                                │
  │  tester=0x0E00 + entity=0x0E80 + code=0x10         │
  │ ◄──────────────────────────────────────────────────│
  │                                                    │
  │  0x8001 诊断报文（此后才允许）                       │
  │ ──────────────────────────────────────────────────►│
```

**用 RF 怎么写**（用例②）：

```robotframework
DoIP Connect
${code}=    DoIP Routing Activation
Should Be Equal As Integers    ${code}    0x10
DoIP Activated Tester Address Should Be    0x0E00
DoIP Entity Address Should Be    0x0E80
```

路由激活有三条必须掌握的规则：

1. **状态按 TCP 连接隔离**。断开重连 = 新连接 = 必须重新激活
   （真实场景：OTA 断线重连后不能直接发诊断）。用例⑩专测这个。
2. **成功码是 0x10**，且响应要**回显 tester 地址**（让 Tester 确认网关认的是它）。
3. **失败不是静默的**：0x00/0x02/0x06 分别对应"地址不认识/地址冲突/激活类型
   不支持"——负向测试要逐条打。

> **为什么叫"路由"激活**：真实的 DoIP 实体（中央网关）后面挂着一堆 ECU，
> 它要根据 tester 地址 + 目标地址维护**路由表**。激活 = 建立"这个 tester
> 能访问哪些 ECU"的路由。

---

## 24.6 诊断报文透传：0x8001 里的 UDS

激活之后，UDS 请求就被**整包**塞进 DoIP 0x8001 的 payload：

```text
DoIP 0x8001 payload = SA(2B) + TA(2B) + UDS 请求

例：经 DoIP 发 UDS `10 03`（进扩展会话）
02 FD 80 01 00 00 00 06 | 0E 00 00 01 10 03
                          └SA=0x0E00 └TA=0x0001 └UDS=10 03
```

网关收到后的完整动作（**顺序很重要**）：

```text
① 校验路由激活状态 / 源地址 / 目标地址
② 先回 0x8002 肯定确认（ack code=0x00）
        —— 告诉 Tester："报文我收到了、格式没问题"，不等 ECU 响应
③ 把 UDS 转发到 CAN：0x7E0 请求 → ECU
④ ECU 回 0x7E8（20 字节 VIN 响应要 ISO-TP FF/CF/FC 多帧重组）
⑤ 把完整 UDS 响应包成 0x8001，方向反过来（SA=0x0001，TA=0x0E00）
⑥ 回给 Tester
```

本项目的实现把 ③④ 直接**复用**了 `simulators/uds_client.py` 的
`DiagnosticTester`——也就是 02 套件用的同一个类。这不是偷懒，而是真实架构的
映射：**诊断服务只实现一次，运输层随便换**。

### 24.6.1 多帧透传：一条用例验证两侧协议

UDS `22 F1 90`（读 VIN）的响应是 **20 字节**：
`62 F1 90` + 17 字节 VIN。在 CAN 上这必然超过单帧 7 字节 → 必须
**FF + CF + FC 多帧**；在 DoIP 上 20 字节一条报文就发完了。

所以用例④一次验证了两段转换：

```text
UDS 响应(20B) --ISO-TP 拆帧--> 0x7E8 FF+CF --网关重组--> DoIP 0x8001 整包
```

**用 RF 怎么写**（用例④）：

```robotframework
DoIP Connect
DoIP Routing Activation
${vin}=    DoIP Read VIN
Should Be Equal    ${vin}    ${VIN}
Length Should Be    17
```

真正的 DoIP 0x8001 长这样（payload 24 字节 = SA2+TA2+UDS20）：

```text
02 FD 80 01 00 00 00 18 | 00 01 0E 00 | 62 F1 90 4C 53 56 41 ...
                          └SA=ECU  └TA=Tester└ UDS 正响应 62 F1 90 + VIN ASCII
```

---

## 24.7 alive check：防止"假在线"的半开连接

TCP 有一个经典问题：对端掉电/网线拔掉时，本地 socket 可能长时间维持
"已连接"假象（半开连接）。如果 Tester 以为通道正常，后面的诊断请求会
**全部超时**，排查半天才发现是链路死了。

DoIP 的答案：**alive check**。

```text
Tester                                     DoIP 实体
  │ 0x0007 alive check 请求（payload 空）      │
  │ ────────────────────────────────────────► │
  │ 0x0008 alive check 响应（源地址 2B）        │
  │ ◄──────────────────────────────────────── │
  │ 0x0E80（DoIP 实体地址）                     │
```

**用 RF 怎么写**（用例⑤）：

```robotframework
DoIP Connect
DoIP Routing Activation
${address}=    DoIP Alive Check
Should Be Equal As Integers    ${address}    0x0E80
```

真实项目里 alive check 由 Tester 按定时器周期发（比如 2s 一次），
本项目由用例**主动触发**，验证请求/响应格式与地址正确性即可
（定时器属于简化点，见 24.11）。

---

## 24.8 协议错误与安全：负向用例才是重头戏

网关是**攻击面**：一个格式非法的报文如果让网关崩溃、或者被"宽容地"
转给 ECU，都是真实缺陷。所以 `07` 套件有 4 条负向用例（⑥⑦⑧⑨），
每条都要求"**明确证明被拒绝**"——用 `Run Keyword And Expect Error` 或
`DoIP Should Get NACK` 断言，绝不允许"看起来没报错就算过"。

| # | 注入的错误 | 网关应答 | 连接 | 为什么这样处置 |
|---|---|---|---|---|
| ⑥ | **未路由激活**就发 0x8001 | 诊断 NACK `0x8003` code=**0x06** | 保持 | 拒绝这一条报文，但不能误杀连接（Tester 可能只是漏了激活） |
| ⑦ | **未知 payload type**（0x1234） | 通用头 NACK `0x0000` code=**0x01** | **保持** | 类型不认识但字节流完好，可以继续用 |
| ⑧ | **非法协议版本**（0x01） | 通用头 NACK `0x0000` code=**0x00** | **关闭** | 头都不可信了，流式解析必须重置（关连接是唯一方式） |
| ⑨ | **payload 长度不符**（声明 10 实发 1） | 通用头 NACK `0x0000` code=**0x04** | **关闭** | 同上：TCP 是字节流，长度字段错 = 边界丢失，只能断开重来 |

> **为什么 ⑧⑨ 必须关连接**：TCP 没有"报文边界"概念，DoIP 完全依赖
> `payload_length` 来切分报文。一旦长度字段不可信，后面的字节流就全错位了——
> 继续解读等于"接着错误往下蒙"。关掉连接让 Tester 重连，是最安全的失败方式。

**负向用例的三板斧**（`libraries/DoipLibrary.py` 提供）：

```robotframework
DoIP Send Raw Frame    02fd123400000000      # 想发什么非法字节都行
DoIP Should Get NACK    0x01    Generic      # 断言 NACK 类型 + code（错误信息带实际 payload）
DoIP Connection Should Be Closed             # 断言网关关闭了连接
```

用例⑥还多做了一步：NACK 之后**补一次路由激活并断言成功**，证明
"网关拒绝的是那一条报文，不是把我踢下线"。这种"拒绝但不误杀"的边界，
是真实网关测试非常容易出缺陷的地方。

### 24.8.1 安全视角：DoIP 为什么会有信任问题

| 风险 | 真实世界的表现 | 测试/设计对策 |
|---|---|---|
| 未授权诊断 | 任何连上 13400 的节点都能发 0x8001 | 路由激活 + 逻辑地址白名单 |
| 强认证缺失 | tester 地址可以伪造 | 真实 DoIP 支持 TLS + authenticated routing activation（本仿真未实现） |
| 报文洪泛/畸形包 | 网关内存耗尽或崩溃 | payload 长度上限、未知类型快速拒绝、单连接串行处理 |
| 误删 ECU 数据 | 刷写类服务被随意调用 | 网关只做透传，权限由 ECU 的 0x27 安全访问兜底 |

> 一句话：**DoIP 的路由激活解决"能不能进来"，UDS 的 0x27 解决"进来后能干什么"**。
> 两层权限模型缺一不可。

---

## 24.9 本项目实现：文件地图与运行方式

### 24.9.1 文件地图

| 文件 | 作用 | 关键点 |
|---|---|---|
| `simulators/doip_gateway.py` | DoIP 实体仿真：TCP 13400 收 DoIP 帧、UDP 13400 做车辆识别、把 UDS 转给 CAN 上的 ECU | 复用 `VirtualCanBus` + `DiagnosticTester`，不重复造 CAN 轮子 |
| `libraries/DoipLibrary.py` | RF 关键字库：管网关子进程 + 实现 Tester 侧的 DoIP 客户端 | 纯 socket，不碰 CAN；`ROBOT_LIBRARY_SCOPE=SUITE` |
| `tests/robot/07_doip_diagnostics.robot` | 10 条用例（5 正 4 负 1 回归），打 5 个需求标签 | 复用 `resources/common.resource` 拉起 ECU |
| `docs/24_车载以太网与DoIP诊断.md` | 本章 | 你正在读 |

### 24.9.2 数据流与进程模型

```text
┌─────────────────────────────┐
│ robot 测试进程                │
│  resources/common.resource   │──Start Vehicle Environment──┐
│     ├─ VehicleBusLibrary     │                             │
│     ├─ DiagnosticLibrary ────┼──► 虚拟 CAN 233.255.0.1:30000
│     ├─ DashboardLibrary      │                             │
│     └─ AdbLibrary            │                             │
│  DoipLibrary（本模块）         │                             │
│     ├─ 管理网关子进程          │                             │
│     └─ DoIP 客户端(TCP/UDP)   │                             │
└──────────┬──────────────────┘                             │
           │ TCP/UDP 127.0.0.1:13400                        │
           ▼                                                ▼
┌─────────────────────────────┐                  ┌──────────────────┐
│ doip_gateway.py（子进程）      │                  │ ecu_sim.py        │
│  ├─ TCP 服务端（DoIP）        │──虚拟 CAN(复用)──►│ 周期报文 +        │
│  ├─ UDP 车辆识别              │                  │ UDS 诊断(0x7E0/8) │
│  └─ DiagnosticTester ────────┘                  └──────────────────┘
└─────────────────────────────┘
```

**为什么网关必须是子进程**（而不是测试进程里的线程）：

- 网关内部要开一条虚拟 CAN 总线（DiagnosticTester）。如果它在测试进程里，
  同一进程就有 **2 个**"收报文"的消费者，和 `resources/common.resource` 的
  DiagnosticLibrary 抢同一份组播帧——这正是 `docs/06` 里记录的"多消费者"
  经典坑。子进程 = 天然隔离的两条总线连接，互不干扰。

### 24.9.3 运行方式

```bash
# 跑整个 DoIP 套件（会自动拉起 ECU + 网关，跑完自动回收）
.\.venv\Scripts\python.exe -m robot --outputdir results/dev_doip tests\robot\07_doip_diagnostics.robot

# 只跑某个需求标签
.\.venv\Scripts\python.exe -m robot --include REQ-DOIP-004 tests\robot\07_doip_diagnostics.robot

# 手动起网关观察日志（另开一个终端先起 ecu_sim.py 更能看到完整透传）
.\.venv\Scripts\python.exe simulators/doip_gateway.py
```

跑完看两个产物：

- `results/dev_doip/report.html` / `log.html`：用例结果、每条 DoIP 收发明细
- `results/doip_gateway_stdout.log`：**网关视角**的日志（收到什么帧、回了什么码、
  UDS 转发结果）——排查"到底哪一层没通"的第一现场

### 24.9.4 端口约定

| 端口 | 协议 | 用途 | 覆盖方式 |
|---|---|---|---|
| 127.0.0.1:**13400** | TCP | DoIP 帧（车辆识别响应/路由激活/诊断透传） | 环境变量 `DOIP_TCP_PORT` |
| 127.0.0.1:**13400** | UDP | 车辆识别请求/响应 | 环境变量 `DOIP_UDP_PORT` |
| 233.255.0.1:**30000** | UDP 组播 | 虚拟 CAN（与 01~06 套件共用，**不变**） | 不覆盖 |

> TCP 和 UDP 可以同号（13400 是 ISO 13400 规定的 DoIP 口），与虚拟 CAN 的
> 30000 完全错开。`DoipLibrary` 和网关读同一套环境变量，所以：
>
> ```powershell
> $env:DOIP_TCP_PORT="13401"; $env:DOIP_UDP_PORT="13401"
> python -m robot --outputdir results/dev_doip tests\robot\07_doip_diagnostics.robot
> ```
>
> 常用于：13400 被别的程序占用、或需要并行跑两套仿真时。

### 24.9.5 关键字表（`libraries/DoipLibrary.py`）

| 关键字 | 作用 | 返回 |
|---|---|---|
| `Start DoIP Gateway` | 启动网关子进程，等"功能就绪"（UDP 识别 + TCP 可连） | — |
| `Stop DoIP Gateway` | 回收网关子进程（幂等） | — |
| `DoIP Gateway Should Be Running` | 断言网关进程在线（排错用） | — |
| `DoIP Vehicle Identification` | UDP 车辆识别 | VIN 字符串（并归档全部字段） |
| `DoIP Vehicle Logical Address Should Be` | 断言车辆识别响应逻辑地址 | — |
| `DoIP Vehicle EID Should Be` | 断言车辆识别响应 EID | — |
| `DoIP Connect` / `DoIP Disconnect` | 建/断 DoIP TCP 连接 | — |
| `DoIP Routing Activation` | 发 0x0005、收 0x0006 | code（成功=0x10） |
| `DoIP Activated Tester Address Should Be` | 断言路由激活回显的 tester 地址 | — |
| `DoIP Entity Address Should Be` | 断言路由激活响应里的实体地址 | — |
| `DoIP Send Diagnostic` | UDS 封装成 0x8001 发出（只发不收） | — |
| `DoIP Read ACK` | 收 0x8002/0x8003 确认 | code（正 ACK=0x00） |
| `DoIP Read Response` | 收 0x8001 诊断响应 | UDS 字节 hex（如 `5003003201f4`） |
| `DoIP Read VIN` | 22 F1 90 全流程（发+确认+响应+解 ASCII） | VIN |
| `DoIP Alive Check` | 0x0007 → 0x0008 | 源地址（0x0E80） |
| `DoIP Should Get NACK` | 断言 NACK 类型 + code（负向用例专用） | code |
| `DoIP Connection Should Be Closed` | 断言网关已关闭连接 | — |
| `DoIP Send Raw Frame` | 发任意原始字节（构造协议错误） | — |

> 💡 **关键字设计原则**：断言类关键字失败时抛 `AssertionError` 并带
> **实际收到的 payload**；读取类关键字返回**标量**（int/str）而不是大对象，
> 让用例能用 `Should Be Equal` 直接写——日志可读性 > 省几行代码。

### 24.9.6 实现里的三个工程细节

1. **先绑端口，再起线程**：端口被占用时 `Start DoIP Gateway` 立刻报
   "TCP 端口 13400 已被占用"，而不是等第一条诊断超时才暴露。
2. **就绪 = 功能可用**：启动后不只看进程活着，还要求 UDP 车辆识别有合法
   响应 + TCP 能连上，才认为网关就绪。
3. **子进程日志 UTF-8**：Windows 控制台默认 GBK，子进程日志里带中文会乱码；
   启动网关时注入 `PYTHONIOENCODING=utf-8`，保证失败时能把日志尾部原样
   读出来放进报错信息（排错体验）。

---

## 24.10 逐条讲 `tests/robot/07_doip_diagnostics.robot`

### 24.10.1 用例总表

| # | 用例 | 类型 | 验证点 | 需求标签 |
|---|---|---|---|---|
| ① | 通过 UDP 车辆识别应拿到 VIN 与逻辑地址 | 正向 | 0x0004 32 字节结构、VIN/EID/逻辑地址 | REQ-DOIP-001 |
| ② | 路由激活应返回 0x10 并回显 tester 地址 | 正向 | 0x0006 13 字节结构、code=0x10、地址回显 | REQ-DOIP-002 |
| ③ | 经 DoIP 通道进入扩展会话应得到正响应 | 正向 | 0x8001 透传 UDS 10 03、0x8002 ACK | REQ-DOIP-003 |
| ④ | 经 DoIP 读取 VIN 应透传 ISO-TP 多帧响应 | 正向 | 20 字节 UDS 响应经 CAN 多帧重组后透传 | REQ-DOIP-003 |
| ⑤ | alive check 应返回 DoIP 实体地址 | 正向 | 0x0007 → 0x0008、源地址=0x0E80 | REQ-DOIP-005 |
| ⑥ | 未路由激活时发送诊断报文应被 NACK 0x06 拒绝 | **负向** | 0x8003 code=0x06、连接保持 | REQ-DOIP-004 |
| ⑦ | 未知 payload type 应收到 generic NACK 0x01 | **负向** | 0x0000 code=0x01、连接保持 | REQ-DOIP-004 |
| ⑧ | 非法协议版本应被 NACK 0x00 并关闭连接 | **负向** | 0x0000 code=0x00 + 连接关闭 | REQ-DOIP-004 |
| ⑨ | payload 长度与实际不符应被拒绝 | **负向** | 0x0000 code=0x04 + 连接关闭 | REQ-DOIP-004 |
| ⑩ | 断开重连后重新路由激活应能继续诊断 | 回归 | 连接级状态隔离、重连后 UDS 3E 00 可用 | REQ-DOIP-005 + REQ-DOIP-003 |

> 10 条用例打 5 个需求标签，标签统计：REQ-DOIP-001×1、002×1、003×3、
> 004×4、005×2（用例⑩同时覆盖 003/005，所以标签数多于用例数）。

### 24.10.2 三条值得逐行读的用例

**用例①：先看"能不能找到车"，再谈诊断**

```robotframework
${vin}=    DoIP Vehicle Identification
Should Be Equal    ${vin}    ${VIN}
Length Should Be    ${vin}    17
DoIP Vehicle Logical Address Should Be    ${DOIP_ENTITY_ADDRESS}
DoIP Vehicle EID Should Be    001122334455
```

为什么断言顺序是 VIN → 长度 → 地址 → EID：先验证最核心的 VIN，再验证
**长度**（32 字节结构的硬性约束），最后验证两个辅助字段。任何一步错，
错误信息都会直接告诉你"是结构错了还是值错了"。

**用例⑥：负向用例要证明"拒绝但不误杀"**

```robotframework
DoIP Connect
DoIP Send Diagnostic    10 03
DoIP Should Get NACK    0x06    Diagnostic
${code}=    DoIP Routing Activation
Should Be Equal As Integers    ${code}    0x10
```

最后两行是本用例的灵魂：如果网关"一怒之下"关掉连接，重激活就会失败。
**真实缺陷往往就出在这种边界**——安全逻辑写成了"发现异常就断开一切"，
结果正常的异常恢复流程也坏了。

**用例⑨：长度不符为什么必须关连接**

```robotframework
DoIP Connect
DoIP Send Raw Frame    02fd00010000000a02
DoIP Should Get NACK    0x04    Generic
DoIP Connection Should Be Closed
```

`02fd00010000000a02` 逐字节拆：

```text
02 FD          DoIP 头：版本 + 反码
00 01          payload type = 车辆识别请求
00 00 00 0A    payload_length = 10（撒谎：实际只跟 1 字节）
02             实际发出的 1 字节 payload
```

网关会等那缺失的 9 字节，超时（1s）后判定"长度与实际不符" → NACK 0x04 +
关闭。这条用例同时教会你一个底层事实：**TCP 是字节流，DoIP 的报文边界
完全靠 payload_length**。

### 24.10.3 实跑结果

```text
$ .\.venv\Scripts\python.exe -m robot --outputdir results/dev_doip tests\robot\07_doip_diagnostics.robot
==============================================================================
07 Doip Diagnostics :: 套件 07：车载以太网 / DoIP 诊断测试
==============================================================================
通过 UDP 车辆识别应拿到 VIN 与逻辑地址                                | PASS |
路由激活应返回 0x10 并回显 tester 地址                                | PASS |
经 DoIP 通道进入扩展会话应得到正响应                                  | PASS |
经 DoIP 读取 VIN 应透传 ISO-TP 多帧响应                               | PASS |
alive check 应返回 DoIP 实体地址                                      | PASS |
未路由激活时发送诊断报文应被 NACK 0x06 拒绝                           | PASS |
未知 payload type 应收到 generic NACK 0x01                            | PASS |
非法协议版本应被 NACK 0x00 并关闭连接                                 | PASS |
payload 长度与实际不符应被拒绝                                        | PASS |
断开重连后重新路由激活应能继续诊断                                    | PASS |
------------------------------------------------------------------------------
10 tests, 10 passed, 0 failed
```

> **和 02 套件的关系**：02 是"UDS 跑在 CAN 上"，07 是"同一个 UDS 跑在
> 以太网上"。两条链路共享同一个 `simulators/ecu_sim.py`——所以 02 套件的用例逻辑
> （断言 SID/NRC/payload）在 07 里完全适用，只是发送与接收的关键字换了一套。
> 这就是"分层"在测试代码里的体现。

---

## 24.11 教学简化点：和真实 ISO 13400 差在哪

**诚实地列出"什么是假的"**，比假装完整更重要——面试被追问时不会翻车：

| # | 教学版做法 | 真实 ISO 13400 / 工程做法 | 影响 |
|---|---|---|---|
| 1 | 用 **127.0.0.1 回环 TCP** 当"以太网" | 100BASE-T1/1000BASE-T1、交换机、VLAN、真实 IP | 不测物理层/交换机，只测 DoIP 应用层 |
| 2 | 只实现 **protocol_version=0x02（2012 版）** | 2019 版起版本号为 `0x03`（头仍 8 字节，新增 TLS/认证等）；车辆识别请求也允许用 `0xFF` | 2019 版设备对接时需扩展版本号接受范围与新增能力 |
| 3 | 车辆识别用 **UDP 点对点单播** | UDP 广播/组播 + 车辆上电主动发 `0x0004` 公告 | 本仿真不会主动"喊"，只能被问 |
| 4 | **单 DoIP 实体 + 单 ECU（0x0001）** | 一个网关后挂几十个 ECU，按逻辑地址路由表转发 | 多 ECU 路由、路由表维护未覆盖 |
| 5 | **无 TLS / 无认证** | TLS、authenticated routing activation、证书管理 | 安全测试只覆盖"协议错误"，不覆盖加密/认证 |
| 6 | DoIP 报文上限 **64 KB**，不分段 | 实际可到 16 MB 级；大诊断结果分多条 0x8001 | 大数据量（如长 DTC 列表、刷写包）透传未覆盖 |
| 7 | UDS 超时**自动重试 1 次** | 由 P2/P2* 定时器决定重发，且非幂等服务不可随意重试 | 教学目的是抗仿真总线偶发丢帧；真实实现要更严谨 |
| 8 | TCP 上也接受车辆识别请求 | ISO 规定车辆识别走 UDP（TCP 上收到应拒绝） | 降低了协议严格度，方便单独验证 |
| 9 | 只支持 **activation type 0x00**、tester 地址段 0x0E00~0x0EFF | 多种 activation type（WWH-OBD/central security 等），地址由 OEM 分配 | 负向可测"不支持的类型"，正向只测默认 |

> **如果要升级成"更真实"的仿真**：把回环换成真实网卡 socket、升级到 2019 版
> 版本号/TLS、实现主动公告、多 ECU 路由表——但**测试用例的骨架不需要变**，
> 这也是分层设计（Tester/网关/ECU 解耦）的价值。

---

## 24.12 本章自检

1. DoIP 通用头一共几字节？每个字段什么含义？为什么要有 inverse_version？
2. 写出"路由激活请求"和"路由激活响应"的 payload 结构，并说出 code=0x10 代表什么。
3. 车辆识别响应 32 字节怎么拆？为什么 VIN 要和 ECU 的 DID F190 一致？
4. 未路由激活就发 0x8001，网关回什么？连接要不要关？为什么？
5. 为什么"未知 payload type"可以保持连接、"长度不符"却必须关闭连接？
6. 经 DoIP 读 VIN 为什么能验证"两侧协议转换"？FF/CF/FC 分别发生在哪一段？
7. 断开重连后还能直接发诊断吗？为什么？真实 OTA 场景里这意味着什么？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | **8 字节**：protocol_version(1B,0x02) + inverse_version(1B,0xFD=~version) + payload_type(2B 大端) + payload_length(4B 大端)，后接 payload。inverse 用于接收方 0 成本判断"这是不是合法 DoIP 头"，头错了就关闭连接 |
| 2 | **请求**：tester 地址(2)+activation type(1)+reserved(4)=7B；**响应**：tester 地址(2)+实体地址(2)+code(1)+reserved(4)+OEM(4)=13B。**code=0x10 表示路由激活成功**，之后该 TCP 连接才能发诊断报文 |
| 3 | VIN(17)+逻辑地址(2)+EID(6)+GID(6)+further action(1)。VIN 一致证明"这是同一台车"：UDP 车辆识别和 UDS 读 VIN 来自同一份车辆数据，不一致就是数据同步缺陷 |
| 4 | 回 **0x8003 诊断 NACK，code=0x06（target unreachable）**；**连接保持**。只拒绝这一条报文，因为 Tester 可能只是漏了路由激活；把连接关掉会让正常的"先发后激活"恢复流程也失效 |
| 5 | 未知 payload type 时**字节流边界仍然完好**（长度已正确读出），可以继续解析下一条；长度不符时 **DoIP 报文边界已丢失**，TCP 后续字节全部错位，继续解读只会更错 → 关连接重来 |
| 6 | VIN 响应 20 字节 > CAN 单帧 7 字节：CAN 侧 **FF（首帧）+ FC（ECU 等流控）+ CF（连续帧）** 完成多帧传输；网关内 DiagnosticTester 重组出完整 20 字节后，**整包放进一条 DoIP 0x8001**。所以一条用例同时验证了 ISO-TP 重组和 DoIP 封装 |
| 7 | **不能**。路由激活状态是**按 TCP 连接**保存的，重连 = 新连接 = 必须重新激活。真实 OTA/远程诊断断线重连后必须重走路由激活，否则诊断请求会被 NACK 0x06 |

---

**上一章：[23 · LIN 总线与车身控制测试](23_LIN总线与车身控制测试.md)** ｜ **下一章：[25 · HMI 视觉自动化测试](25_HMI视觉自动化测试.md)**

**相关章节**：[02 车载网络基础](02_车载网络基础.md)（CAN 以太网分层）、
[03 UDS 协议详解](03_UDS协议详解.md)（UDS 服务与 ISO-TP）、
[06 环境搭建与项目结构](06_环境搭建与项目结构.md)（虚拟总线与多消费者坑）、
[08 UDS 诊断测试实战](08_UDS诊断测试实战.md)（CAN 侧诊断套件逐条讲）
