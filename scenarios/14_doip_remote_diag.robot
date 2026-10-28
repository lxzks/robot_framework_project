*** Settings ***
Documentation    场景 14：DoIP 远程诊断与 OTA 前健康检查
...              【业务背景】4S 店远程诊断 / OTA 升级前，诊断服务器会先通过
...              车载以太网做一轮"健康检查"：识别车辆（VIN）→ 激活诊断路由 →
...              读故障码确认可升级 → 必要时清码 → 断开重连后继续。真实工作中，
...              这条链路的每一步失败都会导致"远程连不上 / 升级到一半断开"。
...              架构：本场景 ──TCP/UDP 13400(DoIP)──> doip_gateway ──虚拟 CAN──> ecu_sim
...              ECU 由 common.resource 拉起，DoIP 网关由本场景 Suite Setup 拉起。
...              标签对应 REQ-DOIP-001~005。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_scen14 scenarios/14_doip_remote_diag.robot
Resource          ../resources/common.resource
Library           ../libraries/DoipLibrary.py
Suite Setup       Start Remote Diagnosis Environment
Suite Teardown    Stop Remote Diagnosis Environment
Test Teardown     DoIP Disconnect

*** Variables ***
# DoIP 逻辑地址约定（与 simulators/doip_gateway.py 一致）
${DOIP_ENTITY_ADDRESS}    0x0E80
${DOIP_TESTER_ADDRESS}    0x0E00
# 与 ecu_sim.py 的 DID F190 一致：车辆识别 VIN 与 UDS 读 VIN 必须相同
${VIN}                    LSVAM4180NC000001

*** Test Cases ***
远程健康检查应能识别车辆（VIN + 逻辑地址）
    [Tags]    doip    remote    REQ-DOIP-001
    [Documentation]    业务第一步：诊断服务器还不知道"对面是哪台车"。ISO 13400
    ...               车辆识别（UDP 0x0001 → 0x0004）返回 VIN(17) + 逻辑地址 +
    ...               EID/GID，是远程诊断唯一的"上车入口"。
    ${vin}=    DoIP Vehicle Identification
    Should Be Equal    ${vin}    ${VIN}
    Length Should Be    ${vin}    17
    DoIP Vehicle Logical Address Should Be    ${DOIP_ENTITY_ADDRESS}

路由激活后经诊断通道读 VIN 应与车辆识别一致
    [Tags]    doip    remote    REQ-DOIP-002    REQ-DOIP-003
    [Documentation]    业务第二步：路由激活（0x0005 → 0x0006，code=0x10）之后
    ...              这条 TCP 连接才允许发诊断。读 VIN 走的是
    ...              以太网 → 网关 → CAN → ISO-TP → ECU 的完整透传链路；
    ...              两次拿到的 VIN 必须一致（证明"识别到的车"和"诊断到的车"是同一台）。
    ${identified}=    DoIP Vehicle Identification
    DoIP Connect
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10
    ${vin}=    DoIP Read VIN
    Should Be Equal    ${vin}    ${identified}
    ...    msg=诊断通道读到的 VIN 与车辆识别不一致：${vin} vs ${identified}

OTA 前健康检查：默认工况应无故障码
    [Tags]    doip    remote    health    REQ-DOIP-003
    [Documentation]    业务第三步：读 DTC 数量（UDS 19 01）作为"能否升级"的门禁。
    ...               默认工况（车速 0、水温 90℃、电压 13.8V）不应有故障码；
    ...               有码时 OTA 服务器会先要求维修清码，再安排升级。
    DoIP Connect
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10
    DoIP Send Diagnostic    19 01
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    Should Start With    ${resp}    5901ff
    ...    msg=19 01 响应格式应为 59 01 FF <count>，实际 ${resp}
    ${count}=    Evaluate    int('${resp}'[-2:], 16)
    Should Be Equal As Integers    ${count}    0
    ...    msg=健康检查应无故障码，实际 DTC 数量 ${count}

清码后健康检查应重新通过（数量归零）
    [Tags]    doip    remote    health    REQ-DOIP-003
    [Documentation]    业务补充：远程清码（0x14 FF FF FF）是 OTA 前常见的服务动作。
    ...               清完必须回读数量证明归零（"发了清码"≠"真的清了"）。
    DoIP Connect
    DoIP Routing Activation
    DoIP Send Diagnostic    14 ff ff ff
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    Should Be Equal    ${resp}    54
    ...    msg=14 FF FF FF 正响应应为 54，实际 ${resp}
    DoIP Send Diagnostic    19 01
    ${ack}=    DoIP Read ACK
    Should Be Equal As Integers    ${ack}    0x00
    ${resp}=    DoIP Read Response
    ${count}=    Evaluate    int('${resp}'[-2:], 16)
    Should Be Equal As Integers    ${count}    0

断开重连后必须重新路由激活才能继续诊断
    [Tags]    doip    remote    regression    REQ-DOIP-005
    [Documentation]    业务第五步：远程链路会因网络抖动断线。路由激活状态是
    ...               **按 TCP 连接**有效的 —— 重连后不重新激活就发诊断会被拒绝，
    ...               重新激活后诊断链路应完全恢复（用 3E 00 的 7E 00 证明）。
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
    ...    msg=重连并重新激活后 TesterPresent 应回 7E 00，实际 ${resp}

未路由激活时发诊断应被 NACK 0x06（负向）
    [Tags]    doip    remote    negative    REQ-DOIP-004
    [Documentation]    负向用例：跳过路由激活直接发诊断报文（0x8001）。网关必须
    ...               回诊断 NACK code=0x06（target unreachable），不能把请求转给
    ...               ECU —— 否则任何能连上 13400 端口的节点都能未经授权做诊断。
    ...               再补一次路由激活，证明"被拒的只是这条报文，连接没被误杀"。
    DoIP Connect
    DoIP Send Diagnostic    10 03
    DoIP Should Get NACK    0x06    Diagnostic
    ${code}=    DoIP Routing Activation
    Should Be Equal As Integers    ${code}    0x10

*** Keywords ***
Start Remote Diagnosis Environment
    [Documentation]    Suite Setup：先起车辆环境（ECU + 仪表盘），再起 DoIP 网关。
    ...               顺序不能反 —— 网关的诊断透传依赖 ECU 在线，而
    ...               Start Vehicle Environment 会顺带验证诊断链路（ECU Should Respond）。
    Start Vehicle Environment
    Start DoIP Gateway

Stop Remote Diagnosis Environment
    [Documentation]    Suite Teardown：逆序回收 —— 先停 DoIP 网关（它占着 13400
    ...               端口与一条虚拟 CAN 连接），再停车辆环境。
    Stop DoIP Gateway
    Stop Vehicle Environment
