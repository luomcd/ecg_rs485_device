#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205n_sta_lag.py —— 证明「串口判据读到的是滞后数据」（T205 收尾）

已定事实
--------
`_t205m_reset_ab.py` 实测：硬复位前后，`0x05` 在**芯片侧落地 8/8 与 7/8**
（SWD 直读 `f64.notch_on`），而我的串口 STA 判据只有 4/8，且 12 帧 STA 直方图
**恒定 `0x02×12` 一动不动**。

⇒ 下行命令根本没丢。唯一坏掉的是我的取样方法。

本脚本要证伪的那个机制
----------------------
每轮我都只 `drain(12 帧)`，但两次取样之间夹着 1~3 s 的 SWD 调用（还要起进程），
这段时间设备照常以 ≈20 fps 产帧 ⇒ **CH340 内核缓冲里的积压只增不减**。
我读 12 帧、积 60 帧 ⇒ 我永远在读几秒甚至十几秒前的旧帧，看起来 STA 就"卡住"了。

判据（三重）
------------
① `in_waiting` 随时间单调涨到几千字节 ⇒ 积压确实在长；
② **在读之前 `reset_input_buffer()` 丢掉积压**，同一场景下 STA 立刻跟手 ⇒ 坐实；
③ 同一轮里「冲洗前 STA」vs「冲洗后 STA」逐条对比。

用法
----
    python tools/_t205n_sta_lag.py --port COM27 --trials 6
"""
import argparse
import collections
import re
import subprocess
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

CLI = (r"C:\Program Files\STMicroelectronics\STM32Cube"
       r"\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe")
ADDR_FLAGS = 0x200008C0


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


def chip_notch():
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug",
             "-r32", hex(ADDR_FLAGS), "4", "-q"],
            capture_output=True, text=True, timeout=40).stdout
    except Exception:
        return None
    m = re.search(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
    return None if not m else (int(m.group(1), 16) & 0xFF)


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
            del self.buf[:total]

    def mode(self, tail: int = 10):
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-tail:]).most_common(1)[0][0] >> 1) & 1

    def hist(self, tail: int = 10) -> str:
        cnt = collections.Counter(self.sta[-tail:])
        return " ".join(f"{k:02X}×{v}" for k, v in sorted(cnt.items()))


def drain(port, rd: Reader, frames: int, timeout: float) -> int:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and (len(rd.sta) - n0) < frames:
        chunk = port.read(256)
        if chunk:
            rd.feed(chunk)
    return len(rd.sta) - n0


def main() -> int:
    ap = argparse.ArgumentParser(description="证明串口判据读到滞后数据")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=6)
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 10, 1.5)
        if len(rd.sta) < 6:
            port.write(build(0x01))
            port.flush()
            time.sleep(0.4)
            drain(port, rd, 10, 1.5)
        if len(rd.sta) < 6:
            print("[FAIL] 无上行帧")
            return 6

        print(f"[ok] 起始 in_waiting={port.in_waiting} B  "
              f"STA={rd.hist()}  芯片 notch={chip_notch()}")
        print()
        print(f"{'#':>2} {'发':>2} {'芯片':>4} | {'积压B':>7} {'冲洗前STA':>9} "
              f"{'冲洗后STA':>9} {'后hist':>10} | {'跟手?':>6}")
        print("-" * 74)

        agree_before = agree_after = 0
        for i in range(1, args.trials + 1):
            f0 = chip_notch()
            if f0 is None:
                print("[FAIL] SWD 读失败")
                return 5
            want = 1 - f0

            port.write(build(0x05, bytes([want])))
            port.flush()
            time.sleep(0.50)

            backlog = port.in_waiting            # ← 判据①：积压字节数
            drain(port, rd, 10, 1.2)             # 只读 10 帧，积压照旧留在缓冲里
            sta_before = rd.mode()

            port.reset_input_buffer()            # ← 判据②：丢掉积压
            rd2 = Reader()
            drain(port, rd2, 10, 0.8)            # 现在读到的就是最新帧
            sta_after = rd2.mode()

            f1 = chip_notch()
            ok_b = (sta_before == want)
            ok_a = (sta_after == want)
            agree_before += ok_b
            agree_after += ok_a
            print(f"{i:>2} {want:>2} {f1:>4} | {backlog:>7} "
                  f"{str(sta_before):>9} {str(sta_after):>9} "
                  f"{rd2.hist():>10} | "
                  f"{'✓' if ok_a else '✗':>6}"
                  f"{'' if ok_b == ok_a else '   ← 冲洗后才跟手'}")
            time.sleep(0.15)

        print("-" * 74)
        print(f"冲洗前（旧取样法）跟手 {agree_before}/{args.trials} ／ "
              f"冲洗后（新取样法）跟手 {agree_after}/{args.trials}")
        print()
        if agree_after > agree_before:
            print("[结论] ★旧取样法读到的是**滞后积压帧** ⇒ T205 的 17% 是测量假象。")
            print("       正确写法：每次取样前 `reset_input_buffer()`，只读新帧。")
        else:
            print("[结论] 冲洗未改善 ⇒ 不是积压问题，回到固件侧查 STA 生成链路。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
