*** Settings ***
Documentation    套件 17：OTA + Bootloader 完整刷写链路
...              模拟真实量产 OTA 的完整链路：**OTA 云端**（TCP mock）→ OTA Agent 下载/校验
...              → **预编程检查**（车速=0/电压正常）→ 编程会话 + 安全解锁 → 0x34/0x36/0x37
...              分块刷写 → 31 01 FF00 完整性校验（长度 + CRC32）→ 0x11 复位激活新版本。
...
...              重点验证"出一半出错怎么办"（比成功路径更重要）：
...                - 坏包（CRC 错 / 大小不符 / 版本不兼容）必须被拒绝，版本保持旧值
...                - 刷写中途断电/复位 → A/B 语义回滚，旧版本仍可运行
...                - 丢块 → ECU 回 NRC 0x73 → 重传后继续
...                - 断点续传：按 offset 分块下载（OTA 云端 Range 语义）
...              版本 DID：F195 应用软件版本（刷写成功+复位后更新）、F1A3 Bootloader、F186 硬件。
...
...              [Tags] 需求映射：
...                REQ-OTA-001 manifest/依赖检查      REQ-OTA-002 刷写主链路
...                REQ-OTA-003 版本管理与复位生效    REQ-OTA-004 坏包/兼容性拒绝
...                REQ-OTA-005 断点续传与回滚
Resource         ../../resources/common.resource
Library          ../../libraries/OtaLibrary.py
Suite Setup      启动 OTA 测试环境
Suite Teardown   关闭 OTA 测试环境
Test Setup       重置 OTA 测试状态

*** Test Cases ***
OTA manifest 应包含完整包元数据且镜像校验通过
    [Tags]    ota    manifest    REQ-OTA-001
    [Documentation]    云端 manifest 必须给出：版本/旧版本/大小/分块/CRC32/目标 ECU/
    ...              兼容性/回滚标志。下载完整镜像后本地 CRC32 必须与 manifest 一致
    ...              —— 这是"包能不能上车"的第一道校验。
    ${m}=    Get OTA Manifest
    Log    OTA manifest: ${m}
    Should Be Equal    ${m}[version]    v1.1.0
    Should Be Equal    ${m}[previous_version]    v1.0.0
    Should Be Equal As Integers    ${m}[size]    512
    Should Be Equal As Integers    ${m}[block_size]    64
    Should Be Equal    ${m}[target_ecu]    POWERTRAIN
    Should Be True    ${m}[compatible]
    Should Be True    ${m}[rollback_supported]
    ${img}=    Download OTA Image
    ${expected_hex_len}=    Evaluate    ${m}[size] * 2
    Length Should Be    ${img}    ${expected_hex_len}
    Check OTA Image Integrity

预编程条件检查应通过且异常条件下被拒绝
    [Tags]    ota    precheck    REQ-OTA-001
    [Documentation]    进入编程前必须先通过 31 01 0203（车速=0、电压 10.5~15.5V）。
    ...              自动化里用两条激励验证拒绝路径：
    ...                - 工程 DID F1B0 byte0 注入 9V 欠压 → NRC 0x22
    ...                - 工程 DID F1B0 byte3 注入"模拟车速 30" → NRC 0x22
    ...              （台架无法真让车跑起来，byte3 是专门为自动化加的激励位）
    Enter Extended Session
    Security Unlock
    Check Preprogramming Conditions
    Set Test Stimulus    voltage=9
    Preprogramming Conditions Should Be Rejected
    Clear Test Stimulus
    Inject Precheck Speed    30
    Preprogramming Conditions Should Be Rejected
    Inject Precheck Speed    0
    Check Preprogramming Conditions

刷写中断电或复位应回滚到旧版本
    [Tags]    ota    rollback    REQ-OTA-005    REQ-OTA-003
    [Documentation]    **A/B 回滚敏感性验证（顺序是关键）**：本用例必须在任何成功刷写
    ...              **之前**执行 —— 此时运行版本是 v1.0.0、OTA 目标是 v1.1.0。
    ...              传 2/8 块后直接复位（模拟断电）：flash_state 被清空、新版本未激活
    ...              → 版本必须仍是 v1.0.0。如果 ECU 错误地在断电后激活了目标版本，
    ...              版本会变成 v1.1.0 → 本用例失败。
    ...              （反例：若放在成功刷写之后，运行版本本来就等于 v1.1.0，断言就
    ...              无法区分"回滚成功"和"错误激活"，属于恒真断言 —— 交叉审查发现。）
    ...              随后验证 ECU 仍能通过预编程检查（没被中断刷写卡死）；完整恢复刷写
    ...              由下一条用例验证。
    ${result}=    Simulate Power Loss During Flash    2
    Log    ${result}
    Should Rollback To Version    v1.0.0
    Enter Extended Session
    Security Unlock
    Check Preprogramming Conditions

完整刷写链路应成功且版本待复位后激活
    [Tags]    ota    flash    REQ-OTA-002
    [Documentation]    完整链路：下载→包校验→预编程→编程会话→解锁→依赖检查→
    ...              0x34→8×0x36（每块 64B）→0x37→31 01 FF00 结果 0。
    ...              A/B 语义：成功后**尚未复位**，运行版本仍是旧版本（v1.0.0），
    ...              新版本登记为"待激活"——这正是"断电也不会变砖"的关键。
    ${summary}=    Flash OTA Image
    Log    ${summary}
    Should Be Equal As Integers    ${summary}[result]    0
    Should Be Equal    ${summary}[version]    v1.1.0
    Should Be Equal As Integers    ${summary}[blocks]    8
    Software Version Should Be    v1.0.0

刷写后 ECU 复位应激活新版本
    [Tags]    ota    version    REQ-OTA-003
    [Documentation]    0x11 01 复位 = Bootloader 切换 A/B 分区 → 版本 DID F195 变 v1.1.0。
    ...              这也是"OTA 生效"的验收点：只有复位后才算真正升级成功。
    ECU Reset
    Software Version Should Be    v1.1.0
    Read Bootloader Version

CRC 错包应被拒绝且版本保持旧值
    [Tags]    ota    negative    REQ-OTA-004
    [Documentation]    坏包的两道防线都要验证：
    ...              ① OTA Agent 侧：manifest CRC32 与实际镜像不符 → 拒绝刷写；
    ...              ② ECU 侧：即使绕过校验强写，31 01 FF00 的 CRC32 也必须拒绝，
    ...                 版本保持旧值（不会把坏镜像激活成运行版本）。
    Inject Bad Image    crc
    Should Refuse Image
    Software Version Should Be    v1.1.0
    Run Keyword And Expect Error    *完整性校验失败*    Force Flash Corrupted Image
    Software Version Should Be    v1.1.0

大小不符的包应被拒绝
    [Tags]    ota    negative    REQ-OTA-004
    [Documentation]    云端只给 400B 但 manifest 声明 512B → 下载不完整 → OTA Agent 拒绝。
    Inject Bad Image    size
    Should Refuse Image
    Software Version Should Be    v1.1.0

版本不兼容的包应被拒绝
    [Tags]    ota    negative    REQ-OTA-004
    [Documentation]    manifest compatible=false（例如要求 BL-9.9.9 而车端只有 BL-1.0.0）
    ...              → OTA Agent 必须在下载/刷写前拒绝（不能让不兼容包上车）。
    Inject Bad Image    version
    Should Refuse Image
    Software Version Should Be    v1.1.0

丢块后重传应成功且断点续传数据一致
    [Tags]    ota    retry    REQ-OTA-005
    [Documentation]    ① 断点续传：先下载前 128B，再从 128B 续传到结束，
    ...                 拼接后的 CRC32 必须与 manifest 一致（证明 Range 下载正确）。
    ...              ② 丢块重传：刷写时故意跳过第 2 块 → ECU 回 NRC 0x73
    ...                 wrongBlockSequenceCounter → 重发正确块 → 继续走完并校验通过。
    ${part}=    Download OTA Image    start_offset=0    length=128
    Length Should Be    ${part}    256
    ${rest}=    Download OTA Image    start_offset=128
    Length Should Be    ${rest}    768
    ${full}=    Evaluate    '${part}' + '${rest}'
    Length Should Be    ${full}    1024
    ${m}=    Get OTA Manifest
    ${crc}=    Evaluate    __import__('zlib').crc32(bytes.fromhex('${full}')) & 0xFFFFFFFF
    Should Be Equal As Integers    ${crc}    ${m}[crc32]
    ${summary}=    Flash With Retry    skip_block=2
    Log    ${summary}
    Should Be Equal As Integers    ${summary}[result]    0
    ECU Reset
    Software Version Should Be    v1.1.0

刷写后默认会话仍可读新版本 DID
    [Tags]    ota    version    REQ-OTA-003
    [Documentation]    刷写/复位后 ECU 回到默认会话；22 F195（软件版本）等读类 DID
    ...              在默认会话必须可读（售后/产线不一定要进扩展会话才能看版本）。
    Enter Default Session
    ${ver}=    Read Software Version
    Should Be Equal    ${ver}    v1.1.0
    ${bl}=    Read Bootloader Version
    Should Not Be Empty    ${bl}
    ${hw}=    Read Hardware Version
    Should Not Be Empty    ${hw}
    Log    bootloader=${bl} / hardware=${hw}

*** Keywords ***
启动 OTA 测试环境
    [Documentation]    起 ECU + headless 仪表（公共环境），再起 OTA 云端 mock。
    ...              启动顺序：车端 → 云端；Teardown 逆序回收。
    Start Vehicle Environment
    Start OTA Server
    Log    OTA 测试环境就绪（ECU 初始软件版本 v1.0.0，OTA 目标版本 v1.1.0）

重置 OTA 测试状态
    [Documentation]    用例隔离（每条用例前）：
    ...              ① 撤销云端坏包注入；② 扩展会话+解锁（为了能清测试激励）；
    ...              ③ 清空工程 DID F1B0 激励（电压/模拟车速）；④ ECU 复位。
    ...              复位会清 flash_state / 会话 / 安全状态，并激活上一条用例
    ...              刷写成功后"待激活"的版本（如果有）。
    Run Keyword And Ignore Error    Clear OTA Injection
    Run Keyword And Ignore Error    Enter Extended Session
    Run Keyword And Ignore Error    Security Unlock
    Run Keyword And Ignore Error    Clear Test Stimulus
    Run Keyword And Ignore Error    ECU Reset
    Sleep    0.4s

关闭 OTA 测试环境
    [Documentation]    逆序回收：先停云端与 OTA 诊断通道，再杀 ECU/仪表进程。
    Run Keyword And Ignore Error    Stop OTA Server
    Run Keyword And Ignore Error    Close OTA Tester
    Stop Vehicle Environment
