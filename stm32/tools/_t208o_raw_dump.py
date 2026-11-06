#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208o_raw_dump.py —— 原始字节抓取，把「横幅 + 乱码」逐字节摊开看

背景
----
猛哥 11-05 10:38 反馈：重新插拔 485/USB 后，复位 STM32 能收到完整数据，
但**完整数据之后总跟着一段乱码**。

这是判「总线空闲无偏置 ⇒ 收噪声」的标准场景，所以要把乱码**逐字节**看清楚：
    - 乱码是 0x00 / 0xFF 一片        ⇒ 空闲差分未定义，接收器在乱翻（缺 fail-safe 偏置）
    - 乱码是 0xFE / 半帧残片          ⇒ 上一帧被截断，波特率/关断沿问题
    - 乱码里能认出 AA 55             ⇒ 是残留的帧流，不是噪声

用法（脚本先跑，再去按 STM32 复位键）
    python _t208o_raw_dump.py --port COM26 --dur 25
"""
import argparse
import sys
import time

import serial


def seg_split(buf):
    """按可打印 ASCII 切段，返回 [(是否ASCII, bytes), ...]"""
    out = []
    cur = bytearray()
    cur_ascii = None
    for b in buf:
        is_ascii = 0x20 <= b <= 0x7E or b in (0x0D, 0x0A, 0x09)
        if cur_ascii is None:
            cur_ascii = is_ascii
        if is_ascii != cur_ascii:
            out.append((cur_ascii, bytes(cur)))
            cur = bytearray()
            cur_ascii = is_ascii
        cur.append(b)
    if cur:
        out.append((cur_ascii, bytes(cur)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM26")
    ap.add_argument("--dur", type=float, default=25.0)
    a = ap.parse_args()

    p = serial.Serial(a.port, 115200, timeout=0.05)
    try:
        p.reset_input_buffer()
        print(f"监听 {a.port} 共 {a.dur:.0f} s —— 现在按 STM32 复位键（可按多次）")
        print("-" * 66)
        buf = bytearray()
        chunks = []
        t0 = time.time()
        while time.time() - t0 < a.dur:
            n = p.in_waiting
            if n:
                c = p.read(n)
                buf += c
                chunks.append((time.time() - t0, c))
            else:
                time.sleep(0.008)
    finally:
        try:
            p.close()
        except Exception:
            pass

    print(f"总计 {len(buf)} B，分 {len(chunks)} 次到达")
    if not buf:
        print("[判定] 零字节 —— 复位后什么都没上来")
        return 1

    print("\n到达时刻与长度：" + ", ".join(f"{t:.2f}s/+{len(c)}B" for t, c in chunks[:20]))

    print("\n按块（含时间戳）:")
    for t, c in chunks[:20]:
        print(f"  [{t:6.2f}s] +{len(c):3d}B  {c.hex(' ').upper()}")

    print("\n按 ASCII / 非 ASCII 切段:")
    for is_ascii, seg in seg_split(bytes(buf)):
        if is_ascii:
            print(f"  [ASCII {len(seg):3d}B] " + seg.decode("ascii", "replace")
                  .replace("\r", "\\r").replace("\n", "\\n"))
        else:
            head = seg[:40]
            print(f"  [BIN   {len(seg):3d}B] " + head.hex(" ").upper()
                  + (" ..." if len(seg) > 40 else ""))

    txt = buf.decode("latin-1")
    print()
    if "ready 115200" in txt:
        print("[判定] ✅ 横幅到达 ⇒ 上行链路通")
    nz = sum(1 for b in buf if b == 0x00)
    ff = sum(1 for b in buf if b == 0xFF)
    hdr = buf.count(b"\xAA\x55")
    print(f"       0x00 计数 {nz}，0xFF 计数 {ff}，AA55 帧头 {hdr} 次")
    if nz + ff >= 3:
        print("       ⚠ 大量 0x00/0xFF ⇒ 空闲态差分未定义，接收器在乱翻（缺 fail-safe 偏置）")
    if hdr:
        print("       ⚠ 出现 AA55 ⇒ 乱码里有帧流残留，不是纯噪声")
    return 0


if __name__ == "__main__":
    sys.exit(main())
