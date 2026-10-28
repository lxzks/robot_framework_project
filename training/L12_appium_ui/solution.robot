*** Settings ***
Documentation    L12 参考答案：Appium UI 自动化（session / 元素定位 / 点击 / 文本 / 截图）
...              被测对象：simulators/appium_mock.py（W3C WebDriver 子集，HTTP 4723）
...              后端：adb_device_state.json（6 个车机界面）；换真机只改 server URL + capabilities。
...              所有操作关键字第一个参数都是 sessionId（${SESSION} 由 Test Setup 创建）。
Library          ../../libraries/AppiumLibrary.py
Library          OperatingSystem
Suite Setup      启动 Appium 服务
Suite Teardown   清理 Appium 环境
Test Setup       创建会话并回到桌面
Test Teardown    Run Keyword And Ignore Error    Delete Session    ${SESSION}

*** Variables ***
${OUT}            ${CURDIR}/../../results/appium
${SCREENSHOT}     ${OUT}/l12_screenshot.png
${SESSION}        ${EMPTY}

*** Test Cases ***
创建会话并断言当前 Activity
    [Documentation]    capabilities 里的 appPackage/appActivity 会让 mock 切到指定界面
    ...                （等价真机 Appium 启动 App）；用 current_activity 断言会话真的生效。
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.launcher/.HomeActivity

页面源码应包含包名与桌面控件
    [Documentation]    GET /source 返回 uiautomator XML：断言包名 + 控件文本都在源码里。
    ...                这比截图更精确：能直接断言"某个按钮/标题在不在"。
    ${source}=    Page Source Should Contain    ${SESSION}
    ...    com.vehicle.launcher    媒体    导航    电话
    Should Contain    ${source}    <hierarchy

按 resource-id 点击媒体图标应切换界面
    [Documentation]    resource-id 是最稳的定位策略；点击桌面"媒体"图标 → mock 执行
    ...                launch 语义 → current_focus 变为 com.vehicle.media/.MainActivity。
    ${eid}=    Element Should Exist    ${SESSION}    id    com.vehicle.launcher:id/app_media
    Click Element    ${SESSION}    ${eid}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.media/.MainActivity
    Page Source Should Contain    ${SESSION}    com.vehicle.media    音乐

按 xpath 定位并断言文本
    [Documentation]    Switch To Package 直接拉起媒体页（真实项目跨应用场景常用），
    ...                xpath 按 text 定位标题并断言文本；再验证"不存在的元素"负向断言。
    Switch To Package    ${SESSION}    com.vehicle.media    .MainActivity
    ${eid}=    Find Element    ${SESSION}    xpath    //*[@text='音乐']
    Element Text Should Be    ${SESSION}    ${eid}    音乐
    Element Should Not Exist    ${SESSION}    id    com.vehicle.media:id/definitely_missing

截图应成功且非空 PNG
    [Documentation]    截图不是"命令没报错"就算成功：要校验文件存在、大小 > 1000、
    ...                PNG 魔数正确（Get Screenshot 内部也会校验尺寸 > 0）。
    ${png}=    Get Screenshot    ${SESSION}    ${SCREENSHOT}
    File Should Exist    ${png}
    ${size}=    Get File Size    ${png}
    Should Be True    ${size} > 1000    msg=截图过小: ${size} 字节
    ${is_png}=    Evaluate    open($png, "rb").read(8).hex() == "89504e470d0a1a0a"
    Should Be True    ${is_png}    msg=截图不是 PNG 文件

*** Keywords ***
启动 Appium 服务
    [Documentation]    Suite Setup：准备产物目录 + 启动 Appium mock 子进程。
    Create Directory    ${OUT}
    Start Appium Server

清理 Appium 环境
    [Documentation]    Suite Teardown：兜底删会话 + 停 mock 子进程（防止端口残留）。
    Run Keyword And Ignore Error    Delete Session    ${SESSION}
    Stop Appium Server

创建会话并回到桌面
    [Documentation]    Test Setup：每条用例从"桌面"干净状态开始（用例间状态隔离）。
    ${sid}=    Create Session    platform_name=Android    device_name=VEHICLE2026AUTO
    ...    app_package=com.vehicle.launcher    app_activity=.HomeActivity
    Set Suite Variable    ${SESSION}    ${sid}
