*** Settings ***
Documentation    场景 4：DTC 全生命周期（pending → confirmed → 清除 → 重建）
...              【业务背景】DTC 不是"检测到就报"，而是有状态机：
...              连续 N 个诊断周期异常才 confirmed（防误报）。
...              本场景验证状态机的每条边界：
...                ① 故障确认后 → 记为 confirmed
...                ② 清除 → 记录归零
...                ③ 故障条件仍在 → 应重新确认（清码不等于修好）
Resource         ../resources/common.resource
Suite Setup      Start Vehicle Environment    coolant=125
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
故障条件持续时应记录为 confirmed 状态
    [Tags]    smoke    dtc    REQ-DTC-001
    [Documentation]    mask=08 只读 confirmed —— 这比"DTC 存在"更严格：
    ...              刚检测到还在 pending 的记录不算通过
    Wait Until Keyword Succeeds    15x    1s
    ...    DTC 应以 confirmed 状态存在    P0116

清除 DTC 后记录应归零
    [Tags]    dtc    REQ-DTC-002
    [Documentation]    ⚠ **必须先暂停 DTC 检测（85 02）再清除**：
    ...              本套件的故障条件（水温 125℃）持续存在，清除后 ECU 会在
    ...              下一个检测周期（500ms）**立即重新记录** → "数量为 0"的断言
    ...              存在竞态，会偶发失败（真实踩过：单独跑能过、全量跑偶挂）。
    ...              这也是产线/台架的真实做法：清码前先暂停检测，
    ...              避免测试动作互相污染。
    Enter Extended Session
    Send UDS Request    85 02              # 暂停 DTC 检测（消除竞态）
    Response Should Be Positive
    Clear DTC
    ${count}=    Read DTC Count
    Should Be Equal As Integers    ${count}    0
    ...    msg=暂停检测后清除，DTC 数量应为 0，实际 ${count}
    Send UDS Request    85 01              # 恢复检测（后续用例要验证重建）
    Response Should Be Positive

故障条件仍在时 DTC 应重新记录
    [Tags]    smoke    dtc    REQ-DTC-003
    [Documentation]    **恢复性验证**：清除只重置状态机，没修故障。
    ...              系统应重新检测并记录 —— 证明清除没破坏检测能力。
    ...              真实场景对照："清码后过几天又亮"
    Wait Until Keyword Succeeds    20x    1s
    ...    DTC 应以 confirmed 状态存在    P0116

仪表警告灯应与诊断记录一致
    [Tags]    dtc    hmi    REQ-DTC-004
    [Documentation]    跨系统一致性：同一个故障，仪表视角和诊断视角必须吻合
    ...              （灯亮着但诊断仪查不到 DTC = 显示与记录不同步的典型缺陷）
    Telltale Should Be Lit    CoolantTempHigh    timeout=10
    DTC 应以 confirmed 状态存在    P0116

*** Keywords ***
DTC 应以 confirmed 状态存在
    [Documentation]    用状态掩码查询（08=confirmed），精确到状态位而不只是"码存在"
    [Arguments]    ${expected_dtc}
    ${records}=    Read DTC By Mask    08
    ${names}=    Evaluate    [r[0] for r in $records]
    Should Contain    ${names}    ${expected_dtc}
    ...    msg=confirmed 状态列表中未找到 ${expected_dtc}，实际: ${names}
