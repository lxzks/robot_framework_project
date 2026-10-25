# L3 · 诊断脚本的固定结构

> **学习目标**：写出诊断类脚本。诊断脚本有固定套路，学会一次就能套用到任何服务。
> **预计时间**：40 分钟
> **前置**：① 完成 L1/L2（会写"读信号 + 断言"、会用轮询等待）
> ② 读教程 [03 章 UDS 协议](../../docs/03_UDS协议详解.md) 的 3.2/3.5 节（协议背景，知道"请求-响应"是什么）

---

## 一、诊断脚本的固定结构

任何诊断测试都是这三步的变形：

```
① 发请求（十六进制字符串）       →  ② 等响应（Instrument 已经帮你等）  →  ③ 断言响应
   Send UDS Request  22 F187          自动                          Response Should Be Positive
```

**三种断言响应方式**（记住这三句，覆盖 95% 场景）：

```robotframework
Response Should Be Positive                              # 期望正响应
Response Should Be Negative With NRC    0x33             # 期望负响应 + 指定 NRC
Should Be Equal    ${resp}    正响应 SID=0x50 payload=...  # 期望精确内容（少用，太脆弱）
```

**封装好的常用操作**（不用手写 hex）：

| 目的 | 关键字 |
|---|---|
| 进扩展会话 | `Enter Extended Session` |
| 读文本型 DID | `Read DID As ASCII    F187` |
| 写 DID（需先解锁） | `Write DID    F1A0    56332E302E30` |
| 安全访问解锁 | `Security Unlock` |
| 读 DTC 数量 | `Read DTC Count` |
| 读 DTC 列表 | `Read DTC By Mask    08` |

---

## 二、任务

打开 `exercise.robot` 写三个用例：

### 用例 1：`默认会话下应能读取零件号`
- 读 DID `F187`（零件号，ASCII），断言等于 `HW-IC-2026-001`
- 这是产线第一个执行的测试：不依赖任何前置状态

### 用例 2：`读取 17 字节 VIN 应自动走多帧`
- VIN 的 DID 是 `F190`，固定 17 字符，值 `LSVAM4180NC000001`
- 断言：值相等 **且** 长度是 17
- **重点体会**：20 字节的响应在 CAN 上超过 8 字节，必须走 ISO-TP 多帧 ——
  而库把多帧重组藏起来了，脚本只看到"读到一个字符串"

### 用例 3：`应能读到 DTC 数量`
- 读 DTC 数量并断言 >= 0（正常车 0 条，故障车更多）

---

## 三、知识点：DID 是什么

**DID（Data Identifier）= ECU 里的一块数据的编号**（2 字节十六进制）：

| DID | 内容 | 类型 |
|---|---|---|
| `F187` | 零件号 | ASCII 文本 |
| `F189` | 软件版本/序列号 | ASCII 文本 |
| `F18A` | 供应商代码 | ASCII 文本 |
| `F190` | VIN 码（17 字节） | ASCII 文本，**必然多帧** |
| `F1A0` | 应用数据 | 本项目设为可写 |

读取用 `22 + DID`，写用 `2E + DID + 数据`（需要扩展会话 + 安全解锁，L4 会验证这点）。

---

## 四、怎么跑

```bash
python -m robot --outputdir results/training training/L3_diagnostic_basics/exercise.robot
```

---

## 五、自查标准

- [ ] 三个用例 PASS
- [ ] 你能说出 `F187` 和 `F190` 的区别（为什么后者一定多帧）
- [ ] 你能说出"读 DID 需要哪个会话、写 DID 又需要什么额外条件"
- [ ] 打开 `results/training/log.html`，找到 `Read DID As ASCII` 那一行，
      点开看它内部调用了哪些关键字

---

## 六、参考答案

→ [`solution.robot`](solution.robot)
