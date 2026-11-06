#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205g_cmd_compare.py —— 同一条链路上比较不同下行命令的单发成功率（T205 第五步）

为什么
------
猛哥 11-04 报：「我在 WinApp 上操作，START 后 STOP 立即生效；操作监护、诊断按钮后，
状态栏也同步更改为对应内容。」——这与本轮实测的「`SET_NOTCH` 单发只有 ~17%」矛盾。

两种可能，必须分清：

    A. 0x04 SET_MODE 真的比 0x05 SET_NOTCH 更容易成功（那说明差异在命令/时序，不在链路）
    B. **WinApp 状态栏只反映本地按钮状态，不是读 STA**（那他的观测就不构成"命令到了"的证据）

判别方法：用**同一套脚本、同样的时间间隔**分别发 0x04 和 0x05，比较单发成功率。
两者帧长都是 8 B、都不加状态门、判据都落在 STA 位上 ⇒ 若成功率相近，则链路问题一致，
则 B 成立（状态栏是本地态），需要回头核对 WinApp 侧。

判据位
------
    STA.bit0 = mode（0 诊断 / 1 监护）      ← 0x04 SET_MODE 影响
    STA.bit1 = notch（0 关 / 1 开）          ← 0x05 SET_NOTCH 影响

每次发**与当前相反**的值 ⇒ 判据是"翻转"，能区分"没变"与"变对了"。

用法
----
    python tools/_t205g_cmd_compare.py --port COM27 --rounds 8
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


CASES = [
    # (标签, CMD, STA 位序号)
    ("0x04 SET_MODE ", 0x04, 0),
    ("0x05 SET_NOTCH", 0x05, 1),
]


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []

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
                self.sta.append((now, frame[7]))
            del self.buf[:total]

    def bit(self, n: int) -> int | None:
        if not self.sta:
            return None
        tail = [s for _, s in self.sta[-8:]]
        return (collections.Counter(tail).most_common(1)[0][0] >> n) & 0x01


def drain(port, rd: Reader, frames: int = 12, timeout: float = 1.5) -> None:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) - n0 < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())


def main() -> int:
    ap = argparse.ArgumentParser(description="比较 0x04 / 0x05 单发成功率")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--gap", type=float, default=0.6, help="两条命令之间的间隔（秒）")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}：{exc}")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 25, 3.0)
        if len(rd.sta) < 10:
            print(f"[FAIL] 只收到 {len(rd.sta)} 帧 ⇒ 不在 RUN 态／串口错")
            return 6
        print(f"[ok] 上行 {len(rd.sta)} 帧，STA=0x{rd.sta[-1][1]:02X}")
        print(f"     判据：0x04→STA.bit0(mode) ／ 0x05→STA.bit1(notch)，每次发反面值")
        print()

        stats = []
        for label, cmd, bitno in CASES:
            hits = 0
            marks = []
            for _ in range(args.rounds):
                cur = rd.bit(bitno)
                if cur is None:
                    continue
                want = 1 - cur
                port.write(build(cmd, bytes([want])))
                port.flush()
                time.sleep(args.gap)
                drain(port, rd, 12, 1.5)
                got = rd.bit(bitno)
                ok = got == want
                hits += 1 if ok else 0
                marks.append("1" if ok else ".")
            p = hits / args.rounds
            stats.append((label, hits, args.rounds, p))
            print(f"  {label}  {hits}/{args.rounds}  {p * 100:5.1f}%  {' '.join(marks)}")

        print()
        print("=" * 58)
        for label, h, n, p in stats:
            print(f"{label}  {h}/{n}  {p * 100:5.1f}%")
        print("-" * 58)
        if len(stats) == 2:
            p4, p5 = stats[0][3], stats[1][3]
            if abs(p4 - p5) <= 0.25:
                print("[判读] 两条命令成功率相近 ⇒ **链路问题一致**。")
                print("       ⇒ WinApp 状态栏若显示「同步生效」，多半是**本地按钮态**，不是读 STA；")
                print("         建议核对 WinApp：状态字节是否取自显示帧 frame[7]。")
            elif p4 > p5 + 0.25:
                print(f"[判读] 0x04 明显好于 0x05（{p4:.0%} vs {p5:.0%}）⇒ 差异在命令侧，不是纯链路。")
                print("       ⇒ 值得比对两条 case 的代码路径与 WinApp 的下发时机。")
            else:
                print(f"[判读] 0x05 反而好于 0x04（{p5:.0%} vs {p4:.0%}）⇒ 与预期相反，需复查判据位。")
        print("=" * 58)
        return 0


if __name__ == "__main__":
    sys.exit(main())
