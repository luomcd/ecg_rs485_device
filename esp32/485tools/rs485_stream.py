#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S9a：把波形文件（T101 v2.2 帧流）按实时速率发到 RS485 总线，供 ESP32 显示。

数据源 = 任意 `AA 55 | TYPE | LEN(2,LE) | PAYLOAD | CRC8` 帧流：
  - `cardiolite-ecg/sdcard/ecg.bin`（显示流，0.1526 µV/LSB 域，BPM 可对账）
  - MIT 的 `<rec>_500.bin`（raw code 域，幅值不对，S9a 不用）
  - 将来 STM32L475 发的帧（同格式）

用法：
  python tools/rs485_stream.py --port COM27 --file cardiolite-ecg/sdcard/ecg.bin
  python tools/rs485_stream.py --port COM27 --file ... --speed 2 --loop --duration 60
  python tools/rs485_stream.py --file ... --check-only      # 只自检文件，不开串口

节流：每帧样本数 / 500 Hz = 该帧应耗时（25 样本 = 50 ms），`--speed` 可加速。
对账：结束时打印发送帧数/字节数 —— 与 ESP32 的 `485-stat` 的 frames/rx_bytes 比。
"""
import argparse
import os
import sys
import time

FS = 500
SYNC = b"\xAA\x55"
LEN_SANITY_MAX = 200


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def split_frames(blob: bytes):
    """按 LEN 切帧（🛑 不能按固定 58/59 字节切：52/53/103 三种帧长共存）。
    返回 [(帧字节, 样本数ch1, seq)]；遇到同步字错位则逐字节滑过。"""
    frames = []
    resync = 0
    i = 0
    n = len(blob)
    while i + 5 <= n:
        if blob[i:i + 2] != SYNC:
            i += 1
            resync += 1
            continue
        ln = blob[i + 3] | (blob[i + 4] << 8)
        if ln < 2 or ln > LEN_SANITY_MAX:
            i += 1
            resync += 1
            continue
        total = ln + 6
        if i + total > n:
            break
        frame = blob[i:i + total]
        payload = frame[5:5 + ln]
        hdr = 3 if (ln & 1) else 2
        n_total = (ln - hdr) // 2
        n_ch1 = n_total // 2 if ln == 103 else n_total
        seq = payload[0] | (payload[1] << 8)
        frames.append((frame, n_ch1, seq))
        i += total
    return frames, resync, i


class StreamParser:
    """分块到达的字节流 → 帧级统计（与固件 ecg_stream.c 同一套判据）。

    用途：`s9a_link_check.py --from-port` 实时转发/旁听时做帧级对账
    （frames / crc_err / seq_gaps / resync），不发帧、不重新打包。
    """

    def __init__(self):
        self.buf = bytearray()
        self.bytes = 0
        self.frames = 0
        self.crc_err = 0
        self.resync = 0
        self.seq_gaps = 0
        self.last_seq = None
        self.lens = {}
        self.samples = 0

    def feed(self, data: bytes):
        self.bytes += len(data)
        self.buf.extend(data)
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                # 没有同步字：留最后一个字节（可能是半个 AA）
                if len(self.buf) > 1:
                    self.resync += len(self.buf) - 1
                    del self.buf[:-1]
                return
            if i > 0:
                self.resync += i
                del self.buf[:i]
            if len(self.buf) < 5:
                return
            ln = self.buf[3] | (self.buf[4] << 8)
            if ln < 2 or ln > LEN_SANITY_MAX:
                self.resync += 1
                del self.buf[:1]
                continue
            total = ln + 6
            if len(self.buf) < total:
                return
            frame = bytes(self.buf[:total])
            del self.buf[:total]

            self.frames += 1
            self.lens[total] = self.lens.get(total, 0) + 1
            if crc8(frame[2:-1]) != frame[-1]:
                self.crc_err += 1
            hdr = 3 if (ln & 1) else 2
            n_total = (ln - hdr) // 2
            self.samples += (n_total // 2) if ln == 103 else n_total
            seq = frame[5] | (frame[6] << 8)
            if self.last_seq is not None and seq != ((self.last_seq + 1) & 0xFFFF):
                self.seq_gaps += 1
            self.last_seq = seq

    def summary(self) -> str:
        return (f"帧 {self.frames}  {self.bytes} B  样本 {self.samples}  "
                f"crc_err {self.crc_err}  seq_gaps {self.seq_gaps}  "
                f"resync {self.resync} B  帧长 {dict(sorted(self.lens.items()))}")


def check_file(path: str) -> int:
    blob = open(path, "rb").read()
    frames, resync, consumed = split_frames(blob)
    if not frames:
        print(f"✗ {path}: 没有解析出任何帧")
        return 1
    lens = {}
    samples = 0
    bad_crc = 0
    gaps = 0
    prev_seq = None
    for frame, n_ch1, seq in frames:
        lens[len(frame)] = lens.get(len(frame), 0) + 1
        samples += n_ch1
        recomputed = crc8(frame[2:-1])
        if recomputed != frame[-1]:
            bad_crc += 1
        if prev_seq is not None and seq != (prev_seq + 1) & 0xFFFF:
            gaps += 1
        prev_seq = seq
    print(f"文件 {path}")
    print(f"  字节 {len(blob)}   帧 {len(frames)}   样本(CH1) {samples}")
    print(f"  帧长分布 {dict(sorted(lens.items()))}   重同步字节 {resync}   尾部残余 {len(blob) - consumed}")
    print(f"  CRC 错 {bad_crc}   SEQ 断档 {gaps}")
    print(f"  按 500 Hz 实时播放时长 ≈ {samples / FS:.1f} s")
    return 0 if bad_crc == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="S9a RS485 波形发送器")
    ap.add_argument("--port", default="COM27", help="485 侧串口（默认 COM27）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--file", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cardiolite-ecg", "sdcard", "ecg.bin"))
    ap.add_argument("--speed", type=float, default=1.0, help="倍速（默认 1x 实时）")
    ap.add_argument("--loop", action="store_true", help="发完从头再来")
    ap.add_argument("--duration", type=float, default=0.0, help="发送秒数（0=发到 EOF/loop 无限）")
    ap.add_argument("--stats-interval", type=float, default=5.0)
    ap.add_argument("--check-only", action="store_true", help="只自检文件，不开串口")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print(f"✗ 文件不存在: {a.file}")
        return 1

    rc = check_file(a.file)
    if a.check_only:
        return rc

    try:
        import serial
    except ImportError:
        print("✗ 缺 pyserial。用统一入口运行，或：\n"
              '   C:\\Users\\luom\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe -m pip install pyserial')
        return 1

    blob = open(a.file, "rb").read()
    frames, _, _ = split_frames(blob)

    ser = serial.Serial(a.port, a.baud, timeout=0.2, write_timeout=2.0)
    # 上电时 ROM bootloader 会往总线灌几十毫秒日志（软件关不掉），这里先读干，
    # 避免我们的接收缓冲里攒着垃圾（不影响发送，但会让本地日志看着乱）
    time.sleep(0.1)
    ser.reset_input_buffer()

    print(f"\n发送 → {a.port} @{a.baud}  {len(frames)} 帧  "
          f"{a.speed:g}x  loop={a.loop} duration={a.duration:g}s")

    t0 = time.time()
    next_t = t0
    sent_frames = 0
    sent_bytes = 0
    last_stat = t0
    idx = 0
    interrupted = False
    try:
        while True:
            frame, n_ch1, _seq = frames[idx]
            ser.write(frame)
            sent_frames += 1
            sent_bytes += len(frame)
            idx += 1

            # 该帧应耗时 = 样本数 / 500 / speed
            next_t += n_ch1 / FS / max(a.speed, 0.01)
            now = time.time()
            if next_t > now:
                time.sleep(next_t - now)
            else:
                next_t = now          # 落后就重置基准，不追赶（避免突发灌爆 UART）

            if now - last_stat >= a.stats_interval:
                last_stat = now
                el = now - t0
                print(f"  [{el:6.1f}s] 帧 {sent_frames}  {sent_bytes} B  "
                      f"{sent_frames / el:.1f} 帧/s  {sent_bytes / el / 1024:.2f} KB/s")

            if a.duration > 0 and now - t0 >= a.duration:
                break
            if idx >= len(frames):
                if not a.loop:
                    break
                idx = 0
    except KeyboardInterrupt:
        # Ctrl+C：Windows 控制台会把 CTRL_C_EVENT 发给整个进程组，python 这边
        # 收成 KeyboardInterrupt；cmd.exe 在跑 .cmd 时可能还会追加一句
        # "终止批处理操作吗(Y/N)?" —— 答 Y 即可，python 已经退出了。
        interrupted = True
    finally:
        ser.close()

    el = time.time() - t0
    head = "中断（Ctrl+C）" if interrupted else "完成"
    print(f"\n{head}：帧 {sent_frames}  字节 {sent_bytes}  耗时 {el:.1f} s  "
          f"({sent_frames / max(el, 1e-9):.1f} 帧/s)")
    print(f"对账：ESP32 侧 `485-stat` 的 frames 应 ≈ {sent_frames}，"
          f"rx_bytes 应 ≈ {sent_bytes}（差 ≤1 帧）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
