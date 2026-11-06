#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208s_dual_send_test.py —— 同一时刻内比较「COM26 发」与「ESP32 发」

前提（很重要）
--------------
STM32 必须处于**静默**状态。否则「发完看到帧流」全是假阳性（11-05 踩过）。
本脚本开头会先量 1 s 字节率，>0 就明确警告判据无效。

实验顺序（每一步都记录基线，避免前后串味）
------------------------------------------
    P0  COM26 字节率（应为 0）
    P1  COM26 发 ASCII `test snapshot` ×2   → 期望快照帧 AA 55 03 4A（80 B）
    P2  若 P1 起了帧流，先不管；直接测 COM26 二进制 START ×2 → 期望帧流
    P3  COM26 STOP ×3，等 3 s
    P4  ESP32 发 `485-send start` ×3        → 期望帧流
    P5  谁成功的就用谁发 STOP，验证能否停

判据
----
    快照帧 AA5503 只在收到命令后生成，帧流伪造不出来 ⇒ 铁证
    帧流出现 + 之前是静默的 ⇒ 该发送方下行通

用法
----
    python _t208s_dual_send_test.py --usb COM26 --esp COM35
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
SNAP = b"\xAA\x55\x03\x4A"
DISP = b"\xAA\x55\x01\x35"
START = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
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


def esp_stat(p):
    if p is None:
        return None
    p.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(p, 0.7).decode("utf-8", "replace"))
    return dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None


def frames(buf):
    n = 0
    i = 0
    while i + 59 <= len(buf):
        if buf[i:i + 4] == DISP:
            n += 1
            i += 59
        else:
            i += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM26")
    ap.add_argument("--esp", default="COM35")
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
        drain(pe, 0.4)
        e0 = esp_stat(pe)
        if e0 is None:
            print("[WARN] ESP32 不应答 485-stat，ESP32 部分跳过")
            pe, e0 = None, None
        else:
            if e0["rx"] == 0 and e0["tx"] == 0:
                pe.write(b"485-start\r\n")
                drain(pe, 2.0)
                e0 = esp_stat(pe) or e0
            print(f"ESP32 基线: rx={e0['rx']} tx={e0['tx']} frames={e0['frames']}")
    except Exception as e:
        print(f"[WARN] 打不开 {a.esp}（{e}），ESP32 部分跳过")
        pe, e0 = None, None

    try:
        time.sleep(0.2)
        pu.reset_input_buffer()

        # ---------------- P0 ------------------------------------------------
        r0 = drain(pu, 1.0)
        print(f"\nP0 {a.usb} 基线：{len(r0)} B/s")
        if len(r0) > 30:
            print("   ⚠ 总线不静默！后面的「看到帧流」不能当证据。请先复位 STM32。")
        else:
            print("   ✅ 静默，判据有效")

        # ---------------- P1: COM26 ASCII -----------------------------------
        print(f"\nP1 {a.usb} 发 ASCII「test snapshot」×2（快照帧是铁证）")
        pu.reset_input_buffer()
        for _ in range(2):
            pu.write(b"test snapshot\r\n")
            pu.flush()
            time.sleep(0.5)
        r1 = drain(pu, 1.5)
        n_snap = r1.count(SNAP)
        n_disp = r1.count(DISP)
        print(f"   收到 {len(r1)} B；快照 AA5503 {n_snap} 次；显示帧 {n_disp} 次")
        if r1 and not n_snap:
            print("   预览: " + r1[:60].decode("latin-1")
                  .replace("\r", "\\r").replace("\n", "\\n"))

        # ---------------- P2: COM26 二进制 START ---------------------------
        print(f"\nP2 {a.usb} 发二进制 START ×2")
        pu.reset_input_buffer()
        for _ in range(2):
            pu.write(START)
            pu.flush()
            time.sleep(0.4)
        r2 = drain(pu, 3.0)
        n2 = frames(r2)
        print(f"   收到 {len(r2)} B，完整帧 {n2}（{n2/3.0:.1f} fps）")

        # ---------------- P3: COM26 STOP -----------------------------------
        print(f"\nP3 {a.usb} 发 STOP ×3，等 3 s 看停不停")
        for _ in range(3):
            pu.write(STOP)
            pu.flush()
            time.sleep(0.3)
        r3 = drain(pu, 3.0)
        stopped = len(drain(pu, 1.0)) == 0
        print(f"   3 s 内残余 {len(r3)} B；随后 1 s 静默={stopped}")

        # ---------------- P4: ESP32 START ----------------------------------
        n4 = 0
        if pe is not None:
            print(f"\nP4 ESP32 发 485-send start ×3")
            pu.reset_input_buffer()
            for k in range(3):
                pe.write(b"485-send start\r\n")
                echo = drain(pe, 0.45).decode("utf-8", "replace")
                print(f"   第{k+1}次: " + echo.strip().replace("\r", "").replace("\n", " | ")[:100])
                time.sleep(0.25)
            r4 = drain(pu, 4.0)
            n4 = frames(r4)
            print(f"   {a.usb} 收到 {len(r4)} B，完整帧 {n4}（{n4/4.0:.1f} fps）")
        else:
            print("\nP4 跳过（ESP32 不可用）")

        e1 = esp_stat(pe)
        if pe is not None:
            pe.write(b"485-send stop\r\n")
            drain(pe, 0.4)
        pu.write(STOP)
        pu.flush()

        # ---------------- 汇总 ----------------------------------------------
        print("\n" + "=" * 64)
        print("汇总")
        print(f"  COM26 ASCII 快照回执 : {'✅ 有' if n_snap else '❌ 无'}")
        print(f"  COM26 二进制 START   : {'✅ 起采 {:.1f} fps'.format(n2/3.0) if n2 >= 3 else '❌ 无帧'}")
        print(f"  COM26 STOP 生效      : {'✅ 是' if stopped else '❌ 否'}")
        if pe is not None:
            print(f"  ESP32 START 起采     : {'✅ 起采 {:.1f} fps'.format(n4/4.0) if n4 >= 3 else '❌ 无帧'}")
            if e1 and e0:
                print(f"  ESP32 tx 增量        : {e1['tx']-e0['tx']:+d} B（期望 +21）")
                print(f"  ESP32 rx 增量        : {e1['rx']-e0['rx']:+d} B")
        print()
        if n_snap or n2 >= 3:
            print("[结论] COM26 下行通" + ("；ESP32 也通" if n4 >= 3 else "；ESP32 不通"))
            return 0
        if n4 >= 3:
            print("[结论] COM26 不通，但 ESP32 下行通 ⇒ 问题在 COM26 那个转换器的发送侧")
            return 1
        print("[结论] COM26 与 ESP32 双双不通 ⇒ 断点在 STM32 侧（UT-2211 接收 / MAX3232 R1 / PD2）")
        return 1
    finally:
        for p in (pe, pu):
            if p is None:
                continue
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
