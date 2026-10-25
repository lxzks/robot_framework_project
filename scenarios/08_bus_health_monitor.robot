*** Settings ***
Documentation    场景 8：总线健康度监控（周期一致性 + 抖动分析）
...              【业务背景】ECU 报文的**周期是设计指标**（车速 100ms / 水温 200ms /
...              灯状态 500ms）。周期跑偏或抖动过大会让依赖它的节点异常
...              （仪表指针跳、ABS 判断延迟）。
...              台架上用 CANoe 的 Trace 分析看这个；我们用录制器自己算，
...              思路完全一致 —— 这是"总线级"的体检，比单点断言更有全局视野。
Resource         ../resources/common.resource
Suite Setup      启动环境并监控总线 5 秒
Suite Teardown   Stop Vehicle Environment

*** Variables ***
${MONITOR_SECONDS}    5

*** Test Cases ***
三条周期报文应全部出现
    [Tags]    smoke    bus    REQ-CAN-001    REQ-CAN-002    REQ-CAN-003
    [Documentation]    存在性：少一条说明 ECU 有功能缺陷（或没启动）
    Recorded Frames Should Contain ID    0x100
    Recorded Frames Should Contain ID    0x200
    Recorded Frames Should Contain ID    0x300

车速报文周期应为 100ms 且抖动可控
    [Tags]    smoke    bus    REQ-CAN-001    REQ-CAN-009
    [Documentation]    容差 ±20ms；抖动（最大间隔-最小间隔）< 40ms
    ...              真实工作里"周期均值对但抖动大"是最隐蔽的问题 —— 平均看正常，
    ...              但个别帧晚了几十毫秒，接收方就可能超时
    Period And Jitter Should Be Within    0x100    100
    ...    period_tolerance_ms=20    jitter_tolerance_ms=40

仪表报文周期应为 200ms
    [Tags]    bus    REQ-CAN-002    REQ-CAN-009
    Period And Jitter Should Be Within    0x200    200
    ...    period_tolerance_ms=40    jitter_tolerance_ms=60

警告灯报文周期应为 500ms
    [Tags]    bus    REQ-CAN-003    REQ-CAN-009
    Period And Jitter Should Be Within    0x300    500
    ...    period_tolerance_ms=80    jitter_tolerance_ms=120

录制内容应能导出为 CANoe 兼容 Trace
    [Tags]    bus    REQ-CAN-010
    [Documentation]    失败时 Trace 是唯一的"现场证据"，格式必须能被 CANoe 打开
    ${n}=    Export Bus Trace    ${ROOT}/results/scenario_bus_health.asc
    ${n2}=    Export Bus Trace    ${ROOT}/results/scenario_bus_health.blf
    Should Be Equal As Integers    ${n}    ${n2}
    Trace Files Should Be Valid
    ...    ${ROOT}/results/scenario_bus_health.asc
    ...    ${ROOT}/results/scenario_bus_health.blf    ${n}

*** Keywords ***
启动环境并监控总线 ${seconds} 秒
    [Documentation]    在 Suite Setup 完成"造场景"：起环境 + 录一段总线
    ...              各用例只负责分析 —— 避免"用例之间抢着录制"的顺序依赖
    Start Vehicle Environment    speed=60
    Start Bus Recorder
    Drain Bus For    ${seconds}
    Stop Bus Recorder
