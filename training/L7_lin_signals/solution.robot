*** Settings ***
Documentation    L7 参考答案：LIN 车身信号读取与校验
...              场景：LIN 调度表每 10ms 一个槽位，4 个报文轮一圈（0x10 雨量 / 0x11 雨刷 /
...              0x12 座椅 / 0x3C 主请求）。测试端设置从机信号后，从下一帧读回并做
...              "原始字节 + LDF 物理值 + 校验和"三重断言；最后验证睡眠命令/唤醒脉冲。
Library          ../../libraries/LinLibrary.py
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation

*** Variables ***
${FRAME_RAIN}      0x10    # 雨量传感器状态帧（1 字节，增强校验和）
${FRAME_MASTER}    0x3C    # 主请求帧（8 字节，经典校验和）

*** Test Cases ***
雨量信号应能编码进状态帧
    [Documentation]    Set LIN Slave Signal 让从机把雨量改成 75%；下一帧 0x10 的
    ...                 原始字节应为 150（0.5%/LSB），按 LDF 解码的物理值应为 75。
    ...                 since_seq=${marker} 保证等到的是"设置之后"的新帧，不会读到旧缓存。
    ${marker}=    Set LIN Slave Signal    ${FRAME_RAIN}    rain_intensity=75
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[data][0]    150
    ...    msg=75%% 雨量应编码为原始值 150，实际字节 ${frame}[data][0]
    LIN Frame Should Contain Signal    ${frame}    rain_intensity    75    tolerance=0.5

LIN 帧校验和应有效
    [Documentation]    0x10 从机应答帧用增强校验和（PID+数据），0x3C 主请求帧用
    ...                 经典校验和（只算数据字节，LIN 2.x 规定）—— 两种都要独立重算验证。
    ${enhanced}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2
    Should Be Equal    ${enhanced}[checksum_type]    enhanced
    ...    msg=0x10 应使用增强校验和，实际 ${enhanced}[checksum_type]
    LIN Checksum Should Be Valid    ${enhanced}
    ${classic}=    Wait For LIN Frame    ${FRAME_MASTER}    timeout=2
    Should Be Equal    ${classic}[checksum_type]    classic
    ...    msg=0x3C 主请求帧应使用经典校验和，实际 ${classic}[checksum_type]
    LIN Checksum Should Be Valid    ${classic}

睡眠后总线应静默、唤醒后应恢复
    [Documentation]    LIN 睡眠协议：主节点发 0x3C 主请求帧（首字节 0x00）→ 调度停止、
    ...                 总线静默；唤醒脉冲 → 调度恢复，下一帧 0x10 在超时内到达。
    Send LIN Master Request    00 00 00 00 00 00 00 00
    Sleep    0.2s
    LIN Bus Should Be Silent    0.3
    ${marker}=    Wake Up LIN Bus
    ${frame}=    Wait For LIN Frame    ${FRAME_RAIN}    timeout=2    since_seq=${marker}
    Should Be Equal As Integers    ${frame}[id]    16
    ...    msg=唤醒后应恢复发 0x10 雨量帧，实际收到帧 ID ${frame}[id]
