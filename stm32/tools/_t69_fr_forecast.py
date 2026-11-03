# -*- coding: utf-8 -*-
"""
_t69_fr_forecast.py —— T69 频率响应上板测试：现场对照预测表（零硬件、零源码改动）

背景
----
T67（09-20 22:57）上板验收：输入阻抗 / 线性(+300 mV 偏移) / 动态范围 三项 OK
⇒ 只剩「频率响应」与「低频脉冲响应」两项。今日（09-21）主线 = 频响。

猛哥令：「目前的固件就是 D3 档，就按你的方式」⇒ 本文所有预测按
**ECG_PROFILE_DIAG150_D3（profile 3）** 出，并额外给出 **notch OFF** 状态。

🛑 为什么要单独给 notch 状态
    判据（GB 9706.225-2021 表 201.107）要求频响测试**关陷波**（`201.12.4.105.3` + 227 附录 AA）。
    D3 的 notch 含 **100 Hz（Q15）** 一节 ⇒ 若忘关，**100 Hz 测试点会被直接打死**
    ⇒ 现场表现为「100 Hz 读数崩掉」= 假失败。
    本脚本把 ON / OFF 两态并列 ⇒ 现场测完 100 Hz 一对照就知道 notch 关没关。

模型（与 T41 同源，已交叉验证）
    DUT = 整机 = ADS1293 AFE（analog + 三级 5 阶 SINC 抽取）× 固件链（dc_block + [陷波] + BP）
    AFE：ADS1293.pdf §8.3.7，R1/R2/R3 = 4/5/6，fS = 102.4 kHz，AFE_RES=0x00
         |H| = Π_k |sin(R_k·x) / (R_k·sin x)|^5，自检 −3.02 dB @175 Hz（手册 Table 8/12）✓
    固件链：**系数从 `Filter/ecg_filter.c` 源码直读**（不用设计值、不用旧脚本硬编码）
         ⚠ 旧脚本 `_t41_freqresp.py` 的固件模型是 `butter(4, ...)`（LP4），
           那是 **T58 之前** 的结构 ⇒ 已过时，本脚本不复用其 BP。

🛑 采样域与折叠（现场必读）
    fs = 853.2423 Hz ⇒ Nyquist = **426.62 Hz**
    `raw853 on` ：803 域，可测到 ~426 Hz；>426.6 Hz 的点（含 **500 Hz**）折叠到 853.24−f
    `raw853 off`（500 域）：Nyquist = 250 Hz ⇒ **250~426 Hz 全部折叠**到 500−f
      （300→200、400→100、500→0）⇒ 若要测 250 Hz 以上的点，**必须 `raw853 on`**。
    ⇒ 本脚本对 >426.62 Hz 的点标 `⚠超Nyq`，并给出折叠落点。

用法
    python _t69_fr_forecast.py                    # 默认 D3，出表 + 图
    python _t69_fr_forecast.py --prof D3 --prof2 N2
输出
    _t69_out.txt ＋ T69_频响现场对照表_2026-09-21.png
"""
import argparse
import io
import os
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_C = os.path.join(HERE, "..", "Filter", "ecg_filter.c")
SRC_H = os.path.join(HERE, "..", "Filter", "ecg_filter_coeffs.h")

FS = 1e6 / 1172.0            # 853.2423208191125
F_NYQ = FS / 2.0             # 426.621
FSD = 102400.0               # SDM 时钟（AFE_RES=0x00）
R123 = (4, 5, 6)
DC_FC = 0.05

# 表 201.107 四行：(lo, hi, 下限%, 上限%)
BANDS = [(0.67, 40.0, 90.0, 110.0),
         (40.0, 100.0, 70.0, 110.0),
         (100.0, 150.0, 70.0, 110.0),
         (150.0, 500.0, 0.0, 110.0)]
# 现场测点（按行分组，含边界与决定性点）
PTS = [0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0,
       60.0, 80.0, 100.0, 120.0, 150.0,
       200.0, 300.0, 400.0, 500.0]
REF = 10.0
# 各行的现场输入幅度（mVpp）
Y_AMP = [(0.67, 40.0, 1.0), (40.0, 100.0, 0.5), (100.0, 150.0, 0.25), (150.0, 500.0, 0.5)]


# ------------------------------------------------------------------ 系数解析
def parse_profile(path, prof):
    """从 ecg_filter.c 取指定档 F64 系数表 -> sos。前瞻含 else，防最后一档抓到文件尾。"""
    s = io.open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r'#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)(.*?)(?=\n#(?:elif|else))'
                  % prof, s, re.S)
    if not m:
        raise RuntimeError("未找到档位 %s" % prof)
    body = m.group(1)
    m2 = re.search(r'ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{(.*?)\n\};', body, re.S)
    if not m2:
        raise RuntimeError("档位 %s 内未找到 F64 表" % prof)
    txt = re.sub(r'/\*.*?\*/', '', m2.group(1), flags=re.S)
    txt = re.sub(r'//[^\n]*', '', txt)
    sos = []
    for g in re.findall(r'\{([^{}]*)\}', txt):
        v = [float(x) for x in g.split(',') if x.strip()]
        if len(v) == 6:
            sos.append(v)
        elif len(v) == 5:
            sos.append([v[0], v[1], v[2], 1.0, v[3], v[4]])
    return np.asarray(sos, float)


def parse_counts(path, prof):
    """从 coeffs.h 取该档 SECTIONS/NOTCH/BP 三个宏。"""
    s = io.open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r'ECG_PROFILE_%s\)\n#define ECG_NUM_SECTIONS (\d+)\n'
                  r'#define ECG_NUM_NOTCH_SECTIONS (\d+)\n#define ECG_NUM_BP_SECTIONS (\d+)'
                  % prof, s, re.S)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


# ------------------------------------------------------------------ 传递函数
def hz_sos(sos, f, fs=FS):
    z = np.exp(-2j * np.pi * np.asarray(f, float) / fs)
    H = np.ones(z.shape, complex)
    for s in sos:
        H *= (s[0] + s[1] * z + s[2] * z ** 2) / (s[3] + s[4] * z + s[5] * z ** 2)
    return H


def h_dcblock(f, fc=DC_FC, fs=FS):
    w = np.exp(-2j * np.pi * np.asarray(f, float) / fs)
    r = 1 - 2 * np.pi * fc / fs
    return (1 - w ** -1) / (1 - r * w ** -1)


def h_afe(f):
    H = np.ones(np.atleast_1d(np.asarray(f, float)).shape, float)
    fin = FSD
    for R in R123:
        x = np.pi * np.asarray(f, float) / fin
        H *= np.abs(np.sin(R * x) / (R * np.sin(x))) ** 5
        fin /= R
    return H


def h_chain(sos, f, notch_on, nn, prof):
    """dc_block -> [notch 节] -> [BP 节]（notch on 时自第 0 节起，off 时跳过前 nn 节）。

    注意：N2 档 BP **4 节**（LP4@150×2 + HP4@0.05×2，T66 回退后），
          D3 档 BP **3 节**（LP2@250×1 + HP4@0.05×2，T58 后）。
    """
    H = h_dcblock(f)
    i0 = 0 if notch_on else nn
    H = H * hz_sos(sos[i0:], f)
    return H


def band_of(f):
    for lo, hi, dlo, dhi in BANDS:
        if lo <= f <= hi:
            return dlo, dhi
    return None, None


def amp_of(f):
    for lo, hi, a in Y_AMP:
        if lo <= f <= hi:
            return a
    return None


def alias_of(f):
    """>Nyquist 时的折叠落点（853 域）。"""
    if f <= F_NYQ:
        return None
    return abs(FS - f)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prof", default="DIAG150_D3")
    ap.add_argument("--prof2", default="IMP620_N2")
    ap.add_argument("--tag", default="T69_频响现场对照表_2026-09-21")
    a = ap.parse_args()

    out = []

    def P(s=""):
        print(s)
        out.append(s)

    P("=" * 96)
    P("T69 频响上板测试 · 现场对照预测表（GB 9706.225-2021 表201.107；参考 = 10 Hz，按行归一化）")
    P("=" * 96)
    P()

    # ---- [0] 结构自检
    P("[0] 结构自检（系数直读固件源码，非设计值）")
    P("    fs = %.4f Hz   Nyquist = %.2f Hz   AFE = ADS1293 三级5阶SINC(R=%d/%d/%d)"
      % (FS, F_NYQ, *R123))
    info = {}
    for prof in [a.prof, a.prof2]:
        sos = parse_profile(SRC_C, prof)
        cnt = parse_counts(SRC_H, prof)
        ns, nn, nb = cnt if cnt else (None, None, None)
        info[prof] = (sos, ns, nn, nb)
        ok = "✓" if (ns == len(sos)) else "✗"
        P("    %-14s 系数行数=%2d  宏 SECTIONS=%s NOTCH=%s BP=%s  %s"
          % (prof, len(sos), ns, nn, nb, ok))
        if nn is not None:
            nb_bp = len(sos) - nn
            P("    %-14s ⇒ BP 实读 %d 节（宏写 %d）%s"
              % ("", nb_bp, nb, "✓" if nb_bp == nb else "✗"))
    P()

    # ---- [1] AFE 自检
    P("[1] AFE 模型自检（应与手册 Table 8/12「ODR 853 sps / BW 175 Hz」吻合）")
    for f in (10.0, 40.0, 100.0, 150.0, 175.0, 200.0, 300.0, 400.0, 500.0):
        P("      %6.1f Hz : %+7.3f dB" % (f, 20 * np.log10(h_afe(np.array([f]))[0])))
    P()

    fp = np.array(PTS, float)

    # ---- [2] 逐档 × notch 两态
    for prof in [a.prof, a.prof2]:
        sos, ns, nn, nb = info[prof]
        for notch_on in (False, True):
            tag = "notch ON " if notch_on else "notch OFF"
            Hf = h_chain(sos, fp, notch_on, nn, prof)
            Ha = h_afe(fp)
            tot = np.abs(Hf * Ha)
            r = abs(h_chain(sos, np.array([REF]), notch_on, nn, prof)[0] * h_afe(np.array([REF]))[0])
            g = tot / r * 100.0
            fw = np.abs(Hf) / abs(h_chain(sos, np.array([REF]), notch_on, nn, prof)[0]) * 100.0

            P("-" * 96)
            P("[2] %s · %s   （%s）" % (prof, tag,
               "★现场实际状态（判据要求关陷波）" if not notch_on else "⚠仅用于现场自检：忘关陷波会长这样"))
            P("-" * 96)
            P("    f(Hz) | 输入mVpp | 固件%  | 整机%  | 判限        | 判决 | 采样域")
            for i, f in enumerate(fp):
                dlo, dhi = band_of(f)
                am = amp_of(f)
                al = alias_of(f)
                ok = "✓" if (dlo <= g[i] <= dhi) else "✗"
                dom = "⚠超Nyq→%.1fHz" % al if al else "853域可测"
                lim = "%3.0f~%3.0f%%" % (dlo, dhi)
                P("   %7.2f | %7.3f | %6.2f | %6.2f | %11s |  %s  | %s"
                  % (f, am, fw[i], g[i], lim, ok, dom))
            # 最差点
            worst = min(((g[i] - band_of(f)[0], f, g[i]) for i, f in enumerate(fp)
                         if band_of(f)[0] is not None))
            P("   ★ 最贴限点：%.2f Hz → %.2f%%（下限 %.0f%%，余量 %+.2f pp）"
              % (worst[1], worst[2], band_of(worst[1])[0], worst[0]))
            P()

    # ---- [3] 决定性点速查
    P("-" * 96)
    P("[3] 决定性点速查（表201.107 四行中，唯一贴限 = 100~150 Hz 行下限 70%）")
    P("-" * 96)
    sos, ns, nn, nb = info[a.prof]
    key = np.array([10.0, 100.0, 120.0, 150.0], float)
    Hk = h_chain(sos, key, False, nn, a.prof)
    Ha = h_afe(key)
    rk = abs(h_chain(sos, np.array([REF]), False, nn, a.prof)[0] * h_afe(np.array([REF]))[0])
    gk = np.abs(Hk * Ha) / rk * 100.0
    for i, f in enumerate(key):
        P("   %s %6.1f Hz  预测 %6.2f%%  判限 %s"
          % ("参考" if f == REF else "★决定性" if f >= 100 else "  ", f, gk[i],
             "—" if f < 100 else "≥70%"))
    P()
    P("   ⇒ 现场若要快速判「档位对不对」，只测 **100 / 120 / 150 Hz** 三点即可。")
    P("   ⇒ 若实测 150 Hz 落在 70~90% ⇒ 与预测一致（本档可用）；若落在 ~55% ⇒ 那是 BP@150 的档，烧错了。")
    P()

    # ---- [4] 现场记录表（空白）
    P("-" * 96)
    P("[4] 现场记录表（照填；输出 p-p 用 500 域列④ 或 853 域列⑤，全程同一列）")
    P("-" * 96)
    P("    频率 | 档 | notch | 输入mVpp | 输出p-p | ÷10Hz归一 | 判限 | 判定")
    for f in PTS:
        dlo, dhi = band_of(f)
        P("   %7.2f |  %s |  %s  |  %5.2f  |         |           | %3.0f~%3.0f |"
          % (f, a.prof[:3], "OFF", amp_of(f), dlo, dhi))
    P()

    # ---- 图
    gx = np.exp(np.linspace(np.log(0.67), np.log(520), 600))
    sosD, nsD, nnD, nbD = info[a.prof]
    sosN, nsN, nnN, nbN = info[a.prof2]
    rD = abs(h_chain(sosD, np.array([REF]), False, nnD, a.prof)[0] * h_afe(np.array([REF]))[0])
    rN = abs(h_chain(sosN, np.array([REF]), False, nnN, a.prof2)[0] * h_afe(np.array([REF]))[0])
    yD = np.abs(h_chain(sosD, gx, False, nnD, a.prof) * h_afe(gx)) / rD * 100
    yN = np.abs(h_chain(sosN, gx, False, nnN, a.prof2) * h_afe(gx)) / rN * 100
    yDon = np.abs(h_chain(sosD, gx, True, nnD, a.prof) * h_afe(gx)) / \
        abs(h_chain(sosD, np.array([REF]), True, nnD, a.prof)[0] * h_afe(np.array([REF]))[0]) * 100

    fig, ax = plt.subplots(figsize=(12.4, 5.8), dpi=130)
    for lo, hi, dlo, dhi in BANDS:
        ax.fill_between([lo, hi], dlo, dhi, color="#EAF3DE", zorder=1)
        ax.plot([lo, hi], [dhi, dhi], color="#639922", lw=1.1, zorder=2)
        ax.plot([lo, hi], [dlo, dlo], color="#639922", lw=1.1, zorder=2)
    ax.axvline(REF, color="#888780", ls=":", lw=1)
    ax.text(REF * 1.04, 4, "参考 10 Hz", fontsize=8, color="#666")
    ax.axvline(F_NYQ, color="#B23B3B", ls="--", lw=1.2, zorder=3)
    ax.text(F_NYQ * 0.965, 60, "Nyquist\n426.6 Hz\n(853 域上限)", fontsize=7.8, color="#B23B3B",
            ha="right", va="center")
    ax.plot(gx, yDon, "--", color="#C98A2B", lw=1.4,
            label="D3 / notch ON   [自检用: 100 Hz 被陷波打死 -> 假失败]")
    ax.plot(gx, yD, "-", color="#185FA5", lw=2.2,
            label="D3 / notch OFF  [今日现场状态, 应全绿]")
    ax.plot(gx, yN, "-", color="#A32D2D", lw=1.4,
            label="IMP620_N2 / notch OFF  [BP@150, 150 Hz 预期不合格]")
    for f in (40, 100, 150):
        ax.axvline(f, color="#D3D1C7", lw=0.8, zorder=0)
    ax.set_xscale("log")
    ax.set_xlim(0.67, 520)
    ax.set_ylim(0, 128)
    ax.set_xticks([0.67, 1, 2, 5, 10, 20, 40, 60, 100, 150, 200, 300, 400, 500])
    ax.set_xticklabels(["0.67", "1", "2", "5", "10", "20", "40", "60", "100", "150",
                        "200", "300", "400", "500"], fontsize=8.5)
    ax.set_xlabel("频率 (Hz)")
    ax.set_ylabel("相对 10 Hz 的归一化增益 (%)")
    ax.set_title("T69 频响现场对照：D3(蓝,今日) vs N2(红) · notch ON/OFF ｜ 绿带 = GB9706.225 表201.107 容差带",
                 fontsize=10.5)
    ax.grid(alpha=0.22, which="both")
    ax.legend(fontsize=8.6, loc="lower left")
    ax.annotate("决定性点 150 Hz\nD3 预测 %.1f%% (下限 70%%)"
                % yD[np.argmin(np.abs(gx - 150))],
                xy=(150, yD[np.argmin(np.abs(gx - 150))]),
                xytext=(166, 40), fontsize=8.4, color="#185FA5",
                arrowprops=dict(arrowstyle="->", color="#185FA5", lw=1.2))
    ax.annotate("N2 档 150 Hz\n预测 %.1f%% 不合格" % yN[np.argmin(np.abs(gx - 150))],
                xy=(150, yN[np.argmin(np.abs(gx - 150))]),
                xytext=(50, 44), fontsize=8.2, color="#A32D2D",
                arrowprops=dict(arrowstyle="->", color="#A32D2D", lw=1.1))
    fig.tight_layout()
    png = os.path.join(HERE, a.tag + ".png")
    fig.savefig(png)
    P("[图] %s" % png)

    with io.open(os.path.join(HERE, "_t69_out.txt"), "w", encoding="utf-8", newline="\n") as fp2:
        fp2.write("\n".join(out))


if __name__ == "__main__":
    main()
