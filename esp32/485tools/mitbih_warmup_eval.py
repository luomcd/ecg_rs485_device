"""实验：`启动预热不计数`（前 N 样本的检出不计）的可行性与代价

命题拆分（两个独立问题，不能混为一谈）：
  Q1 固定时长预热**能不能止住基线段假峰**？
     —— 不能。假峰的成因是自适应阈值在「只有噪声一簇」时退化（threshold ≈ 噪声峰水平），
        而预热期只掐「计数」不掐「signal_peak/noise_peak 的更新」，所以预热一结束，
        假峰立刻接着来。假峰在 5 s 基线里是**均匀分布**的，不是只在开头冒几个。
        实测：24 个假峰按秒分布 6/4/4/6/4 —— 与「有启动瞬态、会收敛」的直觉相反。
  Q2 预热在**直喂场景**（MIT-BIH 原样喂，无基线）下的代价是多少？
     —— 每一拍预热都必然丢掉该记录的首拍。这里把代价量化。

仿真方式（**不改固件**）：
  `前 N 样本不计数` 在评分侧的等价物 = 把落在 [0, N) 的检出直接删除再算 Se/+P/FN。
  依据：`accept_peak()` 里 `peak_total++ / peak_push() / update_bpm()` 三件事**只影响计数与
  BPM 输出**，不回写任何判据量（判据只读 signal_peak / noise_peak / integ / peaks[]）。
  ⚠ 唯一的例外是**回溯搜索**依赖 `peaks[]`，若预热期内不 push，回溯会推迟到开闸后才启用。
    本仿真没有复现这一层（无法在不改源码的前提下做到），所以这里的代价是**下限估计**——
    真实上固件的损失只会更大，不会更小。

用法：
  python tools/mitbih_warmup_eval.py                       # 48 条，N = 0.5/1/2/5/13 s
  python tools/mitbih_warmup_eval.py --warm-s 0,0.5,1,2
"""
import argparse
import glob
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from mitbih_eval import read_truth, score, TOL     # noqa: E402
from mitbih_pad_eval import read_t101_bin, run_host  # noqa: E402

FS = 500
REC_DIR = os.path.join(ROOT, "mit_bih", "records")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warm-s", default="0,0.5,1,2,5,13",
                    help="预热时长列表（秒），0 = 不预热（现行）")
    ap.add_argument("--recs", default="")
    args = ap.parse_args()

    warm = [float(s) for s in args.warm_s.split(",") if s.strip() != ""]
    recs = ([s.strip().zfill(3) for s in args.recs.split(",") if s.strip()]
            if args.recs else
            sorted(os.path.basename(p)[:3]
                   for p in glob.glob(os.path.join(REC_DIR, "*_500.bin"))))

    tmp = tempfile.mkdtemp(prefix="warm_eval_")
    per_rec = []
    for rec in recs:
        src = os.path.join(REC_DIR, f"{rec}_500.bin")
        ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
        if not (os.path.exists(src) and os.path.exists(ann)):
            continue
        peaks = run_host(read_t101_bin(src).astype(np.float64), tmp, f"w_{rec}")
        rows, _ = read_truth(ann)
        truth = [(s, sym) for s, sym, b in rows if b]
        per_rec.append((rec, truth, peaks))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"记录 {len(per_rec)} 条（rpeak_host 跑固件同一份 ecg_rpeak.c；评分=全段不砍）\n")

    hdr = (f"{'预热':>7}{'真值':>8}{'检出':>8}{'TP':>8}{'FP':>7}{'FN':>7}"
           f"{'Se%':>7}{'+P%':>7}{'DER%':>7}{'首拍':>7}{'丢掉的TP':>10}{'丢掉的FP':>10}")
    print(hdr)
    print("-" * len(hdr))
    base = None
    for ws in warm:
        N = int(round(ws * FS))
        n = tp = fp = fn = nd = 0
        ok1 = 0
        lost_tp = lost_fp = 0
        for rec, truth, peaks in per_rec:
            kept = [p for p in peaks if p >= N]
            r = score(truth, kept)
            n += r["n"]; tp += r["tp"]; fp += r["fp"]; fn += r["fn"]; nd += r["ndet"]
            ok1 += 1 if (truth and any(abs(d - truth[0][0]) <= TOL for d in kept)) else 0
            if N > 0:
                r0 = score(truth, peaks)          # 不预热时的同一份检出
                lost_tp += r0["tp"] - r["tp"]
                lost_fp += r0["fp"] - r["fp"]
        if base is None:
            base = dict(tp=tp, n=n)
        print(f"{ws:>6g}s{n:>8}{nd:>8}{tp:>8}{fp:>7}{fn:>7}"
              f"{tp / n * 100:>7.2f}{tp / (tp + fp) * 100:>7.2f}"
              f"{(fp + fn) / n * 100:>7.2f}{ok1:>4}/{len(per_rec):<2}"
              f"{lost_tp:>10}{lost_fp:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
