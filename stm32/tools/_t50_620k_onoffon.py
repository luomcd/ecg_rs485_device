# -*- coding: utf-8 -*-
"""
T50 · 620 kΩ 接入 vs 未接入：notch on→off→on 对比复核
=========================================================================
目的：把 T49（DC 预置修法）搬到**高源阻抗**工况下复验——
      ① 低阻侧 off→on 是否真的修好（T48 改前 ~130 µV，τ≈200 ms）；
      ② 620 kΩ 接入后开关是否仍可用、瞬态能否外推；
      ③ 「关陷波」在高阻下的可用域上端在哪（T45 预测 375 Ω→13.8 µV / 620 kΩ→19.1 mV）。

输入（本目录；过程一致：1 mV / 1 Hz 方波，notch on → off → on）：
  --in    1mv-square-notch-620k(in)-2026-09-20 134612.txt      620 kΩ 接入
  --notin 1mv-square-notch-620k(notin)-2026-09-20 134448.txt   620 kΩ 未接入

数据列义（7 列文本；在 notin 上用「列5/列4 比值恒定 = 1.3598」取证，见 [_0]）：
  列1  t（强制 0，不用）
  列2  ecgraw 未滤波原始码（0.1122 µV/code，零点 6075000）★ 不削顶
  列3  输出样本序号（500 Hz 域）
  列4  输出 int16 LSB（0.1526 µV/LSB）★ 值域 ±32767 ⇒ ±5000 µV，**会削顶**
  列5  滤波后 code 域值（0.1122 µV/code）★ 与列4 严格 1.3601 比例，**不削顶** ⇒ 主信号
  列6  dtcycles（每样本滤波+重采样耗时，80 MHz）⇒ 状态自检
  列7  文本粘连残留（T48 文件里出现 "6notch on"）⇒ **一律忽略**

方法学（本脚本的核心）——**周期折叠模板法**：
  1 Hz 方波在 500 Hz 域 ≈ 502 样本一个周期；把「切换后已稳定段」按测量出的周期折叠取中位，
  得到「若从未切换、稳态应有的波形」= 模板。切换段与模板之差就是**纯切换瞬态**。
  ⇒ 自动剔除方波边沿响应的干扰（全局阈值法的边沿检测在 620 kΩ 下会完全失效——本脚本第一版
     只找到 6 个边沿、把方波周期算成 3.02 s，原因就是大瞬态把全局 p-p 抬高了）。

口径沿用 09-18 定案：
  · 输入阻抗试验（AECG100 手册 line 1021 = 620 kΩ/4.7 nF）：屏幕 p-p(620k)/p-p(直连) ≤ 1.10，源 = 列④
  · raw 域谱线源 = 列②（列④/列⑤ 是滤波后，看不出真实的 620 kΩ 放大倍数）

用法：
  python _t50_620k_onoffon.py            # 全部数值
  python _t50_620k_onoffon.py --fig      # 出图
"""
import argparse
import io
import os

import numpy as np
from scipy.ndimage import median_filter

HERE = os.path.dirname(os.path.abspath(__file__))

FS = 500.0            # ★ 实测 txt 时间轴采样率（不可与滤波器域 853.2423 混用）
UV = 0.1122           # 列5 / 列2 code 域 µV/code
LSB = 0.1526          # 列4 输出 LSB µV
RAW_ZERO = 6075000    # 列2 零点（09-18 §2 定）
CLK = 80e6
PERIOD_CYC = CLK * 1172e-6          # ≈93760 cycles/样本
RAIL = 32760
MED = 100             # 中值窗 0.2 s = 10 个 50 Hz 整周期

FILE_IN = '1mv-square-notch-620k(in)-2026-09-20 134612.txt'
FILE_NOTIN = '1mv-square-notch-620k(notin)-2026-09-20 134448.txt'


def load(path):
    rows, bad = [], 0
    for line in io.open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if len(p) < 6:
            bad += 1
            continue
        try:
            # ★ 第⑦列 gdtover：固件以 %x 输出（十六进制）；合成/旧数据可能给十进制或浮点
            #   ⇒ 先试 hex，失败退回 float（两种都收，绝不因该列丢行）。整行 float() 会在
            #   gdtover 长到含 a~f 时静默丢行（A+ 档 dtcycles > 12000 ⇒ 每样本 ++ 即触发）。
            vals = [float(t) for t in p[:6]]
            if len(p) > 6:
                try:
                    vals.append(float(int(p[6], 16)))
                except ValueError:
                    vals.append(float(p[6]))
            else:
                vals.append(0.0)
            rows.append(vals)
        except ValueError:
            bad += 1
    return np.array(rows), bad


def segments(c6):
    lo = c6 < (c6.min() + c6.max()) / 2
    fl = np.where(np.diff(lo.astype(int)) != 0)[0] + 1
    b = [0] + list(fl) + [len(c6)]
    names = ['on1', 'off', 'on2']
    return [(names[i] if i < 3 else 'on%d' % i, b[i], b[i + 1]) for i in range(len(b) - 1)]


def base_at(v, size=MED):
    return median_filter(v, size=size, mode='nearest')


def residual(v):
    return v - base_at(v)


def spec_line(y, f0, bw=2.0):
    w = np.hanning(len(y))
    Y = np.fft.rfft((y - y.mean()) * w)
    fr = np.fft.rfftfreq(len(y), 1 / FS)
    amp = 2 * np.abs(Y) / w.sum()
    return float(amp[(fr > f0 - bw) & (fr < f0 + bw)].max())


def rms(y):
    return float(np.sqrt(np.mean(np.asarray(y, dtype=float) ** 2)))


def _foldvar(seg, P):
    n = len(seg)
    bi = np.floor(np.arange(n) % P).astype(int)
    cnt = np.bincount(bi).astype(float)
    s1 = np.bincount(bi, weights=seg)
    s2 = np.bincount(bi, weights=seg * seg)
    ok = cnt > 0
    return (s2.sum() - float(np.sum(s1[ok] ** 2 / cnt[ok]))) / n


def est_period(seg, lo=498.0, hi=506.0):
    """折叠周期（样本）。判据 = 折叠后组内方差最小。
    ★ 两级搜索：粗扫 0.02 → 细扫 0.002。单级粗扫会让模板被抹平到找不到方波边沿。"""
    P0 = min(np.arange(lo, hi, 0.02), key=lambda P: _foldvar(seg, P))
    P1 = min(np.arange(max(lo, P0 - 0.4), min(hi, P0 + 0.4), 0.002),
             key=lambda P: _foldvar(seg, P))
    return P1 if _foldvar(seg, P1) < _foldvar(seg, P0) else P0


def template_edges(tpl, P):
    """方波必有 2 个边沿：取 |diff| 全局峰，再在 ±P/2 邻域取第二峰。"""
    dd = np.abs(np.nan_to_num(np.diff(tpl)))
    e1 = int(np.argmax(dd))
    lo = int((e1 + P * 0.3) % P)
    hi = int((e1 + P * 0.7) % P)
    seg = dd[lo:hi] if lo < hi else np.concatenate([dd[lo:], dd[:hi]])
    e2 = (lo + int(np.argmax(seg))) % (len(dd))
    return sorted({e1, e2})


def flat_mask(Tr, P, ted, n, guard=40):
    """模板相位的平段掩码（离方波边沿 guard 样本以上）。"""
    ph = np.floor((np.arange(n) - Tr) % P).astype(int)
    m = np.ones(n, dtype=bool)
    for e in ted:
        d_ = np.abs(ph - e)
        d_ = np.minimum(d_, P - d_)
        m &= d_ > guard
    return m


def fold_template(x, T, e, P):
    """由 [T,e) 段折叠出模板（长度 ceil(P)）。返回 (模板, 组内样本数)。"""
    ii = np.arange(T, e)
    bi = np.floor((ii - T) % P).astype(int)
    nb = int(np.ceil(P))
    tpl = np.full(nb, np.nan)
    cnt = np.zeros(nb, dtype=int)
    for b in range(nb):
        v = x[ii[bi == b]]
        if len(v):
            tpl[b] = np.median(v)
            cnt[b] = len(v)
    return tpl, cnt


def dev_series(x, T, P, tpl, a, b):
    """[a,b) 相对模板的偏差序列（逐样本）。"""
    ii = np.arange(int(a), int(b))
    ph = np.floor((ii - T) % P).astype(int)
    ph = np.clip(ph, 0, len(tpl) - 1)
    return x[ii] - tpl[ph]


# ------------------------------------------------------------------ 分析
def analyse(tag, d, nbad):
    c2, c4, c5, c6 = d[:, 1], d[:, 3], d[:, 4], d[:, 5]
    x = c5 * UV                       # 主信号：滤波后 code 域，不削顶
    xr = (c2 - RAW_ZERO) * UV         # raw 域（未滤波）
    segs = segments(c6)
    R = {'tag': tag, 'd': d, 'x': x, 'xr': xr, 'segs': segs,
         'c4': c4, 'c6': c6}

    print('\n' + '=' * 80)
    print('== %s   n=%d（丢坏行 %d）%.2f s' % (tag, len(d), nbad, len(d) / FS))
    print('=' * 80)

    # [0] 列义审计
    m = (np.abs(c4) < 30000) & (np.abs(c5) > 1000)
    r = c5[m] / c4[m]
    print('\n[0] 列义审计')
    print('     列5/列4（未削顶子集 n=%d）：中位 %.6f  p5 %.4f  p95 %.4f  [期望 1.3601]'
          % (m.sum(), np.median(r), np.percentile(r, 5), np.percentile(r, 95)))
    print('     列4 削顶率 |列4|>=%d：%.2f%%  值域 [%.0f, %.0f] ⇒ ±%.0f µV'
          % (RAIL, 100 * np.mean(np.abs(c4) >= RAIL), c4.min(), c4.max(), (RAIL + 7) * LSB))
    print('     列5（主信号）值域 [%.0f, %.0f] µV' % (c5.min() * UV, c5.max() * UV))
    dr = c2 - RAW_ZERO
    print('     列2 raw 值域 [%.0f, %.0f] µV（零点 %d）' % (dr.min() * UV, dr.max() * UV, RAW_ZERO))

    # [1] dtcycles
    print('\n[1] 开关自检（dtcycles = 免仪器指标）')
    for nm, s, e in segs:
        mu = c6[s:e].mean()
        print('     %-4s [%6d,%6d) %6.2f s  %8.1f cycles = %5.2f%% CPU'
              % (nm, s, e, (e - s) / FS, mu, 100 * mu / PERIOD_CYC))
    on_m = np.mean([c6[s:e].mean() for nm, s, e in segs if nm != 'off'])
    off_m = np.mean([c6[s:e].mean() for nm, s, e in segs if nm == 'off'])
    print('     ⇒ on %.0f → off %.0f，跳节省 %.0f cycles = %.2f pp'
          % (on_m, off_m, on_m - off_m, 100 * (on_m - off_m) / PERIOD_CYC))
    R['dt'] = (on_m, off_m)

    # [2] 周期折叠模板（先做，后面的稳态/相位/瞬态都依赖它）
    s2, e2 = [(s, e) for nm, s, e in segs if nm == 'on2'][0]
    Tr = s2 + 1500                      # 模板参考起点（切换后 3 s，已稳定）
    Te = min(len(x), Tr + 2500)
    P = est_period(x[Tr:Te])
    tpl, cnt = fold_template(x, Tr, Te, P)
    amp = float(np.nanpercentile(tpl, 95) - np.nanpercentile(tpl, 5))
    ted = template_edges(tpl, P)
    fm = flat_mask(Tr, P, ted, len(x))
    print('\n[2] 周期折叠模板（1 Hz 方波 @500 Hz）')
    print('     折叠周期 P = %.3f 样本 = %.4f s（标称 500 / 1.000 s）｜ 组内方差 %.1f'
          % (P, P / FS, _foldvar(x[Tr:Te], P)))
    print('     模板幅度 p95−p5 = %.0f µV（标称 1000）｜ 方波边沿相位 %s（间隔 %.1f 样本）'
          % (amp, ted, (ted[1] - ted[0]) if len(ted) > 1 else float('nan')))
    R.update(P=P, tpl=tpl, tpl_range=(Tr, Te), amp=amp, ted=ted, fm=fm)

    # [3] 三段稳态（时域 + raw 域谱线）
    print('\n[3] 三段稳态（全段 = 含一切；平段 = 剔方波边沿 ±80 ms；边沿振铃 = 边沿后 8~240 ms）')
    st = {}
    segt = {}
    print('     %-4s %10s %8s %8s %9s %8s %9s %8s'
          % ('段', 'x p-p', '列4削顶', '方波幅', '全段RMS', '平段RMS', '边沿振铃', '50Hz'))
    for nm, s, e in segs:
        seg = x[s:e]
        rr = residual(seg)                            # 连续残差（仅用于谱线）
        rawres = residual(xr[s:e])
        # ★ 每段用「自己折叠」的模板：跨段套用 on2 的相位会累积误差
        #   （P 差 0.15 样本 ⇒ 28 s 后偏 ~8 样本 ⇒ 边沿掩码错位 ⇒ 「平段 RMS」被边沿污染）
        m_ = (e - s) // 4
        a0, b0 = s + min(1500, m_), e - m_
        Ps = est_period(x[a0:b0]) if b0 - a0 > 1500 else P
        tpls, _ = fold_template(x, a0, b0, Ps)
        teds = template_edges(tpls, Ps)
        # 平段掩码两档：g40 = 剔 ±80 ms（保留陷波边沿振铃）；g150 = 剔 ±300 ms（连振铃一起剔）
        fs = flat_mask(a0, Ps, teds, len(x))[s:e]
        flat = rr[fs] if fs.sum() > 50 else rr
        # 边沿振铃口径：方波边沿后 4~120 样本（0.2 s，正好覆盖陷波 Q20 的 τ≈200 ms）
        ring = np.zeros(e - s, dtype=bool)
        phs = np.floor((np.arange(s, e) - a0) % Ps).astype(int)
        for e_ in teds:
            d_ = (phs - e_) % Ps
            ring |= (d_ >= 4) & (d_ <= 120)
        sega = float(np.nanpercentile(tpls, 95) - np.nanpercentile(tpls, 5))
        segt[nm] = (a0, Ps, tpls, teds, sega)
        st[nm] = dict(s=s, e=e, pp=np.ptp(seg), rms=rms(flat),
                      ring=rms(rr[ring]) if ring.sum() > 50 else float('nan'),
                      rms_all=rms(rr), n=int(fs.sum()), nring=int(ring.sum()),
                      sq=sega, Ps=Ps,
                      r50=spec_line(rr, 50), r100=spec_line(rr, 100),
                      rail=100 * float(np.mean(np.abs(c4[s:e]) >= RAIL)),
                      raw50=spec_line(rawres, 50), raw150=spec_line(rawres, 150),
                      rawrms=rms(rawres))
        print('     %-4s %10.0f %7.1f%% %8.0f %9.1f %8.1f %9.1f %9.2f'
              % (nm, np.ptp(seg), st[nm]['rail'], sega, st[nm]['rms_all'],
                 st[nm]['rms'], st[nm]['ring'], st[nm]['r50']))
    print('     raw 域（源=列2，未滤波）：' + ' ｜ '.join(
        '%s 50Hz %.1f µV / 150Hz %.0f µV' % (nm, st[nm]['raw50'], st[nm]['raw150'])
        for nm, _, _ in segs))
    # on2 稳态（去掉切换后 1.5 s）
    a0, Ps_, tpls_, teds_, _ = segt['on2']
    _rr = residual(x[s2 + 750:e2])
    _f = flat_mask(a0, Ps_, teds_, len(x))[s2 + 750:e2]
    _ph = np.floor((np.arange(s2 + 750, e2) - a0) % Ps_).astype(int)
    _rg = np.zeros(e2 - s2 - 750, dtype=bool)
    for e_ in teds_:
        d_ = (_ph - e_) % Ps_
        _rg |= (d_ >= 4) & (d_ <= 120)
    st['on2_ss'] = dict(s=s2 + 750, e=e2, rms=rms(_rr[_f]), ring=rms(_rr[_rg]))
    print('     on2 稳态（去掉切换后 1.5 s）：全段 %.1f ｜ 平段 %.1f ｜ 边沿振铃 %.1f µV'
          % (rms(_rr), st['on2_ss']['rms'], st['on2_ss']['ring']))
    R['seg_tpl'] = segt
    print('     ⇒ 关陷波：全段 %.2f mV ｜ 平段 %.2f mV'
          % (st['off']['rms_all'] / 1e3, st['off']['rms'] / 1e3))
    print('     ⇒ 开陷波稳态：on2 全段 %.1f / 平段 %.1f µV'
          % (rms_all := st['off']['rms_all'] and st['on2']['rms_all'], st['on2_ss']['rms']))
    print('     ⇒ 抑制比（off 全段 / on2稳态 平段）= %.0f×'
          % (st['off']['rms_all'] / max(st['on2_ss']['rms'], 1e-9)))
    R['st'] = st


    # [4] 切换点相位（用模板相位）
    print('\n[4] 切换点相位（T49 定：判读 transient 必须记录距方波边沿时间）')
    sw = {}
    for nm, s, e in segs:
        if nm == 'on1':
            sw['on->off'] = e
        elif nm == 'on2':
            sw['off->on'] = s
    R['sw'] = sw
    def edge_ms(p):
        ph = (p - Tr) % P
        if not len(ted):
            return float('nan')
        d_ = np.abs(ted - ph)
        d_ = np.minimum(d_, P - d_)
        return float(d_.min()) / FS * 1000
    R['phase_edge_ms'] = {}
    for k, p in sw.items():
        near = edge_ms(p)
        R['phase_edge_ms'][k] = near
        print('     %-8s @ idx %6d (t=%6.2f s)：模板相位 %.1f 样本，距最近边沿 %6.0f ms ⇒ %s'
              % (k, p, p / FS, (p - Tr) % P, near,
                 '**贴边沿**（<20 ms）' if near < 20 else '常规相位（>20 ms）'))
    # on->off 的相位用 on1 段自己的模板判（Tr 只对 on2 有效）
    T1 = min(len(x), N1 := 1500)
    P1 = est_period(x[T1:T1 + 2500])
    tpl1, _ = fold_template(x, T1, T1 + 2500, P1)
    ted1 = template_edges(tpl1, P1)
    p = sw['on->off']
    d_ = np.abs(ted1 - ((p - T1) % P1))
    d_ = np.minimum(d_, P1 - d_)
    R['phase_edge_ms']['on->off'] = float(d_.min()) / FS * 1000
    print('     （on->off 用 on1 段模板另判：距边沿 %.0f ms）' % R['phase_edge_ms']['on->off'])

    # [5] 切换瞬态（模板法）
    print('\n[5] 切换瞬态（主信号列5；不削顶才看得见真实瞬态）')
    for k, p in sw.items():
        dd = np.abs(np.diff(x[p:p + 120]))
        print('     %-8s 单步 Δ %+9.1f µV ｜ 随后 120 样本 |Δ|max %9.1f ｜ |Δ|中位 %8.1f µV'
              % (k, x[p] - x[p - 1], dd.max(), np.median(dd)))

    p = sw['off->on']
    span = min(3.0, (len(x) - p) / FS - 0.1)
    rows = []
    for k in range(int(span * 1000 / 50)):
        a_ = p + k * 25
        b_ = a_ + 25
        dv = dev_series(x, Tr, P, tpl, a_, b_)
        f = fm[a_:b_]
        ok = bool(f.sum() >= 10)
        if ok:                                       # ★ 平段样本不足 ⇒ 判无效，绝不回退全窗
            dv = dv[f]
        else:
            dv = np.zeros(0)
        rows.append((k * 50, rms(dv) if ok else float('nan'),
                     float(np.ptp(dv)) if ok else float('nan'),
                     float(np.max(np.abs(dv))) if ok else float('nan'), int(f.sum())))
    print('\n     off→on 相对「稳态模板」的偏差（50 ms 窗；模板取自 t+3.0~8.0 s）')
    print('       t+ms  ' + ' '.join('%4d' % r[0] for r in rows[:12]))
    print('       RMS   ' + ' '.join('%4.0f' % r[1] for r in rows[:12]))
    print('       t+ms  ' + ' '.join('%4d' % r[0] for r in rows[12:24]))
    print('       RMS   ' + ' '.join('%4.0f' % r[1] for r in rows[12:24]))
    pk = max(rows, key=lambda r: r[1])
    print('       峰值窗 t+%d ms：RMS %.1f µV ／ 窗 p-p %.1f µV ／ 窗内 |dev|max %.1f µV'
          % (pk[0], pk[1], pk[2], pk[3]))
    print('       （×=该 50 ms 窗落在方波边沿上、平段样本<10，已剔除；模板相位残差在边沿处会假报几百 µV）')
    lvl = max(2.0 * st['on2_ss']['rms'], 30.0)
    rec, note = None, ''
    for i in range(len(rows) - 1, -1, -1):
        if rows[i][4] >= 10 and rows[i][1] > lvl:
            rec = rows[i + 1][0] if i + 1 < len(rows) else None
            break
    else:
        rec, note = 0, '（全程未超过判据 ⇒ 一开始就在稳态）'

    print('       恢复判据：平段窗 RMS 首次且此后持续 < max(2×稳态,30) = %.1f µV ⇒ %s'
          % (lvl, ('t+%d ms' % rec) if rec is not None else '未在 %.1f s 内恢复' % span) + note)
    if rows:
        print('       （各窗可用平段样本数 min/中位 = %d / %d）'
              % (min(r[4] for r in rows), int(np.median([r[4] for r in rows]))))
    R['rows'] = rows
    R['peak'] = pk
    R['rec'] = rec
    # 切换瞬间的工频瞬时值（判"瞬态 ∝ 输入瞬时值"）
    print('       切换瞬间：x = %+.0f µV（off 段工频 ±%.0f µV 的 %.0f%% 位置）'
          % (x[p], st['off']['raw50'] * 1.4142,
             100 * abs(x[p]) / max(st['off']['raw50'] * 1.4142, 1e-9)))
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='fin', default=FILE_IN)
    ap.add_argument('--notin', dest='fnot', default=FILE_NOTIN)
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    res = {}
    for tag, fn in [('620k 未接入', a.fnot), ('620k 接入', a.fin)]:
        d, nbad = load(os.path.join(HERE, fn))
        res[tag] = analyse(tag, d, nbad)
    N, I = res['620k 未接入'], res['620k 接入']

    print('\n' + '=' * 80)
    print('[6] in / notin 对比表')
    print('=' * 80)
    a_s, b_s = N['st'], I['st']
    print('   %-26s %16s %16s %10s' % ('指标', '620k 未接入', '620k 接入', '倍数'))
    print('   （注：off 段「方波幅」在 620k 接入时为 56 mV —— 那是被 1 s 折叠捕获的 50 Hz，不是方波，无意义）')
    print('   ' + '-' * 70)
    rows = [
        ('关陷波 全段 RMS（含50Hz）', a_s['off']['rms_all'], b_s['off']['rms_all'], 'µV'),
        ('关陷波 raw 50Hz', a_s['off']['raw50'], b_s['off']['raw50'], 'µV'),
        ('关陷波列4削顶率', a_s['off']['rail'], b_s['off']['rail'], '%'),
        ('开陷波 on1 平段 RMS', a_s['on1']['rms'], b_s['on1']['rms'], 'µV'),
        ('开陷波 on1 边沿振铃 RMS', a_s['on1']['ring'], b_s['on1']['ring'], 'µV'),
        ('开陷波 on2稳态 平段 RMS', a_s['on2_ss']['rms'], b_s['on2_ss']['rms'], 'µV'),
        ('方波幅度 on1（段模板）', a_s['on1']['sq'], b_s['on1']['sq'], 'µV'),
        ('方波幅度 on2稳态（段模板）', a_s['on2']['sq'], b_s['on2']['sq'], 'µV'),
        ('列4削顶率（on1）', a_s['on1']['rail'], b_s['on1']['rail'], '%'),
    ]
    for nm, va, vb, u in rows:
        rr = vb / va if abs(va) > 1e-9 else float('nan')
        print('   %-26s %14.1f%s %14.1f%s %9.2f×' % (nm, va, u, vb, u, rr))
    print('   %-26s %16s %14.0f×' % ('陷波抑制比(off全段/on稳态平段)', '—',
                                     b_s['off']['rms_all'] / max(b_s['on2_ss']['rms'], 1e-9)))

    # 09-18 输入阻抗口径（列④ 1 s 滑窗 p-p 中位）
    print('\n   09-18 输入阻抗口径（屏幕 p-p 1s 窗中位，源=列④）：')
    def pp1s(x):
        w = 500
        return float(np.median([np.ptp(x[i:i + w]) for i in range(0, len(x) - w + 1, 25)])) * LSB
    for nm, R in (('未接入', N), ('接入', I)):
        s2 = R['st']['on2_ss']['s']
        print('        %s：on1 %.1f µV ｜ on2稳态 %.1f µV'
              % (nm, pp1s(R['c4'][:R['sw']['on->off']]), pp1s(R['c4'][s2:])))
    r_ = pp1s(I['c4'][:I['sw']['on->off']]) / pp1s(N['c4'][:N['sw']['on->off']])
    print('        ⇒ 同口径比值（on1）= %.4f = %.2f%%  [门限 110%%] ⇒ %s'
          % (r_, 100 * r_, '达标' if r_ <= 1.10 else '超标'))

    print('\n[7] 判读')
    print('   T48 改前基线（低阻、2 Hz 方波）：off→on ~130 µV，τ≈200 ms')
    for tag in ('620k 未接入', '620k 接入'):
        R = res[tag]
        p = R['sw']['off->on']
        print('   %-10s off→on：单步 %+8.1f µV ｜ 峰窗 RMS %9.1f µV @ t+%d ms ｜ 恢复 %s ｜ 距边沿 %.0f ms'
              % (tag, R['x'][p] - R['x'][p - 1], R['peak'][1], R['peak'][0],
                 ('t+%d ms' % R['rec']) if R['rec'] is not None else '>%.0f s' % (len(R['rows']) * 0.05),
                 R['phase_edge_ms']['off->on']))

    if not a.fig:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(3, 2, figsize=(14.5, 12))
    for col, tag in enumerate(('620k 未接入', '620k 接入')):
        R = res[tag]
        x, c4, c6 = R['x'], R['c4'], R['c6']
        st, p = R['st'], R['sw']['off->on']
        t = np.arange(len(x)) / FS
        a0 = ax[0, col]
        a0.plot(t, x, lw=.5, color='#1f77b4', label='列5 滤波后 code 域（不削顶）')
        rr = np.abs(c4) >= RAIL
        if rr.any():
            a0.plot(t[rr], x[rr], ls='', marker='.', ms=1.5, color='#d62728',
                    label='列4 削顶处（占 %.0f%%）' % (100 * rr.mean()))
        for nm, s, e in R['segs']:
            a0.axvspan(s / FS, e / FS, alpha=.07,
                       color={'on1': 'tab:green', 'off': 'tab:red', 'on2': 'tab:green'}[nm])
        a0.set_title('%s\n关陷波平段 RMS %.2f mV ｜ 开陷波稳态 %.1f µV（抑制 %.0f×）'
                     % (tag, st['off']['rms'] / 1e3, st['on2_ss']['rms'],
                        st['off']['rms'] / max(st['on2_ss']['rms'], 1e-9)))
        a0.set_xlabel('时间 (s)'); a0.set_ylabel('µV')
        a0.legend(fontsize=7, loc='upper right'); a0.grid(alpha=.3)

        a1 = ax[1, col]
        a1.plot(t, c6, lw=.5, color='#7f7f7f')
        a1.set_title('dtcycles：on %.0f → off %.0f（跳节 −%.0f%%）—— 开关真生效的自检'
                     % (R['dt'][0], R['dt'][1], 100 * (1 - R['dt'][1] / R['dt'][0])))
        a1.set_xlabel('时间 (s)'); a1.set_ylabel('cycles/样本'); a1.grid(alpha=.3)

        w = 400
        seg = x[p - w:p + w]
        a2 = ax[2, col]
        a2.plot(np.arange(-w, w) / FS * 1000, seg, lw=1.1, color='#9467bd', label='实测（列5）')
        Tr, Te = R['tpl_range']
        ph = np.floor((np.arange(p - w, p + w) - Tr) % R['P']).astype(int)
        ph = np.clip(ph, 0, len(R['tpl']) - 1)
        a2.plot(np.arange(-w, w) / FS * 1000, R['tpl'][ph], lw=1.0, color='#2ca02c',
                ls='--', label='稳态模板（若从未切换）')
        a2.axvline(0, color='k', lw=.8, ls=':')
        lim = max(200.0, np.abs(seg).max() * 1.05)
        a2.set_ylim(-lim, lim)
        a2.set_title('(c) off→on 放大 ±800 ms（%s）\n单步 %+.1f µV ｜ 峰 %.0f µV @t+%d ms ｜ 恢复 %s'
                     % (tag, x[p] - x[p - 1], R['peak'][1], R['peak'][0],
                        ('t+%d ms' % R['rec']) if R['rec'] is not None else '>%.0f s' % (len(R['rows']) * .05)))
        a2.set_xlabel('相对切换时刻 (ms)'); a2.set_ylabel('µV')
        a2.legend(fontsize=7); a2.grid(alpha=.3)
    plt.tight_layout()
    out = os.path.join(HERE, 'T50_620k_开关对比_2026-09-20.png')
    plt.savefig(out, dpi=125)
    print('\n图已保存：%s' % out)


if __name__ == '__main__':
    main()
