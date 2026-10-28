# Python 能力练习（8 题 + 自动判分）

> **这套练习解决一个问题**：你能读懂项目代码，但**自己写不出来**。
>
> 8 道题，全是项目里**真实用到**的技能 —— 不是"反转二叉树"那种面试题。

---

## 怎么用（3 步）

```bash
# ① 打开 practice.py，从 P1 开始补 TODO
# ② 每补完一题就存盘，然后跑：
python python_practice/check.py

# ③ 全部通过后，再看 answers.md 对照写法
```

**判分器会逐题告诉你**：哪题过了、哪题还不行、**具体错在哪**。

---

## 8 道题一览

| # | 练什么 | 项目里对应 | 难度 |
|---|---|---|---|
| **P1** | 写一个计算函数（`max()`） | `ecu_sim.py` 算转速 | ⭐ |
| **P2** | pathlib 读 JSON、取字段、处理不存在 | 所有关键字读状态文件 | ⭐⭐ |
| **P3** | `@dataclass` 定义数据结构 | 仿照 `dtc_manager.py` 的 `DtcRecord`（字段按本题简化：`count` 表示发生次数） | ⭐ |
| **P4** | `try/except` 让程序不崩 | 所有"尽力清理"逻辑 | ⭐⭐ |
| **P5** | 列表推导 / `.get()` 默认值 | `dashboard.py` 取点亮的灯 | ⭐⭐ |
| **P6** | `setdefault` 分组统计 | `trace_matrix.py` 建需求→用例映射 | ⭐⭐ |
| **P7** | lambda 当"规则表"（数据驱动） | `FAULT_CATALOG` 故障判定表 | ⭐⭐⭐ |
| **P8** | 周期任务循环（dt 累加） | `ecu_sim.py` 主循环 | ⭐⭐⭐ |

> ⏱️ 全部做完约 **2~3 小时**。P1~P4 各 10-15 分钟，P5~P8 各 20-30 分钟。

---

## 做完之后（P9 / P10 扩展任务）

这两题**没有自动判分**（判分器 `check.py` 只覆盖 P1~P8；P9 要改库、P10 要新建场景文件），
但做完你的能力会有一个台阶：

### P9 · 给关键词库加一个关键字

打开 `libraries/VehicleBusLibrary.py`，在类里加一个方法：

```python
@keyword("Get Fuel Level")
def get_fuel_level(self) -> float:
    """读油量百分比"""
    # 提示：参考同文件里 read_signal_from_bus 的写法
    ...
```

然后在 `scenarios/` 里写一个用例调用 `Get Fuel Level`，跑通它。

**你会学到**：`@keyword` 装饰器怎么工作、库的 `scope` 为什么要 SUITE、关键字命名规范。

### P10 · 写一个全新场景（电压跌落 → 故障灯 + DTC）

照 `scenarios/11_fault_injection.robot` 的风格，自己写一个场景文件：

> **需求**：供电电压跌到 **9V**（冷启动临界）时，ECU 应记录 **P0562**，并点亮仪表**电池警告灯（BatteryLow）**。
> **提示**：
> - 注入低压用工程模式激励：`Set Test Stimulus    voltage=9`（需先 `Enter Extended Session` + `Security Unlock`）
> - 等 DTC：`Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0562`
> - 断言灯：`Telltale Should Be Lit    BatteryLow    timeout=10`
> - **必须写 `Test Teardown` 清激励**（`Clear Test Stimulus` + `11 01`），否则污染后续用例

写完后用 `python -m robot --outputdir results scenarios/你的文件.robot` 跑通。

**你会学到**：从"一句话需求"到"可执行用例"的完整转换（这也是 [docs/17 章](../docs/17_从需求到脚本.md) 的内容）；故障注入 + 清理 = 场景 11 的核心模式。

> 💡 为什么不用"油量低点亮加油灯"？本项目 DBC 的 12 个警告灯里没有加油灯（只有油量百分比），
> 所以那道题在本仓库里做不出来；换成"低压→BatteryLow"就能真实跑通。

---

## 参考答案

在 [answers.md](answers.md)。**先做完再看** —— 看答案学不到东西。

---

## 卡住了怎么办

| 现象 | 怎么办 |
|---|---|
| `check.py` 说"文件本身有语法错误" | 你的缩进/括号错了，看 traceback 最后一行 |
| 某题一直 ✗，但我觉得写对了 | 看判分器给的 `↳` 那行具体原因，对着 docstring 的【验收】检查 |
| `TypeError: 'NoneType' object is not iterable` | 你的函数直接 `pass` / 返回了 `None`，还没实现 |
| 想知道为什么这么写 | 看 [docs/21 章 Python 补强](../docs/21_Python补强.md)；或跑 `python python_basics_demo.py` 看可运行例子 |
