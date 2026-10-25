*** Settings ***
Documentation    L4 参考答案：负向测试
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
未解锁时写 DID 应被拒绝
    [Documentation]    扩展会话 + 未解锁 → 2E 写 DID 必须被拒（NRC 0x33 权限不足）
    ...               这不是"功能不好用"，而是**安全设计生效**的证据
    Enter Extended Session
    ${resp}=    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33

读取未知 DID 应返回参数超范围
    [Documentation]    F999 不存在 → NRC 0x31 requestOutOfRange
    ...               正确地"拒绝"和正确地"响应"一样重要
    ${resp}=    Send UDS Request    22 F9 99
    Response Should Be Negative With NRC    0x31

抑制正响应的请求 ECU 应保持沉默
    [Documentation]    3E 80（抑制正响应位=1）→ ECU 执行但不回复。
    ...               验证"沉默"要用期望超时；紧接着必须验证 ECU 还活着
    ...               （否则可能只是 ECU 挂了，而不是"正确地沉默"）
    Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
    ECU Should Respond
