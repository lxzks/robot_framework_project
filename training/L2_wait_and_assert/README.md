# L2 · 等待与范围断言（车载脚本的核心技能）

> **学习目标**：用**轮询等待**代替 `Sleep`，这是车载脚本与普通软件测试最大的区别。
> **预计时间**：30 分钟
> **前置**：完成 L1

---

## 一、为什么车电测试离不开"等待"

车上的信号是**物理量**：车速要爬坡、水温要有惯性、故障要连续几个诊断周期才确认。
所以"读一次就断言"几乎必然失败——你读到的是**当前瞬间值**，而系统还在变化中。

L1 里你用 `Sleep 8s` 猜时间。它的致命缺陷：

| 问题 | 后果 |
|---|---|
| 时间猜短了 | 偶发失败（机器慢、ECU 启动慢）→ 最讨厌的一种 bug：**今天过明天不过** |
| 时间猜长了 | 每次跑都白等 → 100 个用例多花几分钟 |
| 系统真变慢了 | 失败，但你不知道为什么 |

**正确做法：轮询等待 —— "最多等 N 秒，每秒试一次，满足条件就立刻继续"。**

---

## 二、任务

打开 `exercise.robot`，写两个用例：

### 用例 1：`车速应稳定在目标区间`
- 用**轮询等待**等车速进入 50~70，最多等 20 秒
- 满足条件立即返回，不浪费一秒

### 用例 2：`档位应随车速变为 D`
- 等待档位信号变成 **3**（D 档）
- 提示：`Wait Until Signal In Range    ClusterData    GearPosition    3    3    timeout=20`
  （区间退化成单值就是"精确等待"）

---

## 三、关键知识点

### 3.1 项目提供的等待关键字

```robotframework
Wait Until Signal In Range    EngineData    VehicleSpeed    50    70    timeout=20
```

它的内部实现（`libraries/VehicleBusLibrary.py`）：

```python
while time.monotonic() < deadline:
    last = self.read_signal_from_bus(...)      # 读一次
    if low <= last <= high:                    # 满足条件
        return last                            # 立刻返回
    time.sleep(0.2)                            # 没满足，200ms 后再试
raise AssertionError(f"{timeout}s 内 {last} 未进入 [{low}, {high}]")
```

### 3.2 ⚠️ 一个必须避开的坑（真实面试题）

新手常这么写，**它是错的**：

```robotframework
Wait Until Keyword Succeeds    20x    1s    Read Signal From Bus    EngineData    VehicleSpeed
```

为什么错？`Wait Until Keyword Succeeds` 只看"关键字有没有抛异常"——
`Read Signal From Bus` 读到 5 km/h 也是**成功**（它不判断值），所以"等待"立即结束，
后面的范围断言还是会失败。

> **记住**：`Wait Until Keyword Succeeds` = "重试直到不报错"；
> 而你要的是"重试直到**值满足条件**"——必须把值域判断和等待写在同一个关键字里。

### 3.3 什么时候用哪种等待

| 场景 | 做法 |
|---|---|
| 等信号进区间 | `Wait Until Signal In Range` |
| 等灯亮/灭 | `Telltale Should Be Lit/Off`（内部已轮询） |
| 等 DTC 出现 | `Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116`（这次它是对的——因为被等待的关键字本身会断言） |
| 纯粹想等一下（很少见） | `Sleep`，并写注释说明为什么必须等 |

---

## 四、怎么跑

```bash
python -m robot --outputdir results/training training/L2_wait_and_assert/exercise.robot
```

---

## 五、自查标准

- [ ] 两个用例 PASS
- [ ] 全程**没有** `Sleep`（除了库内部的轮询间隔）
- [ ] 你能说出：为什么 `Wait Until Keyword Succeeds` 包 `Read Signal From Bus` 是错的
- [ ] 你能说出：为什么这种写法比 `Sleep 8s` 更可靠**且**更快

---

## 六、参考答案

→ [`solution.robot`](solution.robot)
