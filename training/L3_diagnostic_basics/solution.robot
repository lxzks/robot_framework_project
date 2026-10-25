*** Settings ***
Documentation    L3 参考答案：诊断脚本的固定结构
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
默认会话下应能读取零件号
    [Documentation]    22 F187：读零件号（文本型 DID）
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001

读取 17 字节 VIN 应自动走多帧
    [Documentation]    VIN 是 17 字节，响应 20 字节 → 必然 ISO-TP 多帧
    ...               库内部完成 FF/CF/FC 重组，脚本只看到结果
    ${vin}=    Read DID As ASCII    F190
    Should Be Equal    ${vin}    LSVAM4180NC000001
    Length Should Be    ${vin}    17

应能读到 DTC 数量
    [Documentation]    19 01：报告 DTC 数量。正常环境为 0
    ${count}=    Read DTC Count
    Should Be True    ${count} >= 0
    ...    msg=DTC 数量异常: ${count}
