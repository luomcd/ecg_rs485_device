# -*- coding: utf-8 -*-
"""T65 步骤 0：四份 620k 数据的**身份校验**（只核对描述，不做分析、不做结论）。

校验项
  A 列语义      raw853 on  ⇒ 列③(ifilt)/列④(dsr) 恒 0 ；off ⇒ 非 0
  B 采样域      idx 增量**均值** ⇒ 853 域(1.0000) / 500 域(1.7065)
                ★ 不能用中位：500 域增量序列是 2,1,2,2,2,1…（2 占 70.6%）⇒ 中位必落 2
  C notch 状态  dtcycles 指纹分段：ON≈9069(12 节) / OFF≈3297(3 节)
  D 620k 接入   列② raw 的 p-p 与频带 rms（接入应高 1~2 个数量级）
  E 完整性      坏行 / 丢样本 / 段长 / 切换瞬态

★ 每段统计一律**去掉首尾**（切 notch 的瞬态会污染 min/max，实测能虚高 18 倍）。
★ 只读数据，不改任何源码。
"""
import io, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t56_raw853_lowfreq as T56

FS853 = 853.2423208191125
FS500 = 500.0
UVPC = 0.1122
ONTH = 6000.0          # dtcycles 判 ON 的阈值（12 节 ON≈9069 / 3 节 OFF≈3297）

FILES = [
    ('620kin-raw853on  ', '620kin-raw835on-2026-09-20 215252.txt',        True,  'on'),
    ('620kin-raw853off ', '620kin-raw835off(on-off-on)-2026-09-20 215252.txt', True,  'off'),
    ('620knotin-off    ', '620knotin-raw835off(on-off-on)-2026-09-20 215252.txt', False, 'off'),
    ('620knotin-on     ', '620knotin-raw835on-2026-09-20 215252.txt',       False, 'on'),
]

BANDS = [(0.3, 2, '0.3~2'), (2, 45, '2~45'), (45, 55, '45~55'),
         (55, 145, '55~145'), (145, 155, '145~155'), (155, 245, '155~245'),
         (245, 340, '245~340'), (340, 426, '340~426')]


def band_rms(y, fs, bands):
    """带内积分 rms（µV）。★ 不用单点查表——工频漂 0.02 Hz 单点读数就腰斩。"""
    y = np.asarray(y, float)
    y = y - y.mean()
    w = np.hanning(len(y))
    F = np.fft.rfft(y * w)
    f = np.fft.rfftfreq(len(y), 1.0 / fs)
    rmsb = (2.0 * np.abs(F) / w.sum()) / np.sqrt(2.0)
    out = {}
    for lo, hi, nm in bands:
        m = (f >= lo) & (f < hi)
        out[nm] = float(np.sqrt(np.sum(rmsb[m] ** 2))) * UVPC if m.sum() else float('nan')
    return out


def seg_report(lab, fn, exp_in, exp_mode):
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        print('══════ %s  **文件缺失**\n' % lab)
        return None
    d = T56.load7(p)
    idx = d['idx'].astype(np.int64)
    n = d['n']
    step = np.diff(idx)
    mean_step = float(step.mean())

    print('══════ %s  %s' % (lab, fn))
    print('  · 行数 %d ｜ 坏行(len≠7) %d' % (n, d['nbad']))

    if abs(mean_step - 1.0) < 0.02:
        dom, fs_use, the_step = '853 域', FS853, 1
    elif abs(mean_step - 1.7065) < 0.02:
        dom, fs_use, the_step = '500 域', FS500, 2
    else:
        dom, fs_use, the_step = '**未知**', FS500, None
    uv, uc = np.unique(step, return_counts=True)
    print('  · 采样域：**%s**（idx 增量均值 %.4f ｜ 分布 %s）'
          % (dom, mean_step,
             ', '.join('%d:%.1f%%' % (v, 100.0 * c / len(step))
                       for v, c in zip(uv, uc))))
    t = (idx - idx[0]) / fs_use
    print('  · 时长 %.2f s（fs=%.2f）' % (t[-1], fs_use))

    if the_step is not None:
        over = step[step > the_step]
        lost = int((over - the_step).sum())
        print('  · 丢样本 %s' % ('0 ✓' if lost == 0 else
              '%d 个 / %d 处 ⇒ %s' % (lost, len(over),
              ', '.join('t=%.1fs跳%d' % (t[k], step[k])
                        for k in np.where(step > the_step)[0][:6]))))

    z3 = bool(np.all(d['ifilt'] == 0.0))
    z4 = bool(np.all(d['dsr'] == 0.0))
    ok = (z3 and z4) if exp_mode == 'on' else (not z3 and not z4)
    print('  · 列③ ifilt 全0=%s ｜ 列④ dsr 全0=%s ⇒ %s（raw853 %s 应%s）'
          % ('是' if z3 else '否', '是' if z4 else '否',
             '一致' if ok else '**不一致**', exp_mode,
             '全0' if exp_mode == 'on' else '非0'))

    # ---------------- 分段 ----------------
    on = d['dtc'] > ONTH
    sw = np.where(np.diff(on.astype(np.int8)) != 0)[0]
    bounds = [0] + list(sw + 1) + [n]
    nseg = len(bounds) - 1
    exp_segs = 1 if exp_mode == 'on' else 3
    print('  · dtcycles：中位 %.0f ｜ 范围 %d~%d ｜ ON 占比 %.1f%% ｜ 切段数 %d（期望 %d）'
          % (np.median(d['dtc']), d['dtc'].min(), d['dtc'].max(),
             100 * on.mean(), nseg, exp_segs))
    exp_states = ['ON'] if exp_mode == 'on' else ['ON', 'OFF', 'ON']
    ok_seg = (nseg == exp_segs) and all(
        ('ON' if on[bounds[k]] else 'OFF') == exp_states[k] for k in range(nseg))
    print('  · 状态序：%s ⇒ %s（期望 %s）'
          % (' → '.join('ON' if on[bounds[k]] else 'OFF' for k in range(nseg)),
             '一致' if ok_seg else '**不一致**', ' → '.join(exp_states)))

    print('  · 逐段（统计一律去首尾：首 4 s / 尾 1 s，避开切换瞬态）')
    print('    %-4s %-17s %7s %7s %7s  %10s %10s %10s   %s'
          % ('状态', '时间窗(s)', '段长s', 'dtc', '节数', '列②p-p', '列⑤p-p', '列④p-p', '带内 rms (µV)'))
    rows = []
    for k in range(nseg):
        a, b = bounds[k], bounds[k + 1]
        st = 'ON' if on[a] else 'OFF'
        dm = float(np.median(d['dtc'][a:b]))
        dur = (b - a) / fs_use
        # 去首尾
        ia = a + int(4.0 * fs_use) if (b - a) > int(6.0 * fs_use) else a
        ib = b - int(1.0 * fs_use) if (b - a) > int(6.0 * fs_use) else b
        raw = d['raw'][ia:ib].astype(float)
        flt = d['filt'][ia:ib].astype(float)
        dsr = d['dsr'][ia:ib].astype(float)
        pp_raw = (raw.max() - raw.min()) * UVPC
        pp_flt = (flt.max() - flt.min()) * UVPC
        pp_dsr = (dsr.max() - dsr.min()) * UVPC
        bm = band_rms(raw, fs_use, BANDS)
        show = ['%s:%.0f' % (nm, bm[nm]) for _lo, _hi, nm in BANDS
                if not np.isnan(bm[nm]) and bm[nm] > 1.0]
        rows.append((st, (t[a], t[b - 1]), dur, dm, raw, flt, dsr))
        print('    %-4s %6.2f~%-10.2f %7.2f %7.0f %7.2f  %9.0f %10.0f %10s   %s'
              % (st, t[a], t[b - 1], dur, dm, (dm - 1373.0) / 641.3,
                 pp_raw, pp_flt, ('%.0f' % pp_dsr) if pp_dsr > 0 else '—(全0)',
                 ' '.join(show)))
    # 列⑤ 频带（只对最长段，853 域才看高频）
    kk = int(np.argmax([r[2] for r in rows]))
    st, (ta, tb), dur, dm, raw, flt, dsr = rows[kk]
    bm = band_rms(flt, fs_use, BANDS)
    print('    └ [列⑤ filt] 最长段(%s, %.1f s) 频带 rms：%s'
          % (st, dur, '  '.join('%s:%.1f' % (nm, bm[nm]) for _lo, _hi, nm in BANDS
                                if not np.isnan(bm[nm]) and bm[nm] > 0.5)))
    # 身份：接入 vs 未接
    pp_main = rows[kk][4]
    ppv = (pp_main.max() - pp_main.min()) * UVPC
    print('  · 620k 身份：最长段列② p-p = %.0f µV ⇒ 期望 %s'
          % (ppv, '接入（应 ≫1 mV 级）' if exp_in else '未接（应 ~1 mV 级）'))
    print()
    return dict(dom=dom, nseg=nseg, ok_seg=ok_seg, ok_col=ok, ppv=ppv,
                nok=n, nbad=d['nbad'])


def main():
    print('=' * 104)
    print('T65-0 · 四份 620k 数据身份校验')
    print('=' * 104)
    res = {}
    for lab, fn, exp_in, exp_mode in FILES:
        res[lab] = seg_report(lab, fn, exp_in, exp_mode)

    print('=' * 104)
    print('汇总（期望 vs 实测）')
    print('=' * 104)
    print('%-20s %-8s %-8s %-6s %-8s %-8s' %
          ('文件', '采样域', '列③④', '切段', '状态序', '坏行'))
    need = [('620kin-raw853on  ', '853 域'), ('620kin-raw853off ', '500 域'),
            ('620knotin-off    ', '500 域'), ('620knotin-on     ', '853 域')]
    allok = True
    for lab, dom_exp in need:
        r = res.get(lab)
        if r is None:
            print('%-20s **缺失**' % lab); allok = False; continue
        okd = (r['dom'] == dom_exp)
        okc = '✓' if r['ok_col'] else '✗'
        oks = '✓' if r['ok_seg'] else '✗'
        if not (okd and r['ok_col'] and r['ok_seg'] and r['nbad'] <= 2):
            allok = False
        print('%-20s %-8s %-8s %-6s %-8s %-8d' %
              (lab, ('%s ✓' % r['dom']) if okd else ('%s ✗(期望%s)' % (r['dom'], dom_exp)),
               okc, oks, oks, r['nbad']))
    print()
    print('⇒ 总体：%s' % ('**四份描述全部一致**' if allok else '**有项不一致，见上**'))


if __name__ == '__main__':
    main()
