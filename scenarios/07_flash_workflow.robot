*** Settings ***
Documentation    场景 7：刷写完整流程与失败路径（一条业务流程的正反两面）
...              【业务背景】刷写（固件升级）是诊断测试里权重最高的场景，
...              因为它有严格的时序依赖和多层权限。真实项目里，
...              **失败路径比成功路径更重要** —— 刷一半失败不能让 ECU 变砖。
...              本场景同时覆盖：完整成功流程 + 4 条失败路径 + 失败后恢复。
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment
Test Teardown    复位 ECU 清理刷写状态

*** Variables ***
${FLASH_ADDR}    08000000
${FLASH_SIZE}    128

*** Test Cases ***
完整刷写流程应成功并通过完整性校验
    [Tags]    smoke    flash    REQ-FLS-001    REQ-FLS-002    REQ-FLS-003
    [Documentation]    四步曲：编程会话 → 解锁 → 请求下载 → 分块传输 → 退出 → 校验
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${block}=    Evaluate    '5A' * 64
    Transfer Data Block    1    ${block}
    Transfer Data Block    2    ${block}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    0

失败路径_未解锁时请求下载应被拒绝
    [Tags]    flash    negative    REQ-DIAG-006
    Enter Programming Session
    Send UDS Request    34 00 44 ${FLASH_ADDR} 00000080
    Response Should Be Negative With NRC    0x33

失败路径_块序号错乱应被拒绝
    [Tags]    flash    negative    REQ-FLS-004
    [Documentation]    跳号（1→3）→ NRC 0x73。真实刷写最常见的失败：丢块
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${block}=    Evaluate    '5A' * 16
    Transfer Data Block    1    ${block}
    Send UDS Request    36 03 ${block}
    Response Should Be Negative With NRC    0x73

失败路径_传输字节不足应报完整性失败
    [Tags]    flash    negative    REQ-FLS-005
    [Documentation]    声明 128 字节只传 64 → 校验结果 01（失败）。
    ...              真实意义：固件不完整必须被识别，不能默默"升级成功"
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${block}=    Evaluate    '5A' * 64
    Transfer Data Block    1    ${block}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    1

失败路径_地址超范围应被拒绝
    [Tags]    flash    negative    REQ-FLS-006
    Enter Programming Session
    Security Unlock
    Send UDS Request    34 00 44 09000000 00000080
    Response Should Be Negative With NRC    0x31

刷写失败后应能重新开始完整流程
    [Tags]    smoke    flash    REQ-FLS-007
    [Documentation]    **恢复性验证**：失败一次后不该把 ECU 卡死在异常状态。
    ...              先故意失败（长度不足），复位清理，再从零走一遍成功流程
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${block}=    Evaluate    '5A' * 32
    Transfer Data Block    1    ${block}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    1
    # 清理后重新完整刷一遍
    Send UDS Request    11 01
    Response Should Be Positive
    Enter Programming Session
    Security Unlock
    Request Download    ${FLASH_ADDR}    ${FLASH_SIZE}
    ${full}=    Evaluate    'A5' * 64
    Transfer Data Block    1    ${full}
    Transfer Data Block    2    ${full}
    Request Transfer Exit
    Check Programming Integrity
    Integrity Check Result Should Be    0

*** Keywords ***
复位 ECU 清理刷写状态
    [Documentation]    刷写状态（flash_state）在用例间必须清空，
    ...              否则下一个 0x34 会撞 NRC 0x24（真实 ECU 复位后刷写也要重来）
    Run Keyword And Ignore Error    Send UDS Request    11 01
    Sleep    0.2s
