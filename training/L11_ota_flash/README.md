# L11 · OTA 刷写：从 manifest 到版本号的全链路

> **学习目标**：用 `OtaLibrary` 走一遍"OTA 刷写"的完整链路 ——
> 下载 OTA manifest → 预编程检查 → 进编程会话 → 分块刷写 → 完整性校验 → 版本号断言 → 坏包拒绝/回滚。
> **预计时间**：45~55 分钟
> **前置**：
> - 完成 L3/L4（UDS 会话、0x10/0x27/0x34/0x36/0x37/0x31 基础；L4 的负向断言很重要）
> - 读教程 [35 章 · OTA 与 Bootloader 全链路](../../docs/35_OTA与Bootloader全链路.md) 的
>   Bootloader/APP 分区、预编程/编程/后编程三阶段、0x34/36/37 + 0x31 例程、版本 DID、回滚
> - 想先看完整答案：读 `tests/robot/17_ota_bootloader.robot`

---

## 一、OTA ≠ "发个文件就完事"

真实 OTA 是一个**多阶段、可回滚、有安全校验**的流程：

```
① 云端/车端下载 manifest（版本/大小/CRC/目标 ECU/回滚标记）
        ↓
② 预编程检查（车速=0、电压正常、依赖版本满足）  ← 不满足就别刷，否则变砖
        ↓
③ 编程会话 + 安全解锁（10 02 + 27 01/02）
        ↓
④ 下载/传输/退出（34 请求下载 → 36 分块传 → 37 传输结束）
        ↓
⑤ 完整性校验（31 01 FF00，CRC/字节数对不上就拒绝）
        ↓
⑥ 复位生效（11 01 → 读 F195 软件版本号）→ 版本从 v1.0.0 变成 v1.1.0
```

**失败保护**（本项目用 A/B 分区/A-B rollback 语义简化）：
- 坏包（CRC 错/大小不符/版本不兼容）→ **拒绝**，旧版本继续可用（不能"刷一半变砖"）
- 传输中断 → 重传某块后继续（真实 Bootloader 的断点续传思想）
- 刷写失败 → **回滚到旧版本**（版本号断言仍为旧值）

> 本项目：`simulators/ota_server.py`（TCP 8080，JSON 行）模拟 OTA 云端；
> `libraries/OtaLibrary.py` 封装"下载/刷写/校验/版本"关键字；ECU 侧 Bootloader 状态机 +
> 版本 DID（F195 应用版本 / F1A3 bootloader 版本 / F186 硬件版本）。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **5 个用例**：

### 用例 1：`OTA manifest 应可取到且字段合法`
- `Get OTA Manifest` 返回 manifest(dict)：版本号、大小、分块大小、CRC32、目标 ECU
- 断言版本/大小/CRC 都存在且合法（`Should Not Be Empty` / `Should Be True`）

### 用例 2：`完整刷写链路应成功且版本号更新`
- `Download OTA Image` 下载镜像到内存
- `Check Preprogramming Conditions`（31 01 0203，车速=0/电压正常）
- `Enter Programming`（编程会话 + 安全解锁）
- `Flash OTA Image`（0x34/0x36/0x37 → 31 01 FF00 完整性校验）
- `ECU Reset`（11 01）→ `Read Software Version`（22 F195）→ `Software Version Should Be    目标版本`

### 用例 3：`坏 CRC 包应被拒绝且版本保持`
- `Inject Bad Image    crc`（或按实际参数注入坏包）
- `Download OTA Image` + `Flash OTA Image` 应失败：`Should Refuse Image`
- 版本仍为旧值：`Should Rollback To Version    旧版本`

### 用例 4：`预编程条件不满足时应拒绝刷写`
- 先 `Enter Extended Session` + `Security Unlock`（31 01 0203 需要扩展会话）
- 造不满足条件：`Set Test Stimulus    voltage=9`（欠压）或 `Inject Precheck Speed    30`（模拟车速 ≠ 0）
- `Preprogramming Conditions Should Be Rejected`（ECU 回 NRC 0x22）
- 恢复：`Clear Test Stimulus` / `Inject Precheck Speed    0`，再 `Check Preprogramming Conditions` 应通过

### 用例 5：`断点续传 + 分块重传后应能成功`
- 断点续传：`Download OTA Image    start_offset=0    length=128` + `start_offset=128` 两段拼接，
  用 `Evaluate` 算拼接后的 CRC32 与 manifest 比对（证明 Range 下载正确）
- 丢块重传：`Flash With Retry    skip_block=2`（内部模拟"跳过某块→ECU 回 NRC 0x73→重发补上"）
  → 刷写成功、`ECU Reset` 后版本号更新

> **Suite Setup/Teardown 已配好**（起 ECU + OTA server，逆序回收），不用你写。
> 关键顺序要记住：**先预编程检查、再编程会话、最后传输**——顺序错会被 ECU 用 NRC 拒绝。

---

## 三、提示：关键字表（`libraries/OtaLibrary.py`）

| 你要做的事 | 关键字 | 说明 |
|---|---|---|
| 起/停 OTA 云端（Suite Setup/Teardown 已配） | `Start OTA Server` / `Stop OTA Server` | 端口 8080，`OTA_HTTP_PORT` 可覆盖 |
| 取 manifest | `Get OTA Manifest` | 返回 version/previous_version/size/block_size/crc32/compatible/rollback_supported |
| 下载镜像 | `Download OTA Image    start_offset=0    length=128` | 分块/Range 下载；返回镜像 hex |
| 包级校验（Agent 侧） | `Check OTA Image Integrity` | 下载量 + CRC32 与 manifest 比对 |
| 预编程检查（31 01 0203） | `Check Preprogramming Conditions` | 车速=0、电压 10.5~15.5V |
| 预编程拒绝路径 | `Preprogramming Conditions Should Be Rejected` | 期望 NRC 0x22（配合电压/车速注入） |
| 注入异常激励 | `Set Test Stimulus    voltage=9` / `Inject Precheck Speed    30` | 欠压 / 模拟车速非 0 |
| 进编程会话 | `Enter Programming` | 10 02 + 27 解锁 |
| 刷写主链路 | `Flash OTA Image` | 0x34/0x36/0x37 + 31 01 FF00 校验，返回 summary |
| 强制刷坏包（负向） | `Force Flash Corrupted Image` | 绕过 Agent 校验直写 → ECU 侧 CRC 必须拒绝 |
| 复位生效 | `ECU Reset` | 11 01（A/B 切换，版本 DID 更新） |
| 读/断言版本 | `Read Software Version` / `Software Version Should Be` | 22 F195；另有 `Read Bootloader Version`(F1A3)/`Read Hardware Version`(F186) |
| 注入坏包 | `Inject Bad Image    crc` | `crc` / `size` / `version`；`Clear OTA Injection` 恢复 |
| 断言拒绝/回滚 | `Should Refuse Image` / `Should Rollback To Version` | 坏包必须拒绝、版本保持/回滚 |
| 断电模拟 | `Simulate Power Loss During Flash    2` | 传 2/8 块后中断 → 回滚旧版本 |
| 分块重试 | `Flash With Retry    skip_block=2` | 模拟丢块→重试成功 |

**版本号约定**（本项目仿真）：当前应用版本 `v1.0.0`；OTA 目标版本 `v1.1.0`。
实际值以 `Get OTA Manifest` 返回和 README 对应套件为准，不要凭记忆写死。

---

## 四、怎么跑

```bash
# 在项目根目录执行（起 ECU + OTA server + 刷写，约 15~25 秒）
python -m robot --outputdir results/training training/L11_ota_flash/exercise.robot
```

预期：`5 tests, 5 passed, 0 failed`
（exercise 里用 `Fail    TODO ...` 占位，空跑会失败；补完后才会绿。）

---

## 五、自查标准

- [ ] 5 个用例 PASS
- [ ] 能说清 OTA 三阶段（预编程/编程/后编程）各自该做什么、为什么顺序不能乱
- [ ] 能说清 0x34/0x36/0x37 与 0x31 FF00 完整性校验的关系
- [ ] 能说清"坏包被拒绝"和"回滚"分别证明了什么（安全性 vs 可用性）
- [ ] 能说出 A/B 分区为什么能避免"刷一半变砖"

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

重点对比：全链路用例的步骤顺序、坏包用例怎么"先证明拒绝、再证明旧版本还在"。

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'Get OTA Manifest'` | 没 `Library    ../../libraries/OtaLibrary.py` | 检查 Settings |
| Setup failed（OTA server 起不来） | 8080 端口被占用 | 等 5 秒重跑；环境变量 `OTA_HTTP_PORT` 换端口；看 `results/ota_server_stdout.log` |
| 刷写返回 NRC 0x24/0x33/0x7F | 顺序错/没解锁/会话不对 | 按"预编程检查→编程会话→传输"顺序；确认 `Enter Programming` 成功 |
| 版本号断言失败 | 没复位（11 01）或读的是 bootloader 版本 | 刷完先 `ECU Reset`，再读 F195 |
| 坏包用例没拒绝 | 注入参数错/没注入成功 | 看 `Inject Bad Image` 的参数文档；用 `Should Refuse Image` 断言 |

---

## 八、进阶（做完之后）

- 读 `tests/robot/17_ota_bootloader.robot`：预编程条件（车速非 0 拒绝）、大小不符、
  版本不兼容、中断回滚、重传、复位后读新版本 DID
- 读 [docs/35 章](../../docs/35_OTA与Bootloader全链路.md)：真实 OTA 架构（T-Box/云端/证书）与安全要求
