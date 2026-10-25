# D3 · 让测试真的"验证"点东西（60 分钟）

> **今天的目标**：从"车上"读到真实的车速，然后判断它对不对。
>
> 昨天你写的 `60` 是自己瞎编的。今天开始，数字**来自那辆虚拟的车**。

---

## 先说清楚：测试怎么"读到"车上的数字？

**用打比方**：

```
真实的车：发动机知道自己转多快，会通过一根线"喊"出来
          仪表盘"听"到之后，显示成指针

本项目的仿真：
    "车"（ecu_sim.py）每隔 0.1 秒喊一次："我现在 60 公里每小时！"
    "仪表"（dashboard.py）听到 → 显示指针
    你的测试脚本也可以"听" → 读到 60 这个数字
```

**关键认知**：
- 你不用"问"车，车**一直在喊**（周期发报文）
- 你只要"听一下"，就能拿到当前值
- 这行代码就是"听一下"：`Read Signal From Bus`

---

## 第 1 步：新建 `test_car.robot`

在项目根目录新建文件 `test_car.robot`，输入：

```
*** Settings ***
Resource         resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车速应该在合理范围
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    Log    读到的车速是 ${speed} km/h
    Should Be True    50 <= ${speed} <= 70    msg=车速 ${speed} 不在 50~70
```

**先别急着跑，看这 4 行 `Settings` 在干什么**：

| 这行 | 干什么 | 为什么必须 |
|---|---|---|
| `Resource  resources/common.resource` | 把"工具包"引进来（里面有读信号、启动环境这些工具） | 不引就用不了 |
| `Suite Setup  Start Vehicle Environment  speed=60` | **测试开始前**，自动启动"车"和"仪表" | 不然没东西可读 |
| `Suite Teardown  Stop Vehicle Environment` | **测试结束后**，自动关掉它们 | 不然进程残留，下次启动会冲突 |

> 💡 **一句话**：**Setup 负责"搭台子"，Teardown 负责"收台子"**。
> 这是所有自动化测试的固定套路。

---

## 第 2 步：跑一次（**它会失败，这是正常的**）

```bash
python -m robot --outputdir results test_car.robot
```

**你会看到**（数字可能不同）：

```
车速应该在合理范围                                          | FAIL |
车速 6.66 不在 50~70
```

### 为什么失败？**这不是你写错了**

因为**车刚从 0 开始加速**：

```
时间线：
  T+0.0s  车启动，车速 0
  T+1.0s  车速 8
  T+2.0s  车速 16
  ...
  T+7.5s  车速 60  ← 到这里才进入 50~70 的范围
```

你的脚本**启动后立刻读**，读到的当然只有 6 点几。

> 🎯 **这就是车载测试的第一个核心难点**：
> **信号是"物理量"，它需要时间变化** —— 不像点一下按钮就立刻有结果。
>
> 而且你会注意到：**你跑出来的数字和教程里的不一样**（可能是 5、9、12…），
> 因为"车启动有多快""电脑跑得多快"每次都不同。**数字不重要，理解原因才重要。**

---

## 第 3 步：先用"笨办法"让它通过

既然车要 7.5 秒才到 60，那就**等一会儿再读**：

改成这样（在读取前加一行 `Sleep    8s`）：

```
*** Test Cases ***
车速应该在合理范围
    Sleep    8s
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    Log    读到的车速是 ${speed} km/h
    Should Be True    50 <= ${speed} <= 70    msg=车速 ${speed} 不在 50~70
```

再跑：

```bash
python -m robot --outputdir results test_car.robot
```

**这次应该 PASS** ✓

---

## 第 4 步：思考一个问题（重要）

`Sleep 8s` 能过，但它有个**致命问题**：

```
如果你的电脑慢，8 秒还不够 → 失败
如果你的电脑快，车 5 秒就到了 → 白等 3 秒
如果车因故一直加速不到 60 → 永远失败，你还以为是"时间不够"
```

**换句话说：你在"猜时间"。**

而"猜时间"是自动化测试的**头号敌人** —— 它会让你的测试**今天能过、明天不过**（叫"偶发失败"/flaky）。

> 🎯 **明天（D4）就解决这个问题**。今天先记住这个痛点，明天解决时你会有"啊哈"的感觉。

---

## 第 5 步：做练习 L1（检验你真的会了）

现在去打开 [training/L1_first_case/README.md](../training/L1_first_case/README.md)：

- 里面有题目（和今天的内容几乎一样）
- 有一个**空模板** `exercise.robot`（带 TODO 提示）
- 有**参考答案** `solution.robot`（做完再看）

**要求**：先自己做，做完再对答案。做完跑一下判分器：

```bash
python training/check_all.py
```

如果 L1 显示通过，说明你今天的核心技能已经掌握了。

---

## ✅ 今天的验收标准

1. 能说清 **`Suite Setup` 和 `Suite Teardown` 分别什么时候执行**
2. 能说清 **`Read Signal From Bus    EngineData    VehicleSpeed` 这两个参数分别是什么**
   （提示：报文名 + 信号名，来自 DBC 字典 —— 不知道没关系，先记住"一个是消息名、一个是消息里的字段"）
3. 能解释**为什么脚本一开始读会失败**（不是脚本错，是物理量要时间）
4. 能说清 **`Sleep 8s` 为什么是"笨办法"**
5. **L1 练习通过**

---

## 🆘 卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `Resource file 'resources/common.resource' not found` | 文件不在项目根目录 | 把 `test_car.robot` 移到 `robot_framework` 这一层 |
| `Suite setup failed` / 环境起不来 | 端口被占用（上次没关干净） | 等 10 秒重试；或检查有没有残留的 python 进程 |
| 一直是 `车速 0 不在 50~70` | 车没启动 / Setup 没执行 | 看终端输出里有没有"ECU 模拟器已启动" |
| `No keyword with name 'Read Signal From Bus'` | `Resource` 那行没写 / 路径错 | 检查第 1 节那 4 行是否完整 |
| 想手动验证一下 | — | 开两个终端跑 `ecu_sim.py` + `uds_client.py`，或直接跑 `python -m robot --test "车速*" test_car.robot` |

---

## 📌 今天你学到的事

1. **测试脚本能"听到"车在喊什么**（周期报文），读到真实的物理值。
2. **`Suite Setup` / `Suite Teardown`** 是所有自动化测试的固定结构（搭台子/收台子）。
3. **信号是物理量，需要时间变化** —— 这是车载测试和"点按钮"类测试最大的不同。
4. **`Sleep` 是猜时间，会制造"偶发失败"** —— 明天学正确解法。

---

**明天 → [D4：学会"等"](day4_学会等待.md)**
（这一天你会学到这个行业最重要的一个技能：**怎么等，才不靠猜**）
