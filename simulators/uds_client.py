"""
uds_client.py — UDS 诊断仪（Tester）命令行客户端
===================================================

模拟真实诊断仪（CANoe/UDS 控制台、4S 店的诊断电脑）：
向 ECU 的 0x7E0 发请求，收 0x7E8 响应，自动处理 ISO-TP 多帧重组。

架构说明（重要，也是工程常识）：
    总线上"收报文"只能有一个消费者。如果两个线程同时 recv 同一个 socket，
    报文会被随机分走，等 FC 的线程可能永远等不到。
    所以本客户端采用「单线程收包 + 收件箱队列」：
        _rx_loop（唯一 recv 者）→ 每帧都放进 _inbox
                              → 顺带完成 ISO-TP 多帧重组（重组结果进 _pending）
        主线程的等待全部从 _inbox / _pending 里取，不再直接碰 socket

用法：
    # 终端 1：先启动 ECU 模拟器
    python ecu_sim.py

    # 终端 2：启动诊断仪
    python uds_client.py
    > 10 03            # 进入扩展会话
    > 22 f190          # 读 VIN（17字节，多帧）
    > 19 02 08         # 读所有 confirmed DTC
    > 27 01            # 请求种子
    > 27 02 <密钥>      # 发送密钥（密钥=种子 XOR 5A5A5A5A，可用 unlock 命令自动算）
    > quit

支持的快捷命令（不用记 hex）：
    session default | extended      = 10 01 / 10 03
    read_did F190                   = 22 F1 90
    write_did F1A0 3132             = 2E F1 A0 31 32（需先 unlock）
    unlock                           = 自动完成 27 01 + 27 02 种子密钥握手
    read_dtc [mask]                 = 19 01 或 19 02 <mask>
    clear_dtc                        = 14 FF FF FF
    tester_present                   = 3E 00
    routine 0200                     = 31 01 02 00（自检）
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bus import VirtualCanBus                     # noqa: E402
from uds_lib import (                             # noqa: E402
    SID_DIAGNOSTIC_SESSION_CONTROL, SID_SECURITY_ACCESS,
    IsoTpReceiver, IsoTpSender, parse_response,
)

log = logging.getLogger("uds_client")

ID_UDS_REQUEST = 0x7E0
ID_UDS_RESPONSE = 0x7E8
SECURITY_XOR_KEY = 0x5A5A5A5A


class DiagnosticTester:
    def __init__(self, bus: VirtualCanBus) -> None:
        self.bus = bus
        self._tp = IsoTpReceiver()
        self._pending: list[bytes] = []   # 重组完成的完整 UDS 响应
        self._inbox: list = []            # 所有原始帧（等 FC 用）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # 唯一收包线程：所有帧进 inbox；响应侧的 FF 自动重组
    # ------------------------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.bus.recv_frame(timeout=0.1)
            except (OSError, ValueError):
                break
            if msg is None or msg.arbitration_id != ID_UDS_RESPONSE or not msg.data:
                continue
            pci = msg.data[0]
            data = bytes(msg.data)
            with self._lock:
                self._inbox.append(msg)
            if pci & 0xF0 == 0x00:        # 单帧响应
                with self._lock:
                    self._pending.append(data[1:])
            elif pci & 0xF0 == 0x10:      # 首帧 FF：Tester 侧回流控帧并启动重组
                total = ((pci & 0x0F) << 8) | data[1]
                self._tp = IsoTpReceiver()
                self._tp.feed(data)
                # FC 是 Tester→ECU 方向，发到请求 ID 0x7E0
                self.bus.send_frame(ID_UDS_REQUEST, bytes([0x30, 0x08, 0x00]))
                log.info("收到多帧响应 FF，总长 %d，已回 FC", total)
            elif pci & 0xF0 == 0x20:      # 连续帧 CF
                done, full = self._tp.feed(data)
                if done and full:
                    with self._lock:
                        self._pending.append(full)

    # ------------------------------------------------------------------
    # 从收件箱/待处理队列取数据（唯一合法的等待方式）
    # ------------------------------------------------------------------
    def _wait_for(self, predicate, timeout: float = 3.0, want_frame: bool = False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                source = self._inbox if want_frame else self._pending
                for i, item in enumerate(source):
                    if predicate(item):
                        source.pop(i)
                        return item
            time.sleep(0.02)
        raise TimeoutError(f"{timeout}s 内没等到期望的数据")

    # ------------------------------------------------------------------
    def send_request(self, request: bytes, timeout: float = 3.0):
        """发一条 UDS 请求，等待响应。返回 (positive, UdsResponse)。"""
        with self._lock:
            self._pending.clear()
            self._inbox.clear()
        if len(request) <= 7:
            self.bus.send_frame(ID_UDS_REQUEST, bytes([len(request)]) + request)
        else:
            frames = IsoTpSender.split_frames(request)
            self.bus.send_frame(ID_UDS_REQUEST, frames[0])
            # 等 ECU 的流控帧（FC 在响应 ID 上），并**按 FC 声明的 BS** 分块发送。
            # 关键：BS 必须从 FC 里解析（不能硬编码）—— BS=0 表示"发完为止，
            # 不用再等我"；BS=N 表示每发 N 个 CF 后要等一个新的 FC。
            fc = self._wait_for(
                lambda m: len(m.data) >= 1 and (m.data[0] & 0xF0) == 0x30,
                timeout=timeout, want_frame=True)
            bs = fc.data[1] if len(fc.data) >= 2 else 0
            sent = 0
            for f in frames[1:]:
                self.bus.send_frame(ID_UDS_REQUEST, f)
                sent += 1
                if bs and sent % bs == 0 and sent < len(frames) - 1:
                    self._wait_for(
                        lambda m: (m.data[0] & 0xF0) == 0x30,
                        timeout=timeout, want_frame=True)
        # 等完整响应
        raw = self._wait_for(lambda _d: True, timeout=timeout)
        resp = parse_response(raw)
        return resp.positive, resp

    # ------------------------------------------------------------------
    def unlock(self) -> None:
        """自动完成 27 01 + 27 02 握手（教学演示密钥算法）"""
        _, resp = self.send_request(bytes([SID_SECURITY_ACCESS, 0x01]))
        seed = resp.payload[1:5]
        key = (int.from_bytes(seed, "big") ^ SECURITY_XOR_KEY).to_bytes(4, "big")
        print(f"  种子={seed.hex()} → 密钥={key.hex()} (seed XOR 5A5A5A5A)")
        ok, resp = self.send_request(
            bytes([SID_SECURITY_ACCESS, 0x02]) + key)
        print(f"  安全访问{'成功' if ok else '失败: ' + resp.describe()}")


HELP = """命令:
  裸 hex          直接发 UDS 请求, 如 10 03 / 22 f190 / 19 02 08 / 14 ff ff ff
  session <def|ext>   会话切换
  read_did <DID>      读 DID
  write_did <DID> <hex>  写 DID(需先 unlock)
  unlock               自动种子密钥握手
  read_dtc [mask]      读 DTC(默认 19 01 数量)
  clear_dtc            清除全部 DTC
  tester_present       3E 00
  routine <RID>        启动例程, 如 routine 0200
  quit                 退出
"""


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    print(HELP)
    # echo_own=False：诊断仪不需要回读自己发的请求帧
    with VirtualCanBus("TESTER", echo_own=False) as bus:
        tester = DiagnosticTester(bus)
        tester.start()
        while True:
            try:
                line = input("uds> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line == "quit":
                break
            try:
                if line.startswith("session "):
                    sub = 1 if line.split()[1].startswith("def") else 3
                    ok, resp = tester.send_request(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, sub]))
                    print(f"  → {resp.describe()}")
                elif line.startswith("read_did"):
                    did = line.split()[1].upper()
                    req = bytes.fromhex(f"22{did}")
                    ok, resp = tester.send_request(req)
                    print(f"  → {resp.describe()}")
                    if ok and len(resp.payload) > 2:
                        val = resp.payload[2:]
                        print(f"  数据({len(val)}B): {val.hex()}  "
                              f"ASCII='{val.decode(errors='replace')}'")
                elif line.startswith("write_did"):
                    parts = line.split()
                    did, val = parts[1].upper(), parts[2]
                    req = bytes.fromhex(f"2E{did}{val}")
                    ok, resp = tester.send_request(req)
                    print(f"  → {resp.describe()}")
                elif line == "unlock":
                    tester.unlock()
                elif line.startswith("read_dtc"):
                    parts = line.split()
                    if len(parts) == 1:
                        ok, resp = tester.send_request(bytes([0x19, 0x01]))
                    else:
                        ok, resp = tester.send_request(bytes([0x19, 0x02, int(parts[1], 16)]))
                    print(f"  → {resp.describe()}")
                    if ok and resp.sid == 0x59:
                        body = resp.payload[1:]
                        if body and body[0] == 0x01:
                            print(f"  DTC 数量: {body[2] if len(body) > 2 else 0}")
                        else:
                            from uds_lib import dtc_to_string
                            i = 1
                            while i + 4 <= len(body):
                                print(f"  DTC {dtc_to_string(body[i], body[i+1], body[i+2])} "
                                      f"status={body[i+3]:#04x}")
                                i += 4
                elif line == "clear_dtc":
                    ok, resp = tester.send_request(bytes([0x14, 0xFF, 0xFF, 0xFF]))
                    print(f"  → {resp.describe()}")
                elif line == "tester_present":
                    ok, resp = tester.send_request(bytes([0x3E, 0x00]))
                    print(f"  → {resp.describe()}")
                elif line.startswith("routine"):
                    rid = line.split()[1].upper()
                    ok, resp = tester.send_request(bytes.fromhex(f"3101{rid}"))
                    print(f"  → {resp.describe()}")
                else:
                    req = bytes.fromhex(line.replace(" ", ""))
                    ok, resp = tester.send_request(req)
                    print(f"  → {resp.describe()}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [ERR] {exc}")
        tester.stop()


if __name__ == "__main__":
    main()
