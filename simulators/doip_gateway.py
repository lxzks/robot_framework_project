"""
doip_gateway.py — DoIP 诊断网关仿真（ISO 13400 教学版）
==========================================================

解决什么问题
------------
真实的车载诊断有两条路：

    ① CAN 诊断：Tester --CAN(0x7E0/0x7E8)--> ECU
    ② DoIP 诊断：Tester --以太网/TCP--> DoIP 实体(网关) --CAN--> ECU

UDS 服务（10 03 / 22 F1 90 / 3E 00 ...）**完全不变**，变的只是"运输层"：
CAN 单帧 8 字节放不下，于是用 ISO-TP 拆多帧；以太网带宽大，UDS 整包
放进 DoIP 帧一次发完。4S 店的远程诊断、OTA 刷写走的就是第 ② 条路。

本项目没有以太网硬件，怎么办？用**本机回环**造一条"以太网"：

    RF 用例 (/ DoipLibrary)
          │  TCP 127.0.0.1:13400  ← 本文件模拟"以太网 + DoIP 实体"
          ▼
    doip_gateway.py（本文件）
          │  UDP 组播 233.255.0.1:30000（复用 simulators/bus.py 的虚拟 CAN 总线）
          ▼
    ecu_sim.py（动力域 ECU，监听 0x7E0、回 0x7E8）

关键实现：本文件**复用项目已有的** VirtualCanBus + DiagnosticTester 来转发
UDS —— 不重复造 CAN 轮子，也保证和 02/03 套件看到的是同一套 ECU 行为。

UDP 13400 模拟 ISO 13400 的"车辆识别"：上电后 Tester 还不知道车在哪、
VIN 是什么，于是发 UDP 车辆识别请求，网关回 VIN/逻辑地址/EID/GID。

DoIP 通用头（8 字节，ISO 13400-2）
---------------------------------
    +0  protocol_version  1B   固定 0x02（ISO 13400-2:2012 版本）
    +1  inverse_version   1B   version 按位取反 = 0xFD（用于快速校验）
    +2  payload_type      2B   big-endian，见下表
    +4  payload_length    4B   big-endian，payload 字节数
    +8  payload           N B

说明：protocol_version=0x02 对应 ISO 13400-2:2012；DoIP 通用头在所有版本里都是
这 8 字节（0x01=2010 版，0x02=2012 版，2019 版起为 0x03，另有 TLS/认证等新能力）。
ISO 13400-2 还允许车辆识别请求用 protocol_version=0xFF（Tester 不知道协议版本时），
本教学版只支持 0x02（见 docs/24 章 §24.11 简化点）。

payload type 一览（本仿真支持的"教学子集"）
------------------------------------------
    0x0000  Generic DoIP header NACK   payload = NACK code(1B)
    0x0001  车辆识别请求（UDP）          payload 空
    0x0002  车辆识别请求（带 EID，UDP）   6B EID
    0x0003  车辆识别请求（带 VIN，UDP）   17B VIN
    0x0004  车辆识别响应/车辆公告         VIN(17)+逻辑地址(2)+EID(6)+GID(6)+further action(1)=32B
    0x0005  路由激活请求                 tester 地址(2)+activation type(1)+reserved(4)=7B
    0x0006  路由激活响应                 tester 地址(2)+实体地址(2)+code(1)+reserved(4)+OEM(4)=13B
    0x0007  alive check 请求             payload 空
    0x0008  alive check 响应             源地址(2)=DoIP 实体逻辑地址
    0x8001  诊断报文                    源地址(2)+目标地址(2)+UDS 数据(N)
    0x8002  诊断报文肯定确认              源地址(2)+目标地址(2)+ack code(1)
    0x8003  诊断报文否定确认              源地址(2)+目标地址(2)+NACK code(1)

NACK / 响应码映射（教学版，文档 24 章有完整表格）
------------------------------------------------
    通用头 NACK (0x0000)：
        0x00 头格式错误（inverse 不等于 version 取反，或协议版本不支持）→ 回完关闭 TCP
        0x01 未知 payload type                                       → 连接保持
        0x02 报文过大（payload_length 超过 64KB 教学上限）             → 回完关闭 TCP
        0x04 payload 长度异常（声明长度收不齐 / 类型要求长度不符）      → 收不齐时关闭
    诊断报文 NACK (0x8003)：
        0x06 target unreachable（路由未激活 / ECU 无响应超时）
        0x02 invalid source address（源地址与已激活的 tester 地址不符）
        0x03 unknown target address（目标地址不是本网关认识的 ECU）
    路由激活响应 code (0x0006)：
        0x10 路由激活成功
        0x00 未知 tester 地址（教学版只认 0x0E00~0x0EFF 外部诊断仪地址段）
        0x02 与已激活的 tester 地址不一致
        0x06 不支持的 activation type（教学版只支持 0x00 default）

逻辑地址约定（本仿真）
----------------------
    Tester（外部诊断仪）  0x0E00
    DoIP 实体（本网关）   0x0E80   ← 车辆识别响应里的"逻辑地址"
    ECU（动力域）         0x0001   ← 诊断报文的 target

运行方式
--------
    .\\.venv\\Scripts\\python.exe simulators/doip_gateway.py
    .\\.venv\\Scripts\\python.exe simulators/doip_gateway.py --tcp-port 13401 --udp-port 13401

环境变量（RF 库 DoipLibrary 用同一套）：
    DOIP_TCP_PORT   默认 13400
    DOIP_UDP_PORT   默认 13400（TCP/UDP 同号不冲突，协议不同）
    DOIP_HOST       默认 127.0.0.1

教学简化点（对应 docs/24 章 §24.11，读源码前先知道"哪些是假的"）
----------------------------------------------------------------
    1. "以太网"用本机回环 TCP 代替，没有 VLAN/交换机/100BASE-T1 物理层；
       端口 13400 是 ISO 13400 规定的 DoIP 端口，与 CAN 仿真端口 30000 错开。
    2. 只模拟一个 DoIP 实体 + 一个目标 ECU（0x0001）；多 ECU/多路由表未实现。
    3. 车辆识别按 UDP 单播请求/响应实现；真实的 UDP 广播/组播公告
       （车辆上电主动发 0x0004 announcement）未实现。
    4. 不做 TLS/安全认证；真实 DoIP 可要求 authenticated routing activation。
    5. DoIP 报文长度上限 64KB（真实上限 16MB 级），大响应不分段。
    6. UDS 超时重试一次（教学目的是抗仿真总线偶发丢帧；真实实现按 P2 定时器
       决定是否重发，且对非幂等服务不可随意重试）。
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
from dataclasses import dataclass
from pathlib import Path

# 复用项目已有的虚拟 CAN 总线与诊断仪（gateway 是"第二个 Tester"）
sys.path.insert(0, str(Path(__file__).parent))
from bus import DEFAULT_CHANNEL, DEFAULT_PORT, VirtualCanBus      # noqa: E402
from uds_client import DiagnosticTester                           # noqa: E402

log = logging.getLogger("doip_gateway")

# ---------------------------------------------------------------------------
# DoIP 协议常量（ISO 13400-2）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = 0x02
INVERSE_VERSION = 0xFD             # = ~0x02（8 位按位取反）
HEADER_LEN = 8

# payload type
PT_GENERIC_NACK = 0x0000
PT_VEHICLE_ID_REQUEST = 0x0001
PT_VEHICLE_ID_REQUEST_EID = 0x0002
PT_VEHICLE_ID_REQUEST_VIN = 0x0003
PT_VEHICLE_ANNOUNCEMENT = 0x0004
PT_ROUTING_ACTIVATION_REQUEST = 0x0005
PT_ROUTING_ACTIVATION_RESPONSE = 0x0006
PT_ALIVE_CHECK_REQUEST = 0x0007
PT_ALIVE_CHECK_RESPONSE = 0x0008
PT_DIAG_MESSAGE = 0x8001
PT_DIAG_ACK = 0x8002
PT_DIAG_NACK = 0x8003

# 本仿真的逻辑地址（见文件头"逻辑地址约定"）
TESTER_ADDRESS = 0x0E00
DOIP_ENTITY_ADDRESS = 0x0E80
ECU_LOGICAL_ADDRESS = 0x0001
TESTER_ADDRESS_MIN = 0x0E00        # 外部诊断仪地址段（真实项目由 OEM 规定）
TESTER_ADDRESS_MAX = 0x0EFF

# 车辆固定信息（真实网关从配置/ECU 读；本仿真与 ecu_sim 的 DID F190 一致）
VEHICLE_VIN = "LSVAM4180NC000001"
VEHICLE_EID = bytes.fromhex("001122334455")     # 实体标识（6B，真实首 3B 常为 OUI）
VEHICLE_GID = bytes.fromhex("000000000000")     # 组标识（6B，0=不分组）
FURTHER_ACTION_NONE = 0x00                      # 0x00 = 无需后续动作

# 路由激活类型：教学版只支持 0x00 default
ACTIVATION_TYPE_DEFAULT = 0x00
ACTIVATION_TYPE_NAMES = {
    0x00: "default",
    0x01: "WWH-OBD",
    0xE0: "central security",
}

# 各类响应码名称（日志/排错用，不是协议必须）
GENERIC_NACK_NAMES = {
    0x00: "incorrect pattern format",
    0x01: "unknown payload type",
    0x02: "message too large",
    0x03: "out of memory",
    0x04: "invalid payload length",
}
ROUTING_ACTIVATION_NAMES = {
    0x00: "unknown source address",
    0x02: "SA different from already activated SA",
    0x03: "SA already active on different socket",
    0x06: "unsupported routing activation type",
    0x10: "routing successfully activated",
}
DIAG_NACK_NAMES = {
    0x02: "invalid source address",
    0x03: "unknown target address",
    0x04: "diagnostic message too large",
    0x05: "out of memory",
    0x06: "target unreachable",
    0x07: "unknown network",
    0x08: "transport protocol error",
}

# 时间参数（秒）
UDS_FORWARD_TIMEOUT = 3.0          # 等 ECU 的 UDS 响应（与项目 DiagnosticTester 默认一致）
UDS_RETRY_INTERVAL = 0.2           # 重试前的冷静时间
PAYLOAD_IDLE_TIMEOUT = 1.0         # 声明长度后多久收不齐 → 判长度异常
MAX_PAYLOAD_LENGTH = 64 * 1024     # 教学版 DoIP 报文体上限
DEFAULT_DOIP_PORT = 13400


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
class _PayloadTimeout(Exception):
    """声明了 payload_length，但在超时时间内没收齐（长度与实际不符）。"""


def _env_int(name: str, default: int) -> int:
    """读环境变量里的端口/整数，非法值给出清晰报错（而不是神秘 traceback）。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip(), 0)
    except ValueError as exc:
        raise SystemExit(f"环境变量 {name}={raw!r} 不是合法整数") from exc
    return value


def _build_frame(payload_type: int, payload: bytes) -> bytes:
    """组一条 DoIP 帧：8 字节通用头 + payload。"""
    header = struct.pack(">BBHI", PROTOCOL_VERSION, INVERSE_VERSION,
                         payload_type, len(payload))
    return header + payload


def _recv_exact(conn: socket.socket, size: int,
                timeout: float | None) -> bytes | None:
    """从 TCP 流里精确读 size 字节。

    timeout=None：一直等（连接空闲时阻塞，靠关闭 socket 打断）。
    timeout=秒    ：到时抛 _PayloadTimeout（用于判定"长度与实际不符"）。
    连接被对端关闭（EOF）→ 返回 None。
    """
    buf = bytearray()
    while len(buf) < size:
        conn.settimeout(timeout)
        try:
            chunk = conn.recv(size - len(buf))
        except socket.timeout:
            raise _PayloadTimeout(
                f"等待 {size} 字节超时（已收到 {len(buf)} 字节）") from None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# 每个 TCP 连接的会话状态（路由激活是"按 socket"生效的）
# ---------------------------------------------------------------------------
@dataclass
class _ClientState:
    peer: tuple
    activated: bool = False
    tester_address: int = 0


class DoipGateway:
    """DoIP 实体/网关：TCP 承载 DoIP、UDP 做车辆识别、CAN 侧复用项目诊断仪。"""

    def __init__(
        self,
        tcp_host: str = "127.0.0.1",
        tcp_port: int = DEFAULT_DOIP_PORT,
        udp_host: str = "127.0.0.1",
        udp_port: int = DEFAULT_DOIP_PORT,
        bus_channel: str = DEFAULT_CHANNEL,
        bus_port: int = DEFAULT_PORT,
        vin: str = VEHICLE_VIN,
    ) -> None:
        vin_bytes = vin.encode("ascii")
        if len(vin_bytes) != 17:
            raise SystemExit(f"VIN 必须是 17 字节 ASCII，收到 {len(vin_bytes)} 字节: {vin!r}")
        self.tcp_host = tcp_host
        self.tcp_port = int(tcp_port)
        self.udp_host = udp_host
        self.udp_port = int(udp_port)
        self.bus_channel = bus_channel
        self.bus_port = int(bus_port)
        self.vin = vin_bytes

        self._tcp_sock: socket.socket | None = None
        self._udp_sock: socket.socket | None = None
        self._bus: VirtualCanBus | None = None
        self._tester: DiagnosticTester | None = None
        self._running = False
        self._threads: list[threading.Thread] = []
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._uds_lock = threading.Lock()      # CAN 诊断仪一次只处理一条请求

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        """绑定 TCP/UDP 端口 → 加入虚拟 CAN 总线 → 起服务线程。

        端口绑定放在最前面：端口被占用时立即失败，避免"网关起来了但连不上"
        的诡异现象（同时保证报错信息足够直白）。
        """
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.bind((self.udp_host, self.udp_port))
            self._udp_sock.settimeout(0.2)

            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.bind((self.tcp_host, self.tcp_port))
            self._tcp_sock.listen(8)
            self._tcp_sock.settimeout(0.2)
        except OSError as exc:
            self._close_listen_sockets()
            raise OSError(
                f"DoIP 端口绑定失败（TCP {self.tcp_host}:{self.tcp_port} / "
                f"UDP {self.udp_host}:{self.udp_port}）：{exc}。"
                f"请先确认没有别的 DoIP 网关/测试在运行，或用环境变量 "
                f"DOIP_TCP_PORT / DOIP_UDP_PORT 换一组端口") from exc

        # 复用项目虚拟 CAN 总线 + 诊断仪：我们把 UDS 请求转给 ECU
        self._bus = VirtualCanBus(
            "DOIP-GW",
            channel=self.bus_channel,
            port=self.bus_port,
            echo_own=False,          # 网关不需要回读自己发的 CAN 帧
        )
        self._tester = DiagnosticTester(self._bus)
        self._tester.start()

        self._running = True
        self._spawn(self._tcp_accept_loop, "tcp-accept")
        self._spawn(self._udp_loop, "udp-vehicle-id")

        # 就绪标记：DoipLibrary 靠它判断"进程起来了"（打印到 stdout，UTF-8）
        print(
            f"[DOIP-GATEWAY] READY tcp={self.tcp_host}:{self.tcp_port} "
            f"udp={self.udp_host}:{self.udp_port} "
            f"vin={self.vin.decode('ascii')} entity={DOIP_ENTITY_ADDRESS:#06x} "
            f"ecu={ECU_LOGICAL_ADDRESS:#06x}",
            flush=True,
        )
        log.info("DoIP 网关已就绪：TCP %s:%d，UDP %s:%d",
                 self.tcp_host, self.tcp_port, self.udp_host, self.udp_port)

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    # ------------------------------------------------------------------ 停止
    def _close_listen_sockets(self) -> None:
        for sock in (self._tcp_sock, self._udp_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._tcp_sock = None
        self._udp_sock = None

    def stop(self) -> None:
        """停止网关：先停服务线程/连接，再停 CAN 诊断仪与总线。"""
        self._running = False
        # 关闭所有 TCP 客户端连接 → 阻塞在 recv 的处理线程会立刻返回
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for conn in clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self._close_listen_sockets()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        if self._tester is not None:
            self._tester.stop()
            self._tester = None
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
        log.info("DoIP 网关已停止")

    # ------------------------------------------------------------------ TCP
    def _tcp_accept_loop(self) -> None:
        while self._running and self._tcp_sock is not None:
            try:
                conn, peer = self._tcp_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(None)
            with self._clients_lock:
                self._clients.add(conn)
            log.info("Tester 已连接：%s", peer)
            self._spawn(
                lambda c=conn, p=peer: self._client_loop(c, p),
                f"client-{peer[1]}",
            )

    def _client_loop(self, conn: socket.socket, peer: tuple) -> None:
        """一个 Tester 连接的报文循环：读帧 → 校验 → 分发 → 应答。"""
        state = _ClientState(peer=peer)
        try:
            while self._running:
                header = _recv_exact(conn, HEADER_LEN, None)
                if header is None:
                    break       # Tester 正常关闭连接
                version, inverse, payload_type, length = struct.unpack(">BBHI", header)

                # ---- 头格式校验 ----
                if inverse != (version ^ 0xFF):
                    log.warning("非法 DoIP 头：inverse=%#04x 与 version=%#04x 不互补", inverse, version)
                    self._send_generic_nack(conn, 0x00)
                    break       # 头都错了，字节流不可信，按 ISO 关闭连接
                if version != PROTOCOL_VERSION:
                    log.warning("不支持的 DoIP 协议版本 %#04x（本仿真只支持 0x02）", version)
                    self._send_generic_nack(conn, 0x00)
                    break
                if length > MAX_PAYLOAD_LENGTH:
                    log.warning("DoIP 报文过大：payload_length=%d > %d", length, MAX_PAYLOAD_LENGTH)
                    self._send_generic_nack(conn, 0x02)
                    break

                # ---- 读 payload（声明长度 >> 实际长度 → 超时判 0x04） ----
                payload = b""
                if length:
                    try:
                        payload = _recv_exact(conn, length, PAYLOAD_IDLE_TIMEOUT)
                    except _PayloadTimeout as exc:
                        log.warning("payload 长度与实际不符：%s", exc)
                        self._send_generic_nack(conn, 0x04)
                        break
                    if payload is None:
                        break
                if length != len(payload):
                    # 理论上走不到（_recv_exact 严格按 length 读），兜底保留
                    self._send_generic_nack(conn, 0x04)
                    break

                close = self._dispatch(conn, state, payload_type, payload)
                if close:
                    break
        except OSError as exc:
            log.debug("Tester 连接异常结束 %s：%s", peer, exc)
        finally:
            with self._clients_lock:
                self._clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass
            log.info("Tester 已断开：%s（路由激活状态已随连接清除）", peer)

    def _dispatch(self, conn: socket.socket, state: _ClientState,
                  payload_type: int, payload: bytes) -> bool:
        """按 payload type 分发。返回 True 表示处理完要关闭连接。"""
        if payload_type in (PT_VEHICLE_ID_REQUEST, PT_VEHICLE_ID_REQUEST_EID,
                            PT_VEHICLE_ID_REQUEST_VIN):
            # ISO 规定车辆识别走 UDP；教学版在 TCP 上也接受（方便单独验证）
            if not self._vehicle_id_request_length_ok(payload_type, payload):
                self._send_generic_nack(conn, 0x04)
                return False
            self._send_frame(conn, PT_VEHICLE_ANNOUNCEMENT,
                             self._vehicle_identification_payload())
            log.info("TCP 车辆识别请求 → 已回 0x0004（VIN/逻辑地址/EID/GID）")
            return False

        if payload_type == PT_ROUTING_ACTIVATION_REQUEST:
            return self._handle_routing_activation(conn, state, payload)

        if payload_type == PT_ALIVE_CHECK_REQUEST:
            if payload:
                self._send_generic_nack(conn, 0x04)
                return False
            self._send_frame(conn, PT_ALIVE_CHECK_RESPONSE,
                             DOIP_ENTITY_ADDRESS.to_bytes(2, "big"))
            log.info("alive check → 已回 0x0008（实体地址 %#06x）", DOIP_ENTITY_ADDRESS)
            return False

        if payload_type == PT_DIAG_MESSAGE:
            return self._handle_diagnostic(conn, state, payload)

        if payload_type in (PT_GENERIC_NACK, PT_VEHICLE_ANNOUNCEMENT,
                            PT_ROUTING_ACTIVATION_RESPONSE, PT_ALIVE_CHECK_RESPONSE,
                            PT_DIAG_ACK, PT_DIAG_NACK):
            # 这些是"实体 → Tester"方向的报文，Tester 发来属于不支持
            log.warning("Tester 发来了实体方向的 payload type %#06x → NACK 0x01", payload_type)
            self._send_generic_nack(conn, 0x01)
            return False

        log.warning("未知 payload type %#06x → generic NACK 0x01（连接保持）", payload_type)
        self._send_generic_nack(conn, 0x01)
        return False

    # ------------------------------------------------------------------ 车辆识别
    @staticmethod
    def _vehicle_id_request_length_ok(payload_type: int, payload: bytes) -> bool:
        expected = {
            PT_VEHICLE_ID_REQUEST: 0,
            PT_VEHICLE_ID_REQUEST_EID: 6,
            PT_VEHICLE_ID_REQUEST_VIN: 17,
        }[payload_type]
        return len(payload) == expected

    def _vehicle_identification_payload(self) -> bytes:
        """0x0004 响应 payload：VIN(17)+逻辑地址(2)+EID(6)+GID(6)+further action(1)。"""
        return (self.vin
                + DOIP_ENTITY_ADDRESS.to_bytes(2, "big")
                + VEHICLE_EID
                + VEHICLE_GID
                + bytes([FURTHER_ACTION_NONE]))

    def _udp_loop(self) -> None:
        while self._running and self._udp_sock is not None:
            try:
                data, addr = self._udp_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_udp_datagram(data, addr)
            except Exception:      # noqa: BLE001 — UDP 线程不能因单包异常退出
                log.exception("处理 UDP 车辆识别请求异常")

    def _handle_udp_datagram(self, data: bytes, addr: tuple) -> None:
        if len(data) < HEADER_LEN:
            log.warning("UDP 数据不足 %d 字节，忽略：%s", HEADER_LEN, data.hex())
            return
        version, inverse, payload_type, length = struct.unpack(">BBHI", data[:HEADER_LEN])
        payload = data[HEADER_LEN:]
        if inverse != (version ^ 0xFF):
            self._send_udp_frame(addr, PT_GENERIC_NACK, b"\x00")
            return
        if version != PROTOCOL_VERSION:
            self._send_udp_frame(addr, PT_GENERIC_NACK, b"\x00")
            return
        if length != len(payload):
            log.warning("UDP 车辆识别 payload 长度不符：声明 %d 实际 %d", length, len(payload))
            self._send_udp_frame(addr, PT_GENERIC_NACK, b"\x04")
            return
        if payload_type in (PT_VEHICLE_ID_REQUEST, PT_VEHICLE_ID_REQUEST_EID,
                            PT_VEHICLE_ID_REQUEST_VIN):
            if not self._vehicle_id_request_length_ok(payload_type, payload):
                self._send_udp_frame(addr, PT_GENERIC_NACK, b"\x04")
                return
            self._send_udp_frame(addr, PT_VEHICLE_ANNOUNCEMENT,
                                 self._vehicle_identification_payload())
            log.info("UDP 车辆识别请求（%s，type=%#06x）→ 已回 VIN=%s 逻辑地址=%#06x",
                     addr, payload_type, self.vin.decode("ascii"), DOIP_ENTITY_ADDRESS)
            return
        log.warning("UDP 收到不支持的 payload type %#06x → NACK 0x01", payload_type)
        self._send_udp_frame(addr, PT_GENERIC_NACK, b"\x01")

    def _send_udp_frame(self, addr: tuple, payload_type: int, payload: bytes) -> None:
        if self._udp_sock is None:
            return
        try:
            self._udp_sock.sendto(_build_frame(payload_type, payload), addr)
        except OSError as exc:
            log.warning("UDP 应答发送失败（%s）：%s", addr, exc)

    # ------------------------------------------------------------------ 路由激活
    def _handle_routing_activation(self, conn: socket.socket, state: _ClientState,
                                   payload: bytes) -> bool:
        if len(payload) != 7:
            log.warning("路由激活请求长度应为 7，实际 %d → NACK 0x04", len(payload))
            self._send_generic_nack(conn, 0x04)
            return False

        tester_address = int.from_bytes(payload[0:2], "big")
        activation_type = payload[2]
        log.info("路由激活请求：tester=%#06x activation_type=%#04x(%s)",
                 tester_address, activation_type,
                 ACTIVATION_TYPE_NAMES.get(activation_type, "unknown"))

        code = 0x10
        if activation_type != ACTIVATION_TYPE_DEFAULT:
            code = 0x06
        elif not TESTER_ADDRESS_MIN <= tester_address <= TESTER_ADDRESS_MAX:
            code = 0x00
        elif state.activated and state.tester_address != tester_address:
            # 同一个 socket 不允许中途换 tester 地址（ISO 的地址冲突规则）
            code = 0x02

        if code == 0x10:
            state.activated = True
            state.tester_address = tester_address
        log.info("路由激活响应：code=%#04x(%s)，tester=%#06x，entity=%#06x",
                 code, ROUTING_ACTIVATION_NAMES.get(code, "unknown"),
                 tester_address, DOIP_ENTITY_ADDRESS)
        # 响应 payload：tester(2)+entity(2)+code(1)+reserved(4)+OEM(4)=13B
        self._send_frame(
            conn, PT_ROUTING_ACTIVATION_RESPONSE,
            tester_address.to_bytes(2, "big")
            + DOIP_ENTITY_ADDRESS.to_bytes(2, "big")
            + bytes([code])
            + b"\x00" * 4          # reserved，必须为 0
            + b"\x00" * 4,         # OEM 特定字段（教学版填 0）
        )
        return False

    # ------------------------------------------------------------------ 诊断透传
    def _handle_diagnostic(self, conn: socket.socket, state: _ClientState,
                           payload: bytes) -> bool:
        if len(payload) < 4:
            log.warning("诊断报文长度 < 4（缺源/目标地址）→ NACK 0x04")
            self._send_generic_nack(conn, 0x04)
            return False
        source = int.from_bytes(payload[0:2], "big")
        target = int.from_bytes(payload[2:4], "big")
        uds = payload[4:]
        if not uds:
            log.warning("诊断报文 UDS 数据为空 → NACK 0x04")
            self._send_generic_nack(conn, 0x04)
            return False

        log.info("DoIP 诊断报文：SA=%#06x TA=%#06x UDS=%s", source, target, uds.hex())

        # ① 路由激活检查：未激活 → 0x8003 NACK code=0x06（target unreachable）
        if not state.activated:
            log.warning("路由未激活就收到诊断报文 → 诊断 NACK 0x06（target unreachable）")
            self._send_diag_nack(conn, source, target, 0x06)
            return False
        # ② 源地址必须是本连接激活过的 tester
        if source != state.tester_address:
            log.warning("诊断报文源地址 %#06x ≠ 已激活 tester %#06x → NACK 0x02",
                        source, state.tester_address)
            self._send_diag_nack(conn, source, target, 0x02)
            return False
        # ③ 目标地址必须是网关认识的 ECU
        if target != ECU_LOGICAL_ADDRESS:
            log.warning("诊断报文目标地址 %#06x 未配置 → NACK 0x03", target)
            self._send_diag_nack(conn, source, target, 0x03)
            return False

        # ④ 先回肯定确认（ISO 13400：DoIP 实体收到诊断报文即回 0x8002 ACK）
        self._send_frame(
            conn, PT_DIAG_ACK,
            source.to_bytes(2, "big") + target.to_bytes(2, "big") + b"\x00",
        )

        # ⑤ 经虚拟 CAN 把 UDS 转给 ECU，等响应
        try:
            raw_response = self._forward_uds_to_ecu(uds)
        except TimeoutError as exc:
            log.warning("ECU 无响应（%s）→ 诊断 NACK 0x06（target unreachable）", exc)
            self._send_diag_nack(conn, source, target, 0x06)
            return False
        except Exception:      # noqa: BLE001
            log.exception("UDS 转发异常 → 诊断 NACK 0x06")
            self._send_diag_nack(conn, source, target, 0x06)
            return False

        # ⑥ 把 UDS 响应包成 0x8001 回给 Tester：SA=ECU，TA=Tester（响应方向相反）
        response_payload = (target.to_bytes(2, "big")        # SA = ECU 逻辑地址
                            + source.to_bytes(2, "big")      # TA = Tester 地址
                            + raw_response)
        self._send_frame(conn, PT_DIAG_MESSAGE, response_payload)
        log.info("UDS 响应已回 Tester：SA=%#06x TA=%#06x UDS=%s",
                 target, source, raw_response.hex())
        return False

    def _forward_uds_to_ecu(self, uds: bytes) -> bytes:
        """把 UDS 请求转给 CAN 上的 ECU，返回完整 UDS 响应原始字节。

        复用项目 DiagnosticTester（自动处理 ISO-TP 多帧重组）。
        重试一次是为了抗 UDP 组播仿真总线的偶发丢帧 —— 教学取舍，见文件头
        简化点第 6 条。重试对 10/22/3E 这类幂等服务无副作用。
        """
        last_exc: TimeoutError | None = None
        for attempt in (1, 2):
            try:
                with self._uds_lock:      # 一次只处理一条 CAN 诊断请求
                    _positive, response = self._tester.send_request(
                        uds, timeout=UDS_FORWARD_TIMEOUT)
                raw = bytes([response.sid]) + response.payload
                if attempt > 1:
                    log.info("UDS 请求第 %d 次转发成功（首次超时后重试）", attempt)
                return raw
            except TimeoutError as exc:
                last_exc = exc
                log.warning("UDS 请求第 %d 次超时：%s（%s）",
                            attempt, uds.hex(), exc)
                time.sleep(UDS_RETRY_INTERVAL)
        raise last_exc if last_exc is not None else TimeoutError("ECU 无响应")

    # ------------------------------------------------------------------ 发送
    def _send_frame(self, conn: socket.socket, payload_type: int,
                    payload: bytes) -> None:
        conn.sendall(_build_frame(payload_type, payload))

    def _send_generic_nack(self, conn: socket.socket, code: int) -> None:
        log.warning("→ 通用头 NACK %#04x(%s)",
                    code, GENERIC_NACK_NAMES.get(code, "unknown"))
        try:
            self._send_frame(conn, PT_GENERIC_NACK, bytes([code]))
        except OSError:
            pass

    def _send_diag_nack(self, conn: socket.socket, source: int, target: int,
                        code: int) -> None:
        log.warning("→ 诊断报文 NACK %#04x(%s)，SA=%#06x TA=%#06x",
                    code, DIAG_NACK_NAMES.get(code, "unknown"), source, target)
        try:
            self._send_frame(conn, PT_DIAG_NACK,
                             source.to_bytes(2, "big")
                             + target.to_bytes(2, "big")
                             + bytes([code]))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DoIP 诊断网关仿真（ISO 13400 教学版）：TCP/UDP 13400，"
                    "诊断报文经虚拟 CAN 转发给 ecu_sim.py")
    parser.add_argument("--tcp-host", default=os.environ.get("DOIP_HOST", "127.0.0.1"),
                        help="TCP 监听地址（默认 127.0.0.1）")
    parser.add_argument("--tcp-port", type=int,
                        default=_env_int("DOIP_TCP_PORT", DEFAULT_DOIP_PORT),
                        help="DoIP TCP 端口（默认 13400，env DOIP_TCP_PORT）")
    parser.add_argument("--udp-host", default=os.environ.get("DOIP_HOST", "127.0.0.1"),
                        help="UDP 监听地址（默认 127.0.0.1）")
    parser.add_argument("--udp-port", type=int,
                        default=_env_int("DOIP_UDP_PORT", DEFAULT_DOIP_PORT),
                        help="车辆识别 UDP 端口（默认 13400，env DOIP_UDP_PORT）")
    parser.add_argument("--bus-channel", default=DEFAULT_CHANNEL,
                        help=f"虚拟 CAN 组播地址（默认 {DEFAULT_CHANNEL}）")
    parser.add_argument("--bus-port", type=int, default=DEFAULT_PORT,
                        help=f"虚拟 CAN 组播端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--vin", default=VEHICLE_VIN,
                        help="车辆识别响应里的 VIN（17 字节 ASCII）")
    parser.add_argument("--log-level", default="INFO",
                        help="日志级别 DEBUG/INFO/WARNING（默认 INFO）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [doip_gateway] %(message)s",
        stream=sys.stderr,
    )
    gateway = DoipGateway(
        tcp_host=args.tcp_host,
        tcp_port=args.tcp_port,
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        bus_channel=args.bus_channel,
        bus_port=args.bus_port,
        vin=args.vin,
    )
    try:
        gateway.start()
    except OSError as exc:
        print(f"[DOIP-GATEWAY] 启动失败：{exc}", flush=True)
        return 2
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[DOIP-GATEWAY] 收到 Ctrl+C，正在退出...", flush=True)
    finally:
        gateway.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
