*** Settings ***
Documentation    L6 参考答案：完整闭环（故障 → 报警 → 诊断 → 清除 → 重建）
...              场景：ECU 以 125℃ 水温启动（超 120℃ 阈值 → P0116）
Resource         ../../resources/common.resource
Suite Setup      Start Vehicle Environment    coolant=125
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
水温超限应点亮水温报警灯
    [Documentation]    CAN 信号 → DTC 状态机 → 警告灯位图 → 仪表显示 全链路的终点
    ...               timeout=25：水温从 90℃ 一阶惯性爬升到 120℃ 约需 8 秒（τ=4s），
    ...               再加 2 个诊断周期确认，给足余量
    Telltale Should Be Lit    CoolantTempHigh    timeout=25

诊断仪应能读到 confirmed 的 P0116
    [Documentation]    仪表视角（灯亮）之外，诊断视角（DTC 记录）也要能看到同一故障
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0116

清除 DTC 后报警灯应熄灭
    [Documentation]    0x14 清除是"干预"：状态位归零 → 非保持型灯立即熄灭。
    ...               ⚠ 故障工况（coolant=125）仍在：必须先 85 02 暂停检测再清，
    ...               否则下一个 500ms 周期 ECU 会立刻重新记录 → 断言偶发失败
    ...               （与 scenarios/04 的写法一致，背景见 docs/06 坑 21）
    Enter Extended Session
    Send UDS Request    85 02                     # 暂停 DTC 检测
    Response Should Be Positive
    Clear DTC
    ${count}=    Read DTC Count
    Should Be Equal As Integers    ${count}    0
    ...    msg=暂停检测后清除，DTC 数量应为 0，实际 ${count}
    Telltale Should Be Off    CoolantTempHigh    timeout=5
    Send UDS Request    85 01                     # 恢复检测（下一条用例要验证重建）
    Response Should Be Positive

故障仍在时 DTC 应重新确认
    [Documentation]    **恢复性验证**：清除只重置状态机，没修好故障。
    ...               系统应在几个周期后重新记录并再次点灯 —— 说明清除没有
    ...               把故障检测能力搞坏（真实场景：清码后过几天又亮）
    Wait Until Keyword Succeeds    20x    1s    DTC List Should Contain    P0116
    Telltale Should Be Lit    CoolantTempHigh    timeout=10
