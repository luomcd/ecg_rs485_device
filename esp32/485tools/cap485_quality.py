#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cap485_quality.py —— 485 链路接收质量测量（抓帧 + 误码分析）

用途：判断"丢帧/坏帧"发生在**物理层**（位错误）还是**协议上层**（丢字节错位），
以及对比不同接收端（ESP32 UART1 / PC 485-USB）的差异。

用法
----
    py cap485_quality.py                        # 抓 COM27 60 s 并分析
    py cap485_quality.py --port COM27 --secs 30
    py cap485_quality.py --file cap.bin         # 只分析已有抓包
    py cap485_quality.py --raw cap.bin          # 抓下来不分析

判读要点（🛑 别搞混）
---------------------
· **CRCBAD 且 LEN 正确** ⇒ 字节收到了、某些 bit 翻了 ⇒ **物理层位错误**
  典型原因：终端电阻不匹配、线太长/未双绞、共模干扰、收发器供电裕量不足
· **CRCBAD 且 LEN 错误 / 大量"跳过字节"** ⇒ 丢字节导致整段错位 ⇒ 发送端 DE
  切换过早（末字节被截）或接收端 FIFO 溢出
· 帧错误若**集中在帧头** ⇒ 指向 DE 时序；**均匀散布** ⇒ 指向随机位错误

背景数据（2026-11-09，AECG100 180 bpm，59 B/帧 @ 20 fps）
    WinApp(COM27) 26 min : 丢帧 21.0%（含大量缓冲/解析开销）
    Python(COM27) 60 s, 无终端 : 坏帧 1.45%（16×CRCBAD + 2×BADLEN）
    ESP32(COM35) 30 s     : seq_gaps 0 / crc 0 / resync 0
"""
import argparse
import collections
import struct
import sys
import time

FRAME_LENS = (52, 53, 103)     # 与固件 ecg_frame.c 一致，否则帧头损坏会把解析器带跑偏


def crc8(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def capture(port, secs, baud=115200):
    try:
        import serial
    except ImportError:
        sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")
    p = serial.Serial()
    p.port = port
    p.baudrate = baud
    p.timeout = 0.05
    p.dtr = False
    p.rts = False
    p.set_buffer_size(rx_size=1048576)
    p.open()
    p.reset_input_buffer()
    print("  抓取 %s %d s …" % (port, secs), flush=True)
    buf = b""
    t_end = time.time() + secs
    while time.time() < t_end:
        c = p.read(8192)
        if c:
            buf += c
    p.close()
    return buf


def analyze(d):
    i = 0
    frames = []          # (seq, offset, samples)
    ev = collections.Counter()
    nbit = 0
    while i + 8 <= len(d):
        if d[i:i + 2] != b"\xaa\x55":
            i += 1
            continue
        L = d[i + 3] | (d[i + 4] << 8)
        if L not in FRAME_LENS:
            ev["BADLEN"] += 1
            i += 1
            continue
        n = (L - 3) // 2
        total = 8 + 2 * n + 1
        if i + total > len(d):
            i += 1
            continue
        co = i + 2 + 6 + 2 * n
        if crc8(d[i + 2:co]) != d[co]:
            ev["CRCBAD_LEN_OK" if L in FRAME_LENS else "CRCBAD_LEN_BAD"] += 1
            nbit += total * 8
            i += 1
            continue
        ev["OK"] += 1
        nbit += total * 8
        frames.append((d[i + 5] | (d[i + 6] << 8), i,
                       struct.unpack_from("<%dh" % n, d, i + 8)))
        i += total

    seqs = [f[0] for f in frames]
    gaps = [(seqs[k], k) for k in range(len(frames) - 1)
            if ((seqs[k + 1] - seqs[k]) & 0xFFFF) != 1]
    span = (seqs[-1] - seqs[0]) & 0xFFFF if len(seqs) > 1 else 0
    lost = sum(((seqs[k + 1] - seqs[k]) & 0xFFFF) - 1 for _, k in gaps) if gaps else 0

    # 段内尖刺：只在 SEQ 连续的段里找，避免把"丢帧接缝"误判成畸变
    segs = []
    cur = []
    for k in range(len(frames)):
        if cur and ((frames[k][0] - frames[k - 1][0]) & 0xFFFF) != 1:
            if len(cur) >= 3:
                segs.append(cur)
            cur = []
        cur.append(frames[k])
    if len(cur) >= 3:
        segs.append(cur)
    spike = 0
    nsmp = 0
    for seg in segs:
        s = [v for _, _, smp in seg for v in smp]
        nsmp += len(s)
        for k in range(1, len(s) - 1):
            if abs(s[k] - (s[k - 1] + s[k + 1]) / 2) > 1500:
                spike += 1

    return dict(bytes=len(d), ok=ev["OK"], crcbad=ev["CRCBAD_LEN_OK"],
                badlen=ev["BADLEN"], frames=frames, span=span, lost=lost,
                nseg=len(segs), nsmp=nsmp, spike=spike, gaps=gaps)


def report(r, label=""):
    ok = r["ok"]
    tot = ok + r["crcbad"] + r["badlen"]
    print("=" * 64)
    print("  485 接收质量%s" % (("  " + label) if label else ""))
    print("=" * 64)
    print("  字节 %d   有效帧 %d   坏帧 %d（CRC错 %d / LEN错 %d）"
          % (r["bytes"], ok, r["crcbad"] + r["badlen"], r["crcbad"], r["badlen"]))
    if tot:
        print("  帧错误率 %.2f%%" % ((r["crcbad"] + r["badlen"]) * 100.0 / tot))
    print("  SEQ 跨度 %d 帧   跳变 %d 处   丢帧 %d（%.2f%%）"
          % (r["span"], len(r["gaps"]), r["lost"],
             r["lost"] * 100.0 / r["span"] if r["span"] else 0.0))
    print("  连续段 %d 个   段内样本 %d   段内尖刺 %d" % (r["nseg"], r["nsmp"], r["spike"]))
    if r["crcbad"]:
        print()
        print("  ⚠ 有 %d 次「长度正确但 CRC 错」⇒ 位错误（物理层），不是丢字节" % r["crcbad"])
    if r["spike"] == 0 and r["nsmp"]:
        print("  ✓ 段内零尖刺 ⇒ 收到的波形值本身干净，畸变来自「缺一段」而非「值错」")


def main():
    ap = argparse.ArgumentParser(description="485 链路接收质量测量")
    ap.add_argument("--port", default="COM27")
    ap.add_argument("--secs", type=int, default=60)
    ap.add_argument("--file", help="分析已有抓包")
    ap.add_argument("--raw", help="抓包存到该文件（不分析）")
    a = ap.parse_args()

    if a.file:
        with open(a.file, "rb") as f:
            d = f.read()
        report(analyze(d), a.file)
        return 0

    d = capture(a.port, a.secs)
    if a.raw:
        with open(a.raw, "wb") as f:
            f.write(d)
        print("  已存 %s（%d B）" % (a.raw, len(d)))
        return 0
    report(analyze(d), "%s / %d s" % (a.port, a.secs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
