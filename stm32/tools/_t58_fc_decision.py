# -*- coding: utf-8 -*-
"""
_t58_fc_decision.py —— 「先做 fc=250」的决策账（离线，不改固件）

背景
----
猛哥 09-20 18:52 决定：先把 fc=250 落到实处，再按上板数据定下一步。
本脚本把 "fc=250" 的可能形态算清，供拍板：

  现状   HP4@0.05 + LP4@150    ← 当前固件（六档 BP 共用，4 节 = 2LP + 2HP）
  A      只抬 fc      HP4@0.05 + LP4@250     （变量隔离：只动 fc）
  B      抬 fc + 降 LP 阶  HP4@0.05 + LP2@250   （只动高端两件事）
  C      T54 完整方案  HP1@0.05 + LP2@250     （低端也降 1 阶，T54 §4 口径）

输出：表 201.107 四行判决 / 150 Hz 精确余量 / 阶跃过冲 / 等效噪声带宽(ENBW)。

口径（与 T53/T54 一致，勿改）
------------------------------
  fs = 853.2423208191125（链域；Nyquist 426.6 ⇒ fc=250 合法）
  853 域 **数字** 滤波（butter + freqz），**不是**解析原型（两者差 3.8 pp，见 T54 §9）
  必须乘 AFE 传递 h_afe（T41 的三级 sinc^5 CIC）
  频响按 10 Hz 归一化
"""
import io
import os
import sys

import numpy as np
from scipy import signal as ss

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t41_freqresp as T41          # h_afe

FS = 853.2423208191125
OUT = []


def say(s=''):
    OUT.append(s)
    print(s)


# ---------------------------------------------------------------- 传递函数
def h_chain(f, hp_order, hp_fc, lp_order, lp_fc, with_afe=True):
    """853 域数字滤波：HP(order)@hp_fc -> LP(order)@lp_fc -> AFE（可选）。"""
    f = np.asarray(f, float)
    w = 2.0 * np.pi * f / FS
    bh, ah = ss.butter(hp_order, hp_fc / (FS / 2.0), 'high')
    _, hh = ss.freqz(bh, ah, worN=w)
    bl, al = ss.butter(lp_order, lp_fc / (FS / 2.0), 'low')
    _, hl = ss.freqz(bl, al, worN=w)
    g = np.abs(hh) * np.abs(hl)
    return g * T41.h_afe(f) if with_afe else g


def overshoot_lp(lp_order, lp_fc, n=40000):
    """★ 只算 LP 部分的阶跃过冲（LP 的 DC 增益=1，稳态可靠）。
    ⚠ 不能用全链：HP@0.05 会把直流阶跃滤到 0，稳态分母趋 0 ⇒ 读数爆炸。"""
    b, a = ss.butter(lp_order, lp_fc / (FS / 2.0), 'low')
    y = ss.lfilter(b, a, np.ones(n))
    return (y.max() - 1.0) * 100.0


def overshoot_pulse(hp_order, hp_fc, lp_order, lp_fc, n=40000, pw=0.100, t0=0.5,
                    in_bw=96.0):
    """100 ms 方波过全链，取「极值 vs 顶部均值（+60~95 ms）」（与 T57 实测同口径）。

    ★ in_bw：输入上升沿带宽。实列② raw 的 10–90% 上升时间 = 3.65 ms（T56 实测）
      ⇒ τ = 3.65/2.2 = 1.66 ms ⇒ 一阶 LP ≈ 96 Hz。
      ⚠ 不加这一段，理想阶跃会无谓放大离散化伪影（fc=250 归一化 0.586 已很高）。
    """
    t = np.arange(n) / FS
    x = ((t >= t0) & (t < t0 + pw)).astype(float)
    if in_bw:
        bi, ai = ss.butter(1, in_bw / (FS / 2.0), 'low')
        x = ss.lfilter(bi, ai, x)
    bh, ah = ss.butter(hp_order, hp_fc / (FS / 2.0), 'high')
    bl, al = ss.butter(lp_order, lp_fc / (FS / 2.0), 'low')
    y = ss.lfilter(bl, al, ss.lfilter(bh, ah, x))
    m = (t >= t0) & (t < t0 + pw)
    pk = float(y[m].max())
    top = float(np.median(y[(t >= t0 + 0.060) & (t < t0 + 0.095)]))
    return (pk - top) / top * 100.0


def enbw(hp_order, hp_fc, lp_order, lp_fc, n=32768):
    """等效噪声带宽 (Hz)：∫|H|²df（|H(0)|=1）。"""
    w, h = ss.freqz(*ss.butter(lp_order, lp_fc / (FS / 2.0), 'low'),
                    worN=n, fs=FS)
    return float(np.trapezoid(np.abs(h) ** 2, w))


# ---------------------------------------------------------------- 候选
CASES = [
    ("现状  HP4@0.05 + LP4@150", 4, 0.05, 4, 150.0, "当前固件（六档共用）"),
    ("A     HP4@0.05 + LP4@250", 4, 0.05, 4, 250.0, "只抬 fc，变量隔离"),
    ("B     HP4@0.05 + LP2@250", 4, 0.05, 2, 250.0, "只动高端：抬 fc + 降 LP 阶"),
    ("C     HP1@0.05 + LP2@250", 1, 0.05, 2, 250.0, "T54 完整方案（低端也降 1 阶）"),
]

ROWS = [(0.67, 40.0, 90.0, 110.0, "±10%"),
        (40.0, 100.0, 70.0, 110.0, "+10/-30%"),
        (100.0, 150.0, 70.0, 110.0, "+10/-30%, 下限70%"),
        (150.0, 500.0, 0.0, 110.0, "+10/-100%(只限上限)")]


def main():
    say("=" * 78)
    say("[0] fc 候选 × LP 阶数 —— 表 201.107 判决（853 域数字 + AFE，归一 10 Hz）")
    say("=" * 78)
    say("  fs = %.7f Hz（链域）⇒ Nyquist = %.1f Hz ⇒ fc=250 合法" % (FS, FS / 2))
    say("")
    say("  %-26s %-30s" % ("候选", "说明"))
    for name, ho, hf, lo, lf, note in CASES:
        say("  %-26s %-30s" % (name, note))

    say("")
    say("  各档 BP 节数：现状 4 节 = 2×LP4 + 2×HP4；A 仍 4 节；B 3 节（2×HP4+1×LP2）；C 1 节（仅 LP2）")
    say("")
    short = [n.split()[0] for n, *_ in CASES]
    say("  %-16s %-22s" % ("行", "容差") + "".join("%15s" % s for s in short))
    for lo_, hi_, tlo, thi, note in ROWS:
        ff = np.logspace(np.log10(lo_), np.log10(hi_), 300)
        cells = []
        for name, ho, hf, lo, lf, _ in CASES:
            ref = float(h_chain(np.array([10.0]), ho, hf, lo, lf)[0])
            g = h_chain(ff, ho, hf, lo, lf) / ref * 100.0
            vmin, vmax = float(g.min()), float(g.max())
            ok = (vmin >= tlo - 1e-9) and (vmax <= thi + 1e-9)
            cells.append("%.1f~%.1f%s" % (vmin, vmax, " OK" if ok else " XX"))
        say("  %-16s %-22s" % ("%g~%g Hz" % (lo_, hi_), note) + "".join(
            "%15s" % c for c in cells))

    say("")
    say("  ★ 150 Hz 单点（表 201.107 第 3 行下限 70%，唯一贴限点）")
    say("    %-26s %10s %12s %10s" % ("候选", "150Hz %", "余量 pp", "判决"))
    for name, ho, hf, lo, lf, _ in CASES:
        ref = float(h_chain(np.array([10.0]), ho, hf, lo, lf)[0])
        v = float(h_chain(np.array([150.0]), ho, hf, lo, lf)[0]) / ref * 100.0
        say("    %-26s %10.2f %+12.2f %10s"
            % (name, v, v - 70.0, "OK" if v >= 70.0 else "XX"))

    say("")
    say("  ★ 100 Hz 单点（同第 3 行，看滚降形状）")
    for name, ho, hf, lo, lf, _ in CASES:
        ref = float(h_chain(np.array([10.0]), ho, hf, lo, lf)[0])
        v = float(h_chain(np.array([100.0]), ho, hf, lo, lf)[0]) / ref * 100.0
        say("    %-26s %10.2f %%" % (name, v))

    say("")
    say("  ★ 阶跃过冲与 ENBW")
    say("    %-26s %10s %9s %12s %10s"
        % ("候选", "LP阶跃%", "判限10%", "方波过冲%", "ENBW Hz"))
    for name, ho, hf, lo, lf, _ in CASES:
        ov = overshoot_lp(lo, lf)
        ovp = overshoot_pulse(ho, hf, lo, lf)
        eb = enbw(ho, hf, lo, lf)
        say("    %-26s %10.2f %9s %12.2f %10.1f"
            % (name, ov, "OK" if ov < 10.0 else "XX", ovp, eb))
    say("    （LP阶跃＝纯 LP 阶跃过冲，T53 §3 口径；方波＝100 ms 方波过全链、极值/顶均，T57 实测口径）")
    say("")
    base = enbw(*CASES[0][1:5])
    say("    噪声（白噪近似，相对现状）:")
    for name, ho, hf, lo, lf, _ in CASES:
        eb = enbw(ho, hf, lo, lf)
        say("      %-26s ENBW ×%.2f  ⇒ 白噪 RMS ×%.2f"
            % (name, eb / base, np.sqrt(eb / base)))

    say("")
    say("=" * 78)
    say("[1] 结论")
    say("=" * 78)
    say("  · fc=250 本身是必须的：150 Hz 现状 54.76% 是硬失败（限 ≥70%），")
    say("    修它只能抬上角；200 余量太薄 ⇒ 250 是既定选择。")
    say("  · 两个解释的差别只在过冲：保 LP4 ⇒ 过冲不变（仍 ✗）；降 LP2 ⇒ 过冲过。")
    say("  · 低端是否降 1 阶（HP4→删）对 150 Hz 频响几乎无影响（HP 在 150Hz ≈ 0 dB），")
    say("    它只决定位移/斜率那两条判据。")
    say("")

    with io.open(os.path.join(HERE, '_t58_out.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
    say("（已写 _t58_out.txt）")


if __name__ == '__main__':
    main()
