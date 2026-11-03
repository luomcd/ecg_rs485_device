# -*- coding: utf-8 -*-
"""
_rv_figs2.py —— 《全过程回顾》D 段（09-17）的补充配图（2026-09-17 建 / 版式修订 v2）

D 段当天为"连续推理型"工作日，多数结论只以数字形式落在日报/报告里，
没有留下图。本脚本把 D1/D2/D3/D5 四条的关键数字重画成图，
配色与 _rv_figs.py / T20~T30 系列一致（白底深字）。

产物（写入 tools/）：
  RV_D1_陷波中心失配与工频漂移.png    D1  T13~T15  失配曲线 / 方案对比 / T14 更正 / 有效 fs
  RV_D2_供电三态与时基反证.png        D2  T16~T17  六批对照 / 固定vs各自工频 / 微缩放反证 / 150Hz
  RV_D3_共模电读与电流闭环.png        D3  T18~T19  电读夹逼 / 三路径闭环 / 磁否决 / 容性自洽
  RV_D5_裙边与主峰_窗口判决.png       D5  T21      窗口效应 / 主峰vs裙边 / S0飘S3不飘 / S0→S3增益

v2 修订：修 v1 的标签互相压盖；D3(d) 轴标签错位；统一加大留白与行距。
全部为解析/报表数值重绘，不依赖原始 txt。
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#ffffff"; TXT = "#1a1a1a"; GRID = "#c9ccd1"
C_RAW = "#8a8f98"; C_BAD = "#d1495b"; C_MID = "#c98a1e"; C_OK = "#1a7f4b"; C_BLUE = "#2b6cb0"
C_BOX = "#f4f6f8"

HERE = os.path.dirname(os.path.abspath(__file__))
FS = 13.8          # 画布宽
FH = 10.4          # 画布高


def style(ax, title=None, xl=None, yl=None):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=9)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.55)
    if title:
        ax.set_title(title, fontsize=10.5, color=TXT, pad=9)
    if xl:
        ax.set_xlabel(xl, fontsize=9.5, color=TXT, labelpad=4)
    if yl:
        ax.set_ylabel(yl, fontsize=9.5, color=TXT, labelpad=4)


def box(ax, xy, s, va="top", ha="left", color=TXT, fs=8.4, ec=GRID, fc=C_BOX):
    ax.text(xy[0], xy[1], s, transform=ax.transAxes, fontsize=fs, color=color,
            va=va, ha=ha, linespacing=1.5,
            bbox=dict(fc=fc, ec=ec, lw=0.6, boxstyle="round,pad=0.4"))


def legend(ax, **kw):
    kw.setdefault("fontsize", 8)
    kw.setdefault("facecolor", BG)
    kw.setdefault("edgecolor", GRID)
    kw.setdefault("framealpha", 0.95)
    ax.legend(**kw)


# ══════════════════════════ D1｜T13~T15 ══════════════════════════
def fig_d1():
    fig, axs = plt.subplots(2, 2, figsize=(FS, FH))
    fig.patch.set_facecolor(BG)

    # (a) 陷波中心失配 → 抑制曲线
    ax = axs[0, 0]
    df = np.linspace(0, 0.62, 621)
    H = df / np.sqrt(df ** 2 + (50.0 / (2 * 30.0)) ** 2)
    ax.plot(df, 20 * np.log10(np.maximum(H, 1e-12)), color=C_BLUE, lw=2.2,
            label="notch(50, Q30) 解析 $|H(\\Delta f)|$", zorder=4)
    ax.axvspan(0.0, 0.16, color=C_MID, alpha=0.18, zorder=1,
               label="实测工频漂移带 49.94~50.10 Hz（宽 0.16 Hz）")
    ax.annotate("", xy=(0.16, -3.0), xytext=(0.0, -3.0),
                arrowprops=dict(arrowstyle="<->", color=C_MID, lw=1.5))
    ax.text(0.085, -1.5, "漂移带 0.16 Hz", ha="center", fontsize=8.6, color=C_MID)
    xa = [0.0355, 0.0243]
    ya = [20 * np.log10(d / np.sqrt(d ** 2 + 0.8333 ** 2)) for d in xa]
    ax.plot(xa, ya, "o", ms=7, color=C_BAD, zorder=6)
    ax.annotate("PE 工频偏 +0.0355 Hz → 27.41 dB\nLi 工频偏 +0.0243 Hz → 30.70 dB",
                xy=(0.0355, ya[0]), xytext=(0.175, -30.5), fontsize=8.4, color=C_BAD,
                linespacing=1.6, va="center",
                arrowprops=dict(arrowstyle="->", color=C_BAD, lw=1.1))
    ax.text(0.30, -12.2, "每偏 0.01 Hz 掉 3~4 dB", fontsize=8.6, color=TXT)
    style(ax, "(a) 陷波深度 ∝ 中心失配", "陷波中心偏差 Δf (Hz)", "带内抑制 (dB)")
    ax.set_xlim(0, 0.62); ax.set_ylim(-42, 3)
    legend(ax, loc="lower right")

    # (b) T15 方案对比
    ax = axs[0, 1]
    ax.bar([0, 1], [925.7, 46.8], color=[C_BAD, C_OK], width=0.5,
           edgecolor=GRID, lw=0.6)
    ax.set_yscale("log"); ax.set_ylim(28, 5000)
    ax.text(0, 925.7 * 1.22, "925.7 µV", ha="center", fontsize=9, color=TXT)
    ax.text(1, 46.8 * 1.22, "46.8 µV", ha="center", fontsize=9, color=C_OK)
    ax.annotate("", xy=(1, 46.8 * 2.6), xytext=(0, 925.7 * 1.9),
                arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.6,
                                connectionstyle="arc3,rad=-0.25"))
    ax.text(0.5, 1500, "19.8×", ha="center", fontsize=15, color=C_OK, fontweight="bold")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["S0 现行\n(单 notch50)", "S3 三陷波\n(49.90/50.00/50.10)"], fontsize=9.5)
    style(ax, "(b) 45–55 Hz 残留：S0 → S3（同口径、加窗 rms）", None, "µV rms（对数轴）")
    box(ax, (0.02, 0.97),
        "带宽代价：44–46 Hz 保持 95.1%\n4 Hz 增益 1.000（信号零损失）", fs=8.4, va="top")

    # (c) T14 更正
    ax = axs[1, 0]
    ax.bar([0, 1], [1895.0, 94.9], color=[C_BAD, C_OK], width=0.5,
           edgecolor=GRID, lw=0.6)
    ax.set_ylim(0, 2600)
    ax.text(0, 1895.0 + 90, "1895.0 µV", ha="center", fontsize=9, color=TXT)
    ax.text(1, 94.9 + 90, "94.9 µV", ha="center", fontsize=9, color=C_OK)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["矩形窗 + 频域硬掩码\n（T14 当时口径）",
                        "加 hanning 窗 + 真 IIR\n（更正后）"], fontsize=9)
    ax.annotate("", xy=(0.62, 1000), xytext=(0.12, 1780),
                arrowprops=dict(arrowstyle="->", color=C_BAD, lw=1.3))
    ax.text(0.68, 950, "高估 65×\n窗泄漏把裙边抬成主峰", fontsize=8.8, color=C_BAD,
            linespacing=1.6)
    style(ax, "(c) T14 结论更正：理想陷波后的 45–55 残留", None, "µV rms")

    # (d) 列② 行网格
    ax = axs[1, 1]
    ax.plot([1.60, 1.7066], [1, 1], lw=16, color=C_BLUE, alpha=0.75, solid_capstyle="butt")
    ax.axvline(853.2423 / 500.0, color=C_BAD, ls="--", lw=1.8)
    ax.text(1.7095, 1.0, "853.2423 / 500\n= 1.70648", fontsize=9, color=C_BAD,
            va="center", linespacing=1.5)
    ax.text(1.620, 1.22, "实测 Δ列① 均值 = 1.7066", ha="left", va="bottom",
            fontsize=9, color=TXT)
    ax.set_xlim(1.598, 1.802); ax.set_ylim(0.35, 2.15)
    ax.set_yticks([1]); ax.set_yticklabels(["列② 行网格"], fontsize=9)
    style(ax, "(d) 列② 行网格 = 500 Hz（不是 853.24 Hz）", "Δ列① 均值（行/行）", None)
    box(ax, (0.02, 0.97),
        "→ 7 列文件里列② 做 FFT 必须先按列① 重排（M27）\n"
        "   否则 50.037 Hz 会被读成 85.39 Hz 的『强线』", fs=8.4)

    fig.suptitle("D1｜09-17 T13~T15：LL 接 GND 无改善 → PE 供电反变差 → "
                 "陷波中心固定 vs 工频漂移（判决：软件可解，且不必牺牲带宽）",
                 fontsize=11.5, color=TXT, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.945), h_pad=3.4, w_pad=3.0)
    p = os.path.join(HERE, "RV_D1_陷波中心失配与工频漂移.png")
    fig.savefig(p, dpi=140, facecolor=BG); plt.close(fig)
    print("OK", p)


# ══════════════════════════ D2｜T16~T17 ══════════════════════════
def fig_d2():
    fig, axs = plt.subplots(2, 2, figsize=(FS, FH))
    fig.patch.set_facecolor(BG)

    # (a) 六批 raw 45–55
    ax = axs[0, 0]
    raw = [11.9, 10322.2, 14534.7, 18110.9, 19754.9, 19823.5]
    cols = [C_OK, C_BLUE, C_MID, C_MID, C_BAD, C_BAD]
    ax.bar(np.arange(6), raw, color=cols, width=0.62, edgecolor=GRID, lw=0.6)
    ax.set_yscale("log"); ax.set_ylim(5, 300000)
    for i, v in enumerate(raw):
        ax.text(i, v * 1.13, f"{v:.0f}", ha="center", fontsize=8.6, color=TXT)
    ax.set_xticks(np.arange(6))
    ax.set_xticklabels(["SEL\n620k短路", "DIR\nUSB直连", "HUB\nUSB-Hub",
                        "T104\n0x03+LL→GND", "NEW\n适配器PE", "Li5V\n锂电浮地"],
                       fontsize=8.2)
    style(ax, "(a) 六批 raw 45–55 Hz 宽带 rms（同段位 fixed 6 s）", None, "µV rms（对数轴）")
    box(ax, (0.02, 0.97), "620k 短路仅 11.9 µV\n→ 干扰由『节点高阻化』决定（T1 律）",
        fs=8.4, color=C_OK, ec=C_OK)

    # (b) 固定 50.0 DFT vs 各自工频 DFT
    ax = axs[0, 1]
    x = np.arange(2); w = 0.32
    fixed = [15137.2, 18086.5]; own = [22762.5, 22833.5]
    ax.bar(x - w / 2, fixed, w, color=C_BAD, alpha=0.85, edgecolor=GRID, lw=0.6,
           label="固定 50.0000 Hz 单频 DFT（落点伪影）")
    ax.bar(x + w / 2, own, w, color=C_OK, edgecolor=GRID, lw=0.6,
           label="按各自实测工频 DFT（正确）")
    for xi, v in list(zip(x - w / 2, fixed)) + list(zip(x + w / 2, own)):
        ax.text(xi, v + 700, f"{v:.0f}", ha="center", fontsize=8.5, color=TXT)
    ax.plot([x[0] - w / 2, x[1] - w / 2], fixed, "o--", color=C_BAD, ms=5, lw=1.2)
    ax.plot([x[0] + w / 2, x[1] + w / 2], own, "o-", color=C_OK, ms=5, lw=1.2)
    ax.set_xticks(x)
    ax.set_xticklabels(["适配器 PE\n(50.0355 Hz)", "锂电浮地\n(50.0243 Hz)"], fontsize=9.5)
    ax.set_ylim(0, 42000)
    style(ax, "(b) 两批工频仅差 0.0112 Hz，就造出 1.19× 的假差异（M24）", None,
          "50 Hz 幅度 (µV)")
    legend(ax, loc="upper right", ncol=1)
    box(ax, (0.02, 0.97),
        "单看固定落点：1.19×  NG（假）\n按各自工频：1.003×  OK（铁证）", fs=8.4, va="top")

    # (c) 时基微缩放反证
    ax = axs[1, 0]
    vals = [958.0, 724.5, 836.9, 1001.9]
    cs = [C_BAD, C_OK, C_OK, C_BAD]
    ax.bar(np.arange(4), vals, color=cs, width=0.56, edgecolor=GRID, lw=0.6)
    ax.set_ylim(0, 1500)
    for i, v in enumerate(vals):
        ax.text(i, v + 30, f"{v:.1f}", ha="center", fontsize=8.8, color=TXT)
    ax.set_xticks(np.arange(4))
    ax.set_xticklabels(["① PE 不动\n(50.0355)", "② PE→搬到 Li 工频\n(α=0.999776)",
                        "③ Li 不动\n(50.0243)", "④ Li→搬到 PE 工频\n(α=1.000224)"],
                       fontsize=8.2)
    ax.annotate("", xy=(1, 830), xytext=(2, 830),
                arrowprops=dict(arrowstyle="<->", color=C_OK, lw=1.6))
    ax.text(1.5, 890, "②≈③（−13%）", ha="center", fontsize=9, color=C_OK)
    ax.annotate("", xy=(0, 1100), xytext=(3, 1100),
                arrowprops=dict(arrowstyle="<->", color=C_BAD, lw=1.4, ls=":"))
    ax.text(1.5, 1160, "④≈①（+5%）", ha="center", fontsize=9, color=C_BAD)
    style(ax, "(c) 终极反证：时基微缩放只搬工频、不动任何硬件", None, "flt 45–55 (µV)")
    box(ax, (0.02, 0.97), "→ 供电方式对 flt 残留的贡献 = 0", fs=8.6, va="top")

    # (d) 150 Hz
    ax = axs[1, 1]
    t150 = [82.5, 574.8, 721.3, 672.4, 729.1]
    ratios = [0.80, 3.95, 3.98, 3.40, 3.68]
    ax.bar(np.arange(5), t150, color=[C_OK] + [C_MID] * 4, width=0.58,
           edgecolor=GRID, lw=0.6)
    ax.set_ylim(0, 1500)
    for i, (v, r) in enumerate(zip(t150, ratios)):
        ax.text(i, v + 28, f"{v:.0f}", ha="center", fontsize=8.6, color=TXT)
        ax.text(i, v + 120, f"150/50={r}%", ha="center", fontsize=7.8, color=C_BAD)
    ax.set_xticks(np.arange(5))
    ax.set_xticklabels(["DIR\nUSB直连", "HUB\nUSB-Hub", "T104\n0x03+LL",
                        "NEW\n适配器PE", "Li5V\n锂电浮地"], fontsize=8.2)
    style(ax, "(d) 150 Hz（3 次谐波）与本地供电无关（1.08×）", None, "µV rms")
    box(ax, (0.02, 0.97),
        "PE 672.4 → Li 729.1（1.08×）→ 排除『适配器引入』\n"
        "四批 150/50 稳定在 3.4~4.0%（DIR 的 0.80% 例外待解）", fs=8.4, va="top")

    fig.suptitle("D2｜09-17 T16~T17：USB 地回路假说拆分与量级否 —— "
                 "供电三态（USB / 适配器 PE / 锂电浮地）全无关，干扰源在 PC 侧",
                 fontsize=11.5, color=TXT, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.945), h_pad=3.4, w_pad=3.0)
    p = os.path.join(HERE, "RV_D2_供电三态与时基反证.png")
    fig.savefig(p, dpi=140, facecolor=BG); plt.close(fig)
    print("OK", p)


# ══════════════════════════ D3｜T18~T19 ══════════════════════════
def fig_d3():
    fig, axs = plt.subplots(2, 2, figsize=(FS, FH))
    fig.patch.set_facecolor(BG)

    # (a) 电读夹逼
    ax = axs[0, 0]
    ax.axhspan(10.3, 19.9, color=C_MID, alpha=0.22, zorder=1)
    ax.text(7.7, 15.1, "五批 raw 反推所需\nVcm：10.3~19.9 mV", ha="right", va="center",
            fontsize=8.4, color="#8a6a12", linespacing=1.6)
    ax.plot([2], [6.4], "o", ms=11, color=C_BLUE, zorder=5)
    ax.errorbar([6], [40.5], yerr=[[5.5], [5.5]], fmt="o", ms=11, color=C_BAD,
                capsize=7, lw=2.2, zorder=5)
    ax.text(2, 2.6, "M-A\nAECG100 GND(11) <-> STM32 GND\n= 6.4 mV（稳定）",
            ha="center", va="bottom", fontsize=8.4, color=C_BLUE, linespacing=1.6)
    ax.text(6, 50.5, "M-B\nEVM GND <-> PE / 大地\n= 35~46 mV（跳动 ±15%）",
            ha="center", va="bottom", fontsize=8.4, color=C_BAD, linespacing=1.6)
    ax.set_xlim(0, 8); ax.set_ylim(0, 72)
    ax.set_xticks([2, 6])
    ax.set_xticklabels(["M-A\n（设备间）", "M-B\n（板地<->大地）"], fontsize=9.5)
    style(ax, "(a) 首次直接观测到共模（此前全是反推）", None, "AC mV（万用表宽带 rms）")
    box(ax, (0.02, 0.97),
        "需求区间被两个电读夹在中间\nM-A 低 1.6~3.1× ｜ M-B 高 1.8~4.5×", fs=8.4, va="top")

    # (b) 三条路径电流闭环
    ax = axs[0, 1]
    lo = [22.6, 5.5, 24.0]; hi = [43.3, 34.0, 27.0]
    mid = [(a + b) / 2 for a, b in zip(lo, hi)]
    ax.axvspan(10, 90, color=C_OK, alpha=0.13, zorder=1)
    ax.axvline(30, color=C_OK, ls="--", lw=1.9, zorder=2)
    for i, (a, b, m) in enumerate(zip(lo, hi, mid)):
        ax.plot([a, b], [i, i], lw=11, color=C_BLUE, alpha=0.65,
                solid_capstyle="butt", zorder=3)
        ax.plot([m], [i], "|", ms=18, color="#ffffff", mew=2.2, zorder=4)
        ax.text(m, i + 0.26, f"{m:.0f}", ha="center", fontsize=9.5, color=TXT)
    ax.text(30, 2.66, "中枢 30 nA", fontsize=10.5, color=C_OK, fontweight="bold",
            ha="center")
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["① 输入网络反推\ni = raw / |Z1|",
                        "② 板地共模 ÷ Y 电容",
                        "③ 阻抗缩放反推\n(1386×)"], fontsize=8.6)
    ax.set_ylim(-0.65, 3.05); ax.set_xlim(0, 100)
    style(ax, "(b) 干扰电流闭环：三条独立路径全落 10~90 nA", "注入电流 (nA)", None)
    box(ax, (0.02, 0.06), "三条全部落在 10~90 nA → 闭环成立", fs=8.4, va="bottom",
        color=C_OK, ec=C_OK)

    # (c) 磁感应判决
    ax = axs[1, 0]
    rows = [(2, 657, 1262, C_BAD, "需 B（A = 0.05 m²）　657 ~ 1262 µT"),
            (1, 33, 63, C_BAD, "需 B（A = 1.0 m²）　33 ~ 63 µT"),
            (0, 0.04, 0.4, C_OK, "房间 50 Hz 磁通密度　0.04 ~ 0.4 µT")]
    for y, a, b, c, lb in rows:
        ax.plot([a, b], [y, y], lw=11, color=c, alpha=0.75,
                solid_capstyle="butt", zorder=3)
        tx = b * 1.5 if b > 5 else 1.2
        ax.text(tx, y, lb, ha="left", va="center", fontsize=8.4, color=c)
    ax.set_xscale("log"); ax.set_xlim(0.015, 40000)
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["环境", "A=1.0 m²", "A=0.05 m²"],
                                                 fontsize=9)
    ax.set_ylim(-0.6, 2.75)
    style(ax, "(c) 磁感应回路判决（对数轴）", "所需 50 Hz 磁通密度 B (µT)", None)
    box(ax, (0.02, 0.055), "需比环境强 82~3155 倍 → 磁感应回路否决",
        fs=8.6, color=C_BAD, ec=C_BAD, fc="#fbeaea", va="bottom")

    # (d) 容性拾取自洽
    ax = axs[1, 1]
    ax.axvspan(1.5e-13, 1.5e-11, color=C_OK, alpha=0.13, zorder=1)
    ax.plot([3.0e-9, 3.9e-9], [1, 1], lw=13, color=C_BAD, alpha=0.8,
            solid_capstyle="butt", zorder=3)
    ax.plot([3.1e-13, 6.0e-13], [0, 0], lw=13, color=C_OK, alpha=0.85,
            solid_capstyle="butt", zorder=3)
    ax.text(5.0e-9, 1.0, "若 V = M-B 实测 40 mV\n→ 需 C = 3.0 ~ 3.9 nF　 NG",
            ha="left", va="center", fontsize=8.4, color=C_BAD, linespacing=1.6)
    ax.text(1.0e-12, 0.0, "若 V = 市电 230 V\n→ 需 C = 0.31 ~ 0.60 pF　 OK",
            ha="left", va="center", fontsize=8.4, color=C_OK, linespacing=1.6)
    ax.text(3.0e-10, 0.62, "线缆杂散电容量级（pF）", ha="center", fontsize=8.2,
            color=C_OK)
    ax.set_xscale("log"); ax.set_xlim(5e-14, 4e-8)
    ax.set_ylim(-0.75, 1.85)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["假设 V = 230 V\n（市电电位）", "假设 V = 40 mV\n（M-B 实测）"],
                       fontsize=8.6)
    style(ax, "(d) 电场容性拾取判决（对数轴）", "所需耦合电容 C (F)", None)
    box(ax, (0.02, 0.055),
        "→ 耦合到的是『市电电位』(230 V)，那 40 mV 是症状、不是驱动源\n"
        "   整台测试台未接大地，只经笔电 Y 电容弱耦 → 全系统相对市电电位摆动",
        fs=8.3, va="bottom")

    fig.suptitle("D3｜09-17 T18~T19：共模电读 SOP 定稿 ＋ 结构性发现 —— "
                 "源与 DUT 同挂一台笔电，干扰电流中枢 30 nA",
                 fontsize=11.5, color=TXT, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.945), h_pad=3.4, w_pad=3.0)
    p = os.path.join(HERE, "RV_D3_共模电读与电流闭环.png")
    fig.savefig(p, dpi=140, facecolor=BG); plt.close(fig)
    print("OK", p)


# ══════════════════════════ D5｜T21 ══════════════════════════
def fig_d5():
    fig, axs = plt.subplots(2, 2, figsize=(FS, FH))
    fig.patch.set_facecolor(BG)

    # (a) 窗口效应
    ax = axs[0, 0]
    old_rect = [1293, 2034, 2361, 1419]; old_hann = [1309, 2131, 2332, 1437]
    new_rect = [484, 779, 766, 458]; new_hann = [48.8, 24.3, 26.6, 32.6]
    x = np.arange(4); w = 0.20
    ax.bar(x - 1.5 * w, old_rect, w, color=C_BAD, alpha=0.42, edgecolor=GRID, lw=0.5,
           label="旧笔电(PE)·矩形窗")
    ax.bar(x - 0.5 * w, old_hann, w, color=C_BAD, edgecolor=GRID, lw=0.5,
           label="旧笔电(PE)·Hann")
    ax.bar(x + 0.5 * w, new_rect, w, color=C_OK, alpha=0.42, edgecolor=GRID, lw=0.5,
           label="新笔电(电池)·矩形")
    ax.bar(x + 1.5 * w, new_hann, w, color=C_OK, edgecolor=GRID, lw=0.5,
           label="新笔电(电池)·Hann")
    ax.set_yscale("log"); ax.set_ylim(8, 40000)
    ax.set_xticks(x); ax.set_xticklabels(["45–48.5", "48.5–49.5", "50.5–51.5", "51.5–55"],
                                         fontsize=9)
    style(ax, "(a) 换窗试验：旧数据的地板是真的，新数据的『地板』是泄漏伪影",
          "频带 (Hz)", "µV rms（对数轴）")
    legend(ax, loc="upper right", ncol=2, fontsize=7.6)

    # (b) 主峰 vs 真实裙边
    ax = axs[0, 1]
    items = [(0, "50 Hz 主峰", 16143, 7117, "−7.2 dB（2.3×）", C_MID),
             (1, "真实宽带裙边", 3709, 70, "−34.5 dB（52.8×）", C_OK)]
    for i, nm, a, b, tag, c in items:
        ax.bar(i - 0.17, a, 0.30, color=C_BAD, alpha=0.72, edgecolor=GRID, lw=0.5)
        ax.bar(i + 0.17, b, 0.30, color=c, edgecolor=GRID, lw=0.5)
        ax.text(i - 0.17, a * 1.22, f"{a}", ha="center", fontsize=8.5, color=C_BAD)
        ax.text(i + 0.17, b * 1.25, f"{b}", ha="center", fontsize=8.5, color=c)
        ax.text(i, max(a, b) * 3.0, tag, ha="center", fontsize=9.5, color=c,
                fontweight="bold")
    ax.set_yscale("log"); ax.set_ylim(30, 900000)
    ax.set_xlim(-0.6, 1.6)
    ax.set_xticks([0, 1]); ax.set_xticklabels(["50 Hz 主峰", "真实宽带裙边"], fontsize=9.5)
    style(ax, "(b) 笔记本脱市电压掉的是『裙边』不是『主峰』", None, "µV（对数轴）")
    box(ax, (0.02, 0.97),
        "旧·适配器PE：主峰 16143／裙边 3709（占 18.6%）\n"
        "新·电池笔电：主峰  7117／裙边   70（占 0.8%）\n"
        "→ 新数据 99.99% 就是一根 49.995 Hz 纯谱线", fs=8.3, va="top")

    # (c) S0 飘 / S3 不飘
    ax = axs[1, 0]
    x = np.arange(3); w = 0.28
    ax.bar(x - w / 2, [8621.8, 63.0, 3.6], w, color=C_RAW, edgecolor=GRID, lw=0.6,
           label="段① 11.03 s")
    ax.bar(x + w / 2, [8561.4, 280.6, 3.8], w, color=C_BLUE, edgecolor=GRID, lw=0.6,
           label="段⑦ 12.12 s（晚 12 s）")
    ax.set_yscale("log"); ax.set_ylim(1, 200000)
    for xi, (va, vb) in zip(x, [(8621.8, 8561.4), (63.0, 280.6), (3.6, 3.8)]):
        ax.text(xi - w / 2, va * 1.30, f"{va:g}", ha="center", fontsize=8.4, color=TXT)
        ax.text(xi + w / 2, vb * 1.30, f"{vb:g}", ha="center", fontsize=8.4, color=TXT)
    ax.text(0, 1.30e5, "比 1.0×", ha="center", fontsize=9.5, color=TXT)
    ax.text(1, 1.30e5, "4.45×", ha="center", fontsize=12, color=C_BAD, fontweight="bold")
    ax.text(2, 1.30e5, "1.06×", ha="center", fontsize=12, color=C_OK, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(["raw 49.5–50.5", "S0 残余", "S3 残余"],
                                         fontsize=9)
    style(ax, "(c) 决定性的可重复性证据：S0 会飘，S3 不飘", None, "µV（对数轴）")
    legend(ax, loc="lower left", fontsize=8)

    # (d) S0 → S3 增益
    ax = axs[1, 1]
    gain = [9.3, 16.2, 4.7, 3.6]
    cols = [C_OK, C_OK, C_MID, C_MID]
    ax.bar(np.arange(4), gain, color=cols, width=0.56, edgecolor=GRID, lw=0.6)
    ax.set_ylim(0, 26)
    for i, v in enumerate(gain):
        ax.text(i, v + 0.55, f"+{v:.1f} dB", ha="center", fontsize=9, color=TXT)
    ax.axhline(4.4, color=C_BAD, ls="--", lw=1.6)
    ax.set_xlim(-0.62, 4.35)
    ax.text(4.30, 4.7, "T20 报的 4.4 dB\n（裙边污染的假下限）", ha="right", va="bottom",
            fontsize=8.2, color=C_BAD, linespacing=1.5)
    ax.set_xticks(np.arange(4))
    ax.set_xticklabels(["新笔电\n620k① 干净", "新笔电\n620k⑦ 干净",
                        "旧笔电\n620k(PE) 脏", "旧笔电\n620k(Li5V) 脏"], fontsize=8.4)
    style(ax, "(d) T20 的 4.4 dB 被推翻：真值 3.6~16.2 dB", None, "S0 → S3 增益 (dB)")
    box(ax, (0.02, 0.97),
        "裙边一撤，陷波效率完全释放：S3 残留 4.0~5.1% → 0.29~0.53%（好 8~14 倍）",
        fs=8.3, va="top")

    fig.suptitle("D5｜09-17 T21：换接收主机后的 LiPc 数据 —— "
                 "T20 的 4.4 dB 是『裙边污染』造成的假下限，真值 9.2~16.2 dB",
                 fontsize=11.5, color=TXT, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.945), h_pad=3.4, w_pad=3.0)
    p = os.path.join(HERE, "RV_D5_裙边与主峰_窗口判决.png")
    fig.savefig(p, dpi=140, facecolor=BG); plt.close(fig)
    print("OK", p)


if __name__ == "__main__":
    fig_d1()
    fig_d2()
    fig_d3()
    fig_d5()
    print("done")
