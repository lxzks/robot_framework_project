# 车载自动化脚本速查表

> 打印出来贴工位的那种。分四块：**RF 语法** / **项目关键字** / **诊断 hex** / **排错**。

---

## 一、Robot Framework 语法速查

### 基本结构

```robotframework
*** Settings ***
Documentation    这个套件干什么的
Resource         ../../resources/common.resource      # 引入共享关键字
Library          CustomLibrary.py                     # 引入 Python 库
Suite Setup      Start Vehicle Environment            # 套件级前置
Suite Teardown   Stop Vehicle Environment             # 套件级收尾
Test Setup       ...                                  # 用例级前置
Test Teardown    ...                                  # 用例级收尾

*** Variables ***
${SPEED}         60
@{SIGNALS}       VehicleSpeed    EngineSpeed          # 列表
&{DICT}          key=value

*** Test Cases ***
用例名字要能读懂在验什么
    [Documentation]    用例意图、前置条件、为什么这么断言
    [Tags]    smoke    diagnostic
    [Template]    被调用关键字        # 数据驱动（参数写在下面每行）
    参数1    参数2

*** Keywords ***
我的关键字
    [Documentation]    做什么、参数含义
    [Arguments]    ${arg1}    ${arg2}=默认值
    步骤一    ${arg1}
    ${结果}=    步骤二
    RETURN    ${结果}                 # 返回（RF 7+）
```

### 变量与断言

```robotframework
${var}=    关键字                      # 赋值
Log    ${var}                          # 写日志（进 log.html）
Log To Console    输出到终端
Should Be Equal    ${a}    ${b}    msg=失败时显示
Should Be Equal As Integers    ${n}    3
Should Be True    ${x} > 10    msg=...
Should Contain    ${text}    ${sub}
Should Not Be Empty    ${list}
Length Should Be    ${s}    17
```

### 流程控制

```robotframework
IF    ${x} > 10
    Log    大
ELSE IF    ${x} > 5
    Log    中
ELSE
    Log    小
END

FOR    ${item}    IN    @{list}
    Log    ${item}
END

FOR    ${k}    ${v}    IN    &{dict}
    Log    ${k}=${v}
END

WHILE    ${i} < 3
    ${i}=    Evaluate    ${i} + 1
END
```

### 等待与重试（车载最常用）

```robotframework
Sleep    2s                                  # ⚠️ 尽量别用（猜时间）
Wait Until Signal In Range    EngineData    VehicleSpeed    50    70    timeout=20
Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116
Telltale Should Be Lit    CoolantTempHigh    timeout=25
```

> ⚠️ `Wait Until Keyword Succeeds` 只检测"关键字抛不抛异常"。
> 等**值**进范围必须用 `Wait Until Signal In Range`（库内轮询）。
> 但等"断言成立"时它是对的（因为被等待的关键字自己会断言）。

### 期望失败（负向测试）

```robotframework
Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
Run Keyword And Expect Error    *NRC=0x33*    关键字
```

### Python 表达式

```robotframework
${max}=    Evaluate    max(${a}, ${b})
${hex}=    Evaluate    hex(int(${dec}))
${diff}=   Evaluate    abs(float(${x}) - float(${y}))
```

---

## 二、项目关键字速查（本项目 libraries/）

### 总线操作（VehicleBusLibrary）

| 关键字 | 用途 |
|---|---|
| `Send CAN Frame    ID    HEX` | 发一帧原始报文 |
| `Send Signal Values    报文    信号=值` | 按 DBC 编码发送（缺的信号自动补默认值） |
| `Read Signal From Bus    报文    信号` | 读一帧并解出信号物理值 |
| `Wait Until Signal In Range    报文    信号    低    高    timeout=20` | 轮询等信号进区间 |
| `Wait For CAN Frame    ID` | 等指定 ID 的帧，返回 hex |
| `Start/Stop Bus Recorder` + `Drain Bus For    2` | 录制总线 |
| `Export Bus Trace    xxx.asc` | 导出 Trace（CANoe 兼容） |
| `Out Of Range Value Should Be Sendable    报文    信号` | 极值（位域填满）测试 |
| `Read Frame And Verify Signals    报文    信号=值 ...` | 读一帧校验多信号 |

### 诊断（DiagnosticLibrary）

| 关键字 | 用途 |
|---|---|
| `Send UDS Request    22 F187` | 发裸请求，返回响应描述 |
| `Response Should Be Positive` | 断言正响应 |
| `Response Should Be Negative With NRC    0x33` | 断言负响应 + NRC |
| `ECU Should Respond` | 验证 ECU 在线（3E 00） |
| `Enter Extended Session` / `Enter Programming Session` / `Enter Default Session` | 切会话 |
| `Security Unlock` | 27 01/02 握手解锁 |
| `Read DID As ASCII    F187` / `Read DID    Fxxx` | 读 DID |
| `Write DID    F1A0    数据hex` | 写 DID（需扩展会话+解锁） |
| `Read DTC Count` / `Read DTC By Mask    08` / `DTC List Should Contain    P0116` | DTC 操作 |
| `Clear DTC` | 清除 DTC（0x14） |
| `Start Routine    0200` / `Start Light Test Routine` | 例程 |
| `Suspend/Resume ECU Periodic Messages` | 0x28 暂停/恢复周期报文 |
| `Request Download` / `Transfer Data Block` / `Request Transfer Exit` / `Check Programming Integrity` | 刷写四步 |
| `Measure Response Time    22 F187` | 测量响应耗时（时序一致性） |

### 仪表/HMI（DashboardLibrary）

| 关键字 | 用途 |
|---|---|
| `Get Displayed Speed/Rpm/Coolant/Gear/Odometer` | 读仪表显示值 |
| `Telltale Should Be Lit/Off    灯名    timeout=5` | 断言警告灯亮/灭 |
| `All Telltales Should Be Lit/Off` | 灯光测试 |
| `Dashboard Should Be Ready` | 环境就绪 |
| `Media Track Should Be    歌名` / `Navi Distance Should Be Below    2000` / `Hvac Temp Should Be    24` | 座舱 IVI 断言 |
| `Trip Should Be Accumulating` | 行程电脑累计 |

### 车机/Android（AdbLibrary）

| 关键字 | 用途 |
|---|---|
| `Device Should Be Connected    VEHICLE2026AUTO` | 设备在线 |
| `Get Prop    ro.product.model` | 读系统属性 |
| `Install APK / Uninstall Package` | 装/卸应用 |
| `Start Activity    包名/.Activity` | 启动界面 |
| `Current Focus Should Contain    包名` | 断言当前前台界面 |
| `Tap / Send Keyevent / Swipe` | 模拟输入 |
| `Battery Level Should Be Greater Than    50` | 电量断言 |
| `Set Headunit Navi/Media/Hvac State` | 模拟车机操作 |
| `Broadcast Headunit State To Bus` | 显式广播 IVI 状态到 CAN |

### 环境管理（common.resource）

```robotframework
Start Vehicle Environment    speed=60    coolant=125    with_android=True
Stop Vehicle Environment
```

| 参数 | 含义 |
|---|---|
| `speed` | ECU 目标车速（km/h），斜坡爬升 8/s |
| `coolant` | ECU 目标水温（℃），>120 触发 P0116 |
| `with_android` | 是否同时启动安卓车机（headless） |

---

## 三、诊断 hex 速查（最常用的 15 条）

| 操作 | 请求 | 正响应 | 说明 |
|---|---|---|---|
| 进默认会话 | `10 01` | `50 01 ...` | |
| 进扩展会话 | `10 03` | `50 03 00 32 01 F4` | P2=50ms, P2*=5000ms |
| 进编程会话 | `10 02` | `50 02 ...` | 刷写前置 |
| ECU 复位 | `11 01` | `51 01` | 会话回默认、清刷写状态 |
| 清 DTC | `14 FF FF FF` | `54` | 全清 |
| 读 DTC 数量 | `19 01` | `59 01 FF nn` | nn=数量 |
| 读 confirmed DTC | `19 02 08` | `59 02 FF [3B][1B]...` | 掩码 08=confirmed |
| 读快照 | `19 03` | `59 03 ...` | 冻结帧 |
| 读零件号 | `22 F1 87` | `62 F1 87 "HW-IC-..."` | |
| 读 VIN | `22 F1 90` | `62 F1 90 + 17B` | **多帧** |
| 要种子 | `27 01` | `67 01 [seed4B]` | |
| 送密钥 | `27 02 [key4B]` | `67 02` | key = seed XOR 5A5A5A5A |
| 写 DID | `2E F1 A0 [data]` | `6E F1 A0` | 需扩展会话+解锁 |
| 启动例程 | `31 01 0200` | `71 01 02 00 00` | 02 00=自检 |
| 保活 | `3E 00` | `7E 00` | 抑制响应：`3E 80` |
| 暂停通信 | `28 01 01` | `68 01 01` | 停发周期报文 |
| 暂停 DTC 检测 | `85 02` | `C5 02` | |

### NRC 速查

| NRC | 名称 | 中文 |
|---|---|---|
| `0x11` | serviceNotSupported | 服务不支持 |
| `0x12` | subFunctionNotSupported | 子功能不支持 |
| `0x13` | incorrectMessageLengthOrInvalidFormat | 长度/格式错 |
| `0x22` | conditionsNotCorrect | 条件不满足 |
| `0x24` | requestSequenceError | 序列错误（前置没做） |
| `0x31` | requestOutOfRange | 参数超范围 |
| `0x33` | securityAccessDenied | 未解锁 |
| `0x35` | invalidKey | 密钥错 |
| `0x73` | wrongBlockSequenceCounter | 块序号错（刷写） |
| `0x78` | responsePending | 处理中，继续等 |
| `0x7F` | serviceNotSupportedInActiveSession | 当前会话不支持 |

### 警告灯名（Telltale）

`MilWarning` `OilPressureLow` `CoolantTempHigh` `BatteryLow` `AbsFault`
`AirbagFault` `SeatbeltWarning` `TurnLeft` `TurnRight` `HighBeam`
`LowBeam` `ParkBrake`

### 常用信号（DBC）

| 报文 | 信号 |
|---|---|
| `EngineData`(0x100) | `VehicleSpeed`(km/h) `EngineSpeed`(rpm) `CoolantTemp`(℃) `ThrottlePosition`(%) |
| `ClusterData`(0x200) | `GearPosition`(0=P..5=L) `FuelLevel`(%) `Odometer`(km) `BatteryVoltage`(V) |
| `TelltaleStatus`(0x300) | 12 个警告灯位 |
| `MediaPhoneData`(0x400) | `MediaStatus` `TrackIndex` `Volume` `CallStatus` `CallerIndex` |
| `NaviHvacData`(0x500) | `NaviTurn` `NaviDistance` `StreetIndex` `FanLevel` |

---

## 四、排错速查

| 报错 | 原因 | 解决 |
|---|---|---|
| `No keyword with name 'X' found` | 关键字名写错/库没引入 | 查本速查表；确认 Resource 引入 |
| `Variable '${X}' not found` | 变量未定义/拼写错 | 检查 `${}` 语法；变量名大小写不敏感 |
| `Setting 'Template' accepts only one value` | `[Template] 关键字 参数` 写法 | 参数放数据行里 |
| `3.0s 内没等到期望的数据` | 超时：ECU 没响应/信号没来 | **先确认环境**（`ECU Should Respond`）；再看超时是否够 |
| 偶发失败（今天过明天不过） | 用了 `Sleep` 猜时间 | 换轮询等待 |
| `JSONDecodeError` / `PermissionError` | 状态文件读写竞争 | 环境问题，重跑；持续出现查 06 章坑 12 |
| 断言失败但"看起来对" | 类型问题（字符串 vs 数字） | 用 `Should Be Equal As Numbers` / 先 `Log` 看实际值 |
| 套件 Setup 失败 | 环境没起来 | 看 `results/ecu_stdout.log` 和 `dashboard_stdout.log` |
| `Expected number, got list` | RF **变量名大小写不敏感**，`${SAMPLES}` 与 `${samples}` 撞车 | 换变量名（`${SAMPLE_COUNT}`） |
| `invalid index` | Evaluate 里写了 `${list}[i]` | 用 `Sort List`+`Lists Should Be Equal`，或 `$list` |
| 跨系统比较对不上 | 动态值还在变化中就比较 | 先等稳态（进入目标区间）再比 |

### 调试三板斧

```bash
# 1. 看详细日志（最关键！）
python -m robot --outputdir results --loglevel DEBUG training/LX/exercise.robot
# 然后打开 results/log.html，逐关键字展开看参数和返回值

# 2. 手动手动跑一遍（先确认系统行为，再写断言）
python simulators/ecu_sim.py --speed 60      # 终端1
python simulators/uds_client.py              # 终端2：手动发 hex 试

# 3. 只跑失败的用例，快速迭代
python -m robot --test "车速*" training/LX/exercise.robot
```

---

## 五、写脚本的十条军规

1. **用例名说清在验什么**（"车速应处于合理范围" ✅ / "测试1" ❌）
2. **断言用范围不用精确值**（物理量有噪声和时序）
3. **失败信息带实际值**（`msg=车速 ${speed} 不在 50~70`）
4. **用轮询等待，不用 Sleep 猜时间**
5. **等待的超时值要有物理依据**（写注释说明为什么是 25 秒）
6. **正向和负向都要测**（该拒绝时真拒绝了吗）
7. **重复的逻辑抽成关键字**（三处以上就抽）
8. **造场景用 Suite Setup，不要在每个用例里重复造**
9. **用例之间要隔离**（前一个用例改的状态，后一个要么重置要么不依赖）
10. **跑通 ≠ 学会**：对照自查问题，能讲出"为什么这么写"才算
