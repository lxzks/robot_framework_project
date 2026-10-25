# 10 · ADB 与 Android 车载基础

> 伟世通的座舱产品大量运行 Android（中控大屏用 Android Automotive 或类车机安卓）。本章讲清：adb 是什么、车机测试怎么用它、Android Automotive 的骨架知识。
>
> 本项目用 `adb_sim.py` 模拟了一台安卓车机——**命令接口和真 adb 完全一致**，学会的概念零成本迁移到真机。

---

## 10.1 ADB：安卓调试桥

ADB（Android Debug Bridge）是安卓的**通用命令行接口**，C/S 架构：

```
你的电脑                        安卓设备
┌──────────────┐   USB/WiFi   ┌──────────────┐
│  adb client  │─────────────►│  adbd(守护进程)│
│  (你敲的命令) │◄─────────────│  (设备上执行)  │
└──────────────┘              └──────────────┘
```

**为什么测试工程师离不开 adb**：
- 装 APK / 卸应用 —— `adb install`；
- 看日志 —— `adb logcat`（安卓排故第一工具）；
- 模拟操作 —— `adb shell input tap/swipe`（不用手点）；
- 读系统状态 —— `adb shell dumpsys battery`（电量/温度）；
- 传文件 —— `adb push/pull`；
- 重启/进 recovery —— `adb reboot`。

**车机场景的差异**：车机 adb 常通过网络连（工控机以太网到车机），`adb connect 192.168.x.x:5555`；且车机常**永久开启 adb**（开发模式），不像手机要敲版本号激活。

---

## 10.2 adb 命令族速查（本项目 adb_sim 全部实现）

### 10.2.1 设备管理

```bash
adb devices                      # 列出在线设备（serial + 状态）
adb connect 192.168.1.100:5555   # 网络连接（真机）
adb -s <serial> shell ...        # 多设备时指定目标
```

`devices` 输出：

```
List of devices attached
VEHICLE2026AUTO	device
```

设备状态：`device`（在线可用）/ `unauthorized`（没授权）/ `offline`（连接异常）。**测试脚本第一步永远是 `adb devices` 确认设备在线**——本项目 04 套件用例 1 就是它。

### 10.2.2 系统属性 getprop

```bash
adb shell getprop ro.product.model        # 单值查询
adb shell getprop                          # 全部属性
```

属性是安卓的"注册表"。车机测试常用：

| 属性 | 含义 |
|---|---|
| `ro.product.model` | 机型 |
| `ro.build.version.release` | 安卓版本（车机多为 10~14） |
| `ro.build.version.sdk` | API level |
| `ro.car.vehicle_type` | 车辆类型（passenger/...） |

**测试用途**：用例开头读版本做前置判断（不同版本功能不同，用例要分流）。

### 10.2.3 应用管理

```bash
adb install app.apk               # 安装
adb install -r app.apk            # 覆盖安装（保留数据，测试可重复的关键）
adb uninstall com.example.app     # 卸载
adb shell pm list packages         # 已装应用列表
adb shell am start -n com.vehicle.navi/.MapActivity   # 启动 Activity
adb shell am force-stop com.vehicle.navi              # 强杀
adb shell am broadcast -a ACTION ...                  # 发广播（模拟按键/意图）
```

`am start -n <包名>/<Activity名>` 是启动指定界面的精确方式——**测试脚本里启动应用不要靠"点图标"（ fragile），直接用 am start 直达目标 Activity**。

### 10.2.4 模拟输入

```bash
adb shell input tap 960 540       # 点击坐标
adb shell input swipe 300 900 300 300 200   # 滑动(起点→终点, 时长ms)
adb shell input keyevent 26       # 按键码
adb shell input text "hello"      # 输入文本
```

常用按键码：`3`=HOME、`4`=BACK、`26`=电源、`82`=菜单、`224`=WAKEUP。

**排雷提示**：`tap` 用绝对坐标，**不同分辨率设备坐标完全不同**——脚本里要用屏幕比例（如屏幕宽高的 50%）而不是写死像素。真机上先 `adb shell wm size` 拿分辨率。

### 10.2.5 状态查询（测试断言的金矿）

```bash
adb shell dumpsys battery         # 电量/温度/充电状态
adb shell dumpsys window          # 当前前台窗口（验证界面跳转！）
adb shell dumpsys activity top    # 当前 Activity 栈
adb shell dumpsys power           # 屏幕亮灭/唤醒状态
```

`dumpsys window` 输出里的 `mCurrentFocus=Window{com.vehicle.navi/.MapActivity}` —— **这就是"当前在哪个界面"的权威答案**。04 套件的 `Current Focus Should Contain` 关键字解析它做断言——比图像识别稳定一万倍。

### 10.2.6 日志

```bash
adb logcat                        # 实时滚动
adb logcat -d                     # dump 一次就退出（脚本用这个）
adb logcat -c                     # 清空缓冲
adb logcat -s MyTag:D             # 只看某标签（D=Debug 级别）
adb logcat *:E                    # 只看 Error 以上
```

**logcat 是安卓排故的 80%**：崩溃栈、ANR、系统广播全在这。测试脚本的标准动作：操作前 `logcat -c` 清空 → 操作 → `logcat -d > after.log` → 搜 ERROR/Exception。

---

## 10.3 Android Automotive OS（AAOS）简介

座舱大屏的主流系统。和手机安卓的关键差异：

| 维度 | 手机 Android | Android Automotive |
|---|---|---|
| 输入 | 触摸为主 | 触摸 + 旋钮 + 方向盘按键 + 语音 |
| 应用生态 | Google Play | **车机专用应用**（媒体/导航/电话/设置），无 Play Store |
| 框架 | — | **Car API**（车辆硬件抽象：车速、档位、传感器只读） |
| 多用户/多屏 | — | 多屏协作（仪表+中控+HUD）、驾驶限制（行车中视频屏蔽） |
| 权限 | 常规 | 车机分级（行车安全限制某些 API） |

**测试关注点**：
1. **驾驶限制（driving restriction）**：行车中视频类界面必须被限制（安全法规）——测试要造"车速>0"信号验证限制生效；
2. **多屏一致性**：同一个功能在仪表和中控的显示/状态要一致；
3. **稳定性**：车机常年不关机，内存泄漏/句柄泄漏是重点；
4. **语音交互**：ASR/TTS 链路测试（伟世通产品的重要卖点）。

> 学习资源：Android 官方 AAOS 文档 + `android-automotive` 开源示例（谷歌有树莓派跑 AAOS 的镜像，进阶可玩）。**入门阶段不需要装 AAOS**，用 adb_sim + 真手机都能练到 90% 的 adb 技能。

---

## 10.4 UI 自动化的进阶路线

adb 命令是"手工级"工具。规模化 UI 测试要上框架：

| 框架 | 原理 | 适用 |
|---|---|---|
| **uiautomator2**（Python） | 基于 UiAutomator，Python 封装，`d.click(0.5, 0.5)` | **推荐入门**，Python 生态无缝衔接 |
| Appium | 跨平台（iOS/Android），WebDriver 协议 | 多端统一、团队有移动端背景 |
| AEE（Android Emulator Engine） | 无需 root，反射式控件定位 | 特殊场景 |

**车机 UI 测试的现实**：车机界面动画多、深色主题、旋转屏，UI 自动化维护成本高。**业界主流做法是混合**：功能路径用 UI 自动化走主干，细节用"测试后门/日志断言"验证。这和 09 章 HMI 测试的"状态外置"思想一致——**能用数据断言就不要猜像素**。

---

## 10.5 本项目 AdbLibrary 的设计

```robotframework
Device Should Be Connected    VEHICLE2026AUTO
${model}=    Get Prop    ro.product.model
Install APK    com.vehicle.navi.apk
Start Activity    com.vehicle.navi/.MapActivity
Current Focus Should Contain    com.vehicle.navi
Tap    960    540
Send Keyevent    3
Battery Level Should Be Greater Than    50
```

**为什么封装成 RF 关键字而不是直接跑 adb 命令？**
1. **可移植**：`ADB_PREFIX` 一处修改（`["adb"]` 换真机），116 个用例零改动；
2. **断言语义化**：`Current Focus Should Contain` 内部完成 dumpsys 解析；
3. **统一错误处理**：install 失败抛 AssertionError（用例 FAIL），而不是"命令返回非 0 没人管"。

**接真机三步**：
1. 装 Android platform-tools（`adb` 进 PATH）；
2. 手机开开发者模式 + USB 调试；
3. `AdbLibrary.py` 里 `ADB_PREFIX = ["adb"]`。

---

## 10.6 安卓车机 GUI（android_gui.py）

本项目不只是命令行 mock——还有一台**看得见的安卓车机**：`python simulators/android_gui.py`。

### 10.6.1 界面构成（Android Automotive 风格）

```
┌──────────────────────────────────────┐
│ 22:45            📶 🛰 🔗 🎵  🔋 78% │  ← 状态栏（电量读 adb 状态）
├──────────────────────────────────────┤
│                                      │
│   22:45                              │  ← 桌面：时钟 + 应用图标网格
│   10月13日                            │    （导航/媒体/电话/空调/设置）
│   ┌────┐ ┌────┐ ┌────┐               │
│   │ 🧭  │ │ 🎵  │ │ 📞  │  ...         │  ← 点击图标 = am start
│   └────┘ └────┘ └────┘               │
│                                      │
├──────────────────────────────────────┤
│           ◀      ●      ■            │  ← 底部导航栏（返回/Home/多任务）
└──────────────────────────────────────┘
```

六个界面：**桌面**（图标网格）/ **导航**（目的地列表 + 转向距离 ETA）/ **媒体**（歌单 + 播放控制 + 音量）/ **电话**（联系人点击呼叫 + 来电卡片）/ **空调**（温度 ± + 风量档位 + A/C 座椅加热内循环除霜开关）/ **设置**（getprop 值展示）。

### 10.6.2 与 adb 命令行双向同步

这是设计的精妙处——**GUI 和 adb 命令共享同一份状态文件**（`adb_device_state.json`）：

```
GUI 点导航图标 ──► current_focus = com.vehicle.navi/.MapActivity
                        │
adb shell am start ─────┴──► 同一个文件 ◄──── GUI 每 300ms 轮询
                        │
                  GUI 自动切到导航界面
```

也就是说：`adb shell am start -n com.vehicle.media/.MainActivity` 之后，GUI 会自己跳到媒体界面——**命令行和界面看到的是同一台设备**。测试里可以用任一侧驱动，另一侧必然跟随。

### 10.6.3 双模式：GUI 给人看，headless 给 CI

和仪表盘一样，车机也有 headless 模式（`--headless`）：不起 Qt 窗口，只跑"状态轮询 + CAN 广播"循环。Robot Framework 的 05 套件用它 + `Broadcast Headunit State To Bus` 关键字做**确定性驱动**——不依赖子进程定时器的隐式假设。

> 开发插曲：GUI 模式在 RF 子进程树里曾出现"运行十几秒后静默停止"的幽灵问题（offscreen Qt 环境特异性），最终用"核心逻辑抽成无 Qt 的 `ivi_backend.py` + 测试显式驱动"解决。这个决策本身值得记住：**测试不要依赖长生命周期子进程的隐式活性，能显式驱动的就不要靠等**。

### 10.6.4 车机操作 → CAN → 仪表（座舱联动）

在车机上做任何操作（播歌/开导航/调空调/打电话），`ivi_backend` 会把状态编码成 **CAN 0x400/0x500** 广播出去，仪表盘的多界面随即显示同样内容——这就是量产座舱域“车机 SoC 通过 CAN 驱动仪表显示”的架构缩影。05 套件把这套链路做成了 6 个自动化用例。

**一键体验完整座舱**（车机 + 仪表 + 车辆三个进程）：

```bash
python start_cockpit.py --speed 60 --coolant 125
```

然后按 09 章 9.12 节的“联动体验清单”逐个操作——你会看到两块屏像真车一样呼吸与共。GUI 模式下仪表盘也会周期落盘显示状态（`dashboard_state.json`），所以**联动效果是可观测的**：改完车机，读一眼状态文件就知道仪表屏幕上显示了什么。

---

## 10.6 本章自检

1. adb 的架构（client/server/daemon）？为什么它能跨 USB 和网络？
2. 车机上启动一个应用的两种方式？测试脚本推荐哪种？为什么？
3. `dumpsys window` 的 `mCurrentFocus` 字段能回答什么问题？
4. Android Automotive 和手机安卓的 3 个关键差异？驾驶限制测试测什么？
5. 本项目的 AdbLibrary 怎么做到"用例零改动接真机"？

---

**下一章：[11 · 综合实战项目](11_综合实战项目.md)**

### 📋 自查要点（答完再对，别提前看）

| # | 自查要点 |
|---|---|
| 1 | 三层：**client**（你敲的命令）→ **server**（PC 后台进程，管理连接）→ **daemon**（设备上的守护进程）。能跨 USB 和网络，是因为 client 与 server 之间可走 USB 或 TCP（`adb tcpip 5555` 切网络模式） |
| 2 | ①`am start -n 包名/.Activity`（直接启动指定界面）②模拟点击桌面图标（`input tap x y`）。测试推荐**方式 ①**：**确定性高、不依赖 UI 坐标**（UI 布局一改，坐标法就失效） |
| 3 | 它回答"**当前前台是哪个应用的哪个界面**"（如 `mCurrentFocus=Window{com.vehicle.navi/.MapActivity}`）→ 用它断言"应用真的启动/切换成功了" |
| 4 | 三个关键差异：①**不依赖 Google 服务**（车厂自建生态）②**多屏 + 多用户**（仪表/中控/副驾，驾驶员/乘客账户）③**集成车辆属性**（VHAL：能读车速/档位/车门）。驾驶限制测试测：**行驶中某些操作必须被禁止**（如打字输入、看视频、复杂菜单） |
| 5 | 把"怎么调 adb"集中在一处（`ADB_PREFIX`）：接真机时**只改这一处**（mock → 真 `adb`），**所有用例零改动** |

---

**上一章：[09 · 仪表盘 HMI 测试实战](09_仪表盘HMI测试实战.md)** ｜ **下一章：[11 · 综合实战项目](11_综合实战项目.md)**
