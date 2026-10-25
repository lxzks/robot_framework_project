*** Settings ***
Documentation    L6 练习：完整闭环（把 TODO 补完，见 README.md）
...              场景：ECU 以 125℃ 水温启动 → 水温灯亮 → DTC 确认 → 清除 → 灯灭 → 重建
Resource         ../../resources/common.resource
# TODO 1: Suite Setup 用 coolant=125 造故障场景；Teardown 关环境
Suite Setup      ...
Suite Teardown   ...

*** Test Cases ***
水温超限应点亮水温报警灯
    [Documentation]    等仪表水温灯亮（提示：timeout 给 25，水温爬升需要时间）
    # TODO 2: Telltale Should Be Lit    CoolantTempHigh    timeout=25
    ...

诊断仪应能读到 confirmed 的 P0116
    [Documentation]    轮询等 DTC 列表出现 P0116
    # TODO 3: Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116
    ...

清除 DTC 后报警灯应熄灭
    [Documentation]    清除 → 断言灯灭
    # TODO 4: Clear DTC → Telltale Should Be Off    CoolantTempHigh    timeout=5
    ...

故障仍在时 DTC 应重新确认
    [Documentation]    恢复性验证：故障没修，系统应重新记录
    # TODO 5: 再等 DTC 出现 + 断言灯再次亮
    ...
