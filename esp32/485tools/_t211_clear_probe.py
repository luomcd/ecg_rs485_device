#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t211_clear_probe.py —— 「清屏不该改变播放状态」的定点验收探针（T211）

为什么单独写：`cli_session.py` 是"发一批命令然后退出"，做不到"保持一个窗口、
让操作者在窗口内点一下屏，同时我把 `pushed` 逐秒记下来"。
而 `pushed` 必须**跨时间点**对比才有意义，关一次端口 ESP32 就复位、状态全丢。

判据（T211）
------------
    起播 → 进入暂停态 → 基线 pushed 应**冻结**
    操作者点「清屏」→ 期望仍然冻结，且串口出现：
        clear -> idle, unfrozen              （app_state，证明**点击已生效**）
        restart requested                    （ecg_pipeline_restart 被调用）
        restart from beginning (rpeak epoch=) （生产者消费 restart，位置归零）
        paused                               （补了 pause，没有继续喂样本）
    改之前：没有 `paused`，`pushed` 立刻开始涨
            ⇒ 那就是「点清屏却有波形输出」的直接证据。

🛑 三条曾经踩过的坑（2026-11-06 修）
-----------------------------------
1. **判定不能只看 `pushed`**。旧版只看增量，若操作者**根本没点**清屏，
   `pushed` 同样冻结 ⇒ 旧版会打印 ✅ ⇒ **假通过**。现在必须**同时**满足
   「窗口内见到 `clear -> idle`」＋「pushed 零增量」才判 PASS；没点到只报
   INCONCLUSIVE，绝不判过。
2. **不能每轮 `reset_input_buffer()`**。点击产生的日志是异步的，旧版在两次
   轮询之间 reset 会把它们直接丢掉 ⇒ 报"窗口内没看到清屏日志"。
   现在改为后台连续读线程 + 全窗口累积缓冲，一个字节都不丢。
3. **`--pause cli` 覆盖不到真实现场**。`cmd_run_stop()`（`cli_console.c:1143`）
   只调 `ecg_pipeline_pause()`，**不碰 `app_state.play`** ⇒ 点清屏时
   `prev_play` 仍是 `APP_PLAY_IDLE`，走的是 IDLE 分支（`ui_button_bar.c:182`）。
   真实 bug 场景是**屏上按两次「回放」**⇒ `play == APP_PAUSED` ⇒ 走
   `prev_play != APP_PLAYING` 那条分支。要验现场必须 `--pause ui`。

用法
----
    py _t211_clear_probe.py --pause ui           # ★现场口径（默认）：屏上按两次「回放」
    py _t211_clear_probe.py --pause cli          # 对照：CLI run-stop（prev_play=IDLE）
    py _t211_clear_probe.py --file test-short --secs 40 --speed 4
    py _t211_clear_probe.py --save t211_clear.txt  # 顺带把原始日志存一份

`--pause ui` 的操作顺序（屏上三下）：
    回放 → 回放 → 清屏
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

# (匹配串, 简称, 说明)
EVENTS = (
    (b"clear -> idle",           "click",   "★ 清屏被点击（app_state）"),
    (b"restart requested",       "restart", "★ 位置归零请求（ecg_pipeline_restart）"),
    (b"restart from beginning",  "fseek",   "★ 生产者已消费 restart，回到文件头"),
    (b"paused",                  "pause",   "★ 已保持暂停（补了 pause）"),
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


def find_events(blob, t0, t_now):
    """在 blob 中定位事件，返回 [(ts, 简称, 说明)]，按首次出现排序。"""
    out = []
    for pat, tag, note in EVENTS:
        i = blob.find(pat)
        if i >= 0:
            # 用"首个匹配在 blob 中的位置"折算时间：只用于排先后，不求秒级精度
            out.append((i, tag, note))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--file", default="alt-120bpm")
    ap.add_argument("--speed", type=int, default=2)
    ap.add_argument("--secs", type=float, default=60.0, help="清屏监听窗口秒数")
    ap.add_argument("--prep", choices=("sd", "play"), default="sd",
                    help="sd=先 485-stop 回到 SD 回放暂停态，纯靠屏上两下「回放」（★推荐）；"
                         "play=发 play 起播（🛑 仅当已是 FILE 源，485 源下 play 是空操作）")
    ap.add_argument("--pause", choices=("ui", "cli"), default="ui",
                    help="ui=屏上按两次回放（★现场口径）；cli=run-stop（prev_play=IDLE，仅对照）")
    ap.add_argument("--wait", type=float, default=90.0,
                    help="ui 模式下等待 `replay: playing -> paused` 日志的超时秒数")
    ap.add_argument("--interval", type=float, default=1.0, help="轮询 485-stat 的间隔")
    ap.add_argument("--loglevel", default="info", choices=("info", "warn", "debug"),
                    help="★必须先开 info，否则事件日志一条都不打印（上电压到 ERROR）")
    ap.add_argument("--fast", type=float, default=6.0,
                    help="见到点击后追加的快采样时长（0.25 s 一次）")
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
        #   不开这一条，`click btn` / `replay: playing -> paused` / `clear -> idle` /
        #   `restart from beginning` **一条都不会打印** ⇒ 事件判据全空（2026-11-06 实发）。
        rd.send(f"log {a.loglevel}")
        time.sleep(0.5)
        print(f"① 日志级别 -> {a.loglevel}")
        show(since(0))
        base_mark = mark()

        print("\n② 准备数据源")
        if a.prep == "sd":
            rd.send("485-stop")          # 已在 FILE 源时是空操作，幂等
            time.sleep(2.0)
        else:
            rd.send(f"play {a.file} --speed {a.speed}")
            time.sleep(1.5)
        show(since(base_mark))

        # ---- 源校验：必须 FILE。485 源下 `play`/`回放` 全是空操作 ----
        m_src = mark()
        rd.send("485-stat")
        time.sleep(1.2)
        src_txt = since(m_src).decode("utf-8", "replace")
        for line in src_txt.splitlines():
            if line.strip().startswith("源"):
                print("   " + line.strip())
        if "RS485" in src_txt:
            print("\n   ✗ 源仍是 RS485 —— `play` 在 485 源下是空操作"
                  "（`ecg_pipeline_set_file()` 只置 s_change，485 生产者从不消费）。")
            print("     ⇒ 本次不判 PASS。改用 --prep sd 重跑。")
            return 2

        # ---- 进入暂停态 ----
        if a.pause == "ui":
            print("\n③ 请**在屏上按「回放」**，一直按到脚本提示「已进入暂停态」为止")
            print("   （`app_state.play` 可能残留在 PLAYING：残留时第 1 下就暂停，"
                  "IDLE 时第 1 下是起播、第 2 下才暂停）")
            print(f"   判据＝出现 `replay: playing -> paused` 日志，最多 {a.wait:.0f} s")
            m = mark()
            t_end = time.time() + a.wait
            got = False
            nxt_hb = time.time() + 10.0
            while time.time() < t_end:
                if PAUSED_RE.search(since(m)):
                    got = True
                    break
                # 心跳：让别人看得出脚本还活着、还剩多少时间
                if time.time() >= nxt_hb:
                    nxt_hb = time.time() + 10.0
                    print(f"   … 仍在等（剩 {t_end - time.time():4.0f} s）")
                time.sleep(0.2)
            show(since(m))
            if not got:
                print("\n   ✗ 超时：没看到 `replay: playing -> paused`")
                print("     ⇒ 可能只按了一下（第一下只是起播），或屏上点到了别处。")
                print("     ⇒ 本次不判 PASS（宁可不判，也不给假结论）。")
                return 2
            print("   ✓ 已进入暂停态（app_state: paused）")
        else:
            print("\n③ 暂停 run-stop（注意：该路径 prev_play=APP_PLAY_IDLE，不等于现场）")
            m = mark()
            rd.send("run-stop")
            time.sleep(1.0)
            show(since(m))

        # ---- 基线采样：暂停态下 pushed 必须站住 ----
        print("\n④ 基线采样（暂停态，3 次）")
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

        # ---- 监听窗口 ----
        print(f"\n⑤ 监听窗口 {a.secs:.0f} s —— ★现在请在屏上点一次「清屏」")
        print("   " + "-" * 62)
        w0 = mark()
        t0 = time.time()
        clicked_at = None
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
                print(f"   [{el:5.1f}s] 等点击…（剩 {a.secs - el:4.0f} s）除了 `485-stat` 自己外，"
                      f"串口没有收到任何点击日志")

            rd.send("485-stat")
            time.sleep(0.2 if clicked_at is not None else a.interval)

            w = since(w0)
            if clicked_at is None and b"clear -> idle" in w:
                clicked_at = el
                fast_until = time.time() + a.fast
                print(f"   [{el:5.1f}s] !! 清屏被点击")

            mm = PUSH_RE.findall(w)
            if mm:
                push = int(mm[-1])
                if push != last_push:
                    print(f"   [{el:5.1f}s] pushed={push}  (基线 {base}, +{push - base})")
                    last_push = push

        print("   " + "-" * 62)

        # ---- 判定 ----
        w = since(w0)
        evs = find_events(w, t0, time.time())
        tags = [t for _, t, _ in evs]
        delta = last_push - base

        print("\n⑥ 判定")
        print(f"   A 点击事件   : {'✓ clear -> idle' if 'click' in tags else '✗ 未见到（没点到？）'}")
        print(f"   B 位置归零   : "
              f"{'✓ restart requested / restart from beginning' if ('restart' in tags and 'fseek' in tags) else ('△ 只见到 ' + str(tags))}")
        print(f"   C 保持暂停   : {'✓ paused' if 'pause' in tags else '✗ 没有 paused（旧行为）'}")
        print(f"   D pushed     : 基线 {base} → 末值 {last_push}  增量 {delta}")
        print(f"   事件顺序     : {' -> '.join(tags) if tags else '（无）'}")

        if "click" not in tags:
            print("\n   ⇒ INCONCLUSIVE（没点到清屏）。本条**不判通过**，请重跑。")
            return 2
        if delta != 0:
            print("\n   ⇒ FAIL：清屏后仍在喂样本 —— 修复未生效，"
                  f"或 `prev_play` 被判成 APP_PLAYING 走了重播分支（`ui_button_bar.c:182`）。")
            return 1
        if "pause" not in tags:
            print("\n   ⇒ WARN：pushed 冻住了但没见到 `paused` 日志。"
                  "多半是管线在点之前就已停（例如上一轮的 pause 残留），不能算修好的证据。")
            return 3
        print(f"\n   ⇒ PASS：清屏后 {a.fast:.0f} s 内 pushed 零增量，"
              "且日志顺序为「点击 → 归零 → 保持暂停」。")
        return 0
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
