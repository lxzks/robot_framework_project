# 车载自动化脚本实训（0 起点）

> **这是给"从没写过自动化脚本"的人准备的动手训练。**
> 每个练习 = 一道题（README）+ 一个空模板（exercise.robot）+ 参考答案（solution.robot）。
> 配套教程在 [`../docs/`](../docs/)：14-17 章专门讲**怎么写脚本**。

---

## 一、怎么用这个实训

> ⚠️ **下面所有命令都在项目根目录执行**（能看到 `docs/`、`training/` 的那一层）。
> 不要在 `training/` 里面执行 —— 路径会找不到。

```
① 读练习的 README.md（含学习目标、任务、提示、自查标准）
② 打开 exercise.robot，按 TODO 补完
③ 跑一遍：python -m robot --outputdir results/training training/LX_xxx/exercise.robot
④ 卡住了？先看 README 的"提示"，别急着看答案
⑤ 通过后，和 solution.robot 对比 —— 重点看"为什么这么写"
⑥ 对照 README 的自查标准，确认你真懂了（不是碰巧跑通）
```

**环境自检**（第一次用先跑这个，确认练习环境正常）：
```bash
python training/check_all.py
# 预期：6/6 个练习的参考答案通过
```

---

## 二、六个练习（按顺序做）

| # | 练习 | 学到什么 | 难度 | 预计 |
|---|---|---|---|---|
| **L1** | [第一个脚本](L1_first_case/README.md) | 读信号 + 断言 + `msg=` 失败信息 | ⭐ | 30 min |
| **L2** | [轮询等待](L2_wait_and_assert/README.md) | **车载脚本的核心技能**：等待代替 Sleep | ⭐⭐ | 30 min |
| **L3** | [诊断脚本的固定结构](L3_diagnostic_basics/README.md) | 请求-响应断言、DID、ISO-TP 多帧 | ⭐⭐ | 40 min |
| **L4** | [负向测试](L4_negative_test/README.md) | NRC 断言、预期失败、**面试加分项** | ⭐⭐⭐ | 40 min |
| **L5** | [数据驱动](L5_data_driven/README.md) | Template、消除重复、报告粒度 | ⭐⭐ | 30 min |
| **L6** | [完整闭环](L6_full_cycle/README.md) | 造场景 → 验证 → 干预 → 恢复（跨系统） | ⭐⭐⭐ | 60 min |

**总计约 4 小时**（含读文档）。建议一次做 1-2 个，做完当天的立刻跑一遍加深印象。

---

## 三、配套教程（先读哪章）

| 什么时候读 | 读什么 |
|---|---|
| **做 L1 之前** | **[14 章 · 从零写第一个车载用例](../docs/14_从零写第一个车载用例.md)（手把手，必读）** |
| 语法查不懂时 | [05 章 · RF 语法速成](../docs/05_RobotFramework从零到实战.md)（当字典查，不必通读） |
| 做 L2 之前 | [15 章 · 模式库第 2 式：等值进入范围](../docs/15_车载脚本模式库.md) |
| 做 L3/L4 之前 | [03 章 · UDS 协议](../docs/03_UDS协议详解.md) 的 3.2/3.5 节 |
| 做 L6 之前 | [09 章 · 仪表盘 HMI 测试](../docs/09_仪表盘HMI测试实战.md)、[04 章 · DTC](../docs/04_DTC详解.md) |
| 写完脚本想提升 | [15 章 · 车载脚本模式库 20 式](../docs/15_车载脚本模式库.md) |
| 脚本跑不通 | [16 章 · 调试技能](../docs/16_调试技能.md) |
| 拿到需求不知怎么下手 | [17 章 · 从需求到脚本](../docs/17_从需求到脚本.md) |
| 想查关键字 | [cheatsheet.md](cheatsheet.md)（速查表） |

---

## 四、判分器

```bash
python training/check_all.py           # 跑全部 6 个参考答案
python training/check_all.py --level L6   # 只跑 L6
```

它验证的是**参考答案**，不是你的答案 —— 用途是：
- 环境自检（跑不通说明环境问题，不是你写错）
- 对照（"我的答案和标准答案差在哪"）

**你的答案**要自己跑：
```bash
python -m robot --outputdir results/training training/L1_first_case/exercise.robot
```

---

## 五、常见问题

**Q：跑练习时报"Variable '${PYTHON}' not found"？**
A：说明你改坏了 `resources/common.resource`。它依赖 `resources/env_vars.py`
（把当前解释器暴露给 RF）。跑 `python training/check_all.py` 能快速定位。

**Q：练习跑得很慢（L6 要 40 秒）？**
A：正常。L6 要等水温从 90℃ 爬升到 120℃（物理惯性）+ 2 个诊断周期确认故障。
车载测试本来就慢，学会读 log 看"卡在等什么"是必备技能。

**Q：我想改练习的期望值试试失败长什么样？**
A：**强烈建议这么做！** 看失败信息是重要学习方式。改完记得跑
`python training/check_all.py --level LX` 确认改回来（或者直接 `git checkout`）。

**Q：这 6 个练习做完了，接下来练什么？**
A：去改真实套件 —— 给 `tests/robot/01_can_signals.robot` 加用例（见
[07 章扩展练习](../docs/07_CAN仿真测试实战.md)），用现成的库和仿真环境。

---

## 六、练习为什么这么设计

| 设计 | 原因 |
|---|---|
| 每题先给"任务"再给"提示"，最后才是答案 | 强迫你**先思考**（看答案学不会写脚本） |
| 每题的 solution 都可以直接跑 | 你能立刻看到"正确的脚本长什么样、报告什么样" |
| L1 故意用 `Sleep`（笨办法） | 为 L2 制造"这不可靠"的痛感 —— 对比学习记得牢 |
| L4 单独讲负向测试 | 新手最缺、面试最爱问的能力 |
| L6 跨三个子系统 | 真实工作就是跨系统场景，单点验证不值钱 |
| 都给了"自查标准" | 跑通 ≠ 学会；自查问题才是检验理解的方式 |
