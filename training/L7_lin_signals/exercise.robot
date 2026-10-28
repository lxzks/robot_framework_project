*** Settings ***
Documentation    L7 练习：LIN 总线（雨量帧 / 校验和 / 睡眠唤醒）
...              目标：用 LinLibrary 设置从机信号、读回 LIN 帧、验证校验和、验证睡眠/唤醒。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
...              提示与关键字表见 README §三；做完对照 solution.robot。
Library          ../../libraries/LinLibrary.py
Suite Setup      Start LIN Simulation
Suite Teardown   Stop LIN Simulation

*** Variables ***
${FRAME_RAIN}      0x10    # 雨量传感器状态帧（1 字节，增强校验和）
${FRAME_MASTER}    0x3C    # 主请求帧（8 字节，经典校验和）

*** Test Cases ***
雨量信号应能编码进状态帧
    [Documentation]    TODO：设置 rain_intensity=75 → 等 0x10 帧（带 since_seq）→
    ...                断言原始字节 data[0]=150 → 用 LDF 断言物理值 75。
    ...                关键字：Set LIN Slave Signal / Wait For LIN Frame /
    ...                Should Be Equal As Integers / LIN Frame Should Contain Signal
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完（提示见 README §三）

LIN 帧校验和应有效
    [Documentation]    TODO：等 0x10 帧并断言 checksum_type=enhanced 后校验；
    ...                再等 0x3C 帧并断言 checksum_type=classic 后校验。
    ...                关键字：Wait For LIN Frame / LIN Checksum Should Be Valid / Should Be Equal
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

睡眠后总线应静默、唤醒后应恢复
    [Documentation]    TODO：发睡眠命令 → Sleep 等生效 → LIN Bus Should Be Silent →
    ...                Wake Up LIN Bus → 用 since_seq 等到 0x10 帧（调度恢复）。
    ...                关键字：Send LIN Master Request / LIN Bus Should Be Silent / Wake Up LIN Bus
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完
