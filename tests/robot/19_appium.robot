*** Settings ***
Documentation    套件 19：Appium 真机 UI 自动化对接（W3C WebDriver + mock server）
...              业务背景：安卓车机/仪表的 UI 自动化，行业标准是 Appium——脚本通过
...              **W3C WebDriver 协议**（HTTP JSON）让 Appium Server 去操作设备：
...              找元素、点击、读文本、截图。本套件验证我们手写的 W3C 客户端 +
...              Appium Server 教学 mock（simulators/appium_mock.py，后端读到
...              adb_device_state.json）的完整协议链路。
...              与视觉测试（09 套件）的分工：视觉测试"看图"（像素比对），
...              Appium 测试"看控件树"（resource-id/text/activity）——两条路线互补。
...              设计要点：① 9 条用例是一条**有状态叙事链**（创建会话 → 点图标切界面
...              → 定位/文本断言 → 点开始导航 → 坐标操作 → 截图 → 删会话），
...              每步都是真实 HTTP 请求；② mock 后端会把点击语义落盘到
...              adb_device_state.json，所以"点击 → 界面/状态变化"是真断言；
...              ③ 换真机只改 server URL + capabilities，用例代码零改动（见 docs/37）。
...              需求追溯：REQ-APPIUM-001 会话与能力管理 / REQ-APPIUM-002 元素查找与定位 /
...              REQ-APPIUM-003 点击/文本/截图操作 / REQ-APPIUM-004 页面源码与界面切换。
Library          ../../libraries/AppiumLibrary.py
Library          OperatingSystem
Suite Setup      启动 Appium 服务
Suite Teardown   清理 Appium 环境

*** Variables ***
# Appium 端口：优先读环境变量 APPIUM_PORT（默认 4723）。
# 交叉审查修复：原来这里写死 4723，导致库/仿真器支持的 env 端口覆盖在套件层失效，
# 机器上 4723 被占用时无法换端口 —— 现在 %{APPIUM_PORT=4723} 让 env 端到端生效。
${APPIUM_PORT}     %{APPIUM_PORT=4723}
${OUT}             ${CURDIR}/../../results/dev_appium
${SCREENSHOT}      ${OUT}/appium_screenshot.png
# 会话 id 由用例 1 创建后写入；先给空值，保证 Teardown 在用例 1 失败时也不报未定义变量
${SESSION}         ${EMPTY}

*** Test Cases ***
创建 Appium 会话应返回 sessionId
    [Tags]    REQ-APPIUM-001
    [Documentation]    POST /session（W3C capabilities：platformName/deviceName/
    ...              appPackage/appActivity）应返回 sessionId；mock 会按 capabilities
    ...              把车机切到指定 Activity（等价真机 Appium 启动 App）。
    ${session}=    Create Session    platform_name=Android    device_name=VEHICLE2026AUTO
    ...    app_package=com.vehicle.launcher    app_activity=.HomeActivity
    Should Not Be Empty    ${session}
    Set Suite Variable    ${SESSION}    ${session}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.launcher/.HomeActivity

按 resource-id 点击媒体图标应切换界面
    [Tags]    REQ-APPIUM-002
    [Documentation]    resource-id 是**最稳的定位策略**（开发预留测试 id）。
    ...              点击桌面"媒体"图标 → mock 后端执行 launch 语义 → current_focus
    ...              变为 com.vehicle.media/.MainActivity → 通过 current_activity 断言。
    ${eid}=    Element Should Exist    ${SESSION}    id    com.vehicle.launcher:id/app_media
    Click Element    ${SESSION}    ${eid}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.media/.MainActivity

页面源码应包含包名 Activity 与控件文本
    [Tags]    REQ-APPIUM-004
    [Documentation]    GET /source 返回 uiautomator XML：断言包名、Activity、控件文本
    ...              都出现在源码里（等价 `adb shell uiautomator dump` + 文本断言）。
    ...              这比截图更精确：能直接断言"某个按钮/标题在不在"。
    ${source}=    Page Source Should Contain    ${SESSION}
    ...    com.vehicle.media    .MainActivity    音乐    播放列表    音量
    Should Contain    ${source}    <hierarchy
    Should Contain    ${source}    [0,0][1920,1080]

按 text(xpath) 与 accessibility id 定位元素
    [Tags]    REQ-APPIUM-002
    [Documentation]    xpath 用 `//*[@text='音乐']` 定位标题并断言文本；
    ...              accessibility id（content-desc）定位底部"主屏"按钮并点击回桌面。
    ...              三种定位策略的取舍：resource-id 最稳、accessibility id 顺带做
    ...              无障碍、xpath 最灵活但易碎（层级/文案一变就挂）。
    ${eid}=    Find Element    ${SESSION}    xpath    //*[@text='音乐']
    Element Text Should Be    ${SESSION}    ${eid}    音乐
    ${home}=    Element Should Exist    ${SESSION}    accessibility id    主屏
    Click Element    ${SESSION}    ${home}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.launcher/.HomeActivity

不存在的元素应可以断言不存在
    [Tags]    REQ-APPIUM-002
    [Documentation]    负向用例：Element Should Not Exist 应通过；直接 Find Element
    ...              则应抛 `no such element`。两者都验证"找不到元素"的语义，
    ...              防止定位逻辑写错（永远返回第一个元素）而假通过。
    Element Should Not Exist    ${SESSION}    id    com.vehicle.launcher:id/app_teleport
    Run Keyword And Expect Error    *no such element*
    ...    Find Element    ${SESSION}    id    com.vehicle.launcher:id/app_teleport

点击开始导航后页面应进入导航中
    [Tags]    REQ-APPIUM-003    REQ-APPIUM-004
    [Documentation]    界面切换 + 点击闭环：Switch To Package 直接拉起导航页，
    ...              点击"开始导航"后 mock 落盘 navi_active=1 → 新 page source 里
    ...              状态文本变为"导航中"、详情含"剩余"。
    ...              真实项目里同样的断言路径：点击 → 业务状态/页面变化。
    Switch To Package    ${SESSION}    com.vehicle.navi    .MapActivity
    ${eid}=    Element Should Exist    ${SESSION}    id    com.vehicle.navi:id/btn_start
    Click Element    ${SESSION}    ${eid}
    Page Source Should Contain    ${SESSION}    导航中    剩余

坐标 tap 与 swipe 应被服务端记录
    [Tags]    REQ-APPIUM-003
    [Documentation]    坐标级操作走 W3C Actions（pointerMove/Down/Up；旧 /touch/perform
    ...              已废弃）。mock 会记录每个操作，断言返回值和 server 端的操作列表
    ...              （类型/坐标）——证明"操作真的发到了服务端"，而不是客户端自己记的。
    ${tap}=    Tap Coordinate    ${SESSION}    960    540
    Should Be Equal As Integers    ${tap}[x]    960
    Should Be Equal As Integers    ${tap}[y]    540
    ${swipe}=    Swipe    ${SESSION}    300    900    300    300    duration_ms=200
    Should Be Equal As Integers    ${swipe}[y2]    300
    ${actions}=    Get Recorded Actions    ${SESSION}
    ${types}=    Evaluate    [item["type"] for item in $actions]
    Should Contain    ${types}    tap
    Should Contain    ${types}    swipe
    ${taps}=    Evaluate    [item for item in $actions if item["type"] == "tap"]
    Should Be Equal As Integers    ${taps}[0][x]    960

截图应返回非空 PNG 且尺寸正确
    [Tags]    REQ-APPIUM-003
    [Documentation]    GET /screenshot 返回 base64 PNG，保存成文件并校验：
    ...              ①文件存在 ②大小 > 1000 字节 ③PNG 魔数正确 ④宽高 > 0。
    ...              真实项目里截图直接进缺陷单/视觉回归基线（与 09 套件联动）。
    ${png}=    Get Screenshot    ${SESSION}    ${SCREENSHOT}
    File Should Exist    ${png}
    ${size}=    Get File Size    ${png}
    Should Be True    ${size} > 1000    msg=截图过小: ${size} 字节
    ${header}=    Evaluate    open(r"${png}", "rb").read(8).hex()
    Should Be Equal    ${header}    89504e470d0a1a0a
    ${dims}=    Evaluate    __import__("struct").unpack(">II", open(r"${png}", "rb").read(24)[16:24])
    Should Be True    ${dims}[0] > 0 and ${dims}[1] > 0
    ...    msg=截图尺寸非法: ${dims}

删除会话后再操作应报错
    [Tags]    REQ-APPIUM-001
    [Documentation]    会话生命周期边界：DELETE /session 之后所有操作都应返回
    ...              `invalid session id`（HTTP 404），客户端要把它变成可读的断言错误。
    ...              真实项目里"会话泄漏/会话过期"是常见 flaky 来源，必须显式验证。
    Delete Session    ${SESSION}
    Run Keyword And Expect Error    *invalid session*
    ...    Get Page Source    ${SESSION}
    Set Suite Variable    ${SESSION}    ${EMPTY}

*** Keywords ***
启动 Appium 服务
    [Documentation]    Suite Setup：创建产物目录并启动 Appium Server（mock 子进程），
    ...              若本机已有真 Appium 在跑则直接复用（真机模式）。
    Create Directory    ${OUT}
    Start Appium Server    port=${APPIUM_PORT}

清理 Appium 环境
    [Documentation]    Suite Teardown：删除会话（若还在）+ 停止 mock 子进程。
    ...              用 Run Keyword And Ignore Error 兜底，保证任何一步失败都不会
    ...              把"端口/进程残留"留给下一个套件。
    Run Keyword And Ignore Error    Delete Session    ${SESSION}
    Stop Appium Server
