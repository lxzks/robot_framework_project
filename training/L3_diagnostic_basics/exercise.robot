*** Settings ***
Documentation    L3 练习：诊断脚本的固定结构（把 TODO 补完，见 README.md）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
默认会话下应能读取零件号
    [Documentation]    读 DID F187（零件号）并断言等于 HW-IC-2026-001
    # TODO 1: 用 Read DID As ASCII 读 F187，断言值
    ...

读取 17 字节 VIN 应自动走多帧
    [Documentation]    VIN（F190，17 字节）应等于 LSVAM4180NC000001
    # TODO 2: 读 F190，断言值相等 + 长度是 17（Length Should Be）
    ...

应能读到 DTC 数量
    [Documentation]    读 DTC 数量并断言 >= 0
    # TODO 3: 用 Read DTC Count
    ...
