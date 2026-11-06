#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209j_rx_rawframe.py —— 直接 dump STM32 侧 UART5 收到的**原始字节**

为什么要有它
------------
    `comm_rx_events` 只能证明「收到过一段字节」（`_t209c` 已实测 events +1），
    但证明不了**长度和内容对不对**。实测现象：
        `485-send snap`     → 回执 1 帧（通）
        `485-send ch2 1`    → 回执 0 帧（不通），但 events 照样 +1
    同一 STOP 态、同一线路、同样 8 B 以内的短帧 ⇒ 差异只可能在
    「STM32 实收的那串字节」本身。本脚本把这串字节原样打出来。

读哪几块（地址来自 `arm-none-eabi-nm demo_ads1293.elf`，2026-11-04 构建）
    s_rx_pub_idx  0x200010B0   最近一次 RxEvent 的页号（0/1 轮换）
    s_rx_pub      0x200010B4   2×128 B —— 回调把 s_rx_work 抄进来，**主循环不清**
    s_rx_work     0x20001370   128 B —— HAL_UARTEx_ReceiveToIdle_IT 的工作缓冲
    VCOM_RxData   0x200013F0   非 0 = 主循环还有待处理帧（★主循环会清它）
    VCOM_RxLength 0x200013F4   待处理帧长度

判读
----
    前 8 B == AA 55 80 02 00 06 01 C2  ⇒ 字节**逐位正确**，问题在固件分派逻辑
    前 8 B 少最后一字节（C2 缺）        ⇒ 半双工方向切换把 CRC 那个字节吃了
    前 8 B 里出现 00 / 乱序              ⇒ 线上波形有损（UT-2211 供电/共地）
    整段全 0 / 仍是上一条帧              ⇒ 本帧根本没触发 RxEvent

🛑 ST-Link 必须 `mode=hotplug`（`mode=normal` 会软复位 ⇒ RAM 假空白）
🛑 子进程输出不能 `text=True`（CLI 进度条混着 GBK 字节 0xB1…）

用法
----
    py _t209j_rx_rawframe.py                          # 只读当前 RAM
    py _t209j_rx_rawframe.py --send "ch2 1"           # 先发一条再读
    py _t209j_rx_rawframe.py --send "snap" --send "ch2 1"
"""
import argparse
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

BASE = 0x200010A8
SPAN = 0x400

ADDR = {
    "tx_frames":    0x200010A8,
    "tx_drops":     0x200010AC,
    "rx_pub_idx":   0x200010B0,
    "rx_pub":       0x200010B4,   # 2 x 128
    "rx_events":    0x200011B4,
    "rx_work":      0x20001370,   # 128
    "VCOM_RxData":  0x200013F0,
    "VCOM_RxLength":0x200013F4,
    "bconvstart":   0x20001421,
    "rx_loss":      0x20001430,
}

PUB_PAGE = 128


def read_ram():
    """一次读 SPAN 字节，返回 (bytes, 符号表值)。"""
    path = Path(tempfile.gettempdir()) / "_t209j_ram.bin"
    if path.exists():
        path.unlink()
    r = subprocess.run([CLI, "-c", "port=SWD", "mode=hotplug",
                        "-u", hex(BASE), hex(SPAN), str(path)],
                       capture_output=True, timeout=90)
    txt = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    if "Data read successfully" not in txt:
        raise RuntimeError("读 RAM 失败：" + txt.strip().splitlines()[-1][:160])
    raw = path.read_bytes()
    if len(raw) < SPAN:
        raise RuntimeError(f"读回长度不足：{len(raw)} < {SPAN}")

    def u32(a):
        return struct.unpack_from("<I", raw, a - BASE)[0]

    vals = {
        "tx_frames": u32(ADDR["tx_frames"]),
        "tx_drops": u32(ADDR["tx_drops"]),
        "rx_pub_idx": u32(ADDR["rx_pub_idx"]),
        "rx_events": u32(ADDR["rx_events"]),
        "VCOM_RxData": u32(ADDR["VCOM_RxData"]),
        "VCOM_RxLength": u32(ADDR["VCOM_RxLength"]),
        "bconvstart": u32(ADDR["bconvstart"]),
        "rx_loss": u32(ADDR["rx_loss"]),
    }
    return raw, vals


def hexdump(tag, buf, base_addr, max_bytes=32):
    """只打前 max_bytes，按 16 B 一行；同时给出 'AA 55 …' 串便于肉眼比对。"""
    n = min(max_bytes, len(buf))
    print(f"  {tag} (0x{base_addr:08X}, 前 {n} B):")
    for off in range(0, n, 16):
        chunk = buf[off:off + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        print(f"    +{off:02d}  {hexs:<48s}")
    print(f"    串: {' '.join(f'{b:02X}' for b in buf[:n])}")


def show(raw, vals, title):
    print(f"\n{title}")
    print(f"  events={vals['rx_events']}  loss={vals['rx_loss']}  "
          f"bconvstart={vals['bconvstart']}  tx_frames={vals['tx_frames']}  "
          f"tx_drops={vals['tx_drops']}")
    print(f"  VCOM_RxData={vals['VCOM_RxData']}  "
          f"VCOM_RxLength={vals['VCOM_RxLength']}  pub_idx={vals['rx_pub_idx']}")

    pub_all = raw[ADDR["rx_pub"] - BASE: ADDR["rx_pub"] - BASE + 2 * PUB_PAGE]
    cur = vals["rx_pub_idx"]
    cur_page = cur % 2
    older = 1 - cur_page
    hexdump(f"s_rx_pub[最新页 {cur_page}]", pub_all[cur_page * PUB_PAGE:(cur_page + 1) * PUB_PAGE],
            ADDR["rx_pub"] + cur_page * PUB_PAGE)
    hexdump(f"s_rx_pub[上一页 {older}]", pub_all[older * PUB_PAGE:(older + 1) * PUB_PAGE],
            ADDR["rx_pub"] + older * PUB_PAGE)
    work = raw[ADDR["rx_work"] - BASE: ADDR["rx_work"] - BASE + 128]
    hexdump("s_rx_work", work, ADDR["rx_work"])


def send_over_esp(port, items):
    try:
        import serial
    except ImportError:
        sys.exit("缺 pyserial —— 用 tools\\py.cmd 跑本脚本")

    print(f"\n② 打开 {port}（DTR/RTS 先置 False，避免 USB-CDC 复位），485-start")
    p = serial.Serial()
    p.port = port
    p.baudrate = 115200
    p.timeout = 0.2
    p.dtr = False
    p.rts = False
    p.open()
    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\r\n")
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"485-start\r\n")
        time.sleep(2.0)
        p.reset_input_buffer()
        for what in items:
            p.write(f"485-send {what}\r\n".encode())
            p.flush()
            print(f"   已发 485-send {what}")
            time.sleep(1.0)
        p.reset_input_buffer()
    finally:
        p.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--send", action="append", default=[],
                    help="要发的 485-send 参数，可重复：--send snap --send 'ch2 1'")
    a = ap.parse_args()

    raw0, v0 = read_ram()
    show(raw0, v0, "① 发送前（STM32 RAM 现状）")

    if not a.send:
        return 0

    send_over_esp(a.esp, a.send)
    time.sleep(1.0)

    raw1, v1 = read_ram()
    show(raw1, v1, "③ 发送后")
    print(f"\n增量：events +{v1['rx_events'] - v0['rx_events']}  "
          f"loss +{v1['rx_loss'] - v0['rx_loss']}  "
          f"pub_idx {v0['rx_pub_idx']} -> {v1['rx_pub_idx']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
