#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc485_cmd.py —— 用 PC 的 485-USB（CH340）直接给 STM32 发下行命令，绕开 ESP32

为什么需要它
------------
`485-send` 是 **ESP32 的 CLI 命令**：它先要 `485-start` 开 UART1，再拼帧发出去。
一旦现场把 ESP32 从 USB 上摘掉（只留 PC 的 485-USB 做抓包），就没人发 `0x01` 了，
STM32 停在 STOP 态 ⇒ COM27 上**一个字节都没有**（本工具就是为解决这个僵局）。

帧格式与 `ecg_cmd.c:27 ecg_cmd_send()` 逐字节一致：

    AA 55 | TYPE(0x80) | LEN_L LEN_H | CMD | ARG[0..N] | CRC8
             └────────── CRC8 覆盖 ──────────┘
    LEN = 1(CMD) + N(ARG)，小端

🛑 `0x01` 不幂等（重发会重走采集链、制造基线瞬态）⇒ 默认只发 1 帧，`--repeat` 别乱开。

用法
----
    py pc485_cmd.py --cmd 0x01                      # START（进入 RUN）
    py pc485_cmd.py --cmd 0x02                      # STOP
    py pc485_cmd.py --cmd 0x01 --capture raw.bin --secs 180   # 发完立刻抓 N 秒原始帧流
    py pc485_cmd.py --cmd 0x04 --arg 0x01           # 带 1 B ARG 的命令
"""
import argparse
import sys
import time

CMD_TYPE = 0x80
FRAME_LENS = (52, 53, 103)


def crc8(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def build(cmd, arg=b""):
    n = 1 + len(arg)
    body = bytes([CMD_TYPE, n & 0xFF, (n >> 8) & 0xFF, cmd]) + arg
    return b"\xaa\x55" + body + bytes([crc8(body)])


def main():
    ap = argparse.ArgumentParser(description="PC 侧 485 下行命令发送器")
    ap.add_argument("--port", default="COM27")
    ap.add_argument("--cmd", required=True, help="命令字节，如 0x01 / 0x02 / 0x04")
    ap.add_argument("--arg", action="append", default=[],
                    help="参数字节（可多次给），如 --arg 0x01")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--gap", type=float, default=0.2, help="重复发送间隔 s")
    ap.add_argument("--capture", help="发送后把总线原始流存到该文件")
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true", help="只打印帧、不开串口")
    a = ap.parse_args()

    cmd = int(a.cmd, 16) if a.cmd.lower().startswith("0x") else int(a.cmd, 0)
    arg = bytes(int(v, 16) if v.lower().startswith("0x") else int(v, 0) for v in a.arg)
    frame = build(cmd, arg)
    print("下行帧：" + " ".join("%02X" % b for b in frame) + "   （%d B）" % len(frame))
    if a.dry_run:
        return 0

    try:
        import serial
    except ImportError:
        sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")

    p = serial.Serial()
    p.port = a.port
    p.baudrate = 115200
    p.timeout = 0.05
    p.dtr = False
    p.rts = False
    p.set_buffer_size(rx_size=1048576)
    p.open()

    # 🛑 发之前清空接收缓冲：半双工下自己发的字节会回环进来，
    #    残留字节会污染后面抓的帧流（表现为 LEN 错/CRC 错）。
    p.reset_input_buffer()
    for k in range(a.repeat):
        p.write(frame)
        p.flush()
        if k + 1 < a.repeat:
            time.sleep(a.gap)
    time.sleep(0.3)
    p.reset_input_buffer()
    print("  已发 %d 帧 @ %s" % (a.repeat, a.port))

    if a.capture:
        print("  抓取 %.0f s …" % a.secs, flush=True)
        buf = bytearray()
        t_end = time.time() + a.secs
        t0 = time.time()
        while time.time() < t_end:
            c = p.read(8192)
            if c:
                buf += c
        with open(a.capture, "wb") as f:
            f.write(buf)
        dt = time.time() - t0
        print("  已存 %s（%d B，%.1f s，%.1f B/s）" % (a.capture, len(buf), dt, len(buf) / dt))

        # 顺手报一下帧率，省得再单跑一次分析
        n, i = 0, 0
        d = bytes(buf)
        while i + 8 <= len(d):
            if d[i:i + 2] != b"\xaa\x55":
                i += 1
                continue
            L = d[i + 3] | (d[i + 4] << 8)
            if L not in FRAME_LENS:
                i += 1
                continue
            i += 8 + 2 * ((L - 3) // 2) + 1
            n += 1
        print("  帧数约 %d   平均 %.2f fps   样本约 %.0f"
              % (n, n / dt if dt else 0, n * 25))

    p.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
