*** Settings ***
Documentation    L5 参考答案：数据驱动（Template）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
读取多个 DID 应正确
    [Documentation]    同一逻辑 × 3 组数据 —— 加一组只是加一行
    [Template]    读取 DID 并断言
    F187    HW-IC-2026-001
    F189    ECU-2026-0918-007
    F18A    VISTEON

多个信号应在各自范围内
    [Documentation]    不同报文/信号的通用范围断言（物理极限范围）
    [Template]    信号应落在范围内
    EngineData     VehicleSpeed    0     250
    EngineData     CoolantTemp     -40   215
    ClusterData    FuelLevel       0     100

*** Keywords ***
读取 DID 并断言
    [Documentation]    读 DID 并与期望值比较（Template 的被调用关键字）
    [Arguments]    ${did}    ${expected}
    ${value}=    Read DID As ASCII    ${did}
    Should Be Equal    ${value}    ${expected}
    ...    msg=DID ${did} 期望 ${expected} 实际 ${value}

信号应落在范围内
    [Documentation]    读信号并断言落在 [low, high]（注意：不等待，读当前值）
    [Arguments]    ${message}    ${signal}    ${low}    ${high}
    ${value}=    Read Signal From Bus    ${message}    ${signal}
    Should Be True    ${low} <= ${value} <= ${high}
    ...    msg=${message}.${signal}=${value} 不在 [${low}, ${high}]
