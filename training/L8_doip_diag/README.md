# L8 · DoIP：以太网诊断的车辆识别与路由激活

> **学习目标**：用 `DoipLibrary` 走一遍"以太网诊断"的完整链路 ——
> 车辆识别（UDP）→ 路由激活（TCP）→ 经 DoIP 透传 UDS → 负向 NACK。
> **预计时间**：40~50 分钟
> **前置**：
> - 完成 L3、L4（UDS 请求-响应、会话、负向 NRC）
> - 读教程 [24 章 · 车载以太网与 DoIP 诊断](../../docs/24_车载以太网与DoIP诊断.md) 的
>   §24.2（DoIP 头）/ §24.3（payload type）/ §24.4（车辆识别）/ §24.5（路由激活）/ §24.6（诊断透传）
> - 想先看完整答案：读 `tests/robot/07_doip_diagnostics.robot`

---

## 一、为什么需要 DoIP

CAN 诊断快但带宽小（8 字节/帧）；刷写/大数据量诊断（OTA、日志上传、大数据 DID）走
**车载以太网 + DoIP（Diagnostics over IP，ISO 13400）**：

```
Tester ──TCP/UDP 13400──► DoIP 网关 ──虚拟 CAN──► ECU
        (DoIP 报文)        (协议转换)     (UDS on CAN)
```

和 CAN 诊断的差别：

| 环节 | CAN 诊断（L3/L4） | DoIP（本练习） |
|---|---|---|
| 入口 | 直接往 0x7E0 发 | 先 **UDP 车辆识别**，再 **TCP 连接 + 路由激活** |
| 报文格式 | UDS 字节直接塞 CAN 帧 | DoIP 头（8 字节）+ payload（0x8001 里装 UDS） |
| 多帧 | ISO-TP FF/CF | DoIP 本身支持多字节 payload（网关内部再拆 ISO-TP） |
| 权限 | 会话 + 安全访问 | 多了"**路由激活**"这层以太网通道许可 |

> 本项目：`simulators/doip_gateway.py` 是教学版 DoIP 网关（TCP/UDP 13400），
> 底层通过虚拟 CAN（30000）连 `ecu_sim.py`；测试端用 `libraries/DoipLibrary.py`。
> 逻辑地址约定：DoIP 实体 `0x0E80`、tester `0x0E00`、ECU `0x0001`；VIN 与 ECU 的 DID F190 一致。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **5 个用例**：

### 用例 1：`车辆识别应返回 VIN 与逻辑地址`
- `DoIP Vehicle Identification` 走 **UDP 0x0001 → 0x0004**，返回 17 位 VIN
- 断言 VIN = `LSVAM4180NC000001`，再用 `DoIP Vehicle Logical Address Should Be` 断言 `0x0E80`、
  `DoIP Vehicle EID Should Be` 断言 `001122334455`

### 用例 2：`路由激活应返回 0x10 并回显地址`
- `DoIP Connect`（TCP）→ `DoIP Routing Activation`（0x0005）
- 断言返回 code = `0x10`（成功），并用 `DoIP Activated Tester Address Should Be` / `DoIP Entity Address Should Be`
  断言回显的 tester 地址 `0x0E00` 和实体地址 `0x0E80`

### 用例 3：`经 DoIP 进入扩展会话应得到正响应`
- Connect → Routing Activation → `DoIP Send Diagnostic    10 03`
- `DoIP Read ACK` 应返回 `0x00`（网关的肯定确认），`DoIP Read Response` 应为 `5003003201f4`

### 用例 4：`经 DoIP 读取 VIN 应透传多帧`
- Connect → Activation → `DoIP Read VIN`（内部发 `22 F1 90`）
- 断言返回 VIN 与用例 1 一致、长度 17（20 字节 UDS 响应必然多帧，网关内部完成 ISO-TP 重组）

### 用例 5（负向）：`未路由激活直接发诊断应被 NACK 0x06`
- **只 Connect，不激活**，直接 `DoIP Send Diagnostic    10 03`
- `DoIP Should Get NACK    0x06    Diagnostic`（target unreachable：没激活不允许诊断）
- 再补一次路由激活并断言成功 —— 证明"拒绝的只是这条报文，连接没有被误杀"

> **Suite Setup/Teardown 已经写好了**（`Start DoIP Test Environment` / `Stop DoIP Test Environment`）：
> 先 `Start Vehicle Environment` 起 ECU，再 `Start DoIP Gateway` 起网关；关闭时**逆序**回收。
> Test Teardown 已配 `DoIP Disconnect`，不用你写。

---

## 三、提示：关键字表（`libraries/DoipLibrary.py`）

| 你要做的事 | 关键字 | 示例 |
|---|---|---|
| 车辆识别（UDP） | `DoIP Vehicle Identification` | `${vin}=    DoIP Vehicle Identification` |
| 断言识别响应字段 | `DoIP Vehicle Logical Address Should Be` / `DoIP Vehicle EID Should Be` | `DoIP Vehicle Logical Address Should Be    0x0E80` |
| 建/断 TCP 连接 | `DoIP Connect` / `DoIP Disconnect` | `DoIP Connect` |
| 路由激活 | `DoIP Routing Activation` | `${code}=    DoIP Routing Activation` |
| 断言激活回显 | `DoIP Activated Tester Address Should Be` / `DoIP Entity Address Should Be` | `...    0x0E00` |
| 发 UDS（经 0x8001） | `DoIP Send Diagnostic` | `DoIP Send Diagnostic    10 03` |
| 读 ACK / 读 UDS 响应 | `DoIP Read ACK` / `DoIP Read Response` | `${ack}=    DoIP Read ACK` |
| 读 VIN（封装 22 F1 90） | `DoIP Read VIN` | `${vin}=    DoIP Read VIN` |
| 断言 NACK | `DoIP Should Get NACK` | `DoIP Should Get NACK    0x06    Diagnostic` |

**记住顺序**：`Connect` → `Routing Activation` → 才能 `Send Diagnostic`。

---

## 四、怎么跑

```bash
# 在项目根目录执行
python -m robot --outputdir results/training training/L8_doip_diag/exercise.robot
```

预期：`5 tests, 5 passed, 0 failed`
（exercise 里用 `Fail    TODO ...` 占位，空跑会失败；补完后才会绿。整个套件约 15~20 秒，
 大部分时间在起 ECU + 网关。）

---

## 五、自查标准

- [ ] 5 个用例 PASS
- [ ] 能说清 **车辆识别（UDP 0x0001）和路由激活（TCP 0x0005）分别解决什么问题**
- [ ] 能说清 `0x8002`（诊断肯定 ACK）和 `0x8001`（诊断响应）在链路里的位置
- [ ] 能解释"未路由激活 → NACK 0x06"为什么是安全设计（否则任何网络节点都能发诊断）
- [ ] 能说清为什么读 VIN 能验证两侧协议转换（DoIP ↔ ISO-TP 多帧）

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

重点对比：连接/激活/发送的**顺序**，以及负向用例为什么"拒绝后还要再激活一次"。

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'DoIP Connect'` | 没 `Library    ../../libraries/DoipLibrary.py` | 检查 Settings |
| Setup failed（网关起不来） | 13400 端口被占用 | 等 10 秒重跑；持续出现看 `results/doip_gateway_stdout.log`，或用环境变量 `DOIP_TCP_PORT` 换端口 |
| `DoIP Should Get NACK` 超时 | 网关没回 NACK（可能已激活） | 负向用例前**不要**调 `DoIP Routing Activation` |
| `DoIP Read Response` 与期望不符 | 会话/顺序不对 | 先确认 ACK=0x00 再读响应；UDS 响应是**小写 hex 字符串** |
| 修改后仍失败 | 残留进程/连接 | 关掉后重跑；Test Teardown 会 `DoIP Disconnect` |

---

## 八、进阶（做完之后）

- 读 `tests/robot/07_doip_diagnostics.robot` 的负向用例：未知 payload type（NACK 0x01）、
  非法协议版本（NACK 0x00 并关闭连接）、payload 长度不符（NACK 0x04）
- 读 [docs/24 章 §24.11](../../docs/24_车载以太网与DoIP诊断.md)：教学简化点 vs 真实 ISO 13400
