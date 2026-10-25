# L1 · 第一个车载测试脚本

> **学习目标**：写出人生第一个车载自动化用例 —— 从总线上读一个信号，断言它符合预期。
> **预计时间**：30 分钟（含读文档）
> **前置**：读教程 [14 章 · 从零写第一个车载用例](../../docs/14_从零写第一个车载用例.md)
> （**必读** —— 它手把手教你怎么用 `Read Signal From Bus` 这类项目关键字，并带你跑通第一个用例）。
> 语法细节可以查 [05 章 RF 语法速成](../../docs/05_RobotFramework从零到实战.md)，不必先通读。

---

## 一、背景：车载脚本在做什么

90% 的车载自动化脚本都在做这件事：

```
从总线上读一个值  →  和期望值比较  →  一致就通过，不一致就报错
     (读)                (断言)

      或者反过来

往总线上发一个值  →  从别处读结果  →  比较
     (发)              (读)
```

**第一个用例就练"读 + 断言"**。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完两个用例：

### 用例 1：`车速信号应处于合理范围`
- ECU 以 **60 km/h** 目标启动（Suite Setup 已经配好）
- 要求：读到车速信号，断言它落在 **50~70** 之间（容差断言，不是精确相等）
- 断言失败时，错误信息要能看出**实际读到了多少**

### 用例 2：`水温信号应为数值`
- 要求：读水温信号，断言它 **> -40**（水温物理上不可能低于 -40℃）
- 目的：体会"先读到一个能参与比较的数字"是断言的前提

---

## 三、提示：你需要的关键字

| 你要做的事 | 关键字 | 用法 |
|---|---|---|
| 读信号 | `Read Signal From Bus` | `Read Signal From Bus    EngineData    VehicleSpeed` |
| 赋值给变量 | `${变量名}=` | `${speed}=    Read Signal From Bus    ...` |
| 数值比较断言 | `Should Be True` | `Should Be True    ${speed} > 50    msg=提示信息` |
| 等待（笨办法） | `Sleep` | `Sleep    8s` |

**为什么要 Sleep？** ECU 启动后车速从 0 开始**爬坡**（8 km/h per 秒），大约 7.5 秒才到 60。
读到的是"当前瞬间值"，所以要等它爬上来。

> 💡 聪明的你能想到：靠 `Sleep` 猜时间很不可靠（机器慢就失败）。
> 这正是 **L2 要解决的问题** —— 先按笨办法做通，再看正确做法。

**信号名从哪来？** 项目里的 DBC 文件定义了一切：
`simulators/dbc/vehicle.dbc`。常用信号：
`VehicleSpeed`（车速 km/h）、`EngineSpeed`（转速）、`CoolantTemp`（水温℃）、
`ThrottlePosition`（油门%）、`GearPosition`（档位）、`Odometer`（里程 km）。

---

## 四、怎么跑

```bash
# 在项目根目录执行（注意路径是 training/...）
python -m robot --outputdir results/training training/L1_first_case/exercise.robot
```

预期输出：`2 tests, 2 passed, 0 failed`

---

## 五、自查标准

- [ ] 两个用例都 PASS
- [ ] 失败时的 msg 里能看到实际值（把断言故意改成 `> 1000` 试一下，看报错信息）
- [ ] 你**能解释**为什么不能用 `Should Be Equal  ${speed}  60`（提示：浮点+时序）

---

## 六、参考答案

写完再看 → [`solution.robot`](solution.robot)

对比时重点看两点：
1. 断言为什么用范围而不是精确值
2. `msg=` 都写了什么（好的失败信息是给未来的自己看的）
