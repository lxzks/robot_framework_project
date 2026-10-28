# 38 · HIL 台架与电源/IO 验证：让"真 ECU"跑起来

> **前面 37 章都是纯软件仿真**：ECU 是 Python 进程、CAN 是 UDP 组播、车机是模拟器。
> 这套东西能把"测试逻辑/脚本/追溯"练到能上岗，但有一整类问题它永远测不到 ——
> **真 ECU 在真实电气条件下的行为**：上电顺序错了会不会不自检？欠压 9V 时 DTC 怎么记？
> 继电器断开真断电后能不能恢复？这些必须上 **HIL（Hardware-in-the-Loop）台架**。
>
> 本章新增「**HIL 台架抽象 + 电源/IO 验证**」模块：
>
> ```bash
> # 模块四件套（全部新增，不改动任何原有文件）
> simulators/hil_rack.py               # 模拟 HIL 台架 IO 板：KL30/KL15 电源 + DI/DO/继电器 + 故障注入
> libraries/HilLibrary.py              # 23 个关键字 + 后端抽象（HIL_BACKEND=sim|real）
> tests/robot/20_hil_rack.robot        # 10 条用例（10/10 全绿，连跑两次稳定）
> docs/38_HIL台架与电源IO验证.md        # 本章
>
> # 一条命令跑本章套件（约 2 秒，不需要 ECU/虚拟 CAN/真实硬件）
> .\.venv\Scripts\python.exe -m robot --outputdir results/dev_hil tests\robot\20_hil_rack.robot
> ```
>
> **本轮实测**：10/10 ×2；上电时序 **KL30_ON → 100.3ms → KL15_ON**；默认 12.6V、欠压 9.0V、
> 过压 16.0V、掉电 0.0V 全部读回一致；Power Cycle（断电 200ms）后恢复 12.6V 且瞬态状态清空；
> 6.5A 过流标记置位/回落清除；`HIL_BACKEND=real` 明确报 **"需接真实台架"**（替换点验证）。
>
> 需求覆盖：`REQ-HIL-001` 上下电时序、`REQ-HIL-002` 电压控制与故障注入、
> `REQ-HIL-003` IO/继电器控制、`REQ-HIL-004` 后端抽象与真实台架替换点。

---

## 📖 零基础读法建议（先看这个）

| 优先级 | 小节 | 说明 |
|---|---|---|
| 🟢 **必读** | 38.1 / 38.2 | HIL 是什么、台架由什么组成、IO 映射表是什么 —— 面试必问 |
| 🟢 **必读** | 38.3 | **后端抽象**：为什么测试脚本不能绑死台架 + `sim|real` 替换点（本章最有价值的设计） |
| 🟢 **必读** | 38.4 | 电源管理测试：KL30/KL15、上下电时序、欠压/过压/掉电/抖动 |
| 🟢 **必读** | 38.5 / 38.6 | DI/DO/继电器与过流保护 —— HIL 最常用的 IO 能力 |
| 🟢 **必读** | 38.9 | 10 条用例逐条精讲，对着 `20_hil_rack.robot` 看 |
| 🟡 了解 | 38.7 / 38.8 | 本项目实现细节与实测数据 |
| 🟡 了解 | 38.10 | **怎么换到真台架**（实现 RealBackend 的步骤与代码框架）—— 入职后最实用 |
| 🟡 了解 | 38.11 | 与纯软件仿真（Wave1/2）的边界：什么该在 SIL 测、什么必须 HIL |
| ⚪ 可跳过 | 38.4.5 暗电流 | 真台架才做得了，先知道概念即可 |

**动手顺序**：先跑命令看 10/10 全绿 → 打开 `results/hil_rack_stdout.log` 看台架日志
（上电时序/故障注入/Power Cycle）→ 把 `20_hil_rack.robot` 里 `HIL_BACKEND` 那段改成
`real` 后不恢复，再跑一次看台架直接拒绝启动（体验"没接台架就红灯"）→ 最后做 38.12 自查题。

---

## 38.1 HIL 是什么：真 ECU + 仿真其余

### 38.1.1 MIL / SIL / HIL / 实车：测试金字塔的四层

| 层级 | 被测对象 | 其余部分 | 能测什么 | 成本/速度 |
|---|---|---|---|---|
| **MIL**（Model-in-the-Loop） | 控制算法模型（Simulink 等） | 全仿真 | 算法逻辑、控制策略 | 最低，毫秒级 |
| **SIL**（Software-in-the-Loop） | 真实**软件**（编译后的代码） | 全仿真（虚拟 ECU/总线） | 软件功能、协议、诊断 | 低，本项目前 37 章属于这层 |
| **HIL**（Hardware-in-the-Loop） | 真实 **ECU 硬件**（含软件） | 台架仿真传感器/执行器/总线/电源 | 电气行为、真实时序、故障注入、电源管理 | 中高，台架建设成本高 |
| **实车**（Vehicle） | 整车 | 真实世界 | EMC、NVH、路试、极端环境 | 最高，周期长 |

> **一句话**：SIL 测"软件写得对不对"，HIL 测"装到真硬件上还对不对"，实车测"到真实世界里对不对"。
> 三者是**层层收窄**的关系，不是替代关系 —— 业界不会因为有了 HIL 就不做 SIL（HIL 台架稀缺且慢），
> 也不会只做 SIL 就敢交付（真硬件问题 SIL 根本发现不了）。

### 38.1.2 纯软件仿真（本项目 Wave1/2）测不到什么

| 问题类型 | 例子 | SIL 能测吗 | HIL 怎么测 |
|---|---|---|---|
| **真实电气时序** | 上电后 ECU 多久发第一帧 CAN、KL15 信号抖动 | ❌（Python 进程无上电过程） | 程控电源 + 硬件时间戳 |
| **电源异常** | 欠压 9V 时 ECU 复位/DTC P0562、过压 16V 保护 | ❌ | 程控电源注入电压 |
| **真断电** | 继电器断开 KL30 后 ECU 冷启动行为 | ❌ | 台架继电器/电源通断 |
| **硬线 IO** | 车门开关、安全带、刹车信号给 ECU 的电气激励 | ❌（只有 CAN 信号） | IO 板卡硬线输出 |
| **执行器驱动** | ECU 驱动继电器/指示灯/风扇的实际输出 | ❌ | IO 板读回/负载箱 |
| **传感器故障** | 传感器断路/短路/信号漂移 | ❌ | 故障注入单元（FIU） |
| **电流/暗电流** | 休眠电流、过流保护、电源纹波 | ❌ | 程控电源/电流钳 |

### 38.1.3 HIL 台架由什么组成

```
┌────────────────────────── HIL 台架 ──────────────────────────┐
│  实时仿真机（dSPACE SCALEXIO / Vector VT System / NI PXI）    │
│    ├─ 电源程控模块（KL30 0~18V、KL15 开关、电流测量）          │
│    ├─ 数字 IO 板（DI 激励 / DO 读回）                         │
│    ├─ 继电器/负载箱（ECU 供电通断、执行器负载）                 │
│    ├─ 故障注入单元（断路/短路/信号错误）                        │
│    └─ CAN/LIN/以太网接口卡（总线通信 + 硬件时间戳）             │
│            │ 线束（针脚定义 = IO 映射表）                     │
│            ▼                                                │
│        真实 ECU（被测对象）                                   │
└─────────────────────────────────────────────────────────────┘
```

真实项目常见设备：

| 厂商/产品 | 用途 |
|---|---|
| **dSPACE SCALEXIO / MicroAutoBox** | 实时仿真机（运行车辆模型、IO 板卡扩展） |
| **Vector VT System**（VT7001 电源、VT2811 通用 IO、VT1004 负载） | 模块化 HIL IO 硬件；CANoe 原生集成 |
| NI PXI / LabVIEW | 通用测试测量平台（自研台架常用） |
| 自研 IO 板 + 程控电源（SCPI） | 成本低、定制灵活，中小团队常见 |
| ECU-TEST / CANoe / 自研 Python 框架 | 台架自动化软件层 |

> **面试话术**："HIL 台架 = 真 ECU + 实时仿真机 + 程控电源 + IO 板 + 故障注入；
> 测试软件通过台架 API 控制引脚电平和电源电压，从而在实验室里复现实车电气工况。"

---

## 38.2 台架组成与 IO 映射表

### 38.2.1 从测试脚本到 ECU 针脚：信号链

```
RF 用例（Power On Sequence / Set Digital Input DOOR_OPEN true）
   → HilLibrary（语义化关键字，台架无关）
      → HilBackend.command({"cmd":"power_on"})       ← 后端抽象层（本章重点）
         → TCP/厂商 API（仿真=本地 TCP；真台架=VT7001/SCALEXIO API）
            → IO 板卡通道（VT7001 ch1 输出 12.6V；VT2811 ch3 输出 0V→12V）
               → 线束（ECU 针脚 A3 = KL30，B7 = 车门开关）
                  → 真 ECU
```

### 38.2.2 IO 映射表：台架工程的"单一事实源"

测试脚本里写 `Set Digital Input    DOOR_OPEN    True`，台架怎么知道是哪个板卡通道？
靠 **IO 映射表（IO map / pin mapping）** —— 它是台架工程的配置管理核心：

| 逻辑名（用例用） | 方向 | 仿真台架 | 真实台架（示例） | 线束针脚 | 说明 |
|---|---|---|---|---|---|
| `KL30` | 电源输出 | `kl30.voltage` | VT7001 ch1 | A3 | 常电 12.6V / 0~18V |
| `KL15` | 电源输出 | `kl15` | VT7001 ch2（或 VT2811 继电器） | A4 | 点火 ON/OFF |
| `DOOR_OPEN` | DI（台架→ECU） | `di.DOOR_OPEN` | VT2811 ch1（低有效） | B7 | 车门开关 |
| `SEATBELT_FASTENED` | DI | `di.SEATBELT_FASTENED` | VT2811 ch2 | B8 | 安全带扣 |
| `BRAKE_PRESSED` | DI | `di.BRAKE_PRESSED` | VT2811 ch3 | B9 | 刹车开关 |
| `GEAR_P` | DI | `di.GEAR_P` | VT2811 ch4 | B10 | 档位 P |
| `MAIN_RELAY` | DO（ECU→台架读回） | `do.MAIN_RELAY` | VT2811 ch5 输入 | C1 | 主继电器反馈 |
| `INDICATOR_LAMP` | DO | `do.INDICATOR_LAMP` | VT1004 负载 + 读回 | C2 | 指示灯 |
| `ECU_POWER` | 继电器（台架控制） | `relays.ECU_POWER` | VT7001 输出使能 | — | 断 ECU 供电做冷启动 |

> **为什么 IO 映射表这么重要**：同一份用例要在"台架 A（dSPACE）"和"台架 B（Vector）"上都能跑，
> 靠的就是"用例只用逻辑名，映射表在台架配置里换"。**把映射表固化成配置（YAML/Excel/ODX 变体）
> 并纳入版本管理**，是 HIL 工程化的第一课。

### 38.2.3 本项目怎么模拟台架

`simulators/hil_rack.py` 用一个独立进程实现上面整张表的**状态语义**（电压/DI/DO/继电器/电流），
测试端通过 TCP JSON 协议操作它：

```
               ┌──────────────── 测试进程（robot）────────────────┐
               │ tests/robot/20_hil_rack.robot                    │
               │      → libraries/HilLibrary.py（后端抽象）        │
               └───────────────┬─────────────────────────────────┘
                               │ HIL_BACKEND=sim → TCP 127.0.0.1:9090 JSON 行
                               ▼
               ┌──────────── 独立进程 simulators/hil_rack.py ────────────┐
               │ RackState：KL30 电压 / KL15 / 4 DI / 4 DO / 2 继电器 /   │
               │            电流与过流 / 故障类型 / 上电时序记录 / 统计    │
               └────────────────────────────────────────────────────────┘
```

**边界要讲清**：它模拟的是"台架 IO 的语义"，不是真实电气特性（没有纹波、上升沿、线束阻抗）。
所以本章能做的是"**验证测试逻辑与后端抽象**"，真台架上的电气验证需要真硬件（38.11）。

---

## 38.3 后端抽象：测试脚本不能绑死台架（本章核心设计）

### 38.3.1 反例：把台架 API 写进用例

```python
# ❌ 错误：用例直接调用某家台架的 API
vt7001.set_voltage(channel=1, volts=12.6)      # Vector VT 专有
ds.scalexio.set_io("A3", 12.6)                 # dSPACE 专有
```

后果：换台架 → 所有用例重写；本地没台架 → 一条都跑不了；CI 里没有台架 → 测试全部 skip。
**测试脚本的可移植性和"本地可跑"就全没了。**

### 38.3.2 抽象接口：三个方法统一所有台架

本项目的 `HilBackend` 抽象（`libraries/HilLibrary.py`）：

```python
class HilBackend(ABC):
    name: str

    @abstractmethod
    def connect(self) -> None: ...          # 建立连接（拉起仿真进程 / 初始化台架 SDK）

    @abstractmethod
    def command(self, payload: dict, timeout: float = 5.0) -> dict: ...
                                            # 统一命令语义，返回统一状态快照

    @abstractmethod
    def close(self) -> None: ...            # 释放资源（停进程 / 关 SDK）
```

所有关键字（`Set Kl30 Voltage` / `Power On Sequence` / `Set Digital Input`……）只依赖这三个方法，
**不出现任何厂商 API**。后端由环境变量选择：

| `HIL_BACKEND` | 后端类 | 行为 |
|---|---|---|
| `sim`（默认） | `SimulatedBackend` | 拉起 `simulators/hil_rack.py`，TCP 9090 JSON |
| `real` | `RealBackend` | **明确报错"real 后端未实现：需接真实台架"** + 接入指引 |

### 38.3.3 为什么 real 后端必须"明确报错"而不是假装通过

这是 HIL 测试最容易犯的错：本地没台架时用 mock 数据"跑通"，到台架上全红；
更糟的是 **mock 数据让测试显示绿色，给出虚假的安全感**。

本项目的选择：

```python
class RealBackend(HilBackend):
    def connect(self):
        raise AssertionError(
            "real 后端未实现：需接真实台架（dSPACE / Vector VT System / 自研 IO 板）。"
            "替换点：实现 RealBackend.command() ...")
```

> 用例 10 用 `Run Keyword And Expect Error    *需接真实台架*    Start HIL Rack` 把这个行为**锁死**：
> 只要有人把 real 后端改成"静默假通过"，这条用例立刻变红。**"没接台架就红灯"是 HIL 的诚实性底线。**

### 38.3.4 替换点：接真台架时只写这一个类

```python
class RealBackend(HilBackend):
    """接 dSPACE / Vector VT System / 自研 IO 板（示例骨架，需按实际 API 实现）"""
    name = "real"

    def connect(self):
        endpoint = os.environ["HIL_RACK_ENDPOINT"]      # 台架地址/配置文件
        self._psu = ScpiPowerSupply(endpoint)            # 程控电源（SCPI/USBTMC）
        self._io = VtSystem(endpoint)                    # Vector VT / dSPACE IO
        # 或：self._canoe = win32com.client.Dispatch("CANoe.Application")

    def command(self, payload):
        cmd = payload["cmd"]
        if cmd == "set_kl30":
            self._psu.set_voltage(payload["voltage"])
        elif cmd == "power_on":
            self._psu.set_voltage(12.6); self._io.set_relay("KL15", False)
            time.sleep(payload.get("delay_ms", 100) / 1000)
            self._io.set_relay("KL15", True)
        elif cmd == "set_di":
            self._io.set_pin(payload["name"], payload["value"])
        elif cmd == "set_do":
            self._io.set_output(payload["name"], payload["value"])
        elif cmd == "power_cycle":
            self._io.set_relay("ECU_POWER", False); time.sleep(payload["off_ms"] / 1000)
            self._io.set_relay("ECU_POWER", True)
        elif cmd == "status":
            return {"ok": True, "state": self._snapshot()}
        else:
            raise NotImplementedError(f"真实后端未映射命令 {cmd}")
        return {"ok": True, "state": self._snapshot()}

    def _snapshot(self):
        """按统一状态结构返回（与仿真后端完全一致，用例无需改动）"""
        return {"ok": True, "kl30": {...}, "kl15": ..., "di": {...}, "do": {...},
                "relays": {...}, "current": {...}, "power": {...}, "stats": {...}}
```

> **关键**：**统一的是"语义"和"状态结构"，不是"实现"**。用例断言 `Rack State Should Contain
> di.DOOR_OPEN True` 在仿真/真台架上含义完全一样，切台架时零改动。

### 38.3.5 抽象层设计的四个原则

| 原则 | 本项目的做法 | 违反的后果 |
|---|---|---|
| **语义统一** | 命令是"设电压/上电/读 DI"而不是"写寄存器" | 换台架就要重写用例 |
| **状态结构统一** | `status` 返回同一份 dict（电压/IO/故障/统计） | 断言关键字要按台架分叉 |
| **能力显式降级** | real 未实现 → 明确报错 + 接入指引 | 假通过，掩盖环境问题 |
| **单一替换点** | 只换 `RealBackend.command()` | 改动扩散到所有关键字 |

---

## 38.4 电源管理测试

### 38.4.1 KL 端子号：车上的"电源命名法"

| 端子 | 含义 | 本项目 | 典型用途 |
|---|---|---|---|
| **KL30** | 常电（蓄电池直连，不受点火开关控制） | 默认 12.6V，可调 0~18V | ECU 常供电、记忆、唤醒 |
| **KL15** | 点火开关 ON 档 | ON/OFF 布尔 | ECU 工作、仪表点亮、大部分功能 |
| **KL31** | 地（GND） | 隐含（台架共地） | 回路 |
| KL50 | 起动机 | 未覆盖 | 冷启动电压跌落 |
| KL61 / KL61 | 发电机充电指示 | 未覆盖 | 充电系统 |

> **12.6V 从哪来**：12V 蓄电池满电静止电压约 12.6~12.8V；发动机运行时发电机把系统电压
> 抬到 13.8~14.4V；起动机瞬间可能跌到 9~10V（这就是"欠压 9V"测试的来源）。

### 38.4.2 上下电时序：为什么"顺序"是需求

**上电：KL30 先 → KL15 后**（本项目实测间隔 100ms）

- ECU 需要 KL30 先建立供电、完成上电自检/初始化/唤醒，才能响应 KL15；
- 顺序反了（KL15 先）在真实 ECU 上可能出现：不进入工作模式、初始化失败、误记 DTC、
  甚至反复复位 —— **这种问题在纯软件仿真里根本不存在**。

**下电：KL15 先 → KL30 后**

- 先关点火让 ECU 有机会"收尾"（保存数据、进入休眠流程），再断常电；
- 反了（直接断 KL30）可能：数据丢失、休眠流程被打断、异常 DTC。

**怎么测时序**（本项目做法）：台架记录每一步的**相对时间戳**，用例断言：

```robotframework
${state}=    Power On Sequence    delay_ms=100
${log}=    Set Variable    ${state}[power][last_sequence]
Should Be Equal    ${log}[0][step]    KL30_ON
Should Be Equal    ${log}[1][step]    KL15_ON
${offset}=    Set Variable    ${log}[1][offset_ms]
Should Be True    ${offset} >= 60          # 间隔必须真实存在（> 设定值的 60%）
```

> **只断言最终状态（12.6V + KL15 ON）是不够的**：先 KL15 后 KL30 最后也能达到同样终态，
> 但过程是错的。**时序类需求必须验"过程"** —— 这和 26 章"性能看 p95 不看平均"、
> 32 章"时序需求验变化"是同一个方法论：**断言要打在被测行为的关键维度上**。

真实台架上的时序验证还会加：电源电压曲线（示波器/程控电源回读）、ECU 首帧 CAN 时间戳、
KL15 硬线电平跳变时刻 —— 精度 μs~ms 级，比软件时间戳可靠得多。

### 38.4.3 欠压 / 过压 / 掉电：三类电源故障

| 注入 | 本项目 | 真实 ECU 的典型反应 | 测试关注点 |
|---|---|---|---|
| **欠压 9V** | `Inject Under Voltage` | 记 DTC P0562（系统电压低）、关大功率负载、可能复位 | 是否降级而非崩溃、DTC 是否正确、恢复后功能是否正常 |
| **过压 16V** | `Inject Over Voltage` | 记 P0563（系统电压高）、保护性关断 | 保护是否及时、恢复后是否可恢复 |
| **掉电 0V** | `Inject Power Loss` | 完全断电/复位 | 数据保持、冷启动行为、DTC 存储 |
| **电源抖动** | `Inject Power Glitch`（PWM 掉电脉冲串） | 继电器弹跳/线束虚接导致反复掉电上电 | 是否误动作/反复复位、能否稳定恢复 |

> **判据不是"电压等于 9V"**（那是台架自己的能力），而是"**ECU 在 9V 下的行为是否符合需求**"。
> 本项目没有真 ECU，所以只验证台架施加/读回；ECU 侧验证是 HIL 台架上真 ECU 的任务（38.11 边界）。

### 38.4.4 电源抖动：PWM 式掉电

真实场景：线束虚接、继电器触点氧化、接触器弹跳 → 电压瞬间跌到 0 又恢复，反复多次。
台架用程控电源按波形输出（或继电器高频通断）模拟。本项目参数：

| 参数 | 含义 | 实测 |
|---|---|---|
| `pulses` | 掉电脉冲个数 | 5 |
| `duration_ms` | 每个脉冲掉电时长 | 120ms |
| `interval_ms` | 脉冲之间恢复时长 | 20ms |
| `total_ms` | 总时长（pulses×(duration+interval)） | **700ms**（5×140） |
| `dip_voltage` / `restored_voltage` | 跌落值/恢复值 | 0.0V / 12.6V |

### 38.4.5 暗电流（静态电流）测试：知道概念即可

车停一晚第二天打不着火 → 静态电流超标（某 ECU 没休眠，几十 mA 持续放电）。
真实项目做法：程控电源测 **mA 级电流**、等 ECU 进入休眠（可能几十秒~几分钟）、记录电流曲线；
判据：整车的暗电流预算（如 < 20mA）与各 ECU 的分配。**本仿真没有真实休眠电流，不覆盖**，
但"KL30 常电 + KL15 OFF + 总线静默 → 节点休眠"的行为在 [scenarios/12 电源管理](19_十六个场景脚本实战.md)
里用虚拟总线做过（录制总线零帧），两者配合理解。

---

## 38.5 DI / DO / 继电器

### 38.5.1 DI：给 ECU 造硬线激励

| DI 通道 | 模拟的车上信号 |  ECU 收到后可能的行为 |
|---|---|---|
| `DOOR_OPEN` | 车门开关 | 仪表车门提示、迎宾灯、网络管理唤醒 |
| `SEATBELT_FASTENED` | 安全带扣开关 | 安全带未系警告（法规项） |
| `BRAKE_PRESSED` | 刹车开关 | 刹车灯、换挡允许条件、定速巡航取消 |
| `GEAR_P` | 档位 P | 启动允许、驻车控制 |

真实台架用 IO 板卡输出高低电平（注意**有效电平/上拉/下拉/共地**）；本仿真用布尔状态模拟，
语义一致。

### 38.5.2 DO：读回 ECU 的执行器输出

DO 在台架上通常是"**ECU 驱动、台架读回**"：ECU 拉动继电器/点亮指示灯，台架测它的输出。
用例验证"ECU 应该动作时真的动作了"：

```robotframework
Set Digital Output    INDICATOR_LAMP    True     # 本仿真里测试端直接驱动（模拟读回路径）
${lamp}=    Get Digital Output    INDICATOR_LAMP
Should Be True    ${lamp}
```

真实台架注意：DO 读回要匹配 ECU 的输出电气特性（高边/低边驱动、12V/5V），必要时加负载箱
（如灯泡负载）否则开路检测会误报。

### 38.5.3 继电器：ECU 供电通断 = 硬件断电重启

`ECU_POWER` 继电器断开 → ECU 完全失电 → 再闭合 = **冷启动**。
这是软件复位（0x11）模拟不了的：RAM 丢失、外设重新初始化、继电器/执行器复位、DTC 重新计算。
很多"只在冷启动出现"的问题（如初始化顺序、NVM 读写）只能靠它复现。

本项目的 `Power Cycle`：

```
断开 ECU_POWER 继电器 → 等 off_ms（默认 300ms）→ 恢复 12.6V、KL15 OFF、清空瞬态状态
```

**注意断言"状态清空"**：断电重启后 DO/DI/电流/故障标记都应回到默认（真实 ECU 也会重新初始化）——
如果不断言，可能把"上次遗留的故障标记"误认为"这是新故障"。

---

## 38.6 电流限制与过流保护

台架程控电源一般有**电流上限**（保护被测件和电源）。本项目教学值 **5A**：

| 场景 | 电流 | over_current | 累计事件 | 说明 |
|---|---|---|---|---|
| 默认 | 0.8A | False | 0 | 正常运行 |
| 过流 | 6.5A | **True** | 1 | 超过 5A 限值 |
| 回落 | 0.8A | False | 保持 1 | 标记清除、事件保留（故障记忆） |

真实台架的过流会**真的限流/断电**（保护 ECU 不被烧），所以过流测试要特别小心：
① 先确认台架限流值；② 故障注入时间要短；③ 确认 ECU 没被"过流断电"造成不可恢复损坏。

---

## 38.7 本项目实现

### 38.7.1 架构

```
tests/robot/20_hil_rack.robot     ← 10 条用例（语义化：上电/欠压/DI/DO/Power Cycle/后端）
        │
        ▼
libraries/HilLibrary.py           ← 23 个关键字 + HilBackend 抽象
        │  backend.command(payload)
        ├── SimulatedBackend ── TCP 127.0.0.1:9090 JSON 行 ──▶ simulators/hil_rack.py
        └── RealBackend ──▶ 明确报"需接真实台架"（替换点，见 38.3.4）
```

### 38.7.2 台架 JSON 协议（仿真后端）

| 命令 | 参数 | 作用 |
|---|---|---|
| `ping` / `status` | — | 就绪探测 / 完整状态 |
| `power_on` / `power_off` | `delay_ms` | 上下电时序（记录 step+offset） |
| `set_kl30` | `voltage`（0~18V） | 设定常电电压 |
| `inject` | `kind` = undervoltage/overvoltage/power_loss | 电源故障注入 |
| `power_cycle` | `off_ms` | 硬件断电重启 |
| `set_di` / `set_do` | `name` / `value` | DI 激励 / DO 输出 |
| `set_relay` | `name` / `state` | 继电器（ECU_POWER / LOAD_BANK） |
| `set_current` | `amps` | 负载电流（>5A 置过流标记） |
| `inject_power_glitch` | `pulses` / `duration_ms` / `interval_ms` | PWM 电源抖动 |
| `reset` / `quit` | — | 用例隔离复位 / 退出 |

### 38.7.3 关键字清单（25 个）

| 关键字 | 作用 | 返回 |
|---|---|---|
| `Start HIL Rack` / `Stop HIL Rack` | 启动/回收台架（按后端） | — |
| `Reset Rack` | 复位到默认状态（用例隔离） | — |
| `Power On Sequence` / `Power Off Sequence` | 执行并断言上下电时序 | 状态 dict |
| `Set Kl30 Voltage` / `Get Kl30 Voltage` / `Kl30 Voltage Should Be` | 电压设定/读取/断言 | float / — |
| `Inject Under Voltage` / `Inject Over Voltage` / `Inject Power Loss` | 电源故障注入 | 状态 dict |
| `Inject Power Glitch` | PWM 电源抖动 | 状态 dict |
| `Power Cycle` | 硬件断电重启 | 状态 dict |
| `Set Digital Input` / `Get Digital Input` | DI 激励/读取 | 状态 dict / bool |
| `Set Digital Output` / `Get Digital Output` | DO 写入/回读 | 状态 dict / bool |
| `Set Relay` | 继电器（ECU 供电通断） | 状态 dict |
| `Set Load Current` / `Over Current Should Be Flagged` | 电流设定 / 过流断言 | 状态 dict / — |
| `Read Rack State` / `Rack State Should Contain` | 读完整状态 / 断言字段（点号路径） | dict / — |
| `Backend Should Be` | 后端类型断言（sim/real 替换点） | — |

### 38.7.4 进程/端口/隔离

- 台架进程用 `sys.executable` 拉起（不硬编码解释器路径），stdout → `results/hil_rack_stdout.log`；
- 端口 `127.0.0.1:9090`（`HIL_PORT` / `--port` 可配）；启动失败/端口占用给出可操作报错；
- `Stop HIL Rack`：quit → 等 3s → terminate → kill，**回收干净**（端口不残留）；
- 启动就绪用 `ping` 轮询 + 进程存活检查（不是 sleep 猜时间）；
- `Test Setup    Reset Rack` 保证用例隔离（台架有状态：电压/IO/故障/计数器）；
- 台架状态里的 `stats`（上电次数/Power Cycle 次数/过流事件）也全部复位。

### 38.7.5 教学简化点（与真实台架差异）

| 项目 | 本仿真 | 真实 HIL 台架 |
|---|---|---|
| 电气特性 | 只有电压数值，无纹波/上升沿/阻抗 | 真实电源波形、线束电气特性 |
| 时间精度 | Python `time.monotonic`（ms 级） | 硬件时间戳（μs 级） |
| DI/DO | 布尔状态 | 真实电平（高低有效、上拉/下拉、共地） |
| 故障注入 | 电压/抖动/过流标记 | + 断路/短路/传感器信号错误（FIU） |
| 被测对象 | 无真 ECU（只验证台架语义） | 真 ECU（本章验证的是"台架抽象"这一层） |
| 暗电流 | 不支持 | 程控电源测 mA 级静态电流 |

---

## 38.8 一轮实测数据（连跑两次稳定）

> 数据来源：`.\.venv\Scripts\python.exe -m robot --outputdir results/dev_hil tests\robot\20_hil_rack.robot`

| 指标 | 实测 | 说明 |
|---|---|---|
| 用例数/通过数 | **10 / 10**（连跑两次均全绿） | 无 flaky |
| 套件总耗时 | **1.60s / 1.60s**（墙钟 2.1s / 2.0s） | 主要是 Power Cycle 的 200ms 断电 |
| 上电时序 KL30→KL15 间隔 | **100.3ms / 100.1ms**（设定 100ms） | 时序真实存在 |
| 默认 / 欠压 / 过压 / 掉电电压 | **12.6 / 9.0 / 16.0 / 0.0 V** | 读回一致 |
| 电源抖动记录 | 5 脉冲 × (120ms 掉电 + 20ms 恢复)，总 **700ms** | 恢复后 12.6V |
| Power Cycle（断电 200ms） | 恢复 **12.6V**、KL15=False、DO/DI/电流/故障清空 | 累计 1 次 |
| 过流验证 | **6.5A → over_current=True**；回落 0.8A → False | 事件计数 ≥1 |
| real 后端 | 明确报 **"需接真实台架"**（`Backend Should Be` 与 `Start HIL Rack` 均拒绝） | 替换点验证 |
| 非法 `HIL_BACKEND` | 报 **"HIL_BACKEND 只支持 sim\|real"** | 配置健壮性 |

单条用例耗时（秒）：`0.11 / 0.005 / 0.007 / 0.007 / 0.009 / 0.419 / 0.009 / 0.013 / 0.008 / 0.010`
（第 6 条含 2×100ms 时序 + 200ms Power Cycle 断电，是唯一 >0.1s 的用例）。

---

## 38.9 tests/robot/20 套件：10 条用例逐条讲

| # | 用例 | 需求 | 核心断言 |
|---|---|---|---|
| ① | 上电时序：KL30 应先于 KL15（含间隔时间校验） | 001 | 顺序 + 间隔 ≥60ms + 终态 |
| ② | 默认 KL30 电压应为 12.6V | 002 | 12.6V / 无故障 |
| ③ | 欠压注入：KL30 应降到 9V 并可读回 | 002 | 9.0V + UNDERVOLTAGE |
| ④ | 过压注入：KL30 应升到 16V，恢复后故障清除 | 002 | 16.0V + 恢复清标记 |
| ⑤ | 掉电注入与 PWM 电源抖动 | 002 | 0V + 抖动波形记录 |
| ⑥ | 下电时序与 Power Cycle：恢复 12.6V 且状态清空 | 001/003 | KL15→KL30 + 断电重启清空 |
| ⑦ | DI 激励：设置后状态应可见 | 003 | 4 路 DI 写读一致 |
| ⑧ | DO 与继电器控制：写入回读一致且影响 ECU 供电标记 | 003 | DO 回读 + ecu_powered |
| ⑨ | 电流限制模拟：超过 5A 应置过流标记 | 002 | 6.5A 置位 / 回落清除 |
| ⑩ | Backend 抽象：HIL_BACKEND=real 应明确报"需接真实台架" | 004 | 三种后端场景 |

### 38.9.1 用例 1：上电时序（顺序 + 间隔）

```robotframework
${state}=    Power On Sequence    delay_ms=100
${log}=    Set Variable    ${state}[power][last_sequence]
Should Be Equal    ${log}[0][step]    KL30_ON
Should Be Equal    ${log}[1][step]    KL15_ON
${offset}=    Set Variable    ${log}[1][offset_ms]
Should Be True    ${offset} >= 60
Rack State Should Contain    kl15    True
Rack State Should Contain    kl30.voltage    12.6
```

**测什么**：台架记录的上电步骤必须是 `KL30_ON → KL15_ON`，且两步间隔真实存在（≥60ms，实测 100.3ms），
终态 12.6V + KL15 ON。**能抓的缺陷**：时序写反（先 KL15）、间隔被写成 0（没有真正等待）——
这两类问题只断言终态是发现不了的。

### 38.9.2 用例 2：默认电压

```robotframework
${voltage}=    Get Kl30 Voltage
Kl30 Voltage Should Be    12.6
Rack State Should Contain    power.fault_type    NONE
```

**测什么**：台架复位后默认 KL30=12.6V、无电源故障。这是所有电源测试的**基准点**。

### 38.9.3 用例 3 / 4：欠压 9V、过压 16V

```robotframework
${state}=    Inject Under Voltage
Kl30 Voltage Should Be    9.0
Rack State Should Contain    power.fault_type    UNDERVOLTAGE
Rack State Should Contain    power.fault_active    True
```

**测什么**：注入后电压读回一致 + 故障类型/激活标记正确；过压用例还验证"恢复 12.6V 后
故障标记清除"（故障可恢复、不残留）。**能抓的缺陷**：注入没生效、故障标记不清除（永远报故障）。

### 38.9.4 用例 5：掉电 + 电源抖动

```robotframework
Inject Power Loss
Kl30 Voltage Should Be    0.0
Rack State Should Contain    power.fault_type    POWER_LOSS
Set Kl30 Voltage    12.6
${glitch}=    Inject Power Glitch    pulses=5    duration_ms=120    interval_ms=20
Rack State Should Contain    glitch.count    1
Kl30 Voltage Should Be    12.6
```

**测什么**：掉电到 0V 且 ECU 未供电标记为 False；恢复后再注入 PWM 抖动，台架记录 5 脉冲、
总 700ms 波形，抖动结束后电压回到 12.6V。**真实价值**：电压抖动能复现"接触器弹跳/线束虚接"
导致的偶发复位，这类问题在稳定电源下永远不复现。

### 38.9.5 用例 6：下电时序 + Power Cycle

```robotframework
${off}=    Power Off Sequence    delay_ms=100
${offlog}=    Set Variable    ${off}[power][last_sequence]
Should Be Equal    ${offlog}[0][step]    KL15_OFF
Should Be Equal    ${offlog}[1][step]    KL30_OFF
# 制造瞬态状态 → 硬件断电重启 → 验证恢复 + 状态清空
Power On Sequence
Set Digital Output    MAIN_RELAY    True
Set Digital Input    DOOR_OPEN    True
Set Load Current    6.5
${state}=    Power Cycle    off_ms=200
Kl30 Voltage Should Be    12.6
Rack State Should Contain    kl15    False
Rack State Should Contain    do.MAIN_RELAY    False
Rack State Should Contain    di.DOOR_OPEN    False
Rack State Should Contain    current.over_current    False
```

**测什么**：① 下电顺序 KL15→KL30（反了会有电压跌落/丢帧问题）；② Power Cycle 后恢复 12.6V、
KL15 OFF、DO/DI/电流/故障全部清空（"真断电"与软复位的本质区别）。

### 38.9.6 用例 7 / 8：DI 与 DO/继电器

```robotframework
Set Digital Input    DOOR_OPEN    True
Rack State Should Contain    di.DOOR_OPEN    True

Set Digital Output    INDICATOR_LAMP    True
${lamp}=    Get Digital Output    INDICATOR_LAMP
Should Be True    ${lamp}
Set Relay    ECU_POWER    False
Rack State Should Contain    power.ecu_powered    False
```

**测什么**：DI 激励写读一致；DO 写入回读一致；ECU_POWER 继电器断开后 `ecu_powered=False`
（模拟硬件断电）、恢复后 True。**能抓的缺陷**：IO 映射接错通道、输出回读链路断裂、继电器控制失效。

### 38.9.7 用例 9：过流保护

```robotframework
Over Current Should Be Flagged    False
${state}=    Set Load Current    6.5
Over Current Should Be Flagged    True
Set Load Current    0.8
Over Current Should Be Flagged    False
```

**测什么**：0.8A 正常；6.5A > 5A 限值 → 过流标记置位 + 事件计数；回落 0.8A 标记清除。
**真实价值**：台架程控电源的过流保护会真的限流/断电，测试要能验证保护动作与恢复。

### 38.9.8 用例 10：后端抽象与真实台架替换点（本章灵魂）

```robotframework
Backend Should Be    simulated
Set Environment Variable    HIL_BACKEND    real
Run Keyword And Expect Error    *需接真实台架*    Backend Should Be    simulated
Run Keyword And Expect Error    *需接真实台架*    Start HIL Rack
Set Environment Variable    HIL_BACKEND    foo
Run Keyword And Expect Error    *HIL_BACKEND 只支持 sim|real*    Backend Should Be    simulated
Set Environment Variable    HIL_BACKEND    sim
Backend Should Be    simulated
```

**测什么**：① 默认 sim 后端可用；② 切到 real 后 `Backend Should Be` 与 `Start HIL Rack`
**都必须明确报"需接真实台架"**（不是静默通过）；③ 非法配置值有清晰报错；④ 用例结束前恢复 sim
（环境变量是进程级全局状态，不恢复会污染后续套件 —— 这也是测试隔离意识）。

> 这条用例把"替换点"变成了**可执行的需求**：以后实现 RealBackend 后，这条用例会自然变绿；
> 如果有人把 real 改成假通过，它立刻变红。**需求被用例锁住，而不是只写在文档里。**

---

## 38.10 怎么把本模块换到真台架（Step by Step）

### Step 1：拿到 IO 映射表

向台架工程师要 IO map（逻辑名 → 板卡通道 → 线束针脚 → 有效电平），填进 38.2.2 的表。
**这是最容易出错的一步**：通道接错会让所有测试结果不可信。

### Step 2：实现 `RealBackend.connect()`

```python
def connect(self):
    endpoint = os.environ["HIL_RACK_ENDPOINT"]       # 如 "192.168.0.10:5025"（SCPI）/ 台架配置名
    self._psu = ScpiPowerSupply(endpoint)             # 程控电源
    self._io = VtSystem(endpoint)                     # 或 dSPACE / 自研 IO 板
    # 安全检查：确认 KL30 初始为 0V、继电器断开，避免上电瞬间烧 ECU
    self._psu.set_voltage(0.0)
    self._io.set_relay("ECU_POWER", False)
```

### Step 3：实现 `RealBackend.command()` 的命令映射

按 38.7.2 的命令表逐条映射（参考 38.3.4 骨架）。**建议顺序**：先实现 `status`/`set_kl30`/`set_di`
（只读与简单输出），跑通 1~2 条用例，再补时序/Power Cycle/抖动。

### Step 4：先验证"显式失败"再验证"能跑"

```bash
set HIL_BACKEND=real
set HIL_RACK_ENDPOINT=<台架地址>
python -m robot --outputdir results/dev_hil_real tests\robot\20_hil_rack.robot
```

- 没实现时：应该看到 **"需接真实台架"** 的清晰错误（不是超时或空指针）；
- 实现后：用例逐条变绿。**建议保留一套 CI 用 sim 后端**（快速回归），nightly 跑 real 后端（真台架）。

### Step 5：把 sim 后端保留在 CI 里

| 环境 | 后端 | 频率 | 用途 |
|---|---|---|---|
| 开发者本地 / PR CI | `sim` | 每次提交 | 验证测试逻辑/关键字/用例本身（2 秒） |
| Nightly / 台架回归 | `real` | 每晚/每周 | 真 ECU 验证（台架稀缺，按计划排期） |

> **这不是"仿真替代台架"，而是"仿真守住测试代码质量，台架专注真硬件验证"** ——
> 否则台架时间全浪费在"用例写错"上。

### Step 6：真实台架上的坑

| 坑 | 后果 | 对策 |
|---|---|---|
| 共地/电平不匹配 | IO 读不到/误触发 | 确认高有效/低有效、上拉下拉、共地 |
| 电源上升沿太慢 | ECU 上电时序不满足 | 程控电源设置斜率/用硬件开关 |
| 继电器弹跳 | 断电重启不干净 | 等待稳定 + 二次确认电压 |
| 软件时间戳精度不够 | 时序判定误判 | 用台架硬件时间戳/CANoe 时间戳 |
| 没做安全初始化 | 上电瞬间大电流/损坏 | connect 时先 0V + 继电器断开 + 急停可用 |
| 台架被多人共用 | 状态互相污染 | 台架预约/独占锁（和本项目 robot_run.lock 一个道理） |

### Step 7：扩到"ECU 在环"的完整链路（后续学习方向）

本章只验证了**台架本身**；真台架上的完整链路还要加：

```
台架 IO 控制 + CAN 通信（真 ECU）+ 诊断（UDS/DoIP）+ 自动化框架 + 报告
```

即：把本项目 Wave1/2 的 CAN/诊断/视觉/性能能力**接到真台架**：
- CAN 用 Vector 硬件 + CANoe（替换 `VirtualCanBus` 的底层）；
- 诊断用真 ECU（P2 时序、DTC、刷写都要重测）；
- 台架 IO 用本章的 `RealBackend`；
- 上层用例/关键字/追溯体系**完全复用** —— 这就是"测试架构分层"的价值。

---

## 38.11 与纯软件仿真（Wave1/2）的边界

| 能力 | Wave1/2（纯软件仿真） | 本章（台架抽象） | 真 HIL 台架 |
|---|---|---|---|
| 总线协议/诊断/DTC/刷写逻辑 | ✅ 已验证（SIL） | — | 需在真 ECU 上重测 |
| 测试框架/关键字/用例/追溯/CI | ✅ | ✅ 复用 | ✅ 复用 |
| 台架 IO 语义（电压/DI/DO/继电器/过流） | ❌ | ✅（仿真后端） | ✅（RealBackend） |
| 真实电气时序/电源波形 | ❌ | ❌ | ✅ |
| ECU 在异常电压下的真实行为 | ❌ | ❌ | ✅ |
| 硬线 IO 电气特性/执行器负载 | ❌ | ❌ | ✅ |
| EMC/温度/振动/实车路况 | ❌ | ❌ | ❌（实车） |

> **测试金字塔**：SIL（本项目前 37 章）在每次提交跑，守住"逻辑正确"；HIL 在 nightly 跑，
> 守住"真硬件行为正确"；实车在里程碑跑，守住"真实世界正确"。**越往下越慢越贵，
> 所以上层要尽量把问题拦住** —— 这正是本项目把测试逻辑、后端抽象、CI 全练一遍的原因。

---

## 38.12 本章自检

1. MIL / SIL / HIL / 实车四层的区别是什么？为什么有了 HIL 还要做 SIL？
2. HIL 台架通常由哪几部分组成？IO 映射表是什么、为什么它必须纳入配置管理？
3. 为什么测试脚本不能直接调用台架厂商 API？本项目的 `HilBackend` 抽象有哪三个方法？
4. 为什么 `HIL_BACKEND=real` 未实现时必须"明确报错"而不是假通过？这条需求是哪个用户需求（REQ-HIL-004）？
5. KL30 和 KL15 分别是什么？上电时序为什么必须 KL30 先于 KL15？只断言终态为什么不够？
6. 欠压 9V / 过压 16V / 掉电 0V 分别模拟什么真实故障？真 ECU 会有什么反应（举 DTC）？
7. 电源抖动（PWM 掉电）模拟什么？为什么这种故障在稳定电源下永远测不出来？
8. Power Cycle（硬件断电）和软件复位（0x11）的区别？为什么"状态清空"也要断言？
9. DI 和 DO 的方向分别是什么？ECU_POWER 继电器断开后 `ecu_powered` 为什么变成 False？
10. 过流保护怎么测？为什么真实台架上做这个测试要特别小心？
11. （替换点）如果明天给你一台 Vector VT System，你要改哪几个文件/类才能让本套用例跑在真台架上？
12. （架构题）为什么"保留 sim 后端 + real 后端"的双轨模式比"只维护真台架一套"更好？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | MIL=模型在环（算法）、SIL=软件在环（真实代码+全仿真）、HIL=硬件在环（真 ECU+台架仿真其余）、实车。SIL 快且便宜，适合每次提交快速回归；HIL 台架稀缺、慢，聚焦真硬件问题 —— **成本与覆盖范围决定分工，不是互相替代** |
| 2 | 实时仿真机 + 程控电源 + DI/DO IO 板 + 继电器/负载箱 + 故障注入 + 总线接口。IO 映射表 = 逻辑信号名↔板卡通道↔线束针脚↔有效电平；不管理会出现"接错通道导致所有测试不可信"，换台架也无法复用 |
| 3 | 绑死厂商 API 后：换台架要重写全部用例、本地无台架就一条不能跑、CI 无法执行。抽象三方法：**connect() / command() / close()**；用例只依赖统一命令语义与统一状态结构 |
| 4 | 假通过会给出**虚假安全感**（本地绿、台架红；或根本没验证真硬件）。"没接台架就红灯"是 HIL 的诚实性底线。REQ-HIL-004：后端抽象与真实台架替换点 |
| 5 | KL30=常电（蓄电池直连）、KL15=点火 ON。ECU 需先 KL30 上电自检/初始化，再收 KL15 才进工作模式；顺序反了可能不自检/误记 DTC/反复复位。只断言终态无法发现"先 KL15 后 KL30"——**时序需求必须验过程** |
| 6 | 欠压=蓄电池亏电/冷启动（真 ECU 记 P0562、关大功率负载、可能复位）；过压=发电机调节器失效/跨接启动（P0563、保护关断）；掉电=蓄电池断开/保险丝熔断（复位、数据保持、DTC 存储） |
| 7 | 线束虚接/继电器触点抖动/接触器弹跳 → 电压瞬间跌落再恢复、反复多次。稳定直流电源下该故障不复现 → 必须用波形/继电器抖动主动注入 |
| 8 | 软件复位重新执行软件初始化，RAM/外设可能保留；硬件断电=真失电，RAM 丢失、外设重新上电、继电器复位。不断言"状态清空"会把上次遗留故障误判为本轮故障 |
| 9 | DI=台架→ECU（模拟车门/安全带/刹车/档位开关）；DO=ECU→台架（读回主继电器/指示灯等执行器）。ECU_POWER 断开 → 电压路径被切断 → `ecu_powered = relay_closed and kl30>6V = False` |
| 10 | 设 >5A 电流 → over_current=True + 事件计数；回落后标记清除、事件保留。真实台架限流会**真的断电**，可能造成 ECU 不可恢复损坏/台架保护动作，必须先确认限流值、注入时间要短、确认可恢复 |
| 11 | 只需实现 `libraries/HilLibrary.py` 的 `RealBackend`（connect/command/close）：把 9 类命令映射到 VT System API/SCPI，并按 38.2.2 填 IO 映射；`20_hil_rack.robot` 与其余关键字**一行不改**；配置 HIL_BACKEND=real + HIL_RACK_ENDPOINT |
| 12 | sim 后端 2 秒可跑，能进 PR CI，快速暴露"用例/关键字/配置"问题；real 后端台架稀缺、慢、可能有安全风险，只做 nightly/计划验证。双轨 = **测试代码质量与真硬件验证解耦**，台架时间不被低级错误浪费 |

---

**上一章** ← [37 · Appium 与真机 UI 自动化](37_Appium与真机UI自动化.md)　|　**下一章** → [39 · 能力自检与下一步学习路线](39_能力自检与下一步学习路线.md)
（扩展篇导航：[22 全景](22_车载自动化测试全景与能力地图.md) → [23 LIN](23_LIN总线与车身控制测试.md) → [24 DoIP](24_车载以太网与DoIP诊断.md) → [25 HMI 视觉](25_HMI视觉自动化测试.md) → [26 非功能](26_非功能测试实战.md) → [27 信息安全](27_信息安全测试入门.md) → [28 功能安全/ASPICE](28_功能安全与ASPICE测试过程.md) → [29 CAN FD](29_CANFD与高速总线.md) → [30 SOME/IP](30_SOMEIP与SOA服务测试.md) → [31 功能寻址/OBD-II](31_功能寻址与OBDII诊断.md) → [32 安全机制](32_功能安全机制验证.md) → [33 工程工具链](33_工程工具链实战.md) → [34 Trace 分析](34_追踪分析实战（ASC与BLF）.md) → 35 OTA/Bootloader（Wave3 并行开发）→ 36 ODX/OTX（Wave3 并行开发）→ [37 Appium UI](37_Appium与真机UI自动化.md) → **38 HIL 台架（本章）** → [39 能力自检与下一步学习路线](39_能力自检与下一步学习路线.md)）

> 📌 说明：本章是 **接口抽象层** 的落地：前面章节的测试逻辑（用例/关键字/CI/追溯）在 HIL 上
> 完全复用，本章只补"IO 与电源如何被测试脚本驱动"这一层。真台架上的完整链路（真 ECU + 台架 IO
> + CAN 真实硬件）是入职后的主战场；建议先按 38.10 用仿真后端把 RealBackend 骨架搭出来，
> 再到台架上逐条替换验证。
