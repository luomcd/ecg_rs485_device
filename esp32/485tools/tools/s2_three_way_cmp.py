"""S2 批处理结果三栏对比：S2 CLI 批处理 vs 板载 float W=40 vs PC/C# double W=40

用法：
  python tools/s2_three_way_cmp.py [--out test_logs/s2_batch_summary_w40.csv]

输出：
  1) 解析 tools/batch_mit_result.txt（S2 权威结果，SD 卡落盘文件）
  2) 按 AAMI 口径对账，产出 S2 的 summary CSV
  3) 与 q6_w40_summary.csv（板载 float W=40 基线）、summary_csharp_w40.csv（C# double W=40）三栏对比
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mitbih_eval as ev  # noqa: E402

REC_DIR = os.path.join(ROOT, "mit_bih", "records")
FS = 500

S2_RESULT = os.path.join(HERE, "batch_mit_result.txt")
BASE_FW = os.path.join(ROOT, "test_logs", "q6_w40_summary.csv")
BASE_CS = os.path.join(ROOT, "test_logs", "summary_csharp_w40.csv")


def parse_s2(path):
    """解析 SD 卡结果文件 → {name: [peak_idx...]}"""
    out, pend = {}, []
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if line.startswith("P "):
            for tok in line[2:].split():
                try:
                    pend.append(int(tok))
                except ValueError:
                    pass
        elif line.startswith("FILE "):
            out[line.split()[1]] = list(pend)
            pend = []
    return out


def load_summary(path):
    d = {}
    for line in open(path, encoding="utf-8"):
        if line.startswith("record"):
            continue
        c = line.strip().split(",")
        d[int(c[0])] = dict(n=int(c[1]), tp=int(c[2]), fp=int(c[3]), fn=int(c[4]),
                             se=float(c[5]), pp=float(c[6]), der=float(c[7]))
    return d


def agg(tp, fp, fn, n):
    return tp / n * 100, tp / (tp + fp) * 100, (fp + fn) / n * 100


def main():
    out_csv = None
    args = sys.argv[1:]
    if "--out" in args:
        out_csv = args[args.index("--out") + 1]

    s2 = parse_s2(S2_RESULT)
    rows = []
    for name, peaks in s2.items():
        rec = name.split("mit_")[1].split("_")[0]
        ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
        rrows, total = ev.read_truth(ann)
        truth = [(s, sym) for s, sym, b in rrows if b]
        if not total:
            total = max([s for s, _ in truth] + peaks) + 1
        lo, hi = ev.SKIP_EDGE_S * FS, total - ev.SKIP_EDGE_S * FS
        r = ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                     [v for v in peaks if lo <= v <= hi])
        rows.append((int(rec), r['n'], r['tp'], r['fp'], r['fn'],
                     r['se'], r['pp'], r['der']))
    rows.sort()

    if out_csv:
        with open(out_csv, "w", encoding="utf-8") as f:
            f.write("record,n_truth,tp,fp,fn,se,pp,der\n")
            for rec, n, tp, fp, fn, se, pp, der in rows:
                f.write(f"{rec},{n},{tp},{fp},{fn},{se:.2f},{pp:.2f},{der:.2f}\n")
        print(f"# 已写 {out_csv}")

    base_fw = load_summary(BASE_FW)
    base_cs = load_summary(BASE_CS)
    s2d = {rec: dict(n=n, tp=tp, fp=fp, fn=fn, se=se, pp=pp, der=der)
           for rec, n, tp, fp, fn, se, pp, der in rows}

    recs = sorted(s2d)
    s_tp = s_fp = s_fn = n_tot = 0
    f_tp = f_fp = f_fn = 0
    c_tp = c_fp = c_fn = 0
    diff_fw = diff_cs = 0
    for rec in recs:
        s, f, c = s2d[rec], base_fw[rec], base_cs[rec]
        n_tot += s['n']
        s_tp += s['tp']; s_fp += s['fp']; s_fn += s['fn']
        f_tp += f['tp']; f_fp += f['fp']; f_fn += f['fn']
        c_tp += c['tp']; c_fp += c['fp']; c_fn += c['fn']
        if (s['tp'], s['fp'], s['fn']) != (f['tp'], f['fp'], f['fn']):
            diff_fw += 1
        if (s['tp'], s['fp'], s['fn']) != (c['tp'], c['fp'], c['fn']):
            diff_cs += 1

    print(f"\n=== 三栏对比（{len(recs)} 条 MIT，真值 n={n_tot}）===\n")
    s_se, s_pp, s_der = agg(s_tp, s_fp, s_fn, n_tot)
    f_se, f_pp, f_der = agg(f_tp, f_fp, f_fn, n_tot)
    c_se, c_pp, c_der = agg(c_tp, c_fp, c_fn, n_tot)

    print(f"{'引擎':<24}{'TP':>8}{'FP':>5}{'FN':>6}{'Se%':>9}{'+P%':>9}{'DER%':>9}")
    print(f"{'S2 CLI 批处理(float)':<22}{s_tp:>8}{s_fp:>5}{s_fn:>6}{s_se:>9.3f}{s_pp:>9.3f}{s_der:>9.3f}")
    print(f"{'板载 float W=40 基线':<22}{f_tp:>8}{f_fp:>5}{f_fn:>6}{f_se:>9.3f}{f_pp:>9.3f}{f_der:>9.3f}")
    print(f"{'PC/C# double W=40':<22}{c_tp:>8}{c_fp:>5}{c_fn:>6}{c_se:>9.3f}{c_pp:>9.3f}{c_der:>9.3f}")

    print(f"\nS2 vs 板载 float：逐条差异 {diff_fw} 条  ΔTP {s_tp-f_tp:+d} ΔFP {s_fp-f_fp:+d} ΔFN {s_fn-f_fn:+d}")
    print(f"S2 vs C# double ：逐条差异 {diff_cs} 条  ΔTP {s_tp-c_tp:+d} ΔFP {s_fp-c_fp:+d} ΔFN {s_fn-c_fn:+d}")
    print(f"\nΔ(Se/+P/DER) S2 vs 板载：{s_se-f_se:+.3f}/{s_pp-f_pp:+.3f}/{s_der-f_der:+.3f}")
    print(f"Δ(Se/+P/DER) S2 vs C#  ：{s_se-c_se:+.3f}/{s_pp-c_pp:+.3f}/{s_der-c_der:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
