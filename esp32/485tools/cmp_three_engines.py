"""三引擎逐条对照：C# double W=40 / host float W=40 / host float W=55

用途（W1-5）：回答「float 比 double 单向多检」在新窗（W=40）下还剩多少、分布在哪。
三份 csv 都已是同口径 AAMI（TOL=75、SKIP_EDGE_S=5、FS=500）的 tp/fp/fn，直接读即可，
无需重跑检测器。

判据口径：
- 「逐位一致」：tp/fp/fn 三值全同。
- 「单向多检」：float 检出（tp+fp）> double 检出，且不存在反向记录。
- 干净组 / 难例分层：以 host float W=40 的 fn 为界（fn==0 记干净，否则难例）。

用法：
  python tools/cmp_three_engines.py
      [--csharp test_logs/summary_csharp_w40.csv]
      [--float40 test_logs/q6_w40_summary.csv]
      [--float55 mit_bih/summary.csv]
      [--csv test_logs/three_engine_cmp.csv]
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(p):
    out = {}
    for r in csv.DictReader(open(p, encoding="utf-8")):
        out[r["record"]] = (int(r["tp"]), int(r["fp"]), int(r["fn"]))
    return out


def agg(rows):
    tp = sum(v[0] for v in rows)
    fp = sum(v[1] for v in rows)
    fn = sum(v[2] for v in rows)
    n = tp + fn
    se = 100.0 * tp / n if n else 0
    pp = 100.0 * tp / (tp + fp) if tp + fp else 0
    der = 100.0 * (fp + fn) / n if n else 0
    return tp, fp, fn, se, pp, der


def fmt_engine(name, tp, fp, fn, se, pp, der):
    return (f"{name:18s} Se {se:6.2f}  +P {pp:6.2f}  DER {der:5.2f}"
            f"  (TP {tp} FP {fp} FN {fn})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csharp", default=os.path.join(ROOT, "test_logs", "summary_csharp_w40.csv"))
    ap.add_argument("--float40", default=os.path.join(ROOT, "test_logs", "q6_w40_summary.csv"))
    ap.add_argument("--float55", default=os.path.join(ROOT, "mit_bih", "summary.csv"))
    ap.add_argument("--csv", default="")
    a = ap.parse_args()

    c = load(a.csharp)          # C# double W=40
    f40 = load(a.float40)       # host float W=40
    f55 = load(a.float55)       # host float W=55

    recs = sorted(c.keys(), key=lambda r: c[r][2], reverse=True)  # 按 C# FN 降序，难例在前
    assert set(c) == set(f40) == set(f55), "三份 csv 记录集不一致"

    print("=" * 78)
    print("三引擎基线（48 条 / 108,887 拍，同口径 AAMI）")
    print("-" * 78)
    print(fmt_engine("C# double W=40", *agg([c[r] for r in recs])))
    print(fmt_engine("host float W=40", *agg([f40[r] for r in recs])))
    print(fmt_engine("host float W=55", *agg([f55[r] for r in recs])))
    print("=" * 78)

    # ── 核心分析 1：float vs double（同窗 W=40）逐条差异 ──
    print("\n# 1) float vs double（同窗 W=40）—— 「单向多检」还剩多少")
    print("-" * 78)
    same, c_more, f_more, swap = [], [], [], []
    for r in recs:
        ct, cfp, cfn = c[r]
        ft, ffp, ffn = f40[r]
        dt, dfp, dfn = ft - ct, ffp - cfp, ffn - cfn
        if dt == 0 and dfp == 0 and dfn == 0:
            same.append(r)
        elif dt > 0 and dfp >= 0 and dfn <= 0:
            f_more.append((r, dt, dfp, dfn))   # float 多检（TP↑/FN↓）
        elif dt < 0 and dfp <= 0 and dfn >= 0:
            c_more.append((r, dt, dfp, dfn))   # double 多检
        else:
            swap.append((r, dt, dfp, dfn))      # 换位/混合

    print(f"  逐位一致     : {len(same)} 条")
    print(f"  float 多检   : {len(f_more)} 条（合计 ΔTP {sum(x[1] for x in f_more):+d}）")
    print(f"  double 多检  : {len(c_more)} 条（合计 ΔTP {sum(x[1] for x in c_more):+d}）")
    print(f"  换位/混合    : {len(swap)} 条")
    if f_more:
        print(f"\n  float 多检明细（ΔTP=float-C#）：")
        for r, dt, dfp, dfn in f_more:
            print(f"    {r:>4}  ΔTP {dt:+d}  ΔFP {dfp:+d}  ΔFN {dfn:+d}"
                  f"   (C# FN={c[r][2]:>3} → float FN={f40[r][2]:>3})")
    if c_more or swap:
        print(f"\n  ⚠ 反向/混合记录（若存在，说明不是纯单向）：")
        for r, dt, dfp, dfn in c_more + swap:
            print(f"    {r:>4}  ΔTP {dt:+d}  ΔFP {dfp:+d}  ΔFN {dfn:+d}")

    # ── 核心分析 2：干净组 vs 问题组分层（口径：DER < 1.4%，34/14 条，2026-10-26 定）──
    print("\n# 2) 干净组 / 问题组分层（干净组界定：PC 基线 DER < 1.4%，34/14 条，历史定死口径）")
    print("-" * 78)
    # 分界用 C# double W=40 的 DER（当前 PC 基线），三引擎切出来都是 34/14，口径一致
    clean = [r for r in recs if c[r][0] + c[r][2] and
             100.0 * (c[r][1] + c[r][2]) / (c[r][0] + c[r][2]) < 1.4]
    hard = [r for r in recs if r not in clean]
    print(f"  干净组 {len(clean)} 条 / 问题组 {len(hard)} 条")
    for label, grp in [("干净组", clean), ("问题组", hard)]:
        if not grp:
            continue
        print(f"\n  【{label}】")
        print("    " + fmt_engine("C# double W=40", *agg([c[r] for r in grp])))
        print("    " + fmt_engine("host float W=40", *agg([f40[r] for r in grp])))
        # 组内 float-vs-double 单向性
        fsum = sum((f40[r][0] - c[r][0]) for r in grp)
        print(f"    ΔTP(float-C#) = {fsum:+d}")

    # ── 核心分析 3：窗宽 55→40 的影响（同 float 引擎）──
    print("\n# 3) 窗宽 55→40（同 host float 引擎）—— 窗收窄的净账")
    print("-" * 78)
    dt, dfp, dfn = 0, 0, 0
    for r in recs:
        dt += f40[r][0] - f55[r][0]
        dfp += f40[r][1] - f55[r][1]
        dfn += f40[r][2] - f55[r][2]
    print(f"  ΔTP {dt:+d}  ΔFP {dfp:+d}  ΔFN {dfn:+d}  （W40 - W55，TP↑且FP↓=净赚）")

    # ── 输出明细 csv ──
    if a.csv:
        fields = ["record", "n_truth",
                  "c_tp", "c_fp", "c_fn",
                  "f40_tp", "f40_fp", "f40_fn",
                  "f55_tp", "f55_fp", "f55_fn",
                  "d_tp_f40_c", "d_fn_f40_c", "d_tp_f40_f55"]
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            for r in recs:
                w.writerow(dict(
                    record=r, n_truth=c[r][0] + c[r][2],
                    c_tp=c[r][0], c_fp=c[r][1], c_fn=c[r][2],
                    f40_tp=f40[r][0], f40_fp=f40[r][1], f40_fn=f40[r][2],
                    f55_tp=f55[r][0], f55_fp=f55[r][1], f55_fn=f55[r][2],
                    d_tp_f40_c=f40[r][0] - c[r][0],
                    d_fn_f40_c=f40[r][2] - c[r][2],
                    d_tp_f40_f55=f40[r][0] - f55[r][0],
                ))
        print(f"\n# 明细已写 {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
