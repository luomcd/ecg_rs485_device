# -*- coding: utf-8 -*-
"""
_rv_figs.py —— 《全过程回顾》文档的补充配图（2026-09-17 建）

用途：早期会话（09-03~09-05）的图在 tools 清理时被删，本脚本用 Desktop 上的原始
      数据重建这些条目的关键图，配色与既有 T20~T30 系列一致（白底深字）。

产物（写入 tools/）：
  RV_A1_工频身份误判与翻案.png     A1  51.13Hz 假峰 -> 49.99Hz 真峰
  RV_A2_三态对照_AFE本底.png       A2  短接 / 无线 / 导线悬空
  RV_A3_滤波链演进.png             A3  2阶 -> 4阶 + 陷波组
  RV_A5_码制真相.png               A5  零点 6075000 = ADCMAX/2
  RV_A8_定标三修.png               A8  0.1653 / 0.0564 / 0.1122
"""
import io
import os
import re
import numpy as np
from scipy import signal as sps
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#ffffff"; TXT = "#1a1a1a"; GRID = "#c9ccd1"
C_RAW = "#8a8f98"; C_BAD = "#d1495b"; C_MID = "#c98a1e"; C_OK = "#1a7f4b"; C_BLUE = "#2b6cb0"

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = "C:/Users/luom/Desktop/ADS1293"

F_OLD = 1e6 / 1172.0            # 853.2423 Hz —— DRDY 实测周期
UV = 0.1122                     # µV/code（09-09 定稿）


# ─────────────────────────── 通用工具 ───────────────────────────

def style(ax, title=None, xl=None, yl=None):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=9)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.55)
    if title:
        ax.set_title(title, fontsize=10.5, color=TXT, pad=6)
    if xl:
        ax.set_xlabel(xl, fontsize=9.5, color=TXT)
    if yl:
        ax.set_ylabel(yl, fontsize=9.5, color=TXT)


def load_t_ecg(path):
    """09-03 格式：`t:01146 ecg:6070905`"""
    t, v = [], []
    for ln in io.open(path, encoding="utf-8", errors="ignore"):
        m = re.search(r"t:(\d+)\s+ecg:(-?\d+)", ln)
        if m:
            t.append(int(m.group(1))); v.append(int(m.group(2)))
    return np.array(t, float), np.array(v, float)


def load_seq2(path):
    """09-04/09-05 格式：每行 `序号 ecg` 两个整数（跳过寄存器 dump / 日志行）

    ⚠ 这些文件尾部常有被截断的残行（如 `60749`，丢了低 2 位），量级差 100×，
      不过滤会把 σ 抬到 4 万。这里按 ADS1293 码的物理区间 [6.0M, 6.2M] 过滤。
    """
    out = []
    for ln in io.open(path, encoding="utf-8", errors="ignore"):
        s = ln.strip()
        if not s or not s[0].isdigit():
            continue
        p = s.split()
        if len(p) != 2:
            continue
        try:
            idx, val = int(p[0]), int(p[1])
        except Exception:
            continue
        if 6_000_000 <= val <= 6_200_000:
            out.append((idx, val))
    a = np.array(out, float)
    return a[:, 0], a[:, 1]


def psd(y, fs, nper=8192, ref=True):
    f, P = sps.welch(y, fs=fs, nperseg=min(nper, len(y)), detrend="linear")
    d = 10 * np.log10(P + 1e-30)
    return f, d


def band_rms_rect(y, fs, f1, f2):
    """矩形窗带内 rms（回归基准，M21：判裙边才必须加窗）"""
    n = len(y)
    Y = np.fft.rfft(y)
    f = np.fft.rfftfreq(n, 1 / fs)
    m = (f >= f1) & (f <= f2)
    return float(np.sqrt(2 * np.sum(np.abs(Y[m]) ** 2)) / n)


def notch_seq(y, spec, fs):
    z = y
    for f0, Q in spec:
        b, a = sps.iirnotch(f0, Q, fs=fs)
        z = sps.lfilter(b, a, z)
    return z


def bp(y, f_lo, f_hi, order, fs, causal=True):
    sos = sps.butter(order, [f_lo, f_hi], btype="bandpass", fs=fs, output="sos")
    return sps.sosfilt(sos, y) if causal else sps.sosfiltfilt(sos, y)


def save(f, name):
    p = os.path.join(HERE, name)
    f.tight_layout()
    f.savefig(p, dpi=132, facecolor=BG)
    plt.close(f)
    print("  [ok] %s" % name)


# ─────────────────────────── A1 ───────────────────────────

def _notch_gain(y, fs, center, band=(48.5, 52.5), Q=30.0):
    """在固定带 band 内，陷波 center 能拿到多少 dB 衰减（矩形窗带内 rms，回归基准）"""
    b0 = band_rms_rect(y, fs, *band)
    z = sps.lfilter(*sps.iirnotch(center, Q, fs=fs), y)
    return 20 * np.log10(b0 / band_rms_rect(z, fs, *band)), float(np.std(z))


def fig_a1():
    print("A1 工频身份误判与翻案 ...")
    t, v = load_t_ecg(os.path.join(DESK, "Serial Debug 2026-09-03 182858.txt"))
    # ⚠ 09-03 的 `t:` 字段不是"累加时间"，是**每次测得的 DRDY 周期**（1146/1147 µs
    #    抖动）。当时误把它当周期用 -> fs = 1e6/1146 = 872.6 Hz（错）。
    #    真实 DRDY 周期是 1172 µs -> fs = 853.2423 Hz。
    fs_bad = 1e6 / float(np.median(t))
    y = v - np.median(v)

    f, ax = plt.subplots(2, 2, figsize=(13.4, 7.6), facecolor=BG)

    # (a) 双时基 PSD 叠加 —— 同一根谱线，两个坐标
    for fs, c, lab in ((fs_bad, C_BAD, "错时基  (t:字段 1146µs)  fs=%.1f Hz" % fs_bad),
                       (F_OLD, C_OK, "正确时基 (DRDY 1172µs)  fs=%.2f Hz" % F_OLD)):
        fq, d = psd(y, fs, 8192)
        m = (fq >= 44.0) & (fq <= 62.0)
        ax[0, 0].plot(fq[m], d[m], color=c, lw=1.1, label=lab)
    ax[0, 0].axvline(51.13, color=C_BAD, ls=":", lw=1.4)
    ax[0, 0].axvline(49.99, color=C_OK, ls=":", lw=1.4)
    ax[0, 0].annotate("51.13\n(+2.26%)", xy=(51.13, 60), xytext=(55.0, 76),
                      fontsize=9.0, color=C_BAD, weight="bold",
                      arrowprops=dict(arrowstyle="->", color=C_BAD, lw=1.2))
    ax[0, 0].annotate("49.99\n(工频)", xy=(49.99, 60), xytext=(44.6, 76),
                      fontsize=9.0, color=C_OK, weight="bold",
                      arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.2))
    style(ax[0, 0], "(a) 主峰特写：同一根谱线，坐标不同 → 峰位差 1.14 Hz",
          "频率 (Hz)", "PSD (dB)")
    ax[0, 0].set_xlim(44.0, 62.0)
    ax[0, 0].legend(fontsize=8.6, facecolor=BG, edgecolor=GRID, labelcolor=TXT,
                    loc="lower right")

    # (b) 谐波区放大
    for fs, c in ((fs_bad, C_BAD), (F_OLD, C_OK)):
        fq, d = psd(y, fs, 8192)
        m = (fq >= 90) & (fq <= 170)
        ax[0, 1].plot(fq[m], d[m], color=c, lw=1.2)
    for k, col in ((100.0, C_OK), (150.0, C_OK)):
        ax[0, 1].axvline(k, color=col, ls=":", lw=1.2)
    for k in (102.26, 153.39):
        ax[0, 1].axvline(k, color=C_BAD, ls=":", lw=1.2)
    ax[0, 1].text(100.0, ax[0, 1].get_ylim()[1] * 0.97, "100 / 150\n(工频谐波)",
                  ha="center", va="top", fontsize=8.8, color=C_OK)
    ax[0, 1].text(158.0, ax[0, 1].get_ylim()[1] * 0.97, "102.26 / 153.39\n(错时基下的\"谐波\")",
                  ha="right", va="top", fontsize=8.8, color=C_BAD)
    style(ax[0, 1], "(b) 谐波链同样整体缩放 1.0227× → 严格 1:2:3 从没变过",
          "频率 (Hz)", "PSD (dB)")

    # (c) 错时基下的"证据"—— 凶手是自洽的
    gb50, sb50 = _notch_gain(y, fs_bad, 50.00)
    gb51, sb51 = _notch_gain(y, fs_bad, 51.13)
    xs = np.arange(2)
    ax[1, 0].bar(xs, [gb50, gb51], width=0.5, color=[C_BAD, C_OK])
    for x, val in zip(xs, (gb50, gb51)):
        ax[1, 0].text(x, val + 0.7, "%.1f dB" % val, ha="center",
                      fontsize=10.6, color=TXT, weight="bold")
    ax[1, 0].set_xticks(xs)
    ax[1, 0].set_xticklabels(["陷波中心 50.00 Hz\n(设计值)", "陷波中心 51.13 Hz\n(迁就假峰)"],
                             fontsize=9.2, color=TXT)
    ax[1, 0].set_ylim(0, max(gb50, gb51) * 1.3)
    style(ax[1, 0], "(c) 【陷阱】按错时基看：只有 51.13 有效 →\"电网跑偏\"看起来被证实",
          None, "48.5–52.5Hz 带内衰减 (dB)")

    # (d) 换成正确时基 —— 结论反转
    ga50, sa50 = _notch_gain(y, F_OLD, 50.00)
    ga51, sa51 = _notch_gain(y, F_OLD, 51.13)
    ax[1, 1].bar(xs, [ga50, ga51], width=0.5, color=[C_OK, C_BAD])
    for x, val in zip(xs, (ga50, ga51)):
        ax[1, 1].text(x, val + 0.7, "%.1f dB" % val, ha="center",
                      fontsize=10.6, color=TXT, weight="bold")
    ax[1, 1].set_xticks(xs)
    ax[1, 1].set_xticklabels(["陷波中心 50.00 Hz\n(设计值)", "陷波中心 51.13 Hz\n(迁就假峰)"],
                             fontsize=9.2, color=TXT)
    ax[1, 1].set_ylim(0, max(ga50, ga51) * 1.3)
    style(ax[1, 1], "(d) 【翻案】同一条链、正确时基：50.00 有效，51.13 作废 —— 是坐标错，不是电网错",
          None, "48.5–52.5Hz 带内衰减 (dB)")

    save(f, "RV_A1_工频身份误判与翻案.png")
    return dict(fs_bad=fs_bad, bad=[gb50, gb51], good=[ga50, ga51],
                std_bad=[sb50, sb51], std_good=[sa50, sa51], std0=float(np.std(y)))


# ─────────────────────────── A2 ───────────────────────────

def fig_a2():
    print("A2 三态对照 ...")
    _, v1 = load_seq2(os.path.join(DESK, "Serial Debug 2026-09-04 150848.txt"))   # 短接
    _, v2 = load_seq2(os.path.join(DESK, "Serial Debug 2026-09-04 181157.txt"))   # 3-leader 无线
    _, v3 = load_seq2(os.path.join(DESK, "Serial Debug 2026-09-05 113257.txt"))   # 导线悬空
    N = 5 * int(round(F_OLD))
    segs = [("短接 IN1=IN2\n(TI Shorted)", v1, C_OK),
            ("3-leader 无导联线\n(RLD+LOD 钳位)", v2, C_BLUE),
            ("导联线悬空\n(1m 天线)", v3, C_BAD)]
    st = [(nm, v[int(N):] - np.median(v[int(N):]), c) for nm, v, c in segs]

    f, ax = plt.subplots(1, 3, figsize=(14.4, 4.3), facecolor=BG)

    # (a) 时域 3s
    n3 = int(3 * F_OLD)
    for i, (nm, y, c) in enumerate(st):
        ax[0].plot(np.arange(n3) / F_OLD, y[:n3] + i * 9000, color=c, lw=0.7)
        ax[0].text(0.15, y[:n3].max() + i * 9000 + 1200, nm.replace("\n", " "),
                   fontsize=8.6, color=c)
    style(ax[0], "(a) 稳态段 3 s（已去中值；纵向错开）", "时间 (s)", "codes")
    ax[0].set_yticks([])

    # (b) PSD
    for nm, y, c in st:
        fq, d = psd(y, F_OLD, 4096)
        m = fq <= 300
        ax[1].plot(fq[m], d[m], color=c, lw=0.9,
                   label="%s  σ=%.1f" % (nm.split("\n")[0], np.std(y)))
    ax[1].axvline(50.0, color=GRID, ls="--", lw=1.0)
    style(ax[1], "(b) PSD：50Hz 从 -0.7dB 飙到 +73dB", "频率 (Hz)", "PSD (dB)")
    ax[1].legend(fontsize=8.2, facecolor=BG, edgecolor=GRID, labelcolor=TXT)
    ax[1].set_xlim(0, 200)

    # (c) σ 阶梯
    sig = [np.std(y) for _, y, _ in st]
    bars = ax[2].bar(np.arange(3), sig, width=0.52, color=[c for _, _, c in st])
    ax[2].set_yscale("log")
    for x, val in zip(np.arange(3), sig):
        ax[2].text(x, val * 1.22, "%.1f" % val, ha="center", fontsize=10.2,
                   color=TXT, weight="bold")
    ax[2].set_xticks(np.arange(3))
    ax[2].set_xticklabels([nm for nm, _, _ in st], fontsize=8.6, color=TXT)
    ax[2].set_ylim(10, max(sig) * 3.2)
    style(ax[2], "(c) 稳态 σ：AFE 本底 21.8 vs 天线 3571（170×）",
          None, "σ (codes, log)")
    ax[2].text(0.5, 0.90, "「无导联线」≈「短接」\n差 0.7 codes",
               transform=ax[2].transAxes, ha="center", fontsize=9.2, color=C_OK)

    save(f, "RV_A2_三态对照_AFE本底.png")
    return dict(sigma=[float(np.std(y)) for _, y, _ in st])


# ─────────────────────────── A3 ───────────────────────────

def fig_a3():
    print("A3 滤波链演进 ...")
    _, v = load_seq2(os.path.join(DESK, "Serial Debug 2026-09-05 113257.txt"))
    # ⚠ 口径：必须"先全段滤波、再截取稳态段"。若先截取再滤波，滤波器会从截取点以
    #    零状态起步，把启动瞬态丢进稳态段——去 DC 用 median 还是 mean 都能让结果差
    #    1.7×（123 vs 72.7 codes）。这里统一：全段滤波后取 7 s 之后。
    y_full = v - np.median(v)
    K0 = int(7 * F_OLD)
    y = y_full[K0:]

    cands = [
        ("v1  2阶BP\n0.5-40", lambda z: bp(z, 0.5, 40, 2, F_OLD), C_RAW),
        ("+notch50\n(2阶BP)", lambda z: bp(notch_seq(z, [(50.0, 30.0)], F_OLD), 0.5, 40, 2, F_OLD), C_BAD),
        ("notch50/100/150\n4阶BP", lambda z: bp(notch_seq(z, [(50.0, 30.0), (100.0, 30.0), (150.0, 30.0)], F_OLD),
                                               0.5, 40, 4, F_OLD), C_MID),
        ("notch50/100/150\n6阶BP", lambda z: bp(notch_seq(z, [(50.0, 30.0), (100.0, 30.0), (150.0, 30.0)], F_OLD),
                                               0.5, 40, 6, F_OLD), C_OK),
    ]
    sig = [float(np.std(fn(y_full)[K0:])) for _, fn, _ in cands]

    f, ax = plt.subplots(1, 2, figsize=(13.2, 4.3), facecolor=BG)
    bars = ax[0].bar(np.arange(len(cands)), sig, width=0.52,
                     color=[c for _, _, c in cands])
    for x, val in zip(np.arange(len(cands)), sig):
        ax[0].text(x, val * 1.12 + 6, "%.1f" % val, ha="center", fontsize=10.5,
                   color=TXT, weight="bold")
    ax[0].set_xticks(np.arange(len(cands)))
    ax[0].set_xticklabels([nm for nm, _, _ in cands], fontsize=8.8, color=TXT)
    ax[0].set_ylim(0, max(sig) * 1.25)
    style(ax[0], "(a) 导线悬空数据上的残余 σ（因果 lfilter，稳态段）",
          None, "σ (codes)")
    ax[0].annotate("", xy=(1, sig[1]), xytext=(0, sig[0]),
                   arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.4))
    ax[0].text(0.5, (sig[0] + sig[1]) / 2 + 90, "陷波组\n把谐波拿走", ha="center",
               fontsize=8.8, color=C_OK)

    for nm, fn, c in cands:
        z = fn(y_full)[K0:]
        fq, d = psd(z, F_OLD, 4096)
        m = fq <= 200
        ax[1].plot(fq[m], d[m], color=c, lw=0.9, label=nm.replace("\n", " "))
    ax[1].axvline(50.0, color=GRID, ls="--", lw=1.0)
    ax[1].axvline(150.0, color=GRID, ls="--", lw=1.0)
    style(ax[1], "(b) 谐波拒止：2 阶带通低通边在 100Hz 只有 -20dB",
          "频率 (Hz)", "PSD (dB)")
    ax[1].legend(fontsize=8.2, facecolor=BG, edgecolor=GRID, labelcolor=TXT)
    ax[1].set_xlim(0, 200)

    save(f, "RV_A3_滤波链演进.png")
    return dict(sigma=sig)


# ─────────────────────────── A5 ───────────────────────────

def fig_a5():
    print("A5 码制真相 ...")
    _, v = load_seq2(os.path.join(DESK, "Serial Debug 2026-09-04 150848.txt"))
    y = v[int(5 * F_OLD):]
    ADCMAX = 12150000.0
    med = float(np.median(y))
    mean = float(np.mean(y))

    f, ax = plt.subplots(1, 2, figsize=(13.2, 4.3), facecolor=BG)

    # (a) 分布
    ax[0].hist(y - 6075000, bins=90, color=C_BLUE, alpha=0.85)
    ax[0].axvline(med - 6075000, color=C_OK, lw=1.8,
                  label="实测中位 %.0f  (= 理论 −25)" % med)
    ax[0].axvline(0, color=C_BAD, ls="--", lw=1.5,
                  label="理论 0V 差分码 6 075 000")
    style(ax[0], "(a) 短接输入 DC 分布：0 差分 ≠ 0 code", "相对 6 075 000 的偏差 (codes)",
          "样本数")
    ax[0].legend(fontsize=8.6, facecolor=BG, edgecolor=GRID, labelcolor=TXT)
    ax[0].text(0.02, 0.94, "手册 §8.4.3:\nADCOUT = ADCMAX/2 @ 0V 差分",
               transform=ax[0].transAxes, va="top", fontsize=9.0, color=TXT)

    # (b) 量程
    ax[1].barh([0], [ADCMAX], height=0.42, color="#e8eaee", edgecolor=GRID)
    ax[1].axvline(ADCMAX / 2, color=C_OK, lw=2.2)
    ax[1].text(ADCMAX / 2, 0.34, "6 075 000\n(= ADCMAX/2)", ha="center",
               fontsize=9.6, color=C_OK, weight="bold")
    ax[1].axvline(med, color=C_BAD, lw=1.4, ls=":")
    ax[1].text(ADCMAX, -0.36, "ADCMAX = 0xB964F0 = 12 150 000   (R1,R2,R3)=(4,5,6), ODR 853Hz",
               ha="right", fontsize=8.8, color=TXT)
    ax[1].text(med - ADCMAX * 0.02, 0.34, "实测 %.0f" % med, ha="right",
               fontsize=9.0, color=C_BAD)
    ax[1].set_xlim(-ADCMAX * 0.06, ADCMAX * 1.06)
    ax[1].set_yticks([])
    style(ax[1], "(b) 24-bit 码轴：中心不是 2²³，是 ADCMAX/2", "ADC 码值", None)
    ax[1].set_ylim(-0.75, 0.75)

    save(f, "RV_A5_码制真相.png")
    return dict(median=med, mean=mean, adcmax=ADCMAX)


# ─────────────────────────── A8 ───────────────────────────

def fig_a8():
    print("A8 定标三修 ...")
    RS_CODES = 8867.0            # 092214 实测 R-S p-p（09-09 日记）
    R_CODES = 7845.0
    CAND = [("0.1653\n(错：G=6 假设)", 0.1653, C_BAD),
            ("0.0564\n(手册推导)", 0.0564, C_MID),
            ("0.1122\n(AECG100 回代)", 0.1122, C_OK)]

    f, ax = plt.subplots(1, 2, figsize=(13.2, 4.3), facecolor=BG)

    vals = [RS_CODES * u for _, u, _ in CAND]
    bars = ax[0].bar(np.arange(3), vals, width=0.52, color=[c for _, _, c in CAND])
    ax[0].axhline(1000.0, color=C_BLUE, lw=2.0, ls="--")
    ax[0].text(2.42, 1040, "AECG100 标称 p-p 1.0 mV", ha="right", fontsize=9.4,
               color=C_BLUE, weight="bold")
    for x, val in zip(np.arange(3), vals):
        ax[0].text(x, val + 45, "%.0f µV" % val, ha="center", fontsize=10.2,
                   color=TXT, weight="bold")
    ax[0].set_xticks(np.arange(3))
    ax[0].set_xticklabels([nm for nm, _, _ in CAND], fontsize=8.8, color=TXT)
    ax[0].set_ylim(0, max(vals) * 1.24)
    style(ax[0], "(a) 同一 raw（R-S = 8867 codes）在三种定标下的「mV」", None, "折算幅度 (µV)")

    uv = [u for _, u, _ in CAND]
    err = [abs(RS_CODES * u - 1000.0) / 1000.0 * 100 for u in uv]
    bars = ax[1].bar(np.arange(3), err, width=0.52, color=[c for _, _, c in CAND])
    for x, val in zip(np.arange(3), err):
        ax[1].text(x, val + 1.6, "%.1f%%" % val, ha="center", fontsize=10.5,
                   color=TXT, weight="bold")
    ax[1].set_xticks(np.arange(3))
    ax[1].set_xticklabels([nm for nm, _, _ in CAND], fontsize=8.8, color=TXT)
    ax[1].set_ylim(0, max(err) * 1.25)
    style(ax[1], "(b) 与标称 1.0 mV 的偏差：0.1122 落在 0.5%", None, "偏差 (%)")
    ax[1].text(0.5, 0.88, "0.1122 定稿 ——\n写进 ecg_out.h:56 ECG_UV_PER_CODE",
               transform=ax[1].transAxes, ha="center", fontsize=9.2, color=C_OK)

    save(f, "RV_A8_定标三修.png")
    return dict(uv=uv, err=err, vals=vals)


# ─────────────────────────── B1 ───────────────────────────

def fig_b1():
    """双喂链：链函数被调两次 -> 等效采样率翻倍 -> 陷波整体挪位"""
    print("B1 双喂链 ...")
    rows = []
    for ln in io.open(os.path.join(HERE, "1mV-ECG-2026-09-10 143928.txt"),
                      encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) < 2:
            continue
        try:
            rows.append(float(p[1]))
        except Exception:
            pass
    v = np.array(rows)
    y = v - np.median(v)
    K0 = int(3 * F_OLD)
    y = y[K0:]
    t = np.arange(len(y)) / F_OLD
    INJ = 300.0 / UV                      # 注入 50Hz 300µV（code 域）
    y = y + INJ * np.sin(2 * np.pi * 50.0 * t)

    spec = [(50.0, 30.0), (100.0, 30.0), (150.0, 30.0)]

    def chain(z):
        return bp(notch_seq(z, spec, F_OLD), 0.05, 150, 4, F_OLD)

    a_ok = chain(y) * UV
    a_dbl = chain(np.repeat(y, 2)) * UV   # 链看到 [x0,x0,x1,x1,...]

    def resid(z, fs):
        n = len(z)
        w = np.hanning(n)
        Y = np.fft.rfft(z * w)
        fq = np.fft.rfftfreq(n, 1 / fs)
        m = (fq >= 49.5) & (fq <= 50.5)
        return float(np.sqrt(2 * np.sum(np.abs(Y[m]) ** 2)) / np.sum(w))

    # ⚠ 双喂后输出序列的采样率是 2×F_OLD（样本被重复），频谱必须用 2×fs 轴，
    #    否则会把「50Hz 没被压掉」误算成「压得很好」。
    r_ok, r_dbl = resid(a_ok, F_OLD), resid(a_dbl, 2 * F_OLD)

    f, ax = plt.subplots(1, 2, figsize=(13.2, 4.3), facecolor=BG)

    for z, fs_, c, lab in ((a_ok, F_OLD, C_OK, "正确单喂  50Hz 残留 %.1f µV" % r_ok),
                           (a_dbl, 2 * F_OLD, C_BAD,
                            "双喂(链调两次)  50Hz 残留 %.1f µV" % r_dbl)):
        fq, d = psd(z, fs_, 4096)
        m = (fq >= 30) & (fq <= 220)
        ax[0].plot(fq[m], d[m], color=c, lw=1.0, label=lab)
    ax[0].axvline(50.0, color=GRID, ls=":", lw=1.2)
    style(ax[0], "(a) 同一段信号（注入 50Hz 300µV）：正确链 vs 双喂链",
          "频率 (Hz)", "PSD (dB)")
    ax[0].legend(fontsize=8.6, facecolor=BG, edgecolor=GRID, labelcolor=TXT)

    xs = np.arange(2)
    ax[1].bar(xs, [r_ok, r_dbl], width=0.5, color=[C_OK, C_BAD])
    for x, val in zip(xs, (r_ok, r_dbl)):
        ax[1].text(x, val * 1.05 + 1, "%.1f µV" % val, ha="center",
                   fontsize=10.6, color=TXT, weight="bold")
    ax[1].set_xticks(xs)
    ax[1].set_xticklabels(["正确单喂\n(每个样本喂链一次)", "双喂\n(restore 后再调一次 filter)"],
                          fontsize=9.2, color=TXT)
    ax[1].set_ylim(0, max(r_ok, r_dbl) * 1.35)
    style(ax[1], "(b) 50 Hz 残留：×%.1f —— 陷波被整体挪走了" % (r_dbl / max(r_ok, 1e-9)),
          None, "49.5–50.5Hz 残留 (µV)")
    ax[1].text(0.5, 0.86, "链看到 [x0,x0,x1,x1,…] →\n等效 fs 853 → 1706 Hz\nnotch 50/100 → 100/200 Hz",
               transform=ax[1].transAxes, ha="center", fontsize=9.0, color=C_BAD)

    save(f, "RV_B1_双喂链.png")
    return dict(r_ok=r_ok, r_dbl=r_dbl)


if __name__ == "__main__":
    print("=" * 74)
    r = {}
    r["A1"] = fig_a1()
    r["A2"] = fig_a2()
    r["A3"] = fig_a3()
    r["A5"] = fig_a5()
    r["A8"] = fig_a8()
    r["B1"] = fig_b1()
    print("=" * 74)
    print("A1  错时基 fs=%.1f Hz  raw std=%.0f codes" % (r["A1"]["fs_bad"], r["A1"]["std0"]))
    print("     错时基下陷波 [50.00, 51.13] = %s dB  →  std %s"
          % (" / ".join("%.1f" % g for g in r["A1"]["bad"]),
             " / ".join("%.0f" % s for s in r["A1"]["std_bad"])))
    print("     正确时基下陷波 [50.00, 51.13] = %s dB  →  std %s"
          % (" / ".join("%.1f" % g for g in r["A1"]["good"]),
             " / ".join("%.0f" % s for s in r["A1"]["std_good"])))
    print("A2  三态 σ = %s codes" % " / ".join("%.1f" % s for s in r["A2"]["sigma"]))
    print("A3  四链 σ = %s codes" % " / ".join("%.1f" % s for s in r["A3"]["sigma"]))
    print("A5  短接中位 %.0f  (理论 6075000, 差 %+.0f)"
          % (r["A5"]["median"], r["A5"]["median"] - 6075000))
    print("A8  三定标折算 %s µV | 偏差 %s"
          % (" / ".join("%.0f" % v for v in r["A8"]["vals"]),
             " / ".join("%.1f%%" % e for e in r["A8"]["err"])))
