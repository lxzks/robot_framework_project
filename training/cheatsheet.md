# 车载自动化脚本速查表

> 打印出来贴工位的那种。分四块：**RF 语法** / **项目关键字** / **诊断 hex** / **排错**。

---

## 一、Robot Framework 语法速查

### 基本结构

```robotframework
*** Settings ***
Documentation    这个套件干什么的
Resource         ../../resources/common.resource      # 路径相对本 .robot 文件；根目录写 resources/common.resource
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
| `Get Displayed Speed/Gear/Coolant/Odometer` | 读仪表显示值（转速请用 `Read Signal From Bus    EngineData    EngineSpeed`） |
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

### LIN 总线（LinLibrary，L7 用）

```robotframework
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation
```

| 关键字 | 用途 |
|---|---|
| `Start LIN Simulation` / `Stop LIN Simulation` | 起/停 LIN 仿真进程（独立进程，UDP 30001 / TCP 30002） |
| `Set LIN Slave Signal    0x10    rain_intensity=75` | 设置从机信号，返回 `since_seq` 标记 |
| `Wait For LIN Frame    0x10    timeout=2    since_seq=${m}` | 等一帧（带 since_seq 只等"操作之后"的新帧） |
| `LIN Frame Should Contain Signal    帧    rain_intensity    75    tolerance=0.5` | 按 LDF 断言物理值 |
| `LIN Checksum Should Be Valid    帧` | 独立重算校验和（0x3C 经典 / 其余增强） |
| `Send LIN Master Request    00 00 00 00 00 00 00 00` | 主请求帧（首字节 0x00=睡眠） |
| `LIN Bus Should Be Silent    0.3` | 断言接下来 0.3s 总线无帧 |
| `Wake Up LIN Bus` | 唤醒脉冲（返回 since_seq） |
| `Get LIN Statistics` | 统计：`sleeping/skipped/emitted/...` |
| `LIN Schedule Timing Should Be Within    expected_slot_ms=10` | 调度时基/周期/抖动断言 |

### DoIP（DoipLibrary，L8 用）

```robotframework
Resource         ../../resources/common.resource      # 提供 Start/Stop Vehicle Environment
Library          ../../libraries/DoipLibrary.py
Suite Setup      自定义关键字：Start Vehicle Environment + Start DoIP Gateway
```

| 关键字 | 用途 |
|---|---|
| `Start DoIP Gateway` / `Stop DoIP Gateway` | 起/停 DoIP 网关（TCP/UDP 13400） |
| `DoIP Vehicle Identification` | UDP 车辆识别（0x0001→0x0004），返回 VIN |
| `DoIP Vehicle Logical Address Should Be    0x0E80` / `DoIP Vehicle EID Should Be    001122334455` | 断言识别响应 |
| `DoIP Connect` / `DoIP Disconnect` | 建/断 TCP 连接 |
| `DoIP Routing Activation` | 路由激活（0x0005），返回 code（成功=0x10） |
| `DoIP Activated Tester Address Should Be` / `DoIP Entity Address Should Be` | 断言激活回显地址 |
| `DoIP Send Diagnostic    10 03` | 把 UDS 封装进 0x8001 发送 |
| `DoIP Read ACK` / `DoIP Read Response` | 读 0x8002 确认 / 读 0x8001 UDS 响应 |
| `DoIP Read VIN` | 经 DoIP 读 VIN（多帧透传） |
| `DoIP Should Get NACK    0x06    Diagnostic` | 断言 NACK（Generic/Diagnostic） |
| `DoIP Send Raw Frame    02fd123400000000` | 发原始 DoIP 帧（协议错误用例） |

### 视觉（VisionLibrary，L9 用）

```robotframework
Library          ../../libraries/VisionLibrary.py
Library          OperatingSystem
Suite Setup      Create Directory    ${OUT}
```

| 关键字 | 用途 |
|---|---|
| `Render Cluster State    ${{ {"speed": 60} }}    out.png` | 渲染仪表状态成 PNG（`${{ }}` 是内联表达式→字典） |
| `Compare With Golden    图    黄金图    tolerance=0.02` | 整图比对，返回差异比例 |
| `Compare Region With Golden    图    黄金图    x    y    w    h` | 只比对 ROI 区域 |
| `Image Diff Ratio    A    B` / `Images Should Differ    A    B    min_ratio=0.02` | 差异比例 / 断言确实不同 |
| `Needle Angle Should Be Near    图    67.5    tolerance=2.0` | 指针角度（车速/240×270°） |
| `Telltale Region Should Be Lit/Off    图    灯名` | 警告灯区域亮/灭（红色像素占比） |
| `Get Telltale Region    灯名` | 取区域坐标 `[x,y,w,h]`（不写死魔法数字） |
| `Save Diff Image` / `Count Red Pixels` | 保存差异图 / 统计红色像素（失败证据） |

### 信息安全（SecurityLibrary，L10 用）

| 关键字 | 用途 |
|---|---|
| `Unlock Sequence Should Succeed` | 27 01→27 02 正常解锁，返回 `[seed, key]` |
| `Request Seed` / `Send Wrong Key For Seed    ${seed}` | 取种子 / 发错误密钥 |
| `Security Access Should Fail With NRC    0x35` | 断言负响应 NRC |
| `Bypass Attempt Should Fail` | **漏洞回归**：未请求种子直接发 5A5A5A5A → 必须 NRC 0x24 |
| `Failed Attempts Should Lock Access` | 连续错误 → NRC 0x36 + 锁定 |
| `Replay Key Should Fail    ${old_key}` | 重放旧密钥必须失败 |
| `Fuzz UDS Requests    300` | 模糊测试，返回 `{total, positive, negative, no_response, alive, elapsed_s}` |
| `Malformed Frame Should Be Rejected    27 02    0x13` | 畸形帧必须回明确 NRC |
| `ECU Should Stay Alive` | fuzz 后确认 ECU 还活着 |
| `SecurityLibrary.Response Should Be Positive` | **同名关键字要加库名前缀**（与 DiagnosticLibrary 重名） |

### OTA 刷写（OtaLibrary，L11 用）

```robotframework
Resource         ../../resources/common.resource      # 提供 Start/Stop Vehicle Environment
Library          ../../libraries/OtaLibrary.py
Suite Setup      自定义关键字：Start Vehicle Environment + Start OTA Server
```

| 关键字 | 用途 |
|---|---|
| `Start OTA Server` / `Stop OTA Server` | 起/停 OTA 云端服务（TCP 8080，`OTA_HTTP_PORT` 可覆盖） |
| `Get OTA Manifest` | 取 manifest（版本/大小/分块/CRC/目标 ECU） |
| `Download OTA Image` | 分块下载镜像到内存 |
| `Check Preprogramming Conditions` | 预编程检查 `31 01 0203`（车速=0/电压正常） |
| `Enter Programming` | 编程会话 + 安全解锁（10 02 + 27） |
| `Flash OTA Image` | 刷写主链路（0x34/0x36/0x37 + 31 01 FF00 完整性校验） |
| `ECU Reset` | 复位生效（11 01） |
| `Read Software Version` / `Software Version Should Be    v1.1.0` | 读/断言 F195 软件版本 |
| `Inject Bad Image    crc` / `Should Refuse Image` | 注入坏包（crc/size/version）/ 断言被拒绝 |
| `Should Rollback To Version    v1.0.0` | 失败后旧版本仍可用（回滚） |
| `Flash With Retry` | 模拟丢块→重试成功（断点续传思想） |

### Appium UI 自动化（AppiumLibrary，L12 用）

```robotframework
Library          ../../libraries/AppiumLibrary.py
Suite Setup      Start Appium Server
Suite Teardown   Stop Appium Server
```

| 关键字 | 用途 |
|---|---|
| `Start Appium Server` / `Stop Appium Server` | 起/停 Appium mock 服务（HTTP 127.0.0.1:4723，`APPIUM_PORT` 可覆盖） |
| `Create Session` / `Delete Session` | 创建/删除 WebDriver session（capabilities：platformName/deviceName/appPackage/appActivity） |
| `Find Element    id=...` | 定位元素（id / xpath / accessibility id） |
| `Element Should Exist` / `Element Should Not Exist` | 元素存在性断言 |
| `Click Element    ${e}` | 点击元素 |
| `Get Element Text` / `Element Text Should Be` | 读/断言控件文本 |
| `Tap Coordinate    x    y` | 坐标点击（actions） |
| `Get Page Source` / `Page Source Should Contain` | 取控件树 XML / 断言包含包名或界面标识 |
| `Switch To Package    com.vehicle.navi` | 切换/断言当前界面 |
| `Get Screenshot    路径.png` | 截图保存（断言非空） |

### 新方向库的运行命令（都在项目根目录）

```bash
# LIN（起独立 LIN 仿真进程）
python -m robot --outputdir results/training training/L7_lin_signals/solution.robot
# DoIP（起 ECU + DoIP 网关，占用 13400）
python -m robot --outputdir results/training training/L8_doip_diag/solution.robot
# 视觉（纯离屏渲染，无需 ECU）
python -m robot --outputdir results/training training/L9_vision_assert/solution.robot
# 信息安全（起 ECU，fuzz 约十几秒）
python -m robot --outputdir results/training training/L10_security_fuzz/solution.robot
# OTA 刷写（起 ECU + OTA server，占用 8080）
python -m robot --outputdir results/training training/L11_ota_flash/solution.robot
# Appium UI（起 mock server，占用 4723，无需 ECU）
python -m robot --outputdir results/training training/L12_appium_ui/solution.robot
# 只跑全部参考答案自检
python training/check_all.py --level L12
```

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

## 三、诊断 hex 速查（最常用的 17 条）

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
python -m robot --outputdir results --loglevel DEBUG training/L1_first_case/exercise.robot
# 然后打开 results/log.html，逐关键字展开看参数和返回值

# 2. 手动手动跑一遍（先确认系统行为，再写断言）
python simulators/ecu_sim.py --speed 60      # 终端1
python simulators/uds_client.py              # 终端2：手动发 hex 试

# 3. 只跑失败的用例，快速迭代（把 L1_first_case 换成你当前的练习）
python -m robot --test "车速*" training/L1_first_case/exercise.robot
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
