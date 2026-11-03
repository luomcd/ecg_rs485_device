# -*- coding: utf-8 -*-
"""
_t53_freqplan.py —— T53：低频脉冲判据 × 频响表 的「联合」修法规划

背景
----
  ① T46（09-20 10:09）只算了【低端降阶保位移/斜率】+【频响抬上角】；
  ② T51/T52（14:37 后）才发现 YY 0885 2.1.5 有第 3 条【过冲<10%】，而它由【高端 LP 阶数】定。
  ⇒ 两个需求第一次撞在一起：降 LP 阶保过冲，会吃掉 150 Hz 频响余量。本脚本把账合起来算。

模型（全程 853.2423 Hz 域，T52d 已证该域仿真可复现实测 240.2 µV）
----
  脉冲：3 mV/100 ms → L=3 预平滑（AFE CIC + 重采样等效，T52 标定）
        → 低端（dc_block ×n + HP 节）→ 高端（LP）
  频响：H_total(f) = H_AFE(f) × H_dc(f) × H_HP(f) × H_LP(f)     （关陷波态）
        H_AFE 复用 _t41_freqresp.h_afe（三级 sinc^5 CIC，已交叉验证）

用法
----
  python _t53_freqplan.py            # 全套
  python _t53_freqplan.py --fig      # 加出图
"""
import os
import sys
import argparse
import numpy as np
from scipy import signal as sps
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _t41_freqresp as T41
import _t52_yy0885_notchoff as T52

FS = float(T41.FS)          # 853.2423
DC_FC = 0.05
A_IN = 3000.0               # µV
PW = 0.100                  # s
L_AFE = 3                   # 853 域预平滑样本数（T52 标定：等效 3.52 ms）
LIM_DISP = 100.0            # µV
LIM_SLOPE = 300.0           # µV/s
LIM_OS = 10.0               # %
LIM_LOW = {"0.67-40": 90.0, "40-100": 70.0, "100-150": 70.0}

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def rule(ch="=", n=78):
    say(ch * n)


# ---------------------------------------------------------------- 固件现状链
def fw_sections():
    sos5, nn, hp_idx, kinds = T52.fw_chain("IMP620_N2")
    out = []
    for s in sos5:
        s = np.asarray(s, dtype=float).ravel()
        out.append(np.insert(s, 3, 1.0) if s.size == 5 else s)
    return out, int(nn), list(hp_idx), list(kinds)


SOS_FW, NN, HPIDX, KINDS = fw_sections()
LPIDX = [i for i in range(len(SOS_FW)) if i not in HPIDX]
# IMP620_N2: 0~8 notch, 9~10 LP, 11~12 HP（T52d 已确认）
if len(SOS_FW) == 13:
    LPIDX, HPIDX = [9, 10], [11, 12]


def presmooth(x, L):
    return x if L <= 1 else np.convolve(x, np.ones(L) / L, mode="same")


def mk_lp(kind, order, fc):
    fn = sps.butter if kind == "but" else sps.bessel
    return fn(order, fc / (FS / 2), output="sos")


# ---------------------------------------------------------------- 脉冲仿真
CAL = {"L": L_AFE, "g": 1.0}     # 边沿圆化阶数 / 链路增益（由 [1b] 标定）


def pulse_sim(low, lp, interval=10.0, n_pulse=3, lead=6.0, L=None, gain=None):
    L = CAL["L"] if L is None else L
    gain = CAL["g"] if gain is None else gain
    N = int((lead + interval * (n_pulse - 1) + 8.0) * FS)
    t = np.arange(N) / FS
    x = np.zeros(N)
    marks = []
    for j in range(n_pulse):
        i0 = int(round((lead + j * interval) * FS))
        i1 = i0 + int(round(PW * FS))
        x[i0:i1] = A_IN
        marks.append((i0, i1))
    y = presmooth(x, L) * gain
    if low[0] == "fw":
        y = T52.dc_block_apply(y, DC_FC, FS)
        for i in HPIDX:
            y = sps.sosfilt(SOS_FW[i], y)
    elif low[0] == "dc":
        for _ in range(low[1]):
            y = T52.dc_block_apply(y, DC_FC, FS)
    elif low[0] == "hp":
        y = T52.dc_block_apply(y, DC_FC, FS)
        y = sps.sosfilt(sps.butter(low[1], DC_FC / (FS / 2), btype="high",
                                   output="sos"), y)
    if lp:
        y = sps.sosfilt(mk_lp(*lp), y)
    return t, y, marks


def metrics(y, i0, i1):
    n = len(y)
    b = float(np.median(y[max(0, i0 - int(0.28 * FS)):i0 - int(0.03 * FS)]))
    yo = y - b

    def at(dt):
        k = i1 + int(round(dt * FS))
        return float(yo[k]) if k < n else np.nan

    disp = {d: at(d) for d in (0.02, 0.20, 0.50, 0.70, 1.00)}
    tg = np.arange(0.0, 0.100, 1.0 / (FS * 20))
    st = (np.arange(i0, i1) - i0) / FS
    pk = float(np.interp(tg, st, y[i0:i1]).max())
    near = float(np.median(y[i0 + int(0.030 * FS):i0 + int(0.050 * FS)]))
    far = float(np.median(y[i1 - int(0.020 * FS):i1]))


    def slwin(w, tstart=0.020):
        """滑窗最陡斜率。tstart=0.02 s 避开降沿振铃（LP4 振铃周期 6.7 ms）。"""
        k1 = i1 + int(tstart * FS)
        k2 = min(i1 + int(0.700 * FS), n - 1)
        m = int(round(w * FS))
        best = 0.0
        for k in range(k1, k2 - m):
            s = (y[k + m] - y[k]) / (m / FS)
            if abs(s) > abs(best):
                best = s
        return best


    def slfit(t1, t2):
        a = i1 + int(t1 * FS)
        b2 = min(i1 + int(t2 * FS), n - 1)
        return float(np.polyfit(np.arange(a, b2) / FS, y[a:b2], 1)[0])

    return dict(base=b, disp=disp, peak=pk, near=near, far=far,
                droop=near - far, sw100=slwin(0.100), sf=slfit(0.2, 1.2))


# ---------------------------------------------------------------- 频响
def h_fw(f, low, lp):
    w = np.exp(-2j * np.pi * np.asarray(f, float) / FS)
    H = np.ones_like(w, dtype=complex)
    r = 1 - 2 * np.pi * DC_FC / FS
    H *= (1 - w ** -1) / (1 - r * w ** -1)
    if low[0] == "fw":
        for i in HPIDX:
            s = SOS_FW[i]
            H *= (s[0] + s[1] * w ** -1 + s[2] * w ** -2) / \
                 (s[3] + s[4] * w ** -1 + s[5] * w ** -2)
    elif low[0] == "hp":
        H *= sps.sosfreqz(sps.butter(low[1], DC_FC / (FS / 2), btype="high",
                                     output="sos"), worN=f, fs=FS)[1]
    if lp:
        H *= sps.sosfreqz(mk_lp(*lp), worN=f, fs=FS)[1]
    return H


PTS = [0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0, 80.0, 100.0, 125.0, 150.0]


def freq_table(low, lp, fpts=PTS, verbose=True):
    f = np.array(fpts, float)
    g = np.abs(h_fw(f, low, lp)) * T41.h_afe(f)
    ref = float(np.abs(h_fw(np.array([10.0]), low, lp))[0] * T41.h_afe(np.array([10.0]))[0])
    pct = g / ref * 100.0
    if verbose:
        say("   %-44s" % "配置")
        say("   %s" % " ".join("%7s" % ("%.2f" % q) for q in fpts))
        for lo, hi, dlo, dhi in T41.BANDS:
            sel = (f >= lo - 1e-9) & (f <= hi + 1e-9)
            row = "".join("%6.1f%s" % (pct[i], "" if dlo - 0.5 <= pct[i] <= dhi + 0.5
                                       else "!") for i in np.where(sel)[0])
        say("   %s  (限 %s)" % (row, T41.BANDS))
    bad = []
    for lo, hi, dlo, dhi in T41.BANDS:
        sel = (f >= lo - 1e-9) & (f <= hi + 1e-9)
        for i in np.where(sel)[0]:
            if not (dlo - 0.5 <= pct[i] <= dhi + 0.5):
                bad.append((fpts[i], pct[i], dlo))
    return pct, bad


# ================================================================ 主流程
ap = argparse.ArgumentParser()
ap.add_argument("--fig", action="store_true")
A = ap.parse_args()

rule()
say("T53 · 低频脉冲判据 × 频响表 联合规划（853.2423 Hz 域，关陷波态）")
rule()

# ------------------------------ [0] 现状链清点
say("[0] 关陷波（notch off）后的现状链 —— 直接读源码系数")
say("    宏：ECG_NUM_SECTIONS=%d，ECG_NUM_NOTCH_SECTIONS=%d（IMP620_N2）"
    % (len(SOS_FW), NN))
say("    关陷波 = 跳过链首 %d 节 ⇒ 剩 %d 个 biquad（= %d 阶）："
    % (NN, len(SOS_FW) - NN, 2 * (len(SOS_FW) - NN)))
for i in range(NN, len(SOS_FW)):
    tag = "LP（低通）" if i in LPIDX else ("HP（高通）" if i in HPIDX else "?")
    say("      sos[%2d]  %s" % (i, tag))
say("    ＋链外 1 节：ecg_dc_block（1 阶 HP @0.05 Hz）")
say("    ─────────────────────────────────────────────")
say("    ⇒ 低端高通合计 = dc_block(1) + HP节(4) = 5 阶 @0.05 Hz")
say("    ⇒ 高端低通     = LP节(4)            = 4 阶 @150 Hz")
say("    ★ 六档 BP 恒为 4 节 ⇒ 关陷波后恒为「低端 5 阶 + 高端 4 阶」（仅 ST40 上角为 40 Hz）")

# ------------------------------ [1] 模型自证
say()
say("[1] ★ 边沿圆化标定（L × 增益扫描），锚点 = T52 关陷波实测（2 s 间隔）")
say("     锚点：位移 305.5 µV ｜ 峰值 3286.3 ｜ 下垂 158.8 ｜ 过冲/标称 9.54%")
say("     %-10s %9s %9s %9s %9s %8s" %
    ("L / 增益", "位移", "峰值", "下垂", "过冲", "总残差"))
best = (1e9, L_AFE, 1.0)
for g in (1.0, 1.012):
    for L in (1, 2, 3, 4, 5, 6):
        t, y, mk = pulse_sim(("fw",), ("but", 4, 150.0), interval=2.0,
                             L=L, gain=g)
        m = metrics(y, *mk[1])
        e = (abs(abs(m["disp"][0.02]) - 305.5) / 305.5
             + abs(m["peak"] - 3286.3) / 3286.3
             + abs(m["droop"] - 158.8) / 158.8)
        if e < best[0]:
            best = (e, L, g)
        say("     L=%-2d g=%.3f %9.1f %9.1f %9.1f %7.2f%% %8.4f"
            % (L, g, abs(m["disp"][0.02]), m["peak"], m["droop"],
               (m["peak"] - 3000) / 30.0, e))
CAL["L"], CAL["g"] = best[1], best[2]
say("     ⇒ 标定值 L=%d，增益=%.3f（残差 %.4f）" % (CAL["L"], CAL["g"], best[0]))
say("     说明：模型绝对过冲预测存在 ±1~2 pp 不确定度 ⇒ 判词以【相对改善】为准，")
say("           绝对是否 <10% 必须由 S2 上板实测定案。")
say("     注：间隔 2 s 会低估位移 ~30%（T52），故 [2][3][5] 一律用 10 s 间隔。")

# ------------------------------ [2] 低端：位移与斜率
say()
say("[2] 低端候选 → 位移 / 斜率（间隔 10 s，真基线；限 %(d).0f µV / %(s).0f µV/s）"
    % {"d": LIM_DISP, "s": LIM_SLOPE})
say("    %-30s %9s %9s %9s %9s %8s" %
    ("低端配置（+LP4@150）", "位移+0.02", "位移+0.5", "滑窗100ms", "拟合.2~1.2", "判决"))
LOWS = [("现状 dc1+HP4（5 阶）", ("fw",)),
        ("dc1（1 阶）", ("dc", 1)),
        ("dc1×2（2 阶）", ("dc", 2)),
        ("dc1+HP2（3 阶）", ("hp", 2)),
        ("dc1+HP3（4 阶）", ("hp", 3))]
for lab, low in LOWS:
    t, y, mk = pulse_sim(low, ("but", 4, 150.0))
    m = metrics(y, *mk[1])
    ok = (abs(m["disp"][0.02]) <= LIM_DISP) and (abs(m["sw100"]) <= LIM_SLOPE)
    say("    %-30s %9.1f %9.1f %9.1f %9.1f %8s"
        % (lab, abs(m["disp"][0.02]), abs(m["disp"][0.5]),
           abs(m["sw100"]), abs(m["sf"]), "✓" if ok else "✗"))

# ------------------------------ [3] 高端：过冲
say()
say("[3] 高端候选 → 脉冲过冲（限 %.0f%%；低端固定 dc1 1 阶）" % LIM_OS)
say("    %-30s %9s %9s %9s %8s   %s"
    % ("高端配置", "峰值µV", "过冲/标称", "过冲/近平台", "判决", "理论过冲"))
THEO = {("but", 2): 5.32, ("but", 3): 9.79, ("but", 4): 12.65,
        ("bes", 2): 0.43, ("bes", 3): 0.62, ("bes", 4): 0.86}
CAND = [("butter LP2 @150", ("but", 2, 150.0)),
        ("butter LP2 @200", ("but", 2, 200.0)),
        ("butter LP2 @220", ("but", 2, 220.0)),
        ("butter LP3 @200", ("but", 3, 200.0)),
        ("butter LP4 @150（现状）", ("but", 4, 150.0)),
        ("butter LP4 @200", ("but", 4, 200.0)),
        ("Bessel LP4 @200", ("bes", 4, 200.0)),
        ("Bessel LP4 @250", ("bes", 4, 250.0))]
for lab, lp in CAND:
    t, y, mk = pulse_sim(("dc", 1), lp)
    m = metrics(y, *mk[1])
    os_nom = (m["peak"] - 3000) / 30.0
    # 去下垂理想幅值 A：1 阶低端下垂极小，用 near 近似平台
    Aest = m["near"]
    os_A = (m["peak"] - Aest) / Aest * 100.0 if Aest > 0 else np.nan
    th = THEO.get((lp[0], lp[1]), np.nan)
    say("    %-30s %9.1f %8.2f%% %8.2f%% %8s   %5.2f%%"
        % (lab, m["peak"], os_nom, os_A,
           "✓" if os_nom < LIM_OS else "✗", th))

# ------------------------------ [4] 频响（含 AFE）
say()
say("[4] 频响表（GB 9706.225 表 201.107，关陷波态，含 AFE 级联）—— 150 Hz 行是瓶颈")
say("    判据：0.67~40 ±10% ｜ 40~100 与 100~150 +10%/−30%（下限 70%）｜ 150~500 只限上限")
say()
say("    %-34s %8s %8s %8s %8s   %s"
    % ("配置", "40Hz", "100Hz", "125Hz", "150Hz", "判决"))
FR_CAND = [("现状 dc1+HP4 & LP4@150", ("fw",), ("but", 4, 150.0)),
           ("dc1 & LP4@150", ("dc", 1), ("but", 4, 150.0)),
           ("dc1 & LP4@200", ("dc", 1), ("but", 4, 200.0)),
           ("dc1 & LP2@150", ("dc", 1), ("but", 2, 150.0)),
           ("dc1 & LP2@200", ("dc", 1), ("but", 2, 200.0)),
           ("dc1 & LP2@220", ("dc", 1), ("but", 2, 220.0)),
           ("dc1 & LP2@250", ("dc", 1), ("but", 2, 250.0)),
           ("dc1 & LP3@200", ("dc", 1), ("but", 3, 200.0)),
           ("dc1 & LP3@220", ("dc", 1), ("but", 3, 220.0)),
           ("dc1 & Bessel4@200", ("dc", 1), ("bes", 4, 200.0)),
           ("dc1 & Bessel4@250", ("dc", 1), ("bes", 4, 250.0)),
           ("dc1 & Bessel3@250", ("dc", 1), ("bes", 3, 250.0))]
FRQ = {}
for lab, low, lp in FR_CAND:
    f = np.array([40.0, 100.0, 125.0, 150.0], float)
    g = np.abs(h_fw(f, low, lp)) * T41.h_afe(f)
    ref = float(np.abs(h_fw(np.array([10.0]), low, lp))[0] * T41.h_afe(np.array([10.0]))[0])
    p = g / ref * 100.0
    FRQ[lab] = p
    ok = (p[1] >= 70) and (p[2] >= 70) and (p[3] >= 70)
    say("    %-34s %7.1f%s %7.1f%s %7.1f%s %7.1f%s   %s"
        % (lab, p[0], "", p[1], "", p[2], "", p[3],
           "" if ok else "✗", "✓" if ok else "✗ 150Hz 差 %.1f pp" % (70 - p[3])))

# ------------------------------ [5] 联合可行解
say()
say("[5] ★ 联合判决（三条脉冲判据 + 频响 150 Hz 同时看）")
say("    %-30s %8s %8s %8s %8s %8s  %s"
    % ("配置", "位移", "滑窗100", "过冲", "150Hz", "抗混叠", "总判"))
FINAL = [("现状 5阶 & LP4@150", ("fw",), ("but", 4, 150.0)),
         ("1阶 & LP4@150", ("dc", 1), ("but", 4, 150.0)),
         ("1阶 & LP4@200", ("dc", 1), ("but", 4, 200.0)),
         ("1阶 & LP2@150", ("dc", 1), ("but", 2, 150.0)),
         ("1阶 & LP2@200", ("dc", 1), ("but", 2, 200.0)),
         ("1阶 & LP2@220", ("dc", 1), ("but", 2, 220.0)),
         ("1阶 & LP2@250", ("dc", 1), ("but", 2, 250.0)),
         ("1阶 & LP3@200", ("dc", 1), ("but", 3, 200.0)),
         ("1阶 & LP3@220", ("dc", 1), ("but", 3, 220.0)),
         ("1阶 & Bessel4@200", ("dc", 1), ("bes", 4, 200.0)),
         ("1阶 & Bessel4@250", ("dc", 1), ("bes", 4, 250.0))]


def alias_metric(low, lp):
    """853→500 重采样无抗混叠 ⇒ 看 300/400/426.6 Hz 残余是否被压住（越小越好）"""
    f = np.array([300.0, 400.0, 426.0])
    g = np.abs(h_fw(f, low, lp)) * T41.h_afe(f)
    ref = float(np.abs(h_fw(np.array([10.0]), low, lp))[0] * T41.h_afe(np.array([10.0]))[0])
    return float(np.max(g / ref) * 100.0)


say("    （抗混叠 = 300/400/426 Hz 相对 10 Hz 的最大残余%，越接近 0 越好；"
    "853→500 无抗混叠低通）")
for lab, low, lp in FINAL:
    t, y, mk = pulse_sim(low, lp)
    m = metrics(y, *mk[1])
    f150 = FRQ.get(lab)
    if f150 is None:
        f = np.array([150.0])
        g = np.abs(h_fw(f, low, lp)) * T41.h_afe(f)
        r = float(np.abs(h_fw(np.array([10.0]), low, lp))[0] * T41.h_afe(np.array([10.0]))[0])
        f150 = g / r * 100.0
    al = alias_metric(low, lp)
    disp = abs(m["disp"][0.02])
    sw = abs(m["sw100"])
    osn = (m["peak"] - 3000) / 30.0
    ok = disp <= LIM_DISP and sw <= LIM_SLOPE and osn < LIM_OS and f150[-1] >= 70
    say("    %-30s %7.1f%s %7.1f%s %6.2f%%%s %6.1f%%%s %7.2f%%  %s"
        % (lab, disp, "✓" if disp <= LIM_DISP else "✗",
           sw, "✓" if sw <= LIM_SLOPE else "✗",
           osn, "✓" if osn < LIM_OS else "✗",
           f150[-1], "✓" if f150[-1] >= 70 else "✗",
           al, "✓ 全过" if ok else "✗"))

# ------------------------------ [6] 过冲口径 × 脉冲重复率
say()
say("[6] ★★ 过冲读数的「口径 × 重复率」交叉 —— 实测 9.54% 是被拖尾救出来的")
say("     同一组高端（LP4@150），只换脉冲间隔与低端：")
say("     %-24s %8s %9s %10s %10s" %
    ("配置 / 间隔", "峰值µV", "过冲/标称", "过冲/近平台", "过冲/去下垂A"))
for lab, low, iv in [("现状 5阶 / 2 s（=实测）", ("fw",), 2.0),
                     ("现状 5阶 / 10 s", ("fw",), 10.0),
                     ("1 阶 / 10 s（修法）", ("dc", 1), 10.0)]:
    t, y, mk = pulse_sim(low, ("but", 4, 150.0), interval=iv)
    m = metrics(y, *mk[1])
    near = m["near"]
    say("     %-24s %8.1f %8.2f%% %9.2f%% %9.2f%%"
        % (lab, m["peak"], (m["peak"] - 3000) / 30.0,
           (m["peak"] - near) / near * 100.0,
           (m["peak"] - 3000 * CAL["g"] * 1.012) / (3000 * CAL["g"] * 1.012) * 100.0))
say("     ⇒ ★「相对标称」口径依赖【绝对基线】，而 2 s 间隔下前脉冲拖尾把整段压低")
say("       ≈2.4 pp ⇒ 实测 9.54% 是虚低；10 s 间隔下同一配置读 12.4% ⇒ ✗")
say("       ★「相对近平台」口径在同一脉冲内取差，**不受重复率影响** ⇒ 最干净")
say("       ★ 实测近平台口径 = 13.37% ✗（T52 §3）。⇒ **过冲现状确实不达标**，")
say("         与 T51 预判一致；只是「标称」口径在 0.5 Hz 下偶然贴限。")

# ------------------------------ [7] 推荐
say()
say("[7] 推荐落点")
_t, _y, _mk = pulse_sim(("dc", 1), ("but", 2, 220.0))
_m = metrics(_y, *_mk[1])
_disp, _sw = abs(_m["disp"][0.02]), abs(_m["sw100"])
say("    ★ 内容 1（治低端）：低端 5 阶 → 1 阶。去掉 HP 节的 4 阶高通，只留 dc_block 单极 @0.05 Hz。")
say("      位移 333→%.0f µV、斜率 244→%.0f µV/s（间隔 10 s）。" % (_disp, _sw))
say("      这是唯一能同时过两条低端判据的形态，且与标准修订说明的基准模型")
say("      （single pole 0.05 Hz，286 µV/s）一致。")
say("    ★ 内容 2（治高端＋频响）：LP4@150 → LP2，fc 抬到 220~250 Hz。")
say("      过冲 12.4%→6.5%（标称）/13.9%→7.9%（近平台）；150 Hz 频响 54.8%→73~76%。")
say("      ⚠ **两件事必须一起做**：只降低端 ⇒ 过冲仍 12.5% ✗、频响仍 54.8% ✗；")
say("        只降高端 ⇒ 位移仍 333 µV ✗。")
say("    ★ 被淘汰的路线：")
say("      · LP3@200 —— 过冲「近平台」口径 11.0% ✗（贴限危险），但抗混叠最优（3.3%）")
say("      · Bessel4 —— 过冲最好（4.2%），但 150 Hz 只有 53.6~65.6% ✗（滚降太缓），淘汰")
say("      · LP4@200 —— 频响 75.7% ✓、抗混叠最优（1.5%），但过冲 12.3% ✗，淘汰")
say("    ★ 两个必须实测确认的风险：")
say("      ① 位移余量仅 %.0f%%（1 阶 10 s 间隔读 ~%.0f µV，判限 100）⇒ S2 上板定案；"
    % (100.0 - _disp, _disp))
say("      ② 抗混叠：fc 220→250 Hz 让 300~426 Hz 残余 0.32%→9.3%（本模型含 AFE）")
say("         ⇒ 先用列②（raw 853 Hz）实测 250~426 Hz 段能量，再定 fc 取 220 还是 250。")

rule()
with open(os.path.join(HERE, "_t53_out.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(OUT) + "\n")
print("[out] %s" % os.path.join(HERE, "_t53_out.txt"))

# ================================================================ 图
if A.fig:
    fig, axes = plt.subplots(2, 2, figsize=(15, 9.5))

    # ① 现状链结构
    ax = axes[0][0]
    ax.axis("off")
    ax.set_title("① 关陷波（notch off）后的现状链结构", fontsize=12, pad=12)
    blocks = [("AFE\n3×sinc⁵", 0.0), ("dc_block\n1阶@0.05Hz", 1.0),
              ("HP节\n4阶@0.05Hz", 2.0), ("LP节\n4阶@150Hz", 3.0),
              ("输出\ny_codes", 4.0)]
    for txt, xx in blocks:
        c = "#FDE7E7" if xx >= 2 else "#EAF3DE"
        ax.add_patch(plt.Rectangle((xx, 0.35), 0.86, 0.32, fc=c, ec="#666", lw=1.2))
        ax.text(xx + 0.43, 0.51, txt, ha="center", va="center", fontsize=9.5)
        if xx < 4.0:
            ax.annotate("", xy=(xx + 0.98, 0.51), xytext=(xx + 0.88, 0.51),
                        arrowprops=dict(arrowstyle="->", color="#666"))
    ax.text(1.0, 0.20, "低端合计 5 阶", ha="center", fontsize=11, color="#c00",
            weight="bold")
    ax.plot([1.43, 2.43], [0.28, 0.28], color="#c00", lw=2)
    ax.text(3.0, 0.20, "高端 4 阶", ha="center", fontsize=11, color="#c00",
            weight="bold")
    ax.plot([2.92, 3.92], [0.28, 0.28], color="#c00", lw=2)
    ax.text(0.0, 0.05,
            "关陷波 = 跳过链首 9 节 notch；与档位无关（所有档 BP 恒为 4 节）",
            fontsize=9.5, color="#444")
    ax.set_xlim(-0.05, 5.0)
    ax.set_ylim(0, 0.85)

    # ② 位移 vs 低端阶数
    ax = axes[0][1]
    labs, vals = [], []
    for lab, low in LOWS:
        t, y, mk = pulse_sim(low, ("but", 4, 150.0))
        m = metrics(y, *mk[1])
        labs.append(lab.replace("（", "\n（"))
        vals.append(abs(m["disp"][0.02]))
    cols = ["#c00" if v > LIM_DISP else "#0a0" for v in vals]
    ax.bar(range(len(vals)), vals, color=cols, alpha=0.85)
    ax.axhline(LIM_DISP, color="#c00", ls="--", lw=1.6)
    ax.text(len(vals) - 0.4, LIM_DISP * 1.08, "限 100 µV", color="#c00", fontsize=9)
    ax.set_xticks(range(len(labs)))
    ax.set_xticklabels(labs, fontsize=7.5, rotation=18, ha="right")
    ax.set_ylabel("位移 +0.02 s / µV")
    ax.set_title("② 位移 ← 低端阶数（间隔 10 s）", fontsize=12)
    ax.grid(alpha=0.25, axis="y")

    # ③ 过冲 vs 高端配置
    ax = axes[1][0]
    labs2, vals2 = [], []
    for lab, lp in CAND:
        t, y, mk = pulse_sim(("dc", 1), lp)
        m = metrics(y, *mk[1])
        labs2.append(lab)
        vals2.append((m["peak"] - 3000) / 30.0)
    cols = ["#0a0" if 0 <= v < LIM_OS else "#c00" for v in vals2]
    ax.barh(range(len(vals2)), vals2, color=cols, alpha=0.85)
    ax.axvline(LIM_OS, color="#c00", ls="--", lw=1.6)
    ax.text(LIM_OS + 0.15, 0.2, "限 10%", color="#c00", fontsize=9)
    ax.set_yticks(range(len(labs2)))
    ax.set_yticklabels(labs2, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("过冲（相对标称 3 mV）/ %")
    ax.set_title("③ 过冲 ← 高端 LP 阶数/类型", fontsize=12)
    ax.grid(alpha=0.25, axis="x")

    # ④ 频响曲线
    ax = axes[1][1]
    gx = np.exp(np.linspace(np.log(0.67), np.log(500), 600))
    for lab, low, lp, col, ls in [
            ("现状 5阶+LP4@150", ("fw",), ("but", 4, 150.0), "#c00", "-"),
            ("1阶+LP2@220", ("dc", 1), ("but", 2, 220.0), "#0a0", "-"),
            ("1阶+LP4@200", ("dc", 1), ("but", 4, 200.0), "#06c", "--"),
            ("1阶+Bessel4@250", ("dc", 1), ("bes", 4, 250.0), "#a0c", ":")]:
        g = np.abs(h_fw(gx, low, lp)) * T41.h_afe(gx)
        r = float(np.abs(h_fw(np.array([10.0]), low, lp))[0] * T41.h_afe(np.array([10.0]))[0])
        ax.semilogx(gx, g / r * 100, lw=1.8, color=col, ls=ls, label=lab)
    ax.axhline(70, color="#c00", ls="--", lw=1.4)
    ax.axvline(150, color="#999", ls=":", lw=1.2)
    ax.text(152, 25, "150 Hz", fontsize=8.5, color="#666")
    ax.text(1.0, 72, "下限 70%", fontsize=8.5, color="#c00")
    xi = np.arange(0.6, 5.6, 1.0)
    ax.set_xticks([0.67, 1, 2, 5, 10, 20, 40, 60, 100, 150, 200, 300, 500])
    ax.set_xticklabels(["0.67", "1", "2", "5", "10", "20", "40", "60", "100",
                        "150", "200", "300", "500"], fontsize=8)
    ax.set_xlim(0.67, 500)
    ax.set_ylim(0, 130)
    ax.set_xlabel("f / Hz")
    ax.set_ylabel("相对 10 Hz / %")
    ax.set_title("④ 频响表（含 AFE）——150 Hz 是瓶颈", fontsize=12)
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(alpha=0.25, which="both")

    fig.tight_layout()
    p = os.path.join(HERE, "T53_频响脉冲联合规划_2026-09-20.png")
    fig.savefig(p, dpi=130)
    print("[fig] %s" % p)
