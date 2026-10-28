*** Settings ***
Documentation    L11 练习：OTA 刷写全链路（manifest / 预编程 / 编程会话 / 分块刷写 / 版本 / 坏包）
...              目标：用 OtaLibrary 完成一次安全的 OTA 刷写并验证版本号与失败保护。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
...              提示与关键字表见 README §三；做完对照 solution.robot。
Resource         ../../resources/common.resource
Library          ../../libraries/OtaLibrary.py
Suite Setup      启动 OTA 测试环境
Suite Teardown   关闭 OTA 测试环境
Test Setup       重置 OTA 测试状态

*** Variables ***
${OLD_VERSION}    v1.0.0
${NEW_VERSION}    v1.1.0

*** Test Cases ***
OTA manifest 应包含完整元数据且镜像校验通过
    [Documentation]    TODO：${m}= Get OTA Manifest → 断言 version/previous_version/size/block_size/
    ...                compatible/rollback_supported → ${img}= Download OTA Image（长度=size×2）
    ...                → Check OTA Image Integrity。
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完

完整刷写链路应成功且版本待复位后激活
    [Documentation]    TODO：${before}= Read Software Version → ${summary}= Flash OTA Image →
    ...                断言 result=0/version/blocks → 复位前仍是 ${before} → ECU Reset →
    ...                Software Version Should Be ${NEW_VERSION}。
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

坏 CRC 包应被拒绝且版本保持
    [Documentation]    TODO：${before}= Read Software Version → Inject Bad Image crc →
    ...                Should Refuse Image → 版本仍为 ${before} →
    ...                Run Keyword And Expect Error *完整性校验失败* Force Flash Corrupted Image
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完

预编程条件不满足时应拒绝刷写
    [Documentation]    TODO：Enter Extended Session + Security Unlock → Set Test Stimulus voltage=9 →
    ...                Preprogramming Conditions Should Be Rejected → Clear Test Stimulus →
    ...                Check Preprogramming Conditions。
    Fail    TODO: 删除这一行，按 README §二 用例 4 补完

断点续传 + 丢块重传应成功
    [Documentation]    TODO：分两段 Download OTA Image（0/128B、128B 续传）→ 拼接后算 CRC32
    ...                与 manifest 比对 → Flash With Retry skip_block=2 → result=0 → ECU Reset →
    ...                版本为 ${NEW_VERSION}。
    Fail    TODO: 删除这一行，按 README §二 用例 5 补完

*** Keywords ***
启动 OTA 测试环境
    [Documentation]    Suite Setup（已写好，不用改）：先起 ECU/仪表，再起 OTA 云端 mock。
    Start Vehicle Environment
    Start OTA Server

重置 OTA 测试状态
    [Documentation]    Test Setup（已写好，不用改）：每条用例前恢复干净状态（清注入/激励/复位）。
    Run Keyword And Ignore Error    Clear OTA Injection
    Run Keyword And Ignore Error    Enter Extended Session
    Run Keyword And Ignore Error    Security Unlock
    Run Keyword And Ignore Error    Clear Test Stimulus
    Run Keyword And Ignore Error    ECU Reset
    Sleep    0.4s

关闭 OTA 测试环境
    [Documentation]    Suite Teardown（已写好，不用改）：逆序回收 —— 先停云端/诊断通道，再停 ECU。
    Run Keyword And Ignore Error    Stop OTA Server
    Run Keyword And Ignore Error    Close OTA Tester
    Stop Vehicle Environment
