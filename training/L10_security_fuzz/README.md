# L10 · 信息安全：安全访问绕过回归与模糊测试

> **学习目标**：用 `SecurityLibrary` 做"安全访问（0x27）"的攻防验证 ——
> 正常种子-密钥、未请求种子的绕过回归、错误密钥 NRC、小规模 fuzz 后 ECU 存活。
> **预计时间**：40~50 分钟
> **前置**：
> - 完成 L3、L4（UDS 请求-响应、会话/权限、负向 NRC 断言）
> - 读教程 [27 章 · 信息安全测试入门](../../docs/27_信息安全测试入门.md) 的
>   §27.2（种子-密钥机制）/ §27.3（攻击面拆解）/ §27.4（本项目真实漏洞与修复）/ §27.6（11 套件）
> - 想先看完整答案：读 `tests/robot/11_security.robot`

---

## 一、为什么信息安全要单独测

功能测试验证"该能用的能用"；信息安全测试验证 **"不该能用的确实用不了"** ——
而且检验手段是"像攻击者一样尝试"：

| 测试类型 | 问的问题 | 本练习覆盖 |
|---|---|---|
| 功能（L3） | 种子-密钥正常流程能解锁吗？ | 用例 1（**正基线**，先证明基线成立） |
| 漏洞回归 | 跳过 27 01 直接发密钥能解锁吗？（历史 bug） | 用例 2（**要永远失败**） |
| 协议负向 | 错误密钥会明确拒绝吗？ | 用例 3（NRC 0x35） |
| 健壮性/模糊 | 几百条畸形请求能打崩 ECU 吗？ | 用例 4（fuzz + 存活/恢复） |

> **本项目的真实漏洞（已修复）**：早期 ECU 的 `27 02`（SendKey）在**未请求种子**时用默认
> `seed=0x00000000` 计算期望密钥（= `0x5A5A5A5A`），任何人直接发 `27 02 5A5A5A5A` 就能解锁。
> 修复后必须回 NRC `0x24`（requestSequenceError）。用例 2 就是把它锁死成**永久回归项**。
> 真实工作对应 ISO 21434 / UN R155 要求的威胁分析与安全测试。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **4 个用例**：

### 用例 1：`正常种子密钥握手应解锁成功`
- `Unlock Sequence Should Succeed`：内部完成 27 01 取种子 → key = seed XOR 0x5A5A5A5A → 27 02，
  返回 `[seed, key]`（RF 里可以 `${seed}    ${key}=` 接）
- 解锁后验证受保护服务可用：`Send Raw Request    2E F1 A0 56332E302E30`（写 DID），
  再用 `SecurityLibrary.Response Should Be Positive` 断言正响应
- 只有正基线成立，后面的负向用例才有意义（否则可能只是"本来就写不了"）

### 用例 2：`未请求种子直接发送密钥应被拒绝`
- `Bypass Attempt Should Fail`：不请求种子，直接发"默认种子算出的密钥" `27 02 5A5A5A5A`，
  内部断言 NRC `0x24`
- 再 `Send Raw Request    2E F1 A0 4841434B` 证明写 DID 仍被拒（NRC `0x33`）

### 用例 3：`错误密钥应返回 NRC 0x35`
- `SecurityLibrary.Request Seed` 取当前种子（返回 4 字节 hex）
- `Send Wrong Key For Seed    ${seed}`（把正确密钥翻转 1 bit）
- `Security Access Should Fail With NRC    0x35`（invalidKey）

### 用例 4：`小规模模糊测试后 ECU 应存活且可恢复`
- `&{stats}=    Fuzz UDS Requests    120`：发 120 条随机构造 UDS（固定随机种子，结果可复现）
- 断言统计：正响应数 `>= 8`、无响应数 `<= 20`、`alive=True`
- `ECU Should Stay Alive`；再 `Send Raw Request    22 F1 87` 并断言正响应（模糊后诊断链路仍可用）

> **Test Setup 已配 `Reset Security State`**（每条用例前 ECU 复位 + 回扩展会话，保证隔离）；
> Suite Setup/Teardown 已配好环境起停，不用你写。

---

## 三、提示：关键字表（`libraries/SecurityLibrary.py`）

| 你要做的事 | 关键字 | 示例 |
|---|---|---|
| 正常解锁 | `Unlock Sequence Should Succeed` | `${seed}    ${key}=    Unlock Sequence Should Succeed` |
| 发裸 UDS（不断言） | `Send Raw Request` | `Send Raw Request    2E F1 A0 56332E302E30` |
| 断言正响应 | `SecurityLibrary.Response Should Be Positive` | 注意加库名前缀（见下） |
| 断言负响应 NRC | `Security Access Should Fail With NRC` / `Response Should Be Negative With NRC` | `...    0x35` |
| 绕过回归 | `Bypass Attempt Should Fail` | 直接调用，内部断言 NRC 0x24 |
| 取种子 | `SecurityLibrary.Request Seed` | `${seed}=    SecurityLibrary.Request Seed` |
| 发错误密钥 | `Send Wrong Key For Seed` | `Send Wrong Key For Seed    ${seed}` |
| 模糊测试 | `Fuzz UDS Requests` | `&{s}=    Fuzz UDS Requests    120` |
| 断言 ECU 存活 | `ECU Should Stay Alive` | `ECU Should Stay Alive` |

> ⚠️ **同名关键字要加库名前缀**：`Response Should Be Positive` 在
> `DiagnosticLibrary`（common.resource 里）和 `SecurityLibrary` 里都有；
> 为明确语义，练习里统一写 `SecurityLibrary.Response Should Be Positive`（`11_security.robot` 同样写法）。

---

## 四、怎么跑

```bash
# 在项目根目录执行（要点：起 ECU + fuzz，约 15~20 秒）
python -m robot --outputdir results/training training/L10_security_fuzz/exercise.robot
```

预期：`4 tests, 4 passed, 0 failed`
（exercise 里用 `Fail    TODO ...` 占位，空跑会失败；补完后才会绿。）

---

## 五、自查标准

- [ ] 4 个用例 PASS
- [ ] 能说清"未请求种子直接发密钥"为什么必须失败（历史漏洞是什么、修复后回哪个 NRC）
- [ ] 能说清"错误密钥"和"绕过"返回的 NRC 为什么不同（0x35 vs 0x24）
- [ ] 能说清 fuzz 的验收口径：为什么"正响应数"和"无响应数"都要卡阈值，为什么还要测"事后可恢复"
- [ ] 打开 `log.html` 看 `Fuzz UDS Requests` 返回的统计字典

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

重点对比：负向用例为什么写得很"确定"（不依赖随机/时序），以及 fuzz 的统计断言阈值来源。

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'Unlock Sequence Should Succeed'` | 没 `Library    ../../libraries/SecurityLibrary.py` | 检查 Settings |
| `期望正响应，实际: 负响应...` | 没先 `Reset Security State`／没有先取种子 | 用 exercise 已配的 Test Setup；按用例顺序写 |
| `27 02` 之类打错 | hex 写错/多空格 | 用空格分隔的 hex 字符串，如 `27 02 5A5A5A5A` |
| Fuzz 正响应太少导致失败 | 环境没起好/ECU 不在 | 先确认 Setup 成功；重跑一次 |
| 锁定/计数残留导致后一条用例异常 | 用例间状态污染 | Test Setup 的 `Reset Security State` 会复位；别绕过它 |

---

## 八、进阶（做完之后）

- 读 `tests/robot/11_security.robot`：连续 3 次错误触发锁定（NRC 0x36 + 5 秒）、
  重放旧密钥、300 条 fuzz、畸形帧 NRC 矩阵、模糊后复位恢复
- 读 [docs/27 章 §27.7](../../docs/27_信息安全测试入门.md)：真实项目里信息安全测试怎么接 ISO 21434 / R155
