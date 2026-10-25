*** Settings ***
Documentation    场景 10：冒烟回归集（用 Tag 组织快速回归）
...              【业务背景】全量 62 个用例要跑 70 秒；但每次改代码后想"快速确认
...              没把主要功能弄坏"，只需要几条关键路径。用 **Tag** 分层：
...
...                python run_tests.py                       # 全量
...                python -m robot --include smoke tests/robot/ scenarios/   # 冒烟
...                python -m robot --exclude slow  ...        # 跳过慢用例
...                python -m robot --include negative ...     # 只跑负向
...
...              本文件的用例覆盖三条"主链路"，全部打 smoke 标签：
...                ① 信号链：ECU 发 → 总线 → 仪表显示
...                ② 诊断链：进会话 → 读数据 → 写数据（含权限）
...                ③ 故障链：注入 → 记录 DTC → 点灯
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60    coolant=125
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
主链路_信号链应通
    [Tags]    smoke    chain    REQ-HMI-001
    [Documentation]    ① 信号链：总线上有信号 + 仪表显示与信号一致
    ...              ⚠ 关键技巧：必须等车速**爬到目标值并稳定**再比较。
    ...              如果在爬升途中比较，两个"动态值"的采样时刻不同，必然对不上
    ...              （第一次写这条用例时就是这样挂的：总线 50.25 vs 仪表 60.0）。
    ...              真实的跨系统一致性测试都要先等系统进入稳态。
    Wait Until Signal In Range    EngineData    VehicleSpeed    58    62    timeout=20
    # 再用轮询包一层一致性断言：即使已进入稳态区间，车速仍在微调，
    # 两次读取的采样时刻可能差一个报文周期 → 单次比较可能偶然不一致。
    # 重试能吸收这种采样时刻差，而真正的不同步（仪表没更新）会持续失败
    Wait Until Keyword Succeeds    5x    500ms    总线与仪表车速应一致

主链路_诊断链应通
    [Tags]    smoke    chain    REQ-DIAG-001    REQ-DIAG-007
    [Documentation]    ② 诊断链：读数据可用 + 权限控制生效
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x7F

主链路_故障链应通
    [Tags]    smoke    chain    REQ-DTC-004
    [Documentation]    ③ 故障链：DTC 记录 + 对应警告灯亮（跨系统闭环的终点）
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116
    Telltale Should Be Lit    CoolantTempHigh    timeout=25

环境自检_核心节点都在线
    [Tags]    smoke    health
    ECU Should Respond
    Dashboard Should Be Ready

*** Keywords ***
总线与仪表车速应一致
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    ${shown}=    Get Displayed Speed
    ${diff}=    Evaluate    abs(${speed} - ${shown})
    Should Be True    ${diff} <= 3
    ...    msg=稳态下总线车速 ${speed} 与仪表 ${shown} 仍不一致（差 ${diff}）
