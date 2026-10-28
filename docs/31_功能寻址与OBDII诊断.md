# 31 · 功能寻址与 OBD-II 诊断：0x7DF / 0x2F / 0x23·0x3D / OBD

> 📖 **零基础读法建议**
> - 🟢 **必读**：31.1（功能寻址 vs 物理寻址）、31.2（OBD-II 与 UDS 的关系）、31.5（本项目实现）——这三节回答"为什么诊断寻址不只 0x7E0、OBD 到底是什么"。
> - 🟡 **了解**：31.3（0x2F 执行器控制）、31.4（0x23/0x3D 内存读写）——想搞懂产线/EOL 和 Bootloader 场景时精读。
> - ⚪ **可跳过**：31.6 逐条讲用例、31.7 真实项目工具（有基础再看）。
>
> **这一章补的是"真实项目里一定遇到、但基础教材很少讲"的三块诊断能力**：
> ① 功能寻址 0x7DF（OBD 扫描仪、全局广播）；② 执行器控制与内存读写（产线/工程模式）；
> ③ OBD-II（法规强制的排放诊断，和厂家 UDS 是两套并行体系）。
> 全部在 [14 套件](../tests/robot/14_functional_addressing_obd.robot) 里有可运行的自动化验证。

---

## 31.0 为什么诊断还有"进阶"？

前面 [03 章](03_UDS协议详解.md)、[07 章](07_CAN仿真测试实战.md)、[08 章](08_UDS诊断测试实战.md) 讲的是**一条诊断仪对一台 ECU 的点对点诊断**：
`0x7E0` 发请求 → `0x7E8` 收响应，做会话/读 DID/读 DTC/刷写。

但真实台架和产线上还会遇到这些：

```
场景 A：OBD 年检扫描仪插上 OBD 口，只说"给我转速/水温/故障码/VIN"
        → 它不知道也不关心你这台车的厂家 UDS 服务，走的是 0x7DF 广播 + OBD-II 标准
场景 B：产线终检给仪表发"点亮所有警告灯 3 秒"（EOL 点灯测试）
        → 用 0x2F 强制控制执行器，不是写数据
场景 C：工程模式/产线要按地址读序列号、写配置参数，或 Bootloader 刷写
        → 用 0x23/0x3D 按内存地址读写
```

**这三块能力 = 本章三节**，对应需求 `REQ-DIAG-017` ~ `REQ-DIAG-022`。

---

## 31.1 功能寻址 vs 物理寻址：0x7DF 是"大喇叭"

### 31.1.1 两种寻址方式

| 维度 | 物理寻址（Physical） | 功能寻址（Functional） |
|---|---|---|
| 请求 CAN ID | 每台 ECU 一个：`0x7E0`、`0x7E1`、`0x7E2`… | **所有 ECU 共用一个**：`0x7DF` |
| 语义 | 点对点："我说给 ECU #1 听" | 广播："所有 ECU 都听着" |
| 谁响应 | 只有被寻址的 ECU | **所有收到且支持的 ECU** |
| 响应 ID | 该 ECU 的物理响应 ID（`0x7E8`） | 仍是各 ECU 自己的物理响应 ID（`0x7E8`、`0x7E9`…） |
| 典型用途 | 读数据、刷写、所有"要结果"的诊断 | OBD-II、全局 TesterPresent、唤醒/广播类请求 |

> 📌 一句话记忆：**"请求可以广播，响应必须实名"**。
> 因为如果所有 ECU 都用同一个 ID 回响应，总线上会撞车 —— 所以响应永远回到各 ECU 的物理响应 ID。

### 31.1.2 时序（本项目的单 ECU 场景）

```
诊断仪 (Tester)                                   ECU（物理响应 ID 0x7E8）
   │  3E 00 → 发到 0x7DF（广播）                    │
   │ ──────────────────────────────────────────────>│  ECU 监听 0x7E0 和 0x7DF
   │                                                │  处理请求
   │  7E 00 ← 从 0x7E8（物理响应 ID）                │
   │ <──────────────────────────────────────────────│
```

多 ECU 时：

```
            0x7DF  [3E 00]   ← 诊断仪广播
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
    ECU1     ECU2     ECU3        （三台都处理）
      │       │        │
      ▼       ▼        ▼
   0x7E8   0x7E9    0x7EA          （各自用物理响应 ID 回答）
```

### 31.1.3 功能寻址的几个真实用途

1. **OBD-II 扫描仪**：法规要求"插上就能读"，扫描仪固定发 `0x7DF`。外部章 31.2 详述。
2. **全局 TesterPresent（0x3E）**：一句话让全车 ECU 的 S3 定时器都续命（台架/OTA 常用）。
3. **全局进会话 / 通信控制**：整车级测试台架一次性让所有 ECU 进入扩展会话。
4. **唤醒**：某些架构用功能寻址报文触发网络唤醒。

### 31.1.4 实现要点（容易踩的坑）

- ECU 的接收过滤要**同时接受 0x7E0 与 0x7DF**（本项目 `_handle_bus_frame` 就是这么改的）。
- **响应永远发 0x7E8**：不能"从哪来回哪去"（真实 ECU 也不会把响应发到 0x7DF）。
- 功能寻址下的**多帧响应**要注意：流控帧（FC）仍按物理方向（本项目 Tester 发 FC 到 `0x7E0`）。
- 测试断言要能区分"响应存在"和"响应来自正确 ID"：本项目的 `Response Should Come From 0x7E8` 就是干这个的。

---

## 31.2 OBD-II 与 UDS 的关系：法规 vs 厂家

### 31.2.1 两套并行的诊断体系

| 维度 | **OBD-II**（法规诊断） | **UDS**（厂家诊断） |
|---|---|---|
| 标准 | ISO 15031 / SAE J1979（排放相关） | ISO 14229（UDS） |
| 目的 | 排放合规、年检、通用扫描仪可读 | 研发/产线/售后全功能诊断 |
| 谁要求 | **法规强制**（美国 OBD-II、欧洲 EOBD、中国国六 OBD） | 车厂/供应商内部规范 |
| 寻址 | 固定功能寻址 `0x7DF` | 物理寻址为主（`0x7E0`）+ 功能寻址 |
| 服务号 | mode `0x01`/`0x03`/`0x04`/`0x09`… | `0x10`/`0x22`/`0x19`/`0x14`/`0x2E`… |
| 权限 | **不需要**厂商会话/安全解锁 | 会话 + 安全访问 + 编程会话 |
| 数据定义 | PID（Parameter ID）固定表 | DID（Data Identifier）厂家自定义 |
| 故障码 | 2 字节 DTC（`P0562` → `05 62`） | 3 字节 DTC（`05 62 00`） |
| 典型工具 | ELM327 / 元征 / Autel 诊断仪、Torque App | CANoe/CANape + ODX、厂家诊断仪 |

> 💡 **面试高频**："OBD 和 UDS 什么关系？"——答案：**OBD 是法规子集、UDS 是厂家超集**。
> 同一台 ECU 通常两套都支持：外部扫描仪走 OBD 读排放数据，4S 店/产线走 UDS 做全功能诊断。
> 两套可以共用同一物理总线、不同的寻址/服务定义。

### 31.2.2 本项目实现的 4 个 OBD mode

| Mode | 服务 | 请求 | 正响应 | 本项目实现 |
|---|---|---|---|---|
| `0x01` | 当前数据 | `01 <PID>` | `41 <PID> <A> <B>` | PID `0x0C` 转速 / `0x0D` 车速 / `0x05` 水温 |
| `0x03` | 读已确认 DTC | `03` | `43 <数量> <DTC 2B>…` | 从 DTC 状态机取 confirmed 记录 |
| `0x04` | 清 DTC | `04` | `44` | 清空全部 DTC |
| `0x09` | 车辆信息 | `09 02` | `49 02 01 <VIN 17B>` | 返回 VIN（20 字节 → ISO-TP 多帧） |

### 31.2.3 PID 与换算公式（必背）

```
PID 0x0C 发动机转速（2 字节 A、B）：
    rpm = ((A * 256) + B) / 4          分辨率 0.25 rpm
PID 0x0D 车速（1 字节 A，本项目按协议要求回 2 字节）：
    km/h = A                            分辨率 1 km/h
PID 0x05 冷却液温度（1 字节 A）：
    ℃ = A - 40                          分辨率 1 ℃（-40~215℃）
```

例：本项目车速 60 km/h、模型 `rpm = speed × 80 = 4800`、水温 90℃：

```
PID 0C → rpm=4800 → raw=4800*4=19200=0x4B00 → 响应 41 0C 4B 00
PID 0D → 响应 41 0D 3C 00          （0x3C = 60）
PID 05 → 响应 41 05 82 00          （0x82 = 130 → 130-40 = 90℃）
```

### 31.2.4 OBD 的 2 字节 DTC 编码

OBD 故障码只有 **2 字节**（UDS 是 3 字节，多一个"故障类型/FTB"字节），前 2 字节与 UDS 完全相同：

```
字节1：bit7-6 首字母（00=P 01=C 10=B 11=U）
       bit5-4 数字1（0~3）      bit3-0 数字2（0~9，BCD）
字节2：bit7-4 数字3（0~9）      bit3-0 数字4（0~9）

P0562 → 字节1=0x05、字节2=0x62      → OBD 响应：43 01 05 62
```

> 对比 [04 章](04_DTC详解.md)：UDS 里 P0562 是 `05 62 00`，OBD 少了最后一个 `00`。

### 31.2.5 扫描仪（OBD 工具）的工作流

```
插 OBD 口 → 发 0x7DF 请求 → 识别支持的 PID（mode 01 PID 00 位图）
   → 周期读 mode 01 实时数据 → mode 03 读故障码 → mode 04 清码（维修后）
   → mode 09 02 读 VIN（确认车型）
```

本项目顺序同理：14 套件的 ⑨⑩ 条就是一个"最小 OBD 扫描仪"。

### 31.2.6 实现注意点

- OBD 服务**不走厂家会话权限**：OBD 扫描仪不会发 `10 03`，所以 ECU 必须在**默认会话**就能响应 OBD 服务（本项目 `_svc_obd` 分支绕过 `_check_session_for_service`）。
- OBD 只认**功能寻址**：物理寻址发 `0x01` 在真实 ECU 上通常回 NRC 0x11/0x7F（本项目按此实现）。
- 响应长度受 ISO-TP 限制：VIN 20 字节必须走多帧（FF/CF + FC），测试端要正确回流控帧。

---

## 31.3 0x2F InputOutputControlByIdentifier：强制控制执行器

### 31.3.1 这是什么

`0x2F` = "不写数据，直接**强制一个执行器动作**"。典型对象：警告灯、仪表指针、继电器、风扇、车窗电机。
和 `0x2E` 写 DID 的区别：

| | 0x2E WriteDataByIdentifier | 0x2F InputOutputControlByIdentifier |
|---|---|---|
| 语义 | 改"数据/配置" | 改"执行器输出" |
| 例子 | 写应用版本、写 VIN | 强制点亮某个警告灯、强制指针到 120 |
| 影响面 | 持久（存 EEPROM/Flash） | 临时（短时控制），交还后恢复 ECU 正常控制 |
| 典型场景 | 产线写配置 | 产线 EOL 点灯、售后执行器诊断 |

### 31.3.2 请求/响应格式（本项目约定）

```
请求：2F <DID 2B> <control 1B> <mask 2B>
  control: 00 returnControlToECU   —— 交还控制权（收尾/售后恢复）
           01 resetToDefault       —— 恢复默认输出
           02 shortTermAdjustment  —— 短时强制（本次控制，不持久）
           03 longTermAdjustment   —— 长时强制（教学版与短时同效，简化）
  mask   : 12 个警告灯位图（位序与 DBC TelltaleStatus 一致）

正响应：6F <DID 2B> <controlState 2B>      （controlState = 控制后的实际灯掩码）
负响应：7F 2F <NRC>
```

### 31.3.3 本项目的执行器 DID 0x3001（12 个警告灯掩码）

| bit | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 灯 | MilWarning | OilPressureLow | CoolantTempHigh | BatteryLow | AbsFault | AirbagFault | SeatbeltWarning | TurnLeft | TurnRight | HighBeam | LowBeam | ParkBrake |

例：点亮 `CoolantTempHigh`（bit2）→ mask = `0x0004`：

```
请求：2F 30 01 02 00 04
响应：6F 30 01 00 04
效果：ECU 把 0x300 TelltaleStatus 的 bit2 强制置 1 → headless 仪表显示该灯亮
```

### 31.3.4 产线/台架为什么需要它

- **产线 EOL（End of Line）点灯测试**：整车下线不拆仪表，一条 `0x2F` 验证每个灯的驱动通路/线束/焊点（比人工看快，且可记录）。
- **售后执行器诊断**：怀疑某个灯/电机不工作，强制动作一次看是否符合预期。
- **台架标定**：强制仪表指针/水温表到指定值，验证仪表的显示映射，而不需要真的把水加热。
- **测试自动化**：想验证"灯亮时 HMI 的显示"时，用 0x2F 比"注入真实故障"更快更可控。

### 31.3.5 权限与安全（重要）

0x2F 能直接驱动执行器，**属于高危服务**，必须限制：

| 会话 | 未解锁 | 已解锁 |
|---|---|---|
| 默认 | ❌ `0x7F`（会话不支持） | ❌ `0x7F` |
| 扩展 | ❌ `0x33`（securityAccessDenied） | ✅ 允许 |
| 编程 | ❌ `0x33` | ✅ 允许 |

> 安全设计原则：**"能造成物理动作的服务，权限永远不放松"**。
> 14 套件第 ⑤ 条就专门验证这两层拒绝（0x7F / 0x33）——负向用例和正向同样重要。

---

## 31.4 0x23 / 0x3D：按地址读写内存

### 31.4.1 是什么

- `0x23 ReadMemoryByAddress`：按"内存地址 + 长度"读原始字节。
- `0x3D WriteMemoryByAddress`：按"内存地址 + 长度 + 数据"写原始字节。

典型用途：
- **工程模式**：读标定区、读内部变量、读序列号。
- **产线**：写配置参数（还是推荐 0x2E 写结构化 DID，0x3D 属于更底层的手段）。
- **Bootloader/刷写**：Flash 驱动最终就是用"地址+数据"写内存（0x34/0x36 在应用层，底层落到这里）。

### 31.4.2 ALFID 编码（addressAndLengthFormatIdentifier）

请求里的 `ALFID` 是一个字节：

```
高 4 位 = memorySize 用几个字节表示（1~4）
低 4 位 = memoryAddress 用几个字节表示（1~4）

例：ALFID=0x14 → 地址 4 字节、长度 1 字节（最常用：32 位地址 + 最多 255 字节）
```

完整请求：

```
23 <ALFID> <address N B> <size M B>            → 正响应 63 + 数据
3D <ALFID> <address N B> <size M B> <data…>    → 正响应 7D
```

例（本项目）：

```
读 0x08000000 起 4 字节：23 14 08 00 00 00 04        → 63 4C 53 56 41（"LSVA"）
写 0x08000010 4 字节 DEADBEEF：
    3D 14 08 00 00 10 04 DE AD BE EF                → 7D （11 字节请求，走 ISO-TP 多帧）
```

### 31.4.3 本项目的内存映射教学区

```
0x08000000 ┌───────────────────────────────┐
           │ VIN 17 字节（与 DID F190 一致）│  ← 只读语义（本教学实现可写）
0x08000011 ├───────────────────────────────┤
           │ 可读写 RAM（教学区，共 1KB）   │
0x080003FF └───────────────────────────────┘
  越界（<0x08000000 或 ≥0x08000400）→ NRC 0x31 requestOutOfRange
```

为什么这样设计：真实 ECU 的内存是 Flash/EEPROM/RAM 多区域，直接暴露地址是**极度危险**的；
教学版用一个 1KB RAM 模拟"按地址读写"的协议行为，同时用"越界 → 0x31"把边界校验也测了。

### 31.4.4 风险（面试常问）

| 风险 | 说明 | 防护 |
|---|---|---|
| 读敏感数据 | 内存里可能有密钥/证书/标定 | 只允许特定区域、需安全解锁、审计日志 |
| 篡改标定 | 改喷油/点火参数影响排放/安全 | 安全访问 + 刷写签名校验 + 写保护 |
| 绕过保护 | 直接改内存绕过故障检测逻辑 | 分层权限 + 代码完整性校验（Secure Boot） |
| 越界踩内存 | 地址/长度不校验导致相邻区域被改 | 严格范围校验（本项目 0x31）+ MPU |

真实项目里 **0x23/0x3D 通常只在编程会话 + 安全解锁 +（必要时）产线特殊解锁** 下开放。
就安全测试而言，它和 [27 章](27_信息安全测试入门.md) 的权限矩阵是同一条设计原则：
"越底层的能力，权限越严"。

---

## 31.5 本项目实现

### 31.5.1 ECU 侧改动（`simulators/ecu_sim.py`）

| 改动 | 位置 | 说明 |
|---|---|---|
| 功能寻址 ID | `ID_UDS_FUNCTIONAL_REQUEST = 0x7DF` | 监听 0x7E0 + 0x7DF；响应统一 0x7E8 |
| 监听路由 | `_handle_bus_frame` / `_handle_request_safely` | 多帧请求记住"功能/物理"来源；OBD 只在功能寻址生效 |
| 0x2F | `_svc_io_control` / `_apply_io_telltale_mask` / `_current_telltale_mask` | 12 灯掩码 → `telltale_override` → 0x300 报文 |
| 0x23/0x3D | `_parse_alfid` / `_svc_read_memory` / `_svc_write_memory` | ALFID 解析 + 1KB 内存区 + 越界 0x31 |
| OBD-II | `_svc_obd` / `_obd_current_data` | mode 01/03/04/09，绕过会话权限 |
| 权限矩阵 | `_check_session_for_service` | 0x2F/0x23/0x3D 需"扩展会话 + 安全解锁" |
| 内存区初始化 | `__init__` | `bytearray(0x400)`，前 17 字节 = VIN |

> 注意：ECU 侧**没有改既有的 14 个服务**，只新增分支；02/03/06/scenarios-03 回归全绿证明了兼容性。

### 31.5.2 ObdLibrary 关键字一览（`libraries/ObdLibrary.py`，SUITE 作用域）

| 关键字 | 作用 |
|---|---|
| `Open OBD Channel` / `Close OBD Channel` | 打开/关闭本库的虚拟总线（复用 `VirtualCanBus`） |
| `Send Functional UDS Request` | 发到 0x7DF（功能寻址），返回完整响应 hex（自动 ISO-TP） |
| `Send Physical UDS Request` | 发到 0x7E0（物理寻址），返回完整响应 hex |
| `Response Should Be Positive` | 断言正响应 |
| `Response Should Be Negative With NRC` | 断言指定 NRC 负响应 |
| `Response Should Come From` | 断言响应来自指定 CAN ID（功能寻址用例验证 0x7E8） |
| `Last Response Payload Hex` | 取响应 payload（去掉 SID 后）的 hex |
| `IOControl Send` | 0x2F 执行器控制（DID + control + mask） |
| `Read Memory By Address` | 0x23 读内存（自动 ALFID，返回数据 hex） |
| `Write Memory By Address` | 0x3D 写内存（>7 字节自动多帧） |
| `OBD Read PID` | mode 01 读任意 PID，返回 A,B 数据 hex |
| `OBD Read RPM / Speed / Coolant` | mode 01 解码后的转速/车速/水温 |
| `OBD Decode RPM / Speed / Coolant` | 纯换算函数（教学：公式与代码一一对应） |
| `OBD Read DTCs` | mode 03 读已确认 DTC（2 字节解码成 `Pxxxx`） |
| `OBD Clear DTCs` | mode 04 清码 |
| `OBD Read VIN` | mode 09 02 读 VIN（多帧） |
| `OBD Response Should Be / Start With` | 对完整响应做精确/前缀断言 |

> 技术细节：本库自带一个**最小 ISO-TP 收发器**（`_send_and_wait`），因为现有 `DiagnosticTester`
> 只支持物理寻址 0x7E0。发送多帧请求时等 ECU 的 FC；接收多帧响应时主动回 FC。
> 另：虚拟总线是 UDP 组播、没有真实 CAN 的帧重传，**超时会做一次幂等重试**
> （真实 ECU 的 TP 层用 N_Cr/N_As 超时重发，原理等价）。

### 31.5.3 14 套件：10 条用例（需求映射）

| # | 用例 | 标签 | 断言要点 |
|---|---|---|---|
| ① | 功能寻址 TesterPresent 应有响应且响应来自 0x7E8 | REQ-DIAG-017 | 响应存在 + 来源 ID = 0x7E8 + `7E 00` |
| ② | 功能寻址读 DID 应正常（多帧响应自动重组） | REQ-DIAG-017 | `62 F1 87` + 15 字节零件号（ISO-TP 多帧） |
| ③ | 0x2F 交还执行器控制权应成功 | REQ-DIAG-018 | 正响应 `6F 30 01 …` |
| ④ | 0x2F 短时控制应点亮指定警告灯 | REQ-DIAG-018 | 响应 `6F 30 01 00 04` + 仪表 `CoolantTempHigh` 亮；交还后灯灭 |
| ⑤ | 0x2F 在默认会话应被拒绝 | REQ-DIAG-018 | 默认会话 → 0x7F；扩展未解锁 → 0x33 |
| ⑥ | 0x23 按地址读内存应成功 | REQ-DIAG-019 | `63 4C 53 56 41`（"LSVA"）+ 17 字节 VIN |
| ⑦ | 0x3D 写入后 0x23 应能读回一致 | REQ-DIAG-019 | 写请求走多帧 + `7D` + 读回 `DE AD BE EF` |
| ⑧ | 0x23 地址越界应回 NRC 0x31 | REQ-DIAG-019 | 上界外/下界外都 0x31 |
| ⑨ | OBD mode01 实时数据换算应与 ECU 信号一致 | REQ-DIAG-020 | PID 0C/0D/05 与总线信号误差在容差内 |
| ⑩ | OBD mode03/04/09 应能读 DTC、清 DTC、读 VIN | REQ-DIAG-021/022 | 注入 P0562 → 读到 → 清码为空 → VIN 正确 |

### 31.5.4 怎么跑

```bash
# 只跑本套件（约 25~30 秒）
python -m robot --outputdir results/dev_diag_14 tests/robot/14_functional_addressing_obd.robot

# 回归旧套件，确认新增服务没破坏既有协议行为
python -m robot --outputdir results/dev_diag_02 tests/robot/02_uds_diagnostic.robot
python -m robot --outputdir results/dev_diag_03 tests/robot/03_dtc_workflow.robot
python -m robot --outputdir results/dev_diag_06 tests/robot/06_uds_flash_conformance.robot
python -m robot --outputdir results/dev_diag_s03 scenarios/03_diag_permission_matrix.robot
```

---

## 31.6 逐条讲 14 套件（关键设计）

**① 为什么要断言"响应来自 0x7E8"？**
只断言"有正响应"是不够的：如果实现错误地把响应发回 0x7DF，测试也会"有响应"，
但真实总线上多 ECU 会撞车。所以必须断言来源 ID —— 这是功能寻址用例的核心。

**② 为什么用 `22 F1 87` 而不是短 DID？**
零件号 15 字节 → 响应 18 字节 > 7 字节，强制走 ISO-TP 多帧。
一条用例同时覆盖"功能寻址 + 多帧响应 + 流控帧"三件事，性价比最高。
断言方式：`62f187` 前缀 + 把 payload 解码成 ASCII 与期望零件号全等（不是只看前缀）。

**④ 为什么用仪表联动来验证 0x2F？**
只断言响应 `6F …` 只能证明"ECU 回了话"，证明不了"执行器真的动了"。
断言 headless 仪表的状态文件里 `CoolantTempHigh` 点亮，才是端到端闭环
（ECU 0x300 报文 → 仪表显示）。交还控制权后再断言灯灭，验证"不会卡在强制状态"。

**⑤ 为什么负向要分两个 NRC？**
- 默认会话 → `0x7F`：会话层不支持；
- 扩展未解锁 → `0x33`：会话对了但权限不够。
这两个 NRC 对应权限矩阵的两层门槛，分开断言才能定位"是哪一层没拦住"。

**⑦ 为什么写内存要测"多帧请求"？**
`3D` 写 4 字节的请求是 11 字节，必须 FF+CF。刷写/Bootloader 场景全是多帧写，
这条用例把"测试端拆帧 + ECU 重组 + 回读校验"完整覆盖。

**⑨ 为什么先等车速稳定再比？**
跨系统比较最怕"两个动态值采样时刻不同"（网络延迟 + 斜坡加速）。
先等车速进入 59~61 并额外稳定 0.5s，再取总线值与 OBD 值，容差才有意义。

**⑩ 为什么用工程 DID 造 P0562？**
OBD mode 03 只报"已确认"DTC。用 `Set Test Stimulus voltage=9` 触发真实检测流程
（pending → confirmed，2 个检测周期），比手改内部状态更接近真实故障，
也能顺便验证"OBD 读到的 DTC 就是 UDS 诊断里的 confirmed DTC"。

---

## 31.7 真实项目怎么接

### 31.7.1 工具链对照

| 场景 | 真实项目工具 | 本项目替代 |
|---|---|---|
| OBD 扫描仪 | **ELM327**（USB/蓝牙）、元征/Autel/Launch 诊断仪、Torque 类 App | `OBD Read *` 关键字（python-can 直发） |
| 功能寻址诊断 | **CANoe / CANape 诊断控制台**（可切换物理/功能寻址）、ODX 数据库 | `Send Functional UDS Request` |
| 执行器控制（产线） | **产线 EOL 设备**、CANoe 诊断面板、售后诊断仪 | `IOControl Send`（0x2F） |
| 内存读写 / Bootloader | CANoe + vTESTstudio、厂家 Flash 工具、UDS 刷写脚本 | `Read Memory By Address` / `Write Memory By Address`（0x23/0x3D） |
| OBD 一致性测试 | 法规认证台架、OBD 模拟器（如 Vector OBD 工具） | 14 套件 ⑨⑩（最小 OBD 扫描仪） |

### 31.7.2 一个真实"产线点灯测试"的流程

```
EOL 工位：诊断仪进入扩展会话 → 安全解锁（产线特殊权限）
  → 0x2F 30 01 02 <mask> 依次点亮每一组灯 → 工位相机/人工确认
  → 0x2F 30 01 00 00 交还控制权 → 恢复现场
  → 结果写入 MES 系统（哪台车、哪个灯、通过/不通过）
```

与 14 套件 ④ 的差别只在：真实项目是"逐个灯 + 视觉/传感器确认"，本项目用 headless 仪表状态断言。

### 31.7.3 测试工程师要关注什么

- **功能寻址**：响应 ID 是否分配正确、多 ECU 时是否撞车、功能寻址是否误触发不该响应的服务。
- **OBD**：PID 换算/单位是否与规范一致、mode 03 的 DTC 数量与列表是否一致、清码后是否还能重新检测。
- **0x2F**：权限是否严格、交还控制权后是否恢复正常、强制动作是否会影响其他功能。
- **0x23/0x3D**：地址/长度边界校验（**越界必须拒绝**）、敏感区域是否被保护、写后校验是否一致。

---

## 31.8 自查题（先想 30 秒再看要点）

1. **功能寻址 0x7DF 与物理寻址 0x7E0 的本质区别？响应发到哪？**
   > 要点：请求是否广播（0x7DF 给所有 ECU，0x7E0 点对点）；响应**永远**发各 ECU 自己的物理响应 ID（本项目 0x7E8），否则多 ECU 会撞车。
2. **为什么 OBD 服务不需要厂家会话/安全解锁？**
   > 要点：OBD 是法规要求，年检/通用扫描仪插上就要能读排放数据，所以设计成默认会话可用、走 0x7DF。
3. **OBD 的 P0562 和 UDS 的 P0562 在报文上差什么？**
   > 要点：OBD 2 字节 `05 62`，UDS 3 字节 `05 62 00`（多一个故障类型字节）。
4. **PID 0x0C 的 rpm 怎么算？给 A=0x4B、B=0x00 求转速。**
   > 要点：`((0x4B*256)+0x00)/4 = 19200/4 = 4800 rpm`。
5. **0x2F 的 returnControlToECU 和 resetToDefault 有什么区别？为什么产线测完必须交还控制权？**
   > 要点：都是恢复 ECU 正常控制（教学版同效，真实实现 reset 还回默认值）；不交还的话执行器会卡在强制状态，污染后续功能（比如灯一直亮）。
6. **0x23/0x3D 的 ALFID=0x14 是什么意思？**
   > 要点：高 4 位=长度字节数=1，低 4 位=地址字节数=4 → 4 字节地址 + 1 字节长度（最多 255 字节数据）。
7. **内存读写的三个典型风险？测试怎么防？**
   > 要点：读敏感数据/篡改标定/越界踩内存；测试要覆盖权限（未解锁拒绝）、越界（0x31）、写后读回一致性。
8. **如果 ECU 把功能寻址响应的 CAN ID 也发成 0x7DF，会出什么问题？测试怎么快速发现？**
   > 要点：多 ECU 响应撞车/诊断仪无法区分来源；测试用"响应来源 ID 断言"（`Response Should Come From 0x7E8`）一测就现形。

### 本章要点回顾（面试速答）

- 请求可广播（0x7DF），**响应必须实名**（0x7E8/0x7E9…）。
- OBD-II 是**法规诊断**（ISO 15031/J1979，0x7DF，默认会话可读），UDS 是**厂家诊断**（ISO 14229，权限严格）；OBD 是 UDS 的法规子集。
- OBD 关键公式：`rpm=((A*256)+B)/4`、`车速=A`、`水温=A-40`；DTC 2 字节编码。
- 0x2F = 强制执行器（产线点灯/EOL）；0x23/0x3D = 按地址读写内存（工程/产线/Bootloader，高风险）。
- 本项目把三块能力都做成了可运行用例，并用 02/03/06/scenarios-03 回归证明"新增服务不破坏既有协议"。

---

## 31.9 上下章与相关章节

- ← 上一章：[30 · SOME/IP 与 SOA 服务测试](30_SOMEIP与SOA服务测试.md)（车载以太网服务化通信）
- → 下一章：[32 · 功能安全机制验证](32_功能安全机制验证.md)（安全机制/故障注入验证）
- 相关章节：
  - [03 · UDS 协议详解](03_UDS协议详解.md)（服务/NRC/会话基础）
  - [04 · DTC 详解](04_DTC详解.md)（DTC 编码与状态机）
  - [07 · CAN 仿真测试实战](07_CAN仿真测试实战.md)（总线/报文基础）
  - [08 · UDS 诊断测试实战](08_UDS诊断测试实战.md)（物理寻址诊断套件逐条讲解）
  - [19 · 十六个场景脚本实战](19_十六个场景脚本实战.md)（权限矩阵场景）
  - [27 · 信息安全测试入门](27_信息安全测试入门.md)（权限矩阵/攻击面视角）
- 代码入口：
  - `simulators/ecu_sim.py`（`ID_UDS_FUNCTIONAL_REQUEST` / `_svc_io_control` / `_svc_read_memory` / `_svc_write_memory` / `_svc_obd`）
  - `libraries/ObdLibrary.py`（本章全部关键字与最小 ISO-TP 收发实现）
  - `tests/robot/14_functional_addressing_obd.robot`（10 条用例）
