#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205r_blank_probe.py —— 只读上行帧，数「全 0 帧」判断伪迹空白是否在跑（T205）

线索链
------
`_t205p` 连跑 10 批实测（同一块板、同一脚本、同一根线）：

    批1~4  100% 100% 100% 100%     ← 距上次硬复位 ≈ 0~45 s
    批5      90%
    批6~10  20% 20% 0% 10% 30%     ← ≈ 2 min 之后

即 **硬复位后有一段"蜜月期"，随后下行成功率塌到 0~30%**。这说明有个东西
「从复位起慢慢累积、攒够了就开始周期性打断 RX」。

嫌疑：**伪迹空白**。`fast_restore.c:128` 判据 `|x − 基线| > 445633 code`，命中就
把整条滤波链复位（τ≈8 s）并在输出列上留一段空白。无电极、输入悬空时基线会一路漂，
漂到阈值就开始周期性触发；每次触发是一段重活，期间 UART 的 EX/IDLE 重新武装延迟
⇒ 这 8 字节下行正好落进空档就被吃掉。

判据（纯串口，不碰 SWD）
------------------------
上行显示帧的 25 个 int16 样本若**连续多帧全 0**，就是空白段（真实 ECG 不会全 0）。
本脚本统计：

    · 采样 60 s 内的帧数 / 帧率
    · 全 0 帧占比 per 5 s 分桶（看空白是"偶发"还是"周期性"）
    · 最长连续全 0 帧长度
    · 非零样本的 |max| 与标准差（看基线在不在漂）

用法
----
    python tools/_t205r_blank_probe.py --port COM27 --seconds 60
"""
import argparse
import collections
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")


def crc8(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build(cmd: int, arg: bytes = b"") -> bytes:
    length = 1 + len(arg)
    body = bytes([0x80, length & 0xFF, (length >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


def main() -> int:
    ap = argparse.ArgumentParser(description="统计上行帧里的全 0（空白）段")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        time.sleep(0.3)
        if port.in_waiting < 60:
            port.write(build(0x01))
            port.flush()
            time.sleep(0.5)
        port.reset_input_buffer()

        buf = bytearray()
        t0 = time.monotonic()
        t_end = t0 + args.seconds
        buckets = collections.defaultdict(lambda: [0, 0])   # 5s 桶 -> [帧数, 全0帧数]
        zero_run = 0
        max_run = 0
        total = 0
        amps = []
        while time.monotonic() < t_end:
            chunk = port.read(512)
            if not chunk:
                continue
            buf += chunk
            while True:
                i = buf.find(b"\xAA\x55")
                if i < 0:
                    if len(buf) > 1:
                        del buf[: len(buf) - 1]
                    break
                if i > 0:
                    del buf[:i]
                if len(buf) < 5:
                    break
                length = buf[3] | (buf[4] << 8)
                tot = 6 + length
                if tot > 260:
                    del buf[0:1]
                    continue
                if len(buf) < tot:
                    break
                fr = bytes(buf[:tot])
                if crc8(fr[2: tot - 1]) != fr[tot - 1]:
                    del buf[0:1]
                    continue
                if buf[2] == 0x01 and length in (53, 103):
                    n = (length - 3) // 2
                    smp = [int.from_bytes(fr[8 + 2 * k: 10 + 2 * k], "little",
                                          signed=True) for k in range(n)]
                    total += 1
                    b = int((time.monotonic() - t0) // 5)
                    buckets[b][0] += 1
                    if all(v == 0 for v in smp):
                        buckets[b][1] += 1
                        zero_run += 1
                        max_run = max(max_run, zero_run)
                    else:
                        zero_run = 0
                        amps.append(max(abs(v) for v in smp))
                del buf[:tot]

        dur = time.monotonic() - t0
        n_zero = sum(v[1] for v in buckets.values())
        print(f"采样 {dur:.1f} s，帧数 {total}（{total / dur:.1f} fps），"
              f"全 0 帧 {n_zero}（{n_zero / max(1, total) * 100:.1f}%），"
              f"最长连续全 0 = {max_run} 帧（≈{max_run / max(1e-6, total / dur):.2f} s）")
        if amps:
            amps_sorted = sorted(amps)
            p50 = amps_sorted[len(amps_sorted) // 2]
            p95 = amps_sorted[int(len(amps_sorted) * 0.95)]
            print(f"非零帧 |max| 中位 {p50} LSB ／ p95 {p95} LSB  "
                  f"（1 LSB = ECG_LSB_OUT_UV µV 口径）")
        print()
        print(f"{'窗口(s)':>8} {'帧数':>5} {'全0':>5} {'占比':>7}")
        print("-" * 32)
        for b in sorted(buckets):
            cnt, z = buckets[b]
            print(f"{b * 5:>4}-{b * 5 + 5:<3} {cnt:>5} {z:>5} "
                  f"{z / max(1, cnt) * 100:>6.1f}%")
        print()
        if n_zero / max(1, total) > 0.02:
            print("[判读] 有明显的全 0 空白段 ⇒ 伪迹空白确实在跑，")
            print("       与『复位后蜜月期、随后塌掉』时间尺度吻合 ⇒ 高度嫌疑为下行被吃的主因。")
        else:
            print("[判读] 几乎没有全 0 帧 ⇒ 伪迹空白不是主因，改查 485 总线电气/驱动侧。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
