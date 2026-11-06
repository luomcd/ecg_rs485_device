#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208v_snap_probe.py —— 用 0x10 二进制快照命令判下行（T208 收敛判据）

为什么这条路最硬
----------------
    0x10 的回执是**只由命令生成**的 80 B 快照帧（TYPE=0x03, LEN=74）。
    显示帧流里永远不会出现 TYPE=0x03 ⇒ 帧流伪造不出这个回执。

前置
----
    STM32 必须处于 **STOP 态**（协议规定 RUN 态静默 —— 静默不是丢包）。
    本脚本先发 STOP（幂等、无回执）再发 SNAP，避免被 RUN 态门挡掉。

判读表
------
    快照回执 ≥1              ⇒ 下行通 ✅（铁证）
    回执 0 且 bad_len/crc_err 在涨 ⇒ 字节到了但被打乱（极性/终端/波特率）
    回执 0 且 两计数不动            ⇒ 字节没到 STM32（UT-2211 接收 / MAX3232 R1 / PD2）

用法
----
    python _t208v_snap_probe.py --esp COM35
"""
import argparse
import re
import sys
import time

import serial

RE_SNAP_ACK = re.compile(r"快照回执\s*(\d+)\s*帧")
RE_STAT = re.compile(
    r"frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)\s+seq_gaps=(\d+)")
RE_SNAPSHOTS = re.compile(r"snapshots=(\d+)")
RE_RX = re.compile(r"rx=(\d+)\s*B\s+tx=(\d+)")


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


def cmd(p, line, wait=1.2):
    p.write(line.encode() + b"\r\n")
    p.flush()
    return drain(p, wait).decode("utf-8", "replace")


def parse_stat(txt):
    d = {}
    m = RE_STAT.search(txt)
    if m:
        d.update(zip(("frames", "crc_err", "resync", "bad_len", "seq_gaps"),
                     (int(x) for x in m.groups())))
    m = RE_SNAPSHOTS.search(txt)
    if m:
        d["snapshots"] = int(m.group(1))
    m = RE_RX.search(txt)
    if m:
        d["rx"] = int(m.group(1))
        d["tx"] = int(m.group(2))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    a = ap.parse_args()

    try:
        p = serial.Serial(a.esp, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.esp}：{e}")
        return 2

    try:
        time.sleep(1.5)          # 烧录后刚复位，等它起完
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.6)

        print("① 485-start（初始化 485 源）")
        out = cmd(p, "485-start", 2.5)
        keep = [l for l in out.splitlines() if "485" in l or "ready" in l.lower()]
        print("   " + (" | ".join(keep[:4]) if keep else "（无相关回显）"))

        print("\n② 485-stat 基线")
        s0 = parse_stat(cmd(p, "485-stat", 1.0))
        print(f"   {s0}")

        print("\n③ 485-send stop ×2（确保 STOP 态；幂等、无回执）")
        for _ in range(2):
            o = cmd(p, "485-send stop", 0.6)
            print("   " + " | ".join(l.strip() for l in o.splitlines()
                                     if "STOP" in l or "485" in l)[:120])
            time.sleep(0.3)

        print("\n④ 485-send snap（0x10 读快照 —— 判据就在这一步）")
        out = cmd(p, "485-send snap", 2.0)
        for line in out.splitlines():
            if line.strip():
                print("   " + line.strip())

        m = RE_SNAP_ACK.search(out)
        ack = int(m.group(1)) if m else None

        print("\n⑤ 485-stat 事后")
        s1 = parse_stat(cmd(p, "485-stat", 1.0))
        print(f"   {s1}")

        print("\n" + "=" * 64)
        if ack is not None:
            print(f"  快照回执 = {ack} 帧")
            if ack >= 1:
                print("[判定] ✅ **下行通** —— 0x10 被 STM32 受理并回了快照帧")
                print("       ⇒ 整条下行链（总线→UT-2211 接收→232→MAX3232 R1→PD2）都是好的")
                return 0
            print("[判定] ❌ 下行不通（回执 0）")
            d_bad = s1.get("bad_len", 0) - s0.get("bad_len", 0)
            d_crc = s1.get("crc_err", 0) - s0.get("crc_err", 0)
            d_rx = s1.get("rx", 0) - s0.get("rx", 0)
            print(f"       坏 LEN Δ{d_bad}，CRC 错 Δ{d_crc}，rx Δ{d_rx} B")
            if d_bad > 0 or d_crc > 0:
                print("       ⇒ 线上有字节但被打乱：查 A/B 极性、终端、波特率")
            else:
                print("       ⇒ 字节根本没到 STM32：查 UT-2211 的 232 发送 / MAX3232 R1 / PD2")
                print("         （也可能 STM32 处在 RUN 态 —— 协议规定 RUN 态静默）")
            return 1
        print("[WARN] 没解析到「快照回执」，见上面原始回显")
        return 3
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
