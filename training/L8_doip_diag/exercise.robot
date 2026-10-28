*** Settings ***
Documentation    L8 练习：DoIP 以太网诊断（车辆识别 / 路由激活 / 诊断透传 / 负向 NACK）
...              目标：用 DoipLibrary 走通 UDP 车辆识别 → TCP 路由激活 → 经 DoIP 透传 UDS。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
Resource         ../../resources/common.resource
Library          ../../libraries/DoipLibrary.py
Suite Setup      Start DoIP Test Environment
Suite Teardown   Stop DoIP Test Environment
Test Teardown    DoIP Disconnect

*** Variables ***
${DOIP_ENTITY_ADDRESS}    0x0E80
${DOIP_TESTER_ADDRESS}    0x0E00
${VIN}                    LSVAM4180NC000001

*** Test Cases ***
车辆识别应返回 VIN 与逻辑地址
    [Documentation]    TODO：DoIP Vehicle Identification 拿 VIN；断言 VIN/逻辑地址/EID。
    ...                关键字：DoIP Vehicle Identification / DoIP Vehicle Logical Address Should Be /
    ...                DoIP Vehicle EID Should Be
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完

路由激活应返回 0x10 并回显地址
    [Documentation]    TODO：DoIP Connect → DoIP Routing Activation（code 应为 0x10）→
    ...                断言回显的 tester 地址 0x0E00 与实体地址 0x0E80。
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

经 DoIP 进入扩展会话应得到正响应
    [Documentation]    TODO：Connect → 激活 → Send Diagnostic "10 03" →
    ...                Read ACK 应为 0x00，Read Response 应为 5003003201f4。
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完

经 DoIP 读取 VIN 应透传多帧
    [Documentation]    TODO：Connect → 激活 → Read VIN，断言 VIN 与用例 1 一致、长度 17。
    Fail    TODO: 删除这一行，按 README §二 用例 4 补完

未路由激活直接发诊断应被 NACK 0x06
    [Documentation]    TODO：只 Connect（不激活）→ Send Diagnostic "10 03" →
    ...                DoIP Should Get NACK 0x06 Diagnostic；再补一次激活证明连接还活着。
    Fail    TODO: 删除这一行，按 README §二 用例 5 补完

*** Keywords ***
Start DoIP Test Environment
    [Documentation]    Suite Setup（已写好，不用改）：先起 ECU/仪表，再起 DoIP 网关。
    Start Vehicle Environment
    Start DoIP Gateway

Stop DoIP Test Environment
    [Documentation]    Suite Teardown（已写好，不用改）：逆序回收 —— 先停网关，再停车辆环境。
    Stop DoIP Gateway
    Stop Vehicle Environment
