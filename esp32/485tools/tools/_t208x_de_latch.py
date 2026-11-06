#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208x_de_latch.py —— 判 ESP32 的 DE 是否在发射后锁死在发送态

假说
----
若 Q1 自动方向电路在发射结束后**没有把 DE 拉回低**，ESP32 会持续在总线上
驱动"空闲 1"，于是：
    · UT-2211 的接收端被按在 1 ⇒ **下行永远收不到**（今天的现象）
    · ESP32 自己也会变聋（RE 跟随 DE 时）或至少读不到别人的数据

判据（不需要第三方观察者、不需要改接线）
--------------------------------------
    ① ESP32 发一帧（485-send snap，7 B）
    ② 请猛哥按 STM32 复位键 ⇒ 总线上会出现 42 B 横幅（已知可靠的上行探针）
    ③ 看 ESP32 的 rx 增量
         rx 涨 ≈60 B ⇒ **仍然能收** ⇒ DE 没锁死，假说否掉
         rx 纹丝不动 ⇒ **发射后变聋** ⇒ DE 锁死，就是它

对照组（脚本会先做）：发射**之前**的接收能力。若基线阶段本来就收不到，
那本脚本的结论无效（会明确报出来）。

用法（脚本先跑，再去按 STM32 复位键）
    python _t208x_de_latch.py --esp COM35 --dur 50
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
    m = RE_STAT.search(cmd(p, "485-stat", 0.9))
    return dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--dur", type=float, default=50.0)
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.5)

        s0 = stat(p)
        if s0 is None:
            print("[FAIL] 读不到 485-stat")
            return 2
        if s0["tx"] == 0 and s0["rx"] == 0:
            print("485 未初始化，先 485-start")
            cmd(p, "485-start", 2.5)
            s0 = stat(p) or s0
        print(f"基线: rx={s0['rx']} tx={s0['tx']} frames={s0['frames']}")

        # ---- ① 先让 ESP32 发射，给 DE 一个"该复位却没复位"的机会 ----------
        print("\n① ESP32 发一帧 485-send snap（若 DE 会锁死，此刻就锁上了）")
        out = cmd(p, "485-send snap", 1.5)
        for line in out.splitlines():
            if "SNAPSHOT" in line or "快照" in line:
                print("   " + line.strip())
        s1 = stat(p)
        print(f"   发射后: rx={s1['rx']} tx={s1['tx']}（tx 应 +7）")

        # ---- ② 等复位横幅 -------------------------------------------------
        print(f"\n② 监听 {a.dur:.0f} s —— 请现在按 STM32 复位键")
        print("   （预期总线上出现约 42 B ASCII 横幅 + afe boot init）")
        print("-" * 62)

        t0 = time.time()
        prev = stat(p) or s1
        hits = 0
        while time.time() - t0 < a.dur:
            time.sleep(1.0)
            s = stat(p)
            if s is None:
                continue
            if s["rx"] != prev["rx"]:
                hits += 1
                print(f"  [{time.time()-t0:5.1f}s] rx {prev['rx']} → {s['rx']} "
                      f"(Δ{s['rx']-s1['rx']:+d} vs 发射后)   frames={s['frames']}  "
                      f"resync={s['resync']}  bad_len={s['bad_len']}")
                prev = s

        sE = stat(p) or prev
        d_after_tx = sE["rx"] - s1["rx"]
        print("-" * 62)
        print(f"发射后累计 rx 增量：{d_after_tx:+d} B")
        print(f"最终: rx={sE['rx']} tx={sE['tx']} frames={sE['frames']} "
              f"resync={sE['resync']} bad_len={sE['bad_len']} crc_err={sE['crc']}")
        print()

        if d_after_tx >= 40:
            print("[判定] ✅ 发射之后仍能接收 ⇒ DE 没有锁死，假说否掉")
            print("       ⇒ 下行不通的责任在 UT-2211 接收侧或 232/PD2 段")
            return 0
        if hits > 0 and d_after_tx > 0:
            print(f"[判定] ⚠ 只收到 {d_after_tx} B（横幅约 60 B）⇒ 严重退化/丢字节")
            return 1
        print("[判定] ❌ 发射之后完全收不到 ⇒ **DE 在发射后锁死在发送态**")
        print("       ⇒ ESP32 持续占着总线驱动空闲 1 ⇒ UT-2211 永远收不到下行")
        print("       ⇒ 查板上 Q1 自动方向电路（RE/DE 的释放是否依赖 TXD 空闲）")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
