#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208l_reset_banner_watch.py —— 抓 STM32 复位横幅，判「STM32→232→UT-2211→总线」上行死活

为什么用横幅当探针
------------------
`comm_port.c:153-161` 上电/复位时无条件吐 42 B：
    \r\n[uart5] ready 115200 8N1  PC12=TX PD2=RX\r\n
这是 STOP 态下 STM32 唯一自发字节，不依赖 AFE、不依赖任何下行命令。
⇒ 复位时收到横幅 = 上行整条链路活着；一个字节都没有 = 上行断了（供电/接线/UT-2211）。

用法（本脚本先跑起来，再去按 STM32 的复位键）
    python _t208l_reset_banner_watch.py --port COM27 --dur 40
"""
import argparse
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM27")
    ap.add_argument("--dur", type=float, default=40.0)
    a = ap.parse_args()

    try:
        p = serial.Serial(a.port, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.port}：{e}")
        return 2

    print(f"监听 {a.port} 共 {a.dur:.0f} s —— 现在请按 STM32 的复位键（RESET / NRST）")
    print("  期望：≈42 B ASCII 横幅  「[uart5] ready 115200 8N1  PC12=TX PD2=RX」+「afe boot init:N」")
    print("-" * 62)

    buf = bytearray()
    marks = []
    t0 = time.time()
    last_report = 0.0
    try:
        p.reset_input_buffer()
        while time.time() - t0 < a.dur:
            n = p.in_waiting
            if n:
                chunk = p.read(n)
                buf += chunk
                marks.append((time.time() - t0, len(chunk)))
            else:
                time.sleep(0.01)
            el = time.time() - t0
            if el - last_report > 5.0:
                last_report = el
                print(f"  [{el:5.1f}s] 累计 {len(buf)} B")
    finally:
        try:
            p.close()
        except Exception:
            pass

    print("-" * 62)
    print(f"总计 {len(buf)} B")
    if not buf:
        print("[判定] ❌ 复位后零字节 ⇒ STM32 的上行完全不出来了")
        print("       ⇒ 查：STM32 是否真在跑（STLink 能不能连上）／UT-2211 是否还接着")
        print("             ／TTL转232 模块的 3.3V-5V 供电／CN9 排线是否被并挂时碰掉")
        return 1

    txt = buf.decode("ascii", errors="replace")
    print("文本预览:")
    print("  " + txt.replace("\r", "\\r").replace("\n", "\\n")[:600])
    print("\n分段到达时刻: " + ", ".join(f"{t:.2f}s/+{n}B" for t, n in marks[:12]))

    up = "ready 115200" in txt
    boot = "afe boot init" in txt
    print()
    if up:
        print("[判定] ✅ 收到 uart5 就绪横幅 ⇒ STM32→232→UT-2211→总线 上行链路活")
    else:
        print("[判定] ⚠ 有字节但不是横幅（可能是帧流残留）")
    if boot:
        print("       ✅ 也收到 afe boot init ⇒ T207b 上电写默认表在跑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
