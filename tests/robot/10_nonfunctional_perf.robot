*** Settings ***
Documentation    套件 10：非功能测试（性能 / 压力 / 长稳 / 总线负载）
...              「功能都对了」不代表能交付 —— 还需要回答：诊断要多快？总线还剩多少
...              带宽？连续压 200 条会不会把 ECU 压挂？跑 20 秒会不会丢帧？
...              本套件用 **真实测量**（time.monotonic 计时 + 真实收帧）回答这四类问题，
...              需求号：REQ-PERF-001（响应时间）/ 002（总线负载）/ 003（吞吐与压力）/
...              004（长稳与抖动）。
...              对应真实工作：台架/HIL 上用 CANoe 做 Bus Statistics、用诊断仪测
...              P2 响应时间、跑 soak test 做长稳回归 —— 口径完全一致。
...              套件总时长目标 ≤60s（含环境启停），可在 CI 慢机器上稳定复跑。
Resource         ../../resources/common.resource
Library          ../../libraries/PerfLibrary.py
Suite Setup      Start Vehicle Environment
Suite Teardown   关闭性能库并回收车辆环境

*** Variables ***
# 阈值都按「实测值 + 足够余量」定，不允许在 CI 慢机器上 flaky：
# 实测响应 p95 ≈ 2ms（阈值 50ms）、负载 ≈ 0.46%（阈值 30%）、
# 吞吐 ≈ 105 条/s（下限 50）、0x100 周期 ≈ 103.8ms（均值容差 ±15ms）。
${P2_MS}                    50        # ECU 声明的 P2（会话响应里 0x0032）
${SAMPLE_COUNT}             9         # P2 采样次数（单次测量会漏掉"偶尔慢"）
${BUS_LOAD_MAX}             30        # 总线负载率红线（工程经验值）
${BUS_RECORD_SECONDS}       2         # 总线负载录制时长
${JITTER_SECONDS}           3         # 周期报文抖动录制时长
${JITTER_MEAN_TOL_MS}       15        # 周期均值容差（仿真 ECU 实际 ~104ms）
${JITTER_MAX_STD_MS}        10        # 间隔标准差上限
${JITTER_MAX_GAP_DEV_MS}    50        # 最大间隔超出设计周期的上限
${BURST_COUNT}              200       # 突发请求条数
${BURST_MIN_THROUGHPUT}     50        # 诊断吞吐下限（条/s）
${SOAK_SECONDS}             20        # 长稳时长
${SOAK_MAX_ERRORS}          5         # 长稳允许的总错误数（丢帧+超时）
${SOAK_MAX_TIMEOUTS}        0         # 长稳允许的诊断超时（诊断链路不允许超时）

*** Test Cases ***
诊断响应时间应满足声明的 P2（采样 9 次并统计 p95）
    [Tags]    nonfunctional    performance    REQ-PERF-001
    [Documentation]    ECU 在会话控制响应里声明 P2=50ms，实测响应时间的 p95 必须低于它。
    ...              为什么采样 9 次：偶发的一次慢响应（GC/调度）不能代表 ECU 慢，
    ...              但"9 次里有 1 次慢 80ms"正是真实项目发现过的缺陷 —— 单次测量
    ...              会漏掉，所以要看 p95（长尾）而不是只看平均。
    ${samples}=    Measure UDS Response Time    3E 00    samples=${SAMPLE_COUNT}
    ${stats}=    Response Time Stats    ${samples}
    Response Time P95 Should Be Below    ${stats}    ${P2_MS}
    Log    响应时间样本(ms): ${samples}
    Log    响应时间统计(ms): min=${stats}[min] avg=${stats}[avg] max=${stats}[max] p95=${stats}[p95] std=${stats}[std] n=${stats}[count]

响应时间抖动应稳定在较小范围（标准差）
    [Tags]    nonfunctional    performance    REQ-PERF-001
    [Documentation]    "平均快"不等于"稳定"：平均值 2ms 但偶尔 80ms 的 ECU，在实车上
    ...              表现就是"诊断仪偶发超时"。用总体标准差量化离散度，阈值 5ms
    ...              （实测 ~0.3ms，留足 CI 抖动余量）。
    ${samples}=    Measure UDS Response Time    3E 00    samples=${SAMPLE_COUNT}
    ${stats}=    Response Time Stats    ${samples}
    Response Time Jitter Should Be Below    ${stats}    max_std_ms=5
    Log    响应时间抖动(标准差): ${stats}[std] ms（min=${stats}[min] ms max=${stats}[max] ms）

默认工况总线负载率应低于 30%
    [Tags]    nonfunctional    bus_load    REQ-PERF-002
    [Documentation]    默认工况 = 3 条周期报文（0x100/0x200/0x300）+ 诊断空闲。
    ...              按 500kbps、每帧 47+8*DLC bit、再加 20% 位填充余量估算，
    ...              与 CANoe Bus Statistics 同口径。30% 是工程红线：超过它，
    ...              低优先级报文延迟抖动急剧变大，诊断/刷写余量不足。
    ${load}=    Record Bus Load    ${BUS_RECORD_SECONDS}
    Bus Load Should Be Below    ${load}    ${BUS_LOAD_MAX}
    Log    默认工况总线负载率实测: ${load} %（红线 ${BUS_LOAD_MAX}%，500kbps）

周期报文 0x100 的平均周期应为 100ms 且抖动受控
    [Tags]    nonfunctional    timing    REQ-PERF-004
    [Documentation]    周期是 ECU 的设计指标：0x100 应 100ms 发一帧。均值对了还不够，
    ...              还要看标准差与最大间隔（"个别帧晚几十毫秒"会让仪表/ABS 判断异常）。
    ...              容差说明：仿真 ECU 主循环用 10ms 轮询累加计时，实际均值稳定在
    ...              ~104ms（系统偏差 4%），所以均值容差 ±15ms、抖动容差另算。
    ${jitter}=    Measure Periodic Message Jitter    0x100    ${JITTER_SECONDS}
    Jitter Should Be Within    ${jitter}    expected_period_ms=100
    ...    mean_tolerance_ms=${JITTER_MEAN_TOL_MS}
    ...    max_std_ms=${JITTER_MAX_STD_MS}
    ...    max_gap_deviation_ms=${JITTER_MAX_GAP_DEV_MS}
    Log    周期报文 ${jitter}[id]: 帧数=${jitter}[frames] 均值=${jitter}[avg_interval_ms]ms 标准差=${jitter}[std_interval_ms]ms 最大间隔=${jitter}[max_interval_ms]ms

突发压力：连续 200 条 TesterPresent 应 100% 成功且吞吐达标
    [Tags]    nonfunctional    stress    REQ-PERF-003
    [Documentation]    默认工况诊断链路是空闲的（~0 条/s）；连续 200 条、~100 条/s
    ...              的请求速率把 ECU 诊断任务压到接近饱和。成功必须用**响应**
    ...              证明（发出去 ≠ 被处理），吞吐下限 50 条/s 是诊断仪实时刷
    ...              20 个 DID 一屏的可用下限。请求间隔 8ms 是仿真总线 5ms
    ...              去重窗口决定的（真实 CAN 无此限制，见 PerfLibrary 文档）。
    ${burst}=    Send UDS Requests In Burst    ${BURST_COUNT}
    Should Be Equal As Integers    ${burst}[success]    ${BURST_COUNT}
    ...    msg=突发压力成功率不足：${burst}
    Should Be Equal As Integers    ${burst}[failed]    0
    ...    msg=突发压力出现失败请求：${burst}
    Throughput Should Be Above    ${burst}    ${BURST_MIN_THROUGHPUT}
    Log    突发压力: 成功 ${burst}[success]/${burst}[sent]，耗时 ${burst}[elapsed_s]s，吞吐 ${burst}[throughput_per_s] 条/s，单条响应 avg=${burst}[rtt_avg_ms]ms max=${burst}[rtt_max_ms]ms

突发压力后 ECU 应仍在线（诊断链路未被打挂）
    [Tags]    nonfunctional    stress    REQ-PERF-003
    [Documentation]    压力测试的价值一半在"压完还能不能干活"：ECU 被 200 条诊断
    ...              请求轰过之后，诊断链路仍必须能正常响应（不能出现'处理队列
    ...              卡死/线程静默退出'——这类缺陷只在压力后显现）。
    ECU Should Respond
    Log    突发压力后 ECU 诊断链路仍正常响应（3E 00 → 7E 00）

长稳：20s soak 采样周期报文与诊断链路应无丢帧/超时
    [Tags]    nonfunctional    soak    REQ-PERF-004
    [Documentation]    soak（浸泡）测试：连续跑 20s，每 0.5s 探一次诊断链路，
    ...              同时逐帧监控 0x100/0x200/0x300 的到达间隔。
    ...              丢帧按"间隔 > 1.5×设计周期"判定（不是拿理论帧数硬减：仿真
    ...              ECU 实际周期 ~104ms，按理论值 20s 会凭空报 7 帧假丢帧）。
    ...              诊断超时上限 0 次 —— 长稳期间诊断链路一次都不允许超时。
    ${soak}=    Run Soak Sampling    ${SOAK_SECONDS}
    Soak Errors Should Be Below    ${soak}    max_errors=${SOAK_MAX_ERRORS}
    ...    max_timeouts=${SOAK_MAX_TIMEOUTS}
    Log    长稳明细: ${soak}

持续诊断会话保活：S3 超时窗口内周期发 3E 00，会话不丢
    [Tags]    nonfunctional    timing    REQ-PERF-004
    [Documentation]    非默认会话有 S3 定时器（本项目 5s）：超过 5s 无任何请求，
    ...              ECU 自动回默认会话并锁定安全访问。本用例在扩展会话里每
    ...              1.2s 发一条 TesterPresent（保活），总时长 >5s（跨过 S3 窗口），
    ...              之后写 DID 应仍成功 —— 若保活无效，S3 超时后写 DID 会报
    ...              NRC 0x7F（会话不支持）。这是"时序类需求验变化"的典型写法。
    Enter Extended Session
    Security Unlock
    FOR    ${i}    IN RANGE    5
        Sleep    1.2s
        ${one}=    Measure UDS Response Time    3E 00    samples=1
        Should Be True    ${one}[0] < ${P2_MS}
        ...    msg=第 ${i + 1} 次保活请求响应 ${one}[0]ms 超过 P2 ${P2_MS}ms
    END
    # 此时已跨过 5s S3 窗口：若保活无效，会话早就回默认，0x2E 会被 NRC 0x7F 拒绝
    Write DID    F1A0    5A5A
    Response Should Be Positive
    Log    跨 S3（5s）保活成功：会话仍是扩展会话，受保护的 0x2E 写服务仍被放行

性能基线记录：输出 min/avg/max/p95 汇总供回归对比
    [Tags]    nonfunctional    baseline    REQ-PERF-001    REQ-PERF-002
    [Documentation]    性能测试的价值在**对比**：今天 p95=2ms，下次提交后 p95=20ms
    ...              就说明改动引入了性能退化。本用例把本轮所有实测指标
    ...              （响应时间/总线负载/周期抖动/吞吐/长稳）汇总输出到 log.html，
    ...              作为下次回归的对比基线（基线只记录、不硬编码成断言 ——
    ...              否则换机器就是假失败）。
    ${samples}=    Measure UDS Response Time    3E 00    samples=${SAMPLE_COUNT}
    ${stats}=    Response Time Stats    ${samples}
    Response Time P95 Should Be Below    ${stats}    ${P2_MS}
    Log Performance Summary    REQ-PERF 性能基线
    Log    性能基线(响应时间 ms): min=${stats}[min] avg=${stats}[avg] max=${stats}[max] p95=${stats}[p95] std=${stats}[std]（样本数 ${stats}[count]）

*** Keywords ***
关闭性能库并回收车辆环境
    [Documentation]    先释放 PerfLibrary 的两条虚拟总线与诊断收包线程，再走公共
    ...              环境回收（停仿真进程）。顺序不能反：ECU 进程被 kill 后收包
    ...              线程会因 socket 关闭而退出，但仍应在停环境前把库资源释放干净，
    ...              否则进程退出时 python-can 会报 "UdpMulticastBus was not
    ...              properly shut down"（socket 句柄泄漏）。
    Run Keyword And Ignore Error    Close Perf Library
    Stop Vehicle Environment
