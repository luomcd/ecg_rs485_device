#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205p_clean_rate.py —— 全程不碰 SWD 的纯净下行成功率（T205）

为什么要这个脚本
----------------
T205 到这里出现了明显矛盾：

    · 猛哥用 WinApp 点，SWD 实测 **10/10** 落地（`_t205i`，那轮我**没有**在点击之间插 SWD 读）；
    · 我用脚本发，中间夹两次 SWD 读，落地 **30%~40%**。

两次唯一的差别是：**我的脚本每次下发前后都要启一个 STM32_Programmer_CLI 进程去
attach/detach SWD**。attach 会短暂接管内核、冻结调试相关时序；如果它恰好发生在
8 字节下行到达的窗口里，HAL 的 IDLE/DMA 回调就可能错过一整包 —— 这会把「SWD
扰动」误记成「固件丢命令」。

判据
----
不碰 SWD，只用**冲洗后的串口 STA**（`_t205n` 已证：冲洗后 STA 与芯片 `f64.notch_on`
逐条一致 6/6）。目标值由上一轮观测状态反推 ⇒ 每轮都必须是真翻转。

额外输出每轮耗时与实际帧率，便于和 WinApp 的实况比对。

用法
----
    python tools/_t205p_clean_rate.py --port COM27 --trials 20
"""
import argparse
import collections
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
        self.frames = 0

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
            if self.buf[2] == 0x01 and length in (53, 103):
                self.sta.append(frame[7])
                self.frames += 1
            del self.buf[:total]

    def mode(self, tail: int = 8):
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-tail:]).most_common(1)[0][0] >> 1) & 1

    def hist(self, tail: int = 8) -> str:
        cnt = collections.Counter(self.sta[-tail:])
        return " ".join(f"{k:02X}×{v}" for k, v in sorted(cnt.items()))


def read_fresh(port, frames: int = 8, timeout: float = 1.0):
    """先冲洗积压，再读最新 N 帧 —— T205 唯一可靠的串口取样法。"""
    port.reset_input_buffer()
    rd = Reader()
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and rd.frames < frames:
        chunk = port.read(256)
        if chunk:
            rd.feed(chunk)
    return rd


def main() -> int:
    ap = argparse.ArgumentParser(description="纯净下行成功率（不碰 SWD）")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--gap", type=float, default=0.30,
                    help="下发后等待多少秒再取样")
    ap.add_argument("--no-flush", action="store_true",
                    help="写后不调 flush()（WinApp 就不 flush）")
    ap.add_argument("--restart", action="store_true",
                    help="测验前先 STOP→停 3 s→START，用来判别"
                         "「衰减是上行帧流造成的」还是「MCU 内部状态累积」")
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        rd = read_fresh(port, 10, 1.5)
        if rd.frames < 6:                       # STOP 态则补一发 START
            port.write(build(0x01))
            port.flush()
            time.sleep(0.5)
            rd = read_fresh(port, 10, 1.5)
        if rd.frames < 6:
            print("[FAIL] 无上行帧")
            return 6

        cur = rd.mode()
        print(f"[ok] 起跑：STA {rd.hist()}  ⇒ notch={cur}")
        if args.restart:
            port.write(build(0x02))          # STOP：让上行帧流停住
            port.flush()
            time.sleep(3.0)                  # 空闲 3 s ⇒ 总线/驱动管道全空
            port.write(build(0x01))          # START
            port.flush()
            time.sleep(0.6)
            rd = read_fresh(port, 10, 1.5)
            cur = rd.mode()
            print(f"[restart] 已 STOP→3s→START，notch={cur}")
        print()
        print(f"{'#':>2} {'发':>2} {'读回':>4} {'结果':>4} {'帧数':>4} "
              f"{'fps':>6} {'耗时ms':>7}  hist")
        print("-" * 66)

        ok = 0
        streaks = []
        run = 0
        per_trial = []
        for i in range(1, args.trials + 1):
            want = 1 - cur
            t0 = time.monotonic()
            port.write(build(0x05, bytes([want])))
            if not args.no_flush:
                port.flush()
            time.sleep(args.gap)
            rd = read_fresh(port, 8, 1.0)
            dt = (time.monotonic() - t0) * 1000
            got = rd.mode()
            good = (got == want)
            ok += good
            per_trial.append(dt)
            run = 0 if good else run + 1
            if good:
                streaks.append(run)
                cur = want
            fps = rd.frames / max(1e-6, dt / 1000.0)
            print(f"{i:>2} {want:>2} {str(got):>4} "
                  f"{'✓' if good else '✗':>4} {rd.frames:>4} "
                  f"{fps:>6.1f} {dt:>7.0f}  {rd.hist()}")

        n = len(per_trial)
        print("-" * 66)
        print(f"落地 {ok}/{n} = {ok / max(1, n) * 100:.1f}% ／ "
              f"平均单轮 {sum(per_trial) / max(1, n):.0f} ms")
        print()
        if ok >= n * 0.9:
            print("[结论] ★不碰 SWD 时下行几乎不丢 ⇒ 之前的 30~40% 是 **SWD 读取扰动**的结果，")
            print("       T205 的『RUN 态下行丢命令 80%』**不成立**，固件命令通道无需改。")
        elif ok <= n * 0.6:
            print("[结论] 不碰 SWD 仍然低 ⇒ 下行丢失是**真实现象**，且与我的测量无关；")
            print("       下一步只需解释『为何 WinApp 能 10/10』（时序/占空比/驱动差异）。")
        else:
            print("[结论] 中间态，样本量不足以定性，建议 --trials 40 复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
