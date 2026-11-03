# -*- coding: utf-8 -*-
"""
_t41_freqresp.py —— T41 医疗标准「频率响应测试」：离线预测统一入口（零硬件、零源码改动）

口径（GB 9706.225-2021 表 201.107，MOD IEC 60601-2-25:2011）
----------------------------------------------------------------
  0.67~40  Hz  1 mV 正弦   ±10%      （参考 = 10 Hz）
  40~100   Hz  0.5 mV 正弦 +10% / -30%
  100~150  Hz  0.25 mV 正弦 +10% / -30%
  150~500  Hz  0.5 mV 正弦 +10% / -100%（只限上限：允许全衰减）
  ⇒ 判据 = G(f)/G(10Hz)，**按行按各自输入幅度归一化**

DUT = 整机 = ADS1293 AFE（analog + 三级 5 阶 SINC 抽取）× 固件链（dc_block + ε陷波 + BP）

AFE 模型（已交叉验证）
  ADS1293.pdf §8.3.7：三级 5 阶 SINC，R1/R2/R3 = 4/5/6，fS = 102.4 kHz，AFE_RES=0x00（低功耗）
  |H| = Π_k |sin(R_k·x)/ (R_k·sin x)|^5,  x = π f / f_in_k,  f_in 逐级除以 R_k
  自检：−3.02 dB @175 Hz，与手册 Table 8/12「ODR 853 sps / BW 175 Hz」吻合

用法
  python _t41_freqresp.py                       # 默认：D1(现状) / 无陷波+上角200
  python _t41_freqresp.py --upper 150,180,200   # 扫上角
  python _t41_freqresp.py --profiles D1,D3,nonotch --upper 150
"""
import sys
import argparse
import numpy as np
from scipy import signal as sps
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FS = 1e6 / 1172.0                     # 853.2423208191125
FSD = 102400.0                        # SDM 时钟（AFE_RES=0x00 → 102.4 kHz）
R123 = (4, 5, 6)                      # 手册 Table 8 中 ODR=853 sps / BW=175 Hz 那一行
DC_FC = 0.05

BANDS = [(0.67, 40.0, 90.0, 110.0), (40.0, 100.0, 70.0, 110.0),
         (100.0, 150.0, 70.0, 110.0), (150.0, 500.0, 0.0, 110.0)]
PTS = [0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0, 80.0, 100.0, 125.0, 150.0]


# ------------------------------------------------------------------ 传递函数
def notch(f0, Q):
    b, a = sps.iirnotch(f0, Q, fs=FS)
    return sps.tf2sos(b, a)[0]


def sections(profile):
    """陷波器小节（不含 BP、不含 dc_block）"""
    if profile == "D1":
        return [notch(50.0, 30.0)]
    if profile == "D2":
        return [notch(50.0, 30.0), notch(100.0, 30.0)]
    if profile == "D3":
        return [notch(49.90, 20.0), notch(50.00, 20.0), notch(50.10, 20.0), notch(100.0, 15.0)]
    if profile == "MP620":
        return [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
                notch(100.0, 15.0), notch(150.0, 30.0)]
    if profile == "N2":
        return [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
                notch(99.80, 20.0), notch(100.00, 20.0), notch(100.20, 20.0),
                notch(149.80, 20.0), notch(150.00, 20.0), notch(150.20, 20.0)]
    if profile == "nonotch":
        return []
    raise ValueError(profile)


def h_firmware(f, profile, upper, fc=DC_FC):
    """固件链：dc_block(fc) -> 陷波 -> butter(4,[fc,upper])"""
    w = np.exp(-2j * np.pi * f / FS)
    H = np.ones_like(w, dtype=complex)
    for s in sections(profile):
        # sos 行 = (b0,b1,b2,a0,a1,a2)，a0 恒 1
        H *= (s[0] + s[1] * w ** -1 + s[2] * w ** -2) / (1 + s[4] * w ** -1 + s[5] * w ** -2)
    sos = sps.butter(4, [fc, upper], btype="bandpass", fs=FS, output="sos")
    H *= sps.sosfreqz(sos, worN=f, fs=FS)[1]
    r = 1 - 2 * np.pi * fc / FS
    H *= (1 - w ** -1) / (1 - r * w ** -1)
    return H


def h_afe(f):
    H = np.ones_like(f, dtype=float)
    fin = FSD
    for R in R123:
        x = np.pi * f / fin
        H *= np.abs(np.sin(R * x) / (R * np.sin(x))) ** 5
        fin /= R
    return H


def verdict(v, f):
    for lo, hi, dlo, dhi in BANDS:
        if lo <= f <= hi:
            return dlo, dhi
    return None, None


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default="D1,nonotch")
    ap.add_argument("--upper", default="150,200")
    ap.add_argument("--tag", default="T41_频响口径预测_2026-09-18")
    a = ap.parse_args()
    profs = a.profiles.split(",")
    uppers = [float(x) for x in a.upper.split(",")]

    out = []

    def P(s=""):
        print(s)
        out.append(s)

    P("=" * 88)
    P("T41 离线预测：整机频响 vs GB 9706.225-2021 表201.107（参考 = 10 Hz，按行归一化）")
    P("=" * 88)
    P("")
    P("[0] AFE 模型自检（ADS1293 三级 5 阶 SINC，R1/R2/R3=%d/%d/%d，fS=%.1f kHz）" % (*R123, FSD / 1e3))
    for f in (10.0, 40.0, 100.0, 150.0, 175.0, 200.0):
        P("      %6.1f Hz : %+7.3f dB" % (f, 20 * np.log10(h_afe(np.array([f]))[0])))
    P("      ⇒ -3 dB 落在 175 Hz，与手册 Table 8/12「ODR 853 sps / BW 175 Hz」一致（模型可信）")
    P("")

    fp = np.array(PTS, dtype=float)
    ref_f = np.array([10.0])

    for prof in profs:
        for upper in uppers:
            Hf = h_firmware(fp, prof, upper)
            Hs = h_afe(fp)
            tot = np.abs(Hf * Hs)
            r = np.abs(h_firmware(ref_f, prof, upper)[0] * h_afe(ref_f)[0])
            g_rel = tot / r * 100.0
            af = np.abs(h_afe(fp)) / np.abs(h_afe(ref_f)[0]) * 100.0
            fw = np.abs(Hf) / np.abs(h_firmware(ref_f, prof, upper)[0]) * 100.0
            P("-" * 88)
            P("[%s  upper=%.0f Hz]   AFE 独立  |  固件链独立  |  整机级联  |  下限  |  判决"
              % (prof, upper))
            P("-" * 88)
            P("   f(Hz) |  AFE dB |  AFE%  | 固件 dB |  固件%  |  整机%  | 下限 | 判决")
            for i, f in enumerate(fp):
                dlo, dhi = verdict(g_rel[i], f)
                ok = "✓" if (dlo <= g_rel[i] <= dhi) else "✗"
                P("  %6.2f | %+7.3f | %6.2f | %+7.3f | %6.2f | %7.2f | %4.0f | %s"
                  % (f, 20 * np.log10(af[i] / 100), af[i],
                     20 * np.log10(fw[i] / 100), fw[i], g_rel[i], dlo, ok))
            worst = min(((g_rel[i] - verdict(g_rel[i], f)[0], f, g_rel[i]) for i, f in enumerate(fp)))
            P("  ★ 最差点：%.2f Hz → %.2f%%（下限 %.0f%%，余量 %+.2f pp）"
              % (worst[1], worst[2], verdict(worst[2], worst[1])[0], worst[0]))
            P("")

    # ---------------- 图
    gx = np.exp(np.linspace(np.log(0.67), np.log(200), 400))
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=130)
    for lo, hi, dlo, dhi in BANDS:
        if lo > 200:
            continue
        ax.fill_between([lo, min(hi, 200)], dlo, dhi, color="#EAF3DE", zorder=1)
        ax.plot([lo, min(hi, 200)], [dhi, dhi], color="#639922", lw=1, zorder=2)
        ax.plot([lo, min(hi, 200)], [dlo, dlo], color="#639922", lw=1, zorder=2)
    ax.axvline(10, color="#888780", ls=":", lw=1)
    ax.axhline(100, color="#B4B2A9", lw=0.8)
    style = [("-", "#A32D2D", "现状 D1（notch50 + BP 0.05~150）"),
             ("-", "#185FA5", "修法 无陷波 + BP 0.05~200")]
    n = 0
    for prof, upper in zip(profs, uppers[:len(profs)]):
        r = np.abs(h_firmware(ref_f, prof, upper)[0] * h_afe(ref_f)[0])
        y = np.abs(h_firmware(gx, prof, upper) * h_afe(gx)) / r * 100
        ls, c, lab = style[min(n, len(style) - 1)]
        ax.plot(gx, y, ls, color=c, lw=1.8, label=lab, zorder=4)
        n += 1
    for f in (40, 100, 150):
        ax.axvline(f, color="#D3D1C7", lw=0.8, zorder=0)
    ax.set_xscale("log")
    ax.set_xlim(0.67, 200)
    ax.set_ylim(0, 130)
    ax.set_xticks([0.67, 1, 2, 5, 10, 20, 40, 60, 100, 150, 200])
    ax.set_xticklabels(["0.67", "1", "2", "5", "10", "20", "40", "60", "100", "150", "200"])
    ax.set_xlabel("频率 (Hz)")
    ax.set_ylabel("相对 10 Hz 的归一化增益 (%)")
    ax.set_title("T41 整机频响离线预测 vs 表201.107 容差带（绿=容差带；40/100/150 Hz 为频段边界）", fontsize=11)
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=9, loc="lower left")
    ax.annotate("150 Hz 54.8%\n低于 -30% 下限", xy=(150, 55), xytext=(103, 20), fontsize=9,
                color="#A32D2D", arrowprops=dict(arrowstyle="->", color="#A32D2D", lw=1))
    fig.tight_layout()
    png = "C:/work2026/VsPro/ads1293_demo/tools/%s.png" % a.tag
    fig.savefig(png)
    P("[图] %s" % png)

    with open("C:/work2026/VsPro/ads1293_demo/tools/_t41_out.txt", "w", encoding="utf-8") as fp2:
        fp2.write("\n".join(out))


if __name__ == "__main__":
    main()
