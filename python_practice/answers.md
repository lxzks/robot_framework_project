# Python 练习参考答案

> **先做完再看。** 看答案学不到东西，卡住了再说。

---

## P1 · 算转速

```python
def calc_rpm(speed_kmh: float) -> float:
    return max(800, speed_kmh * 80)
```

**为什么**：`max(800, ...)` 保证低速时不低于怠速（车停着发动机也在转）。
本项目 `ecu_sim.py` 里就是这么算的。

---

## P2 · pathlib 读 JSON 字段

```python
def read_json_field(path, field_name: str, default=None):
    p = Path(path)
    if not p.exists():
        return default
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get(field_name, default)
```

**两个关键点**：
1. `p.exists()` 先判断 —— 不判断的话 `read_text()` 会抛 `FileNotFoundError`
2. `data.get(k, default)` —— 用 `.get` 而不是 `data[k]`：键不存在时返回默认值而不是报错

---

## P3 · dataclass

```python
@dataclass
class DtcRecord:
    dtc: str
    status: int = 0
    count: int = 0
    snapshot: dict | None = None
```

**为什么这么简单**：`@dataclass` 自动生成 `__init__`，你只写字段。
**注意**：有默认值的字段必须写在没默认值的后面（不然 Python 报错）。

---

## P4 · try/except

```python
def safe_read_text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
```

**为什么抓 `(OSError, ValueError)`**：
- `OSError` 覆盖"文件不存在/没权限/被锁"
- `ValueError` 覆盖"编码不对"（比如二进制文件当文本读）
- **不要只写 `except:`**（裸 except 会连 `KeyboardInterrupt` 都吞掉，很危险）

---

## P5 · 列表推导

```python
def lit_telltales(telltale_map: dict) -> list:
    return [name for name, v in telltale_map.items() if v]
```

**读法（从右往左）**：
`遍历 items()` → `筛出 v 为真（非 0）的` → `收集 name` → `组成列表`。

**为什么这题这么设计**：仪表内部把灯存成"灯名 → 0/1"（因为要按下标快速改），
对外暴露时才转成"点亮的灯名列表"。**"内部结构"和"对外结构"不同，是真实项目里很常见的**
—— 这题练的就是"从一种结构转成另一种"。

---

## P6 · 分组统计

```python
def group_by_requirement(tests: list[tuple[str, str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for rid, case in tests:
        result.setdefault(rid, []).append(case)
    return result
```

**`setdefault` 一行等价于三行**：

```python
if rid not in result:
    result[rid] = []
result[rid].append(case)
```

**为什么不用 `defaultdict`**：可以，但 `setdefault` 更常见、不需要额外 import。
（现代 Python 里 `defaultdict(list)` 更推荐，但读别人代码时 `setdefault` 一定会遇到）

---

## P7 · lambda 规则表

```python
def build_fault_rules() -> dict:
    return {
        "P0116": {"name": "水温过高", "detect": lambda sig: sig["coolant"] > 120},
        "P0562": {"name": "电压过低", "detect": lambda sig: sig["voltage"] < 10.5},
    }
```

**这个设计的价值**：加一条新故障 = 加一行。不需要改任何"循环/判断"代码。
这就是项目里 `FAULT_CATALOG` 的写法，也是"数据驱动"的雏形。

---

## P8 · 周期任务循环

```python
def simulate_periodic(duration_s: float, period_ms: float,
                      on_send: Callable[[], None]) -> int:
    import time
    t = 0.0
    count = 0
    last = time.monotonic()
    deadline = last + duration_s
    while time.monotonic() < deadline:
        time.sleep(0.005)                    # 模拟"干别的活"
        now = time.monotonic()
        dt = now - last                      # 这一轮过了多久
        last = now
        t += dt                              # 累加
        if t >= period_ms / 1000:
            t = 0                            # 清零（注意是 = 0，不是 -= period）
            on_send()
            count += 1
    return count
```

**为什么不用 `time.sleep(period/1000)` 简单循环**：
`sleep` 的实际耗时是"**至少** N 毫秒"（可能 N+2），误差会累积 → 周期越跑越偏。
**累加 dt** 把这个误差吸收掉 → 长期周期稳定。

**这就是项目 `ecu_sim.py` 主循环的核心模式**，也是能测出"周期抖动 2ms"的原因。

---

## 都做完了？

你已经掌握了项目里 **8 项最高频的 Python 技能**。接下来：

1. **P9（扩展）**：给 `libraries/VehicleBusLibrary.py` 加一个你自己的关键字
   （用 `@keyword("你的名字")`），然后在 `scenarios/` 里写个用例调用它
2. **P10（扩展）**：照 `scenarios/01_powertrain_signals.robot` 的风格，写一个全新场景
   （比如"油量低于 10% 应点亮加油灯"）
3. 回到 [`docs/21_Python补强.md`](../docs/21_Python补强.md)，把没读的章节补完
