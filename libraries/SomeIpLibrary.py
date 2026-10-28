"""
SomeIpLibrary.py — Robot Framework SOME/IP 与 SOA 服务测试关键字库
==================================================================

把 SOME/IP 教学版服务仿真（`simulators/someip_service.py`）包装成 RF 关键字，
让测试用例能用"说人话"的步骤做 SOA 服务测试：

    Start SomeIP Simulation                 # 拉起服务子进程（SD 30490 / 服务 30501）
    SomeIP Startup Offer Should Contain    0x1234   # 启动公告里应有 VehicleService
    SomeIP Send SD Find                    0x1234
    SomeIP Service Should Be Offered       0x1234   0x0001
    ${resp}=    SomeIP Call Method         0x1234   0x0001   # GetSpeed
    SomeIP Call Should Succeed
    SomeIP Subscribe Event                 0x1234   0x8001
    ${payload}=    SomeIP Wait For Notification    0x1234   0x8001

设计要点（读源码前先看这三条）
------------------------------
1. 本库是**独立实现的测试端**：SOME/IP 头与 SD 报文的编解码都在本文件里重新
   写了一遍，不 import 被测仿真器的编解码函数 —— 这样"测试端能解开服务端的
   报文"就成了一条真实的互操作证据，而不是自己测自己。
2. 服务进程懒启动/复用，`sys.executable + 脚本路径` 起子进程；SD socket 必须
   在启动子进程**之前**绑定，因为要把它作为 `--announce-port` 告诉服务端
   （启动公告发给测试端，而不是像真实网络那样发到多播组）。
3. 通知与 SD 报文共用同一个 UDP socket（真实项目里客户端也常这样做），
   按 SOME/IP 头里的 message_id / message_type 区分。方法调用的响应走另一个
   独立 UDP socket；TCP 模式则用 TCP 连接按 `length` 字段做流式分帧。

库作用域 ROBOT_LIBRARY_SCOPE=SUITE。端口：
    SD  UDP 127.0.0.1:30490（env SOMEIP_SD_PORT）
    服务 UDP+TCP 127.0.0.1:30501（env SOMEIP_SVC_PORT）

教学简化点（对应 docs/30 章 §30.9）：
    - 真实 SOME/IP-SD 用 UDP 多播并周期性公告；本仿真用回环单播 + 启动公告一次
    - 订阅 option 直接取 options 数组第一个 IPv4 端点（真实按 entry 的 option index 引用）
    - 返回码按本项目教学约定 0x01/0x02/0x03（标准为 0x02/0x03/0x08）
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
# 协议常量（与 simulators/someip_service.py 保持一致，见 docs/30 章表格）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = 0x01
HEADER_LEN = 16

MSG_TYPE_REQUEST = 0x00
MSG_TYPE_REQUEST_NO_RETURN = 0x01
MSG_TYPE_NOTIFICATION = 0x02
MSG_TYPE_RESPONSE = 0x80
MSG_TYPE_ERROR = 0x81

MSG_TYPE_NAMES = {
    MSG_TYPE_REQUEST: "REQUEST",
    MSG_TYPE_REQUEST_NO_RETURN: "REQUEST_NO_RETURN",
    MSG_TYPE_NOTIFICATION: "NOTIFICATION",
    MSG_TYPE_RESPONSE: "RESPONSE",
    MSG_TYPE_ERROR: "ERROR",
}

RC_OK = 0x00
RC_NAMES = {
    0x00: "OK",
    0x01: "unknown service（未知服务，教学简化码）",
    0x02: "unknown method（未知方法，教学简化码）",
    0x03: "interface version mismatch（版本不匹配，教学简化码）",
    0x04: "wrong protocol version",
    0x05: "wrong message type",
    0x06: "malformed payload",
}

# SOME/IP-SD
SD_SERVICE_ID = 0xFFFF
SD_METHOD_ID = 0x8100
SD_ENTRY_LEN = 16
SD_ENTRY_FIND_SERVICE = 0x00
SD_ENTRY_OFFER_SERVICE = 0x01
SD_ENTRY_SUBSCRIBE_EVENTGROUP = 0x06
SD_ENTRY_SUBSCRIBE_EVENTGROUP_ACK = 0x07
SD_OPTION_IPV4_ENDPOINT = 0x04
SD_PROTOCOL_UDP = 0x11
SD_PROTOCOL_TCP = 0x06

SD_ENTRY_NAMES = {
    SD_ENTRY_FIND_SERVICE: "FindService",
    SD_ENTRY_OFFER_SERVICE: "OfferService",
    SD_ENTRY_SUBSCRIBE_EVENTGROUP: "SubscribeEventgroup",
    SD_ENTRY_SUBSCRIBE_EVENTGROUP_ACK: "SubscribeEventgroupAck",
}

DEFAULT_SD_PORT = 30490
DEFAULT_SVC_PORT = 30501
READY_TIMEOUT = 15.0

# 服务定义（测试端视角：method/event 名字 + event 的 payload 类型，用于解码断言）
SERVICE_DEFS: dict[int, dict] = {
    0x1234: {
        "name": "VehicleService",
        "instance": 0x0001,
        "major_version": 1,
        "methods": {0x0001: "GetSpeed", 0x0002: "SetSpeedLimit"},
        "events": {
            0x8001: {"name": "SpeedChanged", "eventgroup": 0x0001, "dtype": "uint16"},
        },
    },
    0x5678: {
        "name": "HvacService",
        "instance": 0x0001,
        "major_version": 1,
        "methods": {0x0001: "SetTemp"},
        "events": {
            0x8001: {"name": "TempChanged", "eventgroup": 0x0001, "dtype": "int8"},
        },
    },
}


# ---------------------------------------------------------------------------
# 报文编解码（测试端独立实现）
# ---------------------------------------------------------------------------
def _build_message(service_id: int, method_id: int, message_type: int,
                   request_id: int, interface_version: int,
                   return_code: int = RC_OK, payload: bytes = b"") -> bytes:
    length = 8 + len(payload)
    return struct.pack(">HHIIBBBB", service_id & 0xFFFF, method_id & 0xFFFF,
                       length, request_id & 0xFFFFFFFF, PROTOCOL_VERSION,
                       interface_version & 0xFF, message_type & 0xFF,
                       return_code & 0xFF) + payload


def _parse_message(data: bytes) -> dict:
    if len(data) < HEADER_LEN:
        raise ValueError(f"SOME/IP 报文不足 {HEADER_LEN} 字节（收到 {len(data)}）")
    service_id, method_id, length, request_id, proto, iface, mtype, rc = \
        struct.unpack(">HHIIBBBB", data[:HEADER_LEN])
    if length != 8 + (len(data) - HEADER_LEN):
        raise ValueError(
            f"length 字段={length} 与实收不符（应为 {8 + len(data) - HEADER_LEN}）")
    return {
        "service_id": service_id, "method_id": method_id, "length": length,
        "request_id": request_id, "protocol_version": proto,
        "interface_version": iface, "message_type": mtype, "return_code": rc,
        "payload": data[HEADER_LEN:],
    }


def _ttl_bytes(ttl: int) -> bytes:
    ttl &= 0xFFFFFF
    return bytes([(ttl >> 16) & 0xFF, (ttl >> 8) & 0xFF, ttl & 0xFF])


def _build_find_entry(service_id: int, instance_id: int, major_version: int = 0xFF,
                      ttl: int = 3) -> bytes:
    """FindService entry（16B）：major=0xFF 表示"任意版本"。"""
    return (struct.pack(">BBBBHHB", SD_ENTRY_FIND_SERVICE, 0, 0, 0,
                        service_id & 0xFFFF, instance_id & 0xFFFF,
                        major_version & 0xFF)
            + _ttl_bytes(ttl)
            + struct.pack(">I", 0))


def _build_subscribe_entry(service_id: int, instance_id: int, major_version: int,
                           ttl: int, eventgroup_id: int, counter: int = 1,
                           option_index: int = 0) -> bytes:
    """SubscribeEventgroup entry（16B）；ttl=0 即 StopSubscribe。"""
    num_options = 0x10 if ttl else 0x00      # 高 4 位 = 第一组 option 个数
    reserved_counter = (0x000 << 4) | (counter & 0x0F)
    return (struct.pack(">BBBBHHB", SD_ENTRY_SUBSCRIBE_EVENTGROUP,
                        option_index & 0xFF, 0, num_options,
                        service_id & 0xFFFF, instance_id & 0xFFFF,
                        major_version & 0xFF)
            + _ttl_bytes(ttl)
            + struct.pack(">HH", reserved_counter, eventgroup_id & 0xFFFF))


def _build_ipv4_option(host: str, port: int,
                       protocol: int = SD_PROTOCOL_UDP) -> bytes:
    ip = socket.inet_aton(host)
    # length 字段 = 之后 10 字节；总 12 字节
    return struct.pack(">HBB4sBBH", 10, SD_OPTION_IPV4_ENDPOINT, 0, ip, 0,
                       protocol, port & 0xFFFF)


def _build_sd_message(entries: list[bytes], options: list[bytes]) -> bytes:
    entries_blob = b"".join(entries)
    options_blob = b"".join(options)
    # flags(1)+reserved(3)+entries_len(4)+entries+options_len(4)+options
    payload = (b"\x00" + b"\x00\x00\x00"
               + struct.pack(">I", len(entries_blob)) + entries_blob
               + struct.pack(">I", len(options_blob)) + options_blob)
    return _build_message(SD_SERVICE_ID, SD_METHOD_ID, MSG_TYPE_NOTIFICATION,
                          request_id=0, interface_version=0x01, payload=payload)


def _parse_sd_payload(payload: bytes) -> tuple[list[dict], list[dict]]:
    if len(payload) < 12:
        raise ValueError(f"SD payload 太短：{len(payload)} 字节")
    entries_len = struct.unpack(">I", payload[4:8])[0]
    if entries_len % SD_ENTRY_LEN != 0:
        raise ValueError(f"SD entries_length={entries_len} 不是 16 的倍数")
    entries_start = 8
    entries_end = entries_start + entries_len
    if entries_end + 4 > len(payload):
        raise ValueError("SD entries/options_length 被截断")
    options_len = struct.unpack(">I", payload[entries_end:entries_end + 4])[0]
    options_start = entries_end + 4
    if options_start + options_len > len(payload):
        raise ValueError("SD options 数组被截断")

    def parse_entries(blob: bytes) -> list[dict]:
        result = []
        for i in range(0, len(blob), SD_ENTRY_LEN):
            raw = blob[i:i + SD_ENTRY_LEN]
            entry = {
                "type": raw[0],
                "type_name": SD_ENTRY_NAMES.get(raw[0], f"unknown({raw[0]:#04x})"),
                "option_index": raw[1],
                "option_count": (raw[3] >> 4) & 0x0F,
                "service_id": struct.unpack(">H", raw[4:6])[0],
                "instance_id": struct.unpack(">H", raw[6:8])[0],
                "major_version": raw[8],
                "ttl": (raw[9] << 16) | (raw[10] << 8) | raw[11],
            }
            if raw[0] in (SD_ENTRY_FIND_SERVICE, SD_ENTRY_OFFER_SERVICE):
                entry["minor_version"] = struct.unpack(">I", raw[12:16])[0]
            else:
                reserved_counter = struct.unpack(">H", raw[12:14])[0]
                entry["counter"] = reserved_counter & 0x0F
                entry["eventgroup_id"] = struct.unpack(">H", raw[14:16])[0]
            result.append(entry)
        return result

    def parse_options(blob: bytes) -> list[dict]:
        result, pos = [], 0
        while pos + 2 <= len(blob):
            opt_len = struct.unpack(">H", blob[pos:pos + 2])[0]
            if pos + 2 + opt_len > len(blob):
                raise ValueError("SD option 被截断")
            body = blob[pos + 2:pos + 2 + opt_len]
            if len(body) >= 2:
                opt = {"type": body[0], "raw": blob[pos:pos + 2 + opt_len].hex()}
                if body[0] == SD_OPTION_IPV4_ENDPOINT and len(body) >= 10:
                    opt["address"] = socket.inet_ntoa(body[2:6])
                    opt["protocol"] = body[7]
                    opt["port"] = struct.unpack(">H", body[8:10])[0]
                result.append(opt)
            pos += 2 + opt_len
        return result

    return (parse_entries(payload[entries_start:entries_end]),
            parse_options(payload[options_start:options_start + options_len]))


# ---------------------------------------------------------------------------
# 关键字库
# ---------------------------------------------------------------------------
class SomeIpLibrary:
    """SOME/IP 与 SOA 服务测试关键字集合（SUITE 作用域）"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    def __init__(self, sd_port=None, svc_port=None) -> None:
        self._root = Path(__file__).resolve().parent.parent
        self._script = self._root / "simulators" / "someip_service.py"
        self._results_dir = self._root / "results"
        self._log_path = self._results_dir / "someip_service_stdout.log"

        self._host = (os.environ.get("SOMEIP_HOST") or "127.0.0.1").strip()
        self._sd_port = self._resolve_port(sd_port, "SOMEIP_SD_PORT", DEFAULT_SD_PORT)
        self._svc_port = self._resolve_port(svc_port, "SOMEIP_SVC_PORT", DEFAULT_SVC_PORT)

        self._proc: subprocess.Popen | None = None
        self._sd_sock: socket.socket | None = None       # SD + 事件通知
        self._udp_sock: socket.socket | None = None      # UDP 方法调用响应
        self._tcp_sock: socket.socket | None = None      # TCP 模式
        self._transport = "udp"

        self._client_id = 0x0001
        self._session = 0
        self._active_subs: set[tuple[int, int]] = set()
        self._last_response: dict | None = None
        self._last_notification: dict | None = None
        self._startup_offer = ""
        self._last_offer = ""

    # ------------------------------------------------------------------
    # 参数/端口辅助
    # ------------------------------------------------------------------
    def _resolve_port(self, explicit, env_name: str, default: int) -> int:
        value = explicit if explicit is not None else os.environ.get(env_name)
        if value is None or str(value).strip() == "":
            return default
        try:
            port = int(str(value).strip(), 0)
        except ValueError as exc:
            raise ValueError(f"端口参数/环境变量 {env_name}={value!r} 不是合法整数") from exc
        if not 0 < port < 65536:
            raise ValueError(f"端口 {port} 超出 1~65535 范围")
        return port

    @staticmethod
    def _to_int(value, name: str = "参数") -> int:
        if isinstance(value, bool):
            raise AssertionError(f"{name} 不能是布尔值: {value!r}")
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip(), 0)
        except ValueError as exc:
            raise AssertionError(f"{name} 必须是整数（支持 0x 前缀），收到 {value!r}") from exc

    @staticmethod
    def _to_float(value, name: str = "timeout") -> float:
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
            raise AssertionError(f"{name} 必须是秒数（如 5 / 5s / 500ms），收到 {value!r}") from exc

    @staticmethod
    def _to_bool(value, name: str = "开关") -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
        raise AssertionError(f"{name} 必须是布尔值（True/False），收到 {value!r}")

    @staticmethod
    def _hex_to_bytes(hex_text, name: str = "十六进制数据") -> bytes:
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

    def _service_def(self, service_id: int) -> dict:
        sdef = SERVICE_DEFS.get(service_id)
        if sdef is None:
            raise AssertionError(
                f"测试库不认识 service_id={service_id:#06x}，已定义："
                f"{[f'{s:#06x}' for s in SERVICE_DEFS]}")
        return sdef

    def _eventgroup(self, service_id: int, event_id: int) -> tuple[int, dict]:
        sdef = self._service_def(service_id)
        edef = sdef["events"].get(event_id)
        if edef is None:
            raise AssertionError(
                f"{sdef['name']} 没有 event_id={event_id:#06x}，已定义："
                f"{[f'{e:#06x}' for e in sdef['events']]}")
        return edef["eventgroup"], edef

    # ------------------------------------------------------------------
    # 子进程生命周期
    # ------------------------------------------------------------------
    @keyword("Start SomeIP Simulation")
    def start_someip_simulation(self, sd_port=None, svc_port=None, speed=60,
                                timeout=READY_TIMEOUT) -> None:
        """启动 SOME/IP 服务仿真子进程并等待就绪（已在运行则复用）。

        参数：
            sd_port    服务发现 UDP 端口（默认 env SOMEIP_SD_PORT / 30490）
            svc_port   服务通信 UDP+TCP 端口（默认 env SOMEIP_SVC_PORT / 30501）
            speed      车速服务数据源初值 km/h（默认 60，独立生成）
            timeout    等待就绪的超时秒数（默认 15s）

        "就绪"的判定不是进程活着，而是**收到了服务端的启动 OfferService 公告**
        （公告里应包含本仿真定义的全部服务）。若公告丢失，会补发 FindService 重试。
        """
        if self._proc is not None and self._proc.poll() is None:
            info(f"SOME/IP 仿真已在运行（PID={self._proc.pid}），直接复用")
            return

        wait = self._to_float(timeout)
        if sd_port is not None:
            self._sd_port = self._resolve_port(sd_port, "参数 sd_port", DEFAULT_SD_PORT)
        if svc_port is not None:
            self._svc_port = self._resolve_port(svc_port, "参数 svc_port", DEFAULT_SVC_PORT)
        if not self._script.is_file():
            raise RuntimeError(f"找不到 SOME/IP 仿真脚本：{self._script}")
        if self._tcp_port_is_busy():
            raise RuntimeError(
                f"TCP 端口 {self._host}:{self._svc_port} 已被占用 —— "
                f"可能已有 SOME/IP 仿真在运行（上次测试未回收？）或同事正在跑测试。"
                f"可先停止占用进程，或用环境变量 SOMEIP_SVC_PORT 换端口")

        self._ensure_sockets()
        self._results_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [
            sys.executable, str(self._script),
            "--host", self._host,
            "--sd-port", str(self._sd_port),
            "--svc-port", str(self._svc_port),
            "--announce-host", self._host,
            "--announce-port", str(self._sd_sock.getsockname()[1]),
            "--speed", str(self._to_float(speed, "speed")),
        ]
        info(f"启动 SOME/IP 仿真：{' '.join(cmd)}")
        log_file = open(self._log_path, "w", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(self._root), stdin=subprocess.DEVNULL,
                stdout=log_file, stderr=subprocess.STDOUT, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log_file.close()

        # ---- 等启动公告（OfferService） ----
        deadline = time.monotonic() + wait
        last_problem = ""
        next_find = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"SOME/IP 仿真进程启动失败（退出码 {self._proc.returncode}）。\n"
                    f"可用 SOMEIP_SD_PORT / SOMEIP_SVC_PORT 换端口；"
                    f"仿真日志尾部：\n{self._gateway_log_tail()}")
            if time.monotonic() >= next_find:
                self._send_sd_find(0xFFFF, 0xFFFF)
                next_find = time.monotonic() + 1.0
            try:
                entries, options = self._read_sd(0.4)
            except AssertionError as exc:
                last_problem = str(exc)
                continue
            offers = [e for e in entries if e["type"] == SD_ENTRY_OFFER_SERVICE and e["ttl"] > 0]
            if offers:
                self._startup_offer = self._describe_offers(offers, options)
                missing = [f"{sid:#06x}" for sid in SERVICE_DEFS
                           if f"{sid:#06x}" not in self._startup_offer]
                if missing:
                    last_problem = f"启动公告缺少服务 {missing}：{self._startup_offer}"
                    continue
                console(f"[SomeIpLibrary] SOME/IP 仿真已就绪：SD {self._host}:"
                        f"{self._sd_port} / 服务 {self._host}:{self._svc_port}（UDP+TCP）")
                info(f"启动 OfferService 公告：{self._startup_offer}")
                return
            last_problem = "收到的 SD 报文里没有 OfferService 条目"

        self.stop_someip_simulation()
        raise RuntimeError(
            f"SOME/IP 仿真在 {wait}s 内未就绪（最后一次问题：{last_problem}）。\n"
            f"仿真日志尾部：\n{self._gateway_log_tail()}")

    @keyword("Stop SomeIP Simulation")
    def stop_someip_simulation(self, timeout=5.0) -> None:
        """停止 SOME/IP 仿真子进程（幂等）+ 释放所有 socket。"""
        try:
            self.someip_cleanup()
        except Exception:      # noqa: BLE001 — Teardown 清理失败不应掩盖用例结果
            pass
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=self._to_float(timeout))
            except subprocess.TimeoutExpired:
                info("SOME/IP 仿真未在超时内退出，强制 kill")
                proc.kill()
                proc.wait(timeout=self._to_float(timeout))
            info(f"SOME/IP 仿真子进程已被主动终止回收"
                 f"（Windows TerminateProcess 退出码 {proc.returncode} 属正常）")
        for attr in ("_sd_sock", "_udp_sock", "_tcp_sock"):
            sock = getattr(self, attr)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)
        self._transport = "udp"
        self._active_subs.clear()
        console("[SomeIpLibrary] SOME/IP 仿真已停止（子进程与 socket 已回收）")

    @keyword("SomeIP Simulation Should Be Running")
    def someip_simulation_should_be_running(self) -> None:
        """断言 SOME/IP 仿真子进程还活着（排错用）。"""
        if self._proc is None:
            raise AssertionError("SOME/IP 仿真尚未启动（先执行 Start SomeIP Simulation）")
        if self._proc.poll() is not None:
            raise AssertionError(
                f"SOME/IP 仿真进程已退出（退出码 {self._proc.returncode}）。"
                f"日志尾部：\n{self._gateway_log_tail()}")

    def _ensure_sockets(self) -> None:
        """创建 SD/通知 socket 与 UDP 方法 socket（SD socket 先绑以便拿公告端口）。"""
        if self._sd_sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self._host, 0))
            sock.settimeout(1.0)
            self._sd_sock = sock
        if self._udp_sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self._host, 0))
            sock.settimeout(1.0)
            self._udp_sock = sock

    def _tcp_port_is_busy(self) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect((self._host, self._svc_port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _gateway_log_tail(self, lines: int = 15) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"（读取仿真日志失败：{exc}）"
        tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
        return "\n".join(tail) if tail else "（仿真日志为空）"

    # ------------------------------------------------------------------
    # SD：查找 / 公告解析
    # ------------------------------------------------------------------
    @keyword("SomeIP Send SD Find")
    def someip_send_sd_find(self, service_id=0xFFFF, instance_id=0xFFFF) -> None:
        """发一条 SOME/IP-SD FindService 请求（默认查找全部服务）。"""
        self._send_sd_find(self._to_int(service_id, "service_id"),
                           self._to_int(instance_id, "instance_id"))

    def _send_sd_find(self, service_id: int, instance_id: int) -> None:
        self._require_started()
        entry = _build_find_entry(service_id, instance_id, major_version=0xFF)
        raw = _build_sd_message([entry], [])
        try:
            self._sd_sock.sendto(raw, (self._host, self._sd_port))
        except OSError as exc:
            raise AssertionError(f"发送 SD Find 失败（{self._host}:{self._sd_port}）：{exc}") from exc
        info(f"SD TX FindService：service={service_id:#06x} instance={instance_id:#06x}")

    @keyword("SomeIP Read SD Offer")
    def someip_read_sd_offer(self, timeout=3.0) -> str:
        """读取下一条 SD 报文里的 OfferService，返回可读摘要（找不到则失败）。

        摘要形如：`0x1234/0x0001 v1.0 TTL=3 endpoint=127.0.0.1:30501/udp`。
        """
        entries, options = self._read_sd(self._to_float(timeout))
        offers = [e for e in entries if e["type"] == SD_ENTRY_OFFER_SERVICE and e["ttl"] > 0]
        if not offers:
            raise AssertionError(
                f"收到的 SD 报文里没有有效 OfferService（entries={[e['type_name'] for e in entries]}）")
        self._last_offer = self._describe_offers(offers, options)
        info(f"SD RX OfferService：{self._last_offer}")
        return self._last_offer

    @keyword("SomeIP Service Should Be Offered")
    def someip_service_should_be_offered(self, service_id, instance_id=0x0001,
                                         timeout=3.0) -> str:
        """发 Find 并断言能在 TTL 内发现指定服务；返回 Offer 摘要。"""
        wanted_service = self._to_int(service_id, "service_id")
        wanted_instance = self._to_int(instance_id, "instance_id")
        self._send_sd_find(wanted_service, wanted_instance)
        deadline = time.monotonic() + self._to_float(timeout)
        last_seen = ""
        while time.monotonic() < deadline:
            try:
                entries, options = self._read_sd(
                    max(0.1, deadline - time.monotonic()))
            except AssertionError:
                continue
            offers = [e for e in entries
                      if e["type"] == SD_ENTRY_OFFER_SERVICE and e["ttl"] > 0
                      and e["service_id"] == wanted_service
                      and e["instance_id"] in (wanted_instance, 0xFFFF)]
            if offers:
                summary = self._describe_offers(offers, options)
                self._last_offer = summary
                self._assert_offer_endpoint(offers[0], options, wanted_service)
                info(f"服务已发现：{summary}")
                return summary
            last_seen = self._describe_offers(
                [e for e in entries if e["type"] == SD_ENTRY_OFFER_SERVICE], options)
        raise AssertionError(
            f"{self._to_float(timeout)}s 内没有收到 service={wanted_service:#06x} "
            f"instance={wanted_instance:#06x} 的 OfferService"
            + (f"（期间看到：{last_seen}）" if last_seen else ""))

    @keyword("SomeIP Startup Offer Should Contain")
    def someip_startup_offer_should_contain(self, service_id) -> None:
        """断言服务端启动时主动发的 OfferService 公告里包含指定服务。"""
        expected = f"{self._to_int(service_id, 'service_id'):#06x}"
        if not self._startup_offer:
            raise AssertionError(
                "启动公告为空 —— 服务端启动时没有发 OfferService？"
                "（检查 Start SomeIP Simulation 的日志）")
        if expected not in self._startup_offer:
            raise AssertionError(
                f"启动公告里没有 {expected}：{self._startup_offer}")
        info(f"启动公告包含 {expected} ✓（{self._startup_offer}）")

    def _assert_offer_endpoint(self, offer: dict, options: list[dict],
                               service_id: int) -> None:
        sdef = self._service_def(service_id)
        if offer["major_version"] != sdef["major_version"]:
            raise AssertionError(
                f"{sdef['name']} Offer 的 major_version={offer['major_version']}"
                f" ≠ 期望 {sdef['major_version']}")
        endpoint = None
        idx = offer.get("option_index", 0)
        if idx < len(options):
            endpoint = options[idx]
        if endpoint is None or "port" not in endpoint:
            raise AssertionError(
                f"{sdef['name']} Offer 没有携带 IPv4 endpoint option（options={options}）")
        if endpoint["port"] != self._svc_port:
            raise AssertionError(
                f"{sdef['name']} Offer 声明的服务端口 {endpoint['port']} "
                f"≠ 实际服务端口 {self._svc_port}")

    @staticmethod
    def _describe_offers(offers: list[dict], options: list[dict]) -> str:
        rows = []
        for offer in offers:
            endpoint = ""
            idx = offer.get("option_index", 0)
            if idx < len(options) and "port" in options[idx]:
                opt = options[idx]
                proto = "udp" if opt.get("protocol") == SD_PROTOCOL_UDP else "tcp"
                endpoint = f" endpoint={opt['address']}:{opt['port']}/{proto}"
            rows.append(
                f"{offer['service_id']:#06x}/{offer['instance_id']:#06x} "
                f"v{offer['major_version']}.{offer['minor_version']} "
                f"TTL={offer['ttl']}{endpoint}")
        return "; ".join(rows)

    def _read_sd(self, timeout: float) -> tuple[list[dict], list[dict]]:
        """从 SD socket 读下一条 SOME/IP-SD 报文（忽略方法响应/通知）。"""
        self._require_sd_socket()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.02, deadline - time.monotonic())
            self._sd_sock.settimeout(remaining)
            try:
                data, addr = self._sd_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise AssertionError(f"读取 SD 报文失败：{exc}") from exc
            try:
                msg = _parse_message(data)
            except ValueError as exc:
                info(f"忽略格式非法的 SD 数据（来自 {addr}）：{exc}")
                continue
            if (msg["service_id"] != SD_SERVICE_ID
                    or msg["method_id"] != SD_METHOD_ID):
                info(f"忽略非 SD 报文（service={msg['service_id']:#06x} "
                     f"method={msg['method_id']:#06x}）")
                continue
            try:
                return _parse_sd_payload(msg["payload"])
            except ValueError as exc:
                info(f"忽略非法 SD payload：{exc}")
                continue
        raise AssertionError(
            f"{timeout}s 内没有收到 SOME/IP-SD 报文（服务是否已启动？"
            f"先执行 Start SomeIP Simulation）")

    # ------------------------------------------------------------------
    # 方法调用
    # ------------------------------------------------------------------
    @keyword("SomeIP Call Method")
    def someip_call_method(self, service_id, method_id, request_payload="",
                           interface_version=None, timeout=3.0) -> str:
        """调用一个 SOME/IP 方法（REQUEST→RESPONSE），返回响应 payload 的 hex。

        参数：
            service_id         服务号，如 0x1234
            method_id          方法号，如 0x0001（GetSpeed）
            request_payload    请求 payload（hex 字符串，默认空）
            interface_version  接口版本（默认取服务定义的版本；负向用例可传 9 制造不匹配）
            timeout            等响应超时秒数

        本关键字**不因错误响应而失败**：ERROR 报文也会正常返回（payload 为空），
        由 `SomeIP Response Should Be Error` / `SomeIP Call Should Succeed` 断言。
        也允许传未定义的 service_id（负向用例要验证服务端回 0x01），此时默认
        interface_version 取 1。
        """
        service = self._to_int(service_id, "service_id")
        method = self._to_int(method_id, "method_id")
        payload = self._hex_to_bytes(request_payload, "请求 payload")
        sdef = SERVICE_DEFS.get(service)      # 负向用例允许未知服务
        if interface_version is None:
            iface = sdef["major_version"] if sdef else 1
        else:
            iface = self._to_int(interface_version, "interface_version")

        self._session = (self._session + 1) & 0xFFFF
        request_id = ((self._client_id & 0xFFFF) << 16) | self._session
        raw = _build_message(service, method, MSG_TYPE_REQUEST, request_id,
                             iface, RC_OK, payload)
        self._send_service_frame(raw)
        response = self._recv_service_response(
            service, method, request_id, self._to_float(timeout))
        self._last_response = response
        info(f"方法调用 {sdef['name'] if sdef else f'service {service:#06x}'} "
             f"method={method_id} → "
             f"{MSG_TYPE_NAMES.get(response['message_type'])} "
             f"rc={response['return_code']:#04x} payload={response['payload'].hex()}")
        return response["payload"].hex()

    @keyword("SomeIP Call Should Succeed")
    def someip_call_should_succeed(self) -> None:
        """断言上一次方法调用得到了 RESPONSE 且 return_code=0x00。"""
        resp = self._require_last_response()
        if resp["message_type"] != MSG_TYPE_RESPONSE:
            raise AssertionError(
                f"期望 RESPONSE(0x80)，实际 {MSG_TYPE_NAMES.get(resp['message_type'])}"
                f"（rc={resp['return_code']:#04x}：{RC_NAMES.get(resp['return_code'])}）")
        if resp["return_code"] != RC_OK:
            raise AssertionError(
                f"期望 return_code=0x00，实际 {resp['return_code']:#04x}"
                f"（{RC_NAMES.get(resp['return_code'], 'unknown')}）")
        info("方法调用成功（RESPONSE + return_code 0x00）✓")

    @keyword("SomeIP Return Code Should Be")
    def someip_return_code_should_be(self, expected) -> None:
        """断言上一次响应的 return_code 等于期望值（正/负响应均可）。"""
        resp = self._require_last_response()
        expected_code = self._to_int(expected, "expected")
        if resp["return_code"] != expected_code:
            raise AssertionError(
                f"期望 return_code={expected_code:#04x}"
                f"（{RC_NAMES.get(expected_code, 'unknown')}），实际 "
                f"{resp['return_code']:#04x}（{RC_NAMES.get(resp['return_code'], 'unknown')}）")
        info(f"return_code={expected_code:#04x} 正确 ✓")

    @keyword("SomeIP Response Should Be Error")
    def someip_response_should_be_error(self, expected_code) -> None:
        """断言上一次调用的响应是 ERROR（0x81）且 return_code 等于期望值。

        负向用例专用：协议错误必须"有明确的错误码"，不能只是"没响应"。
        """
        resp = self._require_last_response()
        expected = self._to_int(expected_code, "expected_code")
        if resp["message_type"] != MSG_TYPE_ERROR:
            raise AssertionError(
                f"期望 ERROR(0x81)，实际 {MSG_TYPE_NAMES.get(resp['message_type'])}"
                f"（return_code={resp['return_code']:#04x}）")
        if resp["return_code"] != expected:
            raise AssertionError(
                f"期望 ERROR return_code={expected:#04x}"
                f"（{RC_NAMES.get(expected, 'unknown')}），实际 "
                f"{resp['return_code']:#04x}（{RC_NAMES.get(resp['return_code'], 'unknown')}）")
        info(f"ERROR 响应 return_code={expected:#04x}"
             f"（{RC_NAMES.get(expected, 'unknown')}）✓")

    # ------------------------------------------------------------------
    # 事件订阅与通知
    # ------------------------------------------------------------------
    @keyword("SomeIP Subscribe Event")
    def someip_subscribe_event(self, service_id, event_id, ttl=10, timeout=3.0) -> None:
        """订阅一个事件（eventgroup），发送 SubscribeEventgroup 并等 Ack。

        参数：
            service_id  服务号（0x1234=VehicleService / 0x5678=HvacService）
            event_id    事件号（0x8001）
            ttl         订阅有效期秒数（本仿真默认 10s）
            timeout     等 SubscribeEventgroupAck 的超时

        订阅成功后服务端开始向本库的 SD socket 发 NOTIFICATION（事件）。
        """
        service = self._to_int(service_id, "service_id")
        event = self._to_int(event_id, "event_id")
        eventgroup, edef = self._eventgroup(service, event)
        sdef = self._service_def(service)
        ttl_value = self._to_int(ttl, "ttl")

        entry = _build_subscribe_entry(
            service, sdef["instance"], sdef["major_version"], ttl_value,
            eventgroup, counter=1, option_index=0)
        option = _build_ipv4_option(self._host, self._sd_sock.getsockname()[1])
        raw = _build_sd_message([entry], [option])
        try:
            self._sd_sock.sendto(raw, (self._host, self._sd_port))
        except OSError as exc:
            raise AssertionError(f"发送 SubscribeEventgroup 失败：{exc}") from exc
        info(f"SD TX SubscribeEventgroup：service={service:#06x} "
             f"eventgroup={eventgroup:#06x} event={event}（{edef['name']}）ttl={ttl_value}s")

        deadline = time.monotonic() + self._to_float(timeout)
        while time.monotonic() < deadline:
            try:
                entries, _options = self._read_sd(
                    max(0.1, deadline - time.monotonic()))
            except AssertionError:
                continue
            for ack in entries:
                if (ack["type"] == SD_ENTRY_SUBSCRIBE_EVENTGROUP_ACK
                        and ack["service_id"] == service
                        and ack.get("eventgroup_id") == eventgroup):
                    self._active_subs.add((service, event))
                    info(f"收到 SubscribeEventgroupAck：eventgroup={eventgroup:#06x} "
                         f"TTL={ack['ttl']}，事件订阅成功 ✓")
                    return
        raise AssertionError(
            f"{timeout}s 内没有收到 service={service:#06x} eventgroup={eventgroup:#06x} "
            f"的 SubscribeEventgroupAck")

    @keyword("SomeIP Unsubscribe Event")
    def someip_unsubscribe_event(self, service_id, event_id) -> None:
        """退订事件（发 TTL=0 的 SubscribeEventgroup，即 StopSubscribe）。"""
        service = self._to_int(service_id, "service_id")
        event = self._to_int(event_id, "event_id")
        eventgroup, _ = self._eventgroup(service, event)
        sdef = self._service_def(service)
        entry = _build_subscribe_entry(
            service, sdef["instance"], sdef["major_version"], 0, eventgroup,
            counter=1, option_index=0)
        raw = _build_sd_message([entry], [])
        try:
            self._sd_sock.sendto(raw, (self._host, self._sd_port))
        except OSError as exc:
            raise AssertionError(f"发送 StopSubscribe 失败：{exc}") from exc
        self._active_subs.discard((service, event))
        info(f"SD TX StopSubscribe（TTL=0）：service={service:#06x} "
             f"eventgroup={eventgroup:#06x}")

    @keyword("SomeIP Wait For Notification")
    def someip_wait_for_notification(self, service_id, event_id, timeout=3.0) -> str:
        """等一条指定事件的通知（NOTIFICATION），返回 payload hex。"""
        service = self._to_int(service_id, "service_id")
        event = self._to_int(event_id, "event_id")
        self._eventgroup(service, event)      # 校验事件号
        payload = self._wait_notification(service, event, self._to_float(timeout))
        return payload.hex()

    def _wait_notification(self, service: int, event: int,
                           timeout: float) -> bytes:
        self._require_sd_socket()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._sd_sock.settimeout(max(0.02, deadline - time.monotonic()))
            try:
                data, addr = self._sd_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise AssertionError(f"读取事件通知失败：{exc}") from exc
            try:
                msg = _parse_message(data)
            except ValueError:
                continue
            if msg["service_id"] != service or msg["method_id"] != event:
                continue
            if msg["message_type"] not in (MSG_TYPE_NOTIFICATION, MSG_TYPE_RESPONSE):
                continue
            self._last_notification = {
                "service_id": service, "event_id": event,
                "payload": msg["payload"], "message_type": msg["message_type"],
                "return_code": msg["return_code"], "source": addr,
            }
            info(f"收到 NOTIFICATION：service={service:#06x} event={event:#06x} "
                 f"payload={msg['payload'].hex()}（来自 {addr}）")
            return msg["payload"]
        raise AssertionError(
            f"{timeout}s 内没有收到 service={service:#06x} event={event:#06x} "
            f"的通知（是否已 Subscribe？服务是否在跑？）")

    @keyword("SomeIP Notification Value Should Be")
    def someip_notification_value_should_be(self, expected, tolerance=0) -> None:
        """断言最近一条通知里的数值等于期望值（按事件定义的类型解码）。

        VehicleService.SpeedChanged 是 uint16，HvacService.TempChanged 是 int8。
        """
        if self._last_notification is None:
            raise AssertionError("还没有收到任何通知（先 SomeIP Subscribe Event + Wait For Notification）")
        service = self._last_notification["service_id"]
        event = self._last_notification["event_id"]
        dtype = self._service_def(service)["events"][event]["dtype"]
        actual = self._decode_payload(dtype, self._last_notification["payload"])
        expected_value = self._to_int(expected, "expected")
        tolerance_value = self._to_int(tolerance, "tolerance")
        if abs(actual - expected_value) > tolerance_value:
            raise AssertionError(
                f"通知值 {actual} 与期望 {expected_value} 的偏差超过容差 {tolerance_value}"
                f"（事件 {event:#06x}，payload={self._last_notification['payload'].hex()}）")
        info(f"通知数值 = {actual}（期望 {expected_value}，容差 {tolerance_value}）✓")

    @keyword("SomeIP Event Period Should Be")
    def someip_event_period_should_be(self, expected_ms, tolerance_ms=200,
                                      samples=5) -> float:
        """测量最近订阅事件的周期与抖动：平均周期应接近期望，每段间隔在容差内。

        参数：
            expected_ms    期望周期（毫秒，本仿真 SpeedChanged=500）
            tolerance_ms   单次间隔允许偏差（默认 ±200ms）
            samples        采多少段间隔（默认 5 段，需要 6 条通知）

        返回实测平均周期（毫秒）。事件周期是"实时性"指标：周期抖动大，
        说明服务端调度/网络有问题（真实项目里会结合 P99 与丢帧一起看）。
        """
        if not self._active_subs:
            raise AssertionError("还没有订阅任何事件（先 SomeIP Subscribe Event）")
        service, event = sorted(self._active_subs)[-1]
        wanted_ms = self._to_float(expected_ms, "expected_ms")
        tolerance = self._to_float(tolerance_ms, "tolerance_ms")
        count = max(1, self._to_int(samples, "samples"))

        self._drain_sd_socket()     # 清掉之前积压的通知，从"现在"开始计时
        timestamps: list[float] = []
        deadline = time.monotonic() + wanted_ms / 1000.0 * (count + 2) + 2.0
        while len(timestamps) <= count and time.monotonic() < deadline:
            self._sd_sock.settimeout(0.5)
            try:
                data, _addr = self._sd_sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                msg = _parse_message(data)
            except ValueError:
                continue
            if (msg["service_id"] == service and msg["method_id"] == event
                    and msg["message_type"] == MSG_TYPE_NOTIFICATION):
                timestamps.append(time.monotonic())
        if len(timestamps) <= count:
            raise AssertionError(
                f"只收到 {len(timestamps)} 条通知，不足以计算 {count} 段周期")

        intervals = [(timestamps[i + 1] - timestamps[i]) * 1000.0
                     for i in range(count)]
        average = sum(intervals) / len(intervals)
        bad = [round(iv, 1) for iv in intervals
               if abs(iv - wanted_ms) > tolerance]
        info(f"事件周期样本(ms)={[round(iv, 1) for iv in intervals]} "
             f"平均={average:.1f}ms（期望 {wanted_ms:.0f}±{tolerance:.0f}ms）")
        if bad:
            raise AssertionError(
                f"事件周期抖动超限：{bad} ms 超出 {wanted_ms:.0f}±{tolerance:.0f}ms"
                f"（全部样本 {[round(iv, 1) for iv in intervals]}）")
        info(f"事件周期 {average:.1f}ms 在容差内 ✓")
        return round(average, 2)

    @keyword("SomeIP Should Receive No Notification")
    def someip_should_receive_no_notification(self, service_id, event_id,
                                              timeout=1.5, grace=0.6) -> None:
        """断言在宽限期之后收不到指定事件的通知（用于验证退订后"静默"）。

        参数：
            timeout  总观察时间（秒）
            grace    宽限期（秒）：退订前已经发到网络里的"在途通知"允许出现，
                     宽限期后仍收到通知才算失败（真实测试也要处理在途报文）。
        """
        service = self._to_int(service_id, "service_id")
        event = self._to_int(event_id, "event_id")
        total = self._to_float(timeout)
        grace_s = self._to_float(grace)
        deadline = time.monotonic() + total
        grace_until = time.monotonic() + grace_s
        in_flight = 0
        while time.monotonic() < deadline:
            self._sd_sock.settimeout(0.1)
            try:
                data, _addr = self._sd_sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                msg = _parse_message(data)
            except ValueError:
                continue
            if (msg["service_id"] != service or msg["method_id"] != event
                    or msg["message_type"] != MSG_TYPE_NOTIFICATION):
                continue
            if time.monotonic() < grace_until:
                in_flight += 1
                continue
            raise AssertionError(
                f"退订宽限期后仍收到通知（payload={msg['payload'].hex()}）—— "
                f"StopSubscribe 未被服务端正确处理？")
        info(f"退订后 {total}s 内无通知（其中 {in_flight} 条在途通知在 "
             f"{grace_s}s 宽限期内被忽略）✓")

    # ------------------------------------------------------------------
    # 传输：UDP / TCP
    # ------------------------------------------------------------------
    @keyword("SomeIP Tcp Mode")
    def someip_tcp_mode(self, enabled=True) -> None:
        """切换方法调用的传输方式：True=TCP，False=回到 UDP。

        SOME/IP 的 Method 调用支持 UDP（无连接、快）和 TCP（可靠、大报文）两种
        传输；同一服务端口（30501）同时监听两者。真实项目选哪种取决于配置
        （ARXML 里的 transport protocol）。
        """
        want_tcp = self._to_bool(enabled, "enabled")
        if want_tcp:
            self._require_started()
            if self._tcp_sock is not None:
                info("已经是 TCP 模式，忽略重复切换")
                return
            try:
                sock = socket.create_connection((self._host, self._svc_port),
                                                timeout=5.0)
            except OSError as exc:
                raise AssertionError(
                    f"TCP 连接 SOME/IP 服务失败（{self._host}:{self._svc_port}）：{exc}")
            sock.settimeout(None)
            self._tcp_sock = sock
            self._transport = "tcp"
            info(f"已切换到 TCP 模式（{self._host}:{self._svc_port}）")
        else:
            if self._tcp_sock is not None:
                try:
                    self._tcp_sock.close()
                except OSError:
                    pass
                self._tcp_sock = None
            self._transport = "udp"
            info("已切换回 UDP 模式")

    @keyword("SomeIP Send Raw Frame")
    def someip_send_raw_frame(self, frame_hex) -> None:
        """发送原始字节（协议错误用例专用；UDP 发到服务端口，TCP 走当前连接）。"""
        raw = self._hex_to_bytes(frame_hex, "原始 SOME/IP 帧")
        if not raw:
            raise AssertionError("原始 SOME/IP 帧不能为空字节")
        self._send_service_frame(raw)
        info(f"已发送原始 SOME/IP 帧（{self._transport}）：{raw.hex()}")

    # ------------------------------------------------------------------
    # 清理（Test Teardown 用）
    # ------------------------------------------------------------------
    @keyword("SomeIP Cleanup")
    def someip_cleanup(self) -> None:
        """用例间隔离：退订所有事件、切回 UDP、清空 socket 里的残留报文。

        无脑放在 Test Teardown：失败用例留下的订阅/残留通知不会污染下一条用例。
        """
        for service, event in sorted(self._active_subs):
            try:
                self.someip_unsubscribe_event(service, event)
            except Exception:      # noqa: BLE001
                pass
        self._active_subs.clear()
        if self._tcp_sock is not None:
            try:
                self._tcp_sock.close()
            except OSError:
                pass
            self._tcp_sock = None
        self._transport = "udp"
        self._last_notification = None
        self._drain_sd_socket()
        self._drain_udp_socket()
        info("SOME/IP 清理完成（退订/传输复位/缓冲区清空）")

    def _drain_sd_socket(self, quiet: float = 0.05) -> int:
        if self._sd_sock is None:
            return 0
        dropped, deadline = 0, time.monotonic() + quiet
        while time.monotonic() < deadline:
            self._sd_sock.settimeout(0.01)
            try:
                self._sd_sock.recvfrom(4096)
                dropped += 1
            except socket.timeout:
                break
            except OSError:
                break
        return dropped

    def _drain_udp_socket(self) -> int:
        if self._udp_sock is None:
            return 0
        dropped = 0
        while True:
            self._udp_sock.settimeout(0.01)
            try:
                self._udp_sock.recvfrom(4096)
                dropped += 1
            except socket.timeout:
                break
            except OSError:
                break
        return dropped

    # ------------------------------------------------------------------
    # 传输内部实现
    # ------------------------------------------------------------------
    def _send_service_frame(self, raw: bytes) -> None:
        self._require_started()
        if self._transport == "tcp":
            if self._tcp_sock is None:
                raise AssertionError("TCP 模式但连接不存在（先 SomeIP Tcp Mode）")
            try:
                self._tcp_sock.sendall(raw)
            except OSError as exc:
                raise AssertionError(f"TCP 发送 SOME/IP 帧失败：{exc}") from exc
        else:
            if self._udp_sock is None:
                raise AssertionError("UDP socket 未创建（先 Start SomeIP Simulation）")
            try:
                self._udp_sock.sendto(raw, (self._host, self._svc_port))
            except OSError as exc:
                raise AssertionError(f"UDP 发送 SOME/IP 帧失败：{exc}") from exc

    def _recv_service_response(self, service: int, method: int,
                               request_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.02, deadline - time.monotonic())
            try:
                if self._transport == "tcp":
                    data = self._tcp_recv_message(remaining)
                else:
                    data = self._udp_recv_message(remaining)
            except socket.timeout:
                continue
            except AssertionError:
                raise
            if data is None:
                continue
            try:
                msg = _parse_message(data)
            except ValueError as exc:
                info(f"忽略非法响应：{exc}")
                continue
            if (msg["service_id"] != service or msg["method_id"] != method
                    or msg["request_id"] != request_id):
                info(f"忽略不匹配的响应（service={msg['service_id']:#06x} "
                     f"method={msg['method_id']:#06x} "
                     f"request_id={msg['request_id']:#010x}）")
                continue
            if msg["message_type"] not in (MSG_TYPE_RESPONSE, MSG_TYPE_ERROR):
                info(f"忽略非响应类型：{MSG_TYPE_NAMES.get(msg['message_type'])}")
                continue
            return msg
        raise AssertionError(
            f"{timeout}s 内没有收到方法响应（{self._transport.upper()}，"
            f"service={service:#06x} method={method:#06x}）—— 服务是否存活？")

    def _udp_recv_message(self, timeout: float) -> bytes:
        self._udp_sock.settimeout(timeout)
        data, _addr = self._udp_sock.recvfrom(4096)
        return data

    def _tcp_recv_message(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        header = self._tcp_recv_exact(HEADER_LEN, deadline)
        if header is None:
            raise AssertionError("TCP 连接被服务端关闭（期望响应却读到 EOF）")
        length = struct.unpack(">I", header[4:8])[0]
        if length < 8:
            raise AssertionError(f"服务端返回的 TCP SOME/IP length 字段非法：{length}")
        body = b"" if length == 8 else self._tcp_recv_exact(length - 8, deadline)
        if body is None:
            raise AssertionError("TCP 响应 payload 未读完连接就被关闭")
        return header + body

    def _tcp_recv_exact(self, size: int, deadline: float) -> bytes | None:
        buf = bytearray()
        while len(buf) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("TCP 读取超时")
            self._tcp_sock.settimeout(remaining)
            try:
                chunk = self._tcp_sock.recv(size - len(buf))
            except socket.timeout:
                raise
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    # ------------------------------------------------------------------
    # 状态/断言辅助
    # ------------------------------------------------------------------
    def _require_started(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise AssertionError(
                "SOME/IP 仿真没有在运行（先执行 Start SomeIP Simulation）")

    def _require_sd_socket(self) -> None:
        if self._sd_sock is None:
            raise AssertionError(
                "SD socket 未创建（先执行 Start SomeIP Simulation）")

    def _require_last_response(self) -> dict:
        if self._last_response is None:
            raise AssertionError("还没有进行过方法调用（先 SomeIP Call Method）")
        return self._last_response

    @staticmethod
    def _decode_payload(dtype: str, payload: bytes) -> int:
        if dtype == "int16":
            if len(payload) < 2:
                raise AssertionError(f"int16 payload 长度不足：{payload.hex()}")
            return struct.unpack(">h", payload[:2])[0]
        if dtype == "uint16":
            if len(payload) < 2:
                raise AssertionError(f"uint16 payload 长度不足：{payload.hex()}")
            return struct.unpack(">H", payload[:2])[0]
        if dtype == "int8":
            if len(payload) < 1:
                raise AssertionError(f"int8 payload 长度不足：{payload.hex()}")
            return struct.unpack(">b", payload[:1])[0]
        raise AssertionError(f"未知 payload 类型 {dtype!r}")

    @keyword("SomeIP Decode Int16")
    def someip_decode_int16(self, payload_hex) -> int:
        """把 payload hex 按大端 int16 解码（如车速响应 `003c` → 60）。"""
        return self._decode_payload("int16", self._hex_to_bytes(payload_hex, "payload"))

    @keyword("SomeIP Decode Uint16")
    def someip_decode_uint16(self, payload_hex) -> int:
        """把 payload hex 按大端 uint16 解码（如限速回显 `0078` → 120）。"""
        return self._decode_payload("uint16", self._hex_to_bytes(payload_hex, "payload"))

    @keyword("SomeIP Decode Int8")
    def someip_decode_int8(self, payload_hex) -> int:
        """把 payload hex 按大端 int8 解码（如空调温度 `16` → 22）。"""
        return self._decode_payload("int8", self._hex_to_bytes(payload_hex, "payload"))
