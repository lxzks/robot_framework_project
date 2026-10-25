*** Settings ***
Documentation    场景 2：警告灯矩阵验证（数据驱动 + 全亮自检）
...              【业务背景】警告灯（telltale）是**安全相关**的显示项：
...              法规要求特定故障必须点亮特定灯。测试三个层面：
...                ① 该亮时亮（故障 → 对应灯）
...                ② 不该亮时别亮（正常行驶 → 全部熄灭）—— 最容易漏测
...                ③ 全亮自检（产线 EOL 用：一次验证 12 个灯的硬件通路）
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Variables ***
@{ALL_TELLTALES}    MilWarning    OilPressureLow    CoolantTempHigh    BatteryLow
...                 AbsFault    AirbagFault    SeatbeltWarning    TurnLeft
...                 TurnRight    HighBeam    LowBeam    ParkBrake

*** Test Cases ***
正常行驶时所有警告灯应熄灭
    [Tags]    smoke    hmi    matrix    REQ-TTL-001
    [Documentation]    负向基线：无故障时一个灯都不该亮。
    ...              （很多现场问题是"灯常亮"，根源就在这里没测）
    All Telltales Should Be Off

灯光自检例程应点亮全部 12 个警告灯
    [Tags]    smoke    hmi    matrix    REQ-TTL-002
    [Documentation]    31 01 0301 = 仪表灯光自检：点亮所有灯 2 秒。
    ...              产线 EOL 靠它一次性验证全部灯的硬件通路（比逐个测快 12 倍）
    ...              注意：0x31 例程在**默认会话不可用**（会报 0x7F），必须先扩会话
    Enter Extended Session
    Start Light Test Routine
    All Telltales Should Be Lit    timeout=3

自检清单应包含全部 12 个灯名
    [Tags]    hmi    matrix    REQ-TTL-002
    [Documentation]    数据驱动遍历：12 个灯名逐个确认出现在点亮清单中。
    ...              如果 ECU/仪表漏实现某个灯，这条用例会精确报出是哪个
    ...              （比"全亮测试失败"这种笼统信息有用得多）
    Enter Extended Session
    Start Light Test Routine
    ${lit}=    Get Lit Telltales
    FOR    ${telltale}    IN    @{ALL_TELLTALES}
        Should Contain    ${lit}    ${telltale}
        ...    msg=灯光自检未点亮 ${telltale}，实际点亮: ${lit}
    END

自检结束后所有灯应熄灭
    [Tags]    hmi    matrix    REQ-TTL-003
    [Documentation]    自检 4 秒后自动结束 —— 灯必须全部熄灭（不能"亮完不复位"）
    Enter Extended Session
    Start Light Test Routine
    All Telltales Should Be Lit    timeout=3
    Sleep    5s
    All Telltales Should Be Off
