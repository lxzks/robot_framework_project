*** Settings ***
Documentation    自动生成的信号测试用例（由 generate_testcases.py 于 2026-10-13 11:44:53 生成）
...              **不要手工修改本文件** —— 改生成器脚本后重新生成。
...              生成策略：
...              1. 数值信号 → 边界值分析（BVA）：min-1/min/min+1/中点/max-1/max/max+1
...                 其中 min-1 与 max+1 为越界值，验证 ECU 对超范围输入的处理
...              2. 枚举信号 → 等价类：值表每个取值
...              3. 多信号 → pairwise 两两组合覆盖（避免全组合爆炸）
...              每条用例都是"发送 → 读回 → 比对"闭环，可直接 robot 运行。
...              需求追溯：[Tags] 里的 REQ-xxx 指向 requirements/requirements.yaml
...              （REQ-CAN-011 = 信号编解码正确性；生成的用例在 Suite Setup
...               用 0x28 暂停 ECU 周期报文，故也关联 REQ-DIAG-014）
Resource         ../../../resources/common.resource
Suite Setup      环境就绪并暂停 ECU 周期报文
Suite Teardown   恢复 ECU 周期报文并关闭环境

*** Test Cases ***

EngineData 信号边界值/等价类测试
    [Documentation]    自动生成：20 个测试点（4 个信号）—— 发送后从总线读回并断言一致
    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014
    [Template]    信号应正确收发
    EngineData    VehicleSpeed    0    最小值
    EngineData    VehicleSpeed    7    最小值+1LSB
    EngineData    VehicleSpeed    327.675    典型值(中点)
    EngineData    VehicleSpeed    648.35    最大值-1LSB
    EngineData    VehicleSpeed    655.35    最大值
    EngineData    EngineSpeed    0    最小值
    EngineData    EngineSpeed    160    最小值+1LSB
    EngineData    EngineSpeed    8000    典型值(中点)
    EngineData    EngineSpeed    15840    最大值-1LSB
    EngineData    EngineSpeed    16000    最大值
    EngineData    CoolantTemp    -40    最小值
    EngineData    CoolantTemp    -37    最小值+1LSB
    EngineData    CoolantTemp    87.5    典型值(中点)
    EngineData    CoolantTemp    212    最大值-1LSB
    EngineData    CoolantTemp    215    最大值
    EngineData    ThrottlePosition    0    最小值
    EngineData    ThrottlePosition    1    最小值+1LSB
    EngineData    ThrottlePosition    50    典型值(中点)
    EngineData    ThrottlePosition    99    最大值-1LSB
    EngineData    ThrottlePosition    100    最大值

EngineData 极值可发送性测试
    [Documentation]    自动生成：8 个极值测试点 —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据时协议层正常（物理越界值因位宽固定无法编码）
    [Tags]    generated    extreme    REQ-CAN-011
    [Template]    Out Of Range Value Should Be Sendable
    EngineData    VehicleSpeed    满量程极值(16bit)
    EngineData    VehicleSpeed    满量程极值(16bit)
    EngineData    EngineSpeed    满量程极值(16bit)
    EngineData    EngineSpeed    满量程极值(16bit)
    EngineData    CoolantTemp    满量程极值(8bit)
    EngineData    CoolantTemp    满量程极值(8bit)
    EngineData    ThrottlePosition    满量程极值(8bit)
    EngineData    ThrottlePosition    满量程极值(8bit)

ClusterData 信号边界值/等价类测试
    [Documentation]    自动生成：21 个测试点（4 个信号）—— 发送后从总线读回并断言一致
    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014
    [Template]    信号应正确收发
    ClusterData    GearPosition    0    P(0)
    ClusterData    GearPosition    1    R(1)
    ClusterData    GearPosition    2    N(2)
    ClusterData    GearPosition    3    D(3)
    ClusterData    GearPosition    4    S(4)
    ClusterData    GearPosition    5    L(5)
    ClusterData    FuelLevel    0    最小值
    ClusterData    FuelLevel    1    最小值+1LSB
    ClusterData    FuelLevel    50    典型值(中点)
    ClusterData    FuelLevel    99    最大值-1LSB
    ClusterData    FuelLevel    100    最大值
    ClusterData    Odometer    0    最小值
    ClusterData    Odometer    167772    最小值+1LSB
    ClusterData    Odometer    8.38861e+06    典型值(中点)
    ClusterData    Odometer    1.66094e+07    最大值-1LSB
    ClusterData    Odometer    1.67772e+07    最大值
    ClusterData    BatteryVoltage    0    最小值
    ClusterData    BatteryVoltage    1    最小值+1LSB
    ClusterData    BatteryVoltage    12.7    典型值(中点)
    ClusterData    BatteryVoltage    24.4    最大值-1LSB
    ClusterData    BatteryVoltage    25.4    最大值

ClusterData 极值可发送性测试
    [Documentation]    自动生成：6 个极值测试点 —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据时协议层正常（物理越界值因位宽固定无法编码）
    [Tags]    generated    extreme    REQ-CAN-011
    [Template]    Out Of Range Value Should Be Sendable
    ClusterData    FuelLevel    满量程极值(8bit)
    ClusterData    FuelLevel    满量程极值(8bit)
    ClusterData    Odometer    满量程极值(24bit)
    ClusterData    Odometer    满量程极值(24bit)
    ClusterData    BatteryVoltage    满量程极值(8bit)
    ClusterData    BatteryVoltage    满量程极值(8bit)

TelltaleStatus 信号边界值/等价类测试
    [Documentation]    自动生成：36 个测试点（12 个信号）—— 发送后从总线读回并断言一致
    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014
    [Template]    信号应正确收发
    TelltaleStatus    MilWarning    0    最小值
    TelltaleStatus    MilWarning    1    最小值+1LSB
    TelltaleStatus    MilWarning    0.5    典型值(中点)
    TelltaleStatus    OilPressureLow    0    最小值
    TelltaleStatus    OilPressureLow    1    最小值+1LSB
    TelltaleStatus    OilPressureLow    0.5    典型值(中点)
    TelltaleStatus    CoolantTempHigh    0    最小值
    TelltaleStatus    CoolantTempHigh    1    最小值+1LSB
    TelltaleStatus    CoolantTempHigh    0.5    典型值(中点)
    TelltaleStatus    BatteryLow    0    最小值
    TelltaleStatus    BatteryLow    1    最小值+1LSB
    TelltaleStatus    BatteryLow    0.5    典型值(中点)
    TelltaleStatus    AbsFault    0    最小值
    TelltaleStatus    AbsFault    1    最小值+1LSB
    TelltaleStatus    AbsFault    0.5    典型值(中点)
    TelltaleStatus    AirbagFault    0    最小值
    TelltaleStatus    AirbagFault    1    最小值+1LSB
    TelltaleStatus    AirbagFault    0.5    典型值(中点)
    TelltaleStatus    SeatbeltWarning    0    最小值
    TelltaleStatus    SeatbeltWarning    1    最小值+1LSB
    TelltaleStatus    SeatbeltWarning    0.5    典型值(中点)
    TelltaleStatus    TurnLeft    0    最小值
    TelltaleStatus    TurnLeft    1    最小值+1LSB
    TelltaleStatus    TurnLeft    0.5    典型值(中点)
    TelltaleStatus    TurnRight    0    最小值
    TelltaleStatus    TurnRight    1    最小值+1LSB
    TelltaleStatus    TurnRight    0.5    典型值(中点)
    TelltaleStatus    HighBeam    0    最小值
    TelltaleStatus    HighBeam    1    最小值+1LSB
    TelltaleStatus    HighBeam    0.5    典型值(中点)
    TelltaleStatus    LowBeam    0    最小值
    TelltaleStatus    LowBeam    1    最小值+1LSB
    TelltaleStatus    LowBeam    0.5    典型值(中点)
    TelltaleStatus    ParkBrake    0    最小值
    TelltaleStatus    ParkBrake    1    最小值+1LSB
    TelltaleStatus    ParkBrake    0.5    典型值(中点)

TelltaleStatus 极值可发送性测试
    [Documentation]    自动生成：24 个极值测试点 —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据时协议层正常（物理越界值因位宽固定无法编码）
    [Tags]    generated    extreme    REQ-CAN-011
    [Template]    Out Of Range Value Should Be Sendable
    TelltaleStatus    MilWarning    满量程极值(1bit)
    TelltaleStatus    MilWarning    满量程极值(1bit)
    TelltaleStatus    OilPressureLow    满量程极值(1bit)
    TelltaleStatus    OilPressureLow    满量程极值(1bit)
    TelltaleStatus    CoolantTempHigh    满量程极值(1bit)
    TelltaleStatus    CoolantTempHigh    满量程极值(1bit)
    TelltaleStatus    BatteryLow    满量程极值(1bit)
    TelltaleStatus    BatteryLow    满量程极值(1bit)
    TelltaleStatus    AbsFault    满量程极值(1bit)
    TelltaleStatus    AbsFault    满量程极值(1bit)
    TelltaleStatus    AirbagFault    满量程极值(1bit)
    TelltaleStatus    AirbagFault    满量程极值(1bit)
    TelltaleStatus    SeatbeltWarning    满量程极值(1bit)
    TelltaleStatus    SeatbeltWarning    满量程极值(1bit)
    TelltaleStatus    TurnLeft    满量程极值(1bit)
    TelltaleStatus    TurnLeft    满量程极值(1bit)
    TelltaleStatus    TurnRight    满量程极值(1bit)
    TelltaleStatus    TurnRight    满量程极值(1bit)
    TelltaleStatus    HighBeam    满量程极值(1bit)
    TelltaleStatus    HighBeam    满量程极值(1bit)
    TelltaleStatus    LowBeam    满量程极值(1bit)
    TelltaleStatus    LowBeam    满量程极值(1bit)
    TelltaleStatus    ParkBrake    满量程极值(1bit)
    TelltaleStatus    ParkBrake    满量程极值(1bit)

MediaPhoneData 信号边界值/等价类测试
    [Documentation]    自动生成：35 个测试点（8 个信号）—— 发送后从总线读回并断言一致
    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014
    [Template]    信号应正确收发
    MediaPhoneData    MediaStatus    0    STOP(0)
    MediaPhoneData    MediaStatus    1    PLAY(1)
    MediaPhoneData    MediaStatus    2    PAUSE(2)
    MediaPhoneData    MediaStatus    3    NEXT(3)
    MediaPhoneData    TrackIndex    0    最小值
    MediaPhoneData    TrackIndex    3    最小值+1LSB
    MediaPhoneData    TrackIndex    127.5    典型值(中点)
    MediaPhoneData    TrackIndex    252    最大值-1LSB
    MediaPhoneData    TrackIndex    255    最大值
    MediaPhoneData    Volume    0    最小值
    MediaPhoneData    Volume    1    最小值+1LSB
    MediaPhoneData    Volume    50    典型值(中点)
    MediaPhoneData    Volume    99    最大值-1LSB
    MediaPhoneData    Volume    100    最大值
    MediaPhoneData    CallStatus    0    NONE(0)
    MediaPhoneData    CallStatus    1    INCOMING(1)
    MediaPhoneData    CallStatus    2    ACTIVE(2)
    MediaPhoneData    CallerIndex    0    最小值
    MediaPhoneData    CallerIndex    3    最小值+1LSB
    MediaPhoneData    CallerIndex    127.5    典型值(中点)
    MediaPhoneData    CallerIndex    252    最大值-1LSB
    MediaPhoneData    CallerIndex    255    最大值
    MediaPhoneData    OutdoorTemp    -40    最小值
    MediaPhoneData    OutdoorTemp    -39    最小值+1LSB
    MediaPhoneData    OutdoorTemp    23.5    典型值(中点)
    MediaPhoneData    OutdoorTemp    86    最大值-1LSB
    MediaPhoneData    OutdoorTemp    87    最大值
    MediaPhoneData    AcOn    0    最小值
    MediaPhoneData    AcOn    1    最小值+1LSB
    MediaPhoneData    AcOn    0.5    典型值(中点)
    MediaPhoneData    HvacTemp    16    最小值
    MediaPhoneData    HvacTemp    17    最小值+1LSB
    MediaPhoneData    HvacTemp    24    典型值(中点)
    MediaPhoneData    HvacTemp    31    最大值-1LSB
    MediaPhoneData    HvacTemp    32    最大值

MediaPhoneData 极值可发送性测试
    [Documentation]    自动生成：12 个极值测试点 —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据时协议层正常（物理越界值因位宽固定无法编码）
    [Tags]    generated    extreme    REQ-CAN-011
    [Template]    Out Of Range Value Should Be Sendable
    MediaPhoneData    TrackIndex    满量程极值(8bit)
    MediaPhoneData    TrackIndex    满量程极值(8bit)
    MediaPhoneData    Volume    满量程极值(8bit)
    MediaPhoneData    Volume    满量程极值(8bit)
    MediaPhoneData    CallerIndex    满量程极值(8bit)
    MediaPhoneData    CallerIndex    满量程极值(8bit)
    MediaPhoneData    OutdoorTemp    满量程极值(8bit)
    MediaPhoneData    OutdoorTemp    满量程极值(8bit)
    MediaPhoneData    AcOn    满量程极值(1bit)
    MediaPhoneData    AcOn    满量程极值(1bit)
    MediaPhoneData    HvacTemp    满量程极值(7bit)
    MediaPhoneData    HvacTemp    满量程极值(7bit)

NaviHvacData 信号边界值/等价类测试
    [Documentation]    自动生成：34 个测试点（7 个信号）—— 发送后从总线读回并断言一致
    [Tags]    generated    bva    REQ-CAN-011    REQ-DIAG-014
    [Template]    信号应正确收发
    NaviHvacData    NaviTurn    0    NONE(0)
    NaviHvacData    NaviTurn    1    LEFT(1)
    NaviHvacData    NaviTurn    2    RIGHT(2)
    NaviHvacData    NaviTurn    3    STRAIGHT(3)
    NaviHvacData    NaviTurn    4    SLIGHT_LEFT(4)
    NaviHvacData    NaviTurn    5    SLIGHT_RIGHT(5)
    NaviHvacData    NaviTurn    6    UTURN(6)
    NaviHvacData    NaviTurn    7    ARRIVE(7)
    NaviHvacData    NaviDistance    0    最小值
    NaviHvacData    NaviDistance    655    最小值+1LSB
    NaviHvacData    NaviDistance    32767.5    典型值(中点)
    NaviHvacData    NaviDistance    64880    最大值-1LSB
    NaviHvacData    NaviDistance    65535    最大值
    NaviHvacData    StreetIndex    0    最小值
    NaviHvacData    StreetIndex    3    最小值+1LSB
    NaviHvacData    StreetIndex    127.5    典型值(中点)
    NaviHvacData    StreetIndex    252    最大值-1LSB
    NaviHvacData    StreetIndex    255    最大值
    NaviHvacData    FanLevel    0    最小值
    NaviHvacData    FanLevel    1    最小值+1LSB
    NaviHvacData    FanLevel    3.5    典型值(中点)
    NaviHvacData    FanLevel    6    最大值-1LSB
    NaviHvacData    FanLevel    7    最大值
    NaviHvacData    SeatHeat    0    最小值
    NaviHvacData    SeatHeat    1    最小值+1LSB
    NaviHvacData    SeatHeat    1.5    典型值(中点)
    NaviHvacData    SeatHeat    2    最大值-1LSB
    NaviHvacData    SeatHeat    3    最大值
    NaviHvacData    Recirculate    0    最小值
    NaviHvacData    Recirculate    1    最小值+1LSB
    NaviHvacData    Recirculate    0.5    典型值(中点)
    NaviHvacData    Defrost    0    最小值
    NaviHvacData    Defrost    1    最小值+1LSB
    NaviHvacData    Defrost    0.5    典型值(中点)

NaviHvacData 极值可发送性测试
    [Documentation]    自动生成：12 个极值测试点 —— 把信号填满（全 1 位）发送，验证 ECU 收到最大可能数据时协议层正常（物理越界值因位宽固定无法编码）
    [Tags]    generated    extreme    REQ-CAN-011
    [Template]    Out Of Range Value Should Be Sendable
    NaviHvacData    NaviDistance    满量程极值(16bit)
    NaviHvacData    NaviDistance    满量程极值(16bit)
    NaviHvacData    StreetIndex    满量程极值(8bit)
    NaviHvacData    StreetIndex    满量程极值(8bit)
    NaviHvacData    FanLevel    满量程极值(4bit)
    NaviHvacData    FanLevel    满量程极值(4bit)
    NaviHvacData    SeatHeat    满量程极值(2bit)
    NaviHvacData    SeatHeat    满量程极值(2bit)
    NaviHvacData    Recirculate    满量程极值(1bit)
    NaviHvacData    Recirculate    满量程极值(1bit)
    NaviHvacData    Defrost    满量程极值(1bit)
    NaviHvacData    Defrost    满量程极值(1bit)

EngineData 多信号 pairwise 组合测试
    [Documentation]    自动生成：pairwise 两两覆盖（3 信号 × 3 值 = 全组合 27 组 → 优化为 11 组）
    [Tags]    generated    pairwise    REQ-CAN-011
    [Template]    信号组合应正确收发
    EngineData    VehicleSpeed=0    EngineSpeed=0    CoolantTemp=-40
    EngineData    VehicleSpeed=655.35    EngineSpeed=8000    CoolantTemp=-40
    EngineData    EngineSpeed=16000    CoolantTemp=-40    VehicleSpeed=328
    EngineData    VehicleSpeed=655.35    CoolantTemp=215    EngineSpeed=0
    EngineData    EngineSpeed=16000    CoolantTemp=88    VehicleSpeed=0
    EngineData    VehicleSpeed=328    EngineSpeed=8000    CoolantTemp=88
    EngineData    EngineSpeed=0    CoolantTemp=88    VehicleSpeed=328
    EngineData    VehicleSpeed=328    CoolantTemp=215    EngineSpeed=8000
    EngineData    EngineSpeed=16000    CoolantTemp=215    VehicleSpeed=0
    EngineData    VehicleSpeed=0    EngineSpeed=8000    CoolantTemp=-40
    EngineData    VehicleSpeed=655.35    CoolantTemp=88    EngineSpeed=16000

*** Keywords ***
环境就绪并暂停 ECU 周期报文
    [Documentation]    启动环境 → 进扩展会话 → 28 01 01 暂停 ECU 周期报文。
    ...               必须暂停：ECU 也在周期发同一报文，不暂停的话测试注入
    ...               的值与 ECU 的周期帧混在总线上，读回结果不可信。
    Start Vehicle Environment
    Enter Extended Session
    Suspend ECU Periodic Messages

恢复 ECU 周期报文并关闭环境
    [Documentation]    28 00 恢复周期报文 → 关闭环境。
    ...               S3 定时器 5 秒会踢回默认会话，所以恢复前要重新进扩展会话。
    Run Keyword And Ignore Error    Enter Extended Session
    Run Keyword And Ignore Error    Resume ECU Periodic Messages
    Stop Vehicle Environment

信号应正确收发
    [Documentation]    发一个信号值 → 从总线读回 → 断言一致（单信号闭环）
    [Arguments]    ${msg}    ${sig}    ${value}    ${desc}
    Send Signal Values    ${msg}    ${sig}=${value}
    ${actual}=    Read Signal From Bus    ${msg}    ${sig}
    ${tolerance}=    Evaluate    max(abs(float(${value})) * 0.02, 0.5)
    ${diff}=    Evaluate    abs(float(${actual}) - float(${value}))
    Should Be True    ${diff} <= ${tolerance}
    ...    msg=${msg}.${sig} 期望 ${value} 实际 ${actual}（${desc}）

信号组合应正确收发
    [Documentation]    一次发同一报文的多个信号 → 读回**同一帧**并校验全部
    ...               （一次编码发一帧，一帧里含多信号；不能"发 1 帧读 N 次"）
    ...               [Template] 每行格式：报文名    短信号名=值    ...
    [Arguments]    ${msg}    &{signals}
    Send Signal Values    ${msg}    &{signals}
    Read Frame And Verify Signals    ${msg}    &{signals}
