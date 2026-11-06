#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208h_pc_start_probe.py —— PC 侧经 TTL-USB 直接给 STM32 UART5 发 START（T208 硬判据）

目的
----
把「STM32 的 PD2 + 固件下行接收到底好不好」与「UT-2211/RS232 那段」彻底切开。

拓扑（必须这样接，不要再串 UT-2211 / TTL转232 模块）：
    PC ── TTL-USB ── TXD → CN9.2 (PD2)      ← 本脚本从这头发
                  ── RXD ← CN9.1 (PC12)     ← 本脚本从这头收帧流
                  ── GND ── GND             ← 必须共地
    🛑 先把 CN9.1–CN9.2 的短接线**拆掉**，否则 PC 的 TXD 与 STM32 的 TX 输出对撞。

为什么用 TTL-USB 而不是 UT-2211：TTL-USB 的 TXD 是**自供电推挽输出**，空闲恒高（3.3 V）。
UT-2211 是无源电荷泵，STM32 静默时它没电（T208f 已用 16524 帧证过），所以不能作为判据基准。

判据
----
    起采  ⇒ 3 s 内收到 `AA 55 01 35`（TYPE=0x01, LEN=0x35）帧头，帧率约 21.7 fps
    不起采 ⇒ 零字节，或只有 ASCII 横幅

退出码：0 起采成功 ／ 1 未起采 ／ 2 端口打不开

用法
----
    python _t208h_pc_start_probe.py --port COM7
    python _t208h_pc_start_probe.py --port COM7 --cmd stop     # 反向验证
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("需要 pyserial：用工作区自带 python 跑")
    sys.exit(2)


# ---- CRC8（与固件 ecg_crc8.c:54-67 位运算版逐位同构，T208 已对拍） ----------
def _crc8_bytes(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def build_frame(cmd, arg=b""):
    """AA 55 | 80 | LEN(2B LE) | CMD | ARG | CRC8，LEN = 1 + len(ARG)，总长 = 6 + LEN"""
    payload = bytes([0x80, 1 + len(arg), 0x00, cmd]) + arg
    return bytes([0xAA, 0x55]) + payload + bytes([_crc8_bytes(payload)])


FRAMES = {
    "start": build_frame(0x01),          # AA 55 80 01 00 01 5D
    "stop":  build_frame(0x02),          # AA 55 80 01 00 02 54
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="TTL-USB 的 COM 号（如 COM7）")
    ap.add_argument("--cmd", default="start", choices=("start", "stop"))
    ap.add_argument("--listen", type=float, default=3.0, help="发完后监听秒数")
    ap.add_argument("--repeat", type=int, default=2, help="重复发送次数")
    a = ap.parse_args()

    frame = FRAMES[a.cmd]
    print(f"准备发送 {a.cmd.upper()}: {' '.join(f'{b:02X}' for b in frame)}  "
          f"({len(frame)} B)")

    try:
        p = serial.Serial(a.port, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.port}：{e}")
        return 2

    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        for i in range(a.repeat):
            p.write(frame)
            p.flush()
            print(f"  已发第 {i + 1} 次")
            time.sleep(0.35)

        print(f"监听 {a.listen:.0f} s ...")
        # 🛑 发送窗口内 STM32 已经在出帧，这些字节积在 PC 缓冲里。
        #    不丢弃就会把 ~1 s 的积压算进 3 s 窗口，fps 被抬高 30%（曾报 26.0 fps，真值 19.9）。
        p.reset_input_buffer()
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < a.listen:
            n = p.in_waiting
            if n:
                buf += p.read(n)
            else:
                time.sleep(0.02)

        if not buf:
            print("\n[结果] 零字节返回 —— 未起采")
            print("       ⇒ PD2/固件下行接收仍有问题（这时该查代码/PD2，不是 UT-2211）")
            return 1

        ascii_preview = buf[:80].decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        print(f"\n[结果] 共收到 {len(buf)} B；前 80 B 文本预览：{ascii_preview}")

        up = buf.count(b"\xAA\x55\x01")
        stat = buf.count(b"\xAA\x55\x03")
        print(f"        显示帧头 AA 55 01 出现 {up} 次，快照帧头 AA 55 03 出现 {stat} 次")

        if up > 0:
            fps = up / a.listen
            print(f"\n[判定] ✅ 起采成功 —— 约 {fps:.1f} fps")
            print("       ⇒ STM32 的 PD2 + 固件下行接收 100% 正常")
            print("       ⇒ 下行不通的责任在 UT-2211 / TTL转232 段（不是代码）")
            return 0

        print("\n[判定] ⚠ 有字节但不是显示帧流（多半是 ASCII 日志），未起采")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
