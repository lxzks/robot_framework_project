*** Settings ***
Documentation    套件 04：ADB / Android 车机测试
...              用 adb_sim 模拟器覆盖车载 Android 测试的常用流程。
...              接真机时把 libraries/AdbLibrary.py 的 ADB_PREFIX
...              改成 ["adb"] 即可，用例代码零改动。
Resource        ../../resources/common.resource
Library          ../../libraries/AdbLibrary.py
Suite Setup      Start Vehicle Environment
Suite Teardown   Stop Vehicle Environment

*** Test Cases ***
应能检测到安卓车机设备
    [Tags]    REQ-ADB-001
    [Documentation]    adb devices 应列出模拟车机
    Device Should Be Connected    VEHICLE2026AUTO

应能读取车机系统属性
    [Tags]    REQ-ADB-001
    [Documentation]    getprop 读型号/安卓版本/车规类型
    ${model}=    Get Prop    ro.product.model
    Should Be Equal    ${model}    Visteon Cockpit P7
    ${release}=    Get Prop    ro.build.version.release
    Should Be Equal    ${release}    14
    ${vtype}=    Get Prop    ro.car.vehicle_type
    Should Be Equal    ${vtype}    passenger

应能安装并启动导航应用
    [Tags]    REQ-ADB-002
    [Documentation]    install → am start → dumpsys window 验证前台
    Install APK    com.vehicle.navi.apk
    Start Activity    com.vehicle.navi/.MapActivity
    Current Focus Should Contain    com.vehicle.navi

应能模拟点击与按键事件
    [Tags]    REQ-ADB-004
    [Documentation]    input tap / input keyevent：命令应正常执行不报错
    Tap    960    540
    Swipe    300    900    300    300    200
    Send Keyevent    3    # 3 = HOME 键，回到桌面
    Current Focus Should Contain    com.vehicle.launcher

应能读取电池状态
    [Tags]    REQ-ADB-003
    [Documentation]    dumpsys battery 解析电量百分比
    Battery Level Should Be Greater Than    50

应能卸载应用
    [Tags]    REQ-ADB-002
    [Documentation]    uninstall 后再 getprop 验证设备仍正常工作
    Uninstall Package    com.vehicle.navi
    ${model}=    Get Prop    ro.product.model
    Should Be Equal    ${model}    Visteon Cockpit P7
