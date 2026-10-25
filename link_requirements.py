"""
link_requirements.py — 给用例打需求标签（建立追溯关系）
==========================================================

在需求追溯体系里，**用例必须显式声明"我在验证哪条需求"**。
本工具把"用例 ↔ 需求"的对应关系写进 .robot 文件的 `[Tags]` 里：

    *** Test Cases ***
    车速应稳定在目标值附近
        [Tags]    REQ-CAN-004        ← 本工具添加/追加

为什么用 Tag 而不是注释或命名约定：
    - RF 的 Tag 是**机器可读**的（results/output.xml 里能取到）→ trace_matrix.py 可解析
    - 可以用 `robot --include REQ-CAN-004` 直接按需求跑用例（需求维度的执行）
    - 不侵入用例逻辑，改动可审计（看 git diff 就清楚）

映射关系维护在本文件的 MAPPING 里（真实项目里通常由需求管理工具导出）。

用法：
    python link_requirements.py --dry-run     # 先看会改什么（推荐）
    python link_requirements.py               # 实际写入
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).parent

# ===========================================================================
# 用例 → 需求 映射表（用例名精确匹配）
# ===========================================================================
MAPPING: dict[str, dict[str, list[str]]] = {
    "scenarios/01_powertrain_signals.robot": {
        "车速应稳定在目标值附近": ["REQ-CAN-004"],
        "转速应与车速保持设计比例": ["REQ-CAN-005"],
        "油门在定速巡航时应有合理开度": ["REQ-CAN-001"],
        "档位在行驶时应为 D 档": ["REQ-CAN-006"],
        "里程应随行驶单调累积": ["REQ-CAN-007"],
        "水温应在正常工作区间": ["REQ-CAN-008"],
    },
    "scenarios/02_telltale_matrix.robot": {
        "正常行驶时所有警告灯应熄灭": ["REQ-TTL-001"],
        "灯光自检例程应点亮全部 12 个警告灯": ["REQ-TTL-002"],
        "自检清单应包含全部 12 个灯名": ["REQ-TTL-002"],
        "自检结束后所有灯应熄灭": ["REQ-TTL-003"],
    },
    "scenarios/03_diag_permission_matrix.robot": {
        "默认会话读数据应允许": ["REQ-DIAG-001"],
        "默认会话写数据应被会话限制": ["REQ-DIAG-007"],
        "扩展会话未解锁写数据应被权限拒绝": ["REQ-DIAG-006"],
        "扩展会话已解锁写数据应成功且可回读": ["REQ-DIAG-004", "REQ-DIAG-005"],
        "ECU 复位后权限应失效": ["REQ-DIAG-013"],
    },
    "scenarios/04_dtc_lifecycle.robot": {
        "故障条件持续时应记录为 confirmed 状态": ["REQ-DTC-001"],
        "清除 DTC 后记录应归零": ["REQ-DTC-002"],
        "故障条件仍在时 DTC 应重新记录": ["REQ-DTC-003"],
        "仪表警告灯应与诊断记录一致": ["REQ-DTC-004"],
    },
    "scenarios/05_session_and_keepalive.robot": {
        "静默超过 S3 时间应回退默认会话并锁定权限": ["REQ-DIAG-009"],
        "TesterPresent 保活应维持会话与权限": ["REQ-DIAG-010"],
        "普通诊断请求也应重置 S3 定时器": ["REQ-DIAG-010"],
    },
    "scenarios/06_cockpit_consistency.robot": {
        "媒体信息应从车机同步到仪表": ["REQ-HMI-002"],
        "媒体音量变化应同步": ["REQ-HMI-002"],
        "导航信息应从车机同步到仪表": ["REQ-HMI-003"],
        "空调设置应从车机同步到仪表": ["REQ-HMI-004"],
        "座舱设置不应影响动力域信号": ["REQ-HMI-005"],
    },
    "scenarios/07_flash_workflow.robot": {
        "完整刷写流程应成功并通过完整性校验":
            ["REQ-FLS-001", "REQ-FLS-002", "REQ-FLS-003"],
        "失败路径_未解锁时请求下载应被拒绝": ["REQ-DIAG-006"],
        "失败路径_块序号错乱应被拒绝": ["REQ-FLS-004"],
        "失败路径_传输字节不足应报完整性失败": ["REQ-FLS-005"],
        "失败路径_地址超范围应被拒绝": ["REQ-FLS-006"],
        "刷写失败后应能重新开始完整流程": ["REQ-FLS-007"],
    },
    "scenarios/08_bus_health_monitor.robot": {
        "三条周期报文应全部出现":
            ["REQ-CAN-001", "REQ-CAN-002", "REQ-CAN-003"],
        "车速报文周期应为 100ms 且抖动可控": ["REQ-CAN-001", "REQ-CAN-009"],
        "仪表报文周期应为 200ms": ["REQ-CAN-002", "REQ-CAN-009"],
        "警告灯报文周期应为 500ms": ["REQ-CAN-003", "REQ-CAN-009"],
        "录制内容应能导出为 CANoe 兼容 Trace": ["REQ-CAN-010"],
    },
    "scenarios/09_response_timing.robot": {
        "会话控制响应应在声明的 P2 内": ["REQ-DIAG-011"],
        "读数据响应时间应稳定在 P2 内（多点采样）": ["REQ-DIAG-011"],
        "负响应不应比正响应慢": ["REQ-DIAG-011"],
    },
    "scenarios/10_smoke_regression.robot": {
        "主链路_信号链应通": ["REQ-HMI-001"],
        "主链路_诊断链应通": ["REQ-DIAG-001", "REQ-DIAG-007"],
        "主链路_故障链应通": ["REQ-DTC-004"],
        # 环境自检用例不关联业务需求（孤儿用例示例，矩阵里会列出）
    },
    "scenarios/11_fault_injection.robot": {
        "低压工况应记录电压过低故障并点亮电池灯": ["REQ-RBX-001"],
        "过压工况应记录电压过高故障": ["REQ-RBX-002"],
        "车速信号丢失时仪表应保持最后有效值": ["REQ-RBX-003"],
        "高丢帧率下诊断通信应仍可用": ["REQ-RBX-004"],
        "清除激励后周期报文应恢复正常": ["REQ-RBX-004"],
    },
    "scenarios/12_power_management.robot": {
        "IGN OFF 后应用报文应停发且 NM 应置睡眠就绪位": ["REQ-PWR-001"],
        "总线静默后节点应进入睡眠（总线零帧）":
            ["REQ-PWR-002", "REQ-PWR-004"],
        "CAN 唤醒帧应唤醒节点并恢复应用报文": ["REQ-PWR-003"],
        "IGN ON 应恢复正常工况": ["REQ-PWR-002"],
    },
    "tests/robot/01_can_signals.robot": {
        "总线应同时存在三条周期报文":
            ["REQ-CAN-001", "REQ-CAN-002", "REQ-CAN-003"],
        "车速信号应在合理范围且接近设定值": ["REQ-CAN-004"],
        "转速信号应与车速联动": ["REQ-CAN-005"],
        "水温信号初始应在正常工作区间": ["REQ-CAN-008"],
        "档位应随车速从 P 切换到 D": ["REQ-CAN-006"],
        "里程信号应随行驶累积": ["REQ-CAN-007"],
        "正常状态下所有警告灯报文应为熄灭": ["REQ-TTL-001"],
        "录制总线并导出 Trace 文件（CANoe 兼容）": ["REQ-CAN-010"],
    },
    "tests/robot/02_uds_diagnostic.robot": {
        "默认会话下应能读取 DID": ["REQ-DIAG-001"],
        "进入扩展会话应返回 P2 与 P2 星超时参数": ["REQ-DIAG-003"],
        "读取 17 字节 VIN 应自动走 ISO-TP 多帧": ["REQ-DIAG-002"],
        "安全访问未解锁时写 DID 应被拒绝": ["REQ-DIAG-006"],
        "完成种子密钥握手后写 DID 应成功":
            ["REQ-DIAG-004", "REQ-DIAG-005"],
        "切回默认会话应重新锁定安全访问": ["REQ-DIAG-013"],
        "读取未知 DID 应返回 requestOutOfRange": ["REQ-DIAG-008"],
        "读取未知服务应返回 serviceNotSupported": ["REQ-DIAG-015"],
        "自检例程应正常启动": ["REQ-DIAG-016"],
        "TesterPresent 应返回保活响应": ["REQ-DIAG-010"],
        "抑制正响应的请求 ECU 不应回复": ["REQ-DIAG-012"],
        "暂停与恢复 DTC 检测应被接受": ["REQ-DTC-006"],
        "静默超过 S3 时间后应回退默认会话": ["REQ-DIAG-009"],
    },
    "tests/robot/03_dtc_workflow.robot": {
        "水温超限应点亮水温报警灯并生成 DTC":
            ["REQ-DTC-001", "REQ-DTC-004"],
        "诊断仪应能读到 confirmed 状态的 P0116": ["REQ-DTC-001"],
        "P0116 状态位应包含 pending 与 confirmed": ["REQ-DTC-001"],
        "DTC 快照应记录故障发生时的车速": ["REQ-DTC-005"],
        "清除 DTC 后报警灯应熄灭": ["REQ-DTC-002", "REQ-DTC-004"],
        "清除后 confirmed 列表应为空": ["REQ-DTC-002"],
        "水温持续超限故障应再次确认": ["REQ-DTC-003"],
    },
    "tests/robot/04_adb_android.robot": {
        "应能检测到安卓车机设备": ["REQ-ADB-001"],
        "应能读取车机系统属性": ["REQ-ADB-001"],
        "应能安装并启动导航应用": ["REQ-ADB-002"],
        "应能模拟点击与按键事件": ["REQ-ADB-004"],
        "应能读取电池状态": ["REQ-ADB-003"],
        "应能卸载应用": ["REQ-ADB-002"],
    },
    "tests/robot/05_ivi_cockpit.robot": {
        "车机空调默认状态应显示在仪表": ["REQ-HMI-004"],
        "车机媒体信息应同步到仪表": ["REQ-HMI-002"],
        "行程电脑应随行驶累计": ["REQ-HMI-006"],
        "adb 命令启动应用应改变车机焦点": ["REQ-ADB-002"],
        "车机开导航后仪表应显示导航信息": ["REQ-HMI-003"],
        "两块屏数据应保持一致": ["REQ-HMI-007"],
    },
    "tests/robot/06_uds_flash_conformance.robot": {
        "编程会话下请求下载应返回单块上限": ["REQ-FLS-001"],
        "分块传输数据应逐块确认": ["REQ-FLS-002"],
        "完整刷写流程应通过完整性校验": ["REQ-FLS-003"],
        "传输字节数不足时完整性校验应失败": ["REQ-FLS-005"],
        "块序号错乱应被拒绝": ["REQ-FLS-004"],
        "未解锁时请求下载应被拒绝": ["REQ-DIAG-006"],
        "默认会话下刷写服务应不可用": ["REQ-DIAG-007"],
        "目标地址超范围应被拒绝": ["REQ-FLS-006"],
        "未请求下载就传数据应报序列错误": ["REQ-FLS-008"],
        "响应时间应符合声明的 P2 参数": ["REQ-DIAG-011"],
        "默认会话读取类服务应始终可用": ["REQ-DIAG-001"],
    },
}


# ===========================================================================
def patch_file(path: Path, mapping: dict[str, list[str]], dry: bool) -> tuple[int, list[str]]:
    """给文件的用例追加 REQ tag。返回 (改动数, 未匹配的用例名列表)"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # 找 Test Cases 段范围
    tc_start = tc_end = None
    for i, l in enumerate(lines):
        if l.strip().startswith("***") and "test cases" in l.lower():
            tc_start = i
        elif tc_start is not None and l.strip().startswith("***") and tc_end is None:
            tc_end = i
    if tc_start is None:
        return 0, list(mapping.keys())
    tc_end = tc_end if tc_end is not None else len(lines)

    changes = 0
    unmatched = []
    new_lines: list[str] = []

    i = 0
    while i < len(lines):
        if not (tc_start < i < tc_end) or lines[i].startswith((" ", "\t")) \
                or not lines[i].strip():
            new_lines.append(lines[i])
            i += 1
            continue

        case_name = lines[i].strip()
        reqs = mapping.get(case_name)
        if reqs is None:
            unmatched.append(case_name)
            new_lines.append(lines[i])
            i += 1
            continue

        # 找该用例块的范围
        j = i + 1
        tags_idx = None
        while j < tc_end:
            line = lines[j]
            if line.strip() and not line.startswith((" ", "\t")):
                break                       # 下一个用例开始
            if line.strip().startswith("[Tags]"):
                tags_idx = j
                break
            if line.strip() and not line.strip().startswith("["):
                break                       # 已进入关键字调用，后面不该再插设置
            j += 1

        new_lines.append(lines[i])
        if tags_idx is not None:
            # 追加到已有 [Tags] 行（去重）
            row = lines[tags_idx].rstrip("\n")
            existing = {c for c in re.split(r"\s{2,}", row.strip())[1:]}
            add = [r for r in reqs if r not in existing]
            if add:
                new_lines.append(row + "    " + "    ".join(add) + "\n")
                changes += 1
            else:
                new_lines.append(lines[tags_idx])
            # 复制到 tags 行之前的内容
            new_lines.extend(lines[i + 1:tags_idx])
            i = tags_idx + 1
        else:
            # 在用例名后插入新的 [Tags] 行
            indent = "    "
            new_lines.append(f"{indent}[Tags]    " + "    ".join(reqs) + "\n")
            changes += 1
            i += 1

    if changes and not dry:
        path.write_text("".join(new_lines), encoding="utf-8")
    return changes, unmatched


def main() -> None:
    parser = argparse.ArgumentParser(description="给用例打需求标签")
    parser.add_argument("--dry-run", action="store_true", help="只看会改什么")
    args = parser.parse_args()

    total_changes = 0
    all_unmatched: list[tuple[str, str]] = []
    print("=" * 70)
    print(" 用例 ↔ 需求 关联" + ("（dry-run，未写入）" if args.dry_run else ""))
    print("=" * 70)
    for rel, mapping in MAPPING.items():
        p = ROOT / rel
        if not p.exists():
            print(f"  ✗ 文件不存在: {rel}")
            continue
        changes, unmatched = patch_file(p, mapping, args.dry_run)
        total_changes += changes
        status = "✅" if changes and not unmatched else ("⚠" if unmatched else "·")
        print(f"  {status} {rel:<48} 打标 {changes} 条"
              + (f"，未匹配 {len(unmatched)} 条" if unmatched else ""))
        for u in unmatched:
            all_unmatched.append((rel, u))

    print("-" * 70)
    print(f" 合计打标 {total_changes} 条用例")
    if all_unmatched:
        print(f"\n ⚠ 以下用例在映射表里没有对应关系（检查拼写或补映射）:")
        for rel, u in all_unmatched:
            print(f"    [{rel}] {u}")
    print("\n 下一步：跑一遍测试生成新结果，然后 python trace_matrix.py")


if __name__ == "__main__":
    main()
