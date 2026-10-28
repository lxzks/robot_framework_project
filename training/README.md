# 车载自动化脚本实训（0 起点）

> **这是给"从没写过自动化脚本"的人准备的动手训练。**
> 每个练习 = 一道题（README）+ 一个空模板（exercise.robot）+ 参考答案（solution.robot）。
> 配套教程在 [`../docs/`](../docs/)：14-17 章讲**怎么写脚本**；23/24/25/27/35/37 章分别对应
> L7 LIN / L8 DoIP / L9 视觉 / L10 信息安全 / L11 OTA / L12 Appium 方向的深入阅读。

---

## 一、怎么用这个实训

> ⚠️ **下面所有命令都在项目根目录执行**（能看到 `docs/`、`training/` 的那一层）。
> 不要在 `training/` 里面执行 —— 路径会找不到。

```
① 读练习的 README.md（含学习目标、任务、提示、自查标准）
② 打开 exercise.robot，按 TODO 补完
③ 跑一遍：python -m robot --outputdir results/training training/L1_first_case/exercise.robot
④ 卡住了？先看 README 的"提示"，别急着看答案
⑤ 通过后，和 solution.robot 对比 —— 重点看"为什么这么写"
⑥ 对照 README 的自查标准，确认你真懂了（不是碰巧跑通）
```

> ⚠️ **先分清两件事**：
> - `python training/check_all.py` 只验证 **12 个参考答案（solution.robot）能跑通**，
>   用来做环境自检/对照，**不代表你的练习通过了**；
> - **你的练习** = 跑自己的 `exercise.robot`，看它是 PASS 还是 FAIL
>   （`python -m robot --outputdir results/training training/LX_xxx/exercise.robot`）。

**环境自检**（第一次用先跑这个，确认练习环境正常）：
```bash
python training/check_all.py
# 预期：12/12 个练习的参考答案通过
```

---

## 二、十二个练习（按顺序做：L1~L6 打基础，L7~L12 按方向深入）

| # | 练习 | 学到什么 | 前置 / 对应章节 | 通过标准（exercise 全绿） | 难度 | 预计 |
|---|---|---|---|---|---|---|
| **L1** | [第一个脚本](L1_first_case/README.md) | 读信号 + 断言 + `msg=` 失败信息 | 14 章 | 2/2 | ⭐ | 30 min |
| **L2** | [轮询等待](L2_wait_and_assert/README.md) | **车载脚本的核心技能**：等待代替 Sleep | L1 + 15 章模式 2 | 2/2 | ⭐⭐ | 30 min |
| **L3** | [诊断脚本的固定结构](L3_diagnostic_basics/README.md) | 请求-响应断言、DID、ISO-TP 多帧 | L1/L2 + 03 章 3.2/3.5 | 3/3 | ⭐⭐ | 40 min |
| **L4** | [负向测试](L4_negative_test/README.md) | NRC 断言、预期失败、**面试加分项** | L3 + 08 章 8.4 | 3/3 | ⭐⭐⭐ | 40 min |
| **L5** | [数据驱动](L5_data_driven/README.md) | Template、消除重复、报告粒度 | L3 + 14 章 | 2/2 | ⭐⭐ | 30 min |
| **L6** | [完整闭环](L6_full_cycle/README.md) | 造场景 → 验证 → 干预 → 恢复（跨系统） | L1~L5 + 09/04 章 | 4/4 | ⭐⭐⭐ | 60 min |
| **L7** | [LIN 车身信号](L7_lin_signals/README.md) | **LIN 总线**：从机信号、校验和、睡眠/唤醒 | L1/L2 + 23 章 | 3/3 | ⭐⭐⭐ | 40 min |
| **L8** | [DoIP 以太网诊断](L8_doip_diag/README.md) | **DoIP**：车辆识别、路由激活、诊断透传、NACK | L3/L4 + 24 章 | 5/5 | ⭐⭐⭐ | 45 min |
| **L9** | [HMI 视觉断言](L9_vision_assert/README.md) | **视觉测试**：黄金图比对、指针角度、灯区域、mutation | L1 + 25 章 | 4/4 | ⭐⭐⭐ | 40 min |
| **L10** | [信息安全](L10_security_fuzz/README.md) | **安全测试**：安全访问绕过回归、NRC、fuzz 健壮性 | L3/L4 + 27 章 | 4/4 | ⭐⭐⭐⭐ | 50 min |
| **L11** | [OTA 刷写](L11_ota_flash/README.md) | **OTA/Bootloader**：manifest、预编程、分块刷写、版本号、坏包回滚 | L3/L4 + 35 章 | 5/5 | ⭐⭐⭐⭐ | 55 min |
| **L12** | [Appium UI](L12_appium_ui/README.md) | **UI 自动化**：session、元素定位、点击、文本、截图 | L1/L9 + 37 章 | 5/5 | ⭐⭐⭐ | 45 min |

> "通过标准"= 你补完 `exercise.robot` 后跑出来的 `X tests, X passed`；每个练习 README 的
> **§五 自查标准** 是更严格的自检（跑通 ≠ 学会）。

**总计约 10 小时**（含读文档）。建议一次做 1-2 个，做完当天的立刻跑一遍加深印象。
L7~L12 可按岗位方向选做：座舱/车身方向优先 L7+L9+L12，网关/诊断/OTA 方向优先 L8+L10+L11。

---

## 三、配套教程（先读哪章）

| 什么时候读 | 读什么 |
|---|---|
| **做 L1 之前** | **[14 章 · 从零写第一个车载用例](../docs/14_从零写第一个车载用例.md)（手把手，必读）** |
| 语法查不懂时 | [05 章 · RF 语法速成](../docs/05_RobotFramework从零到实战.md)（当字典查，不必通读） |
| 做 L2 之前 | [15 章 · 模式库第 2 式：等值进入范围](../docs/15_车载脚本模式库.md) |
| 做 L3/L4 之前 | [03 章 · UDS 协议](../docs/03_UDS协议详解.md) 的 3.2/3.5 节 |
| 做 L6 之前 | [09 章 · 仪表盘 HMI 测试](../docs/09_仪表盘HMI测试实战.md)、[04 章 · DTC](../docs/04_DTC详解.md) |
| **做 L7 之前** | **[23 章 · LIN 总线与车身控制测试](../docs/23_LIN总线与车身控制测试.md)** 的 23.3/23.4/23.6 |
| **做 L8 之前** | **[24 章 · 车载以太网与 DoIP 诊断](../docs/24_车载以太网与DoIP诊断.md)** 的 24.2~24.6 |
| **做 L9 之前** | **[25 章 · HMI 视觉自动化测试](../docs/25_HMI视觉自动化测试.md)** 的 25.2~25.7 |
| **做 L10 之前** | **[27 章 · 信息安全测试入门](../docs/27_信息安全测试入门.md)** 的 27.2~27.6 |
| **做 L11 之前** | **[35 章 · OTA 与 Bootloader 全链路](../docs/35_OTA与Bootloader全链路.md)**（预编程/编程/后编程三阶段、0x34/36/37 + 0x31、版本 DID、回滚） |
| **做 L12 之前** | **[37 章 · Appium 与真机 UI 自动化](../docs/37_Appium与真机UI自动化.md)**（W3C WebDriver、capabilities、定位策略、真机连接步骤） |
| 写完脚本想提升 | [15 章 · 车载脚本模式库 20 式](../docs/15_车载脚本模式库.md) |
| 脚本跑不通 | [16 章 · 调试技能](../docs/16_调试技能.md) |
| 拿到需求不知怎么下手 | [17 章 · 从需求到脚本](../docs/17_从需求到脚本.md) |
| 想查关键字 | [cheatsheet.md](cheatsheet.md)（速查表） |

---

## 四、参考答案自检器（不是给你的答案判分）

```bash
python training/check_all.py               # 跑全部 12 个参考答案
python training/check_all.py --level L6    # 只跑 L6 的参考答案
python training/check_all.py --level L10   # 只跑 L10 的参考答案（L7~L12 同理）
```

它验证的是**参考答案**，不是你的答案 —— 用途是：
- 环境自检（跑不通说明环境问题，不是你写错）
- 对照（"我的答案和标准答案差在哪"）

**你的答案**要自己跑：
```bash
python -m robot --outputdir results/training training/L1_first_case/exercise.robot
```
看到 `2 tests, 2 passed, 0 failed` 才算你的 L1 通过。
`check_all.py` 跑完会顺带提示每个 `exercise.robot` 还剩多少个 TODO，方便自查进度。

---

## 五、常见问题

**Q：跑练习时报"Variable '${PYTHON}' not found"？**
A：说明你改坏了 `resources/common.resource`。它依赖 `resources/env_vars.py`
（把当前解释器暴露给 RF）。跑 `python training/check_all.py` 能快速定位。

**Q：练习跑得很慢（L6 要十几秒）？**
A：正常。L6 要等水温从 90℃ 爬升到 120℃（一阶惯性，约 8 秒）+ 2 个诊断周期确认故障
（约 1 秒），再算上环境启动/清理，整套约 15~20 秒。
车载测试本来就慢，学会读 log 看"卡在等什么"是必备技能。

**Q：我想改练习的期望值试试失败长什么样？**
A：**强烈建议这么做！** 看失败信息是重要学习方式。改完记得跑
`python training/check_all.py --level L6` 确认参考答案/环境正常，再把自己的 exercise 改回来（或者直接 `git checkout`）。

**Q：这 12 个练习做完了，接下来练什么？**
A：去读/改真实套件 —— 每个方向的"完整版"都在 `tests/robot/`：
`07_doip_diagnostics` / `08_lin_bus` / `09_hmi_vision` / `11_security` / `17_ota_bootloader` / `19_appium`（对应 L8/L7/L9/L10/L11/L12）；
或按 [07 章扩展练习](../docs/07_CAN仿真测试实战.md) 给 `tests/robot/01_can_signals.robot` 加用例。

---

## 六、练习为什么这么设计

| 设计 | 原因 |
|---|---|
| 每题先给"任务"再给"提示"，最后才是答案 | 强迫你**先思考**（看答案学不会写脚本） |
| 每题的 solution 都可以直接跑 | 你能立刻看到"正确的脚本长什么样、报告什么样" |
| L1 故意用 `Sleep`（笨办法） | 为 L2 制造"这不可靠"的痛感 —— 对比学习记得牢 |
| L4 单独讲负向测试 | 新手最缺、面试最爱问的能力 |
| L6 跨三个子系统 | 真实工作就是跨系统场景，单点验证不值钱 |
| L7~L10 按总线/诊断/视觉/安全分方向 | 岗位不同方向权重不同；每题都对应一个真实可跑模块（08/07/09/11 套件的简化版） |
| L11 用 A/B 回滚语义练失败保护 | OTA 的真实难点不是"刷成功"而是"刷失败不变砖"——坏包拒绝/回滚/重传是安全底线 |
| L12 用 mock 练 W3C 协议 | 换真车只改 server URL + capabilities；UI 控件树断言与 L9 像素断言互补 |
| 都给了"自查标准" | 跑通 ≠ 学会；自查问题才是检验理解的方式 |
