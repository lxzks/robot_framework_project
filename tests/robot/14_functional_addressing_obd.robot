*** Settings ***
Documentation    套件 14：功能寻址 + 进阶 UDS 服务（0x2F / 0x23 / 0x3D）+ OBD-II
...              本套件验证三块"真实项目里一定会遇到、但基础套件没覆盖"的诊断能力：
...                ① **功能寻址 0x7DF**：请求广播给所有 ECU，响应仍回各 ECU 的物理响应 ID 0x7E8
...                   （OBD-II 扫描仪就是走功能寻址的；真实项目里还用于全局 TesterPresent）
...                ② **进阶 UDS 服务**：
...                   - 0x2F IOControlByIdentifier：强制控制执行器（本项目 DID 0x3001 = 12 个警告灯）
...                   - 0x23/0x3D Read/WriteMemoryByAddress：按地址读写内存（教学区 0x08000000 起 1KB）
...                ③ **OBD-II（ISO 15031 / SAE J1979）**：法规排放诊断，走 0x7DF
...                   - 0x01 PID 0x0C/0x0D/0x05（转速/车速/水温）
...                   - 0x03 读已确认 DTC / 0x04 清 DTC / 0x09 02 读 VIN
...              0x2F/0x23/0x3D 都需要"扩展会话 + 安全解锁"（权限矩阵与 0x2E 写 DID 同级）。
...
...              [Tags] 需求映射：
...                REQ-DIAG-017 功能寻址       REQ-DIAG-018 0x2F 执行器控制
...                REQ-DIAG-019 0x23/0x3D     REQ-DIAG-020 OBD 实时数据
...                REQ-DIAG-021 OBD DTC/清码  REQ-DIAG-022 OBD VIN
Resource         ../../resources/common.resource
Library          ../../libraries/ObdLibrary.py
Suite Setup      启动环境并打开 OBD 通道
Suite Teardown   关闭功能寻址测试环境
Test Setup       准备诊断会话

*** Test Cases ***
功能寻址 TesterPresent 应有响应且响应来自 0x7E8
    [Tags]    diag    functional    REQ-DIAG-017
    [Documentation]    诊断仪把 3E 00 发到功能寻址 0x7DF（广播），ECU 应处理，
    ...              且响应必须从**物理响应 ID 0x7E8** 回来（不是 0x7DF）。
    ...              这是 0x7DF 与 0x7E0 的核心区别：广播请求、点对点响应。
    ${raw}=    Send Functional UDS Request    3E 00
    ObdLibrary.Response Should Come From    0x7E8
    ObdLibrary.Response Should Be Positive
    Should Be Equal    ${raw}    7e00

功能寻址读 DID 应正常（多帧响应自动重组）
    [Tags]    diag    functional    REQ-DIAG-017
    [Documentation]    功能寻址下读零件号 F187：响应 62 F1 87 + 15 字节 = 18 字节，
    ...              必须经 ISO-TP FF/CF 多帧传输并由测试端正确重组。
    ${raw}=    Send Functional UDS Request    22 F1 87
    ObdLibrary.Response Should Be Positive
    Should Start With    ${raw}    62f187
    ${payload}=    Last Response Payload Hex
    ${data}=    Evaluate    '${payload}'[4:]
    ${text}=    Evaluate    bytes.fromhex('${data}').decode('ascii')
    Should Be Equal    ${text}    HW-IC-2026-001

0x2F 交还执行器控制权应成功
    [Tags]    diag    io-control    REQ-DIAG-018
    [Documentation]    0x00 returnControlToECU：把执行器控制权交回 ECU（日常诊断/收尾动作）。
    ...              正响应 6F <DID> <controlState>。
    ${raw}=    IOControl Send    0x3001    0    0
    ObdLibrary.Response Should Be Positive
    Should Start With    ${raw}    6f3001

0x2F 短时控制应点亮指定警告灯
    [Tags]    diag    io-control    REQ-DIAG-018
    [Documentation]    shortTermAdjustment(0x02) + 掩码 0x0004（bit2=CoolantTempHigh）：
    ...              ECU 应强制把 0x300 报文的该灯置 1，仪表盘随之点亮 ——
    ...              这是产线"点灯测试/执行器验证"的最小闭环。
    ...              最后交还控制权，避免污染后续用例（灯灭）。
    ${raw}=    IOControl Send    0x3001    2    0x0004
    ObdLibrary.Response Should Be Positive
    Should Be Equal    ${raw}    6f30010004
    Telltale Should Be Lit    CoolantTempHigh    timeout=10
    # 收尾：交还控制权 → 0x300 回到 DTC 驱动（当前无故障 → 灯应熄灭）
    ${raw}=    IOControl Send    0x3001    0    0
    ObdLibrary.Response Should Be Positive
    Telltale Should Be Off    CoolantTempHigh    timeout=10

0x2F 在默认会话应被拒绝
    [Tags]    diag    io-control    security    REQ-DIAG-018
    [Documentation]    权限矩阵：默认会话 → NRC 0x7F（当前会话不支持）；
    ...              扩展会话但未解锁 → NRC 0x33（securityAccessDenied）。
    Enter Default Session
    Send Physical UDS Request    2F 30 01 02 00 04
    ObdLibrary.Response Should Be Negative With NRC    0x7F
    # 切回扩展会话会重新锁定安全访问（10 03 会清 unlocked）→ 应报 0x33
    Enter Extended Session
    Send Physical UDS Request    2F 30 01 02 00 04
    ObdLibrary.Response Should Be Negative With NRC    0x33

0x23 按地址读内存应成功
    [Tags]    diag    memory    REQ-DIAG-019
    [Documentation]    教学区 0x08000000 起 1KB，前 17 字节映射 VIN。
    ...              先读 4 字节（"LSVA"）验证地址映射，再读 17 字节（VIN，多帧响应）。
    ${data}=    Read Memory By Address    0x08000000    4
    Should Be Equal    ${data}    4c535641
    ${data}=    Read Memory By Address    0x08000000    17
    ${vin}=    Evaluate    bytes.fromhex('${data}').decode('ascii')
    Should Be Equal    ${vin}    LSVAM4180NC000001

0x3D 写入后 0x23 应能读回一致
    [Tags]    diag    memory    REQ-DIAG-019
    [Documentation]    写 4 字节 DEADBEEF 到 0x08000010（请求 11 字节 → 走 ISO-TP 多帧），
    ...              正响应 7D；随后 0x23 读回必须一致。真实用途：产线写配置/Bootloader 刷写。
    ${raw}=    Write Memory By Address    0x08000010    DEADBEEF
    Should Be Equal    ${raw}    7d
    ${data}=    Read Memory By Address    0x08000010    4
    Should Be Equal    ${data}    deadbeef

0x23 地址越界应回 NRC 0x31
    [Tags]    diag    memory    negative    REQ-DIAG-019
    [Documentation]    教学区 0x08000000~0x080003FF：超出上界或低于下界都必须
    ...              回 NRC 0x31 requestOutOfRange（不能越界读到/写坏其他内存）。
    Send Physical UDS Request    23 14 08001000 04
    ObdLibrary.Response Should Be Negative With NRC    0x31
    Send Physical UDS Request    23 14 07FFFFF0 04
    ObdLibrary.Response Should Be Negative With NRC    0x31

OBD mode01 实时数据换算应与 ECU 信号一致
    [Tags]    diag    obd    REQ-DIAG-020
    [Documentation]    等车速稳定到 60 附近后，用 OBD PID 0x0D/0x0C/0x05 读到的值
    ...              必须与总线上 DBC 信号一致（换算公式 rpm=((A*256)+B)/4、
    ...              车速=A、水温=A-40）。这验证 OBD 数据源与整车信号同源。
    Wait Until Signal In Range    EngineData    VehicleSpeed    59    61    timeout=25
    Sleep    0.5s                      # 等斜坡加速完全收敛到目标车速
    ${bus_speed}=    Read Signal From Bus    EngineData    VehicleSpeed
    ${bus_rpm}=    Read Signal From Bus    EngineData    EngineSpeed
    ${bus_coolant}=    Read Signal From Bus    EngineData    CoolantTemp
    ${obd_speed}=    OBD Read Speed
    ${obd_rpm}=    OBD Read RPM
    ${obd_coolant}=    OBD Read Coolant
    ${diff}=    Evaluate    abs(${obd_speed} - ${bus_speed})
    Should Be True    ${diff} <= 2
    ...    msg=OBD 车速 ${obd_speed} 与总线 ${bus_speed} 偏差 ${diff}（应 ≤2）
    ${diff}=    Evaluate    abs(${obd_rpm} - ${bus_rpm})
    Should Be True    ${diff} <= 100
    ...    msg=OBD 转速 ${obd_rpm} 与总线 ${bus_rpm} 偏差 ${diff}（应 ≤100）
    ${diff}=    Evaluate    abs(${obd_coolant} - ${bus_coolant})
    Should Be True    ${diff} <= 2
    ...    msg=OBD 水温 ${obd_coolant} vs 总线 ${bus_coolant}，偏差 ${diff}（应 ≤2）

OBD mode03/04/09 应能读 DTC、清 DTC、读 VIN
    [Tags]    diag    obd    REQ-DIAG-021    REQ-DIAG-022
    [Documentation]    先用工程模式 DID 注入 9V 低压 → ECU 记录并确认 P0562；
    ...              再用 OBD mode 0x03 读到它（2 字节 DTC 编码 05 62 = P0562），
    ...              mode 0x04 清码后列表为空，mode 0x09 02 读到 VIN。
    ...              注意：OBD 服务走功能寻址、不需要厂家会话/解锁（法规要求）。
    Set Test Stimulus    voltage=9
    Wait Until Keyword Succeeds    15x    1s    DTC List Should Contain    P0562
    ${dtcs}=    OBD Read DTCs
    Should Contain    ${dtcs}    P0562
    ${raw}=    OBD Clear DTCs
    Should Be Equal    ${raw}    44
    ${dtcs}=    OBD Read DTCs
    Should Be Empty    ${dtcs}
    ${vin}=    OBD Read VIN
    Should Be Equal    ${vin}    LSVAM4180NC000001
    # 收尾：恢复供电电压（否则污染后续用例/套件）
    Run Keyword And Ignore Error    Clear Test Stimulus
    Run Keyword And Ignore Error    Clear DTC

*** Keywords ***
启动环境并打开 OBD 通道
    [Documentation]    起 ECU + headless 仪表（公共环境），再打开本库的 OBD/功能寻址通道。
    Start Vehicle Environment    speed=60
    Open OBD Channel

准备诊断会话
    [Documentation]    用例隔离 + 权限前置：先复位 ECU（清会话/权限/安全状态），
    ...              再进扩展会话并安全解锁 —— 0x2F/0x23/0x3D 都需要这两层权限。
    ...              复位不影响车速目标/信号模型/DTC 记录（只清诊断状态）。
    Run Keyword And Ignore Error    Send UDS Request    11 01
    Sleep    0.3s
    Enter Extended Session
    Security Unlock

关闭功能寻址测试环境
    [Documentation]    先关 OBD 通道（释放它自己的虚拟总线），再走公共环境回收。
    Run Keyword And Ignore Error    Close OBD Channel
    Stop Vehicle Environment
