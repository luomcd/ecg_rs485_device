#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T88b · 两个稳健性检查

[1] τ 的**逐周期**估计（5 个脉冲各自拟合）⇒ 给出 τ 的真实散布，判断"实测 8.49 s vs 设计 7.96 s"
    到底是**真偏差**还是**拟合噪声**。
[2] 下落沿**细看**（2 ms 网格、5 脉冲中位）⇒ 判 「[4] max|尾| = 101.7 µV」是发生在**脉沿瞬态**
    还是**基线区**（决定它是"边沿振铃"还是"基线偏移超标"）。
[3] 谐波域传函的**分周期**一致性（每个周期各自一个 |H(0.1 Hz)|）。
"""
import importlib.util
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _imp(nm, path):
    spec = importlib.util.spec_from_file_location(nm, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nm] = mod
    spec.loader.exec_module(mod)
    return mod


t85 = _imp('t85', os.path.join(HERE, '_t85_lowfreq_square.py'))
FS_T = t85.FS_T
UV = t85.UV
FILE = '3mv-0.5hz-100ms-Rectangle pulse-notchoff-2026-09-21 175515.txt'
OUT = []


def p(s=''):
    OUT.append(s)
    print(s)


def smooth(v, fc=40.0):
    from scipy.signal import butter, filtfilt
    b, a = butter(4, fc / (FS_T / 2.0), 'low')
    return filtfilt(b, a, v)


def fit1(t, v, tmin=0.6, tmax=8.8):
    taus = np.exp(np.linspace(math.log(0.5), math.log(60.0), 400))
    s = (t >= tmin) & (t <= tmax)
    tt, vv = t[s], v[s]
    best = None
    for tau in taus:
        A = np.column_stack([np.ones_like(tt), np.exp(-tt / tau)])
        c, *_ = np.linalg.lstsq(A, vv, rcond=None)
        r = vv - A @ c
        rss = float(np.mean(r ** 2))
        if best is None or rss < best[0]:
            best = (rss, tau, c[1], c[0])
    return float(best[1]), float(best[2]), float(best[3]), float(math.sqrt(best[0]))


arr, ncol, brc, bw, where = t85.load(os.path.join(HERE, FILE))
x = arr[:, 1] * UV
y = arr[:, 4] * UV
n = len(arr)

# 上升/下降沿
amp = float(np.percentile(x, 99.9) - np.median(x))
thr = np.median(x) + 0.5 * amp
up = x > thr
st = [k for k in range(1, n) if up[k] and not up[k - 1]]
st = [s for i, s in enumerate(st) if i == 0 or s - st[i - 1] > 500]
fe = []
for s in st:
    k = next((i for i in range(s + 50, min(s + 260, n)) if x[i] < thr), None)
    fe.append(k)
pairs = [(s, e) for s, e in zip(st, fe) if e is not None]
P = int(round(float(np.median(np.diff(st)))))
p('=' * 110)
p('T88b · 稳健性检查   file = %s' % FILE)
p('  上升沿 %d ｜ P = %d 样本（%.4f s）｜ 脉宽 %.1f ms' % (len(st), P, P / FS_T, 1000 * (fe[0] - st[0]) / FS_T))
p('=' * 110)

# ---------------- [1] τ 逐周期
p('')
p('[1] τ 的**逐周期**估计（拟合窗 = 下落沿后 +0.6~+8.8 s，单极点 + 常数项）')
p('  %-8s %9s %11s %10s %11s' % ('脉冲#', 'τ s', 'fc Hz', 'C µV', '残差RMS µV'))
taus = []
for i, (s, e) in enumerate(pairs):
    if e + P - 40 > n:
        continue
    seg = y[e:e + P - 40]
    t = np.arange(len(seg)) / FS_T
    tau, C, yinf, rms = fit1(t, seg)
    taus.append(tau)
    p('  %-8d %9.3f %11.5f %10.1f %11.1f' % (i + 1, tau, 1 / (2 * math.pi * tau), C, rms))
taus = np.array(taus)
p('  ⇒ τ 中位 %.3f s（%.5f Hz）｜ 散布 %.3f~%.3f s（极差 %.0f%%）｜ 设计 %.3f s（0.02000 Hz）'
  % (np.median(taus), 1 / (2 * math.pi * np.median(taus)), taus.min(), taus.max(),
     100 * (taus.max() - taus.min()) / np.median(taus), 1 / (2 * math.pi * 0.02)))
p('  ⇒ 判读：%s' % ('τ 散布覆盖设计值 ⇒ 实测与 0.02 Hz **无显著差异**（6%% 的偏差在拟合噪声内）'
                    if taus.min() <= 1 / (2 * math.pi * 0.02) <= taus.max() else
                    'τ 整段偏大/偏小 ⇒ 需查 dc 宏的实际取值'))

# ---------------- [2] 下落沿细节
p('')
p('[2] 下落沿细节（以 col② 下落沿为 0，5 脉冲取中位；原始值，2 ms 网格）')
p('  %8s %12s %12s' % ('t ms', 'col② µV', 'col⑤ µV'))
grid = [-20, -10, -6, -4, -2, 0, 2, 4, 6, 8, 10, 14, 20, 30, 40, 50, 60, 80, 100,
        150, 200, 300, 500, 1000, 2000, 4000, 8000]
rows = []
for g in grid:
    k = int(round(g / 1000.0 * FS_T))
    xx = [x[e + k] for s, e in pairs if 0 <= e + k < n]
    yy = [y[e + k] for s, e in pairs if 0 <= e + k < n]
    rows.append((g, float(np.median(xx)), float(np.median(yy))))
    p('  %8d %12.1f %12.1f' % (g, np.median(xx), np.median(yy)))
mx = max(((g, v) for g, _, v in rows if g >= 0), key=lambda t: abs(t[1]))
p('  ⇒ 尾段 |max| = %.1f µV 出现在 t = **+%d ms**' % (mx[1], mx[0]))
p('  ⇒ 判读：%s'
  % ('该点落在**脉沿瞬态区**（≲20 ms）⇒ 属边沿振铃/建立过程，不是"基线偏移"' if mx[0] <= 20 else
     '该点落在**基线区** ⇒ 是真正的基线偏移'))
p('  参考：LP2@250 的阶跃建立时间 ≈ 5 ms；col② 自身在 -2 ms 仍是平台值 ⇒ 该负峰不可能来自链外。')

# ---------------- [3] 谐波域分周期一致性
p('')
p('[3] 谐波域传函 分周期一致性（每周期各自一个 |H|）')
p('  %-9s %s' % ('f Hz', '|H| 各周期 %'))
for k in (1, 2, 3, 5, 10):
    f = k * FS_T / P
    mg = []
    nn = np.arange(P)
    w = np.exp(-2j * np.pi * k * nn / P)
    for s, e in pairs:
        if s + P > n:
            continue
        xs = x[s:s + P] - np.mean(x[s:s + P])
        ys = y[s:s + P] - np.mean(y[s:s + P])
        Xk, Yk = complex((xs * w).sum()), complex((ys * w).sum())
        mg.append(abs(Yk / Xk) * 100)
    p('  %-9.4f %s  ⇒ 中位 %.2f ｜ 极差 %.3f pp'
      % (f, ' '.join('%.2f' % q for q in mg), np.median(mg), max(mg) - min(mg)))

with open(os.path.join(HERE, '_t88b_out.txt'), 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(OUT) + '\n')
print('\n[写入 _t88b_out.txt]')
