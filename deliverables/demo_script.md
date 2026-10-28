# 15 分钟项目演示脚本（面试/汇报照着跑）

> 用途：面试、主管 1v1、技术分享时，用 15 分钟把"这个项目是什么、怎么工作、我做了什么、局限在哪"讲清楚。
> 配套：[../../../docs/39_能力自检与下一步学习路线.md](../docs/39_能力自检与下一步学习路线.md) §39.3 的结构模板。
> 核心原则：**演示一条完整证据链，而不是把 276 个用例跑一遍给人看。**

---

## 0. 演示前 10 分钟检查（务必提前做）

```powershell
# 1) 进项目 + 激活环境
.\.venv\Scripts\Activate.ps1

# 2) 快速自检：环境/端口/依赖（约 10 秒）
python tools/hardware_check.py          # 应为"纯仿真模式 ✅"
python -m pytest tools/pytest_style -q  # 应为 30 passed（秒级）

# 3) 预热一次冒烟（约 2 分钟，确认本机一切正常）
python run_tests.py --tag smoke

# 4) 把这三样提前打开：README、docs/00 学习路线图、docs/22 能力地图
```

**演示窗口布局（3 个终端 + 2 个浏览器标签）**
- 终端 1：跑测试/命令（主舞台）
- 终端 2：ECU 交互控制台（`ecu_sim.py` 的 stdin，演示注入故障）
- 终端 3：备用（诊断/DoIP 演示或报告命令）
- 浏览器标签：`results/test_summary.html`（交付报告）、`results/trace_matrix.html`（追溯矩阵）
- （可选）两个 GUI 窗口：`python simulators/dashboard.py`、`python simulators/android_gui.py`

> 一句话暖场（30 秒）："这是一个纯软件的迷你座舱台架：动力域 ECU + 仪表 + 安卓车机，
> 用 CAN/UDS 连起来；我用它把车载测试从需求到报告的整条链路跑通。"

---

## 1. 架构与原理（3 分钟）

**讲什么**：三进程 + 一条虚拟 CAN 总线；为什么不用真实硬件。

```powershell
python start_cockpit.py --speed 60 --coolant 125
```
- 指着两个 GUI："左边是仪表（cluster_gui，7 界面），右边是安卓车机（android_gui，6 界面）"。
- 指着终端日志："ECU 每 100/200/500ms 发 0x100/0x200/0x300，仪表和车机监听同一组 CAN 帧。"
- 讲清两个设计决策（**面试官最想听的**）：
  1. **为什么用 UDP 组播模拟 CAN**：跨进程广播语义一致、零硬件成本、CI 可跑；
  2. **为什么 GUI 与测试共用一份显示模型（headless JSON）**：断言数据而不是像素，稳定且可追溯。

> 若被问"这和真实台架差在哪"：直接答（见 §5 局限），**不要掩饰**。

---

## 2. 一条用例的完整链路（5 分钟，重点）

挑一条既有业务含义、又能展示"读值+判断+追溯"的用例：

```powershell
# 只跑一条用例（显式给出名字，避免跑多）
python -m robot --outputdir results/_demo --test "水温超限应点亮水温报警灯并生成 DTC" `
    tests/robot/03_dtc_workflow.robot
```

讲 4 步：
1. **需求**：打开 `requirements/requirements.yaml`，找到 `REQ-DTC-001/004`（水温超限→记录 DTC + 点灯）；
2. **用例**：打开 `tests/robot/03_dtc_workflow.robot` 对应用例，念一遍断言（为什么用 `Wait Until Keyword Succeeds` 而不是 `Sleep`）；
3. **执行**：跑上面命令，看 PASS；打开 `results/_demo/log.html` 点进这条用例，展示每步实际读到的值；
4. **追溯**：`python trace_matrix.py`，在 RTM HTML 里搜 `REQ-DTC-001`，指出"需求→用例→结果"三列都在。

> 这一段的讲法："**测试不是'跑过了'，是'这条需求有证据'**。"

---

## 3. 诊断/刷写/以太网任选其一（3 分钟，按岗位方向）

| 方向 | 命令 | 讲什么 |
|---|---|---|
| 诊断（推荐） | `python simulators/uds_client.py`（或 `python -m robot --outputdir results/_demo2 tests/robot/02_uds_diagnostic.robot`） | 手发 `22 F1 90` 读 VIN、`27 01/02` 解锁、`19 02 08` 读 DTC；展示"协议透明" |
| 刷写 | `python -m robot --outputdir results/_demo3 tests/robot/06_uds_flash_conformance.robot` | 讲 0x34/36/37 + 0x31 FF00 的时序与失败路径 |
| 以太网/OTA | 跑 `07_doip_diagnostics` 或 `17_ota_bootloader` | 讲 DoIP 路由激活 / OTA 预编程+回滚（"新能力面"） |

演示时**只讲一条**，别贪多；重点是"我知道这条协议在测什么边界"。

---

## 4. 证据链与度量（3 分钟）

```powershell
python run_tests.py --report      # 如果时间允许：全量 276 用例 + 三份报告（约 6 分钟）
# 时间紧就用已经跑好的产物（提前跑一次即可）：
start results/test_summary.html
start results/coverage.html
start results/trace_matrix.html
```

依次讲三张图：
1. **交付报告**：通过率、失败明细、耗时——回答"跑得怎么样"；
2. **覆盖率**：UDS 17/17、信号 35/35、DTC 3/5、OBD 4/4、综合 55/57——回答"测全了吗、哪些没测"；
3. **追溯矩阵**：124 需求 / 318 条关联 / 孤儿 1——回答"每条需求谁测的"。

> 关键话术："**覆盖率的价值是暴露盲区，不是追 100%**。DTC 3/5 我是主动披露的：
> P0117/P0135 需要特殊工况，真实项目里这属于'待补测试'，要有风险决策而不是硬凑。"

---

## 5. 主动讲局限与反思（2 分钟，**加分项**）

照着 `KNOWN_ISSUES.md` 讲 4 条（不要背全文）：
1. **仿真不是真实台架**：无物理层/EMC/真实时序，不能替代 HIL/实车；
2. **能力面广但每块是教学深度**：OTA 是 A/B 语义模拟、校验用 CRC32 非签名；Appium/HIL 是模拟后端；
3. **测试有效性有已知边界**：生成套件"自发回读"、DTC 3/5、UDP 组播偶发丢帧（已做幂等重试缓解）；
4. **真实性**："需求/覆盖率是我自己的自洽闭环，不是客户 SRS；真实项目要接 DOORS/Jama/JIRA 和评审流程。"

> 讲局限时的态度："我知道它哪里不真，也知道真项目里该补什么" —— 这比"项目完美"可信 10 倍。

---

## 6. 收尾与追问（1 分钟）

**收尾三句**：
1. "这个项目给我的是**方法**：拿到一个新模块（LIN/DoIP/SOME-IP），我能按同一套结构拆成仿真→库→用例→追溯→报告。"
2. "我最得意的不是用例数，是几个真实 bug 的定位：多诊断仪同 ID 响应串扰、OTA 回滚用例假通过、OTA 云端坏 JSON 崩溃。"
3. "下一步我想把它接到真实 CAN 硬件/真机 adb 上跑一遍（项目已留好 `CAN_INTERFACE`/`ADB_BIN` 切换点）。"

**常见追问 30 秒答案骨架**
| 追问 | 答法 |
|---|---|
| "为什么不用 CANoe？" | CANoe 是工具，测试设计是能力；本项目把工具层抽象出来（docs/33 有 CANoe COM/Appium/HIL 替换点），换工具不动用例 |
| "怎么保证测试不是假通过？" | 负向用例 + 故障注入 + mutation 测试（故意让断言失败证明有效）+ 每次改动全量回归 |
| "覆盖率怎么算的？" | 解析 `results/output.xml` 的执行树：关键字→UDS 服务映射、DBC 信号、故障目录、OBD 模式；不是估算 |
| "遇到 flaky 怎么办？" | 先定位根因（本次查处多诊断仪串扰/UDP 丢帧/锁用例顺序），再加最小重试并记录；不能只重跑 |
| "真实项目还缺什么？" | 真实 ECU/台架、CANoe 工程、DOORS/Jama/JIRA、ASPICE 证据链、实车/产线 |

---

## 7. 应急方案（万一现场翻车）

| 现象 | 处理 |
|---|---|
| 某个套件超时/失败 | 别慌，说"这是仿真总线的已知偶发（UDP 无重传），项目里有幂等重试；我换单条用例演示即可" |
| GUI 打不开/无显示器 | 直接改用 headless + 报告截图，并说明"CI 环境就是 headless" |
| 全量来不及跑 | 用提前生成好的 `results/` 产物讲解（报告/覆盖率/RTM 已归档） |
| 被质疑"用例太少/太多" | 答"276 条在教学项目里是刻意规模；真实项目数千条靠的是同一套方法 + 分层执行（冒烟/回归/nightly）" |

> 演示前**一定**先自己跑一遍 `python run_tests.py --tag smoke`（约 2 分钟），确认环境没被别的 robot 进程占用
> （项目共享虚拟总线，**不能和别的测试并发跑**，见 KNOWN_ISSUES）。
