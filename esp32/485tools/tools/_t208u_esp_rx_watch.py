#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208u_esp_rx_watch.py —— 用 ESP32 的计数当上行探针（抓 STM32 复位横幅）

为什么用复位横幅
----------------
`comm_port.c:153-161` 上电无条件吐 42 B ASCII：
    \\r\\n[uart5] ready 115200 8N1  PC12=TX PD2=RX\\r\\n
STOP 态下这是 STM32 唯一自发字节，不需要任何下行命令。
所以在「ESP32 是唯一主站」的拓扑里，它是干净的上行判据。

判读
----
    rx 涨 ≈61 B（横幅 42 + afe boot init 16 + 余量）且 resync 同步涨
        ⇒ 上行通、极性正确（ASCII 不是帧，必然被计入 resync，这是正常的）
    rx 涨但 bad_len 暴涨 / 数值离谱
        ⇒ 极性反了或线路噪声
    rx 完全不涨
        ⇒ 上行断（STM32→MAX3232→UT-2211→总线 任一环）

用法（脚本先跑，再去按 STM32 复位键）
    python _t208u_esp_rx_watch.py --esp COM35 --dur 45
"""
import argparse
import re
import sys
import time

import serial

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)
KEYS = ("rx", "tx", "frames", "crc", "resync", "bad_len")


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.01)
    return bytes(buf)


def stat(p):
    p.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(p, 0.6).decode("utf-8", "replace"))
    return dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--dur", type=float, default=45.0)
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.2)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.3)
        s0, = (stat(p),)
        if s0 is None:
            print("[FAIL] 读不到 485-stat")
            return 2
        if s0["rx"] == 0 and s0["tx"] == 0 and s0["frames"] == 0:
            p.write(b"485-start\r\n")
            drain(p, 2.0)
            s0 = stat(p) or s0

        print(f"基线: rx={s0['rx']} frames={s0['frames']} resync={s0['resync']} bad_len={s0['bad_len']}")
        print(f"监听 {a.dur:.0f} s —— 请现在按 STM32 复位键")
        print("-" * 62)

        t0 = time.time()
        prev = s0
        last = t0
        while time.time() - t0 < a.dur:
            time.sleep(0.9)
            s = stat(p)
            if s is None:
                continue
            d_rx = s["rx"] - s0["rx"]
            d_fr = s["frames"] - s0["frames"]
            d_rs = s["resync"] - s0["resync"]
            d_bl = s["bad_len"] - s0["bad_len"]
            d_cr = s["crc"] - s0["crc"]
            el = time.time() - t0
            if s["rx"] != prev["rx"] or s["frames"] != prev["frames"]:
                print(f"  [{el:5.1f}s] rx Δ{d_rx:+5d}  frames Δ{d_fr:+4d}  "
                      f"resync Δ{d_rs:+5d}  bad_len Δ{d_bl:+4d}  crc_err Δ{d_cr:+3d}")
                prev = s

        sE = stat(p) or prev
        print("-" * 62)
        d_rx = sE["rx"] - s0["rx"]
        print(f"总计 rx Δ{d_rx:+d}  frames Δ{sE['frames']-s0['frames']:+d}  "
              f"resync Δ{sE['resync']-s0['resync']:+d}  bad_len Δ{sE['bad_len']-s0['bad_len']:+d}")
        print()
        if d_rx >= 40:
            print("[判定] ✅ 上行通 —— 收到 STM32 的横幅（ASCII 被记入 resync 属正常）")
            return 0
        if d_rx > 0:
            print(f"[判定] ⚠ 只收到 {d_rx} B，远少于横幅的 ~60 B ⇒ 上行严重退化")
            return 1
        print("[判定] ❌ 上行不通 —— 复位后 ESP32 一个字节都没收到")
        print("       ⇒ 查 STM32→MAX3232(T1)→UT-2211(SIN) 这一段，或 A/B 极性")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
