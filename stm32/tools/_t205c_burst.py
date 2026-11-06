#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205c_burst.py —— 单发 vs 连发 N 次的成功率（T205 第三步）

前两步的事实
------------
1. 前缀补偿 k=0/1/2/3 ⇒ **不是丢首字节**（k=0 通、k≥1 全 0）。
2. 相位扫描 3 次/相位 ⇒ 噪声太大，且**与线路占用矛盾**：实测上行只占 2370 B/s
   ÷ 11520 B/s ≈ 10%，不可能把下行挡掉 50%。（脚本自动结论"方向抢占成立"不采信。）

本步要回答
----------
1. **失败是独立随机还是结构性**？
   若 p(N=1) ≈ 0.5 而 p(N=4) ≈ 1-(1-p)^4 ≈ 0.94 ⇒ 独立随机 ⇒ **连发就是可用的缓解**。
   若 p(N=4) 仍 ≈ 0.5 ⇒ 结构性（每 n 条只收 1 条之类），要转去查接收事件。
2. **ASCII 与二进制是不是同一个病**？
   二进制判据是 STA 翻转；ASCII 判据是回显里出现 `notch:`。
   若二进制连发能救、ASCII 连发救不了 ⇒ 两者病因不同（ASCII 还多一道
   `comm_send` 忙判，被上行帧顶掉就静默丢回显）。

方法
----
RUN 态（显示帧在流）下，对每种「连发次数」做若干轮：
    连发 N 次（间隔 3 ms，保证每次各自触发一次 IDLE）→ 收 12 帧 → 判定
二进制的期望值**跟随实测状态**（每次发"当前的反面"），避免"值本来就对"的假阳性。

用法
----
    python tools/_t205c_burst.py --port COM27 --rounds 6
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


CMD_SET_NOTCH = 0x05
ASCII_NOTCH = b"test notch\r\n"


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []
        self.raw = bytearray()

    def feed(self, data: bytes, now: float) -> None:
        self.raw += data
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
                self.sta.append((now, frame[7]))
            del self.buf[:total]

    def notch(self) -> int | None:
        if not self.sta:
            return None
        tail = [s for _, s in self.sta[-8:]]
        return (collections.Counter(tail).most_common(1)[0][0] >> 1) & 0x01


def drain(port, rd: Reader, frames: int = 12, timeout: float = 1.5) -> None:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) - n0 < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())


def main() -> int:
    ap = argparse.ArgumentParser(description="单发 vs 连发 N 次成功率")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--rounds", type=int, default=6, help="每个 N 跑几轮")
    ap.add_argument("--bursts", default="1,2,4,8", help="连发次数列表")
    args = ap.parse_args()

    bursts = [int(x) for x in args.bursts.split(",") if x.strip()]

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}: {exc}")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 25, 3.0)
        if len(rd.sta) < 10:
            print(f"[FAIL] 只收到 {len(rd.sta)} 帧 ⇒ 不在 RUN 态／串口错")
            return 6
        print(f"[ok] 上行 {len(rd.sta)} 帧，STA=0x{rd.sta[-1][1]:02X}，"
              f"当前陷波 {rd.notch()}")
        print()

        # ---------------- 二进制 SET_NOTCH ----------------
        print("二进制 SET_NOTCH（判据：STA.bit1 翻转）")
        print(f"{'连发':>4}  {'成功':>8}  {'成功率':>7}  轨迹")
        bin_stats = []
        for n in bursts:
            hits = 0
            marks = []
            for _ in range(args.rounds):
                cur = rd.notch()
                if cur is None:
                    continue
                want = 1 - cur
                payload = build(CMD_SET_NOTCH, bytes([want]))
                for _ in range(n):
                    port.write(payload)
                    port.flush()
                    time.sleep(0.003)
                drain(port, rd, 12, 1.5)
                got = rd.notch()
                ok = got == want
                hits += 1 if ok else 0
                marks.append("1" if ok else ".")
            p = hits / args.rounds
            bin_stats.append((n, p))
            print(f"{n:>4}  {hits:>3}/{args.rounds:<4}  {p * 100:>6.1f}%  {' '.join(marks)}")
        print()

        # ---------------- ASCII test notch ----------------
        print("ASCII `test notch`（判据：回显里出现 notch:）")
        print(f"{'连发':>4}  {'成功':>8}  {'成功率':>7}  轨迹")
        ascii_stats = []
        for n in bursts:
            hits = 0
            marks = []
            for _ in range(args.rounds):
                rd.raw.clear()
                for _ in range(n):
                    port.write(ASCII_NOTCH)
                    port.flush()
                    time.sleep(0.003)
                drain(port, rd, 12, 1.5)
                ok = b"notch:" in bytes(rd.raw)
                hits += 1 if ok else 0
                marks.append("1" if ok else ".")
            p = hits / args.rounds
            ascii_stats.append((n, p))
            print(f"{n:>4}  {hits:>3}/{args.rounds:<4}  {p * 100:>6.1f}%  {' '.join(marks)}")
        print()

        # ---------------- 独立性检验 ----------------
        print("=" * 62)
        print("独立性检验：若失败独立随机，p(N) 应满足 1-(1-p1)^N")
        print(f"{'N':>4}  {'实测':>8}  {'独立模型预测':>14}")
        p1 = bin_stats[0][1] if bin_stats else 0.0
        for n, p in bin_stats:
            pred = 1.0 - (1.0 - p1) ** n
            print(f"{n:>4}  {p * 100:>7.1f}%  {pred * 100:>13.1f}%")
        print("-" * 62)
        p1a = ascii_stats[0][1] if ascii_stats else 0.0
        print(f"ASCII 单发 {p1a * 100:.1f}%   二进制单发 {p1 * 100:.1f}%")
        print("=" * 62)
        return 0


if __name__ == "__main__":
    sys.exit(main())
