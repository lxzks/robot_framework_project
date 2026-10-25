"""
bus.py — 虚拟 CAN 总线封装
================================

教学项目 `robot_framework` 的第一块积木。

它解决一个核心问题：
    **没有真实的 CAN 硬件（CANoe/CANalyzer/Vector 盒），
    怎样在纯软件环境里跑通"多节点通过 CAN 总线通信"？**

方案：使用 python-can 的 UDP 组播（udp_multicast）接口。
多台"虚拟 ECU"（其实是多个 Python 进程）加入同一个组播组，
谁发报文，组里所有人都能收到 —— 这正是 CAN 总线"广播"语义。

类比：
    真实 CAN 总线 = 一根双绞线，所有 ECU 焊在同一根线上，一个人喊全听见
    虚拟 CAN 总线 = 一个微信群，群里发消息全都能收到（UDP 组播就是这个微信群）

注意：这不是 CAN 协议本身（没有位仲裁、没有错误帧），
它只是"应用层视角的总线通道"。位仲裁等概念见 docs/02 章，
在软件仿真层面不影响我们学习 UDS / DTC / 信号收发。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

import can

log = logging.getLogger("vehicle.bus")

# 默认组播参数：频道像"微信群号"，端口像"群号里的房间号"
DEFAULT_CHANNEL = "233.255.0.1"
DEFAULT_PORT = 30000


class VirtualCanBus:
    """把 python-can 的 udp_multicast 总线包成一个简单好用的类。

    用法（同一个组播组内的多个进程）::

        bus_a = VirtualCanBus("ECU")     # 进程 A
        bus_b = VirtualCanBus("TESTER")  # 进程 B
        bus_a.send_frame(0x100, bytes.fromhex("88130000"))
        msg = bus_b.recv_frame(timeout=2)   # B 能收到 A 发的
    """

    def __init__(
        self,
        node_name: str,
        channel: str = DEFAULT_CHANNEL,
        port: int = DEFAULT_PORT,
        interface: str = "udp_multicast",
        dedup: bool = True,
        echo_own: bool = True,
    ) -> None:
        self.node_name = node_name
        self.channel = channel
        self.port = port
        self._dedup = dedup
        self._last_seen: dict[tuple[int, bytes], float] = {}
        # echo_own：本进程发出的帧能被自己"回读"（模拟 CAN 控制器自发自收：
        # 真实控制器发帧时会同时监测总线以做仲裁/错误检测）。
        # 为什么不用 python-can 的 receive_own_messages：udp_multicast
        # 接口明确不支持（raise CanInterfaceNotImplementedError），
        # 所以在总线封装层自己维护一个"自发帧缓存"。
        self._echo_own = echo_own
        self._tx_echo: "deque[tuple[float, can.Message]]" = deque(maxlen=200)
        self._echo_ttl = 2.0     # 缓存帧的存活时间（秒），过期丢弃
        self._bus = can.Bus(
            interface=interface,
            channel=channel,
            port=port,
            receive_own_messages=False,   # 该接口不支持 True，见上
        )
        self._enlarge_rcvbuf()
        log.info("[%s] 已加入虚拟 CAN 总线 %s:%d (%s)", node_name, channel, port, interface)

    def _enlarge_rcvbuf(self, size: int = 4 * 1024 * 1024) -> None:
        """调大 UDP 接收缓冲区（默认仅 64KB）。

        大缓冲能吸收报文突发，降低高负载时的丢包概率。
        注：python-can 未公开 socket 属性，只能走 _multicast._socket
        私有路径；不同版本结构可能变，失败时仅告警不影响功能。
        """
        try:
            import socket as _socket
            sock = getattr(getattr(self._bus, "_multicast", None), "_socket", None)
            if sock is None:
                return
            old = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF)
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, size)
            new = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF)
            log.debug("[%s] SO_RCVBUF: %d → %d 字节", self.node_name, old, new)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] 调整 SO_RCVBUF 失败（不影响基本收发）: %s",
                        self.node_name, exc)

    # ------------------------------------------------------------------ 发送
    def send_frame(self, arbitration_id: int, data: bytes) -> None:
        """发送一帧标准 CAN 报文（11 位 ID，最多 8 字节数据）。"""
        if not 0 <= arbitration_id <= 0x7FF:
            raise ValueError(f"标准帧 ID 必须在 0~0x7FF 之间，收到 {arbitration_id:#x}")
        if not 1 <= len(data) <= 8:
            raise ValueError(f"CAN 数据场长度必须在 1~8 字节之间，收到 {len(data)}")
        msg = can.Message(
            arbitration_id=arbitration_id,
            data=data,
            is_extended_id=False,
        )
        self._bus.send(msg, timeout=1.0)
        if self._echo_own:
            # 记入自发帧缓存：本进程之后 recv 时能读到（模拟控制器自发自收）
            self._tx_echo.append((time.monotonic(), msg))
        log.debug("[%s] TX id=%#x data=%s", self.node_name, arbitration_id, data.hex())

    def _take_echo(self, predicate=None) -> Optional[can.Message]:
        """从自发帧缓存取一帧（FIFO，过期丢弃）。predicate 不匹配则跳过。"""
        now = time.monotonic()
        while self._tx_echo:
            ts, msg = self._tx_echo.popleft()
            if now - ts > self._echo_ttl:
                continue                     # 过期帧丢弃
            if predicate is None or predicate(msg):
                return msg
        return None

    def drain_rx(self, max_frames: int = 500, timeout: float = 0.001) -> int:
        """排空接收缓冲（丢弃 socket 里积压的帧），返回丢弃帧数。

        用途："发送 → 读回"闭环测试前清场。UDP 组播的回环会让本进程
        发出的帧也进入自己的接收缓冲（python-can 的 receive_own_messages
        在该接口不可用），这些历史副本若不清理，会在 echo 缓存为空时
        被读到，造成"读回旧值"的诡异失败。
        注意：只清 socket，不动 echo 缓存（那里是刚发的帧，要留给读回）。
        """
        dropped = 0
        while dropped < max_frames:
            if self._bus.recv(timeout=timeout) is None:
                break
            dropped += 1
        return dropped

    # ------------------------------------------------------------------ 接收
    def recv_frame(self, timeout: float = 1.0,
                   dedup_window_ms: float = 5.0) -> Optional[can.Message]:
        """阻塞接收一帧报文；超时返回 None。

        返回的是 python-can 的 Message 对象，常用字段：
            msg.arbitration_id  CAN ID（十进制整数）
            msg.data            数据（bytes）
            msg.timestamp       收到的时间戳

        **去重说明**（重要）：UDP 组播在**多网卡机器**上会从每个加入组的
        接口各回环一份报文 —— 实测本机 3 个接口（真实网卡 + 虚拟网卡），
        同一帧被收到 2 份。后果：Trace 帧数翻倍、周期均值变一半、
        抖动虚高（识别特征：周期均值 ≈ 理论值/接口数）。
        真实 CAN 控制器不会产生重复帧，这是**模拟环境特有**的问题，
        因此在接收端按 (ID, 数据) 在 5ms 窗口内去重。本工程最快报文
        周期 100ms，5ms 窗口不会误杀真实帧。
        """
        # 优先返回本进程发过的帧（自发自收回读，供"发送→读回"闭环测试）
        echo = self._take_echo()
        if echo is not None:
            return echo
        while True:
            msg = self._bus.recv(timeout=timeout)
            if msg is None:
                return None
            if self._dedup and self._is_loopback_duplicate(msg, dedup_window_ms):
                continue            # 多接口回环重复，丢弃继续取
            log.debug("[%s] RX id=%#x data=%s", self.node_name,
                      msg.arbitration_id, msg.data.hex())
            return msg

    def _is_loopback_duplicate(self, msg: can.Message,
                               window_ms: float) -> bool:
        """(ID, 数据) 相同且在窗口内重复 → 判定为多接口回环重复"""
        key = (msg.arbitration_id, bytes(msg.data))
        now = time.monotonic()
        last = self._last_seen.get(key)
        self._last_seen[key] = now
        # 防止字典无限增长（报文种类有限，但保守起见限一下）
        if len(self._last_seen) > 4096:
            self._last_seen.clear()
        return last is not None and (now - last) * 1000.0 < window_ms

    def recv_until(
        self,
        predicate,
        timeout: float = 5.0,
        poll: float = 0.05,
    ) -> can.Message:
        """循环接收，直到某一帧满足谓词 predicate(msg) 为 True，否则超时抛错。

        测试代码里最常用：比如"等 ECU 的 UDS 响应"。
        """
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            msg = self.recv_frame(timeout=min(poll, remaining))
            if msg is not None and predicate(msg):
                return msg
        raise TimeoutError(f"[{self.node_name}] {timeout}s 内没等到期望的报文")

    # ------------------------------------------------------------------ 其它
    def send_raw_hex(self, arbitration_id: int, hex_data: str) -> None:
        """方便命令行调试：send_raw_hex(0x100, '8813')"""
        self.send_frame(arbitration_id, bytes.fromhex(hex_data))

    def shutdown(self) -> None:
        self._bus.shutdown()
        log.info("[%s] 已离开虚拟 CAN 总线", self.node_name)

    def __enter__(self) -> "VirtualCanBus":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


if __name__ == "__main__":
    # 自测：发一帧 0x100，打印出来
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s")
    with VirtualCanBus("SELF-TEST") as bus:
        bus.send_frame(0x100, bytes([0x88, 0x13, 0x00, 0x80, 0x5A, 0x33, 0, 0]))
        print("已发送测试帧 0x100，data=881300805a330000")
