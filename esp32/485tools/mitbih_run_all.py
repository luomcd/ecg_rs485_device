"""MIT-BIH 全量批处理：prep → 检测 → 评估 → 汇总表

对一个记录目录里所有"三大件齐全"的记录跑完整链路，输出：
  · 逐记录表（Se / +P / DER）
  · 汇总（micro 平均：总 TP / 总 FP / 总 FN）
  · CSV 落盘，便于后续画图或贴进报告

引擎（--engine）：
  host    —— 编**固件同一份 `ecg_rpeak.c`**（`tools/rpeak_host/rpeak_host.exe`）
             ✅ 这才是"验证固件"的意思：同一份 .c、float、无任何改写
             峰序列 → `{rec}_det.txt`
  csharp  —— 走 C# 参考实现（`tools/rpeak_ref`，double）
             仅在 gcc 不可用时兜底；float/double 在噪声段可能分叉
             峰序列 → `{rec}_det_csharp.txt`
             🛑 2026-10-27 之前它与 host **同写 `{rec}_det.txt`** ⇒ 互相覆盖，C# 明细反复丢失

用法：
  python mitbih_run_all.py --engine host
  python mitbih_run_all.py --engine csharp --recs 100,101
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mitbih_eval as ev          # noqa: E402  复用同一套评估口径

REC_DIR = os.path.join(ROOT, "mit_bih", "records")
HOST_EXE = os.path.join(HERE, "rpeak_host", "rpeak_host.exe")
CSREF_DIR = os.path.join(HERE, "rpeak_ref")


def have_record(rec):
    for ext in ("hea", "atr", "dat"):
        if not os.path.exists(os.path.join(REC_DIR, f"{rec}.{ext}")):
            return False
    return True


def run_prep(rec):
    b = os.path.join(REC_DIR, f"{rec}_500.bin")
    a = os.path.join(REC_DIR, f"{rec}_ann500.txt")
    if os.path.exists(b) and os.path.exists(a):
        return True
    r = subprocess.run([sys.executable, os.path.join(HERE, "mitbih_prep.py"), rec,
                        "--dir", REC_DIR], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  prep FAILED: {r.stderr.strip()[:200]}")
        return False
    return True


def run_host(rec):
    b = os.path.join(REC_DIR, f"{rec}_500.bin")
    p = os.path.join(REC_DIR, f"{rec}_det.txt")
    r = subprocess.run([HOST_EXE, b, p], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  host FAILED: {r.stderr.strip()[:200]}")
        return None
    return p


def run_csharp(rec):
    b = os.path.join(REC_DIR, f"{rec}_500.bin")
    # 🛑 W1-1（2026-10-27）：**原来这里和 run_host 同写 `{rec}_det.txt`** ⇒ 两个引擎互相覆盖，
    #   C# 明细反复丢失 —— 那份 `99.04 / 99.06 / 1.90` 的 double 基线就是这么没的（只剩末 32 峰的 `_ref.txt`）。
    #   现改为独立文件名 `{rec}_det_csharp.txt`。
    #   ⚠ host 侧**仍写 `{rec}_det.txt`，故意不改名**：
    #     `q6_locate_analysis.py:77` 与 `sim_afe_degrade.py:279`（自检① 的逐位对账基准）都以它为准，
    #     改名会连带破坏两处，且现有 48 个文件全是 host 输出。
    p = os.path.join(REC_DIR, f"{rec}_det_csharp.txt")
    r = subprocess.run(["dotnet", "run", "--project", CSREF_DIR, "--", b, 
                        os.path.join(REC_DIR, f"{rec}_ref.txt"), "--all-peaks", p],
                       capture_output=True, text=True, cwd=CSREF_DIR)
    if r.returncode != 0:
        print(f"  csharp FAILED: {r.stderr.strip()[:200]}")
        return None
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["host", "csharp"], default="host")
    ap.add_argument("--recs", default=None)
    ap.add_argument("--csv", default=os.path.join(ROOT, "mit_bih", "summary.csv"))
    args = ap.parse_args()

    # 🛑 W1-1b（2026-10-28）：`--engine csharp` 且 `--csv` 仍是默认值时会**覆盖
    #   `mit_bih/summary.csv`（host float 的 48 条冻结基线）**。engine 列只能事后
    #   看出被覆盖，救不回数据。故直接报错，逼人显式给一个带引擎/窗宽的文件名。
    default_csv = os.path.join(ROOT, "mit_bih", "summary.csv")
    if args.engine == "csharp" and os.path.normcase(os.path.abspath(args.csv)) == \
            os.path.normcase(os.path.abspath(default_csv)):
        print("🛑 拒绝：`--engine csharp` 会覆盖 `mit_bih/summary.csv`（host float 的冻结基线）。\n"
              "   请显式指定输出，例如： --csv test_logs/summary_csharp_w40.csv")
        return 2

    if args.engine == "host" and not os.path.exists(HOST_EXE):
        print(f"找不到 {HOST_EXE}\n先编：gcc -O2 -o rpeak_host.exe tools/rpeak_host/main.c "
              f"cardiolite-ecg/main/ecg/ecg_rpeak.c -Icardiolite-ecg/main/ecg")
        return 2

    if args.recs:
        recs = args.recs.split(",")
    else:
        recs = sorted(f[:-4] for f in os.listdir(REC_DIR) if f.endswith(".hea"))

    rows, skipped = [], []
    for rec in recs:
        if not have_record(rec):
            skipped.append(rec)
            continue
        print(f"[{rec}]", end=" ", flush=True)
        if not run_prep(rec):
            skipped.append(rec)
            continue
        det = (run_host if args.engine == "host" else run_csharp)(rec)
        if det is None:
            skipped.append(rec)
            continue

        ann, total = ev.read_truth(os.path.join(REC_DIR, f"{rec}_ann500.txt"))
        peaks = ev.read_peaks(det)
        truth = [(s, sym) for s, sym, b in ann if b]
        lo, hi = ev.SKIP_EDGE_S * ev.FS, total - ev.SKIP_EDGE_S * ev.FS
        r = ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                     [v for v in peaks if lo <= v <= hi])
        rows.append((rec, r))
        print(f"Se {r['se']:6.2f}  +P {r['pp']:6.2f}  DER {r['der']:5.2f}"
              f"   (N={r['n']} TP={r['tp']} FP={r['fp']} FN={r['fn']})")

    if not rows:
        print("没有可跑的记录")
        return 1

    print("\n" + "=" * 78)
    print(f"{'记录':<6}{'真值':>7}{'TP':>7}{'FP':>6}{'FN':>6}{'Se%':>9}{'+P%':>9}{'DER%':>8}")
    print("-" * 78)
    for rec, r in sorted(rows, key=lambda x: x[1]["se"]):
        print(f"{rec:<6}{r['n']:>7}{r['tp']:>7}{r['fp']:>6}{r['fn']:>6}"
              f"{r['se']:>9.2f}{r['pp']:>9.2f}{r['der']:>8.2f}")
    print("-" * 78)
    N = sum(r["n"] for _, r in rows)
    TP = sum(r["tp"] for _, r in rows)
    FP = sum(r["fp"] for _, r in rows)
    FN = sum(r["fn"] for _, r in rows)
    print(f"{'合计':<6}{N:>7}{TP:>7}{FP:>6}{FN:>6}"
          f"{TP/N*100:>9.2f}{TP/(TP+FP)*100:>9.2f}{(FP+FN)/N*100:>8.2f}")
    print("=" * 78)
    print(f"引擎 {args.engine}   记录 {len(rows)} 条   排除首尾 {ev.SKIP_EDGE_S}s   "
          f"匹配窗 ±150 ms")
    if skipped:
        print(f"跳过 {len(skipped)} 条（数据不全/失败）: {','.join(skipped[:12])}"
              f"{'...' if len(skipped) > 12 else ''}")

    with open(args.csv, "w", encoding="utf-8", newline="\n") as f:
        # 🛑 W1-1（2026-10-27）：**加 engine 列**。默认 csv 仍是 `mit_bih/summary.csv`，
        #   两个引擎都会覆盖它 ⇒ 事后分不清是谁跑的（`summary.csv` 长期是 W=55 旧基线就是这个原因）。
        #   改文件名会波及 `cmp_batch_vs_pc.py:44` 的默认基准，故只加列不改名；
        #   读 csv 的一律用 DictReader 按列名取，多这一列不影响既有脚本。
        # 🛑 W1-4（2026-10-28）：**加 newline="\n"**。Windows 上 Python 默认写 CRLF，
        #   违反本仓「内容一律 LF」硬约束（实测 summary_csharp_w40.csv 踩到 49 个 CRLF）。
        f.write("record,n_truth,tp,fp,fn,se,pp,der,engine\n")
        for rec, r in sorted(rows, key=lambda x: x[1]["se"]):
            f.write(f"{rec},{r['n']},{r['tp']},{r['fp']},{r['fn']},"
                    f"{r['se']:.2f},{r['pp']:.2f},{r['der']:.2f},{args.engine}\n")
    print(f"CSV -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
