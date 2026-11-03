#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli_session.py —— 板载 CLI **多命令会话**（可复用统一入口）

为什么要有它：
  `open()` 本身会拉 DTR/RTS 复位板子（见 `serial_capture.py` 顶部那两条硬规矩）。
  验收一条命令就跑一次脚本 ⇒ 6 条命令要复位 6 次、重等 6 次提示符，既慢又容易
  在"还没启动完"时盲发命令。本工具**只复位一次**，然后按序发完所有命令。

  另一个理由：S4 起命令有副作用（写清单），必须"先 A 后 B"地看变化，
  分进程做不到，只能在一个会话里连续敲。

用法：
  tools\\py tools\\cli_session.py --cmds "scan;mit-list;ladder-list"
  tools\\py tools\\cli_session.py --file test_logs/s4_cmds.txt -o test_logs/s4_cli.log
  tools\\py tools\\cli_session.py --cmds "status" --no-reset      # 板子已在跑，只读

输出：UTF-8 纯 LF；每条命令前打 `>>> <cmd>` 标记，命令间留空行，便于人工核对与截取。
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("缺 pyserial（当前解释器里没有）。三选一：\n"
             "  ① 交给 tools\\py.cmd 跑（固定用 WorkBuddy 环境，包最全）：\n"
             "     tools\\py.cmd tools\\cli_session.py --cmds \"scan\"\n"
             "  ② 给当前解释器装上：python -m pip install pyserial\n"
             "  ③ 换用 WorkBuddy 环境解释器：\n"
             "     %USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe\n"
             f"当前解释器：{sys.executable}")

PROMPT = b"cardiolite>"


def open_port(port, baud, reset):
    """开端口。reset=True 时打一个 RTS 低脉冲复位（DTR 全程 False，不进下载模式）。

    🛑 timeout 必须短（0.02 s），不能 0.2 s。USB-Serial-JTAG 的 TX FIFO 很小，
       固件 `printf` 必须等主机来收才会推进；若主机一次 read 就阻塞 0.2 s，
       上一轮的长输出会被排到下一条命令的时间窗里，表现为"命令输出串行滞后"。
    """
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.02
    ser.dtr = False
    ser.rts = False
    ser.open()
    try:
        ser.set_buffer_size(rx_size=1 << 20, tx_size=1 << 16)
    except (AttributeError, NotImplementedError, ValueError, OSError):
        pass
    time.sleep(0.2)
    ser.setDTR(False)
    if reset:
        ser.setRTS(True)
        time.sleep(0.15)
        ser.setRTS(False)
    ser.reset_input_buffer()
    return ser


def drain(ser, seconds):
    """丢掉重连残片。USB-Serial-JTAG 重新枚举那几百毫秒会吐 `escardiolite>` 这类拼接物，
    不清掉会跟后面的命令回显搅在一起 ⇒ 命令被截断。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        ser.read(4096)


def wait_prompt(ser, limit_s=60.0):
    """等提示符。先敲一个回车主动唤出 —— 提示符可能在上电时就打过了，
    我们连上来时已经错过；esp_console 收到空行会重打一次。

    🛑 实测：打 RTS 复位脉冲后，ESP32-S3 的 USB-Serial-JTAG 会**重新枚举**，
       已打开的句柄从此读不到任何字节（不报错、只是静默 0 字节）。
       所以默认 `--no-reset`，靠回车唤提示符；要抓完整启动日志用 serial_capture.py。"""
    ser.write(b"\r\n")
    ser.flush()
    t0 = time.time()
    buf = bytearray()
    while time.time() - t0 < limit_s:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            if PROMPT in buf:
                return True, bytes(buf)
    return False, bytes(buf)


def run_cmd(ser, cmd, limit_s=8.0, quiet_s=0.35, trace=False):
    """发一条命令，收到提示符**并再等一小段静默**才算收工。

    🛑 只看提示符会提前收工：多行输出可能还在 USB 缓冲里没到，
       收工即 `reset_input_buffer()` ⇒ 尾巴丢失、串到下一条命令头上。
    --trace 会逐块打「相对时间 + 字节数 + 前 60 B」，用来定位慢在哪。"""
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    ser.flush()

    buf = bytearray()
    t0 = time.time()
    last = t0
    seen_prompt = False
    while time.time() - t0 < limit_s:
        chunk = ser.read(4096)
        now = time.time()
        if chunk:
            if trace:
                print(f"      +{now - t0:6.2f}s  {len(chunk):4d}B  {chunk[:60]!r}")
            buf += chunk
            last = now
            if PROMPT in buf:
                seen_prompt = True
        elif seen_prompt and (now - last) >= quiet_s:
            break
    return bytes(buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--cmds", default="", help="分号分隔的命令列表")
    ap.add_argument("--file", default=None, help="从文件读命令（一行一条，# 开头跳过）")
    ap.add_argument("--out", "-o", default=None, help="日志输出路径（默认只打屏）")
    ap.add_argument("--per-cmd", type=float, default=8.0, help="每条命令最长等待秒数")
    ap.add_argument("--quiet-s", type=float, default=0.35,
                    help="见到提示符后再等多少秒静默才算收工（默认 0.35）")
    ap.add_argument("--trace", action="store_true",
                    help="逐块打印收包时间戳（定位命令慢在哪一段）")
    ap.add_argument("--no-reset", action="store_true", default=True,
                    help="不复位（默认；复位会让 USB-JTAG 重枚举、句柄失效）")
    ap.add_argument("--reset", dest="no_reset", action="store_false",
                    help="打 RTS 复位脉冲（⚠ 多数情况下会读不到数据，仅供调试）")
    ap.add_argument("--boot-s", type=float, default=12.0, help="等提示符最长秒数")
    args = ap.parse_args()

    cmds = []
    if args.file:
        for line in open(args.file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                cmds.append(line)
    if args.cmds:
        cmds += [c.strip() for c in args.cmds.split(";") if c.strip()]
    if not cmds:
        ap.error("没有命令可发（用 --cmds 或 --file）")

    ser = open_port(args.port, args.baud, not args.no_reset)
    if not args.no_reset:
        drain(ser, 1.5)

    ok, boot = wait_prompt(ser, args.boot_s)
    log = bytearray()
    log += boot
    if not ok:
        ser.close()
        print("🛑 等不到提示符，放弃（板子没启动？串口被占？）", file=sys.stderr)
        if args.out:
            open(args.out, "wb").write(bytes(log))
        return 3

    print(f"提示符就绪，开始发 {len(cmds)} 条命令\n")
    for cmd in cmds:
        t0 = time.time()
        out = run_cmd(ser, cmd, args.per_cmd, args.quiet_s, args.trace)
        log += f"\n\n>>> {cmd}\n".encode()
        log += out
        text = out.decode("utf-8", "replace").replace(PROMPT.decode(), "")
        print(f">>> {cmd}")
        print(text.strip())
        print(f"    [{time.time() - t0:.1f}s]\n")

    ser.close()

    if args.out:
        with open(args.out, "wb") as fp:
            fp.write(bytes(log))
        print(f"日志 → {args.out}（{len(log)} B）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
