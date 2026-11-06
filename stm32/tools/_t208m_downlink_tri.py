#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208m_downlink_tri.py —— 同一次实验里同时验「字节上没上总线」+「STM32 回没回话」

为什么要三个观察者
------------------
只发 START 收 0 B，有两种可能：
    A) 字节根本没上总线（COM27 驱动/接线）
    B) 字节上了总线，但 UT-2211/STM32 收不到或不理
用 ESP32 的 rx 计数当旁证即可分开：ESP32 涨了 ⇒ 总线有字节 ⇒ 走 B。

同时跑两条下行路径：
    ① ASCII `test snapshot`  ⇒ 期望 80 B 快照帧（走 vcomcmd.c:889 的 "test" 闸）
    ② 二进制 START           ⇒ 期望起采帧流（走 :847 的 AA55 闸）
两条都 0 ⇒ 不是某一条路径的代码问题。

用法
----
    python _t208m_downlink_tri.py --usb COM27 --esp COM35
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


def _crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def frame(cmd, arg=b""):
    payload = bytes([0x80, 1 + len(arg), 0x00, cmd]) + arg
    return bytes([0xAA, 0x55]) + payload + bytes([_crc8(payload)])


START = frame(0x01)
STOP = frame(0x02)


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


def esp_stat(p):
    p.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(p, 0.7).decode("utf-8", "replace"))
    return dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM27")
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--listen", type=float, default=3.0)
    a = ap.parse_args()

    try:
        pu = serial.Serial(a.usb, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.usb}：{e}")
        return 2
    pe = None
    try:
        pe = serial.Serial(a.esp, 115200, timeout=0.2)
        pe.write(b"\r\n")
        drain(pe, 0.3)
    except Exception as e:
        print(f"[WARN] 打不开 {a.esp}（{e}）⇒ 少一个旁证，SP32 rx 增量不可用")

    try:
        time.sleep(0.3)
        pu.reset_input_buffer()
        e0 = esp_stat(pe) if pe else None
        if e0:
            print(f"ESP32 基线: rx={e0['rx']} (全零则 485 未初始化，Δrx 无证据力)")
            if e0["rx"] == 0 and e0["tx"] == 0:
                pe.write(b"485-start\r\n")
                drain(pe, 2.0)
                e0 = esp_stat(pe) or e0
                print(f"  485-start 后: rx={e0['rx']}")

        # ---------- ① ASCII test snapshot ----------------------------------
        print(f"\n① {a.usb} 发 ASCII 「test snapshot」×2（期望 80 B 快照帧 AA 55 03 4A）")
        pu.reset_input_buffer()
        for _ in range(2):
            pu.write(b"test snapshot\r\n")
            pu.flush()
            time.sleep(0.5)
        r1 = drain(pu, a.listen)
        n_snap1 = r1.count(b"\xAA\x55\x03")
        n_disp1 = r1.count(b"\xAA\x55\x01")
        print(f"   收到 {len(r1)} B；快照帧头 AA5503 {n_snap1} 次；显示帧头 AA5501 {n_disp1} 次")
        if r1:
            print("   预览: " + r1[:60].decode("ascii", "replace")
                  .replace("\r", "\\r").replace("\n", "\\n"))

        # ---------- ② 二进制 START -----------------------------------------
        print(f"\n② {a.usb} 发二进制 START ×3（{START.hex(' ').upper()}）")
        pu.reset_input_buffer()
        for _ in range(3):
            pu.write(START)
            pu.flush()
            time.sleep(0.35)
        r2 = drain(pu, a.listen)
        n_disp2 = r2.count(b"\xAA\x55\x01")
        n_snap2 = r2.count(b"\xAA\x55\x03")
        print(f"   收到 {len(r2)} B；显示帧头 AA5501 {n_disp2} 次；快照帧头 AA5503 {n_snap2} 次")

        e1 = esp_stat(pe) if pe else None
        pu.write(STOP)
        pu.flush()

        print("\n" + "=" * 62)
        if e1:
            d = e1["rx"] - e0["rx"]
            print(f"旁证：ESP32 rx {e0['rx']} → {e1['rx']} (Δ{d:+d} B)")
            print("      本次共发了 ASCII 30 B + 二进制 21 B = 51 B")
        else:
            d = None
        print()
        if r1 or r2:
            print("[判定] ✅ STM32 有回话")
            if r1:
                print("       ASCII 通道通 ⇒ 下行整链活")
            if r2:
                print("       二进制通道通 ⇒ START 被受理")
            return 0
        print("[判定] ❌ STM32 零回应（两条通道都死）")
        if d is not None and d >= 40:
            print(f"       但 ESP32 旁听到 Δ{d} B ⇒ 字节确实上了总线")
            print("       ⇒ 断点在「总线 → UT-2211 接收 → MAX3232 R1 → PD2」")
            print("       ⇒ 优先：给 UT-2211 断电重上（清自动方向锁）；再不行用 TTL-USB 直连 CN9 复验 R1+PD2")
        elif d is not None:
            print(f"       ESP32 旁听只有 Δ{d} B（期望 ~51）⇒ 总线本身没把这些字节送出去")
            print("       ⇒ 断点在 COM27 的发送侧/接线")
        return 1
    finally:
        for p in (pu, pe):
            if p is None:
                continue
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
