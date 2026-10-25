*** Settings ***
Documentation    L1 参考答案：第一个车载测试脚本
...              「读车速并断言它在合理范围」—— 车载脚本最常见的两种动作：
...              从总线上"读一个值" + 断言它"符合预期"。
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车速信号应处于合理范围
    [Documentation]    ECU 以 60km/h 目标启动，读到的车速应落在 50~70 之间
    ...               ⚠ 这里的 Sleep 8s 是**笨办法**：靠猜时间等车加速。
    ...               机器慢一点、ECU 启动慢一点，8 秒就可能不够 → 偶发失败。
    ...               L2 会教正确做法（轮询等待，而不是猜时间）。
    Sleep    8s
    ${speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    Should Be True    50 <= ${speed} <= 70
    ...    msg=车速 ${speed} 不在 50~70 范围内

水温信号应为数值
    [Documentation]    水温读回来必须是个数字（能参与比较）
    ${coolant}=    Read Signal From Bus    EngineData    CoolantTemp
    Should Be True    ${coolant} > -40
    ...    msg=水温 ${coolant} 异常（不应低于 -40℃）
