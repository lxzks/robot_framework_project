# 08 · UDS 诊断测试实战：02 套件逐行精讲

> 诊断测试是伟世通系统验证岗的核心技能。本章精讲 `tests/robot/02_uds_diagnostic.robot` 的 11 个用例——它们合起来就是一套**最小诊断测试矩阵**，可以照搬思路到你未来的真实项目。
>
> 前置：03 章理论 + 06 章环境。

---

## 8.0 套件骨架

```robotframework
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment
```

注意没有传 `speed`/`coolant` 参数——默认工况（speed=0, coolant=90），诊断测试不需要特定行驶状态。

---

## 8.1 用例 1：默认会话下应能读取 DID

```robotframework
默认会话下应能读取 DID
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001
    ${sn}=    Read DID As ASCII    F189
    Should Be Equal    ${sn}    ECU-2026-0918-007
```

**测什么**：上电默认会话下，读类服务（0x22）应可用——这是最基本的功能验证（产线第一个执行的测试）。

**`Read DID As ASCII` vs `Read DID`**：前者把返回数据按 ASCII 解码（文本型 DID：零件号/VIN/序列号），后者返回 hex（二进制 DID：标定数据、传感器原始值）。库内部都是 `22 <DID>` → 等响应 → 断言正响应。

**测试数据从哪来？** 真实项目里 DID 表由 ODX/CDD 文件定义（项目资产），期望值来自零件号铭牌/软件版本记录。**测试的核心动作 = 发请求 + 断言响应**，这条路永远不变。

---

## 8.2 用例 2：进入扩展会话应返回 P2 与 P2 星超时参数

```robotframework
进入扩展会话应返回 P2 与 P2 星超时参数
    ${resp}=    Send UDS Request    10 03
    Should Be Equal    ${resp}    正响应 SID=0x50 payload=03003201f4
```

**测什么**：`10 03` 的正响应格式与超时参数（03 章讲过：`00 32` = P2 50ms，`01 F4` = P2* 5000ms）。

**注意这个用例的断言风格**：直接比对响应描述全文。好处是**一次验证服务号+子功能+参数全对**；缺点是耦合了库的 `describe()` 格式——教学项目可接受，生产环境更推荐拆成字段断言。

**为什么测 P2/P2*？** 诊断仪的等待超时要按 ECU 声明的来。参数错了（比如 P2* 报成 50ms），ECU 忙时（0x78 responsePending）诊断仪会误判超时。这是**协议一致性测试**，不是功能测试——量产项目里由一致性测试套件（conformance test）覆盖。

---

## 8.3 用例 3：读取 17 字节 VIN 应自动走 ISO-TP 多帧

```robotframework
读取 17 字节 VIN 应自动走 ISO-TP 多帧
    ${vin}=    Read DID As ASCII    F190
    Should Be Equal    ${vin}    LSVAM4180NC000001
    Length Should Be    ${vin}    17
```

**测什么**：20 字节响应（62 F1 90 + 17 字节）必须经 ISO-TP 多帧传输，客户端应自动重组。

**为什么单列一个用例？** 多帧是 TP 层最容易出问题的地方（FC 方向、CF 序号、块大小）。真实排故中"读 VIN 超时但读零件号正常"几乎一定是 TP 层问题。

**库内部发生了什么**（`DiagnosticTester.send_request`）：
1. 发单帧请求 `03 22 F1 90`；
2. 收包线程收到 FF（`10 14 62 F1 90 4C 53 56`）→ 自动回 FC（`30 08 00`）→ 启动重组；
3. 收 CF1/CF2 → 序号校验 → 拼接；
4. `_wait_for` 从队列取完整数据 → 解析 → 断言。

**测试视角的启示**：用例本身只有 3 行，**TP 的复杂度全在库里**——这就是 05 章说的"复杂逻辑下沉"。用例层永远保持业务语义。

---

## 8.4 用例 4：安全访问未解锁时写 DID 应被拒绝

```robotframework
安全访问未解锁时写 DID 应被拒绝
    Enter Extended Session
    ${resp}=    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33
```

**测什么**：**权限控制**——扩展会话 + 未解锁时，写 DID（2E）必须被拒（NRC 0x33 securityAccessDenied）。

**这是"负向测试"的典范**。新手写测试只验证"功能正常时长什么样"，成熟的测试矩阵必须包含"条件不满足时应拒绝什么"：

| 维度 | 正向 | 负向 |
|---|---|---|
| 会话 | 扩展会话能写 | 默认会话不能写（NRC 0x7F） |
| 安全 | 解锁后能写 | 未解锁被拒（NRC 0x33） |
| 参数 | 合法 DID 能写 | 未知 DID 被拒（NRC 0x31） |
| 密钥 | 正确密钥解锁 | 错误密钥被拒（NRC 0x35） |

**安全测试为什么重要？** 写 DID/刷写权限控制不严 = 任何能连上总线的人都能改 ECU 数据——这是真实的车联网安全事故模式。伟世通这类 Tier1 的 diagnostic test spec 里，安全访问测试用例占比很高。

---

## 8.5 用例 5：完成种子密钥握手后写 DID 应成功

```robotframework
完成种子密钥握手后写 DID 应成功
    Enter Extended Session
    Security Unlock
    ${resp}=    Send UDS Request    2E F1 A0 56332E302e30
    Response Should Be Positive
    ${ver}=    Read DID As ASCII    F1A0
    Should Be Equal    ${ver}    V3.0.0
```

**测什么**：完整正向流程——会话→握手→写→回读验证。

**`Security Unlock` 内部**（库关键字）：`Request Seed`（27 01）→ 拿 4 字节种子 → `key = seed XOR 5A5A5A5A` → `Send Key`（27 02）→ 断言正响应。**真实 OEM 算法保密，但流程一模一样**——你的测试代码不需要知道算法，只需要"能算出正确密钥"（产线靠诊断数据库存密钥算法，或硬件安全模块 HSM）。

**写后回读**（write-then-read-back）是数据写入类测试的标准动作：光有 `6E` 正响应不够，**必须读回来确认数据真的落盘**。

**`56332e302e30` 是什么？** ASCII "V3.0.0" 的 hex（V=56, 3=33, .=2e...）。请求总长 9 字节 → 又走了一次多帧请求（FF+CF）——这个用例顺带验证了 Tester 侧的多帧发送。

---

## 8.6 用例 6：切回默认会话应重新锁定安全访问

```robotframework
切回默认会话应重新锁定安全访问
    Enter Default Session
    Enter Extended Session
    ${resp}=    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33
```

**测什么**：**安全状态与会话绑定**——任何会话切换/ECU 复位后，安全访问必须重新验证。

**为什么？** 假设解锁是"持久状态"：诊断仪解锁→离开→恶意工具接入直接写数据。会话绑定保证每次"进入敏感操作窗口"都要重新证明身份。**这是安全设计的基本原则（state resets on transition），不只用于车载。**

---

## 8.7 用例 7/8：未知 DID / 未知服务的错误处理

```robotframework
读取未知 DID 应返回 requestOutOfRange
    ${resp}=    Send UDS Request    22 F9 99
    Response Should Be Negative With NRC    0x31

读取未知服务应返回 serviceNotSupported
    ${resp}=    Send UDS Request    A0
    Response Should Be Negative With NRC    0x11
```

**测什么**：ECU 对"不存在的东西"要给**正确的拒绝**，而不是沉默、崩溃或错误的正响应。

**这类测试叫错误处理测试（negative/error handling）**，价值：
1. **协议健壮性**——总线噪声导致的畸形请求不能让 ECU 挂；
2. **可诊断性**——正确的 NRC 让诊断仪/维修人员知道"为什么不行"，而不是干等超时。

**Fuzz 思想**：更进阶的做法是随机发畸形请求（随机 SID、随机长度、截断的多帧）验证 ECU 不崩溃。车载网络安全（UN R155）正在把这类测试变成强制要求。

---

## 8.8 用例 9/10：例程与保活

```robotframework
自检例程应正常启动
    Enter Extended Session
    Start Routine    0200
    Response Should Be Positive

TesterPresent 应返回保活响应
    ${resp}=    Send UDS Request    3E 00
    Response Should Be Positive
```

**例程测试**：`31 01 0200`（启动自检）→ `71 01 02 00 00`（结果 0=通过）。真实例程库：传感器校准、执行器行程测试、EEPROM 擦除。**例程是"让 ECU 自己跑一段程序"的诊断服务**——测试里常用它主动触发动作（如让大灯闪一下验证 CAN 输出）。

**保活测试**：`3E 00` → `7E 00`。简单但关键：**S3 定时器依赖它**（03 章）。长时间测试脚本里，TesterPresent 的发送频率（通常 1-2s）是脚本稳定性的关键参数——发太勤浪费总线带宽，发太疏被踢出会话。

---

## 8.9 用例 11：抑制正响应

```robotframework
抑制正响应的请求 ECU 不应回复
    Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
    ${resp}=    Send UDS Request    22 F1 87
    Response Should Be Positive
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001
```

**测什么**：`3E 80`（sub-function 最高位置 1）= 抑制正响应——ECU 执行但**静默**。

**怎么验证"没有响应"？** 用 `Run Keyword And Expect Error`：发送后等待必然超时（超时=无响应的证明），错误信息匹配 `*没等到*`。然后立刻发一个正常请求，验证 ECU 还活着（静默功能没把 ECU 搞死）——**"验证沉默"和"验证存活"必须成对做**。

**真实用途**：整车诊断时对多个 ECU 群发功能寻址请求（如全体读 DTC），如果每个 ECU 都回复，响应风暴会冲垮总线。抑制位 + 功能寻址是标准解法。

---

## 8.10 诊断测试矩阵模板（可复用）

把 11 个用例抽象成通用矩阵，套到任何 ECU 上：

| 维度 | 测试项 | 本项目用例 |
|---|---|---|
| 会话 | 默认/扩展/编程 切换正确 | 2 |
| 会话 | 非默认会话 S3 超时回退 | （ecu_sim 有逻辑，可加用例：静默 6s 后写 DID 应收 0x7F） |
| 安全 | 未解锁拒绝 / 解锁成功 / 错误密钥拒绝 / 切会话重锁 | 4、5、6 |
| DID | 读文本/二进制、写+回读、未知 DID 拒绝 | 1、3、5、7 |
| DTC | 读数量/按掩码读/快照/清除（见 09 章） | 03 套件 |
| 例程 | 启动/停止/查结果 | 9 |
| 保活 | TesterPresent 正响应/抑制响应 | 10、11 |
| 错误 | 未知服务/畸形请求拒绝 | 8 |
| 多帧 | 长请求/长响应的 TP 传输 | 3、5 |

**面试话术**："我设计诊断测试时按会话/安全/DID/DTC/例程/错误处理六个维度覆盖，每个维度都有正向和负向用例。"

---

## 8.11 刷写流程与一致性测试（06 套件）

诊断测试的天花板是**刷写（Flash Programming）**——ECU 固件升级。它把会话、
安全访问、例程、传输层全串起来，是 Tier1 诊断测试交付物里权重最高的部分。

### 8.11.1 刷写四步曲

```
① 0x10 02           进编程会话（App 停跑，跳 Bootloader）
② 0x27              安全访问解锁（没有解锁，后面全部被拒）
③ 0x34 RequestDownload      请求下载：告诉 ECU "我要往 0x08000000 写 128 字节"
     ← 74 20 04 00          正响应：单块最大 1024 字节
   0x36 TransferData × N     分块传数据：块序号 1,2,3... + 数据
     ← 76 01                正响应回显块序号
   0x37 RequestTransferExit  退出传输
④ 0x31 01 FF00     检查编程完整性（校验和比对）→ 结果 0x00 = 通过
```

### 8.11.2 06 套件的 11 个用例覆盖什么

| 用例 | 验证点 |
|---|---|
| 编程会话下请求下载应返回单块上限 | 0x34 正响应格式与 maxBlockLength |
| 分块传输数据应逐块确认 | 0x36 回显块序号（多帧请求 66 字节） |
| 完整刷写流程应通过完整性校验 | 端到端：下载→传输→退出→校验 0x00 |
| 传输字节数不足时完整性校验应失败 | **负向**：声明 128 只传 64 → 校验 0x01 |
| 块序号错乱应被拒绝 | NRC 0x73（真实刷写最常见的失败） |
| 未解锁时请求下载应被拒绝 | NRC 0x33（安全访问必须每次会话重做） |
| 默认会话下刷写服务应不可用 | NRC 0x7F（权限模型：编程会话才有刷写权） |
| 目标地址超范围应被拒绝 | NRC 0x31（地址越界保护） |
| 未请求下载就传数据应报序列错误 | NRC 0x24（诊断服务有严格时序依赖） |
| 响应时间应符合声明的 P2 参数 | 时序一致性（实测 < 50ms） |
| 默认会话读取类服务应始终可用 | 一致性：读服务不应被会话限制 |

### 8.11.3 两个值得记住的设计点

**① 用例隔离靠 ECU 复位**：刷写状态（`flash_state`）在用例之间必须清空，否则
下一个 0x34 会撞 NRC 0x24。项目用 `Test Teardown    复位 ECU 清理刷写状态`
（发 `11 01`）——**真实 ECU 复位后刷写序列本来就必须重来**，所以这不是"hack"，
而是符合系统语义的隔离手段。

**② 权限是两层**：刷写服务要求"编程会话 **且** 已解锁"，缺一不可（对应两个
独立 NRC）。测试要**分别**验证缺会话（0x7F）和缺解锁（0x33）两种情况——
这正是"诊断测试六维矩阵"里"负向测试"的价值。

---

## 8.12 扩展练习

1. **S3 超时用例**：进扩展会话 → `Sleep 6s`（不发 3E）→ 发 `2E` → 断言 NRC 0x7F（会话已被踢回默认）。
2. **错误密钥用例**：`27 01` 拿种子 → 发**错误**密钥 → 断言 NRC 0x35。
3. **ECU 复位用例**：`11 01` → `51 01` → 验证会话回默认（发 `2E` 应被拒）。
4. **编程会话拒绝**：默认会话下发 `10 02`（进编程会话）→ 观察本项目 ECU 的行为（当前实现是允许的，真实 ECU 常要求先满足条件）——思考"条件不满足该怎么设计响应"。

---

**下一章：[09 · 仪表盘 HMI 测试实战](09_仪表盘HMI测试实战.md)**
