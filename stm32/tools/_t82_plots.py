# -*- coding: utf-8 -*-
"""T82 · +300 mV 偏移下输入阻抗判读图（620k in / 10 Hz / notch on / A+ 18 节）"""
import io
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.signal import welch
from scipy.integrate import trapezoid as trapz

for f in ('Microsoft YaHei', 'SimHei', 'DejaVu Sans'):
    try:
        plt.rcParams['font.sans-serif'] = [f]
        break
    except Exception:
        pass
plt.rcParams['axes.unicode_minus'] = False

FS = 500.0
UV4 = 0.1526
NOM = 3000.0
LIM = NOM * 1.10
BANDS = [(0.5, 5), (15, 45), (45, 55), (55, 95), (95, 105), (105, 145),
         (145, 155), (155, 245), (245, 340)]


def load(path):
    rows = []
    for line in io.open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if len(p) != 7:
            continue
        try:
            rows.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                         float(p[4]), float(p[5]), float(int(p[6], 16))])
        except ValueError:
            pass
    return np.array(rows)


def band(fr, P, a, b):
    m = (fr >= a) & (fr < b)
    return float(np.sqrt(trapz(P[m], fr[m]))) if m.any() else 0.0


def wins(v, w=1.0):
    W = int(w * FS)
    return np.array([np.ptp(v[i * W:(i + 1) * W]) for i in range(len(v) // W)])


def notch_out(v, fs, lo=44.0, hi=56.0):
    X = np.fft.rfft(v - v.mean())
    fr = np.fft.rfftfreq(len(v), 1 / fs)
    X[(fr >= lo) & (fr <= hi)] = 0
    return np.fft.irfft(X, len(v)) + v.mean()


def lowpass(v, fs, fc):
    X = np.fft.rfft(v - v.mean())
    fr = np.fft.rfftfreq(len(v), 1 / fs)
    X[fr > fc] = 0
    return np.fft.irfft(X, len(v)) + v.mean()


d = load('620kin(300mv偏移)-2026-09-21 141011.txt')
c2 = d[:, 1]
c4 = d[:, 3] * UV4
W = int(FS)
pps = wins(c4)
nw = len(pps)
sig = c4.std() * 2 * np.sqrt(2)

db = load('3mv10hz-620k(in)-notchon-2026-09-21 124735.txt')
c4b = db[:, 3] * UV4
c2b = db[:, 1]
ppsb = wins(c4b)
sigb = c4b.std() * 2 * np.sqrt(2)

dt = load('3mv-10hz-sine-620kin-2026-09-21 104047.txt')
c4t = dt[:, 3] * UV4

fig, ax = plt.subplots(2, 2, figsize=(14.6, 9.2))

# ---------------- (A) 逐窗 p-p 时序 ----------------
t = np.arange(nw) + 0.5
col = np.where(pps > LIM, '#c33', '#3a7')
ax[0, 0].axhspan(22, 45, color='#2a7', alpha=.10)
ax[0, 0].bar(t, pps, width=.86, color=col, linewidth=0)
ax[0, 0].axhline(LIM, color='#c33', ls='--', lw=1.6, label='判限 3300 µV（=110% × 标称）')
ax[0, 0].axhline(np.median(pps), color='#1170aa', lw=1.4,
                 label='中位 %.1f µV = %.2f%%' % (np.median(pps), np.median(pps) / NOM * 100))
ax[0, 0].axhline(sig, color='#555', ls=':', lw=1.3,
                 label='真信号等效 p-p %.0f µV = %.2f%%' % (sig, sig / NOM * 100))
ax[0, 0].annotate('22~45 s 段：p95 106.8%%，超限 0/23\n（与无偏移那份完全一致）',
                  xy=(33.5, 3520), ha='center', fontsize=9, color='#1a5',
                  bbox=dict(fc='w', ec='#2a7', alpha=.92, lw=.8, boxstyle='round,pad=.28'))
ax[0, 0].annotate('超限窗 %d/%d' % ((pps > LIM).sum(), nw), xy=(84, 3620), ha='right',
                  fontsize=9, color='#c33',
                  bbox=dict(fc='w', ec='#c33', alpha=.92, lw=.8, boxstyle='round,pad=.28'))
ax[0, 0].set_xlim(0, nw + 1)
ax[0, 0].set_ylim(3000, 3720)
ax[0, 0].set_xlabel('时间 (s)')
ax[0, 0].set_ylabel('1 s 窗 p-p (µV)')
ax[0, 0].set_title('(A) 逐秒 1 s 窗 p-p：干扰是时段性的 —— 同一份 +300 mV 数据内就有干净段')
ax[0, 0].legend(fontsize=8.5, loc='upper left', framealpha=.92)
ax[0, 0].grid(alpha=.28, axis='y')

# ---------------- (B) 三口径柱状 ----------------
cases = [('原样', c4), ('挖 44~56 Hz', notch_out(c4, FS)), ('低通 40 Hz', lowpass(c4, FS, 40.0))]
meds = [np.median(wins(v)) / NOM * 100 for _, v in cases]
p95s = [np.percentile(wins(v), 95) / NOM * 100 for _, v in cases]
maxs = [wins(v).max() / NOM * 100 for _, v in cases]
x = np.arange(3)
w3 = .26
for off, vals, lab, cc in ((-w3, meds, '中位', '#1170aa'), (0, p95s, 'p95', '#e8a33d'),
                           (w3, maxs, 'max', '#c33')):
    b = ax[0, 1].bar(x + off, vals, w3, label=lab, color=cc)
    for r, v in zip(b, vals):
        ax[0, 1].text(r.get_x() + r.get_width() / 2, v + .7, '%.1f' % v,
                      ha='center', fontsize=8, color=cc)
ax[0, 1].axhline(110, color='#c33', ls='--', lw=1.6, label='判限 110%')
ax[0, 1].set_xticks(x)
ax[0, 1].set_xticklabels([c[0] for c in cases], fontsize=9.5)
ax[0, 1].set_ylabel('占标称 3000 µV 的百分比 (%)')
ax[0, 1].set_ylim(95, 126)
ax[0, 1].set_title('(B) p95 超限全部来自 >40 Hz 带外干扰：\n挖掉 44~56 Hz 得 107.4%；低通 40 Hz 得 104.3%（0/93 超限）')
ax[0, 1].legend(fontsize=8.5, ncol=2, loc='upper left', framealpha=.92)
ax[0, 1].grid(alpha=.28, axis='y')

# ---------------- (C) 链前 / 链后 带外 rms 分段 ----------------
labs = ['%g-%g' % b for b in BANDS]
xs = np.arange(len(BANDS))
def rms_of(col, sc=None):
    fr, P = welch(col, fs=FS, nperseg=512, noverlap=256, detrend='constant')
    return np.array([band(fr, P, *b) for b in BANDS])
r_this = rms_of(c4)
r_b = rms_of(c4b)
r_t = rms_of(c4t)
r_this2 = rms_of(c2) * (sig / np.median([np.ptp(c2[i * W:(i + 1) * W]) for i in range(len(c2) // W)]))
ax[1, 0].bar(xs - .27, r_this, .27, label='本文件 col⑤ 链后', color='#c33')
ax[1, 0].bar(xs, r_b, .27, label='T78-B col⑤（无偏移）', color='#1170aa')
ax[1, 0].bar(xs + .27, r_this2, .27, label='本文件 col② 链前（归一到 µV）', color='#2a7')
ax[1, 0].set_xticks(xs)
ax[1, 0].set_xticklabels(labs, fontsize=7.5, rotation=30)
ax[1, 0].set_yscale('log')
ax[1, 0].set_ylabel('带内 rms (µV，对数)')
ax[1, 0].set_title('(C) 归因：链前 col② 的 50 Hz（45-55 Hz 带）与无偏移那份完全同级（985 µV）\n因此干扰来自外部，不是 +300 mV 偏移或固件链造成的')
ax[1, 0].legend(fontsize=8.5, loc='upper left', framealpha=.92)
ax[1, 0].grid(alpha=.28, axis='y', which='both')

# ---------------- (D) 波形：干净段 vs 干扰段 ----------------
seg_clean = c4[30 * W:31 * W]
seg_bad = c4[83 * W:84 * W]
tt = np.arange(W) / FS
ax[1, 1].plot(tt, seg_clean - seg_clean.mean(), lw=1.2, color='#2a7',
              label='干净段（30~31 s）p-p %.0f µV = %.2f%%' % (np.ptp(seg_clean), np.ptp(seg_clean) / NOM * 100))
ax[1, 1].plot(tt, seg_bad - seg_bad.mean() + 3400, lw=1.2, color='#c33',
              label='干扰段（83~84 s）p-p %.0f µV = %.2f%%' % (np.ptp(seg_bad), np.ptp(seg_bad) / NOM * 100))
ax[1, 1].axhline(3400 + LIM / 2, color='#c33', ls='--', lw=1.0)
ax[1, 1].axhline(3400 - LIM / 2, color='#c33', ls='--', lw=1.0)
ax[1, 1].text(.02, 3400 + LIM / 2 + 60, '判限带 ±1650 µV', fontsize=8.5, color='#c33')
ax[1, 1].set_xlim(0, 1)
ax[1, 1].set_xlabel('时间 (s)')
ax[1, 1].set_ylabel('µV（两段已上下错开显示）')
ax[1, 1].set_title('(D) 波形对比：10 Hz 正弦幅度两段一致（无衰减），\n差别只在叠加的带外干扰')
ax[1, 1].legend(fontsize=8.5, loc='lower left', framealpha=.92)
ax[1, 1].grid(alpha=.28)

plt.tight_layout()
out = 'T82_300mV偏移输入阻抗判读_2026-09-21.png'
plt.savefig(out, dpi=112)
print('saved', out)
print()
print('=== 关键数字 ===')
print('  真信号 col⑤ %.1f µVpp = %.2f%%' % (sig, sig / NOM * 100))
print('  1s窗 p-p 中位 %.1f = %.2f%%' % (np.median(pps), np.median(pps) / NOM * 100))
print('  挖44~56   中位 %.2f%%  p95 %.2f%%' % (meds[1], p95s[1]))
print('  低通40    中位 %.2f%%  p95 %.2f%%' % (meds[2], p95s[2]))
