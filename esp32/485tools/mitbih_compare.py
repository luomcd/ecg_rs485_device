#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mitbih_compare.py —— P4-b：MIT-BIH 48 条 × {fid0, fid1} 全量对账

目的（三件事，一次跑完）
----------------------
1. **fid 修法有没有副作用**：Se / +P / DER 必须不劣化（fid 只改落点坐标、不改检出数，
   所以 Se/+P 应当一位不变；若变了就说明改动溢出到判定层，必须回查）。
2. **落点有没有变好**：每个 TP 的 `|检测峰 − 标注峰|` 均值/中位/p95 —— 这是 fid 唯一会改的量。
3. **判定「可疑现象」是否存在**（T224 §6.2 降级后新增的判据）：
   统计两类指纹，两条都来自 11-09 那 2 个未定性事件：
     a) **落点处峰突出度 << 该记录典型值** —— 11-09 那次是 R 幅度的 6.8%（小鼓包），
        以及一次「−407→−645 单调缓降」（根本没有峰）。用局部突出度而不是绝对值，
        因为绝对值受基线漂移影响，而突出度对基线不敏感。
     b) **rr 恰等于不应期 80** —— 11-09 #16 的指纹，含义是「连续触发被不应期削平」。
   🛑 **MIT-BIH 这条通道是干净的**：PC 侧逐样本重放，没有 RS485、没有环缓、
      不存在"环缓满跳样本"。所以这里若出现同类指纹，就**不能**再用"输入瞬变"解释。

口径
----
**直接 import `mitbih_eval`**，不另写一套匹配逻辑：
  ±150 ms（±75 样本）匹配窗、AAMI 搏动符号集（`is_beat=1`）、首尾各剔 5 s。
自带的一致性检查：本脚本算的 tp 必须等于 `mitbih_eval.score()` 的 tp，不等就报错退出。

用法
----
    py mitbih_compare.py                       # 全 48 条
    py mitbih_compare.py --records 100 105 208 # 只跑指定记录
    py mitbih_compare.py --skip-run            # 只做统计（复用已缓存的峰序列）
"""
import argparse
import glob
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mitbih_eval as E                       # noqa: E402  口径唯一来源

MIT_BASE = r"C:\work2026\CardioLite\mit_bih\records"
OUTDIR = os.path.join(HERE, "_mitcmp")
PEAKDIR = os.path.join(OUTDIR, "peaks")
EXE = {0: os.path.join(HERE, "_rp_cmp", "rp_fid0.exe"),
       1: os.path.join(HERE, "_rp_cmp", "rp_fid1.exe")}

REFRACTORY = 80          # 与 ecg_rpeak.h 的 ECG_RP_REFRACTORY 一致
PROM_W = 20              # 突出度参考窗（±20 样本 = ±40 ms）
PROM_LOW = 0.20          # 突出度 < 典型值的 20% 记为"非 R 形态"
FRAME_BYTES = 59         # AA55|TYPE|LEN(2)|SEQ(2)|STA|25×int16|CRC8
SAMPLES_PER_FRAME = 25
SAMPLE_OFF = 8           # AA55(2)+TYPE(1)+LEN(2)+SEQ(2)+STA(1)


def load_samples(bin_path):
    """把 AA55 帧流解成 int16 数组（整块 numpy 解析，不用逐字节 python 循环）"""
    d = np.fromfile(bin_path, dtype=np.uint8)
    nfr = len(d) // FRAME_BYTES
    assert nfr * FRAME_BYTES == len(d), \
        "%s 长度 %d 不是 %d 的整数倍（帧长不是 53？）" % (bin_path, len(d), FRAME_BYTES)
    fr = d.reshape(nfr, FRAME_BYTES)
    assert (fr[:64, 0] == 0xAA).all() and (fr[:64, 1] == 0x55).all(), "帧头异常"
    payload = fr[:, SAMPLE_OFF:SAMPLE_OFF + 2 * SAMPLES_PER_FRAME].copy()
    return payload.view("<i2").ravel().astype(np.float64)


def run_host(bin_path, rec, fid):
    os.makedirs(PEAKDIR, exist_ok=True)
    out = os.path.join(PEAKDIR, "%s_fid%d.txt" % (rec, fid))
    subprocess.run([EXE[fid], bin_path, out], capture_output=True, text=True)
    return E.read_peaks(out)


def matched_offsets(truth, det):
    """与 mitbih_eval.score 完全相同的贪心匹配，额外返回 (tp 数, 偏移列表)

    🛑 必须与 score() 的规则逐条一致，否则偏移统计会与 Se/+P 对不上，
       而"对不上"这件事本身就是 oracle 失效的信号 —— 所以下面 assert 校验。
    """
    used = [False] * len(det)
    tp = 0
    offs = []
    tpk = []                      # 命中的检测峰（用于取样本值算突出度）
    for s in truth:
        lo, hi = 0, len(det)
        while lo < hi:
            mid = (lo + hi) // 2
            if det[mid] < s - E.TOL:
                lo = mid + 1
            else:
                hi = mid
        best, bestd = -1, E.TOL + 1
        j = lo
        while j < len(det) and det[j] <= s + E.TOL:
            if not used[j]:
                d = abs(det[j] - s)
                if d < bestd:
                    bestd, best = d, j
            j += 1
        if best >= 0:
            used[best] = True
            tp += 1
            offs.append(det[best] - s)
            tpk.append(det[best])
    return tp, offs, tpk


def prominence(samples, idx, w=PROM_W):
    n = len(samples)
    out = []
    for i in idx:
        if i - w < 0 or i + w >= n:
            out.append(0.0)
            continue
        out.append(samples[i] - 0.5 * (samples[i - w] + samples[i + w]))
    return np.array(out, dtype=np.float64)


def summarize(rec, samples, truth_b, det, total):
    trim = [(s, sym) for s, sym in truth_b]
    if total:
        lo, hi = E.SKIP_EDGE_S * E.FS, total - E.SKIP_EDGE_S * E.FS
        trim = [(s, sym) for s, sym in trim if lo <= s <= hi]
    det_t = [v for v in det if (not total) or (E.SKIP_EDGE_S * E.FS <= v <= total - E.SKIP_EDGE_S * E.FS)]

    r = E.score(trim, det_t)
    tp, offs, tpk = matched_offsets([s for s, _ in trim], det_t)
    assert tp == r["tp"], "%s 匹配数不一致：本脚本 %d vs mitbih_eval %d —— oracle 失效" % (rec, tp, r["tp"])

    offs = np.array([abs(o) for o in offs], dtype=np.float64) if offs else np.array([0.0])
    prom = prominence(samples, tpk)
    med = float(np.median(prom)) if len(prom) else 0.0
    n_low = int((prom < PROM_LOW * med).sum()) if med > 0 else 0
    rr = np.diff(np.array(det_t, dtype=np.int64))
    n_rr80 = int((rr == REFRACTORY).sum())

    return dict(rec=rec, n=r["n"], tp=r["tp"], fp=r["fp"], fn=r["fn"],
                se=r["se"], pp=r["pp"], der=r["der"],
                off_mean=float(offs.mean()), off_med=float(np.median(offs)),
                off_p95=float(np.percentile(offs, 95)), off_max=float(offs.max()),
                prom_med=med, n_low=n_low, n_rr80=n_rr80, nbeats=len(det_t))


def main():
    ap = argparse.ArgumentParser(description="P4-b：MIT-BIH × {fid0,fid1} 对账")
    ap.add_argument("--records", nargs="*")
    ap.add_argument("--skip-run", action="store_true")
    a = ap.parse_args()

    bins = sorted(glob.glob(os.path.join(MIT_BASE, "*_500.bin")))
    recs = [os.path.basename(b).replace("_500.bin", "") for b in bins]
    if a.records:
        recs = [r for r in recs if r in set(a.records)]
    if not recs:
        sys.exit("没有找到记录：%s" % MIT_BASE)

    os.makedirs(OUTDIR, exist_ok=True)
    rows = {0: [], 1: []}
    print("记录数 %d   exe: fid0/fid1  口径由 mitbih_eval 提供（±%d 样本、首尾剔 %d s）"
          % (len(recs), E.TOL, E.SKIP_EDGE_S))

    for k, rec in enumerate(recs, 1):
        binp = os.path.join(MIT_BASE, rec + "_500.bin")
        annp = os.path.join(MIT_BASE, rec + "_ann500.txt")
        if not os.path.exists(annp):
            print("  [%2d/%d] %s 缺标注，跳过" % (k, len(recs), rec))
            continue
        truth, total = E.read_truth(annp)
        truth_b = [(s, sym) for s, sym, b in truth if b]
        samples = load_samples(binp)

        line = []
        for fid in (0, 1):
            if a.skip_run:
                det = E.read_peaks(os.path.join(PEAKDIR, "%s_fid%d.txt" % (rec, fid)))
            else:
                det = run_host(binp, rec, fid)
            s = summarize(rec, samples, truth_b, det, total)
            rows[fid].append(s)
            line.append("fid%d Se%6.2f +P%6.2f DER%5.2f 落点中位%4.1f 低突出%2d rr80 %2d"
                        % (fid, s["se"], s["pp"], s["der"], s["off_med"], s["n_low"], s["n_rr80"]))
        print("  [%2d/%d] %s  | %s | %s" % (k, len(recs), rec, line[0], line[1]))

    # ---- 汇总（micro：把 TP/FP/FN 相加，这是 MIT-BIH 文献的通行口径）----
    print("\n" + "=" * 108)
    print("%-8s %8s %8s %8s %8s %8s %8s %8s %8s %8s"
          % ("配置", "真值", "TP", "FP", "FN", "Se%", "+P%", "DER%", "落点中位", "落点p95"))
    print("-" * 108)
    agg = {}
    for fid in (0, 1):
        rs = rows[fid]
        n = sum(r["n"] for r in rs)
        tp = sum(r["tp"] for r in rs)
        fp = sum(r["fp"] for r in rs)
        fn = sum(r["fn"] for r in rs)
        se = 100.0 * tp / n if n else 0
        pp = 100.0 * tp / (tp + fp) if (tp + fp) else 0
        der = 100.0 * (fp + fn) / n if n else 0
        agg[fid] = (n, tp, fp, fn, se, pp, der)
        print("%-8s %8d %8d %8d %8d %8.2f %8.2f %8.2f %8.1f %8.1f"
              % ("fid=%d" % fid, n, tp, fp, fn, se, pp, der,
                 float(np.median([r["off_med"] for r in rs])),
                 float(np.median([r["off_p95"] for r in rs]))))

    d = tuple(b - a2 for a2, b in zip(agg[0], agg[1]))
    print("-" * 108)
    print("差值(fid1−fid0)：TP %+d  FP %+d  FN %+d  Se %+.2f pt  +P %+.2f pt  DER %+.2f pt"
          % (d[1], d[2], d[3], d[4], d[5], d[6]))

    # ---- 可疑现象指纹 ----
    print("\n" + "=" * 108)
    print("可疑现象指纹（T224 §6.2 降级后新增判据）")
    print("  a) 低突出度峰：落点处局部突出度 < 该记录典型值的 %.0f%%" % (PROM_LOW * 100))
    print("  b) rr 恰等于不应期 %d 样本（= 11-09 #16 的指纹）" % REFRACTORY)
    print("-" * 108)
    for fid in (0, 1):
        rs = rows[fid]
        nl = sum(r["n_low"] for r in rs)
        nr = sum(r["n_rr80"] for r in rs)
        nb = sum(r["nbeats"] for r in rs)
        print("%-8s 检出 %6d 拍   低突出 %4d（%.3f%%）   rr==80 %4d（%.3f%%）"
              % ("fid=%d" % fid, nb, nl, 100.0 * nl / nb if nb else 0,
                 nr, 100.0 * nr / nb if nb else 0))
        worst = sorted(rs, key=lambda r: -r["n_low"])[:5]
        print("         低突出最多的 5 条：" +
              ", ".join("%s(%d/%d)" % (w["rec"], w["n_low"], w["nbeats"]) for w in worst))

    # ---- 落盘 ----
    csv = os.path.join(OUTDIR, "summary.csv")
    with open(csv, "w", encoding="utf-8", newline="\n") as f:
        f.write("fid,rec,n,tp,fp,fn,se,pp,der,off_mean,off_med,off_p95,off_max,"
                "prom_med,n_low,n_rr80,nbeats\n")
        for fid in (0, 1):
            for r in rows[fid]:
                f.write("%d,%s,%d,%d,%d,%d,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f,%.3f,%.1f,%d,%d,%d\n"
                        % (fid, r["rec"], r["n"], r["tp"], r["fp"], r["fn"], r["se"], r["pp"],
                           r["der"], r["off_mean"], r["off_med"], r["off_p95"], r["off_max"],
                           r["prom_med"], r["n_low"], r["n_rr80"], r["nbeats"]))
    print("\n逐记录明细 -> %s" % csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
