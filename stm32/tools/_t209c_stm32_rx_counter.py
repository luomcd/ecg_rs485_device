#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209c_stm32_rx_counter.py —— 直接读 STM32 RAM 里的 UART5 收数计数器判下行

为什么必须这么判
----------------
    之前用 ESP32 的 `bad_len` / `crc_err` 推断「字节没到 STM32」是**循环论证**：
    那两个是 ESP32 自己收帧的计数器，描述的是上行，跟 STM32 收到什么毫无关系。
    真正能区分「没到」和「到了但乱码」的只有 STM32 侧的两个数：
        comm_rx_events  —— UART5 的 IDLE/满包事件数（= 收到过一次「一段字节」）
        comm_rx_loss    —— ORE / 重挂失败次数（= 线上有过噪声/溢出）
    它们在 `comm_port.c` 里维护，`comm_port.h:97` 明写「ST-LINK watch 可读」。

做法
----
    ST-Link 直接 `-u` 读 RAM（`mode=hotplug` 不软复位；用 mode=normal 会清空 ⇒ 假 0）。
    attach 会短暂 halt 内核，所以只在发送前后各读一次，不在发送窗口内挂着。

判读表
------
    Δevents > 0 且 STM32 仍无回应  ⇒ 字节到了，是解析/内容问题（波特率/帧格式）
    Δevents = 0 且 Δloss = 0       ⇒ 一个字节都没到 PD2（UT-2211 的 232 发送侧 / 接线）
    Δloss > 0                      ⇒ 线上有噪声或 break，ORE/FE 把接收打断

用法
----
    python _t209c_stm32_rx_counter.py --esp COM35
    python _t209c_stm32_rx_counter.py --read-only          # 只看当前值
"""
import argparse
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

# 来自 `arm-none-eabi-nm demo_ads1293.elf`（2026-11-04 构建，即板上固件）
BASE = 0x200010A8
SPAN = 0x400
OFF = {
    "tx_frames":  0x200010A8 - BASE,   # STM32 成功发起的发送帧数
    "tx_drops":   0x200010AC - BASE,
    "rx_events":  0x200011B4 - BASE,   # ★UART5 收到字节段的事件数
    "bconvstart": 0x20001421 - BASE,   # 采集运行标志（1=RUN）
    "rx_loss":    0x20001430 - BASE,   # ★UART5 溢出/重挂失败
    "tx_busy":    0x2000143A - BASE,
}


def read_ram():
    path = Path(tempfile.gettempdir()) / "_t209c_ram.bin"
    # 🛑 不能 text=True：CLI 的进度条里混着 GBK 字节（0xB1…），utf-8 解码会抛
    r = subprocess.run([CLI, "-c", "port=SWD", "mode=hotplug",
                        "-u", hex(BASE), hex(SPAN), str(path)],
                       capture_output=True, timeout=90)
    txt = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    if "Data read successfully" not in txt:
        raise RuntimeError("读 RAM 失败：" + txt.strip().splitlines()[-1][:120])
    raw = path.read_bytes()
    if len(raw) < SPAN:
        raise RuntimeError(f"读回长度不足：{len(raw)} < {SPAN}")
    out = {}
    for k, o in OFF.items():
        out[k] = struct.unpack_from("<I", raw, o)[0]
    return out


def fmt(d):
    return (f"events={d['rx_events']:<6d} loss={d['rx_loss']:<6d} "
            f"bconvstart={d['bconvstart']} tx_frames={d['tx_frames']} "
            f"tx_drops={d['tx_drops']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--send", default="snap",
                    help="逗号分隔的 485-send 参数，默认 snap；可写 snap,snap,start")
    a = ap.parse_args()

    print("① 读 STM32 RAM 基线（ST-Link hotplug，不清 RAM）")
    d0 = read_ram()
    print("   " + fmt(d0))

    if a.read_only:
        return 0

    import serial
    print(f"\n② 打开发送端 {a.esp}，485-start")
    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\r\n")
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"485-start\r\n")
        time.sleep(2.0)
        p.reset_input_buffer()

        n = 0
        for what in [w.strip() for w in a.send.split(",") if w.strip()]:
            p.reset_input_buffer()
            p.write(f"485-send {what}\r\n".encode())
            p.flush()
            n += 1
            print(f"   已发 485-send {what}")
            time.sleep(0.8)
        p.reset_input_buffer()
    finally:
        p.close()

    time.sleep(1.0)
    print("\n③ 再读 STM32 RAM")
    d1 = read_ram()
    print("   " + fmt(d1))

    de = d1["rx_events"] - d0["rx_events"]
    dl = d1["rx_loss"] - d0["rx_loss"]
    print("\n" + "=" * 66)
    print(f"  发出 {n} 条下行命令（共 {n * 7} B）")
    print(f"  STM32 UART5：事件 Δ{de:+d}   loss Δ{dl:+d}   RUN 标志={d1['bconvstart']}")
    print()
    if de > 0:
        print("[判定] ⚠ **线上有字节到达 PD2**，但 STM32 没回应")
        print("       ⇒ 断点在「解析」而不是「传输」：查波特率/帧格式/CRC")
        return 1
    if dl > 0:
        print("[判定] ⚠ 只有错误计数在涨（ORE/FE）⇒ 线上有噪声或 break")
        return 1
    print("[判定] ❌ **STM32 一个字节都没收到**（events 与 loss 双零）")
    print("       ⇒ 断点在 STM32 上游：UT-2211 的 232 发送侧 / MAX3232 R1 / PD2 走线")
    print("       ⇒ 必测：UT-2211 DB9 pin2(SOUT) 对 pin5(GND) 应 ≈ −5~−10 V（空闲 mark）")
    return 0 if de == 0 and dl == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
