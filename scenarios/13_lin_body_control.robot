*** Settings ***
Documentation    场景 13：车身域 LIN 联动（雨刷 / 座椅记忆 / 睡眠唤醒）
...              【业务背景】车身域控制器（BCM）通过 LIN 单线主从总线连接雨量
...              传感器、雨刷电机、座椅模块等低速执行器。真实工作里，这类验证是
...              "车身功能回归"的日常：传感器信号 → BCM 策略 → 执行器动作；
...              以及熄火后的 LIN 睡眠 / 唤醒恢复（静态电流指标）。
...              本场景复用 LinLibrary + simulators/lin_sim.py（独立 LIN 总线进程），
...              Suite Setup/Teardown 自管仿真进程；标签对应 REQ-LIN-001~005。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_scen13 scenarios/13_lin_body_control.robot
Library          ../libraries/LinLibrary.py
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation
Test Setup       恢复 LIN 总线到清醒状态

*** Variables ***
${FRAME_RAIN}       0x10    # 雨量传感器状态帧（1 字节，增强校验和）
${FRAME_WIPER}      0x11    # 雨刷电机状态帧（1 字节，增强校验和）
${FRAME_SEAT}       0x12    # 座椅位置帧（2 字节，增强校验和）
${FRAME_MASTER}     0x3C    # 主请求帧（8 字节，经典校验和）

*** Test Cases ***
车身 LIN 调度表应按 10ms 时基轮询四个报文
    [Tags]    lin    body    REQ-LIN-001
    [Documentation]    业务含义：雨量/雨刷/座椅报文必须按调度表准时刷新，否则
    ...               BCM 看到的传感器数据会"忽快忽慢"。4 个槽位 × 10ms = 40ms
    ...               周期；采样 1.5s 后统计算相邻帧间隔均值、每 ID 周期与抖动。
    Clear LIN Frame Buffer
    Sleep    1.5s
    ${stats}=    LIN Schedule Timing Should Be Within
    ...    expected_slot_ms=10
    ...    period_tolerance_ms=6
    ...    jitter_tolerance_ms=15
    Should Be True    ${stats}[frames] >= 120
    ...    msg=1.5 秒应收到约 150 帧（4 报文 × 37 圈），实际 ${stats}[frames] 帧

雨量增大时 BCM 应把雨刷从 OFF 切到 HIGH 档
    [Tags]    lin    body    REQ-LIN-002
    [Documentation]    业务链路：雨量传感器 →（BCM 策略）→ 雨刷电机。
    ...               本场景用测试脚本模拟 BCM 决策逻辑（0%=OFF，1~30% 间歇，
    ...               31~70% 低速，>70% 高速），再通过 LIN 从机信号把档位/转速
    ...               下发，最后从 0x11 帧读回——验证"编码→总线→解码"整链路。
    ...               同时断言原始字节 = wiper_state(2bit) + wiper_speed 原始值<<2，
    ...               证明位布局没被改错。
    # 第一步：无雨 → BCM 决策 OFF(0)
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=0
    ${rain}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal    ${rain}    rain_intensity    0
    ${gear}=    BCM 根据雨量决策雨刷档位    0
    Should Be Equal As Integers    ${gear}    0
    # 第二步：雨量 75% → BCM 决策 HIGH(3)，雨刷电机 3600rpm
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=75
    ${rain}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal    ${rain}    rain_intensity    75    tolerance=0.5
    ${gear}=    BCM 根据雨量决策雨刷档位    75
    Should Be Equal As Integers    ${gear}    3
    ${speed}=    Evaluate    ${gear} * 1200
    ${marker}=    Set LIN Slave Signal    ${FRAME_WIPER}
    ...    wiper_state=${gear}    wiper_speed=${speed}
    ${wiper}=    Wait For LIN Frame    ${FRAME_WIPER}    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal    ${wiper}    wiper_state    HIGH
    LIN Frame Should Contain Signal    ${wiper}    wiper_speed    ${speed}    tolerance=100
    Should Be Equal As Integers    ${wiper}[data][0]    147
    ...    msg=0x11 原始字节应为 3 | (3600/100)<<2 = 147，实际 ${wiper}[data][0]

座椅记忆位置应能保存、移动后恢复
    [Tags]    lin    body    REQ-LIN-002
    [Documentation]    业务链路：用户把座椅调到某位置 → 记忆按键保存 → 借车后
    ...               一键恢复。0x12 两字节：seat_slide 0~255mm（1mm/LSB）、
    ...               seat_backrest 0~127.5°（0.5°/LSB）。用例验证"存→动→恢复"
    ...               三个步骤后，LDF 解码出的物理值与记忆值一致。
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}
    ...    seat_slide=200    seat_backrest=45
    ${saved}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${saved}[data][0]    200
    Should Be Equal As Integers    ${saved}[data][1]    90
    ...    msg=45° 按 0.5°/LSB 应编码为 90，实际 ${saved}[data][1]
    # 用户把座椅调走
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}
    ...    seat_slide=120    seat_backrest=30
    ${moved}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal    ${moved}    seat_slide    120
    Should Be Equal As Integers    ${moved}[data][1]    60
    # 一键恢复记忆位置
    ${marker}=    Set LIN Slave Signal    ${FRAME_SEAT}
    ...    seat_slide=200    seat_backrest=45
    ${restored}=    Wait For LIN Frame    ${FRAME_SEAT}    timeout=2    since_seq=${marker}
    ${decoded}=    Decode LIN Frame By LDF    ${restored}
    Should Be Equal As Numbers    ${decoded}[seat_slide]    200
    ...    msg=恢复后 LDF 解码 seat_slide 应为 200mm，实际 ${decoded}[seat_slide]
    Should Be Equal As Numbers    ${decoded}[seat_backrest]    45
    ...    msg=恢复后 LDF 解码 seat_backrest 应为 45°，实际 ${decoded}[seat_backrest]

校验和错误的雨量帧应被识别为无效（负向）
    [Tags]    lin    negative    REQ-LIN-003
    [Documentation]    负向用例：LIN 没有 CRC 硬件重传，校验和错误必须被接收方
    ...               识别并丢弃（否则 BCM 会拿错误雨量值驱动雨刷）。
    ...               注入 1 帧 checksum 错误 → 该帧 checksum_valid=false，且测试
    ...               端的独立重算断言必须失败；随后正常帧仍应通过校验
    ...               （证明只坏了一帧，没有把总线搞死）。
    ${marker}=    Inject LIN Error    checksum    ${FRAME_RAIN}    count=1
    ${bad}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be True    not ${bad}[checksum_valid]
    ...    msg=注入 checksum 错误后帧应标记 checksum_valid=false，实际 ${bad}
    Run Keyword And Expect Error    *校验和*
    ...    LIN Checksum Should Be Valid    ${bad}
    ${normal}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    LIN Checksum Should Be Valid    ${normal}

进入睡眠后 LIN 总线应保持静默
    [Tags]    lin    sleep    REQ-LIN-004
    [Documentation]    业务含义：熄火后 BCM 发 0x3C 主请求（首字节 0x00=睡眠），
    ...               各从机停止应答 → 总线上不再有帧，静态电流才能达标。
    ...               验证方法不是"读状态位"，而是"接下来 0.5s 一帧都不许有"。
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    LIN Bus Should Be Silent    0.5
    ${stats}=    Get LIN Statistics
    Should Be True    ${stats}[sleeping]
    ...    msg=睡眠命令后仿真器应报告 sleeping=true，实际 ${stats}[sleeping]
    Should Be True    ${stats}[sleep_commands] >= 1

唤醒后调度应恢复且从机信号保持
    [Tags]    lin    sleep    REQ-LIN-004
    [Documentation]    业务含义：遥控解锁/开门会发 LIN 唤醒脉冲。唤醒后调度必须
    ...               立即恢复，且从机保持睡眠前的状态（雨量 42% 不能丢），
    ...               否则用户会看到"雨刷先狂刮一下再正常"这类现象。
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=42
    ${before}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    LIN Frame Should Contain Signal    ${before}    rain_intensity    42    tolerance=0.5
    # 进入睡眠 → 确认静默 → 唤醒
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.15s
    LIN Bus Should Be Silent    0.2
    ${marker}=    Wake Up LIN Bus
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[id]    16
    ...    msg=唤醒后应恢复发 0x10 雨量帧，实际收到 ${frame}[id]
    LIN Frame Should Contain Signal    ${frame}    rain_intensity    42    tolerance=0.5
    ${pulses}=    Get LIN Events    wakeup_pulse
    Should Not Be Empty    ${pulses}    msg=唤醒脉冲事件 wakeup_pulse 应被记录

*** Keywords ***
恢复 LIN 总线到清醒状态
    [Documentation]    用例隔离：无论上一个用例留下"睡眠"还是"错误注入"状态，
    ...               本用例开始前都先唤醒总线并清空接收缓存。
    Wake Up LIN Bus
    Clear LIN Frame Buffer

BCM 根据雨量决策雨刷档位
    [Arguments]    ${rain_intensity}
    [Documentation]    模拟 BCM 的雨刷控制策略（真实车上这段逻辑在 BCM 软件里）：
    ...               0% → OFF(0)；1~30% → 间歇(1)；31~70% → 低速(2)；>70% → 高速(3)。
    ...               输入是 0x10 帧解码出的雨量物理值（%），输出是 0x11 的档位原始值。
    IF    ${rain_intensity} <= 0
        RETURN    0
    ELSE IF    ${rain_intensity} <= 30
        RETURN    1
    ELSE IF    ${rain_intensity} <= 70
        RETURN    2
    ELSE
        RETURN    3
    END
