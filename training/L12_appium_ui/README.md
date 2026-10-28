# L12 · Appium UI 自动化：session / 元素定位 / 点击 / 截图

> **学习目标**：用 `AppiumLibrary` 走一遍"真机 UI 自动化"的标准流程 ——
> 创建 session → 拿页面源码 → 按 resource-id/text 定位元素 → 点击 → 断言文本/页面切换 → 截图。
> **预计时间**：40~50 分钟
> **前置**：
> - 完成 L9（HMI 视觉断言）或 L1~L2；理解"UI 测试"和"视觉测试"的区别
> - 读教程 [37 章 · Appium 与真机 UI 自动化](../../docs/37_Appium与真机UI自动化.md) 的
>   W3C WebDriver 协议、capabilities、元素定位策略（resource-id/accessibility id/xpath）
> - 想先看完整答案：读 `tests/robot/19_appium.robot`（本项目 9 条用例的完整版）

---

## 一、Appium 在车载 HMI 测试里的位置

L1~L8 断言的是"信号/协议/状态 JSON"，L9 断言的是"像素"，本练习断言的是
**"界面控件树 + 操作"**——三者互补：

| 层次 | 断言对象 | 能发现 | 本练习关键字 |
|---|---|---|---|
| 信号/协议（L1~L8） | CAN/UDS 数据 | 数值/逻辑错误 | `Read Signal From Bus`… |
| 视觉（L9） | PNG 像素 | 布局/配色/指针 | `Compare With Golden`… |
| **UI 自动化（L12）** | **控件树（UI 层级）** | **控件缺失/文本错/点了没反应/页面没切换** | `Find Element` / `Click Element` / `Get Page Source` |

真实车机用 **Appium + UiAutomator2**：`adb devices` 连真机 → 车上跑 UiAutomator2 server →
Appium server 把 **W3C WebDriver 协议**（HTTP/JSON）翻译成 ADB 操作。
本项目用 `simulators/appium_mock.py`（HTTP 127.0.0.1:4723）模拟该协议，
**脚本写法与真机完全一致**，换真车时只改 server URL/capabilities —— 这就是"抽象层"的价值。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **5 个用例**：

### 用例 1：`创建会话应返回 sessionId`
- `Create Session`（capabilities 已在 Settings/Keywords 里配好；也可显式传参）
- 断言返回的 sessionId 非空（`Should Not Be Empty`）

### 用例 2：`页面源码应包含包名与当前界面`
- `Get Page Source` 取 XML 控件树 → `Page Source Should Contain` 断言包名
  （如 `com.vehicle.media`）和 activity/界面标识；这证明 session 真的连到了"车机界面"。

### 用例 3：`按 resource-id 定位元素并点击`
- `Find Element    ${SESSION}    id    <resource-id>`（真机上 resource-id 通常是 `包名:id/控件名`）
- `Click Element    ${SESSION}    ${元素}` → 之后 `Get Current Activity` / `Page Source Should Contain` 断言界面变化
- 这就是"模拟用户点按"的核心：**定位 → 操作 → 断言反馈**

### 用例 4：`按文本定位并断言文本`
- `Find Element    ${SESSION}    xpath    //*[@text='...']`（或用 `accessibility id`）
- `Get Element Text` / `Element Text Should Be` 断言控件文本符合预期；再用 `Element Should Not Exist` 做负向

### 用例 5：`截图应成功且非空`
- `Get Screenshot    ${SESSION}    ${OUT}/shot.png` → 断言 PNG 文件存在、大小 > 1000（`OperatingSystem.File Should Exist`）

> **Session 生命周期**：Test Setup **已配好** `创建会话并回到桌面`（创建 session 写入 `${SESSION}`，
> 每条用例从桌面干净状态开始）；Test Teardown 已配 `Delete Session    ${SESSION}`。
> **Suite Setup/Teardown 已配好** `Start Appium Server` / `Stop Appium Server`（起停 mock 进程）。

---

## 三、提示：关键字表（`libraries/AppiumLibrary.py`，W3C WebDriver 协议）

| 你要做的事 | 关键字 | 示例（**注意：操作类关键字第一个参数都是 `${SESSION}`**） |
|---|---|---|
| 起/停 Appium 服务（Suite Setup/Teardown 已配） | `Start Appium Server` / `Stop Appium Server` | 不用你写 |
| 创建/删除 session | `Create Session` / `Delete Session` | `${sid}=    Create Session    app_package=com.vehicle.launcher` |
| 按策略找元素 | `Find Element` | `${e}=    Find Element    ${SESSION}    id    com.vehicle.media:id/btn_play` |
| 元素存在性 | `Element Should Exist` / `Element Should Not Exist` | `Element Should Exist    ${SESSION}    id    com.vehicle.launcher:id/app_media` |
| 点击 | `Click Element` | `Click Element    ${SESSION}    ${e}` |
| 读文本 | `Get Element Text` / `Element Text Should Be` | `${t}=    Get Element Text    ${SESSION}    ${e}` |
| 坐标点击 | `Tap Coordinate` | `${tap}=    Tap Coordinate    ${SESSION}    960    540` |
| 页面源码 | `Get Page Source` / `Page Source Should Contain` | `Page Source Should Contain    ${SESSION}    com.vehicle.media    音乐` |
| 当前界面 | `Get Current Activity` | `${a}=    Get Current Activity    ${SESSION}` |
| 截图 | `Get Screenshot` | `${p}=    Get Screenshot    ${SESSION}    ${OUT}/ui.png` |
| 界面切换 | `Switch To Package` | `Switch To Package    ${SESSION}    com.vehicle.navi    .MapActivity` |

> 定位策略优先级（真实项目经验）：**resource-id > accessibility id > xpath**。
> xpath 最灵活但最慢、最容易被布局变化打断；能用 id 就别用 xpath。

---

## 四、怎么跑

```bash
# 在项目根目录执行（mock server + HTTP，无需 ECU/真机，约 5~10 秒）
python -m robot --outputdir results/training training/L12_appium_ui/exercise.robot
```

预期：`5 tests, 5 passed, 0 failed`
（exercise 里用 `Fail    TODO ...` 占位，空跑会失败；补完后才会绿。）

---

## 五、自查标准

- [ ] 5 个用例 PASS
- [ ] 能说清 **session / capabilities / element id** 分别是什么，以及 W3C WebDriver 请求长什么样
- [ ] 能说清 resource-id、accessibility id、xpath 三种定位策略的优劣与优先级
- [ ] 能说清"UI 断言"和 L9"视觉断言"各自能抓什么、为什么互补
- [ ] 能说出换到真车时要改哪两样东西（server URL + capabilities / app 包名）

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

重点对比：为什么每个用例都要 Create/Delete Session；点击后为什么必须重新取 Page Source 断言反馈。

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'Create Session'` | 没 `Library    ../../libraries/AppiumLibrary.py` | 检查 Settings |
| Setup failed（server 起不来） | 4723 端口被占用 | 等 5 秒重跑；可用环境变量 `APPIUM_PORT` 换端口；看 `results/appium_mock_stdout.log` |
| `Find Element` 找不到 | resource-id/xpath 写错 | 先 `Log    ${src}= Get Page Source` 把控件树打出来，从里面复制 id |
| 点击后页面没变化 | 点错元素/界面本身不动 | 用 Page Source 对比点击前后；换一个按钮（如"开始导航"）试 |
| 截图是空/1x1 | mock 限制 | 断言"文件存在且大小 > 0"即可（真机换成真实截图同样写法） |

---

## 八、进阶（做完之后）

- 读 `tests/robot/19_appium.robot`：元素不存在断言、tap 坐标、页面切换、删除 session 后再操作报错
- 读 [docs/37 章](../../docs/37_Appium与真机UI自动化.md)：真机连接四步（adb devices → UiAutomator2 server → Appium server → 脚本）与常见坑
