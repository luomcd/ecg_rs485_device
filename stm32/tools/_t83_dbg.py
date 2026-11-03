# -*- coding: utf-8 -*-
"""T83 调试：为什么 sm100 > rawmax（数学上不可能）"""
import importlib.util
import os
import sys

import numpy as np
from scipy.signal import lfilter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)
FS, FS_T, UV = m.FS, 500.0, 0.1122

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
bp = list(sos[n_notch:])
kinds = [m.section_kind(s) for s in bp]
LP = [bp[i] for i, k in enumerate(kinds) if k == 'LP']
HPs = [bp[i] for i, k in enumerate(kinds) if k == 'HP']


def dc_block(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def movavg(x, w=85):
    pad = w // 2
    xp = np.concatenate([x[1:pad + 1][::-1], x, x[-pad - 1:-1][::-1]])
    c = np.cumsum(np.concatenate([[0.0], xp]))
    return (c[w:] - c[:-w]) / w


x = np.arange(1000.0)
y = movavg(x, 85)
print('线性斜坡自检：len', len(y), ' 最大偏差',
      np.max(np.abs(y - np.clip(np.arange(1000) - 42, 0, 999 - 84) .astype(float))))
# 直接验证「平滑不放大」性质
rng = np.random.RandomState(0)
for w in (85, 84, 43):
    t = rng.randn(5000) * 10
    yy = movavg(t, w) if w % 2 == 1 else None
    if yy is None:
        continue
    print('w=%d len=%d max|x|=%.2f max|movavg|=%.2f  → %s'
          % (w, len(yy), np.max(np.abs(t)), np.max(np.abs(yy)),
             'OK' if np.max(np.abs(yy)) <= np.max(np.abs(t)) + 1e-9 else '★放大!'))

# ---- 真实数据
r = []
for ln in open(os.path.join(HERE, '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt'),
               encoding='utf-8', errors='replace'):
    q = ln.split()
    if len(q) != 7:
        continue
    try:
        r.append([float(v) for v in q[:5]])
    except ValueError:
        pass
a = np.array(r)
c2 = a[:, 1] * UV
c2d = c2 - np.median(c2)
c5d = a[:, 4] * UV
c5d = c5d - np.median(c5d)
print('有效行', len(a), '｜ col⑤ 原始 p-p %.1f µV' % (np.percentile(c5d, 99.99) - np.percentile(c5d, 0.01)))
print('col⑤ 极值 max|.| = %.1f µV  min=%.1f max=%.1f'
      % (np.max(np.abs(c5d)), c5d.min(), c5d.max()))

# 脉冲定位
v = c5d
hi, lo = np.percentile(v, 98), np.median(v)
thr = lo + 0.5 * (hi - lo)
up = v > thr
st, en = [], []
for k in range(1, len(v)):
    if up[k] and not up[k - 1]:
        st.append(k)
    elif up[k - 1] and not up[k]:
        en.append(k)
pr = [(s, next(e for e in en if e > s)) for s in st
      if next((e for e in en if e > s), None) and (next(e for e in en if e > s) - s) / FS_T >= 0.02]
print('脉冲 %d 段' % len(pr))

# 构造激励（同主脚本）
s, e = pr[len(pr) // 2]
a0, a1 = s - int(0.15 * FS_T), e + int(0.20 * FS_T)
w = c2d[a0:a1].copy()
w -= float(np.median(w[:int(0.10 * FS_T)]))
rs = np.random.RandomState(7)
SG = float(np.std(np.concatenate([c5d[q0 - int(0.5 * FS_T):q0 - int(0.05 * FS_T)] for q0, _ in pr])))
lead = rs.randn(int(8.0 * FS_T)) * SG
tail = rs.randn(int(60.0 * FS_T)) * SG
x500 = np.concatenate([lead, w, tail])
t500 = np.arange(len(x500)) / FS_T
t853 = np.arange(0.0, t500[-1], 1.0 / FS)
x853 = np.interp(t853, t500, x500)
k1 = int(round((len(lead) + (e - a0)) / FS_T * FS))
print('激励 len=%d  max|x|=%.1f µV  k1=%d' % (len(x853), np.max(np.abs(x853)), k1))

yy = sosfilt(np.column_stack([np.array(LP + HPs)[:, :3],
                              np.ones(len(LP + HPs)),
                              np.array(LP + HPs)[:, 3:]]), dc_block(x853, 0.05))
bb = float(np.median(yy[k1 - int(0.5 * FS):k1 - int(0.05 * FS)]))
u = yy - bb
sm = movavg(u, 85)
print('len(u)=%d len(sm)=%d' % (len(u), len(sm)))
print('max|u| = %.1f  at %d' % (np.max(np.abs(u)), int(np.argmax(np.abs(u)))))
print('max|sm| = %.1f  at %d' % (np.max(np.abs(sm)), int(np.argmax(np.abs(sm)))))
print('max|u[k1:]| = %.1f ｜ max|sm[k1:]| = %.1f' % (np.max(np.abs(u[k1:])), np.max(np.abs(sm[k1:]))))
j = int(np.argmax(np.abs(sm[k1:]))) + k1
print('sm 峰值处：i=%d  u[i]=%.1f  u[i-42:i+43] max|.|=%.1f' % (j, u[j], np.max(np.abs(u[j - 42:j + 43]))))
print('u 在 [k1, k1+3s] 内 max|.| = %.1f' % np.max(np.abs(u[k1:k1 + int(3 * FS)])))
print('u 在 [k1+3s, end] 内 max|.| = %.1f' % np.max(np.abs(u[k1 + int(3 * FS):])))
print('u 分位 |u|: 50%%=%.1f 99%%=%.1f 99.9%%=%.1f 100%%=%.1f'
      % tuple(np.percentile(np.abs(u[k1:]), [50, 99, 99.9, 100])))
