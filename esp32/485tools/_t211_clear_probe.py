#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t211_clear_probe.py —— 「清屏不该改变播放状态」的定点验收探针

为什么单独写：`cli_session.py` 是"发一批命令然后退出"，做不到"保持一个窗口、
让操作者在窗口内点一下屏，同时我把 `pushed` 逐秒记下来"。
而 `pushed` 必须**跨时间点**对比才有意义，关一次端口 ESP32 就复位、状态全丢。

判据（T211）
------------
    起播 → run-stop（暂停）→ 基线 pushed 应**冻结**
    操作者点「清屏」→ 期望仍然冻结，且串口依次出现：
        clear -> idle, unfrozen              （app_state）
        restart from beginning (rpeak epoch=) （位置归零，生产者 restart 分支）
        paused                               （没有继续喂样本）
    改之前：只有前两行、**没有 paused**，`pushed` 立刻开始涨
            ⇒ 那就是「点清屏却有波形输出」的直接证据。

用法
----
    py _t211_clear_probe.py                      # 默认 play alt-120bpm --speed 2
    py _t211_clear_probe.py --file test-short --secs 30
"""
import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")

PUSH_RE = re.compile(rb"pushed=(\d+)")
EVENTS = (
    (b"clear -> idle",           "★ 清屏被点击"),
    (b"restart from beginning",  "★ 位置已归零"),
    (b"paused",                  "★ 已保持暂停"),
    (b"restarted",               "?"),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--file", default="alt-120bpm")
    ap.add_argument("--speed", type=int, default=2)
    ap.add_argument("--secs", type=float, default=60.0, help="监听窗口秒数")
    ap.add_argument("--interval", type=float, default=1.4, help="每次 485-stat 间隔")
    a = ap.parse_args()

    p = serial.Serial()
    p.port = a.port
    p.baudrate = 115200
    p.timeout = 0.05
    p.dtr = False          # ★ 不能拉 DTR/RTS，否则 USB-CDC 直接把板子复位
    p.rts = False
    p.open()

    def drain(quiet=0.35, limit=4.0):
        """读到安静为止，返回累计字节。"""
        buf = b""
        t_end = time.time() + limit
        last = time.time()
        while time.time() < t_end:
            chunk = p.read(4096)
            if chunk:
                buf += chunk
                last = time.time()
            elif time.time() - last > quiet:
                break
        return buf

    def send(cmd):
        p.write((cmd + "\r\n").encode())
        p.flush()

    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        send("")
        drain()
        p.reset_input_buffer()

        print(f"① 起播 play {a.file} --speed {a.speed}")
        send(f"play {a.file} --speed {a.speed}")
        print("   " + drain().decode("utf-8", "replace").strip().replace("\n", "\n   "))
        time.sleep(1.2)

        print("\n② 暂停 run-stop")
        send("run-stop")
        print("   " + drain().decode("utf-8", "replace").strip().replace("\n", "\n   "))
        time.sleep(0.8)

        print(f"\n③ 监听窗口 {a.secs:.0f} s —— ★现在请在屏上点一次「清屏」")
        print("   " + "-" * 62)

        t0 = time.time()
        base = None
        last_push = None
        events = []
        while time.time() - t0 < a.secs:
            p.reset_input_buffer()
            send("485-stat")
            raw = drain(quiet=0.3, limit=1.6)

            m = PUSH_RE.search(raw)
            push = int(m.group(1)) if m else None
            if base is None and push is not None:
                base = push

            for pat, note in EVENTS:
                if pat in raw and note not in events:
                    events.append(note)
                    print(f"   [{time.time()-t0:5.1f}s] !! {note}  ({pat.decode()})")

            if push is not None:
                delta = (push - base) if base is not None else 0
                flag = "" if delta == 0 else f"  <-- 涨了 {delta}"
                if push != last_push:
                    print(f"   [{time.time()-t0:5.1f}s] pushed={push}{flag}")
                last_push = push

            time.sleep(max(0.0, a.interval - 0.3))

        print("   " + "-" * 62)
        print(f"\n④ 结果：基线 pushed={base}  末次 pushed={last_push}  "
              f"增量={(last_push - base) if (base is not None and last_push is not None) else '?'}")
        if last_push is None or base is None:
            print("   ⚠ 没读到 pushed —— 485-stat 输出没接上，确认板子活着")
        elif last_push == base:
            print("   ✅ 冻结：清屏期间一个样本都没推进 ⇒ T211 修复生效")
        else:
            print("   ✗ 仍在推进：修复未生效，或清屏没点到")
        print(f"   事件: {events if events else '（窗口内没看到清屏日志）'}")
        return 0
    finally:
        p.close()


if __name__ == "__main__":
    sys.exit(main())
