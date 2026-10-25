*** Settings ***
Documentation    套件 01：CAN 总线信号测试
...              验证 ECU 模拟器周期报文的完整性、信号解码、信号值合理性。
...              对应真实工作：用 CANoe/PCAN 抓包验证信号矩阵（signal matrix）。
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
总线应同时存在三条周期报文
    [Tags]    REQ-CAN-001    REQ-CAN-002    REQ-CAN-003
    [Documentation]    ECU 每 100ms 发 0x100、200ms 发 0x200、500ms 发 0x300
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    Recorded Frames Should Contain ID    0x100
    Recorded Frames Should Contain ID    0x200
    Recorded Frames Should Contain ID    0x300

车速信号应在合理范围且接近设定值
    [Tags]    REQ-CAN-004
    [Documentation]    ECU 以 60km/h 目标启动，斜坡加速后应稳定在 60 附近
    Wait Until Signal In Range    EngineData    VehicleSpeed    50    70    timeout=20

转速信号应与车速联动
    [Tags]    REQ-CAN-005
    [Documentation]    模型：rpm = max(800, speed * 80)，即怠速 800
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    ${rpm}=    Read Signal From Bus    EngineData    EngineSpeed
    ${expected}=    Evaluate    max(800, ${speed} * 80)
    Should Be True    abs(${rpm} - ${expected}) < 300
    ...    msg=转速 ${rpm} 与理论值 ${expected} 偏差过大

水温信号初始应在正常工作区间
    [Tags]    REQ-CAN-008
    [Documentation]    冷启动 ECU 水温从 90℃ 开始（一阶惯性逼近目标）
    ${coolant}=    Read Signal From Bus    EngineData    CoolantTemp
    Should Be True    80 <= ${coolant} <= 105
    ...    msg=水温 ${coolant} 不在 80~105℃ 正常区间

档位应随车速从 P 切换到 D
    [Tags]    REQ-CAN-006
    [Documentation]    信号模型：speed < 1 → P(0)，否则 D(3)
    Wait Until Signal In Range    ClusterData    GearPosition    3    3    timeout=20

里程信号应随行驶累积
    [Tags]    REQ-CAN-007
    [Documentation]    Odometer 从 12345km 开始，速度 > 0 后持续累加
    ${odo1}=    Read Signal From Bus    ClusterData    Odometer
    Sleep    1s
    ${odo2}=    Read Signal From Bus    ClusterData    Odometer
    Should Be True    ${odo2} >= ${odo1}
    ...    msg=里程应单调不减: ${odo1} → ${odo2}

正常状态下所有警告灯报文应为熄灭
    [Tags]    REQ-TTL-001
    [Documentation]    0x300 TelltaleStatus 全 0 表示无警告灯
    ${raw}=    Wait For CAN Frame    0x300
    ${lit}=    Read Signal From Bus    TelltaleStatus    MilWarning
    Should Be Equal As Integers    ${lit}    0    msg=正常状态下 MIL 不应点亮

录制总线并导出 Trace 文件（CANoe 兼容）
    [Tags]    REQ-CAN-010
    [Documentation]    录 2 秒 → 导出 ASC（Vector 文本格式，CANoe 可直接打开）
    ...               与 BLF（Vector 二进制格式）→ 回读校验两种格式
    ...               用途：测试失败时归档 Trace，供事后分析"总线上到底发生了什么"
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    ${n}=    Recorded Frame Count
    Should Be True    ${n} > 10    msg=2 秒内应录到多帧周期报文，实际 ${n}
    ${asc_n}=    Export Bus Trace    ${ROOT}/results/bus_trace.asc
    ${blf_n}=    Export Bus Trace    ${ROOT}/results/bus_trace.blf
    Should Be Equal As Integers    ${asc_n}    ${n}
    Should Be Equal As Integers    ${blf_n}    ${n}
    Trace Files Should Be Valid
    ...    ${ROOT}/results/bus_trace.asc    ${ROOT}/results/bus_trace.blf    ${n}
