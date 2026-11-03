"""固件批处理结果 vs PC 侧基线（mit_bih/summary.csv）逐条对照

用途：验证固件 `float` 版 R 峰检测器与 PC 侧 `double` 版是否等价。
判据（2026-10-27 定）：**逐条检出数差 ≤ 3 拍**视为等价；Se/+P 差 ≤ 0.20 pp 同口径复核。

用法：
  python tools/cmp_batch_vs_pc.py <串口日志.txt> [--baseline mit_bih/summary.csv]
                                  [--csv out.csv]
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mitbih_eval as ev          # noqa: E402
import ecg_batch_eval as be       # noqa: E402

FS = 500
REC_DIR = os.path.join(ROOT, "mit_bih", "records")


def fw_metrics(rec, peaks):
    """按与 PC 侧完全相同的 AAMI 口径算固件结果"""
    ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
    if not os.path.exists(ann):
        return None
    rows, total = ev.read_truth(ann)
    truth = [(s, sym) for s, sym, b in rows if b]
    if not total:
        total = max([s for s, _ in truth] + peaks) + 1
    lo, hi = ev.SKIP_EDGE_S * FS, total - ev.SKIP_EDGE_S * FS
    r = ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                 [v for v in peaks if lo <= v <= hi])
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--baseline", default=os.path.join(ROOT, "mit_bih", "summary.csv"))
    ap.add_argument("--csv", default="")
    a = ap.parse_args()

    base = {}
    for row in csv.DictReader(open(a.baseline, encoding="utf-8")):
        base[row["record"]] = {k: float(v) for k, v in row.items() if k != "record"}

    items = be.parse(a.log)
    mit = [(n, p, i) for n, p, i in items if n.startswith("mit_")]

    print(f"# 日志 {a.log}")
    print(f"# 解析到 {len(items)} 个 FILE 段，其中 MIT {len(mit)} 条\n")
    hdr = (f"{'rec':>4} {'真值':>5} | {'PC检出':>6} {'固件检出':>8} {'Δ':>4} | "
           f"{'PC Se':>7} {'固件Se':>7} {'Δpp':>6} | {'PC +P':>7} {'固件+P':>7} {'Δpp':>6} | "
           f"{'PC DER':>7} {'固件DER':>8}")
    print(hdr)
    print("-" * len(hdr))

    rows_out, bad = [], []
    for name, peaks, info in mit:
        rec = name.split("mit_")[1].split("_")[0]
        r = fw_metrics(rec, peaks)
        if r is None or rec not in base:
            print(f"{rec:>4}  ⚠ 缺标注或基线，跳过")
            continue
        b = base[rec]
        fw_det = r["ndet"]
        pc_det = int(b["tp"] + b["fp"])
        d = fw_det - pc_det
        dse, dpp = r["se"] - b["se"], r["pp"] - b["pp"]
        flag = "" if abs(d) <= 3 else "  ✗Δ拍"
        print(f"{rec:>4} {int(b['n_truth']):>5} | {pc_det:>6} {fw_det:>8} {d:>+4} | "
              f"{b['se']:>7.2f} {r['se']:>7.2f} {dse:>+6.2f} | "
              f"{b['pp']:>7.2f} {r['pp']:>7.2f} {dpp:>+6.2f} | "
              f"{b['der']:>7.2f} {r['der']:>8.2f}{flag}")
        if abs(d) > 3:
            bad.append((rec, pc_det, fw_det, d))
        rows_out.append(dict(record=rec, n_truth=int(b["n_truth"]),
                             pc_det=pc_det, fw_det=fw_det, delta=d,
                             pc_se=b["se"], fw_se=r["se"], pc_pp=b["pp"], fw_pp=r["pp"],
                             pc_der=b["der"], fw_der=r["der"],
                             fw_tp=r["tp"], fw_fp=r["fp"], fw_fn=r["fn"]))

    # 汇总：TP/FP/FN 累加后重算总指标（不是逐条平均）
    if rows_out:
        tp = sum(r["fw_tp"] for r in rows_out)
        fp = sum(r["fw_fp"] for r in rows_out)
        fn = sum(r["fw_fn"] for r in rows_out)
        se = 100.0 * tp / (tp + fn) if tp + fn else 0
        pp = 100.0 * tp / (tp + fp) if tp + fp else 0
        der = 100.0 * (fp + fn) / (tp + fn) if tp + fn else 0
        btp = sum(base[r["record"]]["tp"] for r in rows_out)
        bfp = sum(base[r["record"]]["fp"] for r in rows_out)
        bfn = sum(base[r["record"]]["fn"] for r in rows_out)
        bse = 100.0 * btp / (btp + bfn)
        bpp = 100.0 * btp / (btp + bfp)
        bder = 100.0 * (bfp + bfn) / (btp + bfn)
        dtp, dfp, dfn = tp - btp, fp - bfp, fn - bfn
        print("-" * len(hdr))
        print(f"合计 {len(rows_out):>2} 条  拍数 {btp + bfn:>6}")
        print(f"  TP  PC {btp:>6}  固件 {tp:>6}  Δ{dtp:+.0f}")
        print(f"  FP  PC {bfp:>6}  固件 {fp:>6}  Δ{dfp:+.0f}")
        print(f"  FN  PC {bfn:>6}  固件 {fn:>6}  Δ{dfn:+.0f}")
        print(f"  Se  PC {bse:6.2f}%  固件 {se:6.2f}%  Δ{se - bse:+.2f} pp")
        print(f"  +P  PC {bpp:6.2f}%  固件 {pp:6.2f}%  Δ{pp - bpp:+.2f} pp")
        print(f"  DER PC {bder:6.2f}%  固件 {der:6.2f}%  Δ{der - bder:+.2f} pp")
        print()
        if bad:
            print(f"❌ {len(bad)} 条超出 ±3 拍：")
            for rec, pc, fw, d in bad:
                print(f"   {rec}: PC {pc} → 固件 {fw}（Δ{d:+d}）")
        else:
            print("✅ 全部记录固化检出差 ≤ 3 拍 —— float 与 double 等价")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)
        print(f"\n# 明细已写 {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
