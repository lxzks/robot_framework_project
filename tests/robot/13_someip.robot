*** Settings ***
Documentation    套件 13：SOME/IP 与 SOA 服务测试
...              被测对象：simulators/someip_service.py（SOME/IP + SOME/IP-SD 教学版服务）
...              覆盖 4 个需求域：
...              REQ-SOMEIP-001 服务发现      REQ-SOMEIP-002 方法调用
...              REQ-SOMEIP-003 事件订阅通知  REQ-SOMEIP-004 协议错误处理
...              架构：本套件 ──UDP 30490(SOME/IP-SD)──> 服务发现 Offer/Find/Subscribe
...                    ──UDP/TCP 30501(SOME/IP)──> Method REQUEST/RESPONSE + Event NOTIFICATION
...              本套件不依赖 ECU / 虚拟 CAN（仿真服务独立生成车速数据源，默认 60 km/h）
...              运行：python -m robot --outputdir results/dev_someip tests/robot/13_someip.robot
Library           ../../libraries/SomeIpLibrary.py
Suite Setup       Start SomeIP Test Environment
Suite Teardown    Stop SomeIP Test Environment
Test Teardown     SomeIP Cleanup

*** Variables ***
${SERVICE_VEHICLE}     0x1234
${SERVICE_HVAC}        0x5678
${INSTANCE}            0x0001
${METHOD_GET_SPEED}    0x0001
${METHOD_SET_LIMIT}    0x0002
${METHOD_SET_TEMP}     0x0001
${EVENT_SPEED}         0x8001
${EVENTGROUP}          0x0001
# 仿真服务独立生成的车速初值（Start SomeIP Simulation 的 speed 参数默认 60）
${EXPECTED_SPEED}      60
# int16 60 km/h 的大端编码
${SPEED_HEX}           003c

*** Test Cases ***
服务发现应收到启动公告并能通过 Find 发现两个服务
    [Tags]    REQ-SOMEIP-001
    [Documentation]    服务端启动时主动发 OfferService 公告（SD 0x0004 语义，TTL>0），
    ...              公告里必须包含两个服务及其端点（service/instance/major/TTL/endpoint）。
    ...              测试端再发 FindService 请求，服务端应回 Offer —— 这是 SOA"上车发现服务"的入口。
    SomeIP Startup Offer Should Contain    ${SERVICE_VEHICLE}
    SomeIP Startup Offer Should Contain    ${SERVICE_HVAC}
    SomeIP Send SD Find    ${SERVICE_VEHICLE}    ${INSTANCE}
    ${offer}=    SomeIP Read SD Offer
    Should Contain    ${offer}    0x1234
    ${hvac}=    SomeIP Service Should Be Offered    ${SERVICE_HVAC}    ${INSTANCE}
    Should Contain    ${hvac}    0x5678

GetSpeed 方法调用应成功并解码车速
    [Tags]    REQ-SOMEIP-002
    [Documentation]    REQUEST → RESPONSE：0x1234/0x0001（GetSpeed）请求空，
    ...              响应 int16 大端 = 60 km/h（003c）。同时验证第二个服务
    ...              HvacService 的 SetTemp（int8 回显）—— 两个服务都要能调用。
    ${resp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    Should Be Equal    ${resp}    ${SPEED_HEX}
    ${speed}=    SomeIP Decode Int16    ${resp}
    Should Be Equal As Integers    ${speed}    ${EXPECTED_SPEED}
    # 第二个服务：HvacService.SetTemp 请求 int8 0x16=22℃，响应回显
    ${temp_echo}=    SomeIP Call Method    ${SERVICE_HVAC}    ${METHOD_SET_TEMP}    16
    SomeIP Call Should Succeed
    Should Be Equal    ${temp_echo}    16
    ${temp_value}=    SomeIP Decode Int8    ${temp_echo}
    Should Be Equal As Integers    ${temp_value}    22

SetSpeedLimit 应回显限速值且不改变当前车速
    [Tags]    REQ-SOMEIP-002
    [Documentation]    SetSpeedLimit 请求 uint16（120 → 0078），响应回显同一值；
    ...              同时验证"设定值"与"实际值"是两个数据：限速 120 后 GetSpeed 仍为 60。
    ...              这正是 SOA 服务测试容易漏的点——只断言"调用成功"，没验证语义隔离。
    ${echo}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_SET_LIMIT}    0078
    SomeIP Call Should Succeed
    Should Be Equal    ${echo}    0078
    ${limit}=    SomeIP Decode Uint16    ${echo}
    Should Be Equal As Integers    ${limit}    120
    ${resp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    ${speed}=    SomeIP Decode Int16    ${resp}
    Should Be Equal As Integers    ${speed}    ${EXPECTED_SPEED}

订阅 SpeedChanged 后应收到通知且数值与 GetSpeed 一致
    [Tags]    REQ-SOMEIP-003
    [Documentation]    订阅 eventgroup（SD SubscribeEventgroup → Ack），服务端每 500ms 发
    ...              NOTIFICATION(0x8001)。断言通知里的车速与同时刻 GetSpeed 方法响应一致 ——
    ...              "事件流"和"方法请求"是同一个数据源，不一致就是服务实现缺陷。
    SomeIP Subscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
    ${resp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    ${method_speed}=    SomeIP Decode Int16    ${resp}
    ${payload}=    SomeIP Wait For Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
    ${event_speed}=    SomeIP Decode Uint16    ${payload}
    Should Be Equal As Integers    ${event_speed}    ${method_speed}
    SomeIP Notification Value Should Be    ${method_speed}

未知 service_id 应返回错误码 0x01
    [Tags]    REQ-SOMEIP-004
    [Documentation]    负向用例：请求一个未定义的服务 0x9999，服务端必须回 ERROR(0x81)
    ...              且 return_code=0x01（教学版"未知服务"码），而不是静默丢弃或崩溃。
    SomeIP Call Method    0x9999    0x0001
    SomeIP Response Should Be Error    0x01

未知 method_id 应返回错误码 0x02
    [Tags]    REQ-SOMEIP-004
    [Documentation]    负向用例：服务存在但方法号 0x00FF 未定义 → ERROR + return_code=0x02。
    SomeIP Call Method    ${SERVICE_VEHICLE}    0x00FF
    SomeIP Response Should Be Error    0x02

interface_version 不匹配应返回错误码 0x03
    [Tags]    REQ-SOMEIP-004
    [Documentation]    负向用例：VehicleService 接口主版本是 1，用 9 去调用必须被拒绝
    ...              → ERROR + return_code=0x03（教学版"版本不匹配"码）。
    ...              真实项目里这对应"client 与服务端 ARXML 版本不一致"，是集成期高频故障。
    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}    ${EMPTY}    9
    SomeIP Response Should Be Error    0x03

UDP 与 TCP 两种传输均应可用
    [Tags]    REQ-SOMEIP-002
    [Documentation]    同一服务端口 30501 同时监听 UDP 和 TCP。UDP 调一次 → 切 TCP 调一次
    ...              → 结果必须一致 → 切回 UDP 再调一次仍然可用（验证传输切换不残留状态）。
    ${udp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    SomeIP Tcp Mode
    ${tcp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    Should Be Equal    ${tcp}    ${udp}
    SomeIP Tcp Mode    ${False}
    ${again}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    Should Be Equal    ${again}    ${udp}

原始帧长度不符应被拒绝且服务不崩溃
    [Tags]    REQ-SOMEIP-004
    [Documentation]    负向用例：构造两种畸形报文 ——
    ...              ① length 字段声明 32 字节但实际只有 16 字节（截断/伪造）；
    ...              ② 只有 8 字节，连 16 字节头都不完整。
    ...              服务端必须丢弃且**不崩溃**：随后一次正常 GetSpeed 调用仍然成功才算通过。
    SomeIP Send Raw Frame    12340001000000200001000101010000
    SomeIP Send Raw Frame    1234000100000008
    ${resp}=    SomeIP Call Method    ${SERVICE_VEHICLE}    ${METHOD_GET_SPEED}
    SomeIP Call Should Succeed
    Should Be Equal    ${resp}    ${SPEED_HEX}

事件周期应稳定且退订后静默
    [Tags]    REQ-SOMEIP-003
    [Documentation]    事件是按 500ms 周期发的：采样 5 段间隔，每段应在 500±200ms 内；
    ...              然后发 StopSubscribe（TTL=0），宽限期后不得再收到通知 ——
    ...              验证"退订真的生效"，避免服务端忽略 TTL=0 导致事件洪泛。
    SomeIP Subscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
    ${avg}=    SomeIP Event Period Should Be    500    200
    Should Be True    400 <= ${avg} <= 600
    SomeIP Unsubscribe Event    ${SERVICE_VEHICLE}    ${EVENT_SPEED}
    SomeIP Should Receive No Notification    ${SERVICE_VEHICLE}    ${EVENT_SPEED}    1.5

*** Keywords ***
Start SomeIP Test Environment
    [Documentation]    Suite Setup：拉起 SOME/IP 服务仿真子进程。
    ...              就绪判定 = 收到服务端启动 OfferService 公告且包含两个服务。
    Start SomeIP Simulation

Stop SomeIP Test Environment
    [Documentation]    Suite Teardown：回收仿真子进程（terminate + wait）并释放所有 socket。
    Stop SomeIP Simulation
