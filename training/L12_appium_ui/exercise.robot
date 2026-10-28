*** Settings ***
Documentation    L12 练习：Appium UI 自动化（session / 元素定位 / 点击 / 文本 / 截图）
...              目标：用 AppiumLibrary（W3C WebDriver 协议）操作车机 UI。
...              每个用例先用 Fail 占位（空跑会失败），按 README §二 把 TODO 换成正确步骤。
...              提示与关键字表见 README §三；做完对照 solution.robot。
...              注意：所有操作关键字第一个参数都是 sessionId（${SESSION} 由 Test Setup 创建）。
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
    [Documentation]    TODO：${SID} 由 Test Setup 创建；用 Get Current Activity 断言
    ...                当前在 com.vehicle.launcher/.HomeActivity。
    Fail    TODO: 删除这一行，按 README §二 用例 1 补完

页面源码应包含包名与桌面控件
    [Documentation]    TODO：${src}= Page Source Should Contain ${SESSION} com.vehicle.launcher 媒体
    ...                （并断言 XML 根节点 <hierarchy 存在）。
    Fail    TODO: 删除这一行，按 README §二 用例 2 补完

按 resource-id 点击媒体图标应切换界面
    [Documentation]    TODO：Element Should Exist ${SESSION} id com.vehicle.launcher:id/app_media →
    ...                Click Element ${SESSION} ${eid} → Get Current Activity 应为 com.vehicle.media/.MainActivity
    Fail    TODO: 删除这一行，按 README §二 用例 3 补完

按 xpath 定位并断言文本
    [Documentation]    TODO：Switch To Package ${SESSION} com.vehicle.media .MainActivity →
    ...                Find Element xpath //*[@text='音乐'] → Element Text Should Be ... 音乐；
    ...                再 Element Should Not Exist 一个不存在的元素。
    Fail    TODO: 删除这一行，按 README §二 用例 4 补完

截图应成功且非空 PNG
    [Documentation]    TODO：${png}= Get Screenshot ${SESSION} ${SCREENSHOT} → File Should Exist →
    ...                Get File Size > 1000 → 校验 PNG 魔数。
    Fail    TODO: 删除这一行，按 README §二 用例 5 补完

*** Keywords ***
启动 Appium 服务
    [Documentation]    Suite Setup（已写好，不用改）：准备产物目录 + 启动 mock server。
    Create Directory    ${OUT}
    Start Appium Server

清理 Appium 环境
    [Documentation]    Suite Teardown（已写好，不用改）：兜底删会话 + 停 mock 子进程。
    Run Keyword And Ignore Error    Delete Session    ${SESSION}
    Stop Appium Server

创建会话并回到桌面
    [Documentation]    Test Setup（已写好，不用改）：每条用例从桌面干净状态开始。
    ${sid}=    Create Session    platform_name=Android    device_name=VEHICLE2026AUTO
    ...    app_package=com.vehicle.launcher    app_activity=.HomeActivity
    Set Suite Variable    ${SESSION}    ${sid}
