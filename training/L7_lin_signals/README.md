# L7 · LIN 总线：读一帧车身信号并校验

> **学习目标**：用 `LinLibrary` 跟 LIN 总线打交道 —— 设置从机信号、读回帧、验证校验和、验证睡眠/唤醒。
> **预计时间**：35~45 分钟
> **前置**：
> - 完成 L1、L2（会"读值 + 断言 + 轮询等待"）
> - 读教程 [23 章 · LIN 总线与车身控制测试](../../docs/23_LIN总线与车身控制测试.md) 的
>   §23.3（帧结构）/ §23.4（调度、睡眠与唤醒）/ §23.6（逐条讲 08 套件）
> - 想先看完整答案长什么样：读 `tests/robot/08_lin_bus.robot`（本练习的"完整版"）

---

## 一、为什么车身域用 LIN

CAN 适合动力/底盘这种"高速、多节点、强实时"的域；但车门、后视镜、座椅、雨量/雨刷这类
**低速执行器/传感器**用 LIN 更便宜：**单根线、主从结构、调度表固定时基**。

一条 LIN 总线上有 **1 个主节点 + 最多 15 个从节点**，主节点按"调度表"轮流点名，
被点到的从节点把数据放上总线。所以你读 LIN 数据的方式和 CAN 不同：

| | CAN（L3/L4 学过） | LIN（本练习） |
|---|---|---|
| 拓扑 | 多主，谁都能发 | 单主多从，主节点按调度表点名 |
| 帧 ID | 11/29 位仲裁 ID | 6 位 PID（0x00~0x3F） |
| 校验 | CRC15（硬件） | 经典/增强两种**校验和**（软件算法） |
| 时序 | 事件驱动 + 周期 | **调度表时基**（本项目 10ms 一个槽位） |
| 电源 | 常电 | 支持**睡眠/唤醒**（静态电流指标） |

> 本项目用独立进程 `simulators/lin_sim.py` 仿真一条 LIN 总线（4 个帧：0x10 雨量 / 0x11 雨刷 /
> 0x12 座椅 / 0x3C 主请求），测试端由 `libraries/LinLibrary.py` 通过 UDP+TCP 控制/接收。
> 端口约定：UDP 30001（帧流）/ TCP 30002（控制口）。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **3 个用例**（每个 TODO 都有提示，README 第三节有关键字表）：

### 用例 1：`雨量信号应能编码进状态帧`
- 用 `Set LIN Slave Signal` 把雨量 `rain_intensity` 设为 **75%**（该关键字返回 `since_seq` 标记）
- 用 `Wait For LIN Frame` 等到 **0x10** 帧（带上 `since_seq=${标记}`，确保读到的是"设置之后"的新帧）
- 断言帧的原始字节 `data[0] == 150`（LDF 里雨量 0.5%/LSB：75 / 0.5 = 150）
- 再用 `LIN Frame Should Contain Signal` 断言按 LDF 解码出的物理值 ≈ 75

### 用例 2：`LIN 帧校验和应有效`
- 等一帧 **0x10**（从机应答帧，**增强**校验和），断言 `checksum_type == enhanced` 后调用 `LIN Checksum Should Be Valid`
- 等一帧 **0x3C**（主请求帧，**经典**校验和 —— LIN 2.x 规定），断言 `checksum_type == classic` 后再次校验

### 用例 3：`睡眠后总线应静默、唤醒后应恢复`
- `Send LIN Master Request    00 00 00 00 00 00 00 00`（首字节 0x00 = 睡眠命令）
- `Sleep    0.2s` 后断言 `LIN Bus Should Be Silent    0.3`（接下来 0.3 秒总线上没有任何帧）
- `Wake Up LIN Bus` 拿 `since_seq` 标记，再等到 **0x10** 帧，断言调度恢复

---

## 三、提示：你需要的关键字（都在 `libraries/LinLibrary.py`）

| 你要做的事 | 关键字 | 示例 |
|---|---|---|
| 起/停 LIN 仿真（Suite Setup/Teardown 已配好） | `Start LIN Simulation` / `Stop LIN Simulation` | 不用你写 |
| 设置从机信号 | `Set LIN Slave Signal` | `${m}=    Set LIN Slave Signal    0x10    rain_intensity=75` |
| 等一帧（可指定 since_seq） | `Wait For LIN Frame` | `${f}=    Wait For LIN Frame    0x10    timeout=2    since_seq=${m}` |
| 断言帧里的信号物理值 | `LIN Frame Should Contain Signal` | `LIN Frame Should Contain Signal    ${f}    rain_intensity    75    tolerance=0.5` |
| 断言校验和 | `LIN Checksum Should Be Valid` | `LIN Checksum Should Be Valid    ${f}` |
| 发主请求帧（睡眠/唤醒语义） | `Send LIN Master Request` | `Send LIN Master Request    00 00 00 00 00 00 00 00` |
| 断言总线静默 | `LIN Bus Should Be Silent` | `LIN Bus Should Be Silent    0.3` |
| 发送唤醒脉冲 | `Wake Up LIN Bus` | `${m}=    Wake Up LIN Bus` |
| 读统计（可选） | `Get LIN Statistics` | `${s}=    Get LIN Statistics` / `${s}[sleeping]` |

**帧字典长什么样**（`Wait For LIN Frame` 的返回值）：

```python
{"id": 16, "pid": 80, "length": 1, "data": [150],
 "checksum": 0x.., "checksum_type": "enhanced",
 "checksum_valid": True, "pid_parity_valid": True, "seq": 123}
```

---

## 四、怎么跑

```bash
# 在项目根目录执行
python -m robot --outputdir results/training training/L7_lin_signals/exercise.robot
```

预期：`3 tests, 3 passed, 0 failed`
（exercise 里故意用 `Fail    TODO ...` 占位，**空跑会失败**——把 TODO 换成正确步骤后才会绿。）

---

## 五、自查标准

- [ ] 3 个用例 PASS
- [ ] 你能说出 **经典校验和 vs 增强校验和**的区别（各覆盖哪些字节）
- [ ] 你能说出 `since_seq` 解决了什么问题（为什么不能直接 `Wait For LIN Frame` 不带你刚操作的标记）
- [ ] 你能说出 LIN **睡眠命令帧**的首字节是什么、唤醒后为什么要重新等 0x10
- [ ] 打开 `results/training/log.html`，找到 `Wait For LIN Frame` 那一步，看返回的帧字典字段

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

对比时重点看：
1. `since_seq` 是怎么用的（时序安全）
2. 为什么"原始字节"和"物理值"都要断言（协议层 vs 应用层）

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'Set LIN Slave Signal'` | 没 `Library    ../../libraries/LinLibrary.py` | 检查 Settings 里的 Library 行 |
| Setup failed / 端口被占用 | 上一个 LIN 仿真没停掉 | 等 5 秒重跑；仍不行看 `results/lin_sim_stdout.log` |
| `Wait For LIN Frame` 超时"未等到帧" | 睡眠没唤醒，或帧 ID 写错 | 用例 3 先 `Wake Up LIN Bus`；帧 ID 用 `0x10` |
| `LIN Checksum Should Be Valid` 失败 | 帧来自错误注入/或 0x3C 用错校验类型 | 0x3C 是 classic，0x10/0x11/0x12 是 enhanced |
| 断言 `data[0]` 不是 150 | 雨量单位/缩放记错 | LDF：0.5%/LSB → 75 / 0.5 = 150 |

---

## 八、进阶（做完之后）

- 读 `tests/robot/08_lin_bus.robot`：调度时序（`LIN Schedule Timing Should Be Within`）、
  座椅 2 字节多信号、LDF 解码、错误注入（checksum/奇偶/从机无响应）
- 读 [docs/23 章 §23.7](../../docs/23_LIN总线与车身控制测试.md)："能失败的用例才是有效的"
