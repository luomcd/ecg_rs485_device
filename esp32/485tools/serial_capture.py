#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_capture.py —— 板子串口抓取（可复用统一入口）

背景（S7 踩出来的两条硬规矩，别绕过）：
  ① **open/close COM35 会复位板子**（DTR/RTS 接了自动复位电路）
     ⇒ 打开端口后必须立刻把 DTR/RTS 拉到无效，否则一 open 板子就重启、
       而且重启发生在"还没开始读"的那一瞬间 ⇒ boot log 整段丢失。
  ② 抓取要在**同一个进程里**完成"复位 → 立刻读"，中间不能有 shell 往返延迟。

复位时序（ESP32-S3 原生 USB-Serial-JTAG：RTS→EN，DTR→GPIO0）：
  DTR 保持 False（不进下载模式） + RTS 一个低脉冲（拉低=复位，释放=运行）

用法：
  python tools/serial_capture.py -p COM35 -t 30 -o test_logs/q6_boot.log
  python tools/serial_capture.py -p COM35 -t 30 --no-reset      # 已跑起来时只读
  python tools/serial_capture.py -p COM35 -t 120 -o x.log --grep fps

输出：UTF-8 纯 LF（项目约定），同时把匹配 --grep 的行打到 stdout。
"""
import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("缺 pyserial（当前解释器里没有）。二选一：\n"
             "  ① 给当前解释器装上：python -m pip install pyserial\n"
             r"  ② 换用已装好的解释器：C:\Users\luom\.workbuddy\binaries\python"
             r"\envs\default\Scripts\python.exe" + f"\n当前解释器：{sys.executable}")


def capture(port: str, seconds: float, reset: bool, baud: int) -> bytes:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.2
    ser.dsrdtr = False
    ser.rtscts = False
    ser.open()
    # ① 打开后立刻拉无效，避免驱动默认断言 DTR/RTS 把板子按在复位里
    ser.dtr = False
    ser.rts = False
    time.sleep(0.05)

    if reset:
        # ② 显式复位脉冲：RTS 低 → 释放。DTR 全程 False
        ser.rts = True
        time.sleep(0.12)
        ser.rts = False
        time.sleep(0.02)

    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = ser.read(4096)
        if chunk:
            buf.extend(chunk)
    ser.close()
    return bytes(buf)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", default="COM35")
    ap.add_argument("-t", "--seconds", type=float, default=30.0)
    ap.add_argument("-o", "--out", default=None, help="输出文件（纯 LF）；省略则只打屏")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--no-reset", action="store_true", help="不发复位脉冲，只旁听")
    ap.add_argument("--grep", default=None, help="正则，命中行打到 stdout")
    args = ap.parse_args()

    print(f"[capture] {args.port} @{args.baud} {args.seconds:.0f}s "
          f"reset={'no' if args.no_reset else 'yes'}", file=sys.stderr)
    raw = capture(args.port, args.seconds, not args.no_reset, args.baud)

    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"[capture] {len(raw)} 字节 → {args.out}（{text.count(chr(10))} 行）",
              file=sys.stderr)

    if args.grep:
        pat = re.compile(args.grep)
        for line in text.split("\n"):
            if pat.search(line):
                print(line)
    elif not args.out:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
