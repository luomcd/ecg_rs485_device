# -*- coding: utf-8 -*-
"""T32 配图：候选档频响对比 + CPU 账（只算不落盘）"""
import os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from scipy import signal as sps

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False
OUT = os.path.join(HERE, "T32_新档候选_频响与CPU账_2026-09-18.png")

FS = 1e6 / 1172.0


def notch(f0, Q):
    b, a = sps.iirnotch(f0, Q, fs=FS)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def bp():
    return [(r[0], r[1], r[2], r[4], r[5])
            for r in sps.butter(4, [0.05, 150.0], btype="bandpass", fs=FS, output="sos")]


def cdb(secs, f):
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, float) / FS)
    H = np.ones(len(z), dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
    return 20 * np.log10(np.abs(H))


def clust(f0, Q, sp=0.2):
    return [notch(f0 - sp, Q), notch(f0, Q), notch(f0 + sp, Q)]


BP = bp()
S50 = clust(50.0, 12.0)
P0 = S50 + [notch(100.0, 15.0), notch(150.0, 30.0)] + BP
N1a = S50 + clust(100.0, 20.0) + [notch(150.0, 30.0)] + BP
N2a = S50 + clust(100.0, 20.0) + clust(150.0, 20.0) + BP
S50b = clust(50.0, 20.0, 0.1)
N2c = S50b + clust(100.0, 20.0) + clust(150.0, 20.0) + BP
B100 = (2 * 49.9391, 2 * 50.0372)
B150 = (3 * 49.9391, 3 * 50.0372)
R100, R150 = 874.0, 1306.7

fig = plt.figure(figsize=(17.5, 10.6), dpi=110)
gs = fig.add_gridspec(2, 2, hspace=0.33, wspace=0.22)

# ── (a) 100 Hz 域 ─────────────────────────────────────────────────
ax = fig.add_subplot(gs[0, 0])
f = np.arange(93, 107, 0.002)
ax.axvspan(*B100, color="#34495e", alpha=0.14, label="实测漂移带 0.196 Hz")
ax.axhline(-3, color="k", ls=":", lw=1.1)
for lab, secs, c, ls in (("P0 现状：单节 Q15", [notch(100.0, 15.0)], "#7f8c8d", "--"),
                         ("2 节 ±0.15 Q20", clust(100.0, 20.0, 0.3), "#e67e22", "-."),
                         ("3 节 ±0.2 Q20", clust(100.0, 20.0), "#c0392b", "-")):
    d = cdb(secs, f)
    w = cdb(secs, np.arange(*B100, 0.0005)).max()
    r = R100 * 10 ** (w / 20.0)
    ax.plot(f, np.maximum(d, -120), color=c, ls=ls, lw=1.9,
            label="%s\n  带内最差 %.1f dB，残值 %.2f µV" % (lab, w, r))
ax.set_xlim(93, 107)
ax.set_ylim(-110, 3)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("|H| (dB)", fontsize=10.5)
ax.set_title("(a) 100 Hz 簇：单节 6.68 Hz 谷 → 三节 9.84 Hz 谷，压制 −28.9 → −74.2 dB",
             fontsize=11.5, fontweight="bold")
ax.legend(fontsize=8.4, loc="center right")
ax.grid(alpha=0.28)

# ── (b) 150 Hz 域 ─────────────────────────────────────────────────
ax = fig.add_subplot(gs[0, 1])
f = np.arange(144, 156, 0.002)
ax.axvspan(*B150, color="#34495e", alpha=0.14, label="实测漂移带 0.294 Hz")
ax.axhline(-3, color="k", ls=":", lw=1.1)
for lab, secs, c, ls in (("P0 现状：单节 Q30", [notch(150.0, 30.0)], "#7f8c8d", "--"),
                         ("2 节 ±0.15 Q20", clust(150.0, 20.0, 0.3), "#e67e22", "-."),
                         ("3 节 ±0.2 Q20", clust(150.0, 20.0), "#c0392b", "-")):
    d = cdb(secs, f)
    w = cdb(secs, np.arange(*B150, 0.0005)).max()
    r = R150 * 10 ** (w / 20.0)
    ax.plot(f, np.maximum(d, -120), color=c, ls=ls, lw=1.9,
            label="%s\n  带内最差 %.1f dB，残值 %.2f µV" % (lab, w, r))
ax.set_xlim(144, 156)
ax.set_ylim(-95, 3)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("|H| (dB)", fontsize=10.5)
ax.set_title("(b) 150 Hz 簇：单节 Q30 谷只有 5.0 Hz 且中心失配就漏 → 三节覆盖漂移带",
             fontsize=11.5, fontweight="bold")
ax.legend(fontsize=8.4, loc="center right")
ax.grid(alpha=0.28)

# ── (c) 诊断带相对 P0 的额外增益 ────────────────────────────────────
ax = fig.add_subplot(gs[1, 0])
f = np.arange(20, 161, 0.05)
h0 = cdb(P0, f)
for lab, secs, c in (("只改 100 簇（N1a, 11 节）", N1a, "#2980b9"),
                     ("100+150 簇 Q20（N2a, 13 节）", N2a, "#c0392b"),
                     ("+50 簇收窄 ±0.1/Q20（N2c, 13 节）", N2c, "#27ae60")):
    ax.plot(f, cdb(secs, f) - h0, color=c, lw=1.9, label=lab)
ax.axhline(0, color="k", lw=0.9)
ax.axvspan(44, 46, color="#f1c40f", alpha=0.30)
ax.text(44.2, -46, "QRS 能量区\n44-46 Hz\n零触碰（N2c 还 +0.8~1.8 dB）", fontsize=8.6,
        color="#7d6608", fontweight="bold")
ax.set_xlim(20, 160)
ax.set_ylim(-58, 4)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("相对 P0 的额外增益 (dB)", fontsize=10.5)
ax.set_title("(c) 诊断带代价：代价全在 95-105 与 140-150；40-60 Hz 反而变好（N2c）",
             fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9, loc="lower left")
ax.grid(alpha=0.28)

# ── (d) 残值 + CPU 账 ─────────────────────────────────────────────
ax = fig.add_subplot(gs[1, 1])
rows = [("P0\n现状 IMP620\n9 节", P0, 9), ("N1a\n只改100\n11 节", N1a, 11),
        ("N2a\n100+150\n13 节", N2a, 13), ("N2c\nN2a+50收窄\n13 节", N2c, 13)]
xv = np.arange(len(rows))
r100, r150, cpus = [], [], []
for _, s, n in rows:
    r100.append(R100 * 10 ** (cdb(s, np.arange(*B100, 0.0005)).max() / 20.0))
    r150.append(R150 * 10 ** (cdb(s, np.arange(*B150, 0.0005)).max() / 20.0))
    cpus.append(6.507 + (n - 8) * 0.8115)
w = 0.36
b1 = ax.bar(xv - w / 2, r100, w, color="#2980b9", label="100 Hz 域残值")
b2 = ax.bar(xv + w / 2, r150, w, color="#c0392b", label="150 Hz 域残值")
ax.set_yscale("log")
ax.set_ylim(0.01, 300)
ax.set_xticks(xv)
ax.set_xticklabels([r[0] for r in rows], fontsize=9)
ax.set_ylabel("漂移带内最差残值 (µV，对数)", fontsize=10.5)
ax.axhline(3.0, color="#27ae60", ls="--", lw=1.5)
ax.text(3.42, 3.4, "判据 3 µV\n(= 3mVpp 信号的 0.1%)", fontsize=8.4, color="#1e8449",
        fontweight="bold", ha="right")
for b, v in list(zip(b1, r100)) + list(zip(b2, r150)):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.22, "%.2f" % v, ha="center", fontsize=8.6)
ax2 = ax.twinx()
ax2.plot(xv, cpus, "o-", color="#8e44ad", lw=2.0, ms=7, label="CPU 占用（估）")
for x, c in zip(xv, cpus):
    ax2.text(x + 0.06, c + 0.18, "%.2f%%" % c, fontsize=9, color="#6c3483", fontweight="bold")
ax2.set_ylim(4, 13.5)
ax2.set_ylabel("CPU 占用（%，M4F 实测锚点外推）", fontsize=10.5, color="#6c3483")
ax.set_title("(d) 代价与收益：残值（柱，左轴） 与 CPU（线，右轴）", fontsize=11.5, fontweight="bold")
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3)
ax.grid(alpha=0.25, axis="y")

fig.suptitle("T32  新档候选：频响与 CPU 账（只算不落盘）   2026-09-18",
             fontsize=14.5, fontweight="bold", y=0.985)
fig.savefig(OUT, bbox_inches="tight", facecolor="white")
print("saved:", OUT)
