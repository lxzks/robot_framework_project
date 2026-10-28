*** Settings ***
Documentation    套件 08：LIN 总线与车身控制测试
...              LIN（Local Interconnect Network）是单线、低速、主从结构的车身总线：
...              雨量传感器、雨刷、座椅、车门、后视镜等"低速执行器/传感器"都挂 LIN，
...              再通过网关（BCM）和 CAN 主干通信。
...              本套件不起 ECU/仪表盘（LIN 模块独立于 CAN），由 LinLibrary 懒启动
...              simulators/lin_sim.py 仿真 LIN 总线，Suite Setup/Teardown 自管进程。
...              覆盖能力：
...                ① 调度表时基/周期抖动（10ms 槽位 × 4 报文 = 40ms 周期）
...                ② 从节点信号：雨量、雨刷、座椅（2 字节多信号位布局）
...                ③ 两种校验和：经典（0x3C）/ 增强（其余），独立重算交叉校验
...                ④ 睡眠/唤醒：0x3C 主请求 0x00 停调度、wakeup pulse 恢复
...                ⑤ 负向/错误注入：checksum 错、PID 奇偶错、从机无响应（超时）
...              需求标签：REQ-LIN-001 调度/时序，REQ-LIN-002 从节点信号响应，
...              REQ-LIN-003 校验和，REQ-LIN-004 睡眠/唤醒，REQ-LIN-005 错误容错。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_lin tests\\robot\\08_lin_bus.robot
...              端口约定：UDP 127.0.0.1:30001 帧流 / TCP 127.0.0.1:30002 控制口
...              （可用环境变量 LIN_UDP_PORT / LIN_CTRL_PORT 覆盖）。
Library          ../../libraries/LinLibrary.py
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation
Test Setup       恢复 LIN 总线到清醒状态

*** Variables ***
${ROOT}             ${CURDIR}/../..
${FRAME_RAIN}       0x10    # 雨量传感器状态帧（1 字节，增强校验和）
${FRAME_WIPER}      0x11    # 雨刷状态帧（1 字节，增强校验和）
${FRAME_SEAT}       0x12    # 座椅位置帧（2 字节，增强校验和）
${FRAME_MASTER}     0x3C    # 主请求帧（8 字节，经典校验和）

*** Keywords ***
恢复 LIN 总线到清醒状态
    [Documentation]    用例隔离：无论上一个用例留下"睡眠"还是"错误注入"状态，
    ...               本用例开始前都先唤醒总线并清空接收缓存。
    ...               错误注入都是一次性/有限次数任务（count），不会跨用例残留；
    ...               睡眠状态则会跨用例残留，所以必须每次显式唤醒。
    Wake Up LIN Bus
    Clear LIN Frame Buffer

*** Test Cases ***
调度表应按 10ms 时基轮询四个报文
    [Tags]    REQ-LIN-001
    [Documentation]    设计：调度表 4 个槽位 × 每槽 10ms → 每个报文周期 40ms。
    ...               Clear 后采样 2 秒，统计相邻帧间隔均值（应 ≈10ms）、
    ...               每个帧 ID 的周期均值（应 ≈40ms）和抖动 jitter（max-min）。
    ...               真实工作对应：LIN 调度表时序验证（用示波器/分析仪看周期与抖动）。
    Clear LIN Frame Buffer
    Sleep    2s
    ${stats}=    LIN Schedule Timing Should Be Within
    ...    expected_slot_ms=10
    ...    period_tolerance_ms=6
    ...    jitter_tolerance_ms=15
    Should Be True    ${stats}[frames] >= 160
    ...    msg=2 秒应收到约 200 帧（4 报文 × 50 圈），实际 ${stats}[frames] 帧

雨量传感器信号设置后应出现在状态帧中
    [Tags]    REQ-LIN-002
    [Documentation]    Set LIN Slave Signal 写从机信号 → 下一帧 0x10 里应能读到。
    ...               雨量 75% 按 LDF 编码为原始值 150（75 / 0.5 = 150），
    ...               既断言原始字节（协议层）也断言 LDF 物理值（应用层）。
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=75
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[data][0]    150
    ...    msg=75%% 雨量应编码为原始值 150，实际字节 ${frame}[data][0]
    LIN Frame Should Contain Signal    ${frame}    rain_intensity    75    tolerance=0.5

座椅两字节多信号应按位布局正确编码解码
    [Tags]    REQ-LIN-002
    [Documentation]    0x12 用 2 字节装两个信号：seat_slide（bit0~7，1mm/LSB）、
    ...               seat_backrest（bit8~15，0.5°/LSB）。设置 200mm / 45° 后，
    ...               原始字节应为 [200, 90]，且两个信号都要能从同一帧解出来。
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}
    ...    seat_slide=200    seat_backrest=45
    ${frame}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[length]    2
    Should Be Equal As Integers    ${frame}[data][0]    200
    ...    msg=seat_slide 200mm 应为字节 0 = 200，实际 ${frame}[data][0]
    Should Be Equal As Integers    ${frame}[data][1]    90
    ...    msg=seat_backrest 45° 应为字节 1 = 90（45 / 0.5），实际 ${frame}[data][1]
    LIN Frame Should Contain Signal    ${frame}    seat_slide    200
    LIN Frame Should Contain Signal    ${frame}    seat_backrest    45

经典与增强校验和均应校验通过
    [Tags]    REQ-LIN-003
    [Documentation]    0x3C 主请求帧用经典校验和（只累加数据字节，LIN 2.x 规定），
    ...               其余帧用增强校验和（PID + 数据字节）。库里的校验算法是
    ...               **独立实现**（不复用仿真器代码），重算后与帧内 checksum 比对——
    ...               否则仿真器算错时两边一起错，用例就恒真了。
    ${classic}=    Wait For LIN Frame    ${FRAME_MASTER}    timeout=2
    Should Be Equal    ${classic}[checksum_type]    classic
    ...    msg=0x3C 主请求帧必须使用经典校验和，实际 ${classic}[checksum_type]
    LIN Checksum Should Be Valid    ${classic}
    ${enhanced}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2
    Should Be Equal    ${enhanced}[checksum_type]    enhanced
    ...    msg=0x10 应使用增强校验和，实际 ${enhanced}[checksum_type]
    LIN Checksum Should Be Valid    ${enhanced}
    ${seat}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2
    LIN Checksum Should Be Valid    ${seat}

校验和与 PID 奇偶错误注入后应被识别为无效帧
    [Tags]    REQ-LIN-003    REQ-LIN-005
    [Documentation]    负向用例（证明校验不是恒真）：
    ...               ① 注入 checksum 错误 → 帧被标记 checksum_valid=false，
    ...                  LIN Checksum Should Be Valid 必须失败（用 Expect Error 断言）；
    ...               ② 注入 bad_parity → PID 奇偶位错误，LIN PID Should Be Valid 必须失败。
    ${marker}=    Inject LIN Error    checksum    ${FRAME_RAIN}    count=1
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be True    not ${frame}[checksum_valid]
    ...    msg=注入 checksum 错误后帧应标记 checksum_valid=false，实际 ${frame}
    Run Keyword And Expect Error    *校验和*
    ...    LIN Checksum Should Be Valid    ${frame}
    ${marker2}=    Inject LIN Error    bad_parity    ${FRAME_WIPER}    count=1
    ${frame2}=    Wait For LIN Frame    ${FRAME_WIPER}    timeout=2    since_seq=${marker2}
    Should Be True    not ${frame2}[pid_parity_valid]
    ...    msg=注入 bad_parity 后帧应标记 pid_parity_valid=false，实际 ${frame2}
    Run Keyword And Expect Error    *PID*
    ...    LIN PID Should Be Valid    ${frame2}

从节点无响应注入后等待应超时
    [Tags]    REQ-LIN-005
    [Documentation]    负向用例：注入 no_response（连续 5 帧 0x12 从机不应答）后，
    ...               Wait For LIN Frame 必须在超时时间附近报"未等到帧"，
    ...               且错误注入只影响目标帧、不能把整条总线搞死（其他帧仍可收到），
    ...               仿真器错误计数 skipped 应递增（主节点记录到了这次从机丢失）。
    ${marker}=    Inject LIN Error    no_response    ${FRAME_SEAT}    count=5
    ${start}=    Evaluate    __import__('time').time()
    Run Keyword And Expect Error    *未等到帧*
    ...    Wait For LIN Frame    ${FRAME_SEAT}    timeout=0.15    since_seq=${marker}
    ${elapsed}=    Evaluate    __import__('time').time() - ${start}
    Should Be True    ${elapsed} >= 0.14
    ...    msg=等待应立即超时（实测 ${elapsed}s），实际没按 timeout 返回
    Should Be True    ${elapsed} < 1.5
    ...    msg=等待超时后返回过慢（实测 ${elapsed}s），可能挂起
    ${other}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=1    since_seq=${marker}
    Should Be Equal As Integers    ${other}[id]    16
    ...    msg=no_response 只应影响 0x12，其他帧必须照常（实际收到 ${other}[id]）
    ${stats}=    Get LIN Statistics
    Should Be True    ${stats}[skipped] >= 1
    ...    msg=从机无响应的帧应计入错误计数 skipped，实际 ${stats}[skipped]

进入睡眠后总线应保持静默
    [Tags]    REQ-LIN-004
    [Documentation]    LIN 睡眠协议：主节点发 0x3C 主请求帧，首字节 0x00 = 睡眠命令。
    ...               期望：命令帧发出后调度停止，总线上不再有任何帧（静默），
    ...               仿真器状态 sleeping=true。
    ...               真实工作对应：车身网络"熄火后 LIN 从机休眠、静态电流"验证。
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    LIN Bus Should Be Silent    0.5
    ${stats}=    Get LIN Statistics
    Should Be True    ${stats}[sleeping]
    ...    msg=睡眠命令后仿真器应报告 sleeping=true，实际 ${stats}[sleeping]
    Should Be True    ${stats}[sleep_commands] >= 1
    ...    msg=应记录到至少一次睡眠命令，实际 ${stats}[sleep_commands]

唤醒脉冲后调度应恢复
    [Tags]    REQ-LIN-004
    [Documentation]    先睡眠 → 确认静默 → 发唤醒脉冲（Wake Up LIN Bus）→
    ...               调度应立即恢复，下一帧 0x10 在超时内到达，且唤醒事件被记录。
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.15s
    LIN Bus Should Be Silent    0.2
    ${marker}=    Wake Up LIN Bus
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[id]    16
    ...    msg=唤醒后应恢复发 0x10 雨量帧，实际收到 ${frame}[id]
    ${pulses}=    Get LIN Events    wakeup_pulse
    Should Not Be Empty    ${pulses}    msg=唤醒脉冲事件 wakeup_pulse 应被记录
    ${stats}=    Get LIN Statistics
    Should Be True    not ${stats}[sleeping]
    ...    msg=唤醒后 sleeping 应为 false，实际 ${stats}[sleeping]

LDF 解码应还原座椅多信号物理值
    [Tags]    REQ-LIN-002
    [Documentation]    LDF 是 LIN 网络的"数据库"（信号位布局/缩放/枚举）。
    ...               本用例显式加载 simulators/ldf/vehicle.ldf，按定义把原始字节
    ...               [128, 120] 解码回 seat_slide=128mm、seat_backrest=60°，
    ...               并验证 LDF 里不存在的帧 ID 会报清晰错误（证明解码走真定义）。
    Set LDF File    ${ROOT}/simulators/ldf/vehicle.ldf
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}    seat_slide=128    seat_backrest=60
    ${frame}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[data][0]    128
    Should Be Equal As Integers    ${frame}[data][1]    120
    ...    msg=60° 按 0.5°/LSB 应编码为 120，实际 ${frame}[data][1]
    ${decoded}=    Decode LIN Frame By LDF    ${frame}
    Should Be Equal As Numbers    ${decoded}[seat_slide]    128
    ...    msg=LDF 解码 seat_slide 应为 128mm，实际 ${decoded}[seat_slide]
    Should Be Equal As Numbers    ${decoded}[seat_backrest]    60
    ...    msg=LDF 解码 seat_backrest 应为 60°，实际 ${decoded}[seat_backrest]
    ${unknown}=    Evaluate    {"id": 61, "data": [0, 0]}
    Run Keyword And Expect Error    *LDF*    Decode LIN Frame By LDF    ${unknown}

总线无响应时等待超时时间应符合预期
    [Tags]    REQ-LIN-005    REQ-LIN-001
    [Documentation]    总线睡眠 = 完全没有帧。此时 Wait For LIN Frame 既不能立刻成功
    ...               （不能读到旧缓存帧），也不能无限挂起：必须在 timeout 附近返回
    ...               "未等到帧"失败。用 since_seq 标记排除睡眠命令帧本身。
    ${marker}=    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    ${start}=    Evaluate    __import__('time').time()
    Run Keyword And Expect Error    *未等到帧*
    ...    Wait For LIN Frame    ${FRAME_MASTER}    timeout=0.4    since_seq=${marker}
    ${elapsed}=    Evaluate    __import__('time').time() - ${start}
    Should Be True    ${elapsed} >= 0.38
    ...    msg=超时返回过早（实测 ${elapsed}s < 0.38s），等待逻辑可能没生效
    Should Be True    ${elapsed} <= 1.5
    ...    msg=超时返回过晚（实测 ${elapsed}s > 1.5s），等待逻辑可能卡死
