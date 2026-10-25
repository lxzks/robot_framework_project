# L4 · 负向测试：验证"系统该拒绝时真的拒绝了"

> **学习目标**：写出负向用例。这是新手最缺失、面试最加分的能力。
> **预计时间**：40 分钟
> **前置**：完成 L3，读教程 [08 章 8.4 节](../../docs/08_UDS诊断测试实战.md)

---

## 一、为什么负向测试比正向测试更值钱

新手写测试只验证"功能正常时长什么样"：

```
✓ 读 VIN 能读出 17 位       ← 正向
✓ 写 DID 能写进去           ← 正向
```

但真实系统出错往往不是"正常功能不对"，而是**在不该放行的时候放行了**：

```
✗ 没解锁也能写 DID          ← 这是安全事故（任何人连上总线就能改 ECU 数据）
✗ 默认会话也能刷写固件      ← 这是产线事故
✗ 请求不存在的 DID 却回了数据 ← 这是协议实现错误
```

**负向测试就是把这些"应该被拒绝"的场景写成用例。** 在 Tier1 的诊断测试规范里，
负向用例占比通常超过 40%。

---

## 二、三个核心工具

### 2.1 期望负响应 + NRC

```robotframework
${resp}=    Send UDS Request    2E F1 A0 4841434B
Response Should Be Negative With NRC    0x33
```

`0x33` 是 `securityAccessDenied`。常用 NRC：

| NRC | 名称 | 什么时候出现 |
|---|---|---|
| `0x11` | serviceNotSupported | 服务根本没实现 |
| `0x13` | incorrectMessageLengthOrInvalidFormat | 参数长度不对 |
| `0x31` | requestOutOfRange | 参数超范围（如 DID 不存在） |
| `0x33` | securityAccessDenied | 没解锁就做敏感操作 |
| `0x7F` | serviceNotSupportedInActiveSession | 当前会话不支持该服务 |

### 2.2 期望"没有响应"

```robotframework
Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
```

`3E 80` 的 sub-function 最高位置 1 = **抑制正响应**（ECU 执行但不回复）。
验证"没有响应"的方法就是**期望它超时**。

> ⚠️ 只验证"没有响应"是不够的——万一是 ECU 挂了也不响应呢？
> 所以要**紧跟着验证 ECU 还活着**（发一个正常请求并期望成功）。

### 2.3 会话权限矩阵

同一个服务在不同会话下行为不同，负向测试要覆盖：

| 条件 | 期望 |
|---|---|
| 默认会话写 DID | NRC `0x7F`（会话不支持） |
| 扩展会话但**未解锁**写 DID | NRC `0x33`（权限不足） |
| 扩展会话 + 已解锁写 DID | 正响应 |

**权限是两层的（会话 + 安全访问），缺哪层报哪个 NRC —— 这两个用例必须分开写。**

---

## 三、任务

在 `exercise.robot` 写三个用例：

1. `未解锁时写 DID 应被拒绝` → 进扩展会话，**不解锁**，写 DID，期望 NRC `0x33`
2. `读取未知 DID 应返回参数超范围` → 读 `F999`（不存在），期望 NRC `0x31`
3. `抑制正响应的请求 ECU 应保持沉默` → 发 `3E 80` 期望超时，然后**验证 ECU 还活着**

---

## 四、怎么跑

```bash
python -m robot --outputdir results/training training/L4_negative_test/exercise.robot
```

---

## 五、自查标准

- [ ] 三个用例 PASS
- [ ] 你能说出为什么"抑制响应"用例最后要补一个 `ECU Should Respond`
- [ ] 你能说出"默认会话写 DID" 和 "未解锁写 DID" 分别报什么 NRC、为什么不同

---

## 六、参考答案

→ [`solution.robot`](solution.robot)
