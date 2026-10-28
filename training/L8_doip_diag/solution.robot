*** Settings ***
Documentation    L8 参考答案：DoIP 以太网诊断（车辆识别 / 路由激活 / 诊断透传 / 负向 NACK）
...              链路：Tester ──DoIP 13400──► doip_gateway ──虚拟 CAN──► ecu_sim
...              Suite Setup 顺序：先 Start Vehicle Environment（ECU 在线），再 Start DoIP Gateway
...              （网关的透传依赖 ECU）；Teardown 逆序回收。
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
    [Documentation]    ISO 13400 车辆识别：UDP 0x0001 → 0x0004 响应，Tester 由此知道
    ...                "面前是哪台车"（VIN/逻辑地址/EID）；不需要先建立 TCP。
    ${vin}=    DoIP Vehicle Identification
    Should Be Equal    ${vin}    ${VIN}
    Length Should Be    ${vin}    17
    DoIP Vehicle Logical Address Should Be    ${DOIP_ENTITY_ADDRESS}
    DoIP Vehicle EID Should Be    001122334455

路由激活应返回 0x10 并回显地址
    [Documentation]    TCP 0x0005 路由激活 → 0x0006 响应。code=0x10 表示成功；
    ...                之后这条 TCP 连接才允许发诊断报文（权限按连接隔离）。
    DoIP Connect
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10
    DoIP Activated Tester Address Should Be    ${DOIP_TESTER_ADDRESS}
    DoIP Entity Address Should Be    ${DOIP_ENTITY_ADDRESS}

经 DoIP 进入扩展会话应得到正响应
    [Documentation]    DoIP 0x8001 承载 UDS `10 03`：网关回 0x8002 授权 ACK（code=0），
    ...                然后把 UDS 响应包进 0x8001 回给 Tester。
    DoIP Connect
    DoIP Routing Activation
    DoIP Send Diagnostic    10 03
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    Should Be Equal    ${resp}    5003003201f4

经 DoIP 读取 VIN 应透传多帧
    [Documentation]    UDS `22 F1 90` 响应 20 字节：CAN 侧必须 ISO-TP 多帧，网关内部
    ...                重组后整包放进一条 DoIP 0x8001。VIN 与车辆识别一致 = "同一台车"。
    DoIP Connect
    DoIP Routing Activation
    ${vin}=    DoIP Read VIN
    Should Be Equal    ${vin}    ${VIN}
    Length Should Be    ${vin}    17

未路由激活直接发诊断应被 NACK 0x06
    [Documentation]    负向用例：跳过路由激活直接发 0x8001，网关必须回诊断 NACK
    ...                code=0x06 target unreachable（否则任何网络节点都能发起诊断）。
    ...                再补一次激活成功，证明"拒绝的只是这条报文，连接没被误杀"。
    DoIP Connect
    DoIP Send Diagnostic    10 03
    DoIP Should Get NACK    0x06    Diagnostic
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10

*** Keywords ***
Start DoIP Test Environment
    [Documentation]    先起 ECU/仪表（common.resource），再起 DoIP 网关子进程。
    Start Vehicle Environment
    Start DoIP Gateway

Stop DoIP Test Environment
    [Documentation]    逆序回收：先停网关（释放 13400 与虚拟总线连接），再停车辆环境。
    Stop DoIP Gateway
    Stop Vehicle Environment
