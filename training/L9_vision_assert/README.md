# L9 · HMI 视觉：黄金图比对与像素断言

> **学习目标**：用 `VisionLibrary` 对"画出来的仪表像素"做断言 ——
> 整图/区域黄金图比对、指针角度、警告灯区域识别，以及"证明比对不是恒真"的 mutation 测试。
> **预计时间**：35~45 分钟
> **前置**：
> - 完成 L1（会写断言）
> - 读教程 [25 章 · HMI 视觉自动化测试](../../docs/25_HMI视觉自动化测试.md) 的
>   §25.2（像素比对 vs 状态断言）/ §25.3（黄金图管理）/ §25.4（区域与容差）/ §25.5（指针角度）
> - 想先看完整答案：读 `tests/robot/09_hmi_vision.robot`

---

## 一、为什么 HMI 还要"视觉"测试

前 8 个练习断言的几乎都是 **JSON/信号/协议**。但仪表是"给人看的"：
**布局错位、指针卡死、警告灯没画出来、颜色错**——这些缺陷不会让任何状态 JSON 变错，
只有对着**画出来的像素**断言才能发现。

| | 状态断言（L1~L8） | 视觉断言（本练习） |
|---|---|---|
| 断言对象 | `dashboard_state.json` / 信号值 | 渲染出来的 **PNG 像素** |
| 能发现 | 数值/逻辑错误 | 布局、配色、指针角度、灯区域、渲染回归 |
| 稳定性 | 数值精确 | 需要**黄金图 + 容差**（跨平台抗锯齿差异） |
| 风险 | 依赖时序 | **比对恒真**（差分为 0/容差过大 → 什么都抓不到） |

> 本项目用 `simulators/cluster_canvas.py` 做**确定性离屏渲染**（800×480），
> 黄金图在 `simulators/golden/`；`libraries/VisionLibrary.py` 提供比对/角度/区域关键字。
> 视觉套件**不需要起 ECU/总线**，纯渲染 + 纯像素比对，秒级完成、任何机器可复现。

---

## 二、任务

打开 `exercise.robot`，按 TODO 补完 **4 个用例**：

### 用例 1：`默认状态整图应与黄金图一致`
- `Render Cluster State    ${{ {} }}    ${OUT}/default.png` 渲染默认状态（0 km/h、灯全灭、P 档）
- `Compare With Golden    ${png}    ${GOLDEN_DEFAULT}` 返回差异比例，断言 `<= 0.02`
  （2% 容差给跨平台抗锯齿留余量）

### 用例 2：`车速 60 时指针角度应约 67.5 度`
- 车速换算关系：**扫过角度 = 车速 / 240 × 270°** → 60 km/h 对应 67.5°
- 渲染 `${{ {"speed": 60} }}`，用 `Needle Angle Should Be Near    ${png}    67.5    tolerance=2.0`
  （±2° 既容得下像素量化误差，又能抓住"指针卡在 0 / 接错车速信号"）

### 用例 3：`水温报警灯亮/灭的区域识别`
- 亮：渲染 `${{ {"speed": 60, "telltales": {"CoolantTempHigh": 1}} }}` →
  `Telltale Region Should Be Lit    ${png}    CoolantTempHigh`，返回红色占比应 `> 0.25`
- 灭：渲染不带该灯的相同状态 → `Telltale Region Should Be Off    ${png}    CoolantTempHigh`，
  返回占比应 `<= 0.05`
- **同区域"亮→lit、灭→off"成对验证**，才能证明区域识别不是恒真

### 用例 4（关键）：`状态变化必须能被比对检出（mutation）`
- 视觉测试最大的坑是"比对恒真"。故意渲染车速 **240 km/h**（指针大幅变化）：
  - `Images Should Differ    ${mutated}    ${baseline}    min_ratio=0.02`（差异 ≥2%，灵敏度够）
  - `Run Keyword And Expect Error    *整图与黄金图不一致*    Compare With Golden    ${mutated}    ${GOLDEN_DEFAULT}`
    —— 与默认黄金图比对**必须失败**（证明断言有杀伤力）

> `${{ {...} }}` 是 RF 的**内联表达式**（返回 Python 字典对象），等价于先 `Evaluate` 再传参；
> 这里照抄 `tests/robot/09` 的写法即可。

---

## 三、提示：关键字表（`libraries/VisionLibrary.py`）

| 你要做的事 | 关键字 | 示例 |
|---|---|---|
| 渲染状态成 PNG | `Render Cluster State` | `${png}=    Render Cluster State    ${{ {"speed": 60} }}    ${OUT}/x.png` |
| 整图比对黄金图 | `Compare With Golden` | `${ratio}=    Compare With Golden    ${png}    ${GOLDEN_DEFAULT}` |
| 区域比对 | `Compare Region With Golden` | 传 `x y w h` |
| 算两图差异比例 | `Image Diff Ratio` | `${r}=    Image Diff Ratio    ${a}    ${b}` |
| 断言两图确实不同 | `Images Should Differ` | `Images Should Differ    ${a}    ${b}    min_ratio=0.02` |
| 断言指针角度 | `Needle Angle Should Be Near` | `Needle Angle Should Be Near    ${png}    67.5    tolerance=2.0` |
| 灯区域亮/灭 | `Telltale Region Should Be Lit` / `Telltale Region Should Be Off` | `Telltale Region Should Be Lit    ${png}    CoolantTempHigh` |
| 保存差异图（失败证据） | `Save Diff Image` | `${d}=    Save Diff Image    ${png}    ${GOLDEN_DEFAULT}` |

关键变量（exercise 里已写好）：`${ROOT}`、`${GOLDEN_DIR}=${ROOT}/simulators/golden`、`${OUT}=${ROOT}/results/vision`。
黄金图：`cluster_default.png`（默认）与 `cluster_60kmh_alarm.png`（60km/h + 水温报警）。

---

## 四、怎么跑

```bash
# 在项目根目录执行（纯渲染，无需 ECU，约 2~5 秒）
python -m robot --outputdir results/training training/L9_vision_assert/exercise.robot
```

预期：`4 tests, 4 passed, 0 failed`
（exercise 里用 `Fail    TODO ...` 占位，空跑会失败；补完后才会绿。）

---

## 五、自查标准

- [ ] 4 个用例 PASS
- [ ] 能说清"状态断言"和"视觉断言"各自能抓什么缺陷（各举一例）
- [ ] 能说清**容差 2%** 和 **mutation 用例**为什么必须同时存在（一个防误报、一个防恒真）
- [ ] 能说出指针角度换算公式（车速 / 240 × 270°），并算出 120 km/h 对应多少度
- [ ] 打开 `results/vision/` 看生成的 PNG；再让一个比对的用例故意失败，看差异图在哪

---

## 六、参考答案

→ [`solution.robot`](solution.robot)（做完再看）

重点对比：容差参数怎么选、mutation 用例的 `Run Keyword And Expect Error` 写法。

---

## 七、卡住了怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `No keyword with name 'Render Cluster State'` | 没 `Library    ../../libraries/VisionLibrary.py` | 检查 Settings |
| `ModuleNotFoundError: No module named 'PySide6'` | 环境没装 PySide6 | `pip install -r requirements.txt`（见 docs/06 6.1） |
| `整图与黄金图不一致` 但你觉得没错 | 状态/容差不对；或本机渲染差异大 | 先看生成的 PNG 与 `simulators/golden/cluster_default.png` 对比；确认状态字典写对 |
| `基准图不存在` | 黄金图路径写错 | 用 `${GOLDEN_DEFAULT}` 变量，别自己拼路径 |
| 指针角度差很多 | 车速→角度公式记错 | 0→0°、60→67.5°、120→135°、240→270° |

---

## 八、进阶（做完之后）

- 读 `tests/robot/09_hmi_vision.robot`：区域比对（ROI）抗无关变化、同状态两次渲染逐像素一致、
  差异图红色像素统计、差异比例排序合理性
- 读 [docs/25 章 §25.9](../../docs/25_HMI视觉自动化测试.md)：真实项目里视觉测试怎么和状态断言互补
