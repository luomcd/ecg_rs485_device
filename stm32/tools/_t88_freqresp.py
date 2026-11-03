#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T88 · 低频方波的**谐波域频率响应**实测 ＋ 尾段极点拟合（参数定版验收用）

为什么要有这个脚本（与 T85/T86/T88 的相位表判读互补）
------------------------------------------------------
10 s 周期的 3 mV/100 ms 方波串，其频谱是 **0.1 Hz 整数倍** 的线谱（基频 = 1/10 s）。
把 col②（链前）与 col⑤（链后）在**每个周期窗内**各做一次 DFT，逐周期求复数比
⇒ |H(k·0.1 Hz)| 就是**真·频率响应实测**，而且：

  ★ 与源频谱无关（比值把源自己的响应约掉）⇒ 不需要知道 AECG100 的输出平坦度；
  ★ 与"慢极点有没有收敛"**完全无关**（周期稳态下 LTI 的频响定义如此）
    ⇒ 它绕开了 "[6] 间隔 ≥10 s 才敢读 a1" 的全部争议。

于是 0.1 Hz 这一个点就能把整件事钉死：
  A+B 设计 98.07% ／ A+ 设计 89.33% ／ dc=0.03 约 94% ／ dc=0.012 约 99%
  ⇒ 实测落在哪一档，就说明**板内实际跑的是哪条链 + dc 实际是多少**。

另附：
  [3] 尾段单极点拟合 ⇒ 直接读 τ = 1/(2π·Σf)，与设计 τ(0.02 Hz) = 7.958 s 对照；
  [4] a1 多口径读数（尾起点／对称窗／单脉冲换算）＋ ±100 µV 判限对照。

用法
----
  python _t88_freqresp.py --file "xxx.txt" [--prof A+B宏] [--ref A+宏] [--fig] [--out FILE]
"""

import argparse
import importlib.util
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(s=''):
    OUT.append(s)
    print(s)


def _imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


t85 = _imp('t85', os.path.join(HERE, '_t85_lowfreq_square.py'))
m = sys.modules['t51']                 # t85 已把 _t51_yy0885_pulse 注册为 't51'

FS = t85.FS          # 853.2423 算法域
FS_T = t85.FS_T      # 500 实测 txt 行率
UV = t85.UV          # 0.1122 µV/code
LIM = 100.0          # 判据 a 的 ±100 µV 判限


# ------------------------------------------------------------------ 小工具

def smooth(v, fc=40.0):
    from scipy.signal import butter, filtfilt
    b, a = butter(4, fc / (FS_T / 2.0), 'low')
    return filtfilt(b, a, v)


def rising_edges(x, n_min=1000):
    """col② 上升沿（阈值 50% 幅度）。"""
    amp = float(np.percentile(x, 99.9) - np.median(x))
    thr = np.median(x) + 0.5 * amp
    up = x > thr
    out = [k for k in range(1, len(x)) if up[k] and not up[k - 1]]
    # 去抖动（同一次上升若被噪声切成两段）
    keep = []
    for k in out:
        if not keep or k - keep[-1] > n_min // 4:
            keep.append(k)
    return keep


def falling_edges(x, starts, w_nom=100):
    """每个上升沿对应的下降沿（在 [s+0.5w, s+2w] 内找 col② 跌到 50% 的点）。"""
    amp = float(np.percentile(x, 99.9) - np.median(x))
    thr = np.median(x) + 0.5 * amp
    fe = []
    for s in starts:
        a, b = s + int(0.5 * w_nom), s + int(2.5 * w_nom)
        b = min(b, len(x) - 1)
        k = next((i for i in range(a, b) if x[i] < thr), None)
        fe.append(k)
    return fe


def dft_ratio(x, y, starts, N, k):
    """逐周期 k 次谐波复数比 ⇒ (|H| 中位, 最小, 最大, 幅相, |X_k|中位, |Y_k|中位)。"""
    mags, phs, xs_, ys_ = [], [], [], []
    n = np.arange(N)
    w = np.exp(-2j * np.pi * k * n / N)
    for s in starts:
        if s + N > len(x):
            continue
        xs = x[s:s + N] - np.mean(x[s:s + N])
        ys = y[s:s + N] - np.mean(y[s:s + N])
        Xk = complex((xs * w).sum())
        Yk = complex((ys * w).sum())
        if abs(Xk) <= 0:
            continue
        r = Yk / Xk
        mags.append(abs(r))
        phs.append(math.degrees(np.angle(r)))
        xs_.append(abs(Xk) * 2.0 / N)
        ys_.append(abs(Yk) * 2.0 / N)
    if not mags:
        return (float('nan'),) * 6
    mags, phs = np.array(mags), np.array(phs)
    return (float(np.median(mags)), float(mags.min()), float(mags.max()),
            float(np.median(phs)), float(np.median(xs_)), float(np.median(ys_)))


def phase_of(x, yv, starts, N, k):
    """逐周期复数比的相位（度）取中位 —— 与 dft_ratio 同一口径，供「模型过同一管线」对照。"""
    phs = []
    n = np.arange(N)
    w = np.exp(-2j * np.pi * k * n / N)
    for s in starts:
        if s + N > len(x):
            continue
        xs = x[s:s + N] - np.mean(x[s:s + N])
        ys = yv[s:s + N] - np.mean(yv[s:s + N])
        Xk, Yk = complex((xs * w).sum()), complex((ys * w).sum())
        if abs(Xk) > 0:
            phs.append(math.degrees(np.angle(Yk / Xk)))
    return float(np.median(phs)) if phs else float('nan')


def fit_single_pole(t, v, taus=None, tmin=0.30, tmax=9.0, curve=None):
    """拟合 v(t) = yinf + C·exp(-t/τ)：τ 网格 + 给定 τ 下线性最小二乘 (yinf,C)。
    返回 (τ, C, yinf, 残差RMS)；curve 传入 dict 则把 (taus, rss) 曲线也存进去（供出图）。"""
    if taus is None:
        taus = np.exp(np.linspace(math.log(0.15), math.log(200.0), 600))
    s = (t >= tmin) & (t <= tmax)
    tt, vv = t[s], v[s]
    if len(tt) < 20:
        return (float('nan'),) * 4
    best, rss_list = None, []
    for tau in taus:
        A = np.column_stack([np.ones_like(tt), np.exp(-tt / tau)])
        coef, res, *_ = np.linalg.lstsq(A, vv, rcond=None)
        r = vv - A @ coef
        rss = float(np.mean(r ** 2))
        rss_list.append(rss)
        if best is None or rss < best[0]:
            best = (rss, tau, coef[1], coef[0])
    if curve is not None:
        curve['taus'], curve['rss'] = taus, np.array(rss_list)
    rss, tau, C, yinf = best
    return float(tau), float(C), float(yinf), float(math.sqrt(rss))


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--prof', default='ECG_PROFILE_DUAL_A_PLUS_B')
    ap.add_argument('--ref', default='ECG_PROFILE_DUAL_A_PLUS')
    ap.add_argument('--fmax', type=float, default=2.0, help='谐波域最高频率（Hz）')
    ap.add_argument('--fig', action='store_true')
    ap.add_argument('--tag', default='T88')
    ap.add_argument('--fig-out', default=None, dest='fig_out')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(HERE, a.file)
    name = os.path.basename(path)

    arr, ncol, bad_rc, bad_w, where = t85.load(path)
    n = len(arr)
    c1, c2, c3, c4, c5, c6 = (arr[:, i] for i in range(6))
    x = c2 * UV          # 链前（AFE 原始码 → µV）
    y = c5 * UV          # 链后（设备输出）

    dc = t85.read_dc_fc(a.prof)
    ref_dc = t85.read_dc_fc(a.ref)
    secs, n_notch, n_bp = t85.build_chain(a.prof)
    rsecs, rn, rbp = t85.build_chain(a.ref)
    rkinds = [m.section_kind(s) for s in rsecs]
    kinds = [m.section_kind(s) for s in secs]

    p('=' * 122)
    p('T88 · 低频方波「谐波域频率响应」实测 ＋ 尾段极点拟合   file = %s' % name)
    p('  被测 %s：%d 节（%d×LP ＋ %d×HP）＋ dc_block %.3f Hz'
      % (a.prof, len(secs), kinds.count('LP'), kinds.count('HP'), dc))
    p('  对照 %s：%d 节（%d×LP ＋ %d×HP）＋ dc_block %.3f Hz'
      % (a.ref, len(rsecs), rkinds.count('LP'), rkinds.count('HP'), ref_dc))
    p('=' * 122)

    # ---------------- [0] 身份
    p('')
    p('[0] 数据身份（简）')
    p('  行数 %d ｜ 时长 %.2f s ｜ 坏行：列数≠7 %d ｜ 量程异常 %d'
      % (n, (n - 1) / FS_T, bad_rc, bad_w))
    c0 = c1 if float(c1.max() - c1.min()) > 0 else c3
    cnm = '列①(DRDY 853)' if float(c1.max() - c1.min()) > 0 else '列③(idxfilter 500)'
    cfs = FS if cnm.startswith('列①') else FS_T
    d = np.diff(c0).astype(int)
    p('  时钟列 %s ｜ 跨度 %.0f ｜ 行率 %.2f Hz ｜ 增量分布 %s ｜ 增量≤0 处 %d'
      % (cnm, c0[-1] - c0[0], cfs * n / float(c0[-1] - c0[0]),
         dict(zip(*[z.tolist()[:6] for z in np.unique(d, return_counts=True)])),
         int(np.sum(d <= 0))))
    dtc = float(np.median(c6))
    p('  dtc 中位 %.0f ⇒ 执行节数 (%.0f−%.2f)/%.0f = %.2f ⇒ %s'
      % (dtc, dtc, t85.DTC_B, t85.DTC_K, (dtc - t85.DTC_B) / t85.DTC_K,
         a.prof if abs((dtc - t85.DTC_B) / t85.DTC_K - n_bp) < abs((dtc - t85.DTC_B) / t85.DTC_K - rbp) else a.ref))

    # ---------------- [1] 周期网格
    p('')
    p('[1] 周期网格（谐波基频 ＝ 1/周期）')
    st = rising_edges(x)
    fe = falling_edges(x, st)
    st = [s for s, e in zip(st, fe) if e is not None]
    fe = [e for e in fe if e is not None]
    dper = np.diff(st)
    P = int(round(float(np.median(dper))))
    p('  上升沿 %d 个 ｜ 周期（样本）%s ⇒ **P = %d 样本 = %.4f s ⇒ 基频 %.4f Hz**'
      % (len(st), dper.tolist(), P, P / FS_T, FS_T / P))
    p('  脉宽（col② 50%% 口径）%.1f ms ｜ 占空比 d = %.4f' % (1000 * (fe[0] - st[0]) / FS_T, (fe[0] - st[0]) / float(P)))
    nper = int(np.sum([s + P <= n for s in st]))
    p('  可用的**完整周期** %d 个（每周期取 %d 样本窗 ⇒ 覆盖 1 个完整周期）' % (nper, P))
    starts = [s for s in st if s + P <= n][:nper]
    p('  ★谐波 k 次 = %.4f Hz × k；DTF 窗 = 整周期 ⇒ 无泄漏' % (FS_T / P))

    # ---------------- [1b] 模型复放（供 [2] 相位对照 与 [3][4] 用）
    yb = t85.replay_periodic(x, secs, dc, P=P, warm=int(max(8, math.ceil(10 * 8.0 / (P / FS_T)))))
    yr = t85.replay_periodic(x, rsecs, ref_dc, P=P, warm=int(max(8, math.ceil(10 * 8.0 / (P / FS_T)))))
    models = [(a.prof, dc, yb, secs), (a.ref, ref_dc, yr, rsecs)]

    # ---------------- [2] 谐波域实测传函
    p('')
    p('[2] ★★ 谐波域实测传函 |H| = |Y_k| / |X_k|（col⑤/col②，逐周期 DFT 复数比取中位）')
    p('  ★ 与源频谱无关（比值把源自己约掉）；★ 与"慢极点是否收敛"无关（周期稳态定义）')
    p('  %-9s %10s %10s %19s %11s %11s %9s %10s %10s %9s'
      % ('f Hz', '|X_k| µV', '|Y_k| µV', '|H|实测 %(最小~最大)', '设计A+B %', '设计A+ %',
         'Δpp(A+B)', '相位实测°', '相位设计°', 'Δ°'))
    rows = []
    k = 1
    kk = int(round(a.fmax * P / FS_T))
    while k <= kk:
        f = k * FS_T / P
        mg, mn, mx, ph, Xa, Ya = dft_ratio(x, y, starts, P, k)
        Hs = abs(t85.H_chain(secs, dc, f)) * 100
        Hr = abs(t85.H_chain(rsecs, ref_dc, f)) * 100
        phd = math.degrees(np.angle(t85.H_chain(secs, dc, f)))
        p('  %-9.4f %10.1f %10.1f %10.2f(%.2f~%.2f) %11.2f %11.2f %9.2f %10.2f %10.2f %9.2f'
          % (f, Xa, Ya, mg * 100, mn * 100, mx * 100, Hs, Hr, mg * 100 - Hs, ph, phd, ph - phd))
        rows.append((f, mg * 100, Hs, Hr, ph, phd))
        k += 1
    p('  ⇒ 判据：实测若整条贴着「设计A+B」⇒ 板内就是 A+B；若贴「设计A+」⇒ 还是 A+。')
    p('     ★0.1 Hz 一点的分辨力最大：A+B 98.07%% vs A+ 89.33%% ⇒ 差 8.7 pp（远超噪声）')

    # ---------------- [2b] 相位口径反解 dc 截止频率（比 τ 拟合灵敏得多）
    p('')
    p('[2b] ★★ 相位口径反解低端极点（fc）—— 比 τ 拟合灵敏 ~30 倍')
    p('  依据：一阶 HP 在 f 处的相位 φ = 90° − atan(f/fc) ⇒ fc = f / tan(90° − φ)')
    p('  ⚠ 反解前必须先扣掉**管线系统偏差**：把模型复放当"待测数据"过同一套 DFT ⇒ 得到模型相位；')
    p('     模型相位 − 设计相位 = 管线/resample 的固有偏差（实测约 +0.1°，与 interp 抽取相位有关）。')
    p('  %-9s %12s %12s %12s %11s %13s' % ('f Hz', '实测 φ °', '模型 φ °', '设计 φ °',
                                           '管线偏差 °', '反解 fc Hz'))
    for (f, mg, Hs, Hr, ph, phd) in rows[:3]:
        k_ = int(round(f * P / FS_T))
        phm = phase_of(x, yb, starts, P, k_)
        bias = phm - phd
        fc_inv = f / math.tan(math.radians(90.0 - (ph - bias))) if abs(ph - bias) < 89.0 else float('nan')
        p('  %-9.4f %12.2f %12.2f %12.2f %11.2f %13.5f' % (f, ph, phm, phd, bias, fc_inv))
    p('  ⇒ 分辨力估算：逐周期相位散布 ≈ ±0.15° ⇒ 在 0.1 Hz 上 δfc ≈ 0.15/555 ≈ **±0.0003 Hz**')
    p('     （0.1 Hz 处 dφ/dfc ≈ −5.6×10² °/Hz；对照：τ 拟合只能给到 ±10~20%）')

    # ---------------- [3] 尾段极点拟合
    p('')
    p('[3] 尾段单极点拟合（直接读 τ = 1/(2π·Σf)；拟合窗 = 下落沿后 +0.30~+9.0 s）')

    def tail_of(v, fe_list, P):
        """把各周期尾段拼起来（相对下落沿的 t'）"""
        tt, vv = [], []
        for e in fe_list:
            if e + P - int(0.05 * FS_T) > len(v):
                continue
            seg = v[e:e + P - int(0.05 * FS_T)]
            tt.append(np.arange(len(seg)) / FS_T)
            vv.append(seg)
        return np.concatenate(tt), np.concatenate(vv)

    te, ve = tail_of(y, fe, P)
    CURVES = {}
    rows_fit = [('实测 col⑤',) + fit_single_pole(te, ve, curve=CURVES.setdefault('实测 col⑤', {}))]
    for nm, dcf, yv, sc in models:
        # 模型输出与实测相位对齐：用各自上升沿
        fv = rising_edges(yv)
        fv = falling_edges(yv, fv)
        fv = [e for e in fv if e is not None]
        tm, vm = tail_of(yv, fv, P)
        rows_fit.append((nm,) + fit_single_pole(tm, vm, curve=CURVES.setdefault(nm, {})))
    p('  %-34s %9s %11s %12s %11s' % ('曲线', 'τ s', 'fc Hz', 'C(尾起点) µV', '残差RMS µV'))
    for nm, tau, C, yinf, rms in rows_fit:
        p('  %-34s %9.3f %11.5f %12.1f %11.2f' % (nm[:34], tau, 1.0 / (2 * math.pi * tau), C, rms))
    p('  ⇒ 设计 τ：被测档（Σf=%.5f Hz）τ = **%.3f s**／对照档（A+ 的 Σf≈0.18069 Hz）τ_eff ≈ %.3f s'
      % (dc, 1.0 / (2 * math.pi * dc), 1.0 / (2 * math.pi * 0.18069)))
    p('  ★τ 是**独立于 dtc** 的第二把尺子：dtc 证明"节数对了"，τ 证明"那条 dc 截止频率真的在跑"。')
    # 周期均值检核（dc_block 必使周期均值 = 0）
    means = [float(np.mean(y[s:s + P])) for s in starts]
    p('  周期均值检核（直流阻断 ⇒ 每周期均值应 ≈ 0）：中位 %+.2f µV ｜ 最大 |均值| %.2f µV ⇒ %s'
      % (float(np.median(means)), float(np.max(np.abs(means))),
         '✓ 直流阻断生效' if np.max(np.abs(means)) < 5 else '⚠ 偏大'))

    # ---------------- [4] a1 多口径
    p('')
    p('[4] a1 读数（判据 a 的靶心；★三条口径并列，因为"何时读基线"标准未规定）')
    p('  R1 尾起点（下落沿后 t→0+ 的拟合外推，**∝Σf 的真判别量**）')
    p('  R2 对称窗（脉后 +1.0~+1.95 s 中位 − 脉前 −1.95~−1.0 s 中位）——10 s 周期下最直观的"偏移"')
    p('  R3 R1 的单脉冲换算（C_periodic × (1−e^(−P/τ))，＝真正"单发一个脉冲"时的偏移）')
    p('  %-34s %12s %12s %12s %14s' % ('曲线', 'R1 µV', 'R2 µV', 'R3 µV', 'max|尾| µV'))

    def r2(v, fe_list, P):
        posts, pres = [], []
        for e in fe_list:
            a0, b0 = e + int(1.0 * FS_T), e + int(1.95 * FS_T)
            a1_, b1_ = e + int((P / FS_T - 1.95) * FS_T), e + int((P / FS_T - 1.0) * FS_T)
            if b1_ >= len(v):
                continue
            posts.append(float(np.median(v[a0:b0])))
            pres.append(float(np.median(v[a1_:b1_])))
        return (float(np.median(posts)) - float(np.median(pres))) if posts else float('nan')

    def rmax(v, fe_list, P):
        mx = [float(np.max(np.abs(v[e + int(0.05 * FS_T):e + int(0.95 * FS_T)]))) for e in fe_list
              if e + int(0.95 * FS_T) < len(v)]
        return float(np.median(mx)) if mx else float('nan')

    for (nm, tau, C, yinf, rms), v, f_l in [(rows_fit[0], y, fe)] + \
            [(rf, yv, [e for e in falling_edges(yv, rising_edges(yv)) if e is not None])
             for rf, (nm2, dcf, yv, sc) in zip(rows_fit[1:], models)]:
        E = math.exp(-(100.0 / FS_T) / tau)
        Pp = P / FS_T
        F = math.exp(-(Pp - 100.0 / FS_T) / tau)
        vp = F * (1 - E) / (1 - E * F)          # v_pre / A
        fac = 1.0 / (1.0 + vp * E / (1 - E))    # C_single = C_periodic × fac
        p('  %-34s %12.1f %12.1f %12.1f %14.1f'
          % (nm[:34], C, r2(v, f_l, P), C * fac, rmax(v, f_l, P)))
    p('  ⇒ 判限 **±100 µV**。★A+ 的尾起点应 ≈ −345 µV（✗）；A+B 应 ≈ −38 µV（✓）⇒ 两者差 9 倍，')
    p('     即便 10 s 周期让"对称窗"读数缩水 ~1.5 倍，**判 ✓/✗ 的结论不受影响**。')

    # ---------------- [4b] a2 斜率 / a3 过冲
    p('')
    p('[4b] a2 斜率（限 300 µV/s）／ a3 过冲（限 10%）')
    p('  a2 = 脉冲终点后尾段的**初始斜率** |C|/τ（拟合口径；逐样本斜率被噪声淹没 ⇒ 不可用）')
    for nm, tau, C, yinf, rms in rows_fit:
        p('    %-34s a2 ≈ %.1f µV/s（限 300 ⇒ 用量 %.1f%%）'
          % (nm[:34], abs(C) / tau, 100 * abs(C) / tau / 300.0))
    # a3：脉顶 vs 平台
    pl = [float(np.median([y[k + int(round(q * FS_T))] for q in (0.005, 0.010, 0.015, 0.020)]))
          for k in st if k + int(0.05 * FS_T) < len(y)]
    pks = [float(np.max(y[k:k + int(0.03 * FS_T)])) for k in st if k + int(0.03 * FS_T) < len(y)]
    amp_x = float(np.median([x[k:s2 + 1].max() for k, s2 in
                             zip(st, [k + int(0.1 * FS_T) for k in st]) if s2 < len(x)])) - float(np.median(x))
    pl5, pk5 = float(np.median(pl)), float(np.median(pks))
    p('    a3（col⑤ 脉顶 %.1f − 平台 %.1f）/ 源幅度 %.1f = **%.2f%%**（限 10%%）'
      % (pk5, pl5, amp_x, 100 * (pk5 - pl5) / amp_x))
    p('    ⚠ 500 Hz 采样域上「脉顶」只落在 1 个样本上 ⇒ a3 只能**粗读**（T79 已立此结论）；')
    p('      理论参照：4 阶 Butterworth 阶跃过冲 ≈ 10.9%（A+ 实测 6.7~8.7% 贴限）／2 阶 ≈ 4.3% ⇒ A+B 显著改善 ✓')

    # ---------------- [5] 定版判词
    p('')
    p('[5] 定版判词（本脚本自动）')
    f1 = rows[0] if rows else (float('nan'),) * 4
    d1_ = f1[1] - f1[2]
    d2_ = f1[1] - f1[3]
    p('  0.1 Hz 实测 %.2f%% ｜ 设计 A+B %.2f%%（Δ %+.2f pp）｜ 设计 A+ %.2f%%（Δ %+.2f pp）'
      % (f1[1], f1[2], d1_, f1[3], d2_))
    p('  ⇒ %s' % ('✅ 与 A+B 一致（Δ < 1 pp）⇒ 落码生效、低端 0.02 Hz 真的在跑'
                  if abs(d1_) < 1.0 and abs(d1_) < abs(d2_) else
                  '🛑 与 A+B 不符 ⇒ 复核档位/dc 宏'))
    p('  尾段 τ 实测 %.3f s（= %.5f Hz）｜ 设计 %.3f s（%.3f Hz）⇒ 比值 %.3f'
      % (rows_fit[0][1], 1.0 / (2 * math.pi * rows_fit[0][1]),
         1.0 / (2 * math.pi * dc), dc, rows_fit[0][1] and (1.0 / (2 * math.pi * rows_fit[0][1])) / dc))

    if a.fig:
        make_fig(name, x, y, st, fe, P, rows, rows_fit, te, ve, models, secs, dc, ref_dc, a, rsecs, CURVES)

    if a.out:
        with open(os.path.join(HERE, a.out), 'w', encoding='utf-8') as f:
            f.write('\n'.join(OUT) + '\n')
        print('\n[写入 %s]' % a.out)


def make_fig(name, x, y, st, fe, P, rows, rows_fit, te, ve, models, secs, dc, ref_dc, a, rsecs, curves):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    # (a) 谐波域实测 vs 设计
    fr = np.logspace(-1.3, 0.5, 300)
    ax[0, 0].semilogx(fr, [20 * np.log10(abs(t85.H_chain(secs, dc, f))) for f in fr],
                      '-', color='#c00', lw=2.0, label='设计 %s' % a.prof[:22])
    ax[0, 0].semilogx(fr, [20 * np.log10(abs(t85.H_chain(rsecs, ref_dc, f))) for f in fr],
                      '--', color='#06c', lw=1.6, label='设计 %s' % a.ref[:22])
    ax[0, 0].semilogx([r[0] for r in rows], [20 * math.log10(r[1] / 100) for r in rows],
                      'o', ms=7, mfc='none', mec='k', mew=1.8, label='实测（谐波域 DFT 比）')
    if rows:
        f0_, mg0 = rows[0][0], rows[0][1]
        ax[0, 0].annotate('%.2f%%' % mg0, (f0_, 20 * math.log10(mg0 / 100)),
                          textcoords='offset points', xytext=(8, 10), fontsize=10)
        ax[0, 0].annotate('设计 %.2f%%' % rows[0][2], (f0_, 20 * math.log10(rows[0][2] / 100)),
                          textcoords='offset points', xytext=(8, -16), fontsize=9, color='#c00')
    ax[0, 0].set_xlabel('Hz'); ax[0, 0].set_ylabel('|H| dB'); ax[0, 0].grid(alpha=.3, which='both')
    ax[0, 0].set_title('低频段传函：实测 vs 设计（谐波域，绕开「收敛」问题）')
    ax[0, 0].legend(fontsize=8, loc='lower right')

    # (b) 尾段线性轴：实测 vs 两档模型
    ax[0, 1].plot(te[:int(9.0 * FS_T)], ve[:int(9.0 * FS_T)], lw=2.2, color='#c00',
                  alpha=.85, label='实测 col⑤')
    for nm, dcf, yv, sc in models:
        fv = [e for e in falling_edges(yv, rising_edges(yv)) if e is not None]
        if not fv:
            continue
        e0 = fv[0]
        seg = yv[e0:e0 + int(9.0 * FS_T)]
        ax[0, 1].plot(np.arange(len(seg)) / FS_T, seg, '--', lw=1.3, label=nm[:26])
    ax[0, 1].axhline(100, color='k', ls=':', lw=1.0)
    ax[0, 1].axhline(-100, color='k', ls=':', lw=1.0)
    ax[0, 1].text(6.6, 108, '±100 µV 判限', fontsize=8)
    ax[0, 1].set_ylim(-470, 170)
    ax[0, 1].set_xlabel('下落沿后 t (s)'); ax[0, 1].set_ylabel('µV')
    ax[0, 1].set_title('单周期尾段：实测 vs 两档模型（判据 a 靶心；4 周期拼接）')
    ax[0, 1].legend(fontsize=8); ax[0, 1].grid(alpha=.3)

    # (c) τ 拟合的不确定度（RSS 曲线）
    for nm, tau, C, yinf, rms in rows_fit:
        cu = curves.get(nm)
        if cu is None:
            continue
        ax[1, 0].semilogx(cu['taus'], cu['rss'], lw=1.5, label='%s（τ*=%.2f s）' % (nm[:18], tau))
    ax[1, 0].axvline(1.0 / (2 * math.pi * dc), color='#c00', ls=':', lw=1.2,
                     label='设计 τ = %.2f s（%.3f Hz）' % (1 / (2 * math.pi * dc), dc))
    ax[1, 0].set_xlabel('候选 τ (s)'); ax[1, 0].set_ylabel('拟合残差 MSE (µV²)')
    ax[1, 0].set_ylim(0, 5000)
    ax[1, 0].set_title('τ 的不确定度：残差曲线越平 = 越读不准（实测长尾，曲线平坦）')
    ax[1, 0].legend(fontsize=8); ax[1, 0].grid(alpha=.3, which='both')

    # (d) 脉沿细节
    s0 = st[1] if len(st) > 1 else st[0]
    x0 = float(np.median(x))
    sl = slice(s0 - int(0.05 * FS_T), s0 + int(0.25 * FS_T))
    tt2 = (np.arange(sl.start, sl.stop) - s0) * 1000.0 / FS_T
    ax[1, 1].plot(tt2, x[sl] - x0, lw=1.2, color='#888', label='列② 链前（已去 681 mV 直流）')
    ax[1, 1].plot(tt2, y[sl], lw=1.8, color='#c00', label='列⑤ 链后')
    ax[1, 1].axhline(100, color='k', ls=':', lw=.9)
    ax[1, 1].axhline(-100, color='k', ls=':', lw=.9)
    for g in (0, 100):
        ax[1, 1].axvline(g, color='k', ls=':', lw=.8)
    ax[1, 1].set_xlabel('ms（相对上升沿）'); ax[1, 1].set_ylabel('µV')
    ax[1, 1].set_title('脉冲全景（0~100 ms 平台 + 下落沿尾起点）')
    ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3)
    fig.suptitle('%s 谐波域频响 + 尾段极点 · %s' % (a.tag, name[:66]), fontsize=11)
    fig.tight_layout()
    o = a.fig_out or os.path.join(HERE, '%s_频响与尾段_2026-09-21.png' % a.tag)
    fig.savefig(o, dpi=130)
    print('[图 %s]' % os.path.basename(o))


if __name__ == '__main__':
    main()
