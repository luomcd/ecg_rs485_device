#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205m_reset_ab.py —— 硬复位前后 A/B 对照（T205 · 猛哥提示「先复位再操作」）

为什么
------
猛哥 2026-11-04 指出：**他此前所有成功操作（STOP 立即生效、陷波 10/10 落地）
的前置条件都是「先复位 STM32L476」**。而我这几十次下发全部堆在**同一份未复位的
板态**上（中间还夹了 SWD 清零、反复开关流）⇒ 我的 17% 与他的 100% 可能测的根本
不是同一个系统状态。

修掉 _t205l 的两个判据缺陷
--------------------------
① **目标值必须由芯片状态反推**（原来 `want = 1 - STA`）：
   STA 卡在 0 ⇒ 每轮都发 `notch=1`，而 `ecg_filter_f64_set_notch` 是**幂等**的
   （`ecg_filter.c:808  if ((int)f->notch_on == on) return;`）⇒ 第 3~6 次下发是
   **空操作、根本不可能失败**，却被算成「芯片是=成功」的 5/6。真数据只有前两轮：
   1 次真翻转、1 次真失败。
② **打印 STA 直方图**：把「8 帧众数」展开成「各 STA 值各占几帧」，一眼能看出
   是「帧里 STA 真的没变」还是「我的取样窗口/众数算错」。

判据
----
    芯片判据：SWD 直读 `f64.notch_on`（0x200008C0） == want
    串口判据：上行显示帧 frame[7] bit1（`ads1293_status_byte()` 的唯一输出口）
    复位探针：`comm_tx_frames`（0x200010A8）归零 ⇒ 确实复位了

用法
----
    python tools/_t205m_reset_ab.py --port COM27 --trials 8
    python tools/_t205m_reset_ab.py --port COM27 --trials 8 --no-reset   # 只测复位后
    python tools/_t205m_reset_ab.py --port COM27 --trials 8 --before-only # 只测复位前
"""
import argparse
import collections
import subprocess
import re
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

CLI = (r"C:\Program Files\STMicroelectronics\STM32Cube"
       r"\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe")
ADDR_FLAGS = 0x200008C0       # f64.notch_on / f64.mode
ADDR_TXFRAMES = 0x200010A8    # comm_tx_frames


# ---------------------------------------------------------------- 协议 ---
def crc8(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build(cmd: int, arg: bytes = b"") -> bytes:
    length = 1 + len(arg)
    body = bytes([0x80, length & 0xFF, (length >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


# ------------------------------------------------------------------ SWD ---
def swd_word(addr: int, nbytes: int = 4):
    """hotplug 读，返回原始 32bit 整数列表。hotplug 是关键：
    默认连接模式会**软复位**目标 ⇒ 读到的 f64 全是 0（假空白）。"""
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug",
             "-r32", hex(addr), str(nbytes), "-q"],
            capture_output=True, text=True, timeout=40).stdout
    except Exception:
        return []
    return [int(m, 16) for m in
            re.findall(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)]


def chip_state():
    """返回 (notch, mode)；失败 None。"""
    ws = swd_word(ADDR_FLAGS, 4)
    if not ws:
        return None
    return (ws[0] & 0xFF, (ws[0] >> 8) & 0xFF)


def tx_frames():
    ws = swd_word(ADDR_TXFRAMES, 4)
    return ws[0] if ws else None


def hw_reset() -> bool:
    """硬件复位（拉 NRST）。用 -hardRst 而非 -rst：
    -rst 是内核系统复位（SWrst），-hardRst 才是拉 NRST 让整板重来 —— 与猛哥按
    复位键等价。"""
    try:
        r = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug", "-hardRst", "-q"],
            capture_output=True, text=True, timeout=40)
        text = (r.stdout or "") + (r.stderr or "")
        ok = ("Reset" in text or "reset" in text) and r.returncode == 0
        print(f"[reset] rc={r.returncode} " +
              ("OK" if ok else f"未确认：{text.strip()[:120]}"))
        return ok
    except Exception as exc:
        print(f"[reset] 失败：{exc}")
        return False


# --------------------------------------------------------------- 解析 ---
class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            idx = self.buf.find(b"\xAA\x55")
            if idx < 0:
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if idx > 0:
                del self.buf[:idx]
            if len(self.buf) < 5:
                return
            ftype = self.buf[2]
            length = self.buf[3] | (self.buf[4] << 8)
            total = 6 + length
            if total > 260:
                del self.buf[0:1]
                continue
            if len(self.buf) < total:
                return
            frame = bytes(self.buf[:total])
            if crc8(frame[2: total - 1]) != frame[total - 1]:
                del self.buf[0:1]
                continue
            if ftype == 0x01 and length in (53, 103):
                self.sta.append(frame[7])
            del self.buf[:total]

    def drain_new(self, n: int) -> int:
        """返回新增帧数；调用者负责喂数据。"""
        return n

    def hist(self, tail: int = 20) -> str:
        if not self.sta:
            return "(无帧)"
        cnt = collections.Counter(self.sta[-tail:])
        return "  ".join(f"0x{k:02X}×{v}" for k, v in sorted(cnt.items()))

    def notch(self, tail: int = 12):
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-tail:]).most_common(1)[0][0] >> 1) & 1


def drain(port, rd: Reader, frames: int = 12, timeout: float = 1.5) -> int:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and (len(rd.sta) - n0) < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk)
    return len(rd.sta) - n0


# ---------------------------------------------------------------- 主流程 ---
def open_and_start(port_name: str, baud: int):
    """开串口 → 确保有上行帧（STOP 态则发 START）。返回 (port, rd) 或 (None, None)。"""
    for attempt in (1, 2):
        try:
            port = serial.Serial(port_name, baud, timeout=0.05)
        except Exception as exc:
            print(f"[FAIL] 打不开 {port_name}（第 {attempt} 次）：{exc}")
            time.sleep(1.0)
            continue
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 12, 1.5)
        if len(rd.sta) < 6:                     # 可能还在 STOP 态
            port.write(build(0x01))
            port.flush()
            time.sleep(0.4)
            drain(port, rd, 12, 1.5)
        if len(rd.sta) < 6:
            print(f"[FAIL] 无上行帧（第 {attempt} 次）")
            port.close()
            continue
        return port, rd
    return None, None


def consistency(port, rd: Reader, tag: str) -> None:
    """静置一致性：芯片 notch 与串口 STA.bit1 必须相同。"""
    st = chip_state()
    drain(port, rd, 20, 2.0)
    if st is None:
        print(f"[{tag}] SWD 读失败")
        return
    sta = rd.notch()
    print(f"[{tag}] 芯片 notch={st[0]} mode={st[1]} ／ 串口 STA 尾 20 帧：{rd.hist()}")
    print(f"[{tag}] 串口判读 notch={sta} ⇒ "
          f"{'一致 ✓' if sta == st[0] else '❌ 不一致'}")


def trials(port, rd: Reader, n: int, tag: str):
    """按**芯片状态**反推目标值，逐条下发 0x05 并双判据取样。"""
    print()
    print(f"{'#':>2} {'发':>2} {'芯片前':>6} {'芯片后':>6} {'芯片':>4} "
          f"{'STA后':>5} {'串口':>4}  STA直方图")
    print("-" * 78)
    chip_ok = sta_ok = both = 0
    for i in range(1, n + 1):
        f0 = chip_state()
        if f0 is None:
            print("[FAIL] SWD 读失败，中止")
            return chip_ok, sta_ok, both, i - 1
        want = 1 - f0[0]                       # ★由芯片反推，保证每轮都是真翻转
        port.write(build(0x05, bytes([want])))
        port.flush()
        time.sleep(0.45)
        drain(port, rd, 12, 1.2)
        f1 = chip_state()
        if f1 is None:
            print("[FAIL] SWD 读失败，中止")
            return chip_ok, sta_ok, both, i - 1
        c_ok = (f1[0] == want)
        s_val = rd.notch()
        s_ok = (s_val == want)
        chip_ok += c_ok
        sta_ok += s_ok
        both += (c_ok and s_ok)
        print(f"{i:>2} {want:>2} {f0[0]:>6} {f1[0]:>6} "
              f"{'✓' if c_ok else '✗':>4} {str(s_val):>5} "
              f"{'✓' if s_ok else '✗':>4}  {rd.hist(12)}")
    return chip_ok, sta_ok, both, n


def main() -> int:
    ap = argparse.ArgumentParser(description="硬复位前后 A/B 对照")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--no-reset", action="store_true", help="不做硬复位")
    ap.add_argument("--before-only", action="store_true", help="只测复位前")
    args = ap.parse_args()

    summary = []

    # ===================== Phase A：复位前 =====================
    print("=" * 78)
    print("Phase A —— 硬复位【前】（= 我前几轮跑了几十次下发的那份板态）")
    print("=" * 78)
    st = chip_state()
    tf = tx_frames()
    print(f"[A] 芯片 notch/mode = {st} ／ comm_tx_frames = {tf}")
    port, rd = open_and_start(args.port, args.baud)
    if port is None:
        return 6
    try:
        consistency(port, rd, "A")
        a = trials(port, rd, args.trials, "A")
        summary.append(("复位前", a))
    finally:
        port.close()

    if args.before_only:
        return report(summary, args.trials)

    # ===================== 硬复位 =====================
    print()
    print("=" * 78)
    print("Phase R —— 硬件复位（拉 NRST，等价于猛哥按复位键）")
    print("=" * 78)
    tf0 = tx_frames()
    if not hw_reset():
        print("[WARN] 复位命令未确认，继续（下一步的 tx_frames 归零会二次验证）")
    time.sleep(2.0)
    tf1 = tx_frames()
    print(f"[R] comm_tx_frames：复位前 {tf0} → 复位后 {tf1}")
    st = chip_state()
    print(f"[R] 复位后芯片 notch/mode = {st}  "
          f"（ECG_NOTCH_DEFAULT_ON ⇒ 应回到 1）")

    if args.no_reset:
        print("[R] --no-reset：跳过 Phase B")
        return report(summary, args.trials)

    # ===================== Phase B：复位后 =====================
    print()
    print("=" * 78)
    print("Phase B —— 硬复位【后】（= 猛哥每次操作前的干净板态）")
    print("=" * 78)
    port, rd = open_and_start(args.port, args.baud)
    if port is None:
        return 6
    try:
        consistency(port, rd, "B")
        b = trials(port, rd, args.trials, "B")
        summary.append(("复位后", b))
    finally:
        port.close()

    return report(summary, args.trials)


def report(summary, n: int) -> int:
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"{'阶段':<8}{'芯片落地':>10}{'串口判据':>10}{'两者皆中':>10}")
    print("-" * 40)
    for tag, (c, s, b, m) in summary:
        print(f"{tag:<8}{f'{c}/{m}':>10}{f'{s}/{m}':>10}{f'{b}/{m}':>10}")
    print()
    if len(summary) == 2:
        (_, a), (_, b2) = summary
        d = (b2[0] / max(1, b2[3])) - (a[0] / max(1, a[3]))
        print(f"[差值] 芯片落地率：复位后 − 复位前 = {d * 100:+.1f} 个百分点")
        if d > 0.25:
            print("[结论] ★复位显著改善 ⇒ 我前几轮测的不是猛哥操作的板态，")
            print("       17% 是「脏板态」下的数字，不能当作常态成功率。")
        elif abs(d) <= 0.25:
            print("[结论] 复位无显著差异 ⇒ 板态不是主因，需回到固件/驱动层找。")
        else:
            print("[结论] 复位反而更差（反直觉）⇒ 先确认复位是否真的生效。")
        if b2[1] < b2[3] and b2[0] > 0:
            print("[提醒] 芯片落地 > 串口判据 ⇒ **串口判据仍有偏差**，需查取样窗口。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
