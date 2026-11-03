#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
locate 偏移（域混用）诊断 —— 统一入口（2026-10-30）

背景：`ecg_rpeak.c:226-227` 的 `spacing = 粗峰(idx−31) − last_peak(已 locate)` 两个域混用
（照搬 C# `HeartRateDetector.cs:174-178` 同构）。locate_rpeak 在 ±W 窗内的偏移 δ 会直接
加进 spacing：上一峰被后移 δ>0 ⇒ 下一拍的 spacing 变小 ⇒ 更容易触发 T 波门（spacing<360）
被错杀。本脚本量化 δ 分布，并对 FN 做"域混用嫌疑"归因。

做法（零改固件源码）：
  1. gcc 编译 thr_probe（跑固件同一份 ecg_rpeak.c，P 行带 coarse/delta/prev_gap）；
  2. 48 条 MIT 逐条跑 → located 峰列表 + δ 列表；
  3. 用 tools/mitbih_eval.py 同一份 score()（TOL=75 / 掐首尾 5 s / BEAT_SYM）评分；
  4. δ 分布统计（窗口内全部检出峰）；
  5. FN 归因（必要条件 = 上界口径，slope 条件未验）：
       FN 真值拍 t 的 spacing_gate ≈ t − p_prev.located（粗峰 c_t ≈ t，R 尖近似）；
       粗峰域本应为 gap_coarse = gap + δ_prev（p_prev.located = p_prev.coarse + δ_prev）；
       嫌疑成立 ⇔ gap < 360 ≤ gap + δ_prev（δ_prev 把 spacing 从过线压到不过线）。

用法：
  python tools/probe_locate_analysis.py                    # 全 48 条，W=40（Q6 定案）
  python tools/probe_locate_analysis.py --recs 100,101     # 选记录
  python tools/probe_locate_analysis.py --half 55          # 对照旧窗宽
输出：
  --out 目录（默认 test_logs/locate_probe_20261030/）：
    thr_probe_<half>.exe   per_rec.csv   summary.json
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mitbih_eval  # noqa: E402  同一份评估代码、同一套口径（S7B 纪律）

GCC = "gcc"
PROBE_SRC = os.path.join(ROOT, "tools", "rpeak_host", "thr_probe.c")
RPK_C = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg", "ecg_rpeak.c")
RPK_INC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg")
RECDIR = os.path.join(ROOT, "mit_bih", "records")

P_RE = re.compile(
    r"^P(-?\d+),bpm=([\d.eE+-]+),coarse=(-?\d+),delta=(-?\d+),prev_gap=(-?\d+),")


def records_all():
    recs = []
    for fn in sorted(os.listdir(RECDIR)):
        m = re.match(r"^(\d+)_500\.bin$", fn)
        if m:
            recs.append(m.group(1))
    return recs


def build(out_dir, half):
    exe = os.path.join(out_dir, f"thr_probe_{half}.exe")
    cmd = [GCC, "-O2", "-o", exe, PROBE_SRC, RPK_C, "-I" + RPK_INC]
    if half != 40:
        cmd.append(f"-DECG_RP_LOCATE_HALF={half}")
    subprocess.run(cmd, check=True)
    return exe


def run_one(exe, rec):
    """返回 (located, coarse, delta, prev_gap) 四个等长列表（全段，未掐窗）。"""
    binp = os.path.join(RECDIR, f"{rec}_500.bin")
    out = subprocess.run([exe, binp, "0"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    loc, csr, dl, pg = [], [], [], []
    for line in out.splitlines():
        m = P_RE.match(line)
        if m:
            loc.append(int(m.group(1)))
            csr.append(int(m.group(3)))
            dl.append(int(m.group(4)))
            pg.append(int(m.group(5)))
    return loc, csr, dl, pg


def pct(sorted_xs, p):
    if not sorted_xs:
        return 0
    i = min(len(sorted_xs) - 1, int(len(sorted_xs) * p / 100.0))
    return sorted_xs[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recs", default=None)
    ap.add_argument("--half", type=int, default=40)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    out_dir = a.out or os.path.join(ROOT, "test_logs", "locate_probe_20261030")
    os.makedirs(out_dir, exist_ok=True)
    recs = records_all() if not a.recs else [r.strip() for r in a.recs.split(",")]

    exe = build(out_dir, a.half)
    print(f"thr_probe 编译完成（W={a.half}）→ {exe}")

    rows_out = []
    all_delta = []
    fn_suspect_total, fn_total = 0, 0
    suspect_rows = []

    for rec in recs:
        loc, csr, dl, pg = run_one(exe, rec)
        truth_rows, total = mitbih_eval.read_truth(
            os.path.join(RECDIR, f"{rec}_ann500.txt"))
        lo, hi = mitbih_eval.SKIP_EDGE_S * 500, total - mitbih_eval.SKIP_EDGE_S * 500
        truth_w = [(s, sym) for s, sym, b in truth_rows if b and lo <= s <= hi]
        det_w = [x for x in loc if lo <= x <= hi]
        delta_w = [d for x, d in zip(loc, dl) if lo <= x <= hi]
        r = mitbih_eval.score(truth_w, det_w)

        # --- FN 归因（必要条件口径） ---
        suspect = 0
        for s, sym in r["fn_list"]:
            prev = None
            for x, d in zip(det_w, delta_w):
                if x < s:
                    prev = (x, d)
                else:
                    break
            if prev is None:
                continue
            gap = s - prev[0]
            gap_coarse = gap + prev[1]
            if gap < 360 <= gap_coarse:
                suspect += 1
                suspect_rows.append((rec, s, sym, prev[0], prev[1], gap, gap_coarse))
        fn_total += r["fn"]
        fn_suspect_total += suspect
        all_delta.extend(delta_w)

        ds = sorted(delta_w)
        rows_out.append({
            "record": rec, "n_truth": r["n"], "tp": r["tp"], "fp": r["fp"],
            "fn": r["fn"], "se": round(r["se"], 2), "pp": round(r["pp"], 2),
            "der": round(r["der"], 2),
            "delta_min": ds[0] if ds else None, "delta_p50": pct(ds, 50),
            "delta_p95": pct(ds, 95), "delta_max": ds[-1] if ds else None,
            "fn_gap_lt360": sum(1 for s, sym in r["fn_list"]
                                if any(x < s for x in det_w)
                                and s - max(x for x in det_w if x < s) < 360),
            "fn_suspect": suspect,
        })
        print(f"  {rec}: TP {r['tp']} FP {r['fp']} FN {r['fn']} "
              f"Se {r['se']:.2f} +P {r['pp']:.2f}  δ∈[{ds[0] if ds else 0},"
              f"{ds[-1] if ds else 0}] p50={pct(ds,50)}  FN嫌疑 {suspect}/{r['fn']}")

    # --- 汇总 ---
    agg = {k: sum(x[k] for x in rows_out) for k in ("n_truth", "tp", "fp", "fn")}
    ad = sorted(all_delta)
    n = len(ad)
    dist = {f"|δ|<={t}": sum(1 for d in ad if abs(d) <= t) / n * 100
            for t in (0, 5, 10, 20, 40)} if n else {}
    summary = {
        "half": a.half, "n_records": len(rows_out),
        "tp": agg["tp"], "fp": agg["fp"], "fn": agg["fn"],
        "se": round(agg["tp"] / agg["n_truth"] * 100, 2),
        "pp": round(agg["tp"] / (agg["tp"] + agg["fp"]) * 100, 2),
        "der": round((agg["fp"] + agg["fn"]) / agg["n_truth"] * 100, 2),
        "delta_n": n,
        "delta_min": ad[0] if ad else None, "delta_p50": pct(ad, 50),
        "delta_p90": pct(ad, 90), "delta_p95": pct(ad, 95), "delta_p99": pct(ad, 99),
        "delta_max": ad[-1] if ad else None,
        "delta_pos_pct": round(sum(1 for d in ad if d > 0) / n * 100, 2) if n else 0,
        "delta_dist_pct": {k: round(v, 2) for k, v in dist.items()},
        "fn_total": fn_total, "fn_suspect_total": fn_suspect_total,
    }
    with open(os.path.join(out_dir, "per_rec.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)
    if suspect_rows:
        with open(os.path.join(out_dir, "fn_suspects.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["record", "fn_truth_s", "sym", "prev_located", "delta_prev",
                        "gap_located", "gap_coarse_domain"])
            w.writerows(suspect_rows)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n==== 汇总（W={}，{} 条）====".format(a.half, summary["n_records"]))
    print("Se/+P/DER = {se}/{pp}/{der}   TP/FP/FN = {tp}/{fp}/{fn}".format(**summary))
    print("δ = located − 粗峰：min {delta_min}  p50 {delta_p50}  p90 {delta_p90}  "
          "p95 {delta_p95}  p99 {delta_p99}  max {delta_max}  (n={delta_n})".format(**summary))
    print("δ>0 占比 {delta_pos_pct}%   |δ| 分布: {delta_dist_pct}".format(**summary))
    print("FN 域混用嫌疑（必要条件上界）：{}/{}".format(fn_suspect_total, fn_total))
    print(f"\n输出目录：{out_dir}")


if __name__ == "__main__":
    main()
