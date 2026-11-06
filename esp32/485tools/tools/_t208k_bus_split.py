#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t208k_bus_split.py —— 把「485 总线死了」与「STM32/UT-2211 侧死了」切开

背景（11-05）
-------------
并挂 ESP32 之后：COM27 发 `test snapshot` ⇒ 0 B；发二进制 START ⇒ 0 B、0 帧头。
但 20 分钟前同一条链路（无 ESP32）20 s 跑 400 帧 / CRC 400/400 / SEQ 零跳变。
需要判断：是总线被并挂拖死，还是 STM32/UT-2211 那段自己断了。

判据设计
--------
用 ESP32 自己的 `rx` 计数当**第四个观察者**：
    COM27 发 3 帧 START（21 B）→ ESP32 的 rx 若 +21 ⇒ **总线物理完好**，
    责任在 STM32/UT-2211 一侧（它没收到 / 没回应）。
    若 ESP32 的 rx 也纹丝不动 ⇒ **总线真的塌了**（并挂把线拉死）。

    🛑 ESP32 的 `rx` 是 rs485_port.c 里的软件计数，只要 RO 上有字节就涨，
       与 STM32 无关，因此是干净的证词。

用法
----
    python _t208k_bus_split.py --usb COM27 --esp COM35
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
START = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
STOP = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54])

KEYS = ("rx", "tx", "frames", "crc", "resync", "bad_len")


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
    p.write(b"485-stat\r\n")
    txt = drain(p, 0.7).decode("utf-8", "replace")
    m = RE_STAT.search(txt)
    if not m:
        return None, txt
    return dict(zip(KEYS, (int(x) for x in m.groups()))), txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM27")
    ap.add_argument("--esp", default="COM35")
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
        print(f"[FAIL] 打不开 {a.esp}：{e}")
        return 2

    try:
        time.sleep(0.3)
        pu.reset_input_buffer()
        pe.reset_input_buffer()
        pe.write(b"\r\n")
        drain(pe, 0.4)

        e0, raw0 = esp_stat(pe)
        if e0 is None:
            print("[FAIL] ESP32 不应答 485-stat（USB 侧就断了？）")
            print("原始回显: " + raw0.strip().replace("\r\n", " | ")[:300])
            return 3

        # 🛑 全零 = ESP32 重启过、485 未初始化（此时 Δrx 恒 0，毫无证据力）
        if e0["tx"] == 0 and e0["rx"] == 0 and e0["frames"] == 0:
            print("⚠ ESP32 统计全零 ⇒ 485 未初始化（重启过），先 485-start")
            pe.write(b"485-start\r\n")
            drain(pe, 2.0)
            e0, raw0 = esp_stat(pe)
            if e0 is None:
                print("[FAIL] 485-start 之后仍读不到 485-stat")
                print("原始回显: " + raw0.strip().replace("\r\n", " | ")[:300])
                return 3
            if e0["tx"] == 0 and e0["rx"] == 0:
                print("⚠ 485-start 似乎没生效（stat 仍全零），继续但证据力打折")
        print(f"ESP32 基线: {e0}")

        # ---- 实验：COM27 发 3 帧 START，看 ESP32 的 rx 涨不涨 -----------------
        print(f"\n① {a.usb} 连发 3 帧 START（共 {3*len(START)} B），看 ESP32 的 rx 是否跟随")
        pu.reset_input_buffer()
        for i in range(3):
            pu.write(START)
            pu.flush()
            time.sleep(0.25)
        time.sleep(0.6)

        e1, raw1 = esp_stat(pe)
        d_rx = e1["rx"] - e0["rx"]
        d_bad = e1["bad_len"] - e0["bad_len"]
        d_rs = e1["resync"] - e0["resync"]
        print(f"   ESP32 rx: {e0['rx']} → {e1['rx']}  (Δ{d_rx:+d} B，期望 +21)")
        print(f"   ESP32 bad_len Δ{d_bad:+d}，resync Δ{d_rs:+d}")

        # ---- 反向：ESP32 发 START，问 COM27 听到没有 -------------------------
        print(f"\n② ESP32 发 3 帧 START，{a.usb} 监听")
        pu.reset_input_buffer()
        for i in range(3):
            pe.write(b"485-send start\r\n")
            drain(pe, 0.35)
        bus = drain(pu, 1.2)
        cnt = bus.count(START)
        print(f"   {a.usb} 收到 {len(bus)} B，其中完整 START 帧 {cnt} 次")
        if 0 < len(bus) <= 120:
            print("   原始 hex: " + " ".join(f"{b:02X}" for b in bus))

        e2, _ = esp_stat(pe)
        print(f"   ESP32 tx: {e1['tx']} → {e2['tx']}  (Δ{e2['tx']-e1['tx']:+d} B)")

        # ---- 收尾：发 STOP ----------------------------------------------------
        pu.write(STOP)
        pu.flush()

        print("\n" + "=" * 62)
        print("[判读]")
        if d_rx >= 15:
            print(f"  ✅ 总线物理完好：ESP32 收到了 COM27 发的 {d_rx} B")
            print("     ⇒ 并挂没有把总线拉死；责任在 STM32 / UT-2211 一侧")
            print("        （它没收到，或收到了但 AFE 起不来 / 没回显）")
            if cnt > 0:
                print("  ✅ ESP32 的发送也确实上了总线（COM27 看见整帧）")
                print("     ⇒ 两节点总线互通 ⇒ 只剩 STM32 那一端不回话")
        elif d_rx <= 1:
            print(f"  ❌ 总线已塌：ESP32 一个字节都没收到（Δ{d_rx:+d}）")
            print(f"     ⇒ 并挂就是杀手（双 120Ω 终端 / 双偏置互压 / 某节点把线拉死）")
            print("     ⇒ 先摘掉 ESP32 的 A/B，复测确认链路能恢复")
        else:
            print(f"  ⚠ 中间态：ESP32 只收到 {d_rx}B（期望 21B）⇒ 总线部分失效/严重丢字节")
            print("     注意 bad_len Δ{} 与 resync Δ{}（大 ⇒ 碰撞或帧错位）".format(d_bad, d_rs))
        return 0
    finally:
        for p in (pe, pu):
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
