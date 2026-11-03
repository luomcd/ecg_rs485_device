# -*- coding: utf-8 -*-
"""T31 配图：把「49.4~50.6 Hz」钉死为 FFT 表观主瓣宽，而不是工频漂移。"""
import os, sys, glob
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import rcParams
from scipy.signal import butter, sosfiltfilt, get_window

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ecg_analyze as E
import _t31_width_vs_drift as T

rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False
DESK = T.DESK
FS = T.FS
OUT = os.path.join(HERE, "T31_漂移带口径_表观宽度vs真实漂移_2026-09-18.png")

FN = os.path.join(DESK, "3mV-4Hz-620k-in-2026-09-18 093952.txt")


def load(fn):
    rec = E.parse(fn)
    tu, y = E.resample(rec, "raw", FS)
    return rec, tu, y


def seg_start(rec, tu):
    gaps = E.find_gaps(rec)
    if gaps:
        segs, prev = [], 0
        for g in gaps:
            segs.append((prev, int(g[0]) - 1))
            prev = int(g[0]) + 1
        segs.append((prev, len(rec["seq"]) - 1))
        a, b = max(segs, key=lambda s: s[1] - s[0])
        return tu[a] + 1.0
    return tu[0] + 1.0


rec, tu, y = load(FN)
t0 = seg_start(rec, tu)
i0 = int((t0 - tu[0]) * FS)

Ns = [512, 833, 1667, 4096, 8192]
colors = ["#c0392b", "#e67e22", "#27ae60", "#2980b9", "#8e44ad"]

fig = plt.figure(figsize=(17.5, 10.5), dpi=110)
gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], hspace=0.34, wspace=0.24)

# ── (a) 不同窗长下 50Hz 峰的形状 ──────────────────────────────────────────
ax = fig.add_subplot(gs[0, 0])
for N, c in zip(Ns, colors):
    x = y[i0:i0 + N].copy()
    x -= x.mean()
    w = get_window("hann", N)
    X = np.abs(np.fft.rfft(x * w)) * 2 / np.sum(w)
    f = np.fft.rfftfreq(N, 1.0 / FS)
    m = (f > 47) & (f < 53)
    Xn = X[m] / X[m].max()
    ax.plot(f[m], 20 * np.log10(np.maximum(Xn, 1e-6)), color=c, lw=1.9,
            label="N=%d (%.2fs, df=%.3f Hz)" % (N, N / FS, FS / N))
ax.axhline(-6, color="k", ls=":", lw=1.2)
ax.text(47.15, -5.3, "-6 dB", fontsize=9)
ax.set_xlim(47, 53)
ax.set_ylim(-40, 4)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("归一化幅度 (dB)", fontsize=10.5)
ax.set_title("(a) 同一段真实 50 Hz 信号，换窗长后的峰形\n"
             "峰形宽度只由窗长决定，与频率漂移无关", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=8.6, loc="upper right")
ax.grid(alpha=0.28)

# ── (b) 表观宽度 vs 窗长 ────────────────────────────────────────────────
ax = fig.add_subplot(gs[0, 1])
Nall = [512, 700, 833, 1024, 1300, 1667, 2048, 3000, 4096, 6000, 8192]
ws = {3: [], 6: [], 20: []}
for N in Nall:
    if i0 + N > len(y):
        continue
    r = T.apparent_width(y[i0:i0 + N], FS)
    for d in (3, 6, 20):
        ws[d].append(r["w%d" % d])
ax.plot(Nall[:len(ws[3])], ws[3], "o-", color="#2980b9", lw=1.8, ms=5, label="-3 dB 宽")
ax.plot(Nall[:len(ws[6])], ws[6], "s-", color="#c0392b", lw=1.8, ms=5, label="-6 dB 宽")
ax.plot(Nall[:len(ws[20])], ws[20], "^-", color="#27ae60", lw=1.8, ms=5, label="-20 dB 宽")
ax.axhline(1.2, color="k", ls="--", lw=1.6)
ax.text(540, 1.42, "报告里的「49.4-50.6」= 宽 1.2 Hz", fontsize=9.6, fontweight="bold")
for N, w6, w20 in zip(Nall[:len(ws[6])], ws[6], ws[20]):
    if abs(w6 - 1.2) < 0.12:
        ax.annotate("N=%d 的 -6dB 宽 = %.2f Hz" % (N, w6), (N, w6),
                    xytext=(N + 260, w6 + 0.42), fontsize=9,
                    arrowprops=dict(arrowstyle="->", color="k", lw=1.1))
    if abs(w20 - 1.2) < 0.1:
        ax.annotate("N=%d 的 -20dB 宽 = %.2f Hz" % (N, w20), (N, w20),
                    xytext=(N + 200, w20 - 0.55), fontsize=9,
                    arrowprops=dict(arrowstyle="->", color="k", lw=1.1))
ax.set_xscale("log")
ax.xaxis.set_minor_locator(mticker.NullLocator())
ax.tick_params(axis="x", which="minor", length=0)
ax.set_xticks([512, 833, 1667, 4096, 8192])
ax.set_xticklabels(["512\n1.0s", "833\n1.7s", "1667\n3.3s", "4096\n8.2s", "8192\n16.4s"], fontsize=9)
ax.set_xlabel("窗长 N（500 Hz 采样，上轴为时长）", fontsize=10.5)
ax.set_ylabel("测得的主峰宽度 (Hz)", fontsize=10.5)
ax.set_title("(b) 峰宽随窗长单调收窄：1.2 Hz 恰好是 1.7 s 窗的 -6 dB 宽\n"
             " 故 它量的是「窗的分辨率」，不是「工频在漂」", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9)
ax.grid(alpha=0.28, which="both")

# ── (c) 真实漂移（跨天跨环境）──────────────────────────────────────────
ax = fig.add_subplot(gs[1, :])
files = sorted(glob.glob(os.path.join(DESK, "*.txt")))
rows = []
for fn in files:
    try:
        r = E.parse(fn)
    except Exception:
        continue
    if r["ncol"] != 7 or r["goodn"] < 4000:
        continue
    tu2, y2 = E.resample(r, "raw", FS)
    if T.amp50(y2, 50.0) < 300:
        continue
    fr, am = T.inst_freq(tu2, y2, 50.0, 1.0, 2.0)
    keep = am > 0.5 * np.median(am)
    if keep.sum() < 2:
        continue
    rows.append(dict(f=r["file"], n=len(fr), fr=fr[keep], fmin=fr[keep].min(),
                     fmax=fr[keep].max(), med=np.median(fr[keep])))

gmin = min(r["fmin"] for r in rows)
gmax = max(r["fmax"] for r in rows)
shown = [r for r in rows if r["fmin"] - gmax > -0.5 and r["fmax"] - gmin < 0.5]
shown = [r for r in rows if (r["fmax"] - r["fmin"]) < 0.4]     # 剔掉被伪读数拉宽的
shown.sort(key=lambda z: z["med"])
ymap = {r["f"]: i for i, r in enumerate(shown)}

for i, r in enumerate(shown):
    n = len(r["fr"])
    ax.scatter(r["fr"], np.full(n, i) + np.random.uniform(-0.13, 0.13, n),
               s=26, color="#2980b9", alpha=0.8, zorder=3)
    ax.plot([r["med"], r["med"]], [i - 0.32, i + 0.32], color="#c0392b", lw=2.2, zorder=4)
    ax.plot([r["fmin"], r["fmax"]], [i, i], color="#34495e", lw=1.0, alpha=0.5, zorder=2)
ax.axvspan(gmin, gmax, color="#27ae60", alpha=0.16, zorder=1)
ax.axvline(50.0, color="k", ls=":", lw=1.2)
ax.set_yticks(range(len(shown)))
ax.set_yticklabels([r["f"][:40] for r in shown], fontsize=8.8)
ax.set_xlim(49.85, 50.12)
ax.set_xlabel("工频瞬时频率 (Hz)  —— 2 s 窗 Hilbert 相位斜率法，raw 域列②", fontsize=10.5)
ax.set_title("(c) 真实工频漂移：8 个文件覆盖 09-16~09-18 三天、不同供电/接线/接收主机状态\n"
             "极差 %.4f ~ %.4f Hz = 宽 %.4f Hz    故  3x 外推 150 Hz 带 = %.3f Hz"
             % (gmin, gmax, gmax - gmin, 3 * (gmax - gmin)), fontsize=11.5, fontweight="bold")
ax.text(49.858, 0.22, "实测极差带 %.3f Hz  (3x 外推 = %.3f Hz)" % (gmax - gmin, 3*(gmax-gmin)),
        fontsize=9.5, color="#1e8449", fontweight="bold", ha="left")
ax.grid(alpha=0.25, axis="x")

# 右下角插一块：150Hz 带与 P2 覆盖
axin = ax.inset_axes([0.635, 0.07, 0.35, 0.30])
lo3, hi3 = 3 * gmin, 3 * gmax
axin.axvspan(lo3, hi3, color="#27ae60", alpha=0.25, label="实测漂移带 3x 外推（%.3f Hz）" % (hi3 - lo3))
for c_ in (149.80, 150.00, 150.20):
    axin.axvline(c_, color="#c0392b", lw=1.6)
axin.axvspan(149.60, 150.40, color="#c0392b", alpha=0.10, label="P2 三节 Q20 覆盖（0.80 Hz）")
axin.axvline(150.0, color="k", ls=":", lw=1.0)
axin.set_xlim(149.4, 150.6)
axin.set_yticks([])
axin.set_xlabel("150 Hz 域 (Hz)", fontsize=9)
axin.tick_params(labelsize=8)
axin.legend(fontsize=7.6, loc="upper center", framealpha=0.92)
axin.set_title("P2 覆盖 vs 真实漂移带", fontsize=9.4, fontweight="bold")

fig.suptitle("T31  工频「1.2 Hz 漂移带」口径钉死：表观谱宽 vs 真实漂移   2026-09-18",
             fontsize=14.5, fontweight="bold", y=0.985)
fig.savefig(OUT, bbox_inches="tight", facecolor="white")
print("saved:", OUT)
print("rows:", len(rows), "shown:", len(shown), "gmin/gmax:", round(gmin, 4), round(gmax, 4))
