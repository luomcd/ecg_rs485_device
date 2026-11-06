#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209e_link3_matrix.py —— 链路3（CP210x ↔ UT-2211 ↔ CH340）双观察者矩阵

硬件前提（猛哥 2026-11-05 13:50 搭建）
--------------------------------------
    COM9  CP210x(TTL) -- MAX3232 --[RS232]-- UT-2211 --[485 A/B]-- CH340 COM27
                                              └─ ESP32 的 A/B 也并在这对线上

为什么这条链路能定案
--------------------
    两个 PC 串口分别坐在 UT-2211 的**两侧**，所以：
      · COM9 发 → 走 UT-2211 的「232收 + 485发」腿
      · COM27 发 → 走 UT-2211 的「485收 + 232发」腿   ← 下行用的正是这条
    ⇒ 单独一条腿坏没坏，看对应方向就知道。

★本脚本的核心判据
-----------------
    ESP32 在**已 `485-start`**（UART1 已装、Q1 自动方向在工作）的状态下：
      T1/T2 若仍通  ⇒ ESP32 挂着不干扰总线（隐患排除）
      T1/T2 若不通  ⇒ **`485-start` 一执行就把总线占了** ⇒ 这就是测试2的病根
    ESP32 自己发的 7 B 帧会被**两个观察者同时看到**（CH340 直收 + UT-2211 转发给 COM9）
    ⇒ 双观察者交叉验证 ESP32 的 TX 到底有没有上总线。

用法
----
    python _t209e_link3_matrix.py --a COM9 --b COM27 --esp COM35
"""
import argparse
import re
import sys
import threading
import time

import serial

FRAME = bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x10, 0x2A])   # 0x10 REQ_SNAPSHOT


class Tap(threading.Thread):
    """后台持续吞一个串口，记录所有字节，可查询"从某时刻起收到什么"。"""

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
                d = self.p.read(n)
                with self._lock:
                    self.buf += d
            else:
                time.sleep(0.01)

    def mark(self):
        with self._lock:
            return len(self.buf)

    def since(self, k):
        with self._lock:
            return bytes(self.buf[k:])

    def clear(self):
        with self._lock:
            self.buf.clear()

    def close(self):
        self._stop = True
        self.join(timeout=1.0)
        try:
            self.p.close()
        except Exception:
            pass


def hexs(b, limit=48):
    h = " ".join(f"{x:02X}" for x in b[:limit])
    return h + (" ..." if len(b) > limit else "")


def esp_cmd(p, line, wait=1.2):
    p.reset_input_buffer()
    p.write(line.encode() + b"\r\n")
    p.flush()
    time.sleep(wait)
    return p.read(p.in_waiting or 1).decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="COM9", help="CP210x 侧（接 MAX3232 → UT-2211 的 232 侧）")
    ap.add_argument("--b", default="COM27", help="CH340 485 侧")
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--send", default="snap")
    a = ap.parse_args()

    ta, tb = Tap(a.a), Tap(a.b)
    ta.start(); tb.start()
    time.sleep(0.3)
    esp = serial.Serial(a.esp, 115200, timeout=0.2)
    # 🛑🛑 ESP32-S3 原生 USB CDC：pyserial 打开端口时会把 DTR/RTS 拉起来，
    #   芯片内部把 EN/BOOT 接到这两个信号 ⇒ **一开 COM35 就复位**。
    #   实测证据：13:59 刚 `485-start` 成功，14:02 重开端口后 `485-stat`
    #   报「未初始化 / 源=FILE」、`tx=0` —— 不是固件自己重启，是我把它复位了。
    #   对策：开完立刻放掉 DTR/RTS，等它重新起来，**并在同一会话内**完成
    #   485-start → 测量 → 收尾，中途绝不关端口。
    esp.setDTR(False)
    esp.setRTS(False)
    time.sleep(2.5)                      # 等它复位后重新跑起来
    esp.reset_input_buffer()
    esp.write(b"\r\n")
    time.sleep(0.4)
    esp.reset_input_buffer()
    try:
        # ---------- ESP32 状态 ----------
        out = esp_cmd(esp, "485-stat")
        ready = "未初始化" not in out
        print(f"ESP32 状态 : {'已 485-start（UART1 已装）' if ready else '未初始化'}"
              f"   [源={'RS485' if 'RS485' in out else 'FILE'}]")

        if not ready:
            print("           → 在同一会话内补做 485-start（不能靠上一个会话，端口重开会复位）")
            print("   " + " | ".join(l.strip() for l in esp_cmd(esp, "485-start", 2.5).splitlines()
                                     if "485 源" in l or "UART" in l))
            out = esp_cmd(esp, "485-stat")
            ready = "未初始化" not in out
            print(f"           复检: {'已装 UART1' if ready else '❌ 仍未初始化'}")

        rows = []

        # ---------- T1: COM9 → COM27 ----------
        ta.clear(); tb.clear()
        k = tb.mark()
        ta.p.write(FRAME); ta.p.flush()
        time.sleep(0.8)
        got = tb.since(k)
        rows.append(("T1  COM9 → COM27（UT-2211 的「232收+485发」腿）", got))

        # ---------- T2: COM27 → COM9 ----------
        tb.clear(); ta.clear()
        k = ta.mark()
        tb.p.write(FRAME); tb.p.flush()
        time.sleep(0.8)
        got = ta.since(k)
        rows.append(("T2  COM27 → COM9（UT-2211 的「485收+232发」腿＝下行同款）", got))

        # ---------- T3: ESP32 发，双观察者看 ----------
        ta.clear(); tb.clear()
        ka, kb = ta.mark(), tb.mark()
        esp.reset_input_buffer()
        esp.write(f"485-send {a.send}\r\n".encode()); esp.flush()
        time.sleep(1.6)
        ga, gb = ta.since(ka), tb.since(kb)
        rows.append(("T3a ESP32 → COM27（CH340 直收总线）", gb))
        rows.append(("T3b ESP32 → COM9 （UT-2211 转发）", ga))

        # ---------- T4: ESP32 发过之后，T1 再测一次 ----------
        ta.clear(); tb.clear()
        k = tb.mark()
        ta.p.write(FRAME); ta.p.flush()
        time.sleep(0.8)
        got = tb.since(k)
        rows.append(("T4  COM9 → COM27（ESP32 发过之后再验一次）", got))

        print("\n" + "=" * 72)
        ok = {}
        for name, data in rows:
            good = FRAME in data
            ok[name[:2]] = good
            print(f"{'✅' if good else '❌'} {name}")
            print(f"     收到 {len(data)} B: {hexs(data) if data else '（无）'}")
        esp_stat = esp_cmd(esp, "485-stat")
        m = re.search(r"tx=(\d+)\s*B", esp_stat)
        print(f"\nESP32 tx = {m.group(1) if m else '?'} B（应 ≈7×发送次数）")

        print("\n" + "=" * 72)
        if ok.get("T1") and ok.get("T2") and ok.get("T3"):
            print("[结论] ESP32 已 485-start 且挂在总线上，两侧互发与 ESP32 发送**全部正常**")
            print("       ⇒ 总线没被占、ESP32 的 TX 上了总线、UT-2211 两条腿都通")
            print("       ⇒ 测试2「CH340 发、STM32 不应答」的断点只能落在 STM32 那一侧")
        elif not ok.get("T1") and not ok.get("T2"):
            print("[结论] ★ESP32 一旦 485-start，两侧互发**双双失效**")
            print("       ⇒ 「485-start 装上 UART1 就把总线占了」"
                  "（Q1 自动方向在 TX 空闲时未关断 DE）—— 这就是测试2 的病根")
        elif ok.get("T1") and not ok.get("T2"):
            print("[结论] COM27→COM9 不通 ⇒ 断在 UT-2211 的「485收+232发」腿（下行同款）")
        else:
            print("[结论] 组合不齐，看上面逐条明细")
        return 0
    finally:
        ta.close(); tb.close()
        try:
            esp.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
