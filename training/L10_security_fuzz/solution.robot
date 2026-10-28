*** Settings ***
Documentation    L10 参考答案：信息安全（种子-密钥 / 绕过回归 / 错误密钥 / 模糊测试）
...              被测对象：ecu_sim.py 的安全访问（0x27）+ UDS 健壮性。
...              背景：历史漏洞 —— 27 02 在未请求种子时用默认 seed=0 算期望密钥，
...              直接发 27 02 5A5A5A5A 可解锁；修复后必须回 NRC 0x24。
Resource         ../../resources/common.resource
Library          ../../libraries/SecurityLibrary.py
Suite Setup      Start Vehicle Environment
Suite Teardown   关闭安全测试环境
Test Setup       Reset Security State

*** Test Cases ***
正常种子密钥握手应解锁成功
    [Documentation]    正基线：27 01 → key = seed XOR 0x5A5A5A5A → 27 02 解锁；
    ...                解锁后写 DID 应成功。只有基线成立，负向用例才有意义。
    ${seed}    ${key}=    Unlock Sequence Should Succeed
    Log    种子 ${seed} / 密钥 ${key}
    Send Raw Request    2E F1 A0 56332E302E30
    SecurityLibrary.Response Should Be Positive

未请求种子直接发送密钥应被拒绝
    [Documentation]    **漏洞回归**：不请求种子直接发默认 seed 对应的密钥 5A5A5A5A，
    ...                必须回 NRC 0x24（requestSequenceError）；写 DID 仍被权限拒绝（0x33）。
    Bypass Attempt Should Fail
    Send Raw Request    2E F1 A0 4841434B
    SecurityLibrary.Response Should Be Negative With NRC    0x33

错误密钥应返回 NRC 0x35
    [Documentation]    对当前种子发送"翻转 1 bit"的错误密钥 → NRC 0x35 invalidKey。
    ${seed}=    SecurityLibrary.Request Seed
    Send Wrong Key For Seed    ${seed}
    Security Access Should Fail With NRC    0x35

小规模模糊测试后 ECU 应存活且可恢复
    [Documentation]    120 条随机/半随机 UDS（固定随机种子，可复现）：正响应/无响应在阈值内，
    ...                ECU 存活，且模糊后读 DID 仍正常（不是"能回包但已经错乱"）。
    &{stats}=    Fuzz UDS Requests    120
    Log    模糊测试统计: ${stats}
    Should Be True    ${stats}[positive] >= 8
    ...    msg=120 条里正响应过少（${stats}），合法请求可能未被正确处理
    Should Be True    ${stats}[no_response] <= 20
    ...    msg=无响应 ${stats}[no_response] 条（阈值 20），ECU 可能丢帧/卡死
    Should Be True    ${stats}[alive]
    ...    msg=模糊测试后 ECU 已失联（可能崩溃）
    ECU Should Stay Alive
    Send Raw Request    22 F1 87
    SecurityLibrary.Response Should Be Positive

*** Keywords ***
关闭安全测试环境
    [Documentation]    Suite Teardown：先关安全测试仪（停收包线程 + 释放其总线），
    ...                再走 common.resource 的通用回收（杀仿真进程 + 关公共库总线）。
    Run Keyword And Ignore Error    Close Security Tester
    Stop Vehicle Environment
