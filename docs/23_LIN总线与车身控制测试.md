# 23 · LIN 总线与车身控制测试

> 本章解决一个问题：**方向盘后面的"小功能"——雨刷、座椅、车门、后视镜——为什么不用 CAN，而用 LIN？**
> 答案会牵出 LIN 的三根支柱：**单线主从结构、调度表、两种校验和**，以及一套和 CAN 完全不同思路的测试方法。
>
> 学完你应该能：对着一帧 LIN 抓包说出每个 bit 是什么、手算 PID 与校验和、解释"调度表抖动/睡眠唤醒/从机无响应"该怎么测，并跑通 `tests/robot/08_lin_bus.robot` 的 10 个用例。
>
> 前置：02 章（CAN 与总线基础）、07 章（"仿真器 → 关键字库 → 套件"的套路）、22 章（能力地图里 LIN 的位置）。

---

## 📖 零基础读法建议（先看这个，能省你半天）

本章的难度不在"概念多"，而在"细节密"（位、奇偶、校验和）。按下面的优先级分配精力：

| 标记 | 章节 | 为什么这个优先级 |
|---|---|---|
| 🟢 **必读** | **23.3 一帧 LIN 报文**（尤其 23.3.1 PID、23.3.2 校验和）、**23.4 调度表与睡眠唤醒**、**23.6 逐条讲 08 套件** | 你每天工作都要用：看抓包、解信号、判断帧为什么"无效" |
| 🟡 **了解即可** | 23.2 物理层与主从模型、23.7 负向测试设计 | 面试常问"LIN 和 CAN 的区别"；负向设计是拉开水平的地方，能说清思路即可 |
| ⚪ **可以跳过** | 23.8 真实工作映射、23.9 扩展练习 | 等入职台架实操时再回来看，会更有感觉 |

> 💡 **为什么敢让你跳过物理层**：测试工程师的日常是"抓帧、解信号、验行为、判时序"，
> 不碰 LIN 收发器电路。你只需要记住一句：**LIN 一根线、12V、主节点轮询、从节点不许主动发**。
>
> **本章的验收标准只有一条**：给你一帧 `PID=0x92、DATA=80 78、CHECKSUM=74`，
> 你能说出这是 0x12 座椅帧、seat_slide=128mm、seat_backrest=60°，并且校验和 0x74 算得对。

---

## 23.1 为什么车里还需要 LIN（CAN 不够用吗）

CAN 很便宜，但"便宜"是相对的。一个 CAN 节点需要：CAN 控制器（集成在 MCU 里）+ CAN 收发器 + 双绞线 + 终端电阻。而车门里有一堆**更便宜、更慢、更不关键**的东西：

| 功能 | 数据量 | 实时性要求 | 用 CAN 的浪费点 |
|---|---|---|---|
| 雨量传感器 | 1 字节/40ms | 低（雨刷反应慢 100ms 无感） | 收发器比传感器贵 |
| 座椅位置/靠背角度 | 2 字节/40ms | 低 | 每根线都要接进 CAN 主干 |
| 车窗开关、后视镜调节 | 1~2 字节 | 低 | 节点多、线束长 |
| 氛围灯、门把手灯 | 1 字节 | 低 | 成本敏感 |

这就是 LIN 的定位：**车身域（Body Domain）里"低速、低成本、执行器类"的最后一公里**。典型车速：**19.2kbps**（也有 9.6k/2.4k），一个主节点最多带 16 个从节点，一根线串起整个车门/座椅。

**测试视角**：LIN 模块的缺陷非常"接地气"——雨刷乱刮、座椅只动一半、钥匙解锁后氛围灯不亮、锁车后暗电流超标……根因常常是：
- 调度表时序不对（从机没被及时轮询）；
- 信号位布局/缩放错了（LDF 与实现不一致）；
- 校验和算法用错（从机丢帧）；
- 睡眠/唤醒时序不对（亏电或唤醒不了）。

**CAN 与 LIN 的核心区别**（面试高频，必须能顺口说）：

| 维度 | CAN | LIN |
|---|---|---|
| 线束 | 双线差分（CAN_H/CAN_L） | **单线**（+12V 供电地回路） |
| 速率 | 500kbps / 250kbps | **≤20kbps**，典型 19.2kbps |
| 拓扑 | 多主，谁都能发，靠**位仲裁**抢总线 | **单主多从**，从机不许主动发，主机**轮询** |
| 从机成本 | 需要晶振/控制器 | **无晶振**（靠 SYNC 字节校准），几毛钱 |
| 典型场景 | 动力/底盘/安全/诊断 | 车门/座椅/雨刷/后视镜/氛围灯 |
| 一句话 | 快、可靠、贵 | 慢、够用、极便宜 |

> ⚠️ 记住这张表里的"**从机不许主动发**"——它决定了 LIN 的测试方法和 CAN 完全不同：
> CAN 测试是"抓广播、看周期"；LIN 测试是"**主机发报头 → 从机应答**"的一问一答，还要验证调度表。

---

## 23.2 物理层与主从模型（30 秒版）

LIN 总线的电平（**了解即可**）：

```
显性位 (Dominant, 逻辑 0)：总线被拉低 ≈ 0V   ← 帧起始 BREAK、数据 0 位
隐性位 (Recessive, 逻辑 1)：上拉到 ≈ 12V（电池电压）  ← 空闲、数据 1 位
```

只需要记住：**LIN 用电压高低传 0/1，一根线；空闲时是 12V（隐性）**。真实 LIN 的收发电平由收发器（如 TJA1021）处理，测试工程师不碰电路。

**主节点 vs 从节点**：

| | 主节点（Master） | 从节点（Slave） |
|---|---|---|
| 谁担任 | BCM / 域控制器 / 网关 | 雨量传感器、雨刷电机、座椅模块、车门模块 |
| 时钟 | 有晶振，精度高 | **无晶振**，靠 SYNC 字节 `0x55` 校准 |
| 行为 | 按**调度表**发报头（header），发主请求帧，管睡眠唤醒 | 收到匹配自己 ID 的报头，才填数据/校验和应答 |
| 出错时 | 主节点决定降级/重试 | 从机不响应 → 主节点超时（**这是必测项**） |

**关键推论（本项目的设计依据）**：LIN 测试必须有一个"主节点"按时基主动发帧。这正是本项目 LIN 模块不复用 CAN 虚拟总线的根本原因——CAN 的"谁都能广播"模拟不了"从机不许主动说话"。

---

## 23.3 一帧 LIN 报文长什么样（核心）

LIN 2.x 的一帧由**主机发报头 + 从机应答**两段拼成，物理上连续传输：

```
                    一帧 LIN 报文（Master 发 header，Slave 填 data）
┌─────────────┬──────────┬─────────────┬───────────────────┬──────────────┐
│   BREAK     │   SYNC   │     PID     │       DATA        │   CHECKSUM   │
│   ≥13 bit   │  0x55    │  6bit ID    │     1~8 字节      │    8 bit     │
│  显性(0V)   │ 同步波形 │  +2bit 奇偶 │ 从机填/主请求载荷 │ 经典/增强两种│
└─────────────┴──────────┴─────────────┴───────────────────┴──────────────┘
  主机发 ────────────────────────▶│ 匹配 ID 的从机填充 DATA + CHECKSUM ──▶
```

| 字段 | 长度 | 作用 | 测试要知道的 |
|---|---|---|---|
| **BREAK** | ≥13 bit 显性 | 帧起始；也是**唤醒信号**（主机拉低总线 250us~5ms） | 睡眠后发 BREAK/唤醒脉冲能叫醒从机 |
| **SYNC** | 8 bit | 固定 `0x55`（交替方波） | 从机用它校准时钟——**从机没晶振省成本的关键** |
| **PID** | 8 bit | 6 bit 帧 ID + 2 bit 奇偶校验 | **从机以此判断"这帧是不是给我的"**；PID 错 = 整帧无效 |
| **DATA** | 1~8 字节 | 载荷（信号按 LDF 位布局填充） | 信号解码的原材料；`0x3C` 主请求帧 8 字节 |
| **CHECKSUM** | 8 bit | 校验和，两种算法 | 算错 → 从机静默丢帧（**测试要能识别**） |

> 💡 真实抓包工具（CANoe 的 LIN 窗口）会把上面 5 段直接画出来。本项目 `simulators/lin_sim.py` 发的 JSON 帧就是这 5 段的等价模拟：
> `{"break":true, "sync":85, "pid":80, "data":[150], "checksum":25, ...}`。

### 23.3.1 PID：6 bit ID + 2 bit 奇偶（防"张冠李戴"）

LIN 帧 ID 只有 6 bit（0x00~0x3F），但物理上要发 8 bit 的 PID。多出的 2 bit 是**奇偶校验位**，让从机能判断"收到的 ID 是否可信"：

```
b0..b5 = ID 的 6 个 bit
P0 = b0 ^ b1 ^ b2 ^ b4          （偶校验位）
P1 = !(b1 ^ b3 ^ b4 ^ b5)       （奇校验位）
PID = ID | (P0 << 6) | (P1 << 7)
```

**算例（手算必会）：**

| 帧 ID | 二进制（b5..b0） | P0 | P1 | PID |
|---|---|---|---|---|
| `0x10` 雨量 | `010000` | 1 | 0 | **`0x50`** |
| `0x11` 雨刷 | `010001` | 0 | 0 | **`0x11`** |
| `0x12` 座椅 | `010010` | 0 | 1 | **`0x92`** |
| `0x3C` 主请求 | `111100` | 0 | 0 | **`0x3C`**（恰好等于 ID，LIN 规范如此设计） |

**为什么值得单独测**：从机如果收错 PID，会把"座椅帧"当成"雨量帧"去填数据（或直接丢弃），表现为**信号串位/时有时无**。真实缺陷排查第一步就是看 PID 对不对。
本项目仿真器会发 `pid` 字段，`libraries/LinLibrary.py` 的 `LIN PID Should Be Valid` 按上面公式**独立重算**校验；`inject_error kind=bad_parity` 可翻转 P0 位制造故障（套件用例 5 用它做负向）。

### 23.3.2 两种校验和：经典 vs 增强（手算 + 代码）

LIN 有**两套**校验和，用错 = 从机丢帧：

- **经典校验和（classic）**：只累加 **DATA 字节**。LIN 1.x 的默认；LIN 2.x 里**只保留给 `0x3C` 主请求帧**（规范硬性规定）。
- **增强校验和（enhanced）**：**PID + DATA 字节**一起累加。LIN 2.x 普通帧用它——多保护了 PID，防止 ID 被篡改。

算法（两者只差"要不要加 PID"）：

```
1) sum = (classic: sum(DATA)) 或 (enhanced: PID + sum(DATA))
2) 进位回卷：while sum > 0xFF: sum = (sum & 0xFF) + (sum >> 8)
3) checksum = (~sum) & 0xFF      ← 取反码
```

**4 个手算算例（拿笔跟一遍）：**

| 帧 | 算法 | PID | DATA | 累加和 | 回卷后 | 取反 → CHECKSUM |
|---|---|---|---|---|---|---|
| `0x3C` 主请求（唤醒/默认） | classic | 不参与 | `01 00 00 00 00 00 00 00` | 0x01 | 0x01 | **0xFE** |
| `0x10` 雨量 75% | enhanced | 0x50 | `96`（150 raw） | 0x50+0x96=0xE6 | 0xE6 | **0x19** |
| `0x12` 座椅 128mm/60° | enhanced | 0x92 | `80 78`（128,120 raw） | 0x92+0x80+0x78=**0x18A** | 0x8A+0x01=0x8B | **0x74** |
| `0x11` 雨刷关 | enhanced | 0x11 | `00` | 0x11 | 0x11 | **0xEE** |

> ⚠️ **0x3C 必须用经典校验和**。这是 LIN 2.x 规范里唯一"特殊对待"的帧（主请求帧），
> 很多新手会统一按 enhanced 算，导致主请求帧校验失败。套件用例 4 专门断言两个帧的 `checksum_type`。

本项目里校验和算法有**两份独立实现**：
- `simulators/lin_sim.py` —— 发帧时算；
- `libraries/LinLibrary.py` —— 测试端收到帧后**重算一遍**再比对。

为什么故意写两遍？如果测试库直接调用仿真器的函数，仿真器算错时"两边一起错"，断言永远通过（**恒真断言**，测试质量里最隐蔽的坑）。独立重算 + 错误注入负向用例，才能证明"校验和检查是真的"。

---

## 23.4 调度表、睡眠与唤醒（LIN 的"时间观"）

### 23.4.1 调度表：主机按表轮询

LIN 主节点不是"想发就发"，而是执行一张**调度表（Schedule Table）**：每个报文的槽位、周期、顺序都预先定义。本项目模拟车身域典型配置：

```
时间(ms)   0     10    20    30    40    50    60    70    80
         ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┬──
帧 ID    0x10  0x11  0x12  0x3C  0x10  0x11  0x12  0x3C  0x10 ...
         │雨量 │雨刷 │座椅 │主请求│     │     │     │     │
         └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┴──
          ←────── 一个调度周期 40ms ─────→
          ← 每个槽位 10ms，4 个报文轮一圈，每个 ID 每 40ms 出现一次 →
```

**设计数字**：槽位 `10ms`、4 个报文轮询 → 每个报文周期 `4 × 10 = 40ms`。

**为什么时序值得单测（用例 1）**：
- 周期是设计指标，漂移会导致依赖它的功能异常（雨刷响应变慢、座椅调节卡顿）；
- 真实台架上用示波器/CANoe LIN 窗口测周期与抖动（jitter = 周期最大值 - 最小值）；
- 本项目用"到达时间戳统计"实现等价验证：相邻帧间隔 ≈10ms，每个 ID 周期 ≈40ms，抖动在容差内。

### 23.4.2 睡眠 / 唤醒：车身域最敏感的测试点

锁车后，整车要求"暗电流 < 20mA"——任何一个 LIN 从机不睡，第二天车主可能打不着火。所以 LIN 必须支持**睡眠/唤醒**：

```
正常调度 ──▶ 主节点发 0x3C 主请求帧，byte0 = 0x00（睡眠命令）
                │
                ▼
          总线静默（从机全部休眠，电流降到 uA 级）  ← 测试断言：此后一帧都不许有
                │
                ▼
          唤醒脉冲（主机拉低总线 250us~5ms，或 0x3C byte0=0x01）
                │
                ▼
          调度恢复，从机重新应答  ← 测试断言：帧恢复、首帧数据/校验和正常
```

本项目仿真实现：
- `Send LIN Master Request    00 00 00 00 00 00 00 00` → 发一帧 0x3C（睡眠命令帧）后**停止调度**；
- `Wake Up LIN Bus` → 发 `wakeup_pulse` 事件并恢复调度；
- `Get LIN Events` 可拿到 `sleep` / `wakeup_pulse` / `master_request` / `no_response` 事件，用于验证"控制行为真的发生了"（帧本身看不出唤醒脉冲）。

---

## 23.5 本项目实现：一个独立进程 + 两个端口

### 23.5.1 架构图

```
┌───────────────────────────────────┐        UDP 127.0.0.1:30001         ┌────────────────────────────┐
│ simulators/lin_sim.py             │ ────────────────────────────────▶ │ libraries/LinLibrary.py    │
│  （被仿真的 LIN 总线）             │   每帧一条 JSON（等价抓包流）      │  （RF 测试端 = 主机侧）     │
│  · 主节点调度器（10ms/槽）         │                                    │  · 接收线程缓存帧/事件      │
│  · 4 个从机信号存储                │ ◀──────────────────────────────── │  · 独立重算 PID/校验和      │
│  · 睡眠/唤醒/错误注入状态机         │   TCP 127.0.0.1:30002             │  · LDF 解码                 │
└───────────────────────────────────┘   控制命令（JSON 行，一问一答）    └────────────────────────────┘
                                                                                    ▲
                                                                                    │ Robot Framework
                                                                    tests/robot/08_lin_bus.robot（10 用例）
```

- **帧路径（UDP 30001，单向下行）**：仿真器把每帧发给测试库，等价于 LIN 分析仪抓包。测试库只管收，不能"发帧到线上"——真实 LIN 测试里测试端一般不直接驱动总线，而是通过主节点/仿真器控制。
- **控制路径（TCP 30002，请求-响应）**：测试库扮演"测试工程师"，下发设置从机信号、发主请求、注入错误等命令。
- **端口可用环境变量覆盖**：`LIN_UDP_PORT` / `LIN_CTRL_PORT`（测试进程与仿真子进程读同一份环境变量，避免和同事/CI 抢端口）。

### 23.5.2 文件清单与职责

| 文件 | 角色 | 关键内容 |
|---|---|---|
| `simulators/lin_sim.py` | 被仿真 LIN 总线（独立进程） | 调度表、PID/校验和算法、睡眠唤醒、3 种错误注入、TCP 控制服务 |
| `simulators/ldf/vehicle.ldf` | LIN 数据库（教学版 LDF 子集，JSON 载体） | 4 个 frame、每个 signal 的 start_bit/length/scale/offset/枚举 |
| `libraries/LinLibrary.py` | Robot Framework 关键字库 | 子进程懒启动/回收、UDP 接收、15 个契约关键字 + 扩展关键字、独立校验和、LDF 解码 |
| `tests/robot/08_lin_bus.robot` | 套件 08：10 个用例 | 正向 4 + 信号 2 + 睡眠唤醒 2 + 负向/超时 2；5 个需求标签 |
| `docs/23_LIN总线与车身控制测试.md` | 本章 | 你现在读的这份 |

### 23.5.3 控制命令与 JSON 帧格式

TCP 控制命令（一行一条 JSON，响应也一行 JSON）：

| 命令 | 参数 | 作用 | 对应 RF 关键字 |
|---|---|---|---|
| `set_slave` | `id`, `signals` | 设置从机信号（按 LDF 编码） | `Set LIN Slave Signal` |
| `send_master_request` | `data`（8 字节） | 发 0x3C 主请求，byte0=0x00 睡眠 / 0x01 唤醒 | `Send LIN Master Request` |
| `send_header` | `id` | 手动发报头，从机立即应答 | `Send LIN Header` |
| `inject_error` | `kind`(checksum/no_response/bad_parity), `id`, `count` | 排队错误注入（负向测试） | `Inject LIN Error` |
| `wakeup` | — | 发唤醒脉冲、恢复调度 | `Wake Up LIN Bus` |
| `stats` | — | 读仿真器统计 | `Get LIN Statistics` |
| `ping` / `quit` | — | 健康检查 / 退出 | `Start/Stop LIN Simulation` 内部使用 |

UDP 帧 JSON 示例（`Set LIN Slave Signal 0x12 seat_slide=128 seat_backrest=60` 之后的一帧，字段已省略无关项）：

```json
{"kind":"frame", "seq":45, "id":18, "pid":146, "data":[128,120],
 "checksum":116, "checksum_type":"enhanced", "checksum_valid":true,
 "pid_parity_valid":true, "break":true, "sync":85,
 "direction":"slave_response", "reason":"schedule"}
```

对照 23.3 的图读一遍：`pid=146=0x92`（0x12 的 PID）、`data` 两字节、`checksum=116=0x74`（与 23.3.2 手算一致）。**这就是"一帧 LIN 报文"在软件里的样子**。

### 23.5.4 三个关键设计（也是三个真实坑）

**① `since_seq` 标记：只等"操作之后"的新帧**

`Set LIN Slave Signal` 等关键字返回一个**只增不减的帧序号标记**。之后用 `Wait For LIN Frame ... since_seq=${marker}` 只接受 seq 更大的帧：

```robotframework
${marker}=    Set LIN Slave Signal    0x10    rain_intensity=75
${frame}=     Wait For LIN Frame      0x10    timeout=2    since_seq=${marker}
```

**为什么必须这样**：接收线程是异步的，执行 `Set` 前后可能已经缓存了 1~2 帧**旧值**。如果直接 `Wait For LIN Frame 0x10`，很可能读到旧帧 → 断言随机失败（经典的 flaky）。用标记过滤，"旧帧污染"这条路被彻底堵死。这是本项目 RF 测试里最重要的稳定性设计之一。

**② 调度器必须用高精度 `time.sleep()`**

本项目开发时实测过一个 Windows 坑：`threading.Event.wait(0.01)` 的定时精度会被系统定时器量化成 ~15.6ms，导致调度出现"**52ms / 26ms 交替**"的周期抖动（周期均值仍是 40ms，但 jitter 高达 26ms，用例 1 直接挂）。改用 `time.sleep()`（Python 3.11+ 在 Windows 上是高精度 waitable timer）后，实测抖动 **< 1ms**。`simulators/lin_sim.py` 的调度循环里保留了这段注释——真实项目里"定时器精度"确实会决定测试稳定性。

**③ 子进程回收：quit → wait → terminate → kill**

`Suite Setup` 启动 `simulators/lin_sim.py` 子进程，`Suite Teardown` 必须把它收干净，否则残留进程占着 30001/30002，下一次跑必挂。库里的回收顺序：

```
发 quit → proc.wait(5s) → 超时则 terminate → wait(3s) → 再超时 kill → wait
```

对应 `Stop LIN Simulation` 的 docstring；套件里的 `Suite Teardown    Stop LIN Simulation` 调用它。

### 23.5.5 LDF：LIN 网络的"字典"

真实 LDF（LIN Description File）是文本格式（类似 C 语法），由 LDF 工具解析。本项目为了零依赖（只用标准库 `json`），把它裁剪成 **JSON 载体、扩展名仍用 `.ldf`**，字段语义与真实 LDF 对齐：

| 字段 | 含义 | 本项目取值（`simulators/ldf/vehicle.ldf`） |
|---|---|---|
| `frame.id` | LIN 帧 ID（6 bit） | 0x10 / 0x11 / 0x12 / 0x3C |
| `frame.length` | 数据字节数 | 1 / 1 / 2 / 8 |
| `frame.checksum` | 校验和类型 | 0x3C=classic，其余=enhanced |
| `signal.start_bit` | 起始 bit（小端位序） | 如 `seat_backrest` start=8 |
| `signal.length` | 位宽 | 8 / 8 / 8 / 6 / 2 / 56 |
| `signal.scale` | 物理值 = 原始值 × scale + offset | 雨量 0.5、靠背 0.5、其余 1 |
| `signal.values` | 枚举值表（VAL_） | `wiper_state`: 0=OFF 1=INTERMITTENT 2=LOW 3=HIGH |

**测试工作的意义**：LDF 是主节点/从节点/测试端的**共同契约**。代码里实现与 LDF 不一致 = 真实项目里的高频缺陷。套件用例 9 用 LDF 解码去交叉验证仿真器编码，两边只要有一边改错，用例立刻失败。

---

## 23.6 逐条讲 08 套件（`tests/robot/08_lin_bus.robot`）

### 23.6.0 套件骨架

```robotframework
*** Settings ***
Documentation    套件 08：LIN 总线与车身控制测试
Library          ../../libraries/LinLibrary.py
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation
Test Setup       恢复 LIN 总线到清醒状态

*** Keywords ***
恢复 LIN 总线到清醒状态
    Wake Up LIN Bus
    Clear LIN Frame Buffer
```

三个要点：

1. **不 import `resources/common.resource`** —— 那是 CAN/ECU 的环境资源，本模块不需要 ECU；LIN 模块是**独立可运行**的最小闭环（这是"模块化新增"的示范：新模块不和旧模块耦合）。
2. **Suite Setup/Teardown 自管进程** —— 一条 `robot` 命令拉起、回收 LIN 仿真器，零手动操作。
3. **Test Setup 做用例隔离** —— `Wake Up` 把上一个用例可能留下的睡眠状态复位，`Clear` 清空接收缓存，保证每个用例从干净状态开始（项目里另一条铁律：**用例之间不能有隐式状态依赖**）。

### 23.6.1 用例 1：调度表应按 10ms 时基轮询四个报文

```robotframework
调度表应按 10ms 时基轮询四个报文
    [Tags]    REQ-LIN-001
    Clear LIN Frame Buffer
    Sleep    2s
    ${stats}=    LIN Schedule Timing Should Be Within
    ...    expected_slot_ms=10
    ...    period_tolerance_ms=6
    ...    jitter_tolerance_ms=15
    Should Be True    ${stats}[frames] >= 160
```

**测什么**：调度表是否按设计时基运行——相邻帧间隔 ≈10ms、每个 ID 周期 ≈40ms、抖动在容差内。

**怎么测**：清缓存 → 采样 2 秒（约 200 帧）→ 库内按到达时间戳算平均间隔/周期/jitter，超差抛错并指出具体帧 ID 的实测值。

**真实工作对应**：LIN 调度表时序验证。CANoe LIN 窗口看周期抖动、示波器测 bus timing；周期漂移会导致雨刷/座椅响应异常，是台架必测项。

**设计亮点**：断言用**范围 + 抖动**而不是"周期必须精确 40.000ms"——真实总线永远有抖动，测试指标要可执行。`frames >= 160` 顺带验证"真的在发帧"（防止分析空数据恒真）。

### 23.6.2 用例 2：雨量传感器信号设置后应出现在状态帧中

```robotframework
雨量传感器信号设置后应出现在状态帧中
    [Tags]    REQ-LIN-002
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=75
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[data][0]    150
    LIN Frame Should Contain Signal    ${frame}    rain_intensity    75    tolerance=0.5
```

**测什么**：从机信号设置 → 编码 → 线上字节 → 解码回物理值，整条链路正确。75% 雨量按 LDF 的 0.5%/LSB 编码 = 原始值 **150**。

**设计亮点**：**一层协议、一层应用**双重断言——字节 `data[0]==150` 证明协议层编码对，`LIN Frame Should Contain Signal` 证明 LDF 解码对。真实排故时两个视角都要有（hex 看协议、物理值看功能）。

### 23.6.3 用例 3：座椅两字节多信号应按位布局正确编码解码

```robotframework
座椅两字节多信号应按位布局正确编码解码
    [Tags]    REQ-LIN-002
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}    seat_slide=200    seat_backrest=45
    ${frame}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[data][0]    200
    Should Be Equal As Integers    ${frame}[data][1]    90
    LIN Frame Should Contain Signal    ${frame}    seat_slide    200
    LIN Frame Should Contain Signal    ${frame}    seat_backrest    45
```

**测什么**：一个 2 字节帧里装两个信号（bit0~7 滑轨、bit8~15 靠背）的**位布局/缩放**是否正确。200mm → 原始 200；45° 按 0.5°/LSB → 原始 **90**。

**真实工作对应**：信号矩阵验证的核心——"多个信号共享一帧"时位布局错一位，就会串位/溢出。台架上这类问题表现为"座椅能动但角度不对"。

### 23.6.4 用例 4：经典与增强校验和均应校验通过

```robotframework
经典与增强校验和均应校验通过
    [Tags]    REQ-LIN-003
    ${classic}=    Wait For LIN Frame    ${FRAME_MASTER}    timeout=2
    Should Be Equal    ${classic}[checksum_type]    classic
    LIN Checksum Should Be Valid    ${classic}
    ${enhanced}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2
    Should Be Equal    ${enhanced}[checksum_type]    enhanced
    LIN Checksum Should Be Valid    ${enhanced}
```

**测什么**：0x3C 主请求帧是**经典校验和**（只数据），其余帧是**增强校验和**（PID+数据）。测试端独立重算并比对。

**设计亮点**：`LIN Checksum Should Be Valid` 是**独立实现的第二份算法**（不复用仿真器代码）——这是"测试的独立性"原则：验证方必须和被验证方实现独立，否则一起错。本用例与用例 5（负向）配对存在，才是有效测试。

### 23.6.5 用例 5：校验和与 PID 奇偶错误注入后应被识别为无效帧

```robotframework
校验和与 PID 奇偶错误注入后应被识别为无效帧
    [Tags]    REQ-LIN-003    REQ-LIN-005
    ${marker}=    Inject LIN Error    checksum    ${FRAME_RAIN}    count=1
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be True    not ${frame}[checksum_valid]
    Run Keyword And Expect Error    *校验和*    LIN Checksum Should Be Valid    ${frame}
    ...
    ${marker2}=    Inject LIN Error    bad_parity    ${FRAME_WIPER}    count=1
    ${frame2}=    Wait For LIN Frame    ${FRAME_WIPER}    timeout=2    since_seq=${marker2}
    Run Keyword And Expect Error    *PID*    LIN PID Should Be Valid    ${frame2}
```

**测什么**：**负向用例**——注入错误后，校验关键字必须失败。用 `Run Keyword And Expect Error` 断言"它确实会失败"，这才能证明断言不是恒真。

**设计亮点**：
- 注入 `checksum`：帧照发，但 checksum 被翻转（接收端应丢弃）；
- 注入 `bad_parity`：PID 的 P0 位被翻转（接收端应判帧 ID 不可信）；
- 两个方向的负向各一条 `Expect Error`，**错误消息用通配符精确匹配**（`*校验和*` / `*PID*`），避免"因为它挂了所以通过"的假阴性。

### 23.6.6 用例 6：从节点无响应注入后等待应超时

```robotframework
从节点无响应注入后等待应超时
    [Tags]    REQ-LIN-005
    ${marker}=    Inject LIN Error    no_response    ${FRAME_SEAT}    count=5
    Run Keyword And Expect Error    *未等到帧*
    ...    Wait For LIN Frame    ${FRAME_SEAT}    timeout=0.15    since_seq=${marker}
    ${other}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=1    since_seq=${marker}
    Should Be Equal As Integers    ${other}[id]    16
    ${stats}=    Get LIN Statistics
    Should Be True    ${stats}[skipped] >= 1
```

**测什么**：① 从机不响应时，等待必须在超时时间返回错误（不允许"假装成功"或挂死）；② 错误注入**只影响目标帧**，其他从机/其他帧照常工作（故障隔离）；③ 仿真器的错误计数 `skipped` 递增（主节点确实"记录到了这次错误"）。

**真实工作对应**：真实 LIN 从机掉线/线束断开 = 主节点必须超时并进入降级（仪表提示、DTC）。"一个从机坏了不能拖垮整条总线"是架构要求，也是测试要求。

### 23.6.7 用例 7：进入睡眠后总线应保持静默

```robotframework
进入睡眠后总线应保持静默
    [Tags]    REQ-LIN-004
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    LIN Bus Should Be Silent    0.5
    ${stats}=    Get LIN Statistics
    Should Be True    ${stats}[sleeping]
```

**测什么**：0x3C 主请求 byte0=0x00 → 睡眠命令帧发出 → 调度停止 → **0.5 秒内总线上 0 帧**；仿真器状态 `sleeping=true`。

**真实工作对应**：整车暗电流/静态电流指标。真实测试里会用电流钳测整机电流，同时抓总线确认"没有节点偷发帧"；仿真里用"静默断言"等价验证。

### 23.6.8 用例 8：唤醒脉冲后调度应恢复

```robotframework
唤醒脉冲后调度应恢复
    [Tags]    REQ-LIN-004
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.15s
    LIN Bus Should Be Silent    0.2
    ${marker}=    Wake Up LIN Bus
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    ${pulses}=    Get LIN Events    wakeup_pulse
    Should Not Be Empty    ${pulses}
```

**测什么**：睡眠 → 确认静默 → 唤醒脉冲 → 调度恢复（帧重新出现），并且**唤醒事件被记录**（`wakeup_pulse`）。先断言静默再断言恢复，才能证明"确实是唤醒让它恢复的"。

**真实工作对应**：唤醒时间指标（从唤醒脉冲到首帧通常要求 < 100ms）、唤醒后首帧的数据/校验和是否正常、反复睡醒是否稳定。

### 23.6.9 用例 9：LDF 解码应还原座椅多信号物理值

```robotframework
LDF 解码应还原座椅多信号物理值
    [Tags]    REQ-LIN-002
    Set LDF File    ${ROOT}/simulators/ldf/vehicle.ldf
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}    seat_slide=128    seat_backrest=60
    ${frame}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    ${decoded}=    Decode LIN Frame By LDF    ${frame}
    Should Be Equal As Numbers    ${decoded}[seat_slide]    128
    Should Be Equal As Numbers    ${decoded}[seat_backrest]    60
    ...（负向：LDF 中不存在的 0x3D 帧应报清晰错误）
```

**测什么**：拿 `simulators/ldf/vehicle.ldf` 的正式定义，把原始字节 `[128, 120]` 解码回 `128mm / 60°`，证明"数据库 → 位布局 → 物理值"链路与实现一致；并验证未知帧 ID 报清晰错误（防止解码器"乱猜"）。

**设计亮点**：`Set LDF File` 显式指定数据库文件——真实项目里测试台架要"选择正确的 LDF 版本"（App/配置不同 LDF 不同），路径配错是高频现场问题，所以关键字对不存在/结构非法的文件给出明确报错。

### 23.6.10 用例 10：总线无响应时等待超时时间应符合预期

```robotframework
总线无响应时等待超时时间应符合预期
    [Tags]    REQ-LIN-005    REQ-LIN-001
    ${marker}=    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    ${start}=    Evaluate    __import__('time').time()
    Run Keyword And Expect Error    *未等到帧*
    ...    Wait For LIN Frame    ${FRAME_MASTER}    timeout=0.4    since_seq=${marker}
    ${elapsed}=    Evaluate    __import__('time').time() - ${start}
    Should Be True    ${elapsed} >= 0.38
    Should Be True    ${elapsed} <= 1.5
```

**测什么**：总线完全无帧时，`Wait For LIN Frame` 必须**在 timeout 附近失败**：
- 下界 `>= 0.38s`：不能"立刻返回失败"（说明等待逻辑没生效）；
- 上界 `<= 1.5s`：不能"挂死"（说明超时逻辑没生效）。

**设计亮点**：同时断言**下界和上界**。很多测试只断言"会报错"，却没测"多久报错"——而超时时长本身是需求（真实项目常规定"诊断响应超时 2s"之类）。这是把"失败行为"也当功能来测的示范。

### 23.6.11 需求标签覆盖

| 需求 ID | 含义 | 覆盖用例 |
|---|---|---|
| **REQ-LIN-001** | 调度/时序 | 用例 1、10 |
| **REQ-LIN-002** | 从节点信号响应 | 用例 2、3、9 |
| **REQ-LIN-003** | 校验和 | 用例 4、5 |
| **REQ-LIN-004** | 睡眠/唤醒 | 用例 7、8 |
| **REQ-LIN-005** | 错误容错 | 用例 5、6、10 |

10 个用例、5 条需求全部被引用（可用 `robot --include REQ-LIN-003` 单独跑某需求相关用例，这是需求追溯的落地方式）。

---

## 23.7 负向测试：错误注入与"能失败的用例才是有效的"

### 23.7.1 三种错误注入

| 注入类型 | 仿真行为 | 用哪个关键字验证"必须失败" | 对应用例 |
|---|---|---|---|
| `checksum` | 帧照发，checksum 翻转成错误值（`checksum_valid=false`） | `LIN Checksum Should Be Valid` | 用例 5 |
| `bad_parity` | PID 的 P0 奇偶位翻转（`pid_parity_valid=false`） | `LIN PID Should Be Valid` | 用例 5 |
| `no_response` | 目标帧连续 `count` 次不发送（从机"装死"） | `Wait For LIN Frame` 超时 | 用例 6 |

`Inject LIN Error` 返回 `since_seq` 标记，保证"注入之后"的帧才受影响、等待时不会先读到注入前的正常帧。

### 23.7.2 为什么负向用例要写成 `Run Keyword And Expect Error`

正向断言（`LIN Checksum Should Be Valid` 通过）只能证明"正常帧没误报"；它**不能**证明"坏帧会被抓住"。如果校验关键字实现成 `def lin_checksum_should_be_valid(self, frame): return frame`（永远不检查），正向用例照样全绿——这就是**恒真断言**。

负向写法：

```robotframework
Run Keyword And Expect Error    *校验和*    LIN Checksum Should Be Valid    ${frame}
```

含义是：**"调用这个关键字，必须抛出一个匹配 `*校验和*` 的错误"**。
- 关键字真检查 → 抛出 → 用例通过；
- 关键字恒真 → 不抛 → `Expect Error` 失败 → 用例抓住问题。

本项目历史上踩过"发送→读回自证"的恒真断言（见 22 章审计故事），所以 LIN 模块从设计上就内置了负向证明。**写任何断言时都问自己一句：如果被测代码错了，这条断言会挂吗？**

### 23.7.3 错误消息也要"可断言"

`Expect Error` 的 pattern 依赖错误消息。所以库里的错误消息刻意包含稳定关键词：

| 关键字 | 失败消息关键词 | 负向用例 pattern |
|---|---|---|
| `LIN Checksum Should Be Valid` | "校验和无效"/"校验和不匹配" | `*校验和*` |
| `LIN PID Should Be Valid` | "PID 奇偶校验无效"/"不匹配" | `*PID*` |
| `Wait For LIN Frame` | "未等到帧" | `*未等到帧*` |

真实项目同理：错误消息要能被人看懂、被机器匹配——"报错了"三个字对自动化毫无价值。

---

## 23.8 真实工作怎么接（本项目 ↔ 台架工具映射）

| 本项目 | 真实工作对应物 | 你要会的事 |
|---|---|---|
| `simulators/lin_sim.py`（LIN 主节点仿真） | CANoe LIN 通道 + Vector VN16xx 接口盒；或真实 BCM 主节点 | 配置 LIN 通道波特率、导入 LDF、选择主/从角色 |
| UDP 帧流（JSON） | CANoe Trace 窗口 / LIN 分析仪抓包 | 看 PID/数据/校验和、过滤 ID、分析周期 |
| `simulators/ldf/vehicle.ldf` | 标准 LDF（Vector LDF Explorer / CANoe 打开） | 读 signal 位布局、缩放、枚举、调度表 |
| `libraries/LinLibrary.py` 关键字 | vTESTstudio/CANoe 的测试脚本、ecu.test 测试步骤 | 用工具 API 发主请求/读帧/断言 |
| `Inject LIN Error` | 台架故障注入盒 / 线束改线 / 从机断电 | 注入校验错、从机掉线、信号短路 |
| 睡眠/唤醒用例 | 电源管理测试（暗电流、唤醒时间） | 配合电源程控、电流钳、总线抓包一起判 |

**真实 LIN 测试的典型顺序**（拿到一个车门/座椅 LIN 模块后）：
1. 要输入：**LDF + 调度表 + 信号矩阵 + 需求规范**；
2. 台架接线：LIN 通道接 BCM 主节点或仿真主节点，电源程控；
3. 冒烟：调度表时序、每个报文周期、信号默认值；
4. 功能：信号矩阵（每个信号边界/枚举/多信号帧）；
5. 协议：PID/两种校验和、主请求帧、诊断帧；
6. 睡眠唤醒：静默、唤醒时间、误唤醒、反复睡醒；
7. 容错：从机掉线、校验和错误、信号超范围、总线短路（台架故障注入）。

面试可以这样讲："我做过 LIN 模块的完整验证：读 LDF 解信号、用 CANoe 看调度周期和抖动、手算并验证经典/增强两种校验和、做过睡眠唤醒和从机无响应容错，并且用错误注入证明断言是有效的。"

---

## 23.9 扩展练习（自己动手）

1. **加一个从节点**：给调度表加 0x13 车窗位置（2 字节，含 2 个信号）。需要改哪些文件？提示：`simulators/lin_sim.py` 的 `FRAME_LENGTH`/`SIGNAL_DEFS`/`SCHEDULE_IDS`、`simulators/ldf/vehicle.ldf`、`libraries/LinLibrary.py` 的 `SCHEDULE_IDS`（时序分析按 4 个报文算周期），以及套件里断言 `frames >= 160` 的期望值。
2. **收紧时基**：把槽位从 10ms 改成 5ms（周期变 20ms），哪些断言会挂？为什么"采样帧数/周期容差"要跟着改？（理解"测试参数化"的必要性）
3. **跨帧联动用例**：写"雨量大 → 雨刷应切高速"：`Set LIN Slave Signal 0x10 rain_intensity=90` + `Set LIN Slave Signal 0x11 wiper_state=HIGH`，然后断言两个帧里的信号满足联动关系（参考 07 章用例 3 的"信号一致性"思路）。
4. **从机丢失报警**：注入 `no_response` 让 0x10 连续丢 20 帧，先扩展仿真器加"从机丢失计数"，再写用例断言主节点应报故障——体会"先有需求/机制，再有测试"。
5. **Trace 归档**：用 `Get LIN Schedule Statistics` 把统计写进 RF 日志（`Log` 关键字），失败时当证据——对应 CAN 模块的 `Export Bus Trace` 思路。
6. **接进 CI**：理解为什么新增套件要被 Lead 加进 `run_tests.py` 全量回归（本模块独立端口、独立进程，天然可并行）。

---

## 23.10 本章自检

1. LIN 和 CAN 的三个核心区别是什么？各自典型场景？
2. 一帧 LIN 报文由哪 5 段组成？哪一段是从机"识别自己"的依据？
3. 手算：帧 ID `0x12` 的 PID 是多少？`0x3C` 主请求帧为什么必须用经典校验和？
4. `DATA=96`（雨量 75%）、PID=0x50 的增强校验和是多少？写出回卷步骤。
5. LIN 睡眠命令是什么？睡眠后总线应满足什么条件？如何验证"唤醒真的生效"？
6. 为什么测试端要**独立实现**校验和算法，而不是调用仿真器的函数？
7. `Run Keyword And Expect Error` 在 LIN 套件里解决了什么问题？举一个恒真断言的例子。
8. 从机无响应（no_response）时，主节点/测试端应该看到什么？为什么"超时时间"本身也要断言？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | ①**线束**：LIN 单线 / CAN 双线差分；②**拓扑**：LIN 单主多从（主机轮询）/ CAN 多主（位仲裁）；③**速率**：LIN ≤20kbps / CAN 500kbps 或 250kbps。场景：LIN=车门/座椅/雨刷/后视镜，CAN=动力/底盘/安全/诊断 |
| 2 | **BREAK + SYNC(0x55) + PID + DATA + CHECKSUM**。从机靠 **PID**（6bit ID + 2bit 奇偶）判断"这帧是不是给我的、ID 可不可信" |
| 3 | 0x12 = `010010`：P0 = 0^1^0^1=0，P1 = !(1^0^1^0)=1 → **PID=0x92**。`0x3C` 是 **LIN 2.x 规定的主请求帧，必须用经典校验和**（只累加数据字节）；其余帧用增强校验和（PID+数据） |
| 4 | 0x50 + 0x96 = 0xE6，未超 0xFF 无需回卷，取反 → **0x19**。若和超过 0xFF（如 0x18A → 0x8A+0x01=0x8B），先回卷再取反 → 0x74 |
| 5 | 睡眠命令 = **0x3C 主请求帧 byte0=0x00**；之后调度停止、总线静默（一帧都不许有）。验证唤醒生效：**先断言静默、再发唤醒脉冲、再断言帧恢复**（并可用事件 `wakeup_pulse` 佐证） |
| 6 | 防止**恒真断言**：若测试端直接复用被测实现的算法，实现错了两边一起错，断言永远通过。独立实现 + 错误注入，才能证明"检查是真的在检查" |
| 7 | 用于负向验证：证明"坏帧/错误注入时校验关键字确实会失败"。恒真断言例子：`LIN Checksum Should Be Valid` 实现成直接 `return frame`（不检查），正向用例照样全绿 |
| 8 | 从机不响应时，`Wait For LIN Frame` 应在 timeout 附近报"未等到帧"，且**只影响目标帧**（其他帧照常）。超时时间本身是需求（如下界不能立即返回、上界不能挂死），所以用例 10 同时断言 `>= 0.38s` 和 `<= 1.5s` |

---

**上一章** ← [22 · 车载自动化测试全景](22_车载自动化测试全景与能力地图.md)　|　**下一章** → [24 · 车载以太网与 DoIP 诊断](24_车载以太网与DoIP诊断.md)
（扩展篇导航：22 全景 → 23 LIN → 24 车载以太网/DoIP → 25 HMI 视觉 → Wave2 章节持续补充）
