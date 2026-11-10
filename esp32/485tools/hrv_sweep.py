#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hrv_sweep.py —— HRV 精度扫频采集（按心率序列自动记录 / 回传 / 落盘 / 汇总）

为什么需要它
------------
ESP32 上的记录文件名是**固定**的 `/sdcard/rpeak_hrv.txt`，每次 `rpeak rec` 覆盖写。
2026-11-06 就因为这一点，180 bpm 那份原始峰序列被 210 bpm 顶掉、永久丢失，
只剩分析结果没法复算。**本脚本每轮回传后立刻写本地文件**，从根上解决。

另外，一轮扫频要跑十几到几十次「等 180 s + 取数」，人工盯不现实。

配合方式（只需两步）
--------------------
脚本会在每次开始前停下来，打印：
    ★ 请把 AECG100 设为 60 bpm，然后按回车
你改仪器 → 敲回车。之后等待、记录、回传、命名、分析全自动。

用法
----
    py hrv_sweep.py                                  # 默认 60/90/120/150/180/210 × 3 次 × 180 s
    py hrv_sweep.py --bpms 60,120,180,210 --repeat 2 # 快速版
    py hrv_sweep.py --secs 300 --settle 20           # 改时长/建立时间

输出
----
    hrv_data/rpeak_hrv_{bpm}bpm_{n}.txt   每轮原始峰序列（result-dump 原文）
    hrv_data/summary.md                   汇总表（bpm × σ(e) × 异常率 × 判定）

前置
----
    板子已进 485 实时源：485-start → 485-send start → frames 在涨
    COM35 未被 SerialDebug 等工具占用
"""
import argparse
import os
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")

from hrv_analyze import analyze, parse_peaks   # 同目录复用分析实现

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hrv_data")


class Link:
    """串口收发：后台连续读，绝不清缓冲（清缓冲会丢掉异步到达的完成提示）。"""

    def __init__(self, port, baud=115200):
        self.p = serial.Serial()
        self.p.port = port
        self.p.baudrate = baud
        self.p.timeout = 0.05
        self.p.dtr = False       # ★ 不能拉 DTR/RTS，否则 USB-CDC 直接复位板子
        self.p.rts = False
        self.p.set_buffer_size(rx_size=262144, tx_size=8192)
        self.p.open()


    def send(self, cmd):
        self.p.write((cmd + "\r\n").encode())
        self.p.flush()

    def read_until(self, pattern, timeout, quiet=0.0):
        """读到匹配 pattern（bytes 正则）或超时；返回 (matched, text)。

        🛑 正则必须在**字节缓冲**上匹配：pattern 全是 rb"..."，若拿解码后的 str
           去 re.search 会报 "cannot use a bytes pattern on a string-like object"。
        """
        buf = b""
        t_end = time.time() + timeout
        last = time.time()
        while time.time() < t_end:
            c = self.p.read(4096)
            if c:
                buf += c
                last = time.time()
            elif quiet and (time.time() - last) > quiet:
                break
            time.sleep(0.05)
        return (re.search(pattern, buf) is not None), buf.decode("utf-8", "replace")

    def close(self):
        try:
            self.p.close()
        except Exception:
            pass


def check_live(link):
    """确认已在 485 实时源且 frames 在涨。"""
    link.send("485-stat")
    ok, txt = link.read_until(rb"frames=\d+", 4.0, quiet=0.6)
    if not ok:
        return False, "485-stat 无响应 —— 板子活着吗？"
    m_src = re.search(r"源\s*:\s*(\S+)", txt)
    m_fr = re.search(r"frames=(\d+)", txt)
    src = m_src.group(1) if m_src else "?"
    f0 = int(m_fr.group(1)) if m_fr else -1
    if "RS485" not in src:
        return False, "当前源是 %s，不是 RS485 ⇒ 先 `485-start` 再 `485-send start`" % src
    time.sleep(2.0)
    link.send("485-stat")
    _, txt2 = link.read_until(rb"frames=\d+", 4.0, quiet=0.6)
    m2 = re.search(r"frames=(\d+)", txt2)
    f1 = int(m2.group(1)) if m2 else f0
    if f1 <= f0:
        return False, "frames 不涨（%d→%d）⇒ STM32 没在发，补 `485-send start`（0x01）" % (f0, f1)
    return True, "实时源 OK，frames %d→%d" % (f0, f1)


def read_bpm(link):
    """读 ESP32 自己算出的 BPM（485-stat 的心率行）。用于判断信号是否真的正常。"""
    link.send("485-stat")
    ok, txt = link.read_until(rb"bpm=[\d.]+", 4.0, quiet=0.5)
    m = re.search(r"bpm=([\d.]+)", txt)
    return float(m.group(1)) if m else None


def query_state(link, timeout=4.0, retries=3):
    """查记录状态：返回 (running, remain_s) / None=连续多次无响应。

    🛑 一次超时不能判死：2026-11-09 第 1 轮 90 bpm 在 133 s 处单次查询超时，
       脚本直接判"板子无响应"，但实际只是短暂通信中断（随后 result-dump 又通了）。
       改成连续 retries 次都失败才算无响应。
    """
    for i in range(retries):
        try:
            link.send("rpeak")
            ok, txt = link.read_until(rb"QRS", timeout, quiet=0.6)
        except Exception:
            ok, txt = False, ""
        if ok:
            if "QRS 记录：on" in txt:
                m = re.search(r"剩 (\d+) s", txt)
                return (True, int(m.group(1)) if m else -1)
            return (False, 0)
        time.sleep(0.6)
    return None


def wait_record_done(link, secs):
    """轮询到记录结束，带进度条。True=正常结束，False=异常（本轮必须作废）。"""
    t0 = time.time()
    bar_n = 30
    seen_running = False
    miss = 0
    while True:
        st = query_state(link)
        el = time.time() - t0
        if st is None:
            miss += 1
            # 连续 3 次整体查询失败（每次内部还重试 3 次）才判死
            if miss >= 3:
                print("\n  ⚠ 板子连续无响应 —— USB 掉了或固件卡死")
                return False
            print("\n  ⚠ 查询超时（%d/3），继续重试…" % miss)
            time.sleep(2)
            continue
        miss = 0
        running, remain = st
        if not running:
            if not seen_running:
                print("\n  ⚠ 记录未启动成功（`rpeak` 报 off）")
                return False
            print()
            return True
        seen_running = True
        done = secs - remain if remain >= 0 else int(el)
        done = max(0, min(secs, done))
        k = int(bar_n * done / float(secs))
        print("\r  [%s%s] %3d/%d s" % ("#" * k, "-" * (bar_n - k), done, secs),
              end="", flush=True)
        if el > secs + 120:
            print("\n  ⚠ 超过 %d s 仍未结束（板子可能卡在 SD 写）" % (secs + 120))
            return False
        time.sleep(5)


def dump_peaks(link, name="rpeak_hrv.txt", timeout=30.0):
    """result-dump 一次并解析峰号（文件不存在则返回空表）。"""
    link.send("result-dump %s" % name)
    # 同时匹配「正常结束」与「找不到」
    ok, txt = link.read_until(rb"# ---- END|\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0", timeout, quiet=1.0)
    return parse_peaks(txt)


def one_round(link, bpm, secs, settle, idx, total, first_of_bpm=False,
              remain_min=0.0, fs=500.0, prevcheck=True):
    print()
    print("=" * 66)
    print("  第 %d/%d 轮    剩余约 %.0f 分钟    ★ 目标心率 %d bpm" % (idx, total, remain_min, bpm))
    print("=" * 66)
    while True:
        r = input("  ★ 请把 AECG100 设为 %d bpm，好了按回车（s=跳过此轮 r=重新提示）… " % bpm)
        r = r.strip().lower()
        if r == "s":
            print("  → 已跳过本轮")
            return None
        if r == "r":
            continue          # 重新提示，什么都不做
        break                 # 空回车 = 开始

    print("  ⏳ 等待 %d s 让信号与检测器收敛 …" % settle, flush=True)
    time.sleep(settle)

    # ★ 前置自检：ESP32 自己算的 BPM 与设定值差太多，说明信号有问题
    #   （AECG100 没开 / 设错档 / 电极接触不良），此时记下去只是记噪声峰。
    got = read_bpm(link)
    if got is None or got < 1.0:
        print("  ⚠ 读不到有效 BPM（=%.1f）⇒ 可能没有心电信号（AECG100 关着？）" % (got or 0.0))
        if first_of_bpm:
            r = input("     仍要继续这个心率点吗？(y/N) ").strip().lower()
            if r != "y":
                return None
    else:
        dev = abs(got - bpm) / bpm * 100.0
        if dev > 20.0:
            print("  ⚠ 屏上 BPM=%.1f，与设定 %d 差 %.0f%% ⇒ 信号可能不正常" % (got, bpm, dev))
            if first_of_bpm:
                r = input("     仍要继续这个心率点吗？(y/N) ").strip().lower()
                if r != "y":
                    return None
        else:
            print("  ✓ 屏上 BPM=%.1f，与设定吻合（差 %.1f%%）" % (got, dev))


    print("  ▶ rpeak rec %d" % secs, flush=True)
    link.send("rpeak rec %d" % secs)
    time.sleep(3.0)
    st = query_state(link)
    if st is None:
        print("  ✗ 发完记录命令后板子无响应")
        return None
    if not st[0]:
        print("  ✗ 记录没启动（`rpeak` 报 off）—— 可能上一轮还没退干净，或 SD 未挂载")
        return None

    # 🛑 必须检查返回值：2026-11-09 第 1 轮 90 bpm 在 133 s 查询超时，旧代码不管
    #   返回值直接 dump，把**上一轮的旧文件**存成了 90bpm_1（179 峰 + σ 与 60bpm_3
    #   完全相同，是同一份数据）。现在异常直接作废，不取数。
    if not wait_record_done(link, secs):
        print("  ✗ 本轮作废（记录未正常结束，不取数、不存盘）")
        return None
    time.sleep(1.5)
    link.send("result-dump rpeak_hrv.txt")
    ok, txt = link.read_until(rb"# ---- END", 30.0, quiet=1.0)
    if not ok:
        print("  ✗ result-dump 没返回完整内容")
        return None

    peaks = parse_peaks(txt)
    if len(peaks) < 10:
        print("  ✗ 只解析到 %d 个峰，数据异常" % len(peaks))
        return None

    # ★ 防呆：用**跨度**校验，不用绝对峰号。
    #   🛑 2026-11-09 踩过：绝对峰号会被 stop/start 重置（`s_push_seq` 归零），
    #   旧文件末峰 2682629 > 新数据首峰 365625 ⇒ 把有效数据判成旧数据。
    #   跨度与峰号基准无关：180 s @500 Hz 应约 90000 样本。
    if prevcheck:
        span = peaks[-1] - peaks[0]
        exp = secs * fs
        if span < exp * 0.85:
            print("  ✗ 跨度只有 %.1f s（应约 %d s）⇒ 取回的是旧/不完整数据，作废"
                  % (span / fs, secs))
            return None

    path = os.path.join(OUT_DIR, "rpeak_hrv_%dbpm_%d.txt" % (bpm, idx_of(bpm, OUT_DIR) + 1))
    # 🛑 串口 dump 是 CRLF（ESP-IDF 控制台格式），项目约定纯 LF ⇒ 落盘前统一
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt.replace("\r\n", "\n").replace("\r", "\n"))

    # ---- 本轮即时反馈：不用等我，当场就知道质量 ----
    res = analyze(peaks, fs, bpm)
    if "error" in res:
        print("  ✓ 已存 %s（%d 峰）" % (os.path.basename(path), len(peaks)))
        print("     ⚠ %s" % res["error"])
        return path, None

    s = res["sigma_e"]
    verdict = "PASS" if s <= 0.5 else ("WARN" if s <= 1.0 else "FAIL")
    print("  ✓ 已存 %s（%d 峰）" % (os.path.basename(path), len(peaks)))
    print("     σ(e)=%.2f 样本 (%.1f ms)   异常率 %.2f%%   多检 %d / 漏检 %d   ⇒ %s" % (
        s, s / fs * 1000.0, res["anom_rate"] * 100,
        len(res["extra"]), len(res["missed"]), verdict))
    return path, res


def idx_of(bpm, out_dir):
    """该 bpm 已有几个文件（用于命名递增，避免覆盖）。"""
    if not os.path.isdir(out_dir):
        return 0
    n = 0
    for fn in os.listdir(out_dir):
        if re.match(r"rpeak_hrv_%dbpm_(\d+)\.txt$" % bpm, fn):
            n += 1
    return n


def collect_all(fs=500.0):
    """扫描 hrv_data 下所有 rpeak_hrv_{bpm}bpm_{n}.txt，返回 [(bpm, res, 文件名)]。

    🛑 为什么不只用本次运行的 rows：扫频常常分几次跑（中断/分批），
    只统计本次会让之前的心率点从汇总里消失。全量扫描才能一张表看全。
    """
    rows = []
    if not os.path.isdir(OUT_DIR):
        return rows
    names = sorted(os.listdir(OUT_DIR))
    pat = re.compile(r"rpeak_hrv_(\d+)bpm_(\d+)\.txt$")
    for fn in names:
        m = pat.match(fn)
        if not m:
            continue
        bpm = int(m.group(1))
        try:
            with open(os.path.join(OUT_DIR, fn), encoding="utf-8") as f:
                peaks = parse_peaks(f.read())
            rows.append((bpm, analyze(peaks, fs, bpm), fn) if len(peaks) >= 8 else (bpm, None, fn))
        except Exception:
            rows.append((bpm, None, fn))
    return rows


def summarize(rows):
    """按 bpm 聚合出汇总表 + 写 summary.md。

    ⚠ 同一 bpm 的多次记录**不取平均当结论** —— 重复性本身就是被测对象。
    这里给出均值与**极差**，极差大说明"退化"不稳定，见 T218 §7C.2。
    """
    by_bpm = {}
    for item in rows:
        bpm, r = item[0], item[1]
        by_bpm.setdefault(bpm, []).append(r)

    lines = ["# HRV 扫频汇总（%s）\n" % time.strftime("%Y-%m-%d %H:%M"),
             "| bpm | 次数 | 峰数 | σ(e) d2 均值 | 极差 | 异常率 均值 | 极差 | 判定 |",
             "|---|---|---|---|---|---|---|---|"]
    detail = []
    for bpm in sorted(by_bpm):
        rs = [r for r in by_bpm[bpm] if r is not None]
        if not rs:
            lines.append("| %d | 0 | — | — | — | — | — | 数据异常 |" % bpm)
            continue
        sig = [r["sigma_e_d2"] for r in rs]
        anom = [r["anom_rate"] * 100 for r in rs]
        s_mean = sum(sig) / len(sig)
        a_mean = sum(anom) / len(anom)
        s_pp = max(sig) - min(sig)
        a_pp = max(anom) - min(anom)
        v = "PASS" if s_mean <= 0.5 else ("WARN" if s_mean <= 1.0 else "FAIL")
        n_mean = int(sum(r["n"] for r in rs) / len(rs))
        lines.append("| %d | %d | %d | %.2f | %.2f | %.2f%% | %.2f | %s |" % (
            bpm, len(rs), n_mean, s_mean, s_pp, a_mean, a_pp, v))
        detail.append("### %d bpm（%d 次）\n" % (bpm, len(rs)))
        for i, r in enumerate(rs, 1):
            detail.append("- 第 %d 次：峰数 %d，σ(d2)=%.2f，σ(rr)=%.2f，比值 %.2f，"
                          "多检 %d、漏检 %d，异常率 %.2f%%" % (
                              i, r["n"], r["sigma_e_d2"], r["sigma_e_rr"],
                              r["sigma_e_d2"] / r["sigma_e_rr"] if r["sigma_e_rr"] else 0,
                              len(r["extra"]), len(r["missed"]), r["anom_rate"] * 100))
        detail.append("")

    txt = "\n".join(lines) + "\n\n## 逐次明细\n\n" + "\n".join(detail)
    print(txt)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "summary.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    print("汇总已写 %s" % path)


def main():
    # 🛑 强制行缓冲：2026-11-09 实测，每轮结束后的 print 会积压在缓冲区里
    #   （终端看不到"已存 / σ=..."），直到 Ctrl+C 才一次性刷出 ⇒ 用户误以为脚本卡死。
    #   进度条虽然用了 flush=True，但其余 print 没有，块缓冲下就出事。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="HRV 精度扫频采集")
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--bpms", default="60,90,120,150,180,210", help="心率序列，逗号分隔")
    ap.add_argument("--repeat", type=int, default=3, help="每个心率记录几次")
    ap.add_argument("--secs", type=int, default=180, help="每次记录时长（固件允许 30~600）")
    ap.add_argument("--settle", type=int, default=15, help="切换心率后等待稳定秒数")
    ap.add_argument("--fs", type=float, default=500.0)
    ap.add_argument("--no-prevcheck", action="store_true",
                    help="跳过'取回数据必须新于记录前末峰'的校验（ESP32 复位后用）")
    a = ap.parse_args()

    bpms = [int(x) for x in a.bpms.split(",") if x.strip()]
    if not bpms:
        sys.exit("--bpms 为空")
    if not (30 <= a.secs <= 600):
        sys.exit("--secs 必须在 30~600（固件限制）")

    os.makedirs(OUT_DIR, exist_ok=True)

    total = len(bpms) * a.repeat
    est_min = total * (a.secs + a.settle + 10) / 60.0
    print("=" * 64)
    print("  HRV 扫频采集")
    print("  心率点 : %s" % ", ".join("%d" % b for b in bpms))
    print("  每点   : %d 次 × %d s（切换后等 %d s）" % (a.repeat, a.secs, a.settle))
    print("  合计   : %d 轮，预计 %.0f 分钟（不含你改仪器的时间）" % (total, est_min))
    print("  输出   : %s" % OUT_DIR)
    print("=" * 64)
    input("  准备好了按回车开始（Ctrl+C 随时中止）… ")

    link = Link(a.port)
    rows = []
    try:
        ok, msg = check_live(link)
        print("\n  自检: %s\n" % msg)
        if not ok:
            print("  ✗ 前置不满足，退出。")
            return 2

        n = 0
        for bpm in bpms:
            for rep in range(a.repeat):
                n += 1
                remain = (total - n + 1) * (a.secs + a.settle + 10) / 60.0
                got = one_round(link, bpm, a.secs, a.settle, n, total,
                                first_of_bpm=(rep == 0), remain_min=remain, fs=a.fs,
                                prevcheck=not a.no_prevcheck)
    except KeyboardInterrupt:
        print("\n\n  ⚠ 已中止。已完成的文件都在 %s" % OUT_DIR)
    finally:
        link.close()

    # ★ 全量汇总：扫目录，把之前分批跑的心率点也纳入
    allrows = collect_all(a.fs)
    if allrows:
        print("\n" + "=" * 72)
        print("  全量汇总（扫描 %s，含之前分批跑的数据）" % os.path.basename(OUT_DIR))
        print("=" * 72)
        summarize(allrows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
