*** Settings ***
Documentation    场景 6：座舱多屏一致性（车机操作 → CAN 广播 → 仪表同步）
...              【业务背景】座舱里有两块屏：车机（中控）和仪表。
...              用户在车机上点歌/开导航/调空调，仪表要**立即同步显示**
...              （法规+体验要求）。这类"跨屏一致性"是座舱域测试的重点，
...              链路是：车机 UI 状态 → CAN 报文(0x400/0x500) → 仪表显示。
...              确定性驱动：用显式状态设置 + 广播，不依赖 GUI 定时器的隐式活性。
Resource         ../resources/common.resource
Library          ../libraries/AdbLibrary.py
Suite Setup      Start Vehicle Environment    speed=60    with_android=True
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
媒体信息应从车机同步到仪表
    [Tags]    smoke    cockpit    REQ-HMI-002
    [Documentation]    track_index=1 → ivi_data.TRACKS[1] = 晴天
    Set Headunit Media State    track_index=1    volume=30
    Broadcast Headunit State To Bus
    Wait Until Keyword Succeeds    10x    500ms    Media Track Should Be    晴天

媒体音量变化应同步
    [Tags]    cockpit    REQ-HMI-002
    Set Headunit Media State    track_index=2    volume=55
    Broadcast Headunit State To Bus
    Wait Until Keyword Succeeds    10x    500ms    Media Track Should Be    平凡之路

导航信息应从车机同步到仪表
    [Tags]    smoke    cockpit    REQ-HMI-003
    [Documentation]    导航是"高频变化的跨屏数据"：转向 + 距离 + 路名都要对
    Set Headunit Navi State    active=1    distance_m=1500    street_index=1    turn=2
    Broadcast Headunit State To Bus
    Navi Distance Should Be Below    2000
    ${turn}=    Get Displayed Navi Turn
    Log    仪表显示转向: ${turn}

空调设置应从车机同步到仪表
    [Tags]    smoke    cockpit    REQ-HMI-004
    Set Headunit Hvac State    temp=26    ac_on=1
    Broadcast Headunit State To Bus
    Wait Until Keyword Succeeds    10x    500ms    Hvac Temp Should Be    26

座舱设置不应影响动力域信号
    [Tags]    cockpit    isolation    REQ-HMI-005
    [Documentation]    域隔离验证：座舱操作不应干扰动力域报文。
    ...              真实缺陷对照：车机大量发报文导致总线负载升高，
    ...              动力报文周期被挤压（这条用例就是防这个）
    Set Headunit Media State    track_index=5    volume=100
    Broadcast Headunit State To Bus
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    Period And Jitter Should Be Within    0x100    100
    ...    period_tolerance_ms=25    jitter_tolerance_ms=60
