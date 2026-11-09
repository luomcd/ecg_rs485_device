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
        """读到匹配 pattern（bytes 正则）或超时；返回 (matched, text)。"""
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
        txt = buf.decode("utf-8", "replace")
        return (re.search(pattern, txt) is not None), txt

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


def one_round(link, bpm, secs, settle, idx, total, first_of_bpm=False):
    print()
    print("=" * 64)
    print("  [%d/%d]  %d bpm" % (idx, total, bpm))
    print("=" * 64)
    input("  ★ 请把 AECG100 设为 %d bpm，确认后按回车 … " % bpm)

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
    ok, txt = link.read_until(rb"QRS \xe8\xae\xb0\xe5\xbd\x95\xe5\xbc\x80\xe5\xa7\x8b", 6.0, quiet=0.8)
    if not ok:
        print("  ✗ 没看到「QRS 记录开始」，输出如下：")
        print("    " + txt.strip().replace("\n", "\n    "))
        return None

    print("  ⏳ 记录中 %d s（无需操作）…" % secs, flush=True)
    ok, txt = link.read_until(rb"\[rpeak rec\]", secs + 90.0, quiet=0.0)
    if not ok:
        print("  ✗ 等待落盘超时（%d s + 90 s），可能是记录没启动成功" % secs)
        return None
    m = re.search(r"\[rpeak rec\][^\n]*", txt)
    print("  ✓ %s" % (m.group(0).strip() if m else "落盘完成"))

    time.sleep(1.0)
    link.send("result-dump rpeak_hrv.txt")
    ok, txt = link.read_until(rb"# ---- END", 30.0, quiet=1.0)
    if not ok:
        print("  ✗ result-dump 没返回完整内容")
        return None

    peaks = parse_peaks(txt)
    if len(peaks) < 10:
        print("  ✗ 只解析到 %d 个峰，数据异常" % len(peaks))
        return None

    path = os.path.join(OUT_DIR, "rpeak_hrv_%dbpm_%d.txt" % (bpm, idx_of(bpm, OUT_DIR) + 1))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    print("  ✓ 已存 %s（%d 峰）" % (os.path.basename(path), len(peaks)))
    return path


def idx_of(bpm, out_dir):
    """该 bpm 已有几个文件（用于命名递增，避免覆盖）。"""
    if not os.path.isdir(out_dir):
        return 0
    n = 0
    for fn in os.listdir(out_dir):
        if re.match(r"rpeak_hrv_%dbpm_(\d+)\.txt$" % bpm, fn):
            n += 1
    return n


def summarize(rows):
    """按 bpm 聚合出汇总表 + 写 summary.md。

    ⚠ 同一 bpm 的多次记录**不取平均当结论** —— 重复性本身就是被测对象。
    这里给出均值与**极差**，极差大说明"退化"不稳定，见 T218 §7C.2。
    """
    by_bpm = {}
    for bpm, r in rows:
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
    ap = argparse.ArgumentParser(description="HRV 精度扫频采集")
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--bpms", default="60,90,120,150,180,210", help="心率序列，逗号分隔")
    ap.add_argument("--repeat", type=int, default=3, help="每个心率记录几次")
    ap.add_argument("--secs", type=int, default=180, help="每次记录时长（固件允许 30~600）")
    ap.add_argument("--settle", type=int, default=15, help="切换心率后等待稳定秒数")
    ap.add_argument("--fs", type=float, default=500.0)
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
                path = one_round(link, bpm, a.secs, a.settle, n, total,
                                 first_of_bpm=(rep == 0))
                if path:
                    with open(path, encoding="utf-8") as f:
                        peaks = parse_peaks(f.read())
                    rows.append((bpm, analyze(peaks, a.fs, bpm)))
                else:
                    rows.append((bpm, None))
    except KeyboardInterrupt:
        print("\n\n  ⚠ 已中止。已完成的文件都在 %s" % OUT_DIR)
    finally:
        link.close()

    if rows:
        print("\n" + "=" * 64)
        print("  汇总（同一 bpm 可能有多行，最终以 summary.md 为准）")
        print("=" * 64)
        summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
