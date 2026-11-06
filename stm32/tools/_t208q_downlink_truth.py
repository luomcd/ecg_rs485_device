#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208q_downlink_truth.py —— 下行到底通不通（不会自欺的判据）

为什么需要这个脚本
------------------
_t208p 用「ESP32 发 START 后有没有帧流」判下行 —— **这是错判**。
如果 STOP 没生效，帧流一直在跑，那任何 START 之后都能看到帧，全是假阳性。
11-05 就踩了这个坑（12 s 内 316 帧不停，"ESP32 下行成功"是假的）。

本脚本只看两个**不可伪造**的量：
    ① ASCII `test snapshot` ⇒ 必须回到 `AA 55 03 4A` 快照帧（80 B）。
       这个帧只在收到命令后才生成，帧流里没有它。
    ② STOP 的**效果** ⇒ 发完 3 次 STOP 后，字节率必须掉到 0。
       如果 8 s 后还在出帧 ⇒ STOP 没被受理 ⇒ 下行不通。

判据
----
    A. 收到 AA5503 快照 ⇒ 下行通（铁证）
    B. STOP 后字节率→0     ⇒ 下行通（强证据，方向反证）
    两者都失败 ⇒ 下行不通
    只有一个成立 ⇒ 部分通（要单独查那一条路径）

用法
----
    python _t208q_downlink_truth.py --port COM26
"""
import argparse
import sys
import time

import serial

SNAP = b"\xAA\x55\x03\x4A"
DISP = b"\xAA\x55\x01\x35"
STOP = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.008)
    return bytes(buf)


def rate(p, dur=1.0):
    return len(drain(p, dur)) / dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM26")
    a = ap.parse_args()

    p = serial.Serial(a.port, 115200, timeout=0.2)
    try:
        time.sleep(0.3)
        p.reset_input_buffer()

        base = rate(p, 1.0)
        print(f"基线字节率 {base:.0f} B/s"
              + ("（帧流在跑，约 {:.1f} fps）".format(base / 59) if base > 100 else "（静默）"))

        # ---------- ① 快照 --------------------------------------------------
        print(f"\n① 发 ASCII「test snapshot」×2")
        p.reset_input_buffer()
        for _ in range(2):
            p.write(b"test snapshot\r\n")
            p.flush()
            time.sleep(0.5)
        r = drain(p, 1.5)
        n_snap = r.count(SNAP)
        n_disp = r.count(DISP)
        print(f"   收到 {len(r)} B；快照帧 AA5503 {n_snap} 次；显示帧 AA5501 {n_disp} 次")
        if n_snap:
            k = r.find(SNAP)
            print(f"   快照帧位置 {k}，其后 20 B: {r[k:k+20].hex(' ').upper()}")

        # ---------- ② STOP 的效果 -------------------------------------------
        print(f"\n② 连发 STOP ×3，然后每 1 s 量一次字节率（看它到底停不停）")
        for _ in range(3):
            p.write(STOP)
            p.flush()
            time.sleep(0.3)
        rates = []
        for k in range(8):
            rr = rate(p, 1.0)
            rates.append(rr)
            print(f"   +{k+1}s  {rr:7.0f} B/s" + ("   ← 已静默" if rr == 0 else ""))

        stopped = rates[-1] == 0
        print("\n" + "=" * 62)
        print("[判定]")
        print(f"  ① 快照回执：{'✅ 有' if n_snap else '❌ 无'}")
        print(f"  ② STOP 生效：{'✅ 已停' if stopped else '❌ 仍在出帧'}")
        print()
        if n_snap and stopped:
            print("  ✅ 下行通（两条独立判据都成立）")
            return 0
        if n_snap or stopped:
            print("  ⚠ 部分通：只有一条判据成立，需单独查另一条路径")
            return 1
        print("  ❌ 下行不通（ASCII 快照无回执 + STOP 无效果）")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
