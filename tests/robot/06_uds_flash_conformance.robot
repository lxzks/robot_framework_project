*** Settings ***
Documentation    套件 06：UDS 刷写流程与协议一致性测试
...              两部分内容：
...              1. **刷写流程**（0x34 请求下载 → 0x36 分块传输 → 0x37 退出
...                 → 0x31 01 FF00 完整性校验）—— 完整走通一次固件下载
...              2. **一致性测试**（Conformance Test）：按 ISO 14229 验证
...                 ECU 的权限模型、错误处理、时序是否符合协议 ——
...                 这是 Tier1 诊断测试交付物里的标准项（不是"功能能用"，
...                 而是"协议说得对不对"）
Resource        ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment
Test Teardown    复位 ECU 清理刷写状态

*** Variables ***
${FLASH_ADDR}    08000000
${FLASH_SIZE}    128

*** Keywords ***
复位 ECU 清理刷写状态
    [Documentation]    刷写用例之间的隔离：ECU 复位（11 01）会清空刷写
    ...               状态与安全解锁 —— 否则前一个用例留下的 flash_state
    ...               会让后续 0x34 请求全部撞 NRC 0x24（真实 ECU 也是这样：
    ...               复位后刷写序列必须重来）。
    Run Keyword And Ignore Error    Send UDS Request    11 01
    Sleep    0.2s

*** Test Cases ***
编程会话下请求下载应返回单块上限
    [Tags]    REQ-FLS-001
    [Documentation]    0x34 正响应 = 74 + lengthFormat(0x20) + maxBlockLength(2B)
    Enter Programming Session
    Security Unlock
    ${max_block}=    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    Should Be True    ${max_block} > 0    msg=应返回单块上限
    Should Be True    ${max_block} <= 4096    msg=单块上限应是合理值

分块传输数据应逐块确认
    [Tags]    REQ-FLS-002
    [Documentation]    0x36 正响应回显块序号；两块数据各 64 字节
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${block1}=    Evaluate    'AA' * 64
    ${block2}=    Evaluate    'BB' * 64
    Transfer Data Block    1    ${block1}
    Transfer Data Block    2    ${block2}

完整刷写流程应通过完整性校验
    [Tags]    REQ-FLS-003
    [Documentation]    下载 → 两块传输 → 退出 → 31 01 FF00 校验结果 00（通过）
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${data}=    Evaluate    '5A' * 64
    Transfer Data Block    1    ${data}
    Transfer Data Block    2    ${data}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    0

传输字节数不足时完整性校验应失败
    [Tags]    REQ-FLS-005
    [Documentation]    声明 128 字节但只传 64 → 校验结果 01（失败）——
    ...               真实刷写里这意味着固件不完整，必须拒绝
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${data}=    Evaluate    '5A' * 64
    Transfer Data Block    1    ${data}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    1

块序号错乱应被拒绝
    [Tags]    REQ-FLS-004
    [Documentation]    块序号必须严格递增；跳号（1→3）应报
    ...               NRC 0x73 wrongBlockSequenceCounter —— 真实刷写最常见的失败
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${data}=    Evaluate    '5A' * 16
    Transfer Data Block    1    ${data}
    Send UDS Request    36 03 ${data}
    Response Should Be Negative With NRC    0x73

未解锁时请求下载应被拒绝
    [Tags]    REQ-DIAG-006
    [Documentation]    编程会话但未过安全访问 → NRC 0x33 securityAccessDenied
    ...               （安全访问必须每次会话重新做）
    Enter Programming Session
    Send UDS Request    34 00 44 08000000 00000080
    Response Should Be Negative With NRC    0x33

默认会话下刷写服务应不可用
    [Tags]    REQ-DIAG-007
    [Documentation]    编程会话才有刷写权限；默认会话调 0x34 → NRC 0x7F
    Enter Default Session
    Send UDS Request    34 00 44 08000000 00000080
    Response Should Be Negative With NRC    0x7F

目标地址超范围应被拒绝
    [Tags]    REQ-FLS-006
    [Documentation]    地址超出 App 分区（0x08000000~0x08100000）→ NRC 0x31
    Enter Programming Session
    Security Unlock
    Send UDS Request    34 00 44 09000000 00000080
    Response Should Be Negative With NRC    0x31

未请求下载就传数据应报序列错误
    [Tags]    REQ-FLS-008
    [Documentation]    0x36 的前置是 0x34；直接传数据 → NRC 0x24
    ...               requestSequenceError（诊断服务有严格的时序依赖）
    Enter Programming Session
    Security Unlock
    Send UDS Request    36 01 5A5A
    Response Should Be Negative With NRC    0x24

响应时间应符合声明的 P2 参数
    [Tags]    REQ-DIAG-011
    [Documentation]    会话控制响应里声明的 P2 = 50ms；实测往返应远小于它，
    ...               否则诊断仪会误判超时（协议一致性必测项）
    Enter Programming Session
    Security Unlock
    ${ms}=    Measure Response Time    22 F187
    Should Be True    ${ms} < 50    msg=响应耗时 ${ms}ms 超过 P2(50ms)

默认会话读取类服务应始终可用
    [Tags]    REQ-DIAG-001
    [Documentation]    一致性：读 VIN / 读 DTC 在任何会话都应可用。
    ...              断言用**交叉校验**而不是"数量 >= 0"（后者恒真、零验证力）：
    ...              19 01 报告的数量应与 19 02 返回的列表长度一致。
    Enter Default Session
    ${vin}=    Read DID As ASCII    F190
    Should Be Equal    ${vin}    LSVAM4180NC000001
    ${count}=    Read DTC Count
    ${dtcs}=    Read DTC By Mask    FF
    ${actual}=    Evaluate    len($dtcs)
    Should Be Equal As Integers    ${count}    ${actual}
    ...    msg=19 01 报告的 DTC 数量 ${count} 与 19 02 实际列表长度 ${actual} 不一致
