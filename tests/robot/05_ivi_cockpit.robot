*** Settings ***
Documentation    套件 05：座舱 IVI 双屏一致性测试
...              安卓车机（headless 模式常驻：状态轮询 + CAN 广播）把 IVI
...              信息通过 CAN 0x400/0x500 推给仪表盘 —— 本套件验证
...              "车机操作 → CAN → 仪表显示"全链路，以及两块屏的数据一致性。
...              确定性设计：状态写入用库关键字、广播用
...              Broadcast Headunit State To Bus 显式触发，不依赖子进程
...              定时器的隐式活性。
...              对应真实工作：座舱域多屏一致性是量产 HMI 测试的重点。
Resource        ../../resources/common.resource
Library          ../../libraries/AdbLibrary.py
Suite Setup      Start Vehicle Environment    speed=60    with_android=True
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车机空调默认状态应显示在仪表
    [Tags]    REQ-HMI-004
    [Documentation]    android_gui 启动后广播 0x400：默认 AC 开、24℃
    Hvac Temp Should Be    24
    ${hvac}=    Get Displayed Hvac Temp
    Should Be Equal As Integers    ${hvac}    24

车机媒体信息应同步到仪表
    [Tags]    REQ-HMI-002
    [Documentation]    默认曲目《海阔天空》- Beyond，经 CAN 0x400 索引映射
    Media Track Should Be    海阔天空
    ${artist}=    Get Displayed Media Artist
    Should Be Equal    ${artist}    Beyond

行程电脑应随行驶累计
    [Tags]    REQ-HMI-006
    [Documentation]    ECU 以 60km/h 行驶，仪表 trip 界面持续累加
    Trip Should Be Accumulating    timeout=15

adb 命令启动应用应改变车机焦点
    [Tags]    REQ-ADB-002
    [Documentation]    adb shell am start 与 android_gui 共享状态文件，
    ...               GUI 轮询到焦点变化即切换界面（等价点击图标）
    Start Activity    com.vehicle.navi/.MapActivity
    Current Focus Should Contain    com.vehicle.navi

车机开导航后仪表应显示导航信息
    [Tags]    REQ-HMI-003
    [Documentation]    模拟在车机上点"开始导航"（库关键字写 ivi 状态），
    ...               再显式广播到 CAN → 仪表导航段出现转向/距离/街道。
    ...               设计要点：① Broadcast Headunit State To Bus 显式推送，
    ...               不依赖子进程定时器；② 距离断言用轮询关键字 ——
    ...               Wait Until Keyword Succeeds 包"读距离"在距离为 0 时
    ...               不算失败会立即结束（值域判断必须下沉到关键字）。
    Set Headunit Navi State    active=1    distance_m=2000    street_index=2    turn=2
    Broadcast Headunit State To Bus
    Navi Distance Should Be Below    2000    timeout=5
    Navi Turn Should Be One Of    左转    右转    直行

两块屏数据应保持一致
    [Tags]    REQ-HMI-007
    [Documentation]    一致性核心断言：仪表显示的媒体曲目 == 车机状态文件的
    ...               曲目索引映射结果（同一数据源，两块屏不可能不一致）
    Set Headunit Media State    track_index=2    status=1    volume=50
    Broadcast Headunit State To Bus
    Media Track Should Be    平凡之路    timeout=5
    ${ivi}=    Get Headunit IVI State
    Should Be Equal As Integers    ${ivi['track_index']}    2
