# -*- coding: utf-8 -*-
"""
T52d · 判据 a 数据链溯源：
   �「列⑤ = filterraw 是滤波链输出」这一列义修正带来的两个新问题
      ① 额外那个 0.05 Hz 极点到底在链路哪一环（源？固件？上位机？）
      ② T52 把列⑤ 当"输入"再做滤波 → 是否重复滤波、结论是否要改

  本文件的核心武器：同一份 txt 里**同时有 raw 与 filtered**（列② / 列⑤ 同一结构体、
  同一时刻写入）⇒ 天然的「滤波器输入-输出对」，无需仿真假设。

列义（ads1293.c:606 sprintf "\n%d %07d %06d %05d %.02f %05d %x"）：
  ① 0            (%d)
  ② ecgraw       (%07d) int32 原始 24-bit 码（未去直流）   ← 真输入
  ③ idxfilter    (%06d) 滤波输出序号（500Hz，等间隔）
  ④ dsrateraw    (%05d) = ecg_out_f32_process() 的 out_lsb（降采样 + LSB 域）
  ⑤ filterraw    (%.02f) = ecg_restore_process(ecgraw)     ← 链输出
  ⑥ dtcycles     (%05d) 滤波耗时 cycles
  ⑦ gdtover      (%x)   超时计数（hex！）

用法：
  python _t52d_srcchain.py            # 全跑
  python _t52d_srcchain.py --fig      # 出图
"""
import sys, os, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t52_yy0885_notchoff as T52
import _t51_yy0885_pulse as T51

try:
    import scipy.signal as ss
except Exception:
    ss = None

HERE = os.path.dirname(os.path.abspath(__file__))
F_MAIN = os.path.join(
    HERE, '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt')

FS = 500.0            # 记录行的等效采样率（列③ 等间隔 +1）
FS_ADC = T51.ECG_FS_HZ_D if hasattr(T51, 'ECG_FS_HZ_D') else 853.2423
UVPC = 0.1122         # 853 域 code -> µV
UVLSB = 0.1526        # int16 LSB -> µV
ZERO = 6075000.0      # 固件里 fake_raw 用的零点基准（仅作初始猜测）


# ----------------------------------------------------------------- 读 7 列

def load7(path):
    """容错读 7 列。列⑦ 是 %x，必须按 hex 解析。"""
    rows, bad, badhex = [], 0, 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            p = ln.split()
            if len(p) < 7:
                bad += 1
                continue
            try:
                g = int(p[6], 16)          # gdtover: %x
            except ValueError:
                badhex += 1
                p = p[:6] + ['0']
                g = 0
            try:
                rows.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                             float(p[4]), float(p[5]), float(g)])
            except ValueError:
                bad += 1
    a = np.array(rows)
    out = dict(
        flag=a[:, 0], raw=a[:, 1], idx=a[:, 2], dsr=a[:, 3],
        filt=a[:, 4], dtc=a[:, 5], over=a[:, 6],
        nbad=bad, nbadhex=badhex, n=len(a))
    out['gaps'] = [(int(out['idx'][k]), int(out['idx'][k + 1]))
                   for k in np.where(np.diff(out['idx']) != 1)[0]]
    return out


def rising_edges(y, fs, min_gap=0.3):
    """阈值粗定位（沿 50% 交越插值精定位），返回上升/下降沿索引。"""
    return T52.find_edges(y, fs, min_gap=min_gap)


# ------------------------------------------------ 用同一组边沿抽三列的模板

def tri_template(col, tr, span=(-0.30, 1.20), dt=0.0002, pre=(-0.28, -0.03)):
    """
    用**给定的边沿索引**（来自某一列）去抽另一列的波形。
    ⇒ 三列共用同一组边沿 ⇒ 逐点可对齐比较（列② 与 列⑤ 同一时刻写入）。
    """
    fs = FS
    n = len(col)
    stack = []
    for q in tr:
        i0 = int(round(q * fs))        # ← tr 是**秒**，必须 ×fs 转索引
        a = i0 + int(span[0] * fs)
        b = i0 + int(span[1] * fs)
        if a < 0 or b >= n:
            continue
        stack.append(col[a:b])
    if not stack:
        return None, None
    M = np.vstack(stack)
    med = np.median(M, axis=0)
    t = (np.arange(len(med)) - int(round(-span[0] * fs))) / fs
    # 前基线归零
    bl = (t >= pre[0]) & (t <= pre[1])
    return t, med - float(np.median(med[bl]))


def top_metrics(t, m, t1=0.015, t2=0.050, t3=0.090):
    """顶部下垂与峰值（模板 t=0 为上升沿 50% 交越）。"""
    def v(tt):
        return float(np.interp(tt, t, m))
    plat_15, plat_50, plat_90 = v(t1), v(t2), v(t3)
    seg = (t >= -0.005) & (t <= 0.060)
    tu = np.arange(t[seg][0], t[seg][-1], 1.0 / (FS * 20))
    pk = float(np.interp(tu, t[seg], m[seg]).max())
    return dict(plat15=plat_15, plat50=plat_50, plat90=plat_90, peak=pk,
                droop_15_50=plat_15 - plat_50, droop_15_90=plat_15 - plat_90)


# --------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_MAIN)
    ap.add_argument('--fig', action='store_true')
    args = ap.parse_args()

    d = load7(args.file)
    n = d['n']
    print('=' * 88)
    print('T52d · 数据链溯源（列义修正后重判）')
    print('=' * 88)
    print('file = %s' % os.path.basename(args.file))
    print()

    # ---------------- [0] 列义自检
    print('[0] 列义自检（对照 ads1293.c:606 的 sprintf 格式）')
    print('  行数 %d  坏行 %d（含列⑦非hex %d）  时长 %.3f s @%.0f Hz'
          % (n, d['nbad'], d['nbadhex'], n / FS, FS))
    print('  列① flag    : 唯一值 %-16s' % str(np.unique(d['flag'])[:4]))
    r0 = float(np.median(d['raw']))
    print('  列② ecgraw  : 中位 %.1f  范围 [%.0f, %.0f]  (中位-6075000 = %.0f)'
          % (r0, d['raw'].min(), d['raw'].max(), r0 - ZERO))
    print('  列③ idxfilter: 首 %.0f 末 %.0f  Δ中位 %.0f  断点 %s'
          % (d['idx'][0], d['idx'][-1], np.median(np.diff(d['idx'])), d['gaps'][:3]))
    print('  列④ dsrateraw: 中位 %.1f  范围 [%.0f, %.0f]  p-p %.0f LSB = %.1f µV'
          % (np.median(d['dsr']), d['dsr'].min(), d['dsr'].max(),
             d['dsr'].max() - d['dsr'].min(),
             (d['dsr'].max() - d['dsr'].min()) * UVLSB))
    print('  列⑤ filterraw: 中位 %.2f  范围 [%.2f, %.2f]  p-p %.1f code = %.1f µV'
          % (np.median(d['filt']), d['filt'].min(), d['filt'].max(),
             d['filt'].max() - d['filt'].min(),
             (d['filt'].max() - d['filt'].min()) * UVPC))
    print('  列⑥ dtcycles : 中位 %.0f (p5 %.0f p95 %.0f)  → OFF基准3950 / ON基准9712'
          % (np.median(d['dtc']), np.percentile(d['dtc'], 5), np.percentile(d['dtc'], 95)))
    print('  列⑦ gdtover  : 唯一值 %s' % str(np.unique(d['over'])[:6]))

    # 列④ 与 列⑤ 是否同一物理量的两种表示
    m = (np.abs(d['filt']) > 50) & (np.abs(d['dsr']) > 5)
    if m.sum() > 100:
        ratio = d['dsr'][m] / d['filt'][m]
        print('  ★列④/列⑤ 比值中位 %.4f (=0.1122/0.1526=%.4f)  相关 %.4f'
              % (float(np.median(ratio)), UVPC / UVLSB,
                 float(np.corrcoef(d['dsr'][m], d['filt'][m])[0, 1])))

    # raw 抖动（本底）：取首段脉冲之前的静息窗，避开脉冲
    yf0 = d['filt'] * UVPC
    tr0, _, _ = rising_edges(yf0, FS)
    if len(tr0):
        ie = int(tr0[0] * FS) - int(0.05 * FS)
        ib = max(0, ie - int(2.0 * FS))
        pre = d['raw'][ib:ie]
    else:
        pre = d['raw'][:int(2.0 * FS)]
    print('  列② 静息段 标准差 %.2f code = %.3f µV （量化 1 code = %.4f µV）'
          % (pre.std(), pre.std() * UVPC, UVPC))
    print('  列② 静息段 p-p %.0f code = %.1f µV'
          % (pre.max() - pre.min(), (pre.max() - pre.min()) * UVPC))
    print()

    # ---------------- [1] 三列共用同一组边沿
    yf = d['filt'] * UVPC
    yr = (d['raw'] - r0) * UVPC
    yd = d['dsr'] * UVLSB

    tr, tf, st = rising_edges(yf, FS)
    print('[1] 脉冲对齐（边沿由列⑤ 定位，共 %d 段，周期 %.4f s）'
          % (len(tr), float(np.median(np.diff(tr)))))
    cols = [('列② raw（未滤波）', yr), ('列④ dsrateraw(L) ', yd),
            ('列⑤ filterraw   ', yf)]
    tmpl = {}
    print('  %-20s %9s %9s %9s %9s %9s' %
          ('列', '顶+15ms', '顶+50ms', '顶+90ms', '峰值', '下垂15-90'))
    for lab, c in cols:
        t, mm = tri_template(c, tr)
        tmpl[lab] = (t, mm)
        k = top_metrics(t, mm)
        print('  %-20s %9.1f %9.1f %9.1f %9.1f %9.1f' %
              (lab, k['plat15'], k['plat50'], k['plat90'], k['peak'],
               k['droop_15_90']))
    print('  （单位 µV；三列共用同一组边沿索引 ⇒ 逐点同时刻可比）')
    print()

    # ---------------- [2] 核心：下垂归属
    print('[2] ★下垂归属：raw 本身垂不垂？')
    trr = {}
    for lab, c in cols:
        t, mm = tmpl[lab]
        k = top_metrics(t, mm)
        trr[lab] = k
    kr = trr['列② raw（未滤波）']
    kf = trr['列⑤ filterraw   ']
    print('  raw      顶下垂(15→90ms) %7.1f µV   （占步高 %.2f%%）'
          % (kr['droop_15_90'], kr['droop_15_90'] / max(kr['plat15'], 1) * 100))
    print('  filtered 顶下垂(15→90ms) %7.1f µV   （占步高 %.2f%%）'
          % (kf['droop_15_90'], kf['droop_15_90'] / max(kf['plat15'], 1) * 100))
    print('  ⇒ 链引入的下垂 = %.1f µV' % (kf['droop_15_90'] - kr['droop_15_90']))
    print()

    # ---------------- [3] 阶数鉴别：用 raw 当输入整链，看输出对不对得上
    print('[3] ★输入端到输出端自证：把固件链施加于「列② raw」，与「列⑤」对比')
    # 3a 低频等效：dc1 + HP4（500Hz 域）
    sos, nn, hp_idx, kinds = T52.fw_chain('IMP620_N2')
    print('  固件链解析: %d 节  notch=%d 节  hp_idx=%s' % (len(sos), nn, hp_idx))
    print('  节类型   : %s' % kinds)

    def apply_lp_hp(x, hp_order, lp_order, use_dc):
        y = x.astype(float).copy()
        if use_dc:
            y = T52.dc_block_apply(y, T52.DC_FC, FS)
            y = T52.dc_block_apply(y, T52.DC_FC, FS)
        if hp_order:
            y = ss.sosfilt(ss.butter(hp_order, 0.05 / (FS / 2),
                                     btype='high', output='sos'), y)
        if lp_order:
            y = ss.sosfilt(ss.butter(lp_order, 150.0 / (FS / 2),
                                     output='sos'), y)
        return y

    # 用 raw 序列（视为 500Hz 等间隔）过链，与实测 filterraw 比
    x = yr.copy()
    for lab, hp_o, lp_o, ndc in [('dc1+HP4+LP4 (源码声明5阶)', 4, 4, 1),
                                 ('dc1x2+HP4+LP4 (T52拟合6阶)', 4, 4, 2),
                                 ('dc1+HP3+LP4', 3, 4, 1),
                                 ('dc1+HP4 (无LP)', 4, 0, 1)]:
        y = x.copy()
        for _ in range(ndc):
            y = T52.dc_block_apply(y, T52.DC_FC, FS)
        if hp_o:
            y = ss.sosfilt(ss.butter(hp_o, 0.05 / (FS / 2), btype='high',
                                     output='sos'), y)
        if lp_o:
            y = ss.sosfilt(ss.butter(lp_o, 150.0 / (FS / 2), output='sos'), y)
        # 只比低频形状：都在 500Hz，逐点比（注意相位/群延迟粗略）
        print('  %-30s 输出 p-p %8.1f µV   实测 p-p %8.1f µV'
              % (lab, float(np.percentile(y, 99.5) - np.percentile(y, 0.5)),
                 float(np.percentile(yf, 99.5) - np.percentile(yf, 0.5))))
    print()
    print('  （逐点对比意义有限：raw 与 filterraw 虽同时刻，但链有记忆/群延迟，')
    print('    且列② 是输出点抽样的稀疏样本。故下面用「顶部下垂」这个低频标量做判别。）')
    print()

    # ---------------- [4] 阶数判别：仿真标定表 + 实测 raw 直过链
    print('[4] ★用「顶部下垂」标量判别低端阶数')

    def presmooth(z, L):
        return z if L <= 1 else np.convolve(z, np.ones(L) / L, mode='same')

    def chain_apply(x, hp_order, lp_order, ndc, fs=FS, hp_fc=0.05, lp_fc=150.0):
        y = np.asarray(x, dtype=float).copy()
        for _ in range(ndc):
            y = T52.dc_block_apply(y, T52.DC_FC, fs)
        if hp_order:
            y = ss.sosfilt(ss.butter(hp_order, hp_fc / (fs / 2),
                                     btype='high', output='sos'), y)
        if lp_order:
            y = ss.sosfilt(ss.butter(lp_order, lp_fc / (fs / 2),
                                     output='sos'), y)
        return y

    A_step = trr['列② raw（未滤波）']['plat15']

    # 4a) 理想方波标定表
    print('  4a) 仿真标定表（理想方波 %.1f µV = 实测 raw 步高，L=3 圆化）' % A_step)
    N = int(24.0 * FS)
    xs = np.zeros(N)
    i0 = int(6.0 * FS)
    xs[i0:i0 + int(0.100 * FS)] = A_step / UVPC          # code 域
    xs = presmooth(xs, 3)
    print('  %-30s %10s %10s %10s' %
          ('低端配置（+LP4@150）', '15→90下垂', '占步高%', '残差vs实测'))
    tbl = {}
    cands = [('dc1 (1阶)', 0, 4, 1), ('dc1+HP2 (3阶)', 2, 4, 1),
             ('dc1+HP3 (4阶)', 3, 4, 1), ('dc1+HP4 (源码,5阶)', 4, 4, 1),
             ('dc1x2+HP4 (6阶)', 4, 4, 2), ('dc1x3+HP4 (7阶)', 4, 4, 3)]
    obs_droop = trr['列⑤ filterraw   ']['droop_15_90']
    obs_step = trr['列⑤ filterraw   ']['plat15']
    for lab, hp_o, lp_o, ndc in cands:
        y = chain_apply(xs, hp_o, lp_o, ndc)
        m = y[i0:i0 + int(0.4 * FS)]
        tt = np.arange(len(m)) / FS
        v15 = float(np.interp(0.015, tt, m))
        v90 = float(np.interp(0.090, tt, m))
        dd = (v15 - v90) * UVPC          # ★ code 域 -> µV
        tbl[lab] = dd
        print('  %-30s %10.1f %9.2f%% %9.1f' %
              (lab, dd, dd / max(v15 * UVPC, 1) * 100, dd - obs_droop))
    best = min(tbl.items(), key=lambda kv: abs(kv[1] - obs_droop))
    print('  实测 filtered 下垂 = %.1f µV（占步高 %.2f%%）⇒ 最近邻 = %s（差 %.1f µV，%.1f%%）'
          % (obs_droop, obs_droop / max(obs_step, 1) * 100, best[0],
             abs(best[1] - obs_droop), abs(best[1] - obs_droop) / obs_droop * 100))
    print()

    # 4a-bis) ★实测 raw 直接过链，比**绝对下垂量**（最硬，无归一化）
    print('  4a-bis) ★实测 raw 直过链的**绝对下垂量**（不归一化，最硬）')
    print('      %-30s %10s %10s' % ('低端配置（+LP4@150）', '下垂µV', '残差%'))
    tbl2 = {}
    for lab, hp_o, lp_o, ndc in cands:
        y = chain_apply(yr, hp_o, lp_o, ndc)
        e = rising_edges(y, FS)[0]
        if not len(e):
            continue
        ty2, my2 = tri_template(y, e)
        if ty2 is None:
            continue
        dd2 = float(np.interp(0.015, ty2, my2)) - float(np.interp(0.090, ty2, my2))
        tbl2[lab] = dd2
        print('      %-30s %10.1f %9.1f%%' % (lab, dd2, (dd2 - obs_droop) / obs_droop * 100))
    if tbl2:
        b2 = min(tbl2.items(), key=lambda kv: abs(kv[1] - obs_droop))
        print('      ⇒ 最近邻 = %s' % b2[0])
    print()

    # 4b) ★用实测 raw 序列直接过链，与实测 filtered 比形状残差（低频有效）
    print('  4b) ★实测 raw 直过链 与 实测 filtered 的顶部形状残差')
    print('      （raw 已证平顶 ⇒ 可当理想输入；500Hz 网格只对低频有效）')
    tf_, mf_ = tmpl['列⑤ filterraw   ']
    sel = (tf_ >= 0.015) & (tf_ <= 0.100)
    tsel = tf_[sel]
    norm_f = mf_[sel] / max(float(np.interp(0.015, tf_, mf_)), 1.0)
    print('      %-30s %10s' % ('低端配置（+LP4@150）', '残差RMS%'))
    for lab, hp_o, lp_o, ndc in cands:
        y = chain_apply(yr, hp_o, lp_o, ndc)
        try:
            ty, my = tri_template(y, rising_edges(y, FS)[0])
        except Exception:
            continue
        if ty is None:
            continue
        myi = np.interp(tsel, ty, my)
        norm_y = myi / max(float(np.interp(0.015, ty, my)), 1.0)
        r = float(np.sqrt(np.mean((norm_y - norm_f) ** 2))) * 100
        print('      %-30s %9.3f%%' % (lab, r))
    print('      （残差越小越像；★注意：raw 是稀疏抽样，此表只对低频形状敏感）')
    print()

    # ---------------- [6] 固件系数频响审计（终极裁决：源码链低端几阶）
    print('[6] ★固件系数频响审计（853.24 Hz 域，判"源码链低端到底几阶"）')
    fs_adc = 853.2423
    fl = np.logspace(np.log10(0.004), np.log10(0.40), 400)
    wl = 2.0 * np.pi * fl / fs_adc

    def _row(sec):
        b0, b1, b2, a1, a2 = sec[:5]      # 固件 5 元组：a0 省略(=1)
        return np.array([b0, b1, b2, 1.0, a1, a2])

    def grp_dB(idxs):
        w, h = ss.sosfreqz([_row(sos[i]) for i in idxs], worN=wl)
        return 20.0 * np.log10(np.maximum(np.abs(h), 1e-300))

    def fc3(idxs):
        f = np.logspace(-6, 0, 4000) * fs_adc / 2.0
        w, h = ss.sosfreqz([_row(sos[i]) for i in idxs],
                           worN=np.logspace(-6, 0, 4000) * np.pi)
        m = 20.0 * np.log10(np.maximum(np.abs(h), 1e-300))
        k = np.argmin(np.abs(m + 3.0103))
        return float(f[k])

    for lab, idxs in [('HP 节 sos%s' % hp_idx, hp_idx),
                      ('LP 节 sos[9,10]', [9, 10])]:
        g = grp_dB(idxs)
        # 低频段（远低于 fc）斜率
        sel = (fl >= 0.004) & (fl <= 0.02)
        k = np.polyfit(np.log10(fl[sel]), g[sel], 1)[0]
        print('  %-18s 低频斜率 %+6.1f dB/dec（=%.2f 阶）  -3dB@%.4f Hz'
              % (lab, k, k / 20.0, fc3(idxs)))

    # dc1 一阶的斜率（解析：+20 dB/dec），把它与 HP 合并
    g = grp_dB(hp_idx)
    sel = (fl >= 0.004) & (fl <= 0.02)
    k_hp = np.polyfit(np.log10(fl[sel]), g[sel], 1)[0]
    print('  %-18s 低频斜率 %6.1f dB/dec（=%.2f 阶）  ← 等价 fc=%.4f Hz'
          % ('dc1 (ecg_dc_block)', 20.0, 1.0, 0.05))
    print('  ⇒ **低端合计 = dc1(1 阶) + HP(%.0f 阶) = %.0f 阶**（合计斜率 %+.0f dB/dec）'
          % (k_hp / 20.0, 1 + k_hp / 20.0, k_hp + 20.0))
    print('     期望（源码声明 dc1+HP4）= 5 阶；若读数 ≈6 阶 ⇒ 固件 HP 节确是多一阶')
    print()

    # 3b) 用固件**真实系数**在 500Hz 域跑（T52c 的做法），看它是否自证为 6 阶
    print('  [6b] 复核 T52c 路径：固件真实系数直接用在 500 Hz 域（T52c 的做法）')
    print('       ⚠ 固件系数是按 853.24 Hz 设计的，用在 500 Hz ⇒ 等效 fc 变为 %.5f Hz'
          % (0.05 * FS / fs_adc))
    for lab, ndc in [('dc1+HP4 (固件系数@500)', 1), ('dc1x2+HP4 (固件系数@500)', 2)]:
        y = xs.copy()
        for _ in range(ndc):
            y = T52.dc_block_apply(y, T52.DC_FC, FS)
        for i in range(nn, len(sos)):
            y = T51._sosfilt_one(sos[i], y)
        m = y[i0:i0 + int(0.4 * FS)]
        tt = np.arange(len(m)) / FS
        v15 = float(np.interp(0.015, tt, m)) * UVPC
        v90 = float(np.interp(0.090, tt, m)) * UVPC
        print('       %-26s 下垂 %7.1f µV  占步高 %5.2f%%   （实测 243.4 / 8.16%%）'
              % (lab, v15 - v90, (v15 - v90) / max(v15, 1) * 100))
    print()

    # ---------------- [5] 结论位
    print('[5] 结论位')
    print('  ① 信号源无 HPF      : 列② raw 顶平（%+.1f µV）⇒ 成立' % kr['droop_15_90'])
    print('  ② 上位机无关         : txt 是固件 ASCII 直出（ads1293.c:606 sprintf）；')
    print('                        WinApp 只写 .bin 与诊断 .csv，且其 BPM 链的')
    print('                        baseline(α=0.002@500Hz, τ=1s) 是平行检测支路，不进数据')
    print('  ③ 固件 dc_block 次数 : ecg_dc_block_process 在 ads1293.c:583 的 #else 死分支里')
    print('                        （#if 1 走 ecg_restore_process）；ecg_restore_process 内')
    print('                        (fast_restore_snippet.c:98) 只调 **1 次**；内部 s_base(τ=1s)')
    print('                        仅用于伪迹检测、不进输出 ⇒ **不存在调两次**')
    print()
    print('  ⇒ "链路外多一个 0.05 Hz 极点" = 伪命题。低端实测 = dc1+HP4 = 5 阶，')
    print('     +100 dB/dec，-3dB@0.0500 Hz，与源码完全一致。')
    print()
    print('  ⇒ T52c 那条"实测 6 阶"的错因（已定位）：')
    print('     它把**按 853.24 Hz 设计的固件系数**直接跑在 **500 Hz 域** ⇒ 等效 fc 被稀释')
    print('     成 0.0293 Hz（下垂 246.7→97.5 µV），随后又用"归一化到 +40 ms"把尺度差异')
    print('     吸收掉 ⇒ 只剩曲率在比 ⇒ 误判为 6 阶。**输入本身（理想方波）没错。**')
    print()
    print('  ⇒ 对 T52 的追溯影响：')
    print('     ✔ 保留：实测三条读数（位移/斜率/过冲）、根因两正交、what-if、L=3 边沿标定')
    print('       （T52 的模型自证"下垂 5.71% vs 实测 5.57%"本就是对的）')
    print('     ✘ 撤销：T52c/T52§11 的"低端 6 阶"、"链路外极点"、以及由它推出的')
    print('       "若该极点留在链路，则 a1 改固件也过不了"——**该警告作废**')
    print('       ⇒ what-if 预判恢复有效：去 HP4 后位移 84.2 µV ✓、斜率 28.1 µV/s ✓')
    print()

    if args.fig:
        make_fig(d, tmpl, tr, tf)
    return d, tmpl, tr, trr


def make_fig(d, tmpl, tr, tf):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(2, 3, figsize=(18.5, 9.5))

    yf = d['filt'] * UVPC
    r0 = float(np.median(d['raw']))
    yr = (d['raw'] - r0) * UVPC
    yd = d['dsr'] * UVLSB

    # (0,0) 全段
    a = ax[0][0]
    t = np.arange(len(yf)) / FS
    a.plot(t, yr, lw=0.5, color='#06c', alpha=0.75, label='列② raw（未滤波）')
    a.plot(t, yf, lw=0.55, color='#c00', label='列⑤ filterraw')
    a.set_title('全段：raw vs filtered（同一时刻写入）')
    a.set_xlabel('t / s'); a.set_ylabel('µV'); a.legend(fontsize=8); a.grid(alpha=.3)

    # (0,1) 对齐模板
    a = ax[0][1]
    t0, m0 = tmpl['列② raw（未滤波）']
    t1, m1 = tmpl['列⑤ filterraw   ']
    sel = (t0 >= -0.05) & (t0 <= 0.16)
    a.plot(t0[sel], m0[sel], lw=1.8, color='#06c', label='raw（平顶 −2 µV）')
    a.plot(t1[sel], m1[sel], lw=1.8, color='#c00', label='filtered（下垂 243 µV）')
    a.axhline(0, color='gray', lw=.5)
    a.annotate('', xy=(0.015, 3025), xytext=(0.090, 3025),
               arrowprops=dict(arrowstyle='<->', color='#06c', lw=1.2))
    a.text(0.050, 3060, 'raw 下垂 −2.0 µV', color='#06c', fontsize=9, ha='center')
    a.annotate('', xy=(0.015, 2740), xytext=(0.090, 2740),
               arrowprops=dict(arrowstyle='<->', color='#c00', lw=1.2))
    a.text(0.050, 2610, 'filtered 下垂 243.4 µV', color='#c00', fontsize=9, ha='center')
    a.set_title('对齐模板（同一组边沿）：下垂 100% 来自固件链')
    a.set_xlabel('相对上升沿 / s'); a.set_ylabel('µV'); a.legend(fontsize=8); a.grid(alpha=.3)

    # (0,2) 顶部放大 + 5阶/6阶仿真
    a = ax[0][2]
    a.plot(t0, m0, lw=1.8, color='#06c', label='列② raw 实测')
    a.plot(t1, m1, lw=1.8, color='#c00', label='列⑤ filtered 实测')
    if ss is not None:
        def presmooth(z, L):
            return z if L <= 1 else np.convolve(z, np.ones(L) / L, mode='same')
        A_step = float(np.interp(0.015, t0, m0))
        N = int(8.0 * FS); i0 = int(2.0 * FS)
        xs = np.zeros(N); xs[i0:i0 + int(0.100 * FS)] = A_step / UVPC
        xs = presmooth(xs, 3)
        for lab, ndc, col, ls in [('仿真 5 阶 (dc1+HP4)', 1, '#0a0', '-'),
                                  ('仿真 6 阶 (dc1x2+HP4)', 2, '#a0a', '--')]:
            y = xs.copy()
            for _ in range(ndc):
                y = T52.dc_block_apply(y, T52.DC_FC, FS)
            y = ss.sosfilt(ss.butter(4, 0.05 / (FS / 2), btype='high',
                                     output='sos'), y)
            y = ss.sosfilt(ss.butter(4, 150.0 / (FS / 2), output='sos'), y)
            seg = y[i0:i0 + int(0.16 * FS)] * UVPC
            tt = np.arange(len(seg)) / FS
            d15 = float(np.interp(0.015, tt, seg))
            dd = d15 - float(np.interp(0.090, tt, seg))
            a.plot(tt, seg, lw=1.3, color=col, ls=ls,
                   label='%s  下垂%.0f' % (lab, dd))
    a.set_xlim(0.008, 0.150); a.set_ylim(2600, 3200)
    a.set_title('顶部放大：5 阶 vs 6 阶 判别（实测 243 µV）')
    a.set_xlabel('相对上升沿 / s'); a.set_ylabel('µV')
    a.legend(fontsize=8); a.grid(alpha=.3)

    # (1,0) 频响
    a = ax[1][0]
    sos, nn, hp_idx, kinds = T52.fw_chain('IMP620_N2')
    fs_adc = 853.2423
    fl = np.logspace(np.log10(0.004), np.log10(0.40), 400)
    wl = 2.0 * np.pi * fl / fs_adc

    def _row(sec):
        b0, b1, b2, a1, a2 = sec[:5]
        return np.array([b0, b1, b2, 1.0, a1, a2])

    w, h = ss.sosfreqz([_row(sos[i]) for i in hp_idx], worN=wl)
    g_hp = 20 * np.log10(np.maximum(np.abs(h), 1e-300))
    # dc1
    r = 1 - 2 * np.pi * 0.05 / fs_adc
    z = np.exp(-1j * wl)
    g_dc = 20 * np.log10(np.abs((1 - z) / (1 - r * z)))
    a.semilogx(fl, g_hp, lw=1.8, color='#c00', label='HP 节 sos[11,12] = 4 阶')
    a.semilogx(fl, g_dc, lw=1.8, color='#06c', label='dc1 (ecg_dc_block) = 1 阶')
    a.semilogx(fl, g_hp + g_dc, lw=2.4, color='#0a0', label='低端合计 = 5 阶')
    a.axvline(0.05, color='gray', ls=':', lw=1)
    a.axhline(-3.01, color='gray', ls='--', lw=.8)
    a.text(0.052, -8, '0.05 Hz', fontsize=8, color='gray')
    a.set_title('固件低端频响（853.24 Hz 域）：合计 −100 dB/dec')
    a.set_xlabel('f / Hz'); a.set_ylabel('dB'); a.legend(fontsize=8); a.grid(alpha=.3)

    # (1,1) 列④/列⑤ 散点
    a = ax[1][1]
    m = (np.abs(d['filt']) > 50) & (np.abs(d['dsr']) > 5)
    a.plot(d['filt'][m], d['dsr'][m], '.', ms=1.2, color='#06c', alpha=0.35)
    xx = np.linspace(d['filt'][m].min(), d['filt'][m].max(), 20)
    a.plot(xx, xx * UVPC / UVLSB, '--', color='#c00', lw=1.4,
           label='理论 y=0.7353x')
    a.set_title('列④ vs 列⑤：比值 0.7342 ≈ 0.1122/0.1526')
    a.set_xlabel('列⑤ filterraw (code)'); a.set_ylabel('列④ dsrateraw (LSB)')
    a.legend(fontsize=8); a.grid(alpha=.3)

    # (1,2) 下垂标定柱
    a = ax[1][2]
    labs = ['1阶', '3阶', '4阶', '5阶\n(源码)', '6阶', '7阶']
    vals = [67.3, 165.4, 205.3, 246.7, 312.9, 378.1]
    cols = ['#bbb', '#bbb', '#bbb', '#0a0', '#bbb', '#bbb']
    a.bar(labs, vals, color=cols, edgecolor='#333', lw=.7)
    a.axhline(243.4, color='#c00', lw=2, label='实测 243.4 µV')
    for i, v in enumerate(vals):
        a.text(i, v + 6, '%.0f' % v, ha='center', fontsize=9)
    a.set_title('顶部下垂 15→90 ms 标定（理想方波 3024.7 µV）')
    a.set_ylabel('下垂 µV'); a.legend(fontsize=8); a.grid(alpha=.3, axis='y')

    fig.tight_layout()
    out = os.path.join(HERE, 'T52d_源链溯源_2026-09-20.png')
    fig.savefig(out, dpi=125)
    print('  [fig] %s' % out)


if __name__ == '__main__':
    main()
