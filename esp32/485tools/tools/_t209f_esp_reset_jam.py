#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209f_esp_reset_jam.py —— 测「ESP32 复位期间是否把总线灌死」

要证的假设
----------
`app_config.h:290` 记着：ESP32 的 ROM console 默认在 IO43，bootloader 启动时
往里打日志，而板上 Q1 是**靠 DI/TX 自动切方向**的 ⇒ 复位/开机那一两秒，
收发器被反复推进发送态，总线上出现垃圾 ⇒ **别人的下行被顶掉**。

这能解释一个静态电路解释不了的事实：
    · 测试 3（CP210x 两头互发 + ESP32 并线）全通
    · 测试 2（STM32 + CH340 + ESP32 并线）下行死
    两者静态拓扑几乎等价，差别只在 **ESP32 当时是否在复位/启动**。

做法（不改硬件，就用现在的链路 3）
--------------------------------
    COM9 每 200 ms 发一帧 7 B → COM27 应该在 200 ms 内收到同样的 7 B。
    中途**故意反复开关 COM35**（pyserial 一开就拉 DTR/RTS ⇒ ESP32 必复位），
    逐次记录「发出去有没有被收到」+ 时间戳 + ESP32 当时的输出。
    若丢帧**只在复位窗口内**成簇出现 ⇒ 假设成立。

用法
----
    python _t209f_esp_reset_jam.py --a COM9 --b COM27 --esp COM35 --rounds 40
"""
import argparse
import re
import sys
import threading
import time

import serial

FRAME = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x10, 0x2A])


class Tap(threading.Thread):
    def __init__(self, port):
        super().__init__(daemon=True)
        self.p = serial.Serial(port, 115200, timeout=0.05)
        self.p.reset_input_buffer()
        self.buf = bytearray()
        self._lock = threading.Lock()
        self._stop = False

    def run(self):
        while not self._stop:
            n = self.p.in_waiting
            if n:
                with self._lock:
                    self.buf += self.p.read(n)
            else:
                time.sleep(0.005)

    def clear(self):
        with self._lock:
            self.buf.clear()

    def take(self):
        with self._lock:
            d = bytes(self.buf)
            self.buf.clear()
            return d

    def close(self):
        self._stop = True
        self.join(timeout=1.0)
        try:
            self.p.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="COM9", help="发送方（接 UT-2211 的 232 侧）")
    ap.add_argument("--b", default="COM27", help="观察方（485 侧）")
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--gap", type=float, default=0.2)
    ap.add_argument("--reset-every", type=int, default=8,
                    help="每 N 轮开关一次 COM35（= 复位一次 ESP32）")
    a = ap.parse_args()

    ta, tb = Tap(a.a), Tap(a.b)
    ta.start(); tb.start()
    time.sleep(0.4)

    rows = []
    try:
        t0 = time.time()
        for i in range(a.rounds):
            reset_now = (a.reset_every > 0) and (i > 0) and (i % a.reset_every == 0)
            note = ""
            if reset_now:
                # 🛑 开关 COM35 就是给 ESP32 一次复位（DTR/RTS 被拉起）
                try:
                    e = serial.Serial(a.esp, 115200, timeout=0.1)
                    e.setDTR(False); e.setRTS(False)
                    time.sleep(0.05)
                    e.close()
                    note = "ESP32 复位"
                except Exception as ex:
                    note = f"复位失败({ex})"

            tb.clear()
            ta.p.write(FRAME); ta.p.flush()
            time.sleep(0.15)                 # 等 UT-2211 + 总线
            got = tb.take()
            ok = FRAME in got
            rows.append((round(time.time() - t0, 2), i, ok, len(got), note))
            time.sleep(max(0.0, a.gap - 0.15))
    finally:
        ta.close(); tb.close()

    good = sum(1 for r in rows if r[2])
    print(f"{'时刻':>7} {'轮':>3}  结果  收到")
    jam = []
    for t, i, ok, n, note in rows:
        mark = ""
        if note:
            mark = f"  ← {note}"
            jam.append(t)
        print(f"{t:>7.2f} {i:>3}  {'✅' if ok else '❌'} {n:>3} B{mark}")

    print("\n" + "=" * 66)
    print(f"成功 {good}/{len(rows)} = {good / len(rows) * 100:.0f}%")
    if good == len(rows):
        print("[结论] ESP32 复位**没有**把总线灌死（这一轮全过）")
        print("       ⇒ 「复位灌总线」在这块板上不成立，测试2 得另找时变因素")
    else:
        print("丢帧位置（相对秒）：", [f"{t:.2f}" for t, i, ok, n, note in rows if not ok])
        print("ESP32 复位时刻      ：", [f"{t:.2f}" for t in jam])
        print("[结论] 若丢帧紧跟在复位时刻之后 ⇒ **ESP32 复位期间把总线顶死了**，")
        print("       与 `app_config.h:290` 记的 ROM console 灌总线一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
