#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208n_uplink_verify.py —— 上行帧流严格验收（CRC / SEQ / 真帧率 / 采样率）

帧格式（T208 已对拍）
--------------------
    AA 55 | TYPE=01 | LEN=35 00 | SEQ(2B LE) | STA(1) | 25×int16 LE | CRC8   = 59 B
                              ↑ 4+1+2+50 = 57 ... 实际 [3]=0x35 [4]=0x00 两个字节
    CRC8 覆盖范围与固件一致：TYPE + LEN(2B) + payload（即 idx 2..57），落在 [58]

判据
----
    ① 对齐成功率：按 59 B 步进能连续对齐的帧数 / 总帧数 → 应 100%
    ② CRC 通过率 → 应 100%
    ③ SEQ 连续性：相邻帧 SEQ 差应为 1（uint16 回绕），跳变数 → 应 0
    ④ 帧率 = 有效帧数 / 时长，应 ≈ 20.00 fps
    ⑤ 采样率 = 帧率 × 25，应 ≈ 500 Hz

用法
----
    python _t208n_uplink_verify.py --port COM26 --dur 10
"""
import argparse
import statistics
import sys
import time

import serial

HDR = b"\xAA\x55\x01\x35\x00"
FRAME = 59
CRC_SPAN = (2, 58)          # [2..57] inclusive


def _crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def _frame(cmd, arg=b""):
    payload = bytes([0x80, 1 + len(arg), 0x00, cmd]) + arg
    return bytes([0xAA, 0x55]) + payload + bytes([_crc8(payload)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM26")
    ap.add_argument("--dur", type=float, default=10.0)
    ap.add_argument("--start", action="store_true", default=True)
    ap.add_argument("--keep-running", action="store_true", help="结束时不发 STOP")
    a = ap.parse_args()

    p = serial.Serial(a.port, 115200, timeout=0.05)
    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(_frame(0x02))          # 先 STOP 清场，避免接着上一轮
        p.flush()
        time.sleep(0.8)
        p.reset_input_buffer()

        p.write(_frame(0x01))          # START
        p.flush()
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < a.dur:
            n = p.in_waiting
            if n:
                buf += p.read(n)
            else:
                time.sleep(0.005)
        el = time.time() - t0

        if not a.keep_running:
            p.write(_frame(0x02))
            p.flush()
    finally:
        try:
            p.close()
        except Exception:
            pass

    # ---- 对齐 + 校验 ------------------------------------------------------
    i = 0
    ok = []
    bad_crc = 0
    resync = 0
    while i + FRAME <= len(buf):
        if buf[i:i + 5] == HDR:
            fr = buf[i:i + FRAME]
            if _crc8(fr[CRC_SPAN[0]:CRC_SPAN[1]]) == fr[58]:
                ok.append((fr[5] | (fr[6] << 8), fr[7]))
                i += FRAME
                continue
            bad_crc += 1
        resync += 1
        i += 1

    print(f"端口 {a.port}  窗口 {el:.2f} s  共收 {len(buf)} B")
    print(f"有效帧 {len(ok)}  坏 CRC {bad_crc}  重同步字节 {resync}")
    if not ok:
        print("\n[判定] ❌ 没有一帧通过 CRC —— 上行不可用")
        return 1

    fps = len(ok) / el
    per_frame = len(buf) / len(ok)
    seqs = [s for s, _ in ok]
    jumps = [(k, seqs[k - 1], seqs[k]) for k in range(1, len(seqs))
             if (seqs[k] - seqs[k - 1]) & 0xFFFF != 1]

    print(f"\n帧率 {fps:.2f} fps   采样率 {fps * 25:.1f} Hz（假设 25 样本/帧）")
    print(f"每帧平均字节 {per_frame:.1f}（应为 {FRAME}）")
    print(f"SEQ 范围 {seqs[0]} → {seqs[-1]}   期望跨度 {len(seqs) - 1}")
    print(f"SEQ 跳变 {len(jumps)} 处" + (f"：{jumps[:5]}" if jumps else ""))

    crc_rate = 100.0 * len(ok) / (len(ok) + bad_crc) if (len(ok) + bad_crc) else 0.0
    print(f"CRC 通过率 {crc_rate:.2f}%")

    stas = {}
    for _, st in ok:
        stas[st] = stas.get(st, 0) + 1
    print(f"STA 分布 {stas}")

    print("\n[判定]", end=" ")
    fail = []
    if crc_rate < 99.0:
        fail.append(f"CRC {crc_rate:.1f}%")
    if len(jumps) > 0:
        fail.append(f"SEQ 跳 {len(jumps)}")
    if abs(fps - 20.0) > 1.0:
        fail.append(f"帧率 {fps:.2f}")
    if per_frame > FRAME + 1.5:
        fail.append(f"每帧 {per_frame:.1f}B")
    if fail:
        print("⚠ " + " / ".join(fail))
        return 1
    print("✅ 全部通过 —— 上行链路稳定")
    return 0


if __name__ == "__main__":
    sys.exit(main())
