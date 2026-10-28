"""
OdxLibrary.py — Robot Framework ODX/OTX 诊断数据库驱动测试关键字库
====================================================================

为什么要"数据库驱动"测试？
--------------------------
手写 hex 的测试（`Send UDS Request    22 F1 90`）能跑，但真实项目的诊断定义
是**几百到几千个 DID/服务/DTC**，而且不同车型/配置（变体）不一样。把
"ID 对应什么数据、多长、怎么编码、服务请求怎么拼"写在测试脚本里，必然：

    - 改一个 DID 要改 N 个脚本 → 容易漏、review 成本高；
    - 同一个 DID 在不同脚本里长度写错 → 测试之间矛盾；
    - 换车型/换 ECU 版本，脚本全废。

**ODX（ISO 22901）** 就是解决这个问题的"诊断单一事实源"：DID/服务/DTC/编码
规则集中定义在数据库里，测试工具（CANoe/OTX 执行器/本库）按名字生成请求、
按定义解码响应。**OTX（ISO 13209）** 则把"测试序列"也标准化成可交换的 XML——
业务同事用 ODX/OTX 工具编辑，测试工程师直接执行，不必逐条翻译成代码。

本库实现（标准库 xml.etree，无第三方依赖）
------------------------------------------
    Load ODX / Get ECU Info / List Data Identifiers / List DTCs / List Services
    DID Request Bytes / Decode DID Response / Service Request Template / Comparam
    Generate Request From ODX / Read DID By Name / Enter Session By Name / Clear All DTCs
    ODX Should Be Valid
    Load OTX / Run OTX Sequence / OTX Step Should Have Passed / OTX Summary
    Send ODX Request（执行任意请求并返回结构化响应，供测试断言）

    —— 文件：simulators/diag/vehicle.odx.xml / simulators/diag/vehicle.otx.xml
    —— 被测 ECU：simulators/ecu_sim.py（通过虚拟 CAN + uds_client.DiagnosticTester）

设计要点
--------
1. **加载时才做结构校验，失败不污染已有状态**：`Load ODX` 先完整解析校验，
   全部通过才替换当前数据库（和真实工具"打开数据库失败不切库"一致）。
2. **数据库与 ECU 一致性**：`ODX Should Be Valid` 会检查 ODX 里的服务 SID 集合
   是否等于 ECU 实际支持的 17 个 SID（`ECU_SUPPORTED_SIDS` 常量与
   ecu_sim.handle_uds_request 的 handler 对齐）。数据库写错/ECU 升级后数据库
   没同步，都会在这里暴露——这正是 ODX 版本管理的日常。
3. **OTX 失败要能定位到步骤**：`Run OTX Sequence` 逐步骤执行并记录结果，
   失败抛 `[OTX] 第 N 步「步骤名」失败：…`；`Get OTX Results` 可看到前面
   已通过/失败步骤，是"测试步骤级可观测性"的演示。
4. 不新增依赖：XML 解析用 xml.etree；发请求复用项目已有的
   simulators/bus.py（VirtualCanBus）与 simulators/uds_client.py（DiagnosticTester）。
"""

from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from robot.api.deco import keyword
from robot.api.logger import console, info

# 复用项目已有的虚拟 CAN 总线与诊断仪（不新增依赖）
sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                           # noqa: E402
from uds_client import DiagnosticTester                 # noqa: E402

# ---------------------------------------------------------------------------
# 路径 / 常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DIAG_DIR = ROOT / "simulators" / "diag"
DEFAULT_ODX_PATH = DIAG_DIR / "vehicle.odx.xml"
DEFAULT_OTX_PATH = DIAG_DIR / "vehicle.otx.xml"

# 与 simulators/ecu_sim.py 的 17 个 UDS handler 一一对应：
#   10 11 14 19 22 27 28 2E 2F 31 34 36 37 3D 3E 85 23
# 数据库（ODX）里的服务集合必须和它一致，否则说明"数据库与 ECU 版本不匹配"。
ECU_SUPPORTED_SIDS = frozenset({
    0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x28, 0x2E, 0x2F,
    0x31, 0x34, 0x36, 0x37, 0x3D, 0x3E, 0x85, 0x23,
})

DTC_CODE_RE = re.compile(r"^[PCBU][0-9A-F]{4}$")
DID_ID_RE = re.compile(r"^[0-9A-F]{4}$")
VALID_ENCODINGS = {"ASCII", "HEX", "UINT8"}
VALID_STEP_TYPES = {"request", "expect"}


# ---------------------------------------------------------------------------
# XML 辅助：去掉命名空间读取标签（OTX 用 otx: 命名空间，ODX 教学版无命名空间）
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    """返回去掉 {namespace} 前缀后的标签名。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _children(element: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in list(element) if _local(child.tag) == name]


def _child(element: ET.Element, name: str) -> Optional[ET.Element]:
    for child in list(element):
        if _local(child.tag) == name:
            return child
    return None


def _text(element: ET.Element, name: str, required: bool = False,
          default: str = "") -> str:
    child = _child(element, name)
    if child is None or (child.text or "").strip() == "":
        if required:
            raise AssertionError(f"[ODX] 缺少必填元素 <{name}>（所在元素 <{_local(element.tag)}>）")
        return default
    return (child.text or "").strip()


def _parse_int(text: str, what: str = "数值") -> int:
    """解析 '0x10' / '10' / 'F190'（十六进制 DID 场景）等写法。"""
    value = str(text).strip()
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value, 16)
        except ValueError as exc:
            raise AssertionError(f"[ODX] {what} 不是合法数值：{text!r}") from exc


def _to_bool_text(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


class OdxLibrary:
    """ODX 数据库解析 + 数据驱动请求/解码 + OTX 序列执行。"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, odx_path: Optional[str] = None,
                 otx_path: Optional[str] = None) -> None:
        self._default_odx = Path(odx_path) if odx_path else DEFAULT_ODX_PATH
        self._default_otx = Path(otx_path) if otx_path else DEFAULT_OTX_PATH

        self._odx: Optional[Dict[str, Any]] = None
        self._odx_path: Optional[Path] = None
        self._otx: Optional[Dict[str, Any]] = None
        self._otx_path: Optional[Path] = None
        self._step_results: List[Dict[str, Any]] = []
        self._last_summary: Optional[Dict[str, Any]] = None
        self._last_response: Optional[Dict[str, Any]] = None

        # 懒启动：只有"执行请求"类关键字才需要总线（纯解析不需要 ECU）
        self._bus: Optional[VirtualCanBus] = None
        self._tester: Optional[DiagnosticTester] = None

        info(f"[OdxLibrary] 默认数据库：ODX={self._default_odx.name}，"
             f"OTX={self._default_otx.name}；ECU 支持 {len(ECU_SUPPORTED_SIDS)} 个服务 SID")

    # ==================================================================
    # ODX：加载 / 查询
    # ==================================================================
    @keyword("Load ODX")
    def load_odx(self, path: Optional[str] = None) -> Dict[str, Any]:
        """加载并校验 ODX 数据库文件（默认 simulators/diag/vehicle.odx.xml）。

        相对路径按项目根目录解析。解析/校验失败抛清晰 AssertionError
        （文件不存在 / XML 非法 / 缺 DIAG-LAYER / DID 重复 / DTC 编号非法…），
        且**不会替换当前已加载的数据库**（失败不污染状态）。
        返回摘要：ECU 名称、诊断地址、DID/DTC/服务数量。
        """
        target = self._resolve_path(path, self._default_odx)
        data = self._parse_odx_file(target)
        self._odx = data
        self._odx_path = target
        summary = {
            "file": str(target),
            "version": data["file_info"].get("VERSION", ""),
            "ecu": data["ecu"]["name"],
            "diag_address": data["ecu"]["diag_address"],
            "response_address": data["ecu"]["response_address"],
            "did_count": len(data["dids"]),
            "dtc_count": len(data["dtcs"]),
            "service_count": len(data["services"]),
            "comparam_count": len(data["comparams"]),
        }
        info(f"[ODX] 已加载 {target.name}：ECU={summary['ecu']}，"
             f"DID={summary['did_count']}，DTC={summary['dtc_count']}，"
             f"服务={summary['service_count']}，COMPARAM={summary['comparam_count']}")
        return summary

    @keyword("ODX Should Be Valid")
    def odx_should_be_valid(self) -> List[str]:
        """校验 ODX 结构完整性与"数据库 ↔ ECU 能力"一致性。

        检查项：
            ① DIAG-LAYER/ECU-VARIANT 存在，诊断地址/响应地址合法；
            ② DID 的 ID 唯一且为 4 位十六进制，编码类型受支持；
            ③ DTC 编号合法（P/B/C/U + 4 位十六进制）且唯一；
            ④ 服务 SID 唯一，且**集合与 ECU 实际支持的 17 个 SID 完全一致**；
            ⑤ COMPARAM 名称唯一。
        返回检查项描述列表（RF 日志可见）。
        """
        data = self._ensure_odx()
        checks: List[str] = []

        ecu = data["ecu"]
        if not ecu.get("name"):
            raise AssertionError("[ODX] ECU-VARIANT 缺少 short_name")
        for key, label in (("diag_address", "诊断地址"), ("response_address", "响应地址")):
            value = ecu.get(key, 0)
            if not 0 <= value <= 0x7FF:
                raise AssertionError(f"[ODX] {label} {value:#x} 不是合法标准帧 ID")
        checks.append(f"ECU-VARIANT={ecu['name']}，诊断地址={ecu['diag_address']:#x}")

        did_ids = [did["id"] for did in data["dids"]]
        if len(did_ids) != len(set(did_ids)):
            raise AssertionError(f"[ODX] DID 重复定义：{did_ids}")
        for did in data["dids"]:
            if not DID_ID_RE.match(did["id_hex"]):
                raise AssertionError(f"[ODX] DID {did['name']} 的 ID 非法：{did['id_hex']}")
            if did["encoding"] not in VALID_ENCODINGS:
                raise AssertionError(
                    f"[ODX] DID {did['name']} 编码类型非法：{did['encoding']}"
                    f"（支持 {sorted(VALID_ENCODINGS)}）")
            if did["length"] <= 0:
                raise AssertionError(f"[ODX] DID {did['name']} 长度非法：{did['length']}")
        checks.append(f"DID 唯一且合法：{len(did_ids)} 条")

        dtc_codes = [dtc["code"] for dtc in data["dtcs"]]
        if len(dtc_codes) != len(set(dtc_codes)):
            raise AssertionError(f"[ODX] DTC 重复定义：{dtc_codes}")
        for code in dtc_codes:
            if not DTC_CODE_RE.match(code):
                raise AssertionError(f"[ODX] DTC 编号非法：{code}（应为 P/B/C/U + 4 位十六进制）")
        checks.append(f"DTC 编号合法且唯一：{len(dtc_codes)} 条")

        odx_sids = {service["sid"] for service in data["services"]}
        if len(odx_sids) != len(data["services"]):
            raise AssertionError(f"[ODX] 服务 SID 重复定义：{data['services']}")
        if odx_sids != ECU_SUPPORTED_SIDS:
            missing = sorted(ECU_SUPPORTED_SIDS - odx_sids)
            extra = sorted(odx_sids - ECU_SUPPORTED_SIDS)
            raise AssertionError(
                "[ODX] 数据库服务集合与 ECU 支持（17 个 SID）不一致："
                f"数据库缺 {[f'0x{sid:02X}' for sid in missing]}，"
                f"数据库多 {[f'0x{sid:02X}' for sid in extra]}"
                "—— 典型原因：ODX 版本与 ECU 软件版本不匹配")
        checks.append(f"服务 SID 与 ECU 一致：{len(odx_sids)} 个")

        comparam_names = list(data["comparams"])
        if len(comparam_names) != len(set(comparam_names)):
            raise AssertionError("[ODX] COMPARAM 名称重复")
        checks.append(f"COMPARAM 唯一：{len(comparam_names)} 条")

        info("[ODX] 结构校验通过：" + "；".join(checks))
        return checks

    @keyword("Get ECU Info")
    def get_ecu_info(self) -> Dict[str, Any]:
        """返回 ODX 中 DIAG-LAYER 的 ECU 变体信息（名称/地址/协议）。"""
        data = self._ensure_odx()
        info(f"ECU 信息：{data['ecu']}")
        return dict(data["ecu"])

    @keyword("List Data Identifiers")
    def list_data_identifiers(self) -> List[Dict[str, Any]]:
        """返回 ODX 中定义的全部 DID（名称/ID/编码/长度/是否可写/描述）。"""
        data = self._ensure_odx()
        result = [dict(did) for did in data["dids"]]
        info(f"ODX 共 {len(result)} 个 DID："
             f"{[(item['name'], item['id_hex']) for item in result]}")
        return result

    @keyword("DID Request Bytes")
    def did_request_bytes(self, did: Any) -> str:
        """按 DID（名称或 ID）生成 0x22 读数据请求，返回十六进制字符串。

        例：DID Request Bytes    VIN        → "22F190"
            DID Request Bytes    F1 90      → "22F190"
        """
        definition = self._resolve_did(did)
        request = f"22{definition['id_hex']}"
        info(f"DID {definition['name']}（{definition['id_hex']}）请求 = {request}")
        return request

    @keyword("Decode DID Response")
    def decode_did_response(self, did: Any, response: Any) -> Any:
        """按 ODX 定义解码 DID 响应（ASCII 文本 / HEX 串 / UINT8 整数）。

        response 可以是：
          - `Send ODX Request` 返回的响应字典（取 payload_hex）
          - 完整响应 hex（"62F190..."，自动去掉 62+DID 前缀）
          - 纯数据 hex（"4C5356414D..."）
        解码时会校验数据长度与 ODX 的 LENGTH 一致，不一致抛清晰错误
        （数据库版本与 ECU 不匹配的典型症状）。
        """
        definition = self._resolve_did(did)
        payload = self._extract_did_payload(definition, response)
        encoding = definition["encoding"]
        expected_length = int(definition["length"])
        if len(payload) != expected_length:
            raise AssertionError(
                f"[ODX] DID {definition['name']} 数据长度不符：ODX 定义 {expected_length} 字节，"
                f"实际响应 {len(payload)} 字节（payload={payload.hex()}）"
                "—— 数据库与 ECU 版本可能不一致")
        if encoding == "ASCII":
            try:
                value: Any = payload.decode("ascii")
            except UnicodeDecodeError as exc:
                raise AssertionError(
                    f"[ODX] DID {definition['name']} 按 ASCII 解码失败："
                    f"{payload.hex()}：{exc}") from exc
        elif encoding == "HEX":
            value = payload.hex()
        elif encoding == "UINT8":
            value = int.from_bytes(payload, "big")
        else:  # pragma: no cover - 加载时已校验
            raise AssertionError(f"[ODX] DID {definition['name']} 编码类型非法：{encoding}")
        info(f"DID {definition['name']} 解码（{encoding}，{len(payload)}B）→ {value!r}")
        return value

    @keyword("List DTCs")
    def list_dtcs(self) -> List[Dict[str, Any]]:
        """返回 ODX 中定义的全部 DTC（编号/名称/描述/严重度）。"""
        data = self._ensure_odx()
        result = [dict(dtc) for dtc in data["dtcs"]]
        info(f"ODX 共 {len(result)} 个 DTC：{[item['code'] for item in result]}")
        return result

    @keyword("DTC Name")
    def dtc_name(self, code: str) -> str:
        """按 DTC 编号查名称（大小写不敏感），如 P0562 → 系统电压过低。"""
        data = self._ensure_odx()
        key = str(code).strip().upper()
        dtc = data["dtcs_by_code"].get(key)
        if dtc is None:
            raise AssertionError(
                f"[ODX] 数据库中不存在 DTC {code}，已知：{list(data['dtcs_by_code'])}")
        info(f"DTC {key} → {dtc['long_name']}")
        return dtc["long_name"]

    @keyword("List Services")
    def list_services(self) -> List[Dict[str, Any]]:
        """返回 ODX 中定义的全部诊断服务（SID/短名/请求/正响应/会话/安全要求）。"""
        data = self._ensure_odx()
        result = [dict(service) for service in data["services"]]
        info(f"ODX 共 {len(result)} 个服务："
             f"{[(item['short_name'], f'0x{item['sid']:02X}') for item in result]}")
        return result

    @keyword("List Service SIDs")
    def list_service_sids(self) -> List[int]:
        """返回 ODX 中定义的全部服务 SID（升序整数列表，便于和 ECU 能力比对）。"""
        data = self._ensure_odx()
        return sorted(service["sid"] for service in data["services"])

    @keyword("Service Request Template")
    def service_request_template(self, service: Any) -> Dict[str, Any]:
        """按 SID 或服务名查请求模板（返回数据库中的服务定义字典）。

        例：Service Request Template    0x22
            Service Request Template    ReadDataByIdentifier
        """
        data = self._ensure_odx()
        record = self._resolve_service(data, service)
        info(f"服务模板：0x{record['sid']:02X} {record['short_name']}："
             f"请求={record['request']!r}；正响应={record['positive_response']!r}")
        return dict(record)

    @keyword("Comparam")
    def comparam(self, name: str) -> Any:
        """读取 COMPARAM 常量（会话/安全访问子功能/超时/P2 等）。

        例：${ext}=    Comparam    SESSION_EXTENDED    → 3
            ${p2}=     Comparam    P2_MAX_MS            → 50
        """
        data = self._ensure_odx()
        key = str(name).strip().upper()
        record = data["comparams"].get(key)
        if record is None:
            raise AssertionError(
                f"[ODX] 不存在 COMPARAM {name}，已知：{sorted(data['comparams'])}")
        info(f"COMPARAM {key} = {record['value']!r}")
        return record["value"]

    @keyword("Generate Request From ODX")
    def generate_request_from_odx(self, operation: str,
                                  target: Optional[str] = None) -> str:
        """按 ODX 生成常用请求字节（数据驱动测试的入口）。

        operation 支持：
            read_did        target=DID 名称/ID   → 22 + DID
            enter_session   target=SESSION_*     → 10 + 子功能
            clear_dtc       （target 可省）      → 14 + CLEAR_DTC_GROUP
            tester_present  （target 可省）      → 3E 00
        返回无空格大写十六进制字符串，可直接交给 `Send UDS Request`。
        """
        data = self._ensure_odx()
        op = str(operation).strip().lower()
        if op == "read_did":
            if target is None:
                raise AssertionError("[ODX] read_did 需要 target（DID 名称或 ID）")
            return self.did_request_bytes(target)
        if op == "enter_session":
            if target is None:
                raise AssertionError("[ODX] enter_session 需要 target（SESSION_DEFAULT/EXTENDED/PROGRAMMING）")
            session = self._resolve_comparam_value(data, str(target))
            return f"10{session:02X}"
        if op == "clear_dtc":
            group = self._comparam_int(data, "CLEAR_DTC_GROUP", 0xFFFFFF)
            return "14" + group.to_bytes(3, "big").hex().upper()
        if op == "tester_present":
            sub = self._comparam_int(data, "TESTER_PRESENT_SUBFUNC", 0x00)
            return f"3E{sub:02X}"
        raise AssertionError(
            f"[ODX] 不支持的 operation {operation!r}（支持 read_did / enter_session / "
            f"clear_dtc / tester_present）")

    # ==================================================================
    # 执行：通过虚拟 CAN + 诊断仪发请求
    # ==================================================================
    @keyword("Send ODX Request")
    def send_odx_request(self, request_hex: str) -> Dict[str, Any]:
        """发送一条请求并返回结构化响应（数据驱动测试的执行入口）。

        返回：{"positive", "sid", "payload_hex", "nrc", "describe"}
        payload_hex 是"去掉响应 SID 后"的参数（0x22 响应中即 DID+数据），
        方便后续用 Decode DID Response 解码。
        """
        self._ensure_odx()
        tester = self._ensure_tester()
        request = bytes.fromhex(str(request_hex).replace(" ", ""))
        return self._send_validated_request(tester, request)

    def _send_validated_request(self, tester: DiagnosticTester,
                                request: bytes) -> Dict[str, Any]:
        """发请求并校验"响应对应的是本次请求"，对迟到重复响应做重试。

        为什么要这层校验：本项目虚拟 CAN 是多网卡 UDP 组播，**同一帧可能从
        不同网卡回环出多份副本**（项目已知问题：极少数副本会晚于 bus.py 的
        5ms 去重窗口到达）。对 0x22 读 DID 来说，连续读不同 DID 的响应 SID
        都是 0x62，uds_client 的 send_request 只按 SID 匹配 → 迟到的上一帧
        响应会被误当成本次响应（现象：读 VIN 却拿到 F1B0 的数据）。
        本库按"响应 payload 前 2 字节必须等于请求 DID"做二次校验，不符就重试
        （tester.send_request 每次会清空收件箱，重试即重新发请求）。
        """
        expected_did = request[1:3] if len(request) >= 3 and request[0] == 0x22 else None
        last_description = ""
        for attempt in range(1, 4):
            try:
                _ok, response = tester.send_request(request)
            except TimeoutError as exc:
                raise AssertionError(
                    f"[ODX] 发送 {request.hex()} 超时：{exc}\n"
                    f"提示：ECU 是否已启动（Suite Setup Start Vehicle Environment）？"
                ) from exc
            result = {
                "positive": bool(response.positive),
                "sid": int(response.sid),
                "payload_hex": response.payload.hex(),
                "nrc": response.nrc,
                "describe": response.describe(),
            }
            self._last_response = result
            if expected_did is not None and result["positive"]:
                payload = bytes.fromhex(result["payload_hex"])
                if payload[:2] != expected_did:
                    last_description = result["describe"]
                    info(f"[ODX] 第 {attempt} 次收到的响应 {last_description} 与请求 "
                         f"DID {expected_did.hex().upper()} 不符（迟到重复响应），重试")
                    time.sleep(0.04)
                    continue
            info(f"请求 {request.hex()} → {result['describe']}")
            return result
        raise AssertionError(
            f"[ODX] 连续 3 次收到与请求 {request.hex()} 不匹配的响应"
            f"（最后一条：{last_description}）—— 怀疑总线多接口回环重复导致的响应串扰")

    @keyword("Read DID By Name")
    def read_did_by_name(self, did: Any) -> Any:
        """按 DID 名称（或 ID）读并解码，返回物理值（ASCII 字符串 / HEX / int）。

        全程由 ODX 驱动：生成 22 请求 → 发总线 → 按 ODX 定义解码。
        例：${vin}=    Read DID By Name    VIN
        """
        definition = self._resolve_did(did)
        response = self.send_odx_request(self.did_request_bytes(definition["name"]))
        if not response["positive"]:
            raise AssertionError(
                f"[ODX] 读 DID {definition['name']} 返回负响应：{response['describe']}")
        value = self.decode_did_response(definition["name"], response)
        info(f"Read DID By Name {definition['name']} = {value!r}")
        return value

    @keyword("Enter Session By Name")
    def enter_session_by_name(self, session: str) -> str:
        """按 COMPARAM 名称进入诊断会话（数据驱动，不手写 10 03）。

        例：Enter Session By Name    SESSION_EXTENDED
        """
        request = self.generate_request_from_odx("enter_session", session)
        response = self.send_odx_request(request)
        if not response["positive"]:
            raise AssertionError(
                f"[ODX] 进入会话 {session} 失败：{response['describe']}")
        info(f"已进入会话 {session}（请求 {request}）")
        return response["describe"]

    @keyword("Clear All DTCs")
    def clear_all_dtcs(self) -> str:
        """按 ODX 的 CLEAR_DTC_GROUP 生成清码请求（14 FF FF FF）并执行。"""
        request = self.generate_request_from_odx("clear_dtc")
        response = self.send_odx_request(request)
        if not response["positive"]:
            raise AssertionError(f"[ODX] 清除 DTC 失败：{response['describe']}")
        info(f"已按 ODX 生成并执行清码请求：{request}")
        return response["describe"]

    @keyword("Close Odx Tester")
    def close_odx_tester(self) -> None:
        """关闭 ODX 库的诊断仪与总线（Suite Teardown 调用；幂等）。"""
        if self._tester is not None:
            try:
                self._tester.stop()
            except Exception:  # noqa: BLE001 - teardown 不让清理动作失败
                pass
            self._tester = None
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
        info("[OdxLibrary] ODX 诊断仪已关闭")

    # ==================================================================
    # OTX：加载 / 执行
    # ==================================================================
    @keyword("Load OTX")
    def load_otx(self, path: Optional[str] = None) -> Dict[str, Any]:
        """加载并校验 OTX 测试序列文件（默认 simulators/diag/vehicle.otx.xml）。

        返回摘要：序列名、步骤数、步骤列表（id/name/type）。
        """
        target = self._resolve_path(path, self._default_otx)
        data = self._parse_otx_file(target)
        self._otx = data
        self._otx_path = target
        summary = {
            "file": str(target),
            "sequence": data["name"],
            "version": data["version"],
            "step_count": len(data["steps"]),
            "steps": [{"id": step["id"], "name": step["name"], "type": step["type"]}
                      for step in data["steps"]],
        }
        info(f"[OTX] 已加载 {target.name}：序列 {summary['sequence']}，"
             f"{summary['step_count']} 步："
             f"{[(step['id'], step['name']) for step in summary['steps']]}")
        return summary

    @keyword("List OTX Steps")
    def list_otx_steps(self) -> List[Dict[str, Any]]:
        """返回 OTX 序列的步骤列表（只含元信息，不含执行结果）。"""
        data = self._ensure_otx()
        return [{"id": step["id"], "name": step["name"], "type": step["type"],
                 "description": step.get("description", "")} for step in data["steps"]]

    @keyword("Run OTX Sequence")
    def run_otx_sequence(self, path: Optional[str] = None,
                         expect_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """逐步骤执行 OTX 序列（request 发请求 / expect 断言上一条响应）。

        参数：
            path             OTX 文件路径（省略=使用当前已加载序列）
            expect_overrides 负向测试用：按步骤 id 覆盖期望值，例如
                             `Create Dictionary    5=^BAD-VERSION$`。
                             步骤有 pattern 时覆盖 pattern，否则覆盖 expected。

        返回摘要：{"sequence", "total", "passed", "failed", "duration_ms", "results"}；
        任何步骤失败都会抛出 **`[OTX] 第 N 步「步骤名」失败：…`**，
        且已执行步骤的结果保留在 `Get OTX Results` 中（步骤级定位）。
        """
        data = self._ensure_otx()
        if path:
            target = self._resolve_path(path, self._default_otx)
            if self._otx is None or target != self._otx_path:
                self.load_otx(str(target))
                data = self._ensure_otx()
        self._ensure_odx()
        tester = self._ensure_tester()

        overrides = self._normalize_overrides(expect_overrides)
        results: List[Dict[str, Any]] = []
        started = time.monotonic()
        last_response: Optional[Dict[str, Any]] = None

        for step in data["steps"]:
            step_started = time.monotonic()
            result: Dict[str, Any] = {
                "id": step["id"], "name": step["name"], "type": step["type"],
                "status": "PASS", "message": "", "elapsed_ms": 0.0,
            }
            try:
                if step["type"] == "request":
                    request_hex = self._build_request_from_step(step)
                    response = self._execute_request(tester, request_hex)
                    last_response = response
                    result["request"] = request_hex
                    result["response"] = response["describe"]
                    if not response["positive"]:
                        raise AssertionError(f"请求 {request_hex} 返回负响应：{response['describe']}")
                elif step["type"] == "expect":
                    if last_response is None:
                        raise AssertionError("前面没有 request 步骤，无法断言响应")
                    detail = self._check_expect_step(
                        step, last_response, overrides.get(str(step["id"])))
                    result["response"] = last_response["describe"]
                    result["message"] = detail
                else:  # pragma: no cover - 加载时已校验
                    raise AssertionError(f"未知步骤类型 {step['type']}")
            except (AssertionError, TimeoutError) as exc:
                result["status"] = "FAIL"
                result["message"] = str(exc)
                result["elapsed_ms"] = round((time.monotonic() - step_started) * 1000.0, 2)
                results.append(result)
                self._step_results = results
                self._last_summary = self._build_otx_summary(data, results, started)
                raise AssertionError(
                    f"[OTX] 第 {step['id']} 步「{step['name']}」失败：{exc}"
                    f"（已完成 {len(results) - 1} 步）") from None
            result["elapsed_ms"] = round((time.monotonic() - step_started) * 1000.0, 2)
            results.append(result)

        self._step_results = results
        summary = self._build_otx_summary(data, results, started)
        self._last_summary = summary
        info(f"[OTX] 序列 {summary['sequence']} 执行完成："
             f"{summary['passed']}/{summary['total']} 步通过，"
             f"耗时 {summary['duration_ms']}ms")
        if summary["failed"]:
            raise AssertionError(
                f"[OTX] 序列存在 {summary['failed']} 个失败步骤："
                f"{[item['name'] for item in summary['results'] if item['status'] != 'PASS']}")
        return summary

    @keyword("OTX Step Should Have Passed")
    def otx_step_should_have_passed(self, step_id: Any) -> None:
        """断言某一步骤在最近一次 OTX 执行中通过（步骤级断言）。"""
        step = self._find_step_result(step_id)
        if step["status"] != "PASS":
            raise AssertionError(
                f"[OTX] 第 {step['id']} 步「{step['name']}」状态为 {step['status']}："
                f"{step['message']}")
        info(f"OTX 第 {step['id']} 步「{step['name']}」通过 ✓")

    @keyword("OTX Summary")
    def otx_summary(self) -> Dict[str, Any]:
        """返回最近一次 OTX 执行的摘要（无执行记录时报错）。"""
        if self._last_summary is None:
            raise AssertionError("[OTX] 还没有执行过 OTX 序列（先 Run OTX Sequence）")
        return dict(self._last_summary)

    @keyword("Get OTX Results")
    def get_otx_results(self) -> List[Dict[str, Any]]:
        """返回最近一次 OTX 执行的逐步骤结果（失败后仍可查看已执行步骤）。"""
        return [dict(item) for item in self._step_results]

    # ==================================================================
    # 内部：ODX 解析
    # ==================================================================
    def _parse_odx_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise AssertionError(f"[ODX] 文件不存在：{path}")
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise AssertionError(f"[ODX] XML 解析失败（文件损坏？）：{path}：{exc}") from exc
        root = tree.getroot()
        if _local(root.tag) != "ODX":
            raise AssertionError(f"[ODX] 根元素应为 <ODX>，实际 <{_local(root.tag)}>（{path}）")
        file_info: Dict[str, str] = {}
        file_info_elem = _child(root, "FILE-INFO")
        if file_info_elem is not None:
            for name in ("FILE-NAME", "VERSION", "FORMAT", "DESCRIPTION"):
                file_info[name] = _text(file_info_elem, name)

        diag_layer = _child(root, "DIAG-LAYER")
        if diag_layer is None:
            raise AssertionError("[ODX] 缺少 <DIAG-LAYER>（ECU 诊断层定义）")
        variant = _child(diag_layer, "ECU-VARIANT")
        if variant is None:
            raise AssertionError("[ODX] <DIAG-LAYER> 下缺少 <ECU-VARIANT>")
        ecu = {
            "name": variant.get("short_name", "").strip(),
            "long_name": _text(variant, "LONG-NAME"),
            "diag_address": _parse_int(_text(variant, "DIAG-ADDRESS", required=True), "诊断地址"),
            "response_address": _parse_int(_text(variant, "RESPONSE-ADDRESS", required=True), "响应地址"),
            "functional_address": _parse_int(_text(variant, "FUNCTIONAL-ADDRESS", default="0x7DF"), "功能寻址地址"),
            "protocol": _text(variant, "PROTOCOL", default="UDS"),
            "description": _text(variant, "DESCRIPTION"),
        }
        if not ecu["name"]:
            raise AssertionError("[ODX] <ECU-VARIANT> 缺少 short_name 属性")

        dids: List[Dict[str, Any]] = []
        dids_by_name: Dict[str, Dict[str, Any]] = {}
        dids_by_id: Dict[int, Dict[str, Any]] = {}
        dids_container = _child(root, "DATA-IDENTIFIERS")
        if dids_container is None:
            raise AssertionError("[ODX] 缺少 <DATA-IDENTIFIERS>")
        for elem in _children(dids_container, "DATA-IDENTIFIER"):
            name = _text(elem, "SHORT-NAME", required=True)
            id_hex = _text(elem, "ID", required=True).upper()
            did_id = _parse_int(id_hex, f"DID {name} 的 ID")
            encoding = _text(elem, "ENCODING", required=True).upper()
            length = _parse_int(_text(elem, "LENGTH", required=True), f"DID {name} 的 LENGTH")
            did = {
                "name": name,
                "id": did_id,
                "id_hex": f"{did_id:04X}",
                "long_name": _text(elem, "LONG-NAME"),
                "encoding": encoding,
                "length": length,
                "read_only": _to_bool_text(_text(elem, "READ-ONLY", default="true")),
                "description": _text(elem, "DESCRIPTION"),
            }
            if name in dids_by_name:
                raise AssertionError(f"[ODX] DID 名称重复：{name}")
            if did_id in dids_by_id:
                raise AssertionError(f"[ODX] DID ID 重复：{did['id_hex']}")
            dids.append(did)
            dids_by_name[name] = did
            dids_by_id[did_id] = did
        if not dids:
            raise AssertionError("[ODX] <DATA-IDENTIFIERS> 中没有定义任何 DID")

        dtcs: List[Dict[str, Any]] = []
        dtcs_by_code: Dict[str, Dict[str, Any]] = {}
        dtcs_container = _child(root, "DTCS")
        if dtcs_container is None:
            raise AssertionError("[ODX] 缺少 <DTCS>")
        for elem in _children(dtcs_container, "DTC"):
            code = _text(elem, "CODE", required=True).upper()
            if code in dtcs_by_code:
                raise AssertionError(f"[ODX] DTC 编号重复：{code}")
            dtc = {
                "code": code,
                "long_name": _text(elem, "LONG-NAME"),
                "description": _text(elem, "DESCRIPTION"),
                "type": _text(elem, "TYPE", default="2-BYTE-P"),
                "severity": _text(elem, "SEVERITY", default="MEDIUM"),
            }
            dtcs.append(dtc)
            dtcs_by_code[code] = dtc
        if not dtcs:
            raise AssertionError("[ODX] <DTCS> 中没有定义任何 DTC")

        services: List[Dict[str, Any]] = []
        services_by_sid: Dict[int, Dict[str, Any]] = {}
        services_container = _child(root, "SERVICES")
        if services_container is None:
            raise AssertionError("[ODX] 缺少 <SERVICES>")
        for elem in _children(services_container, "SERVICE"):
            short_name = _text(elem, "SHORT-NAME", required=True)
            sid = _parse_int(_text(elem, "SID", required=True), f"服务 {short_name} 的 SID")
            service = {
                "short_name": short_name,
                "sid": sid,
                "request": _text(elem, "REQUEST"),
                "positive_response": _text(elem, "POSITIVE-RESPONSE"),
                "supported_sessions": _text(elem, "SUPPORTED-SESSIONS"),
                "security_required": _to_bool_text(_text(elem, "SECURITY-REQUIRED", default="false")),
            }
            if sid in services_by_sid:
                raise AssertionError(f"[ODX] 服务 SID 重复：0x{sid:02X}")
            services.append(service)
            services_by_sid[sid] = service
        if not services:
            raise AssertionError("[ODX] <SERVICES> 中没有定义任何服务")

        comparams: Dict[str, Dict[str, Any]] = {}
        comparams_container = _child(root, "COMPARAMS")
        if comparams_container is None:
            raise AssertionError("[ODX] 缺少 <COMPARAMS>")
        for elem in _children(comparams_container, "COMPARAM"):
            name = _text(elem, "NAME", required=True).upper()
            if name in comparams:
                raise AssertionError(f"[ODX] COMPARAM 名称重复：{name}")
            raw_value = _text(elem, "VALUE", required=True)
            comparams[name] = {
                "name": name,
                "value": _parse_comparam_value(raw_value),
                "description": _text(elem, "DESCRIPTION"),
            }
        if not comparams:
            raise AssertionError("[ODX] <COMPARAMS> 中没有定义任何 COMPARAM")

        return {
            "file": str(path),
            "path": path,
            "file_info": file_info,
            "ecu": ecu,
            "dids": dids,
            "dids_by_name": dids_by_name,
            "dids_by_id": dids_by_id,
            "dtcs": dtcs,
            "dtcs_by_code": dtcs_by_code,
            "services": services,
            "services_by_sid": services_by_sid,
            "comparams": comparams,
        }

    # ==================================================================
    # 内部：OTX 解析
    # ==================================================================
    def _parse_otx_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise AssertionError(f"[OTX] 文件不存在：{path}")
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise AssertionError(f"[OTX] XML 解析失败（文件损坏？）：{path}：{exc}") from exc
        root = tree.getroot()
        if _local(root.tag) != "test-sequence":
            raise AssertionError(
                f"[OTX] 根元素应为 <test-sequence>，实际 <{_local(root.tag)}>（{path}）")
        sequence_name = root.get("short-name") or root.get("name") or path.stem
        version = root.get("version", "")
        description = _text(root, "description")

        steps: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for elem in root.iter():
            if _local(elem.tag) != "step":
                continue
            raw_id = elem.get("id", "")
            try:
                step_id = int(str(raw_id).strip())
            except ValueError as exc:
                raise AssertionError(f"[OTX] 步骤 id 不是整数：{raw_id!r}") from exc
            step_type = (elem.get("type") or "").strip().lower()
            if step_type not in VALID_STEP_TYPES:
                raise AssertionError(
                    f"[OTX] 第 {step_id} 步 type={step_type!r} 非法（只支持 request/expect）")
            name = (elem.get("name") or f"步骤{step_id}").strip()
            if step_id in seen_ids:
                raise AssertionError(f"[OTX] 步骤 id 重复：{step_id}")
            seen_ids.add(step_id)
            step: Dict[str, Any] = {
                "id": step_id,
                "name": name,
                "type": step_type,
                "description": _text(elem, "description"),
                "service": elem.get("service"),
                "subfunction": elem.get("subfunction"),
                "did": elem.get("did"),
                "raw": elem.get("raw"),
                "encoding": (elem.get("encoding") or "").upper(),
                "length": elem.get("length"),
                "pattern": elem.get("pattern"),
                "expected": elem.get("expected"),
            }
            if step_type == "request" and not (step["service"] or step["raw"]):
                raise AssertionError(
                    f"[OTX] 第 {step_id} 步（request）缺少 service 或 raw 属性")
            if step_type == "expect":
                if not (step["pattern"] or step["expected"] or step["length"]
                        or step["encoding"]):
                    raise AssertionError(
                        f"[OTX] 第 {step_id} 步（expect）缺少期望条件"
                        f"（pattern/expected/length/encoding 至少一个）")
            steps.append(step)
        if not steps:
            raise AssertionError(f"[OTX] 序列中没有 step：{path}")
        steps.sort(key=lambda item: item["id"])
        return {
            "file": str(path),
            "path": path,
            "name": sequence_name,
            "version": version,
            "description": description,
            "steps": steps,
        }

    # ==================================================================
    # 内部：OTX 执行
    # ==================================================================
    def _normalize_overrides(self, overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if overrides in (None, ""):
            return {}
        if not isinstance(overrides, dict):
            raise AssertionError(
                "[OTX] expect_overrides 应为字典（步骤 id → 覆盖后的期望），"
                f"收到 {type(overrides).__name__}")
        return {str(key): value for key, value in overrides.items()}

    def _build_request_from_step(self, step: Dict[str, Any]) -> str:
        raw = step.get("raw")
        if raw:
            return str(raw).replace(" ", "").upper()
        sid = _parse_int(step["service"], f"步骤 {step['id']} 的 service")
        parts = [f"{sid:02X}"]
        subfunction = step.get("subfunction")
        if subfunction not in (None, ""):
            parts.append(f"{_parse_int(subfunction, 'subfunction'):02X}")
        did = step.get("did")
        if did not in (None, ""):
            definition = self._resolve_did(did)
            parts.append(definition["id_hex"])
        return "".join(parts)

    def _execute_request(self, tester: DiagnosticTester,
                         request_hex: str) -> Dict[str, Any]:
        request = bytes.fromhex(request_hex)
        result = self._send_validated_request(tester, request)
        info(f"OTX 请求 {request_hex} → {result['describe']}")
        return result

    def _check_expect_step(self, step: Dict[str, Any], response: Dict[str, Any],
                           override: Any = None) -> str:
        if not response["positive"]:
            raise AssertionError(f"上一条响应为负响应：{response['describe']}")
        expected_sid = _parse_int(step["service"], "expect service") + 0x40 \
            if step.get("service") else None
        if expected_sid is not None and int(response["sid"]) != expected_sid:
            raise AssertionError(
                f"响应 SID 期望 0x{expected_sid:02X}，实际 0x{int(response['sid']):02X}")
        payload = bytes.fromhex(response["payload_hex"])
        did = step.get("did")
        if did not in (None, ""):
            definition = self._resolve_did(did)
            did_bytes = definition["id"].to_bytes(2, "big")
            if payload[:2] != did_bytes:
                raise AssertionError(
                    f"响应 DID 期望 {definition['id_hex']}，实际 {payload[:2].hex().upper()}")
            data = payload[2:]
        else:
            data = payload

        pattern = step.get("pattern")
        expected = step.get("expected")
        length = step.get("length")
        encoding = step.get("encoding") or ""
        if override not in (None, ""):
            override_text = str(override)
            if pattern:
                pattern = override_text
                info(f"OTX 第 {step['id']} 步期望 pattern 被覆盖为 {override_text!r}")
            else:
                expected = override_text
                info(f"OTX 第 {step['id']} 步期望 expected 被覆盖为 {override_text!r}")

        if encoding == "ASCII":
            try:
                text = data.decode("ascii")
            except UnicodeDecodeError as exc:
                raise AssertionError(
                    f"响应按 ASCII 解码失败：{data.hex()}：{exc}") from exc
            if length not in (None, "") and len(text) != _parse_int(length, "length"):
                raise AssertionError(
                    f"ASCII 长度期望 {length}，实际 {len(text)}（text={text!r}）")
            if expected is not None and text != expected:
                raise AssertionError(f"ASCII 期望 {expected!r}，实际 {text!r}")
            if pattern:
                if re.fullmatch(str(pattern), text) is None:
                    raise AssertionError(f"ASCII 不匹配正则 {pattern!r}，实际 {text!r}")
            return f"ASCII 校验通过（{text!r}，长度 {len(text)}）"
        if encoding == "HEX":
            hex_text = data.hex()
            if length not in (None, "") and len(data) != _parse_int(length, "length"):
                raise AssertionError(
                    f"HEX 长度期望 {length} 字节，实际 {len(data)} 字节（{hex_text}）")
            if expected is not None and hex_text.lower() != str(expected).replace(" ", "").lower():
                raise AssertionError(f"HEX 期望 {expected}，实际 {hex_text}")
            if pattern and re.fullmatch(str(pattern), hex_text) is None:
                raise AssertionError(f"HEX 不匹配正则 {pattern!r}，实际 {hex_text}")
            return f"HEX 校验通过（{hex_text}）"
        # 未指定 encoding：只做长度/期望的原始字节比较
        if length not in (None, "") and len(data) != _parse_int(length, "length"):
            raise AssertionError(f"payload 长度期望 {length}，实际 {len(data)}")
        if expected is not None and data.hex() != str(expected).replace(" ", "").lower():
            raise AssertionError(f"payload 期望 {expected}，实际 {data.hex()}")
        return f"payload 校验通过（{data.hex()}，{len(data)} 字节）"

    def _build_otx_summary(self, data: Dict[str, Any], results: List[Dict[str, Any]],
                           started: float) -> Dict[str, Any]:
        passed = sum(1 for item in results if item["status"] == "PASS")
        return {
            "sequence": data["name"],
            "total": len(data["steps"]),
            "passed": passed,
            "failed": len(results) - passed,
            "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
            "results": [dict(item) for item in results],
        }

    def _find_step_result(self, step_id: Any) -> Dict[str, Any]:
        if not self._step_results:
            raise AssertionError("[OTX] 还没有执行过 OTX 序列（先 Run OTX Sequence）")
        target = _parse_int(step_id, "step_id")
        for item in self._step_results:
            if int(item["id"]) == target:
                return item
        raise AssertionError(f"[OTX] 最近一次执行中没有第 {target} 步")

    # ==================================================================
    # 内部：解析/加载辅助
    # ==================================================================
    def _resolve_path(self, path: Optional[str], default: Path) -> Path:
        if path in (None, ""):
            return default
        candidate = Path(str(path))
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        return candidate

    def _ensure_odx(self) -> Dict[str, Any]:
        if self._odx is None:
            self.load_odx(str(self._default_odx))
        assert self._odx is not None
        return self._odx

    def _ensure_otx(self) -> Dict[str, Any]:
        if self._otx is None:
            self.load_otx(str(self._default_otx))
        assert self._otx is not None
        return self._otx

    def _ensure_tester(self) -> DiagnosticTester:
        if self._tester is None:
            self._bus = VirtualCanBus("ODX-TESTER", echo_own=False)
            self._tester = DiagnosticTester(self._bus)
            self._tester.start()
            console("[OdxLibrary] ODX 诊断仪已连接虚拟 CAN（0x7E0 → 0x7E8）")
        return self._tester

    def _resolve_did(self, did: Any) -> Dict[str, Any]:
        data = self._ensure_odx()
        text = str(did).strip()
        if text.upper() in data["dids_by_name"]:
            return data["dids_by_name"][text.upper()]
        # 按名称（大小写不敏感）匹配
        for name, definition in data["dids_by_name"].items():
            if name.lower() == text.lower():
                return definition
        normalized = text.replace("0x", "").replace(" ", "").upper()
        try:
            did_id = int(normalized, 16)
        except ValueError as exc:
            raise AssertionError(
                f"[ODX] 无法解析 DID {did!r}（支持名称或 4 位十六进制 ID）") from exc
        definition = data["dids_by_id"].get(did_id)
        if definition is None:
            raise AssertionError(
                f"[ODX] 数据库中不存在 DID {text}，已知："
                f"{[(item['name'], item['id_hex']) for item in data['dids']]}")
        return definition

    @staticmethod
    def _extract_did_payload(definition: Dict[str, Any], response: Any) -> bytes:
        if isinstance(response, dict):
            if not response.get("positive", True):
                raise AssertionError(
                    f"[ODX] 读 DID {definition['name']} 的响应是负响应：{response.get('describe')}")
            raw_hex = str(response.get("payload_hex", ""))
        else:
            raw_hex = str(response)
        raw_hex = raw_hex.replace(" ", "").replace("0x", "").upper()
        if not raw_hex:
            raise AssertionError(f"[ODX] DID {definition['name']} 响应为空")
        if raw_hex.startswith("7F"):
            nrc = raw_hex[4:6] if len(raw_hex) >= 6 else "??"
            raise AssertionError(
                f"[ODX] DID {definition['name']} 响应为负响应 NRC=0x{nrc}")
        did_hex = definition["id_hex"]
        if raw_hex.startswith("62"):
            body = raw_hex[2:]
            if body[:4].upper() != did_hex:
                raise AssertionError(
                    f"[ODX] 响应 DID 前缀 {body[:4]} 与期望 {did_hex} 不一致")
            data_hex = body[4:]
        elif raw_hex[:4].upper() == did_hex:
            # Send ODX Request 的 payload_hex 已去掉响应 SID，但保留 DID 两字节
            data_hex = raw_hex[4:]
        else:
            data_hex = raw_hex
        if len(data_hex) % 2:
            raise AssertionError(f"[ODX] 响应数据 hex 长度为奇数：{data_hex}")
        return bytes.fromhex(data_hex)

    @staticmethod
    def _resolve_service(data: Dict[str, Any], service: Any) -> Dict[str, Any]:
        text = str(service).strip()
        if text.upper() in ("",):
            raise AssertionError("[ODX] 服务不能为空")
        try:
            sid = _parse_int(text, "服务 SID") if not text.isalpha() else None
        except AssertionError:
            sid = None
        if sid is not None:
            record = data["services_by_sid"].get(sid)
            if record is None:
                raise AssertionError(
                    f"[ODX] 数据库中不存在 SID 0x{sid:02X}，已知："
                    f"{[f'0x{item['sid']:02X}' for item in data['services']]}")
            return record
        for record in data["services"]:
            if record["short_name"].lower() == text.lower():
                return record
        raise AssertionError(
            f"[ODX] 数据库中不存在服务 {text!r}，已知："
            f"{[f'0x{item['sid']:02X} {item['short_name']}' for item in data['services']]}")

    def _resolve_comparam_value(self, data: Dict[str, Any], name: str) -> int:
        key = str(name).strip().upper()
        record = data["comparams"].get(key)
        if record is None:
            # 允许直接传会话名：DEFAULT / EXTENDED / PROGRAMMING
            alias = f"SESSION_{key}" if not key.startswith("SESSION_") else key
            record = data["comparams"].get(alias)
        if record is None:
            raise AssertionError(
                f"[ODX] 不存在 COMPARAM {name}（会话名可写 DEFAULT/EXTENDED/PROGRAMMING "
                f"或 SESSION_*），已知：{sorted(data['comparams'])}")
        return int(record["value"])

    @staticmethod
    def _comparam_int(data: Dict[str, Any], name: str, default: int) -> int:
        record = data["comparams"].get(name)
        return int(record["value"]) if record is not None else default

    def __del__(self) -> None:  # pragma: no cover - 兜底清理
        try:
            self.close_odx_tester()
        except Exception:  # noqa: BLE001
            pass


def _parse_comparam_value(text: str) -> Any:
    """COMPARAM 值：数字优先按整数（支持 0x 前缀），否则保留字符串。"""
    value = str(text).strip()
    try:
        return int(value, 0)
    except ValueError:
        return value


if __name__ == "__main__":
    # 手工自测：python libraries/OdxLibrary.py（纯解析，不需要 ECU）
    library = OdxLibrary()
    print("ODX 摘要:", library.load_odx())
    print("结构校验:", library.odx_should_be_valid())
    print("ECU:", library.get_ecu_info())
    print("DID:", [(item["name"], item["id_hex"], item["encoding"], item["length"])
                   for item in library.list_data_identifiers()])
    print("DTC:", [item["code"] for item in library.list_dtcs()])
    print("服务 SID:", [f"0x{sid:02X}" for sid in library.list_service_sids()])
    print("COMPARAM 扩展会话:", library.comparam("SESSION_EXTENDED"))
    print("VIN 请求:", library.did_request_bytes("VIN"))
    print("OTX 摘要:", library.load_otx())
