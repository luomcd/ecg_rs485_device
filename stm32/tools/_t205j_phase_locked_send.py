#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205j_phase_locked_send.py —— 验证「下发相位」是不是那个被我踩到的坑（T205 第七步）

我自己的测量坑（本步要证实的东西）
----------------------------------
前面所有成功率实验的间隔都是 **50 ms 的整数倍**：

    0.6 s = 12 个上行帧周期   0.65 s = 13 个   1.2 s = 24 个

而上行是 **20 fps ⇒ 周期恰好 50 ms**。⇒ **每次下发都落在同一相位上**。
如果那个相位正好落在"换向死区"，测出来就会是**稳定的 ~17%**，而不是随机分布。

这也回头解释了被我一票否决的相位扫描（`_t205b`）：当时 16~20 ms 全通、0/24/48 ms 全灭，
**那不是噪声，是真的**。我用"3 次/相位太少"把它否掉，属于矫枉过正。

本步三组对照
------------
    A 固定 0.650 s        —— 复现我之前的假象（应仍 ~17%）
    B 随机 0.30~0.95 s + 0~49 ms 抖动 —— 打散相位锁（应显著上升）
    C **节拍下发**：等到一帧到达，再等 `--delay` ms 才发 —— 若上升到 ~100%，
      则相位模型坐实，且**上位机可直接照此实现**（它本来就每 50 ms 收一帧）

⬆ C 是真正的工程解：**不用连发，只要在空闲窗口里发。**
   WinApp 的 `SerialPort` 写会被读线程的 250 ms ReadTimeout 天然抖动 ⇒ 它一直是随机相位，
   这就是「UI 点着好使、我的脚本老是失败」的根源。

用法
----
    python tools/_t205j_phase_locked_send.py --port COM27 --trials 12 --delay 18
"""
import argparse
import collections
import random
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")


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


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []
        self.t_arrive = []

    def feed(self, data: bytes, now: float) -> None:
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
            if total > 200:
                del self.buf[0:1]
                continue
            if len(self.buf) < total:
                return
            frame = bytes(self.buf[:total])
            if crc8(frame[2 : total - 1]) != frame[total - 1]:
                del self.buf[0:1]
                continue
            if ftype == 0x01 and length in (53, 103):
                self.sta.append(frame[7])
                self.t_arrive.append(now)
            del self.buf[:total]

    def bit(self, n: int) -> int | None:
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-8:]).most_common(1)[0][0] >> n) & 0x01


def wait_frame(port, rd: Reader, timeout: float = 1.0) -> bool:
    """阻塞到**新的一帧**到达（以帧为时钟源，比墙钟可靠）。"""
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())
            if len(rd.sta) > n0:
                return True
    return False


def judge(port, rd: Reader, want: int, frames: int = 10, timeout: float = 1.0) -> bool:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) - n0 < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())
    return rd.bit(1) == want


def main() -> int:
    ap = argparse.ArgumentParser(description="相位锁 vs 随机 vs 节拍下发")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--delay", type=float, default=18.0, help="节拍组：帧到后等几 ms 再发")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}：{exc}")
        print("       ↑ PermissionError ＝ WinApp／串口助手还占着，关掉再跑")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()
        if not wait_frame(port, rd, 2.0):
            port.write(build(0x01))
            port.flush()
            time.sleep(0.4)
            if not wait_frame(port, rd, 2.0):
                print("[FAIL] 无上行帧 ⇒ 不在 RUN 态／串口错")
                return 6
        # 攒一点帧
        for _ in range(10):
            wait_frame(port, rd, 1.0)
        print(f"[ok] 上行 {len(rd.sta)} 帧，STA=0x{rd.sta[-1]:02X}")
        ts = rd.t_arrive[-10:]
        gaps = [(ts[i + 1] - ts[i]) * 1000 for i in range(len(ts) - 1)]
        gaps = [g for g in gaps if g > 0.5]
        if gaps:
            print(f"     帧周期中位 {sorted(gaps)[len(gaps) // 2]:.2f} ms")
        print()

        groups = [
            ("A 固定 0.650 s（相位锁）", "fixed"),
            ("B 随机间隔＋抖动", "random"),
            (f"C 节拍下发（帧后 {args.delay:.0f} ms）", "rhythm"),
        ]
        results = []
        for label, mode in groups:
            hits = 0
            marks = []
            for _ in range(args.trials):
                cur = rd.bit(1)
                if cur is None:
                    break
                want = 1 - cur
                if mode == "fixed":
                    time.sleep(0.650)                       # 13 个周期 ⇒ 锁相位
                elif mode == "random":
                    time.sleep(random.uniform(0.30, 0.95) + random.uniform(0, 0.049))
                else:
                    if not wait_frame(port, rd, 1.0):
                        continue
                    time.sleep(args.delay / 1000.0)         # 帧到后进空闲窗口
                port.write(build(0x05, bytes([want])))
                port.flush()
                ok = judge(port, rd, want)
                hits += 1 if ok else 0
                marks.append("1" if ok else ".")
            results.append((label, hits, args.trials))
            print(f"  {label:<28} {hits:>3}/{args.trials}  "
                  f"{hits / args.trials * 100:5.1f}%  {' '.join(marks)}")

        print()
        print("=" * 66)
        for label, h, n in results:
            print(f"{label:<30}{h}/{n}  {h / n * 100:5.1f}%")
        print("-" * 66)
        a, b, c = (r[1] / r[2] for r in results)
        if c > a + 0.3 or b > a + 0.3:
            print("[命中] 相位就是那个坑。")
            print(f"       固定相位 {a:.0%} → 随机 {b:.0%} / 节拍 {c:.0%}")
            print("       ⇒ 之前那个『下行只有 17%』是**相位锁造成的测量假象**，链路本身没这么差。")
            print("       ⇒ 工程解：上位机**按显示帧节拍、在帧后 15~25 ms 下发**，不必连发。")
        else:
            print("[否定] 相位不是主因（三组都没拉开差距）⇒ 回到链路/固件侧继续查。")
        print("=" * 66)
        return 0


if __name__ == "__main__":
    sys.exit(main())
