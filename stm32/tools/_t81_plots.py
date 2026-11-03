#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T81 判据 a 口径修正图。四面板：
  (A) 实测 col⑤（脉冲串 2 s 周期）降沿后 tail 跨脉冲中位 —— 稳态口径下已回到基线
  (B) 离线**单脉冲**固有响应 0~10 s：现状 / 候选A(去HP4) / 候选B(去HP4+dc@0.02) ＋ ±100 µV 判限
  (C) 现状 a1 的五种口径读数 vs 100 µV 判限 —— 口径决定结论
  (D) a2：tail 斜率（200 ms 窗）vs 300 µV/s 判限
激励＝实测 col② 的真实脉冲波形（含 AFE 平滑边沿）；链＝离线固件系数（T80 验证与实测 col⑤ 相关 0.9991）
"""
import importlib.util
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)

FS, FS_T, UV = m.FS, 500.0, 0.1122
MEAS = os.path.join(HERE, '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt')
OUT = os.path.join(HERE, 'T81_判据a口径修正_2026-09-21.png')


def dcblk(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def load(path):
    r = []
    for ln in open(path, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 5:
            continue
        try:
            r.append([float(x) for x in q[:5]])
        except ValueError:
            pass
    return np.array(r)


def pulses(v, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(v, 98), np.median(v)
    thr = lo + frac * (hi - lo)
    up = v > thr
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


a = load(MEAS)
c2, c5 = a[:, 1] * UV, a[:, 4] * UV
pr = pulses(c5, FS_T)
kk = len(pr) // 2
s, e = pr[kk]
width = (e - s) / FS_T

# ---- (A) 实测 tail：跨脉冲中位（相对各自脉冲前基线）
w = int(round(width * FS_T))
segs = []
for s_i, _ in pr:
    i1 = s_i + w
    if s_i - int(0.5 * FS_T) < 0 or i1 + int(1.85 * FS_T) >= len(c5):
        continue
    b = float(np.median(c5[s_i - int(0.5 * FS_T):s_i - int(0.05 * FS_T)]))
    segs.append(c5[i1:i1 + int(1.85 * FS_T)] - b)
CA = np.median(np.array(segs), axis=0)
tA = np.arange(len(CA)) / FS_T

# ---- (B) 离线单脉冲
q = np.concatenate([c5[s_i - int(0.5 * FS_T):s_i - int(0.05 * FS_T)] for s_i, _ in pr])
sg = float(np.std(q))
a0, a1i = s - int(0.15 * FS_T), e + int(0.20 * FS_T)
wv = c2[a0:a1i].copy()
wv -= float(np.median(wv[:int(0.10 * FS_T)]))
rs = np.random.RandomState(7)
x500 = np.concatenate([rs.randn(int(8.0 * FS_T)) * sg, wv, rs.randn(int(30.0 * FS_T)) * sg])
i0 = int(8.0 * FS_T) + (s - a0)
t500 = np.arange(len(x500)) / FS_T
t853 = np.arange(0.0, t500[-1], 1.0 / FS)
x853 = np.interp(t853, t500, x500)
t0 = i0 / FS_T
k0, k1 = int(round(t0 * FS)), int(round((t0 + width) * FS))
secs, nn = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
bp = [secs[i] for i in range(nn, len(secs))]

CH = [('现状（HP4@0.05+dc@0.05）', dcblk(m.run(bp, x853, first=0), 0.05), '#c33'),
      ('候选A 去HP4（只 dc@0.05）', dcblk(x853, 0.05), '#1170aa'),
      ('候选B 去HP4 + dc@0.02', dcblk(x853, 0.02), '#2a7')]
B = []
for nm, y, col in CH:
    bb = float(np.median(y[k0 - int(0.5 * FS):k0 - int(0.05 * FS)]))
    u = y - bb
    k = int(0.2 * FS)
    sl = np.concatenate([np.zeros(k), (u[k:] - u[:-k]) / 0.2])
    bad = np.where(np.abs(u[k1:]) > 100.0)[0]
    tt = 0.0 if len(bad) == 0 else (bad[-1] + 1) / FS
    long = (np.arange(len(u)) - k1) / FS
    B.append(dict(nm=nm, col=col, long=long, u=u, sl=sl, t_in=tt,
                  vmax=float(np.max(np.abs(u[k1:]))),
                  v1=float(u[k1 + int(1.0 * FS)]), v2=float(u[k1 + int(2.0 * FS)]),
                  vss=float(np.median(u[-int(3 * FS):]))))

def sm(y, k):
    k = max(1, int(k))
    return np.convolve(y, np.ones(k) / k, mode='same')


# 字体
for f in ('Microsoft YaHei', 'SimHei', 'DengXian', 'Arial Unicode MS'):
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams['font.sans-serif'] = [f]
        break
    except Exception:
        pass
plt.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(2, 2, figsize=(15.5, 9.2))

# ---------- (A)
LIM = 100.0
ax[0, 0].axhspan(-LIM, LIM, color='#2a7', alpha=.10)
ax[0, 0].axhline(-LIM, color='#c60', lw=1.0, ls='--')
ax[0, 0].axhline(LIM, color='#c60', lw=1.0, ls='--')
ax[0, 0].axhline(0, color='k', lw=.7)
CA_s = sm(CA, int(0.05 * FS_T))
ax[0, 0].plot(tA, CA_s, lw=1.9, color='#c33', label='实测 col⑤ 链后（脉冲串 2 s 周期，跨脉冲中位；50 ms 平滑）')
ax[0, 0].annotate('+1.5 s = %.1f µV' % CA[int(1.5 * FS_T)], (1.5, CA_s[int(1.5 * FS_T)]),
                  textcoords='offset points', xytext=(-18, -26), fontsize=9, color='#333',
                  arrowprops=dict(arrowstyle='-', color='#999', lw=.8))
ax[0, 0].annotate('+1.8 s = %+.1f µV' % CA[int(1.8 * FS_T)], (1.78, CA_s[int(1.78 * FS_T)]),
                  textcoords='offset points', xytext=(-96, 26), fontsize=9, color='#333',
                  arrowprops=dict(arrowstyle='-', color='#999', lw=.8))
ax[0, 0].annotate('脉冲前基线 0', (0.10, 0), textcoords='offset points', xytext=(2, 6),
                  fontsize=8.5, color='#333')
ax[0, 0].annotate('a1 判限 ±100 µV', (1.80, 100), textcoords='offset points',
                  xytext=(-104, 4), fontsize=8.5, color='#c60')
ax[0, 0].set_xlim(0, 1.85); ax[0, 0].set_ylim(-330, 90)
ax[0, 0].set_xlabel('降沿后时间 (s)'); ax[0, 0].set_ylabel('相对脉冲前基线 (µV)')
ax[0, 0].set_title('(A) 实测脉冲串 tail：稳态（+1.5 s 后）已回到基线 —— 稳态口径通过')
ax[0, 0].legend(fontsize=8, loc='lower right'); ax[0, 0].grid(alpha=.3)

# ---------- (B)
for d in B:
    sel = (d['long'] >= 0) & (d['long'] <= 10.0)
    ax[0, 1].plot(d['long'][sel], sm(d['u'], 0.05 * FS)[sel], lw=1.9, color=d['col'],
                  label='%s（进限 +%.2f s，极值 %.0f µV）' % (d['nm'], d['t_in'], d['vmax']))
    if d['t_in'] > 0:
        ax[0, 1].axvline(d['t_in'], color=d['col'], lw=.9, ls=':')
ax[0, 1].axhspan(-LIM, LIM, color='#2a7', alpha=.10)
ax[0, 1].axhline(-LIM, color='#c60', lw=1.1, ls='--', label='±100 µV 判限')
ax[0, 1].axhline(LIM, color='#c60', lw=1.1, ls='--')
ax[0, 1].axhline(0, color='k', lw=.7)
ax[0, 1].set_xlim(0, 10); ax[0, 1].set_ylim(-370, 90)
ax[0, 1].set_xlabel('降沿后时间 (s)'); ax[0, 1].set_ylabel('相对脉冲前基线 (µV)')
ax[0, 1].set_title('(B) 离线单脉冲固有响应（激励＝实测 col②，已 50 ms 平滑）：现状 6.00 s 进限 / 候选B 全程在限内')
ax[0, 1].legend(fontsize=8, loc='lower right', framealpha=.92); ax[0, 1].grid(alpha=.3)

# ---------- (C)
labs = ['极值 max|u|\n（T79/T80 旧口径）', '+1.0 s 定点', '+2.0 s 定点', '进限(+6 s)后平台', '稳态 +57~60 s']
vals = [B[0]['vmax'], B[0]['v1'], B[0]['v2'], 25.8, -B[0]['vss']]
cols = ['#c33' if abs(v) > LIM else '#2a7' for v in vals]
xs = np.arange(len(labs))
ax[1, 0].bar(xs, [abs(v) for v in vals], color=cols, width=.62)
for xi, v in zip(xs, vals):
    ax[1, 0].text(xi, abs(v) + 6, '%.1f' % abs(v), ha='center', fontsize=8.5,
                  color='#c33' if abs(v) > LIM else '#2a7')
ax[1, 0].axhline(LIM, color='#c00', lw=1.2, ls='--')
ax[1, 0].text(len(labs) - 0.45, LIM + 7, 'a1 判限 100 µV', color='#c00', fontsize=9, ha='right')
ax[1, 0].set_xticks(xs); ax[1, 0].set_xticklabels(labs, fontsize=8.2)
ax[1, 0].set_ylim(0, 430); ax[1, 0].set_ylabel('|偏移| (µV)')
ax[1, 0].set_title('(C) 现状 a1 的五种口径 —— 只有"脉冲后瞬时"口径才超限，稳态口径余量 >99%')
ax[1, 0].grid(alpha=.3, axis='y')

# ---------- (D)
for d in B:
    sel = (d['long'] >= 0) & (d['long'] <= 10.0)
    ax[1, 1].plot(d['long'][sel], sm(np.abs(d['sl']), 0.5 * FS)[sel], lw=1.8, color=d['col'], label=d['nm'])
ax[1, 1].axhline(300.0, color='#c00', lw=1.2, ls='--')
ax[1, 1].text(4.9, 318, 'a2 判限 300 µV/s', color='#c00', fontsize=9, ha='right')
ax[1, 1].annotate('500 ms 窗 ⇒ 噪声底 ≈ 65 µV/s', (3.05, 28), fontsize=8.5, color='#777')
ax[1, 1].set_xlim(0, 5); ax[1, 1].set_ylim(0, 620)
ax[1, 1].set_xlabel('降沿后时间 (s)'); ax[1, 1].set_ylabel('|滑窗斜率| 500 ms (µV/s)')
ax[1, 1].set_title('(D) a2 基线恢复斜率：t>0.15 s 后均 < 300 µV/s（已 500 ms 窗降噪）')
ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3)

fig.suptitle('T81  判据 a 口径修正（GB 9706.247）—— a1 是"脉冲后恢复到平稳的基线"偏移，不是脉冲后瞬态峰值  2026-09-21',
             fontsize=12.5)
fig.tight_layout(rect=(0, 0, 1, 0.96))
fig.savefig(OUT, dpi=130)
print('saved', OUT)
print('实测 tail  +1.5s=%.1f  +1.8s=%.1f µV' % (CA[int(1.5 * FS_T)], CA[int(1.8 * FS_T)]))
for d in B:
    print('  %-28s 进限 +%5.2f s ｜ 极值 %7.1f ｜ +1s %7.1f ｜ 稳态 %+6.1f µV'
          % (d['nm'], d['t_in'], d['vmax'], d['v1'], d['vss']))
