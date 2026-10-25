*** Settings ***
Documentation    L1 练习：第一个车载测试脚本（把 TODO 补完）
...              目标：读信号 + 断言，见 README.md 的任务说明
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    speed=60
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
车速信号应处于合理范围
    [Documentation]    ECU 以 60km/h 目标启动，读到的车速应落在 50~70 之间
    # TODO 1: 等车速爬上来（提示：ECU 从 0 爬到 60 大约需要 7.5 秒）
    #         这里先用笨办法 Sleep，L2 教你正确做法
    ...
    # TODO 2: 读车速信号到 ${speed}
    #         关键字：Read Signal From Bus    EngineData    VehicleSpeed
    ...
    # TODO 3: 断言 50 <= ${speed} <= 70，失败时 msg 里带上实际值
    #         关键字：Should Be True
    ...

水温信号应为数值
    [Documentation]    水温读回来必须是个数字（能参与比较）
    # TODO 4: 读水温信号（CoolantTemp）并断言 > -40
    ...
