#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205o_drain_compare.py —— 「PC 侧读不读」对下行落地率的影响（T205）

为什么要做
----------
`_t205n` 暴露了一个此前从未考虑过的变量：

    · `_t205n` 里我**不读**串口（只 sleep），CH340 内积压涨到 **8~11 KB**
      ⇒ 这一轮芯片落地只有 1/6；
    · `_t205m` 里我一边读一边测，芯片落地 8/8 与 7/8。

两种做法下数据差出一个数量级 ⇒ 嫌疑对象从「固件丢命令」转到
**「PC 侧不读 ⇒ 驱动停止取 IN ⇒ 下行也跟着烂」**。

WinApp 是什么模型？`SerialSource.cs:76` 后台线程死循环 `port.Read(bytes,0,4096)`
⇒ **它一直在读**。如果"读"确实影响下行，那猛哥 10/10 就完全解释得通，而我的
17% 测的是「不读」这个非现实工况。

设计
----
两阶段，各 N 轮，唯一差别是**有没有一个后台线程在死读**：

    Phase 1 不读：发 0x05 → sleep → （不读）→ SWD 读芯片
    Phase 2 死读：后台线程 read(256) 死循环 → 发 0x05 → sleep → SWD 读芯片

落地判据统一用 **SWD 直读 `f64.notch_on`**（不受串口判据干扰）。
目标值由芯片状态反推 ⇒ 每轮都是真翻转，杜绝「幂等空操作被算成成功」。

旁证量：`in_waiting`（不读阶段应单调涨）、后台线程读到的帧数。

用法
----
    python tools/_t205o_drain_compare.py --port COM27 --trials 10
"""
import argparse
import re
import subprocess
import sys
import threading
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

CLI = (r"C:\Program Files\STMicroelectronics\STM32Cube"
       r"\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe")
ADDR_FLAGS = 0x200008C0


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


def chip_notch(tries: int = 3):
    for _ in range(tries):
        try:
            out = subprocess.run(
                [CLI, "-c", "port=SWD", "mode=hotplug",
                 "-r32", hex(ADDR_FLAGS), "4", "-q"],
                capture_output=True, text=True, timeout=40).stdout
        except Exception:
            continue
        m = re.search(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
        if m:
            return int(m.group(1), 16) & 0xFF
    return None


class BgReader:
    """照抄 WinApp 的后台读线程：死循环读、丢数据、只计数。"""

    def __init__(self, port) -> None:
        self.port = port
        self.stop = threading.Event()
        self.bytes = 0
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                chunk = self.port.read(256)
            except Exception:
                break
            if chunk:
                self.bytes += len(chunk)

    def start(self) -> None:
        self.t.start()

    def halt(self) -> None:
        self.stop.set()
        self.t.join(timeout=1.0)


def phase(port, n: int, reading: bool, tag: str):
    bg = None
    if reading:
        port.timeout = 0.05
        bg = BgReader(port)
        bg.start()
        time.sleep(0.3)                  # 让它把积压先抽干
    port.reset_input_buffer()
    time.sleep(0.2)

    print()
    print(f"--- {tag}（{'后台死读' if reading else '不读'}）---")
    print(f"{'#':>2} {'发':>2} {'芯片前':>6} {'芯片后':>6} {'落地':>4} "
          f"{'积压B':>7} {'已读B':>8}")
    print("-" * 48)
    ok = 0
    done = 0
    for i in range(1, n + 1):
        f0 = chip_notch()
        if f0 is None:
            print("[FAIL] SWD 读失败，中止本阶段")
            break
        want = 1 - f0
        port.write(build(0x05, bytes([want])))
        port.flush()
        time.sleep(0.50)
        f1 = chip_notch()
        if f1 is None:
            print("[FAIL] SWD 读失败，中止本阶段")
            break
        backlog = 0 if reading else port.in_waiting
        got = bg.bytes if bg else 0
        good = (f1 == want)
        ok += good
        done += 1
        print(f"{i:>2} {want:>2} {f0:>6} {f1:>6} "
              f"{'✓' if good else '✗':>4} {backlog:>7} {got:>8}")
        time.sleep(0.10)

    if bg:
        bg.halt()
    print(f"[{tag}] 落地 {ok}/{done}")
    return ok, done


def main() -> int:
    ap = argparse.ArgumentParser(description="读不读 vs 下行落地率")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=10)
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        time.sleep(0.3)
        if port.in_waiting < 60:                 # 可能 STOP 态，补一发 START
            port.write(build(0x01))
            port.flush()
            time.sleep(0.5)
        print(f"[ok] 起跑 in_waiting={port.in_waiting} B 芯片 notch={chip_notch()}")

        r1 = phase(port, args.trials, False, "Phase 1")
        r2 = phase(port, args.trials, True, "Phase 2")

        print()
        print("=" * 60)
        print(f"不读   {r1[0]}/{r1[1]} = {r1[0] / max(1, r1[1]) * 100:.1f}%")
        print(f"死读   {r2[0]}/{r2[1]} = {r2[0] / max(1, r2[1]) * 100:.1f}%")
        print("=" * 60)
        if r2[0] > r1[0] + r2[1] // 4:
            print("[结论] ★'PC 侧在读'显著提升下行落地 ⇒ 我此前的 17% 测的是")
            print("       「不读」这个非现实工况；WinApp 常驻后台读 ⇒ 10/10 合理。")
            print("       固件侧无需改命令通道，改的是**上位机必须持续读**这条纪律。")
        elif r1[0] >= r2[0]:
            print("[结论] '读不读'无影响 ⇒ 下行丢失另有原因（回到固件 RX 链路）。")
        else:
            print("[结论] 死读略好但差值不足 1/4，样本量不够，建议 --trials 加大复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
