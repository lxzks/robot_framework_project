*** Settings ***
Documentation    L11 参考答案：OTA 刷写全链路（manifest / 预编程 / 编程会话 / 分块刷写 / 版本 / 坏包）
...              链路：OTA 云端(TCP 8080) → OTA Agent 下载/校验 → 预编程检查(31 01 0203)
...                    → 编程会话+解锁 → 0x34/0x36/0x37 分块刷写 → 31 01 FF00 完整性校验
...                    → 0x11 复位激活新版本（A/B 语义：复位前运行版本仍是旧的）
...              失败保护：坏包（crc/size/version）必须拒绝、版本保持；丢块重传后仍能成功。
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
    [Documentation]    manifest 必须给出：版本/旧版本/大小/分块/CRC32/目标 ECU/兼容性/回滚。
    ...                下载完整镜像后本地 CRC32 与 manifest 一致，才能上车。
    ${m}=    Get OTA Manifest
    Log    OTA manifest: ${m}
    Should Be Equal    ${m}[version]    ${NEW_VERSION}
    Should Be Equal    ${m}[previous_version]    ${OLD_VERSION}
    Should Be Equal As Integers    ${m}[size]    512
    Should Be Equal As Integers    ${m}[block_size]    64
    Should Be True    ${m}[compatible]
    Should Be True    ${m}[rollback_supported]
    ${img}=    Download OTA Image
    Length Should Be    ${img}    1024        # 512 字节 → hex 1024 字符
    Check OTA Image Integrity

完整刷写链路应成功且版本待复位后激活
    [Documentation]    完整链路：下载→包校验→预编程→编程会话→解锁→0x34→8×0x36→0x37
    ...                →31 01 FF00 结果 0。A/B 语义：成功后**未复位**，运行版本仍是旧版本。
    ${before}=    Read Software Version
    ${summary}=    Flash OTA Image
    Log    ${summary}
    Should Be Equal As Integers    ${summary}[result]    0
    Should Be Equal    ${summary}[version]    ${NEW_VERSION}
    Should Be Equal As Integers    ${summary}[blocks]    8
    Software Version Should Be    ${before}      # 复位前仍运行旧版本（不会"刷到一半变砖"）
    ECU Reset
    Software Version Should Be    ${NEW_VERSION}   # 复位 = Bootloader 切换 A/B → 新版本生效

坏 CRC 包应被拒绝且版本保持
    [Documentation]    两道防线：① OTA Agent 的 manifest CRC32 不符 → 拒绝；
    ...                ② 即使绕过强写，ECU 的 31 01 FF00 CRC32 校验也必须拒绝（NRC/断言失败）。
    ${before}=    Read Software Version
    Inject Bad Image    crc
    Should Refuse Image
    Software Version Should Be    ${before}
    Run Keyword And Expect Error    *完整性校验失败*    Force Flash Corrupted Image
    Software Version Should Be    ${before}

预编程条件不满足时应拒绝刷写
    [Documentation]    预编程检查 31 01 0203：欠压 9V 时必须 NRC 0x22；恢复后应通过。
    ...                真实意义：条件不满足硬刷 = 变砖风险。
    Enter Extended Session
    Security Unlock
    Set Test Stimulus    voltage=9
    Preprogramming Conditions Should Be Rejected
    Clear Test Stimulus
    Check Preprogramming Conditions

断点续传 + 丢块重传应成功
    [Documentation]    ① 断点续传：先下前 128B 再从 128B 续传，拼接后 CRC32 与 manifest 一致；
    ...                ② 丢块重传：刷写时跳过第 2 块 → NRC 0x73 → 重发 → 走完并校验通过。
    ${part}=    Download OTA Image    start_offset=0    length=128
    Length Should Be    ${part}    256
    ${rest}=    Download OTA Image    start_offset=128
    Length Should Be    ${rest}    768
    ${full}=    Evaluate    '${part}' + '${rest}'
    Length Should Be    ${full}    1024
    ${m}=    Get OTA Manifest
    ${crc}=    Evaluate    __import__('zlib').crc32(bytes.fromhex('${full}')) & 0xFFFFFFFF
    Should Be Equal As Integers    ${crc}    ${m}[crc32]    msg=断点续传拼接后的镜像 CRC32 不一致
    ${summary}=    Flash With Retry    skip_block=2
    Log    ${summary}
    Should Be Equal As Integers    ${summary}[result]    0
    ECU Reset
    Software Version Should Be    ${NEW_VERSION}

*** Keywords ***
启动 OTA 测试环境
    [Documentation]    Suite Setup（已写好，不用改）：先起 ECU/仪表，再起 OTA 云端 mock。
    Start Vehicle Environment
    Start OTA Server

重置 OTA 测试状态
    [Documentation]    Test Setup（已写好，不用改）：撤销坏包注入 → 扩展会话+解锁 →
    ...                清测试激励 → ECU 复位（清 flash_state/会话/安全状态，激活待激活版本）。
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
