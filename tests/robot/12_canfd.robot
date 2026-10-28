*** Settings ***
Documentation    套件 12：CAN FD 高速总线测试
...              CAN FD（Flexible Data-rate）是经典 CAN 的升级版：数据段最长 64 字节
...              （DLC 0~15 映射到 0~64 字节）、数据段可切高速率（BRS=1）、增加 ESI
...              错误状态指示。ADAS 域控的点云摘要、OTA 元数据、大块传感器融合结果
...              都靠 FD 传输，是"高速总线测试"的核心对象。
...              本套件不起 ECU/仪表盘（与经典 CAN 相互独立），由 CanFdLibrary 懒启动
...              simulators/canfd_sim.py 仿真 FD 总线，Suite Setup/Teardown 自管进程。
...              覆盖能力：
...                ① 64 字节 FD 帧完整收发（DLC=15）
...                ② DLC 0~15 映射表逐项验证（含 12~64 字节的 FD 扩展长度）
...                ③ BRS 位速率切换标志传递
...                ④ ESI 错误状态指示标志传递
...                ⑤ float32 信号解码精度（IEEE-754 单精度）
...                ⑥ 12 灯位图逐灯解码
...                ⑦ 对象列表 4 个障碍物解码
...                ⑧ 序列号/发送计数器连续 + 丢帧检测
...                ⑨ 长度不符/CRC 错误注入与非法帧拒绝
...                ⑩ FD 与经典 CAN 端口隔离、帧类型边界、FD 总线静默
...              需求标签：REQ-CANFD-001 64 字节收发，REQ-CANFD-002 DLC 映射，
...              REQ-CANFD-003 BRS/ESI 标志，REQ-CANFD-004 FD 信号解码与错误处理。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_canfd tests\\robot\\12_canfd.robot
...              端口约定：UDP 组播 233.255.0.3:30003 帧流（CANFD_BUS_PORT 可覆盖）/
...              TCP 127.0.0.1:30004 控制口（CANFD_CTRL_PORT 可覆盖），不占用经典 CAN 30000。
Library          ../../libraries/CanFdLibrary.py
Library          String
Suite Setup      Start CanFD Simulation
Suite Teardown   Stop CanFD Simulation
Test Setup       清空 FD 接收缓存

*** Variables ***
${ROOT}             ${CURDIR}/../..
${ID_STATUS}        0x400    # VehicleStatusFD（64 字节，ADAS 周期 20ms）
${ID_OBJECTS}       0x401    # ObjectList（64 字节，4 个障碍物，ADAS 周期 50ms）
${ID_CONTROL}       0x402    # ControlCommand（测试端发送，变长 8~64 字节）
${ID_CLASSIC}       0x123    # 用于 FD/经典 CAN 边界验证的普通帧 ID

# CAN FD 的 DLC 0~15 对应数据长度（用例②逐项验证）
@{DLC_LENGTHS}      0    1    2    3    4    5    6    7    8    12    16    20    24    32    48    64

# 12 个警告灯（顺序与 vehicle_fd.dbc 一致）
@{TELLTALE_NAMES}   MilWarning    OilPressureLow    CoolantTempHigh    BatteryLow
...                 AbsFault      AirbagFault       SeatbeltWarning    TurnLeft
...                 TurnRight     HighBeam          LowBeam            ParkBrake
# 位图 0xAAA = 1010 1010 1010b：奇数位亮、偶数位灭
@{TELLTALE_BITS}    0    1    0    1    0    1    0    1    0    1    0    1

*** Keywords ***
清空 FD 接收缓存
    [Documentation]    用例隔离：清掉上个用例残留的帧/序列去重窗口。
    Clear FD Frame Buffer

*** Test Cases ***
64 字节 FD 帧应完整收发
    [Tags]    REQ-CANFD-001
    [Documentation]    CAN FD 最大的卖点是数据段 64 字节（DLC=15）。本用例发送一帧
    ...               64 字节 FD 帧，等待仿真器回显到总线上，验证长度/DLC/FD 标志/数据一致性。
    ...               注意：等的是"仿真器回显"（tester_seq 匹配），不是自己发出去的回环副本，
    ...               避免"发送→读回自己"的恒真断言。
    ${data}=    Evaluate    ''.join(f'{index:02x}' for index in range(64))
    ${sent}=    Send FD Frame    ${ID_CONTROL}    ${data}    brs=${True}    esi=${False}
    ${frame}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent}
    FD Frame Length Should Be    ${frame}    64
    Should Be Equal As Integers    ${frame}[dlc]    15
    ...    msg=64 字节 FD 帧的 DLC 必须是 15，实际 ${frame}[dlc]
    Should Be True    ${frame}[is_fd]
    ...    msg=回显帧应标记为 CAN FD 帧，实际 is_fd=${frame}[is_fd]
    Should Be Equal    ${frame}[data_hex]    ${data}
    ...    msg=回显的数据场与发送不一致

DLC 0 到 15 应映射到 CAN FD 规定长度
    [Tags]    REQ-CANFD-002
    [Documentation]    CAN FD 的 DLC 不是"字节数"：0~8 与经典 CAN 相同，9~15 表示
    ...               12/16/20/24/32/48/64 字节（长度压缩编码）。本用例先逐项验证映射表，
    ...               再用 8/12/16/20/24/32/48/64 字节实发，验证 DLC 与数据长度一致。
    FOR    ${dlc}    ${length}    IN ENUMERATE    @{DLC_LENGTHS}
        DLC Should Map To Length    ${dlc}    ${length}
    END
    FOR    ${dlc}    ${length}    IN ENUMERATE    @{DLC_LENGTHS}
        IF    ${length} >= 8
            ${data}=    Evaluate    'cc' * ${length}
            ${sent}=    Send FD Frame    ${ID_CONTROL}    ${data}
            ${frame}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent}
            FD Frame Length Should Be    ${frame}    ${length}
            Should Be Equal As Integers    ${frame}[dlc]    ${dlc}
            ...    msg=${length} 字节数据的 DLC 应为 ${dlc}，实际 ${frame}[dlc]
        END
    END

BRS 标志应随 FD 帧正确传递
    [Tags]    REQ-CANFD-003
    [Documentation]    BRS（Bit Rate Switch）=1 表示"仲裁段用慢速率、数据段切高速率"，
    ...               这是 CAN FD 提升带宽的核心机制。验证 BRS=1/0 两种帧的传递，
    ...               并抽查 ADAS 周期帧 0x400 默认 BRS=1。
    ${data}=    Evaluate    '5a' * 64
    ${sent_on}=    Send FD Frame    ${ID_CONTROL}    ${data}    brs=${True}
    ${frame_on}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent_on}
    BRS Flag Should Be    ${frame_on}    True
    ${sent_off}=    Send FD Frame    ${ID_CONTROL}    ${data}    brs=${False}
    ${frame_off}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent_off}
    BRS Flag Should Be    ${frame_off}    False
    ${status}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    BRS Flag Should Be    ${status}    True

ESI 标志应随 FD 帧正确传递
    [Tags]    REQ-CANFD-003
    [Documentation]    ESI（Error State Indicator）=1 表示发送节点处于 error-passive
    ...               （曾经发错较多），接收方可据此采取降级策略。验证 ESI=1/0 传递，
    ...               并抽查 ADAS 正常周期帧 ESI=0。
    ${data}=    Evaluate    '3c' * 16
    ${sent_normal}=    Send FD Frame    ${ID_CONTROL}    ${data}    dlc=10
    ${frame_normal}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent_normal}
    ESI Flag Should Be    ${frame_normal}    False
    ${sent_error}=    Send FD Frame    ${ID_CONTROL}    ${data}    dlc=10    esi=${True}
    ${frame_error}=    Wait For FD Frame    ${ID_CONTROL}    timeout=2    tester_seq=${sent_error}
    ESI Flag Should Be    ${frame_error}    True
    ${status}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ESI Flag Should Be    ${status}    False

float32 信号解码精度应符合 IEEE-754 单精度
    [Tags]    REQ-CANFD-004
    [Documentation]    CAN FD 大数据帧常用 float32 传输（ADAS 距离/加速度等）。
    ...               本用例设置车速 123.456 km/h，等下一帧 0x400，按 DBC 的
    ...               SIG_VALTYPE_=1（IEEE-754 单精度）解码，并与测试端用 struct
    ...               独立计算出的单精度值比对——证明解码与 DBC 布局一致。
    ${marker}=    Set FD Signal    speed_kmh=123.456    engine_rpm=3456    gear=3
    ${frame}=    Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker}
    ${signals}=    Decode FD Signals    ${frame}
    ${single}=    Evaluate    __import__('struct').unpack('<f', __import__('struct').pack('<f', 123.456))[0]
    Should Be Equal As Numbers    ${signals}[VehicleSpeedFD]    ${single}    precision=6
    Should Be True    abs(${signals}[VehicleSpeedFD] - 123.456) < 0.001
    ...    msg=单精度解码 ${signals}[VehicleSpeedFD] 与 123.456 偏差超过 0.001
    Should Be Equal As Integers    ${signals}[EngineSpeed]    3456
    Should Be Equal As Integers    ${signals}[GearPosition]    3

12 灯位图应逐灯正确解码
    [Tags]    REQ-CANFD-004
    [Documentation]    0x400 的 byte8~9 是 12 个警告灯的位图（bit0~11）。设置
    ...               0xAAA（奇数位亮）后，按 DBC 逐灯解码，12 个灯全部与期望一致。
    ${marker}=    Set FD Signal    telltale_bitmap=0xAAA
    ${frame}=    Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker}
    ${signals}=    Decode FD Signals    ${frame}
    FOR    ${index}    ${name}    IN ENUMERATE    @{TELLTALE_NAMES}
        Should Be Equal As Integers    ${signals}[${name}]    ${TELLTALE_BITS}[${index}]
        ...    msg=灯 ${name} 期望 ${TELLTALE_BITS}[${index}]，实际 ${signals}[${name}]
    END
    ${hex}=    Set Variable    ${frame}[data_hex]
    ${bitmap_hex}=    Get Substring    ${hex}    16    20
    Should Be Equal    ${bitmap_hex}    aa0a
    ...    msg=位图 0xAAA 的小端字节应为 aa 0a，实际 ${bitmap_hex}

对象列表 4 个障碍物应正确解码
    [Tags]    REQ-CANFD-004
    [Documentation]    0x401 ObjectList 一帧 64 字节装 4 个障碍物，每个障碍物
    ...               3 个 float32（x/y/distance）。设置 4 组不同坐标后，验证 12 个
    ...               浮点信号与 ObjectCount 全部正确解码。
    Set FD Object    1    x=10.5    y=-0.25    distance=10.6
    Set FD Object    2    x=20.25    y=1.75    distance=20.4
    Set FD Object    3    x=35.0    y=-3.5    distance=35.2
    ${marker}=    Set FD Object    4    x=-5.5    y=0.125    distance=5.75
    ${frame}=    Wait For FD Frame    ${ID_OBJECTS}    timeout=2    since_seq=${marker}
    FD Frame Length Should Be    ${frame}    64
    ${signals}=    Decode FD Signals    ${frame}
    Should Be Equal As Numbers    ${signals}[Obj1X]    10.5    precision=4
    Should Be Equal As Numbers    ${signals}[Obj1Y]    -0.25    precision=4
    Should Be Equal As Numbers    ${signals}[Obj1Distance]    10.6    precision=4
    Should Be Equal As Numbers    ${signals}[Obj2X]    20.25    precision=4
    Should Be Equal As Numbers    ${signals}[Obj2Y]    1.75    precision=4
    Should Be Equal As Numbers    ${signals}[Obj2Distance]    20.4    precision=4
    Should Be Equal As Numbers    ${signals}[Obj3X]    35.0    precision=4
    Should Be Equal As Numbers    ${signals}[Obj3Y]    -3.5    precision=4
    Should Be Equal As Numbers    ${signals}[Obj3Distance]    35.2    precision=4
    Should Be Equal As Numbers    ${signals}[Obj4X]    -5.5    precision=4
    Should Be Equal As Numbers    ${signals}[Obj4Y]    0.125    precision=4
    Should Be Equal As Numbers    ${signals}[Obj4Distance]    5.75    precision=4
    Should Be Equal As Integers    ${signals}[ObjectCount]    4

序列号与发送计数器应连续且能发现丢帧
    [Tags]    REQ-CANFD-001
    [Documentation]    连续读取 3 帧 0x400：全局 seq 必须严格递增，同一 ID 的发送
    ...               counter 必须逐帧 +1。随后注入 sequence_loss（丢 1 帧），
    ...               即使数据/帧结构看起来正常，counter 跳号也必须被关键字识别——
    ...               这是真实项目里"总线丢帧"的典型检测手段（安全报文常带计数器）。
    ${f1}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ${f2}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ${f3}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    FD Frame Counter Should Advance    ${f1}    ${f2}    ${f3}
    ${before}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    ${marker}=    Inject FD Error    sequence_loss    ${ID_STATUS}    count=1
    ${after}=    Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker}
    ${before_signals}=    Decode FD Signals    ${before}
    ${after_signals}=    Decode FD Signals    ${after}
    Should Be True    ${after_signals}[TimestampMs] > ${before_signals}[TimestampMs]
    ...    msg=时间戳应随帧递增（前后两帧 TimestampMs 未增长）
    Run Keyword And Expect Error    *计数器*
    ...    FD Frame Counter Should Advance    ${before}    ${after}

长度不符与 CRC 错误应被拒绝并识别
    [Tags]    REQ-CANFD-004
    [Documentation]    负向用例：
    ...               ① 注入 length_mismatch：帧结构非法（valid=false），FD Frame Length
    ...                  Should Be 必须报"长度不符"（用 Expect Error 证明灵敏度）；
    ...               ② 注入 crc_error：帧内 crc_valid=false；
    ...               ③ 测试端故意发"64 字节数据 + DLC=8"的非法帧：仿真器必须拒绝、
    ...                  不回显（等待超时）、并在错误日志记录 length_mismatch。
    ${marker}=    Inject FD Error    length_mismatch    ${ID_STATUS}    count=1
    ${bad}=    Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker}
    Should Be True    not ${bad}[valid]
    ...    msg=注入 length_mismatch 后帧应标记 valid=false
    Run Keyword And Expect Error    *长度不符*
    ...    FD Frame Length Should Be    ${bad}    64
    ${marker2}=    Inject FD Error    crc_error    ${ID_STATUS}    count=1
    ${crc_frame}=    Wait For FD Frame    ${ID_STATUS}    timeout=2    since_seq=${marker2}
    Should Be True    not ${crc_frame}[crc_valid]
    ...    msg=注入 crc_error 后帧应标记 crc_valid=false
    Get FD Errors    clear=True
    ${data}=    Evaluate    'aa' * 64
    ${raw}=    Send Raw FD Frame    ${ID_CONTROL}    ${data}    dlc=8
    Run Keyword And Expect Error    *未等到 FD 帧*
    ...    Wait For FD Frame    ${ID_CONTROL}    timeout=0.3    tester_seq=${raw}
    ${errors}=    Get FD Errors
    Should Not Be Empty    ${errors}
    ...    msg=非法帧被拒绝后错误日志不应为空
    ${text}=    Evaluate    str(${errors})
    Should Contain    ${text}    length_mismatch
    ...    msg=错误日志应记录 length_mismatch，实际 ${text}

FD 与经典 CAN 应互不干扰
    [Tags]    REQ-CANFD-001
    [Documentation]    本模块用 233.255.0.3:30003，与经典 CAN 的 233.255.0.1:30000
    ...               完全错开（端口隔离）。另外验证帧类型边界：经典 CAN 帧（is_fd=false、
    ...               最多 8 字节）即使走这条总线也不能被当作 FD 帧解码。最后验证
    ...               FD 总线可静默（暂停周期发送后 0 帧，关键字内部自动恢复）。
    ${info}=    Get FD Bus Info
    ${expected_port}=    Evaluate    int(__import__('os').environ.get('CANFD_BUS_PORT', '30003'))
    Should Be Equal As Integers    ${info}[bus_port]    ${expected_port}
    Should Not Be Equal As Integers    ${info}[bus_port]    ${info}[classic_can_port]
    ...    msg=FD 总线端口不能与经典 CAN 端口 ${info}[classic_can_port] 相同
    Should Be Equal    ${info}[bus_group]    233.255.0.3
    FD Bus Should Be Silent    0.2
    # 静默结束后 ADAS 周期帧应自动恢复
    ${status}=    Wait For FD Frame    ${ID_STATUS}    timeout=2
    FD Frame Length Should Be    ${status}    64
    ${data}=    Evaluate    '11 22 33 44 55 66 77 88'
    ${sent}=    Send FD Frame    ${ID_CLASSIC}    ${data}    is_fd=${False}    brs=${False}
    ${classic}=    Wait For FD Frame    ${ID_CLASSIC}    timeout=2    tester_seq=${sent}
    Should Be True    not ${classic}[is_fd]
    ...    msg=经典 CAN 帧回显应保持 is_fd=false
    FD Frame Length Should Be    ${classic}    8
    Classic CAN Frame Should Not Be Decoded As FD    ${classic}
