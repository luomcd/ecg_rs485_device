#!/usr/bin/env python3
"""
把一段双通道（LEN=103）录制数据循环拼接成指定时长，用于长时间回放验证。

为什么不能直接 copy 拼接：
  每帧 payload = SEQ(2) + STA(1) + 样本(100 B)
  ① SEQ 是采集端帧序号，逐帧 +1。直接拼会出现 SEQ 跳变（回绕/倒退）
  ② CRC8 覆盖 TYPE+LEN+PAYLOAD，改了 SEQ 就必须重算
  ⇒ 必须「取样本 + 重写 SEQ + 重算 CRC」逐帧重建。

用法:
    python tools/gen_dual_testdata.py                     # 默认 5 min
    python tools/gen_dual_testdata.py --seconds 600       # 10 min
    python tools/gen_dual_testdata.py -o xxx.bin
"""

import argparse
import os
import struct
import sys

SRC_DEFAULT = (r"C:/work2026/VsPro/WinApp_ADSECG/ADS1293_Ecg/bin/Debug/"
               r"net10.0-windows/recordings/ecg_20260928_113120.bin")
OUT_DEFAULT = "C:/work2026/CardioLite/cardiolite-ecg/sdcard/ecg_dual_5min.bin"

FS = 500                 # 采样率
SAMPLES_PER_FRAME = 25   # 每帧每通道样本数（LEN=103 双通道）


def crc8(data: bytes) -> int:
    """多项式 0x07、初值 0x00（与固件一致）"""
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
    return c


def read_frames(path):
    """按 LEN+6 切帧，返回 [(payload_bytes), ...]"""
    d = open(path, "rb").read()
    out, i, bad = [], 0, 0
    while i + 6 <= len(d):
        if d[i] != 0xAA or d[i + 1] != 0x55:
            break
        L = d[i + 3] | (d[i + 4] << 8)
        tot = 6 + L
        if i + tot > len(d):
            break
        frame = d[i:i + tot]
        if crc8(frame[2:tot - 1]) != frame[tot - 1]:
            bad += 1
        out.append(frame[5:5 + L])       # payload
        i += tot
    return out, bad, len(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--seconds", type=int, default=300, help="目标时长（秒），默认 300 = 5 min")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT)
    args = ap.parse_args()

    payloads, bad, src_bytes = read_frames(args.src)
    if not payloads:
        sys.exit(f"[错误] {args.src} 里没有可解析的帧")
    if bad:
        sys.exit(f"[错误] 源文件有 {bad} 帧 CRC 错，先修源数据")

    L = len(payloads[0])
    if L != 103:
        sys.exit(f"[错误] 源文件 LEN={L}，本脚本只处理 LEN=103（双通道）")

    # payload = SEQ(2) + STA(1) + 样本(100)
    sta = payloads[0][2]
    bodies = [p[3:] for p in payloads]
    n_src = len(bodies)

    target_frames = args.seconds * FS // SAMPLES_PER_FRAME
    print(f"源: {args.src}")
    print(f"     {src_bytes} B, {n_src} 帧, STA=0x{sta:02X}, LEN={L}")
    print(f"目标: {args.seconds} s = {target_frames} 帧 = {target_frames * 109} B "
          f"({target_frames * 109 / 1024 / 1024:.2f} MiB)")
    print(f"     = 源文件循环 {target_frames / n_src:.1f} 遍")

    out = bytearray()
    seq = 0
    for k in range(target_frames):
        body = bodies[k % n_src]
        payload = struct.pack("<H", seq) + bytes([sta]) + body
        head = b"\xAA\x55\x01" + struct.pack("<H", len(payload)) + payload
        out += head + bytes([crc8(head[2:])])
        seq = (seq + 1) & 0xFFFF

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(out)

    # ---- 自检：逐帧 CRC + SEQ 连续性 ----
    chk, bad2, out_bytes = read_frames(args.out)
    seqs = [p[0] | (p[1] << 8) for p in chk]
    gaps = sum(1 for i in range(len(seqs) - 1)
               if seqs[i + 1] != (seqs[i] + 1) & 0xFFFF)

    print()
    print(f"写出: {args.out}")
    print(f"     {len(out)} B, {len(chk)} 帧")
    print(f"自检: CRC 错 {bad2} 帧, SEQ 跳变 {gaps} 处")
    if bad2 or gaps:
        sys.exit("[失败] 自检不通过")
    print("     ✅ 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
