"""
SecurityLibrary.py — Robot Framework 信息安全 / UDS 安全访问测试库
====================================================================

把"安全访问（0x27）攻击面 + 模糊测试（fuzzing）"包装成 RF 关键字，
用来验证 ECU 的安全设计是否真的生效：

    真实信息安全测试的工作方式（本模块就是按这个流程写的）：
      ① 找缺陷（本项目：27 02 未请求种子即可用 0x5A5A5A5A 解锁）
      ② 提缺陷 → 修复 ECU
      ③ 写**漏洞回归用例**（Bypass Attempt Should Fail）把缺陷锁死
      ④ 系统性验证：种子随机性（nonce）、重放防护、爆破锁定、畸形帧处理、
         模糊测试下不崩溃且可恢复

覆盖的攻击面：
    - **绕过**：不取种子直接发密钥（历史漏洞回归）
    - **重放**：抓到的旧密钥/旧种子在新会话里重放
    - **爆破**：连续错误密钥 → 触发 NRC 0x36 锁定
    - **故障注入 / 模糊**：随机 SID + 随机 payload + 合法模板混合，验证
      ECU 对畸形/未知输入不崩溃、响应时间可控、之后仍可正常诊断
    - **越权**：未解锁/复位后写 DID 应被拒绝（配合 DiagnosticLibrary）

技术要点：
    - ROBOT_LIBRARY_SCOPE = "SUITE"：每个套件一个实例，持有自己的
      VirtualCanBus + DiagnosticTester（单收包线程 + 收件箱，见 uds_client.py）。
    - 本库关键字**默认不吞错**：断言类关键字失败即抛 AssertionError，
      负向用例用「先发再断言」或「期望错误」组合。
    - 模糊测试用固定随机种子（可传参）保证可复现；不会伪造统计数字。

用法（套件里）::

    Resource        ../../resources/common.resource
    Library         ../../libraries/SecurityLibrary.py

    Suite Setup     Start Vehicle Environment
    Suite Teardown  关闭安全测试环境
    Test Setup      重置 ECU 安全状态

    Unlock Sequence Should Succeed
    Bypass Attempt Should Fail
    ${stats}=    Fuzz UDS Requests    300
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

from robot.api.deco import keyword
from robot.api.logger import console, info

sys.path.insert(0, str(Path(__file__).parent.parent / "simulators"))

from bus import VirtualCanBus                                   # noqa: E402
from uds_client import SECURITY_XOR_KEY, DiagnosticTester       # noqa: E402


class SecurityLibrary:
    """信息安全 / UDS 安全访问 / 模糊测试关键字库"""

    ROBOT_LIBRARY_SCOPE = "SUITE"
    ROBOT_LIBRARY_VERSION = "1.0"

    #: 模糊测试的 SID 池：覆盖合法服务 + 未实现服务 + 保留/私有范围
    FUZZ_SIDS = [
        0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x28, 0x2E, 0x2F, 0x31,
        0x34, 0x36, 0x37, 0x3E, 0x85, 0x83, 0xA0, 0xBA, 0xFF, 0x00,
    ]
    #: 合法请求模板（保证模糊过程中也有正响应；也用于变异/半随机）
    LEGAL_TEMPLATES = [
        "3E 00", "10 03", "10 01", "22 F1 87", "22 F1 90", "19 01",
        "19 02 08", "27 01", "31 01 02 00", "28 00", "85 01",
    ]

    def __init__(self, node_name: str = "RF-SECURITY") -> None:
        # echo_own=False：安全测试仪不需要回读自己发的请求帧
        self._bus = VirtualCanBus(node_name, echo_own=False)
        self._tester = DiagnosticTester(self._bus)
        self._tester.start()
        self._last_response = None
        console("[SecurityLibrary] 安全测试仪已就绪（27 安全访问 + 模糊测试）")

    # ==================================================================
    # 一、基础：收发 + 断言
    # ==================================================================
    @keyword("Send Raw Request")
    def send_raw_request(self, request_hex: str) -> str:
        """发送一条裸 UDS 请求并返回响应描述（**不断言**成败，便于负向用例）。

        例：``Send Raw Request    27 02 5A5A5A5A``

        超时（ECU 无响应）会抛 TimeoutError —— 对健壮性测试来说"无响应"
        通常就是缺陷信号，交给调用方/Malformed Frame 关键字处理。
        """
        req = bytes.fromhex(request_hex.replace(" ", ""))
        ok, resp = self._tester.send_request(req)
        self._last_response = resp
        info(f"请求 {req.hex()} → {resp.describe()}")
        return resp.describe()

    @keyword("Response Should Be Positive")
    def response_should_be_positive(self) -> None:
        """断言上一条响应是正响应。"""
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if not self._last_response.positive:
            raise AssertionError(
                f"期望正响应，实际: {self._last_response.describe()}")

    @keyword("Security Access Should Fail With NRC")
    def security_access_should_fail_with_nrc(self, nrc) -> None:
        """断言上一条响应是指定 NRC 的负响应（安全访问负向用例专用）。

        nrc 支持 ``0x33`` / ``51`` 两种写法。常见值：
        0x24 requestSequenceError、0x35 invalidKey、0x36 exceedNumberOfAttempts。
        """
        nrc_int = int(str(nrc), 0)
        if self._last_response is None:
            raise AssertionError("还没有发送过任何请求")
        if self._last_response.positive:
            raise AssertionError(
                f"期望负响应 NRC={nrc_int:#04x}，实际: "
                f"{self._last_response.describe()}")
        if self._last_response.nrc != nrc_int:
            raise AssertionError(
                f"期望 NRC={nrc_int:#04x}，实际: {self._last_response.describe()}")
        info(f"NRC={nrc_int:#04x} ✓")

    @keyword("Response Should Be Negative With NRC")
    def response_should_be_negative_with_nrc(self, nrc) -> None:
        """与 DiagnosticLibrary 同名的负响应断言（同一套关键字命名习惯）。"""
        self.security_access_should_fail_with_nrc(nrc)

    # ==================================================================
    # 二、会话 / 生命周期
    # ==================================================================
    @keyword("Open Security Session")
    def open_security_session(self) -> None:
        """进入扩展会话（10 03）—— 安全访问（0x27）的前置会话。

        注意：ECU 的会话切换会**重新锁定**安全访问（security_unlocked=False、
        旧种子作废），这是安全设计常识，也是后面对比用例的依据。
        """
        try:
            self.send_raw_request("10 03")
        except TimeoutError:
            info("会话切换超时（UDP 组播无重传，疑似丢帧）→ 重试一次")
            self.send_raw_request("10 03")
        self.response_should_be_positive()

    @keyword("Reset Security State")
    def reset_security_state(self) -> None:
        """用例隔离：ECU 复位（11 01）清空会话/解锁/种子/爆破计数/锁定，
        然后重新进扩展会话，让每条用例都从"未解锁 + 未取种子"开始。
        """
        try:
            self.send_raw_request("11 01")
        except TimeoutError:
            info("复位请求无响应（忽略，继续按下一条用例处理）")
        time.sleep(0.3)          # 等 ECU 完成复位（会话/安全状态已清）
        self.open_security_session()

    @keyword("Close Security Tester")
    def close_security_tester(self) -> None:
        """关闭安全测试仪：停收包线程 + 释放虚拟总线（套件 Teardown 调用）。"""
        self._tester.stop()
        self._bus.shutdown()
        info("安全测试仪已关闭")

    # ==================================================================
    # 三、安全访问（0x27）正常流程与攻击面
    # ==================================================================
    @keyword("Request Seed")
    def request_seed(self) -> str:
        """请求安全访问种子（27 01），断言正响应，返回 4 字节种子的 hex。"""
        try:
            self.send_raw_request("27 01")
        except TimeoutError:
            info("请求种子超时（UDP 组播无重传，疑似丢帧）→ 重试一次")
            self.send_raw_request("27 01")
        self.response_should_be_positive()
        seed = self._last_response.payload[1:5]
        if len(seed) != 4:
            raise AssertionError(f"种子长度异常: {seed.hex()}")
        info(f"种子 = {seed.hex()}")
        return seed.hex()

    @keyword("Send Key")
    def send_key(self, key_hex: str) -> str:
        """发送安全访问密钥（27 02 <4字节>），返回响应描述（**不自动断言**，
        便于负向用例随后用 Security Access Should Fail With NRC 断言）。

        正响应 67 02 = 解锁成功（之后允许受保护的 0x2E/0x31 等）。
        """
        key = key_hex.replace(" ", "")
        if len(key) != 8:
            raise AssertionError(f"密钥必须是 4 字节 hex（8 位），收到 {key!r}")
        self.send_raw_request(f"27 02 {key}")
        return self._last_response.describe()

    @keyword("Unlock Sequence Should Succeed")
    def unlock_sequence_should_succeed(self) -> list:
        """正基线：27 01 取种子 → key = seed XOR 0x5A5A5A5A → 27 02 → 断言成功。

        返回 ``[seed_hex, key_hex]``（RF 里可 ``${seed}    ${key}=`` 接）。
        """
        seed = bytes.fromhex(self.request_seed())
        key_int = int.from_bytes(seed, "big") ^ SECURITY_XOR_KEY
        key = key_int.to_bytes(4, "big")
        self.send_key(key.hex())
        self.response_should_be_positive()
        info(f"解锁成功：种子 {seed.hex()} → 密钥 {key.hex()}")
        return [seed.hex(), key.hex()]

    @keyword("Seed Should Be Random")
    def seed_should_be_random(self) -> list:
        """nonce 语义：连续请求的种子不应重复（拦截"固定种子"实现）。

        返回多次请求到的种子列表（通常 2 个）。
        """
        seeds = [self.request_seed() for _ in range(3)]
        if len(set(seeds)) < 2:
            raise AssertionError(f"连续请求的种子全部相同（固定种子？）: {seeds}")
        info(f"seed(nonce) 校验通过: {seeds}")
        return seeds

    @keyword("Send Wrong Key For Seed")
    def send_wrong_key_for_seed(self, seed_hex: str) -> str:
        """对给定种子发送**必定错误**的密钥（把正确密钥翻转 1 bit）。

        用它来测"错误密钥 → NRC 0x35"和"连续错误 → 锁定"，
        结果确定、不依赖随机数。
        """
        seed = bytes.fromhex(seed_hex.replace(" ", ""))
        if len(seed) != 4:
            raise AssertionError(f"种子必须是 4 字节: {seed_hex!r}")
        correct = int.from_bytes(seed, "big") ^ SECURITY_XOR_KEY
        wrong = (correct ^ 0x00000001).to_bytes(4, "big")
        return self.send_key(wrong.hex())

    @keyword("Bypass Attempt Should Fail")
    def bypass_attempt_should_fail(self) -> None:
        """**漏洞回归**：不请求种子，直接发"默认种子算出的密钥"（0x5A5A5A5A）。

        历史缺陷：ECU 用默认 seed=0x00000000 计算期望密钥 →
        ``27 02 5A5A5A5A`` 直接解锁。修复后必须回 NRC 0x24 requestSequenceError。
        """
        self.open_security_session()      # 10 03 会清空种子（防止上一用例残留）
        self.send_key("5a5a5a5a")
        self.security_access_should_fail_with_nrc(0x24)
        info("绕过尝试被拒绝（NRC 0x24）✓ —— 漏洞回归通过")

    @keyword("Failed Attempts Should Lock Access")
    def failed_attempts_should_lock_access(self) -> None:
        """爆破防护：连续错误密钥 → 达到阈值后 NRC 0x36，且锁定期间
        连 27 01 都被拒（防止换个请求继续爆破）。

        对 UDP 丢帧的韧性：虚拟总线是 UDP 组播、没有真实 CAN 的帧重传，
        偶发丢一帧会表现为"某次错误密钥请求超时"。这里对超时容忍并继续
        尝试（最多 6 次），但**核心语义仍严格断言**：至少观察到一个
        NRC 0x35（证明计数在起作用）、最终必须出现 NRC 0x36（锁定），
        锁定期间 27 01 同样必须被拒。100% 无响应仍会失败。
        """
        self.open_security_session()
        seed = self.request_seed()
        saw_invalid_key = 0
        locked = False
        for attempt in range(1, 7):          # 最多 6 次（阈值 3 + 容忍丢帧）
            try:
                self.send_wrong_key_for_seed(seed)
            except TimeoutError:
                info(f"第 {attempt} 次错误密钥无响应（UDP 组播丢帧）→ 继续重试")
                continue
            resp = self._last_response
            if resp is not None and not resp.positive and resp.nrc == 0x35:
                saw_invalid_key += 1
                info(f"第 {attempt} 次错误密钥 → NRC 0x35 ✓")
                continue
            if resp is not None and not resp.positive and resp.nrc == 0x36:
                locked = True
                info(f"第 {attempt} 次错误密钥 → NRC 0x36（已锁定）✓")
                break
            raise AssertionError(
                f"第 {attempt} 次错误密钥期望 NRC 0x35/0x36，实际: "
                f"{resp.describe() if resp else 'None'}")
        if saw_invalid_key < 1:
            raise AssertionError("未观察到任何 NRC 0x35，错误密钥计数未生效")
        if not locked:
            # 再发一次确认锁定（容忍的丢帧可能让"阈值那一次"没收到响应）
            self.send_wrong_key_for_seed(seed)
            self.security_access_should_fail_with_nrc(0x36)
            info("补发一次错误密钥 → NRC 0x36（已锁定）✓")
        # 锁定期间重新请求种子也必须被拒
        self.send_raw_request("27 01")
        self.security_access_should_fail_with_nrc(0x36)
        info("锁定期间 27 01 也被拒绝（NRC 0x36）✓")

    @keyword("Replay Key Should Fail")
    def replay_key_should_fail(self, old_key_hex: str) -> None:
        """重放防护：重新取新种子后，用**上一次的旧密钥**发送必须失败。

        实现细节：key = seed XOR 0x5A5A5A5A 是可逆的，所以能从旧 key 反推
        旧 seed；再确认新 seed ≠ 旧 seed（避免天文概率的碰撞导致假失败），
        最后发旧 key → 期望 NRC 0x35 invalidKey。
        """
        old_key = bytes.fromhex(old_key_hex.replace(" ", ""))
        if len(old_key) != 4:
            raise AssertionError(f"旧密钥必须是 4 字节: {old_key_hex!r}")
        old_seed_int = int.from_bytes(old_key, "big") ^ SECURITY_XOR_KEY

        self.open_security_session()          # 重新锁定 + 作废旧种子
        for _ in range(5):                    # 若新种子恰好等于旧种子则再取一次
            new_seed_int = int.from_bytes(
                bytes.fromhex(self.request_seed()), "big")
            if new_seed_int != old_seed_int:
                break
        else:
            raise AssertionError("连续 5 次都拿到与旧种子相同的种子（nonce 失效？）")

        self.send_key(old_key_hex)
        self.security_access_should_fail_with_nrc(0x35)
        info("重放旧密钥被拒绝（NRC 0x35）✓")

    # ==================================================================
    # 四、健壮性 / 模糊测试（fuzzing）
    # ==================================================================
    @keyword("ECU Should Stay Alive")
    def ecu_should_stay_alive(self, retries: int = 3) -> None:
        """断言 ECU 还活着：发 TesterPresent（3E 00）直到拿到正响应。

        模糊测试之后必须立刻验证这一点 —— "没响应"可能是 ECU 崩溃/线程死掉，
        也可能是"正确地静默"（如 3E 80 抑制响应），两者要区分开。
        """
        last_err = None
        for _ in range(int(retries)):
            try:
                self.send_raw_request("3E 00")
                if self._last_response.positive:
                    info("ECU 存活 ✓（3E 00 正响应）")
                    return
            except TimeoutError as exc:
                last_err = exc
            time.sleep(0.3)
        raise AssertionError(f"ECU 无响应（可能已崩溃/诊断线程死亡）: {last_err}")

    @keyword("Fuzz UDS Requests")
    def fuzz_uds_requests(self, count=300, seed=20261028) -> dict:
        """模糊测试：发 N 条随机/半随机 UDS 请求，返回统计字典。

        策略（固定随机种子，结果可复现）：
          - 约 40% 用合法模板（保证正/负响应都有，也覆盖状态切换类请求）
          - 其余：随机 SID + 随机 payload（长度 0~6，保证单帧）
        统计：``{total, positive, negative, no_response, alive, elapsed_s}``

        判定口径：
          - ``no_response`` 不是越多越坏（例如 3E 80 抑制正响应是**正确的**
            静默行为），但过高说明 ECU 处理不过来/丢帧/崩溃；
          - ``alive`` 用一条 3E 00 复核（False 表示模糊后 ECU 已失联）。
        """
        rng = random.Random(int(seed))
        stats = {
            "total": 0, "positive": 0, "negative": 0,
            "no_response": 0, "alive": False, "elapsed_s": 0.0,
        }
        t0 = time.monotonic()
        for _ in range(int(count)):
            req = self._random_request(rng)
            stats["total"] += 1
            try:
                ok, resp = self._tester.send_request(req, timeout=0.5)
            except TimeoutError:
                stats["no_response"] += 1
                continue
            self._last_response = resp
            if ok:
                stats["positive"] += 1
            else:
                stats["negative"] += 1
        stats["elapsed_s"] = round(time.monotonic() - t0, 2)
        # 模糊结束后复核 ECU 是否还活着（不抛错，只记统计；断言交给测试用例）
        try:
            self.send_raw_request("3E 00")
            stats["alive"] = bool(self._last_response.positive)
        except TimeoutError:
            stats["alive"] = False
        info(f"模糊测试统计: {stats}")
        return stats

    def _random_request(self, rng: random.Random) -> bytes:
        """构造一条模糊请求：合法模板 / 随机 SID+payload（单帧 ≤7 字节）。"""
        if rng.random() < 0.4:
            return bytes.fromhex(rng.choice(self.LEGAL_TEMPLATES).replace(" ", ""))
        sid = rng.choice(self.FUZZ_SIDS)
        payload_len = rng.randint(0, 6)
        payload = bytes(rng.randrange(256) for _ in range(payload_len))
        return bytes([sid]) + payload

    @keyword("Malformed Frame Should Be Rejected")
    def malformed_frame_should_be_rejected(self, request_hex: str,
                                           expected_nrc) -> None:
        """畸形/截断请求必须得到**明确负响应**（而不是无响应/崩溃）。

        覆盖：payload 截断、超长、非法子功能等。
        无响应（TimeoutError）会被转成断言失败并给出清晰原因 —— 这是
        "ECU 静默吞掉畸形帧"类缺陷的检测点。

        为什么带一次重试：虚拟总线是 UDP 组播、没有真实 CAN 的帧重传，
        高负载下偶发丢一帧会让"本来会回 NRC"的请求看起来像无响应。这里
        只对**超时**重试一次；连续两次无响应仍判失败（真静默吞帧骗不过去），
        期望的 NRC 仍会被严格断言。
        """
        nrc_int = int(str(expected_nrc), 0)
        try:
            self.send_raw_request(request_hex)
        except TimeoutError:
            info("畸形帧首次无响应（UDP 组播无重传，疑似丢帧）→ 重试一次确认")
            try:
                self.send_raw_request(request_hex)
            except TimeoutError as exc:
                raise AssertionError(
                    f"畸形帧 {request_hex!r} 连续两次无响应（ECU 应按协议回 "
                    f"NRC {nrc_int:#04x}，无响应说明请求被吞/ECU 异常）: {exc}"
                ) from exc
        self.security_access_should_fail_with_nrc(nrc_int)
        info(f"畸形帧 {request_hex!r} → NRC {nrc_int:#04x} ✓")

    @keyword("Send Raw Request With Retry")
    def send_raw_request_with_retry(self, request_hex: str,
                                    retries: int = 1) -> str:
        """发送裸 UDS 请求；**幂等请求**在超时后重试（默认 1 次）。

        仅用于重复执行无副作用的请求（如 11 01 复位、22 读 DID）；
        有副作用的请求（27 02 错误密钥、14 清码计数等）不要用这个，
        避免重试把动作做两遍影响状态机。
        """
        last_exc: Exception | None = None
        for attempt in range(int(retries) + 1):
            try:
                return self.send_raw_request(request_hex)
            except TimeoutError as exc:
                last_exc = exc
                if attempt < int(retries):
                    info(f"请求 {request_hex} 超时（UDP 组播无重传，疑似丢帧）"
                         f"→ 重试 {attempt + 1}/{retries}")
        raise TimeoutError(
            f"请求 {request_hex} 连续 {int(retries) + 1} 次无响应: {last_exc}")

    @keyword("Measure Response After Fuzz")
    def measure_response_after_fuzz(self, request_hex: str = "22 F187") -> float:
        """模糊测试后测量一次"请求→响应"往返耗时（毫秒），并断言正响应。

        用途：证明 ECU 虽然被轰炸过，但状态机/诊断链路仍能正常、及时响应
        （不是"还能回包但慢到不可用"）。若首测超时（UDP 丢帧），重试一次
        并只统计重试的耗时，避免把偶然丢帧误判为"恢复失败"。
        """
        for attempt in range(2):
            t0 = time.monotonic()
            try:
                self.send_raw_request(request_hex)
            except TimeoutError:
                if attempt == 0:
                    info(f"模糊后首测 {request_hex} 超时（UDP 组播丢帧）→ 重试一次")
                    continue
                raise
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.response_should_be_positive()
            info(f"模糊后响应耗时 {elapsed_ms:.1f} ms")
            return round(elapsed_ms, 2)
        raise AssertionError("不可达")
