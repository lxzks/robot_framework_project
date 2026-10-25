*** Settings ***
Documentation    场景 12：网络管理（NM）与电源模式 / 休眠唤醒
...              【业务背景】车辆熄火后所有 ECU 必须协商进入低功耗睡眠 ——
...              整车"暗电流"通常要求 < 20mA，任何一个节点偷偷发报文都会
...              导致亏电（车主第二天打不着火）。协商机制是三重的：
...                ① **电源模式广播**：BCM 通过 CAN 0x600 广播 IGN OFF
...                ② **网络管理协商**：各节点发 NM 报文(0x510) + 睡眠就绪位
...                ③ **总线静默判定**：都就绪且静默够久 → 一起进睡眠
...              唤醒：任意 CAN 活动（真实常见唤醒源：开车门、遥控钥匙、IGN ON）
...              注：仿真把"静默判定时长"缩短到 2 秒（真实项目通常几十秒），
...                  其余机制与真实一致。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment
Test Teardown    恢复正常电源模式

*** Variables ***
${POWER_OFF}        00      # 电源模式取值：0=OFF 1=ACC 2=IGN 3=CRANK
${POWER_IGN}        02
${ID_NM}            0x510   # 网络管理报文
${ID_POWER_MODE}    0x600   # 电源模式广播（真实由 BCM 发，本仿真测试可扮演）

*** Test Cases ***
IGN OFF 后应用报文应停发且 NM 应置睡眠就绪位
    [Tags]    smoke    power    REQ-PWR-001
    [Documentation]    熄火后 ECU 应停止应用报文（0x100/0x200/0x300），
    ...              改为周期发 NM 报文并置"睡眠就绪"位（byte1 = 1）
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    55    65    timeout=20
    Send CAN Frame    ${ID_POWER_MODE}    ${POWER_OFF}       # BCM 广播熄火
    Sleep    1s
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    Recorded Frames Should Not Contain ID    0x100
    Recorded Frames Should Not Contain ID    0x200
    Recorded Frames Should Not Contain ID    0x300
    Recorded Frames Should Contain ID    ${ID_NM}
    ${nm}=    Get Last Frame Data For ID    ${ID_NM}
    # 注意引号：RF 的切片语法会先把 ${nm}[2:4] 求值成字符串 "01"，
    # 必须用引号包住（int('01', 16)），否则 Python 报 leading zeros 错误
    ${sleep_ready}=    Evaluate    int('${nm}'[2:4], 16) & 0x01
    Should Be Equal As Integers    ${sleep_ready}    1
    ...    msg=IGN OFF 后 NM 睡眠就绪位应为 1（NM 数据: ${nm}）

总线静默后节点应进入睡眠（总线零帧）
    [Tags]    smoke    power    REQ-PWR-002    REQ-PWR-004
    [Documentation]    睡眠判定：IGN OFF + 静默超过阈值 → 连 NM 也停发。
    ...              验证方式：录制 3 秒，总线上应该**一帧都没有**
    Send CAN Frame    ${ID_POWER_MODE}    ${POWER_OFF}
    Sleep    4s                     # 等过睡眠判定（2 秒）+ 余量
    Start Bus Recorder
    Drain Bus For    3
    Stop Bus Recorder
    ${n}=    Recorded Frame Count
    Should Be Equal As Integers    ${n}    0
    ...    msg=进入睡眠后总线应完全静默，实际录到 ${n} 帧（暗电流不达标）

CAN 唤醒帧应唤醒节点并恢复应用报文
    [Tags]    smoke    power    REQ-PWR-003
    [Documentation]    **唤醒验证**：睡眠中最怕"叫不醒"。任意 CAN 活动（真实场景
    ...              如开车门触发 BCM 发报文）都应唤醒节点并恢复周期报文
    Send CAN Frame    ${ID_POWER_MODE}    ${POWER_OFF}
    Sleep    4s                     # 先进入睡眠
    Send CAN Frame    0x123    00   # 模拟唤醒源（车门/遥控）
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    55    65    timeout=15
    Log    节点已唤醒，车速恢复为 ${speed} ✓

IGN ON 应恢复正常工况
    [Tags]    power    REQ-PWR-002
    Send CAN Frame    ${ID_POWER_MODE}    ${POWER_OFF}
    Sleep    4s
    Send CAN Frame    ${ID_POWER_MODE}    ${POWER_IGN}
    Sleep    1s
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    55    65    timeout=15
    Log    IGN ON 后恢复正常工况 ✓

*** Keywords ***
恢复正常电源模式
    [Documentation]    **电源管理用例的隔离清理**：必须把电源模式恢复成 IGN，
    ...              否则下一个用例（甚至后续套件）都在"熄火"状态下运行 ——
    ...              表现为"所有报文突然消失"的诡异失败
    Run Keyword And Ignore Error    Send CAN Frame    0x600    02
    Run Keyword And Ignore Error    Send CAN Frame    0x123    00
    Sleep    0.5s
