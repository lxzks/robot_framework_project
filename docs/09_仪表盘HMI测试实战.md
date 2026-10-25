# 09 · 仪表盘 HMI 测试实战：03 套件逐行精讲

> 本章是全教程的**集成高潮**：把 CAN 信号、UDS 诊断、DTC 状态机、仪表显示四条线拧成一个闭环——"故障注入 → 警告灯亮 → 诊断确认 → 清除 → 灯灭"。
>
> 同时讲透 HMI 自动化的本质问题：**没有屏幕的测试环境，怎么验证"屏幕上显示了什么"**。

---

## 9.1 HMI 自动化的本质：把"显示"变成"数据"

真实车上验证仪表盘，传统做法是**台架 + 摄像头 + 图像识别**：

```
仪表屏 ──摄像头──► 工控机 ──OCR/图像比对──► "水温灯亮没亮?"
```

这条路的问题：光照变化、角度偏移、动画过渡、多语言——图像比对脆弱且难维护。

**本项目的解法（也是现代 HMI 测试框架的主流思路）**：

```
仪表盘渲染状态 ──序列化──► dashboard_state.json ──断言──► "telltales_lit 包含 CoolantTempHigh?"
```

`dashboard.py --headless` 每 100ms 把当前显示模型快照成 JSON：

```json
{
  "speed_kmh": 80.0,
  "rpm": 6400.0,
  "coolant_c": 123.0,
  "gear": "D",
  "telltales_lit": ["CoolantTempHigh"],
  "updated_at": 1791818115.3
}
```

**这叫"状态外置"**：被测系统把内部显示状态暴露成一个可查询的数据契约（data contract）。测试断言 JSON，不猜像素。

> 真实对应：量产 HMI 框架（如 Qt 应用）常预留"测试后门"——通过调试接口/QML 属性树读出当前显示元素；Android 侧则是 uiautomator dump（把界面 XML 吐出来）。思路完全一致：**UI 状态 → 可断言的树/JSON**。

**DashboardLibrary 就是这个数据契约的消费者**：`Get Displayed Speed` / `Telltale Should Be Lit` / `All Telltales Should Be Lit`——每个关键字背后都是读 JSON + 断言字段。

---

## 9.2 套件骨架：用参数化环境制造故障

```robotframework
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60    coolant=125
Suite Teardown   Stop Vehicle Environment
```

**`coolant=125`**：ECU 一启动水温目标就是 125℃（>120℃ 阈值）——**一个参数制造出真实故障场景**。这是"环境即用例"的设计：套件级 setup 注入故障前置条件，用例们按时间线验证后续演变。

**时间线**（启动后自动发生）：

```
t=0     ECU 启动，speed_target=60, coolant_target=125
t≈7.5s  车速到 60（斜坡 8km/h/s）
t≈15s   水温到 120℃ 以上（一阶惯性 4s 时间常数 + 启动延迟）
t≈16s   诊断循环1：testFailed=1, pending=1（status=0x27）
t≈17s   诊断循环2：confirmed=1, warningIndicator=1（status=0xAF）→ 水温灯亮
```

所以 03 套件的用例都带长 timeout（15s/5s）——**测试时序要和系统时序匹配**，写死 sleep 是脆弱的，`Wait Until ... timeout=` 才是正确姿势。

---

## 9.3 用例 1：水温超限应点亮水温报警灯并生成 DTC

```robotframework
水温超限应点亮水温报警灯并生成 DTC
    Telltale Should Be Lit    CoolantTempHigh    timeout=15
```

**测什么**：**这条链：CAN 水温信号 > 120℃ → DTC 状态机确认 → 0x300 报文 bit 置 1 → 仪表点灯**。一个断言跨了四个子系统。

**`Telltale Should Be Lit` 的轮询语义**（库实现）：

```python
while time.monotonic() < deadline:
    state = self._fresh_state(allow_stale_retry=True)   # 读 JSON
    if name in state["telltales_lit"]: return            # 命中
    time.sleep(0.1)                                      # 100ms 重试
raise AssertionError(...)
```

**为什么允许 15 秒？** 水温从 90 爬到 120 要 ~15 秒（物理惯性），不是系统慢——**测试超时参数必须理解被测对象的物理过程**。这是车载测试和普通软件测试的最大差异：**信号是渐变的、有惯性的**。

---

## 9.4 用例 2/3：诊断仪视角确认 DTC

```robotframework
诊断仪应能读到 confirmed 状态的 P0116
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116

P0116 状态位应包含 pending 与 confirmed
    ${dtcs}=    Read DTC By Mask    FF
    ${status}=    Set Variable    ${EMPTY}
    FOR    ${item}    IN    @{dtcs}
        IF    '${item}[0]' == 'P0116'
            ${status}=    Set Variable    ${item}[1]
        END
    END
    ${status_int}=    Evaluate    int('${status}', 16)
    ${has_pending}=    Evaluate    (${status_int} & 0x04) != 0
    ${has_confirmed}=    Evaluate    (${status_int} & 0x08) != 0
    Should Be True    ${has_pending}
    Should Be True    ${has_confirmed}
```

**两个用例的分工**：
- 用例 2：**列表断言**——confirmed DTC 里有 P0116（业务视角："这个故障被记录了"）；
- 用例 3：**状态位断言**——P0116 的状态字节同时含 pending(0x04) 和 confirmed(0x08)（协议视角："状态机走到了正确位置"）。

**为什么用位运算而不是全等？** 真实状态字节还有 testFailed(0x01)、warningIndicator(0x80) 等位（实际是 0xAF）。**断言关键位、忽略无关位**——状态位测试的正确姿势（全等断言会把用例绑死在某个 ECU 标定版本上）。

**RF 语法点**：
- `Read DTC By Mask` 返回 `[(dtc, status_hex), ...]` 列表；
- `FOR ${item} IN @{dtcs}` 逐项取 tuple，`${item}[0]`/`${item}[1]` 索引访问；
- `Evaluate` 调 Python 表达式做十六进制转换和位运算——**复杂计算就应该下沉到库或用 Evaluate**，用例层只做编排。

---

## 9.5 用例 4：DTC 快照应记录故障发生时的车速

```robotframework
DTC 快照应记录故障发生时的车速
    ${resp}=    Send Uds Request    19 03
    Response Should Be Positive
```

**测什么**：`19 03`（ReadDTCSnapshotRecord）能返回快照——冻结帧机制在工作（04 章）。

**为什么只断言正响应？** 教学简化：完整断言应该验证快照内容（车速接近故障时刻值）。扩展方向：解析 `59 03 ...` 的 payload，提取 DID 0x9001 的值，断言在合理范围。**留给你当练习**。

---

## 9.6 用例 5：清除 DTC 后报警灯应熄灭

```robotframework
清除 DTC 后报警灯应熄灭
    Clear DTC
    ${count}=    Read DTC Count
    Should Be Equal As Integers    ${count}    0
    Telltale Should Be Off    CoolantTempHigh    timeout=5
```

**测什么**：**0x14 清除的完整效果**——DTC 数量归零 **且** 警告灯熄灭（非保持型灯策略）。

**为什么这里用 `Read DTC Count` 全等 0？** 因为 `14 FF FF FF` 清除是"立即全部归零"的原子操作，此时故障虽还在但状态位刚被清——下一个诊断循环（500ms）才会重建 pending。**断言窗口**：清除后立刻读数量应为 0。（如果 ECU 的 DTC 缓冲刷新更快，这个断言可能偶发失败——真实项目里这种"操作后立即读"的竞态要靠读取时序设计规避，本项目 500ms 周期给了足够窗口。）

**灯灭的逻辑**（非保持型）：清除 → warningIndicator 位清 → 下一帧 0x300 的 CoolantTempHigh bit 归 0 → 仪表收到 → 灯灭。所以 `Telltale Should Be Off` 要等最多 5 秒（一帧 0x300 周期 + 余量）。

---

## 9.7 用例 6/7：故障自恢复与再次确认

```robotframework
清除后 confirmed 列表应为空
    ${confirmed}=    Read DTC By Mask    08
    Should Be Empty    ${confirmed}

水温持续超限故障应再次确认
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116
    Telltale Should Be Lit    CoolantTempHigh    timeout=5
```

**测什么**：故障条件仍在（125℃），清除只是"重置状态机"——**系统应该在几个循环后重新走完 pending→confirmed→点灯**。

**这是很有价值的测试类型：恢复性测试（recovery）**。维修现场常见场景："清码后故障灯灭了，但开两天又亮"——恢复性测试在台架上加速验证的就是这个。它验证的不是"清除功能"，而是**故障检测系统本身还活着**（没被清除操作搞成永久静默）。

---

## 9.8 从 03 套件学"闭环测试"设计法

03 套件的 7 个用例连起来是一条**时间线叙事**：

```
setup 制造故障 → ①灯亮(系统检测到) → ②③诊断仪确认(DTC记录对) → ④快照(现场留证)
→ ⑤⑥清除(干预生效) → ⑦故障还在→重新确认(系统没聋)
```

**设计闭环测试的要点**：
1. **setup 注入前置条件**，而不是用例里到处造现场；
2. **每个用例验证链上一环**，不重复验证别人的环节；
3. **断言时序匹配物理过程**（等待而不是死等）；
4. **恢复性/反向用例收尾**——系统被干预后还能正常工作吗。

这套方法论套到任何车载功能测试都适用：**造场景 → 验证感知 → 验证记录 → 验证干预 → 验证恢复**。

---

## 9.9 GUI 仪表盘：给人看的那一半

`python simulators/dashboard.py`（不带 --headless）会弹出 PySide6 窗口：

- 左：车速表（0-240），右：转速表（0-8000，6500 以上红线）；
- 中间：档位大字 + 里程/水温/油量数字；
- 底部：12 个警告灯（灰=灭，彩色=亮）。

**建议体验路径**：
1. 终端 1 启动 `ecu_sim.py --speed 60 --coolant 125`；
2. 终端 2 启动 `dashboard.py`；
3. 看车速指针爬升、约 15 秒后水温灯亮起；
4. 终端 1 输入 `coolant 90`，看水温灯熄灭；
5. 终端 1 输入 `inject P0135`，看 MIL 灯亮；`unject P0135` 后**MIL 保持亮**（latch 策略，04 章）。

这个"看得见的系统"比任何图表都帮你建立直觉。

---

## 9.11 多界面座舱仪表（cluster_gui.py）

真实仪表盘远不止"两块表"——它是一块**多界面座舱屏**。本项目 `cluster_gui.py` 实现了完整的量产仪表界面集：

| 界面 | 内容 | 数据来源 |
|---|---|---|
| 启动画面 | Logo + 进度条（2 秒 boot 动画） | 本地 |
| **主页** | 车速表 + 转速表 + 档位 + 里程/水温/油量/电压/室外温度 + 12 警告灯 | CAN 0x100/0x200/0x300/0x400 |
| **行程电脑** | 小计里程/行驶时间/平均车速/平均油耗/瞬时油耗/续航 | 本地积分（`ClusterDisplay.advance`） |
| **媒体** | 专辑封面色块 + 歌名/歌手 + 进度条 + 播放控制 + 音量 | CAN 0x400（索引→`ivi_data` 查表） |
| **导航** | 自绘转向箭头（左/右/直行/偏/掉头/到达）+ 剩余距离 + 街道 + ETA | CAN 0x500 |
| **电话** | 来电卡片（联系人/号码/接听挂断）+ 联系人列表 | CAN 0x400 |
| **设置** | 单位/语言/亮度/关于（视觉演示） | 静态 |
| **车辆信息** | 警告摘要（点亮的灯→中文说明）+ 水温/电压/室外/空调卡片 | CAN 0x300/0x400 |

**工程要点**：
- **界面切换**：底部 5 个 tab（主页/行程/媒体/导航/电话），右上角进设置/车辆信息 —— 模拟真实车的方向盘按键/MMI 旋钮翻页；
- **转向灯闪烁**：真实车约 1.5Hz（亮 333ms/灭 333ms），用 QTimer 333ms 节拍 + "仅在灯应亮时闪烁"逻辑；
- **数据契约不变**：GUI 和 headless 共用同一个 `ClusterDisplay` 模型和 `snapshot()` —— RF 断言的和人看到的是同一份数据（05 套件正是靠这个做双屏一致性）；
- **索引查表模式**：CAN 8 字节放不下歌名/街道名，报文里传索引（TrackIndex/StreetIndex），GUI 侧查 `ivi_data.py` 表 —— 这是真实车机的标准做法。

**headless 快照新增字段**（05 套件断言用）：`media` / `navi` / `phone` / `hvac` / `trip`，见 `dashboard.py` 的 `snapshot()`。

---

## 9.12 三屏联动：完整座舱体验

仪表不是孤立的——它和**安卓车机**、**动力域 ECU** 一起组成迷你座舱。一条命令启动全部：

```bash
python start_cockpit.py --speed 60 --coolant 125
```

启动后你会看到三个进程（Windows 上两个 GUI 各有一个控制台窗口）：

```
┌────────────────────┐        ┌────────────────────┐        ┌────────────────────┐
│   ecu_sim.py       │        │  dashboard.py      │        │  android_gui.py    │
│   动力域 ECU        │        │  座舱仪表 GUI       │        │  安卓车机 GUI       │
│                    │ 0x100  │                    │ 0x400  │                    │
│  车速/转速/水温 ────┼───────►│  主页表盘+警告灯     │◄───────┼─ 媒体/导航/空调     │
│  档位/里程/电压 ────┼───────►│  行程/媒体/导航界面  │───────►│  （点哪播哪）       │
│  DTC+警告灯位图 ────┼───────►│                    │        │                    │
└────────────────────┘ 0x300  └────────────────────┘ 0x500  └────────────────────┘
         │                                                      │
         │  UDS 0x7E0/0x7E8 ◄──── uds_client.py（诊断仪）────────│
         │                                                      │
         └──────────── 虚拟 CAN 总线（UDP 组播 233.255.0.1）─────┘
```

**联动体验清单**（建议逐个试）：
1. 车机点《海阔天空》→ 仪表切到“媒体” tab：同样的歌名/歌手/播放状态；
2. 车机点“📍 建设大道”开导航 → 仪表“导航” tab：转向箭头 + 距离（每秒递减，模拟行驶）+ ETA；
3. 车机空调“＋”到 26℃ → 仪表“车辆信息”页：空调设定同步变；
4. 车机点联系人“妈妈”呼叫 → 仪表“电话” tab：来电卡片（2 秒后自动接听）；
5. ECU 水温到 125℃ → 仪表水温灯亮 → 诊断仪（`python simulators/uds_client.py`）`19 02 08` 读到 P0116；
6. 终端里 `python simulators/adb_sim.py shell am start -n com.vehicle.media/.MainActivity` → 车机 GUI 自己跳到媒体界面。

**这就是量产座舱域的架构缩影**：车机 SoC（安卓）通过 CAN 把要在仪表显示的信息推给仪表控制器；多块屏共用同一数据源，因此显示天然一致。05 套件把这套链路做成了自动化用例。

---

## 9.10 本章自检

1. HMI 自动化的本质是什么？"状态外置"和图像识别路线各有什么优劣？
2. `Telltale Should Be Lit` 为什么要轮询等待？timeout 该按什么定？
3. 为什么状态位断言语位运算而不断全等？
4. "清除 DTC"后为什么灯会灭？非保持型和保持型灯的行为差异？
5. 闭环测试设计四要点是什么？用本项目例子说明。

---

**下一章：[10 · ADB 与 Android 车载基础](10_ADB与Android车载基础.md)**

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | 本质：**把"显示状态"变成"可断言的数据"**。**状态外置**（读状态文件/属性树）优势：稳定（不受渲染、分辨率、主题影响）、快；劣势：需被测系统配合暴露状态。**图像识别**（OCR/模板匹配）优势：不依赖被测系统；劣势：脆弱（字体/主题/分辨率一变就挂）、慢 |
| 2 | 因为灯亮有**延迟**（水温爬升 + 2 个诊断周期确认）→ 必须轮询等。timeout 按**物理过程最长时间 + 余量**定（如 25s = 水温爬升约 15s + 确认周期 + 余量） |
| 3 | 因为状态字节是**位图**（8 个独立标志位），你只关心某一位；其他位可能是别的含义 → 用 `& 0x08 != 0` 判断。**用全等 `== 0x08` 会在其他位也置 1 时误判失败** |
| 4 | 清 DTC（`0x14`）后状态位归零 → **非保持型灯立即灭**；**保持型灯（如 MIL）**即使状态归零也可能保持亮，要满足特定条件（清码/跑够循环）才灭 |
| 5 | 四要点：①**造场景**（注入故障，如 coolant=125）②**验证感知**（等灯亮）③**验证记录**（等 DTC 出现）④**执行干预 + 验证恢复**（清除后确认系统仍能重新检测） |

---

**上一章：[08 · UDS 诊断测试实战](08_UDS诊断测试实战.md)** ｜ **下一章：[10 · ADB 与 Android 车载](10_ADB与Android车载基础.md)**
