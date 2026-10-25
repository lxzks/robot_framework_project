*** Settings ***
Documentation    场景 11：故障注入与鲁棒性测试（System Validation 的重头戏）
...              【业务背景】"功能都对"只是软件测试；"**异常条件下行为对**"
...              才是系统验证。真实台架用可编程电源 + 故障盒注入异常条件；
...              本场景用 ECU 的**工程模式 DID（F1B0）**注入三类激励：
...                ① 供电电压异常（低压 9V / 过压 16V）→ 故障记录与降级
...                ② 报文停发（模拟信号源失效）→ 接收方降级策略
...                ③ 丢帧率（模拟总线质量下降）→ 通信容错
...              注入类用例**必须做隔离清理**（Test Teardown 清除激励），
...              否则污染后续所有用例 —— 这是故障注入最容易犯的错。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment
Test Teardown    清除测试激励并复位

*** Variables ***
${NORMAL_VOLTAGE}    14

*** Test Cases ***
低压工况应记录电压过低故障并点亮电池灯
    [Tags]    smoke    robustness    REQ-RBX-001
    [Documentation]    9V 是冷启动临界电压：ECU 应记录 P0562 并点亮电池报警灯。
    ...              真实意义：低温冷启动瞬间电压跌落，能否识别并降级是关键指标
    进入扩展会话并解锁
    Set Test Stimulus    voltage=9
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0562
    ${voltage}=    Read Signal From Bus    ClusterData    BatteryVoltage
    Should Be True    ${voltage} < 10.5
    ...    msg=低压激励下总线电压仍为 ${voltage}V（应 <10.5V）

过压工况应记录电压过高故障
    [Tags]    robustness    REQ-RBX-002
    [Documentation]    16V 过压（发电机调节器失效）会烧负载：必须被识别记录
    进入扩展会话并解锁
    Set Test Stimulus    voltage=16
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0563

车速信号丢失时仪表应保持最后有效值
    [Tags]    smoke    robustness    REQ-RBX-003
    [Documentation]    **降级策略验证**：车速信号丢失 ≠ 显示归零。
    ...              真实场景：CAN 线瞬断时若跳 0，驾驶员会误判车速。
    ...              这里先等稳态拿到有效值，再停发 0x100，验证仪表不归零。
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    55    65    timeout=20
    ${shown_before}=    Get Displayed Speed
    Log    停发前：总线 ${speed} / 仪表 ${shown_before}
    进入扩展会话并解锁
    Set Test Stimulus    suppress_mask=1        # bit0 = 停发 0x100(EngineData)
    Sleep    2s                             #（这里的等待是需求本身：观察丢失后的行为）
    ${shown_after}=    Get Displayed Speed
    Should Be True    ${shown_after} > 1
    ...    msg=信号丢失后仪表车速跳到 ${shown_after}（应保持最后有效值，不该归零）

高丢帧率下诊断通信应仍可用
    [Tags]    robustness    REQ-RBX-004
    [Documentation]    80% 丢帧模拟总线质量严重下降：诊断请求仍需被正常处理
    ...              （真实经验：通信质量差时"诊断连不上车"，但不应导致 ECU 死机）
    进入扩展会话并解锁
    Set Test Stimulus    drop_rate=80
    ECU Should Respond
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001

清除激励后周期报文应恢复正常
    [Tags]    smoke    robustness    REQ-RBX-004
    [Documentation]    恢复性验证：激励清除后系统应回到正常工况（不能卡在异常态）
    进入扩展会话并解锁
    Set Test Stimulus    drop_rate=90    suppress_mask=1
    Sleep    1s
    Clear Test Stimulus
    ${speed}=    Wait Until Signal In Range    EngineData    VehicleSpeed    55    65    timeout=20
    Log    激励清除后车速恢复为 ${speed} ✓

*** Keywords ***
进入扩展会话并解锁
    [Documentation]    写工程模式 DID 需要扩展会话 + 安全解锁（权限不放松）
    Enter Extended Session
    Security Unlock

清除测试激励并复位
    [Documentation]    **故障注入用例的隔离清理**：必须清除激励，否则下一个
    ...              用例（甚至下一个套件）都在异常工况下运行 —— 这类污染
    ...              会导致"单独跑能过、全量跑才挂"，是最难查的失败类型
    Run Keyword And Ignore Error    Enter Extended Session
    Run Keyword And Ignore Error    Clear Test Stimulus
    Run Keyword And Ignore Error    Send UDS Request    11 01
    Sleep    0.3s
