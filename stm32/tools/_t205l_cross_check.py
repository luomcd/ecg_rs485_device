#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205l_cross_check.py —— 串口下发 + SWD 直读芯片，两套判据同时上（T205 自查）

为什么
------
T205 到这一步，唯一还站不稳的就是「我的判据本身」：

    · 我用的是 STA（上行显示帧第 8 字节的 bit1）
    · 猛哥用 WinApp 点，芯片状态 10/10 都翻（SWD 直读证实）

若我的 STA 判据有偏差（窗口太短、众数取样错、状态被后一帧顶掉），那测出来的 17% 就是假的。
所以本脚本对**同一条命令**同时取两个判据：

    判据①  STA.bit1       —— 从串口上行帧解析（我一直在用的）
    判据②  f64.notch_on   —— SWD 直读芯片（不受串口、不受 UI 影响）

两者**必须一致**（`ads1293_status_byte()` 就是读 `f64.notch_on`）。
若不一致 ⇒ 我的串口判据有 bug，前几轮数据全部作废。

地址（IAR map 已交叉验证）
--------------------------
    f64.notch_on = 0x200008C0     f64.mode = 0x200008C1

用法
----
    python tools/_t205l_cross_check.py --port COM27 --trials 6
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

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
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


def swd_flags() -> tuple | None:
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug", "-r32", hex(ADDR_FLAGS), "4", "-q"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    m = re.search(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
    if not m:
        return None
    w = int(m.group(1), 16)
    return (w & 0xFF, (w >> 8) & 0xFF)


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
                self.sta.append(frame[7])
            del self.buf[:total]

    def notch(self) -> int | None:
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-8:]).most_common(1)[0][0] >> 1) & 0x01


def drain(port, rd: Reader, frames: int = 8, timeout: float = 1.0) -> None:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) - n0 < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())


def main() -> int:
    ap = argparse.ArgumentParser(description="串口判据 vs SWD 判据 交叉校验")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=6)
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}：{exc}")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 15, 2.0)
        if len(rd.sta) < 8:
            port.write(build(0x01))
            port.flush()
            time.sleep(0.4)
            drain(port, rd, 15, 2.0)
        if len(rd.sta) < 8:
            print("[FAIL] 无上行帧")
            return 6
        print(f"[ok] 上行 {len(rd.sta)} 帧，STA=0x{rd.sta[-1]:02X}")
        print()
        print(f"{'#':>2} {'发出':>4} {'f64前':>6} {'f64后':>6} {'芯片翻转':>8} "
              f"{'STA翻转':>8}  一致?")
        print("-" * 62)
        agree = 0
        chip_ok = 0
        sta_ok = 0
        for i in range(1, args.trials + 1):
            f0 = swd_flags()
            if f0 is None:
                return 5
            cur = rd.notch()
            if cur is None:
                continue
            want = 1 - cur
            port.write(build(0x05, bytes([want])))
            port.flush()
            time.sleep(0.50)
            drain(port, rd, 10, 1.2)
            f1 = swd_flags()
            if f1 is None:
                return 5

            chip_flip = f1[0] == want
            sta_flip = rd.notch() == want
            same = chip_flip == sta_flip
            agree += 1 if same else 0
            chip_ok += 1 if chip_flip else 0
            sta_ok += 1 if sta_flip else 0
            print(f"{i:>2} {want:>4} {f0[0]:>6} {f1[0]:>6} "
                  f"{'是' if chip_flip else '否':>8} {'是' if sta_flip else '否':>8}  "
                  f"{'✓' if same else '✗ 不一致!'}")

        print("-" * 62)
        print(f"芯片判据 {chip_ok}/{args.trials} ／ STA 判据 {sta_ok}/{args.trials} "
              f"／ 两判据一致 {agree}/{args.trials}")
        print()
        if agree == args.trials:
            print("[结论] 两套判据完全一致 ⇒ **我的 STA 判据没问题**，")
            print("       前几轮测到的低成功率是真实的（RUN 态下行确实差）。")
        else:
            print("[结论] 两套判据不一致 ⇒ **我的串口判据有 bug**，前几轮数据作废，")
            print("       需先修判据再重测。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
