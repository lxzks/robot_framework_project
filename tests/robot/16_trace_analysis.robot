*** Settings ***
Documentation    套件 16：Trace 分析（ASC/BLF 解析 + 总线负载 + 周期抖动 + 异常检测）
...              业务背景：测试失败后，工程师 70% 的时间在**分析 Trace**——
...              "当时总线上到底发生了什么？谁丢帧了？负载正常吗？"
...              本套件验证 Trace 分析模块：真实录制一段总线 → 导出 ASC/BLF →
...              解析统计（帧数/ID/负载/周期/抖动）→ 异常检测（丢帧/未知 ID）→
...              导出 HTML/CSV 分析报告；最后用负向用例证明异常检测不是恒真。
...              被测链路：ecu_sim.py（周期报文）→ VehicleBusLibrary 录制 →
...              trace_tools 导出 → asc_analyzer 分析 → TraceAnalysisLibrary 断言。
...              设计要点：① Suite Setup 只录一次、导出两种格式（ASC/BLF 交叉验证）
...              ② 周期/抖动容差按"接收方超时阈值"倒推，不写死理想值
...              ③ 异常检测用**合成夹具**（已知缺帧/未知 ID 的文件）做负向验证
...              ④ 空文件/坏格式必须给可读报错。
...              需求追溯：REQ-TRACE-001 ASC/BLF Trace 解析 / REQ-TRACE-002 总线负载计算 /
...              REQ-TRACE-003 周期抖动与异常检测。
Resource         ../../resources/common.resource
Library          ../../libraries/TraceAnalysisLibrary.py
Library          OperatingSystem
Suite Setup      启动环境并录制导出 Trace
Suite Teardown   Stop Vehicle Environment

*** Variables ***
# 分析产物统一放 results/dev_trace/（.gitignore 已排除 results/）
${TRACE_OUT}       ${CURDIR}/../../results/dev_trace
${ASC_TRACE}       ${TRACE_OUT}/bus_recording.asc
${BLF_TRACE}       ${TRACE_OUT}/bus_recording.blf
${MISSING_TRACE}   ${TRACE_OUT}/synthetic_missing_frame.asc
${UNKNOWN_TRACE}   ${TRACE_OUT}/synthetic_unknown_id.asc
${EMPTY_TRACE}     ${TRACE_OUT}/bad_empty.asc
${GARBAGE_TRACE}   ${TRACE_OUT}/bad_garbage.asc

*** Test Cases ***
项目导出的 ASC 应能解析且帧数时长合理
    [Tags]    REQ-TRACE-001
    [Documentation]    验证"录制→导出→分析"链路：Suite Setup 录 2 秒并导出 ASC，
    ...              这里解析它并断言 ①格式识别为 asc ②帧数够多（2 秒至少 20 帧）
    ...              ③时间跨度接近 2 秒（±0.6s，首末帧不落在录制边界上）。
    ${report}=    Analyze Trace File    ${ASC_TRACE}
    Should Be Equal    ${report}[format]    asc
    Trace Frame Count Should Be    ${ASC_TRACE}    minimum=20
    Trace Time Range Should Be    ${ASC_TRACE}    expected_seconds=2.0
    ...    tolerance_seconds=0.6

ASC 应包含三条周期报文
    [Tags]    REQ-TRACE-001
    [Documentation]    0x100（100ms）/0x200（200ms）/0x300（500ms）三条应用报文
    ...              必须都能在 Trace 里找到；帧数下限按各自周期推算（录制 2 秒：
    ...              0x100 约 20 帧、0x200 约 10 帧、0x300 约 4 帧）。
    Trace Should Contain ID    ${ASC_TRACE}    0x100    min_frames=5
    Trace Should Contain ID    ${ASC_TRACE}    0x200    min_frames=3
    Trace Should Contain ID    ${ASC_TRACE}    0x300    min_frames=2

总线负载应在合理范围
    [Tags]    REQ-TRACE-002
    [Documentation]    负载口径：Σ(每帧位数×(47+8×DLC)×1.2) / (比特率×时长)。
    ...              教学环境只有 3 条应用报文（500kbps 下理论约 0.5%），
    ...              断言 >0 证明计算不是恒零、<5% 证明远未饱和（真实项目门禁常设 30%）。
    ${load}=    Get Trace Bus Load    ${ASC_TRACE}
    Should Be True    0 < ${load} < 50    msg=总线负载 ${load}% 不合理
    Bus Load Should Be Below    ${ASC_TRACE}    max_percent=50
    Should Be True    ${load} < 5    msg=教学仿真环境总线负载应远低于 5%（实测 ${load}%）

0x100 周期与抖动应满足设计值
    [Tags]    REQ-TRACE-003
    [Documentation]    周期是设计指标：0x100 应 100ms、0x200 应 200ms。
    ...              Period Should Be Near 断言周期均值（容差 ±25ms，按接收方超时倒推）；
    ...              Jitter Should Be Within 断言最大间隔-最小间隔（抖动大 = 时序不稳，
    ...              比"均值正常"更能暴露调度问题）。
    ID Stats Should Report    ${ASC_TRACE}    0x100    min_frames=10
    Period Should Be Near    ${ASC_TRACE}    0x100    expected_ms=100
    ...    tolerance_ms=25
    Jitter Should Be Within    ${ASC_TRACE}    0x100    max_jitter_ms=100
    Period Should Be Near    ${ASC_TRACE}    0x200    expected_ms=200
    ...    tolerance_ms=50

缺帧的 Trace 应被检测为丢帧异常
    [Tags]    REQ-TRACE-003
    [Documentation]    负向/灵敏度用例：手工构造一份"0x100 在 0.2s→0.4s 之间缺了一帧"
    ...              的合成 ASC（期望周期 100ms，实际出现 200ms 间隔）。
    ...              异常检测必须报 missing_frame 且估算丢 1 帧 —— 证明检测算法
    ...              真的在算间隔，不是永远返回"无异常"。
    ${frames}=    Evaluate    [[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"], [0.2, 0x100, "1122334455667788"], [0.4, 0x100, "1122334455667788"], [0.5, 0x100, "1122334455667788"]]
    Write Synthetic Trace    ${MISSING_TRACE}    ${frames}
    ${anomalies}=    Anomalies Should Be Detected    ${MISSING_TRACE}
    ...    anomaly_type=missing_frame    arbitration_id=0x100
    Anomaly Count Should Be    ${MISSING_TRACE}    expected=1
    ...    anomaly_type=missing_frame
    ${gap}=    Set Variable    ${anomalies}[0][actual_gap_ms]
    Should Be True    ${gap} > 150
    ...    msg=丢帧间隔应约 200ms，实际 ${gap}ms
    ${missing}=    Set Variable    ${anomalies}[0][missing_estimated]
    Should Be Equal As Integers    ${missing}    1

未知 ID 应被检测出来
    [Tags]    REQ-TRACE-003
    [Documentation]    负向用例：合成 Trace 里混入 0x123（不在期望周期清单
    ...              {0x100:100ms, 0x200:200ms, 0x300:500ms} 内），
    ...              异常检测必须报 unknown_id —— 对应真实场景"新节点上线/报文配错/
    ...              诊断报文混入应用 Trace"。
    ${frames}=    Evaluate    [[0.0, 0x100, "1122334455667788"], [0.1, 0x100, "1122334455667788"], [0.2, 0x100, "1122334455667788"], [0.05, 0x123, "AABB"]]
    Write Synthetic Trace    ${UNKNOWN_TRACE}    ${frames}
    ${anomalies}=    Anomalies Should Be Detected    ${UNKNOWN_TRACE}
    ...    anomaly_type=unknown_id
    Anomaly Count Should Be    ${UNKNOWN_TRACE}    expected=1
    ...    anomaly_type=unknown_id
    ${id_hex}=    Set Variable    ${anomalies}[0][id_hex]
    Should Be Equal    ${id_hex}    0x123

ASC 与 BLF 两种格式应解析出一致结果
    [Tags]    REQ-TRACE-001
    [Documentation]    同一份录制分别导出 ASC 与 BLF：
    ...              ①格式自动识别正确（asc / blf）②帧数完全相同
    ...              ③Compare Two Traces 断言 ID 集合、帧数、各 ID 周期均值一致。
    ...              真实价值：ASC 给人看、BLF 给工具跑，两种格式解析结果必须等价，
    ...              否则"分析结论取决于用哪种格式"就是工具链 bug。
    ${asc_report}=    Analyze Trace File    ${ASC_TRACE}
    ${blf_report}=    Analyze Trace File    ${BLF_TRACE}
    Should Be Equal    ${blf_report}[format]    blf
    Should Be Equal As Integers    ${asc_report}[frames]    ${blf_report}[frames]
    Compare Two Traces    ${ASC_TRACE}    ${BLF_TRACE}
    Trace Should Contain ID    ${BLF_TRACE}    0x100    min_frames=5

分析报告应能导出 HTML 与 CSV 且内容非空
    [Tags]    REQ-TRACE-002
    [Documentation]    失败现场证据包 = Trace + 分析报告。这里验证 HTML（含"总线负载"/
    ...              "异常检测"表格，可直接贴缺陷单）与 CSV（Excel 可开，含 0x100 行）
    ...              都能生成且内容非空。
    ${html}=    Export Analysis Report    ${ASC_TRACE}    ${TRACE_OUT}/trace_report.html
    ${csv}=    Export Analysis Report    ${ASC_TRACE}    ${TRACE_OUT}/trace_report.csv
    File Should Exist    ${html}
    File Should Exist    ${csv}
    ${html_content}=    Get File    ${html}
    Should Contain    ${html_content}    总线负载
    Should Contain    ${html_content}    异常检测
    ${csv_content}=    Get File    ${csv}
    Should Contain    ${csv_content}    0x100
    Should Contain    ${csv_content}    周期均值
    ${html_size}=    Get File Size    ${html}
    Should Be True    ${html_size} > 1000
    ...    msg=HTML 报告太小（${html_size} 字节），可能没内容

空文件或坏格式应给出清晰报错
    [Tags]    REQ-TRACE-001
    [Documentation]    文件解析健壮性：空文件、非 Trace 文本都不能抛 Python 堆栈，
    ...              必须报"没有解析到任何数据帧（空文件/格式不支持/内容损坏）"，
    ...              用例用 Run Keyword And Expect Error 锁死错误信息关键词。
    Create File    ${EMPTY_TRACE}
    Run Keyword And Expect Error    *没有解析到任何数据帧*
    ...    Analyze Trace File    ${EMPTY_TRACE}
    Create File    ${GARBAGE_TRACE}    this is not a can trace file\n123 456\n
    Run Keyword And Expect Error    *没有解析到任何数据帧*
    ...    Analyze Trace File    ${GARBAGE_TRACE}

*** Keywords ***
启动环境并录制导出 Trace
    [Documentation]    在 Suite Setup 完成"造现场"：起 ECU/仪表 → 录制 2 秒 →
    ...              同一份录制导出 ASC（可读文本）与 BLF（二进制）两种格式。
    ...              各用例只负责分析，避免用例之间抢着录制造成顺序依赖。
    Create Directory    ${TRACE_OUT}
    Start Vehicle Environment    speed=60
    Start Bus Recorder
    Drain Bus For    2
    Stop Bus Recorder
    ${count}=    Export Bus Trace    ${ASC_TRACE}
    Should Be True    ${count} > 10
    ...    msg=录制帧数太少（${count}）——检查 ECU 是否在发周期报文
    Export Bus Trace    ${BLF_TRACE}
    Log    已录制 ${count} 帧 → ${ASC_TRACE} / ${BLF_TRACE}


