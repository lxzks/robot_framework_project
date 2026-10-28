*** Settings ***
Documentation    场景 15：HMI 视觉回归套件（发布前一键跑）
...              【业务背景】座舱仪表是"用户每天都在看"的安全显示界面。功能测试
...              断言 JSON 状态正确，但"布局错位、指针卡死、警告灯没画出来、配色
...              变灰"这类 GUI 缺陷只有**像素级**断言能发现。真实项目的做法：
...              维护一套基准图（golden），每次发版前跑视觉回归，失败自动留下
...              差异图给 UI/开发定位。
...              被测画面：simulators/cluster_canvas.py 确定性离屏渲染（800x480）。
...              本场景不启动任何子进程（纯渲染 + 纯像素比对，稳定可复现）。
...              标签对应 REQ-VIS-001~004。
...              运行：.venv\\Scripts\\python.exe -m robot --outputdir results/dev_scen15 scenarios/15_vision_regression.robot
Library           ../libraries/VisionLibrary.py
Library           OperatingSystem
Suite Setup       Create Directory    ${OUT}
Suite Teardown    Log    视觉回归产物目录：${OUT}

*** Variables ***
# ${CURDIR} = 本场景所在目录 scenarios/，项目根目录是它的上一级
${ROOT}             ${CURDIR}/..
${GOLDEN_DIR}       ${ROOT}/simulators/golden
${OUT}              ${ROOT}/results/dev_scen15/vision
${GOLDEN_DEFAULT}   ${GOLDEN_DIR}/cluster_default.png
${GOLDEN_ALARM}     ${GOLDEN_DIR}/cluster_60kmh_alarm.png

*** Test Cases ***
发布前默认首页画面应与基线一致
    [Tags]    vision    hmi    regression    REQ-VIS-001
    [Documentation]    视觉回归第一道门禁：默认状态（0 km/h、灯光全灭、P 档）必须与
    ...               git 里提交的 `cluster_default.png` 一致（容差 2%，吸收跨平台
    ...               抗锯齿的亚像素差异）。不一致 = 本轮 UI 改动改到了不该改的地方，
    ...               或基准图该更新了（更新走 Code Review 的 golden 流程）。
    ${png}=    Render Cluster State    ${{ {}}}    ${OUT}/default.png
    ${ratio}=    Compare With Golden    ${png}    ${GOLDEN_DEFAULT}
    Should Be True    ${ratio} <= 0.02
    ...    msg=默认首页与基线差异 ${ratio}（>2%），请打开差异图确认 UI 是否被误改

车速变化时指针角度应符合刻度换算
    [Tags]    vision    hmi    display    REQ-VIS-002
    [Documentation]    业务：车速表 0~240 km/h 对应指针扫过 0~270°。60→67.5°、
    ...               120→135°、240→270°。断言角度而不是断言像素颜色，是因为
    ...               "指针卡死在 0"这类缺陷人眼看得出、像素比对却可能被其它区域
    ...               变化淹没；角度识别（容差 ±2°）既能抓住卡死/接错信号，
    ...               又不会因抗锯齿抖动而 flaky。
    ${png60}=    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/speed_60.png
    Needle Angle Should Be Near    ${png60}    67.5    tolerance=2.0
    ${png120}=    Render Cluster State    ${{ {"speed": 120} }}    ${OUT}/speed_120.png
    Needle Angle Should Be Near    ${png120}    135.0    tolerance=2.0
    ${png240}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/speed_240.png
    Needle Angle Should Be Near    ${png240}    270.0    tolerance=2.0

水温报警灯点亮时对应区域应识别为 lit
    [Tags]    vision    hmi    safety    REQ-VIS-003
    [Documentation]    业务：水温 >120℃ 是安全相关报警，必须确认"灯真的画出来了"。
    ...               验证两层：①整图与报警基线 `cluster_60kmh_alarm.png` 一致
    ...               ②CoolantTempHigh 区域红色占比 ≥25% → 判定 lit。
    ...               区域坐标由渲染器 TELLTALE_REGIONS 提供（库与渲染器同一份几何
    ...               常量，避免"库和画面各写一套坐标"）。
    ${state}=    Evaluate
    ...    {"speed": 60, "rpm": 4800, "coolant": 121, "fuel": 45, "gear": "D", "telltales": {"CoolantTempHigh": 1}, "trip": {"km": 12.3}, "media": {"volume": 45}, "navi": {"turn": 3}}
    ${png}=    Render Cluster State    ${state}    ${OUT}/alarm.png
    Compare With Golden    ${png}    ${GOLDEN_ALARM}
    ${ratio}=    Telltale Region Should Be Lit    ${png}    CoolantTempHigh
    Should Be True    ${ratio} > 0.25
    ...    msg=水温报警灯区域红色占比 ${ratio}，应 ≥25% 判定为点亮

同一区域灯灭时应识别为 off（亮灭成对验证）
    [Tags]    vision    hmi    safety    negative    REQ-VIS-003
    [Documentation]    负向断言：同样的车速/水温场景，只把水温灯信号置 0。
    ...               必须与上一条"亮"用例成对看 —— 否则一个"永远返回 lit"的
    ...               识别 bug 会让"灯亮"用例恒真通过。
    ${state}=    Evaluate
    ...    {"speed": 60, "rpm": 4800, "coolant": 121, "fuel": 45, "gear": "D"}
    ${png}=    Render Cluster State    ${state}    ${OUT}/lamp_off.png
    ${ratio}=    Telltale Region Should Be Off    ${png}    CoolantTempHigh
    Should Be True    ${ratio} <= 0.05
    ...    msg=灯灭状态下区域红色占比 ${ratio}，应 ≤5% 判定为熄灭

画面变化必须能被差分检出（mutation，证明比对不是恒真）
    [Tags]    vision    hmi    negative    REQ-VIS-004
    [Documentation]    视觉测试最大的坑是"比对恒真"：差分算法写错、容差设成 1.0，
    ...               所有用例都绿但什么都没验证。mutation 用例故意把车速 0→240
    ...               （指针 + 进度弧大幅变化），要求差分比例 ≥2% 且与基线比对**必须
    ...               失败** —— 这是"测试的测试"，证明断言有杀伤力。
    ${baseline}=    Render Cluster State    ${{ {}}}    ${OUT}/mutation_baseline.png
    ${mutated}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/mutation_speed240.png
    Images Should Differ    ${mutated}    ${baseline}    min_ratio=0.02
    Run Keyword And Expect Error    *整图与黄金图不一致*
    ...    Compare With Golden    ${mutated}    ${GOLDEN_DEFAULT}

比对失败时应留下差异图证据
    [Tags]    vision    hmi    evidence    REQ-VIS-004
    [Documentation]    真实项目里视觉回归失败必须"可定位"：Save Diff Image 在
    ...               实际截图上把差异像素涂红 + 红框标范围，产出
    ...               `diff_speed240.png` 附进报告。断言差异图存在且红色像素 >1000
    ...               （不是空白图），否则"失败证据"形同虚设。
    ${mutated}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/evidence_probe.png
    ${diff}=    Save Diff Image    ${mutated}    ${GOLDEN_DEFAULT}
    ...    ${OUT}/diff_speed240.png
    File Should Exist    ${diff}
    ${red}=    Count Red Pixels    ${diff}
    Should Be True    ${red} > 1000
    ...    msg=差异图红色高亮像素 ${red}，太少说明差异图没标出变化区域
