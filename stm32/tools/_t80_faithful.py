#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T80 忠实性验证：拿实测【链前 col②】当激励，喂离线固件链，看能否复现【链后 col⑤】。

若复现 ⇒ ① 离线链模型可信，可做 what-if ② 实测的 a3/过冲读数就是真值
（此前用"理想方波"当激励得到 16% 过冲，是激励边沿过锐造成的虚高）。

★ T174(2026-10-09) 参数化：新增 `--file/--macro/--dc-fc`。
   旧版把两份历史 txt 与 `dc_fc=0.05` **写死**，既不能喂新数据、dc_fc 也不随档位（A+B 档是 0.02）。
   无参数运行 ⇒ 逐字沿用旧版两份数据（旧报告读数可复现）。

★ T177(2026-10-09) 新增 `--with-notch`：离线链**含链首 15 节陷波**（旧口径只跑 BP）。
   用途＝复现 `notch on` 的实测 ⑤。⚠ 口径必须与数据的 notch 状态配对：
   拿 notch OFF 数据配 `--with-notch`（或反之）必然不吻合 —— **口径错配、非链坏**
   （S6 补测 §2.4 已定量：旧 BP-only 口径跑 ON 数据 ⇒ r=0.60957 的**假失败**）。
"""
import argparse
import importlib.util
import os
import sys

import numpy as np
from scipy.signal import lfilter, butter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)

FS = m.FS
FS_T = 500.0
UV = 0.1122


def dc_block(x, fc=0.05, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def load(path):
    r = []
    for ln in open(path, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 6:
            continue
        try:
            r.append([float(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5])])
        except ValueError:
            pass
    return np.array(r)


def pulses(v, fs, frac=0.5):
    hi, lo = np.percentile(v, 98), np.median(v)
    thr = lo + frac * (hi - lo)
    up = v > thr
    st, en = [], []
    for k in range(1, len(v)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    out = []
    for s in st:
        e = next((x for x in en if x > s), None)
        if e and (e - s) / fs >= 0.02:
            out.append((s, e))
    return out


def metrics(y, fs, pr, amp=3000.0, width=0.1):
    w = int(round(width * fs))
    res = []
    for s, e in pr:
        i0, i1 = s, s + w
        if i1 + int(1.5 * fs) >= len(y) or i0 < int(0.3 * fs):
            continue
        base = np.median(y[i0 - int(0.3 * fs):i0])
        u = y - base
        seg = u[i0:i0 + int(0.03 * fs)]
        pk = float(np.max(seg))
        pn = float(np.median(u[i0 + int(0.015 * fs):i0 + int(0.03 * fs)]))
        pf = float(np.median(u[i1 - int(0.01 * fs):i1]))
        vl = float(np.min(u[i1:i1 + int(0.03 * fs)]))
        res.append(dict(ov=(pk - pn) / pn * 100 if pn > 0 else np.nan,
                        dn=-vl / pn * 100 if pn > 0 else np.nan,
                        droop=pf - pn, pn=pn,
                        tail={xv: float(np.median(np.abs(u[i1 + int(xv * fs):i1 + int((xv + 0.3) * fs)])))
                              for xv in (0.05, 0.1, 0.2, 0.5, 1.0)}))
    return res


def med(rs, k):
    v = [r[k] for r in rs if np.isfinite(r[k])]
    return float(np.median(v)) if v else float('nan')


# ★ T174(2026-10-09)：dc_block 截止频率**随档位**走。
#   真值表逐条取自 Filter/ecg_filter_coeffs.h 的 ECG_DC_BLOCK_FC_HZ_F（:367-424）。
#   ⚠ 9 档里**只有 A+B 是 0.02 Hz**，其余 8 档全 0.05 Hz —— 写死 0.05 会让 A+B 档失真。
PROFILE_FC = {
    'ECG_PROFILE_ST40': 0.05,
    'ECG_PROFILE_DIAG150': 0.05,
    'ECG_PROFILE_DIAG150_D2': 0.05,
    'ECG_PROFILE_DIAG150_D3': 0.05,
    'ECG_PROFILE_IMP620': 0.05,
    'ECG_PROFILE_IMP620_N2': 0.05,
    'ECG_PROFILE_DUAL_A': 0.05,
    'ECG_PROFILE_DUAL_A_PLUS': 0.05,
    'ECG_PROFILE_DUAL_A_PLUS_B': 0.02,
}
DEFAULT_MACRO = 'ECG_PROFILE_DUAL_A_PLUS_B'   # 当前投产档（coeffs.h:274）

# 旧版（T80 原始）写死的两份历史 txt —— 无参运行时逐字沿用，保证旧报告读数可复现。
LEGACY_FILES = [('3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt', 'ECG_PROFILE_DUAL_A_PLUS', 0.05, 0.05, 250.0),
                ('3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt', 'ECG_PROFILE_DIAG150_D3', 0.05, 0.05, 250.0)]


def build_jobs(args):
    """返回 [(fname, macro, dc_fc, hp_fc, lp_fc)]。
    无 --file ⇒ 沿用 LEGACY_FILES（行为与 T80 一致）；
    有 --file ⇒ 每条一条作业：macro 缺省＝投产档 A+B，dc_fc 缺省＝按 macro 查 PROFILE_FC。"""
    if not args.file:
        return LEGACY_FILES
    macros = args.macro or []
    jobs = []
    for i, f in enumerate(args.file):
        macro = macros[i] if i < len(macros) else (macros[-1] if macros else DEFAULT_MACRO)
        if args.dc_fc is not None:
            dc = args.dc_fc
        else:
            if macro not in PROFILE_FC:
                print('⚠ 未知档位宏 %s ⇒ dc_fc 兜底 0.05 Hz' % macro)
            dc = PROFILE_FC.get(macro, 0.05)
        jobs.append((f, macro, dc, dc, 250.0))
    return jobs


def _resolve(fname):
    """绝对路径直接用；否则依次在脚本目录、当前目录找。"""
    if os.path.isabs(fname):
        return fname
    p = os.path.join(HERE, fname)
    if not os.path.exists(p):
        p2 = os.path.join(os.getcwd(), fname)
        if os.path.exists(p2):
            p = p2
    return p


ap = argparse.ArgumentParser(description='T80 忠实性验证（实测 col② → 离线固件链 → 实测 col⑤）')
ap.add_argument('--file', action='append', metavar='TXT',
                help='ascii-1 txt（可重复）；不给则跑 T80 原版两份历史数据')
ap.add_argument('--macro', action='append', metavar='ECG_PROFILE_*',
                help='档位宏（可重复，按 --file 顺序配对；缺省＝投产档 A+B）')
ap.add_argument('--with-notch', action='store_true',
                help='离线链**含**链首 15 节陷波（复现 notch ON 的实测 ⑤）；缺省＝只跑 BP（notch off 口径）')
ap.add_argument('--dc-fc', type=float, default=None,
                help='dc_block 截止频率 Hz；缺省＝按档位查表（A+B=0.02、其余 0.05）')
args = ap.parse_args()

for fname, macro, dc_fc, hp_fc, lp_fc in build_jobs(args):
    p = _resolve(fname)
    if not os.path.exists(p):
        print('缺 %s' % fname)
        continue
    a = load(p)
    c2 = (a[:, 1] - a[:, 1].mean()) * UV      # 链前（AFE 输出），去直流
    c5 = (a[:, 4] - a[:, 4].mean()) * UV      # 链后（滤波输出）
    pr500 = pulses(c5, FS_T)

    print('=' * 96)
    print('%s   n=%d  %.2f s  脉冲 %d 段' % (fname, len(a), len(a) / FS_T, len(pr500)))
    print('  [档位] %s ｜ dc_fc=%.3f Hz  (hp_fc=%.3f lp_fc=%.1f)' % (macro, dc_fc, hp_fc, lp_fc))
    print('=' * 96)
    r5 = metrics(c5, FS_T, pr500)
    print('  [实测 col⑤] 过冲 %6.2f%% ｜ 下冲 %6.2f%% ｜ droop %7.1f µV ｜ 平台 %7.1f µV'
          % (med(r5, 'ov'), med(r5, 'dn'), med(r5, 'droop'), med(r5, 'pn')))
    print('      tail 中位|u| ' + ' '.join('+%.2fs=%5.1f' % (x, med(r5, 'tail')[x]) for x in (0.05, 0.1, 0.2, 0.5, 1.0))
          if False else '      tail 中位|u| ' + ' '.join('+%.2fs=%5.1f' % (x, float(np.median([r['tail'][x] for r in r5]))) for x in (0.05, 0.1, 0.2, 0.5, 1.0)))

    # 链前自身的平台/过冲
    r2 = metrics(c2, FS_T, pr500)
    print('  [实测 col②] 过冲 %6.2f%% ｜ 平台 %7.1f µV（标称 3000）' % (med(r2, 'ov'), med(r2, 'pn')))

    # ---- 离线：col② 升采样到 853 域 → 固件真实链 → 降回 500 域
    secs, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, macro)
    # ★ T177(2026-10-09)：--with-notch ⇒ 离线链**从头跑**（含链首 15 节陷波）
    #   ⇒ 用于复现 `notch on` 的实测 ⑤；缺省仍为旧口径（只跑 BP ＝ notch off 对照）。
    #   ⚠ 口径必须与数据的 notch 状态一致：拿 notch OFF 的数据配 --with-notch（或反之）
    #     会人为制造不吻合 —— 那是口径错配，不是链坏了（S6 补测 §2.4 已定量：0.60957 假失败）。
    chain = list(secs) if args.with_notch else [secs[i] for i in range(n_notch, len(secs))]
    print('  [离线链] %s ＝ %d 节（档总 %d 节 ＝ 陷波 %d ＋ BP %d）'
          % ('含链首陷波（notch ON 口径）' if args.with_notch else '仅 BP（notch OFF 口径）',
             len(chain), len(secs), n_notch, len(secs) - n_notch))
    t500 = np.arange(len(a)) / FS_T
    t853 = np.arange(0, t500[-1], 1 / FS)
    x853 = np.interp(t853, t500, c2)
    y853 = m.run(chain, x853, first=0)
    y853 = dc_block(y853, dc_fc, FS)
    y500 = np.interp(t500, t853, y853)
    r_sim = metrics(y500, FS_T, pr500)
    print('  [离线复现]   过冲 %6.2f%% ｜ 下冲 %6.2f%% ｜ droop %7.1f µV ｜ 平台 %7.1f µV'
          % (med(r_sim, 'ov'), med(r_sim, 'dn'), med(r_sim, 'droop'), med(r_sim, 'pn')))
    print('      tail 中位|u| ' + ' '.join('+%.2fs=%5.1f' % (x, float(np.median([r['tail'][x] for r in r_sim]))) for x in (0.05, 0.1, 0.2, 0.5, 1.0)))

    # 逐点吻合度
    k = min(len(y500), len(c5))
    err = y500[:k] - c5[:k]
    print('  逐点误差：RMS %.1f µV ｜ 相对 p-p %.2f%% ｜ 相关系数 %.5f'
          % (np.sqrt(np.mean(err ** 2)), np.sqrt(np.mean(err ** 2)) / np.ptp(c5) * 100,
             np.corrcoef(y500[:k], c5[:k])[0, 1]))

    # ★ T177(2026-10-09)：异常自诊 —— ② 共模单点毛刺 / ⑤ 快恢复静默窗会把 r **单点拉垮**。
    #   实测（S6 补测的 notch-ON 文件）：修毛刺后 r=0.98541；再剔 0.29 s 静默窗 ⇒ **0.99672**。
    #   ⇒ 「本脚本 r 偏低」多数是**数据异常未处理**，不是链坏。同构对比请用
    #     `_t169/_s6_faithful_full.py`（自动修 ② 毛刺 ＋ `--exclude` 剔静默窗）。
    gthr = 20000.0
    ng = int(np.sum(np.abs(c2) > gthr))
    run = best = 0
    for z in (a[:, 4] == 0.0):
        run = run + 1 if z else 0
        best = max(best, run)
    if ng or best >= 20:
        print('  ⚠ 异常自诊：② |u|>%.0f mV 单点 %d 个 ｜ ⑤ 最长全零段 %.1f ms'
              % (gthr / 1000.0, ng, best / FS_T * 1000.0))
        print('     ⇒ 上述异常会**主导 RMS 与相关系数** ⇒ 本口径 r 偏低属预期，'
              '不是链坏（需要异常已处理的同构对比 → `_t169/_s6_faithful_full.py`）。')

    # ---- 候选：去 HP4（只留 dc）在同一实测激励下
    y853c = dc_block(np.interp(t853, t500, c2), dc_fc, FS)
    y500c = np.interp(t500, t853, y853c)
    rc = metrics(y500c, FS_T, pr500)
    print('  [候选 去HP4] 过冲 %6.2f%% ｜ 下冲 %6.2f%% ｜ droop %7.1f µV ｜ 平台 %7.1f µV'
          % (med(rc, 'ov'), med(rc, 'dn'), med(rc, 'droop'), med(rc, 'pn')))
    print('      tail 中位|u| ' + ' '.join('+%.2fs=%5.1f' % (x, float(np.median([r['tail'][x] for r in rc]))) for x in (0.05, 0.1, 0.2, 0.5, 1.0)))
    print()
