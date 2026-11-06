#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t211_clear_probe.py —— 「清屏」行为的定点验收探针（T211 / T212 两套口径）

为什么单独写：`cli_session.py` 是"发一批命令然后退出"，做不到"保持一个窗口、
在窗口内触发一次清屏，同时把 `pushed` 逐点记下来"。而 `pushed` 必须**跨时间点**
对比才有意义，关一次端口 ESP32 就复位、状态全丢。

两套口径（🛑 别混用，判据相反）
--------------------------------
| 场景 | 前置 | 清屏后应当 | 对应 |
|---|---|---|---|
| `--scenario run`   | **播放中** | `pushed` **继续增长**、不归零、不停 | ★T212 现状 |
| `--scenario still` | **暂停中** | `pushed` **零增长**（画面保持空白） | T211 老口径 |

`run` 场景额外要求**不出现** `restart requested` / `ecg_pipe: paused`，
因为 T212 已把清屏改成纯视觉（`app_state.c` `app_action_clear()` 不再改 play、
不再归零）；`still` 场景只要求零增长，不再要求 `paused` 日志 ——
T212 固件根本没调用 `pause()`，那条日志不会出现（T211 固件才会）。

触发方式
--------
    --trigger btn   人工点屏（现场口径，唯一能覆盖 LVGL 事件分发的路径）
    --trigger cli   脚本自己发 `ui-clear`（需固件含该命令；无人值守回归用）

🛑 三条曾经踩过的坑（2026-11-06 修）
-----------------------------------
1. **判定不能只看 `pushed`**。旧版只看增量，若根本没点清屏，`pushed` 同样冻结
   ⇒ 旧版会打印 ✅ ⇒ **假通过**。现在必须同时满足「见到清屏日志」＋增量条件。
2. **不能每轮 `reset_input_buffer()`**。点击日志是异步的，旧版在两次轮询之间
   reset 会把它丢掉 ⇒ 报"窗口内没看到清屏日志"。现在改为后台连续读线程 +
   全窗口累积缓冲，一个字节都不丢。
3. **`--pause cli` 覆盖不到真实现场**。`cmd_run_stop()`（`cli_console.c:1143`）
   只调 `ecg_pipeline_pause()`，**不碰 `app_state.play`**。要验 UI 现场必须
   `--pause ui`，由人按「回放」进入暂停。

用法
----
    py _t211_clear_probe.py --scenario run --trigger cli      # T212 全自动回归
    py _t211_clear_probe.py --scenario still --trigger cli    # T211 口径回归
    py _t211_clear_probe.py --scenario run --trigger btn      # 现场口径（人点一下）
    py _t211_clear_probe.py --file test-short --speed 4 --secs 40
"""
import argparse
import re
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")

PUSH_RE   = re.compile(rb"pushed=(\d+)")
PAUSED_RE = re.compile(rb"replay: playing -> paused")   # app_state：屏上「回放」按第二次

CLICK_PATS = (b"clear -> visual only", b"clear -> idle")  # 新 / 旧日志文案

# 事件：(匹配串, 简称, 说明)
EVENTS = (
    (b"restart requested",       "restart", "位置归零请求（T211 行为，T212 不该出现）"),
    (b"restart from beginning",  "fseek",   "生产者已消费 restart，回到文件头"),
    (b"ecg_pipe: paused",        "pause",   "管线被暂停"),
)


class Reader(threading.Thread):
    """后台连续读：只 append，绝不清缓冲。"""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.p = port
        self.buf = bytearray()
        self._lk = threading.Lock()
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                chunk = self.p.read(4096)
            except Exception:
                break
            if chunk:
                with self._lk:
                    self.buf.extend(chunk)

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lk:
            return bytes(self.buf)

    def send(self, cmd):
        self.p.write((cmd + "\r\n").encode())
        self.p.flush()


def has_click(blob):
    return any(pat in blob for pat in CLICK_PATS)


def tags_in(blob):
    return [tag for pat, tag, _ in EVENTS if pat in blob]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--file", default="alt-120bpm")
    ap.add_argument("--speed", type=int, default=2)
    ap.add_argument("--scenario", choices=("run", "still"), default="run",
                    help="run=播放中点清屏（★T212：应继续推进不归零）；"
                         "still=暂停中点清屏（T211：应保持静止）")
    ap.add_argument("--trigger", choices=("btn", "cli"), default="btn",
                    help="btn=人工点屏（现场口径）；cli=脚本发 ui-clear（需固件含该命令）")
    ap.add_argument("--pause", choices=("ui", "cli"), default="ui",
                    help="仅 still 场景：ui=屏上按「回放」进暂停（★现场）；cli=run-stop")
    ap.add_argument("--secs", type=float, default=60.0, help="清屏监听窗口秒数")
    ap.add_argument("--wait", type=float, default=90.0,
                    help="still/ui 下等待 `replay: playing -> paused` 的超时秒数")
    ap.add_argument("--interval", type=float, default=1.0, help="轮询 485-stat 的间隔")
    ap.add_argument("--loglevel", default="info", choices=("info", "warn", "debug"),
                    help="★必须先开 info，否则事件日志一条都不打印（上电压到 ERROR）")
    ap.add_argument("--fast", type=float, default=6.0,
                    help="触发后继续快采样的时长（0.2 s 一次）")
    ap.add_argument("--save", default="", help="把原始日志另存一份")
    a = ap.parse_args()

    p = serial.Serial()
    p.port = a.port
    p.baudrate = 115200
    p.timeout = 0.05
    p.dtr = False          # ★ 不能拉 DTR/RTS，否则 USB-CDC 直接把板子复位
    p.rts = False
    p.set_buffer_size(rx_size=262144, tx_size=8192)   # 默认 4 KB 太小，逐块读会溢出
    p.open()

    rd = Reader(p)
    rd.start()
    time.sleep(0.3)

    def mark():
        return len(rd.snapshot())

    def since(n):
        return rd.snapshot()[n:]

    def show(blob):
        txt = blob.decode("utf-8", "replace").strip()
        if txt:
            print("   " + txt.replace("\n", "\n   "))

    try:
        rd.send("")
        time.sleep(0.3)
        # ★ 上电默认全局日志被压到 ERROR（main.c：esp_log_level_set("*", ESP_LOG_ERROR)）
        #   不开这一条，`click btn` / `clear -> visual only` / `paused` **一条都不打印**。
        rd.send(f"log {a.loglevel}")
        time.sleep(0.5)
        print(f"① 日志级别 -> {a.loglevel}   场景 = {a.scenario}   触发 = {a.trigger}")
        show(since(0))
        base_mark = mark()

        # ---- ② 回到 FILE 源（485 源下 play/回放 全是空操作）----
        print("\n② 数据源")
        rd.send("485-stop")              # 已在 FILE 源时是空操作，幂等
        time.sleep(2.0)
        show(since(base_mark))

        m_src = mark()
        rd.send("485-stat")
        time.sleep(1.2)
        src_txt = since(m_src).decode("utf-8", "replace")
        for line in src_txt.splitlines():
            if line.strip().startswith("源"):
                print("   " + line.strip())
        if "RS485" in src_txt:
            print("\n   ✗ 源仍是 RS485 —— `play` 在 485 源下是空操作")
            print("     （`ecg_pipeline_set_file()` 只置 s_change，485 生产者从不消费）。")
            print("     ⇒ 不判 PASS。")
            return 2

        # ---- ③ 起播 ----
        print(f"\n③ 起播 play {a.file} --speed {a.speed}")
        rd.send(f"play {a.file} --speed {a.speed}")
        time.sleep(1.5)
        show(since(mark()))

        # ---- ④ still 场景：先进暂停 ----
        if a.scenario == "still":
            if a.pause == "ui":
                print("\n④ 请**在屏上按「回放」**，一直按到脚本提示「已进入暂停态」为止")
                print(f"   判据＝出现 `replay: playing -> paused`，最多 {a.wait:.0f} s")
                m = mark()
                t_end = time.time() + a.wait
                got = False
                nxt_hb = time.time() + 10.0
                while time.time() < t_end:
                    if PAUSED_RE.search(since(m)):
                        got = True
                        break
                    if time.time() >= nxt_hb:
                        nxt_hb = time.time() + 10.0
                        print(f"   … 仍在等（剩 {t_end - time.time():4.0f} s）")
                    time.sleep(0.2)
                show(since(m))
                if not got:
                    print("\n   ✗ 超时：没看到 `replay: playing -> paused` ⇒ 不判 PASS。")
                    return 2
                print("   ✓ 已进入暂停态")
            else:
                print("\n④ 暂停 run-stop（⚠ 该路径不碰 app_state.play，不等于现场）")
                m = mark()
                rd.send("run-stop")
                time.sleep(1.0)
                show(since(m))
        else:
            print("\n④ 保持播放中（run 场景不暂停）")

        # ---- ⑤ 基线 ----
        print("\n⑤ 基线采样（3 次）")
        m = mark()
        base = None
        for _ in range(3):
            rd.send("485-stat")
            time.sleep(0.9)
            mm = PUSH_RE.findall(since(m))
            if mm:
                base = int(mm[-1])
                print(f"   pushed={base}")
        if base is None:
            print("   ⚠ 没读到 pushed —— 485-stat 输出没接上，确认板子活着")
            return 2

        # ---- ⑥ 触发 ----
        w0 = mark()
        t0 = time.time()
        if a.trigger == "cli":
            print("\n⑥ 触发 ui-clear（脚本自发）")
            rd.send("ui-clear")
            time.sleep(0.5)
            probe = since(w0).decode("utf-8", "replace")
            if "Unrecognized" in probe or "unrecognized" in probe:
                print("   ✗ 固件没有 `ui-clear` 命令 ⇒ 请改用 --trigger btn，或先烧入该命令。")
                return 2
        else:
            print(f"\n⑥ 监听窗口 {a.secs:.0f} s —— ★现在请在屏上点一次「清屏」")
        print("   " + "-" * 62)

        clicked_at = None
        push_at_click = None
        last_push = base
        fast_until = None
        nxt_hb = time.time() + 10.0
        while True:
            el = time.time() - t0
            if clicked_at is None and el > a.secs:
                break
            if clicked_at is not None and time.time() > fast_until:
                break

            if clicked_at is None and time.time() >= nxt_hb:
                nxt_hb = time.time() + 10.0
                print(f"   [{el:5.1f}s] 等清屏…（剩 {a.secs - el:4.0f} s）")

            rd.send("485-stat")
            time.sleep(0.2 if clicked_at is not None else a.interval)

            w = since(w0)
            mm = PUSH_RE.findall(w)
            push = int(mm[-1]) if mm else last_push

            if clicked_at is None and has_click(w):
                clicked_at = el
                push_at_click = push
                fast_until = time.time() + a.fast
                print(f"   [{el:5.1f}s] ★ 清屏已触发   pushed={push}")

            if push != last_push:
                print(f"   [{el:5.1f}s] pushed={push}  (基线 {base}, 相对触发 +{push - (push_at_click or base)})")
                last_push = push

        print("   " + "-" * 62)

        # ---- ⑦ 判定 ----
        w = since(w0)
        tags = tags_in(w)
        if clicked_at is None:
            print("\n⑦ 判定：INCONCLUSIVE —— 窗口内没有见到清屏日志。")
            print("    本次**不判通过**（没触发 ≠ 修好了）。")
            return 2

        delta = last_push - push_at_click
        print("\n⑦ 判定")
        print(f"   触发时刻      : {clicked_at:.1f} s   触发时 pushed={push_at_click}")
        print(f"   触发后 pushed : {last_push}  （增量 {delta:+d}，采样 {a.fast:.0f} s）")
        print(f"   伴随事件      : {tags if tags else '（无）'}")

        if a.scenario == "run":
            ok = (delta > 0) and ("restart" not in tags) and ("pause" not in tags)
            print(f"   A 继续推进    : {'✓' if delta > 0 else '✗ 增量 0，被停住了'}")
            print(f"   B 未归零      : {'✗ 出现 restart' if 'restart' in tags else '✓ 无 restart requested'}")
            print(f"   C 未暂停      : {'✗ 出现 paused' if 'pause' in tags else '✓ 无 ecg_pipe: paused'}")
            print(f"\n   ⇒ {'PASS（T212：清屏纯视觉，播放不中断）' if ok else 'FAIL'}")
        else:
            ok = (delta == 0)
            print(f"   A 保持静止    : {'✓ 零增量' if delta == 0 else '✗ 增了 %d' % delta}")
            if "pause" in tags:
                print("   （注：见到 paused —— T211 老固件的行为；T212 不该有，出现说明跑的是旧固件）")
            print(f"\n   ⇒ {'PASS（still：清屏后不动）' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        rd.stop()
        time.sleep(0.1)
        if a.save:
            blob = rd.snapshot()
            with open(a.save, "wb") as f:
                f.write(blob)
            print(f"\n（原始日志 {len(blob)} B 已存 {a.save}）")
        p.close()


if __name__ == "__main__":
    sys.exit(main())
