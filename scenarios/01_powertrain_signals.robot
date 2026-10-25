*** Settings ***
Documentation    场景 1：动力域信号验证（范围 + 信号间联动）
...              【业务背景】仪表显示的所有动力信息都来自 ECU 的周期报文。
...              这类测试是每天都会跑的基础回归：确认信号①存在 ②范围合理
...              ③信号之间满足物理关系。第 ③ 点最关键 —— 单点范围断言查不出
...              "传感器模型算错"，但联动断言可以（如 转速 = 车速 × 80）。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=100
Suite Teardown   Stop Vehicle Environment

*** Variables ***
${TARGET_SPEED}    100

*** Test Cases ***
车速应稳定在目标值附近
    [Tags]    smoke    powertrain    REQ-CAN-004
    [Documentation]    目标 100km/h，容差 ±10（不是精确相等：物理量有噪声）
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    90    110    timeout=25
    Log    车速稳定于 ${speed} km/h ✓

转速应与车速保持设计比例
    [Tags]    smoke    powertrain    REQ-CAN-005
    [Documentation]    ECU 模型：rpm = max(800, speed × 80)
    ...              **联动验证**：先等车速到位再读转速，两者必须满足比例关系。
    ...              如果 ECU 的转速模型写错了，单看"转速在 0~16000 范围内"是查不出来的。
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    90    110    timeout=25
    ${rpm}=    Read Signal From Bus    EngineData    EngineSpeed
    ${expected}=    Evaluate    max(800, ${speed} * 80)
    ${diff}=    Evaluate    abs(${rpm} - ${expected})
    ${tolerance}=    Evaluate    max(200, ${expected} * 0.05)
    Should Be True    ${diff} <= ${tolerance}
    ...    msg=转速 ${rpm} 与期望=${expected}（车速×80）偏差 ${diff}，超过容差 ${tolerance}

油门在定速巡航时应有合理开度
    [Tags]    powertrain    REQ-CAN-001
    ${throttle}=    Read Signal From Bus    EngineData    ThrottlePosition
    Should Be True    0 <= ${throttle} <= 100
    ...    msg=油门开度 ${throttle}% 超出物理范围 0~100

档位在行驶时应为 D 档
    [Tags]    smoke    powertrain    REQ-CAN-006
    [Documentation]    能量模型：车速 > 1 → 档位 D(3)。区间写 3 3 = 等它精确等于 3
    Wait Until Signal In Range    ClusterData    GearPosition    3    3    timeout=25

里程应随行驶单调累积
    [Tags]    powertrain    REQ-CAN-007
    [Documentation]    连续采样 3 次，里程必须单调不减（累加器不该回退）。
    ...              实现技巧：**排序前后是否相同等价于单调不减** ——
    ...              比在 Evaluate 里手写列表推导更稳（RF 里跨语言访问列表元素
    ...              的语法容易踩坑：${list}[i] 会被当成 RF 索引语法）
    @{readings}=    Create List
    FOR    ${i}    IN RANGE    3
        ${odo}=    Read Signal From Bus    ClusterData    Odometer
        Append To List    ${readings}    ${odo}
        Sleep    1s
    END
    ${sorted}=    Copy List    ${readings}
    Sort List    ${sorted}
    Lists Should Be Equal    ${readings}    ${sorted}
    ...    msg=里程未单调累积: 采样 ${readings}，排序后 ${sorted}
    Log    里程采样序列: ${readings}

水温应在正常工作区间
    [Tags]    powertrain    REQ-CAN-008
    ${coolant}=    Wait Until Signal In Range    EngineData    CoolantTemp    80    105    timeout=25
    Log    水温 ${coolant}℃ 正常 ✓
