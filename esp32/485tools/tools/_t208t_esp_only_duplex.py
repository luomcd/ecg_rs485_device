#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208t_esp_only_duplex.py —— 只剩 ESP32 一个主站时的双通道判定

拓扑（11:14 猛哥改的）
----------------------
    ESP32 SP3485 → UT-2211 → 232转TTL(MAX3232) → STM32 UART5
    🛑 PC 的 USB-485（COM26）**已从总线摘除** ⇒ 总线只有一个主站

为什么这个配置有价值
--------------------
之前所有「ESP32 上电 ⇒ 下行断」的观测，COM26 都还并挂在总线上。
现在把 COM26 摘掉，若下行恢复 ⇒ 凶手是 COM26 那个转换器（终端/偏置/DE 锁），
而不是 ESP32。这是一直没做过的单变量。

判据设计（不依赖"发完看到帧"的假阳性）
------------------------------------
先测基线趋势：1.2 s 内 ESP32 的 frames 计数有没有涨。
    A) 基线在涨 ⇒ STM32 本来就在流 ⇒ START 不能当证据，改用 **STOP 能否冻结**判断行
    B) 基线不动 ⇒ 空闲 ⇒ **START 能否起流**判断行；随后再用 STOP 验反向

    START 与 STOP 都有静默语义（不回字节），所以唯一判据是 **frames 计数是否变化**。
    ESP32 的 `485-stat` 里 frames 就是它自己解析出来的完整显示帧数，干净可用。

用法
----
    python _t208t_esp_only_duplex.py --esp COM35
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
            time.sleep(0.01)
    return bytes(buf)


def stat(p):
    p.write(b"485-stat\r\n")
    txt = drain(p, 0.7).decode("utf-8", "replace")
    m = RE_STAT.search(txt)
    return (dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None), txt


def show(tag, s0, s1):
    if not (s0 and s1):
        print(f"   {tag}: stat 读不到")
        return 0
    d = s1["frames"] - s0["frames"]
    print(f"   {tag}: frames {s0['frames']} → {s1['frames']} (Δ{d:+d})  "
          f"rx Δ{s1['rx']-s0['rx']:+d}  crc_err Δ{s1['crc']-s0['crc']:+d}  "
          f"resync Δ{s1['resync']-s0['resync']:+d}  bad_len Δ{s1['bad_len']-s0['bad_len']:+d}")
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
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)

        s, raw = stat(p)
        if s is None:
            print("[FAIL] 不应答 485-stat")
            print("回显: " + raw.strip().replace("\r\n", " | ")[:300])
            return 3
        if s["rx"] == 0 and s["tx"] == 0 and s["frames"] == 0:
            print("⚠ 485 未初始化，执行 485-start")
            p.write(b"485-start\r\n")
            drain(p, 2.0)
            s, _ = stat(p)
        print(f"起始: rx={s['rx']} tx={s['tx']} frames={s['frames']}")

        # ---------- 基线趋势 -------------------------------------------------
        print("\n① 基线趋势（1.2 s，判断 STM32 现在是否已在出帧）")
        t0, _ = stat(p)
        time.sleep(1.2)
        t1, _ = stat(p)
        trend = (t1["frames"] - t0["frames"]) if (t0 and t1) else 0
        print(f"   1.2 s 内 frames 变化 {trend:+d}"
              + ("  ⇒ 已在流" if trend > 0 else "  ⇒ 空闲"))

        downlink = None
        uplink = (trend > 0)          # 已在流本身就证明上行通

        if trend > 0:
            # ---- A 路：用 STOP 判断行 --------------------------------------
            print("\n② 已在流 ⇒ 用 STOP 判下行（连发 3 次，然后量 8 s）")
            for _ in range(3):
                p.write(b"485-send stop\r\n")
                drain(p, 0.35)
            sA, _ = stat(p)
            time.sleep(8.0)
            sB, _ = stat(p)
            d = show("STOP 后", sA, sB)
            downlink = (d == 0)
            print(f"   ⇒ {'帧流已冻结，STOP 被受理（下行通）' if downlink else '帧流仍在跑，STOP 未被受理（下行不通）'}")
        else:
            # ---- B 路：用 START 判断行 -------------------------------------
            print("\n② 空闲 ⇒ 用 START 判下行（连发 3 次，然后量 6 s）")
            for _ in range(3):
                p.write(b"485-send start\r\n")
                drain(p, 0.35)
            sA, _ = stat(p)
            time.sleep(6.0)
            sB, _ = stat(p)
            d = show("START 后", sA, sB)
            downlink = (d > 3)
            uplink = (d > 3)
            print(f"   ⇒ {'起了帧流，START 被受理（下行通）' if downlink else '没有任何帧（下行不通）'}")

            if downlink:
                print("\n③ 反向验证：发 STOP，看能不能停")
                for _ in range(3):
                    p.write(b"485-send stop\r\n")
                    drain(p, 0.35)
                sC, _ = stat(p)
                time.sleep(6.0)
                sD, _ = stat(p)
                dd = show("STOP 后", sC, sD)
                print(f"   ⇒ {'已停' if dd == 0 else f'仍在跑（Δ{dd}），STOP 没被受理'}")

        sE, _ = stat(p)
        print("\n" + "=" * 64)
        print(f"累计: rx={sE['rx']} tx={sE['tx']} frames={sE['frames']} "
              f"crc_err={sE['crc']} resync={sE['resync']} bad_len={sE['bad_len']}")
        print()
        print(f"  上行（STM32→总线→ESP32）：{'✅ 通' if uplink else '❌ 不通'}")
        print(f"  下行（ESP32→总线→STM32）：{'✅ 通' if downlink else '❌ 不通'}")
        print()
        if downlink and uplink:
            print("  ✅ 双通道通 —— 且此时 PC 的 USB-485 已摘除")
            print("     ⇒ 之前的『ESP32 上电就断』是 COM26 并挂引起的，不是 ESP32 的问题")
            return 0
        print("  ❌ 至少一个方向不通（见上表）；注意此刻总线上只有 ESP32，没有 COM26 干扰")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
