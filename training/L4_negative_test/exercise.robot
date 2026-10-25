*** Settings ***
Documentation    L4 练习：负向测试（把 TODO 补完，见 README.md）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
未解锁时写 DID 应被拒绝
    [Documentation]    进扩展会话（不解锁）→ 写 DID → 期望 NRC 0x33
    # TODO 1: Enter Extended Session + Send UDS Request 2E F1 A0 4841434B
    #         断言用 Response Should Be Negative With NRC    0x33
    ...

读取未知 DID 应返回参数超范围
    [Documentation]    读不存在的 DID F999 → 期望 NRC 0x31
    # TODO 2: Send UDS Request 22 F9 99 + 断言 NRC 0x31
    ...

抑制正响应的请求 ECU 应保持沉默
    [Documentation]    3E 80 应无响应；之后验证 ECU 还活着
    # TODO 3: Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
    #         然后调用 ECU Should Respond 确认 ECU 正常
    ...
