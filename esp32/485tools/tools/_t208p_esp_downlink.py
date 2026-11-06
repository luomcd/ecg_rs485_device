#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208p_esp_downlink.py —— ESP32 下行能否穿透 UT-2211（带 STOP 延迟门控）

拓扑
----
    STM32 → TTL转232 → UT-2211 → 485总线 ┬─ USB-485 (COM26, PC 监听)
                                          └─ ESP32 SP3485 (COM35, 发 START)

为什么不能直接套用旧脚本
------------------------
11-05 实测发现 **STOP 生效有约 4 s 延迟**（SEQ 0x581→0x5D1，80 帧后才停）。
旧脚本 STOP 后只等 1.2 s 就发 START，残流会被错算成「ESP32 下行成功」。
本脚本改为：STOP → 轮询等待总线静默（最多 12 s）→ 才开始实验。

判据
----
    ① 静默确认：STOP 后连续 300 ms 零字节
    ② ESP32 发 START（连发 3 次，半双工下命中率约 50%/次）
    ③ COM26 监听 5 s，统计 `AA 55 01 35` 帧头
       - 有帧 ⇒ ESP32 下行通
       - 无帧但 ESP32 的 tx 涨了 ⇒ 字节发出去了但 STM32 没反应
       - tx 不涨 ⇒ 命令没发出（485 未初始化 / CLI 问题）

用法
----
    python _t208p_esp_downlink.py --usb COM26 --esp COM35
"""
import argparse
import re
import sys
import time

import serial

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)
KEYS = ("rx", "tx", "frames", "crc", "resync", "bad_len")
HDR = b"\xAA\x55\x01\x35"
STOP = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.01)
    return bytes(buf)


def esp_stat(p):
    if p is None:
        return None
    p.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(p, 0.7).decode("utf-8", "replace"))
    return dict(zip(KEYS, (int(x) for x in m.groups()))) if m else None


def wait_quiet(pu, max_s=12.0, quiet_ms=300):
    """等总线连续 quiet_ms 毫秒零字节，返回 (是否静默, 用时, 期间收到字节数)"""
    t0 = time.time()
    quiet_from = None
    got = 0
    while time.time() - t0 < max_s:
        n = pu.in_waiting
        if n:
            got += len(pu.read(n))
            quiet_from = None
        else:
            if quiet_from is None:
                quiet_from = time.time()
            elif (time.time() - quiet_from) * 1000 >= quiet_ms:
                return True, time.time() - t0, got
            time.sleep(0.01)
    return False, time.time() - t0, got


def count_frames(buf):
    n = 0
    i = 0
    while i + 59 <= len(buf):
        if buf[i:i + 4] == HDR:
            n += 1
            i += 59
        else:
            i += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM26")
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--listen", type=float, default=5.0)
    a = ap.parse_args()

    try:
        pu = serial.Serial(a.usb, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.usb}：{e}")
        return 2
    try:
        pe = serial.Serial(a.esp, 115200, timeout=0.2)
    except Exception as e:
        pu.close()
        print(f"[FAIL] 打不开 {a.esp}：{e}（ESP32 串口是否被终端占着？）")
        return 2

    try:
        time.sleep(0.3)
        pu.reset_input_buffer()
        pe.reset_input_buffer()
        pe.write(b"\r\n")
        drain(pe, 0.4)

        e0 = esp_stat(pe)
        if e0 is None:
            print("[FAIL] ESP32 不应答 485-stat")
            return 3
        if e0["rx"] == 0 and e0["tx"] == 0 and e0["frames"] == 0:
            print("⚠ ESP32 485 未初始化（统计全零），先 485-start")
            pe.write(b"485-start\r\n")
            drain(pe, 2.0)
            e0 = esp_stat(pe) or e0
        print(f"ESP32 基线: rx={e0['rx']} tx={e0['tx']} frames={e0['frames']}")

        # ---- ① 停流并等静默 ------------------------------------------------
        print(f"\n① {a.usb} 发 STOP，然后等总线静默（最多 12 s，注意 STOP 有 ~4 s 延迟）")
        pu.write(STOP)
        pu.flush()
        ok, el, got = wait_quiet(pu, 12.0)
        print(f"   静默={ok}  用时 {el:.2f} s  期间仍收到 {got} B"
              f"（≈{got/59:.1f} 帧）")
        if not ok:
            print("   ⚠ 总线一直不静默，后续判据不可靠（可能仍在流）")

        # ---- ② ESP32 发 START ---------------------------------------------
        print(f"\n② ESP32 发 485-send start ×3")
        pu.reset_input_buffer()
        for k in range(3):
            pe.write(b"485-send start\r\n")
            echo = drain(pe, 0.45).decode("utf-8", "replace").strip()
            print(f"   第{k+1}次回显: {echo.replace(chr(13), '').replace(chr(10), ' | ')[:110]}")
            time.sleep(0.25)

        # ---- ③ 监听 --------------------------------------------------------
        print(f"\n③ {a.usb} 监听 {a.listen:.0f} s")
        buf = drain(pu, a.listen)
        n = count_frames(buf)
        print(f"   收到 {len(buf)} B，完整 59 B 帧 {n} 帧（{n/a.listen:.2f} fps）")

        e1 = esp_stat(pe)
        pu.write(STOP)
        pu.flush()

        print("\n" + "=" * 62)
        if e1:
            print(f"ESP32 tx {e0['tx']} → {e1['tx']} (Δ{e1['tx']-e0['tx']:+d} B，期望 +21)")
            print(f"ESP32 rx {e0['rx']} → {e1['rx']} (Δ{e1['rx']-e0['rx']:+d} B)")
        print()
        if n >= 3:
            print(f"[判定] ✅ ESP32 下行通 —— 起了 {n} 帧（{n/a.listen:.1f} fps）")
            print("       ⇒ UT-2211 能收到 ESP32 发的 START")
            return 0
        print("[判定] ❌ ESP32 下行不通（COM26 没看到帧流）")
        if e1 and e1["tx"] > e0["tx"]:
            print(f"       ESP32 自报已发 {e1['tx']-e0['tx']} B，但总线上没有起采反应")
            print("       ⇒ 字节上了总线但 STM32 没受理")
            print("       ⇒ 查 UT-2211 接收、或半双工冲突（COM26 的转换器在占总线）")
        else:
            print("       ESP32 的 tx 都没涨 ⇒ 命令没发出去（485 未初始化/CLI 被拒）")
        return 1
    finally:
        for p in (pe, pu):
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
