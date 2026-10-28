*** Settings ***
Documentation    套件 20：HIL 台架抽象与电源/IO 验证
...              被测对象：simulators/hil_rack.py —— HIL 台架 IO 板模拟器（TCP 127.0.0.1:9090，
...              端口可用环境变量 HIL_PORT 覆盖），模拟 KL30/KL15 电源、DI/DO、继电器、
...              过流保护与电源故障注入（欠压/过压/掉电/抖动）。
...              本套件验证 4 类需求：REQ-HIL-001 上下电时序 / REQ-HIL-002 电压控制与故障注入 /
...              REQ-HIL-003 IO 与继电器控制 / REQ-HIL-004 后端抽象与真实台架替换点。
...              **后端抽象**：libraries/HilLibrary.py 通过 HIL_BACKEND=sim|real 选择后端；
...              sim 后端跑本模拟器，real 后端会明确报"需接真实台架"（用例 10 验证该替换点）。
...              运行：.\.venv\Scripts\python.exe -m robot --outputdir results/dev_hil tests\robot\20_hil_rack.robot
Library          ../../libraries/HilLibrary.py
Library          OperatingSystem
Suite Setup      Start HIL Rack
Suite Teardown   Stop HIL Rack
Test Setup       Reset Rack
Test Timeout     30s

*** Test Cases ***
上电时序：KL30 应先于 KL15（含间隔时间校验）
    [Tags]    hil    power    timing    REQ-HIL-001
    [Documentation]    上电必须 KL30（常电）先就位，再等一段时间给 KL15（点火）：
    ...              真实 ECU 要求先完成上电自检/初始化，再收到点火信号。只断言最终
    ...              "12.6V + KL15 ON" 不够 —— 必须验证"谁先谁后"以及间隔是否真的存在。
    ${state}=    Power On Sequence    delay_ms=100
    ${log}=    Set Variable    ${state}[power][last_sequence]
    Log    上电时序记录: ${log}
    Should Be Equal    ${log}[0][step]    KL30_ON
    ...    msg=上电第一步必须是 KL30_ON：${log}
    Should Be Equal    ${log}[1][step]    KL15_ON
    ...    msg=上电第二步必须是 KL15_ON：${log}
    ${offset}=    Set Variable    ${log}[1][offset_ms]
    Should Be True    ${offset} >= 60
    ...    msg=KL30→KL15 间隔 ${offset}ms 过短（设定 100ms 的 60% 以下），时序可能没有真正等待
    Rack State Should Contain    kl15    True
    Rack State Should Contain    kl30.voltage    12.6
    Rack State Should Contain    power.state    ON
    Log    上电时序: KL30_ON → ${offset}ms → KL15_ON（KL30=12.6V，KL15=True）

默认 KL30 电压应为 12.6V（12V 蓄电池满电）
    [Tags]    hil    power    REQ-HIL-002
    [Documentation]    台架复位后的默认 KL30 电压应为 12.6V（12V 蓄电池满电静止电压）。
    ...              这是电源测试的基准点：后续欠压/过压/掉电注入都相对它判断。
    ${voltage}=    Get Kl30 Voltage
    Kl30 Voltage Should Be    12.6
    Rack State Should Contain    kl30.nominal_v    12.6
    Rack State Should Contain    power.fault_type    NONE
    Log    默认 KL30 电压实测: ${voltage} V（标称 12.6V，无电源故障）

欠压注入：KL30 应降到 9V 并可读回
    [Tags]    hil    power    fault_injection    REQ-HIL-002
    [Documentation]    注入欠压 9V（发动机冷启动/蓄电池亏电边界），台架读回必须一致，
    ...              并置 fault_type=UNDERVOLTAGE。真实 ECU 在此电压下可能记录 P0562、
    ...              关闭大功率负载 —— ECU 侧反应需真 ECU 才能验证（见 docs/38 边界表）。
    ${state}=    Inject Under Voltage
    Kl30 Voltage Should Be    9.0
    Rack State Should Contain    power.fault_type    UNDERVOLTAGE
    Rack State Should Contain    power.fault_active    True
    Log    欠压注入: KL30=${state}[kl30][voltage]V，fault_type=${state}[power][fault_type]

过压注入：KL30 应升到 16V，恢复后故障标记清除
    [Tags]    hil    power    fault_injection    REQ-HIL-002
    [Documentation]    注入过压 16V（发电机调节器失效/跨接启动），读回一致且故障标记置位；
    ...              把电压设回 12.6V 后故障标记必须清除 —— 证明"故障可恢复、不残留"。
    Inject Over Voltage
    Kl30 Voltage Should Be    16.0
    Rack State Should Contain    power.fault_type    OVERVOLTAGE
    Set Kl30 Voltage    12.6
    Kl30 Voltage Should Be    12.6
    Rack State Should Contain    power.fault_type    NONE
    Rack State Should Contain    power.fault_active    False

掉电注入与 PWM 电源抖动
    [Tags]    hil    power    fault_injection    REQ-HIL-002
    [Documentation]    掉电注入：KL30=0V（蓄电池断开/保险丝熔断），ECU 供电标记应为 False；
    ...              恢复 12.6V 后再注入 PWM 式电源抖动（线束虚接/接触器弹跳），
    ...              台架必须记录抖动波形（脉冲数/脉宽），且抖动结束后电压恢复正常。
    ${state}=    Inject Power Loss
    Kl30 Voltage Should Be    0.0
    Rack State Should Contain    power.fault_type    POWER_LOSS
    Should Not Be True    ${state}[power][ecu_powered]
    ...    msg=掉电后 ECU 应处于未供电状态：${state}[power]
    Set Kl30 Voltage    12.6
    Kl30 Voltage Should Be    12.6
    ${glitch}=    Inject Power Glitch    pulses=5    duration_ms=120    interval_ms=20
    Rack State Should Contain    glitch.count    1
    Kl30 Voltage Should Be    12.6
    Log    电源抖动记录: ${glitch}[glitch][last]

下电时序与 Power Cycle：恢复 12.6V 且瞬态状态清空
    [Tags]    hil    power    io    timing    REQ-HIL-001    REQ-HIL-003
    [Documentation]    ① 下电时序必须是 KL15 OFF → KL30 OFF（反了会让电压跌落中丢帧/异常）；
    ...              ② Power Cycle（硬件断电重启）后应恢复 12.6V、KL15 OFF、故障/IO/电流
    ...              等瞬态状态全部清空 —— 这是"真断电"与软件复位最本质的区别。
    ${off}=    Power Off Sequence    delay_ms=100
    ${offlog}=    Set Variable    ${off}[power][last_sequence]
    Log    下电时序记录: ${offlog}
    Should Be Equal    ${offlog}[0][step]    KL15_OFF
    ...    msg=下电第一步必须是 KL15_OFF：${offlog}
    Should Be Equal    ${offlog}[1][step]    KL30_OFF
    ...    msg=下电第二步必须是 KL30_OFF：${offlog}
    Kl30 Voltage Should Be    0.0
    Rack State Should Contain    kl15    False
    # 制造一些瞬态状态，再硬件断电重启，验证"状态清空"
    Power On Sequence
    Set Digital Output    MAIN_RELAY    True
    Set Digital Input    DOOR_OPEN    True
    Set Load Current    6.5
    ${state}=    Power Cycle    off_ms=200
    Kl30 Voltage Should Be    12.6
    Rack State Should Contain    kl15    False
    Rack State Should Contain    power.fault_type    NONE
    Rack State Should Contain    do.MAIN_RELAY    False
    Rack State Should Contain    di.DOOR_OPEN    False
    Rack State Should Contain    current.over_current    False
    Should Be True    ${state}[stats][power_cycle_count] >= 1
    ...    msg=Power Cycle 计数应至少为 1：${state}[stats]
    Log    Power Cycle 完成: 恢复 ${state}[kl30][voltage]V，累计 ${state}[stats][power_cycle_count] 次

DI 激励：设置后状态应可见
    [Tags]    hil    io    REQ-HIL-003
    [Documentation]    DI 模拟车身开关硬线输入（车门/安全袋/刹车/档位）。设置后读回必须一致，
    ...              这是"给 ECU 造激励"的基础；真实台架用 VT 板卡/继电器给硬线。
    Set Digital Input    DOOR_OPEN    True
    Set Digital Input    BRAKE_PRESSED    True
    Set Digital Input    SEATBELT_FASTENED    False
    Rack State Should Contain    di.DOOR_OPEN    True
    Rack State Should Contain    di.BRAKE_PRESSED    True
    Rack State Should Contain    di.SEATBELT_FASTENED    False
    ${door}=    Get Digital Input    DOOR_OPEN
    Should Be True    ${door}
    ${belt}=    Get Digital Input    SEATBELT_FASTENED
    Should Not Be True    ${belt}
    Log    DI 状态: DOOR_OPEN=${door}，SEATBELT_FASTENED=${belt}

DO 与继电器控制：写入回读一致且影响 ECU 供电标记
    [Tags]    hil    io    relay    REQ-HIL-003
    [Documentation]    DO 写入后读回必须一致（验证执行器输出链路）；ECU_POWER 继电器断开时
    ...              power.ecu_powered 必须为 False（模拟硬件断电）；恢复后重新为 True。
    Set Digital Output    INDICATOR_LAMP    True
    Set Digital Output    FAN    True
    Set Relay    ECU_POWER    False
    ${lamp}=    Get Digital Output    INDICATOR_LAMP
    Should Be True    ${lamp}
    ${fan}=    Get Digital Output    FAN
    Should Be True    ${fan}
    Rack State Should Contain    do.INDICATOR_LAMP    True
    Rack State Should Contain    relays.ECU_POWER    False
    Rack State Should Contain    power.ecu_powered    False
    Set Relay    ECU_POWER    True
    Rack State Should Contain    relays.ECU_POWER    True
    Rack State Should Contain    power.ecu_powered    True
    Set Digital Output    INDICATOR_LAMP    False
    ${lamp_off}=    Get Digital Output    INDICATOR_LAMP
    Should Not Be True    ${lamp_off}
    Log    DO/继电器回读一致：INDICATOR_LAMP 通断正常，ECU_POWER 通断影响 ecu_powered

电流限制模拟：超过 5A 应置过流标记
    [Tags]    hil    power    over_current    REQ-HIL-002
    [Documentation]    台架程控电源有电流上限（本项目教学值 5A）。默认 0.8A 不应标记过流；
    ...              设到 6.5A 后 over_current=True 且累计过流事件；电流回落后标记清除。
    ...              真实台架会直接限流/断电保护，这里是"标记 + 统计"的教学简化。
    Over Current Should Be Flagged    False
    ${state}=    Set Load Current    6.5
    Rack State Should Contain    current.over_current    True
    Over Current Should Be Flagged    True
    Should Be True    ${state}[current][amps] >= 5.0
    ...    msg=过流时电流应 ≥ 限值 5A：${state}[current]
    Should Be True    ${state}[stats][over_current_events] >= 1
    ...    msg=过流事件应被累计：${state}[stats]
    Set Load Current    0.8
    Over Current Should Be Flagged    False
    Log    过流保护验证：6.5A → 标记置位；回落到 0.8A → 标记清除

Backend 抽象：HIL_BACKEND=real 应明确报"需接真实台架"
    [Tags]    hil    backend    REQ-HIL-004
    [Documentation]    这是"抽象层 + 替换点"的教学验证：默认 sim 后端可用；
    ...              把 HIL_BACKEND 切成 real 后，后端必须**明确报错"需接真实台架"**，
    ...              而不是静默用假数据继续。台架没接就该红灯 —— HIL 测试的诚实性。
    ...              非法后端值同样要有清晰报错。用例结束前恢复 sim，避免污染环境。
    Backend Should Be    simulated
    Set Environment Variable    HIL_BACKEND    real
    Run Keyword And Expect Error    *需接真实台架*
    ...    Backend Should Be    simulated
    Run Keyword And Expect Error    *需接真实台架*
    ...    Start HIL Rack
    Set Environment Variable    HIL_BACKEND    foo
    Run Keyword And Expect Error    *HIL_BACKEND 只支持 sim|real*
    ...    Backend Should Be    simulated
    Set Environment Variable    HIL_BACKEND    sim
    Backend Should Be    simulated
    Log    Backend 抽象验证：sim 可用；real 明确报"需接真实台架"（替换点=RealBackend.command）
