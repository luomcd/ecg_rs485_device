#!/usr/bin/env python3
"""Q6 分析：LocateRPeak ±55 定位窗在短 RR 下会不会锁错极值？

问题定义
--------
`locate_rpeak()` 在 `integrated_peak ± 55` 的窗口里找 |滤波值| 最大的点。
±55 ⇒ 窗宽 **110 样本 = 220 ms**。而 RR 越短，这个窗占 RR 的比例越大：

    RR=1000（60 bpm）：110/1000 = 11 %   ← 安全
    RR=500 （120 bpm）：110/500  = 22 %   ← 尚可
    RR=400 （150 bpm）：110/400  = 27.5 % ← 偏大
    RR=250 （240 bpm）：110/250  = 44 %   ← 危险：窗内可能含**前一个或后一个波**
    RR=333 （180 bpm）：110/333  = 33 %   ← 偏大

一旦窗内含到相邻心搏的更大极值（S 波深、或下一个 R 波的上升支），
定位点就会跳过去 —— 这**不影响 Se/+P**（±75 样本容差内），
但会让 R 峰圆点画错位置、让 RR 抖动进而让 BPM 抖动。

本工具只做**诊断**，不改算法。输出：
  1. 偏移量（检测峰 − 真值峰）随 RR 的分布
  2. 每个 RR 档的离群率（|offset| > 5 / 10 / 20 / 30 样本）
  3. 离群样本清单（哪条记录、哪一拍、偏多少、窗宽占 RR 多少）

用法
----
    python tools/q6_locate_analysis.py
    python tools/q6_locate_analysis.py --recs 100,119,200
    python tools/q6_locate_analysis.py --csv q6_offset.csv
"""

from __future__ import annotations

import argparse
import bisect
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mitbih_eval as ev  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REC_DIR = os.path.join(ROOT, "mit_bih", "records")

TOL = ev.TOL              # ±75 样本（±150 ms）匹配窗
FS = ev.FS

# RR 分档（样本数 @500 Hz）→ (下界, 上界, 标签)
RR_BINS = [
    (0,    300, "<300  (>200 bpm)"),
    (300,  400, "300-400 (150-200)"),
    (400,  500, "400-500 (120-150)"),
    (500,  600, "500-600 (100-120)"),
    (600,  800, "600-800 (75-100)"),
    (800,  1000, "800-1000 (60-75)"),
    (1000, 10**9, ">1000 (<60)"),
]

LOCATE_HALF = 55          # 与 ecg_rpeak.h 的 ECG_RP_LOCATE_HALF 一致


def rr_bin(rr: int) -> str:
    for lo, hi, lab in RR_BINS:
        if lo <= rr < hi:
            return lab
    return RR_BINS[-1][2]


def analyze(recs: list[str]) -> tuple[list[dict], list[dict]]:
    """返回 (全部匹配样本, 离群样本)。"""
    allrows: list[dict] = []
    miss_total: dict[str, int] = {}
    for rec in recs:
        ann_p = os.path.join(REC_DIR, f"{rec}_ann500.txt")
        det_p = os.path.join(REC_DIR, f"{rec}_det.txt")
        if not (os.path.exists(ann_p) and os.path.exists(det_p)):
            continue

        ann, total = ev.read_truth(ann_p)
        peaks = ev.read_peaks(det_p)
        truth = [s for s, _sym, b in ann if b]
        lo, hi = ev.SKIP_EDGE_S * FS, total - ev.SKIP_EDGE_S * FS

        # 真值峰 → 本地 RR（用相邻真值峰，端点用最近的一侧）
        rr_of = {}
        for k, s in enumerate(truth):
            if k + 1 < len(truth):
                rr_of[s] = truth[k + 1] - s
            elif k > 0:
                rr_of[s] = s - truth[k - 1]
            else:
                rr_of[s] = 1000

        # 🛑 **必须以【检测峰】为中心统计偏移，不能以真值峰为中心。**
        # 以真值峰为中心 + 贪心匹配（即使带 used 互斥）会制造大量虚假 +75：
        #   真值 140647 漏检 → 其 ±75 窗口把"相邻拍的孤立误检峰 140722"吃进来，
        #   算成 offset=+75（= TOL 边界）。实测 205 号就是这样。
        # 以检测峰为中心则天然避开：每个检测峰只统计一次，漏检拍不出现、误检拍被 |d|>TOL 排除。
        peaks_sorted = sorted(peaks)
        used_cnt = 0
        for p in peaks_sorted:
            if not (lo <= p <= hi):
                continue
            a = bisect.bisect_left(truth, p)
            cands = [truth[i] for i in (a - 1, a) if 0 <= i < len(truth)]
            if not cands:
                continue
            s = min(cands, key=lambda x: abs(x - p))
            d = p - s
            if abs(d) > TOL:
                continue                    # 误检（FP），不属于 Q6 的定位统计
            used_cnt += 1
            rr = rr_of.get(s, 1000)
            allrows.append({
                "rec": rec,
                "truth": s,
                "det": p,
                "offset": d,
                "rr": rr,
                "rr_bpm": 60.0 * FS / rr if rr else 0.0,
                "rr_bin": rr_bin(rr),
                "win_frac": 2 * LOCATE_HALF / rr * 100.0,
            })
        miss_total[rec] = len([s for s in truth if lo <= s <= hi]) - used_cnt

    outliers = [r for r in allrows if abs(r["offset"]) > 20]
    return allrows, outliers, miss_total


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[k]


def main() -> int:
    ap = argparse.ArgumentParser(description="Q6：±55 定位窗诊断")
    ap.add_argument("--recs", default=None, help="逗号分隔；默认全部")
    ap.add_argument("--csv", default=None, help="离群样本落盘")
    args = ap.parse_args()

    if args.recs:
        recs = args.recs.split(",")
    else:
        suf = "_500.bin"
        recs = sorted(f[:-len(suf)] for f in os.listdir(REC_DIR) if f.endswith(suf))

    rows, outliers, miss = analyze(recs)
    if not rows:
        print("没有可分析的数据（先跑 mitbih_run_all.py --engine host）")
        return 1

    print("=" * 96)
    print(f"Q6 分析：LocateRPeak ±{LOCATE_HALF} 定位窗（窗宽 {2*LOCATE_HALF} 样本 = "
          f"{2*LOCATE_HALF/FS*1000:.0f} ms）")
    n_miss = sum(miss.values())
    print(f"记录 {len(recs)} 条，成功匹配 {len(rows):,} 拍；"
          f"漏检 {n_miss:,} 拍（不计入偏移统计，那是 Se 的事）")
    print("=" * 96)

    # ---- 按 RR 分档
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[r["rr_bin"]].append(r)

    print(f"\n{'RR 档 (样本/bpm)':<22}{'拍数':>8}{'窗占比':>8}"
          f"{'|off|均':>9}{'中位':>7}{'P95':>7}{'P99':>7}{'max':>7}"
          f"{'>5':>7}{'>10':>7}{'>20':>7}{'>30':>7}")
    print("-" * 96)
    for _lo, _hi, lab in RR_BINS:
        g = buckets.get(lab)
        if not g:
            continue
        ao = [abs(r["offset"]) for r in g]
        n = len(g)
        wf = sum(r["win_frac"] for r in g) / n
        c5 = sum(1 for a in ao if a > 5)
        c10 = sum(1 for a in ao if a > 10)
        c20 = sum(1 for a in ao if a > 20)
        c30 = sum(1 for a in ao if a > 30)
        print(f"{lab:<22}{n:>8,}{wf:>7.1f}%{sum(ao)/n:>9.2f}{pct(ao,0.5):>7.1f}"
              f"{pct(ao,0.95):>7.1f}{pct(ao,0.99):>7.1f}{max(ao):>7.1f}"
              f"{c5:>7}{c10:>7}{c20:>7}{c30:>7}")

    # ---- 总计
    all_ao = [abs(r["offset"]) for r in rows]
    n = len(rows)
    print("-" * 96)
    print(f"{'合计':<22}{n:>8,}{'':>8}{sum(all_ao)/n:>9.2f}{pct(all_ao,0.5):>7.1f}"
          f"{pct(all_ao,0.95):>7.1f}{pct(all_ao,0.99):>7.1f}{max(all_ao):>7.1f}"
          f"{sum(1 for a in all_ao if a>5):>7}{sum(1 for a in all_ao if a>10):>7}"
          f"{sum(1 for a in all_ao if a>20):>7}{sum(1 for a in all_ao if a>30):>7}")

    # ---- 偏移符号分布（正=偏右，负=偏左）
    pos = sum(1 for r in rows if r["offset"] > 0)
    neg = sum(1 for r in rows if r["offset"] < 0)
    zero = n - pos - neg
    print(f"\n偏移符号：偏右 {pos:,}（{pos/n*100:.1f} %）｜偏左 {neg:,}（{neg/n*100:.1f} %）"
          f"｜正好 {zero:,}（{zero/n*100:.1f} %）")

    # ---- 离群分析
    print(f"\n{'='*96}")
    print(f"离群样本（|offset| > 20 样本 = 40 ms）：{len(outliers)} 拍"
          f"（占 {len(outliers)/n*100:.3f} %）")
    print("=" * 96)
    if outliers:
        print(f"{'记录':<7}{'真值位置':>10}{'检测位置':>10}{'偏移':>8}{'RR':>7}"
              f"{'bpm':>7}{'窗占RR':>8}  说明")
        print("-" * 96)
        for r in sorted(outliers, key=lambda x: -abs(x["offset"]))[:40]:
            note = ""
            if abs(abs(r["offset"]) - r["rr"]) < 30:
                note = "← 疑似锁到相邻拍"
            elif r["win_frac"] > 40:
                note = "← 窗占比 >40 %"
            print(f"{r['rec']:<7}{r['truth']:>10}{r['det']:>10}{r['offset']:>+8}"
                  f"{r['rr']:>7}{r['rr_bpm']:>7.1f}{r['win_frac']:>7.1f}%  {note}")
        if len(outliers) > 40:
            print(f"... 共 {len(outliers)} 拍")

    # ---- 与"自适应窗宽"的假设检验：如果窗宽改成 min(55, RR/4)，哪些拍会变？
    print(f"\n{'='*96}")
    print("假设检验：窗宽改 min(55, RR/4) 会影响的拍数")
    print("=" * 96)
    affected = [r for r in rows if 2 * LOCATE_HALF > r["rr"] / 4]
    print(f"  会被收窄的拍：{len(affected):,} / {n:,}（{len(affected)/n*100:.2f} %）")
    if affected:
        by = defaultdict(int)
        for r in affected:
            by[r["rr_bin"]] += 1
        for _lo, _hi, lab in RR_BINS:
            if by.get(lab):
                print(f"    {lab:<22} {by[lab]:>7,}")
        ao_aff = [abs(r["offset"]) for r in affected]
        print(f"  这些拍的 |offset|：均 {sum(ao_aff)/len(ao_aff):.2f} "
              f"P95 {pct(ao_aff,0.95):.1f} max {max(ao_aff):.1f}")
        print(f"  其中 |offset|>20 的：{sum(1 for a in ao_aff if a>20)} 拍"
              f"（占离群总数 {len(outliers)} 的 "
              f"{sum(1 for a in ao_aff if a>20)/max(len(outliers),1)*100:.0f} %）")

    # ---- CSV
    if args.csv and outliers:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(outliers[0].keys()), lineterminator="\n")
            w.writeheader()
            for r in outliers:
                w.writerow({k: (round(v, 3) if isinstance(v, float) else v)
                            for k, v in r.items()})
        print(f"\n离群样本已写 {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
