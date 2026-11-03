# -*- coding: utf-8 -*-
"""_t33_plots.py —— T33 回放验证配图（4 panel）"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal as sps

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FS_RAW = 1e6 / 1172.0
FS_OUT = 500.0
OUT = "C:/work2026/VsPro/ads1293_demo/tools/T33_回放验证_IMP620N2_2026-09-18.png"


def notch(f0, Q, fs=FS_RAW):
    b, a = sps.iirnotch(f0, Q, fs=fs)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def bp(fs=FS_RAW):
    return [(r[0], r[1], r[2], r[4], r[5]) for r in
            sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")]


IMP620 = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
          notch(100.0, 15.0), notch(150.0, 30.0)] + bp()
N2A = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
       notch(99.80, 20.0), notch(100.00, 20.0), notch(100.20, 20.0),
       notch(149.80, 20.0), notch(150.00, 20.0), notch(150.20, 20.0)] + bp()


def Hdb(secs, f):
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, float) / FS_RAW)
    H = np.ones(len(np.atleast_1d(f)), dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
    return 20 * np.log10(np.abs(H))


fig = plt.figure(figsize=(15.4, 10.2), dpi=145)
gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.24,
                      left=0.062, right=0.975, top=0.905, bottom=0.075)
C_P0, C_N2 = "#c0392b", "#1a7f37"

# ---------- (a) 100 Hz 带 ----------
ax = fig.add_subplot(gs[0, 0])
f = np.arange(95.0, 105.01, 0.02)
ax.plot(f, Hdb(IMP620, f), color=C_P0, lw=2.0, label="IMP620（现状 单节 Q15）")
ax.plot(f, Hdb(N2A, f), color=C_N2, lw=2.0, label="IMP620_N2（三节 Q20）")
ax.axvspan(99.88, 100.07, color="#4a90d9", alpha=0.16, lw=0)
ax.text(100.0, -30, "实测漂移带\n99.88~100.07", ha="center", va="top", fontsize=8.6, color="#1f4e79")
for x, c in ((99.8, "#555"), (100.2, "#555")):
    ax.axvline(x, color=c, ls=":", lw=1.0)
ax.set_ylim(-120, 4)
ax.set_xlim(95, 105)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("|H| (dB)", fontsize=10.5)
ax.set_title("(a) 100 Hz 带：单节 Q15 → 三节 Q20（谷底拼宽）", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9.5, loc="lower right")
ax.grid(alpha=0.25, ls=":")

# ---------- (b) 150 Hz 带 ----------
ax = fig.add_subplot(gs[0, 1])
f = np.arange(144.0, 156.01, 0.02)
ax.plot(f, Hdb(IMP620, f), color=C_P0, lw=2.0, label="IMP620（现状 单节 Q30）")
ax.plot(f, Hdb(N2A, f), color=C_N2, lw=2.0, label="IMP620_N2（三节 Q20）")
ax.axvspan(149.82, 150.11, color="#4a90d9", alpha=0.16, lw=0)
ax.text(150.0, -38, "实测漂移带\n149.82~150.11", ha="center", va="top", fontsize=8.6, color="#1f4e79")
ax.plot([149.84], [Hdb(IMP620, [149.84])[0]], "v", color=C_P0, ms=7.5)
ax.annotate("实测峰位 149.84\n单节 Q30 在这里只剩 %.1f dB" % Hdb(IMP620, [149.84])[0],
            xy=(149.84, Hdb(IMP620, [149.84])[0]), xytext=(144.6, -62),
            annotation_clip=True,
            fontsize=8.8, color=C_P0,
            arrowprops=dict(arrowstyle="->", color=C_P0, lw=1.1))
ax.set_ylim(-120, 4)
ax.set_xlim(144, 156)
ax.set_xlabel("频率 (Hz)", fontsize=10.5)
ax.set_ylabel("|H| (dB)", fontsize=10.5)
ax.set_title("(b) 150 Hz 带：屏幕第一号残余的来源", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9.5, loc="lower right")
ax.grid(alpha=0.25, ls=":")

# ---------- (c) 判决比值 ----------
ax = fig.add_subplot(gs[1, 0])
labels = ["3mV / 4Hz", "5mV / 10Hz"]
p0 = [104.46, 102.71]
n2 = [103.18, 102.46]
xp = np.arange(2)
w = 0.33
b1 = ax.bar(xp - w / 2, p0, w, color=C_P0, alpha=0.9, label="IMP620 实测（现状）")
b2 = ax.bar(xp + w / 2, n2, w, color=C_N2, alpha=0.9, label="IMP620_N2 回放预测")
ax.axhline(110, color="#333", ls="--", lw=1.6)
ax.text(1.42, 110.55, "门限 110%", fontsize=9.5, color="#333", ha="right")
for b in list(b1) + list(b2):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.35, "%.2f%%" % b.get_height(),
            ha="center", fontsize=10, fontweight="bold")
ax.set_xticks(xp)
ax.set_xticklabels(labels, fontsize=10.5)
ax.set_ylim(96, 114)
ax.set_ylabel("p-p(620k) ÷ p-p(直连)  (%)", fontsize=10.5)
ax.set_title("(c) 判决比值：104.46→103.18 / 102.71→102.46", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9.5, loc="lower left")
ax.grid(alpha=0.25, axis="y", ls=":")

# ---------- (d) 三线残留 ----------
ax = fig.add_subplot(gs[1, 1])
fl = [50, 100, 150]
r0 = [2.90, 16.77, 23.96]
r2 = [2.90, 0.19, 0.07]
xp = np.arange(3)
w = 0.33
ax.bar(xp - w / 2, r0, w, color=C_P0, alpha=0.9, label="IMP620（实测）")
ax.bar(xp + w / 2, r2, w, color=C_N2, alpha=0.9, label="IMP620_N2（回放）")
ax.set_yscale("log")
ax.set_ylim(0.03, 60)
for i in range(3):
    ax.text(xp[i] - w / 2, r0[i] * 1.22, "%.2f" % r0[i], ha="center", fontsize=9.4, color=C_P0)
    ax.text(xp[i] + w / 2, max(r2[i], 0.035) * 1.22, "%.2f" % r2[i], ha="center", fontsize=9.4, color=C_N2)
ax.text(0.62, 26, "50 Hz：一个频点都没动\n（三节簇已压到 −73.9 dB）", ha="center", va="center", fontsize=8.8,
        color="#333", bbox=dict(fc="#f4f4f4", ec="#bbb", boxstyle="round,pad=0.4"))
ax.text(1.5, 4.2, "89.7× / 337.5×", ha="center", fontsize=10.5, fontweight="bold", color=C_N2)
ax.set_xticks(xp)
ax.set_xticklabels(["50 Hz", "100 Hz", "150 Hz"], fontsize=10.5)
ax.set_ylabel("单谱线峰值残留 (µV, 对数)", fontsize=10.5)
ax.set_title("(d) 三线残留：钱只花在 100/150 上", fontsize=11.5, fontweight="bold")
ax.legend(fontsize=9.5, loc="upper right")
ax.grid(alpha=0.25, axis="y", ls=":")

fig.suptitle("T33  IMP620_N2（13 节）落码回放验证 —— 3mV/4Hz 实测数据 × 抠线法（只换 100/150 两条窄带）",
             fontsize=14.5, fontweight="bold", y=0.968)
fig.text(0.5, 0.017,
         "数据：3mV-4Hz-620k-in / noin（2026-09-18 09:39~09:42）｜p-p = 500Hz 域 1s 滑动窗中位数｜"
         "外推仅一处：100/150 窄带换档后的传递比（该模型在 IMP620 上标定过：41.26 vs 实测 41.15 µV，差 0.3%）",
         ha="center", fontsize=8.6, color="#555")
fig.savefig(OUT, facecolor="white")
print("saved:", OUT)
