#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209h_tx_rx_codebook.py —— 「发 X 收 Y」对照表：一次定死极性 / 波特率 / 位偏移

为什么这组图案
--------------
    0x55 = 0101_0101      0xAA = 1010_1010   ← **互为反码**，极性一翻就互相换位
    0xFF = 1111_1111      0x00 = 0000_0000   ← 幅度/钳位方向
    四组一起看，能同时区分：

    收到的字节        | 结论
    ------------------|------------------------------------------------
    = 发出去的        | 极性正确、波特率正确
    = 发出去的反码    | ★**极性反了**（0x55↔0xAA、0xFF↔0x00 互换）
    其他固定值        | 波特率不匹配 / 位偏移（用数值反推比值）
    每次事件都很碎    | 每字节都帧错（FE），说明送进 PD2 的波形不成字

两个判据
--------
    ① `s_rx_pub[idx]` 里的原始字节（`comm_port.c:223` 抄进去的，主循环不清它）
    ② `comm_rx_events` 增量：40 字节若正常 ⇒ 只 +1；每字节都错 ⇒ +几十

用法
----
    python _t209h_tx_rx_codebook.py --pc COM27
"""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

import serial

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("d", _here / "_t209d_alive_check.py")
_d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_d)
rd = _d._cli_read

PUB = 0x200010B4
IDX = 0x200010B0
WORK = 0x20001370
LOSS_CNT = 0x20001430
EVT_CNT = 0x200011B4

N = 40          # 每种图案发多少字节


def snapshot():
    import struct
    idx = rd(IDX, 1)[0]
    pub = rd(PUB, 256)
    work = rd(WORK, 128)
    evt = struct.unpack("<I", rd(EVT_CNT, 4))[0]
    loss = struct.unpack("<I", rd(LOSS_CNT, 4))[0]
    return {"idx": idx, "pub_last": pub[idx * 128:(idx + 1) * 128],
            "pub0": pub[:128], "work": work, "evt": evt, "loss": loss}


def run_len(buf, target):
    """buf 从头开始连续等于 target 的个数"""
    n = 0
    for b in buf:
        if b == target:
            n += 1
        else:
            break
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", default="COM27")
    ap.add_argument("--n", type=int, default=N)
    a = ap.parse_args()

    p = serial.Serial(a.pc, 115200, timeout=0.1)
    p.reset_input_buffer()
    time.sleep(0.3)

    print(f"每种图案发 {a.n} 字节（115200 下 ≈{a.n * 10 / 115200 * 1000:.1f} ms 连续）")
    print("对照表：0x55 ↔ 0xAA 互为反码；0xFF ↔ 0x00 互为反码\n")

    rows = []
    for pat in (0x55, 0xAA, 0xFF, 0x00):
        s0 = snapshot()
        p.reset_input_buffer()
        p.write(bytes([pat]) * a.n)
        p.flush()
        time.sleep(0.8)
        p.read(p.in_waiting or 1)          # 吸掉自己发出去的回波/尾巴
        time.sleep(0.2)
        s1 = snapshot()

        de = s1["evt"] - s0["evt"]
        dl = s1["loss"] - s0["loss"]
        buf = s1["pub_last"]
        head = " ".join(f"{x:02X}" for x in buf[:16])
        rows.append((pat, buf, de, dl))
        print(f"发 0x{pat:02X} ×{a.n}")
        print(f"   Δevents={de:+d}  Δloss={dl:+d}   s_rx_pub[idx][0:16] = {head}")
        for cand in (pat, pat ^ 0xFF):
            r = run_len(buf, cand)
            if r:
                print(f"   连续 0x{cand:02X} 开头 {r} 个")
        print()

    p.close()

    # ---------------- 判定 ----------------
    print("=" * 70)
    verdict = []
    for pat, buf, de, dl in rows:
        same = run_len(buf, pat)
        inv = run_len(buf, pat ^ 0xFF)
        verdict.append((pat, buf, de, dl, same, inv))

    n_ok = sum(1 for _, _, _, _, same, _ in verdict if same >= 10)
    n_inv = sum(1 for _, _, _, _, _, inv in verdict if inv >= 10)

    if n_ok >= 3:
        print("[判定] ✅ **极性正确**（收到的字节 = 发出去的字节）")
        print("       ⇒ 下行通路是通的；不通的话问题在帧解析/CRC，不在物理层")
    elif n_inv >= 3:
        print("[判定] ★★ **极性反了**（收到的字节 = 发出字节的反码）")
        print("       ⇒ 0x55 收成 0xAA、0xFF 收成 0x00 —— 链路里多了一级反相")
        print("       ⇒ 查：DB9 pin2/pin3 是否接反、模块 T/R 通道是否用错")
    else:
        print("[判定] ⚠ 收到的既不是原码也不是反码，看下面每种的实际值：")
        for pat, buf, de, dl, same, inv in verdict:
            print(f"       发 0x{pat:02X} → 收 0x{buf[0]:02X}×{run_len(buf, buf[0])}"
                  f"   (Δevents={de:+d} Δloss={dl:+d})")
        print("       ⇒ 若收到的值恒定且与图案无关 ⇒ 波特率严重不匹配或电平幅度不足")
        print("       ⇒ 若 Δevents 巨大（≈字节数）⇒ 每个字节都帧错，波形不成字")

    print("\n参考：40 字节正常应 Δevents=+1（一次性收满再 IDLE）；")
    print("      若 Δevents 接近 40 ⇒ 每字节都被拆成一次错误事件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
