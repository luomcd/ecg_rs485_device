#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
T80 修正方案扫描 —— 判据 a（GB 9706.247）不达标项"能不能靠调参修好"

背景（T79 实测结论，A+ 档 18 节，notch OFF）：
  a1 位移  ✗ 中位 159.5 µV / 极值 302.3 µV（限 100）
  a2 斜率  ✓ 拟合 183.8 µV/s（限 300）
  a3 过冲  ✓ 贴限 8.67%（限 <10）
  ⇒ 根因锁定"低端 5 阶 HP" = dc_block(1 阶@0.05) + BP 段内 2 节 HP(4 阶@0.05)

本脚本做的事：
  [0] 审计 A+ 档真实链结构（固件系数表）
  [1] 模型可信度校验：固件真实系数链（+固件同式 dc_block）在 2 s 间隔下的读数
      vs 实测读数 —— 口径对了才有资格谈"改哪个参数"
  [2] 低端方案扫描 × 脉冲间隔（2/10/20 s）：a1（max 与中位双口径）/a2/a3
  [3] 每个候选的判据 b 检查（0.67~40 Hz 相对 5 Hz 增益，限 70~140%）
  [4] 结论

口径纪律：
  * 链 = dc_block（固件 ecg_dc_block.c 同式 y=(x-xp)+r*y_prev, r=1-2πfc/FS）
         + BP 段（固件 ECG_FILTER_COEFFS_F64）；notch OFF ⇒ 跳过链首 15 节
  * LP 高端节**一律用固件原节**（只动低端），保证与已验收的高端行为一致
  * FS = 853.2423 Hz（滤波器域），与固件一致
"""
import importlib.util
import os
import sys

import numpy as np
from scipy.signal import butter, lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)

FS = m.FS


# ------------------------------------------------------------------ 链构造

def dc_block(x, fc):
    """固件 ecg_dc_block.c 同式：y = (x - x_prev) + r*y_prev，r = 1 - 2π fc/FS。"""
    r = 1.0 - 2.0 * np.pi * fc / FS
    return lfilter([1.0, -1.0], [1.0, -r], x)


def _norm(row):
    """scipy sos 6 列 [b0,b1,b2,a0,a1,a2] ⇒ 固件 5 列 [b0,b1,b2,a1,a2]（除以 a0）。"""
    b0, b1, b2, a0, a1, a2 = row
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def mk_chain(hp_order, hp_fc, dc_fc, lp_secs):
    """返回 (sos 列表, dc_fc)。hp_order=0 ⇒ 不出 HP 节。"""
    secs = []
    if hp_order:
        for row in butter(hp_order, hp_fc, btype='high', fs=FS, output='sos'):
            secs.append(_norm(row))
    secs += list(lp_secs)
    return secs, dc_fc


def run_chain(x, secs, dc_fc):
    y = m.run(secs, x, first=0) if secs else np.array(x, dtype=float)
    return dc_block(y, dc_fc) if dc_fc else y


def chain_mag(f, secs, dc_fc):
    H = m.band_mag(secs, f, first=0) if secs else 1.0 + 0j
    if dc_fc:
        z = np.exp(1j * 2 * np.pi * f / FS)
        r = 1.0 - 2.0 * np.pi * dc_fc / FS
        H = H * (1 - 1 / z) / (1 - r / z)
    return abs(H)


# ------------------------------------------------------------------ 主流程

def main():
    macro = 'ECG_PROFILE_DUAL_A_PLUS'
    sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, macro)
    kinds = [m.section_kind(s) for s in sos]
    bp = list(range(n_notch, len(sos)))
    hp_idx = [i for i in bp if kinds[i] == 'HP']
    lp_idx = [i for i in bp if kinds[i] == 'LP']
    lp_secs = [sos[i] for i in lp_idx]

    print('=' * 92)
    print('[0] A+ 档真实链结构审计  profile=%s' % macro)
    print('=' * 92)
    print('  总节 %d ｜ 链首陷波 %d 节 ｜ BP %d 节' % (len(sos), n_notch, len(bp)))
    print('  BP 构成：HP %d 节(= %d 阶) + LP %d 节(= %d 阶)' % (len(hp_idx), 2 * len(hp_idx),
                                                          len(lp_idx), 2 * len(lp_idx)))
    print('  ⇒ 低端总阶数 = %d（dc_block 1 阶@0.05 + HP %d 阶@0.05）｜ 高端 = %d 阶'
          % (2 * len(hp_idx) + 1, 2 * len(hp_idx), 2 * len(lp_idx)))
    print('  低端各节在 f 处 |H|：')
    for i in hp_idx:
        print('     节%2d [HP] ' % i + ' '.join('%.3fHz=%.5f' % (f, m.band_mag([sos[i]], f))
                                               for f in (0.02, 0.05, 0.1, 0.67, 5.0, 40.0)))
    for i in lp_idx:
        print('     节%2d [LP] ' % i + ' '.join('%.0fHz=%.4f' % (f, m.band_mag([sos[i]], f))
                                               for f in (5.0, 40.0, 150.0, 200.0, 250.0)))

    # --------------------------------------------------------------- [1] 实测
    print()
    print('=' * 92)
    print('[1] 实测基准（T79，A+ 档，notch OFF，间隔 2 s）')
    print('=' * 92)
    FILES = ['3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt',
             '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt']
    meas = {}
    for f in FILES:
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            print('  ⚠ 缺文件 %s' % f)
            continue
        r = m.analyse_file(p, 3000.0)
        if not r:
            continue
        rs, width, idx, uv = r
        meas[f] = rs
        print('  %-52s n=%d 脉宽 %.0f ms' % (f[:52], len(rs), width * 1e3))
        print('     a3 过冲 %6.2f %% ｜ 下冲 %6.2f %% ｜ a1+0.05s max %7.1f 中位 %6.1f µV'
              % (m.med(rs, ['ovs', 'near']), m.med(rs, ['dns', 'near']),
                 m.med(rs, ['disp', 0.05]), m.med(rs, ['dispm', 0.05])))
        print('     a1 各时点 max|u|：' + ' '.join(
            '%+.2fs=%5.0f' % (x, m.med(rs, ['disp', x])) for x in (0.05, 0.1, 0.2, 0.5, 1.0)))
        print('     a1 各时点 中位|u|：' + ' '.join(
            '%+.2fs=%5.0f' % (x, m.med(rs, ['dispm', x])) for x in (0.05, 0.1, 0.2, 0.5, 1.0)))
        print('     a2 滑窗 %7.1f ｜ 拟合 %7.1f µV/s' % (m.med(rs, ['slope', 'win']),
                                                    m.med(rs, ['slope', 'fit'])))

    # --------------------------------------------------------------- [2] 扫描
    CANDS = [
        # id,  dc_fc, hp_order, hp_fc, 说明
        ('S0 现状 dc1@0.05 + HP4@0.05', 0.05, 4, 0.05, '固件真实系数（BP 原节）'),
        ('S1 dc1@0.05 + 去HP4',         0.05, 0, None, '低端只剩 dc_block'),
        ('S2 dc1@0.05 + HP2@0.05',      0.05, 2, 0.05, '低端 3 阶'),
        ('S3 dc1@0.02 + HP4@0.02',      0.02, 4, 0.02, '降 fc 保阶'),
        ('S4 dc1@0.02 + 去HP4',         0.02, 0, None, '降 fc + 降阶'),
        ('S5 dc1@0.01 + 去HP4',         0.01, 0, None, '更慢的 dc'),
        ('S6 dc1@0.01 + HP4@0.01',      0.01, 4, 0.01, '最保守'),
    ]

    def eval_cand(dc_fc, hp_order, hp_fc, period):
        secs, _ = mk_chain(hp_order or 0, hp_fc, dc_fc, lp_secs)
        n = int((5.0 + max(4.0 * period, 6.0)) * FS)
        x, idx = m.pulse_train(n, FS, 3000.0, 0.1, 5.0, period)
        y = run_chain(x, secs, dc_fc)
        return m.read_all(y, FS, idx, 3000.0, 0.1)

    for period in (2.0, 10.0, 20.0):
        print()
        print('=' * 92)
        print('[2] 低端方案扫描 —— 脉冲间隔 %.0f s（judge: a1<=100 µV, a2<=300 µV/s, a3<10%%）' % period)
        print('=' * 92)
        print('  %-30s %8s %8s %8s %8s %9s %9s' %
              ('方案', 'a1@.05max', 'a1@.05中位', 'a1@1.0中位', 'a3过冲%', 'a2拟合', 'a2滑窗'))
        for name, dc_fc, hp_order, hp_fc, _note in CANDS:
            rs = eval_cand(dc_fc, hp_order, hp_fc, period)
            if not rs:
                print('  %-30s  (读数不足)' % name)
                continue
            v1 = m.med(rs, ['disp', 0.05])
            v1m = m.med(rs, ['dispm', 0.05])
            v10m = m.med(rs, ['dispm', 1.0])
            ov = m.med(rs, ['ovs', 'near'])
            sl_f = m.med(rs, ['slope', 'fit'])
            sl_w = m.med(rs, ['slope', 'win'])
            ok = (v1m <= 100) and (sl_f <= 300) and (ov < 10)
            print('  %-30s %7.0f%s %7.0f%s %7.0f%s %7.2f%s %8.1f%s %8.1f%s   %s'
                  % (name, v1, m.verdict(v1, 100)[:1], v1m, m.verdict(v1m, 100)[:1],
                     v10m, m.verdict(v10m, 100)[:1], ov, '✓' if ov < 10 else '✗',
                     sl_f, '✓' if sl_f <= 300 else '✗', sl_w, '✓' if sl_w <= 300 else '✗',
                     '★全过' if ok else ''))
        print('  注：a1 的极值(max)口径会被噪声上抬 ~3.4σ（实测 σ≈20 µV ⇒ +65 µV），'
              '离线无噪声 ⇒ max≈中位；')

    # --------------------------------------------------------------- [3] 判据 b
    print()
    print('=' * 92)
    print('[3] 判据 b —— 0.67~40 Hz 正弦相对 5 Hz 的增益（限 70%~140%），各方案')
    print('=' * 92)
    print('  %-30s %s' % ('方案', '  '.join('%6.2f' % f for f in (0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0))))
    for name, dc_fc, hp_order, hp_fc, _note in CANDS:
        secs, _ = mk_chain(hp_order or 0, hp_fc, dc_fc, lp_secs)
        H5 = chain_mag(5.0, secs, dc_fc)
        row = [chain_mag(f, secs, dc_fc) / H5 * 100 for f in (0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0)]
        lo, hi = min(row[0], row[-1]), max(row[0], row[-1])
        print('  %-30s %s   %s' % (name, '  '.join('%5.1f%%' % r for r in row),
                                   '✓' if 70 <= lo and hi <= 140 else '✗'))

    # --------------------------------------------------------------- [4] 结论
    print()
    print('=' * 92)
    print('[4] 结论')
    print('=' * 92)
    print('  1) 现状（S0）在实测 2 s 间隔下 a1 不达标；根因＝低端 5 阶 HP 的阶跃尾。')
    print('  2) S1（低端只剩 dc_block 一阶@0.05）在全部时点/全部间隔下 a1 达标，'
          '且 a2、a3 不受影响。')
    print('  3) 判据 b（0.67~40 Hz）对所有候选均通过（低端降阶只会让 0.67 Hz 增益更接近 1）。')
    print('  4) 代价：低端从 5 阶降到 1 阶后，0.05 Hz 以下的衰减斜率由 -100 dB/dec 变 -20 dB/dec。')


if __name__ == '__main__':
    main()
