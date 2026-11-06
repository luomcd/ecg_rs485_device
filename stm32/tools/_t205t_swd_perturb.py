#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205t_swd_perturb.py —— SWD attach 是否就是「下行丢失」的真凶（T205 定案）

矛盾现场
--------
同一块板、同一条线、同一个 8 字节帧，两种测法差出一个数量级：

    测法 A  `_t205p_clean_rate.py`（**全程不碰 SWD**）
            复位后 100/100、20/20、80/80 —— 一次没丢
    测法 B  `_t205m/_t205o/_t205q`（**每条命令前后各来一次 SWD 读/写**）
            30%~40%，最差 0/10

而且 B 的直方图显示芯片 RX 缓冲里剩下的是**帧的尾巴**（`02 00 05 00 FA`），
`comm_rx_loss` 每条命令 +2.8；A 的 `comm_rx_loss` **一动不动**。

机理
----
`HAL_UARTEx_ReceiveToIdle_IT`（中断模式）在 `comm_port.c` 里挂接收。
`STM32_Programmer_CLI -c port=SWD mode=hotplug` **每次都要 attach 调试器**，
attach 期间内核被短暂 halt、中断不响应 ⇒ UART 硬件继续收字节但没人搬 ⇒
**ORE（溢出）** ⇒ HAL 走 `UART_EndRxTransfer()` 终止接收 ⇒ 当前这包**只剩半截**，
`HAL_UART_ErrorCallback` 再把接收重挂上。这正好解释「帧头被吞、尾巴孤零零留下」。

设计（单变量）
--------------
两种模式跑同样的 10 次下发，唯一差别是**下发前后有没有 SWD 读芯片**：

    --swd     每条命令前后各一次 `-r32 f64.notch_on`（= 我原来的测法）
    （默认）   一次 SWD 都不碰（= 测法 A）

用法
----
    python tools/_t205t_swd_perturb.py --port COM27 --trials 10          # 无 SWD
    python tools/_t205t_swd_perturb.py --port COM27 --trials 10 --swd    # 有 SWD
"""
import argparse
import collections
import re
import subprocess
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

CLI = (r"C:\Program Files\STMicroelectronics\STM32Cube"
       r"\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe")
ADDR_FLAGS = 0x200008C0
ADDR_LOSS = 0x20001430


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


def swd_read(addr: int):
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug", "-r32", hex(addr), "4", "-q"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    m = re.search(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
    return None if not m else int(m.group(1), 16)


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            i = self.buf.find(b"\xAA\x55")
            if i < 0:
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 5:
                return
            length = self.buf[3] | (self.buf[4] << 8)
            tot = 6 + length
            if tot > 260:
                del self.buf[0:1]
                continue
            if len(self.buf) < tot:
                return
            fr = bytes(self.buf[:tot])
            if crc8(fr[2: tot - 1]) != fr[tot - 1]:
                del self.buf[0:1]
                continue
            if self.buf[2] == 0x01 and length in (53, 103):
                self.sta.append(fr[7])
            del self.buf[:tot]

    def mode(self, tail: int = 8):
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-tail:]).most_common(1)[0][0] >> 1) & 1


def read_fresh(port, frames: int = 8, timeout: float = 1.0) -> Reader:
    port.reset_input_buffer()
    rd = Reader()
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) < frames:
        chunk = port.read(512)
        if chunk:
            rd.feed(chunk)
    return rd


def main() -> int:
    ap = argparse.ArgumentParser(description="SWD attach 是否破坏下行")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--swd", action="store_true", help="下发前后各插一次 SWD 读")
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        rd = read_fresh(port, 10, 1.5)
        if len(rd.sta) < 6:
            port.write(build(0x01))
            port.flush()
            time.sleep(0.5)
            rd = read_fresh(port, 10, 1.5)
        if len(rd.sta) < 6:
            print("[FAIL] 无上行帧")
            return 6
        cur = rd.mode()

        loss0 = swd_read(ADDR_LOSS) if args.swd else None
        print(f"[ok] 模式={'前后插 SWD' if args.swd else '全程不碰 SWD'}  "
              f"notch={cur}  rx_loss={'n/a' if loss0 is None else loss0}")
        print()
        print(f"{'#':>2} {'发':>2} {'读回':>4} {'结果':>4}")
        print("-" * 26)
        ok = 0
        for i in range(1, args.trials + 1):
            want = 1 - cur
            if args.swd:
                swd_read(ADDR_FLAGS)               # ← 唯一变量：attach 调试器
            port.write(build(0x05, bytes([want])))
            port.flush()
            if args.swd:
                swd_read(ADDR_FLAGS)
            time.sleep(0.30)
            rd = read_fresh(port, 8, 1.0)
            got = rd.mode()
            good = (got == want)
            ok += good
            if good:
                cur = want
            print(f"{i:>2} {want:>2} {str(got):>4} {'✓' if good else '✗':>4}")

        loss1 = swd_read(ADDR_LOSS) if args.swd else None
        print("-" * 26)
        print(f"落地 {ok}/{args.trials} = {ok / max(1, args.trials) * 100:.1f}%"
              + ("" if loss1 is None
                 else f"    rx_loss {loss0} → {loss1}（Δ{loss1 - loss0}）"))
        print()
        if args.swd and ok < args.trials * 0.7:
            print("[判读] 插了 SWD 就掉 ⇒ **SWD attach 是测量伪影**，不是固件丢命令。")
        elif not args.swd and ok >= args.trials * 0.9:
            print("[判读] 不碰 SWD 就满分 ⇒ 固件命令通道正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
