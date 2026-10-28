*** Settings ***
Documentation    L9 练习：HMI 视觉断言（黄金图 / 指针角度 / 警告灯区域 / mutation）
...              目标：用 VisionLibrary 对离屏渲染出的仪表 PNG 做像素级断言。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
...              提示与关键字表见 README §三；做完对照 solution.robot。
Library          ../../libraries/VisionLibrary.py
Library          OperatingSystem
Suite Setup      Create Directory    ${OUT}

*** Variables ***
# ${CURDIR} = 本文件所在目录 training/L9_vision_assert/，项目根目录是它的上两级
${ROOT}            ${CURDIR}/../..
${GOLDEN_DIR}      ${ROOT}/simulators/golden
${OUT}             ${ROOT}/results/vision
${GOLDEN_DEFAULT}  ${GOLDEN_DIR}/cluster_default.png

*** Test Cases ***
默认状态整图应与黄金图一致
    [Documentation]    TODO：渲染默认状态（${{ {} }}）→ Compare With Golden →
    ...                断言差异比例 <= 0.02（默认容差，跨平台抗锯齿余量）。
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完

车速 60 时指针角度应约 67.5 度
    [Documentation]    TODO：渲染 ${{ {"speed": 60} }} → Needle Angle Should Be Near 67.5，容差 2°。
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

水温报警灯亮灭的区域识别
    [Documentation]    TODO：渲染 spped=60 + CoolantTempHigh=1 → Telltale Region Should Be Lit
    ...                （占比 > 0.25）；再渲染不带灯的状态 → Telltale Region Should Be Off（占比 <= 0.05）。
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完

状态变化必须能被比对检出（mutation）
    [Documentation]    TODO：渲染默认与 240 km/h 两张图 → Images Should Differ(min_ratio=0.02)；
    ...                再用 Run Keyword And Expect Error *整图与黄金图不一致* 包 Compare With Golden，
    ...                证明"比对不是恒真"。
    Fail    TODO: 删除这一行，按 README §二 用例 4 补完
