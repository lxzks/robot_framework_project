*** Settings ***
Documentation    公共资源：车辆仿真环境管理 + 路径变量 + 关键字库加载。
...              每个 .robot 套件在自己的 Suite Setup 里调用
...              Start Vehicle Environment 自动拉起 ECU 和仪表盘，
...              Suite Teardown 里回收进程 —— 一条 robot 命令跑完全部测试。
Library          Process
Library          Collections
Library          OperatingSystem
Variables        env_vars.py
# 库路径相对"本资源文件目录"(<root>/resources/)，所以是 ../libraries/
Library          ../libraries/VehicleBusLibrary.py
Library          ../libraries/DiagnosticLibrary.py
Library          ../libraries/DashboardLibrary.py

*** Variables ***
# 注意：${CURDIR} 是"本资源文件所在目录"（<root>/resources/），
# 所以项目根目录是 ${CURDIR}/..（不是 /../..，那会退到项目外面）
${ROOT}          ${CURDIR}/..
# 本项目使用的 Python 解释器：由变量文件 env_vars.py 暴露"当前解释器"
# （sys.executable）—— 无论谁怎么启动 robot 都自动正确：
# 直接 `python -m robot`、run_tests.py、CI 都无需配置。
# 想强制指定：robot --variable PYTHON:/path/to/python ...
${PYTHON}        ${PYTHON_EXE}
${ECU_SCRIPT}    ${ROOT}/simulators/ecu_sim.py
${DASH_SCRIPT}   ${ROOT}/simulators/dashboard.py
${AGUI_SCRIPT}   ${ROOT}/simulators/android_gui.py
${STATE_FILE}    ${ROOT}/simulators/dashboard_state.json
${ADB_STATE}     ${ROOT}/simulators/adb_device_state.json

*** Keywords ***
Start Vehicle Environment
    [Documentation]    启动 ECU 模拟器 + headless 仪表盘（可选安卓车机 GUI）。
    [Arguments]    ${speed}=0    ${coolant}=90    ${with_android}=False
    # stdin=DEVNULL：避免仿真进程继承并占用测试进程的 stdin 句柄，
    # 否则后续 subprocess 调外部命令（如 adb_sim）在 Windows 上会挂起
    Start Process    ${PYTHON}    ${ECU_SCRIPT}
    ...    --speed    ${speed}    --coolant    ${coolant}
    ...    alias=ecu    stdin=DEVNULL    stdout=${ROOT}/results/ecu_stdout.log
    Start Process    ${PYTHON}    ${DASH_SCRIPT}    --headless
    ...    alias=dashboard    stdin=DEVNULL    stdout=${ROOT}/results/dashboard_stdout.log
    IF    '${with_android}' == 'True'
        # 安卓车机用 headless 模式跑（CAN 广播 + 状态轮询，不起 Qt 窗口）。
        # GUI 模式留给人手动体验；CI/RF 环境用 headless —— 与 dashboard
        # 的双模式设计一致，且避免 offscreen Qt 在子进程树里的不稳定问题。
        Start Process    ${PYTHON}    ${AGUI_SCRIPT}    --headless
        ...    alias=android_gui    stdin=DEVNULL    stdout=${ROOT}/results/android_gui_stdout.log
    END
    Wait Until Keyword Succeeds    15x    1s
    ...    Dashboard Should Be Ready
    # ECU 就绪检查：直接验证诊断链路，防止被上一个套件残留的
    # 新鲜状态文件"骗过"（文件新鲜 ≠ 本套件的 ECU 已启动）
    Wait Until Keyword Succeeds    15x    1s
    ...    ECU Should Respond
    Log    车辆环境已启动: ecu + dashboard(headless)[+ android_gui] + 诊断链路已验证

Stop Vehicle Environment
    [Documentation]    关闭所有仿真进程 + 释放 RF 库里的总线连接。
    ...              注意：terminate 后必须 Wait For Process 确认退出，
    ...              否则下一个套件启动的新进程会和没死透的旧进程
    ...              同时写 dashboard_state.json，互相覆盖。
    Run Keyword And Ignore Error    Terminate Process    android_gui    kill=True
    Run Keyword And Ignore Error    Wait For Process    android_gui    timeout=5s
    Run Keyword And Ignore Error    Terminate Process    dashboard    kill=True
    Run Keyword And Ignore Error    Wait For Process    dashboard    timeout=5s
    Run Keyword And Ignore Error    Terminate Process    ecu    kill=True
    Run Keyword And Ignore Error    Wait For Process    ecu    timeout=5s
    Sleep    0.5s
    # 归档总线 Trace（CANoe 兼容）：有录制内容才导出，失败时供事后分析。
    # 用固定文件名（套件名含中文/冒号不能做 Windows 文件名）
    Run Keyword And Ignore Error
    ...    Export Bus Trace    ${ROOT}/results/bus_trace_last.asc
    Run Keyword And Ignore Error    Close CAN Bus
    Run Keyword And Ignore Error    Close Diagnostic Tester
    # 测试隔离：清掉车机状态文件，下一个套件从全新状态开始
    # （否则 04 套件"卸载 navi"的残留会让 05 套件的 am start 失败）
    Run Keyword And Ignore Error    Remove File    ${ADB_STATE}
    Log    车辆环境已关闭
