#!/usr/bin/env python3
"""Q6 窗宽敏感性实验：`ECG_RP_LOCATE_HALF` 取不同值时，检测结果会不会变？

为什么必须做"扫描"而不是"只看偏移"
------------------------------------
`locate_rpeak()` 的返回值会写进 `rp->last_peak`，而 `last_peak` 参与三处判据：
  ① `spacing = peak - last_peak`（决定是否进 T 波判别）
  ② `spacing >= ECG_RP_REFRACTORY`（不应期）
  ③ 回溯搜索的 `last_peak + REFRACTORY`
⇒ **窗宽不只决定"红点画在哪"，还会级联改变"哪一拍被检出"**。
所以必须完整跑评估（Se / +P / DER），不能只比位置。

方法
----
用 `gcc -DECG_RP_LOCATE_HALF=W` 编多个 host 夹具（**同一份 ecg_rpeak.c**），
对同一批 48 条 MIT-BIH 各跑一遍，与 W=55（固件现值）逐条对照。

判据
----
  · 峰列表逐位一致 ⇒ 该窗宽对该记录无影响
  · 峰数不同     ⇒ 级联改变了检出（重点看指标是否变差）
  · 峰数相同但有位移 ⇒ 纯定位变化

用法
----
    python tools/q6_window_sweep.py                 # 默认 55,40,30,20,10
    python tools/q6_window_sweep.py --ws 55,30,15
"""

from __future__ import annotations

import argparse
import bisect
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mitbih_eval as ev  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REC_DIR = os.path.join(ROOT, "mit_bih", "records")
HOST_SRC = os.path.join(HERE, "rpeak_host", "main.c")
RP_SRC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg", "ecg_rpeak.c")
RP_INC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg")

FS = ev.FS
TOL = ev.TOL
SKIP = ev.SKIP_EDGE_S


def build(W: int, exe: str) -> bool:
    cmd = ["gcc", "-O2", "-std=c11", f"-DECG_RP_LOCATE_HALF={W}",
           "-o", exe, HOST_SRC, RP_SRC, f"-I{RP_INC}", "-lm"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [FAIL] W={W} 编译失败: {r.stderr.strip()[:300]}")
        return False
    return True


def run_one(exe: str, rec: str, out: str) -> list[int] | None:
    b = os.path.join(REC_DIR, f"{rec}_500.bin")
    if not os.path.exists(b):
        return None
    r = subprocess.run([exe, b, out], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return ev.read_peaks(out)


def score_rec(rec: str, peaks: list[int]) -> dict:
    ann, total = ev.read_truth(os.path.join(REC_DIR, f"{rec}_ann500.txt"))
    truth = [(s, sym) for s, sym, b in ann if b]
    lo, hi = SKIP * FS, total - SKIP * FS
    return ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                    [v for v in peaks if lo <= v <= hi])


def offsets(rec: str, peaks: list[int]) -> list[int]:
    """每个检测峰到最近真值峰的 |偏移|（仅 |d|≤TOL，即 TP 峰）。

    🛑 必须以【检测峰】为中心 —— 以真值峰为中心 + 贪心匹配会制造虚假 +75
    （漏检拍的窗口会把相邻拍的孤立误检峰吃进来，见 Q6 报告）。
    """
    ann, total = ev.read_truth(os.path.join(REC_DIR, f"{rec}_ann500.txt"))
    truth = [s for s, _sym, b in ann if b]
    if not truth:
        return []
    lo, hi = SKIP * FS, total - SKIP * FS
    out: list[int | None] = []
    for p in peaks:
        if not (lo <= p <= hi):
            out.append(None)
            continue
        a = bisect.bisect_left(truth, p)
        cands = [truth[i] for i in (a - 1, a) if 0 <= i < len(truth)]
        if not cands:
            out.append(None)
            continue
        s = min(cands, key=lambda x: abs(x - p))
        d = p - s
        out.append(abs(d) if abs(d) <= TOL else None)
    return out


def pct(v: list[int], q: float) -> float:
    if not v:
        return 0.0
    w = sorted(v)
    k = min(len(w) - 1, max(0, int(round(q * (len(w) - 1)))))
    return float(w[k])


def main() -> int:
    ap = argparse.ArgumentParser(description="Q6 窗宽扫描")
    ap.add_argument("--ws", default="55,40,30,20,10", help="逗号分隔的窗宽半径")
    ap.add_argument("--recs", default=None)
    args = ap.parse_args()

    ws = [int(x) for x in args.ws.split(",")]
    if 55 not in ws:
        ws = [55] + ws
    base_w = 55

    recs = (args.recs.split(",") if args.recs else
            sorted(f[:-len("_500.bin")] for f in os.listdir(REC_DIR)
                   if f.endswith("_500.bin")))
    print(f"记录 {len(recs)} 条，窗宽 {ws}\n")

    tmp = tempfile.mkdtemp(prefix="q6sweep_")
    exes: dict[int, str] = {}
    for W in ws:
        exe = os.path.join(tmp, f"rp_w{W}.exe")
        if build(W, exe):
            exes[W] = exe
            print(f"  W={W:<3} 编译 OK -> {exe}")
    print()

    # ---- 逐 W 跑
    results: dict[int, dict[str, dict]] = {}      # W -> rec -> {peaks, metrics}
    for W in ws:
        exe = exes.get(W)
        if not exe:
            continue
        results[W] = {}
        for i, rec in enumerate(recs):
            out = os.path.join(tmp, f"{rec}_w{W}.txt")
            peaks = run_one(exe, rec, out)
            if peaks is None:
                continue
            results[W][rec] = {"peaks": peaks,
                                  "m": score_rec(rec, peaks),
                                  "off": offsets(rec, peaks)}
        print(f"  W={W:<3} 跑完 {len(results[W])} 条", flush=True)

    # ---- 汇总
    print("\n" + "=" * 100)
    print("窗宽扫描汇总（micro 平均；与 W=55 对照）")
    print("=" * 100)
    print(f"{'W':>4}{'总峰数':>9}{'TP':>8}{'FP':>7}{'FN':>7}"
          f"{'Se%':>9}{'+P%':>9}{'DER%':>8}{'ΔDER':>8}"
          f"{'|off|均':>9}{'中位':>6}{'P95':>6}{'max':>6}{'>5%':>7}"
          f"{'位移拍':>8}{'更准':>7}{'更差':>7}")
    print("-" * 100)

    base = results.get(base_w, {})
    base_peaks = {r: v["peaks"] for r, v in base.items()}
    base_der = None
    for W in ws:
        rs = results.get(W)
        if not rs:
            continue
        N = sum(v["m"]["n"] for v in rs.values())
        TP = sum(v["m"]["tp"] for v in rs.values())
        FP = sum(v["m"]["fp"] for v in rs.values())
        FN = sum(v["m"]["fn"] for v in rs.values())
        ndet = sum(len(v["peaks"]) for v in rs.values())
        der = (FP + FN) / N * 100 if N else 0.0
        se = TP / N * 100 if N else 0.0
        pp = TP / (TP + FP) * 100 if (TP + FP) else 0.0
        if W == base_w:
            base_der = der

        # 与 baseline 比对
        n_rec_diff = 0
        n_peak_diff = 0
        n_moved = 0
        disp_sum = 0
        disp_max = 0
        for rec, v in rs.items():
            bp = base_peaks.get(rec)
            if bp is None:
                continue
            cp = v["peaks"]
            if len(cp) != len(bp):
                n_rec_diff += 1
                n_peak_diff += abs(len(cp) - len(bp))
            if len(cp) == len(bp):
                for a, b in zip(cp, bp):
                    d = abs(a - b)
                    if d:
                        n_moved += 1
                        disp_sum += d
                        disp_max = max(disp_max, d)
        avg_disp = disp_sum / n_moved if n_moved else 0.0
        dder = f"{der - base_der:+7.2f}" if base_der is not None else "      —"
        # 定位精度（所有 TP 峰的 |offset|）
        allo = [a for v in rs.values() for a in v["off"] if a is not None]
        ao_mean = sum(allo) / len(allo) if allo else 0.0
        ao_med = pct(allo, 0.5)
        ao_p95 = pct(allo, 0.95)
        ao_max = max(allo) if allo else 0
        ao_gt5 = sum(1 for a in allo if a > 5) / max(len(allo), 1) * 100
        # 逐位对应：同一拍在 W 与 W=55 下谁的 offset 更小
        n_better = n_worse = 0
        for rec, v in rs.items():
            bp = base_peaks.get(rec)
            cp = v["peaks"]
            if bp is None or len(cp) != len(bp):
                continue
            ob = offsets(rec, bp)
            on = v["off"]
            if len(ob) != len(on):
                continue
            for a, b in zip(on, ob):
                if a is None or b is None:
                    continue
                if a < b:
                    n_better += 1
                elif a > b:
                    n_worse += 1
        print(f"{W:>4}{ndet:>9,}{TP:>8,}{FP:>7,}{FN:>7,}"
              f"{se:>9.2f}{pp:>9.2f}{der:>8.2f}{dder:>8}"
              f"{ao_mean:>9.2f}{ao_med:>6.1f}{ao_p95:>6.1f}{ao_max:>6}"
              f"{ao_gt5:>7.1f}{n_moved:>8,}{n_better:>7,}{n_worse:>7,}")

    print("-" * 100)
    print("列说明：")
    print("  峰数异记录 = 与 W=55 峰数不同的记录条数；峰数异拍 = 这些记录峰数差的绝对值之和")
    print("  位移拍     = 峰数相同、但位置不同的拍数（纯定位变化）；均位移/max 单位=样本")
    print("  ⚠ 峰数有差时不做位移统计（无法逐位对应）")

    # ---- 逐记录明细（只看有差异的）
    print("\n" + "=" * 100)
    print("逐记录差异明细（只列与 W=55 有峰的差异或指标差异的记录）")
    print("=" * 100)
    for W in ws:
        if W == base_w or W not in results:
            continue
        rs = results[W]
        rows = []
        for rec, v in sorted(rs.items()):
            bp = base_peaks.get(rec)
            if bp is None:
                continue
            cp = v["peaks"]
            same_n = len(cp) == len(bp)
            moved = sum(1 for a, b in zip(cp, bp) if a != b) if same_n else -1
            dm = v["m"]["der"] - base[rec]["m"]["der"] if rec in base else 0
            nf = len(cp) - len(bp)
            if nf != 0 or moved > 0 or abs(dm) > 0.001:
                rows.append((rec, nf, moved, dm, v["m"]["der"], base[rec]["m"]["der"]))
        if not rows:
            print(f"\nW={W}：**48 条全部逐位一致、指标完全相同**")
            continue
        print(f"\nW={W}：{len(rows)} 条有差异")
        print(f"  {'记录':<7}{'峰数差':>8}{'位移拍':>8}{'DER(新)':>10}{'DER(55)':>10}{'ΔDER':>8}")
        for rec, nf, moved, dm, d_new, d_old in rows:
            mv = "—" if moved < 0 else str(moved)
            print(f"  {rec:<7}{nf:>+8}{mv:>8}{d_new:>10.2f}{d_old:>10.2f}{dm:>+8.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
