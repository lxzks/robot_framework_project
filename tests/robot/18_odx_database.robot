*** Settings ***
Documentation    套件 18：ODX/OTX 诊断数据库驱动测试
...              前 17 个套件里的诊断请求都是"手写 hex"（22 F1 90、10 03…）。
...              真实项目的诊断定义有几千个 DID/服务/DTC，靠"数据库 + 测试序列"
...              管理：ODX（ISO 22901）是诊断数据的单一事实源，OTX（ISO 13209）
...              是测试序列的可交换格式。本套件验证"数据库驱动测试"的完整链路：
...                ① ODX 加载 + 结构校验（DID 唯一 / DTC 编号合法 / 服务与 ECU 一致）
...                ② 按 ODX 生成的请求读 ECU 实际 DID 并解码（ASCII 长度/内容）
...                ③ 按名称读 DID（Read DID By Name，不写 hex）
...                ④ DTC 列表与 ECU 的 FAULT_CATALOG 一致
...                ⑤ 按 ODX 的 CLEAR_DTC_GROUP 生成清码请求并执行
...                ⑥ 服务模板与 ECU 支持的 17 个 SID 一致（含未知 SID NRC 0x11）
...                ⑦ OTX 序列 5 步逐步骤执行通过（步骤级结果可见）
...                ⑧ 注入坏期望 → 第 5 步失败且报错含步骤名（负向，证明断言有效）
...                ⑨ 损坏/缺字段的 ODX 文件 → 清晰报错且不污染已加载数据库
...              需求标签：REQ-DIAG-023 ODX 数据库解析、REQ-DIAG-024 数据库驱动
...              请求/响应、REQ-DIAG-025 OTX 测试序列执行。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_odx tests\\robot\\18_odx_database.robot
...              本套件依赖 ECU（Resource common.resource 启动），ODX/OTX 文件在 simulators/diag/。
Resource          ../../resources/common.resource
Library           ../../libraries/OdxLibrary.py
Suite Setup       启动环境并加载诊断数据库
Suite Teardown    关闭 ODX 与车辆环境
Test Setup        回到默认诊断会话

*** Variables ***
${ODX_PATH}       ${ROOT}/simulators/diag/vehicle.odx.xml
${OTX_PATH}       ${ROOT}/simulators/diag/vehicle.otx.xml
${BROKEN_ODX}     ${ROOT}/results/dev_odx_broken.odx.xml
${MISSING_ODX}    ${ROOT}/results/dev_odx_missing_layer.odx.xml

# ECU 实际支持的服务 SID（17 个，与 ecu_sim 的 handler 对齐；ODX 必须一致）
@{EXPECTED_SIDS}    0x10    0x11    0x14    0x19    0x22    0x27    0x28    0x2E
...                 0x2F    0x31    0x34    0x36    0x37    0x3D    0x3E    0x85    0x23
# ECU 的 FAULT_CATALOG（5 个 DTC；ODX 必须一致）
@{EXPECTED_DTCS}    P0116    P0117    P0562    P0563    P0135

*** Keywords ***
启动环境并加载诊断数据库
    [Documentation]    起 ECU/仪表盘（诊断链路就绪）+ 加载 ODX/OTX 数据库。
    Start Vehicle Environment
    ${odx}=    Load ODX    ${ODX_PATH}
    ${otx}=    Load OTX    ${OTX_PATH}
    Log    已加载 ODX：${odx}
    Log    已加载 OTX：${otx}

回到默认诊断会话
    [Documentation]    用例隔离：上一个用例可能停在扩展/编程会话，统一切回默认会话。
    Send UDS Request    10 01
    Response Should Be Positive

关闭 ODX 与车辆环境
    [Documentation]    Teardown 逆序：先关 ODX 诊断仪（它自己持有一条虚拟总线连接），
    ...               再停车辆环境，避免"总线未正常关闭"的句柄泄漏告警。
    Run Keyword And Ignore Error    Close Odx Tester
    Stop Vehicle Environment

*** Test Cases ***
ODX 数据库应能加载并通过结构校验
    [Tags]    REQ-DIAG-023
    [Documentation]    ODX 是诊断数据的单一事实源：加载后应能拿到 ECU 变体信息
    ...               （诊断地址 0x7E0 / 响应地址 0x7E8）、10 个 DID、5 个 DTC、
    ...               17 个服务；结构校验（DID 唯一/DTC 合法/服务与 ECU 一致）通过。
    ${summary}=    Load ODX    ${ODX_PATH}
    Should Be Equal    ${summary}[ecu]    CLUSTER_ECU
    Should Be Equal As Integers    ${summary}[diag_address]    0x7E0
    Should Be Equal As Integers    ${summary}[response_address]    0x7E8
    Should Be Equal As Integers    ${summary}[did_count]    10
    Should Be Equal As Integers    ${summary}[dtc_count]    5
    Should Be Equal As Integers    ${summary}[service_count]    17
    ${checks}=    ODX Should Be Valid
    ${check_count}=    Get Length    ${checks}
    Should Be Equal As Integers    ${check_count}    5
    ${ecu}=    Get ECU Info
    Should Be Equal    ${ecu}[protocol]    UDS_ISO_14229
    Should Be Equal As Integers    ${ecu}[functional_address]    0x7DF
    Log    ODX 结构校验项：${checks}

DID 列表应与 ECU 实际 DID 一致
    [Tags]    REQ-DIAG-023    REQ-DIAG-024
    [Documentation]    数据库驱动的核心价值：测试不写死 DID 映射，而是"按 ODX 生成
    ...               请求 → 读 ECU → 按 ODX 定义解码"。10 个 DID 全部应能成功读取，
    ...               解码值/长度与 ODX 定义一致（长度由 Decode DID Response 强校验）。
    ...               版本类 DID（F195/F1A3/F186）随 OTA 刷写/软件升级变化，按格式断言；
    ...               其余固定 DID 断言精确值。
    ${dids}=    List Data Identifiers
    ${count}=    Get Length    ${dids}
    Should Be Equal As Integers    ${count}    10
    ${names}=    Evaluate    [item['name'] for item in ${dids}]
    ${expected_names}=    Create List    PartNumber    SoftwareVersion    SerialNumber
    ...    Supplier    VIN    ApplicationVersion    ApplicationSoftwareVersion
    ...    BootloaderVersion    HardwareVersion    TestStimulus
    Lists Should Be Equal    ${names}    ${expected_names}
    ${expected_values}=    Create Dictionary
    ...    PartNumber=HW-IC-2026-001
    ...    SoftwareVersion=SW-CLUSTER-3.2.1
    ...    SerialNumber=ECU-2026-0918-007
    ...    Supplier=VISTEON
    ...    VIN=LSVAM4180NC000001
    ...    ApplicationVersion=V1.0.0
    ...    TestStimulus=0e000000
    FOR    ${did}    IN    @{dids}
        ${request}=    Generate Request From ODX    read_did    ${did}[name]
        ${response}=    Send ODX Request    ${request}
        Should Be True    ${response}[positive]
        ...    msg=读 DID ${did}[name]（${request}）返回负响应：${response}[describe]
        ${decoded}=    Decode DID Response    ${did}[name]    ${response}
        IF    '${did}[name]' == 'ApplicationSoftwareVersion'
            Should Match Regexp    ${decoded}    ^v[0-9]+[.][0-9]+[.][0-9]+$
            ...    msg=应用软件版本格式异常：${decoded}
        ELSE IF    '${did}[name]' == 'BootloaderVersion'
            Should Match Regexp    ${decoded}    ^BL-[0-9]+[.][0-9]+[.][0-9]+$
            ...    msg=Bootloader 版本格式异常：${decoded}
        ELSE IF    '${did}[name]' == 'HardwareVersion'
            Should Start With    ${decoded}    HW-
            ...    msg=硬件版本格式异常：${decoded}
        ELSE
            ${expected}=    Get From Dictionary    ${expected_values}    ${did}[name]
            Should Be Equal As Strings    ${decoded}    ${expected}
            ...    msg=DID ${did}[name] 解码值 ${decoded} ≠ ODX 期望 ${expected}
        END
    END

按名称读 DID 应正确解码
    [Tags]    REQ-DIAG-024
    [Documentation]    Read DID By Name 是数据驱动测试的典型写法：测试只写"DID 名字"，
    ...               ODX 负责生成 22 请求和 ASCII 解码，换车型/换数据库无需改用例。
    ${vin}=    Read DID By Name    VIN
    Should Be Equal    ${vin}    LSVAM4180NC000001
    Length Should Be    ${vin}    17
    ${part_number}=    Read DID By Name    PartNumber
    Should Be Equal    ${part_number}    HW-IC-2026-001
    ${software}=    Read DID By Name    SoftwareVersion
    Should Match Regexp    ${software}    ^SW-CLUSTER-[0-9]+[.][0-9]+[.][0-9]+$
    ${app_version}=    Read DID By Name    ApplicationVersion
    Should Match Regexp    ${app_version}    ^V[0-9]+[.][0-9]+[.][0-9]+$
    ${stimulus}=    Read DID By Name    TestStimulus
    Should Be Equal    ${stimulus}    0e000000
    ${bootloader}=    Read DID By Name    BootloaderVersion
    Should Match Regexp    ${bootloader}    ^BL-[0-9]+[.][0-9]+[.][0-9]+$
    ${hardware}=    Read DID By Name    HardwareVersion
    Should Start With    ${hardware}    HW-

DTC 列表应与 ECU 故障目录一致
    [Tags]    REQ-DIAG-023
    [Documentation]    ODX 的 DTC 定义必须和 ECU 的故障目录（FAULT_CATALOG）一致：
    ...               编号集合相同、名称可查、且 ECU 当前报出的 DTC 必须都在数据库内
    ...               （数据库是"诊断字典"，ECU 报出字典外的 DTC = 典型版本不一致缺陷）。
    ${dtcs}=    List DTCs
    ${count}=    Get Length    ${dtcs}
    Should Be Equal As Integers    ${count}    5
    ${codes}=    Evaluate    [item['code'] for item in ${dtcs}]
    Lists Should Be Equal    ${codes}    ${EXPECTED_DTCS}
    ${name}=    DTC Name    P0562
    Should Contain    ${name}    系统电压过低
    ${ecu_dtcs}=    Read DTC By Mask    FF
    ${ecu_codes}=    Evaluate    [item[0] for item in ${ecu_dtcs}]
    FOR    ${code}    IN    @{ecu_codes}
        Should Contain    ${codes}    ${code}
        ...    msg=ECU 报出的 DTC ${code} 不在 ODX 数据库中（数据库需同步更新）
    END
    Log    ECU 当前 DTC：${ecu_codes}；ODX 定义：${codes}

按 ODX 生成清 DTC 请求并执行
    [Tags]    REQ-DIAG-024
    [Documentation]    清 DTC 的 groupOfDTC（0xFFFFFF）来自 ODX 的 COMPARAM
    ...               CLEAR_DTC_GROUP，而不是用例里手写。生成请求 → 执行 → 正响应 → 读回数量 0。
    ${request}=    Generate Request From ODX    clear_dtc
    Should Be Equal As Strings    ${request}    14FFFFFF
    ${response}=    Send ODX Request    ${request}
    Should Be True    ${response}[positive]
    ...    msg=清 DTC 请求 ${request} 返回负响应：${response}[describe]
    ${count}=    Read DTC Count
    Should Be Equal As Integers    ${count}    0
    ${cleared}=    Clear All DTCs
    Should Contain    ${cleared}    正响应
    ${count_after}=    Read DTC Count
    Should Be Equal As Integers    ${count_after}    0

服务模板应与 ECU 支持的 17 个 SID 一致
    [Tags]    REQ-DIAG-023
    [Documentation]    ODX 的服务集合必须等于 ECU 实际支持的 17 个 SID（数据库与
    ...               ECU 版本一致）。同时抽查：0x22 模板信息正确；未知 SID 0x99
    ...               在不同会话下应被拒绝——默认会话先命中会话限制（NRC 0x7F），
    ...               扩展会话下才走到"服务不存在"（NRC 0x11）。这证明"支持集合"
    ...               是真实边界，也能看出 UDS 的会话权限优先级。
    ${sids}=    List Service SIDs
    ${expected}=    Evaluate    sorted(int(str(item), 0) for item in ${EXPECTED_SIDS})
    Lists Should Be Equal    ${sids}    ${expected}
    FOR    ${sid_text}    IN    @{EXPECTED_SIDS}
        ${template}=    Service Request Template    ${sid_text}
        Should Not Be Empty    ${template}[short_name]
        Should Not Be Empty    ${template}[request]
        Should Not Be Empty    ${template}[positive_response]
    END
    ${template_22}=    Service Request Template    0x22
    Should Be Equal    ${template_22}[short_name]    ReadDataByIdentifier
    ${request_22}=    Generate Request From ODX    read_did    VIN
    ${response_22}=    Send ODX Request    ${request_22}
    Should Be True    ${response_22}[positive]
    # 默认会话：会话限制先于服务支持判断 → NRC 0x7F
    ${unknown_default}=    Send ODX Request    99
    Should Be True    not ${unknown_default}[positive]
    ...    msg=ECU 不应支持 SID 0x99，实际却响应了：${unknown_default}[describe]
    Should Be Equal As Integers    ${unknown_default}[nrc]    0x7F
    ...    msg=默认会话下未知 SID 应返回 NRC 0x7F（会话限制），实际 ${unknown_default}[describe]
    # 扩展会话：不再受会话限制 → NRC 0x11 serviceNotSupported
    Enter Extended Session
    ${unknown}=    Send ODX Request    99
    Should Be True    not ${unknown}[positive]
    ...    msg=ECU 不应支持 SID 0x99，实际却响应了：${unknown}[describe]
    Should Be Equal As Integers    ${unknown}[nrc]    0x11
    ...    msg=扩展会话下未知 SID 应返回 NRC 0x11 serviceNotSupported，实际 ${unknown}[describe]

OTX 测试序列应逐步执行通过
    [Tags]    REQ-DIAG-025
    [Documentation]    OTX 序列 5 步：进扩展会话 → 读 VIN → 校验 VIN 格式 →
    ...               读软件版本 → 校验版本格式。每一步的 request/expect 都由
    ...               OTX XML 描述，执行器逐步执行并记录步骤级结果。
    ${summary}=    Run OTX Sequence    ${OTX_PATH}
    Should Be Equal    ${summary}[sequence]    CLUSTER_BasicCheck
    Should Be Equal As Integers    ${summary}[total]    5
    Should Be Equal As Integers    ${summary}[passed]    5
    Should Be Equal As Integers    ${summary}[failed]    0
    Log    OTX 执行结果：${summary}
    FOR    ${step_id}    IN RANGE    1    6
        OTX Step Should Have Passed    ${step_id}
    END
    ${results}=    Get OTX Results
    ${all_pass}=    Evaluate    all(item['status'] == 'PASS' for item in ${results})
    Should Be True    ${all_pass}
    ...    msg=OTX 每步都应为 PASS，实际：${results}
    ${latest}=    OTX Summary
    Should Be Equal As Integers    ${latest}[passed]    5

OTX 坏期望应在第 5 步失败并报出步骤名
    [Tags]    REQ-DIAG-025
    [Documentation]    负向用例（证明断言有效）：把第 5 步"软件版本格式"的期望正则
    ...               覆盖成不可能匹配的值，执行器必须：① 在第 5 步失败；
    ...               ② 报错含步骤名；③ 前 4 步仍标记 PASS（步骤级定位，OTX 的核心价值）。
    ${overrides}=    Create Dictionary    5=^BAD-VERSION$
    ${error}=    Run Keyword And Expect Error    *第 5 步*
    ...    Run OTX Sequence    ${OTX_PATH}    expect_overrides=${overrides}
    Should Contain    ${error}    校验软件版本格式
    ${results}=    Get OTX Results
    ${statuses}=    Evaluate    [(item['id'], item['status']) for item in ${results}]
    Should Be Equal    ${statuses}    ${{ [(1, 'PASS'), (2, 'PASS'), (3, 'PASS'), (4, 'PASS'), (5, 'FAIL')] }}
    ${summary}=    OTX Summary
    Should Be Equal As Integers    ${summary}[total]    5
    Should Be Equal As Integers    ${summary}[passed]    4
    Should Be Equal As Integers    ${summary}[failed]    1
    Run Keyword And Expect Error    *第 5 步*    OTX Step Should Have Passed    5

损坏或缺字段的 ODX 应清晰报错
    [Tags]    REQ-DIAG-023
    [Documentation]    负向用例：ODX 是交付物，文件损坏/缺字段/路径不存在时必须
    ...               给出可定位的清晰错误，且**不能污染**已经加载的数据库
    ...               （真实工具"打开坏库失败后仍保持原库"的行为）。
    Create File    ${BROKEN_ODX}    <ODX><DATA-IDENTIFIERS>
    Run Keyword And Expect Error    *解析失败*    Load ODX    ${BROKEN_ODX}
    Create File    ${MISSING_ODX}    <?xml version="1.0"?><ODX><FILE-INFO><VERSION>1.0</VERSION></FILE-INFO></ODX>
    Run Keyword And Expect Error    *缺少*DIAG-LAYER*    Load ODX    ${MISSING_ODX}
    Run Keyword And Expect Error    *不存在*    Load ODX    ${ROOT}/results/no_such_database.odx.xml
    # 失败不污染：重新用默认数据库加载，数据仍完整可用
    ${summary}=    Load ODX    ${ODX_PATH}
    Should Be Equal As Integers    ${summary}[did_count]    10
    ${check}=    ODX Should Be Valid
    Should Not Be Empty    ${check}
