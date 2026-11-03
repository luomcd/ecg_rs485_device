# -*- coding: utf-8 -*-
"""T83 诊断 2：col② 与 col⑤ 的下降沿到底差多少（定 k1 用）"""
import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)
FS, FS_T, UV = m.FS, 500.0, 0.1122

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
c2d = a[:, 1] * UV
c2d -= np.median(c2d)
c5d = a[:, 4] * UV
c5d -= np.median(c5d)

hi, lo = np.percentile(c5d, 98), np.median(c5d)
thr = lo + 0.5 * (hi - lo)
up = c5d > thr
st, en = [], []
for k in range(1, len(c5d)):
    if up[k] and not up[k - 1]:
        st.append(k)
    elif up[k - 1] and not up[k]:
        en.append(k)
pr = [(s, next(e for e in en if e > s)) for s in st
      if next((e for e in en if e > s), None) and (next(e for e in en if e > s) - s) / FS_T >= 0.02]
s5, e5 = pr[6]
pl = float(np.median(c2d[s5 + int(0.03 * FS_T):s5 + int(0.09 * FS_T)]))
s2 = next(k for k in range(s5 - 50, s5 + 50) if c2d[k] > 0.5 * pl)
e2 = next(k for k in range(e5, e5 + int(0.3 * FS_T)) if abs(c2d[k]) < 0.05 * pl)
print('平台 %.0f µV ｜ col⑤ 阈值段 s5=%d e5=%d ｜ col② s2=%d e2=%d ｜ e2-s2 = %.1f ms'
      % (pl, s5, e5, s2, e2, (e2 - s2) / FS_T * 1e3))
print('col② 平台处值 %.0f µV；col② 在 e2 处 %.1f µV' % (pl, c2d[e2]))
print()
print('  t(ms, 相对 s2)   col②(µV)   col⑤(µV)')
for dt_ms in list(range(80, 260, 10)):
    k = s2 + int(dt_ms / 1000.0 * FS_T)
    mark = ''
    if k == e2:
        mark = '  ← col② 5%'
    if k == e5:
        mark = '  ← col⑤ 50%'
    print('    %6.0f      %9.1f  %9.1f%s' % (dt_ms, c2d[k], c5d[k], mark))
print()
w = [k for k in range(e5, e5 + int(0.5 * FS_T)) if k < len(c5d)]
kk = min(w, key=lambda k: c5d[k])
print('col⑤ 在 [e5, e5+0.5s] 的谷底：k=%d（相对 s2 %.1f ms）值 %.1f µV' % (kk, (kk - s2) / FS_T * 1e3, c5d[kk]))
w2 = [k for k in range(e2 - int(0.05 * FS_T), e2 + int(0.5 * FS_T)) if 0 <= k < len(c5d)]
kk2 = min(w2, key=lambda k: c5d[k])
print('col⑤ 在 [e2-50ms, e2+0.5s] 的谷底：k=%d（相对 s2 %.1f ms）值 %.1f µV' % (kk2, (kk2 - s2) / FS_T * 1e3, c5d[kk2]))
# tail 峰值在哪
w3 = [k for k in range(kk2, min(kk2 + int(2.0 * FS_T), len(c5d)))]
print('谷底之后 2 s 内 col⑤ 最大 %.1f µV（相对去直流后的全程中位）' % max(c5d[k] for k in w3))
print('谷底处是 col⑤ 的最小值 %.1f ⇒ tail 是正向的' % c5d[kk2])

# ---------------- 逐段复现主脚本的口径（找出哪几段坏）
def pulses(v, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(v, 98), np.median(v)
    t = lo + frac * (hi - lo)
    up = v > t
    st, en = [], []
    for k in range(1, len(v)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    o = []
    for s in st:
        e = next((x for x in en if x > s), None)
        if e and (e - s) / fs >= minw:
            o.append((s, e))
    return o


SEGS = []
for s5, e5x in pulses(c5d, FS_T):
    lo_i = max(0, s5 - int(0.10 * FS_T))
    pl2 = float(np.median(c2d[s5 + int(0.03 * FS_T):s5 + int(0.09 * FS_T)]))
    if not (1500.0 < pl2 < 4500.0):
        continue
    s2 = next((k for k in range(lo_i, s5 + int(0.10 * FS_T)) if c2d[k] > 0.5 * pl2), None)
    e2 = next((k for k in range(e5x, min(e5x + int(0.30 * FS_T), len(c2d))) if abs(c2d[k]) < 0.05 * pl2), None)
    if s2 is None or e2 is None:
        continue
    SEGS.append((s2, e2, pl2))


def e5_of(e):
    g0, g1 = max(0, e - int(0.05 * FS_T)), min(len(c5d), e + int(0.15 * FS_T))
    return g0 + int(np.argmin(c5d[g0:g1]))


print()
print('%-4s %7s %7s %8s %9s %9s %10s %10s' % ('seg', 's2', 'e2', 'k1=e5', '脉宽ms', 'bb(µV)', 'max|seg|', 'argmax_rel'))
for i, (s2, e2, pl2) in enumerate(SEGS):
    k1 = e5_of(e2)
    bb = float(np.median(c5d[max(0, k1 - int(0.55 * FS_T)):max(1, k1 - int(0.15 * FS_T))]))
    u = c5d - bb
    n = int(min(len(u) - k1, 1.9 * FS_T))
    seg = u[k1:k1 + n]
    print('%-4d %7d %7d %8d %9.1f %9.1f %10.1f %10d'
          % (i, s2, e2, k1, (k1 - s2) / FS_T * 1e3, bb, float(np.max(np.abs(seg))),
             int(np.argmax(np.abs(seg)))))
