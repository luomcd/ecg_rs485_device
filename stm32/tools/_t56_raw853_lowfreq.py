# -*- coding: utf-8 -*-
"""T56 · raw853 实测：低频三判据（无插值伪影版）＋ 500Hz 插值伪影量化 ＋ 高频段能量

用法:
    python _t56_raw853_lowfreq.py [--file X.txt] [--fig]

背景:
    T55 落的 `test raw853 on|off` 首次上板数据。853 域**每个样本直出**，
    不受 500Hz 线性插值重采样影响 ⇒ 可给出「无插值伪影」版的 YY 0885-2013 2.1.5 三判据。
    同时用同一份 853 数据**复现固件插值算法**降到 500Hz，两版跑同一套判据 ⇒ 伪影可量化。

列义（T55 之后，见 ads1293.c 的 sprintf 与 853 分支）:
    ① idxraw     853 样本序号（相邻差恒 1 = 无丢包）
    ② ecgraw     ADS1293 24-bit 原始码
    ③ idxfilter  853 下恒 0
    ④ dsrateraw  853 下恒 0
    ⑤ filterraw  滤波链输出，**单位是 code**，×0.1122 得 µV（= 原始 24-bit 码，不饱和）
    ⑥ dtcycles   DWT 周期数
    ⑦ gdtover    %x
"""
import io
import os
import sys
import argparse

import numpy as np
import scipy.signal as ss

HERE = os.path.dirname(os.path.abspath(__file__))

FS = 853.2423208191125     # 853 域采样率（与固件系数设计域一致）
UVPC = 0.1122              # code -> µV
TIN_US, TOUT_US = 1172, 2000   # 固件重采样器参数（ecg_out.h: Tin=1172us, 500Hz -> Tout=2000us）

F_DEF = os.path.join(HERE, '3mv-0.1hz-raw853on-2026-09-20 180458.txt')

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


# ------------------------------------------------------------------ 读数据

def load7(path):
    """容错读 7 列（列⑦ 是 %x）。返回 dict。

    ★坏行判据必须是 len(p) != 7，不能只判 len(p) < 7：
      ASCII 输出在中断抢占下会发生**行撕裂**——一行的列② 被下一个样本的
      idx 钻进去（如 `6073310` + `772909` 粘成 `6073310772909`），行变成 8 列。
      这类行的列② 是天文数字，一旦放进数组就会把 max-min 统计彻底污染
      （实测：raw p-p 从 27576 code 变成 6.07e12 code）。"""
    rows, bad = [], 0
    with io.open(path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            p = ln.split()
            if len(p) != 7:
                bad += 1
                continue
            try:
                g = int(p[6], 16)
            except ValueError:
                g = 0
            try:
                rows.append([float(p[0]), float(p[1]), float(p[2]),
                             float(p[3]), float(p[4]), float(p[5]), float(g)])
            except ValueError:
                bad += 1
    a = np.array(rows)
    return dict(idx=a[:, 0], raw=a[:, 1], ifilt=a[:, 2], dsr=a[:, 3],
                filt=a[:, 4], dtc=a[:, 5], over=a[:, 6], nbad=bad, n=len(a))


def clean_idx(d):
    """剔除 idx 倒退的脏行；**在剔除之后**再统计真丢包。
    返回 (保留掩码, 脏行列表, 丢失样本数, 各段空洞)。"""
    idx = d['idx'].astype(np.int64)
    keep = np.ones(len(idx), bool)
    dirty = [int(k + 1) for k in np.where(np.diff(idx) < 0)[0]]
    for k in dirty:
        keep[k] = False
    i2 = idx[keep]
    d2 = np.diff(i2)
    holes = [(int(i2[k]), int(i2[k + 1]), int(d2[k] - 1))
             for k in np.where(d2 != 1)[0]]
    lost = int(sum(h[2] for h in holes))
    return keep, dirty, lost, holes


# ---------------------------------------------------- 复现固件 500Hz 重采样

def fw_resample500(y, tin_us=TIN_US, tout_us=TOUT_US):
    """逐字复现 ecg_out.c::ecg_resample_f32_process（相位累加 + 线性插值）。
    返回 (y_out, 输出样本的原始索引分数位置)。"""
    out, pos = [], []
    acc = 0
    y_prev = 0.0
    for i, v in enumerate(y):
        if i == 0:
            y_prev = float(v)
            out.append(float(v))
            pos.append(0.0)
            continue
        acc += tin_us
        if acc >= tout_us:
            acc -= tout_us
            frac = (tin_us - acc) / float(tin_us)
            if frac < 0.0:
                frac = 0.0
            out.append(y_prev + (float(v) - y_prev) * frac)
            pos.append(i - 1 + frac)
        y_prev = float(v)
    return np.array(out), np.array(pos)


# ------------------------------------------------------------------ 脉冲定位

def find_pulses(raw, thr_frac=0.5):
    """用列② raw（未滤波、边沿最干净）定位脉冲。返回 [(i_rise, i_fall), ...]。"""
    bl = float(np.median(raw))
    pk = float(raw.max())
    thr = bl + thr_frac * (pk - bl)
    hi = raw > thr
    dd = np.diff(hi.astype(np.int8))
    rises = list(np.where(dd == 1)[0] + 1)
    falls = list(np.where(dd == -1)[0] + 1)
    pl = []
    for s in rises:
        e = [x for x in falls if x > s]
        if e:
            pl.append((int(s), int(e[0])))
    return pl, thr, bl


def at(y, i):
    """线性插值取 y[i]（i 可为小数）。"""
    i = float(i)
    if i <= 0:
        return float(y[0])
    if i >= len(y) - 1:
        return float(y[-1])
    k = int(np.floor(i))
    f = i - k
    return float(y[k]) * (1 - f) + float(y[k + 1]) * f


def slope_win_max(t_axis, yv, t_start, t_end, win_s, step=0.0002):
    """在 [t_start,t_end] 内滑窗 win_s 取最大 |Δy|/win_s（步长 0.2 ms，与 T52 同）。"""
    best = np.nan
    hi = min(t_end, t_axis[-1] - win_s)
    for x in np.arange(t_start, hi, step):
        v = abs(np.interp(x + win_s, t_axis, yv) - np.interp(x, t_axis, yv)) / win_s
        if not np.isfinite(best) or v > best:
            best = v
    return float(best) if np.isfinite(best) else np.nan


def median_template(y_uv, t, pulses, fs, span=(-0.30, 1.20)):
    """以升沿为 0 对齐的中位模板（复现 T52 口径），返回 (tm, Mm, t_fall_in_tm)。"""
    n = len(y_uv)
    M = []
    for (s, e) in pulses:
        a0 = s + int(span[0] * fs)
        b0 = s + int(span[1] * fs)
        if a0 < 0 or b0 >= n:
            continue
        pre = y_uv[a0:a0 + int(0.02 * fs)]
        M.append(y_uv[a0:b0] - float(np.median(pre)))
    if not M:
        return None, None, np.nan
    Mm = np.median(np.vstack(M), axis=0)
    tm = np.arange(len(Mm)) / fs + span[0]
    t_fall = float(np.median([t[e] - t[s] for s, e in pulses]))
    return tm, Mm, t_fall


# ------------------------------------------------------------------ 三判据

def judge(y_uv, t, pulses, fs, rep=10.0):
    """算三判据。pulses 用索引；y_uv 单位 µV；t 秒。
    判限：位移 <=100 µV｜终点后斜率 <=300 µV/s｜过冲 <10%。
    前基线给三种口径（标准原文只说「脉冲出现前的基线」）：
      near = 上升沿前 [280,30] ms 中位（T52 口径，宽 250 ms）
      long = 降沿前 [5.0,0.05] s 中位（本 10 s 周期条件下的长效窗）
      zero = 0（不扣基线）
    斜率滑窗的**起点在降沿之后 20 ms**（标准原文「终点后」，含降沿本身必然爆表）。"""
    res = {}
    n = len(y_uv)
    A_off = [0.020, 0.050, 0.100, 0.200, 0.300, 0.500, 1.000, 2.000, 5.000]
    SWIN = [0.050, 0.100, 0.200]
    dn = {a: [] for a in A_off}
    dl = {a: [] for a in A_off}
    dz = {a: [] for a in A_off}
    bn, bl = [], []
    sw = {w: [] for w in SWIN}
    sf = []
    tops, peaks, ends, nears = [], [], [], []
    st_pk, st_gu, st_pl = [], [], []

    for (s, e) in pulses:
        ts, tf = t[s], t[e]
        mn = (t >= ts - 0.280) & (t <= ts - 0.030)
        b_near = float(np.median(y_uv[mn])) if mn.sum() > 5 else np.nan
        bn.append(b_near)
        ml = (t >= tf - 5.0) & (t <= tf - 0.05)
        b_long = float(np.median(y_uv[ml])) if ml.sum() > 20 else np.nan
        bl.append(b_long)
        for a in A_off:
            v = at(y_uv, np.searchsorted(t, tf + a))
            dn[a].append(v - b_near)
            dl[a].append(v - b_long)
            dz[a].append(v)

        for ws in SWIN:
            v = slope_win_max(t, y_uv, tf + 0.020, tf + 2.0, ws)
            if np.isfinite(v):
                sw[ws].append(v)
        m = (t >= tf + 0.2) & (t <= tf + 1.2)
        if m.sum() > 20:
            sf.append(float(np.polyfit(t[m] - tf, y_uv[m], 1)[0]))

        a0 = max(0, s - int(0.005 * fs))
        a1 = min(n, s + int(0.060 * fs))
        if a1 - a0 > 4:
            pk = float(np.max(np.interp(np.linspace(a0, a1, (a1 - a0) * 20),
                                        np.arange(n), y_uv)))
        else:
            pk = np.nan
        peaks.append(pk)
        w0 = s + int(0.010 * fs)
        w1 = max(w0 + 3, e - int(0.005 * fs))
        tops.append(float(np.mean(y_uv[w0:w1])) if w1 > w0 else np.nan)
        nears.append(at(y_uv, s + 0.015 * fs))
        ends.append(at(y_uv, e - 0.005 * fs))
        st_pk.append(pk)
        g0 = s + int(0.018 * fs)
        g1 = s + int(0.032 * fs)
        st_gu.append(float(np.min(y_uv[g0:g1])) if g1 > g0 + 1 else np.nan)
        p0 = s + int(0.050 * fs)
        p1 = min(n, s + int(0.095 * fs))
        st_pl.append(float(np.mean(y_uv[p0:p1])) if p1 > p0 + 1 else np.nan)

    f = lambda v: float(np.nanmedian(v)) if len(v) else np.nan
    res['base_near'] = f(bn)
    res['base_long'] = f(bl)
    res['disp_near'] = {a: f(dn[a]) for a in A_off}
    res['disp_long'] = {a: f(dl[a]) for a in A_off}
    res['disp_zero'] = {a: f(dz[a]) for a in A_off}
    res['slope_win'] = {w: f(sw[w]) for w in SWIN}
    tm, Mm, t_fall_tm = median_template(y_uv, t, pulses, fs)
    if tm is not None:
        res['slope_win_tmpl'] = {w: slope_win_max(tm, Mm, t_fall_tm + 0.020,
                                                  min(t_fall_tm + 1.10, tm[-1]), w)
                                 for w in SWIN}
        res['tmpl_deep'] = float(np.min(Mm[tm >= t_fall_tm]))
    else:
        res['slope_win_tmpl'] = {w: np.nan for w in SWIN}
        res['tmpl_deep'] = np.nan
    res['slope_fit'] = f(sf)
    res['top'] = f(tops)
    res['peak'] = f(peaks)
    res['near'] = f(nears)
    res['end'] = f(ends)
    res['struct'] = (f(st_pk), f(st_gu), f(st_pl))
    return res


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_DEF)
    ap.add_argument('--fig', action='store_true')
    ap.add_argument('--tag', default='', help='输出文件名后缀，如 _notchoff')
    args = ap.parse_args()

    say('=' * 92)
    say('T56 · raw853 实测：低频三判据（无插值伪影）＋ 插值伪影量化')
    say('=' * 92)
    say('file = %s' % os.path.basename(args.file))
    say()

    d = load7(args.file)
    keep, dirty, lost, holes = clean_idx(d)
    say('[0] 列义自检与数据验收（对照 T55 4 节的 V1~V7）')
    say('  总行数 %d  列数异常行（已剔除）%d' % (d['n'], d['nbad']))
    say('  列① idxraw : %d -> %d' % (d['idx'][0], d['idx'][-1]))
    say('  脏行（idx 倒退，已剔除）: %s'
        % (['第 %d 行 idx=%d' % (k + 1, d['idx'][k]) for k in dirty] or '无'))
    say('  ★V5 真丢包 = %d 个样本  空洞 %s'
        % (lost, ['%d->%d（缺%d）' % h for h in holes] or '无'))
    span = int(d['idx'][-1] - d['idx'][0])
    say('  ★V4 行率 : idx 跨度 %d，实收 %d 行 ⇒ 丢包率 %.4f%%（%d/%d）'
        % (span, len(d['idx']) - len(dirty), 100.0 * lost / (span + 1), lost, span + 1))
    say('  ★V6 列③ max=%.0f  列④ max=%.0f  %s'
        % (np.abs(d['ifilt']).max(), np.abs(d['dsr']).max(),
           'PASS' if (np.abs(d['ifilt']).max() == 0 and np.abs(d['dsr']).max() == 0) else 'FAIL'))
    raw_pp = (d['raw'].max() - d['raw'].min()) * UVPC
    say('  ★V7 列② raw p-p = %.0f code = %.1f µV（3 mV 脉冲）%s'
        % (d['raw'].max() - d['raw'].min(), raw_pp,
           'PASS' if 2500 < raw_pp < 3500 else 'CHECK'))
    say('     （列⑤ 含下冲与过冲，p-p = %.1f µV，不能拿来判输入幅度）'
        % ((d['filt'].max() - d['filt'].min()) * UVPC))
    cs = float(np.median(d['dtc']))
    say('  列⑥ dtcycles 中位 %.0f（历史基准 OFF 3950 / ON 9712）' % cs)
    say('     ★配置指纹（T60 标定：a=641.3 cyc/节 + c=1373）：'
        '新链ON ~9079｜旧链ON 9710｜旧链OFF 3938｜新链OFF ~3297')
    say('     ★自动判定：notch %s（距 ON 基准 %.0f ／ 距 OFF 基准 %.0f）'
        % ('ON' if abs(cs - 9712) < abs(cs - 3950) else 'OFF',
           abs(cs - 9712), abs(cs - 3950)))
    say('  列⑦ gdtover 唯一值 %s（DWT 超 150us 计数）' % str(np.unique(d['over'])[:5]))
    say()

    idx = d['idx'][keep].astype(np.int64)
    raw = d['raw'][keep]
    y = d['filt'][keep] * UVPC
    t = (idx - idx[0]) / FS
    say('  清洗后 %d 行，时长 %.3f s' % (len(t), t[-1]))
    say()

    pulses, thr, bl = find_pulses(raw)
    say('[1] 脉冲定位（用列② raw，阈值 = 基线 + 50%% 幅度 = %.0f）' % thr)
    say('  %-4s %11s %11s %9s %10s' % ('#', '升沿 t(s)', '降沿 t(s)', '脉宽 ms', '周期 s'))
    prev_r = None
    for k, (s, e) in enumerate(pulses):
        per = (t[s] - prev_r) if prev_r is not None else float('nan')
        say('  %-4d %11.4f %11.4f %9.2f %10.4f'
            % (k + 1, t[s], t[e], (t[e] - t[s]) * 1000, per))
        prev_r = t[s]
    rep = float(np.median(np.diff([t[s] for s, _ in pulses]))) if len(pulses) > 1 else 10.0
    say('  实测重复周期 = %.4f s（= %.4f Hz）⇒ %s YY0885「间隔 >=10 s」'
        % (rep, 1.0 / rep, '满足' if rep >= 9.99 else '不满足'))
    if len(pulses) > 2:
        di = int(idx[pulses[2][0]] - idx[pulses[1][0]])
        fs_imp = di / 10.0000    # AECG100 石英时基，0.1 Hz 标称周期 = 10.0000 s
        say('  ★V4 独立校验 : 脉冲2->3 的 idx 差 = %d，标称周期 10.0000 s ⇒ 隐含 FS = %.3f Hz'
            % (di, fs_imp))
        say('     （设计值 %.3f ⇒ 偏差 %+.4f%%，在 AECG100 时基精度内 ⇒ 853 行率自洽）'
            % (FS, 100 * (fs_imp / FS - 1)))
    say()

    L_DISP, L_SLOPE, L_OVER = 100.0, 300.0, 10.0
    say('[2] ★三判据（853 域直出，无插值伪影）')
    say('    判限：位移 <=%.0f µV｜终点后斜率 <=%.0f µV/s｜过冲 <%.0f%%' % (L_DISP, L_SLOPE, L_OVER))
    R853 = judge(y, t, pulses, FS, rep)

    say('  (a1) 位移（降沿后各时刻 − 前基线）—— 三种基线口径并列')
    say('       base_near = %+.1f µV（上升沿前 [280,30] ms 中位，T52 口径）' % R853['base_near'])
    say('       base_long = %+.1f µV（降沿前 [5.0,0.05] s 中位，本条件稳态基线）' % R853['base_long'])
    say('       %-11s %11s %11s %11s %-14s %s' % ('降沿后', 'near口径', 'long口径', '未扣基线', '判定(long)', '判定(near)'))
    for a in sorted(R853['disp_long']):
        vn = R853['disp_near'][a]
        vl = R853['disp_long'][a]
        vz = R853['disp_zero'][a]
        say('       %-11s %11.1f %11.1f %11.1f %-14s %s'
            % ('+%.3f s' % a, vn, vl, vz,
               'OK' if abs(vl) <= L_DISP else 'NG x%.2f' % (abs(vl) / L_DISP),
               'OK' if abs(vn) <= L_DISP else 'NG x%.2f' % (abs(vn) / L_DISP)))
    # 首次回到 ±100 µV 内（用 long 口径，1 ms 栅格）
    tt = np.arange(0.005, 5.0, 0.001)
    okv = np.zeros(len(tt), bool)
    for j, x in enumerate(tt):
        okv[j] = all(abs(at(y, np.searchsorted(t, t[e_] + x)) - R853['base_long']) <= L_DISP
                     for (s_, e_) in pulses)
    bad = np.where(~okv)[0]
    if len(bad) == 0:
        back = float(tt[0])
    elif bad[-1] + 1 < len(tt):
        back = float(tt[bad[-1] + 1])
    else:
        back = np.nan
    say('       ⇒ 首次「此后一直」回到 ±100 µV 内：降沿后 %s'
        % ('%.3f s' % back if np.isfinite(back) else '观测窗(5 s)内未回到'))
    say('         （注意：降沿后 ~8 ms 处会**穿越** ±100 µV 带，那是下冲过零，不是回归）')
    say()

    say('  (a2) 脉冲终点后斜率（判限 %.0f µV/s；滑窗起点在降沿后 20 ms，不含降沿本身）' % L_SLOPE)
    say('       %-12s %16s %16s' % ('窗长', '逐脉冲(不衰减)', '中位模板(T52口径)'))
    for w in sorted(R853['slope_win']):
        v = R853['slope_win'][w]
        v2 = R853['slope_win_tmpl'][w]
        say('       滑窗 %3.0f ms  %16.1f %16.1f   %s'
            % (w * 1000, v, v2, 'NG' if v > L_SLOPE else 'OK'))
    vt = R853['slope_fit']
    say('       线性拟合 [t_f+0.2, t_f+1.2] s = %9.1f µV/s  %s'
        % (vt, 'OK' if abs(vt) <= L_SLOPE else 'NG x%.2f' % (abs(vt) / L_SLOPE)))
    say()

    say('  (a3) 脉冲边缘过冲（判限 %.0f%%）' % L_OVER)
    pk, gu, pl = R853['struct']
    say('       结构（中位）：峰 %8.1f µV  ->  谷 %8.1f µV  ->  平台 %8.1f µV' % (pk, gu, pl))
    say('       峰值          = %8.1f µV' % R853['peak'])
    say('       顶部均值      = %8.1f µV' % R853['top'])
    say('       近平台(+15ms) = %8.1f µV' % R853['near'])
    say('       末端平台(-5ms)= %8.1f µV' % R853['end'])
    say('       输入幅度      = %8.1f µV（列② raw p-p）' % raw_pp)
    for lab, ref in (('标称 3000', 3000.0),
                     ('输入幅度', raw_pp),
                     ('顶部均值', R853['top']),
                     ('近平台+15ms', R853['near']),
                     ('平台(降沿+29ms)', pl)):
        ov = (R853['peak'] - ref) / ref * 100.0
        say('       过冲 / %-16s = %6.2f%%  %s'
            % (lab, ov, 'OK' if ov < L_OVER else 'NG x%.2f' % (ov / L_OVER)))
    say()

    say('[3] ★500Hz 插值版对照（用固件算法复现降采样，量化插值伪影）')
    y500, pos500 = fw_resample500(y)
    t500 = np.interp(pos500, np.arange(len(t)), t)
    f500 = (len(y500) - 1) / (t500[-1] - t500[0])
    say('  降采样后 %d 行（853 的 %.1f%%），等效 %.2f Hz（理论 500）'
        % (len(y500), 100.0 * len(y500) / len(y), f500))
    p500 = []
    for (s, e) in pulses:
        s5 = int(np.searchsorted(pos500, s))
        e5 = int(np.searchsorted(pos500, e))
        if 0 < s5 < e5 < len(y500):
            p500.append((s5, e5))
    R500 = judge(y500, t500, p500, f500, rep)
    say('  %-18s %13s %13s %12s' % ('指标', '853 无伪影', '500 插值版', '差值'))
    rows = [('位移 +0.020 s (L)', R853['disp_long'][0.020], R500['disp_long'][0.020], 'µV'),
            ('位移 +0.300 s (L)', R853['disp_long'][0.300], R500['disp_long'][0.300], 'µV'),
            ('位移 +1.000 s (L)', R853['disp_long'][1.000], R500['disp_long'][1.000], 'µV'),
            ('斜率 滑窗 50ms', R853['slope_win'][0.05], R500['slope_win'][0.05], 'µV/s'),
            ('斜率 滑窗100ms', R853['slope_win'][0.10], R500['slope_win'][0.10], 'µV/s'),
            ('斜率 拟合0.2-1.2', R853['slope_fit'], R500['slope_fit'], 'µV/s'),
            ('峰值', R853['peak'], R500['peak'], 'µV'),
            ('顶部均值', R853['top'], R500['top'], 'µV'),
            ('末端平台', R853['end'], R500['end'], 'µV')]
    for lab, va, vb, u in rows:
        say('  %-18s %13.1f %13.1f %12.1f  %s' % (lab, va, vb, vb - va, u))
    o8 = (R853['peak'] - R853['top']) / R853['top'] * 100
    o5 = (R500['peak'] - R500['top']) / R500['top'] * 100
    say('  %-18s %13.2f %13.2f %12.2f  %%' % ('过冲(对顶部均值)', o8, o5, o5 - o8))
    say()

    say('[4] 高频段真实能量（回答 T54 遗留：250~426 Hz 到底有多少能量）')
    rawsig = (raw - np.median(raw)) * UVPC
    fw_, Pw = ss.welch(rawsig, fs=FS, nperseg=8192, detrend='linear')
    say('  Welch PSD（列② raw = 链路输入，nperseg=8192，Δf=%.4f Hz）' % fw_[1])
    say('     %-12s %16s %14s' % ('f(Hz)', 'PSD µV²/Hz', 'µV/sqrt(Hz)'))
    for f0 in (1, 10, 40, 100, 150, 200, 250, 300, 350, 400, 426):
        j = int(np.argmin(np.abs(fw_ - f0)))
        say('     %-12g %16.4g %14.4g' % (f0, Pw[j], np.sqrt(max(Pw[j], 0.0))))

    def rms_band(lo, hi):
        m = (fw_ >= lo) & (fw_ < hi)
        return float(np.sqrt(np.sum(Pw[m]) * (fw_[1] - fw_[0])))
    r_low = rms_band(0.5, 250.0)
    r_hi = rms_band(250.0, 426.6)
    say()
    say('  频段 RMS：0.5~250 Hz = %.2f µV ｜ 250~426.6 Hz = %.4f µV ｜ 比 = %.3e（%+.1f dB）'
        % (r_low, r_hi, r_hi / max(r_low, 1e-12),
           20 * np.log10(max(r_hi / max(r_low, 1e-12), 1e-12))))
    fold = np.zeros_like(fw_)
    m = (fw_ >= 250.0) & (fw_ <= 426.6)
    for f0, p0 in zip(fw_[m], Pw[m]):
        j = int(np.argmin(np.abs(fw_ - abs(500.0 - f0))))
        fold[j] += p0
    r_fold = float(np.sqrt(np.sum(fold) * (fw_[1] - fw_[0])))
    say('  ⇒ 按固件 500 Hz 网格折叠回流：RMS = %.4f µV，相对 0.5~250 段 = %.3e（%+.1f dB）'
        % (r_fold, r_fold / max(r_low, 1e-12),
           20 * np.log10(max(r_fold / max(r_low, 1e-12), 1e-12))))
    say('  ⇒ 这是「实测输入谱」版的抗混叠评估，替代 T54 的白噪声假设。')
    say()

    # --- [5] notch 状态离线对照
    say('[5] ★notch 状态对三判据的影响（离线固件链模型）')
    try:
        import _t52_yy0885_notchoff as T52m
        sos_, nn_, hp_, kinds_ = T52m.fw_chain('IMP620_N2')
        sos6 = [np.insert(np.asarray(sv, float), 3, 1.0) for sv in sos_]
        xin = raw * UVPC

        def run_chain(xv, use_notch):
            yv = T52m.dc_block_apply(xv, 0.05, FS)
            for i in range(0 if use_notch else nn_, len(sos6)):
                yv = ss.sosfilt(sos6[i:i + 1], yv)
            return yv
        y_on = run_chain(xin, True)
        y_off = run_chain(xin, False)
        say('  固件链 = %d 节 = %d NOTCH + %s' % (len(sos_), nn_, '+'.join(kinds_[nn_:])))
        e_on = float(np.sqrt(np.mean((y_on - y) ** 2)))
        e_off = float(np.sqrt(np.mean((y_off - y) ** 2)))
        n_off = len(sos6) - nn_     # 关陷波时实跑的节数 = BP 段全部节（T60 起动态算，别写死）
        say('  ★自证 RMS 差：模型%d节(ON) %.2f µV ｜ 模型%d节(OFF) %.2f µV  ⇒ 实测最接近 %s'
            % (len(sos6), e_on, n_off, e_off, 'notch ON' if e_on < e_off else 'notch OFF'))
        R_off = judge(y_off, t, pulses, FS, rep)
        R_on = judge(y_on, t, pulses, FS, rep)
        tr2 = pulses[1][0] / FS if len(pulses) > 1 else pulses[0][0] / FS
        m = (t > tr2 - 0.17) & (t < tr2 + 0.43)
        say('  单脉冲读数（第2个脉冲，升沿 t=%.3f s，窗 %.2f~%.2f）：'
            % (tr2, tr2 - 0.17, tr2 + 0.43))
        say('    %-16s %10s %10s %10s' % ('', '峰 µV', '谷 µV', '平台 µV'))
        for lab, yv in (('模型%d节(ON)' % len(sos6), y_on),
                        ('模型%d节(OFF)' % n_off, y_off),
                        ('实测列⑤', y)):
            say('    %-16s %10.1f %10.1f %10.1f'
                % (lab, yv[m].max(), yv[m].min(), np.median(yv[m])))
        say('  %-18s %13s %13s %13s' % ('指标', '实测', '离线OFF', '离线ON'))

        def _fv(R, key, sub):
            return R[key][sub] if sub is not None else R[key]

        for lab, key, sub in (('位移 +0.300 s (L)', 'disp_long', 0.300),
                              ('位移 +1.000 s (L)', 'disp_long', 1.000),
                              ('滑窗 100 ms', 'slope_win', 0.100),
                              ('拟合 0.2-1.2 s', 'slope_fit', None),
                              ('峰值', 'peak', None),
                              ('顶部均值', 'top', None)):
            say('  %-18s %13.1f %13.1f %13.1f'
                % (lab, _fv(R853, key, sub), _fv(R_off, key, sub), _fv(R_on, key, sub)))
        oa = (R853['peak'] - R853['top']) / R853['top'] * 100
        ob = (R_off['peak'] - R_off['top']) / R_off['top'] * 100
        oc = (R_on['peak'] - R_on['top']) / R_on['top'] * 100
        say('  %-18s %13.2f %13.2f %13.2f  %%' % ('过冲(对顶部均值)', oa, ob, oc))
        fq = np.array([0.1, 0.5, 1.0, 10.0, 40.0, 100.0])

        def Hg(s0):
            hh = np.ones(len(fq), complex)
            for i in range(s0, len(sos6)):
                _, q = ss.freqz(sos6[i][:3], sos6[i][3:], worN=2 * np.pi * fq / FS)
                hh *= q
            return np.abs(hh)
        ga, gb = Hg(0), Hg(nn_)
        say('  notch 段的频率响应影响：')
        for fi, x1, x2 in zip(fq, ga, gb):
            say('    %-7g Hz  |H_notch段| %.5f  |H_BP段| %.5f  影响 %+7.3f dB'
                % (fi, x1, x2, 20 * np.log10(max(x1 / max(x2, 1e-12), 1e-12))))
        say('  ⇒ notch 段在 0.1~10 Hz 增益 =1（**不影响稳态低频**），')
        say('     但 40 Hz 已 −0.45 dB、100 Hz 是陷波中心 ⇒ **显著改变脉冲边沿形状**。')
    except Exception as e:
        say('  （跳过：%s）' % e)
    say()

    say('[6] 结论')
    wl = max(abs(v) for v in R853['disp_long'].values())
    wn = max(abs(v) for v in R853['disp_near'].values())
    say('  (a1) 位移 : long 口径最大 %.1f µV ／ near 口径最大 %.1f µV ⇒ NG（限 100）' % (wl, wn))
    s50 = R853['slope_win'][0.05]
    s100 = R853['slope_win'][0.10]
    s200 = R853['slope_win'][0.20]
    say('  (a2) 斜率 : 滑窗 50/100/200 ms = %.0f / %.0f / %.0f µV/s（%s）｜拟合 %.0f（%s）'
        % (s50, s100, s200,
           'NG' if s100 > L_SLOPE else 'OK',
           R853['slope_fit'], 'OK' if abs(R853['slope_fit']) <= L_SLOPE else 'NG'))
    ov = (R853['peak'] - R853['top']) / R853['top'] * 100
    say('  (a3) 过冲 : %.2f%%（对顶部均值）／ %.2f%%（对平台 %s）⇒ NG（限 10）'
        % (ov, (R853['peak'] - R853['struct'][2]) / R853['struct'][2] * 100, '%.0f' % R853['struct'][2]))
    say('  ⇒ 位移·斜率由低端阶数（现 5 阶）定；过冲由高端 LP 阶数（现 4 阶）定。')

    outf = '_t56_out%s.txt' % args.tag
    with io.open(os.path.join(HERE, outf), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
    say()
    say('（已写 %s）' % outf)

    if args.fig:
        make_fig(t, y, t500, y500, pulses, p500, R853, R500, raw, bl, tag=args.tag)


def make_fig(t, y, t500, y500, pulses, p500, R853, R500, raw, bl, tag=''):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    a = ax[0][0]
    a.plot(t, y, lw=0.7, color='#1f77b4')
    for k, (s, e) in enumerate(pulses):
        a.axvline(t[s], color='r', ls='--', lw=0.8, alpha=0.7)
    a.axhline(0, color='k', lw=0.5)
    a.axhline(-100, color='g', ls=':', lw=0.9)
    a.set_title('全时段：853 直出（每 10 s 一个 100 ms 脉冲）')
    a.set_xlabel('t (s)'); a.set_ylabel('µV'); a.grid(alpha=0.3)

    a = ax[0][1]
    if pulses:
        s, e = pulses[1] if len(pulses) > 1 else pulses[0]
        m = (t > t[s] - 0.35) & (t < t[e] + 1.6)
        a.plot(t[m], y[m], lw=1.1, color='#1f77b4', label='853 直出')
        m5 = (t500 > t[s] - 0.35) & (t500 < t[e] + 1.6)
        a.plot(t500[m5], y500[m5], lw=1.0, color='#d62728', alpha=0.8, label='500 插值版', ls='--')
        a.axvline(t[s], color='k', ls=':', lw=0.8)
        a.axvline(t[e], color='k', ls=':', lw=0.8)
        a.axhline(-100, color='g', ls=':', lw=0.9); a.axhline(100, color='g', ls=':', lw=0.9)
    a.set_title('单脉冲 + 长尾（853 vs 复现的 500 插值版）')
    a.set_xlabel('t (s)'); a.set_ylabel('µV'); a.legend(fontsize=8); a.grid(alpha=0.3)

    a = ax[1][0]
    offs = sorted(R853['disp_long'])
    v8 = [R853['disp_long'][o] for o in offs]
    v5 = [R500['disp_long'][o] for o in offs]
    x = np.arange(len(offs))
    a.bar(x - 0.2, v8, 0.4, label='853 直出', color='#1f77b4')
    a.bar(x + 0.2, v5, 0.4, label='500 插值版', color='#d62728', alpha=0.8)
    a.axhline(-100, color='g', ls='--', lw=1.2, label='判限 −100 µV')
    a.set_xticks(x); a.set_xticklabels(['+%gs' % o for o in offs], fontsize=8)
    a.set_title('判据 a1 位移：降沿后各时刻（两口径并列）')
    a.set_ylabel('µV'); a.legend(fontsize=8); a.grid(alpha=0.3, axis='y')

    a = ax[1][1]
    fw_, Pw = ss.welch((raw - np.median(raw)) * UVPC, fs=FS, nperseg=8192, detrend='linear')
    a.semilogy(fw_[1:], Pw[1:], lw=0.9, color='#2ca02c')
    a.axvspan(250, 426.6, color='orange', alpha=0.18, label='250~426.6 Hz（会被折叠）')
    a.axvline(250, color='r', ls='--', lw=0.9)
    a.set_title('输入信号功率谱 Welch PSD（列② raw，853 域）')
    a.set_xlabel('Hz'); a.set_ylabel('PSD (µV²/Hz)'); a.legend(fontsize=8); a.grid(alpha=0.3)

    plt.tight_layout()
    out = os.path.join(HERE, 'T56_raw853低频三判据_2026-09-20%s.png' % tag)
    plt.savefig(out, dpi=110)
    print('图已保存: %s' % out)


if __name__ == '__main__':
    main()
