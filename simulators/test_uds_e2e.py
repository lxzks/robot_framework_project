"""
test_uds_e2e.py — UDS 端到端联调（开发时自测用）
启动 ecu_sim 子进程 → 完整走一遍诊断流程 → 断言结果
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
# 用当前解释器（脚本由目标 venv 的 python 启动，天然正确、可移植）
PY = sys.executable

sys.path.insert(0, str(HERE))
from bus import VirtualCanBus                                    # noqa: E402
from uds_client import DiagnosticTester          # noqa: E402
from uds_lib import SID_DIAGNOSTIC_SESSION_CONTROL, SID_SECURITY_ACCESS  # noqa: E402

XOR = 0x5A5A5A5A


def main() -> None:
    ecu = subprocess.Popen(
        [PY, str(HERE / "ecu_sim.py"), "--speed", "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        time.sleep(1.5)  # 等 ECU 进程起来（就绪判定用下面的轮询，不靠猜）
        with VirtualCanBus("E2E-TESTER") as bus:
            t = DiagnosticTester(bus)
            t.start()

            # ECU 就绪轮询：发 TesterPresent 直到有正响应（最多 15 秒）。
            # 固定 sleep 在高负载/慢启动时会偶发失败（真实踩过的坑）。
            ready = False
            for _ in range(30):
                ok, r = t.send_request(bytes([0x3E, 0x00]))
                if ok and r.positive:
                    ready = True
                    break
                time.sleep(0.5)
            assert ready, "ECU 未就绪（15 秒内诊断无响应）"
            print("== 0. ECU 就绪（3E 00 正响应）==")

            print("== 1. 进入扩展会话 10 03 ==")
            ok, r = t.send_request(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, 0x03]))
            assert ok and r.sid == 0x50, f"失败: {r.describe()}"
            print("   OK:", r.describe())

            print("== 2. 读 VIN (22 F1 90, 17字节→多帧) ==")
            ok, r = t.send_request(bytes([0x22, 0xF1, 0x90]))
            assert ok, f"失败: {r.describe()}"
            vin = r.payload[2:].decode()
            print(f"   OK: VIN={vin} ({len(vin)}字节, 响应{len(r.payload)+1}字节经ISO-TP重组)")
            assert vin == "LSVAM4180NC000001"

            print("== 3. 读零件号 22 F1 87 ==")
            ok, r = t.send_request(bytes([0x22, 0xF1, 0x87]))
            assert ok
            print("   OK:", r.payload[2:].decode())

            print("== 4. 安全访问 27 01/02 ==")
            ok, r = t.send_request(bytes([SID_SECURITY_ACCESS, 0x01]))
            seed = r.payload[1:5]
            key = (int.from_bytes(seed, "big") ^ XOR).to_bytes(4, "big")
            ok, r = t.send_request(bytes([SID_SECURITY_ACCESS, 0x02]) + key)
            assert ok, f"安全访问失败: {r.describe()}"
            print(f"   OK: 种子{seed.hex()} 密钥{key.hex()}")

            print("== 5. 写 DID F1A0 (2E, 需安全解锁) ==")
            ok, r = t.send_request(bytes([0x2E, 0xF1, 0xA0]) + b"V2.0.0")
            assert ok, f"写失败: {r.describe()}"
            print("   OK:", r.describe())
            ok, r = t.send_request(bytes([0x22, 0xF1, 0xA0]))
            assert r.payload[2:] == b"V2.0.0", "回读不一致"
            print("   OK: 回读 =", r.payload[2:].decode())

            print("== 6. 未解锁时写 DID 应被拒 (NRC 33) ==")
            # 先回默认会话再进扩展（不重新解锁）——会话切换会重新锁定
            t.send_request(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, 0x01]))
            t.send_request(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, 0x03]))
            ok, r = t.send_request(bytes([0x2E, 0xF1, 0xA0]) + b"HACK")
            assert not ok and r.nrc == 0x33, f"应被拒绝: {r.describe()}"
            print("   OK: 正确拒绝 -", r.describe())

            print("== 7. 自检例程 31 01 0200 ==")
            ok, r = t.send_request(bytes([0x31, 0x01, 0x02, 0x00]))
            assert ok
            print("   OK:", r.describe())

            print("== 8. 读 DTC 数量 19 01 ==")
            ok, r = t.send_request(bytes([0x19, 0x01]))
            assert ok
            print("   OK: DTC数量 =", r.payload[2])

            print("== 9. TesterPresent 保活 3E 00 ==")
            ok, r = t.send_request(bytes([0x3E, 0x00]))
            assert ok
            print("   OK:", r.describe())

            t.stop()
        print("\n*** UDS 端到端测试全部通过 ***")
    finally:
        ecu.terminate()
        try:
            out, _ = ecu.communicate(timeout=3)
            print("--- ECU 日志(尾部) ---")
            print("\n".join(out.splitlines()[-12:]))
        except subprocess.TimeoutExpired:
            ecu.kill()


if __name__ == "__main__":
    main()