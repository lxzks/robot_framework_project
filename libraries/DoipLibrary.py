"""
DoipLibrary.py — Robot Framework DoIP 诊断关键字库
====================================================

把 ISO 13400 教学版 DoIP 网关（`simulators/doip_gateway.py`）包装成 RF 关键字，
让测试用例能用"说人话"的步骤做以太网诊断测试：

    Start DoIP Gateway                 # 拉起网关子进程（TCP/UDP 13400）
    ${vin}=    DoIP Vehicle Identification   # UDP 车辆识别 → VIN
    DoIP Connect                       # TCP 连接 DoIP 实体
    ${code}=    DoIP Routing Activation      # 路由激活 → 0x10
    DoIP Send Diagnostic    10 03      # DoIP 0x8001 承载 UDS
    ${ack}=    DoIP Read ACK           # 0x8002 肯定确认（code 0x00）
    ${resp}=    DoIP Read Response     # 0x8001 承载的 UDS 响应

设计要点（读源码前先看这三条）
------------------------------
1. 本库只做"以太网侧的 Tester"（纯 socket），**不直接碰 CAN**。
   CAN 侧由网关子进程复用 VirtualCanBus + DiagnosticTester 转发给 ECU，
   与真实架构 "Tester ⇄ DoIP 实体 ⇄ CAN ⇄ ECU" 一致。
2. 网关跑在**独立子进程**里（sys.executable + 脚本路径），而不是本进程线程：
   这样测试进程里不会多出一条虚拟 CAN 总线，避免和 common.resource 里的
   DiagnosticLibrary 抢帧（总线上"收报文"的消费者越多越容易互相干扰）。
3. 端口用环境变量可覆盖：DOIP_TCP_PORT / DOIP_UDP_PORT（默认 13400，
   与虚拟 CAN 总线的 233.255.0.1:30000 错开）。环境变量在**跑 robot 之前**
   设置即可，本库和网关读同一套。

库作用域 ROBOT_LIBRARY_SCOPE=SUITE：整个套件共用一个网关/一条连接，
用例之间用 Test Teardown 的 DoIP Disconnect 做隔离。

注意：网关异常退出时（比如端口被占用），Start DoIP Gateway 会抛出带
"网关日志最后若干行"的清晰错误，而不是让第一条诊断超时后才暴露。
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

# ---------------------------------------------------------------------------
# 协议常量（与 simulators/doip_gateway.py 保持一致，见 docs/24 章表格）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = 0x02
INVERSE_VERSION = 0xFD
HEADER_LEN = 8

PT_GENERIC_NACK = 0x0000
PT_VEHICLE_ID_REQUEST = 0x0001
PT_VEHICLE_ANNOUNCEMENT = 0x0004
PT_ROUTING_ACTIVATION_REQUEST = 0x0005
PT_ROUTING_ACTIVATION_RESPONSE = 0x0006
PT_ALIVE_CHECK_REQUEST = 0x0007
PT_ALIVE_CHECK_RESPONSE = 0x0008
PT_DIAG_MESSAGE = 0x8001
PT_DIAG_ACK = 0x8002
PT_DIAG_NACK = 0x8003

TESTER_ADDRESS = 0x0E00             # 外部诊断仪逻辑地址
ECU_LOGICAL_ADDRESS = 0x0001        # 目标 ECU（动力域）
DOIP_ENTITY_ADDRESS = 0x0E80        # DoIP 实体（网关）逻辑地址，车辆识别响应里回这个

DEFAULT_DOIP_PORT = 13400
READY_TIMEOUT = 15.0                # 等网关就绪的上限（秒）

GENERIC_NACK_NAMES = {
    0x00: "incorrect pattern format（头格式/协议版本错误）",
    0x01: "unknown payload type（未知 payload type）",
    0x02: "message too large（报文过大）",
    0x03: "out of memory",
    0x04: "invalid payload length（payload 长度异常）",
}
DIAG_NACK_NAMES = {
    0x02: "invalid source address（源地址与已激活 tester 不符）",
    0x03: "unknown target address（未知目标地址）",
    0x04: "diagnostic message too large",
    0x05: "out of memory",
    0x06: "target unreachable（路由未激活 / ECU 无响应）",
    0x07: "unknown network",
    0x08: "transport protocol error",
}
ROUTING_ACTIVATION_NAMES = {
    0x00: "unknown source address（未知 tester 地址）",
    0x02: "SA different from already activated SA",
    0x03: "SA already active on different socket",
    0x06: "unsupported routing activation type",
    0x10: "routing successfully activated（成功）",
}
RESPONSE_TYPE_NAMES = {
    PT_GENERIC_NACK: "通用头 NACK",
    PT_VEHICLE_ANNOUNCEMENT: "车辆识别响应/公告",
    PT_ROUTING_ACTIVATION_RESPONSE: "路由激活响应",
    PT_ALIVE_CHECK_RESPONSE: "alive check 响应",
    PT_DIAG_MESSAGE: "诊断报文",
    PT_DIAG_ACK: "诊断报文肯定确认",
    PT_DIAG_NACK: "诊断报文否定确认",
}


class DoipLibrary:
    """DoIP 诊断关键字集合（SUITE 作用域：整个套件共用一条网关/连接）"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, tcp_port=None, udp_port=None) -> None:
        self._root = Path(__file__).resolve().parent.parent
        self._script = self._root / "simulators" / "doip_gateway.py"
        self._results_dir = self._root / "results"
        self._log_path = self._results_dir / "doip_gateway_stdout.log"

        self._host = os.environ.get("DOIP_HOST", "127.0.0.1").strip() or "127.0.0.1"
        self._tcp_port = self._resolve_port(tcp_port, "DOIP_TCP_PORT")
        self._udp_port = self._resolve_port(udp_port, "DOIP_UDP_PORT")

        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._rx_buf = bytearray()

        # 最近一次相关响应的存档（断言关键字从这里取数据）
        self._last_vehicle: dict | None = None
        self._last_activation: dict | None = None
        self._last_ack: dict | None = None
        self._last_uds_request: bytes = b""
        self._last_uds_response: bytes = b""
        self._active_tester: int | None = None

    # ------------------------------------------------------------------
    # 初始化辅助
    # ------------------------------------------------------------------
    def _resolve_port(self, explicit, env_name: str) -> int:
        """端口优先级：关键字参数 > 环境变量 > 默认 13400。"""
        value = explicit if explicit is not None else os.environ.get(env_name)
        if value is None or str(value).strip() == "":
            return DEFAULT_DOIP_PORT
        try:
            port = int(str(value).strip(), 0)
        except ValueError as exc:
            raise ValueError(
                f"端口参数/环境变量 {env_name}={value!r} 不是合法整数") from exc
        if not 0 < port < 65536:
            raise ValueError(f"端口 {port} 超出 1~65535 范围")
        return port

    @staticmethod
    def _to_int(value, name: str = "参数") -> int:
        """把 RF 传进来的字符串/整数统一转成 int（支持 '0x0E00' / '14' / 14）。"""
        if isinstance(value, bool):
            raise AssertionError(f"{name} 不能是布尔值: {value!r}")
        if isinstance(value, int):
            return value
        text = str(value).strip()
        try:
            return int(text, 0)
        except ValueError as exc:
            raise AssertionError(
                f"{name} 必须是整数（支持 0x 前缀），收到 {value!r}") from exc

    @staticmethod
    def _to_float(value, name: str = "timeout") -> float:
        """把 '3' / '3s' / '500ms' / 3.0 统一转成秒。"""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        text = str(value).strip().lower()
        try:
            if text.endswith("ms"):
                return float(text[:-2]) / 1000.0
            if text.endswith("s"):
                return float(text[:-1])
            return float(text)
        except ValueError as exc:
            raise AssertionError(
                f"{name} 必须是秒数（如 5 / 5s / 500ms），收到 {value!r}") from exc

    @staticmethod
    def _hex_to_bytes(hex_text, name: str = "十六进制数据") -> bytes:
        """'10 03' / '0x1003' / '1003' → bytes，非法输入给清晰报错。"""
        text = str(hex_text).replace(" ", "").replace("\t", "").strip()
        if text.lower().startswith("0x"):
            text = text[2:]
        if not text:
            return b""
        if len(text) % 2 != 0:
            raise AssertionError(f"{name} 的十六进制字符数必须为偶数，收到 {hex_text!r}")
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise AssertionError(f"{name} 含非法十六进制字符：{hex_text!r}") from exc

    @staticmethod
    def _build_frame(payload_type: int, payload: bytes) -> bytes:
        return struct.pack(">BBHI", PROTOCOL_VERSION, INVERSE_VERSION,
                           payload_type, len(payload)) + payload

    # ------------------------------------------------------------------
    # 网关子进程生命周期
    # ------------------------------------------------------------------
    @keyword("Start DoIP Gateway")
    def start_doip_gateway(self, tcp_port=None, udp_port=None, timeout=READY_TIMEOUT) -> None:
        """启动 DoIP 网关子进程（若已在运行则复用），并等待其就绪。

        参数：
            tcp_port   DoIP TCP 端口（默认取环境变量 DOIP_TCP_PORT，再默认 13400）
            udp_port   车辆识别 UDP 端口（默认取 DOIP_UDP_PORT，再默认 13400）
            timeout    等待就绪的超时秒数（默认 15s）

        "就绪"的判定不是"进程起来了"，而是**功能可用**：UDP 车辆识别能拿到
        合法 0x0004 响应 + TCP 端口能连上。这样端口被占用/网关启动失败会
        在这里立刻报错，而不是等到第一条诊断超时才暴露。
        """
        if self._proc is not None and self._proc.poll() is None:
            info(f"DoIP 网关已在运行（PID={self._proc.pid}），直接复用")
            return

        wait_seconds = self._to_float(timeout)
        if tcp_port is not None:
            self._tcp_port = self._resolve_port(tcp_port, "参数 tcp_port")
        if udp_port is not None:
            self._udp_port = self._resolve_port(udp_port, "参数 udp_port")

        if not self._script.is_file():
            raise RuntimeError(f"找不到 DoIP 网关脚本：{self._script}")
        if self._tcp_port_is_busy():
            raise RuntimeError(
                f"TCP 端口 {self._host}:{self._tcp_port} 已被占用 —— "
                f"可能已有 DoIP 网关在运行（上次测试未回收？）或同事正在跑测试。"
                f"可先手动停止占用进程，或用环境变量 DOIP_TCP_PORT 换端口")

        self._results_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        # 子进程 stdout/stderr 重定向到 UTF-8 日志文件：Windows 默认 GBK 会让
        # 日志里的中文变乱码；显式指定编码后本库能原样读出错误上下文。
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [
            sys.executable, str(self._script),
            "--tcp-host", self._host,
            "--tcp-port", str(self._tcp_port),
            "--udp-host", self._host,
            "--udp-port", str(self._udp_port),
        ]
        info(f"启动 DoIP 网关：{' '.join(cmd)}")
        log_file = open(self._log_path, "w", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self._root),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            # 子进程已继承文件句柄，父进程这份可以关掉（子进程仍能继续写）
            log_file.close()

        # ---- 等"功能就绪" ----
        deadline = time.monotonic() + wait_seconds
        last_problem = ""
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"DoIP 网关进程启动失败（退出码 {self._proc.returncode}）。\n"
                    f"可用 DOIP_TCP_PORT / DOIP_UDP_PORT 换端口；"
                    f"网关日志尾部：\n{self._gateway_log_tail()}")
            try:
                ptype, payload = self._udp_exchange(PT_VEHICLE_ID_REQUEST, b"", timeout=0.5)
                if ptype == PT_VEHICLE_ANNOUNCEMENT and len(payload) == 32:
                    if self._tcp_probe():
                        console(
                            f"[DoipLibrary] DoIP 网关已就绪：TCP/UDP {self._host}:"
                            f"{self._tcp_port}/{self._udp_port}")
                        return
                    last_problem = "TCP 端口探测失败（UDP 已响应，TCP 未就绪）"
                else:
                    last_problem = (f"UDP 车辆识别响应异常：payload type={ptype:#06x}，"
                                    f"payload 长度={len(payload)}")
            except AssertionError as exc:
                last_problem = str(exc)
            time.sleep(0.2)

        self.stop_doip_gateway()
        raise RuntimeError(
            f"DoIP 网关在 {wait_seconds}s 内未就绪（最后一次问题：{last_problem}）。\n"
            f"网关日志尾部：\n{self._gateway_log_tail()}")

    @keyword("Stop DoIP Gateway")
    def stop_doip_gateway(self, timeout=5.0) -> None:
        """停止 DoIP 网关子进程（幂等：没启动过/已停止时直接返回）。

        顺序很关键：先断开 Tester 连接（避免网关边退边发响应），再 terminate
        子进程；超时未退出则 kill。整个套件结束时必须调用，否则 13400 端口
        和虚拟总线会一直被占用，下一个套件会起不来。
        """
        self.doip_disconnect()
        proc = self._proc
        self._proc = None
        if proc is None:
            info("DoIP 网关未启动（或已停止），无需回收")
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=self._to_float(timeout))
            except subprocess.TimeoutExpired:
                info("DoIP 网关未在超时内退出，强制 kill")
                proc.kill()
                proc.wait(timeout=self._to_float(timeout))
            # Windows 上 TerminateProcess 的退出码固定为 1，不是"网关崩溃"，
            # 只是被测试框架主动回收 —— 日志里说清楚，避免新手误判。
            info(f"DoIP 网关子进程已被主动终止回收"
                 f"（Windows TerminateProcess 退出码 {proc.returncode} 属正常）")
        else:
            info(f"DoIP 网关子进程此前已自行退出（退出码 {proc.returncode}）")
        console("[DoipLibrary] DoIP 网关已停止（子进程已回收）")

    @keyword("DoIP Gateway Should Be Running")
    def doip_gateway_should_be_running(self) -> None:
        """断言 DoIP 网关子进程还活着（排错用）。"""
        if self._proc is None:
            raise AssertionError("DoIP 网关尚未启动（先执行 Start DoIP Gateway）")
        if self._proc.poll() is not None:
            raise AssertionError(
                f"DoIP 网关进程已退出（退出码 {self._proc.returncode}）。"
                f"日志尾部：\n{self._gateway_log_tail()}")

    def _tcp_port_is_busy(self) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect((self._host, self._tcp_port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _tcp_probe(self) -> bool:
        """就绪探针：TCP 能连上就立即关闭（不占连接）。"""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect((self._host, self._tcp_port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _gateway_log_tail(self, lines: int = 15) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"（读取网关日志失败：{exc}）"
        tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
        return "\n".join(tail) if tail else "（网关日志为空）"

    # ------------------------------------------------------------------
    # 车辆识别（UDP 13400）
    # ------------------------------------------------------------------
    @keyword("DoIP Vehicle Identification")
    def doip_vehicle_identification(self, timeout=3.0) -> str:
        """通过 UDP 车辆识别请求（0x0001）获取车辆信息，返回 VIN 字符串。

        ISO 13400 的"车辆识别"：Tester 不知道车在哪、VIN 是什么，先发 UDP
        请求；DoIP 实体回 0x0004 响应（32 字节）：
            VIN(17) + 逻辑地址(2) + EID(6) + GID(6) + further action(1)

        本关键字解析全部字段并归档，返回 VIN；逻辑地址/EID 可再用
        `DoIP Vehicle Logical Address Should Be` / `DoIP Vehicle EID Should Be`
        断言（比把一长串 payload 丢给用例自己去切更可读）。
        """
        ptype, payload = self._udp_exchange(
            PT_VEHICLE_ID_REQUEST, b"", timeout=self._to_float(timeout))

        if ptype == PT_GENERIC_NACK:
            code = payload[0] if payload else -1
            raise AssertionError(
                f"UDP 车辆识别被网关拒绝：generic NACK code={code:#04x}"
                f"（{GENERIC_NACK_NAMES.get(code, 'unknown')}）")
        if ptype != PT_VEHICLE_ANNOUNCEMENT:
            raise AssertionError(
                f"UDP 车辆识别响应 payload type 应为 0x0004，实际 {ptype:#06x}"
                f"（payload={payload.hex()}）")
        if len(payload) != 32:
            raise AssertionError(
                f"车辆识别响应 payload 应为 32 字节"
                f"（VIN17+地址2+EID6+GID6+further action1），实际 {len(payload)} 字节")
        try:
            vin = payload[0:17].decode("ascii")
        except UnicodeDecodeError:
            raise AssertionError(f"车辆识别响应里的 VIN 不是合法 ASCII：{payload[0:17].hex()}")
        self._last_vehicle = {
            "vin": vin,
            "logical_address": int.from_bytes(payload[17:19], "big"),
            "eid": payload[19:25].hex(),
            "gid": payload[25:31].hex(),
            "further_action": payload[31],
        }
        v = self._last_vehicle
        info(f"车辆识别响应：VIN={v['vin']} 逻辑地址={v['logical_address']:#06x} "
             f"EID={v['eid']} GID={v['gid']} further_action={v['further_action']:#04x}")
        return vin

    @keyword("DoIP Vehicle Logical Address Should Be")
    def doip_vehicle_logical_address_should_be(self, expected) -> None:
        """断言车辆识别响应里的逻辑地址（DoIP 实体地址，本仿真 0x0E80）。"""
        if self._last_vehicle is None:
            raise AssertionError("还没有做过车辆识别（先执行 DoIP Vehicle Identification）")
        expected_value = self._to_int(expected, "expected")
        actual = self._last_vehicle["logical_address"]
        if actual != expected_value:
            raise AssertionError(
                f"车辆识别响应逻辑地址 {actual:#06x} ≠ 期望 {expected_value:#06x}")
        info(f"车辆识别逻辑地址 = {actual:#06x} 正确")

    @keyword("DoIP Vehicle EID Should Be")
    def doip_vehicle_eid_should_be(self, expected_hex) -> None:
        """断言车辆识别响应里的 EID（实体标识，6 字节，hex 字符串比较）。"""
        if self._last_vehicle is None:
            raise AssertionError("还没有做过车辆识别（先执行 DoIP Vehicle Identification）")
        expected = str(expected_hex).replace(" ", "").lower()
        actual = self._last_vehicle["eid"]
        if actual != expected:
            raise AssertionError(f"车辆识别响应 EID={actual} ≠ 期望 {expected}")
        info(f"车辆识别 EID = {actual} 正确")

    def _udp_exchange(self, payload_type: int, payload: bytes,
                      timeout: float) -> tuple[int, bytes]:
        """UDP 发一条 DoIP 帧并等一条响应，返回 (payload_type, payload)。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(self._build_frame(payload_type, payload),
                        (self._host, self._udp_port))
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            raise AssertionError(
                f"UDP 车辆识别 {timeout}s 内无响应（{self._host}:{self._udp_port}）—— "
                f"网关是否已启动？先用 Start DoIP Gateway") from None
        except OSError as exc:
            raise AssertionError(
                f"UDP 车辆识别发送失败（{self._host}:{self._udp_port}）：{exc}") from exc
        finally:
            sock.close()

        if len(data) < HEADER_LEN:
            raise AssertionError(f"UDP 响应不足 {HEADER_LEN} 字节：{data.hex()}")
        version, inverse, rtype, length = struct.unpack(">BBHI", data[:HEADER_LEN])
        rpayload = data[HEADER_LEN:]
        if inverse != (version ^ 0xFF):
            raise AssertionError(
                f"UDP 响应 DoIP 头非法：version={version:#04x} inverse={inverse:#04x}")
        if length != len(rpayload):
            raise AssertionError(
                f"UDP 响应 payload 长度不符：声明 {length} 实际 {len(rpayload)}")
        info(f"UDP DoIP 响应：payload_type={rtype:#06x} payload={rpayload.hex()}")
        return rtype, rpayload

    # ------------------------------------------------------------------
    # TCP 连接管理
    # ------------------------------------------------------------------
    @keyword("DoIP Connect")
    def doip_connect(self, timeout=5.0) -> None:
        """建立到 DoIP 实体的 TCP 连接（默认 127.0.0.1:13400）。

        每次连接都是**新的 DoIP 会话**：路由激活状态按连接隔离，
        所以上一个用例断开后，本用例必须重新做路由激活。
        """
        self.doip_disconnect()
        try:
            sock = socket.create_connection(
                (self._host, self._tcp_port), timeout=self._to_float(timeout))
        except OSError as exc:
            raise AssertionError(
                f"无法连接 DoIP 网关 {self._host}:{self._tcp_port}：{exc} —— "
                f"先执行 Start DoIP Gateway，并确认端口未被占用") from exc
        sock.settimeout(None)
        self._sock = sock
        self._rx_buf = bytearray()
        info(f"已连接 DoIP 网关：{self._host}:{self._tcp_port}")

    @keyword("DoIP Disconnect")
    def doip_disconnect(self) -> None:
        """关闭 DoIP TCP 连接（幂等；Teardown 里无脑调用）。"""
        sock = self._sock
        self._sock = None
        self._rx_buf = bytearray()
        self._active_tester = None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass
        info("已断开 DoIP 连接")

    # ------------------------------------------------------------------
    # 路由激活
    # ------------------------------------------------------------------
    @keyword("DoIP Routing Activation")
    def doip_routing_activation(self, source_address=None, activation_type=None,
                                timeout=5.0) -> int:
        """发送路由激活请求（0x0005），返回 0x0006 响应里的 code。

        请求 payload：tester 地址(2) + activation type(1) + reserved(4) = 7 字节
        响应 payload：tester(2) + 实体(2) + code(1) + reserved(4) + OEM(4) = 13 字节

        code=0x10 表示成功（本仿真期望值）；0x00/0x02/0x06 是失败原因。
        关键字返回 code 让用例自己断言（比在关键字里吞掉更能体现"测试意图"），
        同时归档完整响应供 `DoIP Activated Tester Address Should Be` 等断言。
        """
        tester = TESTER_ADDRESS if source_address is None else self._to_int(
            source_address, "source_address")
        act_type = 0x00 if activation_type is None else self._to_int(
            activation_type, "activation_type")
        payload = (tester.to_bytes(2, "big") + bytes([act_type]) + b"\x00" * 4)
        self._send_frame(PT_ROUTING_ACTIVATION_REQUEST, payload)

        ptype, resp = self._recv_frame(self._to_float(timeout))
        if ptype != PT_ROUTING_ACTIVATION_RESPONSE:
            raise AssertionError(
                f"路由激活响应 payload type 应为 0x0006，实际 {ptype:#06x}"
                f"（payload={resp.hex()}）")
        if len(resp) != 13:
            raise AssertionError(
                f"路由激活响应 payload 应为 13 字节，实际 {len(resp)}：{resp.hex()}")
        echo_tester = int.from_bytes(resp[0:2], "big")
        entity = int.from_bytes(resp[2:4], "big")
        code = resp[4]
        self._last_activation = {
            "tester_address": echo_tester,
            "entity_address": entity,
            "code": code,
            "reserved": resp[5:9].hex(),
            "oem": resp[9:13].hex(),
        }
        if code == 0x10:
            self._active_tester = echo_tester
        info(f"路由激活响应：code={code:#04x}"
             f"（{ROUTING_ACTIVATION_NAMES.get(code, 'unknown')}），"
             f"tester={echo_tester:#06x}，entity={entity:#06x}，"
             f"reserved={resp[5:9].hex()}，OEM={resp[9:13].hex()}")
        return code

    @keyword("DoIP Activated Tester Address Should Be")
    def doip_activated_tester_address_should_be(self, expected) -> None:
        """断言路由激活响应回显的 tester 地址（需求 REQ-DOIP-002）。"""
        if self._last_activation is None:
            raise AssertionError("还没有发过路由激活请求")
        expected_value = self._to_int(expected, "expected")
        actual = self._last_activation["tester_address"]
        if actual != expected_value:
            raise AssertionError(
                f"路由激活响应回显 tester 地址 {actual:#06x} ≠ 期望 {expected_value:#06x}")
        info(f"路由激活回显 tester 地址 = {actual:#06x} 正确")

    @keyword("DoIP Entity Address Should Be")
    def doip_entity_address_should_be(self, expected) -> None:
        """断言路由激活响应里的 DoIP 实体地址（本仿真 0x0E80）。"""
        if self._last_activation is None:
            raise AssertionError("还没有发过路由激活请求")
        expected_value = self._to_int(expected, "expected")
        actual = self._last_activation["entity_address"]
        if actual != expected_value:
            raise AssertionError(
                f"路由激活响应实体地址 {actual:#06x} ≠ 期望 {expected_value:#06x}")
        info(f"路由激活响应实体地址 = {actual:#06x} 正确")

    # ------------------------------------------------------------------
    # 诊断报文收发（0x8001 / 0x8002 / 0x8003）
    # ------------------------------------------------------------------
    @keyword("DoIP Send Diagnostic")
    def doip_send_diagnostic(self, uds_hex, source_address=None,
                             target_address=None) -> None:
        """把一条 UDS 请求封装进 DoIP 诊断报文（0x8001）发给网关。

        参数：
            uds_hex          UDS 请求，如 `10 03` / `22 F1 90`（空格可有可无）
            source_address   tester 逻辑地址（默认 0x0E00）
            target_address   目标 ECU 逻辑地址（默认 0x0001）

        本关键字**只发不收**（和真实 Tester 的分层设计一致）：
        收到的是肯定确认还是 NACK，由 `DoIP Read ACK` / `DoIP Should Get NACK` 读。
        """
        uds = self._hex_to_bytes(uds_hex, "UDS 请求")
        if not uds:
            raise AssertionError("UDS 请求不能为空字节")
        source = TESTER_ADDRESS if source_address is None else self._to_int(
            source_address, "source_address")
        target = ECU_LOGICAL_ADDRESS if target_address is None else self._to_int(
            target_address, "target_address")
        self._send_frame(PT_DIAG_MESSAGE,
                         source.to_bytes(2, "big") + target.to_bytes(2, "big") + uds)
        self._last_uds_request = uds
        info(f"DoIP 诊断请求已发送：SA={source:#06x} TA={target:#06x} UDS={uds.hex()}")

    @keyword("DoIP Read ACK")
    def doip_read_ack(self, timeout=5.0) -> int:
        """读取下一条报文，期望诊断确认（0x8002 正 ACK / 0x8003 负 ACK）。

        返回确认码：正 ACK 的 code=0x00；负 ACK 则是 NACK 原因码，
        用例可用 `Should Be Equal As Integers` 直接断言。
        """
        ptype, payload = self._recv_frame(self._to_float(timeout))
        if ptype not in (PT_DIAG_ACK, PT_DIAG_NACK):
            raise AssertionError(
                f"期望诊断确认（0x8002/0x8003），实际 payload type {ptype:#06x}"
                f"（{RESPONSE_TYPE_NAMES.get(ptype, 'unknown')}），payload={payload.hex()}")
        if len(payload) != 5:
            raise AssertionError(
                f"诊断确认 payload 应为 5 字节（SA2+TA2+code1），实际 {len(payload)}：{payload.hex()}")
        source = int.from_bytes(payload[0:2], "big")
        target = int.from_bytes(payload[2:4], "big")
        code = payload[4]
        kind = "positive ACK" if ptype == PT_DIAG_ACK else "negative ACK"
        self._last_ack = {"type": ptype, "source": source, "target": target, "code": code}
        names = {} if ptype == PT_DIAG_ACK else DIAG_NACK_NAMES
        info(f"诊断确认：{kind} code={code:#04x}"
             f"（{names.get(code, 'routing confirmation / OK')}），SA={source:#06x} TA={target:#06x}")
        return code

    @keyword("DoIP Read Response")
    def doip_read_response(self, timeout=5.0) -> str:
        """读取下一条报文，期望诊断响应（0x8001），返回其中 UDS 响应字节的 hex。

        返回示例：`5003003201f4`（10 03 会话控制的正响应）、`7e00`（3E 00 保活）。
        DoIP 层的 SA/TA 会做校验：SA 必须是 ECU 逻辑地址 0x0001，
        TA 必须等于本连接激活的 tester 地址（证明响应确实回给了我们）。
        """
        ptype, payload = self._recv_frame(self._to_float(timeout))
        if ptype != PT_DIAG_MESSAGE:
            raise AssertionError(
                f"期望诊断响应 0x8001，实际 payload type {ptype:#06x}"
                f"（{RESPONSE_TYPE_NAMES.get(ptype, 'unknown')}），payload={payload.hex()}")
        if len(payload) < 4:
            raise AssertionError(
                f"诊断响应 payload 至少 4 字节（SA2+TA2），实际 {len(payload)}：{payload.hex()}")
        source = int.from_bytes(payload[0:2], "big")
        target = int.from_bytes(payload[2:4], "big")
        uds = payload[4:]
        if source != ECU_LOGICAL_ADDRESS:
            raise AssertionError(
                f"诊断响应源地址应为 ECU 0x{ECU_LOGICAL_ADDRESS:04x}，实际 {source:#06x}")
        if self._active_tester is not None and target != self._active_tester:
            raise AssertionError(
                f"诊断响应目标地址 {target:#06x} ≠ 已激活 tester {self._active_tester:#06x}")
        self._last_uds_response = uds
        info(f"诊断响应：SA={source:#06x} TA={target:#06x} UDS={uds.hex()}")
        return uds.hex()

    @keyword("DoIP Read VIN")
    def doip_read_vin(self) -> str:
        """经 DoIP 通道发 `22 F1 90` 读 VIN，返回 17 位 VIN 字符串。

        这条用例的价值在于**多帧透传**：UDS 响应是 20 字节
        （62 F1 90 + 17 字节 VIN），在 CAN 上必须 FF+CF 多帧传输；
        网关内部用项目的 DiagnosticTester 完成 ISO-TP 重组后，再整包放进
        一条 DoIP 0x8001 报文回给 Tester —— 一次验证"以太网 ⇄ CAN"两侧的
        协议转换都正确。
        """
        self.doip_send_diagnostic("22 F1 90")
        ack = self.doip_read_ack()
        if ack != 0x00:
            raise AssertionError(
                f"诊断请求未被网关肯定确认：ACK/NACK code={ack:#04x}"
                f"（{DIAG_NACK_NAMES.get(ack, 'unknown')}）")
        response_hex = self.doip_read_response()
        if not response_hex.startswith("62f190"):
            raise AssertionError(
                f"读 VIN 响应应以 62 F1 90 开头（22 服务的正响应），实际 {response_hex}")
        vin_bytes = bytes.fromhex(response_hex[6:])
        try:
            vin = vin_bytes.decode("ascii")
        except UnicodeDecodeError:
            raise AssertionError(f"VIN 不是合法 ASCII：{vin_bytes.hex()}")
        if len(vin) != 17:
            raise AssertionError(f"VIN 应为 17 字节，实际 {len(vin)} 字节：{vin!r}")
        info(f"经 DoIP 读到 VIN：{vin}（UDS 响应 {len(response_hex) // 2} 字节，"
             f"CAN 侧多帧重组后透传）")
        return vin

    @keyword("DoIP Alive Check")
    def doip_alive_check(self, timeout=5.0) -> int:
        """发 alive check 请求（0x0007），返回 0x0008 响应里的源地址。

        用途：诊断会话长时间空闲时，Tester 用它确认 DoIP 实体还活着
        （防止 TCP 半开连接被误认为"通道正常"）。本仿真回 DoIP 实体地址 0x0E80。
        """
        self._send_frame(PT_ALIVE_CHECK_REQUEST, b"")
        ptype, payload = self._recv_frame(self._to_float(timeout))
        if ptype != PT_ALIVE_CHECK_RESPONSE:
            raise AssertionError(
                f"期望 alive check 响应 0x0008，实际 payload type {ptype:#06x}"
                f"（payload={payload.hex()}）")
        if len(payload) != 2:
            raise AssertionError(
                f"alive check 响应 payload 应为 2 字节（源地址），实际 {len(payload)}：{payload.hex()}")
        address = int.from_bytes(payload, "big")
        info(f"alive check 响应：源地址={address:#06x}")
        return address

    # ------------------------------------------------------------------
    # 负向断言 / 底层发送
    # ------------------------------------------------------------------
    @keyword("DoIP Should Get NACK")
    def doip_should_get_nack(self, nack_code, kind="Generic", timeout=5.0) -> int:
        """断言下一条报文是指定 NACK（协议错误用例专用，失败时信息完整）。

        参数：
            nack_code  期望的 NACK 码，如 0x06（支持 '0x06' / '6'）
            kind       `Generic`  = 通用头 NACK（payload type 0x0000）
                       `Diagnostic` = 诊断报文 NACK（payload type 0x8003）
            timeout    等待秒数

        静默失败是最难查的测试缺陷：这个关键字不仅断言 type，还断言 code，
        并且在错误信息里带上实际的 payload —— 协议错误用例必须"证明被拒绝"。
        """
        expected_code = self._to_int(nack_code, "nack_code")
        kind_text = str(kind).strip().lower()
        kind_map = {"generic": PT_GENERIC_NACK, "header": PT_GENERIC_NACK,
                    "diagnostic": PT_DIAG_NACK, "diag": PT_DIAG_NACK}
        if kind_text not in kind_map:
            raise AssertionError(f"kind 只能是 Generic/Diagnostic，收到 {kind!r}")
        expected_type = kind_map[kind_text]
        type_label = "通用头 NACK(0x0000)" if expected_type == PT_GENERIC_NACK \
            else "诊断报文 NACK(0x8003)"

        ptype, payload = self._recv_frame(self._to_float(timeout))
        if ptype != expected_type:
            raise AssertionError(
                f"期望{type_label}，实际收到 payload type {ptype:#06x}"
                f"（{RESPONSE_TYPE_NAMES.get(ptype, 'unknown')}），payload={payload.hex()}")
        names = GENERIC_NACK_NAMES if expected_type == PT_GENERIC_NACK else DIAG_NACK_NAMES
        if expected_type == PT_GENERIC_NACK:
            if len(payload) != 1:
                raise AssertionError(
                    f"通用头 NACK payload 应为 1 字节 code，实际 {len(payload)}：{payload.hex()}")
            actual_code = payload[0]
        else:
            if len(payload) != 5:
                raise AssertionError(
                    f"诊断报文 NACK payload 应为 5 字节（SA2+TA2+code1），"
                    f"实际 {len(payload)}：{payload.hex()}")
            actual_code = payload[4]
        if actual_code != expected_code:
            raise AssertionError(
                f"期望 NACK code {expected_code:#04x}"
                f"（{names.get(expected_code, 'unknown')}），实际 {actual_code:#04x}"
                f"（{names.get(actual_code, 'unknown')}）：payload={payload.hex()}")
        info(f"已确认 {type_label}，code={actual_code:#04x}"
             f"（{names.get(actual_code, 'unknown')}）")
        return actual_code

    @keyword("DoIP Connection Should Be Closed")
    def doip_connection_should_be_closed(self, timeout=3.0) -> None:
        """断言网关已关闭 TCP 连接（协议错误用例的常见处置）。"""
        if self._sock is None:
            raise AssertionError("当前没有 DoIP 连接 —— 无法断言'被关闭'")
        if self._rx_buf:
            raise AssertionError(
                f"DoIP 连接未关闭：接收缓冲区还有 {len(self._rx_buf)} 字节未读数据")
        wait_seconds = self._to_float(timeout)
        self._sock.settimeout(wait_seconds)
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            raise AssertionError(
                f"DoIP 连接在 {wait_seconds}s 内仍未关闭（网关应关闭该连接）") from None
        except OSError as exc:
            raise AssertionError(f"读取 DoIP 连接状态失败：{exc}") from exc
        if data:
            raise AssertionError(
                f"DoIP 连接未关闭：又收到 {len(data)} 字节数据 {data[:32].hex()}...")
        info("DoIP 连接已被网关关闭（符合协议错误的处置约定）")

    @keyword("DoIP Send Raw Frame")
    def doip_send_raw_frame(self, frame_hex) -> None:
        """发送原始字节（协议错误用例专用：可构造非法版本/未知类型/长度不符）。

        参数是完整帧的 hex，比如：
            02fd123400000000   # 合法头 + 未知 payload type 0x1234
            01fe000100000000   # 非法协议版本 0x01
            02fd00010000000a02 # 头声明 10 字节 payload，实际只给 1 字节

        注意：本关键字造的是**非法/半截报文**，发送后用例必须用
        `DoIP Should Get NACK` / `DoIP Connection Should Be Closed` 收尾，
        否则这些字节会污染后续连接（TCP 是字节流，没有报文边界！）。
        """
        raw = self._hex_to_bytes(frame_hex, "原始 DoIP 帧")
        if not raw:
            raise AssertionError("原始 DoIP 帧不能为空字节")
        if self._sock is None:
            raise AssertionError("还没有连接 DoIP 网关（先执行 DoIP Connect）")
        try:
            self._sock.sendall(raw)
        except OSError as exc:
            raise AssertionError(f"发送原始 DoIP 帧失败：{exc}") from exc
        info(f"已发送原始 DoIP 帧：{raw.hex()}")

    # ------------------------------------------------------------------
    # TCP 流收发内部实现
    # ------------------------------------------------------------------
    def _send_frame(self, payload_type: int, payload: bytes) -> None:
        if self._sock is None:
            raise AssertionError(
                "还没有连接 DoIP 网关（先执行 DoIP Connect —— 注意路由激活是按连接生效的）")
        frame = self._build_frame(payload_type, payload)
        try:
            self._sock.sendall(frame)
        except OSError as exc:
            raise AssertionError(f"发送 DoIP 帧失败（连接可能已断开）：{exc}") from exc
        info(f"DoIP TX payload_type={payload_type:#06x} len={len(payload)} "
             f"payload={payload.hex()}")

    def _recv_frame(self, timeout: float) -> tuple[int, bytes]:
        """从 TCP 流读一条完整 DoIP 帧，返回 (payload_type, payload)。"""
        header = self._recv_exact(HEADER_LEN, timeout, "DoIP 头")
        if header is None:
            raise AssertionError("DoIP 连接已被网关关闭（期望收到报文却读到 EOF）")
        version, inverse, payload_type, length = struct.unpack(">BBHI", header)
        if inverse != (version ^ 0xFF):
            raise AssertionError(
                f"网关返回的 DoIP 头非法：version={version:#04x} inverse={inverse:#04x}")
        payload = b""
        if length:
            payload = self._recv_exact(length, timeout, "DoIP payload")
            if payload is None:
                raise AssertionError("DoIP 报文 payload 未读完连接就被关闭")
        info(f"DoIP RX payload_type={payload_type:#06x} len={length} "
             f"payload={payload.hex()}")
        return payload_type, payload

    def _recv_exact(self, size: int, timeout: float, what: str) -> bytes | None:
        """从缓冲/TCP 精确读 size 字节；超时抛 AssertionError，EOF 返回 None。"""
        deadline = time.monotonic() + timeout
        while len(self._rx_buf) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"等待 {what}（{size} 字节）超时 {timeout}s"
                    f"（已收到 {len(self._rx_buf)} 字节）")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise AssertionError(
                    f"等待 {what}（{size} 字节）超时 {timeout}s"
                    f"（已收到 {len(self._rx_buf)} 字节）") from None
            except OSError as exc:
                raise AssertionError(f"读取 DoIP 连接失败：{exc}") from exc
            if not chunk:
                if not self._rx_buf:
                    return None
                raise AssertionError(f"连接在 {what} 读取中途被关闭")
            self._rx_buf.extend(chunk)
        data = bytes(self._rx_buf[:size])
        del self._rx_buf[:size]
        return data
