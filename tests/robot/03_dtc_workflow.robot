*** Settings ***
Documentation    套件 03：DTC 故障全流程测试（诊断 + 仪表联动）
...              本套件的重头戏：模拟"水温飙高"故障的完整生命周期：
...              故障注入 → pending → confirmed → 警告灯亮 → 诊断仪确认
...              → 0x14 清除 → 灯灭。这是仪表盘测试工程师的日常。
Resource        ../../resources/common.resource
Library          ../../libraries/DiagnosticLibrary.py
Library          ../../libraries/DashboardLibrary.py
Suite Setup      Start Vehicle Environment    speed=60    coolant=125
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
水温超限应点亮水温报警灯并生成 DTC
    [Tags]    REQ-DTC-001    REQ-DTC-004
    [Documentation]    ECU 以 125℃ 启动（阈值 120℃）：
    ...              循环1 pending → 循环2 confirmed → 请求点灯
    Telltale Should Be Lit    CoolantTempHigh    timeout=15

诊断仪应能读到 confirmed 状态的 P0116
    [Tags]    REQ-DTC-001
    [Documentation]    19 02 08 按 confirmed 掩码读取，应返回 P0116
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116

P0116 状态位应包含 pending 与 confirmed
    [Tags]    REQ-DTC-001
    [Documentation]    0x19 02 FF 读全部，P0116 状态应为 0x2C
    ...              (bit2 pending=0x04 + bit3 confirmed=0x08 + bit5 failedSinceClear=0x20)
    ...              实际状态还会有 testFailed(0x01)/warningIndicator(0x80) 等位，
    ...              所以断言用位运算检查关键位，不做全等比较
    ${dtcs}=    Read DTC By Mask    FF
    ${status}=    Set Variable    ${EMPTY}
    FOR    ${item}    IN    @{dtcs}
        IF    '${item}[0]' == 'P0116'
            ${status}=    Set Variable    ${item}[1]
        END
    END
    Should Not Be Empty    ${status}    msg=P0116 不在 DTC 列表中
    ${status_int}=    Evaluate    int('${status}', 16)
    ${has_pending}=    Evaluate    (${status_int} & 0x04) != 0
    ${has_confirmed}=    Evaluate    (${status_int} & 0x08) != 0
    Should Be True    ${has_pending}    msg=P0116 缺少 pending 位(0x04)
    Should Be True    ${has_confirmed}    msg=P0116 缺少 confirmed 位(0x08)

DTC 快照应记录故障发生时的车速
    [Tags]    REQ-DTC-005
    [Documentation]    19 03 读快照（冻结帧）：含故障时刻的车速
    ${resp}=    Send Uds Request    19 03
    Response Should Be Positive

清除 DTC 后报警灯应熄灭
    [Tags]    REQ-DTC-002    REQ-DTC-004
    [Documentation]    14 FF FF FF 清除 → DTC 数量归零 → 非保持型灯熄灭
    Clear DTC
    ${count}=    Read DTC Count
    Should Be Equal As Integers    ${count}    0
    Telltale Should Be Off    CoolantTempHigh    timeout=5

清除后 confirmed 列表应为空
    [Tags]    REQ-DTC-002
    [Documentation]    故障仍在（125℃），清除后 pending 会立即重建，
    ...              但 confirmed 必须为空 —— 这正是 0x14 的语义
    ${confirmed}=    Read DTC By Mask    08
    Should Be Empty    ${confirmed}

水温持续超限故障应再次确认
    [Tags]    REQ-DTC-003
    [Documentation]    ECU 仍在 125℃，2 个循环后重新 confirmed（诊断自恢复场景）
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116
    Telltale Should Be Lit    CoolantTempHigh    timeout=5
