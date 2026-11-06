#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208z_stat_then_snap.py —— 不碰 485-start，直接「读计数 → 发快照 → 再读计数」

用途
----
刚用 ST-Link 复位过 STM32（横幅刚发完），此时：
    · 485 源已经在跑（不能再来一次 485-start，那会清零统计）
    · STM32 必在 STOP 态（复位即 STOP）
⇒ 这是发 0x10 最干净的时机。

判据
----
    rx 已经 >0          ⇒ 横幅被 ESP32 收到了，上行通
    snap 回执 ≥1        ⇒ 下行通
    两者合计即可定性

用法
----
    python _t208z_stat_then_snap.py --esp COM35
"""
import argparse
import re
import sys
import time

import serial

RE_SNAP_ACK = re.compile(r"快照回执\s*(\d+)\s*帧")
RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)
RE_SNAPSHOTS = re.compile(r"snapshots=(\d+)")
KEYS = ("rx", "tx", "frames", "crc", "resync", "bad_len")


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.02)
    return bytes(buf)


def cmd(p, line, wait=1.0):
    p.write(line.encode() + b"\r\n")
    p.flush()
    return drain(p, wait).decode("utf-8", "replace")


def stat(p):
    txt = cmd(p, "485-stat", 0.9)
    m = RE_STAT.search(txt)
    if not m:
        return None
    d = dict(zip(KEYS, (int(x) for x in m.groups())))
    mm = RE_SNAPSHOTS.search(txt)
    d["snapshots"] = int(mm.group(1)) if mm else -1
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)

        s0 = stat(p)
        if s0 is None:
            print("[FAIL] 读不到 485-stat（485 源可能没启动）")
            return 2
        print(f"当前统计: {s0}")
        if s0["rx"] > 0:
            print(f"  ⇒ rx={s0['rx']} B > 0，**横幅被收到了，上行通**")
        else:
            print("  ⇒ rx=0，复位横幅没被收到（或 485 源启动晚于复位）")

        print("\n发 485-send snap（STM32 刚复位，必在 STOP 态）")
        out = cmd(p, "485-send snap", 2.0)
        for line in out.splitlines():
            if line.strip():
                print("  " + line.strip())
        m = RE_SNAP_ACK.search(out)
        ack = int(m.group(1)) if m else -1

        s1 = stat(p)
        print(f"\n事后统计: {s1}")
        print("\n" + "=" * 60)
        up = s0["rx"] > 0 or (s1 and s1["rx"] > 0)
        print(f"  上行（横幅）: {'✅ 收到' if up else '❌ 没收到'}")
        print(f"  下行（快照）: {'✅ 通' if ack >= 1 else '❌ 不通（回执 0）'}")
        if ack >= 1:
            print("\n[结论] ✅ 双通道全通 —— 0x10 被受理并回了 80 B 快照帧")
            return 0
        if up:
            print("\n[结论] ❌ 上行通、下行不通 ⇒ 断点在下行半段：")
            print("       总线 → UT-2211 的 485 接收 → 它的 232 发送(pin2 SOUT)")
            print("       → MAX3232 R1 → PD2")
            print("       下一步量 UT-2211 DB9 pin2 对 pin5 的电压（应为负 5~10 V）")
            return 1
        print("\n[结论] ⚠ 上行下行都没通，先解决上行")
        return 2
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
