*** Settings ***
Documentation    套件 02：UDS 诊断服务测试
...              覆盖诊断测试的核心场景：会话控制、安全访问、DID 读写、
...              DTC 读取、例程控制、负响应码验证。
...              对应真实工作：CANoe 诊断控制台 / ODX 自动化测试。
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
默认会话下应能读取 DID
    [Tags]    REQ-DIAG-001
    [Documentation]    上电后 ECU 处于默认会话(01)，允许读数据
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001
    ${sn}=    Read DID As ASCII    F189
    Should Be Equal    ${sn}    ECU-2026-0918-007

进入扩展会话应返回 P2 与 P2 星超时参数
    [Tags]    REQ-DIAG-003
    [Documentation]    10 03 → 50 03 00 32 01 F4：P2=50ms, P2*=5000ms
    ${resp}=    Send UDS Request    10 03
    Should Be Equal    ${resp}    正响应 SID=0x50 payload=03003201f4

读取 17 字节 VIN 应自动走 ISO-TP 多帧
    [Tags]    REQ-DIAG-002
    [Documentation]    22 F1 90 的响应 20 字节 > 7，必须 FF+CF 多帧传输
    ${vin}=    Read DID As ASCII    F190
    Should Be Equal    ${vin}    LSVAM4180NC000001
    Length Should Be    ${vin}    17

安全访问未解锁时写 DID 应被拒绝
    [Tags]    REQ-DIAG-006
    [Documentation]    2E 在扩展会话下还需 27 安全解锁，否则 NRC 0x33
    Enter Extended Session
    ${resp}=    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33

完成种子密钥握手后写 DID 应成功
    [Tags]    REQ-DIAG-004    REQ-DIAG-005
    [Documentation]    27 01 拿种子 → key = seed XOR 5A5A5A5A → 27 02
    Enter Extended Session
    Security Unlock
    ${resp}=    Send UDS Request    2E F1 A0 56332E302e30
    Response Should Be Positive
    ${ver}=    Read DID As ASCII    F1A0
    Should Be Equal    ${ver}    V3.0.0

切回默认会话应重新锁定安全访问
    [Tags]    REQ-DIAG-013
    [Documentation]    安全设计常识：会话切换/复位后必须重新解锁
    Enter Default Session
    Enter Extended Session
    ${resp}=    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x33

读取未知 DID 应返回 requestOutOfRange
    [Tags]    REQ-DIAG-008
    [Documentation]    22 F9 99 不存在 → NRC 0x31
    ${resp}=    Send UDS Request    22 F9 99
    Response Should Be Negative With NRC    0x31

读取未知服务应返回 serviceNotSupported
    [Tags]    REQ-DIAG-015
    [Documentation]    A0 不存在 → NRC 0x11
    ${resp}=    Send UDS Request    A0
    Response Should Be Negative With NRC    0x11

自检例程应正常启动
    [Tags]    REQ-DIAG-016
    [Documentation]    31 01 0200 → 71 01 02 00 00（结果 00 = 通过）
    Enter Extended Session
    Start Routine    0200
    Response Should Be Positive

TesterPresent 应返回保活响应
    [Tags]    REQ-DIAG-010
    [Documentation]    3E 00 → 7E 00；0x80 置位则抑制响应（静默保活）
    ${resp}=    Send UDS Request    3E 00
    Response Should Be Positive

抑制正响应的请求 ECU 不应回复
    [Tags]    REQ-DIAG-012
    [Documentation]    3E 80：sub-function 最高位置 1，ECU 静默执行不回复。
    ...              验证方法：发送后等待响应必然超时（超时=无响应），
    ...              紧接着发一个正常请求，收到的响应应对应正常请求。
    Run Keyword And Expect Error    *没等到*    Send UDS Request    3E 80
    ${resp}=    Send UDS Request    22 F1 87
    Response Should Be Positive
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001

暂停与恢复 DTC 检测应被接受
    [Tags]    REQ-DTC-006
    [Documentation]    85 02 暂停 DTC 检测 / 85 01 恢复。
    ...              真实用途：台架测试前先暂停，避免测试动作触发无关 DTC
    ...              污染结果（产线终检也这么做）。
    Enter Extended Session
    ${resp}=    Send UDS Request    85 02
    Response Should Be Positive
    ${resp}=    Send UDS Request    85 01
    Response Should Be Positive

静默超过 S3 时间后应回退默认会话
    [Tags]    REQ-DIAG-009
    [Documentation]    S3 = 5 秒：非默认会话下 5 秒无请求自动回退 + 锁定安全访问。
    ...              这里 Sleep 6s **是需求本身**（验证"静默"），不是猜时间。
    ...              回退后写 DID 应报 0x7F（当前会话不支持），而不是 0x33。
    Enter Extended Session
    Security Unlock
    Sleep    6s
    Send UDS Request    2E F1 A0 4841434B
    Response Should Be Negative With NRC    0x7F
