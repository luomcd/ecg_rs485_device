#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208r_idle_downlink.py —— 在 STM32「空闲」状态下测下行（关键对照）

要拆开的两个假说
----------------
    H1  帧流在跑 ⇒ UT-2211 方向锁在发送 ⇒ 下行走 MT-2211 收不到（与 ESP32 无关）
    H2  ESP32 上电给总线加负载/偏置 ⇒ 下行收不到（与帧流无关）

做法：先等总线连续静默（靠**复位 STM32** 实现，因为 STOP 已被忽略、
一复位它就回到静默，`afe boot init` 不会自动起采），
然后在静默状态下发下行命令。

判据
----
    静默下：ASCII `test snapshot` 能回 `AA 55 03 4A` ⇒ 下行通
            ⇒ H1 成立（是帧流期间方向锁的问题，跟 ESP32 无关）
    静默下仍不通                                    ⇒ H2 成立

用法（脚本先跑，再去按 STM32 复位键）
    python _t208r_idle_downlink.py --port COM26
"""
import argparse
import sys
import time

import serial

SNAP = b"\xAA\x55\x03\x4A"
DISP = b"\xAA\x55\x01\x35"
STOP = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])
START = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM26")
    ap.add_argument("--wait", type=float, default=90.0, help="等静默的最长秒数")
    a = ap.parse_args()

    p = serial.Serial(a.port, 115200, timeout=0.2)
    try:
        time.sleep(0.2)
        p.reset_input_buffer()
        print(f"等待总线静默（最长 {a.wait:.0f} s）—— 请现在按 STM32 复位键")
        print("-" * 66)

        t0 = time.time()
        quiet_from = None
        idle_ok = False
        while time.time() - t0 < a.wait:
            n = p.in_waiting
            if n:
                p.read(n)
                quiet_from = None
            else:
                if quiet_from is None:
                    quiet_from = time.time()
                elif time.time() - quiet_from >= 1.2:
                    idle_ok = True
                    break
                time.sleep(0.01)
        print(f"静默达成={idle_ok}  用时 {time.time()-t0:.1f} s")
        if not idle_ok:
            print("[中止] 一直没静默，先解决 STOP 失效的问题")
            return 2

        # ---------- 静默下测下行 -------------------------------------------
        print("\n① 静默态发 ASCII「test snapshot」×2")
        p.reset_input_buffer()
        for _ in range(2):
            p.write(b"test snapshot\r\n")
            p.flush()
            time.sleep(0.5)
        r1 = drain(p, 1.5)
        n1 = r1.count(SNAP)
        print(f"   收到 {len(r1)} B；快照帧 AA5503 {n1} 次；显示帧 AA5501 {r1.count(DISP)} 次")
        if r1:
            print("   原始前 60 B: " + r1[:60].hex(" ").upper())
            print("   文本预览: " + r1[:60].decode("ascii", "replace")
                  .replace("\r", "\\r").replace("\n", "\\n"))

        print("\n② 静默态发二进制 START，看能不能起采")
        p.reset_input_buffer()
        p.write(START)
        p.flush()
        r2 = drain(p, 3.0)
        n2 = r2.count(DISP)
        print(f"   3 s 内收到 {len(r2)} B；显示帧头 AA5501 {n2} 次"
              f"（{n2/3.0:.1f} fps）")

        print("\n③ 收尾：发 STOP")
        p.write(STOP)
        p.flush()
        r3 = drain(p, 2.0)
        print(f"   2 s 内残余 {len(r3)} B（≈{len(r3)/59:.1f} 帧）")

        print("\n" + "=" * 62)
        print("[判定]")
        print(f"  静默态 ASCII 下行：{'✅ 通' if n1 else '❌ 不通'}")
        print(f"  静默态二进制下行：{'✅ 通' if n2 >= 5 else '❌ 不通'}")
        print()
        if n1 or n2 >= 5:
            print("  ⇒ H1 成立：**帧流在跑时**下行走不通；静默时是通的")
            print("     根因在 UT-2211 的方向切换被持续帧流锁在发送态，与 ESP32 无关")
            return 0
        print("  ⇒ H2 成立：静默下也不通 ⇒ 是 ESP32 上电引入的负载/偏置问题")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
