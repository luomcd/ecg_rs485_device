#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208y_reset_then_snap.py —— 「先初始化 → 按复位 → 立刻发快照」的严格序列

为什么必须这个顺序
------------------
    ① `485-start` 会重启 485 源并**清零统计**。若复位发生在它之前，
       横幅被丢（UART1 还没装好）且计数被清零 ⇒ 看到 rx=0 纯属假象。
       11:35 就踩了这一下。
    ② 复位后 STM32 必定处在 **STOP 态** ⇒ 满足 `0x10` 快照命令的状态门。
       这样「快照回执 0」才真正等于「下行不通」。

一次运行完成三件事
------------------
    A 等横幅（rx 增量 ≥30 B）⇒ 证明上行通、且 STM32 在跑
    B 立刻发 `485-send snap`（STOP 态下必回 80 B）
    C 报快照回执数与各项统计增量

判读
----
    A 收到 + C 回执 ≥1  ⇒ 双通道通，收工
    A 收到 + C 回执 =0  ⇒ **下行不通**（上行已证明好，AFE 状态见横幅）
    A 没收到            ⇒ 上行也不通，先解决上行

用法（脚本先跑，脚本会提示你再按复位）
    python _t208y_reset_then_snap.py --esp COM35 --wait 90
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
    d = dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None
    if d is not None:
        mm = RE_SNAPSHOTS.search(txt)
        d["snapshots"] = int(mm.group(1)) if mm else -1
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--wait", type=float, default=90.0)
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.5)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.5)

        print("① 485-start（必须在复位之前，否则横幅丢失且计数清零）")
        cmd(p, "485-start", 2.5)
        s0 = stat(p)
        print(f"   基线: {s0}")

        print(f"\n② 请现在按 STM32 复位键（等 {a.wait:.0f} s）")
        print("   期望：rx 增量 ≈60 B（42 B 横幅 + afe boot init）")
        print("-" * 62)

        t0 = time.time()
        got = 0
        while time.time() - t0 < a.wait:
            time.sleep(0.4)
            s = stat(p)
            if s is None:
                continue
            d = s["rx"] - s0["rx"]
            if d != got:
                print(f"  [{time.time()-t0:5.1f}s] rx Δ{d:+d}  resync Δ{s['resync']-s0['resync']:+d}  "
                      f"bad_len Δ{s['bad_len']-s0['bad_len']:+d}")
                got = d
            if d >= 30:
                break

        sA = stat(p)
        up = (sA["rx"] - s0["rx"]) >= 30
        print("-" * 62)
        print(f"横幅结果: rx Δ{sA['rx']-s0['rx']:+d} B ⇒ 上行{'通' if up else '未收到'}")

        print("\n③ 立刻发 485-send snap（此刻必为 STOP 态）")
        out = cmd(p, "485-send snap", 2.0)
        for line in out.splitlines():
            if line.strip():
                print("   " + line.strip())
        m = RE_SNAP_ACK.search(out)
        ack = int(m.group(1)) if m else -1

        sB = stat(p)
        print(f"\n④ 事后统计: {sB}")
        print("\n" + "=" * 64)
        print(f"  上行横幅 : {'✅ 通' if up else '❌ 未收到'}")
        print(f"  快照回执 : {ack} 帧")
        print()
        if up and ack >= 1:
            print("[判定] ✅ **双通道全通** —— 0x10 被受理并回了 80 B 快照帧")
            return 0
        if up and ack == 0:
            print("[判定] ❌ 下行不通（上行已证明好，且 STM32 刚复位必在 STOP 态）")
            print("       ⇒ 断点在 总线→UT-2211 接收→232→MAX3232 R1→PD2 这一段")
            print("       ⇒ 下一步：量 UT-2211 的 DB9 pin2(SOUT) 对 pin5 的电压")
            return 1
        print("[判定] ⚠ 上行没收到横幅，先解决上行再谈下行")
        print("       ⇒ 横幅都上不来，说明 STM32→MAX3232(T1)→UT-2211(SIN) 断了")
        return 2
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
