"""T208j —— 在「STM32 正在发帧流」的条件下测 ESP32 下行（控制住 UT-2211 的供电变量）

背景（11-05 10:05 实测）：
    ESP32 发的 `AA 55 80 01 00 01 5D` 在总线上**逐字节正确**（COM27 收到两帧完整原样），
    但 UT-2211 不给 STM32；而同一个 UT-2211，COM27 发的 7 字节能通。
    ⇒ 唯一的差异变量是「发送那一刻 UT-2211 自身是否处于带电/可工作状态」。

本实验把该变量控住：
    ① COM27 发 START        ⇒ STM32 起采，TXD 持续跳变（UT-2211 必定带电）
    ② 确认帧流稳定（≈20 fps）
    ③ **ESP32 发 STOP**     ⇒ 若 UT-2211 能把它转给 STM32，帧流会停
    ④ COM27 再监听 2 s      ⇒ 帧率归零 = 穿过去了

判据：
    帧流停  ⇒ ESP32 下行在「UT-2211 带电」时可行 ⇒ 11-04 的电荷泵假说成立
    帧流不停 ⇒ ESP32 这条路与 UT-2211 供电无关，是别的问题
⚠ 半双工：RUN 态下行实测 ~50% 丢（MEMORY §10），STOP 连发 3 次、间隔 0.3 s。

用法：python _t208j_run_downlink.py --usb COM27 --esp COM35
"""
import argparse
import re
import sys
import time

import serial

RE_STAT = re.compile(r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)", re.S)
START = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
STOPB = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.005)
    return bytes(buf)


def count_frames(buf):
    n = i = 0
    while i + 59 <= len(buf):
        if buf[i] == 0xAA and buf[i + 1] == 0x55 and buf[i + 2] == 0x01 and buf[i + 3] == 0x35:
            n += 1
            i += 59
        else:
            i += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM27")
    ap.add_argument("--esp", default="COM35")
    a = ap.parse_args()

    try:
        pu = serial.Serial(a.usb, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.usb}：{e}")
        return 2
    try:
        pe = serial.Serial(a.esp, 115200, timeout=0.2)
    except Exception as e:
        pu.close()
        print(f"[FAIL] 打不开 {a.esp}：{e}")
        return 2

    try:
        time.sleep(0.3)
        pu.reset_input_buffer()
        pe.reset_input_buffer()
        pe.write(b"\r\n")
        drain(pe, 0.4)
        pe.write(b"485-stat\r\n")
        if "未初始化" in drain(pe, 0.7).decode("utf-8", "replace"):
            pe.write(b"485-start\r\n")
            drain(pe, 1.8)

        print("① COM27 发 START，让 STM32 起采")
        pu.write(START)
        pu.flush()
        time.sleep(0.5)
        pu.reset_input_buffer()
        seg1 = drain(pu, 2.0)
        n1 = count_frames(seg1)
        print(f"   2 s 内 {n1} 帧 = {n1/2.0:.2f} fps")
        if n1 < 20:
            print("[中止] 帧流没起来，先修上行再谈下行")
            return 1

        print("\n② 此时 STM32 TXD 在跳变（UT-2211 必定带电），让 ESP32 发 STOP ×3")
        frames_before = n1
        for k in range(3):
            pe.write(b"485-send stop\r\n")
            drain(pe, 0.45)
            seg = drain(pu, 0.9)
            n = count_frames(seg)
            print(f"   第 {k+1} 次 STOP 后 0.9 s 内 {n} 帧 = {n/0.9:.2f} fps")
            if n <= 3:
                print("\n" + "=" * 62)
                print(f"*** ESP32 的 STOP 生效了（第 {k+1} 次）—— 帧流被停掉 ***")
                print("*** ⇒ ESP32 下行在「UT-2211 带电」时可穿过去 ***")
                print("*** ⇒ 11-04 的电荷泵假说成立 ***")
                print("=" * 62)
                return 0

        print("\n③ 收尾复核 2 s")
        tail = drain(pu, 2.0)
        n2 = count_frames(tail)
        print(f"   {n2} 帧 = {n2/2.0:.2f} fps（仍 >10 ⇒ STOP 没生效）")
        pu.write(STOPB)
        pu.flush()

        print()
        print("[判定] ❌ ESP32 的 STOP 在「UT-2211 带电」时也穿不过去")
        print("       ⇒ 与 UT-2211 供电状态无关，问题在 ESP32→UT-2211 的 485 段")
        return 1
    finally:
        for p in (pe, pu):
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
