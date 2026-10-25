*** Settings ***
Documentation    场景 5：会话时序管理（S3 超时 + 保活机制）
...              【业务背景】非默认会话有 S3 定时器：一段时间无请求就自动回退默认
...              会话并锁定安全访问（防止"诊断接头拔了但权限还开着"）。
...              这类**时序类需求**必须验"变化"而不是"状态"：
...              要证明"超时会回退"和"保活能维持"两件事。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Variables ***
${S3_SECONDS}    5

*** Test Cases ***
静默超过 S3 时间应回退默认会话并锁定权限
    [Tags]    smoke    diagnostic    timing    REQ-DIAG-009
    [Documentation]    这里 Sleep 是**需求本身**（验证"静默"），不是猜时间。
    ...              回退后写数据应报 0x7F（会话不支持）而非 0x33（权限不足）——
    ...              这两种 NRC 的区别正是"回退"的证据
    Enter Extended Session
    Security Unlock
    Sleep    6s
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x7F

TesterPresent 保活应维持会话与权限
    [Tags]    diagnostic    timing    REQ-DIAG-010
    [Documentation]    对照用例：静默期间每 2 秒发 3E 00（保活），
    ...              超过 S3 时间后会话与权限都应仍在
    Enter Extended Session
    Security Unlock
    FOR    ${i}    IN RANGE    3
        Sleep    2s
        Send UDS Request    3E 00
        Response Should Be Positive
    END
    Write DID    F1A0    5A5A
    Response Should Be Positive

普通诊断请求也应重置 S3 定时器
    [Tags]    diagnostic    timing    REQ-DIAG-010
    [Documentation]    很多实现里任何请求都刷新 S3，不只是 TesterPresent。
    ...              用"读数据"当保活手段 —— 验证这个行为是否符合规范
    Enter Extended Session
    Security Unlock
    FOR    ${i}    IN RANGE    3
        Sleep    2s
        ${pn}=    Read DID As ASCII    F187
    END
    Write DID    F1A0    5A5A
    Response Should Be Positive
