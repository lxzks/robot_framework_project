*** Settings ***
Documentation    L10 练习：信息安全（种子-密钥 / 绕过回归 / 错误密钥 / 模糊测试）
...              目标：用 SecurityLibrary 做 UDS 安全访问的攻防验证。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
Resource         ../../resources/common.resource
Library          ../../libraries/SecurityLibrary.py
Suite Setup      Start Vehicle Environment
Suite Teardown   关闭安全测试环境
Test Setup       Reset Security State

*** Test Cases ***
正常种子密钥握手应解锁成功
    [Documentation]    TODO：Unlock Sequence Should Succeed（接 ${seed} ${key}）→
    ...                Send Raw Request 2E F1 A0 56332E302E30 → 断言正响应。
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完

未请求种子直接发送密钥应被拒绝
    [Documentation]    TODO：Bypass Attempt Should Fail（内部断言 NRC 0x24）→
    ...                Send Raw Request 2E F1 A0 4841434B → 断言 NRC 0x33。
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

错误密钥应返回 NRC 0x35
    [Documentation]    TODO：Request Seed 取种子 → Send Wrong Key For Seed ${seed} →
    ...                Security Access Should Fail With NRC 0x35。
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完

小规模模糊测试后 ECU 应存活且可恢复
    [Documentation]    TODO：&{stats}= Fuzz UDS Requests 120 → 断言 positive>=8 / no_response<=20 /
    ...                alive=True → ECU Should Stay Alive → 读 F187 断言正响应。
    Fail    TODO: 删除这一行，按 README §二 用例 4 补完

*** Keywords ***
关闭安全测试环境
    [Documentation]    Suite Teardown（已写好，不用改）：先关安全测试仪，再停车辆环境。
    Run Keyword And Ignore Error    Close Security Tester
    Stop Vehicle Environment
