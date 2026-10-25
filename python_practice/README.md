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
| **P3** | `@dataclass` 定义数据结构 | `dtc_manager.py` 的 `DtcRecord` | ⭐ |
| **P4** | `try/except` 让程序不崩 | 所有"尽力清理"逻辑 | ⭐⭐ |
| **P5** | 列表推导 / `.get()` 默认值 | `dashboard.py` 取点亮的灯 | ⭐⭐ |
| **P6** | `setdefault` 分组统计 | `trace_matrix.py` 建需求→用例映射 | ⭐⭐ |
| **P7** | lambda 当"规则表"（数据驱动） | `FAULT_CATALOG` 故障判定表 | ⭐⭐⭐ |
| **P8** | 周期任务循环（dt 累加） | `ecu_sim.py` 主循环 | ⭐⭐⭐ |

> ⏱️ 全部做完约 **2~3 小时**。P1~P4 各 10-15 分钟，P5~P8 各 20-30 分钟。

---

## 做完之后（P9 / P10 扩展任务）

这两题**没有自动判分**（因为要改项目文件），但做完你的能力会有一个台阶：

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

### P10 · 写一个全新场景

照 `scenarios/01_powertrain_signals.robot` 的风格，自己写一个场景文件：

> **需求**：油量低于 10% 时，仪表应点亮"加油"警告灯。
> （提示：油量信号叫 `FuelLevel`，在 `ClusterData` 报文里；先看 `scenarios/02` 怎么断言灯）

写完后用 `python -m robot --outputdir results scenarios/你的文件.robot` 跑通。

**你会学到**：从"一句话需求"到"可执行用例"的完整转换（这也是 [docs/17 章](../docs/17_从需求到脚本.md) 的内容）。

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
