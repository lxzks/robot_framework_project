# KNOWN_ISSUES · 已知限制与待改进清单

> 更新：**2026-10-28（v4 · 扩展篇 Wave1 + Wave2 + Wave3 交付后）**
> 项目规模：**276 条自动化用例**（主套件 198 + 场景 78）/ **124 条需求** / **40 篇教程** /
> **18 个关键字库** / **12 个训练练习** / **30 个 pytest 单元测试**。
> 用途：① 学习时知道"哪些是教学简化、别当真实行为" ② 面试时**主动披露局限**
> ③ 后续继续扩展的 checklist。详细取舍见 `docs/23~38` 各章"教学简化点"与 `docs/39` 能力自检。
> 分级：🟥 影响正确性｜🟨 测试有效性（会漏 bug）｜🟦 教学简化（有意为之）｜⬜ 工程待改进

---

## 一、🟦 核心协议 / 仿真简化（00~21 章时代）

| # | 位置 | 限制 |
|---|---|---|
| 1 | `ecu_sim.py` 安全访问 | 密钥算法为 XOR 教学版（真实 OEM 算法保密）。27 02 绕过、种子一次性、爆破锁定已修复（见 §八） |
| 2 | `uds_lib.py` | 未实现 `0x78 responsePending`；Tester 收到 0x78 会当最终负响应 |
| 3 | `uds_client.py` | Tester 回 FC 时声明 `BS=8` 但不续发 FC → 单次响应 >62 字节会超时；无 padding/重传 |
| 4 | `ecu_sim.py` `_svc_read_dtc` | `0x19 01/02/03` 响应为简化格式（非严格 ISO 14229） |
| 5 | `ecu_sim.py` `_svc_communication_control` | `0x28` 的"停止接收"不生效；suppressPosRsp 仅对 `0x3E` 生效 |
| 6 | `ecu_sim.py` `_svc_clear_dtc` | 只支持全清 `14 FF FF FF`；按 groupOfDTC 单清未实现 |
| 7 | `dtc_manager.py` | 自然检测故障不写快照；状态位 bit4/bit6 永不为 1、bit2 只置不清 |
| 8 | `ecu_sim.py` `_svc_ecu_reset` | `0x11` 复位不清 routines / telltale_override / DTC 检测开关 |

## 二、🟦 Wave1 模块简化（23~27 章）

- **LIN**：JSON/UDP 帧代替物理层（break/sync 电平未模拟）；4 帧调度表；`.ldf` 是 JSON 子集；错误注入为软件级；无 LIN 诊断传输层（ISO 14229-7）。
- **DoIP**：回环 TCP/UDP 代替 100BASE-T1；只实现 protocol_version=0x02（无 TLS）；单实体+单 ECU；UDP 识别为单播；activation type 仅 0x00。
- **HMI 视觉**：被测画面来自确定性离屏渲染器（`cluster_canvas.py`），**不是真屏截图**；无文字渲染；12 灯统一红/灰；只做车速表指针；跨平台容差 2%。
- **非功能**：性能数字是本机基线（p95≈2ms、负载≈0.46%、吞吐≈105 条/s）；20s soak 非长稳；5ms 去重窗口 → 间隔需 ≥8ms；无 CPU/内存指标。
- **信息安全**：安全算法 XOR 教学版；爆破防护 3 次/锁 5 秒；模糊测试为固定种子随机 UDS（非 Peach/AFL 语法 fuzz）；只覆盖 UDS 应用层。

## 三、🟦 Wave2 模块简化（29~34 章）

- **CAN FD**：UDP 组播仿真 FD 帧；BRS/ESI/CRC 为语义级标志（无真实 CRC17/21 与位填充）；ADAS 域控只有 3 条报文。
- **SOME/IP**：SD 用回环单播代替多播周期公告；返回码为教学映射（文档同时给真实标准码表）；单进程 2 个服务；无 TLS。
- **诊断进阶**：功能寻址只做单 ECU 响应 0x7E8；0x2F 只控 12 灯掩码；0x23/0x3D 内存区 1KB 教学；OBD-II 只实现 mode 01/03/04/09 的少量 PID。
- **功能安全机制**：E2E Profile 1 教学实现（无 Profile 2/4/5/6/7）；看门狗阈值/恢复简化；安全节点只走独立 UDP。
- **Trace 分析**：ASC/BLF 数据帧子集（无 FD/错误帧/远程帧/事件行完整语义）；负载为位估算；异常检测为规则型。
- **工程工具链**：pytest 只做纯单元层（30 单测）；Dockerfile 未本机 build；CI 矩阵 [3.12, 3.14] 首次运行需确认 wheel 可用性；无 Allure 实装。

## 四、🟦 Wave3 模块简化（35~38 章）

### OTA / Bootloader（35 章 / `ota_server.py` + `ecu_sim.py`）
- **A/B 分区是语义模拟**（不真写双分区）；**完整性校验用 CRC32**，真实项目要 SHA-256 + 签名 + Secure Boot + 防降级（UN R156/ISO 24089）。
- OTA 云端为明文 TCP（无 TLS/证书）；"车速非 0 拒绝刷写"用 F1B0 byte3 模拟车速（台架无法真让车跑）；固件镜像 512B 教学大小。

### ODX / OTX（36 章 / `simulators/diag/*.xml`）
- 教学版 ODX 子集（10 DID/5 DTC/17 服务/10 COMPARAM），不是完整 ISO 22901（无变体继承/物理层/功能字典等）。
- OTX 是极简序列执行器（request/expect），不是完整 ISO 13209；F191 未在 ECU 实现故 ODX 未收录（文档已说明）。

### Appium 真机 UI（37 章 / `appium_mock.py`）
- **mock Appium 服务**（标准库 http.server）实现 W3C WebDriver 子集；页面树为教学简化（6 界面固定布局）；xpath 只实现属性等值子集。
- 截图是确定性合成 PNG（480x800），不是真屏；未覆盖多窗口/系统弹窗/真机时延。**换真 Appium 只改 server URL + capabilities（用例零改动）**。

### HIL 台架（38 章 / `hil_rack.py`）
- **仿真台架**（TCP JSON 模拟 IO 板），不是真实 dSPACE/Vector VT System；电压/IO/继电器语义是教学实现。
- 后端抽象 `HIL_BACKEND=sim|real`：**real 未实现**，会明确报"需接真实台架"并给接入位置（这是有意设计的替换点，不假通过）。

## 五、🟨 测试有效性局限（会漏 bug）

| # | 位置 | 限制 |
|---|---|---|
| 1 | `generated/generated_signals.robot` | 发送后"读回"读到自己的 echo 帧；219 行只验证编码器自洽，不验证 ECU 行为 |
| 2 | `tests/robot/01` 里程用例 | Odometer 整数 km，1s 内增量被截断 → "累积"实际恒真 |
| 3 | `tests/robot/05` 两个联动用例 | 断言值等于仪表初始默认值（CAN 断链也会过） |
| 4 | `tests/robot/03` DTC 快照用例 | 只断言正响应；自然故障不写快照 → 恒真 |
| 5 | `scenarios/11` 丢帧率用例 | 丢帧只作用于周期报文，诊断路径不受影响 |
| 6 | `tests/robot/06` P2 测量 | 20ms 轮询把测量量化到 0~25ms |
| 7 | `simulators/test_android_gui_click.py` | 27 项点击断言是状态断言，不是真实 GUI 事件 |
| 8 | `coverage_report.py` | 信号 35/35 含生成器构造覆盖；UDS 分母为脚本维护的 17 项清单；需求 100% 是 Tag 关联率 |
| 9 | Wave1~3 各模块 | 验证对象是教学仿真器，不等于真实 ECU/台架/真机行为（OTA/Appium/HIL/视觉尤其明显） |

## 六、🟥 正确性风险

**已修（保留记录）**：OTA 云端坏 JSON 崩溃（P0，UnboundLocalError）；OTA/Appium/HIL 三库启动失败不回收子进程（孤儿占 8080/4723/9090）；17 回滚用例假通过（改为成功刷写前断言回滚到 v1.0.0）;OTA 锁于 27 01/02 双诊断仪 0x67 串扰；0x36 多帧块超时重试（0x73 视为首次已到）；06/11 幂等请求超时重试与恢复测量重试；27 02 绕过漏洞；ISO-TP 异常兜底；0x37 刷写状态；车速超限杀进程；02/14 用例独立性；03 清码竞态；总线句柄泄漏；0x36 回显序号断言；`inject` 强制注入；多诊断仪响应串扰（SID 匹配）；**ODX 读 DID 迟到重复响应**（同 SID 0x62 误认，OdxLibrary 做 DID 二次校验 + 重试）。

**仍存在（低概率/低优先级）**：

| # | 位置 | 风险 |
|---|---|---|
| 1 | 状态文件 | `dashboard_state.json`/`adb_device_state.json` 读-改-写无文件锁，多进程并发可能丢更新 |
| 2 | `resources/common.resource` | Teardown 对 Terminate/Wait 用 Ignore Error，杀不掉时可能残留双 ECU（低概率） |
| 3 | Dashboard 新鲜度 | 用"最后 CAN 帧时间"判活；总线合法静默期读仪表会误报过期 |
| 4 | `generate_testcases.py` | pairwise 结果依赖集合迭代顺序（非确定性） |
| 5 | 运行噪声 | 全量跑完偶发 1~2 条 `UdpMulticastBus was not properly shut down` WARN（不影响结果） |
| 6 | UDP 无重传 | 06/14/18 等套件在高负载下仍可能偶发单帧丢失（已加幂等重试/重试，非 100% 消除） |

## 七、⬜ 工程 / 流程待改进 + 🗺️ 下一步

**工程**
- **测试不能并发运行**：所有套件共享虚拟总线（UDP 组播 233.255.0.1:30000）、端口（13400/8080/4723/9090/30001+）与状态文件，同时跑两个 robot/全量回归会互相干扰（复查期间曾因此出现 4 个假失败）。**全量回归必须独占运行**，库里的 `.audit/robot_run.lock` 只是团队约定。
- 残余 flaky：虚拟总线是 UDP 组播、无真实 CAN 的帧重传，极端负载下仍可能偶发单帧丢失（已做多层缓解：幂等重试/块重试/锁定容忍/恢复重试）；全量回归建议连跑两次确认稳定（本次复查最终隔离运行 276/276 通过）。
- CI 双 Python 版本矩阵耗时翻倍（约 8 分钟/版本）；PR 仍跑全量，可拆"PR 冒烟 + nightly 全量"。
- 提交历史含 `new commit`；公开仓库若要整洁需 squash（改写已推送历史）。
- `test_spec_check.py` 只校验"需求/用例是否存在"，未校验 spec 引用与用例 Tags 一致性。
- Dockerfile 未本机 build 验证；无 Allure/CI 报告平台实装；无正式《测试计划》《变更影响分析》模板。

**Wave4（下一步可扩展，详见 docs/39 §39.4）**
- 真实台架/CANoe 工程实战（33/38 章已给接口与替换点）、实车与产线 EOL、车型项目案例复盘。
- 面试演示脚本与录屏、能力认证自检表落地（docs/39 已给结构）。
- 可选：OTA 签名/安全启动深化、SOME-IP TLS、HMI 真屏截图接入（QScreen/ADB/采集卡）、性能资源指标（psutil）。

## 八、Wave1~3 新增与修复摘要

**新增能力模块（14 个可跑模块 + 18 篇教程）**
- Wave1：LIN（23/10）、DoIP（24/10）、HMI 视觉（25/10）、非功能（26/9）、信息安全（27/10）+ 22 能力地图 + 28 功能安全/ASPICE。
- Wave2：CAN FD（29/10）、SOME/IP（30/10）、诊断进阶（31/10）、功能安全机制（32/10）、工程工具链（33：pytest 30 单测/Docker/CI 矩阵）、Trace 分析（34/9）。
- Wave3：OTA/Bootloader（35/10）、ODX/OTX（36/9）、Appium 真机 UI（37/9）、HIL 台架抽象（38/10）+ 39 能力自检与学习路线。
- 场景 13~16（24 条）；训练 L7~L12（6 个新练习，check_all 12/12）；需求 63→124；用例 116→276；教程 22→40；库 4→18。

**修复**
1. 安全访问绕过/种子一次性/爆破锁定。
2. ISO-TP 异常兜底、0x37 刷写状态、rpm 超限崩溃、用例独立性、清码竞态、总线句柄泄漏。
3. 多诊断仪响应串扰（DiagnosticLibrary / ObdLibrary 均改为按请求 SID 匹配响应）。
4. 覆盖率工具扩展（UDS 17 项 + OBD 维度）与单套件统计为 0 的 bug。
5. OTA 引入后 0x31 01 FF00 校验从"只比长度"扩展为"长度+CRC32"（未配置 CRC 时保持旧行为，既有套件兼容）。
6. ODX 读 DID 迟到重复响应误认（DID 二次校验 + 重试）。

## 九、最近一次全量验证（2026-10-28）

- `python run_tests.py --report` → **276/276 通过（100%）**，耗时 **365.9s（约 6 分钟）**
- 覆盖率：UDS **17/17**、信号 35/35、DTC 3/5、OBD 4/4、综合 **55/57（96.5%）**
- 需求追溯：**124/124 = 100%**，关联 **318 条**，孤儿用例 **1**
- `robot --dryrun` 276/276；文档链接 **500+ 条（本次复查 513 条）0 失效**；`python -m pytest tools/pytest_style -q` → 30 passed

> 更细的审计结论见 `.audit/`（本地未提交）。教程到此完整：00~39 篇 + 12 个训练 + 16 个场景。

