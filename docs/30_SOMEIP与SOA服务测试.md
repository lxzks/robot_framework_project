# 30 · SOME/IP 与 SOA 服务测试

> 前面 29 章讲 CAN FD，是把"一帧信号"做得更快更大。本章讲一个更根本的变化：
> 车上的软件架构正在从"**信号**"转向"**服务**"——这就是 SOA
> （Service-Oriented Architecture，面向服务架构）。
>
> 你可能听过这些词：SOA、SOME/IP、服务发现、Method/Event/Field、ARXML。
> 本章会用一套可跑的仿真服务，把 SOA 测试最核心的三件事讲透：
> **服务能不能被发现、方法调用对不对、事件订阅收不收得到**。
>
> 学习方法：先跑一遍套件（30.8 的命令），再打开
> `simulators/someip_service.py` 边读边对照本章的报文表。

---

## 📖 零基础读法建议

本章是扩展篇里"面向服务"的入口。别被 SD、序列化这些词吓到——你只需要记住
**一个服务有三种交互方式**，其余都是格式细节：

| 标记 | 章节 | 为什么这个优先级 |
|---|---|---|
| 🟢 **必读** | **30.2 SOME/IP 头**、**30.5 Method/Event/Field**、**30.8 本项目实现** | 写用例、看抓包、面试都靠这三块 |
| 🟡 **第二轮再读** | 30.1 为什么转向 SOA、30.4 SD 服务发现、30.6 事件订阅、30.10 逐条讲套件 | 联调/排错时用；技术面会问服务发现流程 |
| ⚪ **当字典查** | **30.3 序列化**、**30.7 返回码表**、30.9 教学简化点 | 对字节、对错误码时回来查，别背 |

> **一句话抓重点**：SOME/IP = **16 字节头 + 三种交互**
> （Method 一问一答 / Event 订阅推送 / Field 可读可写可通知），
> 外加一个"**服务发现**"负责让客户端先找到服务在哪。

---

## 30.1 为什么车载软件要转向 SOA

回顾一下前 29 章的世界：ECU 周期发 CAN 报文，仪表按 DBC 解码显示。
这在"功能固定"的年代没问题，但智能汽车变了：

| 变化 | 带来的问题 |
|---|---|
| 功能越来越多（ADAS、座舱、OTA） | 信号矩阵爆炸：一个功能要跨 5 个 ECU 协调 N 个信号 |
| 软件要独立升级 | 信号语义写死在 DBC 里，**没有接口契约**，改一处全车回归 |
| 算力上移到域控/中央计算 | 跨进程/跨板通信变成常态，CAN 带宽和寻址方式不够用 |

**SOA 的答案**：把车辆功能定义成**服务（Service）**，每个服务有：
服务号、版本、实例、方法（Method）、事件（Event）、字段（Field），
并写进接口描述文件（ARXML）。谁需要就"**发现 → 订阅/调用**"，而不是提前
约定好一根线。

承载这套机制的通信中间件，在车载领域就是 **SOME/IP**
（Scalable service-Oriented MiddlewarE over IP），跑在车载以太网上。
它和 24 章的 DoIP 是"同事"关系：

| 协议 | 跑在哪 | 面向谁 | 解决什么 |
|---|---|---|---|
| DoIP（ISO 13400） | TCP/UDP 13400 | 诊断仪 | 诊断 over IP（读 DTC、刷写、路由激活） |
| **SOME/IP** | UDP/TCP 30490/30501 | 车内的软件组件 | **服务 over IP**（功能调用、事件推送） |
| SOME/IP-SD | UDP 30490 | 服务端 ↔ 客户端 | 服务发现（Find/Offer/Subscribe） |

> 所以测试的工作也变了：从"断言信号值"变成"**断言接口契约**"——
> 服务能不能被发现、方法参数/返回对不对、事件周期和值对不对、
> 错误请求会不会被正确拒绝。

---

## 30.2 SOME/IP 报文头：16 个字节，四个字段

所有 SOME/IP 报文（Method 请求/响应、Event 通知、甚至 SD 服务发现）都从
**16 字节头**开始：

```
+----------+----------+----------+----------+---------------------------+
| message_id (4B)     | length (4B)         | request_id (4B)           |
| service_id(2B)      | = 8 + payload 长度   | client_id(2B)             |
| method_id(2B)       |                     | session_id(2B)            |
+----------+----------+----------+----------+----------+----+----+----+
| protocol | interface| message  | return   | payload (N B)              |
| version  | version  | type     | code     |                            |
| (1B)     | (1B)     | (1B)     | (1B)     |                            |
+----------+----------+----------+----------+----------------------------+
```

| 字段 | 长度 | 说明 | 测试关注点 |
|---|---|---|---|
| message_id | 4 B | 高 2B = service_id，低 2B = method/event_id | 服务号/方法号必须与接口契约一致 |
| length | 4 B | **从 request_id 到报文结尾**的字节数 = `8 + payload 长度`（注意不含 message_id 和 length 本身） | 最常见的解析 bug 点：把 length 当成"整包长度"就会全错 |
| request_id | 4 B | 高 2B = client_id，低 2B = session_id | 响应必须**原样回显**；乱序/并发时靠它配对请求 |
| protocol_version | 1 B | 固定 `0x01` | 版本错了服务端应拒收 |
| interface_version | 1 B | 服务接口主版本号（本仿真 = 1） | **集成期高频故障**：ARXML 版本不一致 → 返回码 0x03 |
| message_type | 1 B | 见下表 | 发错类型（如给服务端发 RESPONSE）应被拒绝 |
| return_code | 1 B | 0x00=OK，其他=错误（30.7） | 负向测试断言的核心 |

**message_type 取值**：

| 值 | 名称 | 方向 | 用途 |
|---|---|---|---|
| `0x00` | REQUEST | Client→Server | 方法调用，需要响应 |
| `0x01` | REQUEST_NO_RETURN | Client→Server | 方法调用，不需要响应（"发了就不管"） |
| `0x02` | NOTIFICATION | Server→Client | 事件/字段变化推送 |
| `0x80` | RESPONSE | Server→Client | 方法成功响应 |
| `0x81` | ERROR | Server→Client | 方法失败响应（带 return_code） |

### 30.2.1 三条能直接背下来的 hex 例子

```text
① GetSpeed 请求（VehicleService 0x1234 / method 0x0001）
12 34 00 01 | 00 00 00 08 | 00 01 00 01 | 01 01 00 00
└─ msg_id ─┘ └─ length ─┘ └ request_id┘ └proto┘└iface┘└type┘└rc ┘
  16 字节头，payload 空（length = 8）

② GetSpeed 响应（车速 60 km/h = int16 0x003C）
12 34 00 01 | 00 00 00 0A | 00 01 00 01 | 01 01 80 00 | 00 3C
                          ↑ request_id 原样回显      ↑ RESPONSE  ↑ rc=OK ↑ int16 60
payload = 2 字节 → length = 8 + 2 = 0x0A

③ SpeedChanged 事件通知（每 500ms 一条，车速 60）
12 34 80 01 | 00 00 00 0A | 00 00 00 05 | 01 01 02 00 | 00 3C
      ↑ event_id 0x8001                 ↑ 服务端 client_id=0，session 递增
                                          ↑ NOTIFICATION
```

> ⚠️ **最容易考的细节**：`length` 字段是 `8 + payload`，不是整包长度。
> 一个 16 字节头 + 2 字节 payload 的报文，length 写 `0x0A`（10），
> 不是 18、也不是 2。写错 length 的测试端是最常见的"假 bug 制造机"。

### 30.2.2 request_id 的意义：并发调用的"对讲机频道"

真实客户端会并行发很多请求（读车速、读电量、调空调……），而响应可能乱序。
`request_id`（client_id + session_id）就是配对的钥匙：

- **响应必须回显请求的 request_id**（本仿真严格实现，测试用例也断言了这一点）；
- session_id 每发一条请求递增，防止"旧响应"被当成本次请求的响应；
- 测试端要检查 `request_id` 匹配，否则偶发的响应错配会让断言"看起来过了"。

---

## 30.3 序列化：字节序和类型是契约的一部分

SOME/IP 的 payload 是**裸字节**，没有 JSON/XML 那种自描述结构——**类型和字节序
由接口契约（ARXML）约定**。本教学仿真统一用**大端（网络序）**：

| 类型 | 字节数 | Python 编码 | 例子 |
|---|---|---|---|
| int16 | 2 | `struct.pack(">h", v)` | 60 km/h → `00 3C` |
| uint16 | 2 | `struct.pack(">H", v)` | 限速 120 → `00 78` |
| int8 | 1 | `struct.pack(">b", v)` | 温度 22℃ → `16` |
| float32（扩展） | 4 | `struct.pack(">f", v)` | 本仿真服务未用到，但真实项目大量使用 |

**为什么测试必须关注序列化**：

1. **字节序错**：小端机器上如果按小端解析，60 会变成 `0x3C00 = 15360` ——
   断言值"离谱但不崩溃"，非常隐蔽。
2. **类型边界**：int8 只能 -128~127，真实项目里"温度 -40℃"用 int8 合法，
   但"1000℃"会被截断——测试要覆盖边界值。
3. **长度契约**：方法请求的 payload 长度必须与契约一致（如 SetSpeedLimit 必须
   2 字节），长度不对应返回 return_code 0x06（30.7）。

本套件用 `SomeIP Decode Int16 / Uint16 / Int8` 三个关键字**显式解码**，
而不是直接比较 hex 字符串——这样断言失败时能打印出"到底是多少"，
也防止"hex 一样就算过"的弱断言。

---

## 30.4 服务发现（SOME/IP-SD）：先找到服务，再谈调用

客户端不知道服务在哪个 IP、哪个端口、哪个实例，所以 SOME/IP 有一套
**服务发现**（Service Discovery，简称 SD），走 UDP 30490。
SD 报文本身也是一条 SOME/IP 报文，只是固定：service_id = `0xFFFF`、
method_id = `0x8100`、message_type = NOTIFICATION。

### 30.4.1 SD 报文结构

```text
SOME/IP 头（16B：FFFF 8100 …）
+ flags(1B) + reserved(3B)
+ entries_length(4B) + entries（每条 16B）
+ options_length(4B) + options
```

**entry（16 字节）** 字段（Find/Offer）：

```text
type(1B) | index1st(1B) | index2nd(1B) | num_options(1B) |
service_id(2B) | instance_id(2B) | major_version(1B) | TTL(3B) | minor_version(4B)
```

| entry type | 名称 | 谁发 | 含义 |
|---|---|---|---|
| `0x00` | FindService | Client | "谁有这个服务？" |
| `0x01` | OfferService | Server | "我有这个服务，端点在这里" |
| `0x06` | SubscribeEventgroup | Client | "我要订阅这个事件组" |
| `0x07` | SubscribeEventgroupAck | Server | "订阅成功" |

> **StopOffer / StopSubscribe 不用新类型**：Offer 的 TTL 置 0 = StopOffer，
> Subscribe 的 TTL 置 0 = StopSubscribe。SD 用 TTL（秒）表达"有效期"，
> 这让服务/订阅"自动过期"成为协议的一部分——测试也因此必须验证
> "退订后还发不发通知"（套件用例⑩）。

**option（IPv4 端点，12 字节）**：

```text
length(2B)=10 | type(1B)=0x04 | reserved(1B) | IPv4(4B) | reserved(1B) | protocol(1B) | port(2B)
                                                    0x11=UDP / 0x06=TCP
```

### 30.4.2 一次完整的发现 + 订阅流程

```text
Client（测试端）                                  Server（SOME/IP 服务）
    │  ① 服务端启动时主动发 OfferService 公告（TTL>0）      │
    │ ◄────────────────────────────────────────────────────┤
    │  ② FindService（0x0000，service/instance）            │
    │ ────────────────────────────────────────────────────► │
    │  ③ OfferService（0x0001，带 endpoint option）          │
    │ ◄────────────────────────────────────────────────────┤
    │  ④ SubscribeEventgroup（0x0006，TTL + 通知端点 option）│
    │ ────────────────────────────────────────────────────► │
    │  ⑤ SubscribeEventgroupAck（0x0007，TTL）              │
    │ ◄────────────────────────────────────────────────────┤
    │  ⑥ NOTIFICATION（0x02，事件数据，周期/变化触发）        │
    │ ◄────────────────────────────────────────────────────┤
    │  ⑦ StopSubscribe（Subscribe 且 TTL=0）                │
    │ ────────────────────────────────────────────────────► │
```

> 真实项目里 SD 走 **UDP 多播/广播**：服务端周期性地"喊"Offer，客户端广播
> "找"Find，所以一上车就能自动发现。本教学仿真为了确定性和不干扰虚拟 CAN
> 总线，改用**回环单播 + 启动公告一次**（30.9 简化点 1）。

---

## 30.5 Method / Event / Field：SOA 的三种交互

这是本章最重要的一张表——**面试和实际工作都绕不开**：

| 交互方式 | 方向 | 是否需要响应 | SOME/IP 实现 | 测试策略 |
|---|---|---|---|---|
| **Method** | Client→Server | 需要（REQUEST/RESPONSE） | message_type 0x00/0x80/0x81 | 参数边界、返回值、错误码、超时、幂等性 |
| **Method（fire&forget）** | Client→Server | 不需要 | message_type 0x01 | 只验证"执行了"（副作用），不验证响应 |
| **Event** | Server→Client | 单向推送 | message_type 0x02 + 订阅 | 订阅流程、周期/抖动、数值一致性、退订静默 |
| **Field** | 双向 | Getter/Setter + Notifier | 组合 Method + Event | 读-写-读回、notifier 触发条件 |

**用大白话理解**：

- **Method** = "你问我答"：调 `GetSpeed()` 立刻拿到当前车速 → 测**返回值**。
- **Event** = "我主动播报"：订阅 `SpeedChanged` 后，每 500ms 收到一条 →
  测**周期和值**。事件是"订阅才发"的，没订阅的客户端收不到。
- **Field** = "可读可写、变了会通知的属性"：比如"限速值"，既能 Get 也能 Set，
  还会在变化时发 Notification。它本质是 **Method + Event 的组合**，
  真实接口里很常见（车门状态、空调温度、音量……）。

本项目的两个服务包含 Method 和 Event 两类（Field 在真实项目里由
Get/Set 方法 + Notifier 事件组合实现，见 30.11 的扩展练习）：

| 服务 | service_id | Method | Event |
|---|---|---|---|
| VehicleService | `0x1234`（instance 0x0001, major 1） | `0x0001 GetSpeed`（空→int16）<br>`0x0002 SetSpeedLimit`（uint16→回显） | `0x8001 SpeedChanged`（eventgroup 0x0001，周期 500ms，uint16） |
| HvacService | `0x5678`（instance 0x0001, major 1） | `0x0001 SetTemp`（int8→回显） | `0x8001 TempChanged`（eventgroup 0x0001，**温度变化时**推送 int8） |

> **event_id 从 0x8000 开始**是 SOME/IP 的约定：最高位为 1 表示"事件"，
> 0x0001~0x7FFF 才是方法号。所以看到 `0x8001` 就要反应过来"这是事件不是方法"。

---

## 30.6 事件订阅与通知：周期、抖动、静默

事件测试有三个层次，套件里的用例④和⑩正好覆盖：

1. **收得到吗**（订阅流程对不对）：Subscribe → Ack → 收到 NOTIFICATION。
2. **值对不对**（数据一致性）：通知里的车速要和 `GetSpeed()` 返回的一致
   —— 这验证"事件流"和"方法请求"读的是同一个数据源。
3. **稳不稳**（周期/抖动）：每 500ms 一条，实测间隔要在容差内；
   **退订后必须真的静默**（StopSubscribe TTL=0 → 不再推送）。

```robotframework
SomeIP Subscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
${payload}=    SomeIP Wait For Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
SomeIP Notification Value Should Be    ${EXPECTED_SPEED}
${avg}=    SomeIP Event Period Should Be    500    200
SomeIP Unsubscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
SomeIP Should Receive No Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}    1.5
```

**"在途通知"**：真实网络里，退订请求发出去的瞬间，可能已经有 1~2 条通知
在路上。所以 `SomeIP Should Receive No Notification` 有一个**宽限期**
（默认 0.6s）：宽限期内的通知算"在途"忽略，宽限期后还收到才算缺陷。
这是真实测试和"想当然测试"的区别——不做宽限期的用例会因为时序而随机失败。

---

## 30.7 返回码：错误请求必须"有理由地被拒绝"

SOME/IP 的错误响应是 `message_type=0x81 ERROR` + `return_code`。本教学版
按**本项目教学约定**使用下面这组码（对应套件负向用例）：

| return_code | 本仿真含义 | 触发条件 | 套件用例 |
|---|---|---|---|
| `0x00` | OK | 正常处理 | ②③④⑧ |
| `0x01` | unknown service | 请求的服务未定义（如 0x9999） | ⑤ |
| `0x02` | unknown method | 服务在但方法号未定义 | ⑥ |
| `0x03` | version mismatch | interface_version 与服务的 major 不一致 | ⑦ |
| `0x04` | wrong protocol version | 头的 protocol_version ≠ 0x01 | — |
| `0x05` | wrong message type | 服务端收到 RESPONSE/NOTIFICATION 等 | — |
| `0x06` | malformed payload | 方法请求 payload 长度/内容不符合契约 | — |

> ⚠ **重要：真实 SOME/IP 标准的返回码与上表不同！** 面试或真实项目请用标准表：
> `0x01 E_NOT_OK`、`0x02 E_UNKNOWN_SERVICE`、`0x03 E_UNKNOWN_METHOD`、
> `0x07 E_WRONG_PROTOCOL_VERSION`、`0x08 E_WRONG_INTERFACE_VERSION`、
> `0x09 E_MALFORMED_MESSAGE`、`0x0A E_WRONG_MESSAGE_TYPE`。
> 本项目为了让初学者先记住"未知服务/未知方法/版本不匹配"三个最常见的错误，
> 把码压缩成 0x01/0x02/0x03（见 30.9 简化点 8）。**测试断言的是"契约"，
> 换项目要换码表** —— 这正是接口一致性测试的意义。

**负向测试的三板斧**：

```robotframework
SomeIP Call Method    0x9999    0x0001        # 想发什么非法请求都行
SomeIP Response Should Be Error    0x01       # 断言是 ERROR 且 return_code 正确
SomeIP Send Raw Frame  <构造的畸形字节>       # 长度/头都不合法的原始帧
```

注意：**长度不符**（length 字段与实际不符）和"未知方法"不同——前者连
报文边界都不可信，服务端只能**丢弃**（UDP）或**关闭连接**（TCP）。
套件用例⑨验证的是"丢弃后服务不崩溃"：发两条畸形帧，再发一条正常
GetSpeed，能成功才算通过。这是健壮性测试的经典写法。

---

## 30.8 本项目实现：文件、端口与关键字

### 30.8.1 文件地图

| 文件 | 作用 | 关键点 |
|---|---|---|
| `simulators/someip_service.py` | SOME/IP 服务端 + SD 实体（单进程扮演整台车的两个服务） | 16B 头编解码、SD Offer/Find/Subscribe、Method 执行、事件定时器、UDP+TCP 双传输、可选 CAN→SOA 车速桥 |
| `libraries/SomeIpLibrary.py` | RF 关键字库：测试端（Client） | **独立实现**头/SD 编解码（保证互操作验证）、管理服务子进程、订阅/通知/周期测量 |
| `tests/robot/13_someip.robot` | 10 条用例（正+负），打 4 个需求标签 | 服务发现 / 方法调用 / 事件订阅 / 错误处理 |
| `docs/30_SOMEIP与SOA服务测试.md` | 本章 | 你正在读 |

### 30.8.2 架构与数据流

```text
┌────────────────────────────┐   UDP 30490（SD）      ┌─────────────────────────────┐
│ robot 测试进程              │ ─────────────────────► │ someip_service.py（子进程）   │
│  SomeIpLibrary            │   Find/Subscribe       │  ├─ SD 线程                  │
│   ├─ SD socket            │ ◄───────────────────── │  ├─ UDP 服务线程              │
│   ├─ UDP 方法 socket      │   Offer/Ack/通知        │  ├─ TCP 服务线程              │
│   └─ TCP socket（可选）    │                        │  ├─ 事件定时器（500ms）       │
│                           │   UDP/TCP 30501        │  └─ 可选：CAN 0x100 车速桥    │
│                           │ ◄────────────────────► │                              │
└────────────────────────────┘  REQUEST/RESPONSE      └─────────────────────────────┘
                                  NOTIFICATION
```

**为什么测试端要独立实现一遍协议编解码**：如果测试端直接 import 服务端的
编解码函数，"能解析"就成了循环论证（自己测自己）。独立实现后，
两端能互相解开对方的报文，才算真正的**互操作验证**——这也是真实工作中
"用第三方工具（CANoe/Wireshark）验证你的代码"的同一思路。

### 30.8.3 端口约定

| 端口 | 协议 | 用途 | 覆盖方式 |
|---|---|---|---|
| 127.0.0.1:**30490** | UDP | SOME/IP-SD：Offer/Find/Subscribe/Ack + 事件通知 | 环境变量 `SOMEIP_SD_PORT` |
| 127.0.0.1:**30501** | UDP **+** TCP | SOME/IP 服务通信（同一端口同时监听两种传输） | 环境变量 `SOMEIP_SVC_PORT` |
| 233.255.0.1:**30000** | UDP 组播 | 虚拟 CAN（本模块默认不占用；`--use-can` 时才读） | 不覆盖 |

```powershell
# 默认端口跑套件
.\.venv\Scripts\python.exe -m robot --outputdir results/dev_someip tests\robot\13_someip.robot

# 端口被占用/需要并行时（库与仿真进程读同一套环境变量）
$env:SOMEIP_SD_PORT="30491"; $env:SOMEIP_SVC_PORT="30502"
.\.venv\Scripts\python.exe -m robot --outputdir results/dev_someip tests\robot\13_someip.robot
```

服务端日志：`results/someip_service_stdout.log`（收到的每条请求、执行了哪个方法、
拒绝了什么、发了哪些通知都在里面）。

### 30.8.4 关键字表（`libraries/SomeIpLibrary.py`，23 个）

| 关键字 | 作用 | 返回 |
|---|---|---|
| `Start SomeIP Simulation` | 启动服务子进程，等"启动 Offer 公告"就绪 | — |
| `Stop SomeIP Simulation` | 回收子进程 + 释放 socket（幂等） | — |
| `SomeIP Simulation Should Be Running` | 断言仿真进程在线 | — |
| `SomeIP Send SD Find` | 发 FindService（默认查全部） | — |
| `SomeIP Read SD Offer` | 读下一条 SD Offer，返回可读摘要 | 摘要字符串 |
| `SomeIP Service Should Be Offered` | 发 Find 并断言能发现指定服务（含端点校验） | Offer 摘要 |
| `SomeIP Startup Offer Should Contain` | 断言启动公告里包含指定服务 | — |
| `SomeIP Call Method` | 调用方法，返回响应 payload hex | hex |
| `SomeIP Call Should Succeed` | 断言 RESPONSE + return_code 0x00 | — |
| `SomeIP Return Code Should Be` | 断言 return_code | — |
| `SomeIP Response Should Be Error` | 断言 ERROR + 指定 return_code | — |
| `SomeIP Subscribe Event` | SD 订阅事件组并等 Ack | — |
| `SomeIP Unsubscribe Event` | StopSubscribe（TTL=0） | — |
| `SomeIP Wait For Notification` | 等指定事件通知 | payload hex |
| `SomeIP Notification Value Should Be` | 解码通知值并断言（支持容差） | — |
| `SomeIP Event Period Should Be` | 采样 N 段间隔，断言平均周期与抖动 | 平均周期 ms |
| `SomeIP Should Receive No Notification` | 退订后静默断言（含在途宽限期） | — |
| `SomeIP Tcp Mode` | 切换方法调用的传输（True=TCP / False=UDP） | — |
| `SomeIP Send Raw Frame` | 发原始字节（畸形帧用例） | — |
| `SomeIP Cleanup` | 用例隔离：退订/切回 UDP/清空缓冲 | — |
| `SomeIP Decode Int16` / `SomeIP Decode Uint16` / `SomeIP Decode Int8` | 大端解码 payload（显式断言用） | int |

### 30.8.5 实现里的三个工程细节

1. **就绪判定是"功能可用"**：`Start SomeIP Simulation` 不是看到进程活着就返回，
   而是**必须收到服务端启动 OfferService 公告**（且包含两个服务）才认为就绪；
   公告丢了会补发 Find 重试。端口被占用时子进程直接报错退出，库会把仿真日志
   尾部带进异常信息里。
2. **UDP 和 TCP 的畸形帧处置不同**：UDP 是数据报，长度不对**丢弃**即可，
   服务继续跑；TCP 是字节流，length 字段不可信后无法再切分报文，必须**关闭连接**。
   这个区别和 24 章 DoIP 完全一致——**"TCP 没有报文边界"是车载以太网测试的
   通用考点**。
3. **事件周期用单调时钟驱动**：服务端用 `time.monotonic()` 对齐 500ms 节拍，
   测试端也用单调时钟测间隔（不用墙上时间，避免系统时间跳变导致假失败）。

---

## 30.9 教学简化点：和真实 SOME/IP 差在哪

面试被追问时，能主动说出"我的仿真简化了什么"是加分项：

| # | 教学版做法 | 真实 SOME/IP / 工程实践 | 影响 |
|---|---|---|---|
| 1 | SD 走 **UDP 回环单播**，启动公告只发一次 | SD 走 UDP 多播/广播，服务端**周期公告**、客户端周期 Find | 不测多播/网络风暴/发现超时 |
| 2 | 服务端**单进程**扮演两个服务 | 每个 ECU 一个协议栈，多节点跨交换机 | 不测跨节点路由/网络分区 |
| 3 | SD option 简化：订阅直接取第一个 IPv4 端点 | 按 entry 的 option index/count 精确引用；支持多 option | 复杂 SD 报文解析未覆盖 |
| 4 | 返回码压缩为 0x01/0x02/0x03 | 标准 0x02/0x03/0x08 等（30.7） | **换项目必须换码表** |
| 5 | 事件固定走 UDP、周期固定 500ms | UDP/TCP 可配；周期由配置/模型决定 | 不测 TCP 事件/大 payload |
| 6 | 车速默认独立生成 60 km/h | 数据源来自传感器/其他 ECU（网关桥接） | `--use-can` 展示了 CAN→SOA 桥接思路 |
| 7 | 无 TLS/IPsec、无 E2E 保护、无服务版本兼容协商 | SOME/IP over TLS、AUTOSAR E2E Profile、版本兼容策略 | 安全和功能安全测试见 27/32 章 |

> **扩展练习**：给 HvacService 加一个 Field（温度）：Get 方法 + Set 方法 +
> 变化时 Notifier，然后用 13 套件的写法补 3 条用例——你就能覆盖真实项目里
> 最常见的 Field 测试形态。

---

## 30.10 逐条讲 `tests/robot/13_someip.robot`

### 30.10.1 用例总表

| # | 用例 | 类型 | 验证点 | 需求标签 |
|---|---|---|---|---|
| ① | 服务发现应收到启动公告并能通过 Find 发现两个服务 | 正向 | Offer 公告、Find→Offer、service/instance/端点 | REQ-SOMEIP-001 |
| ② | GetSpeed 方法调用应成功并解码车速 | 正向 | REQUEST/RESPONSE、int16 大端、两个服务都可调用 | REQ-SOMEIP-002 |
| ③ | SetSpeedLimit 应回显限速值且不改变当前车速 | 正向 | uint16 echo、"设定值 ≠ 实际值"语义 | REQ-SOMEIP-002 |
| ④ | 订阅 SpeedChanged 后应收到通知且数值与 GetSpeed 一致 | 正向 | 订阅/Ack/通知、事件流与方法响应一致性 | REQ-SOMEIP-003 |
| ⑤ | 未知 service_id 应返回错误码 0x01 | **负向** | ERROR + rc=0x01 | REQ-SOMEIP-004 |
| ⑥ | 未知 method_id 应返回错误码 0x02 | **负向** | ERROR + rc=0x02 | REQ-SOMEIP-004 |
| ⑦ | interface_version 不匹配应返回错误码 0x03 | **负向** | ERROR + rc=0x03 | REQ-SOMEIP-004 |
| ⑧ | UDP 与 TCP 两种传输均应可用 | 正向 | 双传输一致性、切换不残留状态 | REQ-SOMEIP-002 |
| ⑨ | 原始帧长度不符应被拒绝且服务不崩溃 | **负向** | 畸形帧丢弃后服务仍可用 | REQ-SOMEIP-004 |
| ⑩ | 事件周期应稳定且退订后静默 | 正向 | 500ms±200ms、StopSubscribe 生效 | REQ-SOMEIP-003 |

> 10 条用例打 4 个需求标签：REQ-SOMEIP-001×1、002×3、003×2、004×4。
> 每个需求域都有"正向 + 负向"覆盖，避免"只证明能跑通，没证明会被拒绝"。

### 30.10.2 三条值得逐行读的用例

**用例④：事件流和方法响应必须"同源一致"**

```robotframework
SomeIP Subscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
${resp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
SomeIP Call Should Succeed
${method_speed}=    SomeIP Decode Int16    ${resp}
${payload}=    SomeIP Wait For Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
${event_speed}=    SomeIP Decode Uint16    ${payload}
Should Be Equal As Integers    ${event_speed}    ${method_speed}
```

为什么这条用例有价值：真实缺陷里常见"事件用了缓存旧值、方法读了最新传感器"，
两边都"看起来正常"，但 SOA 客户端会收到**互相矛盾的数据**。
只有把两条通路放在一起断言，才能发现这类数据源不一致。

**用例⑧：UDP/TCP 双传输——别只测一种**

```robotframework
${udp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
SomeIP Tcp Mode
${tcp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
Should Be Equal    ${tcp}    ${udp}
SomeIP Tcp Mode    ${False}
${again}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
Should Be Equal    ${again}    ${udp}
```

真实项目里同一个服务常同时配置 UDP（快、可多播）和 TCP（可靠、大报文）。
如果只测 UDP，TCP 路径的分帧 bug（粘包/半包）永远发现不了。
注意 TCP 是**字节流**：一次 `recv` 可能拿到 1.5 条报文，必须按 length 字段分帧
——本库的 `_tcp_recv_message` 就是标准写法。

**用例⑩：周期 + 抖动 + 静默，一条用例三种断言**

```robotframework
SomeIP Subscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
${avg}=    SomeIP Event Period Should Be    500    200
Should Be True    400 <= ${avg} <= 600
SomeIP Unsubscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
SomeIP Should Receive No Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}    1.5
```

周期测量用 5 段间隔（需要 6 条通知，约 3 秒）；静默断言留了宽限期，
避免"在途通知"造成随机失败。**只订阅不测退订**是事件测试最常见的漏洞：
服务端忽略了 StopSubscribe 的话，事件洪泛会拖垮总线负载，
而"能收到通知"的用例永远是绿的。

### 30.10.3 实跑结果

```text
$ .\.venv\Scripts\python.exe -m robot --outputdir results/dev_someip tests\robot\13_someip.robot
==============================================================================
13 Someip :: 套件 13：SOME/IP 与 SOA 服务测试
==============================================================================
服务发现应收到启动公告并能通过 Find 发现两个服务                        | PASS |
GetSpeed 方法调用应成功并解码车速                                      | PASS |
SetSpeedLimit 应回显限速值且不改变当前车速                              | PASS |
订阅 SpeedChanged 后应收到通知且数值与 GetSpeed 一致                    | PASS |
未知 service_id 应返回错误码 0x01                                      | PASS |
未知 method_id 应返回错误码 0x02                                       | PASS |
interface_version 不匹配应返回错误码 0x03                              | PASS |
UDP 与 TCP 两种传输均应可用                                            | PASS |
原始帧长度不符应被拒绝且服务不崩溃                                      | PASS |
事件周期应稳定且退订后静默                                             | PASS |
------------------------------------------------------------------------------
10 tests, 10 passed, 0 failed
```

> 套件不依赖 ECU/虚拟 CAN：仿真服务自己生成 60 km/h 车速（`--speed` 可改），
> 所以可以和其他套件并行开发时独立运行（仍建议用 `.audit/robot_run.lock` 串行跑）。

---

## 30.11 真实项目里 SOME/IP 测试怎么做

| 场景 | 真实工具/做法 | 本项目对应物 |
|---|---|---|
| 服务端仿真 | `vsomeip`（开源 SOME/IP 栈，BMW/VW 参与）、CANoe SOME/IP 仿真节点 | `simulators/someip_service.py` |
| 抓包分析 | Wireshark 的 SOME/IP + SOME/IP-SD 解析器、CANoe Trace | 各服务的日志 + 手写解析代码 |
| 接口契约 | ARXML（AUTOSAR 服务描述）：service/method/event ID、版本、序列化 | `SERVICE_TABLE` / `SERVICE_DEFS` 字典 |
| 服务 ID 分配 | OEM 统一分配表（避免冲突）；instance ID 区分同服务多实例 | 项目内固定的 0x1234/0x5678 |
| 集成测试 | 客户端 SDK 真实调用 + 服务端仿真 / 两车互连台架 | RF 测试端（独立编解码） |
| 常见故障 | ① SD 找不到服务（多播配置/交换机 IGMP）② interface_version 不匹配 ③ instance ID 错 ④ 事件周期抖动 ⑤ TCP 粘包 | 本套件⑤⑥⑦⑩⑧正好对应 |

**面试高频追问**："你们怎么测 SOME/IP 服务？" 好的回答结构：
1. 先看 **接口契约（ARXML）**，确认 service/method/event ID 和序列化；
2. **服务发现**测试：能否 Find/Offer，instance 和 endpoint 对不对；
3. **Method** 测试：正常值、边界值、错误码、超时；
4. **Event** 测试：订阅/Ack、周期抖动、值一致性、退订静默；
5. **传输**测试：UDP/TCP 两条路径都要覆盖；
6. 最后落到**失败可诊断**：抓包 + 日志 + 断言信息能定位到"哪一层"。

---

## 30.12 本章自检

1. 画出 SOME/IP 16 字节头的字段图，并说出 `length` 字段到底算多长。
2. Method、Event、Field 三者的区别？各举一个车上的例子。
3. 服务发现（SD）的完整流程？Offer/Find/Subscribe/Ack 分别是哪个 type？
4. 为什么响应必须回显 request_id？测试端不校验会有什么风险？
5. 事件测试只看"能收到通知"够不够？还应该验证什么？
6. 未知服务/未知方法/版本不匹配，本教学版返回什么码？真实 SOME/IP 标准返回什么码？
7. TCP 传输 SOME/IP 时，"一次 recv 就是一条报文"这句话为什么是错的？
8. 畸形帧（length 不符）在 UDP 和 TCP 下处置为什么不同？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | message_id(4=service2+method2) + length(4) + request_id(4=client2+session2) + protocol_version(1) + interface_version(1) + message_type(1) + return_code(1)。**length = 8 + payload 长度**，从 request_id 开始算，不含前 8 字节 |
| 2 | Method=一问一答（GetSpeed 读车速）；Event=订阅推送（SpeedChanged 每 500ms）；Field=可读可写可通知的属性（空调温度：Get/Set + TempChanged）。Field 本质是 Method+Event 组合 |
| 3 | 服务端启动发 OfferService(0x01) → 客户端 FindService(0x00) → 服务端回 OfferService(0x01) → 客户端 SubscribeEventgroup(0x06) → 服务端 SubscribeEventgroupAck(0x07) → 服务端发 NOTIFICATION(0x02)；Stop 用 TTL=0 |
| 4 | 并发调用时响应可能乱序，request_id 是请求/响应的配对钥匙；不校验可能把旧响应/别的请求的响应当成这次的，造成"假通过" |
| 5 | 不够。还要验证：订阅/Ack 流程、通知数值与数据源（方法/信号）一致、周期与抖动、**退订后静默**、多订阅者、事件丢失/重复 |
| 6 | 教学版：0x01 未知服务、0x02 未知方法、0x03 版本不匹配；标准：0x02 E_UNKNOWN_SERVICE、0x03 E_UNKNOWN_METHOD、0x08 E_WRONG_INTERFACE_VERSION（协议版本错是 0x07） |
| 7 | TCP 是字节流没有报文边界，一次 recv 可能包含半条/多条报文，必须按 length 字段切包（先读 16 字节头→解析 length→读 length-8 字节 payload） |
| 8 | UDP 是数据报：长度不可信的报文直接丢弃，服务继续；TCP 是字节流：长度字段不可信后无法再切分后续报文，只能关闭连接让对端重连 |

---

**上一章：[29 · CAN FD 与高速总线](29_CANFD与高速总线.md)** ｜ **下一章：[31 · 功能寻址与 OBD-II 诊断](31_功能寻址与OBDII诊断.md)**

**相关章节**：[24 车载以太网与 DoIP 诊断](24_车载以太网与DoIP诊断.md)（同为车载以太网应用层协议）、
[22 车载自动化测试全景](22_车载自动化测试全景与能力地图.md)（SOA 能力在能力矩阵中的位置）、
[27 信息安全测试入门](27_信息安全测试入门.md)（服务暴露的攻击面）、
[32 功能安全机制验证](32_功能安全机制验证.md)（E2E 保护承载在服务报文之上）
