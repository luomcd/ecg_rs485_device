#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209a_reset_snap_cycle.py —— 全自动「初始化 → ST-Link 复位 STM32 → 抓横幅 → 发快照」

为什么现在能全自动
------------------
ST-Link V3SET 已接上（SN 002100083156501920323443），`STM32_Programmer_CLI -c port=SWD -rst`
可以随时复位 STM32 ⇒ 不需要人工按复位键，也不会再出现「复位发生在 485-start 之前」
导致横幅丢失 / 计数被清零的假象（11:35、11:37 各踩过一次）。

⚠ SWD attach 会 halt 内核（T205 已证会让 UART5 丢字节）⇒ 只在复位这一下用，
  复位完成后立刻 detach，不在测量窗口内挂着。

流程
----
    ① ESP32 `485-start`（必须最先，之后不能再调，否则清零统计）
    ② 记基线 → ST-Link 复位 STM32 → 等 1.2 s
    ③ 读 485-stat：rx 增量应 ≈60 B（42 B 横幅 + afe boot init）
    ④ `485-send snap`（此时必在 STOP 态）→ 读快照回执
    ⑤ 汇总定性

用法
----
    python _t209a_reset_snap_cycle.py --esp COM35
"""
import argparse
import re
import subprocess
import sys
import time

import serial

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

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


def stm32_reset():
    """用 ST-Link 复位 STM32；返回 (ok, 摘要)"""
    try:
        r = subprocess.run([CLI, "-c", "port=SWD", "mode=normal", "-rst"],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, f"调用失败: {e}"
    out = r.stdout or ""
    ok = ("MCU Reset" in out) or ("Software reset is performed" in out)
    dev = ""
    m = re.search(r"Device name\s*:\s*(.+)", out)
    if m:
        dev = m.group(1).strip()
    return ok, dev or out.strip().splitlines()[-1][:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--settle", type=float, default=1.4)
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)

        print("① 485-start（最先执行；之后不再调用，避免清零统计）")
        cmd(p, "485-start", 2.5)
        s0 = stat(p)
        if s0 is None:
            print("[FAIL] 读不到 485-stat")
            return 2
        print(f"   基线: {s0}")

        print("\n② ST-Link 复位 STM32")
        ok, info = stm32_reset()
        print(f"   复位{'成功' if ok else '失败'}：{info}")
        if not ok:
            return 2
        time.sleep(a.settle)

        print("\n③ 读统计（期望 rx 增量 ≈60 B = 42 B 横幅 + afe boot init）")
        s1 = stat(p)
        d_rx = s1["rx"] - s0["rx"]
        d_rs = s1["resync"] - s0["resync"]
        d_bl = s1["bad_len"] - s0["bad_len"]
        print(f"   rx Δ{d_rx:+d} B   resync Δ{d_rs:+d}   bad_len Δ{d_bl:+d}   "
              f"frames Δ{s1['frames']-s0['frames']:+d}")
        up = d_rx >= 30

        print("\n④ 485-send snap（STM32 刚复位 ⇒ STOP 态，状态门必过）")
        out = cmd(p, "485-send snap", 2.0)
        for line in out.splitlines():
            if line.strip():
                print("   " + line.strip())
        m = RE_SNAP_ACK.search(out)
        ack = int(m.group(1)) if m else -1

        s2 = stat(p)
        print(f"\n⑤ 事后: rx={s2['rx']} tx={s2['tx']} frames={s2['frames']} "
              f"snapshots={s2['snapshots']} bad_len={s2['bad_len']} crc_err={s2['crc']}")

        print("\n" + "=" * 66)
        print(f"  上行（复位横幅）: {'✅ 通，rx +%d B' % d_rx if up else '❌ 未收到'}")
        print(f"  下行（0x10 快照）: {'✅ 通，回执 %d 帧' % ack if ack >= 1 else '❌ 不通（回执 0）'}")
        print()
        if up and ack >= 1:
            print("[结论] ✅ **双通道全通**")
            print("       ⇒ 0x10 被受理并回了 80 B 快照帧；ESP32↔STM32 链路在功能上闭合")
            return 0
        if up and ack == 0:
            print("[结论] ❌ 上行通、下行不通（且此刻必为 STOP 态，状态门已排除）")
            if d_bl > 0:
                print("       线上有字节但被打乱（bad_len 涨）⇒ 查 A/B 极性/终端/波特率")
            else:
                print("       线上没有可解的字节 ⇒ UT-2211 的 232 发送 / MAX3232 R1 / PD2")
                print("       下一步：量 UT-2211 DB9 pin2(SOUT) 对 pin5，应为 −5~−10 V")
            return 1
        if not up and ack >= 1:
            print("[结论] ⚠ 快照回来了但横幅没收到 —— 罕见，先复查 rx 计数口径")
            return 1
        print("[结论] ❌ 双通道都不通 ⇒ 断点在 STM32 一侧（横幅都上不来）")
        return 2
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
