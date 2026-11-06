"""T208e —— 后台监听 ESP32 的 rx 变化，用来抓 STM32 复位的上电横幅。

判据：STM32 每次上电/复位，`comm_init()` 末尾会发 42 B ASCII 横幅
      （`comm_port.c:153-161`：`\\r\\n[uart5] ready 115200 8N1  PC12=TX PD2=RX\\r\\n`），
      实测累计约 640 B（含 demo 侧日志）。
      ⇒ rx 跳变 ⇒ **上行物理链路此刻是通的、STM32 活着**
      ⇒ 若跳变后连发 START 仍无 frames ⇒ 问题锁定在下行方向。

用法：python _t208e_rx_watch.py [COM35] [时长秒]
"""
import re
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM35"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
BAUD = 115200

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)


def drain(port, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = port.in_waiting
        if n:
            buf += port.read(n)
        else:
            time.sleep(0.02)
    return buf.decode("utf-8", errors="replace")


def main():
    try:
        p = serial.Serial(PORT, BAUD, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {PORT}：{e}")
        return 2

    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)
        p.write(b"485-stat\r\n")
        head = drain(p, 0.7)
        if "未初始化" in head:
            print("485 未初始化 → 485-start")
            p.write(b"485-start\r\n")
            drain(p, 1.8)
            p.write(b"485-stat\r\n")
            drain(p, 0.7)

        prev = None
        t0 = time.time()
        print(f"=== 监听 {DUR:.0f}s（每 ~2s 一次 485-stat）。请现在复位 STM32 ===")
        while time.time() - t0 < DUR:
            p.write(b"485-stat\r\n")
            txt = drain(p, 0.55)
            m = RE_STAT.search(txt)
            if not m:
                continue
            cur = dict(zip(("rx", "tx", "frames", "crc_err", "resync", "bad_len"),
                           (int(x) for x in m.groups())))
            ts = time.time() - t0
            if prev is None:
                print(f"[{ts:6.1f}s] 基线 rx={cur['rx']} tx={cur['tx']} "
                      f"frames={cur['frames']} resync={cur['resync']}")
            else:
                d = cur["rx"] - prev["rx"]
                mark = ""
                if d > 0:
                    mark = f"   *** rx {d:+d} B ***"
                print(f"[{ts:6.1f}s] rx={cur['rx']} (Δ{d:+d}) tx={cur['tx']} "
                      f"frames={cur['frames']} crc={cur['crc_err']} "
                      f"resync={cur['resync']}{mark}")
            prev = cur
            time.sleep(1.3)

        print("=== 监听结束 ===")
        print(f"最终: rx={prev['rx']} tx={prev['tx']} frames={prev['frames']}")
        return 0
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
