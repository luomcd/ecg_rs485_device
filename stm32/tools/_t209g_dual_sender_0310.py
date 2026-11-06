#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209g_dual_sender_0310.py —— 同一套接线、两个发送方，用 0x03/0x01/0x10 三探针

现场接线（猛哥 2026-11-05 14:02 换成）
--------------------------------------
    STM32 UART5 --(TTL)--> 232电平转换 --[RS232]--> UT-2211 --[485 A/B]--> CH340 (COM27)
                                                                  └─ ESP32 的 A/B 也并着

为什么用三条命令而不是只测 0x10
-------------------------------
    0x10 在 RUN 态**静默**（`vcomcmd.c:652` `if (_bconvstart) return;`）⇒ 会假阴性。
    0x03 RESET **不加状态门**（`vcomcmd.c:662`），RUN/STOP 都回 80 B 快照帧。
    0x01 START 的隐式 ACK 是**帧流起**，效果最大、最不可能被误读。

两个判据同时拿
--------------
    ① 线上：COM27 收到什么（快照帧 / 帧流 / 什么都没有）
    ② 芯片内：ST-Link 读 `comm_rx_events` / `comm_rx_loss` / `_bconvstart` 增量
       ★只有这个数能区分「字节没到 PD2」和「到了但被解析丢掉」
         （ESP32 的 bad_len/crc_err 是它自己收帧的数，作不了证）

用法
----
    python _t209g_dual_sender_0310.py --pc COM27 --esp COM35
"""
import argparse
import importlib.util
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import serial

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

# 复用 _t209c 的 RAM 读法（同一份符号表）
_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("m209c", _here / "_t209c_stm32_rx_counter.py")
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
read_ram = _m.read_ram
fmt_ram = _m.fmt

F_RESET  = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x03, 0x53])
F_SNAP   = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x10, 0x2A])
F_START  = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
F_STOP   = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])


def stm32_reset():
    r = subprocess.run([CLI, "-c", "port=SWD", "mode=normal", "-rst"],
                       capture_output=True, timeout=60)
    t = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    return ("MCU Reset" in t) or ("Software reset is performed" in t)


def grab(p, dur):
    """在 dur 秒内尽量多收，返回 (总字节, 前 96 字节)"""
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.01)
    return bytes(buf), bytes(buf[:96])


def hexs(b):
    return " ".join(f"{x:02X}" for x in b) if b else "（无）"


def count_frames(b):
    """粗数 59 B 显示帧（AA 55 01 35 00）"""
    return b.count(bytes([0xAA, 0x55, 0x01, 0x35, 0x00]))


def count_snapshots(b):
    """数 80 B 快照帧（AA 55 03 4A 00）"""
    return b.count(bytes([0xAA, 0x55, 0x03, 0x4A, 0x00]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", default="COM27")
    ap.add_argument("--esp", default="COM35")
    a = ap.parse_args()

    pc = serial.Serial(a.pc, 115200, timeout=0.1)
    pc.reset_input_buffer()

    def step(title):
        print("\n" + "-" * 70)
        print(title)

    try:
        step("① 基线（先读芯片内计数）")
        r0 = read_ram()
        print("   " + fmt_ram(r0))

        step("② ST-Link 复位 STM32 → 看上行横幅（上行健康探针）")
        print(f"   复位{'成功' if stm32_reset() else '失败'}")
        time.sleep(1.6)
        d, head = grab(pc, 0.6)
        print(f"   COM27 收到 {len(d)} B：{hexs(head)}")
        up_ok = len(d) > 30
        r1 = read_ram()
        print("   " + fmt_ram(r1))
        print(f"   ⇒ 上行 {'✅ 通' if up_ok else '❌ 不通'}（rx_events Δ{r1['rx_events'] - r0['rx_events']:+d}）")

        # ---------- A: CH340 发 ----------
        step("③ [A] CH340 发 0x03 RESET ×3（不加状态门，STOP/RUN 都回快照）")
        pc.reset_input_buffer()
        for _ in range(3):
            pc.write(F_RESET); pc.flush(); time.sleep(0.35)
        d, head = grab(pc, 1.8)
        n_snap = count_snapshots(d)
        print(f"   COM27 收到 {len(d)} B，快照帧 {n_snap} 个：{hexs(head)}")
        r2 = read_ram()
        print("   " + fmt_ram(r2))
        de_A = r2["rx_events"] - r1["rx_events"]
        A_ok = n_snap > 0
        print(f"   ⇒ 线上 {'✅ 有快照回执' if A_ok else '❌ 无回执'}；芯片 rx_events Δ{de_A:+d}"
              + ("（**字节到了 PD2**）" if de_A > 0 else "（**一个字节都没到 PD2**）"))

        step("④ [A2] CH340 发 0x01 START（隐式 ACK＝帧流起，效果最大）")
        pc.reset_input_buffer()
        pc.write(F_START); pc.flush()
        d, head = grab(pc, 2.5)
        n_fr = count_frames(d)
        print(f"   COM27 收到 {len(d)} B，显示帧 {n_fr} 个")
        r3 = read_ram()
        print("   " + fmt_ram(r3))
        de_A2 = r3["rx_events"] - r2["rx_events"]
        print(f"   ⇒ {'✅ 帧流起来了' if n_fr > 5 else '❌ 帧流没起'}；"
              f"rx_events Δ{de_A2:+d}；bconvstart={r3['bconvstart']}")

        if n_fr > 5:
            pc.write(F_STOP); pc.flush()
            time.sleep(1.2)
            pc.reset_input_buffer()

        # ---------- B: ESP32 发 ----------
        step("⑤ [B] 同一套接线，换 ESP32 发 0x03 RESET ×3")
        r4 = read_ram()
        print("   " + fmt_ram(r4))
        esp = serial.Serial(a.esp, 115200, timeout=0.2)
        # 🛑 开端口会拉 DTR/RTS ⇒ ESP32 复位；放掉并等它起来，**同一会话内**做完
        esp.setDTR(False); esp.setRTS(False)
        time.sleep(2.5)
        esp.reset_input_buffer(); esp.write(b"\r\n"); time.sleep(0.4)
        esp.reset_input_buffer()
        esp.write(b"485-start\r\n"); time.sleep(2.2)
        out = esp.read(esp.in_waiting or 1).decode("utf-8", "replace")
        print("   485-start：" + (" | ".join(l.strip() for l in out.splitlines()
                                             if "485 源" in l or "UART" in l) or "（无回显）"))
        esp.reset_input_buffer()
        pc.reset_input_buffer()
        for _ in range(3):
            esp.write(b"485-send snap\r\n")
            esp.flush()
            time.sleep(0.35)
        time.sleep(0.3)
        d, head = grab(pc, 2.0)
        n_snap_b = count_snapshots(d)
        print(f"   COM27 收到 {len(d)} B，快照帧 {n_snap_b} 个：{hexs(head)}")
        st = esp.read(esp.in_waiting or 1).decode("utf-8", "replace")
        m = re.search(r"tx=(\d+)\s*B", st) if st else None
        print(f"   ESP32 tx = {m.group(1) if m else '?'} B")
        r5 = read_ram()
        print("   " + fmt_ram(r5))
        de_B = r5["rx_events"] - r4["rx_events"]
        B_ok = n_snap_b > 0
        esp.close()

        # ---------- 汇总 ----------
        print("\n" + "=" * 72)
        print(f"  上行（复位横幅到 COM27）      : {'✅ 通' if up_ok else '❌ 不通'}")
        print(f"  [A] CH340 发 0x03  → 快照回执 : {'✅ 有' if A_ok else '❌ 无'}"
              f"   rx_events Δ{de_A:+d}")
        print(f"  [A2] CH340 发 0x01 → 帧流     : {'✅ 起' if n_fr > 5 else '❌ 没起'}"
              f"   rx_events Δ{de_A2:+d}")
        print(f"  [B] ESP32 发 0x03  → 快照回执 : {'✅ 有' if B_ok else '❌ 无'}"
              f"   rx_events Δ{de_B:+d}")
        print()
        if A_ok and B_ok:
            print("[结论] ✅ 两个发送方都能把下行送进 STM32 —— 下行通，问题不复现")
        elif A_ok and not B_ok:
            print("[结论] ★CH340 能、ESP32 不能 ⇒ 问题在 ESP32 这一侧的上车方式")
            print("       （ESP32 自己发的东西 COM27 收得到 = T3a 已证，但进不了 STM32）")
        elif not A_ok and B_ok:
            print("[结论] ★ESP32 能、CH340 不能 ⇒ 问题在 CH340/UT-2211 方向切换")
        else:
            print("[结论] ❌ 两个发送方都进不了 STM32")
            if de_A == 0 and de_B == 0:
                print("       ⇒ 字节根本没到 PD2（UT-2211 的 232 输出 / 232 转接板 / PD2 走线）")
                print("       ⚠ 但上行横幅是通的 ⇒ 同一条 232 通路的上行方向好、下行方向坏，")
                print("         优先查 232 侧那两根线是否接在同一脚（模块 TX/RX 短到 UT-2211 的 pin2 上）")
            else:
                print("       ⇒ 字节到了但被解析丢掉（波特率/CRC/帧格式）")
        return 0
    finally:
        try:
            pc.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
