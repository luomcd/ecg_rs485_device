"""T208i —— 并挂对照：ESP32 下行是否真的上了总线（判据全在 COM27 侧）

拓扑：
    STM32 → TTL转232 → UT-2211 → 485 ─┬─ USB-485 (COM27, PC 监听/发停止)
                                       └─ ESP32 SP3485 (COM35, 发 START)

判据设计（不依赖 ESP32 自报，全部落在第三个观察者 COM27 上）：
    ① COM27 发 STOP        ⇒ STM32 停流（已知必通，COM27 驱动）
    ② ESP32 发 0x01 START  ⇒ 若字节真上了总线且 UT-2211 收得到，STM32 会起采
    ③ COM27 监听 4 s       ⇒ 出现 `AA 55 01 35` 帧流 = ESP32 下行通

    COM27 收不到帧流 ⇒ ESP32 的 START 没进 STM32：
        - 若 ESP32 的 `tx` 涨了而 COM27 一个字节都没看到 ⇒ 信号没上总线（驱动/DE）
        - 若如此，说明并挂也救不回来 ⇒ 转量 A/B 差分

用法：
    python _t208i_dual_probe.py --usb COM27 --esp COM35
"""
import argparse
import re
import sys
import time

import serial

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)", re.S)
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
    p.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(p, 0.6).decode("utf-8", "replace"))
    if not m:
        return None
    return dict(zip(("rx", "tx", "frames", "crc"), (int(x) for x in m.groups())))


def count_frames(buf):
    n = 0
    i = 0
    while i + 59 <= len(buf):
        if buf[i] == 0xAA and buf[i + 1] == 0x55 and buf[i + 2] == 0x01 and buf[i + 3] == 0x35:
            n += 1
            i += 59
        else:
            i += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="COM27")
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--listen", type=float, default=4.0)
    a = ap.parse_args()

    try:
        pu = serial.Serial(a.usb, 115200, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {a.usb}：{e}（串口助手是否占着？）")
        return 2
    try:
        pe = serial.Serial(a.esp, 115200, timeout=0.2)
    except Exception as e:
        pu.close()
        print(f"[FAIL] 打不开 {a.esp}：{e}（ESP32 终端是否占着？）")
        return 2

    try:
        time.sleep(0.3)
        pu.reset_input_buffer()
        pe.reset_input_buffer()
        pe.write(b"\r\n")
        drain(pe, 0.4)
        pe.write(b"485-stat\r\n")
        if "未初始化" in drain(pe, 0.7).decode("utf-8", "replace"):
            print("ESP32 485 未初始化 → 485-start")
            pe.write(b"485-start\r\n")
            drain(pe, 1.8)
        e0 = esp_stat(pe)
        print(f"ESP32 基线: rx={e0['rx']} tx={e0['tx']} frames={e0['frames']}")

        print("\n① COM27 发 STOP（先让 STM32 停流，清空总线）")
        pu.write(STOP)
        pu.flush()
        time.sleep(1.2)
        left = drain(pu, 1.0)
        print(f"   STOP 后 1 s 残余 {len(left)} B（应接近 0）")

        print("\n② ESP32 发 0x01 START（485-send start，连发 3 次提高命中率）")
        pu.reset_input_buffer()
        pu.write(b"485-send start\r\n")
        echo = drain(pe, 0.5).decode("utf-8", "replace")
        print("   " + echo.strip().replace("\r\n", " | "))
        burst = bytearray()
        for k in range(2):
            pu.write(STOP)          # 先停流，避免上行帧流淹没证据
            pu.flush()
            time.sleep(0.4)
            pu.reset_input_buffer()
            pe.write(b"485-send start\r\n")
            drain(pe, 0.5)
            burst += drain(pu, 1.2)

        print(f"\n③ COM27 监听 {a.listen:.0f} s ...")
        buf = bytes(burst) + drain(pu, a.listen)
        n = count_frames(buf)

        # ★ 关键：把 COM27 收到的原始字节打出来，确认 ESP32 的 7 字节是否完整
        echo_bytes = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D])
        cnt = buf.count(echo_bytes)
        print(f"   COM27 共 {len(buf)} B；其中 ESP32 START 整帧出现 {cnt} 次")
        if len(buf) <= 200:
            print("   原始 hex: " + " ".join(f"{b:02X}" for b in buf))

        e1 = esp_stat(pe)
        print(f"\nCOM27 收到 {len(buf)} B / 完整帧 {n} 帧（{n/a.listen:.2f} fps）")
        print(f"ESP32: rx={e1['rx']}(Δ{e1['rx']-e0['rx']:+d})  "
              f"tx={e1['tx']}(Δ{e1['tx']-e0['tx']:+d})  "
              f"frames={e1['frames']}(Δ{e1['frames']-e0['frames']:+d})")

        pu.write(STOP)
        pu.flush()

        print()
        if n > 0:
            print("[判定] ✅ ESP32 下行通 —— COM27 观察到 STM32 起采帧流")
            print("       ⇒ 并挂 COM27 之后能通 ⇒ ESP32 侧缺 fail-safe 偏置的可能性最大")
            return 0

        print("[判定] ❌ ESP32 下行仍不通（COM27 没看到帧流）")
        if e1["tx"] > e0["tx"]:
            print(f"       ESP32 自报已发 {e1['tx']-e0['tx']} B，但总线上没有反应")
            print("       ⇒ 信号没上总线（DE 未使能 / 驱动不足）⇒ 量发送瞬间 A/B 差分")
        else:
            print("       ESP32 连 tx 都没涨 ⇒ 命令没发出去（485 未初始化或 CLI 问题）")
        return 1
    finally:
        for p in (pe, pu):
            try:
                p.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
