*** Settings ***
Documentation    套件 07：车载以太网 / DoIP 诊断测试
...              被测对象：simulators/doip_gateway.py（ISO 13400 教学版 DoIP 网关）
...              覆盖 5 个需求域：
...              REQ-DOIP-001 车辆识别与公告       REQ-DOIP-002 路由激活
...              REQ-DOIP-003 诊断报文透传         REQ-DOIP-004 协议错误处理
...              REQ-DOIP-005 连接保活
...              架构：本套件 ──TCP/UDP 13400(DoIP)──> doip_gateway ──虚拟 CAN──> ecu_sim
...              前置：ECU 由 common.resource 拉起；DoIP 网关由本套件 Suite Setup 拉起
...              运行：python -m robot --outputdir results/dev_doip tests/robot/07_doip_diagnostics.robot
Resource          ../../resources/common.resource
Library           ../../libraries/DoipLibrary.py
Suite Setup       Start DoIP Test Environment
Suite Teardown    Stop DoIP Test Environment
Test Teardown     DoIP Disconnect

*** Variables ***
# DoIP 逻辑地址约定（与 simulators/doip_gateway.py、docs/24 章一致）
${DOIP_ENTITY_ADDRESS}    0x0E80
${DOIP_TESTER_ADDRESS}    0x0E00
${DOIP_ECU_ADDRESS}       0x0001
# 与 ecu_sim.py 的 DID F190 一致：车辆识别响应和 UDS 读 VIN 必须相同
${VIN}                    LSVAM4180NC000001

*** Test Cases ***
通过 UDP 车辆识别应拿到 VIN 与逻辑地址
    [Tags]    REQ-DOIP-001
    [Documentation]    ISO 13400 车辆识别：UDP 0x0001 → 0x0004 响应。
    ...              响应 32 字节 = VIN(17) + 逻辑地址(2) + EID(6) + GID(6) + further action(1)，
    ...              这是 Tester 在"还不知道车是谁"时唯一的上车入口。
    ${vin}=    DoIP Vehicle Identification
    Should Be Equal    ${vin}    ${VIN}
    Length Should Be    ${vin}    17
    DoIP Vehicle Logical Address Should Be    ${DOIP_ENTITY_ADDRESS}
    DoIP Vehicle EID Should Be    001122334455

路由激活应返回 0x10 并回显 tester 地址
    [Tags]    REQ-DOIP-002
    [Documentation]    路由激活请求 0x0005（7 字节）→ 响应 0x0006（13 字节）。
    ...              code=0x10 表示路由激活成功，之后该 TCP 连接才允许发诊断报文；
    ...              响应必须回显 tester 地址与 DoIP 实体地址（便于 Tester 关联请求）。
    DoIP Connect
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10
    DoIP Activated Tester Address Should Be    ${DOIP_TESTER_ADDRESS}
    DoIP Entity Address Should Be    ${DOIP_ENTITY_ADDRESS}

经 DoIP 通道进入扩展会话应得到正响应
    [Tags]    REQ-DOIP-003
    [Documentation]    DoIP 0x8001 承载 UDS `10 03`：网关先回 0x8002 肯定确认（code=0x00），
    ...              再经虚拟 CAN 把请求转给 ECU（0x7E0），把 UDS 响应 50 03 00 32 01 F4
    ...              包进 0x8001 回给 Tester。验证"以太网诊断 → CAN 诊断"的协议转换。
    DoIP Connect
    DoIP Routing Activation
    DoIP Send Diagnostic    10 03
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    Should Be Equal    ${resp}    5003003201f4

经 DoIP 读取 VIN 应透传 ISO-TP 多帧响应
    [Tags]    REQ-DOIP-003
    [Documentation]    DoIP 通道发 UDS `22 F1 90`，响应 20 字节（62 F1 90 + 17 字节 VIN）。
    ...              这条响应在 CAN 上必然是多帧（FF+CF），网关内 DiagnosticTester 完成
    ...              ISO-TP 重组后才包成一条 DoIP 报文 —— 一次验证两侧协议转换都正确。
    ...              断言 VIN 与 UDP 车辆识别拿到的一致，证明"同一个车"。
    DoIP Connect
    DoIP Routing Activation
    ${vin}=    DoIP Read VIN
    Should Be Equal    ${vin}    ${VIN}
    Length Should Be    ${vin}    17

alive check 应返回 DoIP 实体地址
    [Tags]    REQ-DOIP-005
    [Documentation]    0x0007 alive check 请求 → 0x0008 响应（2 字节源地址）。
    ...              真实用途：远程诊断通道长时间空闲时确认对端还活着，防止 TCP
    ...              半开连接被误判为"通道正常"（此时诊断请求会全部超时）。
    DoIP Connect
    DoIP Routing Activation
    ${address}=    DoIP Alive Check
    Should Be Equal As Integers    ${address}    ${DOIP_ENTITY_ADDRESS}

未路由激活时发送诊断报文应被 NACK 0x06 拒绝
    [Tags]    REQ-DOIP-004
    [Documentation]    负向用例：跳过路由激活直接发 0x8001。
    ...              网关必须回诊断报文 NACK（0x8003）code=0x06 target unreachable，
    ...              而不是把请求转给 ECU —— 否则任何网络节点都能未经授权发起诊断。
    ...              再补一次路由激活，证明"拒绝的只是这一条报文，连接没有被误杀"。
    DoIP Connect
    DoIP Send Diagnostic    10 03
    DoIP Should Get NACK    0x06    Diagnostic
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10

未知 payload type 应收到 generic NACK 0x01
    [Tags]    REQ-DOIP-004
    [Documentation]    负向用例：发合法 DoIP 头但 payload type=0x1234（未支持）。
    ...              网关应回通用头 NACK（0x0000）code=0x01 unknown payload type，
    ...              且连接保持可用（用后续路由激活成功来证明）。
    DoIP Connect
    DoIP Send Raw Frame    02fd123400000000
    DoIP Should Get NACK    0x01    Generic
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10

非法协议版本应被 NACK 0x00 并关闭连接
    [Tags]    REQ-DOIP-004
    [Documentation]    负向用例：protocol_version=0x01、inverse=0xFE（互补但版本不支持）。
    ...              头格式不可信时按 ISO 处置：回 generic NACK code=0x00
    ...              （incorrect pattern format）后关闭 TCP 连接，避免后续字节流错位。
    DoIP Connect
    DoIP Send Raw Frame    01fe000100000000
    DoIP Should Get NACK    0x00    Generic
    DoIP Connection Should Be Closed

payload 长度与实际不符应被拒绝
    [Tags]    REQ-DOIP-004
    [Documentation]    负向用例：DoIP 头声明 payload_length=10，实际只发 1 字节。
    ...              网关等不齐 payload，判定 payload 长度异常 →
    ...              generic NACK code=0x04（invalid payload length）+ 关闭连接。
    ...              这条用例同时验证了"TCP 是字节流，长度字段是唯一的报文边界"。
    DoIP Connect
    DoIP Send Raw Frame    02fd00010000000a02
    DoIP Should Get NACK    0x04    Generic
    DoIP Connection Should Be Closed

断开重连后重新路由激活应能继续诊断
    [Tags]    REQ-DOIP-005    REQ-DOIP-003
    [Documentation]    回归用例：路由激活状态是**按 TCP 连接**有效的。
    ...              第一条连接激活 → 断开 → 新连接必须重新激活才能诊断；
    ...              重激活后跑 UDS `3E 00`（TesterPresent）证明通道完全可用。
    ...              真实场景：OTA/远程诊断断线重连后，必须重新走路由激活。
    DoIP Connect
    ${first}=    DoIP Routing Activation
    Should Be Equal As Integers    ${first}    0x10
    DoIP Disconnect
    DoIP Connect
    ${second}=    DoIP Routing Activation
    Should Be Equal As Integers    ${second}    0x10
    DoIP Send Diagnostic    3E 00
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    Should Be Equal    ${resp}    7e00

*** Keywords ***
Start DoIP Test Environment
    [Documentation]    Suite Setup：先起车辆环境（ECU + 仪表盘），再起 DoIP 网关。
    ...              顺序不能反 —— 网关的诊断透传依赖 ECU 在线；而 common.resource
    ...              的 Start Vehicle Environment 会顺带验证诊断链路（ECU Should Respond）。
    Start Vehicle Environment
    Start DoIP Gateway

Stop DoIP Test Environment
    [Documentation]    Suite Teardown：**逆序**回收 —— 先停 DoIP 网关子进程，
    ...              再停车辆环境。网关必须先回收：它占着 13400 端口和一条虚拟
    ...              CAN 总线连接，留着会干扰下一个套件。
    Stop DoIP Gateway
    Stop Vehicle Environment
