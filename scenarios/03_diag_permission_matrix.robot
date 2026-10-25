*** Settings ***
Documentation    场景 3：诊断权限矩阵（会话 × 安全访问，二维全覆盖）
...              【业务背景】诊断服务有**严格的权限模型**：同一个操作在不同会话、
...              不同解锁状态下行为不同。权限矩阵是 Tier1 诊断测试的核心交付项
...              —— 每条权限边界都要有正向和负向用例，缺一条就是覆盖漏洞。
...
...              本场景覆盖的边界（★ 为负向）：
...                     默认会话   扩展未解锁   扩展已解锁   复位后
...                读数据   ✅ 允许     ✅ 允许      ✅ 允许     ✅ 允许
...                写数据   ★0x7F      ★0x33       ✅ 成功     ★0x7F
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
默认会话读数据应允许
    [Tags]    smoke    diagnostic    security    REQ-DIAG-001
    [Documentation]    读类服务在任何会话都该可用（售后/产线都靠它）
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001

默认会话写数据应被会话限制
    [Tags]    diagnostic    security    REQ-DIAG-007
    [Documentation]    门槛一：会话。默认会话下写操作 → NRC 0x7F
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x7F

扩展会话未解锁写数据应被权限拒绝
    [Tags]    smoke    diagnostic    security    REQ-DIAG-006
    [Documentation]    门槛二：安全访问。扩了会话但没解锁 → NRC 0x33
    ...              这个用例证明"解锁真的有用"——安全设计生效的证据
    Enter Extended Session
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33

扩展会话已解锁写数据应成功且可回读
    [Tags]    smoke    diagnostic    security    REQ-DIAG-004    REQ-DIAG-005
    [Documentation]    写操作后**必须回读**：正响应只说明"收到了"，不代表"写对了"
    Enter Extended Session
    Security Unlock
    Write DID    F1A0    56332E302E30
    ${ver}=    Read DID As ASCII    F1A0
    Should Be Equal    ${ver}    V3.0.0

ECU 复位后权限应失效
    [Tags]    diagnostic    security    REQ-DIAG-013
    [Documentation]    复位后回到默认会话且安全锁定 —— 之前的解锁**不该残留**。
    ...              （权限残留是安全隐患：退出诊断后别人能直接接着写）
    Enter Extended Session
    Security Unlock
    Send UDS Request    11 01
    Response Should Be Positive
    Sleep    0.3s
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x7F
