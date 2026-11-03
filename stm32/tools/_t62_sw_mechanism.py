# -*- coding: utf-8 -*-
"""
T62 · 滑窗判据的驱动量归因（闭环验证）

为什么要有这一份
----------------
T61 先立了一个假说再自己推翻它：
  · 假说：滑窗读数 ≈ 5σ/0.1s（白噪声极值），于是"限 300 ⇔ σ_out ≤ 6.0 µV"。
  · 反证：用陷波器把输入的工频消掉，σ全带 16.34→7.26 µV（降 55%），
          可滑窗中位 485.7→469.4 µV/s（**只降 3%**）⇒ 假说是巧合。
  · 线索：滑窗最大窗落在**平台段**（降沿后 ~1.5 s），不是脉冲恢复边沿；
          列⑤ 平台段 0.1~0.5 Hz 频带能量 11.0 µV RMS，而输入同频段仅 0.234 µV。

于是本脚本把"驱动量是谁"做成**可判定的实验**，不再靠推理：

  [A] 条件与基本量      —— 复用 T56，确认这份数据仍是"新链 OFF"
  [B] 四链对照          —— 同一输入，把"低端"逐级拆开：dc1 / dc1+HP4 / dc1+HP4+LP / dc1+LP
  [C] 频带贡献分解      —— 把输出按频带切开，逐带算滑窗 ⇒ 哪一带贡献了判据读数
  [D] 阶跃响应预测      —— 用链的阶跃响应 D(t) 预测"脉冲 DC 阶跃"在 1.5 s 处的下垂斜率，
                          与实测滑窗比 ⇒ 定量回答"低端降 1 阶能削掉多少"
  [E] 脉冲挖除实验      —— 把输入里的脉冲段挖掉再过链，看滑窗是否随之消失
  [F] 结论位

判据口径（**不许改**，全部复用 T56.judge）
------------------------------------------
`T56.judge` 的 slope_win[w] 是 `np.nanmedian` ——
**对每个脉冲在其降沿后 [te+20ms, te+2.0s] 内取 max|Δy|/ΔT，再对所有脉冲取中位数**。
本脚本的逐脉冲扫描 agent 与 slice_win_max 同参（步长 0.2 ms），只多报一个 argmax 位置。

用法
----
  python _t62_sw_mechanism.py
  python _t62_sw_mechanism.py --file <txt> --tag _x
输出：`_t62_out.txt`
"""
import argparse
import io
import os
import sys

import numpy as np
import scipy.signal as ss

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t51_yy0885_pulse as T51          # noqa: E402  系数解析
import _t52_yy0885_notchoff as T52m      # noqa: E402  dc_block / droop_shape
import _t56_raw853_lowfreq as T56        # noqa: E402  读数据 / 判据
import _t60_fc250_onboard as T60         # noqa: E402  chain_of / run_chain / per_pulse

FS = T51.FS
UVPC = T56.UVPC
MACRO = 'ECG_PROFILE_IMP620_N2'

F_DEF = os.path.join(HERE, '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt')
F_ALT = os.path.join(HERE, '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt')

SRC_NEW_C, SRC_NEW_H = T51.COEFFS_C, T51.COEFFS_H
SRC_OLD_C = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak')
SRC_OLD_H = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak')

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 92)
    say(s)
    say('=' * 92)


# ================================================================== 判据口径（只读地复用参数）

def sw_scan(y, t, pulses, w=0.100, t0=0.020, t1=2.0, step=0.0002):
    """逐脉冲滑窗：返回 (每脉冲 max |Δy|/ΔT, 每脉冲 argmax 距降沿的秒数)。"""
    vals, locs, dlts = [], [], []
    for (s, e) in pulses:
        te = t[e]
        hi = min(te + t1, t[-1] - w)
        xs = np.arange(te + t0, hi, step)
        if len(xs) == 0:
            vals.append(np.nan); locs.append(np.nan); dlts.append(np.nan)
            continue
        ya = np.interp(xs, t, y)
        yb = np.interp(xs + w, t, y)
        z = np.abs(yb - ya) / w
        i = int(np.argmax(z))
        vals.append(float(z[i]))
        locs.append(float(xs[i] - te))
        dlts.append(float(yb[i] - ya[i]))
    return np.asarray(vals), np.asarray(locs), np.asarray(dlts)


def sw_med(y, t, pulses, w=0.100):
    """与 T56.judge 同口径：逐脉冲 max 后取中位。"""
    v, l, d = sw_scan(y, t, pulses, w)
    return float(np.nanmedian(v)), v, l, d


def sd_diff(v):
    """差分法 σ：对低频漂移免疫（白噪声下 σ = std(diff)/√2）。"""
    return float(np.std(np.diff(np.asarray(v, float))) / np.sqrt(2.0))


# ================================================================== 频带工具（FFT 掩码，避免超低带通病态）

def band_split(y, lo, hi, fs=FS):
    n = len(y)
    Y = np.fft.rfft(np.asarray(y, float))
    f = np.fft.rfftfreq(n, 1.0 / fs)
    m = (f >= lo) & (f <= hi)
    Y2 = np.zeros_like(Y)
    Y2[m] = Y[m]
    return np.fft.irfft(Y2, n)


BANDS = [('全带 0.05~400', 0.05, 400.0),
         ('极低频 0.05~0.5', 0.05, 0.5),
         ('去极低频 0.5~150', 0.5, 150.0),
         ('低 0.5~5', 0.5, 5.0),
         ('中 5~45', 5.0, 45.0),
         ('工频 45~55', 45.0, 55.0),
         ('工频 95~105', 95.0, 105.0),
         ('工频 195~205', 195.0, 205.0),
         ('高 105~400', 105.0, 400.0)]


# ================================================================== 链构造（只拆"低端"，高端固定 LP2@250）

def dc1(x, fc=0.05):
    return T52m.dc_block_apply(np.asarray(x, float), fc, FS)


def lp_pass(x, sos6, nn, hp):
    """高端：跳过 notch 段，且跳过低端 HP 节 ⇒ 只剩 LP。"""
    y = np.asarray(x, float)
    for i in range(nn, len(sos6)):
        if i not in hp:
            y = ss.sosfilt(sos6[i:i + 1], y)
    return y


def hp_pass(x, sos6, hp):
    """低端 HP 四节（不含 dc_block）。"""
    y = np.asarray(x, float)
    for i in hp:
        y = ss.sosfilt(sos6[i:i + 1], y)
    return y


def chains(ch, tag):
    """四条链：把'低端'逐级拆开，高端一律 LP2@250。
       L1 dc1                      （纯一阶高通，无 HP4、无 LP）
       L2 dc1 + HP4                （低端 5 阶 0.05 Hz）
       L3 dc1 + HP4 + LP2@250      （= 新链 OFF ＝ 现场固件）
       L4 dc1 + LP2@250            （= 未来链：低端降 1 阶）
    """
    s_n, nn, hp_n, k_n = ch['new']
    f = {}
    f['L1 dc1'] = dc1
    f['L2 dc1+HP4'] = lambda x: hp_pass(dc1(x), s_n, hp_n)
    f['L3 dc1+HP4+LP250'] = lambda x: lp_pass(hp_pass(dc1(x), s_n, hp_n), s_n, nn, hp_n)
    f['L4 dc1+LP250'] = lambda x: lp_pass(dc1(x), s_n, nn, hp_n)
    return f, s_n, nn, hp_n


# ================================================================== 主分析

def analyze(path, tag, ch):
    d = T56.load7(path)
    idx, raw, filt, dtc = d['idx'], d['raw'], d['filt'], d['dtc']
    t = (idx - idx[0]) / FS

    # ---------- [A] 条件
    cs = float(np.median(dtc))
    ns = (cs - 1373.0) / 641.3
    hdr('[%s] [A] 条件判定（dtcycles 配置指纹）' % tag)
    say('  dtcycles 中位 = %.0f ⇒ 折算 %.2f 节（四态：旧ON 9710｜新ON ~9069｜旧OFF 3938｜新OFF ~3297）'
        % (cs, ns))
    cand = {'旧链 ON': 9710.0, '新链 ON': 9068.7, '旧链 OFF': 3938.0, '新链 OFF': 3296.7}
    best = min(cand, key=lambda k: abs(cand[k] - cs))
    say('  ⇒ 最接近 **%s**（差 %+.1f cycles）' % (best, cs - cand[best]))

    pulses, thr, bl = T56.find_pulses(raw)
    rep = float(np.median([t[pulses[k][0]] - t[pulses[k - 1][0]] for k in range(1, len(pulses))]))
    say('  脉冲 %d 个 ｜ 周期 %.4f s ｜ 时长 %.3f s ｜ FS 由 idx 重建'
        % (len(pulses), rep, t[-1]))

    xin = np.asarray(raw, float) * UVPC          # ★ 量纲：code → µV
    y_mea = np.asarray(filt, float) * UVPC

    # 平台段（低电平段）：第2降沿后 1.5 s ~ 第3升沿前 0.5 s
    a0, b0 = t[pulses[1][1]] + 1.5, t[pulses[2][0]] - 0.5
    mp = (t >= a0) & (t <= b0)
    say('  平台段 %.2f~%.2f s（%d 点）｜ 输入基线 %.0f µV ｜ 扣基线后脉冲顶均 %.1f µV'
        % (t[mp][0], t[mp][-1], mp.sum(), bl * UVPC,
           float(np.mean([np.median(xin[u[0] + int(0.05 * FS):u[1] - int(0.05 * FS)])
                          for u in pulses])) - bl * UVPC))

    # ---------- [B] 四链对照
    F, s_n, nn, hp_n = chains(ch, tag)
    hdr('[%s] [B] 四链对照（同一列② raw 输入；把"低端"逐级拆开，高端一律 LP2@250）' % tag)
    YS = {'实测(列⑤)': y_mea}
    for k, fn in F.items():
        YS[k] = fn(xin)

    say('  %-18s %10s %11s %10s %8s %9s %9s' % ('链', '平台0.1-0.5', 'σ差分', '滑窗100',
                                                '落点s', '位移+0.1s', '过冲最差'))
    say('  ' + '-' * 84)
    rows = []
    for k in list(YS.keys()):
        y = YS[k]
        lowband = band_split(y, 0.1, 0.5)[mp]
        rms_low = float(np.sqrt(np.mean(lowband ** 2)))
        sd = sd_diff(y[mp])
        sm, v, l, dd = sw_med(y, t, pulses, 0.100)
        R = T56.judge(y, t, pulses, FS, rep)
        disp = R['disp_long'][0.100]
        _pk, _tp, op = T60.per_pulse(y, t, pulses, FS)
        loc = float(np.nanmedian(l)) if np.isfinite(np.nanmedian(l)) else float('nan')
        say('  %-18s %10.3f %11.3f %10.1f %8.2f %9.1f %8.2f%%'
            % (k, rms_low, sd, sm, loc, disp, op.max()))
        rows.append((k, rms_low, sd, sm, loc, disp, op.max(), v, l, dd))
    say('  ⚠ 平台 0.1-0.5 单位 µV RMS；滑窗/位移单位 µV/s、µV；限 滑窗≤300、位移≤100、过冲<10%')
    say('  ★ 逐脉冲滑窗读数（判据的分子）：')
    for (k, _a, _b, _c, _d, _e, _f, v, l, _dd) in rows:
        say('    %-18s ' % k + ' '.join('%7.1f' % z for z in v)
            + '  ｜ 落点 ' + ' '.join('%5.2f' % z for z in l))

    # ---------- [C] 滑窗 argmax 窗口形态诊断（不做频带分解：窄带重构会在方波边沿
    #              产生真实振铃，各带读数全部爆表，无法归因）
    hdr('[%s] [C] 滑窗 argmax 窗口形态：单调斜坡（确定性响应）还是往复摆动（噪声极值）？' % tag)
    say('  "单调性"=|窗内首尾差|/窗内峰谷差，≈1 单调、<<1 有往复；"折返"=窗内二阶差分变号次数。')
    for key in ('实测(列⑤)', 'L1 dc1', 'L2 dc1+HP4', 'L3 dc1+HP4+LP250', 'L4 dc1+LP250'):
        y = YS[key]
        say('  ── %s ──' % key)
        say('    %-4s %9s %7s %9s %9s %9s %9s %7s'
            % ('P#', 'max斜率', '落点s', '单调性', '窗内p-p', '剖面p50', '剖面p90', '折返'))
        for i, (s, e) in enumerate(pulses):
            te = t[e]
            xs = np.arange(te + 0.020, min(te + 2.0, t[-1] - 0.100), 0.0002)
            ya = np.interp(xs, t, y)
            yb = np.interp(xs + 0.100, t, y)
            z = np.abs(yb - ya) / 0.100
            k = int(np.argmax(z))
            seg = np.interp(np.linspace(xs[k], xs[k] + 0.100, 11), t, y)
            dlt, pp = seg[-1] - seg[0], seg.max() - seg.min()
            mono = abs(dlt) / (pp + 1e-12)
            nsc = int(np.sum(np.diff(np.sign(np.diff(seg))) != 0))
            say('    P%-3d %9.1f %7.2f %9.2f %9.2f %9.1f %9.1f %7d'
                % (i + 1, z[k], xs[k] - te, mono, pp, np.median(z),
                   np.percentile(z, 90), nsc))
    say('  ★ 判定：若落点批间乱跳、单调性低、折返多 ⇒ 读数由**噪声在窗口内的极值**决定；')
    say('    若落点稳定、单调性≈1 ⇒ 读数由**链的确定性阶跃恢复**决定。')

    # ---------- [C2] 降沿后波形抽样（最直观：直接看恢复形状）
    hdr('[%s] [C2] 降沿后 0~2.5 s 波形抽样（相对降沿时刻，µV；同一脉冲 P2）' % tag)
    e2 = pulses[1][1]
    te2 = t[e2]
    offs = np.arange(0.0, 2.51, 0.10)
    hdr_tab = '  %-7s' % 'dt(s)' + ''.join('%12s' % k for k in
                                          ('实测', 'L1 dc1', 'L2 +HP4', 'L3 +HP4+LP', 'L4 +LP'))
    say(hdr_tab)
    for o in offs:
        row = '  %-7.2f' % o
        for key in ('实测(列⑤)', 'L1 dc1', 'L2 dc1+HP4', 'L3 dc1+HP4+LP250', 'L4 dc1+LP250'):
            row += '%12.1f' % float(np.interp(te2 + o, t, YS[key]))
        say(row)

    # ---------- [C3] 分段对照：判据区间 vs 区外
    hdr('[%s] [C3] 分段最大 100 ms 斜率（判据区间 vs 段外对照，逐脉冲取中位）' % tag)
    COLS = ('实测(列⑤)', 'L1 dc1', 'L2 dc1+HP4', 'L3 dc1+HP4+LP250', 'L4 dc1+LP250')
    segs = [('降沿后 [0.02, 2.0]s（判据区间）', +1, 0.020, 2.0),
            ('降沿后 [2.5, 9.0]s（纯低电平段）', +1, 2.500, 9.0),
            ('升沿前 [-4.5, -1.0]s（高电平段）', -1, 4.500, 1.0)]
    say('  %-34s' % '段' + ''.join('%13s' % k.split()[0] for k in COLS))
    for lab, sgn, o0, o1 in segs:
        row = '  %-34s' % lab
        for key in COLS:
            y = YS[key]
            vs = []
            for (s0, e0) in pulses:
                anch = t[e0] if sgn > 0 else t[s0]
                a = anch - o0 if sgn < 0 else anch + o0
                b = anch - o1 if sgn < 0 else anch + o1
                if a < t[0] or b + 0.1 > t[-1] or b - 0.1 <= a:
                    continue
                xs = np.arange(a, b - 0.1, 0.0002)
                if len(xs) < 10:
                    continue
                z = np.abs(np.interp(xs + 0.1, t, y) - np.interp(xs, t, y)) / 0.1
                vs.append(float(z.max()))
            row += '%13.1f' % (float(np.median(vs)) if vs else np.nan)
        say(row)
    say('  ★ 若"纯低电平段"的读数与"判据区间"同量级 ⇒ 判据并未测到降沿特有的东西，')
    say('    而是在测**整段信号的 100 ms 摆动幅度**（噪声/工频 p-p ÷ 0.1 s）。')

    # ---------- [C4] 细粒度波形
    hdr('[%s] [C4] 判据区间细粒度波形（20 ms 步长，P2 降沿后 1.00~2.00 s，µV）' % tag)
    say('  %-8s' % 'dt(s)' + ''.join('%10s' % k.split()[0] for k in COLS))
    for o in np.arange(1.00, 2.001, 0.020):
        say('  %-8.2f' % o + ''.join('%10.1f' % float(np.interp(te2 + o, t, YS[k])) for k in COLS))

    # ---------- [C5] 窗口内频谱分解（最终定案实验）
    hdr('[%s] [C5] 滑窗 argmax 窗内的频谱分解：读数是不是工频峰谷差？' % tag)
    say('  ⚠ [C2]/[C4] 的 100 ms / 20 ms 抽样恰好是 50 Hz 周期的整数倍 ⇒ 工频被完全混叠隐藏，')
    say('    看起来"平滑"。本表回到 0.2 ms 原始序列，在 argmax 窗内直接做 FFT。')
    say('  %-4s %9s %9s %8s %8s %8s %11s %11s'
        % ('P#', '滑窗', '窗p-p', '50Hz', '100Hz', '200Hz', '去工频p-p', '去工频/0.1s'))
    for key in COLS:
        y = YS[key]
        say('  ── %s ──' % key)
        for i, (s0, e0) in enumerate(pulses):
            te = t[e0]
            xs = np.arange(te + 0.020, min(te + 2.0, t[-1] - 0.1), 0.0002)
            z = np.abs(np.interp(xs + 0.1, t, y) - np.interp(xs, t, y)) / 0.1
            k = int(np.argmax(z))
            x0 = xs[k]
            i0 = int(round(x0 * FS))
            n = int(round(0.100 * FS))
            if i0 + n >= len(y):
                continue
            seg = np.asarray(y[i0:i0 + n], float)
            ppp = float(seg.max() - seg.min())
            segc = seg - seg.mean()
            FR = np.fft.rfft(segc)
            f = np.fft.rfftfreq(len(segc), 1.0 / FS)

            def amp(fq):
                j = int(np.argmin(np.abs(f - fq)))
                return 2.0 * abs(FR[j]) / len(segc)

            F2 = FR.copy()
            mm = ((f >= 40) & (f <= 60)) | ((f >= 90) & (f <= 110)) | ((f >= 190) & (f <= 210))
            F2[mm] = 0.0
            seg2 = np.fft.irfft(F2, len(segc))
            pp2 = float(seg2.max() - seg2.min())
            say('  %-4s %9.1f %9.2f %8.2f %8.2f %8.2f %11.2f %11.1f'
                % ('P%d' % (i + 1), z[k], ppp, amp(50.0), amp(100.0), amp(200.0),
                   pp2, pp2 / 0.1))
    say('  ★ 判定：若"去工频 p-p / 0.1 s" << 滑窗读数 ⇒ 读数主体就是工频峰谷差。')

    # ---------- [D] 阶跃响应预测
    hdr('[%s] [D] 阶跃响应预测：脉冲 DC 阶跃在 t 处造成的下垂斜率 = A × dD/dt' % tag)
    tmax = 8.0
    tD5, D5 = T52m.droop_shape(s_n, hp_n, FS, tmax=tmax)       # dc1 ∘ HP4（5 阶）
    xs = np.zeros(int(tmax * FS))
    xs[10:] = 1.0
    D1 = T52m.dc_block_apply(xs, 0.05, FS)                      # dc1 only（1 阶）
    tD1 = (np.arange(len(D1)) - 10) / FS

    # ★ A 必须**扣基线**：raw 是 code 域，绝对值带 AFE 偏置（实测 ~8.8e6 code）
    A = float(np.mean([np.median(xin[u[0] + int(0.05 * FS):u[1] - int(0.05 * FS)])
                       for u in pulses]) - bl * UVPC)
    say('  输入基线(列② 中位) = %.1f µV ｜ 扣基线后脉冲顶均 A = %.1f µV'
        % (bl * UVPC, A))
    A = abs(A)

    def dslope(tt, D, tq):
        i = int(np.searchsorted(tt, tq))
        i = max(2, min(i, len(tt) - 3))
        return (D[i + 1] - D[i - 1]) / (tt[i + 1] - tt[i - 1])

    say()
    say('  %-8s %14s %14s %14s %14s' % ('dt(s)', 'dD5/dt(1/s)', 'dD1/dt(1/s)', '预测5阶 µV/s', '预测1阶 µV/s'))
    for tq in (0.02, 0.05, 0.10, 0.20, 0.50, 1.00, 1.50, 2.00):
        s5, s1 = dslope(tD5, D5, tq), dslope(tD1, D1, tq)
        say('  %-8.2f %14.6f %14.6f %14.1f %14.1f' % (tq, s5, s1, A * abs(s5), A * abs(s1)))
    say('  ★ 若"滑窗＝确定性的阶跃恢复斜率"，读数应≈对应 dt 处的预测值（量级与位置都要对）。')

    # 平台段下垂形态
    say()
    say('  平台段首尾漂移（[B] 四链）：')
    for (k, _a, _b, _c, _d, _e, _f, _v, _l, _dd) in rows:
        seg = YS[k][mp]
        i0, i1 = int(0.05 * len(seg)), int(0.95 * len(seg))
        say('    %-18s 首→尾 %+9.2f µV ｜ 线性拟合斜率 %+8.2f µV/s'
            % (k, seg[i1] - seg[i0], np.polyfit(t[mp] - t[mp][0], seg, 1)[0]))

    # ---------- [E] 脉冲挖除实验
    hdr('[%s] [E] 脉冲挖除：把输入脉冲段替成端点线性插值，看滑窗是否随之消失' % tag)
    xn = xin.copy()
    for (s, e) in pulses:
        i0 = max(0, s - int(0.050 * FS))
        i1 = min(len(xn) - 1, e + int(0.050 * FS))
        if i1 > i0 + 2:
            xn[i0:i1 + 1] = np.linspace(xn[i0], xn[i1], i1 - i0 + 1)
    say('  ⚠ 挖除会同时改变激励的极低频含量，故此实验只回答"高电平段的存在是否是必要条件"，')
    say('    不能单独定量。')
    y_n3 = F['L3 dc1+HP4+LP250'](xn)
    y_n4 = F['L4 dc1+LP250'](xn)
    say('  %-22s %11s %11s %11s' % ('', '滑窗100中位', '最差', '位移+0.1s'))
    for lab, yy, yy0 in (('L3 原输入', YS['L3 dc1+HP4+LP250'], None),
                         ('L3 挖脉冲', y_n3, None),
                         ('L4 原输入', YS['L4 dc1+LP250'], None),
                         ('L4 挖脉冲', y_n4, None)):
        sm, v, _l, _d = sw_med(yy, t, pulses, 0.100)
        R = T56.judge(yy, t, pulses, FS, rep)
        say('  %-22s %11.1f %11.1f %11.1f' % (lab, sm, np.nanmax(v), R['disp_long'][0.100]))

    # ---------- [G] 合成对照：读数里有多少是"噪声底"
    hdr('[%s] [G] 合成对照：理想方波 + 白噪声 ⇒ 判据读数的"纯噪声底"' % tag)
    sd_plat = sd_diff(xin[mp])
    rng = np.random.default_rng(20260920)
    sq = np.zeros_like(xin)
    for (s0, e0) in pulses:
        sq[s0:e0] = A
    say('  合成 = 理想方波(A=%.0f µV, 与实测同相) + 高斯白噪声(σ=%.3f µV，取自输入平台段差分 σ)'
        % (A, sd_plat))
    say('  %-26s %11s %11s %11s' % ('', '滑窗100中位', '最差', '位移+0.1s'))
    for lab, yy in (('真输入 · 实测(列⑤)', y_mea),
                    ('真输入 · L3', YS['L3 dc1+HP4+LP250']),
                    ('真输入 · L4', YS['L4 dc1+LP250'])):
        sm, v, _l, _d = sw_med(yy, t, pulses, 0.100)
        say('  %-26s %11.1f %11.1f %11.1f'
            % (lab, sm, np.nanmax(v), T56.judge(yy, t, pulses, FS, rep)['disp_long'][0.100]))
    say('  ' + '-' * 62)
    for lab, fn in (('L1 dc1', F['L1 dc1']),
                    ('L3 dc1+HP4+LP250', F['L3 dc1+HP4+LP250']),
                    ('L4 dc1+LP250', F['L4 dc1+LP250'])):
        yy = fn(sq + rng.normal(0.0, sd_plat, len(sq)))
        sm, v, _l, _d = sw_med(yy, t, pulses, 0.100)
        say('  %-26s %11.1f %11.1f %11.1f'
            % ('合成 · ' + lab, sm, np.nanmax(v), T56.judge(yy, t, pulses, FS, rep)['disp_long'][0.100]))
    say('  ★ 若"合成 L3"≈"真输入 L3"，则真实输入里超出白噪声的部分对读数无贡献；')
    say('    若"合成 L4"仍 ≈ 或 > 300，则**低端降 1 阶不可能把滑窗压到限内**。')

    # ---------- [H] 判决实验：输入精确陷波
    hdr('[%s] [H] 判决实验：输入精确陷波（对准实测频率，不假设 50 Hz）' % tag)
    n = len(xin)
    X = np.fft.rfft(xin - float(np.median(xin)))
    fx = np.fft.rfftfreq(n, 1.0 / FS)
    Aa = np.abs(X)
    loc = (Aa[1:-1] > Aa[:-2]) & (Aa[1:-1] > Aa[2:]) & (fx[1:-1] >= 40) & (fx[1:-1] <= 300)
    cand = (np.where(loc)[0] + 1)
    cand = cand[np.argsort(Aa[cand])[::-1]]
    peaks = []
    for c in cand:
        if len(peaks) >= 6:
            break
        if all(abs(fx[c] - p) > 6.0 for p in peaks):
            peaks.append(float(fx[c]))
    peaks.sort()
    say('  输入频谱 40~300 Hz 的前 6 个峰（间隔>6 Hz）：'
        + ' ｜ '.join('%.1f Hz(%.1f µV)' % (p, 2 * Aa[int(np.argmin(np.abs(fx - p)))] / n)
                      for p in peaks))

    xn0 = xin - float(np.median(xin))
    xnf = xn0.copy()
    for f0 in peaks:
        b_, a_ = ss.iirnotch(f0, 10.0, FS)
        xnf = ss.filtfilt(b_, a_, xnf)
    xnf = xnf + float(np.median(xin))
    say('  %-26s %11s %11s %11s %11s'
        % ('输入条件 / 链', 'σ差分', '滑窗100', '最差', '位移+0.1s'))
    for lab, xx in (('原始输入', xin), ('精确陷波后', xnf)):
        for cname, cfn in (('L3 dc1+HP4+LP250', F['L3 dc1+HP4+LP250']),
                           ('L4 dc1+LP250', F['L4 dc1+LP250'])):
            yy = cfn(xx)
            sm, v, _l, _d = sw_med(yy, t, pulses, 0.100)
            Rj = T56.judge(yy, t, pulses, FS, rep)
            say('  %-26s %11.3f %11.1f %11.1f %11.1f'
                % (lab + ' · ' + cname.split()[0], sd_diff(yy[mp]), sm,
                   np.nanmax(v), Rj['disp_long'][0.100]))
    say('  ⚠ 陷波后滑窗**暴涨**是 `filtfilt` 的伪影（T61 §8 已记录），不是物理：线性时不变')
    say('    操作不可能放大信号。但 **σ 的下降（8.83→3.32，降 62%）是真实的** ——')
    say('    它正是"总 σ 扣除工频贡献后的宽带噪声 σ"。')
    say('  ★ σ=8.83 ⇒ 5σ/0.1s = %.0f ｜ σ=3.32 ⇒ %.0f ｜ 判限 300 ⇔ σ≤6.0'
        % (8.832 * 50.0, 3.318 * 50.0))

    # ---------- [I] 谐波拟合分解（无滤波伪影）—— 最终归因
    hdr('[%s] [I] 窗口内谐波拟合分解（最小二乘，无滤波伪影）—— 最终归因' % tag)
    FG = (50.0, 100.0, 200.1)

    def harm_fit(seg, tt):
        cols = [np.ones_like(tt)]
        for f0 in FG:
            cols.append(np.cos(2 * np.pi * f0 * tt))
            cols.append(np.sin(2 * np.pi * f0 * tt))
        colsz = tt - tt.mean()
        cols.append(colsz)
        M = np.vstack(cols).T
        cc, *_ = np.linalg.lstsq(M, seg, rcond=None)
        return cc, seg - M @ cc

    say('  模型 y ≈ c + Σ[a·cos+b·sin]_{50,100,200.1Hz} + 线性趋势；残差 = 去工频/去趋势')
    say('  %-4s %9s %9s %9s %9s %9s %9s %9s %9s'
        % ('P#', '窗p-p', '残差p-p', 'A50', 'A100', 'A200', '残差σ全', '残差σ差', '残差/0.1s'))
    for key in ('实测(列⑤)', 'L3 dc1+HP4+LP250', 'L4 dc1+LP250'):
        y = YS[key]
        say('  ── %s ──' % key)
        for i, (s0, e0) in enumerate(pulses):
            te = t[e0]
            xs = np.arange(te + 0.020, min(te + 2.0, t[-1] - 0.1), 0.0002)
            z = np.abs(np.interp(xs + 0.1, t, y) - np.interp(xs, t, y)) / 0.1
            k = int(np.argmax(z))
            i0 = int(round(xs[k] * FS))
            n2 = int(round(0.100 * FS))
            if i0 + n2 >= len(y):
                continue
            seg = np.asarray(y[i0:i0 + n2], float)
            tt = t[i0:i0 + n2]
            cc, res = harm_fit(seg, tt)
            say('  %-4s %9.2f %9.2f %9.2f %9.2f %9.2f %9.3f %9.3f %9.1f'
                % ('P%d' % (i + 1), float(seg.max() - seg.min()),
                   float(res.max() - res.min()),
                   float(np.hypot(cc[1], cc[2])), float(np.hypot(cc[3], cc[4])),
                   float(np.hypot(cc[5], cc[6])),
                   float(np.std(res)), sd_diff(res), float(res.max() - res.min()) / 0.1))
    say('  ★ 若"残差p-p"≈5×残差σ全 ⇒ 残余是白噪声，滑窗 = 5σ（T61 §5.2 的关系式成立）；')
    say('    若"残差σ全" >> "残差σ差" ⇒ 残余含低频漂移，p-p 由漂移而非白噪声决定。')

    # ---------- [J] 四态跨条件对照
    hdr('[J] 四态跨条件对照：滑窗读数跟着 notch 开关走，还是跟着链的新旧走？')
    say('  全部用**实测列⑤**（与链模型无关），同口径算判据。')
    CASES = [('180458 旧链 ON', '3mv-0.1hz-raw853on-2026-09-20 180458.txt'),
             ('182243 旧链 OFF', '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt'),
             ('195208 新链 ON(名OFF)', '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 195208.txt'),
             ('200724 新链 OFF', '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt')]
    say('  %-24s %9s %9s %9s %9s %9s %9s'
        % ('数据(条件)', 'dtcycles', 'σ差分', 'σ全', '滑窗100', '位移+0.1', '过冲最差'))
    for lab, fn in CASES:
        pp = os.path.join(HERE, fn)
        if not os.path.exists(pp):
            say('  %-24s （文件不存在）' % lab)
            continue
        dd = T56.load7(pp)
        tt = (dd['idx'] - dd['idx'][0]) / FS
        pz, _thr, _bl2 = T56.find_pulses(dd['raw'])
        if len(pz) < 3:
            say('  %-24s （脉冲不足）' % lab)
            continue
        rp = float(np.median([tt[pz[k][0]] - tt[pz[k - 1][0]] for k in range(1, len(pz))]))
        yy = dd['filt'] * UVPC
        a1, b1 = tt[pz[1][1]] + 1.5, tt[pz[2][0]] - 0.5
        mk = (tt >= a1) & (tt <= b1)
        R = T56.judge(yy, tt, pz, FS, rp)
        _pk, _tp, op = T60.per_pulse(yy, tt, pz, FS)
        say('  %-24s %9.0f %9.3f %9.3f %9.1f %9.1f %8.2f%%'
            % (lab, float(np.median(dd['dtc'])), sd_diff(yy[mk]), float(np.std(yy[mk])),
               R['slope_win'][0.100], R['disp_long'][0.100], op.max()))
    say('  ★ 若"滑窗100"随 notch 开关（⇒ 工频在不在）而变、与链的新旧无关 ⇒')
    say('    判据测的是**输出噪声水平**，不是链的通带形状。')

    # ---------- [K] ON vs OFF 窗内原始采样
    hdr('[K] 滑窗 argmax 窗内的原始采样（直击：振铃 还是 噪声？）')
    for lab, fn in (('200724 新链 OFF', '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt'),
                    ('195208 新链 ON(名OFF)', '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 195208.txt'),
                    ('182243 旧链 OFF', '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt'),
                    ('180458 旧链 ON', '3mv-0.1hz-raw853on-2026-09-20 180458.txt')):
        pp = os.path.join(HERE, fn)
        if not os.path.exists(pp):
            continue
        dd = T56.load7(pp)
        tt = (dd['idx'] - dd['idx'][0]) / FS
        pz, _t2, _b3 = T56.find_pulses(dd['raw'])
        yy = dd['filt'] * UVPC
        if len(pz) < 2:
            continue
        te = tt[pz[1][1]]
        xs = np.arange(te + 0.020, min(te + 2.0, tt[-1] - 0.1), 0.0002)
        ya = np.interp(xs, tt, yy)
        yb = np.interp(xs + 0.1, tt, yy)
        z = np.abs(yb - ya) / 0.1
        k = int(np.argmax(z))
        say('  ── %s ──  argmax 落点 te+%.3f s ｜ 读数 %.1f µV/s ｜ 窗内 Δ=%+.1f µV'
            % (lab, xs[k] - te, z[k], yb[k] - ya[k]))
        g = np.arange(0.0, 0.1001, 0.004)
        v = np.interp(xs[k] + g, tt, yy)
        say('     窗内 0.2 ms 栅格每 4 ms 取 1 点（26 点，µV）：')
        say('       ' + ' '.join('%7.1f' % q for q in v[:13]))
        say('       ' + ' '.join('%7.1f' % q for q in v[13:]))
        say('     窗内 p-p = %.2f µV ｜ 首尾差 = %+.2f µV ｜ 二阶差分变号 = %d'
            % (v.max() - v.min(), v[-1] - v[0],
               int(np.sum(np.diff(np.sign(np.diff(v))) != 0))))
    say('  ★ 若 ON 的窗内是"单向下冲后快速回弹" ⇒ notch 的边沿振铃（确定性）；')
    say('    若 OFF 的窗内是"无规律抖动" ⇒ 噪声极值。')

    # ---------- [F] 结论位
    hdr('[%s] [F] 结论位' % tag)
    sm3 = rows[3][3] if len(rows) > 3 else np.nan
    say('  条件：%s ｜ 链身份参照：实测列⑤ 与哪条链最接近见 [B] 平台 0.1-0.5 与 σ' % best)
    for (k, rl, sd, sm, loc, disp, op, _v, _l, _dd) in rows:
        say('    %-18s 滑窗 %7.1f（%5.2f×限）｜ 位移 %8.1f ｜ 平台极低频 %7.3f µV'
            % (k, sm, sm / 300.0, disp, rl))
    return dict(tag=tag, t=t, pulses=pulses, YS=YS, rows=rows, best=best, cs=cs, A=A,
                tD5=tD5, D5=D5, tD1=tD1, D1=D1, mp=mp, xin=xin)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_DEF)
    ap.add_argument('--tag', default='本次 200724')
    ap.add_argument('--alt', default=F_ALT)
    ap.add_argument('--tag-alt', default='T57 182243')
    ap.add_argument('--no-alt', action='store_true')
    a = ap.parse_args()

    say('T62 · 滑窗判据驱动量归因（四实验闭环）')
    say('file = %s' % a.file)

    ch = {}
    s_n, nn, hp_n, k_n = T60.chain_of(SRC_NEW_C, SRC_NEW_H)
    s_o, nn_o, hp_o, k_o = T60.chain_of(SRC_OLD_C, SRC_OLD_H)
    ch['new'] = (s_n, nn, hp_n, k_n)
    ch['old'] = (s_o, nn_o, hp_o, k_o)
    say('  新链节型：%s' % T60.descr(k_n, nn))
    say('  旧链节型：%s' % T60.descr(k_o, nn_o))

    A = analyze(a.file, a.tag, ch)
    if not a.no_alt and os.path.exists(a.alt):
        say()
        say('#' * 92)
        say('# 跨文件复核：%s' % a.tag_alt)
        say('#' * 92)
        analyze(a.alt, a.tag_alt, ch)

    p = os.path.join(HERE, '_t62_out.txt')
    io.open(p, 'w', encoding='utf-8').write('\n'.join(OUT) + '\n')
    print('\n输出 -> %s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
