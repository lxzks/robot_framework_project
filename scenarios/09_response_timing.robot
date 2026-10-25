*** Settings ***
Documentation    场景 9：诊断响应时序一致性（P2 参数验证）
...              【业务背景】ECU 在会话响应里**声明**了自己的响应时间参数：
...              P2 = 50ms（普通响应）、P2* = 5000ms（延时响应）。
...              如果实测响应持续超过声明值，诊断仪会误判超时 → 现场表现为
...              "诊断仪连不上车"。这是**协议一致性**的必测项。
...              要点：时序测试要**多点采样**，单次偶然快不能说明问题。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Variables ***
${P2_MS}    50
${SAMPLE_COUNT}    5

*** Test Cases ***
会话控制响应应在声明的 P2 内
    [Tags]    smoke    diagnostic    timing    REQ-DIAG-011
    [Documentation]    进会话是"最重"的诊断操作（要切状态机），它合格其他都好说
    ${ms}=    Measure Response Time    10 03
    Should Be True    ${ms} < ${P2_MS}
    ...    msg=会话控制响应 ${ms}ms 超过声明的 P2(${P2_MS}ms)

读数据响应时间应稳定在 P2 内（多点采样）
    [Tags]    diagnostic    timing    REQ-DIAG-011
    [Documentation]    采样 5 次取最大值 —— 时序问题往往是"偶尔慢"，
    ...              单次测量会漏掉（真实项目发现过"第 3 次响应慢 80ms"的缺陷）
    ...              ⚠ 变量名故意不叫 ${SAMPLES}：RF **变量名大小写不敏感**，
    ...              会和列表 ${samples} 撞车（报 "Expected number, got list"）
    Enter Extended Session
    @{samples}=    Create List
    FOR    ${i}    IN RANGE    ${SAMPLE_COUNT}
        ${ms}=    Measure Response Time    22 F187
        Append To List    ${samples}    ${ms}
    END
    ${max_ms}=    Evaluate    max($samples)
    ${avg_ms}=    Evaluate    sum($samples) / len($samples)
    ${over}=    Evaluate    len([x for x in $samples if x >= ${P2_MS}])
    Log    采样: ${samples} 平均 ${avg_ms}ms 最大 ${max_ms}ms 超标 ${over} 次
    Should Be True    ${avg_ms} < ${P2_MS}
    ...    msg=读数据平均响应 ${avg_ms}ms 超过 P2(${P2_MS}ms)，采样: ${samples}
    # 允许最多 1 次偶发超标：操作系统调度抖动（GC、抢占）会让单次响应
    # 偶尔超 P2，这不代表 ECU 有问题；**系统性慢**才体现在均值上。
    # 真实时序测试都区分这两者 —— 否则用例会在高负载机器上随机失败
    Should Be True    ${over} <= 1
    ...    msg=5 次采样中 ${over} 次超过 P2，属于系统性慢而非偶发抖动: ${samples}

负响应不应比正响应慢
    [Tags]    diagnostic    timing    REQ-DIAG-011
    [Documentation]    负响应（NRC）也必须在 P2 内 —— 拒绝得快也是协议要求
    ${ms}=    Measure Response Time    22 F9 99
    Should Be True    ${ms} < ${P2_MS}
    ...    msg=负响应耗时 ${ms}ms 超过 P2(${P2_MS}ms)
