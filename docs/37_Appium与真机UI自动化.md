# 37 · Appium 与真机 UI 自动化：用 W3C WebDriver 操作车机

> **前面 09/25 章验证的是"仪表盘显示"**（状态 JSON + 像素比对）。本章补上第三块：
> **安卓车机/中控屏本身的 UI 自动化** —— 点哪个图标、在哪个界面、控件文本是什么、
> 当前 Activity 是哪个。行业标准工具就是 **Appium**。
>
> 本章不装 Appium、也不需要安卓真机：我们自己实现 **Appium Server 的 W3C WebDriver
> 协议子集**（`simulators/appium_mock.py`）+ 一个**标准库 urllib 客户端**
> （`libraries/AppiumLibrary.py`），把"协议"这一层彻底跑通。**换真机时只改
> server URL + capabilities，用例代码一行不用改。**
>
> ```bash
> # 本模块三件套（全部新增，不改现有文件）
> simulators/appium_mock.py          # Appium Server 教学 mock（标准库 http.server，W3C 子集）
> libraries/AppiumLibrary.py         # 18 个 RF 关键字（urllib 手写 W3C 客户端）
> tests/robot/19_appium.robot        # 9 个用例（9/9 全绿，连跑两次一致，约 3 秒）
>
> # 跑本章套件（自动拉起 mock 子进程，不需要 ECU/安卓设备）
> .\.venv\Scripts\python.exe -m robot --outputdir results/dev_appium tests\robot\19_appium.robot
>
> # 手工起 mock 服务探活（另开终端）
> .\.venv\Scripts\python.exe simulators/appium_mock.py --port 4723
> curl http://127.0.0.1:4723/status
> ```

---

## 📖 零基础读法建议（先看这个）

| 优先级 | 小节 | 说明 |
|---|---|---|
| 🟢 **必读** | 37.1 / 37.2 | Appium 在车载测试中的位置、W3C 协议长什么样（面试高频） |
| 🟢 **必读** | 37.3 / 37.4 | capabilities 与三种定位策略的取舍——写脚本天天用 |
| 🟢 **必读** | 37.5 / 37.6 | 真机连接步骤 + 本项目的 mock/真机替换点（最实用的工程知识） |
| 🟢 **必读** | 37.7 | 9 条用例逐条讲，对着 `19_appium.robot` 看 |
| 🟡 了解 | 37.8 | 真实项目的坑（多窗口/弹窗/时延）——面试能说出来就加分 |
| ⚪ 可跳过 | 37.2.3 的 element key 长字符串 | 记不住没关系，知道"有个 W3C 元素 key"即可 |

**动手顺序**：先跑 19 套件看 9/9 全绿 → 命令行起 mock 用 curl 走一遍协议 →
打开 `results/dev_appium/appium_screenshot.png` 看截图 → 最后做 37.9 自查题。

---

## 37.1 Appium 在车载 HMI 测试中的位置

车载 UI 测试有三条互补路线，本章是第三条：

| 路线 | 章节 | 断言对象 | 擅长抓的缺陷 | 稳定性 | 速度 |
|---|---|---|---|---|---|
| **状态外置** | 09 | 显示状态 JSON | 信号→显示映射、阈值、状态机 | 极高 | 毫秒 |
| **视觉比对** | 25 | 屏幕像素 | 布局/配色/指针角度/警告灯没画出来 | 高（需容差） | 几十 ms |
| **Appium 控件树** | 本章 | uiautomator XML（控件/文本/Activity） | 点错界面、控件缺失、文本错误、导航路径断 | 高（控件树比像素稳） | 百毫秒~秒 |

**一句话选型**：

```
逻辑对不对 → 状态断言
画面对不对 → 视觉比对
点得对不对 / 界面跳没跳 / 控件在不在 → Appium（控件树）
```

> 真实项目对应：车机（Android Automotive）的 HMI 验收一般三层都做：
> ① Appium/uiautomator2 做 UI 功能自动化；② 截图比对做视觉回归；
> ③ 车机背后用 CAN/诊断断言业务状态。**本章 + 09 + 25 章就是这三层的最小实现。**

---

## 37.2 W3C WebDriver 协议：Appium 到底在忙什么

### 37.2.1 一句话原理

Appium 是**协议翻译官**：把 W3C WebDriver 的 HTTP JSON 请求，翻译成设备上的
UI 操作（Android = 通过 uiautomator2 server + adb；iOS = XCUITest）：

```
脚本/库  ──HTTP JSON──►  Appium Server  ──adb/uiautomator2──►  车机
   (本章 AppiumLibrary)      (本章 appium_mock.py)             (本章 adb_sim 状态文件)
```

**协议是标准的**：只要客户端说 W3C，服务端可以是 Appium、mock、甚至自己写的测试替身。
这就是"mock 也能练真本事"的原因——**练的是协议和思路，不是某个工具的按钮位置**。

### 37.2.2 请求/响应格式

所有请求都是 JSON over HTTP；所有响应统一是 `{"value": ...}`：

```jsonc
// POST /session —— 创建会话（W3C capabilities）
{
  "capabilities": {
    "alwaysMatch": {
      "platformName": "Android",
      "deviceName": "VEHICLE2026AUTO",
      "appPackage": "com.vehicle.launcher",
      "appActivity": ".HomeActivity"
    },
    "firstMatch": [{}]
  }
}
// 200 OK
{"value": {"sessionId": "appium-mock-db95bf78daa1", "capabilities": {...}}}
```

```jsonc
// POST /session/{id}/element —— 查找元素
{"using": "id", "value": "com.vehicle.navi:id/btn_start"}
// 200 OK：value 是"元素对象"，key 是 W3C 规定的长字符串（记住有这回事即可）
{"value": {"element-6066-11e4-a52e-4f735466cecf": "element-4b8acd3717",
           "ELEMENT": "element-4b8acd3717"}}
```

```jsonc
// 错误响应也带 value（有 error code + message）
{"value": {"error": "no such element",
           "message": "An element could not be located using id='...'",
           "stacktrace": ""}}
```

### 37.2.3 本章用到的端点（W3C 子集）

| 方法 & 路径 | 作用 | 典型客户端关键字 |
|---|---|---|
| `GET /status` | 服务就绪探测 | `Start Appium Server` 内部轮询 |
| `POST /session` | 创建会话 | `Create Session` |
| `DELETE /session/{id}` | 删除会话 | `Delete Session` |
| `POST /session/{id}/element` | 查找元素 | `Find Element` |
| `POST /session/{id}/element/{eid}/click` | 点击 | `Click Element` |
| `GET /session/{id}/element/{eid}/text` | 读文本 | `Get Element Text` |
| `POST /session/{id}/actions` | W3C Actions（tap/swipe） | `Tap Coordinate` / `Swipe` |
| `GET /session/{id}/source` | 页面源码（uiautomator XML） | `Get Page Source` |
| `GET /session/{id}/screenshot` | 截图（base64 PNG） | `Get Screenshot` |
| `GET /session/{id}/appium/device/current_activity` | 当前 Activity | `Get Current Activity` |
| `POST /session/{id}/appium/device/start_activity` | 启动指定 App/Activity | `Switch To Package` |

> ⚠️ **坐标操作必须走 W3C Actions**：老 Appium 的 `/touch/perform` 在 uiautomator2
> （API 21+）已废弃。`actions` 里用 pointerMove + pointerDown + pointerUp 表达点击，
> 用两段 pointerMove 表达滑动 —— 真机和 mock 都吃这套。

---

## 37.3 capabilities：告诉 Appium"操作哪台设备、启动哪个 App"

| capability | 示例 | 说明 |
|---|---|---|
| `platformName` | `Android` | 平台（Android/iOS）。**必填**，mock 也只接受 Android |
| `deviceName` | `VEHICLE2026AUTO` | 设备名/序列号（对应 `adb devices` 的 serial） |
| `appPackage` | `com.vehicle.navi` | 要启动的应用包名 |
| `appActivity` | `.MapActivity` | 要启动的 Activity（相对包名或全名） |
| `automationName` | `UiAutomator2` | 驱动（真机必填；mock 忽略） |
| `noReset` | `True` | 不重置 App 状态（回归测试常用，避免每条用例重装） |
| `newCommandTimeout` | `120` | 客户端空闲多久后 Appium 回收会话 |

**W3C 合并规则**：`alwaysMatch` 是"所有候选都必须满足"的公共能力；
`firstMatch` 是"备选组合列表"（按顺序尝试，第一个能创建的胜出）。
本项目只发 `alwaysMatch` + 空 `firstMatch`，等价"就这一组能力，不行就报错"。

> 🎯 **面试话术**："capabilities 是 Appium 的'设备+应用选择器'，W3C 规定用
> alwaysMatch/firstMatch 表达能力组合；我会把 appPackage/appActivity 显式写死，
> 避免 Appium 自动判断启动哪个 Activity 造成不确定性。"

---

## 37.4 元素定位策略：resource-id / accessibility id / xpath

Appium 找控件的本质是"在 uiautomator XML 里匹配一个节点"。三种主流的取舍：

| 策略 | `using` | 示例 | 稳定性 | 适用 |
|---|---|---|---|---|
| **resource-id** | `id` | `com.vehicle.navi:id/btn_start` | ⭐⭐⭐⭐⭐ | 开发预留测试 id —— **首选** |
| **accessibility id** | `accessibility id` | `主屏`（content-desc） | ⭐⭐⭐⭐ | 有无障碍标注的按钮/图标；顺带做无障碍验收 |
| **xpath** | `xpath` | `//*[@text='开始导航']` | ⭐⭐ | 兜底：无 id 时按文本/层级找；文案/层级一变就挂 |

**uiautomator XML 长什么样**（本项目 mock 实际返回，节选）：

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.vehicle.navi" content-desc="" clickable="false"
        enabled="true" bounds="[0,0][1920,1080]" activity="com.vehicle.navi/.MapActivity">
    <node index="0" text="导航" resource-id="com.vehicle.navi:id/title"
          class="android.widget.TextView" bounds="[60,100][1200,180]"/>
    <node index="1" text="开始导航" resource-id="com.vehicle.navi:id/btn_start"
          class="android.widget.Button" clickable="true" bounds="[100,380][360,460]"/>
    ...
  </node>
</hierarchy>
```

定位策略的工程建议：

1. **和开发约定测试 id**（`android:id`/`resource-id`），写进规范 —— 这是 Appium 用例
   稳定性的第一因素；没有 id 的按钮，xpath 迟早会挂；
2. xpath 优先用 `//*[@resource-id='...']` 或 `//*[@text='...']` 这种**属性精确匹配**，
   少写绝对路径（`/hierarchy/node[2]/node[1]`）；
3. 文案相关断言（"按钮文字对不对"）才用 text，**不要用 text 当定位键**（多语言/改文案就废）；
4. 找不到元素先看"是不是还没加载完"（隐式等待），而不是疯狂 sleep。

> 本项目 mock 的 xpath 是**教学子集**：只支持 `//*[@属性='值']` 这类属性等值匹配
> （真 Appium 支持完整 XPath 1.0：层级、位置、函数）。子集足够练"定位策略选择"，
> 换成真 Appium 后同样的 `//*[@text='音乐']` 照样能用。

---

## 37.5 真机连接步骤（从 USB 到第一条脚本）

真实项目里把本章用例跑在真车机上，按这个顺序：

```bash
# ① 车机/手机开启开发者选项 + USB 调试，连上测试机（或 adb over Wi-Fi）
adb devices                          # 应看到设备序列号（如 VEHICLE2026AUTO  device）
adb shell getprop ro.build.version.release   # 确认 Android 版本

# ② 安装并启动 Appium Server（Node.js 环境，全局安装 Appium 2）
npm install -g appium
appium driver install uiautomator2   # 首次安装 uiautomator2 驱动
appium --port 4723 --base-path /wd/hub

# ③ 探活（浏览器/curl 均可）
curl http://127.0.0.1:4723/status   # 应返回 {"value":{"ready":true,...}}

# ④ 把测试指向真 Appium（本项目的替换点）
#    - 方式 A：环境变量 APPIUM_SERVER_URL=http://127.0.0.1:4723
#    - 方式 B：Robot 里 Start Appium Server 传 server_url=http://127.0.0.1:4723
#    - 方式 C：库初始化 AppiumLibrary(server_url=...)
#    用例代码、断言、关键字一律不用改。

# ⑤ capabilities 换成真机参数后跑：
#    platformName=Android / deviceName=<真机 serial> / appPackage=... / appActivity=...
.\.venv\Scripts\python.exe -m robot --outputdir results/appium_real tests\robot\19_appium.robot
```

> 真机上 Appium 首次会话会**自动往设备装两个 APK**（`io.appium.uiautomator2.server`
> 和 `...server.test`），过程可能要几十秒 → capabilities 里给足超时
> （`uiautomator2ServerInstallTimeout`、`adbExecTimeout`），CI 里把这步做进镜像/预热。

### 37.5.1 本项目 mock 与真机的"替换点"对照

| 能力 | 本项目（mock） | 真实项目（真机） | 替换动作 |
|---|---|---|---|
| Appium Server | `simulators/appium_mock.py`（标准库 HTTP） | `appium --port 4723`（Node.js） | 改 `APPIUM_SERVER_URL` / `server_url=` |
| 设备 | `adb_device_state.json` 模拟状态 | 真车机/Android 模拟器 | `deviceName` 改成 `adb devices` 的 serial |
| 页面树 | mock 按 6 个界面生成 XML | uiautomator2 从真机 dump | 无（协议一样） |
| 截图 | mock 生成确定性 PNG（480x800） | 设备真实屏幕分辨率（如 1920x1080） | 无（断言尺寸时按真机分辨率改） |
| 点击副作用 | 写 `adb_device_state.json` | 真机 UI 真的变化 | 无（断言对象变成真界面） |
| `Get Recorded Actions` | mock 调试端点 | **真 Appium 没有**；用 logcat/页面断言代替 | 只在 mock 用例里用 |

---

## 37.6 本项目实现

### 37.6.1 数据流与文件

```
tests/robot/19_appium.robot
        │  （Library）
libraries/AppiumLibrary.py ──HTTP JSON（urllib）──► simulators/appium_mock.py
        │                                               │  HTTP 127.0.0.1:4723
        │                                               │  （--port / APPIUM_PORT 可配）
        └───────────────────────────────────────────────┴──► simulators/adb_device_state.json
                                                              （adb_sim 维护；缺失用默认值）
```

### 37.6.2 `simulators/appium_mock.py` 架构

| 模块 | 内容 |
|---|---|
| HTTP 层 | `ThreadingHTTPServer` + `BaseHTTPRequestHandler`；解析路由/JSON 请求体，统一 W3C 响应/错误格式 |
| 会话层 | `AppiumMockServer.sessions`（sessionId → capabilities/elements/actions）；DELETE 后访问返回 `invalid session id` |
| 页面层 | 6 个界面（桌面/媒体/导航/电话/空调/设置）的 UI 树构建器，数据来自 `adb_device_state.json` 的 `current_focus` + `ivi`；序列化成 uiautomator XML（含 package/activity/bounds） |
| 定位层 | `find_node()` 支持 `id` / `accessibility id` / `xpath`（属性等值子集）/ `text` / `class name` |
| 动作层 | `apply_action()`：点桌面图标 = `am start` 切界面；点"开始导航" = `navi_active=1`；点媒体播放 = `media_status` 切换……并把动作写回状态文件 + 在 `logs` 里留记录 |
| 截图 | `make_screenshot_png()`：**纯标准库**生成确定性 PNG（界面主题色 + 渐变 + 伪纹理，480x800），不依赖字体/Qt |
| 调试端点 | `GET /session/{id}/appium/mock/actions` 返回已记录操作（mock 专用） |

**mock 的 6 个界面与主要控件**（与 `android_gui.py` 的页面一一对应）：

| 界面 | Activity | 主要控件（resource-id 尾段 / text） |
|---|---|---|
| 桌面 | `com.vehicle.launcher/.HomeActivity` | `app_navi`/`app_media`/`app_phone`/`app_hvac`/`app_settings`（图标按钮） |
| 媒体 | `com.vehicle.media/.MainActivity` | `btn_prev`/`btn_play`/`btn_next`、`volume`、`track_0..5`、文本"音乐/播放列表" |
| 导航 | `com.vehicle.navi/.MapActivity` | `btn_start`（开始导航）、`btn_stop`、`status`、`detail`、`dest_0..5` |
| 电话 | `com.vehicle.phone/.DialerActivity` | `call_status`、`caller`、`btn_hangup`、`contact_0..3` |
| 空调 | `com.vehicle.hvac/.HvacActivity` | `temp`、`temp_down`/`temp_up`、`fan`、`fan_0..3` |
| 设置 | `com.vehicle.settings/.SettingsActivity` | `setting_0..N`（props 列表） |

### 37.6.3 `libraries/AppiumLibrary.py` 关键字表（18 个）

| # | 关键字 | 作用 | 对应 W3C 端点 |
|---|---|---|---|
| 1 | `Start Appium Server` | 探测/启动 server（有真 Appium 就复用） | `GET /status` |
| 2 | `Stop Appium Server` | 停止 mock 子进程（terminate→kill 兜底） | — |
| 3 | `Create Session` | 创建会话，返回 sessionId | `POST /session` |
| 4 | `Delete Session` | 删除会话 | `DELETE /session/{id}` |
| 5 | `Find Element` | 按策略查找元素，返回 elementId | `POST .../element` |
| 6 | `Element Should Exist` | 断言存在 | 同上 |
| 7 | `Element Should Not Exist` | 断言不存在（负向） | 同上 |
| 8 | `Click Element` | 点击元素 | `POST .../element/{eid}/click` |
| 9 | `Get Element Text` | 读元素文本 | `GET .../element/{eid}/text` |
| 10 | `Element Text Should Be` | 断言文本 | 同上 |
| 11 | `Tap Coordinate` | 坐标点击（W3C Actions） | `POST .../actions` |
| 12 | `Swipe` | 滑动（W3C Actions） | `POST .../actions` |
| 13 | `Get Page Source` | 取 uiautomator XML | `GET .../source` |
| 14 | `Page Source Should Contain` | 断言源码含若干子串 | 同上 |
| 15 | `Switch To Package` | 拉起指定包/Activity 并断言 | `POST .../appium/device/start_activity` |
| 16 | `Get Current Activity` | 当前前台 Activity | `GET .../appium/device/current_activity` |
| 17 | `Get Screenshot` | 截图存 PNG 并校验魔数/尺寸 | `GET .../screenshot` |
| 18 | `Get Recorded Actions` | 读 mock 记录的操作（mock 专用） | `GET .../appium/mock/actions` |

> 客户端的两个关键质量点：① 默认 **探测已有 server 再决定是否启动 mock**（真机/CI 两用）；
> ② 所有 HTTP 错误都转成带 `error/message` 的 AssertionError —— **错误信息质量也是被测对象**。

---

## 37.7 逐条讲 `tests/robot/19_appium.robot`（9 条用例）

**套件设计**：9 条用例是一条**有状态叙事链**（Appium 本来就有状态：会话/界面/焦点）。
Suite Setup 启动 mock server；用例 1 创建会话；2~8 顺着"桌面 → 媒体 → 桌面 → 导航"
的操作流验证协议能力；用例 9 删除会话并验证边界。**全套约 3 秒。**

```robotframework
*** Settings ***
Library          ../../libraries/AppiumLibrary.py
Library          OperatingSystem
Suite Setup      启动 Appium 服务
Suite Teardown   清理 Appium 环境
```

| # | 用例 | 需求标签 | 验证点 |
|---|---|---|---|
| 1 | 创建 Appium 会话应返回 sessionId | REQ-APPIUM-001 | caps → sessionId、Activity 生效 |
| 2 | 按 resource-id 点击媒体图标应切换界面 | REQ-APPIUM-002 | id 定位 + 点击 + Activity 变化 |
| 3 | 页面源码应包含包名 Activity 与控件文本 | REQ-APPIUM-004 | source 断言包名/Activity/文本 |
| 4 | 按 text(xpath) 与 accessibility id 定位元素 | REQ-APPIUM-002 | 另两种定位策略 + 文本断言 |
| 5 | 不存在的元素应可以断言不存在 | REQ-APPIUM-002 | 负向：no such element |
| 6 | 点击开始导航后页面应进入导航中 | REQ-APPIUM-003/004 | 界面切换 + 点击闭环 |
| 7 | 坐标 tap 与 swipe 应被服务端记录 | REQ-APPIUM-003 | W3C Actions + 服务端记录 |
| 8 | 截图应返回非空 PNG 且尺寸正确 | REQ-APPIUM-003 | base64→PNG 校验 |
| 9 | 删除会话后再操作应报错 | REQ-APPIUM-001 | invalid session id 边界 |

### 37.7.1 用例 1：创建会话（caps 生效）

```robotframework
    ${session}=    Create Session    platform_name=Android    device_name=VEHICLE2026AUTO
    ...    app_package=com.vehicle.launcher    app_activity=.HomeActivity
    Should Not Be Empty    ${session}
    Set Suite Variable    ${SESSION}    ${session}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.launcher/.HomeActivity
```

**测什么**：W3C `POST /session` 能建会话；capabilities 里的 appPackage/appActivity 让 mock
切到桌面（真机上等价于 Appium 启动 App）。实测 sessionId 形如
`appium-mock-db95bf78daa1`，当前 Activity = `com.vehicle.launcher/.HomeActivity`。

### 37.7.2 用例 2：resource-id 定位 + 点击切换界面

```robotframework
    ${eid}=    Element Should Exist    ${SESSION}    id    com.vehicle.launcher:id/app_media
    Click Element    ${SESSION}    ${eid}
    ${activity}=    Get Current Activity    ${SESSION}
    Should Be Equal    ${activity}    com.vehicle.media/.MainActivity
```

**测什么**：`Find Element(id)` → `Click` → 后端执行 launch 语义 → **当前 Activity 改变**。
这是 UI 自动化的最小闭环：**定位 → 操作 → 断言结果**，每一步都走真实 HTTP。

### 37.7.3 用例 3：页面源码断言

```robotframework
    ${source}=    Page Source Should Contain    ${SESSION}
    ...    com.vehicle.media    .MainActivity    音乐    播放列表    音量
    Should Contain    ${source}    <hierarchy
    Should Contain    ${source}    [0,0][1920,1080]
```

**测什么**：不点按也能断言界面状态。实测媒体页源码 **6861 字符**；根节点带
`package`/`activity` 属性、bounds 是 1920x1080 全屏。真实项目里 `Page Source Should
Contain` 是"页面加载完成/关键控件出现"的最常用断言。

### 37.7.4 用例 4：xpath 与 accessibility id

```robotframework
    ${eid}=    Find Element    ${SESSION}    xpath    //*[@text='音乐']
    Element Text Should Be    ${SESSION}    ${eid}    音乐
    ${home}=    Element Should Exist    ${SESSION}    accessibility id    主屏
    Click Element    ${SESSION}    ${home}
```

**测什么**：三种定位策略都通。xpath 按文本找到"音乐"标题并校验文本；
accessibility id 找到系统栏"主屏"按钮（content-desc）并点击回桌面。
**定位策略覆盖率本身就是一条需求**（REQ-APPIUM-002）。

### 37.7.5 用例 5：负向——元素不存在

```robotframework
    Element Should Not Exist    ${SESSION}    id    com.vehicle.launcher:id/app_teleport
    Run Keyword And Expect Error    *no such element*
    ...    Find Element    ${SESSION}    id    com.vehicle.launcher:id/app_teleport
```

**测什么**：`Element Should Not Exist` 通过 + 直接 `Find Element` 抛 `no such element`。
**负向用例证明定位逻辑不是恒真**（返回第一个元素的 bug 会被这条抓住）。

### 37.7.6 用例 6：点击"开始导航"→ 界面进入"导航中"

```robotframework
    Switch To Package    ${SESSION}    com.vehicle.navi    .MapActivity
    ${eid}=    Element Should Exist    ${SESSION}    id    com.vehicle.navi:id/btn_start
    Click Element    ${SESSION}    ${eid}
    Page Source Should Contain    ${SESSION}    导航中    剩余
```

**测什么**：`start_activity` 拉起导航页（不依赖"回桌面再点图标"的路径），点击
"开始导航"后 mock 落盘 `navi_active=1`，新 page source 状态文本变为"导航中"、
详情含"剩余 1500 米"。**点击有真实副作用**——这是 Appium 测试与"假点击"的根本区别。

### 37.7.7 用例 7：W3C Actions（tap / swipe）

```robotframework
    ${tap}=    Tap Coordinate    ${SESSION}    960    540
    Should Be Equal As Integers    ${tap}[x]    960
    ${swipe}=    Swipe    ${SESSION}    300    900    300    300    duration_ms=200
    Should Be Equal As Integers    ${swipe}[y2]    300
    ${actions}=    Get Recorded Actions    ${SESSION}
    ${types}=    Evaluate    [item["type"] for item in $actions]
    Should Contain    ${types}    tap
    Should Contain    ${types}    swipe
```

**测什么**：坐标级操作走 W3C Actions；mock 服务端记录到 5 个操作
（3 次 click + tap + swipe），并校验 tap 的坐标 960/540 —— **证明操作真的发到了服务端**，
不是客户端自己编的。真实项目里"点击没生效"最常见原因就是坐标/元素选错，这条能定位。

### 37.7.8 用例 8：截图（base64 → PNG 校验）

```robotframework
    ${png}=    Get Screenshot    ${SESSION}    ${SCREENSHOT}
    File Should Exist    ${png}
    ${size}=    Get File Size    ${png}
    Should Be True    ${size} > 1000
    ${header}=    Evaluate    open(r"${png}", "rb").read(8).hex()
    Should Be Equal    ${header}    89504e470d0a1a0a      # PNG 魔数
    ${dims}=    Evaluate    __import__("struct").unpack(">II", open(r"${png}","rb").read(24)[16:24])
    Should Be True    ${dims}[0] > 0 and ${dims}[1] > 0
```

**测什么**：截图链路完整：Appium 返回 base64 → 解码 → 校验 PNG 魔数/宽高 → 落盘。
实测 **152,394 字节、480x800**。截图是失败证据链的一环（可与 25 章视觉比对联动）。

### 37.7.9 用例 9：删除会话后的边界

```robotframework
    Delete Session    ${SESSION}
    Run Keyword And Expect Error    *invalid session*
    ...    Get Page Source    ${SESSION}
```

**测什么**：`DELETE /session` 之后所有操作返回 `invalid session id`（HTTP 404）。
会话泄漏/过期是 UI 自动化常见 flaky 来源，**必须显式验证**。

### 37.7.10 实测结果

```
==============================================================================
19 Appium :: 套件 19：Appium 真机 UI 自动化对接（W3C WebDriver + mock server）
==============================================================================
创建 Appium 会话应返回 sessionId                          | PASS |
按 resource-id 点击媒体图标应切换界面                       | PASS |
页面源码应包含包名 Activity 与控件文本                      | PASS |
按 text(xpath) 与 accessibility id 定位元素                | PASS |
不存在的元素应可以断言不存在                                | PASS |
点击开始导航后页面应进入导航中                              | PASS |
坐标 tap 与 swipe 应被服务端记录                           | PASS |
截图应返回非空 PNG 且尺寸正确                               | PASS |
删除会话后再操作应报错                                      | PASS |
------------------------------------------------------------------------------
9 tests, 9 passed, 0 failed    （连跑两次均 9/9；单次约 3.0 秒）
```

---

## 37.8 真实项目的坑（Appium 上车机）

| 坑 | 现象 | 处理 |
|---|---|---|
| **多窗口/浮层** | 弹窗、悬浮球、系统权限框抢焦点，find 到的是别的窗口 | `driver.switch_to.window()` / `contexts`；先断言/关闭弹窗再操作；uiautomator2 的 `windows` 集合 |
| **系统弹窗** | "允许 USB 调试？""电量低"挡在前面，用例随机失败 | 用例 Setup 里统一处理已知弹窗（`accept alert`/点"允许"）；CI 里预授权 |
| **元素找不到 ≠ 不存在** | 页面还在加载就 find，抛 no such element | 用**隐式等待**（`appium:implicitWaitTimeout`）或显式等待（轮询直到出现）；别用固定 sleep |
| **stale element** | 界面刷新后旧 elementId 失效 | 一次操作后重新 find；或封装"find→click"原子关键字 |
| **Activity 切换时延** | 点完图标立刻断言界面，偶尔还在旧页面 | 等待 Activity 变化（轮询 `current_activity`）或等待目标控件出现 |
| **真机时延/网络 adb** | `adb connect` 掉线、USB 松动，会话中断 | CI 用有线/网络链路监控；设置 `adbExecTimeout`；失败先看 `adb devices` |
| **分辨率/语言差异** | 坐标写死、xpath 用中文文案 → 换车型/语言就废 | 优先 resource-id；坐标按分辨率换算；文案断言与定位分离 |
| **首次安装驱动** | 第一次会话卡几十秒（装 uiautomator2 server） | 镜像预热/CI 缓存；`noReset=True` 减少重装 |

> **面试话术**："Appium 上车机最难的不是协议，是**等待与窗口管理**：
> 我会用隐式等待+轮询 Activity/控件替代 sleep，并在 Setup 里统一清理系统弹窗，
> 再把失败截图和 page source 存档，避免'偶发失败'变成无头案。"

---

## 37.9 本章自检

1. Appium 的 W3C 协议里，一次"创建会话 + 找元素 + 点击"分别是什么 HTTP 请求？
2. capabilities 的 alwaysMatch / firstMatch 有什么区别？appPackage 和 appActivity 各起什么作用？
3. resource-id / accessibility id / xpath 三种定位策略，稳定性为什么不同？你首选哪个，为什么？
4. 为什么坐标点击必须用 W3C Actions？旧的 `/touch/perform` 有什么问题？
5. 从零把本章用例跑在真车机上，需要哪些步骤？**只需改哪两个东西**？
6. "元素找不到"的常见原因有哪些？为什么不能用固定 sleep 解决？
7. 在本项目里，Appium（控件树）、视觉比对（像素）、状态断言（JSON）三条路线各自最适合抓什么缺陷？

---

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | `POST /session`（caps→sessionId）→ `POST /session/{id}/element`（using+value→elementId）→ `POST /session/{id}/element/{eid}/click`，全程 HTTP JSON、响应都是 `{"value": ...}` |
| 2 | alwaysMatch=所有候选必须满足的公共能力；firstMatch=备选组合按序尝试。appPackage=包名，appActivity=启动界面（不写 Activity，Appium 自动判断，可能启动错界面） |
| 3 | resource-id 由开发预留、与文案/布局解耦 → 最稳（首选）；accessibility id 顺带无障碍验收；xpath 依赖文案/层级，最灵活也最易碎（兜底） |
| 4 | uiautomator2（API 21+）废弃 /touch/perform；W3C Actions 用 pointerMove/Down/Up 表达 tap/swipe，是规范且真机/mock 通用 |
| 5 | ① adb 连设备并确认 `adb devices` ② 启 Appium Server（装 uiautomator2 driver，`appium --port 4723`）③ 改 **server URL** ④ 改 **capabilities**（platformName/deviceName/appPackage/appActivity）；用例代码零改动 |
| 6 | 页面未加载完/弹窗遮挡/定位写错/界面还在切换。固定 sleep 要么白等要么不够——用隐式等待或轮询"目标元素/Activity 出现"才有确定性 |
| 7 | 状态断言（09）=逻辑/信号映射；视觉比对（25）=布局/配色/指针等呈现；Appium（37）=UI 功能/导航/控件/文本/多应用交互 |

---

**上一章**：[36 · ODX 与 OTX 数据库驱动测试](36_ODX与OTX数据库驱动测试.md)　|　**下一章**：[38 · HIL 台架与电源 IO 验证](38_HIL台架与电源IO验证.md)

> 📌 扩展篇导航：[22 全景](22_车载自动化测试全景与能力地图.md) → [23 LIN](23_LIN总线与车身控制测试.md) → [24 DoIP](24_车载以太网与DoIP诊断.md) → [25 HMI 视觉](25_HMI视觉自动化测试.md) → [26 非功能](26_非功能测试实战.md) → [27 信息安全](27_信息安全测试入门.md) → [28 功能安全/ASPICE](28_功能安全与ASPICE测试过程.md) → 29 CAN FD → 30 SOME/IP → 31 功能寻址与 OBD-II → 32 安全机制 → [33 工程工具链](33_工程工具链实战.md) → [34 追踪分析](34_追踪分析实战（ASC与BLF）.md) → [35 OTA/Bootloader](35_OTA与Bootloader全链路.md) → [36 ODX/OTX](36_ODX与OTX数据库驱动测试.md) → **37 Appium 真机 UI 自动化（本章）** → [38 HIL 台架](38_HIL台架与电源IO验证.md) → [39 能力自检](39_能力自检与下一步学习路线.md)
>
> 章节目录与读法见 [00 · 学习路线图](00_学习路线图.md)；安卓车机基础见 [10 · ADB 与 Android 车载基础](10_ADB与Android车载基础.md)；HMI 视觉比对见 [25 章](25_HMI视觉自动化测试.md)。
