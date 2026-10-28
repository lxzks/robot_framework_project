*** Settings ***
Documentation    L9 参考答案：HMI 视觉断言（黄金图 / 指针角度 / 警告灯区域 / mutation）
...              被测画面：simulators/cluster_canvas.py 的确定性离屏渲染（800x480）。
...              黄金图：simulators/golden/cluster_default.png（默认状态）等。
...              纯渲染 + 纯像素比对：不启动 ECU/总线，秒级、可复现。
Library          ../../libraries/VisionLibrary.py
Library          OperatingSystem
Suite Setup      Create Directory    ${OUT}

*** Variables ***
${ROOT}            ${CURDIR}/../..
${GOLDEN_DIR}      ${ROOT}/simulators/golden
${OUT}             ${ROOT}/results/vision
${GOLDEN_DEFAULT}  ${GOLDEN_DIR}/cluster_default.png

*** Test Cases ***
默认状态整图应与黄金图一致
    [Documentation]    视觉回归基线：默认状态（0 km/h / 灯全灭 / P 档）与 git 里的
    ...                 黄金图一致；断言"差异比例 ≤ 默认容差 2%"，不要求逐位相等。
    ${png}=    Render Cluster State    ${{ {} }}    ${OUT}/l9_default.png
    ${ratio}=    Compare With Golden    ${png}    ${GOLDEN_DEFAULT}
    Should Be True    ${ratio} <= 0.02
    ...    msg=默认状态与黄金图差异 ${ratio}，超过 0.02 容差

车速 60 时指针角度应约 67.5 度
    [Documentation]    指针换算：扫过角度 = 车速 / 240 × 270°（60 → 67.5°）。
    ...                 Needle Angle Should Be Near 按"指针橙色像素向量拟合"识别角度。
    ${png}=    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/l9_speed60.png
    Needle Angle Should Be Near    ${png}    67.5    tolerance=2.0

水温报警灯亮灭的区域识别
    [Documentation]    同一区域"亮→lit、灭→off"成对断言，证明区域识别不是恒真：
    ...                 点亮时红色像素占比 ≥ 25%，熄灭时 ≤ 5%。
    ${state_on}=    Evaluate    {"speed": 60, "telltales": {"CoolantTempHigh": 1}}
    ${png_on}=    Render Cluster State    ${state_on}    ${OUT}/l9_alarm_on.png
    ${ratio_on}=    Telltale Region Should Be Lit    ${png_on}    CoolantTempHigh
    Should Be True    ${ratio_on} > 0.25
    ...    msg=CoolantTempHigh 点亮时红色占比 ${ratio_on}，应 > 0.25
    ${state_off}=    Evaluate    {"speed": 60}
    ${png_off}=    Render Cluster State    ${state_off}    ${OUT}/l9_alarm_off.png
    ${ratio_off}=    Telltale Region Should Be Off    ${png_off}    CoolantTempHigh
    Should Be True    ${ratio_off} <= 0.05
    ...    msg=CoolantTempHigh 熄灭时红色占比 ${ratio_off}，应 ≤ 0.05

状态变化必须能被比对检出（mutation）
    [Documentation]    视觉测试最大的坑是"比对恒真"：故意把车速改成 240 km/h，要求
    ...                 ①两图差异 ≥ 2%（灵敏度）；②与默认黄金图比对必须失败（有杀伤力）。
    ${baseline}=    Render Cluster State    ${{ {} }}    ${OUT}/l9_mutation_base.png
    ${mutated}=    Render Cluster State    ${{ {"speed": 240} }}    ${OUT}/l9_mutation_240.png
    Images Should Differ    ${mutated}    ${baseline}    min_ratio=0.02
    Run Keyword And Expect Error    *整图与黄金图不一致*
    ...    Compare With Golden    ${mutated}    ${GOLDEN_DEFAULT}
