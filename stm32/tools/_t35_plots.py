# -*- coding: utf-8 -*-
"""_t35_plots.py —— T35 验收配图：判决 / 三线残留 / 鲁棒性压力测试"""
import io
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

D = "C:/Users/luom/Desktop/ADS1293/"
UV4, UV2, ZERO, FS = 0.1526, 0.1122, 6075000.0, 500.0
FS_M = 1e6 / 1172.0

C_A, C_B, C_NG = "#D85A30", "#1D9E75", "#A32D2D"

F = {("A", "3mV/4Hz", "in"): "3mV-4Hz-620k-in-2026-09-18 093952.txt",
     ("A", "3mV/4Hz", "noin"): "3mV-4Hz-620k-noin-2026-09-18 094253.txt",
     ("A", "5mV/10Hz", "in"): "5mV-10Hz-620k-in-2026-09-18 094441.txt",
     ("A", "5mV/10Hz", "noin"): "5mV-10Hz-620k-noin-2026-09-18 094358.txt",
     ("B", "3mV/4Hz", "in"): "3mv-4hz-620k-in-2026-09-18 111023.txt",
     ("B", "3mV/4Hz", "noin"): "3mv-4hz-620k-noin-2026-09-18 111133.txt",
     ("B", "5mV/10Hz", "in"): "5mv-10hz-620k-in-2026-09-18 111248.txt",
     ("B", "5mV/10Hz", "noin"): "5mv-10hz-620k-noin-2026-09-18 111214.txt"}


def load(fn):
    i16, raw = [], []
    for ln in io.open(D + fn, encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) != 7:
            continue
        try:
            raw.append(int(p[1])); i16.append(int(p[3]))
        except ValueError:
            pass
    return np.array(i16, float) * UV4, np.array(raw, float)


def pp_med(x, win=1.0, step=0.25):
    n, s = int(win * FS), int(step * FS)
    return float(np.median([np.ptp(x[i:i + n]) for i in range(0, len(x) - n + 1, s)]))


def spec_amp(x, fs, f0, half=3.0):
    x = np.asarray(x, float) - np.mean(x)
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w)) / (w.sum() / 2.0)
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    m = (f >= f0 - half) & (f <= f0 + half)
    idx = np.where(m)[0]
    return X[idx].max()


def scale(x, lo, hi, k):
    m = x.mean()
    y = x - m
    X = np.fft.rfft(y)
    f = np.fft.rfftfreq(len(y), 1.0 / FS)
    X[(f >= lo) & (f <= hi)] *= k
    return np.fft.irfft(X, n=len(y)) + m


S = {k: load(v) for k, v in F.items()}

fig = plt.figure(figsize=(14.6, 10.6))
gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.22,
                      left=0.075, right=0.975, top=0.915, bottom=0.065)

fig.suptitle("IMP620_N2（13 节）上板实测验收    A = IMP620(9节, 09:4x)   vs   B = IMP620_N2(13节, 11:1x)",
             fontsize=15.5, fontweight="bold", y=0.972)
fig.text(0.5, 0.937, "口径：屏幕列④（0.1526 µV/LSB, 500Hz）；p-p = 1s 滑动窗中位数；门限 110%",
         ha="center", fontsize=10.2, color="#444")

ax = fig.add_subplot(gs[0, 0])
lab = ["3mV/4Hz", "5mV/10Hz"]
ra = [pp_med(S[("A", s, "in")][0]) / pp_med(S[("A", s, "noin")][0]) * 100 for s in lab]
rb = [pp_med(S[("B", s, "in")][0]) / pp_med(S[("B", s, "noin")][0]) * 100 for s in lab]
dx = np.arange(2)
b1 = ax.bar(dx - 0.19, ra, 0.36, color=C_A, label="A  IMP620（9 节）")
b2 = ax.bar(dx + 0.19, rb, 0.36, color=C_B, label="B  IMP620_N2（13 节）")
ax.axhline(110, color=C_NG, lw=1.6, ls="--")
ax.text(1.42, 110.6, "门限 110%", color=C_NG, fontsize=10, ha="right", fontweight="bold")
for bs in (b1, b2):
    for r in bs:
        ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 0.5, "%.2f%%" % r.get_height(),
                ha="center", fontsize=10.2, fontweight="bold")
ax.set_xticks(dx); ax.set_xticklabels(lab, fontsize=11)
ax.set_ylim(95, 116); ax.set_ylabel("屏幕 p-p(620k) / p-p(开路)", fontsize=10.5)
ax.set_title("(a) 主判据：两组四次抓包全部达标", fontsize=12.2)
ax.legend(fontsize=9.6, loc="upper left", framealpha=0.9)
ax.grid(axis="y", alpha=0.25)

ax = fig.add_subplot(gs[0, 1])
fs3 = [50.0, 100.0, 150.0]
def three(g, sig):
    x = S[(g, sig, "in")][0]
    return [spec_amp(x, FS, f) for f in fs3]
va3, vb3 = three("A", "3mV/4Hz"), three("B", "3mV/4Hz")
va5, vb5 = three("A", "5mV/10Hz"), three("B", "5mV/10Hz")
d2 = np.arange(3)
w = 0.2
ax.bar(d2 - 0.3, va3, w, color=C_A, label="A 3mV/4Hz")
ax.bar(d2 - 0.1, va5, w, color="#F0997B", label="A 5mV/10Hz")
ax.bar(d2 + 0.1, vb3, w, color=C_B, label="B 3mV/4Hz")
ax.bar(d2 + 0.3, vb5, w, color="#5DCAA5", label="B 5mV/10Hz")
ax.set_yscale("log")
ax.set_ylim(0.08, 60)
ax.set_xticks(d2)
ax.set_xticklabels(["50 Hz\n(未动，对照)", "100 Hz\n(+2 节 Q20)", "150 Hz\n(+2 节 Q20)"], fontsize=10.2)
ax.set_ylabel("屏幕残留峰值 (µV, log)", fontsize=10.5)
ax.set_title("(b) 三条工频线的屏幕残留", fontsize=12.2)
ax.legend(fontsize=8.8, ncol=2, loc="upper right", framealpha=0.9)
ax.grid(axis="y", alpha=0.25, which="both")
for x0, v in zip(d2 - 0.3, va3):
    ax.text(x0, v * 1.12, "%.1f" % v, ha="center", fontsize=8.6, color="#222", fontweight="bold")
for x0, v in zip(d2 - 0.1, va5):
    ax.text(x0, v * 1.12, "%.1f" % v, ha="center", fontsize=8.6, color="#222", fontweight="bold")
for x0, v in zip(d2 + 0.1, vb3):
    ax.text(x0, v * 1.12, "%.2f" % v, ha="center", fontsize=8.6, color="#222", fontweight="bold")
for x0, v in zip(d2 + 0.3, vb5):
    ax.text(x0, v * 1.12, "%.2f" % v, ha="center", fontsize=8.6, color="#222", fontweight="bold")
ax.annotate("", xy=(2.52, 24.0), xytext=(2.52, 0.55),
            arrowprops=dict(arrowstyle="<->", color="#555", lw=1.1))
ax.text(2.60, 4.0, "150Hz\n184x", fontsize=9.2, color="#333", va="center", fontweight="bold")

ax = fig.add_subplot(gs[1, 0])
ks = [1, 2, 4, 8]
def curve(g, sig, lo, hi):
    out = []
    for k in ks:
        a = scale(S[(g, sig, "in")][0], lo, hi, k)
        b = scale(S[(g, sig, "noin")][0], lo, hi, k)
        out.append(pp_med(a) / pp_med(b) * 100)
    return out
for g, sig, c, ls, mk in (("A", "3mV/4Hz", C_A, "-", "o"), ("A", "5mV/10Hz", "#F0997B", "-", "s"),
                          ("B", "3mV/4Hz", C_B, "-", "o"), ("B", "5mV/10Hz", "#5DCAA5", "-", "s")):
    ax.plot(ks, curve(g, sig, 145, 155), color=c, ls=ls, marker=mk, lw=2, ms=6,
            label="%s %s" % ("A IMP620" if g == "A" else "B N2", sig))
ax.axhline(110, color=C_NG, lw=1.8, ls="--")
ax.text(8.0, 110.8, "门限 110%", color=C_NG, fontsize=10, ha="right", fontweight="bold")
ax.text(4.05, 110.9, "A 档 k=4 就击穿", color=C_NG, fontsize=9.6, ha="left")
ax.set_xscale("log", base=2); ax.set_xticks(ks); ax.set_xticklabels(["1x", "2x", "4x", "8x"])
ax.set_xlim(0.9, 9.0); ax.set_ylim(100, 120)
ax.set_xlabel("150 Hz 干扰放大倍数 k", fontsize=10.5)
ax.set_ylabel("判决比值 (%)", fontsize=10.5)
ax.set_title("(c) 鲁棒性压力测试：150 Hz 恶化 k 倍", fontsize=12.2)
ax.legend(fontsize=9, loc="upper left", framealpha=0.9)
ax.grid(alpha=0.25)

ax = fig.add_subplot(gs[1, 1])
segs = []
for g, sig, c, lab in (("A", "3mV/4Hz", C_A, "A 3mV/4Hz"),
                       ("A", "5mV/10Hz", "#F0997B", "A 5mV/10Hz"),
                       ("B", "3mV/4Hz", C_B, "B 3mV/4Hz"),
                       ("B", "5mV/10Hz", "#5DCAA5", "B 5mV/10Hz")):
    x = S[(g, sig, "in")][0]
    n = int(5 * FS)
    segs.append([np.ptp(x[i:i + n]) for i in range(0, len(x) - n + 1, n)])
nseg = max(len(v) for v in segs)
xs = np.arange(nseg)
for i, (v, (g, sig, c, lab)) in enumerate(zip(segs, (("A", "3mV/4Hz", C_A, "A 3mV/4Hz"),
                                                   ("A", "5mV/10Hz", "#F0997B", "A 5mV/10Hz"),
                                                   ("B", "3mV/4Hz", C_B, "B 3mV/4Hz"),
                                                   ("B", "5mV/10Hz", "#5DCAA5", "B 5mV/10Hz")))):
    ax.bar(xs[:len(v)] + (i - 1.5) * 0.2, v, 0.2, color=c, label=lab)
ax.axhline(0, color="#888", lw=0.6)
ax.annotate("B 5mV 在 15~20s 有一次 8.1mV 偶发事件\n(中位数口径不受影响)",
            xy=(3.3, 8064.9), xytext=(4.6, 8500), fontsize=9, color=C_NG,
            arrowprops=dict(arrowstyle="->", color=C_NG, lw=1.1))
ax.set_xticks(xs)
ax.set_xticklabels(["%d" % (i * 5) for i in xs], fontsize=9)
ax.set_xlabel("时间段起点 (s)", fontsize=10.5)
ax.set_ylabel("段内 p-p (µV)", fontsize=10.5)
ax.set_ylim(0, 10200)
ax.set_title("(d) 逐 5s 段的 p-p（620k 接入组）", fontsize=12.2)
ax.legend(fontsize=8.8, ncol=2, loc="upper left", framealpha=0.9)
ax.grid(axis="y", alpha=0.22)

OUT = "C:/work2026/VsPro/ads1293_demo/tools/T35_上板实测验收_IMP620N2_2026-09-18.png"
fig.savefig(OUT, dpi=155, facecolor="white")
print("saved:", OUT)
print("主判据  A:", ["%.2f%%" % v for v in ra], " B:", ["%.2f%%" % v for v in rb])
