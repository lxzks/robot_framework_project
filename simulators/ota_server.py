"""
ota_server.py — OTA 云端 mock（TCP 127.0.0.1:8080，JSON 行协议）
====================================================================

纯软件仿真里没有真的 OTA 云端，本模块用**独立进程**模拟它的核心职责：

    云端（本文件）                          OTA Agent / 测试库（OtaLibrary）
    ────────────────────────────────────────────────────────────────────
    固件包元数据 manifest  ──── GET ────►  版本/大小/分块/CRC32/兼容性
    固件镜像分块            ──── RANGE ──►  按 offset/length 下载（支持断点续传）
    坏包注入开关            ──── INJECT ─►  CRC 错 / 大小不符 / 版本不兼容
    下载统计               ──── STATS ───►  次数/字节数/当前注入状态

协议：一行一个 JSON（请求一行、响应一行），字段全部 utf-8：

    {"cmd": "manifest"}                                 → {"ok": true, "manifest": {...}}
    {"cmd": "download", "offset": 0, "length": 64}      → {"ok": true, "offset": 0, "data": "hex"}
    {"cmd": "inject", "kind": "crc|size|version"}       → {"ok": true, "injection": "crc"}
    {"cmd": "reset"}                                    → {"ok": true, "injection": "none"}
    {"cmd": "stats"}                                    → {"ok": true, "stats": {...}}
    {"cmd": "quit"}                                     → {"ok": true}

运行：
    python simulators/ota_server.py                 # 默认 8080
    python simulators/ota_server.py --port 18080    # 或 env OTA_HTTP_PORT=18080

教学说明（真实 OTA 云端还应该有）：
    - HTTPS + 双向证书（车端证书/TLS pinning）
    - 固件签名（RSA/ECDSA）+ 安全启动链（Secure Boot）
    - 灰度发布、分车型/渠道/批次、细粒度回滚
    - 断点续传（HTTP Range）与重试退避
本 mock 只保留"测试链路需要观察到的行为"：manifest/分块/坏包/统计。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import zlib

log = logging.getLogger("ota_server")

# ---- 固件包参数（教学固定值，与 libraries/OtaLibrary.py、tests/robot/17 对齐）----
IMAGE_SIZE = 512            # 固件镜像大小（字节）
BLOCK_SIZE = 64             # 推荐分块大小（一次 0x36 传输的字节数）
TARGET_VERSION = "v1.1.0"   # 本次 OTA 目标版本（新）
PREVIOUS_VERSION = "v1.0.0"  # 车端当前版本（旧）
BOOTLOADER_VERSION = "BL-1.0.0"  # 车端 Bootloader 版本（manifest 里声明最低要求）
TARGET_ECU = "POWERTRAIN"
# "size" 注入时云端只返回这么多字节（但 manifest 仍声明 IMAGE_SIZE）→ 下载不完整
INJECT_SIZE_TRUNCATE = 400

# 注入开关合法值
INJECTIONS = ("none", "crc", "size", "version")


def build_image(seed: int, size: int = IMAGE_SIZE) -> bytes:
    """确定性生成"固件镜像"（同一 seed 每次生成完全相同 → 测试可复现）。"""
    return bytes((i * 7 + seed) & 0xFF for i in range(size))


class FirmwareStore:
    """云端固件仓库：目标镜像 + 旧镜像 + 坏包注入开关 + 下载统计。"""

    def __init__(self) -> None:
        self.target_image = build_image(0x5A)      # 待升级的 v1.1.0 镜像
        self.previous_image = build_image(0x33)    # 车端已有的 v1.0.0 镜像
        self.injection = "none"
        self.download_requests = 0
        self.bytes_served = 0

    # ------------------------------------------------------------------
    def manifest(self) -> dict:
        """固件包元数据（正常包 / 各种坏包的差异都在这里体现）。"""
        crc = zlib.crc32(self.target_image) & 0xFFFFFFFF
        version = TARGET_VERSION
        compatible = True
        min_bootloader = BOOTLOADER_VERSION
        if self.injection == "crc":
            # CRC 错：数据本身正常，但 manifest 里的校验值对不上
            # → OTA Agent 下载后校验失败，应拒绝刷写
            crc = crc ^ 0x0000FFFF
        elif self.injection == "version":
            # 版本不兼容：例如新包要求更高版本的 Bootloader / 车型不匹配
            version = "v9.9.9"
            compatible = False
            min_bootloader = "BL-9.9.9"
        return {
            "package_id": f"PKG-{version}",
            "version": version,
            "previous_version": PREVIOUS_VERSION,
            "size": IMAGE_SIZE,
            "block_size": BLOCK_SIZE,
            "crc32": crc,
            "target_ecu": TARGET_ECU,
            "compatible": compatible,
            "min_bootloader": min_bootloader,
            "rollback_supported": True,
        }

    def download(self, offset: int, length: int) -> bytes:
        """按 offset/length 返回镜像分块（支持断点续传/重试）。"""
        self.download_requests += 1
        if offset < 0:
            raise ValueError("offset 不能为负")
        # size 注入：manifest 说 512，但云端只给 400 → 下载不完整（大小不符）
        available = (len(self.target_image)
                     if self.injection != "size"
                     else min(INJECT_SIZE_TRUNCATE, len(self.target_image)))
        data = self.target_image[offset:min(offset + length, available)]
        self.bytes_served += len(data)
        return data

    def stats(self) -> dict:
        return {
            "download_requests": self.download_requests,
            "bytes_served": self.bytes_served,
            "injection": self.injection,
        }


class OtaServer:
    """极简 TCP 服务端：一行 JSON 请求 → 一行 JSON 响应。"""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.firmware = FirmwareStore()
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    def _handle(self, request: dict):
        """处理单条请求，返回响应 dict。"""
        cmd = str(request.get("cmd", "")).lower()
        if cmd == "manifest":
            return {"ok": True, "manifest": self.firmware.manifest()}
        if cmd == "download":
            offset = int(request.get("offset", 0))
            length = int(request.get("length", BLOCK_SIZE))
            data = self.firmware.download(offset, length)
            return {"ok": True, "offset": offset, "data": data.hex()}
        if cmd == "inject":
            kind = str(request.get("kind", "")).lower()
            if kind not in INJECTIONS:
                return {"ok": False,
                        "error": f"未知注入类型 {kind!r}，可选: {INJECTIONS}"}
            self.firmware.injection = kind
            log.warning("坏包注入: %s", kind)
            return {"ok": True, "injection": kind}
        if cmd == "reset":
            self.firmware.injection = "none"
            log.info("已恢复正常固件包")
            return {"ok": True, "injection": "none"}
        if cmd == "stats":
            return {"ok": True, "stats": self.firmware.stats()}
        if cmd == "ping":
            return {"ok": True, "pong": True}
        if cmd == "quit":
            return {"ok": True, "goodbye": True}
        return {"ok": False, "error": f"未知命令 {cmd!r}"}

    # ------------------------------------------------------------------
    def serve_forever(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        print(f"[OTA-SERVER] 监听 {self.host}:{self.port}")
        print(f"[OTA-SERVER] 目标版本 {TARGET_VERSION}（{IMAGE_SIZE}B，"
              f"block={BLOCK_SIZE}B，CRC32 由 manifest 提供）")
        print("[OTA-SERVER] 命令: manifest / download / inject / reset / stats / quit")
        sys.stdout.flush()
        try:
            while True:
                conn, addr = self._sock.accept()
                log.info("客户端连接: %s", addr)
                try:
                    self._serve_client(conn)
                except (ConnectionError, OSError) as exc:
                    log.warning("客户端连接异常断开: %s", exc)
                finally:
                    conn.close()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
        finally:
            self._sock.close()
            print("[OTA-SERVER] 已停止")
            sys.stdout.flush()

    def _serve_client(self, conn: socket.socket) -> None:
        reader = conn.makefile("rb")
        try:
            for raw_line in reader:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                # 必须先初始化 request：坏 JSON 时 json.loads 抛异常，若不在
                # 这里先赋值，后面的 request.get(...) 会抛 UnboundLocalError；
                # 非对象 JSON（数组/字符串）也必须归一成 {}，否则 .get() 抛
                # AttributeError —— 任一条坏请求都能把整个 OTA 云端进程打死
                # （真实缺陷：2026-10-28 交叉审查发现并修复）。
                request: dict = {}
                try:
                    parsed = json.loads(line)
                    if not isinstance(parsed, dict):
                        raise ValueError("request 必须是 JSON 对象")
                    request = parsed
                except (json.JSONDecodeError, ValueError) as exc:
                    payload = {"ok": False, "error": f"JSON 解析失败: {exc}"}
                else:
                    try:
                        payload = self._handle(request)
                    except Exception as exc:  # noqa: BLE001 —— 云端不能因单条坏请求崩溃
                        log.exception("处理请求异常: %s", exc)
                        payload = {"ok": False, "error": f"服务端异常: {exc}"}
                conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n")
                             .encode("utf-8"))
                if str(request.get("cmd", "")).lower() == "quit":
                    return
        finally:
            reader.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OTA 云端 mock（教学版）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("OTA_HTTP_PORT", "8080")),
                        help="TCP 端口（默认 8080，可用 env OTA_HTTP_PORT 覆盖）")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(name)s %(message)s",
                        stream=sys.stdout)
    try:
        OtaServer(args.host, args.port).serve_forever()
    except OSError as exc:
        print(f"[OTA-SERVER][ERROR] 绑定 {args.host}:{args.port} 失败: {exc}")
        print("[OTA-SERVER][ERROR] 端口可能被占用，可用 --port 或 OTA_HTTP_PORT 换端口")
        sys.exit(1)


if __name__ == "__main__":
    main()
