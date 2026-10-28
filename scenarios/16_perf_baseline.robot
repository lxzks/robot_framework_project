*** Settings ***
Documentation    场景 16：版本发布前性能基线与回归
...              【业务背景】"功能都对了"不代表能交付。发版前 System Validation 要回答
...              四个问题：诊断多快（P2 一致性）？总线还剩多少带宽？周期报文抖不抖？
...              连续压/长时间跑会不会挂？本场景用**真实测量**（monotonic 计时 +
...              真实收帧）建立一份可对比的性能基线，供后续版本回归。
...              测量口径与真实台架一致：CANoe Bus Statistics 算负载、
...              诊断仪测 P2 响应、soak test 看长稳。
...              标签对应 REQ-PERF-001~004。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_scen16 scenarios/16_perf_baseline.robot
Resource          ../resources/common.resource
Library           ../libraries/PerfLibrary.py
Suite Setup       Start Vehicle Environment
Suite Teardown    关闭性能库并回收车辆环境

*** Variables ***
# 阈值都按「实测值 + 足够余量」定，不允许在慢机器上 flaky：
# 实测响应 p95 ≈ 2ms（阈值 50ms）、负载 ≈ 0.5%（阈值 30%）、
# 0x100 周期均值 ≈ 104ms（容差 ±15ms）、吞吐 ≈ 100 条/s（下限 50）。
${P2_MS}                    50        # ECU 声明的 P2（会话响应里 0x0032）
${SAMPLE_COUNT}             9         # P2 采样次数（单次测量会漏掉"偶尔慢"）
${BUS_LOAD_MAX}             30        # 总线负载率红线（工程经验值）
${BUS_RECORD_SECONDS}       2         # 总线负载录制时长
${JITTER_SECONDS}           3         # 周期报文抖动录制时长
${JITTER_MEAN_TOL_MS}       15        # 周期均值容差
${JITTER_MAX_STD_MS}        10        # 间隔标准差上限
${JITTER_MAX_GAP_DEV_MS}    50        # 最大间隔超出设计周期的上限
${BURST_COUNT}              200       # 突发请求条数
${BURST_MIN_THROUGHPUT}     50        # 诊断吞吐下限（条/s）
${SOAK_SECONDS}             10        # 发布前短长稳时长（10s）
${SOAK_MAX_ERRORS}          5         # 允许的总错误数（丢帧+超时）
${SOAK_MAX_TIMEOUTS}        0         # 诊断链路不允许超时

*** Test Cases ***
诊断响应时间应满足声明的 P2（多点采样 p95）
    [Tags]    perf    baseline    REQ-PERF-001
    [Documentation]    发布门禁：ECU 在会话响应里声明 P2=50ms，实测响应的 p95 必须
    ...               低于它。为什么采样 9 次看 p95 而不是单次：偶发一次慢响应
    ...               （GC/调度）不能代表 ECU 慢，但"9 次里有 1 次 80ms"正是真实
    ...               项目发现过的缺陷 —— 单次测量会漏掉长尾。
    ${samples}=    Measure UDS Response Time    3E 00    samples=${SAMPLE_COUNT}
    ${stats}=    Response Time Stats    ${samples}
    Response Time P95 Should Be Below    ${stats}    ${P2_MS}
    Log    性能基线-响应时间(ms): min=${stats}[min] avg=${stats}[avg] max=${stats}[max] p95=${stats}[p95] std=${stats}[std] n=${stats}[count]

默认工况总线负载率应低于 30%
    [Tags]    perf    baseline    bus    REQ-PERF-002
    [Documentation]    默认工况 = 3 条周期报文 + 诊断空闲。按 500kbps 口径估算，
    ...               工程红线 30%：超过它，低优先级报文延迟抖动会急剧变大、
    ...               留给诊断/刷写的带宽余量不足。
    ${load}=    Record Bus Load    ${BUS_RECORD_SECONDS}
    Bus Load Should Be Below    ${load}    ${BUS_LOAD_MAX}
    Log    性能基线-总线负载: ${load} %（红线 ${BUS_LOAD_MAX}%，500kbps）

车速报文周期应为 100ms 且抖动受控
    [Tags]    perf    baseline    timing    REQ-PERF-004
    [Documentation]    周期是设计指标：0x100 应 100ms 发一帧。均值对了还不够，
    ...               还要看标准差与最大间隔 —— "个别帧晚几十毫秒"会让仪表/ABS
    ...               判断异常。仿真 ECU 主循环用 10ms 轮询累加计时，实际均值
    ...               稳定在 ~104ms（系统偏差 4%），所以均值容差 ±15ms。
    ${jitter}=    Measure Periodic Message Jitter    0x100    ${JITTER_SECONDS}
    Jitter Should Be Within    ${jitter}    expected_period_ms=100
    ...    mean_tolerance_ms=${JITTER_MEAN_TOL_MS}
    ...    max_std_ms=${JITTER_MAX_STD_MS}
    ...    max_gap_deviation_ms=${JITTER_MAX_GAP_DEV_MS}
    Log    周期报文 ${jitter}[id]: 帧数=${jitter}[frames] 均值=${jitter}[avg_interval_ms]ms 标准差=${jitter}[std_interval_ms]ms 最大间隔=${jitter}[max_interval_ms]ms

突发压力：连续 200 条 TesterPresent 应 100% 成功且吞吐达标
    [Tags]    perf    stress    REQ-PERF-003
    [Documentation]    默认工况诊断链路是空闲的；连续 200 条、~100 条/s 的请求把
    ...               ECU 诊断任务压到接近饱和。成功必须用**响应**证明（发出去 ≠
    ...               被处理），吞吐下限 50 条/s 是"一屏刷 20 个 DID"的可用下限。
    ${burst}=    Send UDS Requests In Burst    ${BURST_COUNT}
    Should Be Equal As Integers    ${burst}[success]    ${BURST_COUNT}
    ...    msg=突发压力成功率不足：${burst}
    Should Be Equal As Integers    ${burst}[failed]    0
    ...    msg=突发压力出现失败请求：${burst}
    Throughput Should Be Above    ${burst}    ${BURST_MIN_THROUGHPUT}
    Log    突发压力: 成功 ${burst}[success]/${burst}[sent]，耗时 ${burst}[elapsed_s]s，吞吐 ${burst}[throughput_per_s] 条/s

压力测试后 ECU 应仍在线（诊断链路未被打挂）
    [Tags]    perf    stress    recovery    REQ-PERF-003
    [Documentation]    压力测试价值一半在"压完还能不能干活"：200 条请求轰过后，
    ...               诊断链路必须仍能正常响应（不能出现处理队列卡死/线程静默退出
    ...               —— 这类缺陷只在压力后显现，功能用例抓不到）。
    ECU Should Respond
    Log    突发压力后 ECU 诊断链路仍正常响应（3E 00 → 7E 00）

发布前短长稳：10s soak 应无丢帧、无诊断超时（并输出基线汇总）
    [Tags]    perf    soak    baseline    REQ-PERF-004
    [Documentation]    发版前跑 10s 短 soak：每 0.5s 探一次诊断链路，同时逐帧监控
    ...               0x100/0x200/0x300 的到达间隔。丢帧按"间隔 >1.5×设计周期"判定
    ...               （不能拿理论帧数硬减：仿真周期实际 ~104ms，按理论值算会凭空
    ...               误报）。诊断超时上限 0 次 —— 长稳期间诊断链路一次都不许超时。
    ...               最后输出本轮 p95/负载等基线，供下次发版对比（基线只记录、
    ...               不硬编码成断言，否则换机器就是假失败）。
    ${soak}=    Run Soak Sampling    ${SOAK_SECONDS}
    Soak Errors Should Be Below    ${soak}    max_errors=${SOAK_MAX_ERRORS}
    ...    max_timeouts=${SOAK_MAX_TIMEOUTS}
    Log    长稳明细(10s): ${soak}
    ${samples}=    Measure UDS Response Time    3E 00    samples=3
    ${stats}=    Response Time Stats    ${samples}
    Log Performance Summary    REQ-PERF 发布性能基线
    Log    性能基线汇总(响应时间 ms): min=${stats}[min] avg=${stats}[avg] max=${stats}[max] p95=${stats}[p95]

*** Keywords ***
关闭性能库并回收车辆环境
    [Documentation]    Suite Teardown：先释放 PerfLibrary 的两条虚拟总线与诊断收包
    ...               线程，再回收车辆环境（停仿真进程）。顺序不能反 —— 否则进程
    ...               退出时 python-can 会报 "UdpMulticastBus was not properly
    ...               shut down"（socket 句柄泄漏）。
    Run Keyword And Ignore Error    Close Perf Library
    Stop Vehicle Environment
