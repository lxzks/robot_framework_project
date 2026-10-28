*** Settings ***
Documentation    套件 09：HMI 视觉自动化测试（截图基准比对 / 指针角度 / 警告灯区域 / 差异图）
...              被测对象：`simulators/cluster_canvas.py` 的确定性离屏渲染器（800x480）。
...              与 03/05 套件"断言显示状态 JSON"互补：本套件断言"画出来的像素"——
...              布局错位、配色错误、指针卡死、警告灯没画出来，这些 GUI 缺陷只有
...              视觉断言能发现。
...              本套件不启动 ECU/总线/子进程：纯离屏渲染 + 纯像素比对，结果稳定、
...              速度快，任何机器都能复现。
...              设计要点：① 基准图（golden）由渲染器生成并提交 git，可复现
...              ② 比对失败自动保存红框差异图 ③ mutation 用例证明比对不是恒真
...              ④ 区域比对抑制无关变化 ⑤ 指针角度/灯色占比用纯像素算法识别。
...              需求追溯：REQ-VIS-001 仪表画面基准比对 / REQ-VIS-002 指针角度与车速一致 /
...              REQ-VIS-003 警告灯区域识别 / REQ-VIS-004 视觉变化检测灵敏度。
Library          ../../libraries/VisionLibrary.py
Library          OperatingSystem
Suite Setup      Create Directory    ${OUT}

*** Variables ***
# ${CURDIR} = 本套件所在目录 tests/robot/，项目根目录是它的上两级
${ROOT}            ${CURDIR}/../..
${GOLDEN_DIR}      ${ROOT}/simulators/golden
${OUT}             ${ROOT}/results/vision
${GOLDEN_DEFAULT}  ${GOLDEN_DIR}/cluster_default.png
${GOLDEN_ALARM}    ${GOLDEN_DIR}/cluster_60kmh_alarm.png

*** Test Cases ***
默认状态整图应与黄金图一致
    [Tags]    REQ-VIS-001
    [Documentation]    视觉回归基线：默认状态（0 km/h、灯光全灭、P 档）的渲染结果
    ...               必须与 git 里提交的 `cluster_default.png` 一致。
    ...               同机运行差异比例通常为 0.00%；跨平台抗锯齿有亚像素差异，
    ...               所以断言的是"≤ 默认容差 2%"，而不是逐位相等（逐位相等见
    ...               "同一状态两次渲染应像素完全一致"用例）。
    ${png}=    Render Cluster State    ${{ {}}}    ${OUT}/default.png
    ${ratio}=    Compare With Golden    ${png}    ${GOLDEN_DEFAULT}
    Should Be True    ${ratio} <= 0.02

车速 60 与 120 时指针角度应约为 67.5 度与 135 度
    [Tags]    REQ-VIS-002
    [Documentation]    指针角度换算：扫过角度 = 车速 / 240 * 270°
    ...               0→0°，60→67.5°，120→135°，240→270°。
    ...               Get Needle Angle 按"指针专属橙色像素"做向量拟合识别，
    ...               容差 ±2°（像素量化误差约 0.3~0.5°，留 2° 既不 flaky 又能抓住
    ...               "指针卡在 0 / 接错车速信号"这类真实缺陷）。
    ${png60}=    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/speed_60.png
    Needle Angle Should Be Near    ${png60}    67.5    tolerance=2.0
    ${png120}=    Render Cluster State    ${{ {"speed": 120} }}    ${OUT}/speed_120.png
    Needle Angle Should Be Near    ${png120}    135.0    tolerance=2.0
    ${png240}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/speed_240.png
    Needle Angle Should Be Near    ${png240}    270.0    tolerance=2.0

水温报警灯点亮时对应区域应识别为 lit
    [Tags]    REQ-VIS-003
    [Documentation]    渲染"60 km/h 行驶 + 水温 121℃ 超限"状态：
    ...               ①整图与报警基准图 `cluster_60kmh_alarm.png` 一致
    ...               ②CoolantTempHigh 区域红色占比 ≥ 25% → 判定为点亮。
    ...               区域坐标来自渲染器的 TELLTALE_REGIONS（测试用名字查询，不写死）。
    ${state}=    Evaluate    {"speed": 60, "rpm": 4800, "coolant": 121, "fuel": 45, "gear": "D", "telltales": {"CoolantTempHigh": 1}, "trip": {"km": 12.3}, "media": {"volume": 45}, "navi": {"turn": 3}}
    ${png}=    Render Cluster State    ${state}    ${OUT}/alarm.png
    Compare With Golden    ${png}    ${GOLDEN_ALARM}
    ${ratio}=    Telltale Region Should Be Lit    ${png}    CoolantTempHigh
    Should Be True    ${ratio} > 0.25

同一区域灯灭时应识别为 off
    [Tags]    REQ-VIS-003
    [Documentation]    负向断言：同样的车速/水温场景，只是水温灯信号为 0。
    ...               同一个区域"亮→lit、灭→off"成对验证，才能证明区域识别不是
    ...               恒真（永远返回 lit 的 bug 会被这条抓住）。
    ${state}=    Evaluate
    ...    {"speed": 60, "rpm": 4800, "coolant": 121, "fuel": 45, "gear": "D"}
    ${png}=    Render Cluster State    ${state}    ${OUT}/lamp_off.png
    ${ratio}=    Telltale Region Should Be Off    ${png}    CoolantTempHigh
    Should Be True    ${ratio} <= 0.05

状态变化时必须能被比对检出（mutation，证明比对不是恒真）
    [Tags]    REQ-VIS-004
    [Documentation]    视觉测试最大的坑是"比对恒真"：差分算法写错、容差设成 1.0，
    ...               所有用例都通过但什么都没验证。mutation 用例故意把状态从
    ...               0 km/h 改成 240 km/h（指针 + 进度弧大幅变化），要求：
    ...               ①图像差分比例 ≥ 2%（灵敏度足够）
    ...               ②与默认黄金图比对**必须失败**（Run Keyword And Expect Error）。
    ...               这是"测试的测试"——证明断言有杀伤力。
    ${baseline}=    Render Cluster State    ${{ {}}}    ${OUT}/mutation_baseline.png
    ${mutated}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/mutation_speed240.png
    Images Should Differ    ${mutated}    ${baseline}    min_ratio=0.02
    Run Keyword And Expect Error    *整图与黄金图不一致*
    ...    Compare With Golden    ${mutated}    ${GOLDEN_DEFAULT}

只比警告灯区域时车速变化不应影响比对结果
    [Tags]    REQ-VIS-001    REQ-VIS-003
    [Documentation]    区域比对（ROI）的价值：车速 240 km/h 让整图差异 > 2%
    ...               （表盘区域大改），但警告灯区域完全没变 → Compare Region
    ...               With Golden 通过。真实项目里"只关心局部变化"时，区域比对
    ...               能显著降低误报（例如只验警告灯、不管仪表翻页动画）。
    ${png}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/region_speed240.png
    ${whole}=    Image Diff Ratio    ${png}    ${GOLDEN_DEFAULT}
    Should Be True    ${whole} > 0.02
    ${region}=    Get Telltale Region    CoolantTempHigh
    ${ratio}=    Compare Region With Golden    ${png}    ${GOLDEN_DEFAULT}
    ...    ${region}[0]    ${region}[1]    ${region}[2]    ${region}[3]
    Should Be True    ${ratio} <= 0.02

差异图应生成且包含红色高亮
    [Tags]    REQ-VIS-004
    [Documentation]    比对失败必须留下"可看的证据"：Save Diff Image 生成差异图，
    ...               底层图 = 实际截图，差异像素涂纯红 + 红框标出差异范围。
    ...               断言差异图文件存在且红色像素数 > 0（不是空白图）。
    ${mutated}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/diff_probe.png
    ${diff}=    Save Diff Image    ${mutated}    ${GOLDEN_DEFAULT}
    ...    ${OUT}/diff_speed240.png
    File Should Exist    ${diff}
    ${red}=    Count Red Pixels    ${diff}
    Should Be True    ${red} > 1000

同一状态两次渲染应像素完全一致（渲染确定性）
    [Tags]    REQ-VIS-004
    [Documentation]    视觉测试可复现的前提 = 渲染确定性。同一状态分别用
    ...               Render Cluster State（渲染器直调）与 Capture Cluster
    ...               Screenshot（截图入口，当前同样走离屏渲染）各渲染一次，
    ...               用 `color_tolerance=0` 严格逐像素比较，差异比例必须为 0。
    ...               真实项目里这一条是"基准图能不能信"的第一道门禁。
    ${state}=    Evaluate    {"speed": 120, "telltales": {"BatteryLow": 1}}
    ${png_render}=    Render Cluster State    ${state}    ${OUT}/determinism_render.png
    ${png_capture}=    Capture Cluster Screenshot    ${state}    ${OUT}/determinism_capture.png
    ${ratio}=    Image Diff Ratio    ${png_render}    ${png_capture}    color_tolerance=0
    Should Be Equal As Numbers    ${ratio}    0.0

黄金图缺失时应给出清晰报错
    [Tags]    REQ-VIS-001
    [Documentation]    基准图管理也是测试资产：文件被误删/路径写错时，报错必须
    ...               直接说"基准图不存在 + 查了哪些路径 + 怎么生成"，而不是
    ...               Python 的 FileNotFoundError 堆栈。这里用 Run Keyword And
    ...               Expect Error 锁死错误信息里的关键词。
    ${actual}=    Render Cluster State    ${{ {}}}    ${OUT}/missing_golden_probe.png
    Run Keyword And Expect Error    *基准图不存在*
    ...    Compare With Golden    ${actual}    ${GOLDEN_DIR}/__no_such_golden__.png

灯亮与车速变化的整图差异比例排序应合理
    [Tags]    REQ-VIS-004
    [Documentation]    用差异比例做"数量级"断言，证明差分对变化方向/幅度敏感：
    ...               基准自身 ≈ 0% < 车速 60 的变化 < 车速 60 + 水温灯亮 < 车速 240。
    ...               同时 "60 灯亮 vs 60 灯灭" 的差异必须 > 0（灯亮确实被画出来），
    ...               且远小于车速 240 的差异（变化幅度排序合理）。
    ${base}=    Render Cluster State    ${{ {}}}    ${OUT}/ratio_base.png
    ${speed60}=    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/ratio_speed60.png
    ${speed60_lit}=    Render Cluster State
    ...    ${{ {"speed": 60, "telltales": {"CoolantTempHigh": 1}} }}
    ...    ${OUT}/ratio_speed60_lit.png
    ${speed240}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/ratio_speed240.png
    ${ratio_base}=    Image Diff Ratio    ${base}    ${GOLDEN_DEFAULT}
    ${ratio_60}=    Image Diff Ratio    ${speed60}    ${GOLDEN_DEFAULT}
    ${ratio_60_lit}=    Image Diff Ratio    ${speed60_lit}    ${GOLDEN_DEFAULT}
    ${ratio_240}=    Image Diff Ratio    ${speed240}    ${GOLDEN_DEFAULT}
    ${lamp_delta}=    Image Diff Ratio    ${speed60_lit}    ${speed60}
    Should Be True    ${ratio_base} <= 0.02
    Should Be True    0 < ${ratio_60} < ${ratio_60_lit} < ${ratio_240}
    Should Be True    0 < ${lamp_delta} < ${ratio_240}
