#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mit2aecg.py —— 把 MIT-BIH 记录导出成 AECG100「Waveform Raw」(.txt)

AECG100 手册 §4.8（p66-67）规定的 Raw Data 格式：
    [sampling frequency]            取样率 Hz
    [number of samples per signal]  每通道点数
    [number of signals]             通道数
    [desc-1],[desc-2],...           信号描述
    [v1],[v2],...                   第 1 点（单位 mV，ECG 范围 [-5,+5]）
    ...
⚠ payload 从**第 5 行**开始（不是第 6 行），别多空一行。

操作路径（手册 p31-32）：ECG 模式 → 下半部 **Player 标签** → (9) 载入 .txt
→ (10) 输出。⚠ p31 注：**只有 Waveform Raw 才能走 Player 标签**，
Standalone/PC(.ecg) 走的是 Signal 标签，别混。

★ 三个口径决定（写死，理由记在这）
  1. **默认不重采样，按 MIT 原生 360 Hz 播放**。AECG100 的取样率是文件头里给的，
     而板子按自己的时钟采模拟信号 —— 模拟信号是连续的，重采样只会白丢一次信息。
     需要和 500 Hz 的 `*_500.bin` 结果对齐时，在**分析侧**重采样，不要在播放侧。
  2. **幅度用真实 mV，不做归一化**。AECG100 D/A 分辨率 2.5 µV，而 MIT 增益
     200 ADU/mV ⇒ 1 ADU = 5 µV，比 DAC 粗 2 倍，不会引入额外量化误差。
     小数位固定 **3** 位：MIT 的值恒为 5 µV = 0.005 mV 的整数倍，3 位刚好无损；
     2 位会把它量化到 10 µV，反而把源分辨率砍半。
  3. 🛑 **超 ±5 mV 的记录用「整体缩放」而不是「削顶」**（`--scale-to-limit`，`--all` 默认开）。
     削顶会在波形上留平顶 ⇒ 微分器在平顶两端各出一个尖峰 ⇒ 人为制造候选，污染检出序列。
     整体缩放保住形态与时间轴，只乘一个常数（系数记进 index.csv 的 `scale` 列）。
     全 48 条里**只有 116 号**触发：977 点 / 0.1503%，范围 -5.120..+5.115 mV ⇒ 系数 0.9766。

用法：
  python tools/mit2aecg.py --record 100 --dur 60 --out 100_60s.txt
  python tools/mit2aecg.py --record 100                       # 全 30 min
  python tools/mit2aecg.py --record 108 --start 300 --dur 120 # 切片
  python tools/mit2aecg.py --all                              # 48 条全量 + index.csv
  python tools/mit2aecg.py --all --chunk 300                  # 48 条切成 5 min 小段
  python tools/mit2aecg.py --record 100 --dry-run             # 只看统计不写文件
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mitbih_prep import read_header, read_dat_212   # noqa: E402

ROOT = os.path.dirname(HERE)
REC_DIR = os.path.join(ROOT, "mit_bih", "records")

AECG_MV_LIMIT = 5.0      # 手册：ECG 数据范围 [-5, +5] mV


def load_mv(record, ch, start_s, dur_s):
    """读一条记录的单通道，返回 (mV 数组, 原生 fs, 信号名, 完整时长 s)"""
    hea = os.path.join(REC_DIR, record + ".hea")
    dat = os.path.join(REC_DIR, record + ".dat")
    if not os.path.exists(hea):
        raise SystemExit("找不到 %s" % hea)
    nsig, fs, nsamp, sigs = read_header(hea)
    if ch >= nsig:
        raise SystemExit("%s 只有 %d 个通道" % (record, nsig))

    adu = read_dat_212(dat, nsamp, nsig)[:, ch].astype(np.float64)
    g, b = sigs[ch]["gain"], sigs[ch]["baseline"]
    mv = (adu - b) / g                       # ADC 单位 → mV

    i0 = int(round(start_s * fs))
    i1 = len(mv) if dur_s is None else min(len(mv), i0 + int(round(dur_s * fs)))
    if i0 >= len(mv):
        raise SystemExit("--start %.1fs 超出记录长度 %.1fs" % (start_s, len(mv) / fs))
    return mv[i0:i1], fs, sigs[ch]["name"], len(mv) / fs


def resample(x, fs_in, fs_out):
    """线性插值重采样。只在显式 --fs 时用（默认不重采样，见文件头口径 1）"""
    if abs(fs_in - fs_out) < 1e-9:
        return x
    n_out = int(round(len(x) * fs_out / fs_in))
    t_in = np.arange(len(x)) / fs_in
    t_out = np.arange(n_out) / fs_out
    return np.interp(t_out, t_in, x)


def write_aecg(path, mv, fs, desc, decimals, eol, with_desc=True):
    """写 AECG100 Waveform Raw (.txt)。纯文本，一行一个采样点。"""
    nl = "\r\n" if eol == "crlf" else "\n"
    fmt = "%%.%df" % decimals
    lines = [str(int(round(fs))), str(len(mv)), "1"]
    if with_desc:
        lines.append(desc)
    lines.extend(fmt % v for v in mv)
    with open(path, "w", encoding="ascii", newline="") as fh:
        fh.write(nl.join(lines) + nl)


def write_manifest(path, rows):
    """写 index.csv —— 播放时靠它对号，别再靠文件名猜"""
    import csv
    cols = ["record", "file", "fs", "samples", "start_s", "duration_s",
            "min_mv", "max_mv", "pp_mv", "over5", "scale", "bytes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def one(record, args, rows=None):
    mv, fs, name, total_s = load_mv(record, args.ch, args.start, args.dur)
    if args.fs:
        mv = resample(mv, fs, args.fs)
        fs = args.fs
    desc = "MIT-BIH %s ch%d %s" % (record, args.ch + 1, name)

    over = int(np.count_nonzero(np.abs(mv) > AECG_MV_LIMIT))
    lo, hi = float(mv.min()), float(mv.max())
    print("%s  (记录全长 %.1f s @ %.0f Hz)" % (record, total_s, fs))
    print("  %7d 点  %7.2f s  |范围| %+.3f .. %+.3f mV  pp %.3f mV"
          % (len(mv), len(mv) / fs, lo, hi, hi - lo))

    scale = 1.0
    if over:
        if args.scale_to_limit:
            scale = AECG_MV_LIMIT / max(abs(lo), abs(hi))
            mv = mv * scale
            print("  ⚠ 超 ±5 mV %d 点（%.4f%%）⇒ **整体缩放 ×%.4f**，保形态不削顶"
                  % (over, 100.0 * over / len(mv), scale))
        elif args.clip:
            mv = np.clip(mv, -AECG_MV_LIMIT, AECG_MV_LIMIT)
            print("  ⚠ 超 ±5 mV %d 点 ⇒ 已削顶（平顶会在微分器两端造尖峰）" % over)
        elif not args.allow_over:
            print("  ⚠ 超 ±5 mV %d 点，未处理 —— AECG100 会削顶" % over)

    if args.dry_run:
        return None

    if args.chunk:
        step = max(1, int(round(args.chunk * fs)))
        segs = [(o, mv[o:o + step]) for o in range(0, len(mv), step)]
        names = [os.path.join(args.outdir or ".", "%s_seg%02d.txt" % (record, k + 1))
                 for k in range(len(segs))]
    else:
        segs = [(0, mv)]
        names = [args.out or os.path.join(
            args.outdir or ".",
            "%s%s.txt" % (record, "" if args.dur is None and not args.start
                          else "_%ds-%ds" % (int(args.start),
                                             int(args.start + len(mv) / fs))))]

    out_rows = []
    for (off, seg), fn in zip(segs, names):
        os.makedirs(os.path.dirname(fn) or ".", exist_ok=True)
        write_aecg(fn, seg, fs, desc, args.decimals, args.eol, not args.no_desc)
        nb = os.path.getsize(fn)
        print("  → %-20s %7d 点  %6.1f s  %7.1f KB"
              % (os.path.basename(fn), len(seg), len(seg) / fs, nb / 1024.0))
        out_rows.append(dict(
            record=record, file=os.path.basename(fn), fs=int(round(fs)),
            samples=len(seg), start_s=round(args.start + off / fs, 2),
            duration_s=round(len(seg) / fs, 2),
            min_mv=round(float(seg.min()), 3), max_mv=round(float(seg.max()), 3),
            pp_mv=round(float(seg.max() - seg.min()), 3),
            over5=over, scale=round(scale, 5), bytes=nb))
    if rows is not None:
        rows.extend(out_rows)
    return out_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", help="记录号，如 100")
    ap.add_argument("--all", action="store_true", help="全部 48 条")
    ap.add_argument("--ch", type=int, default=0, help="通道序号，0=MLII（默认）")
    ap.add_argument("--start", type=float, default=0.0, help="起始秒")
    ap.add_argument("--dur", type=float, default=None, help="时长秒；省略=到结尾")
    ap.add_argument("--fs", type=float, default=None,
                    help="重采样到该 fs；省略=保持 MIT 原生 360 Hz（推荐）")
    ap.add_argument("--out", default=None, help="输出文件")
    ap.add_argument("--outdir", default="aecg_raw", help="--all 时的输出目录")
    ap.add_argument("--chunk", type=float, default=None,
                    help="切成每段 N 秒（AECG100 若载不动大文件就用它，建议 300）")
    ap.add_argument("--manifest", default=None,
                    help="清单输出路径；--all 时默认 <outdir>/index.csv")
    ap.add_argument("--decimals", type=int, default=3, help="小数位（手册示例 3 位）")
    ap.add_argument("--eol", choices=["crlf", "lf"], default="crlf",
                    help="行尾；Windows 软件用 crlf，实测不符再换 lf")
    ap.add_argument("--no-desc", action="store_true", help="省略信号描述行")
    ap.add_argument("--clip", action="store_true", help="把超 ±5 mV 的点削顶")
    ap.add_argument("--scale-to-limit", action="store_true",
                    help="超 ±5 mV 时整体缩放（推荐；--all 默认开）")
    ap.add_argument("--allow-over", action="store_true", help="超限时不打印提醒")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    if args.all:
        if not args.scale_to_limit:
            args.scale_to_limit = True          # --all 一律保形态，不削顶
        recs = sorted(f[:-4] for f in os.listdir(REC_DIR) if f.endswith(".hea"))
        print("共 %d 条记录 → %s/\n" % (len(recs), args.outdir))
        rows = []
        for r in recs:
            one(r, args, rows)
        if rows:
            mp = args.manifest or os.path.join(args.outdir, "index.csv")
            write_manifest(mp, rows)
            tot_s = sum(x["duration_s"] for x in rows)
            tot_n = sum(x["samples"] for x in rows)
            tot_b = sum(x["bytes"] for x in rows)
            print("\n汇总：%d 个文件  %d 点  %.1f s = **%.2f 小时**  %.1f MB"
                  % (len(rows), tot_n, tot_s, tot_s / 3600, tot_b / 1048576.0))
            print("清单：%s" % mp)
        return
    if not args.record:
        raise SystemExit("要么给 --record，要么给 --all")
    rows = []
    one(args.record, args, rows)
    if args.manifest and rows:
        write_manifest(args.manifest, rows)
        print("清单：%s" % args.manifest)


if __name__ == "__main__":
    main()
