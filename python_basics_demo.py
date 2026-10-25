"""
python_basics_demo.py — 本项目用到的 Python 特性：最小可运行例子
=================================================================

**用法**：
    python python_basics_demo.py            # 跑全部
    python python_basics_demo.py 05         # 只跑第 5 个例子（可选）

**怎么用这个文件**：配合 [21 章 · Python 补强](docs/21_Python补强.md) 一起看 ——
21 章讲"是什么"，这个文件让你"看到它跑起来"。

每段都标了 **【项目里用在哪】**，所以你看到的不只是孤立语法，而是项目里的真实用法。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


def section(n: str, title: str) -> None:
    print(f"\n{'=' * 64}\n [{n}] {title}\n{'=' * 64}")


# ===========================================================================
# 01 f-string —— 项目里 28 个文件在用
# ===========================================================================
def demo_01_fstring() -> None:
    section("01", "f-string：字符串里塞变量")
    speed, rpm = 60.53, 4842.7

    # 基本用法
    print(f"  车速 {speed} km/h")                      # 直接插值
    print(f"  车速 {speed:.1f} km/h")                  # .1f = 保留 1 位小数
    print(f"  转速 {int(rpm)} rpm")                    # int() 取整
    print(f"  ID {0x100:#x}")                          # #x = 十六进制（0x100）
    print(f"  校验和 {0x2d00:04x}")                     # 04x = 4 位十六进制，不足补 0

    # 忘了写 f 会怎样
    print(f"  ⚠️ 对比：写 f → {speed:.1f}  |  忘写 f → {{speed:.1f}}")

    # 【项目里用在哪】失败信息（断言报错时能看到实际值）
    msg = f"车速 {speed:.1f} 不在 50~70 范围内"
    print(f"  项目里的实际用法: msg={msg}")


# ===========================================================================
# 02 pathlib —— 项目里 26 个文件在用
# ===========================================================================
def demo_02_pathlib() -> None:
    section("02", "pathlib：用 / 拼路径，不用手写反斜杠")
    # __file__ = 本文件的路径（Python 自动提供）
    here = Path(__file__).parent
    print(f"  本文件所在目录: {here.name}")

    # / 运算符拼子路径（跨平台自动处理 \ 和 /）
    dbc = here / "simulators" / "dbc" / "vehicle.dbc"
    print(f"  拼出来的路径: ...{dbc.name}")
    print(f"  这个文件存在吗: {dbc.exists()}")

    # 读写文件（不用 open/close）
    tmp = here / "_demo_tmp.txt"
    tmp.write_text("hello 车电测试", encoding="utf-8")
    print(f"  写入后读回: {tmp.read_text(encoding='utf-8')}")
    tmp.unlink()          # 删除

    # 遍历文件
    n = len(list((here / "scenarios").glob("*.robot")))
    print(f"  scenarios/ 下有 {n} 个 .robot 文件")

    # 【项目里用在哪】所有脚本开头的 ROOT = Path(__file__).parent


# ===========================================================================
# 03 import 与 sys.path —— 29 个文件在用
# ===========================================================================
def demo_03_syspath() -> None:
    section("03", "import 与 sys.path：为什么开头要改搜索路径")
    # sys.path = Python 找模块的目录列表（前几项是默认的）
    print("  sys.path 前 3 项：")
    for p in sys.path[:3]:
        print(f"    {p or '(空字符串 = 当前目录)'}")

    # 项目里的写法：把自己的目录塞进搜索路径，保证同目录模块能被 import
    here = str(Path(__file__).parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    print(f"  现在本文件目录已在搜索路径里: {sys.path[0] == here}")

    # 三种 import 形式
    print("  import json            → 用 json.dumps(...)")
    print("  from pathlib import Path → 直接用 Path(...)")
    print("  from bus import X       → 从自己的文件导入（写模块名，不带 .py）")
    print(f"  演示: json.dumps({{'a': 1}}) = {json.dumps({'a': 1})}")


# ===========================================================================
# 04 类型注解 —— 25 个文件在用
# ===========================================================================
def demo_04_type_hints() -> None:
    section("04", "类型注解：给人看的提示，运行时不强制检查")

    def expect_speed(value: float) -> str:
        return f"收到 {value}"

    print(f"  正常调用: {expect_speed(60.5)}")

    # 关键认知：注解不强制！传错类型照样能跑（只是编辑器会警告）
    print(f"  传字符串也能跑: {expect_speed('六十')}   ← 注解不强制检查")

    # 常见注解
    def f1(x: int) -> None: ...
    def f2(x: str) -> list[dict]: ...
    def f3(x: Optional[float] = None) -> str: ...
    print("  ->None 表示不返回值；list[dict] 表示'字典组成的列表'")
    print("  Optional[float] = None 表示'可以是 float 也可以是 None'")
    # 【项目里用在哪】读函数签名就知道输入输出，不用读整个函数体


# ===========================================================================
# 05 类 / __init__ / self —— 11 个文件在用
# ===========================================================================
def demo_05_class() -> None:
    section("05", "类 / __init__ / self：对象的'出生'和'我是谁'")

    class MiniEcu:
        def __init__(self, target_speed: float):      # 造对象时自动执行
            self.target_speed = target_speed           # 存到"自己身上"
            self.actual_speed = 0.0                    # 初始值

        def tick(self, dt: float) -> None:             # 方法：第一个参数永远是 self
            if self.actual_speed < self.target_speed:
                self.actual_speed += 8 * dt            # 车速每秒涨 8

        def __repr__(self) -> str:                     # 定义 print(对象) 显示什么
            return f"<MiniEcu 目标{self.target_speed} 实际{self.actual_speed:.1f}>"

    ecu = MiniEcu(60)                # ← self 不用传，Python 自动填
    print(f"  刚造出来: {ecu}")
    for _ in range(3):
        ecu.tick(2.5)                # 每 tick 2.5 秒
    print(f"  跑 7.5 秒后: {ecu}")
    print("  ⚠️ 关键：def __init__(self, x) 但调用写 MiniEcu(60) —— self 是自动传的")


# ===========================================================================
# 06 装饰器（@keyword 的原理）—— 4 个文件在用
# ===========================================================================
def demo_06_decorator() -> None:
    section("06", "装饰器：给函数'改身份'（@keyword 就是这个原理）")

    # 一个最小装饰器：把函数登记到一个"名字表"里
    registry: dict[str, Callable] = {}

    def register(alias: str):
        def wrapper(func):
            registry[alias] = func        # 记下"别名 → 函数"
            return func
        return wrapper

    class MyLibrary:
        @register("Read Signal From Bus")     # ← 项目里是 @keyword("...")
        def read_signal_from_bus(self, msg, sig):
            return 60.5

    print(f"  被登记的别名: {list(registry.keys())}")
    lib = MyLibrary()
    # 于是可以用"别名"调到它（RF 内部就这么干的）
    print(f"  用别名调用: {registry['Read Signal From Bus'](lib, 'EngineData', 'VehicleSpeed')}")
    print("  ⚠️ 读代码时看到 @keyword('XXX') → 就知道 .robot 里能用 XXX 调它")


# ===========================================================================
# 07 @dataclass —— 2 个文件在用
# ===========================================================================
def demo_07_dataclass() -> None:
    section("07", "@dataclass：只写字段就能用的类")

    @dataclass
    class DtcRecord:
        """一个 DTC 的状态（等价于项目里的 dtc_manager.DtcRecord）"""
        dtc: str                                   # 必填
        status: int = 0                            # 有默认值
        aging_counter: int = 0
        snapshot: Optional[dict] = None

    rec = DtcRecord("P0116")                       # 只传必填的
    rec.status = 0x08                              # 直接改字段
    print(f"  造出来: {rec}")
    print(f"  改字段后: {rec.dtc} status={rec.status:#04x}")

    # 对比：不用 @dataclass 得手写一堆 __init__ 和 self.xxx = xxx
    print("  ⚠️ @dataclass 自动生成了 __init__ / __repr__ / __eq__，你只写字段")


# ===========================================================================
# 08 try / except —— 16 个文件在用
# ===========================================================================
def demo_08_exception() -> None:
    section("08", "try/except：出了错怎么办（关键：要记日志）")

    def risky(n: int) -> float:
        return 100 / n

    # 只抓特定异常
    try:
        risky(0)
    except ZeroDivisionError as e:
        print(f"  抓到 ZeroDivisionError: {e}")

    # 兜底抓所有（项目里监听线程就这么写）
    try:
        risky(0)
    except Exception as e:
        print(f"  兜底抓到: {type(e).__name__}: {e}")

    # 【项目里的真实教训】为什么必须记日志
    print("  ⚠️ 项目踩过的坑：监听线程里未捕获异常 → 线程静默退出 →")
    print("     '周期报文还在发但诊断全无响应'，极难排查。所以：")
    print("     except 里至少要 log.exception(...) 或 print(...)")
    print("  ⚠️ finally 块无论成败都会执行（用于清理资源）")


# ===========================================================================
# 09 logging —— 7 个文件在用
# ===========================================================================
def demo_09_logging() -> None:
    section("09", "logging：带级别的日志（比 print 强在哪）")
    logging.basicConfig(
        level=logging.INFO,
        format="    %(asctime)s %(name)s %(message)s",
        stream=sys.stdout,          # ⚠️ 项目踩过坑：默认走 stderr 会阻塞线程
    )
    log = logging.getLogger("demo")
    log.debug("这条看不到（级别低于 INFO）")
    log.info("UDS 请求: SID=0x10 params=03")
    log.warning("测试激励已应用: 电压=9.0V")
    log.error("写入 DID 失败")
    print("  ⚠️ 项目里所有仿真进程都用 logging 而不是 print —— 能控级别、能带时间戳")


# ===========================================================================
# 10 argparse —— 12 个文件在用
# ===========================================================================
def demo_10_argparse() -> None:
    section("10", "argparse：--speed 60 是怎么进程序的")
    import argparse
    parser = argparse.ArgumentParser(description="演示", add_help=False)
    parser.add_argument("--speed", type=float, default=0.0)
    parser.add_argument("--coolant", type=float, default=90.0)

    # 模拟两次命令行调用
    for argv in (["--speed", "60"], ["--speed", "60", "--coolant", "125"]):
        args = parser.parse_args(argv)
        print(f"  命令行 {' '.join(argv):<32} → args.speed={args.speed} args.coolant={args.coolant}")

    print("  ⚠️ type=float 会自动把字符串 '60' 转成 60.0；不写就是字符串")
    print("  ⚠️ if __name__ == '__main__': 里的代码只有'直接运行本文件'才执行")


# ===========================================================================
# 11 threading.Thread —— 2 个文件在用
# ===========================================================================
def demo_11_thread() -> None:
    section("11", "threading.Thread：为什么 ECU 要开线程")

    counter = {"sent": 0, "received": 0}

    def sender():                       # 模拟主循环：一直发
        for _ in range(5):
            counter["sent"] += 1
            time.sleep(0.02)

    def listener():                     # 模拟监听线程：一直收
        for _ in range(5):
            counter["received"] += 1
            time.sleep(0.02)

    t = threading.Thread(target=listener, daemon=True)   # daemon=True：主线程退出它跟着结束
    t.start()
    sender()                            # 主线程干活
    t.join(timeout=1)
    print(f"  主线程发了 {counter['sent']} 次，监听线程收了 {counter['received']} 次")
    print("  ⚠️ 单线程做不到：发的时候没法收 → 诊断永远不响应")
    print("  ⚠️ daemon=True 很重要：不然程序会卡着退不出去")


# ===========================================================================
# 12 threading.Timer —— 延时一次
# ===========================================================================
def demo_12_timer() -> None:
    section("12", "threading.Timer：N 秒后执行一次（不阻塞）")

    fired = []

    def light_off() -> None:            # 相当于项目里的 _finish_light_test
        fired.append(time.monotonic())

    t0 = time.monotonic()
    threading.Timer(0.5, light_off).start()      # 0.5 秒后自动调 light_off
    print("  Timer 已挂上，主线程继续干别的（不阻塞）...")

    # 对照：用 sleep 会卡住
    print("  对比：time.sleep(0.5) 会把当前线程卡住 0.5 秒，什么都干不了")

    time.sleep(0.7)
    print(f"  Timer 触发了吗: {bool(fired)}（延迟 {fired[0] - t0:.2f}s）")
    print("  【项目里用在哪】灯光自检：点亮后 4 秒自动熄灭")


# ===========================================================================
# 13 time.monotonic —— 16 个文件在用
# ===========================================================================
def demo_13_monotonic() -> None:
    section("13", "time.monotonic vs time.time：计时用哪个")

    t1, t2 = time.time(), time.monotonic()
    time.sleep(0.1)
    print(f"  time.time()  差值: {time.time() - t1:.3f}s   ← 墙上时钟（可能被系统对时改）")
    print(f"  monotonic()  差值: {time.monotonic() - t2:.3f}s   ← 单调递增，永不倒退 ✓")
    print("  ⚠️ 写'超时/等待'逻辑必须用 monotonic —— 否则系统对时一次就全错")
    print("  【项目里用在哪】所有'等 20 秒内进区间'的轮询逻辑")


# ===========================================================================
# 14 主循环 dt 累加模式 —— 项目里 ECU/dashboard 的核心结构
# ===========================================================================
def demo_14_main_loop() -> None:
    section("14", "主循环 dt 累加：周期任务的正确写法")

    print("  模拟 ECU 主循环 0.5 秒（只跑 5 轮，免得真等）：")
    t_100 = 0.0                      # 100ms 报文的计时器
    sent = 0
    last = time.monotonic()
    for _ in range(5):
        time.sleep(0.06)             # 模拟一轮循环耗 60ms
        now = time.monotonic()
        dt = now - last              # 这一轮过了多久
        last = now
        t_100 += dt                  # 累加
        if t_100 >= 0.1:             # 够 100ms 了 → 发一次
            t_100 = 0
            sent += 1
    print(f"    5 轮循环（约 0.3s）发了 {sent} 帧")
    print("  ⚠️ 为什么不用 sleep(0.1) 发一帧？")
    print("     sleep 实际耗时'至少'0.1s（可能 0.105），误差累积 → 周期越跑越偏")
    print("     而'dt 累加'吸收误差，长期周期稳定 → 这正是 19 章能测出'抖动 2ms'的原因")


# ===========================================================================
# 15 lambda —— 10 个文件在用
# ===========================================================================
def demo_15_lambda() -> None:
    section("15", "lambda：把'逻辑'当数据存起来")

    # 项目里的样子（simulators/dtc_manager.py 的 FAULT_CATALOG）
    FAULT_CATALOG = {
        "P0116": {"name": "水温过高", "detect": lambda sig: sig["coolant_temp"] > 120.0},
        "P0562": {"name": "电压过低", "detect": lambda sig: sig["battery_voltage"] < 10.5},
    }

    def check(signals: dict) -> list[str]:
        hit = []
        for dtc, info in FAULT_CATALOG.items():
            detector = info["detect"]        # 取出 lambda（先取变量，可读性更好）
            if detector(signals):            # 像普通函数一样调用
                hit.append(f"{dtc}({info['name']})")
        return hit

    print(f"  水温 125℃ / 电压 13.8V → {check({'coolant_temp': 125, 'battery_voltage': 13.8})}")
    print(f"  水温 90℃  / 电压 9V    → {check({'coolant_temp': 90, 'battery_voltage': 9})}")
    print("  ⚠️ 好处：加一条新故障只需在字典里加一行（含判断逻辑），不用改其他代码")


# ===========================================================================
# 16 列表/字典推导 + setdefault —— 26 + 12 个文件在用
# ===========================================================================
def demo_16_comprehension() -> None:
    section("16", "推导式 & setdefault：一行干完循环的活")

    telltales = {"MilWarning": 0, "CoolantTempHigh": 1, "TurnLeft": 1, "LowBeam": 0}

    # 列表推导（从右往左读：循环 → 筛选 → 收集什么）
    lit = [name for name, v in telltales.items() if v]
    print(f"  点亮的灯: {lit}")

    # 等价的三行写法（对比理解）
    lit2 = []
    for name, v in telltales.items():
        if v:
            lit2.append(name)
    print(f"  等价的循环写法: {lit2}")

    # 字典推导：给所有信号铺 0
    signals = {name: 0 for name in telltales}
    print(f"  字典推导铺 0: {list(signals.items())[:2]}...")

    # setdefault：分组统计的标准写法
    tests = [("REQ-CAN-001", "用例A"), ("REQ-CAN-001", "用例B"), ("REQ-DTC-002", "用例C")]
    by_req: dict[str, list[str]] = {}
    for rid, case in tests:
        by_req.setdefault(rid, []).append(case)     # 一行 = "没有就创建列表，再追加"
    print(f"  setdefault 分组: {by_req}")
    # 【项目里用在哪】trace_matrix.py 建立"需求 → 用例列表"的映射


# ===========================================================================
# 17 yield 生成器（了解）—— 2 个文件在用
# ===========================================================================
def demo_17_generator() -> None:
    section("17", "yield 生成器（了解即可）：惰性遍历")

    def walk(nodes):
        """递归遍历所有叶子（简化自 trace_matrix.py 的结果树遍历）"""
        for node in nodes:
            if isinstance(node, list):     # 是子列表 → 往里走
                yield from walk(node)
            else:                          # 是叶子 → 产出一个
                yield node

    data = ["A", ["B", ["C", "D"]], "E"]
    print(f"  遍历嵌套结构: {list(walk(data))}")
    print("  ⚠️ 有 yield 的函数是'生成器'：调用它不会立刻算完，而是每次要一个才产一个")
    print("  ⚠️ 读代码时把它当'会被反复调用的循环'理解就够了")


# ===========================================================================
# 18 Enum / 上下文管理器（了解）—— 各 1-2 个文件在用
# ===========================================================================
def demo_18_enum_with() -> None:
    section("18", "Enum 与 with（了解即可）")

    class Session(Enum):
        DEFAULT = 0x01
        PROGRAMMING = 0x02
        EXTENDED = 0x03

    print(f"  Enum: Session.PROGRAMMING = {Session.PROGRAMMING} (值 {Session.PROGRAMMING.value:#04x})")
    print("  ⚠️ 用有名字的常量代替魔法数字，代码更好读")

    # 上下文管理器（with 背后的 __enter__ / __exit__）
    class FakeBus:
        def __enter__(self):
            print("    [FakeBus] 打开总线")
            return self

        def __exit__(self, *exc):
            print("    [FakeBus] 关闭总线（无论是否异常都会执行）")
            return False

    with FakeBus() as bus:
        print("    [使用中] 发一帧报文")
    print("  ⚠️ with 保证'退出时一定清理' —— 项目里用它保证总线被正确关闭")


# ===========================================================================
def main() -> None:
    demos = [
        demo_01_fstring, demo_02_pathlib, demo_03_syspath, demo_04_type_hints,
        demo_05_class, demo_06_decorator, demo_07_dataclass, demo_08_exception,
        demo_09_logging, demo_10_argparse, demo_11_thread, demo_12_timer,
        demo_13_monotonic, demo_14_main_loop, demo_15_lambda,
        demo_16_comprehension, demo_17_generator, demo_18_enum_with,
    ]

    # 支持只跑某一个：python python_basics_demo.py 05
    if len(sys.argv) > 1:
        want = sys.argv[1].zfill(2)
        picked = [d for d in demos if d.__name__.split("_")[1] == want]
        if not picked:
            print(f"没有第 {want} 个例子（有效范围 01~{len(demos):02d}）")
            return
        demos = picked

    print("=" * 64)
    print(" 本项目用到的 Python 特性 —— 18 个最小可运行例子")
    print(f" 共 {len(demos)} 段，配合 docs/21_Python补强.md 一起看")
    print("=" * 64)
    for d in demos:
        d()
    print(f"\n{'=' * 64}\n 全部跑完 ✓  回到 21 章对照阅读效果最好\n{'=' * 64}")


if __name__ == "__main__":
    main()
