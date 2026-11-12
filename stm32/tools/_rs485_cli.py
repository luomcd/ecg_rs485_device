# -*- coding: utf-8 -*-
"""
RS485 ASCII 控制台 —— STM32 固件的**主调试口**统一入口（2026-11-12）
================================================================================
背景（🛑 先读，否则会找错口、白折腾一小时）：
  本板 `comm_port.h:49  ECG_COMM_PORT = ECG_COMM_PORT_UART5`
  ⇒ `printf_msg()` 的 ASCII 控制台**不在 PC 侧 USB**，而是走
     UART5 → RS485 → **ESP32**（屏上的 `cardiolite>` 提示符）。
  想从 PC 直接敲 STM32 的 ASCII 命令，必须接 **485→USB 转换器**（CH340，本机 COM26）。

用法（本脚本是"一个入口"，不要为每条命令再新建一次性脚本）：
    python tools/_rs485_cli.py                      # 列串口 + 探活
    python tools/_rs485_cli.py "test notch"         # 发一条
    python tools/_rs485_cli.py "test chstat" "test ch2" "test ch2 off"

    python tools/_rs485_cli.py --port COM26 --baud 115200 --wait 0.8 "test notch"

关键参数：
    --rts / --no-rts   方向控制方式（默认 auto = 发送期间 RTS=True）
        CH340-485 是**半双工**：RTS 拉高才让本机驱动总线，拉低才收得到 STM32 的回话。
        默认序列 `rts=True → 写 → 0.10 s → rts=False → 读 --wait 秒`。
    --wait             发送后读多久（默认 0.6 s；`test chstat` 一行够，抓流式日志要加长）
    --grep REGEX       只打印匹配的行（🛑 常用！总线在出流时同时跑 109 B 二进制帧，
                       会把你读回的缓冲撑成几万字节乱码；用 `--grep "ch:|notch|mode|raw853"`
                       只看回显，其余丢弃并报字节数）
    --hex              按十六进制打印（看二进制帧时用）

🛑 三条实发事故，别踩：
  ① **`485-send` 类命令会打坏上行流**：每发一条 ASCII 命令，ESP32 侧
     `crc_err` 会 +1~3，偶尔命令本身被静默吃掉 ⇒ **命令要重发确认**，别一次就当真。
  ② **`stop` → 改 notch/mode → `start` 无效**：`filter_chain_init()` 里的
     `ecg_filter_f64_init()` 把 notch 写回"开"、mode 写回 diag（fail-safe）
     ⇒ 顺序必须「先 START，再改设置」。
  ③ 命令**必须带 `\\r\\n` 结尾**：固件按行解析，只发 `\\r` 会等下一个字节。
  ④ 上位机同一条总线上可能还有 ESP32 在收：**不要**在同一时刻用两个程序抢 COM26。
"""
import argparse
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    raise SystemExit("!! 缺 pyserial：pip install pyserial")

DEFAULT_PORT = "COM26"
DEFAULT_BAUD = 115200
DEFAULT_WAIT = 0.6


def show_ports():
    print("=" * 70)
    print("本机串口")
    print("=" * 70)
    for p in list_ports.comports():
        print("  %-8s %s" % (p.device, p.description))
    print()
    print("  本项目的既有分工：")
    print("    COM26  485→USB（CH340）  ← **STM32 ASCII 控制台**（2026-11-12 接入）")
    print("    COM35  ESP32              ← 屏侧 CLI（cardiolite>），`485-stat` 在这边")
    print("    COM3   ST-Link VCP       ← 固件未用（ECG_COMM_PORT=UART5，别指望它有输出）")
    print("    COM33/34 J-Link CDC UART ← RTT/调试用")


def send(port, baud, cmds, wait, rts_mode, as_hex, grep=None, hard=None):
    """逐条发送，逐条读回。返回 [(cmd, bytes, 匹配行数)]，供上层断言。

    🛑 **`hard` 是必须的**：起流后总线上是**连续**二进制帧（`_popoutcnt=25` ⇒ 20 帧/s、
    109 B/帧 ≈ 2.2 kB/s），若读循环"有数据就顺延"，`t_end` 会被无限推后 ⇒ **永不返回**
    （2026-11-12 实测：进程被外层 SIGTERM 杀掉，且 stdout 还没 flush，表现为"无任何输出"）。
    ⇒ 单条命令的绝对上限 = `hard`（默认 max(wait*3, 1.5) s），到点无条件返回。
    """
    pat = re.compile(grep) if grep else None
    hard = hard if hard else max(wait * 3.0, 1.5)
    results = []
    with serial.Serial(port, baud, timeout=0.05) as sp:
        sp.reset_input_buffer()
        for cmd in cmds:
            if rts_mode:
                sp.rts = True
                time.sleep(0.02)
            sp.write((cmd + "\r\n").encode("ascii"))
            sp.flush()
            if rts_mode:
                time.sleep(0.10)          # 让 STM32 收全并开始回话
                sp.rts = False            # 松总线 ⇒ 才收得到回话
            t_start = time.time()
            buf = bytearray()
            t_end = t_start + wait
            while True:
                now = time.time()
                if now >= t_end or (now - t_start) >= hard or len(buf) > 262144:
                    break
                n = sp.in_waiting
                if n:
                    buf += sp.read(n)
                    t_end = time.time() + 0.10   # 有数据就顺延（但受 hard 钳制）
                else:
                    time.sleep(0.01)
            data = bytes(buf)
            print("─" * 70)
            print(">> %s" % cmd)
            sys.stdout.flush()
            if not data:
                print("   (无回显)")
                results.append((cmd, data, 0))
                continue
            if as_hex:
                print("   " + " ".join("%02X" % b for b in data))
                results.append((cmd, data, len(data)))
                continue
            n_show = 0
            for line in data.decode("utf-8", "replace").replace("\r", "").split("\n"):
                if not line.strip():
                    continue
                if pat is not None and not pat.search(line):
                    continue
                print("   " + line)
                n_show += 1
            if pat is not None and n_show == 0:
                print("   (收 %d B，无行匹配 /%s/)" % (len(data), grep))
            results.append((cmd, data, n_show))
    return results


def main():
    ap = argparse.ArgumentParser(add_help=True, description="RS485 ASCII 控制台（STM32 UART5）")
    ap.add_argument("cmds", nargs="*", help="要发送的 ASCII 命令（不带 \\r\\n）")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--wait", type=float, default=DEFAULT_WAIT, help="每条命令读回时长(s)")
    ap.add_argument("--rts", dest="rts", action="store_true", default=None, help="强制 RTS 方向控制")
    ap.add_argument("--no-rts", dest="rts", action="store_false", help="不做 RTS 方向控制")
    ap.add_argument("--hex", action="store_true", help="按十六进制打印")
    ap.add_argument("--grep", default=None, help="只打印匹配的正则（出流时必用）")
    ap.add_argument("--hard", type=float, default=None,
                    help="单条命令的**绝对**读回上限(s)，默认 max(wait*3, 1.5)。"
                         "起流后总线连续有数据，没有它读循环永不返回")
    args = ap.parse_args()

    if not args.cmds:
        show_ports()
        print()
        print("=" * 70)
        print("探活：往 %s 发一条 `test notch`" % args.port)
        print("=" * 70)
        res = send(args.port, args.baud, ["test notch"], args.wait, True, args.hex,
                   "notch|mode|profile", args.hard)
        if not res or not res[0][1]:
            print("\n!! 无回显。按顺序查：① 485 转换器是否插着（设备管理器有没有 CH340）"
                  "\n   ② A/B 线是否接反 ③ 是否没接 RTS 方向控制（试 --no-rts）"
                  "\n   ④ 板子是否已上电并烧录（固件只在主循环里回显）")
            return 1
        return 0

    rts = True if args.rts is None else args.rts
    send(args.port, args.baud, args.cmds, args.wait, rts, args.hex, args.grep, args.hard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
