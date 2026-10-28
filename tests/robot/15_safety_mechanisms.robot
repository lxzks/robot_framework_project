*** Settings ***
Documentation    套件 15：功能安全机制验证（E2E Profile 1 / 看门狗 / 降级与安全状态）
...              被测对象：simulators/safety_ecu.py —— 独立安全节点进程（UDP 127.0.0.1:30600，
...              端口可用环境变量 SAFETY_UDP_PORT 覆盖）。本套件不需要 ECU/虚拟 CAN，可独立进 CI。
...              验证三类安全机制：① E2E 端到端保护（CRC + 计数器 + 重放防护）
...              ② 看门狗（心跳停止 300ms → SAFE_STATE → 恢复）③ 降级/安全状态（NORMAL→DEGRADED→
...              SAFE_STATE→恢复）。对应需求：REQ-SAFE-001 E2E CRC / REQ-SAFE-002 计数器与重放 /
...              REQ-SAFE-003 看门狗与降级 / REQ-SAFE-004 安全状态与恢复 / REQ-SAFE-005 独立验证。
...              **独立性原则**：测试端独立重算 CRC（不复用被测实现），并用 CRC-8/SAE-J1850 的
...              公开 check value 自证实现正确，再对坏帧做 mutation 负向验证。
...              运行：.\.venv\Scripts\python.exe -m robot --outputdir results/dev_safety tests\robot\15_safety_mechanisms.robot
Library          ../../libraries/SafetyLibrary.py
Suite Setup      Start Safety Node
Suite Teardown   Stop Safety Node
Test Setup       Reset Safety Node
Test Timeout     30s

*** Variables ***
# 看门狗阈值：需求规定"心跳停止超过 300ms → SAFE_STATE"（safety_ecu.py 默认同值）
${WATCHDOG_TIMEOUT_MS}    300

*** Test Cases ***
正常 E2E 帧应被接收且状态为 NORMAL
    [Tags]    safety    e2e    REQ-SAFE-001    REQ-SAFE-002
    [Documentation]    基线用例：合法 CRC + 连续计数器的安全帧必须被节点接收（正响应路径先得对），
    ...              节点进入 NORMAL 并把转向角透传为输出。后面所有负向用例都建立在这条基线上。
    ${resp}=    Send Safety Frame    steering_angle_deg=15.0
    Should Be True    ${resp}[accepted]
    ...    msg=合法 E2E 帧被拒绝：${resp}
    Should Be Equal    ${resp}[reason]    ok
    Safety Status Should Be    NORMAL
    ${status}=    Read Safety Status
    Should Be True    ${status}[output][valid]
    Should Be Equal As Numbers    ${status}[output][steering_angle_deg]    15.0
    ...    msg=NORMAL 状态下应把收到的转向角透传为输出（status=${status}）
    Fault Flag Should Be    E2E_CRC_ERROR    False

E2E 计数器应按 0~14 循环递增
    [Tags]    safety    e2e    REQ-SAFE-002
    [Documentation]    本项目 E2E 约定计数器 0~14 循环（4bit 字段，15 视为非法值；真实项目按
    ...              ICD 为准）。连续发 16 帧，断言发送端与接收端看到的计数器逐帧 +1，
    ...              且在 14 之后回绕到 0。计数器是发现丢帧/重复/重放的基础，必须逐帧验证。
    ${seq}=    E2E Counter Should Advance    16
    Log    计数器序列（16 帧）: ${seq}
    Should Be Equal As Integers    ${seq}[0]    0
    ...    msg=复位后第一帧计数器应为 0: ${seq}
    Should Be Equal As Integers    ${seq}[14]    14
    ...    msg=第 15 帧计数器应为 14: ${seq}
    Should Be Equal As Integers    ${seq}[15]    0
    ...    msg=第 16 帧应回绕到 0（合法范围 0..14）: ${seq}

CRC 错误帧应被拒绝并计入 CRC 错误计数
    [Tags]    safety    e2e    fault_injection    REQ-SAFE-001    REQ-SAFE-005
    [Documentation]    注入一帧 CRC 字节被翻错的报文：节点必须拒绝（不得把坏数据当有效数据），
    ...              同时累加 crc_errors。**单次错误不应降级** —— 安全机制要能区分"偶发传输错误"
    ...              与"持续故障"，否则一次位翻转就把系统打到安全状态，可用性太差。
    ${resp}=    Inject E2E Error    crc
    Should Not Be True    ${resp}[accepted]
    ...    msg=CRC 错误帧不应被接收：${resp}
    Should Be Equal    ${resp}[reason]    crc_error
    E2E Error Counter Should Be    crc    1
    Safety Status Should Be    NORMAL
    Fault Flag Should Be    E2E_CRC_ERROR    False

连续 3 条 E2E 错误应进入 DEGRADED 并输出安全值
    [Tags]    safety    degrade    fault_injection    REQ-SAFE-003    REQ-SAFE-004    REQ-SAFE-005
    [Documentation]    连续 3 条 CRC 错误 → 超过错误阈值 → 降级：状态 DEGRADED、fault_level=1、
    ...              输出置"安全值"（转向角=0，执行器回正不乱动）、故障标志 E2E_CRC_ERROR 置位。
    ...              这是"检测到故障后要能反应"的核心验证（安全机制三件事：能检测/能反应/能恢复）。
    FOR    ${i}    IN RANGE    3
        ${resp}=    Inject E2E Error    crc
        Should Not Be True    ${resp}[accepted]
    END
    Safety Status Should Be    DEGRADED
    Safety Output Should Be Safe Value
    Fault Flag Should Be    E2E_CRC_ERROR    True
    E2E Error Counter Should Be    crc    3
    ${status}=    Read Safety Status
    Should Be Equal As Integers    ${status}[fault_level]    1
    ...    msg=DEGRADED 的 fault_level 应为 1：${status}

计数器跳变超阈值应立即判定丢帧并降级
    [Tags]    safety    e2e    degrade    REQ-SAFE-002    REQ-SAFE-003    REQ-SAFE-005
    [Documentation]    先发一帧正常帧建立序列基线，再注入 delta=5 的计数器跳变（丢了 4 帧）。
    ...              节点应判定 counter_jump + lost_frames=4 + 立即降级（不等连续 3 次）：
    ...              "计数器跳变超阈值"本身就是严重通信故障，必须快速进安全状态。
    ...              注意 delta=2~3 的轻微丢帧是可容忍的（只记丢失帧数），本用例验证的是"超阈值"。
    Send Safety Frame    steering_angle_deg=0.0
    ${resp}=    Inject E2E Error    counter    jump=5
    Should Not Be True    ${resp}[accepted]
    ...    msg=计数器跳变帧不应被接收：${resp}
    Should Be Equal    ${resp}[reason]    counter_jump
    Should Be Equal As Integers    ${resp}[lost_frames]    4
    ...    msg=delta=5 应判定丢失 4 帧：${resp}
    E2E Error Counter Should Be    lost    4
    E2E Error Counter Should Be    counter    1
    Safety Status Should Be    DEGRADED
    Safety Output Should Be Safe Value
    Fault Flag Should Be    E2E_COUNTER_ERROR    True

故障消失后应自动恢复 NORMAL，Clear Fault 可手动恢复
    [Tags]    safety    recovery    REQ-SAFE-004
    [Documentation]    验证两条恢复路径：
    ...              ① 自动恢复：降级后连续收到 3 条完全合法帧 → 退出 DEGRADED、清故障标志、输出恢复；
    ...              ② 手动恢复：再次降级后用 Clear Fault（模拟诊断仪 0x14 清故障）立即回 NORMAL。
    ...              只测"能降级"不测"能恢复"是功能安全测试最常见的缺口 —— 卡在安全状态的车同样不可用。
    # 路径 A：制造 DEGRADED → 连续 3 条合法帧 → 自动恢复
    FOR    ${i}    IN RANGE    3
        Inject E2E Error    crc
    END
    Safety Status Should Be    DEGRADED
    FOR    ${i}    IN RANGE    3
        ${resp}=    Send Safety Frame    steering_angle_deg=5.0
        Should Be True    ${resp}[accepted]
    END
    Safety Status Should Be    NORMAL
    Fault Flag Should Be    E2E_CRC_ERROR    False
    ${status}=    Read Safety Status
    Should Be True    ${status}[output][valid]
    Should Be Equal As Numbers    ${status}[output][steering_angle_deg]    5.0
    ...    msg=恢复后输出应恢复为最新合法值：${status}

    # 路径 B：再次降级 → Clear Fault 手动清除故障 → NORMAL
    FOR    ${i}    IN RANGE    3
        Inject E2E Error    crc
    END
    Safety Status Should Be    DEGRADED
    Clear Fault
    Safety Status Should Be    NORMAL
    Fault Flag Should Be    E2E_CRC_ERROR    False

心跳停止超过 300ms 应进入 SAFE_STATE 并上报 WATCHDOG 故障
    [Tags]    safety    watchdog    REQ-SAFE-003    REQ-SAFE-004
    [Documentation]    注入"内部任务心跳停止"（等价任务卡死不再喂狗）。看门狗应在阈值 300ms
    ...              之后触发：状态 SAFE_STATE、fault_level=2、输出无效、WATCHDOG 故障标志置位。
    ...              断言节点自报的反应时间 ≥ 阈值×0.8（太快说明判定条件写错了）。
    Inject Heartbeat Stop
    ${reaction_ms}=    Watchdog Should Trigger Degradation    timeout=2
    Safety Output Should Be Invalid
    Fault Flag Should Be    WATCHDOG    True
    ${status}=    Read Safety Status
    Should Be Equal As Integers    ${status}[fault_level]    2
    ...    msg=SAFE_STATE 的 fault_level 应为 2：${status}
    Should Be True    ${reaction_ms} >= ${WATCHDOG_TIMEOUT_MS} * 0.8
    ...    msg=看门狗反应时间 ${reaction_ms}ms 早于阈值 ${WATCHDOG_TIMEOUT_MS}ms 的 80%
    Log    看门狗反应时间: ${reaction_ms} ms（阈值 ${WATCHDOG_TIMEOUT_MS} ms）

心跳恢复后应自动退出 SAFE_STATE 回到 NORMAL
    [Tags]    safety    watchdog    recovery    REQ-SAFE-003    REQ-SAFE-004
    [Documentation]    恢复心跳（任务恢复运行）→ 看门狗应自动清除 WATCHDOG 故障并回到 NORMAL，
    ...              输出重新有效。恢复也必须是自动的：真实 ECU 卡死被复位后，系统要能自愈。
    Inject Heartbeat Stop
    Watchdog Should Trigger Degradation
    Safety Output Should Be Invalid
    Resume Heartbeat
    ${elapsed_ms}=    Safety Status Should Be    NORMAL    timeout=2
    Fault Flag Should Be    WATCHDOG    False
    ${status}=    Read Safety Status
    Should Be Equal As Integers    ${status}[fault_level]    0
    Should Be True    ${status}[output][valid]
    Log    心跳恢复后 ${elapsed_ms} ms 回到 NORMAL（fault_level=0，输出重新有效）

重复帧/重放帧应被拒绝（E2E 计数器语义）
    [Tags]    safety    e2e    replay    REQ-SAFE-002    REQ-SAFE-005
    [Documentation]    把上一帧正常报文原样重放：E2E 计数器 delta=0 → 判定重复/重放并拒绝。
    ...              为什么这也算功能安全：重放旧数据可能让执行器按过期指令动作（信息安全与
    ...              功能安全在这里交叉）。重放被拒后，下一帧正常报文仍应被接受（序列未被破坏）。
    ${first}=    Send Safety Frame    steering_angle_deg=10.0
    Should Be True    ${first}[accepted]
    ${replay}=    Inject E2E Error    replay
    Should Not Be True    ${replay}[accepted]
    ...    msg=重放帧不应被接收：${replay}
    Should Be Equal    ${replay}[reason]    replay
    E2E Error Counter Should Be    replay    1
    Safety Status Should Be    NORMAL
    ${next}=    Send Safety Frame    steering_angle_deg=12.0
    Should Be True    ${next}[accepted]
    ...    msg=重放不应破坏计数器序列，下一帧正常报文应被接受：${next}
    Should Be Equal    ${next}[reason]    ok

安全输出帧应通过测试端独立 CRC 重算（含坏帧 mutation 验证）
    [Tags]    safety    e2e    independent_verification    REQ-SAFE-001    REQ-SAFE-005
    [Documentation]    REQ-SAFE-005 的核心：安全机制不能"自己证明自己"。
    ...              ① 先用 CRC-8/SAE-J1850 公开 check value（"123456789" → 0x4B）证明测试端
    ...              独立实现本身正确；② 读取节点发出的 E2E 安全状态帧（dataId=0x5A02），
    ...              用测试端实现重算 CRC 并通过，解码字段与节点状态一致；
    ...              ③ mutation 负向验证：坏 CRC 帧必须被判失败（证明校验不是恒真）。
    Crc8 Should Be    313233343536373839    0x4B
    Send Safety Frame    steering_angle_deg=15.0
    Sleep    0.15s
    ${status}=    Read Safety Status
    ${decoded}=    E2E Crc Should Be Valid    ${status}[e2e][last_tx_frame]
    ...    expected_data_id=0x5A02
    Should Be Equal As Numbers    ${decoded}[steering_angle_deg]    15.0
    ...    msg=节点输出帧里的转向角应与内部输出一致（decoded=${decoded}）
    Should Be Equal As Integers    ${decoded}[fault_level]    0
    # mutation：把一帧 CRC 故意改坏，测试端的独立校验必须报错
    ${bad}=    Inject E2E Error    crc
    Run Keyword And Expect Error    *CRC 校验失败*
    ...    E2E Crc Should Be Valid    ${bad}[frame]
