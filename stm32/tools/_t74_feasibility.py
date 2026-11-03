# -*- coding: utf-8 -*-
"""_t74_feasibility.py —— T74「单固件可行性」出图：判据级冲突的可视化。

三面板：
  ① 台架治理曲线：把 620k 数据里的三根市电谐波按因子 s 缩放，跑 1 s 窗 p-p 判据
     ⇒ 标出中位 / p95 的 110% 临界点，与"链侧天花板"对比。
  ② 候选低通的权衡散点：x = 150 Hz 归一（判据须 ≥70%），y = 250 Hz 归一（越小越好）
     ⇒ 说明"保 150 就压不住 250"。
  ③ AFE(sinc) 与固件链对整机频响的贡献分解（相对 10 Hz 归一）。

用法：python _t74_feasibility.py
输出：T74_单固件可行性_判据冲突_2026-09-21.png
"""
import io
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t69_fr_forecast as F                      # noqa: E402
import _t73_input_z as T                          # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
SYS_OUT = os.path.join(HERE, "T74_单固件可行性_判据冲突_2026-09-21.png")
DATA = os.path.join(HERE, "3mv-10hz-sine-620kin-2026-09-21 104047.txt")
FS = T.FS            # 实测 txt 采样域（500）
FS_FILT = F.FS       # 滤波器设计域（853.2423）
NOM = 3000.0
LINES = [(149.87, 132.9), (199.74, 30.9), (249.73, 89.8)]


def load_chain():
    d, _, _ = T.load(DATA)
    s = 250
    return (d[:, 3] * 0.1526)[s:-s]


def pct(v):
    p = T.pp_windows(v)
    return float(np.median(p)) / NOM * 100, float(np.percentile(p, 95)) / NOM * 100


def rescale(c4, facs):
    v = c4.copy()
    for (f, _), fa in zip(LINES, facs):
        v = v - T.narrow(c4, FS, f) * (1 - fa)
    return v


def crit(c4, facs_fn, key):
    """二分找 key 口径命中 110% 的缩放因子。"""
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if pct(rescale(c4, facs_fn(mid)))[key] > 110:
            hi = mid
        else:
            lo = mid
    return lo


def main():
    c4 = load_chain()

    # ---------- ① 台架曲线 ----------
    ss = np.linspace(0, 1.0, 41)
    med, p95 = [], []
    for s in ss:
        m, p = pct(rescale(c4, [s, s, s]))
        med.append(m)
        p95.append(p)
    s_med = crit(c4, lambda x: [x, x, x], 0)
    s_p95 = crit(c4, lambda x: [x, x, x], 1)

    # ---------- ② 候选低通 ----------
    d3 = F.parse_profile(os.path.join(HERE, "..", "Filter", "ecg_filter.c"), "DIAG150_D3")
    nz, HP = d3[:4], d3[5:]

    def lp_resp(N, fc):
        full = np.vstack([nz, signal.butter(N, fc / (FS_FILT / 2), "low", output="sos"), HP])

        def H(f):
            x = np.abs(F.h_chain(full, f, True, 4, "x") * F.h_afe(f))
            return float(np.ravel(x)[0])
        return {f: H(f) / H(10.0) * 100 for f in (150.0, 200.0, 250.0)}

    cands = [(2, 250, "2阶@250 现状D3"), (4, 250, "4阶@250"), (2, 220, "2阶@220"),
             (4, 220, "4阶@220"), (2, 200, "2阶@200"), (4, 200, "4阶@200★"),
             (2, 180, "2阶@180"), (4, 180, "4阶@180"), (4, 150, "4阶@150 =N2")]
    cs = [(lab, lp_resp(N, fc)) for N, fc, lab in cands]

    # ---------- ③ AFE / 固件链分解 ----------
    fr = [150.0, 200.0, 250.0, 300.0]

    def Hafe(f):
        return float(np.ravel(F.h_afe(f))[0])

    def Htot(f):
        x = np.abs(F.h_chain(d3, f, True, 4, "x") * F.h_afe(f))
        return float(np.ravel(x)[0])

    afe_db = [20 * np.log10(Hafe(f) / Hafe(10.0)) for f in fr]
    tot_db = [20 * np.log10(Htot(f) / Htot(10.0)) for f in fr]
    fw_db = [t - a for t, a in zip(tot_db, afe_db)]

    # ================= 绘图 =================
    fig, ax = plt.subplots(1, 3, figsize=(18, 5.6))

    ax[0].plot(ss * 132.9, med, "-o", ms=3, color="#c0392b", label="中位口径")
    ax[0].plot(ss * 132.9, p95, "-s", ms=3, color="#2471a3", label="p95 口径")
    ax[0].axhline(110, color="k", ls="--", lw=1.2, label="判限 110%")
    ax[0].axvline(132.9 * s_med, color="#c0392b", ls=":", lw=1.5)
    ax[0].axvline(132.9 * s_p95, color="#2471a3", ls=":", lw=1.5)
    ax[0].axvline(123.4, color="#f39c12", ls="-", lw=2.2,
                  label="链侧天花板 123.4 µV")
    ax[0].axvspan(0, 123.4, color="#f39c12", alpha=.12)

    ax[0].text(132.9 * s_med + 2, 117.5, "中位临界\n%.1f µV" % (132.9 * s_med),
               fontsize=8.5, color="#c0392b")
    ax[0].text(132.9 * s_p95 + 2, 112.3, "p95 临界\n%.1f µV" % (132.9 * s_p95),
               fontsize=8.5, color="#2471a3")
    ax[0].text(126, 106.2, "← 链够不到的区域", fontsize=9, color="#b9770e")
    ax[0].set_xlabel("620 kΩ 数据中 150 Hz 谐波线的等效幅度（µV）")
    ax[0].set_ylabel("1 s 窗 p-p ÷ 3 mV 标称（%）")
    ax[0].set_title("① 台架治理曲线：要把 150 线压到多低才达标")
    ax[0].legend(fontsize=8.5, loc="upper right")
    ax[0].grid(alpha=.3)
    ax[0].set_ylim(105, 120)

    for lab, r in cs:
        ok = r[150.0] >= 70
        ax[1].scatter(r[250.0], r[150.0], s=70 if "★" in lab or "现状" in lab else 42,
                      color="#27ae60" if ok else "#c0392b",
                      marker="*" if "★" in lab else ("D" if "现状" in lab else "o"),
                      zorder=3, edgecolors="k", linewidths=.5)
        ax[1].annotate(lab, (r[250.0], r[150.0]), textcoords="offset points",
                       xytext=(6, 4), fontsize=8.5)
    ax[1].axhline(70, color="k", ls="--", lw=1.2)
    ax[1].text(30, 71.2, "频响判据下限 70%", fontsize=8.5)
    ax[1].set_xlabel("250 Hz 相对 10 Hz 归一（%，越小＝越能压市电 5 次谐波）")
    ax[1].set_ylabel("150 Hz 相对 10 Hz 归一（%，须 ≥70）")
    ax[1].set_title("② 候选低通权衡：保 150 Hz 就压不住 250 Hz")
    ax[1].invert_xaxis()
    ax[1].grid(alpha=.3)

    x = np.arange(len(fr))
    ax[2].bar(x - .2, fw_db, .4, label="固件链（D3：2阶@250）", color="#2471a3")
    ax[2].bar(x + .2, afe_db, .4, label="ADS1293 AFE（三级 sinc）", color="#e67e22")
    for i in x:
        ax[2].text(i, -0.35, "%.2f" % tot_db[i], ha="center", fontsize=9,
                   color="k", weight="bold")
    ax[2].axhline(20 * np.log10(0.70), color="k", ls="--", lw=1.2)
    ax[2].text(2.6, 20 * np.log10(0.70) - 0.7, "150 Hz 判限 −3.1 dB", fontsize=8.5)
    ax[2].set_xticks(x)
    ax[2].set_xticklabels(["%d Hz" % f for f in fr])
    ax[2].set_ylabel("相对 10 Hz 归一（dB）")
    ax[2].set_title("③ 整机衰减分解：AFE 才是主要限制（黑字＝整机合计）")
    ax[2].legend(fontsize=8.5)
    ax[2].grid(alpha=.3, axis="y")

    plt.tight_layout()
    plt.savefig(SYS_OUT, dpi=140)
    print("图 ->", SYS_OUT)
    print("中位临界 s=%.4f ⇒ %.1f µV｜p95 临界 s=%.4f ⇒ %.1f µV"
          % (s_med, 132.9 * s_med, s_p95, 132.9 * s_p95))


if __name__ == "__main__":
    main()
