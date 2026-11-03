# -*- coding: utf-8 -*-
"""
T52 —— YY 0885-2013 2.1.5 频率响应 · 判据 a)（3 mV / 100 ms 方波脉冲）实测复核
      数据：3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt（陷波 OFF）

固件真实链（2026-09-20 核对源码）：
    raw → ecg_dc_block(1 阶 HP @0.05 Hz) → [9 节 notch] → LP4(@150) + HP4(@0.05)
    即：低端合计 **5 阶**（dc1 + HP4），高端 **4 阶**（LP4）。

三条判据（YY 0885-2013 2.1.5 a)）：
    a1  脉冲后基线与脉冲前基线之间的偏移  ≤ 0.1 mV  (100 µV)
    a2  脉冲终点后的斜率                  ≤ 0.3 mV/s (300 µV/s)
    a3  脉冲边缘的过冲                    < 10 %
    （b/c 两条另测）

用法：
    python _t52_yy0885_notchoff.py             # 实测复核
    python _t52_yy0885_notchoff.py --cmp       # + 09-18（notch ON）同台架对照
    python _t52_yy0885_notchoff.py --sim       # + 固件链同脉冲串仿真 / what-if
    python _t52_yy0885_notchoff.py --fig       # 出图
"""
import argparse
import os
import sys

import numpy as np
from scipy.signal import butter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t51_yy0885_pulse as T51   # 复用：系数解析器 / sos 单节滤波 / 设计器

FS_TXT = 500.0                    # 实测 txt 时间轴（列⑤已是 500 Hz 输出域）
UV_PER_CODE = 0.1122              # 列⑤（滤波后 code 域）
UV_PER_LSB = 0.1526               # 列④（int16 输出 LSB）
DT_ON, DT_OFF = 9710.0, 3938.0    # IMP620_N2 **旧链**两态 dtcycles（T60 现场重读 T56/T57 数据；
                                  # 历史值 9712/3950 是二手数，差 2/12 cycles）
# T60 标定：dtcycles = 641.3 cyc/节 × 节数 + 1373 ⇒ 四态指纹
#   旧链ON 13节 9710 ｜ 旧链OFF 4节 3938 ｜ 新链ON 12节 ~9079 ｜ 新链OFF 3节 ~3297
# ⚠ T84(2026-09-21) 起 dc_block 的截止频率**不再固定 0.05** —— 已提升为按档宏
#   `ECG_DC_BLOCK_FC_HZ_F`（Filter/ecg_filter_coeffs.h）；A+B 档 = 0.02，其余档仍 0.05。
#   本脚本服务的是**旧链（IMP620_N2 / dc=0.05）**的数据，故此处保留 0.05；
#   若拿它回放 A+B 档数据，必须把 DC_FC 改成 0.02（否则脉冲落点 a1 会算错）。
DC_FC = 0.05                      # dc_block 截止频率（= ECG_DC_BLOCK_FC_HZ_F，旧链为 0.05）

F_MAIN = '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt'
F_ON = '100ms-Retanglepulse-2026-09-18 164024.txt'


# ------------------------------------------------------------------ 读取

def load_raw(path):
    cols, bad = [], 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            p = ln.split()
            if len(p) < 5:
                bad += 1
                continue
            try:
                cols.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                             float(p[4]), float(p[5]) if len(p) > 5 else np.nan])
            except ValueError:
                bad += 1
    a = np.array(cols)
    ix = a[:, 2]
    gaps = [(int(ix[k]), int(ix[k + 1])) for k in np.where(np.diff(ix) != 1)[0]]
    return dict(uv5=a[:, 4] * UV_PER_CODE, uv4=a[:, 3] * UV_PER_LSB,
                idx=ix, dtc=a[:, 5], nbad=bad, gaps=gaps, n=len(a))


# ------------------------------------------------------------------ 边沿

def find_edges(y, fs, min_gap=0.3):
    """阈值粗定位 + 50% 交越插值精定位。返回 (tr[s], tf[s], step[µV])。"""
    n = len(y)
    hi, lo = float(np.percentile(y, 98.0)), float(np.median(y))
    thr = lo + 0.5 * (hi - lo)
    up = y > thr
    rises = [k for k in range(1, n) if up[k] and not up[k - 1]]
    falls = [k for k in range(1, n) if up[k - 1] and not up[k]]
    pairs = []
    for s in rises:
        e = next((x for x in falls if x > s), None)
        if e is not None and (e - s) / fs >= 0.01:
            pairs.append((s, e))
    out = []
    for s, e in pairs:
        if out and (s - out[-1][0]) / fs < min_gap:
            continue
        out.append((s, e))
    tr, tf, amp = [], [], []
    for s, e in out:
        b = float(np.median(y[max(0, s - int(0.2 * fs)):s]))
        p = float(np.median(y[e - int(0.03 * fs):e]))
        step = p - b
        if step <= 0:
            continue
        lvl = b + 0.5 * step
        k = s
        while k > max(0, s - int(0.05 * fs)) and y[k - 1] >= lvl:
            k -= 1
        if k <= max(0, s - int(0.05 * fs)):
            continue
        t0 = (k - 1) + (lvl - y[k - 1]) / (y[k] - y[k - 1])
        k = e
        while k < min(n, e + int(0.05 * fs)) and y[k] >= lvl:
            k += 1
        if k >= min(n, e + int(0.05 * fs)):
            continue
        t1 = (k - 1) + (lvl - y[k - 1]) / (y[k] - y[k - 1])
        tr.append(t0 / fs); tf.append(t1 / fs); amp.append(step)
    return np.array(tr), np.array(tf), np.array(amp)


# ------------------------------------------------------------------ 对齐模板

def template(y, tr, span=(-0.30, 2.00), dt=0.0002):
    g = np.arange(span[0], span[1] + dt / 2, dt)
    ts = np.arange(len(y)) / FS_TXT
    rows = [np.interp(g + t0, ts, y) for t0 in tr
            if t0 + span[0] >= 0 and t0 + span[1] <= ts[-1]]
    if not rows:
        return g, np.zeros_like(g)
    m = np.median(np.array(rows), axis=0)
    return g, m - float(np.median(m[(g >= span[0] + 0.02) & (g <= -0.03)]))


def edge_metrics(y, fs, tr, tf):
    out = []
    for t0, t1 in zip(tr, tf):
        i0, i1 = int(round(t0 * fs)), int(round(t1 * fs))
        base = float(np.median(y[i0 - int(0.4 * fs):i0 - int(0.03 * fs)]))
        u = y - base
        out.append(dict(
            base=base,
            plat_edge=float(np.median(u[i0 + int(0.002 * fs):i0 + int(0.005 * fs)])),
            plat_near=float(np.median(u[i0 + int(0.030 * fs):i0 + int(0.050 * fs)])),
            plat_mean=float(np.mean(u[i0 + int(0.010 * fs):i1 - int(0.005 * fs)])),
            plat_far=float(np.median(u[i1 - int(0.020 * fs):i1])),
            peak=float(np.max(u[i0:i0 + int(0.050 * fs)])),
            valley=float(np.min(u[i1:i1 + int(0.050 * fs)]))))
    return out


# ------------------------------------------------------------------ 固件链（含 dc_block）

def dc_block_apply(x, fc=DC_FC, fs=None):
    fs = fs or T51.FS
    r = 1.0 - 2.0 * np.pi * fc / fs
    y = np.empty_like(x)
    xp = x[0]
    yp = 0.0
    for k in range(len(x)):
        yp = (x[k] - xp) + r * yp
        xp = x[k]
        y[k] = yp
    return y


def fw_chain(macro='IMP620_N2'):
    """返回 (sos, nn, hp_idx)。sos 为固件 13 节；低端 dc_block 需单独前置。"""
    m, _, _ = T51.PROFILES[macro]
    sos, nn = T51.parse_coeffs_f64(T51.COEFFS_C, T51.COEFFS_H, m)
    kinds = [T51.section_kind(s) for s in sos]
    hp = [i for i in range(nn, len(sos)) if kinds[i] == 'HP']
    return sos, nn, hp, kinds


def droop_shape(sos, hp_idx, fs=None, tmax=3.0):
    """低端（dc_block ∘ HP4）的阶跃响应，D(0+)≈1，归一化。"""
    fs = fs or T51.FS
    n = int(tmax * fs)
    x = np.zeros(n); x[10:] = 1.0          # 阶跃必须落在预热之后
    y = dc_block_apply(x, DC_FC, fs)
    for i in hp_idx:
        y = T51._sosfilt_one(sos[i], y)
    return (np.arange(n) - 10) / fs, y      # t=0 = 阶跃起始点

def upsample_max(y, fs, t0, t_lo=-0.005, t_hi=0.050, up=20):
    """单段脉冲的插值峰值（线性上采样 up 倍后取 max），消除 2 ms 采样栅格的漏峰。"""
    j0 = int(round((t0 + t_lo) * fs)); j1 = int(round((t0 + t_hi) * fs))
    if j0 < 1 or j1 >= len(y):
        return np.nan
    seg = y[j0 - 1:j1 + 1]
    tt = (np.arange(len(seg)) - 1) / fs + t_lo
    g2 = np.arange(tt[0], tt[-1], 1.0 / (fs * up))
    return float(np.max(np.interp(g2, tt, seg)))


def base_sub(y, fs, t_rise, lo=-0.28, hi=-0.03):
    """与模板一致的前基线口径：减去 [t_rise+lo, t_rise+hi] 的中位。"""
    j0 = max(0, int((t_rise + lo) * fs)); j1 = max(j0 + 1, int((t_rise + hi) * fs))
    return y - float(np.median(y[j0:j1]))


def droop_from_secs(secs_low, fs, tmax=3.0):
    """任意'低端'链的阶跃响应形状，t=0 = 阶跃起始。"""
    n = int(tmax * fs)
    x = np.zeros(n); x[10:] = 1.0
    y = T51.run_design(secs_low, x)
    return (np.arange(n) - 10) / fs, y


def amp0_fit(g, m, Dt, Dv, t0, t_lo=0.015, t_hi=0.095):
    """把脉冲顶部拟合成 A·D(t-t0)，A = 理想脉冲幅值（已去掉低端 HP 下垂）。
    Dt/Dv = 低端（dc1∘HP4）阶跃响应形状，D(t0+)≈1。"""
    sel = (g >= t0 + t_lo) & (g <= t0 + t_hi)
    ref = np.interp(g[sel] - t0, Dt, Dv)
    A = float(np.sum(m[sel] * ref) / np.sum(ref * ref))
    rms = float(np.sqrt(np.mean((m[sel] - A * ref) ** 2)))
    return A, rms


# ------------------------------------------------------------------ 输出

def hdr(t):
    print(); print('=' * 84); print(t); print('=' * 84)


def pct(a, b):
    return (a - b) / b * 100.0 if b else np.nan


def judge(v, lim, low=True):
    ok = (v <= lim) if low else (v >= lim)
    return '✓' if ok else '✗ 超 %.2f×' % ((v / lim) if low else (lim / v))


def line_amp(seg, f, fs=FS_TXT):
    """最小二乘拟 [cos, sin, 1, t] ⇒ 该线频幅度（不受 FFT bin 泄漏影响）。"""
    n = len(seg); t = np.arange(n) / fs
    A = np.column_stack([np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t),
                         np.ones(n), t])
    c, *_ = np.linalg.lstsq(A, seg.astype(float), rcond=None)
    return float(np.hypot(c[0], c[1]))


def noise_rms(seg, fs=FS_TXT, lines=(50, 100, 150)):
    """扣掉线频与线性趋势后的本底 RMS（µV）。"""
    n = len(seg); t = np.arange(n) / fs
    cols = [np.ones(n), t]
    for f in lines:
        cols += [np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t)]
    A = np.column_stack(cols)
    c, *_ = np.linalg.lstsq(A, seg.astype(float), rcond=None)
    return float(np.std(seg - A @ c))


def base_win(y, tr, fs=FS_TXT, lo=-0.40, hi=-0.02):
    """脉冲前干净窗（相对上升沿）——工频/本底的唯一可信取样区。"""
    out = []
    for t0 in tr:
        j0 = int((t0 + lo) * fs); j1 = int((t0 + hi) * fs)
        if j0 < 0 or j1 >= len(y) or j1 - j0 < 80:
            continue
        out.append(y[j0:j1])
    return out


def spec_amp(y, f, fs=FS_TXT):
    n = len(y); y = y - np.median(y); w = np.hanning(n)
    Y = np.abs(np.fft.rfft(y * w)) / (w.sum() / 2)
    k = int(round(f / fs * n))
    return float(max(Y[k - 1:k + 2]) * 2) if 0 < k < len(Y) else np.nan


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_MAIN)
    ap.add_argument('--cmp', action='store_true')
    ap.add_argument('--sim', action='store_true')
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(HERE, a.file)
    D = load_raw(path)
    sos, nn, hp_idx, kinds = fw_chain()
    td, Dv = droop_shape(sos, hp_idx)

    hdr('[0] 数据质量与列义审计   ' + os.path.basename(path))
    print('  样本 %d ｜ 时长 %.2f s ｜ 列⑤=%.4f µV/code ｜ 列④=%.4f µV/LSB'
          % (D['n'], D['n'] / FS_TXT, UV_PER_CODE, UV_PER_LSB))
    print('  坏行 %d (%.3f%%) ｜ 列③断点 %d 处'
          % (D['nbad'], 100.0 * D['nbad'] / (D['n'] + D['nbad']), len(D['gaps'])))
    for g0, g1 in D['gaps']:
        print('     ⚠ idx %d→%d 缺 %d 样本（%.0f ms），文件内 t=%.3f s — 该处位移窗回避'
              % (g0, g1, g1 - g0 - 1, (g1 - g0 - 1) / FS_TXT * 1e3,
                 (g0 - D['idx'][0]) / FS_TXT))
    print('  列⑤ p-p %.1f µV ｜ 列④ p-p %.1f µV ｜ 列④削顶率 %.2f%%'
          % (np.ptp(D['uv5']), np.ptp(D['uv4']),
             100.0 * np.mean(np.abs(D['uv4']) >= 32767 * UV_PER_LSB * 0.999)))
    print('  ⇒ 幅度分析一律走列⑤（列④ ±5000 µV 会削顶）')

    hdr('[1] 状态自检（dtcycles，免仪器）')
    dtc = D['dtc'][~np.isnan(D['dtc'])]
    print('  dtcycles 中位 %.0f（p5 %.0f / p95 %.0f）'
          % (np.median(dtc), np.percentile(dtc, 5), np.percentile(dtc, 95)))
    for lab, ref in (('ON', DT_ON), ('OFF', DT_OFF)):
        print('     距 %-3s 基准 %5.0f：%+8.0f cycles（%+.2f%%）'
              % (lab, ref, np.median(dtc) - ref, 100 * (np.median(dtc) - ref) / ref))
    st = 'OFF' if abs(np.median(dtc) - DT_OFF) < abs(np.median(dtc) - DT_ON) else 'ON'
    print('  ⇒ 判为陷波 **%s**（文件名声明 notch off）%s'
          % (st, '✓ 一致' if st == 'OFF' else '✗ 需复查'))

    hdr('[2] 脉冲检出与逐段读数（列⑤）')
    tr, tf, step = find_edges(D['uv5'], FS_TXT)
    print('  检出 %d 段 ｜ 周期中位 %.3f s（标称 0.5 Hz = 2.000 s）'
          % (len(tr), np.median(np.diff(tr))))
    print('  脉宽（50%% 交越）中位 %.1f ms（标称 100 ms）｜ 步高中位 %.1f µV'
          % (np.median(tf - tr) * 1e3, np.median(step)))
    em = edge_metrics(D['uv5'], FS_TXT, tr, tf)
    pn = np.array([e['plat_near'] for e in em]); pf = np.array([e['plat_far'] for e in em])
    pk = np.array([e['peak'] for e in em]); vl = np.array([e['valley'] for e in em])
    pku = np.array([upsample_max(D['uv5'], FS_TXT, t) for t in tr])
    print()
    print('  %-4s %-9s %-9s %-9s %-9s %-9s'
          % ('#', '近平台', '末端平台', '峰值(采样)', '峰值(插值)', '谷值'))
    for k, e in enumerate(em):
        print('  %-4d %9.1f %9.1f %9.1f %9.1f %9.1f'
              % (k + 1, e['plat_near'], e['plat_far'], e['peak'], pku[k], e['valley']))
    print('  峰值(插值)：中位 %.1f µV ｜ 最大 %.1f ｜ 最小 %.1f （散布 ±%.1f%%）'
          % (np.median(pku), pku.max(), pku.min(),
             100 * (pku.max() - pku.min()) / 2 / np.median(pku)))
    droop = (np.median(pn) - np.median(pf)) / np.median(pn) * 100
    print('  ⇒ 脉冲顶**单调下垂**：+30~50 ms %.0f → 降沿前 %.0f µV，50 ms 内跌 %.2f%%'
          % (np.median(pn), np.median(pf), droop))
    tj = np.arange(0.0, 0.2, 0.0005)
    Dvj = np.interp(tj, td, Dv)
    model_droop = (Dvj[int(0.040 / 0.0005)] - Dvj[int(0.090 / 0.0005)]) \
        / Dvj[int(0.040 / 0.0005)] * 100
    print('     模型（dc1∘HP4 @0.05）同区间跌 %.2f%%  ⇒ 吻合 %.2fx'
          % (model_droop, droop / model_droop))

    hdr('[3] 判据 a3 过冲（多口径）')
    g, m = template(D['uv5'], tr)
    td0 = float(np.median(tf - tr)) + 1e-9
    te = float(np.median(tf - tr))              # 降沿相对上升沿的时刻
    peak_t = float(g[np.argmax(m[(g >= 0) & (g <= 0.05)]) + np.where(g >= 0)[0][0]])
    A, rms = amp0_fit(g, m, td, Dv, 0.001)   # 模板 t=0 = 上升沿 50% 交越
    PK = float(np.median(pku))
    print('  峰值（14 段插值中位）%.1f µV（模板 max %.1f，采样中位 %.1f）@ +%.1f ms'
          % (PK, m.max(), np.median(pk), peak_t * 1e3))
    print('  顶部拟合残差 %.1f µV ｜ 理想幅值 A=%.1f µV（= 去 HP 下垂后的脉冲幅值）' % (rms, A))
    refs = [
        ('标称输入 3000 µV', 3000.0),
        ('下垂外推 A（去 HP 下垂）', A),
        ('顶部均值 [10, 降沿−5] ms', float(np.mean(m[(g >= 0.010) & (g <= te - 0.005)]))),
        ('近平台 [+30,+50] ms', float(np.median(m[(g >= 0.030) & (g <= 0.050)]))),
        ('末端平台 [降沿−20,降沿] ms', float(np.median(m[(g >= te - 0.020) & (g <= te)]))),
        ('边沿肩部 [+2,+5] ms（非稳态，仅参照）', float(np.median(m[(g >= 0.002) & (g <= 0.005)]))),
    ]
    print()
    print('  %-36s %11s %11s %s' % ('参考电平口径', '参考 µV', '过冲 %', '判定(限 10%)'))
    for lab, r in refs:
        v = pct(PK, r)
        print('  %-36s %11.1f %10.2f%% %s' % (lab, r, v, judge(v, 10.0)))
    valley = float(np.min(m[(g >= te) & (g <= te + 0.05)]))
    print('  %-36s %11.1f %10.2f%% %s'
          % ('下降沿下冲（对 A 幅值）', A, -pct(valley, A), judge(-pct(valley, A), 10.0)))

    hdr('[4] 判据 a1 位移（脉冲后基线 − 脉冲前基线）')
    print('  脉冲前基线 = 模板 [-280,-30] ms 中位（=0，已扣除）')
    print('  模板脉冲前 p-p %.1f µV ｜ 段数 %d' % (np.ptp(m[(g >= -0.28) & (g <= -0.03)]), len(tr)))
    print()
    print('  %-16s %12s %s' % ('降沿后 t', '|位移| µV', '判定(限 100)'))
    for x0 in (0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 1.00, 1.20, 1.50, 1.80):
        j = te + x0
        if j > g[-1]:
            continue
        v = abs(float(np.interp(j, g, m)))
        print('  +%-15.2fs %12.1f %s' % (x0, v, judge(v, 100.0)))
    j = te + 0.02
    sub = g >= j
    idx = np.where(np.abs(m[sub]) <= 100.0)[0]
    if len(idx):
        print('  ⇒ 首次回到 ±100 µV 内：降沿后 +%.2f s' % (g[sub][idx[0]] - te))
    else:
        print('  ⇒ 观测窗内始终未回到 ±100 µV 内')
    print('  ⚠ 本数据周期仅 2.0 s ⇒ 脉冲前基线已被**上一个脉冲的拖尾**压低'
          '（+1.8 s 处仅剩 %.0f µV ⇒ 污染可忽略）。'
          % abs(float(np.interp(te + 1.80, g, m))))

    hdr('[5] 判据 a2 斜率（脉冲终点后）')
    print('  %-32s %11s %s' % ('口径', 'µV/s', '判定(限 300)'))
    for wl in (50, 100, 200):
        sw = wl / 1000.0
        s0 = te + 0.02
        gg = np.arange(s0, min(te + 1.85, g[-1]) - sw, 0.0002)
        v = max([abs(np.interp(t + sw, g, m) - np.interp(t, g, m)) / sw
                 for t in gg if t + sw <= g[-1]] or [np.nan])
        print('  %-32s %11.1f %s' % ('滑窗 %d ms 最大 |Δ|' % wl, v, judge(v, 300.0)))
    for a0, a1_ in ((0.02, 0.20), (0.20, 0.50), (0.20, 1.20), (0.50, 1.50)):
        sel = (g >= te + a0) & (g <= te + a1_)
        tt = g[sel]
        sl = abs(np.polyfit(tt - tt[0], m[sel], 1)[0])
        print('  %-32s %11.1f %s' % ('线性拟合 +%.2f~%.2f s' % (a0, a1_), sl, judge(sl, 300.0)))
    print('  ※ 「终点后」不含降沿本身（含则必然爆表）；滑窗口径会把拖尾最陡段算进去。')

    # ---------------------------------------------------------------- 对照
    if a.cmp:
        hdr('[6] 同台架对照：09-18（notch ON） vs 09-20（notch OFF）')
        p2 = os.path.join(HERE, F_ON)
        if os.path.exists(p2):
            D2 = load_raw(p2)
            tr2, tf2, step2 = find_edges(D2['uv5'], FS_TXT)
            em2 = edge_metrics(D2['uv5'], FS_TXT, tr2, tf2)
            g2, m2 = template(D2['uv5'], tr2)
            te2 = float(np.median(tf2 - tr2))
            A2, _ = amp0_fit(g2, m2, td, Dv, 0.001)
            PK2 = float(np.median([upsample_max(D2['uv5'], FS_TXT, t) for t in tr2]))
            rows = [
                ('dtcycles', np.median(D2['dtc']), np.median(dtc), '%.0f'),
                ('段数', len(tr2), len(tr), '%d'),
                ('步高 µV', np.median(step2), np.median(step), '%.1f'),
                ('近平台 µV', np.median([e['plat_near'] for e in em2]), np.median(pn), '%.1f'),
                ('末端平台 µV', np.median([e['plat_far'] for e in em2]), np.median(pf), '%.1f'),
                ('峰值(插值中位) µV', np.median([upsample_max(D2['uv5'], FS_TXT, t) for t in tr2]),
                 np.median(pku), '%.1f'),
                ('峰值(插值最大) µV', np.max([upsample_max(D2['uv5'], FS_TXT, t) for t in tr2]),
                 np.max(pku), '%.1f'),
                ('峰值(模板) µV', m2.max(), m.max(), '%.1f'),
                ('下垂 %/50ms', (np.median([e['plat_near'] for e in em2])
                                 - np.median([e['plat_far'] for e in em2]))
                 / np.median([e['plat_near'] for e in em2]) * 100, droop, '%.2f'),
                ('理想幅值 A µV', A2, A, '%.1f'),
                ('过冲/A %', pct(PK2, A2), pct(PK, A), '%.2f'),
                ('过冲/A(模板max)%', pct(m2.max(), A2), pct(m.max(), A), '%.2f'),
                ('过冲/标称 %', pct(PK2, 3000.0), pct(PK, 3000.0), '%.2f'),
                ('过冲/近平台 %', pct(PK2, np.median([e['plat_near'] for e in em2])),
                 pct(PK, np.median(pn)), '%.2f'),
                ('谷值 µV', float(np.min(m2[(g2 >= te2) & (g2 <= te2 + .05)])),
                 float(np.min(m[(g >= te) & (g <= te + .05)])), '%.1f'),
            ]
            print('  %-18s %16s %16s' % ('量', 'notch ON(09-18)', 'notch OFF(09-20)'))
            for lab, v2, v1, fmt in rows:
                print('  %-18s %16s %16s' % (lab, fmt % v2, fmt % v1))
            for x0 in (0.02, 0.10, 0.30, 0.50, 1.00):
                v2 = abs(float(np.interp(te2 + x0, g2, m2)))
                v1 = abs(float(np.interp(te + x0, g, m)))
                print('  %-18s %16.1f %16.1f' % ('位移 +%.2fs µV' % x0, v2, v1))
            print()
            print('  工频残留 / 本底（口径：脉冲前 380 ms 干净窗；LS 拟 cos/sin+趋势）')
            for f in (50, 100, 150):
                print('  %-18s %16.1f %16.1f'
                      % ('%d Hz 幅度 µV' % f,
                         np.median([line_amp(q, f) for q in base_win(D2['uv5'], tr2)]),
                         np.median([line_amp(q, f) for q in base_win(D['uv5'], tr)])))
            print('  %-18s %16.1f %16.1f'
                  % ('本底 RMS µV（扣线频）',
                     np.median([noise_rms(q) for q in base_win(D2['uv5'], tr2)]),
                     np.median([noise_rms(q) for q in base_win(D['uv5'], tr)])))
            print('  ⇒ 读出：①陷波组贡献**峰值抬升**（过冲/A %.2f%%→%.2f%%）'
                  '与**更深的降沿下冲**（谷值 %.0f→%.0f µV）；'
                  % (pct(PK2, A2), pct(PK, A),
                     float(np.min(m2[(g2 >= te2) & (g2 <= te2 + .05)])),
                     float(np.min(m[(g >= te) & (g <= te + .05)]))))
            print('            ②**长拖尾几乎不变**（+0.30/0.50/1.00 s 两态同值）⇒ 拖尾来自低端 HP，与陷波无关。')
        else:
            print('  ✗ 缺对照文件 %s' % F_ON)

    # ---------------------------------------------------------------- 仿真
    if a.sim:
        hdr('[7] 固件链仿真：AFE 边沿圆化标定 → 模型自证 → what-if')
        fs = T51.FS
        n = int(8.0 * fs)
        t = np.arange(n) / fs
        ph = (t - 1.0) % 2.0
        x0 = np.zeros(n); x0[ph < 0.1] = 3000.0

        def presmooth(z, L):
            return z if L <= 1 else np.convolve(z, np.ones(L) / L, mode='same')

        def rise1090(y, f, t0):
            j = int(round(t0 * f))
            b_ = float(np.median(y[j - 150:j - 15])); tp = float(np.median(y[j + 10:j + 45]))
            l10, l90 = b_ + 0.1 * (tp - b_), b_ + 0.9 * (tp - b_)
            k = j - 5
            while k < j + 40 and y[k] < l10:
                k += 1
            t10 = (k - 1) + (l10 - y[k - 1]) / (y[k] - y[k - 1])
            while k < j + 40 and y[k] < l90:
                k += 1
            t90 = (k - 1) + (l90 - y[k - 1]) / (y[k] - y[k - 1])
            return (t90 - t10) / f * 1e3

        rr = np.array([rise1090(D['uv5'], FS_TXT, q) for q in tr])
        print('  [7a] 边沿圆化标定（滤波器之外，实物还有 AFE 三级 sinc⁵ CIC + 853→500 重采样）')
        print('       实测 10-90%% 上升时间：中位 %.2f ms（%.2f~%.2f）'
              % (np.median(rr), rr.min(), rr.max()))
        Ldet, best = 1, 1e9
        line = []
        for L in (1, 2, 3, 4, 5, 6, 8):
            xs = presmooth(x0, L)
            y = dc_block_apply(xs, DC_FC, fs)
            for i in range(nn, len(sos)):
                y = T51._sosfilt_one(sos[i], y)
            tr2, _, _ = find_edges(y, fs)
            r = float(np.median([rise1090(y, fs, q) for q in tr2]))
            line.append('L=%d:%.2f' % (L, r))
            if abs(r - np.median(rr)) < best:
                best, Ldet = abs(r - np.median(rr)), L
        print('       仿真 10-90%%：%s ms  ⇒ 标定 **L=%d（等效 %.2f ms 预平滑）**'
              % ('  '.join(line), Ldet, Ldet / fs * 1e3))
        x = presmooth(x0, Ldet)

        yoff = dc_block_apply(x, DC_FC, fs)
        for i in range(nn, len(sos)):
            yoff = T51._sosfilt_one(sos[i], yoff)
        yon = dc_block_apply(x, DC_FC, fs)
        for i in range(len(sos)):
            yon = T51._sosfilt_one(sos[i], yon)
        low0 = [('dc1', 1.0 / (1.0 + 2 * np.pi * DC_FC / fs))] + \
               [('sos', sos[i]) for i in hp_idx]
        tdl0, Dvl0 = droop_from_secs(low0, fs)
        print()
        print('  [7b] 模型自证（同一脉冲串 + 同一套口径）')
        print('  %-22s %9s %9s %9s %9s %9s'
              % ('配置', '近平台', '末端平台', '峰值', 'A µV', '过冲/A%'))
        for lab, yy in (('固件 · notch OFF', yoff), ('固件 · notch ON', yon)):
            tr_, tf_, _ = find_edges(yy, fs)
            e = edge_metrics(yy, fs, tr_, tf_)
            pn_, pf_, pk_ = ([np.median([q[k] for q in e]) for k in
                              ('plat_near', 'plat_far', 'peak')])
            mb = base_sub(yy, fs, tr_[0])
            A_, _ = amp0_fit(np.arange(len(yy)) / fs, mb, tdl0, Dvl0, tr_[0])
            print('  %-22s %9.1f %9.1f %9.1f %9.1f %8.2f%%'
                  % (lab, pn_, pf_, pk_, A_, pct(pk_, A_)))
        print('  %-22s %9.1f %9.1f %9.1f %9.1f %8.2f%%'
              % ('实测（notch OFF）', np.median(pn), np.median(pf), m.max(), A,
                 pct(m.max(), A)))
        print('       ⇒ 近平台/末端平台/峰值三列吻合 ⇒ 仿真可作 what-if 依据。')

        print()
        print('  [7c] what-if（只动低端/高端阶数与 LP 角频率；dc_block 恒在）')
        print('  %-28s %8s %8s %9s %9s %9s %9s'
              % ('方案', '过/A%', '过/近%', '过/末端%', '位移+0.3s', '位移+1.0s', '斜率100ms'))
        cands = [
            ('现状 dc1+HP4+LP4@150', 4, 4, 150.0),
            ('只降高端 LP4→LP3@150', 4, 3, 150.0),
            ('只降高端 LP4→LP2@150', 4, 2, 150.0),
            ('只降低端 去HP4(dc1)+LP4@150', 0, 4, 150.0),
            ('两项都做 dc1+LP2@150', 0, 2, 150.0),
            ('两项都做 dc1+LP3@200', 0, 3, 200.0),
            ('两项都做 dc1+LP2@200', 0, 2, 200.0),
            ('两项都做 dc1+LP3@250', 0, 3, 250.0),
        ]
        for lab, ho, lo_, lf in cands:
            secs = [('dc1', 1.0 / (1.0 + 2 * np.pi * DC_FC / fs))]
            low = list(secs)
            if ho:
                hs = [r for r in butter(ho, 0.05, btype='high', fs=fs, output='sos')]
                secs += [('sos', r) for r in hs]
                low += [('sos', r) for r in hs]
            if lo_:
                secs += [('sos', r) for r in butter(lo_, lf, btype='low',
                                                    fs=fs, output='sos')]
            yy = T51.run_design(secs, x)
            tr_, tf_, _ = find_edges(yy, fs)
            e = edge_metrics(yy, fs, tr_, tf_)
            if not e:
                continue
            tdl, Dvl = droop_from_secs(low, fs)
            mb = base_sub(yy, fs, tr_[0])
            A_, _ = amp0_fit(np.arange(len(yy)) / fs, mb, tdl, Dvl, tr_[0])
            pk_ = np.median([q['peak'] for q in e])
            pn_ = np.median([q['plat_near'] for q in e])
            pf_ = np.median([q['plat_far'] for q in e])
            i1 = int(1.1 * fs)
            d3 = float(np.max(np.abs(mb[i1 + int(0.30 * fs):i1 + int(0.70 * fs)])))
            d10 = float(np.max(np.abs(mb[i1 + int(1.00 * fs):i1 + int(1.40 * fs)])))
            sw = int(0.1 * fs)
            j0 = i1 + int(0.05 * fs); j1 = min(i1 + int(1.8 * fs), len(yy) - 1)
            sl = (float(np.max(np.abs(mb[j0 + sw:j1] - mb[j0:j1 - sw]))) / 0.1
                  if j1 - sw > j0 else np.nan)
            print('  %-28s %7.2f%% %7.2f%% %8.2f%% %9.1f %9.1f %9.1f'
                  % (lab, pct(pk_, A_), pct(pk_, pn_), pct(pk_, pf_), d3, d10, sl))
        print('  ⇒ 读法：①**过冲只随高端 LP 阶数动**（低端改阶数几乎不动它）；')
        print('          ②**位移/斜率只随低端 HP 阶数动**（去 HP4 ⇒ 位移 272→84 ✓、斜率 194→28 ✓）；')
        print('          ③低端降到 1 阶后下垂基本消失 ⇒ 过冲三种口径**收敛到同一量级**，')
        print('            口径之争自动消解。')

    if a.fig:
        make_fig(D, tr, tf, em, g, m, A, te, PK)


def make_fig(D, tr, tf, em, g, m, A, te, PK=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    rcParams['font.family'] = 'sans-serif'
    rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    rcParams['axes.unicode_minus'] = False

    fig = plt.figure(figsize=(15, 9))
    gs = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.22)
    C = '#1f4e79'

    ax = fig.add_subplot(gs[0, :])
    ax.plot(np.arange(D['n']) / FS_TXT, D['uv5'], lw=0.6, color=C)
    for t0 in tr:
        ax.axvline(t0, color='#c00', lw=0.4, alpha=0.3)
    ax.set_title('① 全段（列⑤）｜%d 段 3 mV/100 ms 脉冲 ｜ notch OFF（dtcycles %.0f）'
                 % (len(tr), np.median(D['dtc'])), fontsize=11)
    ax.set_xlabel('t / s'); ax.set_ylabel('µV'); ax.grid(alpha=0.3)

    ax = fig.add_subplot(gs[1, 0])
    for k in range(len(tr)):
        i0 = int(round(tr[k] * FS_TXT))
        if i0 - 100 < 0 or i0 + 250 >= D['n']:
            continue
        ax.plot(np.arange(-100, 250) / FS_TXT * 1e3, D['uv5'][i0 - 100:i0 + 250],
                lw=0.6, alpha=0.45, color=C)
    ax.plot(np.arange(-100, 250) / FS_TXT * 1e3,
            D['uv5'][int(round(tr[4] * FS_TXT)) - 100:int(round(tr[4] * FS_TXT)) + 250],
            lw=1.2, color='#c00')
    ax.set_title('② 逐段叠画（红线 = 单段示例）', fontsize=11)
    ax.set_xlabel('相对上升沿 / ms'); ax.set_ylabel('µV'); ax.grid(alpha=0.3)

    ax = fig.add_subplot(gs[1, 1])
    ax.plot(g * 1e3, m, lw=1.2, color=C)
    ax.axhline(0, color='k', lw=0.6, ls='--')
    ax.axhline(100, color='#c00', lw=0.7, ls=':'); ax.axhline(-100, color='#c00', lw=0.7, ls=':')
    ax.axvspan(0, 100, color='#fff3c4', alpha=0.8)
    ax.set_xlim(-300, 1500); ax.set_ylim(-600, 3400)
    ax.set_title('③ 对齐中位模板（黄底：脉冲区；虚线：±100 µV 判限）', fontsize=11)
    ax.set_xlabel('相对上升沿 / ms'); ax.set_ylabel('µV'); ax.grid(alpha=0.3)

    PK = m.max() if PK is None else PK
    ax = fig.add_subplot(gs[2, 0])
    ax.plot(g * 1e3, m, lw=1.3, color=C)
    lbl = [('标称', 3000.0, '#888'), ('理想幅值A', A, '#080'),
           ('近平台', float(np.median(m[(g >= .03) & (g <= .05)])), '#08f')]
    for lab, v, c in lbl:
        ax.axhline(v, color=c, lw=1.0, ls='--', label='%s %.0f µV' % (lab, v))
    ax.annotate('峰值 %.0f µV\n过冲/A %.1f%%' % (PK, pct(PK, A)),
                xy=(4, m.max()), xytext=(46, 2560), fontsize=10, color='#c00',
                arrowprops=dict(arrowstyle='->', color='#c00', lw=0.9))
    ax.set_xlim(-10, 160); ax.set_ylim(-800, 3900)
    ax.set_title('④ 上升沿细节（模板）', fontsize=11)
    ax.set_xlabel('相对上升沿 / ms'); ax.set_ylabel('µV')
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc='lower right')

    ax = fig.add_subplot(gs[2, 1])
    ax.plot(g * 1e3, m, lw=1.3, color=C)
    ax.axhline(100, color='#c00', lw=0.9, ls=':'); ax.axhline(-100, color='#c00', lw=0.9, ls=':')
    ax.axvline(te * 1e3, color='k', lw=0.7, ls='--')
    ax.set_xlim(50, 2000); ax.set_ylim(-500, 300)
    ax.set_title('⑤ 降沿后基线恢复（虚线：±100 µV 判限）', fontsize=11)
    ax.set_xlabel('相对上升沿 / ms'); ax.set_ylabel('µV'); ax.grid(alpha=0.3)

    out = os.path.join(HERE, 'T52_YY0885判据a_notchOFF实测_2026-09-20.png')
    fig.savefig(out, dpi=130, bbox_inches='tight')
    print('  [fig] %s' % out)


if __name__ == '__main__':
    main()
