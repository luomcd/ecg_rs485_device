# -*- coding: utf-8 -*-
"""3-leader 寄存器路由总览图（对应 ADS1293_3leader寄存器配置梳理_2026-10-30.md）"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(15.5, 8.2))
ax.set_xlim(0, 100); ax.set_ylim(0, 62); ax.axis("off")

C_IN, C_CH, C_DIG, C_RLD = "#e8f0fe", "#e6f4ea", "#fff4e5", "#fdecea"
E_IN, E_CH, E_DIG, E_RLD = "#4285f4", "#1e8e3e", "#e37400", "#c5221f"


def box(x, y, w, h, text, fc, ec, fs=10.5, bold=False, lw=1.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.2",
                                facecolor=fc, edgecolor=ec, linewidth=lw))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", linespacing=1.55)


def arrow(x1, y1, x2, y2, color="#5f6368", style="-|>", lw=1.5, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=14, color=color, lw=lw, linestyle=ls,
                                 shrinkA=2, shrinkB=2))


# ── 左：四个输入脚 ─────────────────────────────────────────────
ax.text(4, 58.5, "EVM 电极输入（J1）", fontsize=11, fontweight="bold", color="#202124")
for y, t, c in [(49, "IN1  ← RA", E_IN), (38, "IN2  ← LA", E_IN),
                (27, "IN3  ← LL", E_IN), (13, "IN4  ← RL\n（RLD 输出）", E_RLD)]:
    box(3, y, 17, 6.2, t, C_IN, c, fs=10.5)

# ── 中：FLEX 路由 ─────────────────────────────────────────────
ax.text(30, 58.5, "FLEX 路由（0x01 / 0x02）", fontsize=11, fontweight="bold", color="#202124")
box(29, 41.5, 25, 10.5,
    "CH1 = IN2 - IN1\n导联 I\n0x01 = 0x11", C_CH, E_CH, fs=10.8, bold=True)
box(29, 22.0, 25, 10.5,
    "CH2 = IN3 - IN1\n导联 II\n0x02 = 0x19", C_CH, E_CH, fs=10.8, bold=True)
ax.text(41.5, 17.4, "0x03 = 0x00  CH3 断开", fontsize=9, color="#5f6368", ha="center")

arrow(20, 52.1, 28.4, 48.5)          # IN1 -> CH1 NEG / CH2 NEG
arrow(20, 52.1, 28.4, 27.0)
arrow(20, 41.1, 28.4, 45.5)          # IN2 -> CH1 POS
arrow(20, 30.1, 28.4, 30.0)          # IN3 -> CH2 POS

# ── 右：抽取链 ────────────────────────────────────────────────
ax.text(60, 58.5, "数字抽取链 -> 853.33 Hz（0x13 / 0x21 / 0x22 / 0x25）", fontsize=11,
        fontweight="bold", color="#202124")
box(60, 42.0, 36, 12.0,
    "SDM  102,400 Hz      (0x13 = 0x00, FS_HIGH=0)\n"
    "  / R1=4  ->  25,600 Hz      (0x25 = 0x00)\n"
    "  / R2=5  ->   5,120 Hz      (0x21 = 0x02)\n"
    "  / R3=6  ->     853.33 Hz   (0x22/23/24 = 0x02)", C_DIG, E_DIG, fs=10.0)
box(60, 32.0, 36, 6.6, "ECG 数字滤波：BW = 175 Hz（0x26 = 0x00，未旁路）", C_DIG, E_DIG, fs=10.0)
box(60, 22.0, 36, 6.6, "DRDYB <- CH1 ECG，每 1.172 ms 一次（0x27 = 0x08）", C_DIG, E_DIG, fs=10.0)

arrow(54.9, 46.8, 59.4, 47.5)
arrow(54.9, 27.2, 59.4, 34.0)
arrow(78, 31.6, 78, 35.2)
arrow(78, 21.6, 78, 18.2)

# ── 下：共模 / RLD 环 ────────────────────────────────────────
ax.text(4, 8.4, "共模检测与右腿驱动（0x0A / 0x0C）", fontsize=11, fontweight="bold", color="#202124")
box(3, 0.8, 44, 6.6,
    "CMOUT = (IN1 + IN2 + IN3) / 3     0x0A = 0x07（三路等权）", C_RLD, E_RLD, fs=10.0)
box(52, 0.8, 44, 6.6,
    "RLD 放大器上电，SELRLD = 100 -> 输出经 IN4 引出     0x0C = 0x04", C_RLD, E_RLD, fs=10.0)
arrow(47.6, 4.1, 51.4, 4.1, color=E_RLD)
arrow(74, 7.4, 74, 11.6, color=E_RLD, ls="--")
ax.text(75.4, 9.4, "RLD 输出 -> IN4(RL)", fontsize=9.0, color=E_RLD, va="center")
ax.text(60, 14.4, "注意：0x17 = 0x05 的 PACE2RLDIN=1 可能旁路此环（待核实）", fontsize=9.0,
        color=E_RLD, va="center")

ax.text(50, 60.9,
        "ADS1293 3-leader 配置总览    regs_leaderIII（36 条）    LOD 全关（0x06=0x08）｜Wilson 全关（0x0D~0x10=0x00）",
        ha="center", fontsize=12.5, fontweight="bold", color="#202124")

fig.savefig("ads1293_3leader_map.png", dpi=125, bbox_inches="tight", facecolor="white")
print("[saved] ads1293_3leader_map.png")
