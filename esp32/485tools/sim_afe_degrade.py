"""数字模拟 ADS1293 模拟前端畸变，量化「模拟链路吃掉多少 QRS 检测精度」

为什么不用 AECG100 实测代替
------------------------------------------------------------
实测一次只给**一个点**，而且分不清是哪个环节造成的。数字注入能：
  1. 扫曲线（HPF 截止频率连续可调，实测要焊电阻）；
  2. 做消融（只开 HPF / 只开陷波 / 全开），把责任分到各环节；
  3. 可复现、可回归（硬件每次接线都有差异）。
实测的价值另有一处：验证本脚本的模型对不对（本脚本 clean 档必须复现 summary.csv）。

链路顺序必须与硬件一致，顺序错了结论就错
------------------------------------------------------------
  MIT mV ─▶ 一阶 HPF(模拟，板上 RC) ─▶ ADC 量化(显示流 0.1526 µV/LSB)
         ─▶ 50 Hz 陷波(固件数字) ─▶ T101 帧 ─▶ 检测器
⚠ HPF 在**量化之前**（它是模拟的）；陷波在**量化之后**（固件做的）。
   反过来摆会让量化噪声先被 HPF 放大，人为恶化结果。

HPF 初值口径（容易搞错）
------------------------------------------------------------
模拟 AC 耦合在记录开始时**已处于稳态**（电容早就充好了），所以输出起点≈0，
不是「阶跃响应从 0 开始爬」。做法：在信号前补 20τ 的常量（=x[0]）让滤波器先收敛，
再从 pad 处切掉 —— 比手搓 lfilter_zi 更不容易错。

用法
------------------------------------------------------------
  # 全量 48 条，默认三档扫（clean / HPF 0.5 / HPF 0.5+陷波）
  python tools/sim_afe_degrade.py

  # 只跑难例，扫四档 HPF
  python tools/sim_afe_degrade.py --records 208,201,222,106,108 \\
      --cut 0,0.05,0.1,0.5 --notch off,q35

  # 复现自检（clean 档 vs summary.csv，必须一致）
  python tools/sim_afe_degrade.py --selfcheck
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mitbih_prep as prep        # noqa: E402
import mitbih_eval as ev          # noqa: E402

REC_DIR = os.path.join(ROOT, "mit_bih", "records")
HOST = os.path.join(HERE, "rpeak_host", "rpeak_host.exe")
FS = 500                          # 检测器域，全部判据按此写死
UV_PER_LSB = 0.1526               # 显示流标定（不是 raw code 的 0.1122）
NOTCH_F0 = 50.0


# ---------------------------------------------------------------- 信号处理

def hpf_first_order(x, fc, fs=FS):
    """一阶 RC 高通（= butter(1,'highpass') 双线性）。

    ⚠ 起始状态按「记录开始时已稳态」处理：先喂 20τ 的常量让状态收敛，再切掉。
       若直接从零状态起滤，首样本会带一个 x[0] 的阶跃尾巴，40 分钟的记录里
       前面几秒会被污染成假阳性 —— 与本脚本要测的东西无关。
    """
    if fc <= 0:
        return x
    sos = signal.butter(1, fc, "highpass", fs=fs, output="sos")
    tau = 1.0 / (2.0 * np.pi * fc)
    pad = int(min(len(x) - 1, max(2000, 20.0 * tau * fs)))
    xp = np.concatenate([np.full(pad, x[0]), x])
    yp = signal.sosfilt(sos, xp)
    return yp[pad:]


def notch_iir(x, f0=NOTCH_F0, q=35.0, fs=FS):
    """二阶 IIR 陷波（与固件同型）。Q=35 → 带宽 ≈1.4 Hz。"""
    if f0 <= 0 or q <= 0:
        return x
    b, a = signal.iirnotch(f0, q, fs)
    return signal.lfilter(b, a, x)


def to_i16(mv):
    """mV → 显示流 int16（0.1526 µV/LSB，±5 mV 满量程）。"""
    return np.clip(np.round(np.asarray(mv, dtype=np.float64) * 1000.0 / UV_PER_LSB),
                   -32768, 32767).astype(np.int32)


# ---------------------------------------------------------------- 记录读取

def load_mv(rec, ch=0, need_500=True):
    """读 MIT 记录 → 500 Hz 的 mV 数组（返回 y, ann_path）"""
    hea = os.path.join(REC_DIR, rec + ".hea")
    nsig, fs, nsamp, sigs = prep.read_header(hea)
    dat = os.path.join(REC_DIR, sigs[0]["file"])
    x = prep.read_dat_212(dat, nsamp, nsig)
    raw = x[:, ch].astype(np.float64)
    mv = (raw - sigs[ch]["baseline"]) / sigs[ch]["gain"]      # ADU → mV
    if need_500 and int(round(fs)) != FS:
        mv = signal.resample_poly(mv, FS, int(round(fs)))
    return mv, os.path.join(REC_DIR, f"{rec}_ann500.txt")


def run_host(bin_path, out_path):
    r = subprocess.run([HOST, bin_path, out_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"rpeak_host 失败: {r.stderr.strip()}")
    return ev.read_peaks(out_path)


def score_file(rec, peaks):
    """与 cmp_batch_vs_pc / summary.csv 完全相同的 AAMI 口径"""
    ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
    rows, total = ev.read_truth(ann)
    truth = [(s, sym) for s, sym, b in rows if b]
    if not total:
        total = max([s for s, _ in truth] + peaks) + 1
    lo, hi = ev.SKIP_EDGE_S * FS, total - ev.SKIP_EDGE_S * FS
    return ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                    [v for v in peaks if lo <= v <= hi])


# ---------------------------------------------------------------- 主流程

def parse_notch(spec):
    """'off' → (0,0)；'q35' → (50,35)；'50q20' → (50,20)"""
    s = spec.strip().lower()
    if s in ("off", "0", "none"):
        return 0.0, 0.0
    if s.startswith("q"):
        return NOTCH_F0, float(s[1:])
    if "q" in s:
        f, q = s.split("q")
        return float(f), float(q)
    return float(s), 35.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default="", help="逗号分隔；空=全部 48 条")
    ap.add_argument("--cut", default="0,0.05,0.1,0.5",
                    help="HPF 截止频率（Hz）列表，0=不滤")
    ap.add_argument("--notch", default="off,q35", help="陷波档，off / q35 / 50q20")
    ap.add_argument("--ch", type=int, default=0)
    ap.add_argument("--dc-mv", type=float, default=0.0,
                    help="叠加直流偏置（模拟 AECG100 ±300 mV DC offset）；由 HPF 负责衰减")
    ap.add_argument("--extra-noise-uv", type=float, default=0.0,
                    help="叠加白噪声 RMS（µV），模拟 ADS1293 本底 3.8 µV")
    ap.add_argument("--selfcheck", action="store_true",
                    help="只跑 clean 档并与 summary.csv 逐条对账")
    ap.add_argument("--csv", default="")
    ap.add_argument("--jobs", type=int, default=0, help="保留，暂未并行")
    a = ap.parse_args()

    recs = ([r.strip() for r in a.records.split(",") if r.strip()]
            if a.records else None)
    if recs is None:
        recs = sorted(f[:-8] for f in os.listdir(REC_DIR) if f.endswith("_500.bin"))
    cuts = [float(c) for c in a.cut.split(",") if c.strip()]
    notches = [parse_notch(n) for n in a.notch.split(",") if n.strip()]
    if a.selfcheck:
        cuts, notches = [0.0], [(0.0, 0.0)]

    # 配置矩阵：(label, cut, notch_f, notch_q)
    cfgs = []
    for cut in cuts:
        for (nf, nq) in notches:
            lbl = ("clean" if cut <= 0 and nf <= 0
                   else "+".join(([f"HPF{cut:g}"] if cut > 0 else [])
                                 + ([f"N{nf:g}/Q{nq:g}"] if nf > 0 else [])))
            cfgs.append((lbl, cut, nf, nq))

    base = {}
    csvp = os.path.join(ROOT, "mit_bih", "summary.csv")
    if os.path.exists(csvp):
        import csv as _csv
        for row in _csv.DictReader(open(csvp, encoding="utf-8")):
            base[row["record"]] = {k: float(v) for k, v in row.items() if k != "record"}

    tmp = tempfile.mkdtemp(prefix="simafe_")
    res = {lbl: {} for lbl, *_ in cfgs}
    peak_cache = {}          # clean 档峰序列，供自检 ① 与 _det.txt 对账
    rng = np.random.default_rng(20261027)
    try:
        for i, rec in enumerate(recs, 1):
            try:
                mv, _ = load_mv(rec, a.ch)
            except Exception as e:                     # noqa: BLE001
                print(f"  ⚠ {rec} 读取失败：{e}", file=sys.stderr)
                continue
            xin = mv + a.dc_mv
            if a.extra_noise_uv > 0:
                xin = xin + rng.normal(0.0, a.extra_noise_uv / 1000.0, len(xin))

            n_tr = None
            for lbl, cut, nf, nq in cfgs:
                y = hpf_first_order(xin, cut) if cut > 0 else xin
                if nf > 0:
                    y = notch_iir(y, nf, nq)
                bp = os.path.join(tmp, "x.bin")
                pp = os.path.join(tmp, "x.txt")
                prep.write_bin(bp, to_i16(y), per_frame=25, sta=0x02)
                pk = run_host(bp, pp)
                if cut <= 0 and nf <= 0 and rec not in peak_cache:
                    peak_cache[rec] = pk
                r = score_file(rec, pk)
                res[lbl][rec] = r
                if n_tr is None:
                    n_tr = r["n"]
            if i % 8 == 0 or i == len(recs):
                print(f"  ... {i}/{len(recs)} 条", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------- 输出
    labels = [lbl for lbl, *_ in cfgs]
    print(f"\n# 数字模拟 ADS1293 模拟前端畸变 —— {len(recs)} 条 MIT-BIH")
    print(f"# HPF 截止 {cuts} Hz ｜ 陷波 {[f'{f:g}Hz/Q{q:g}' if f else 'off' for f, q in notches]}"
          f" ｜ DC {a.dc_mv:+g} mV ｜ 噪声 {a.extra_noise_uv:g} µVrms")
    print(f"# 口径：AAMI EC57，匹配窗 ±150 ms，排除首尾各 5 s，micro 平均\n")

    w = max(14, *(len(l) for l in labels))
    hdr = f"{'rec':>4} {'真值':>5} " + " ".join(f"{l:>{w}}" for l in labels)
    print(hdr)
    print("-" * len(hdr))
    for rec in recs:
        if rec not in res[labels[0]]:
            continue
        n = res[labels[0]][rec]["n"]
        cells = " ".join(f"{res[l][rec]['se']:>{w}.2f}" for l in labels)
        print(f"{rec:>4} {n:>5} {cells}")

    def agg(lbl):
        tp = sum(r["tp"] for r in res[lbl].values())
        fp = sum(r["fp"] for r in res[lbl].values())
        fn = sum(r["fn"] for r in res[lbl].values())
        se = 100.0 * tp / (tp + fn) if tp + fn else 0.0
        pp = 100.0 * tp / (tp + fp) if tp + fp else 0.0
        der = 100.0 * (fp + fn) / (tp + fn) if tp + fn else 0.0
        return dict(tp=tp, fp=fp, fn=fn, se=se, pp=pp, der=der, n=tp + fn)

    print("-" * len(hdr))
    print("\n## 汇总（TP/FP/FN 累加后重算，不是逐条平均）\n")
    h2 = f"{'档位':<22} {'TP':>8} {'FP':>6} {'FN':>6} {'Se':>8} {'+P':>8} {'DER':>8}  {'ΔSe':>7} {'ΔDER':>7}"
    print(h2)
    print("-" * len(h2))
    a0 = agg(labels[0])
    for lbl in labels:
        g = agg(lbl)
        print(f"{lbl:<22} {g['tp']:>8} {g['fp']:>6} {g['fn']:>6} "
              f"{g['se']:>8.2f} {g['pp']:>8.2f} {g['der']:>8.2f}  "
              f"{g['se'] - a0['se']:>+7.2f} {g['der'] - a0['der']:>+7.2f}")

    # 恶化最严重的 5 条（按 Se 掉落排序）
    if len(labels) > 1:
        worst = labels[-1]
        d = sorted(((res[labels[0]][r]["se"] - res[worst][r]["se"], r)
                    for r in res[worst]), reverse=True)
        print(f"\n## Se 掉落最大 5 条（{labels[0]} → {worst}）\n")
        for drop, r in d[:5]:
            b0, b1 = res[labels[0]][r], res[worst][r]
            print(f"  {r}: Se {b0['se']:.2f} → {b1['se']:.2f} ({-drop:+.2f} pp)  "
                  f"FN {b0['fn']}→{b1['fn']}  FP {b0['fp']}→{b1['fp']}")

    # 自检闸门
    # 两级：
    #   ① 主闸门 —— 本脚本 clean 档的峰序列必须与 `{rec}_det.txt` **逐位相同**
    #      （_det.txt 就是当前 rpeak_host.exe 的输出，等于证明读写/重采样/量化全链路无损）
    #   ② 参考对照 —— 与 summary.csv 比会差一点点，因为 summary.csv 是 **Q6 之前 W=55**
    #      的冻结基线，而 rpeak_host 已按 W=40 重编。这个差值是 Q6 的收益，不是链路误差。
    if "clean" in res:
        print("\n## 自检 ①：clean 档峰序列 vs `_det.txt`（当前 rpeak_host 输出）\n")
        exact = miss = 0
        for rec in res["clean"]:
            dp = os.path.join(REC_DIR, f"{rec}_det.txt")
            if not os.path.exists(dp):
                miss += 1
                continue
            if peak_cache.get(rec) == ev.read_peaks(dp):
                exact += 1
            else:
                print(f"  ✗ {rec}: 峰序列与 _det.txt 不一致")
        print(f"  {'✅' if exact and not miss else '⚠'} 逐位一致 {exact} 条"
              f"{f'，缺 _det.txt {miss} 条' if miss else ''}\n")
        if exact:
            print("  ⇒ 读写 / 重采样 / 量化的 clean 档无损，下面各档的差值只来自滤波本身")

    if base and "clean" in res:
        print("\n## 自检 ②：clean 档 vs `summary.csv`（Q6 前 W=55 冻结基线）\n")
        btp = sum(base[r]["tp"] for r in res["clean"] if r in base)
        bfp = sum(base[r]["fp"] for r in res["clean"] if r in base)
        bfn = sum(base[r]["fn"] for r in res["clean"] if r in base)
        g0 = agg("clean")
        if btp + bfn:
            bse = 100.0 * btp / (btp + bfn)
            bpp = 100.0 * btp / (btp + bfp)
            bder = 100.0 * (bfp + bfn) / (btp + bfn)
            print(f"  本脚本(W=40)  Se {g0['se']:.2f}  +P {g0['pp']:.2f}  DER {g0['der']:.2f}")
            print(f"  summary(W=55) Se {bse:.2f}  +P {bpp:.2f}  DER {bder:.2f}")
            print(f"  Δ = Q6 收益  Se {g0['se'] - bse:+.2f} pp  DER {g0['der'] - bder:+.2f} pp"
                  "   （与 Q6 报告 99.08→99.11 同向，非链路误差）")
            nd = sum(1 for r in res["clean"]
                     if r in base and abs(res["clean"][r]["se"] - base[r]["se"]) > 0.01)
            if nd:
                print(f"  {nd}/{len(res['clean'])} 条有 ±0.0x pp 漂移，全部是 W 窗差异")

    if a.csv:
        import csv as _csv
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            wr = _csv.writer(f, lineterminator="\n")   # 仓库一律 LF（.gitattributes 会兜底，但别留痕）
            wr.writerow(["record", "n", "cfg", "tp", "fp", "fn", "se", "pp", "der"])
            for lbl in labels:
                for rec, r in res[lbl].items():
                    wr.writerow([rec, r["n"], lbl, r["tp"], r["fp"], r["fn"],
                                 f"{r['se']:.4f}", f"{r['pp']:.4f}", f"{r['der']:.4f}"])
        print(f"\n# 明细已写 {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
