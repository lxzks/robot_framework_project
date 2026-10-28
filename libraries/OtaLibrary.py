"""
OtaLibrary.py — OTA + Bootloader 完整刷写链路测试库
====================================================

把"OTA 云端下载 → OTA Agent 校验 → 预编程检查 → 编程会话/安全访问
→ 0x34/0x36/0x37 分块刷写 → 31 01 FF00 完整性校验 → 0x11 复位激活"
这条真实量产链路，做成可以在纯软件仿真里跑通的 Robot Framework 关键字。

三个角色（对应真实架构）：

    ┌──────────────┐  TCP JSON   ┌───────────────────┐  UDS over CAN   ┌─────────┐
    │ OTA 云端 mock │ ◄─────────► │ OtaLibrary（本库） │ ◄─────────────► │ ECU 仿真 │
    │ ota_server.py │  manifest   │ = OTA Agent + 诊断仪 │  0x7E0/0x7E8   │ Bootloader│
    └──────────────┘  download   └───────────────────┘                  └─────────┘
                      inject

覆盖的测试场景（与 tests/robot/17_ota_bootloader.robot 一一对应）：
    - manifest 获取与校验（版本/大小/CRC/兼容性）
    - 预编程条件检查（车速=0、电压正常）→ 不满足回 NRC 0x22
    - 正常刷写：0x34 → 0x36 分块 → 0x37 → 31 01 FF00（长度 + CRC32）
    - A/B 分区语义：成功后"待激活"，0x11 复位才更新版本；失败/断电保持旧版本
    - 坏包拒绝：CRC 错 / 大小不符 / 版本不兼容（OTA Agent 先校验；ECU 再兜底 CRC）
    - 断点续传：按 offset 分块下载（OTA 云端 Range 语义）
    - 块重传：模拟丢块 → ECU 回 NRC 0x73 → 重发正确块继续

版本 DID（ECU 侧，见 simulators/ecu_sim.py）：
    F195 应用软件版本（刷写成功 + 复位后更新为包版本）
    F1A3 Bootloader 版本 / F186 硬件版本
    F1A1 目标版本暂存（0x2E 写） / F1A2 期望 CRC32（0x2E 写，FF00 校验用）

关键字命名与 DiagnosticLibrary 有少量重名（如 `Response Should Be Positive`
不存在于本库——本库不做通用断言），本库自含隧道与断言，避免跨库歧义。

用法（套件里）::

    Resource    ../../resources/common.resource
    Library     ../../libraries/OtaLibrary.py

    Suite Setup     Start Vehicle Environment
    Suite Setup     Start OTA Server
    Suite Teardown  Stop OTA Server
    Suite Teardown  Close OTA Tester
    Suite Teardown  Stop Vehicle Environment

    ${m}=      Get OTA Manifest
    ${sum}=    Flash OTA Image
    ECU Reset
    Software Version Should Be    v1.1.0
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import zlib
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                                   # noqa: E402
from uds_client import SECURITY_XOR_KEY, DiagnosticTester       # noqa: E402

# 与 simulators/ecu_sim.py 对齐
FLASH_ADDRESS = 0x08000000          # 教学 Flash App 分区起始地址
DEFAULT_BLOCK_SIZE = 64             # 与 ota_server.py 的 BLOCK_SIZE 对齐
OTA_SERVER_SCRIPT = Path(__file__).parent.parent / "simulators" / "ota_server.py"
DEFAULT_OTA_PORT = 8080


class OtaLibrary:
    """OTA 云端 + Bootloader 刷写链路关键字库（SUITE 作用域）"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"
    FLASH_ADDRESS = FLASH_ADDRESS
    DEFAULT_BLOCK_SIZE = DEFAULT_BLOCK_SIZE

    def __init__(self) -> None:
        self._port = int(os.environ.get("OTA_HTTP_PORT", str(DEFAULT_OTA_PORT)))
        self._proc: subprocess.Popen | None = None
        self._log_handle = None
        self._sock: socket.socket | None = None
        self._rfile = None
        self._bus: VirtualCanBus | None = None
        self._tester: DiagnosticTester | None = None
        self._last_response = None
        self._manifest: dict | None = None

    # ==================================================================
    # 一、OTA 服务进程生命周期
    # ==================================================================
    def _close_log_handle(self) -> None:
        handle, self._log_handle = self._log_handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _terminate_ota_process(self, grace: float = 5.0) -> None:
        """回收 OTA 服务子进程（terminate → wait → kill 兜底）并关闭日志句柄。

        启动失败路径也必须调用它：否则进程没死透会一直占着 8080，
        下一次 Start OTA Server 会因端口被占用失败（交叉审查发现的资源泄漏）。
        """
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    info("[OtaLibrary] ⚠ OTA 服务进程未能回收（kill 后仍超时）")
        self._close_log_handle()

    @keyword("Start OTA Server")
    def start_ota_server(self, port=None) -> None:
        """启动 OTA 云端 mock 子进程（TCP 127.0.0.1:8080，可传 port 覆盖）。

        启动后通过 TCP 发 ping 确认服务就绪；若端口被占用/进程启动失败，
        会给出清晰错误（而不是等 3 秒超时后一句「无响应」）。
        启动失败会回收子进程与日志句柄，不会留下占端口的孤儿进程。
        """
        if self._proc is not None and self._proc.poll() is None:
            info("OTA 服务已在运行（复用现有进程）")
            return
        if port is not None:
            self._port = int(port)
        if not OTA_SERVER_SCRIPT.exists():
            raise AssertionError(f"找不到 OTA 服务脚本: {OTA_SERVER_SCRIPT}")
        log_dir = Path(__file__).parent.parent / "results"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / "ota_server_stdout.log"
        env = dict(os.environ, OTA_HTTP_PORT=str(self._port))
        self._log_handle = open(log_path, "w", encoding="utf-8")
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(OTA_SERVER_SCRIPT), "--port", str(self._port)],
                env=env, stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    tail = ""
                    try:
                        tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                    except OSError:
                        pass
                    raise AssertionError(
                        f"OTA 服务启动失败（退出码 {self._proc.returncode}，"
                        f"端口 {self._port} 可能被占用）: {tail}")
                try:
                    self._ota_request({"cmd": "ping"})
                    console(f"[OtaLibrary] OTA 云端 mock 已就绪: 127.0.0.1:{self._port}")
                    return
                except OSError:
                    time.sleep(0.2)
            raise AssertionError(f"OTA 服务 10s 内未就绪（端口 {self._port}）")
        except BaseException:
            # 启动失败：连接/子进程/日志句柄全部回收（不把问题留给下一个套件）
            self._close_connection()
            self._terminate_ota_process()
            raise

    @keyword("Stop OTA Server")
    def stop_ota_server(self) -> None:
        """关闭 OTA 客户端连接并回收 OTA 服务子进程（terminate + wait + kill）。"""
        try:
            if self._sock is not None:
                try:
                    self._ota_request({"cmd": "quit"})
                except (OSError, AssertionError):
                    pass
        finally:
            self._close_connection()
        self._terminate_ota_process()
        info("OTA 云端 mock 已停止")

    # ==================================================================
    # 二、OTA 云端 TCP 客户端（JSON 行协议）
    # ==================================================================
    def _connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.create_connection(("127.0.0.1", self._port), timeout=3)
        sock.settimeout(10)
        self._sock = sock
        self._rfile = sock.makefile("rwb")

    def _close_connection(self) -> None:
        if self._rfile is not None:
            try:
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _ota_request(self, payload: dict) -> dict:
        """发一条 JSON 命令并读回响应（断线自动重连一次）。"""
        for attempt in (1, 2):
            try:
                self._connect()
                assert self._sock is not None and self._rfile is not None
                self._sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                raw = self._rfile.readline()
                if not raw:
                    raise OSError("OTA 服务连接被关闭")
                resp = json.loads(raw.decode("utf-8"))
                if not resp.get("ok"):
                    raise AssertionError(f"OTA 云命令失败 {payload}: {resp}")
                return resp
            except OSError:
                self._close_connection()
                if attempt == 2:
                    raise AssertionError(f"无法连接 OTA 服务 127.0.0.1:{self._port}")
                time.sleep(0.3)
        raise AssertionError("unreachable")

    @keyword("Get OTA Manifest")
    def get_ota_manifest(self) -> dict:
        """获取 OTA 固件包元数据（版本/大小/分块/CRC32/兼容性/回滚标志）。"""
        resp = self._ota_request({"cmd": "manifest"})
        self._manifest = dict(resp["manifest"])
        info(f"OTA manifest: {self._manifest}")
        return self._manifest

    @keyword("Download OTA Image")
    def download_ota_image(self, start_offset=0, length=None) -> str:
        """从 OTA 云端分块下载固件镜像，返回镜像 hex。

        参数：
            start_offset  起始偏移（默认 0；>0 表示"断点续传"从中间继续）
            length        下载长度（默认下载到镜像末尾）

        校验：未指定 length 时，下载量必须与 manifest 声明的 size 一致，
        否则报"固件大小不符"（对应坏包注入 size）。
        """
        manifest = self.get_ota_manifest()
        size = int(manifest["size"])
        block = int(manifest.get("block_size", DEFAULT_BLOCK_SIZE))
        start = int(start_offset)
        end = size if length is None else min(size, start + int(length))
        chunks: list[bytes] = []
        offset = start
        while offset < end:
            want = min(block, end - offset)
            resp = self._ota_request(
                {"cmd": "download", "offset": offset, "length": want})
            data = bytes.fromhex(resp["data"])
            if not data:
                break
            chunks.append(data)
            offset += len(data)
        image = b"".join(chunks)
        if length is None and len(image) != size - start:
            raise AssertionError(
                f"OTA 固件大小不符：manifest 声明 {size}B（从 {start} 起应下载 "
                f"{size - start}B），实际只拿到 {len(image)}B")
        info(f"OTA 下载完成: offset={start} 长度={len(image)}B")
        return image.hex()

    @keyword("Check OTA Image Integrity")
    def check_ota_image_integrity(self) -> bool:
        """下载完整镜像并校验大小与 CRC32（OTA Agent 的第一道包校验）。"""
        manifest = self.get_ota_manifest()
        image = bytes.fromhex(self.download_ota_image())
        self._validate_image(manifest, image)
        info(f"OTA 镜像校验通过: {len(image)}B，CRC32 与 manifest 一致")
        return True

    @keyword("Inject Bad Image")
    def inject_bad_image(self, kind: str) -> None:
        """让 OTA 云端返回坏包。kind 支持：

        - ``crc``     ：CRC 错（数据正常，manifest 的 crc32 被改坏）
        - ``size``    ：大小不符（manifest 说 512B，云端只给 400B）
        - ``version`` ：版本不兼容（compatible=false，要求更高 Bootloader）
        - ``none``    ：恢复正常包
        """
        mapping = {"crc": "crc", "size": "size", "version": "version",
                   "大小": "size", "版本": "version", "none": "none"}
        inject = mapping.get(str(kind).strip().lower())
        if inject is None:
            raise AssertionError(f"未知坏包类型 {kind!r}，可选: crc / size / version")
        if inject == "none":
            self.clear_ota_injection()
            return
        resp = self._ota_request({"cmd": "inject", "kind": inject})
        info(f"已注入坏包: {resp.get('injection')}")

    @keyword("Clear OTA Injection")
    def clear_ota_injection(self) -> None:
        """恢复正常固件包（撤销坏包注入）。"""
        self._ota_request({"cmd": "reset"})
        info("OTA 固件包已恢复正常")

    @keyword("Get OTA Stats")
    def get_ota_stats(self) -> dict:
        """获取 OTA 云端下载统计（请求次数/字节数/当前注入状态）。"""
        resp = self._ota_request({"cmd": "stats"})
        return dict(resp["stats"])

    # ==================================================================
    # 三、UDS 诊断通道（OTA Agent 与 ECU 的 Bootloader 交互）
    # ==================================================================
    def _ensure_tester(self) -> None:
        if self._tester is not None:
            return
        self._bus = VirtualCanBus("RF-OTA", echo_own=False)
        self._tester = DiagnosticTester(self._bus)
        self._tester.start()
        console("[OtaLibrary] OTA 诊断通道（0x7E0/0x7E8）已就绪")

    def _send_bytes(self, request: bytes, timeout: float = 5.0):
        self._ensure_tester()
        assert self._tester is not None
        ok, resp = self._tester.send_request(request, timeout=timeout)
        self._last_response = resp
        return ok, resp

    def _send_hex(self, request_hex: str, timeout: float = 5.0):
        return self._send_bytes(
            bytes.fromhex(request_hex.replace(" ", "")), timeout=timeout)

    def _assert_positive(self, context: str = "") -> None:
        resp = self._last_response
        if resp is None or not resp.positive:
            raise AssertionError(f"{context}: 期望正响应，实际 {resp.describe() if resp else '无响应'}")

    def _read_did_ascii(self, did_hex: str) -> str:
        ok, resp = self._send_hex(f"22 {did_hex}")
        if not ok:
            raise AssertionError(f"读 DID {did_hex} 失败: {resp.describe()}")
        return resp.payload[2:].decode("ascii", errors="replace")

    def _security_unlock(self, attempts: int = 2) -> None:
        """安全访问：种子-密钥握手（带种子校验与失败重试）。

        为什么要重试/校验：本套件环境里有**两个诊断仪实例**（common.resource 的
        DiagnosticLibrary + 本库的 OTA 诊断仪）同时监听 0x7E8，而 27 01/27 02 的
        正响应 SID 都是 0x67。UDP 组播下迟到/重复响应可能让 27 01 读到非本次请求
        的 0x67 响应（例如上一条 27 02 的 payload=02），表现为随机 NRC 0x35
        invalidKey（交叉审查实跑中复现的 flaky）。这里校验种子长度、失败自动
        重取种子重试，并在错误里带种子信息，避免把问题留成偶发失败。
        """
        total = max(1, int(attempts))
        last_error = ""
        for attempt in range(1, total + 1):
            ok, resp = self._send_hex("27 01")
            if not ok:
                raise AssertionError(f"请求种子失败: {resp.describe()}")
            seed = resp.payload[1:5]
            if len(seed) != 4:
                last_error = (f"种子响应格式异常（payload={resp.payload.hex()}，"
                              f"期望 0x01+4 字节种子）")
                info(f"安全访问第 {attempt} 次：{last_error}，重取种子重试")
                time.sleep(0.05)
                continue
            key = (int.from_bytes(seed, "big") ^ SECURITY_XOR_KEY).to_bytes(4, "big")
            info(f"安全访问种子 {seed.hex()} → 密钥 {key.hex()}（第 {attempt} 次）")
            ok, resp = self._send_hex("27 02 " + key.hex())
            if ok:
                return
            last_error = resp.describe()
            if resp.nrc == 0x36:      # 已进入锁定：继续重试只会一直失败
                raise AssertionError(f"安全解锁失败（安全访问已锁定）: {last_error}")
            info(f"安全访问第 {attempt} 次失败：{last_error}，重取种子重试")
            time.sleep(0.05)
        raise AssertionError(
            f"安全解锁失败（{total} 次尝试）: {last_error}")

    def _write_did(self, did_hex: str, value: bytes) -> None:
        ok, resp = self._send_hex(f"2E {did_hex} {value.hex()}")
        if not ok:
            raise AssertionError(f"写 DID {did_hex} 失败: {resp.describe()}")

    # ==================================================================
    # 四、Bootloader 三阶段关键字
    # ==================================================================
    @keyword("Check Preprogramming Conditions")
    def check_preprogramming_conditions(self) -> bool:
        """预编程条件检查（31 01 0203）——需先处于扩展会话。

        条件（ECU 侧实现）：车速 = 0、蓄电池电压 10.5~15.5V、无未完成下载。
        不满足时 ECU 回 NRC 0x22，此关键字会抛 AssertionError 并带原因提示。
        """
        ok, resp = self._send_hex("31 01 02 03")
        if not ok:
            hint = "（若 NRC=0x7F 请先 Enter Extended Session）" \
                if resp.nrc == 0x7F else ""
            raise AssertionError(f"预编程条件检查失败{hint}: {resp.describe()}")
        info("预编程条件检查通过（车速=0、电压正常）")
        return True

    @keyword("Preprogramming Conditions Should Be Rejected")
    def preprogramming_conditions_should_be_rejected(self) -> None:
        """断言预编程条件**不满足**（ECU 应回 NRC 0x22 conditionsNotCorrect）。"""
        ok, resp = self._send_hex("31 01 02 03")
        if ok:
            raise AssertionError("预编程条件检查居然通过了（期望被拒绝）")
        if resp.nrc != 0x22:
            raise AssertionError(
                f"期望 NRC 0x22 conditionsNotCorrect，实际 {resp.describe()}")
        info("预编程条件被正确拒绝（NRC 0x22）")

    @keyword("Enter Programming")
    def enter_programming(self) -> None:
        """进入编程会话（10 02）——Bootloader 刷写的前置会话。"""
        ok, resp = self._send_hex("10 02")
        if not ok:
            raise AssertionError(f"进入编程会话失败: {resp.describe()}")
        info("已进入编程会话（0x02）")

    @keyword("ECU Reset")
    def ecu_reset(self) -> None:
        """ECU 复位（11 01）。

        OTA 语义：刷写成功后的新版本在复位后才"激活"（A/B 分区切换）；
        刷写失败/中断则复位后仍是旧版本（回滚）。
        """
        ok, resp = self._send_hex("11 01")
        if not ok:
            raise AssertionError(f"ECU 复位失败: {resp.describe()}")
        info("ECU 已复位（11 01）")

    @keyword("Read Software Version")
    def read_software_version(self) -> str:
        """读应用软件版本（22 F195，如 v1.0.0 / v1.1.0）。"""
        version = self._read_did_ascii("F195")
        info(f"应用软件版本 = {version}")
        return version

    @keyword("Read Bootloader Version")
    def read_bootloader_version(self) -> str:
        """读 Bootloader 版本（22 F1A3）。"""
        version = self._read_did_ascii("F1A3")
        info(f"Bootloader 版本 = {version}")
        return version

    @keyword("Read Hardware Version")
    def read_hardware_version(self) -> str:
        """读硬件版本（22 F186）。"""
        version = self._read_did_ascii("F186")
        info(f"硬件版本 = {version}")
        return version

    @keyword("Software Version Should Be")
    def software_version_should_be(self, expected: str) -> None:
        """断言当前应用软件版本等于期望值（如 v1.1.0）。"""
        actual = self.read_software_version()
        if actual != str(expected):
            raise AssertionError(f"软件版本 {actual!r} ≠ 期望 {str(expected)!r}")
        info(f"软件版本 = {actual} ✓")

    @keyword("Should Rollback To Version")
    def should_rollback_to_version(self, expected: str) -> None:
        """断言刷写失败/中断后回滚到了旧版本（A/B 的旧槽仍可运行）。"""
        actual = self.read_software_version()
        if actual != str(expected):
            raise AssertionError(
                f"回滚失败：期望旧版本 {str(expected)!r}，实际 {actual!r}")
        info(f"已回滚到旧版本 {actual} ✓（A/B 旧槽可运行）")

    @keyword("Inject Precheck Speed")
    def inject_precheck_speed(self, speed_kmh) -> None:
        """设置"预编程检查用模拟车速"（工程 DID F1B0 byte3）。

        自动化测试无法真的让车跑起来；写 byte3=30 就能验证
        "车速非 0 → 预编程检查拒绝（NRC 0x22）"分支。写 0 清除。
        前提：扩展会话 + 安全解锁（权限与其他 0x2E 一致）。
        """
        speed = int(speed_kmh) & 0xFF
        ok, resp = self._send_hex(
            f"2E F1 B0 0E 00 00 {speed:02X}")
        if not ok:
            raise AssertionError(
                f"注入预编程模拟车速失败（需扩展会话+解锁）: {resp.describe()}")
        info(f"预编程检查模拟车速 = {speed} km/h（0=用真实车速）")

    # ==================================================================
    # 五、包校验 + 刷写主链路（内部共用）
    # ==================================================================
    def _validate_image(self, manifest: dict, image: bytes) -> None:
        """OTA Agent 侧包校验：兼容性 → 大小 → CRC32 → Bootloader 版本要求。

        这是"坏包不进车"的第一道防线（真实项目还有签名校验，见 docs/35）。
        """
        if not bool(manifest.get("compatible", True)):
            raise AssertionError(
                f"固件包版本不兼容（manifest compatible=false，"
                f"目标版本 {manifest.get('version')}）")
        size = int(manifest["size"])
        if len(image) != size:
            raise AssertionError(
                f"固件大小不符：manifest 声明 {size}B，实际 {len(image)}B")
        crc_expect = int(str(manifest["crc32"]), 0)
        crc_actual = zlib.crc32(image) & 0xFFFFFFFF
        if crc_actual != crc_expect:
            raise AssertionError(
                f"固件 CRC 校验失败：manifest={crc_expect:#010x}，"
                f"实际={crc_actual:#010x}")
        min_bl = str(manifest.get("min_bootloader", "") or "")
        if min_bl:
            ecu_bl = self._read_did_ascii("F1A3")
            if ecu_bl < min_bl:      # 教学版：BL-x.y.z 字典序比较已足够
                raise AssertionError(
                    f"Bootloader 版本不兼容：ECU {ecu_bl} < 包要求 {min_bl}")

    def _start_download_session(self, manifest: dict, image: bytes):
        """执行刷写前置阶段并发出 0x34，返回 (max_block, block_size)。"""
        # 1) 预编程阶段：扩展会话 → 31 01 0203 条件检查
        ok, resp = self._send_hex("10 03")
        if not ok:
            raise AssertionError(f"进入扩展会话失败: {resp.describe()}")
        self.check_preprogramming_conditions()
        # 2) 编程阶段：编程会话 + 安全访问 + 31 01 0202 依赖检查
        self.enter_programming()
        self._security_unlock()
        ok, resp = self._send_hex("31 01 02 02")
        if not ok:
            raise AssertionError(f"编程依赖检查失败: {resp.describe()}")
        # 3) 暂存目标版本与期望 CRC32（ECU 的 31 01 FF00 用它做第二道校验）
        version = str(manifest["version"]).encode("ascii", errors="replace")
        self._write_did("F1A1", version)
        self._write_did("F1A2", int(str(manifest["crc32"]), 0).to_bytes(4, "big"))
        # 4) 0x34 RequestDownload
        size = len(image)
        req = (bytes([0x34, 0x00, 0x44])
               + FLASH_ADDRESS.to_bytes(4, "big") + size.to_bytes(4, "big"))
        ok, resp = self._send_bytes(req)
        if not ok:
            raise AssertionError(f"0x34 请求下载被拒: {resp.describe()}")
        max_block = int.from_bytes(resp.payload[1:3], "big") \
            if len(resp.payload) >= 3 else 1024
        block_size = min(int(manifest.get("block_size", DEFAULT_BLOCK_SIZE)),
                         max_block)
        info(f"0x34 通过：地址 {FLASH_ADDRESS:#010x} 大小 {size}B，"
             f"ECU 单块上限 {max_block}B，本次块大小 {block_size}B")
        return max_block, block_size

    def _transfer_blocks(self, image: bytes, block_size: int,
                         skip_block: int | None = None) -> list:
        """0x36 分块传输；skip_block 用于模拟"丢块后重传"。

        返回实际发送的 [(块序号, 字节数), ...]。

        **负载敏感的重试**：0x36 请求带 64B 数据（66 字节）> 7 字节，必须走
        ISO-TP 多帧（FF→FC→CF…）。虚拟 CAN 是 UDP 组播，机器负载高时偶发
        FC/CF 丢失 → 测试端 5s 等不到 0x76 响应（项目 KNOWN_ISSUES 里同类
        0x36 偶发超时）。这里对单块做一次重试，并处理两种结果：
            - 重试成功 → 继续；
            - 重试收到 NRC 0x73（wrongBlockSequenceCounter）→ 说明**首次请求其实
              已被 ECU 接收并处理**（ECU 的块计数器已前进），按成功继续。
        交叉审查：该 flake 在 OTA 套件 env 端口验证时实跑复现过一次。
        """
        transferred = []
        blocks = [image[i:i + block_size] for i in range(0, len(image), block_size)]
        for idx, chunk in enumerate(blocks, start=1):
            counter = idx & 0xFF
            if skip_block is not None and idx == skip_block:
                # 模拟丢块：先发"下一个块序号"→ ECU 必须回 NRC 0x73
                wrong = (counter + 1) & 0xFF
                ok, resp = self._send_bytes(bytes([0x36, wrong]) + chunk)
                if ok or resp.nrc != 0x73:
                    raise AssertionError(
                        f"模拟丢块未被识别：块 {idx} 期望 NRC 0x73，"
                        f"实际 {resp.describe()}")
                info(f"块 {idx} 丢块被 ECU 拒绝（NRC 0x73），重传正确块…")
            request = bytes([0x36, counter]) + chunk
            try:
                ok, resp = self._send_bytes(request)
            except TimeoutError:
                info(f"0x36 块 {idx} 响应超时（多帧请求负载敏感），重试一次…")
                try:
                    ok, resp = self._send_bytes(request)
                except TimeoutError as exc:
                    raise AssertionError(
                        f"0x36 块 {idx}（{len(chunk)}B）连续两次超时: {exc}") from None
                if not ok and resp.nrc == 0x73:
                    info(f"块 {idx} 首次请求其实已被 ECU 接收（重试回 NRC 0x73），按成功继续")
                    ok = True
            if not ok:
                raise AssertionError(
                    f"0x36 块 {idx}（{len(chunk)}B）传输失败: {resp.describe()}")
            transferred.append((idx, len(chunk)))
            if idx % 4 == 0:
                info(f"刷写进度: {idx}/{len(blocks)} 块")
        return transferred

    def _exit_and_verify(self) -> int:
        """0x37 退出传输 + 31 01 FF00 完整性校验，返回结果字节（0=通过）。"""
        ok, resp = self._send_hex("37")
        if not ok:
            raise AssertionError(f"0x37 退出传输失败: {resp.describe()}")
        ok, resp = self._send_hex("31 01 FF00")
        if not ok:
            raise AssertionError(f"31 01 FF00 校验请求失败: {resp.describe()}")
        result = resp.payload[-1] if resp.payload else 0xFF
        if result != 0:
            raise AssertionError(
                f"完整性校验失败（结果 {result:#04x}）：ECU 保持旧版本（回滚）")
        info("完整性校验通过（长度 + CRC32）→ 新版本待 0x11 复位激活")
        return result

    # ==================================================================
    # 六、对外刷写关键字
    # ==================================================================
    @keyword("Flash OTA Image")
    def flash_ota_image(self) -> dict:
        """完整刷写链路：manifest → 下载 → 包校验 → 预编程 → 编程 → 分块 → 校验。

        返回 summary dict：``{version, size, blocks, crc32, result}``。
        坏包（CRC/大小/兼容性）会在**OTA Agent 校验阶段**被拒绝并抛错。
        """
        manifest = self.get_ota_manifest()
        image = bytes.fromhex(self.download_ota_image())
        self._validate_image(manifest, image)
        _max_block, block_size = self._start_download_session(manifest, image)
        transferred = self._transfer_blocks(image, block_size)
        result = self._exit_and_verify()
        summary = {
            "version": str(manifest["version"]),
            "size": len(image),
            "blocks": len(transferred),
            "crc32": int(str(manifest["crc32"]), 0),
            "result": result,
        }
        info(f"OTA 刷写完成: {summary}")
        return summary

    @keyword("Flash With Retry")
    def flash_with_retry(self, skip_block=2) -> dict:
        """刷写主链路 + **丢块重传**：故意跳过某块触发 NRC 0x73，再补发继续。

        真实刷写最常见的失败是"丢块"；ECU 用块序号识别（0x73），
        测试端重传后必须能继续走完 → 验证端到端重试机制。
        """
        manifest = self.get_ota_manifest()
        image = bytes.fromhex(self.download_ota_image())
        self._validate_image(manifest, image)
        _max_block, block_size = self._start_download_session(manifest, image)
        transferred = self._transfer_blocks(image, block_size,
                                           skip_block=int(skip_block))
        result = self._exit_and_verify()
        summary = {
            "version": str(manifest["version"]),
            "size": len(image),
            "blocks": len(transferred),
            "retried_block": int(skip_block),
            "result": result,
        }
        info(f"丢块重传后刷写完成: {summary}")
        return summary

    @keyword("Force Flash Corrupted Image")
    def force_flash_corrupted_image(self) -> dict:
        """绕过 OTA Agent 校验，把坏包**直接写进 ECU**，验证 ECU 侧 CRC 兜底。

        用途：证明"两道防线"都在工作 —— 即使包校验被绕过，ECU 的
        31 01 FF00（长度 + CRC32）也必须拒绝，且版本保持不变（回滚）。
        本关键字预期失败（抛"完整性校验失败"），测试用 Expect Error 断言。
        """
        manifest = self.get_ota_manifest()
        image = bytes.fromhex(self.download_ota_image())
        # 故意跳过 _validate_image
        _max_block, block_size = self._start_download_session(manifest, image)
        self._transfer_blocks(image, block_size)
        self._exit_and_verify()      # 坏包在这里抛 AssertionError
        raise AssertionError("坏包居然通过了 ECU 完整性校验（缺陷！）")

    @keyword("Simulate Power Loss During Flash")
    def simulate_power_loss_during_flash(self, blocks=2) -> dict:
        """刷写到一半"断电"：传完前 N 块后直接 11 01 复位。

        预期：flash_state 被复位清空、新版本未激活 → 旧版本仍可运行（回滚）。
        """
        manifest = self.get_ota_manifest()
        image = bytes.fromhex(self.download_ota_image())
        self._validate_image(manifest, image)
        _max_block, block_size = self._start_download_session(manifest, image)
        full_blocks = (len(image) + block_size - 1) // block_size
        keep = max(1, min(int(blocks), full_blocks - 1))
        partial = image[:keep * block_size]
        self._transfer_blocks(partial, block_size)
        info(f"已传输 {keep}/{full_blocks} 块，模拟断电/复位…")
        self.ecu_reset()             # 模拟断电重启：未完成刷写必须回滚
        return {"transferred_blocks": keep, "total_blocks": full_blocks,
                "target_version": str(manifest["version"])}

    @keyword("Should Refuse Image")
    def should_refuse_image(self) -> str:
        """断言坏包被拒绝：调用完整刷写流程，期望在包校验阶段抛错。

        成功后返回拒绝原因（便于写进报告），并证明"坏包没有上车"。
        """
        try:
            self.flash_ota_image()
        except AssertionError as exc:
            reason = str(exc)
            info(f"固件包被拒绝（符合预期）: {reason}")
            return reason
        raise AssertionError("坏包没有被拒绝：刷写流程居然走通了（缺陷！）")

    @keyword("Close OTA Tester")
    def close_ota_tester(self) -> None:
        """关闭 OTA 诊断通道（停收包线程 + 释放虚拟总线）。"""
        if self._tester is not None:
            self._tester.stop()
            self._tester = None
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
        info("OTA 诊断通道已关闭")
