*** Settings ***
Documentation    套件 11：信息安全测试（UDS 安全访问 / 重放 / 爆破 / 模糊 / 健壮性）
...              真实信息安全测试的工作流：先找缺陷 → 修复 → 用回归用例锁死 → 再系统验证攻击面。
...              本套件的背景缺陷：ECU 的 27 02（SendKey）在**未请求种子**时用默认
...              seed=0x00000000 计算期望密钥（=0x5A5A5A5A），任何人直接发
...              `27 02 5A5A5A5A` 即可解锁 —— 典型的"安全访问绕过"漏洞。
...              修复后本套件第 ③ 条用例把它变成永久回归项。
...
...              覆盖的 5 个安全测试面（[Tags] 对应需求）：
...                REQ-SEC-001 安全访问流程（正基线 / 绕过 / 复位边界）
...                REQ-SEC-002 种子随机性与重放防护（nonce / replay）
...                REQ-SEC-003 爆破防护与锁定（3 次错误 → NRC 0x36 / 5 秒锁定）
...                REQ-SEC-004 模糊测试健壮性（300 条随机 UDS 不崩溃、可恢复）
...                REQ-SEC-005 协议畸形帧处理（截断/超长/非法子功能 → 明确 NRC）
...              对应真实工作：ISO 21434 / UN R155 要求的威胁分析与安全测试项。
Resource         ../../resources/common.resource
Library          ../../libraries/SecurityLibrary.py
Suite Setup      Start Vehicle Environment
Suite Teardown   关闭安全测试环境
Test Setup       重置 ECU 安全状态

*** Test Cases ***
正常种子密钥握手应解锁成功
    [Tags]    security    REQ-SEC-001
    [Documentation]    正基线：27 01 取种子 → key = seed XOR 0x5A5A5A5A → 27 02
    ...              解锁后受保护服务（0x2E 写 DID）应可用。只有基线成立，
    ...              后面的负向用例才有意义（否则可能只是"本来就写不了"）。
    ${seed}    ${key}=    Unlock Sequence Should Succeed
    Log    种子 ${seed} / 密钥 ${key}
    Send Raw Request    2E F1 A0 56332E302E30
    SecurityLibrary.Response Should Be Positive

两次请求的种子应不同
    [Tags]    security    REQ-SEC-002
    [Documentation]    nonce 语义：每次 27 01 都必须返回新随机种子。
    ...              固定种子 = 重放攻击的温床（抓到一次种子+密钥就能永久解锁）。
    ${seeds}=    Seed Should Be Random
    Log    连续种子: ${seeds}

未请求种子直接发送密钥应被拒绝
    [Tags]    security    REQ-SEC-001    REQ-SEC-002
    [Documentation]    **漏洞回归**：历史版本 27 02 用默认 seed=0 计算期望密钥，
    ...              直接发 `27 02 5A5A5A5A` 即可解锁。修复后必须回 NRC 0x24
    ...              requestSequenceError，且写 DID 仍被权限拒绝（0x33）。
    Bypass Attempt Should Fail
    Send Raw Request    2E F1 A0 4841434B
    SecurityLibrary.Response Should Be Negative With NRC    0x33

错误密钥应被拒绝
    [Tags]    security    REQ-SEC-001
    [Documentation]    对当前种子发送"翻转 1 bit"的错误密钥 → NRC 0x35 invalidKey。
    ...              钥匙错误必须明确拒绝，不能静默通过、也不能回正响应。
    ${seed}=    SecurityLibrary.Request Seed
    Send Wrong Key For Seed    ${seed}
    Security Access Should Fail With NRC    0x35

连续 3 次错误密钥应触发锁定并自动恢复
    [Tags]    security    REQ-SEC-003
    [Documentation]    爆破防护：连续 3 次错误 → 第 3 次起 NRC 0x36，锁定 5 秒
    ...              （期间连 27 01 都拒绝）；5 秒后应自动解锁可重试。
    ...              锁定期间用 3E 00 保活，避免 S3 会话超时干扰断言。
    Failed Attempts Should Lock Access
    Sleep    2s
    Send Raw Request    3E 00
    SecurityLibrary.Response Should Be Positive
    Sleep    2s
    Send Raw Request    3E 00
    SecurityLibrary.Response Should Be Positive
    Sleep    1.5s
    ${seed}=    SecurityLibrary.Request Seed
    Should Not Be Empty    ${seed}    msg=5 秒锁定到期后应允许重新请求种子

重放旧密钥应失败
    [Tags]    security    REQ-SEC-002
    [Documentation]    攻防场景：攻击者抓到了上一次的"种子-密钥"对。
    ...              ECU 换新种子后，旧密钥必须失效（nonce + 一次性种子）。
    ...              用旧密钥反推旧种子做碰撞保护，避免天文概率导致假失败。
    ${seed}    ${key}=    Unlock Sequence Should Succeed
    Replay Key Should Fail    ${key}
    Send Raw Request    2E F1 A0 4841434B
    SecurityLibrary.Response Should Be Negative With NRC    0x33

解锁后复位应重新锁定权限
    [Tags]    security    REQ-SEC-001
    [Documentation]    会话/复位边界：解锁后 ECU 复位（11 01）→ 会话回默认、
    ...              权限失效、种子作废。真实场景：诊断仪退出/ECU 重启后，
    ...              之前抓到的密钥不能被继续使用（权限残留是安全隐患）。
    Open Security Session
    ${seed}    ${key}=    Unlock Sequence Should Succeed
    Send Raw Request With Retry    11 01
    SecurityLibrary.Response Should Be Positive
    Sleep    0.3s
    Send Raw Request    2E F1 A0 4841434B
    SecurityLibrary.Response Should Be Negative With NRC    0x7F

300 条随机 UDS 不应打崩 ECU
    [Tags]    security    REQ-SEC-004
    [Documentation]    模糊测试（fuzzing）：40% 合法模板 + 60% 随机 SID/payload，
    ...              共 300 条。验收：无响应数在阈值内（3E 80 这类"正确静默"会
    ...              计入无响应，所以阈值不是 0）、ECU 存活、模糊后仍能正常诊断。
    &{stats}=    Fuzz UDS Requests    300
    Log    模糊测试统计: ${stats}
    Should Be True    ${stats}[positive] >= 20
    ...    msg=300 条里正响应过少（${stats}），合法请求可能未被正确处理
    Should Be True    ${stats}[no_response] <= 20
    ...    msg=无响应 ${stats}[no_response] 条（阈值 20），ECU 可能丢帧/卡死
    Should Be True    ${stats}[alive]    msg=模糊测试后 ECU 已失联（可能崩溃）
    ECU Should Stay Alive
    Send Raw Request With Retry    22 F1 87
    SecurityLibrary.Response Should Be Positive

畸形帧应返回明确 NRC 而不是无响应
    [Tags]    security    REQ-SEC-005
    [Documentation]    协议健壮性：截断 payload、超长 payload、非法子功能，
    ...              都必须得到明确的负响应（0x13 长度/格式错、0x12 子功能不支持）。
    ...              "无响应"是最差结果：既查不出原因，也可能意味着 ECU 吞帧/崩溃。
    Malformed Frame Should Be Rejected    27        0x13
    Malformed Frame Should Be Rejected    27 02     0x13
    Malformed Frame Should Be Rejected    27 02 12 34 56 78 90    0x13
    Malformed Frame Should Be Rejected    27 05     0x12
    Malformed Frame Should Be Rejected    10        0x13
    Malformed Frame Should Be Rejected    19        0x13
    Malformed Frame Should Be Rejected    22 F1     0x13
    Malformed Frame Should Be Rejected    31 01     0x13

模糊测试后 ECU 状态应可恢复
    [Tags]    security    REQ-SEC-004    REQ-SEC-005
    [Documentation]    恢复性验证：被 300 条随机请求轰炸过之后，复位一次应回到
    ...              正常工况 —— 读 VIN（20 字节，走 ISO-TP 多帧）响应及时，
    ...              后续普通诊断也正常。安全测试同样要证明"没有把人搞坏"。
    Send Raw Request With Retry    11 01
    SecurityLibrary.Response Should Be Positive
    Sleep    0.3s
    ${ms}=    Measure Response After Fuzz    22 F190
    Should Be True    ${ms} < 500
    ...    msg=模糊测试后读 VIN 响应 ${ms}ms 过慢（>500ms），ECU 状态可能未恢复
    Send Raw Request With Retry    22 F1 87
    SecurityLibrary.Response Should Be Positive

*** Keywords ***
重置 ECU 安全状态
    [Documentation]    用例隔离：ECU 复位（11 01）会清空会话/解锁/种子/爆破计数/
    ...              锁定状态，再进扩展会话，保证每条用例都从"未解锁 + 未取种子"
    ...              的干净状态开始（否则上一条用例的锁定/解教会污染下一条）。
    Reset Security State

关闭安全测试环境
    [Documentation]    先关安全测试仪（停收包线程 + 释放它自己的虚拟总线），
    ...              再走公共环境回收（杀仿真进程 + 关公共库的总线）。
    Run Keyword And Ignore Error    Close Security Tester
    Stop Vehicle Environment

