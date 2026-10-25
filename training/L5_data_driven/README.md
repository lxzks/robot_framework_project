# L5 · 数据驱动：一个用例跑 N 组数据

> **学习目标**：用 Template（模板）把"同一逻辑、不同数据"的重复脚本压缩成一张表。
> **预计时间**：30 分钟
> **前置**：完成 L3

---

## 一、什么时候用数据驱动

如果你发现自己在**复制粘贴**用例：

```robotframework
读取零件号应正确
    ${pn}=    Read DID As ASCII    F187
    Should Be Equal    ${pn}    HW-IC-2026-001

读取序列号应正确
    ${sn}=    Read DID As ASCII    F189
    Should Be Equal    ${sn}    ECU-2026-0918-007

读取供应商应正确
    ${v}=    Read DID As ASCII    F18A
    Should Be Equal    ${v}    VISTEON
```

三个用例的**逻辑完全一样，只有数据不同** —— 这就是数据驱动的信号。

改写成 Template：

```robotframework
读取多个 DID 应正确
    [Template]    读取 DID 并断言
    F187    HW-IC-2026-001
    F189    ECU-2026-0918-007
    F18A    VISTEON

*** Keywords ***
读取 DID 并断言
    [Arguments]    ${did}    ${expected}
    ${value}=    Read DID As ASCII    ${did}
    Should Be Equal    ${value}    ${expected}    msg=DID ${did} 值不符
```

**好在哪里**：
- 逻辑只写一遍（改断言逻辑只改一处）
- 加一组数据只是加一行
- 报告里每组数据独立统计（一眼看出哪组挂了）

---

## 二、任务

1. 在 `exercise.robot` 里用 Template 写 `读取多个 DID 应正确`，覆盖 3 组 DID：

| DID | 期望值 |
|---|---|
| `F187` | `HW-IC-2026-001` |
| `F189` | `ECU-2026-0918-007` |
| `F18A` | `VISTEON` |

2. 再写一个 `多个信号应在各自范围内`，用 Template 覆盖 3 个信号：

| 报文 | 信号 | 范围 |
|---|---|---|
| `EngineData` | `VehicleSpeed` | 0 ~ 250 |
| `EngineData` | `CoolantTemp` | -40 ~ 215 |
| `ClusterData` | `FuelLevel` | 0 ~ 100 |

（提示：参数可以用 `Should Be True    低 <= ${值} <= 高` 这种形式，
      或者用内置的 `Should Be True    ${low} <= ${v} <= ${high}`）

---

## 三、⚠️ Template 的三个坑

1. **`[Template]` 只接受一个关键字名**，不能写成 `[Template] 关键字 参数`
   （会报 `Setting 'Template' accepts only one value, got 2`）——
   参数必须放在**每一行数据**里。
2. **Template 用例里的每一行参数个数必须与关键字 `[Arguments]` 对齐**，
   否则报参数错误。
3. **Template 展开后不能有步骤级变量赋值**（模板关键字本身可以，但用例体里不行）。

---

## 四、怎么跑

```bash
python -m robot --outputdir results/training training/L5_data_driven/exercise.robot
```

预期：`2 tests, 6 passed, 0 failed`（2 个用例 × 各 3 组数据 = 报告里 6 条记录）

---

## 五、自查标准

- [ ] 报告里显示 6 条记录（每个数据组独立统计）
- [ ] 你故意把一组期望值改错，能看到**只有那一组**失败（定位精准）
- [ ] 你能说出"什么时候该用 Template、什么时候不该"（提示：逻辑不同就别硬套）

---

## 六、参考答案

→ [`solution.robot`](solution.robot)
