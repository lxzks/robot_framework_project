"""
someip_service.py — SOME/IP + SOME/IP-SD 教学版服务仿真
==========================================================

解决什么问题
------------
SOA（Service-Oriented Architecture，面向服务架构）是智能汽车软件的主线：
车上的功能不再只是"CAN 上一帧信号"，而是**一个个可被发现、可被调用的服务**
（车窗服务、空调服务、车速服务……）。承载它的通信中间件在车载领域就是
**SOME/IP**（Scalable service-Oriented MiddlewarE over IP）。

对测试工程师来说，你要验证的东西从"信号值对不对"变成三件事：

    ① 服务能不能被发现？          → SOME/IP-SD（Service Discovery，UDP 30490）
    ② 方法调用对不对？            → Method REQUEST/RESPONSE（UDP/TCP 30501）
    ③ 事件订阅能不能收到？        → Eventgroup Subscribe + NOTIFICATION

本文件在**一个进程**里同时扮演"服务端 + 服务发现实体"：

    ┌──────────────────────┐  UDP 127.0.0.1:30490   ┌──────────────────────┐
    │ 测试端（RF / 工具）    │ ────────────────────► │ someip_service.py     │
    │  SomeIpLibrary       │ ◄──────────────────── │  ├─ SOME/IP-SD 线程   │
    │                      │  Offer/SubscribeAck   │  ├─ UDP 服务线程       │
    │                      │  UDP/TCP 127.0.0.1:30501  ├─ TCP 服务线程     │
    │                      │ ◄────────────────────►│  ├─ 事件发送线程      │
    └──────────────────────┘  REQUEST/RESPONSE     │  └─ 可选 CAN 车速桥   │
                              NOTIFICATION         └──────────────────────┘

SOME/IP 头（16 字节，网络字节序 = 大端）
---------------------------------------
    +0   message_id       4B   service_id(2B) + method/event_id(2B)
    +4   length           4B   从 request_id 起到报文结尾的字节数 = 8 + payload 长度
    +8   request_id       4B   client_id(2B) + session_id(2B)，响应必须回显
    +12  protocol_version 1B   固定 0x01
    +13  interface_version 1B  服务接口主版本号（本仿真各服务 = 1）
    +14  message_type     1B   0x00 REQUEST / 0x01 REQUEST_NO_RETURN /
                               0x02 NOTIFICATION / 0x80 RESPONSE / 0x81 ERROR
    +15  return_code      1B   0x00 成功；错误码见下
    +16  payload          N B

payload 序列化（教学版自定义，全部大端）：
    int16  = 2 字节补码        struct.pack(">h", v)
    uint16 = 2 字节无符号      struct.pack(">H", v)
    int8   = 1 字节补码        struct.pack(">b", v)

服务定义（至少 2 个，与 docs/30 章表格一致）
-------------------------------------------
    VehicleService  service_id=0x1234, instance=0x0001, major=1
        Method 0x0001 GetSpeed        请求空            → 响应 int16 车速(km/h)
        Method 0x0002 SetSpeedLimit   请求 uint16 限速   → 响应 uint16 回显
        Event  0x8001 SpeedChanged    eventgroup=0x0001，周期 500ms → uint16 车速
    HvacService     service_id=0x5678, instance=0x0001, major=1
        Method 0x0001 SetTemp         请求 int8 温度     → 响应 int8 回显
        Event  0x8001 TempChanged     eventgroup=0x0001，温度变化时通知 → int8 温度

SOME/IP-SD 报文（教学简化，但字段对齐 AUTOSAR PRS_SOMEIPSD）
------------------------------------------------------------
SD 报文本身就是一条 SOME/IP 报文，只是固定：
    message_id = 0xFFFF 0x8100（service 0xFFFF / method 0x8100）
    message_type = 0x02 NOTIFICATION

SD payload 结构：
    flags(1B) + reserved(3B) + entries_length(4B) + entries + options_length(4B) + options

SD entry（固定 16 字节）：
    type(1B) | index1st(1B) | index2nd(1B) | num_options(1B: 高4位 run1/低4位 run2)
    | service_id(2B) | instance_id(2B) | major_version(1B) | TTL(3B)
    | minor_version(4B)          ← FindService(0x00) / OfferService(0x01)
    或 reserved(12bit)+counter(4bit)(2B) | eventgroup_id(2B)
                                 ← Subscribe(0x06) / SubscribeAck(0x07)

    · StopOffer       = OfferService 且 TTL=0
    · StopSubscribe   = SubscribeEventgroup 且 TTL=0
SD option（IPv4 端点，本仿真唯一用到的 option）：
    length(2B)=10 | type(1B)=0x04 | reserved(1B) | IPv4(4B) | reserved(1B)
    | protocol(1B: 0x11=UDP / 0x06=TCP) | port(2B)          → 共 12 字节

返回值（return_code）
---------------------
    0x00 OK
    0x01 未知服务        0x02 未知方法      0x03 interface_version 不匹配
    0x04 协议版本错误    0x05 message_type 错误   0x06 payload 长度/内容非法

    ⚠ 教学简化：真实 SOME/IP 标准返回码是 0x02 E_UNKNOWN_SERVICE、
      0x03 E_UNKNOWN_METHOD、0x08 E_WRONG_INTERFACE_VERSION（见 docs/30 章），
      本仿真按"本项目教学约定"统一压成 0x01/0x02/0x03，方便初学者记；
      对接真实项目时以标准表为准。

运行方式
--------
    .\\.venv\\Scripts\\python.exe simulators/someip_service.py
    .\\.venv\\Scripts\\python.exe simulators/someip_service.py --speed 80 --sd-port 30491 --svc-port 30502

环境变量（RF 库 SomeIpLibrary 用同一套）：
    SOMEIP_SD_PORT   服务发现 UDP 端口，默认 30490
    SOMEIP_SVC_PORT  SOME/IP 服务通信端口，默认 30501（UDP + TCP 同号）
    SOMEIP_HOST      默认 127.0.0.1

教学简化点（对应 docs/30 章 §30.9）
----------------------------------
1. SD 用 UDP **回环单播**（Find/Offer 点对点）代替真实的多播/广播 + 主动周期公告；
   启动公告发给 `--announce-port`（RF 库的 SD socket 端口）。
2. SD 的 option 索引简化：只放一个 IPv4 端点 option，订阅时取 options 数组里
   第一个端点作为通知目标（真实实现按 entry 的 option index/count 精确引用）。
3. 一个进程同时扮演多个服务实例（真实是不同 ECU/进程），instance 固定 0x0001。
4. 服务只在 IPv4 回环上跑，不做 TLS/IPsec；真实 SOME/IP 常跑在 VLAN/交换机上。
5. 事件通知固定走 UDP（真实可 UDP/TCP，取决于配置），周期 500ms 由定时器线程驱动。
6. 车速默认由仿真器"独立生成"（固定 60 km/h，`--speed` 可改）；传 `--use-can`
   可改为读虚拟 CAN 上 0x100 EngineData 的 VehicleSpeed 信号（CAN→SOA 桥接演示）。
7. 不做 offer 的 TTL 续约/超时自动下线（订阅 TTL 到期会被清理）。
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("someip_service")

# ---------------------------------------------------------------------------
# SOME/IP 协议常量
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = 0x01
HEADER_LEN = 16

MSG_TYPE_REQUEST = 0x00
MSG_TYPE_REQUEST_NO_RETURN = 0x01
MSG_TYPE_NOTIFICATION = 0x02
MSG_TYPE_RESPONSE = 0x80
MSG_TYPE_ERROR = 0x81

# 返回码（教学简化映射，见文件头）
RC_OK = 0x00
RC_UNKNOWN_SERVICE = 0x01
RC_UNKNOWN_METHOD = 0x02
RC_VERSION_MISMATCH = 0x03
RC_WRONG_PROTOCOL_VERSION = 0x04
RC_WRONG_MESSAGE_TYPE = 0x05
RC_MALFORMED_PAYLOAD = 0x06

RC_NAMES = {
    RC_OK: "OK",
    RC_UNKNOWN_SERVICE: "unknown service（未知服务）",
    RC_UNKNOWN_METHOD: "unknown method（未知方法）",
    RC_VERSION_MISMATCH: "interface version mismatch（版本不匹配）",
    RC_WRONG_PROTOCOL_VERSION: "wrong protocol version（协议版本错误）",
    RC_WRONG_MESSAGE_TYPE: "wrong message type（报文类型错误）",
    RC_MALFORMED_PAYLOAD: "malformed payload（payload 非法）",
}

MSG_TYPE_NAMES = {
    MSG_TYPE_REQUEST: "REQUEST",
    MSG_TYPE_REQUEST_NO_RETURN: "REQUEST_NO_RETURN",
    MSG_TYPE_NOTIFICATION: "NOTIFICATION",
    MSG_TYPE_RESPONSE: "RESPONSE",
    MSG_TYPE_ERROR: "ERROR",
}

# ---------------------------------------------------------------------------
# SOME/IP-SD 常量（AUTOSAR PRS_SOMEIPServiceDiscoveryProtocol）
# ---------------------------------------------------------------------------
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

# 本仿真的逻辑地址/端口之外的常量
DEFAULT_SD_PORT = 30490
DEFAULT_SVC_PORT = 30501
MAX_MESSAGE_LEN = 64 * 1024       # 教学版报文上限
SD_TTL_SECONDS = 3                # Find/Offer 的 TTL（本仿真只做展示）
SD_ACK_TTL_SECONDS = 10           # SubscribeAck 里下发的订阅 TTL

# ---------------------------------------------------------------------------
# 服务定义表（真实项目里由 ARXML/服务清单描述，这里用 Python 字典）
# ---------------------------------------------------------------------------
SERVICE_TABLE: dict[int, dict] = {
    0x1234: {
        "name": "VehicleService",
        "instance": 0x0001,
        "major_version": 1,
        "minor_version": 0,
        "methods": {
            0x0001: {"name": "GetSpeed", "request_len": 0, "response_len": 2},
            0x0002: {"name": "SetSpeedLimit", "request_len": 2, "response_len": 2},
        },
        "events": {
            0x8001: {"name": "SpeedChanged", "eventgroup": 0x0001,
                     "period": 0.5, "payload_len": 2},
        },
    },
    0x5678: {
        "name": "HvacService",
        "instance": 0x0001,
        "major_version": 1,
        "minor_version": 0,
        "methods": {
            0x0001: {"name": "SetTemp", "request_len": 1, "response_len": 1},
        },
        "events": {
            0x8001: {"name": "TempChanged", "eventgroup": 0x0001,
                     "period": None, "payload_len": 1},
        },
    },
}


# ---------------------------------------------------------------------------
# 报文编解码（服务端独立实现；测试端 SomeIpLibrary 也自己实现一套，
# 两边独立编码/解码才能互相校验，而不是"自己测自己"）
# ---------------------------------------------------------------------------
@dataclass
class SomeIpMessage:
    service_id: int
    method_id: int
    length: int
    request_id: int
    protocol_version: int
    interface_version: int
    message_type: int
    return_code: int
    payload: bytes
    raw: bytes = b""

    @property
    def client_id(self) -> int:
        return (self.request_id >> 16) & 0xFFFF

    @property
    def session_id(self) -> int:
        return self.request_id & 0xFFFF

    def describe(self) -> str:
        return (f"service={self.service_id:#06x} method={self.method_id:#06x} "
                f"type={MSG_TYPE_NAMES.get(self.message_type, hex(self.message_type))} "
                f"rc={self.return_code:#04x} request_id={self.request_id:#010x} "
                f"payload={self.payload.hex()}")


def build_message(service_id: int, method_id: int, message_type: int,
                  request_id: int, interface_version: int,
                  return_code: int = RC_OK, payload: bytes = b"") -> bytes:
    """组一条 16 字节头的 SOME/IP 报文。"""
    length = 8 + len(payload)
    return struct.pack(
        ">HHIIBBBB",
        service_id & 0xFFFF, method_id & 0xFFFF, length, request_id & 0xFFFFFFFF,
        PROTOCOL_VERSION, interface_version & 0xFF, message_type & 0xFF,
        return_code & 0xFF,
    ) + payload


def parse_message(data: bytes) -> SomeIpMessage:
    """解析 SOME/IP 报文；长度字段与实际不一致时抛 ValueError。"""
    if len(data) < HEADER_LEN:
        raise ValueError(f"报文不足 {HEADER_LEN} 字节（收到 {len(data)}）")
    service_id, method_id, length, request_id, proto, iface, mtype, rc = \
        struct.unpack(">HHIIBBBB", data[:HEADER_LEN])
    actual_payload = len(data) - HEADER_LEN
    if length != 8 + actual_payload:
        raise ValueError(
            f"length 字段={length}，实际应为 8+{actual_payload}={8 + actual_payload}（报文被截断或伪造）")
    return SomeIpMessage(
        service_id=service_id, method_id=method_id, length=length,
        request_id=request_id, protocol_version=proto, interface_version=iface,
        message_type=mtype, return_code=rc, payload=data[HEADER_LEN:], raw=bytes(data),
    )


# ------------------------------ SD 编解码 ----------------------------------
def build_sd_option_ipv4(host: str, port: int,
                         protocol: int = SD_PROTOCOL_UDP) -> bytes:
    """IPv4 端点 option：length(2)=10 + type(1)=0x04 + reserved + IP + ... + port。"""
    ip_bytes = socket.inet_aton(host)
    # length 字段值 = 长度字段之后的字节数 = 1+1+4+1+1+2 = 10
    return struct.pack(">HBB4sBBH", 10, SD_OPTION_IPV4_ENDPOINT, 0,
                       ip_bytes, 0, protocol, port & 0xFFFF)


def _ttl3(ttl: int) -> bytes:
    ttl &= 0xFFFFFF
    return bytes([(ttl >> 16) & 0xFF, (ttl >> 8) & 0xFF, ttl & 0xFF])


def build_service_entry(entry_type: int, service_id: int, instance_id: int,
                        major_version: int, ttl: int, minor_version: int = 0,
                        option_index: int = 0, option_count: int = 0) -> bytes:
    """FindService(0x00) / OfferService(0x01) 的 16 字节 entry。"""
    num_options = ((option_count & 0x0F) << 4)  # 高 4 位 = 第一组 option 数量
    return (struct.pack(">BBBBHHB", entry_type, option_index & 0xFF, 0,
                        num_options, service_id & 0xFFFF, instance_id & 0xFFFF,
                        major_version & 0xFF)
            + _ttl3(ttl)
            + struct.pack(">I", minor_version & 0xFFFFFFFF))


def build_eventgroup_entry(entry_type: int, service_id: int, instance_id: int,
                           major_version: int, ttl: int, eventgroup_id: int,
                           counter: int = 0, option_index: int = 0,
                           option_count: int = 0) -> bytes:
    """Subscribe(0x06) / SubscribeAck(0x07) 的 16 字节 entry。

    注意：last 4 字节 = reserved(12 bit)+counter(4 bit) 然后 eventgroup_id(2B)。
    """
    num_options = ((option_count & 0x0F) << 4)
    reserved_counter = ((0x000 & 0x0FFF) << 4) | (counter & 0x0F)
    return (struct.pack(">BBBBHHB", entry_type, option_index & 0xFF, 0,
                        num_options, service_id & 0xFFFF, instance_id & 0xFFFF,
                        major_version & 0xFF)
            + _ttl3(ttl)
            + struct.pack(">HH", reserved_counter, eventgroup_id & 0xFFFF))


def build_sd_payload(entries: list[bytes], options: list[bytes]) -> bytes:
    """SD payload = flags(1)+reserved(3)+entries_len(4)+entries+options_len(4)+options。"""
    entries_blob = b"".join(entries)
    options_blob = b"".join(options)
    return (b"\x00" + b"\x00\x00\x00"
            + struct.pack(">I", len(entries_blob)) + entries_blob
            + struct.pack(">I", len(options_blob)) + options_blob)


def build_sd_message(entries: list[bytes], options: list[bytes]) -> bytes:
    """把 SD payload 包进 SOME/IP 报文（service 0xFFFF / method 0x8100）。"""
    return build_message(SD_SERVICE_ID, SD_METHOD_ID, MSG_TYPE_NOTIFICATION,
                         request_id=0x00000000, interface_version=0x01,
                         payload=build_sd_payload(entries, options))


def parse_sd_payload(payload: bytes) -> tuple[list[dict], list[dict]]:
    """解析 SD payload，返回 (entries, options)。教学版按规范化布局解析。"""
    if len(payload) < 12:
        raise ValueError(f"SD payload 太短：{len(payload)} 字节")
    # flags(1)+reserved(3)
    offset = 4
    entries_len = struct.unpack(">I", payload[offset:offset + 4])[0]
    offset += 4
    if entries_len % SD_ENTRY_LEN != 0:
        raise ValueError(f"entries_length={entries_len} 不是 16 的倍数")
    entries_raw = payload[offset:offset + entries_len]
    if len(entries_raw) != entries_len:
        raise ValueError("SD entries 数组被截断")
    offset += entries_len
    if offset + 4 > len(payload):
        raise ValueError("SD options_length 字段缺失")
    options_len = struct.unpack(">I", payload[offset:offset + 4])[0]
    offset += 4
    options_raw = payload[offset:offset + options_len]
    if len(options_raw) != options_len:
        raise ValueError("SD options 数组被截断")

    entries: list[dict] = []
    for i in range(0, entries_len, SD_ENTRY_LEN):
        raw = entries_raw[i:i + SD_ENTRY_LEN]
        entry = {
            "type": raw[0],
            "option_index": raw[1],
            "num_options": (raw[3] >> 4) & 0x0F,
            "service_id": struct.unpack(">H", raw[4:6])[0],
            "instance_id": struct.unpack(">H", raw[6:8])[0],
            "major_version": raw[8],
            "ttl": (raw[9] << 16) | (raw[10] << 8) | raw[11],
            "raw": bytes(raw),
        }
        if raw[0] in (SD_ENTRY_FIND_SERVICE, SD_ENTRY_OFFER_SERVICE):
            entry["minor_version"] = struct.unpack(">I", raw[12:16])[0]
        else:
            reserved_counter = struct.unpack(">H", raw[12:14])[0]
            entry["counter"] = reserved_counter & 0x0F
            entry["eventgroup_id"] = struct.unpack(">H", raw[14:16])[0]
        entries.append(entry)

    options: list[dict] = []
    pos = 0
    while pos + 2 <= len(options_raw):
        opt_len = struct.unpack(">H", options_raw[pos:pos + 2])[0]
        if pos + 2 + opt_len > len(options_raw):
            raise ValueError("SD option 被截断")
        body = options_raw[pos + 2:pos + 2 + opt_len]
        if len(body) < 2:
            raise ValueError("SD option 太短")
        opt = {"type": body[0], "raw": bytes(options_raw[pos:pos + 2 + opt_len])}
        if body[0] == SD_OPTION_IPV4_ENDPOINT and len(body) >= 10:
            opt["address"] = socket.inet_ntoa(body[2:6])
            opt["protocol"] = body[7]
            opt["port"] = struct.unpack(">H", body[8:10])[0]
        options.append(opt)
        pos += 2 + opt_len
    return entries, options


# ---------------------------------------------------------------------------
# 订阅记录
# ---------------------------------------------------------------------------
@dataclass
class _Subscription:
    address: tuple[str, int]
    expires_at: float
    eventgroup_id: int


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class SomeIpService:
    """SOME/IP 服务端 + SOME/IP-SD 实体（一个进程扮演整台车里的服务集合）。"""

    def __init__(self, host: str = "127.0.0.1", sd_port: int = DEFAULT_SD_PORT,
                 svc_port: int = DEFAULT_SVC_PORT,
                 announce_host: str = "127.0.0.1", announce_port: int = 0,
                 speed: float = 60.0, use_can: bool = False) -> None:
        self.host = host
        self.sd_port = int(sd_port)
        self.svc_port = int(svc_port)
        self.announce_host = announce_host
        self.announce_port = int(announce_port)
        self.speed = float(speed)              # km/h（SOME/IP 车速服务的数据源）
        self.speed_limit = 0                   # km/h（SetSpeedLimit 设定值）
        self.temp = 22                         # ℃（HvacService 当前温度）
        self.use_can = bool(use_can)

        self._sd_sock: socket.socket | None = None
        self._udp_sock: socket.socket | None = None
        self._tcp_sock: socket.socket | None = None
        self._tcp_clients: set[socket.socket] = set()
        self._tcp_clients_lock = threading.Lock()
        self._subs: dict[tuple[int, int], list[_Subscription]] = {}
        self._subs_lock = threading.Lock()
        self._running = False
        self._threads: list[threading.Thread] = []
        self._session_counter = 0
        self._can_bridge: threading.Thread | None = None

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        """绑定端口 → 起 SD/服务/事件线程 → 发启动公告。"""
        try:
            self._sd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sd_sock.bind((self.host, self.sd_port))
            self._sd_sock.settimeout(0.2)

            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.bind((self.host, self.svc_port))
            self._udp_sock.settimeout(0.2)

            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.bind((self.host, self.svc_port))
            self._tcp_sock.listen(8)
            self._tcp_sock.settimeout(0.2)
        except OSError as exc:
            self._close_sockets()
            raise OSError(
                f"SOME/IP 端口绑定失败（SD UDP {self.host}:{self.sd_port} / "
                f"服务 UDP+TCP {self.host}:{self.svc_port}）：{exc}。"
                f"请确认没有别的 SOME/IP 仿真在跑，或用环境变量 "
                f"SOMEIP_SD_PORT / SOMEIP_SVC_PORT 换端口") from exc

        self._running = True
        self._spawn(self._sd_loop, "sd-loop")
        self._spawn(self._udp_service_loop, "udp-service-loop")
        self._spawn(self._tcp_accept_loop, "tcp-accept-loop")
        self._spawn(self._event_loop, "event-loop")
        if self.use_can:
            self._start_can_bridge()

        # 服务端启动时主动发 OfferService 公告（真实是周期多播；本仿真单播发给测试端）
        self._send_offer_announcement(stop=False)

        print(
            f"[SOMEIP] READY sd={self.host}:{self.sd_port} "
            f"svc={self.host}:{self.svc_port}(udp+tcp) "
            f"services={','.join(f'{sid:#06x}' for sid in SERVICE_TABLE)} "
            f"speed={self.speed:g}km/h",
            flush=True,
        )
        log.info("SOME/IP 服务已就绪：SD %s:%d，服务端口 %s:%d（UDP+TCP）",
                 self.host, self.sd_port, self.host, self.svc_port)

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    # ------------------------------------------------------------------ 停止
    def _close_sockets(self) -> None:
        with self._tcp_clients_lock:
            clients = list(self._tcp_clients)
            self._tcp_clients.clear()
        for conn in clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        for sock in (self._tcp_sock, self._udp_sock, self._sd_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._tcp_sock = self._udp_sock = self._sd_sock = None

    def stop(self) -> None:
        # 下线前发 StopOffer（TTL=0）：告诉测试端"服务要没了"
        if self._running and self.announce_port:
            self._send_offer_announcement(stop=True)
        self._running = False
        self._close_sockets()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        if self._can_bridge is not None and self._can_bridge.is_alive():
            self._can_bridge.join(timeout=1.0)
        log.info("SOME/IP 服务已停止")

    # ------------------------------------------------------------------ SD
    def _sd_loop(self) -> None:
        while self._running and self._sd_sock is not None:
            try:
                data, addr = self._sd_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_sd_datagram(data, addr)
            except Exception:      # noqa: BLE001 — SD 线程不能因单个坏包退出
                log.exception("处理 SD 报文异常（服务继续运行）")

    def _handle_sd_datagram(self, data: bytes, addr: tuple) -> None:
        try:
            msg = parse_message(data)
        except ValueError as exc:
            log.warning("SD 报文格式非法，已丢弃：%s", exc)
            return
        if msg.service_id != SD_SERVICE_ID or msg.method_id != SD_METHOD_ID:
            log.debug("非 SD 报文（service=%#06x method=%#06x），忽略",
                      msg.service_id, msg.method_id)
            return
        try:
            entries, options = parse_sd_payload(msg.payload)
        except ValueError as exc:
            log.warning("SD payload 非法，已丢弃：%s", exc)
            return

        for entry in entries:
            etype = entry["type"]
            if etype == SD_ENTRY_FIND_SERVICE:
                self._handle_find_service(entry, options, addr)
            elif etype == SD_ENTRY_SUBSCRIBE_EVENTGROUP:
                self._handle_subscribe(entry, options, addr)
            elif etype == SD_ENTRY_OFFER_SERVICE:
                # 服务端收到别人的 Offer 不处理（本仿真只有自己一个服务端）
                log.debug("收到 OfferService entry（忽略）：service=%#06x", entry["service_id"])
            else:
                log.debug("忽略 SD entry type=%#04x", etype)

    def _handle_find_service(self, entry: dict, options: list[dict],
                             addr: tuple) -> None:
        service_id = entry["service_id"]
        instance_id = entry["instance_id"]
        targets = []
        for sid, sdef in SERVICE_TABLE.items():
            if service_id not in (0xFFFF, sid):
                continue
            if instance_id not in (0xFFFF, sdef["instance"]):
                continue
            targets.append(sid)
        if not targets:
            log.info("收到 FindService 但无匹配服务：service=%#06x instance=%#06x",
                     service_id, instance_id)
            return
        log.info("收到 FindService（service=%#06x instance=%#06x）→ 回 Offer：%s",
                 service_id, instance_id, [f"{s:#06x}" for s in targets])
        self._send_offers(addr, targets)

    def _handle_subscribe(self, entry: dict, options: list[dict],
                          addr: tuple) -> None:
        service_id = entry["service_id"]
        instance_id = entry["instance_id"]
        eventgroup_id = entry["eventgroup_id"]
        ttl = entry["ttl"]
        sdef = SERVICE_TABLE.get(service_id)
        if sdef is None or instance_id not in (sdef["instance"], 0xFFFF):
            log.warning("订阅了未知服务 service=%#06x instance=%#06x，忽略",
                        service_id, instance_id)
            return
        if entry["major_version"] not in (sdef["major_version"], 0xFF):
            log.warning("订阅版本不匹配 service=%#06x major=%d，忽略",
                        service_id, entry["major_version"])
            return
        known_groups = {ev["eventgroup"] for ev in sdef["events"].values()}
        if eventgroup_id not in known_groups:
            log.warning("订阅了未知 eventgroup %#06x（service=%#06x），忽略",
                        eventgroup_id, service_id)
            return

        # 从 options 里找 IPv4 endpoint（教学版取第一个）
        sub_addr = None
        for opt in options:
            if opt["type"] == SD_OPTION_IPV4_ENDPOINT and "port" in opt:
                sub_addr = (opt["address"], opt["port"])
                break
        if sub_addr is None:
            sub_addr = (addr[0], addr[1])   # 没带 option 就回到源地址
        key = (service_id, eventgroup_id)
        with self._subs_lock:
            subs = self._subs.setdefault(key, [])
            subs[:] = [s for s in subs if s.address != sub_addr]
            if ttl == 0:
                log.info("StopSubscribe：service=%#06x eventgroup=%#06x addr=%s",
                         service_id, eventgroup_id, sub_addr)
            else:
                subs.append(_Subscription(address=sub_addr,
                                          expires_at=time.monotonic() + ttl,
                                          eventgroup_id=eventgroup_id))
                log.info("订阅成功：service=%#06x eventgroup=%#06x addr=%s ttl=%ds",
                         service_id, eventgroup_id, sub_addr, ttl)
        # 回 SubscribeEventgroupAck（0x07）
        self._send_sd(addr, [
            build_eventgroup_entry(
                SD_ENTRY_SUBSCRIBE_EVENTGROUP_ACK, service_id, instance_id,
                sdef["major_version"], SD_ACK_TTL_SECONDS, eventgroup_id,
                counter=entry["counter"],
            )
        ], [])

    # ------------------------------------------------------------------ 公告
    def _offer_entries(self, service_ids: list[int], ttl: int, stop: bool) -> bytes:
        entries = []
        for sid in service_ids:
            sdef = SERVICE_TABLE[sid]
            entries.append(build_service_entry(
                SD_ENTRY_OFFER_SERVICE, sid, sdef["instance"],
                sdef["major_version"], 0 if stop else ttl,
                sdef["minor_version"], option_index=0, option_count=1,
            ))
        # 端点 option：服务通信 UDP 端口
        options = [build_sd_option_ipv4(self.host, self.svc_port, SD_PROTOCOL_UDP)]
        return build_sd_message(entries, options)

    def _send_offers(self, addr: tuple, service_ids: list[int]) -> None:
        self._send_sd_raw(addr, self._offer_entries(service_ids, SD_TTL_SECONDS, stop=False))

    def _send_offer_announcement(self, stop: bool) -> None:
        if not self.announce_port:
            log.info("未指定 --announce-port，跳过启动公告（测试端仍可 Find）")
            return
        addr = (self.announce_host, self.announce_port)
        payload = self._offer_entries(list(SERVICE_TABLE), SD_TTL_SECONDS, stop=stop)
        self._send_sd_raw(addr, payload)
        log.info("已向 %s 发送 %s 公告（%d 个服务）", addr,
                 "StopOffer" if stop else "OfferService", len(SERVICE_TABLE))

    def _send_sd(self, addr: tuple, entries: list[bytes],
                 options: list[bytes]) -> None:
        self._send_sd_raw(addr, build_sd_message(entries, options))

    def _send_sd_raw(self, addr: tuple, raw: bytes) -> None:
        sock = self._sd_sock
        if sock is None:
            return
        try:
            sock.sendto(raw, addr)
        except OSError as exc:
            log.warning("SD 报文发送失败（%s）：%s", addr, exc)

    # ------------------------------------------------------------------ 事件
    def _event_loop(self) -> None:
        """周期事件：VehicleService.SpeedChanged 每 500ms 一条。"""
        next_tick = time.monotonic()
        while self._running:
            next_tick += 0.5
            now = time.monotonic()
            if next_tick > now:
                time.sleep(next_tick - now)
            if not self._running:
                break
            try:
                self._expire_subscriptions()
                self._send_speed_notification()
            except Exception:      # noqa: BLE001 — 事件线程不能死
                log.exception("事件发送异常（继续运行）")

    def _expire_subscriptions(self) -> None:
        now = time.monotonic()
        with self._subs_lock:
            for key in list(self._subs):
                self._subs[key] = [s for s in self._subs[key] if s.expires_at > now]
                if not self._subs[key]:
                    del self._subs[key]

    def _subscribers(self, service_id: int, eventgroup_id: int) -> list[tuple]:
        with self._subs_lock:
            return [s.address for s in self._subs.get((service_id, eventgroup_id), [])]

    def _next_notification_request_id(self) -> int:
        self._session_counter = (self._session_counter + 1) & 0xFFFF
        return (0x0000 << 16) | self._session_counter   # client_id=0（服务端发出）

    def _send_speed_notification(self) -> None:
        targets = self._subscribers(0x1234, 0x0001)
        if not targets:
            return
        payload = struct.pack(">H", max(0, min(65535, int(round(self.speed)))))
        raw = build_message(0x1234, 0x8001, MSG_TYPE_NOTIFICATION,
                            self._next_notification_request_id(),
                            SERVICE_TABLE[0x1234]["major_version"],
                            RC_OK, payload)
        for addr in targets:
            try:
                self._sd_sock.sendto(raw, addr) if self._sd_sock else None
            except OSError as exc:
                log.warning("发送 SpeedChanged 通知失败（%s）：%s", addr, exc)

    def _send_temp_notification(self) -> None:
        targets = self._subscribers(0x5678, 0x0001)
        if not targets:
            return
        payload = struct.pack(">b", max(-128, min(127, int(self.temp))))
        raw = build_message(0x5678, 0x8001, MSG_TYPE_NOTIFICATION,
                            self._next_notification_request_id(),
                            SERVICE_TABLE[0x5678]["major_version"],
                            RC_OK, payload)
        for addr in targets:
            try:
                self._sd_sock.sendto(raw, addr) if self._sd_sock else None
            except OSError as exc:
                log.warning("发送 TempChanged 通知失败（%s）：%s", addr, exc)

    # ------------------------------------------------------------------ UDP 服务
    def _udp_service_loop(self) -> None:
        while self._running and self._udp_sock is not None:
            try:
                data, addr = self._udp_sock.recvfrom(MAX_MESSAGE_LEN)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                response = self._handle_request(data, transport="udp")
            except _MalformedFrame as exc:
                # UDP：静默丢弃（不响应、不崩溃），日志留证据
                log.warning("UDP 收到畸形 SOME/IP 报文，已丢弃：%s", exc)
                continue
            except Exception:      # noqa: BLE001
                log.exception("处理 UDP 请求异常（服务继续运行）")
                continue
            if response is not None:
                try:
                    self._udp_sock.sendto(response, addr)
                except OSError as exc:
                    log.warning("UDP 响应发送失败（%s）：%s", addr, exc)

    # ------------------------------------------------------------------ TCP 服务
    def _tcp_accept_loop(self) -> None:
        while self._running and self._tcp_sock is not None:
            try:
                conn, peer = self._tcp_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(None)
            with self._tcp_clients_lock:
                self._tcp_clients.add(conn)
            log.info("TCP 客户端已连接：%s", peer)
            self._spawn(lambda c=conn, p=peer: self._tcp_client_loop(c, p),
                        f"tcp-client-{peer[1]}")

    def _tcp_client_loop(self, conn: socket.socket, peer: tuple) -> None:
        try:
            while self._running:
                header = _recv_exact(conn, HEADER_LEN)
                if header is None:
                    break
                length = struct.unpack(">I", header[4:8])[0]
                if length < 8 or 8 + (length - 8) > MAX_MESSAGE_LEN:
                    log.warning("TCP SOME/IP length 字段非法（%d），关闭连接 %s",
                                length, peer)
                    break
                body = _recv_exact(conn, length - 8) if length > 8 else b""
                if body is None:
                    break
                data = header + body
                try:
                    response = self._handle_request(data, transport="tcp")
                except _MalformedFrame as exc:
                    # TCP 是字节流，长度/格式不可信后无法再同步 → 关闭连接
                    log.warning("TCP 收到畸形 SOME/IP 报文，关闭连接 %s：%s", peer, exc)
                    break
                except Exception:      # noqa: BLE001
                    log.exception("处理 TCP 请求异常（关闭该连接）")
                    break
                if response is not None:
                    conn.sendall(response)
        except OSError as exc:
            log.debug("TCP 客户端连接异常结束 %s：%s", peer, exc)
        finally:
            with self._tcp_clients_lock:
                self._tcp_clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass
            log.info("TCP 客户端已断开：%s", peer)

    # ------------------------------------------------------------------ 请求处理
    def _handle_request(self, data: bytes, transport: str) -> bytes | None:
        """处理一条 SOME/IP 请求，返回响应报文（无响应返回 None）。

        抛 _MalformedFrame：长度/头不可信（UDP 丢弃、TCP 关闭连接）。
        """
        if len(data) < HEADER_LEN:
            raise _MalformedFrame(f"报文不足 {HEADER_LEN} 字节（{len(data)}）")
        try:
            msg = parse_message(data)
        except ValueError as exc:
            raise _MalformedFrame(str(exc)) from exc

        log.info("[%s] RX %s", transport.upper(), msg.describe())

        # ① 协议版本
        if msg.protocol_version != PROTOCOL_VERSION:
            return self._error_response(
                msg, RC_WRONG_PROTOCOL_VERSION,
                f"protocol_version={msg.protocol_version:#04x}（期望 0x01）")
        # ② 报文类型：服务端只处理 REQUEST / REQUEST_NO_RETURN
        if msg.message_type not in (MSG_TYPE_REQUEST, MSG_TYPE_REQUEST_NO_RETURN):
            return self._error_response(
                msg, RC_WRONG_MESSAGE_TYPE,
                f"message_type={msg.message_type:#04x}（服务端不接受）")
        # ③ 服务
        sdef = SERVICE_TABLE.get(msg.service_id)
        if sdef is None:
            return self._error_response(
                msg, RC_UNKNOWN_SERVICE, f"service_id={msg.service_id:#06x} 未定义")
        # ④ 版本
        if msg.interface_version != sdef["major_version"]:
            return self._error_response(
                msg, RC_VERSION_MISMATCH,
                f"interface_version={msg.interface_version}（期望 {sdef['major_version']}）")
        # ⑤ 方法
        mdef = sdef["methods"].get(msg.method_id)
        if mdef is None:
            return self._error_response(
                msg, RC_UNKNOWN_METHOD,
                f"method_id={msg.method_id:#06x} 在 {sdef['name']} 中未定义")
        # ⑥ payload 长度
        if len(msg.payload) != mdef["request_len"]:
            return self._error_response(
                msg, RC_MALFORMED_PAYLOAD,
                f"{mdef['name']} 请求 payload 应为 {mdef['request_len']} 字节，"
                f"实际 {len(msg.payload)}")

        response_payload = self._execute_method(msg.service_id, msg.method_id, msg.payload, mdef)
        log.info("执行方法 %s.%s → payload=%s", sdef["name"], mdef["name"],
                 response_payload.hex())

        # REQUEST_NO_RETURN：执行但不回复（真实 SOME/IP 的"发了就不管"语义）
        if msg.message_type == MSG_TYPE_REQUEST_NO_RETURN:
            return None
        return build_message(msg.service_id, msg.method_id, MSG_TYPE_RESPONSE,
                             msg.request_id, msg.interface_version, RC_OK,
                             response_payload)

    def _execute_method(self, service_id: int, method_id: int,
                        payload: bytes, mdef: dict) -> bytes:
        if service_id == 0x1234 and method_id == 0x0001:      # GetSpeed
            return struct.pack(">h", max(-32768, min(32767, int(round(self.speed)))))
        if service_id == 0x1234 and method_id == 0x0002:      # SetSpeedLimit
            self.speed_limit = struct.unpack(">H", payload)[0]
            log.info("限速设定为 %d km/h（不改变当前车速）", self.speed_limit)
            return bytes(payload)                             # echo
        if service_id == 0x5678 and method_id == 0x0001:      # SetTemp
            self.temp = struct.unpack(">b", payload)[0]
            log.info("空调温度设定为 %d℃", self.temp)
            self._send_temp_notification()                    # 温度变化 → TempChanged
            return bytes(payload)                             # echo
        raise AssertionError(f"未实现的方法 service={service_id:#06x} method={method_id:#06x}")

    def _error_response(self, msg: SomeIpMessage, code: int, detail: str) -> bytes:
        log.warning("请求被拒绝：%s（%s）", RC_NAMES.get(code, hex(code)), detail)
        return build_message(msg.service_id, msg.method_id, MSG_TYPE_ERROR,
                             msg.request_id, msg.interface_version, code)

    # ------------------------------------------------------------------ CAN 桥
    def _start_can_bridge(self) -> None:
        """可选：把虚拟 CAN 上 0x100 的 VehicleSpeed 桥接成 SOME/IP 服务数据源。"""
        self._can_bridge = threading.Thread(target=self._can_bridge_loop,
                                            name="can-bridge", daemon=True)
        self._can_bridge.start()

    def _can_bridge_loop(self) -> None:
        try:
            import cantools
            sys.path.insert(0, str(Path(__file__).parent))
            from bus import VirtualCanBus

            db = cantools.database.load_file(
                str(Path(__file__).parent / "dbc" / "vehicle.dbc"))
            msg_def = db.get_message_by_name("EngineData")
            bus = VirtualCanBus("SOMEIP-SVC", echo_own=False)
        except Exception as exc:      # noqa: BLE001
            log.warning("CAN 车速桥启动失败（回退到独立生成车速）：%s", exc)
            return
        log.info("CAN 车速桥已启动：监听 CAN 0x%03x 的 VehicleSpeed", msg_def.frame_id)
        try:
            while self._running:
                msg = bus.recv_frame(timeout=0.2)
                if msg is None or msg.arbitration_id != msg_def.frame_id:
                    continue
                try:
                    decoded = db.decode_message(msg.arbitration_id, bytes(msg.data))
                    self.speed = float(decoded.get("VehicleSpeed", self.speed))
                except Exception:      # noqa: BLE001
                    continue
        finally:
            bus.shutdown()

    # ------------------------------------------------------------------ 自测辅助
    @staticmethod
    def describe_services() -> str:
        rows = []
        for sid, sdef in SERVICE_TABLE.items():
            methods = ", ".join(f"{mid:#06x} {m['name']}"
                                for mid, m in sdef["methods"].items())
            events = ", ".join(f"{eid:#06x} {e['name']}"
                               for eid, e in sdef["events"].items())
            rows.append(f"{sdef['name']}({sid:#06x}) methods=[{methods}] events=[{events}]")
        return "\n".join(rows)


class _MalformedFrame(Exception):
    """SOME/IP 报文长度/头不可信（length 字段与实际不符等）。"""


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    """TCP 流精确读 size 字节；对端关闭返回 None。"""
    buf = bytearray()
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip(), 0)
    except ValueError as exc:
        raise SystemExit(f"环境变量 {name}={raw!r} 不是合法整数") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SOME/IP + SOME/IP-SD 教学版服务仿真："
                    "SD UDP 30490 / 服务 UDP+TCP 30501")
    parser.add_argument("--host", default=os.environ.get("SOMEIP_HOST", "127.0.0.1"),
                        help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--sd-port", type=int,
                        default=_env_int("SOMEIP_SD_PORT", DEFAULT_SD_PORT),
                        help="SOME/IP-SD UDP 端口（默认 30490，env SOMEIP_SD_PORT）")
    parser.add_argument("--svc-port", type=int,
                        default=_env_int("SOMEIP_SVC_PORT", DEFAULT_SVC_PORT),
                        help="SOME/IP 服务 UDP+TCP 端口（默认 30501，env SOMEIP_SVC_PORT）")
    parser.add_argument("--announce-host", default="127.0.0.1",
                        help="启动 OfferService 公告的目标地址（默认 127.0.0.1）")
    parser.add_argument("--announce-port", type=int, default=0,
                        help="启动公告的目标 UDP 端口（RF 库的 SD socket 端口；0=不发）")
    parser.add_argument("--speed", type=float, default=60.0,
                        help="车速服务的数据源初值 km/h（默认 60，独立生成）")
    parser.add_argument("--use-can", action="store_true",
                        help="启用后从虚拟 CAN 0x100 的 VehicleSpeed 取车速（CAN→SOA 桥接演示）")
    parser.add_argument("--log-level", default="INFO",
                        help="日志级别 DEBUG/INFO/WARNING（默认 INFO）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [someip_service] %(message)s",
        stream=sys.stderr,
    )
    service = SomeIpService(
        host=args.host, sd_port=args.sd_port, svc_port=args.svc_port,
        announce_host=args.announce_host, announce_port=args.announce_port,
        speed=args.speed, use_can=args.use_can,
    )
    try:
        service.start()
    except OSError as exc:
        print(f"[SOMEIP] 启动失败：{exc}", flush=True)
        return 2
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[SOMEIP] 收到 Ctrl+C，正在退出...", flush=True)
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
