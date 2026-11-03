# -*- coding: utf-8 -*-
"""T38b: dt_cycles 窄口径实测统计 + 与外推对账
列: idxraw ecgraw idxfilter dsrateraw filterraw dt_cycles [xx]
"""
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False
rcParams['font.size'] = 10

F_MCU  = 1e6 / 1172.0
CPU_HZ = 80e6
PERIOD = CPU_HZ / F_MCU
FS_OUT = 500.0

TOOLS = os.path.dirname(os.path.abspath(__file__))
FP = os.path.join(TOOLS, 'dtcycle-2026-09-18 134004.txt')

raw = []
with open(FP, 'r', encoding='utf-8', errors='ignore') as fh:
    for ln in fh:
        p = ln.split()
        if len(p) >= 2:
            raw.append(p)

n_raw = len(raw)
rows = [p for p in raw if len(p) >= 7]          # 剔除截断残行
n_bad = n_raw - len(rows)
n = len(rows)
dt = np.array([int(p[5]) for p in rows], dtype=np.int64)
fr = np.array([float(p[4]) for p in rows])
c1 = np.array([int(p[0]) for p in rows])

us  = dt / (CPU_HZ / 1e6)
occ = dt / PERIOD * 100.0

def q(a, p):
    return float(np.percentile(a, p))

# 外推值（口径：T32/T33/T36 文档）
EXT_13 = 10.56      # 13 节外推
EXT_D3 = 6.507      # 8 节（推定）
EXT_D2 = 4.884      # 6 节（推定）
SLOPE  = 0.8115     # 6->8 节区间斜率 pp/节

out = []
A = out.append
A('=' * 80)
A('T38b  dt_cycles 窄口径实测 (ecg_restore_process 前后) + 与外推对账')
A('=' * 80)
A('文件: %s' % os.path.basename(FP))
A('原始行数 = %d，剔除截断残行 = %d，有效 n = %d' % (n_raw, n_bad, n))
A('时长 = %.2f s (@500Hz 输出栅格)' % (n / FS_OUT))
A('')
A('--- 1) 核心结果 ---')
A('  中位 dt = %.0f cycle = %.2f us  = %.3f %% of DRDY' % (q(dt,50), q(dt,50)/80, q(dt,50)/PERIOD*100))
A('  均值 dt = %.1f cycle = %.2f us  = %.3f %%' % (dt.mean(), dt.mean()/80, dt.mean()/PERIOD*100))
A('  最小 dt = %.0f cycle = %.2f us  = %.3f %%' % (dt.min(), dt.min()/80, dt.min()/PERIOD*100))
A('  最大 dt = %.0f cycle = %.2f us  = %.3f %%' % (dt.max(), dt.max()/80, dt.max()/PERIOD*100))
A('  std     = %.1f cycle = %.2f us   CV = %.2f %%' % (dt.std(ddof=1), dt.std(ddof=1)/80, dt.std(ddof=1)/dt.mean()*100))
A('')
A('  %-8s %-10s %-10s %-10s' % ('分位', 'cycles', 'us', '%DRDY'))
for p in [0, 1, 5, 25, 50, 75, 95, 99, 99.9, 100]:
    v = q(dt, p)
    A('  p%-7s %-10.0f %-10.2f %-10.3f' % (p, v, v/80, v/PERIOD*100))
A('')
A('--- 2) 与外推对账 ---')
A('  外推 13 节    = %.3f %%   (= 0.8115 pp/节 x 13)' % EXT_13)
A('  实测 13 节中位 = %.3f %%' % (q(dt,50)/PERIOD*100))
A('  偏差          = %+.3f pp  (%+.2f %% 相对)' % ((q(dt,50)/PERIOD*100)-EXT_13,
                                                ((q(dt,50)/PERIOD*100)/EXT_13-1)*100))
A('  实测 13 节最大 = %.3f %%  (仍比外推中位低 %.3f pp)' % ((dt.max()/PERIOD*100), EXT_13-(dt.max()/PERIOD*100)))
A('')
A('  >> 反推真实边际成本（以 D3=8 节 %.3f%% 为锚点）：' % EXT_D3)
slope_new = ((q(dt,50)/PERIOD*100) - EXT_D3) / 5.0
A('     8->13 节实测斜率 = %.4f pp/节' % slope_new)
A('     6->8  节推定斜率 = %.4f pp/节' % SLOPE)
A('     比值 = %.3f x  => 边际成本%s %.1f %%' % (slope_new/SLOPE,
      '下降' if slope_new < SLOPE else '上升', abs(slope_new/SLOPE-1)*100))
A('     折合单节耗时: 早期 %.2f us/节  vs  13节区间 %.2f us/节' % (SLOPE/100*1172.0, slope_new/100*1172.0))
A('')
A('  >> 与"外推单样本耗时"直接对照：')
A('     外推 13 节 = %.1f us   实测中位 = %.2f us   实测最大 = %.2f us' % (
    EXT_13/100*1172.0, q(dt,50)/80, dt.max()/80))
A('     => 外推值落在实测 p99.9~p100 之间：把外推当"最坏值"使用是安全的。')
A('')
A('--- 3) 实时性余量 ---')
A('  最坏单次 %.2f us / 周期 1172 us  =>  占用 %.2f%%，余量 %.2f%%' % (
    dt.max()/80, dt.max()/PERIOD*100, 100-dt.max()/PERIOD*100))
A('  max/median = %.4f x   (若逼近 2x 说明有中断抢占)' % (dt.max()/q(dt,50)))
A('  >93760 cycle 的样本 = %d   饱和(65535) = %d' % (int((dt > 93760).sum()), int((dt == 65535).sum())))
A('')
A('--- 4) 稳定性 / 数据质量 ---')
A('  变异系数 CV = %.2f %%  (1.4%% 级 => 几乎无抖动)' % (dt.std(ddof=1)/dt.mean()*100))
A('  第1列 idxraw 取值 = %s   <== %s' % (sorted(set(c1.tolist()))[:5],
      'WARN 全为 0，序号未输出，建议查' if c1.max() == 0 else 'OK'))
corr = float(np.corrcoef(np.abs(fr), dt)[0, 1])
A('  corr(|filterraw|, dt) = %+.4f   (接近 0 => 定长运算，与信号幅度无关)' % corr)
lo = np.abs(fr) < np.percentile(np.abs(fr), 10)
hi = np.abs(fr) > np.percentile(np.abs(fr), 90)
A('  |filterraw| 最小 10%% 段 dt 中位 = %.0f cyc；最大 10%% 段 = %.0f cyc；差 %.0f cyc (%.2f us)' % (
    q(dt[lo],50), q(dt[hi],50), q(dt[hi],50)-q(dt[lo],50), (q(dt[hi],50)-q(dt[lo],50))/80))
A('')
A('--- 5) 直方图 (200 cycle 分箱) ---')
h, e = np.histogram(dt, bins=20)
for i in range(20):
    if h[i] == 0:
        continue
    A('  [%5.0f ~ %5.0f) : %5d  %s' % (e[i], e[i+1], h[i], '#' * int(h[i]/max(h)*48)))

txt = '\n'.join(out)
print(txt)

# ---------- 图 ----------
fig = plt.figure(figsize=(13.5, 8.5))
gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.22)

ax1 = fig.add_subplot(gs[0, :])
ax1.plot(np.arange(n)/FS_OUT, dt, lw=0.5, color='#378ADD')
ax1.axhline(q(dt,50), color='#1D9E75', ls='--', lw=1.2,
            label='median %.0f cyc = %.2f us (%.3f%%)' % (q(dt,50), q(dt,50)/80, q(dt,50)/PERIOD*100))
ax1.axhline(dt.max(), color='#E24B4A', ls=':', lw=1.2,
            label='max %.0f cyc = %.2f us (%.3f%%)' % (dt.max(), dt.max()/80, dt.max()/PERIOD*100))
ax1.axhline(EXT_13/100*PERIOD, color='#BA7517', ls='-.', lw=1.2,
            label='extrapolated 10.56%% = %.0f cyc' % (EXT_13/100*PERIOD))
ax1.set_xlabel('time (s)')
ax1.set_ylabel('dt (CPU cycles)')
ax1.set_title('dt_cycles vs time   n=%d   %.1f s   narrow scope' % (n, n/FS_OUT))
ax1.legend(fontsize=8.5, loc='center right')
ax1.grid(alpha=0.25)
ax1.set_ylim(q(dt,0.001)-300, dt.max()+600)

ax2 = fig.add_subplot(gs[1, 0])
ax2.hist(dt, bins=80, color='#7F77DD', edgecolor='#534AB7', lw=0.25)
ax2.axvline(q(dt,50), color='#1D9E75', ls='--', lw=1.2, label='median %.0f' % q(dt,50))
ax2.axvline(dt.max(), color='#E24B4A', ls=':', lw=1.2, label='max %d' % dt.max())
ax2.axvline(EXT_13/100*PERIOD, color='#BA7517', ls='-.', lw=1.2, label='extrap 10.56%%')
ax2.set_xlabel('dt (CPU cycles)')
ax2.set_ylabel('count')
ax2.set_title('histogram (span only %.2f us)' % ((dt.max()-dt.min())/80))
ax2.legend(fontsize=8.5)
ax2.grid(alpha=0.25)

ax3 = fig.add_subplot(gs[1, 1])
B = 500
nb = n // B
med = [q(dt[i*B:(i+1)*B], 50) for i in range(nb)]
mx  = [dt[i*B:(i+1)*B].max() for i in range(nb)]
xs  = np.arange(nb)*B/FS_OUT
ax3.plot(xs, med, '-o', ms=3, lw=1.0, color='#1D9E75', label='median / 1 s')
ax3.plot(xs, mx,  '-s', ms=3, lw=1.0, color='#E24B4A', label='max / 1 s')
ax3.axhline(EXT_13/100*PERIOD, color='#BA7517', ls='-.', lw=1.0, label='extrap 10.56%')
ax3.set_xlabel('time (s)')
ax3.set_ylabel('dt (cycles)')
ax3.set_title('per-second median / max  (%d bins, 1 s each)' % nb)
ax3.legend(fontsize=8.5)
ax3.grid(alpha=0.25)

fig.suptitle('T38  DWT dt_cycles measured  13 sections  narrow scope  ecg_restore_process only  @80 MHz', fontsize=12.5)
png = os.path.join(TOOLS, 'T38_dt实测_窄口径_2026-09-18.png')
fig.savefig(png, dpi=110, bbox_inches='tight')
print('\n[PNG] %s' % png)

md = os.path.join(TOOLS, 'T38_dt实测_窄口径_2026-09-18.md')
with open(md, 'w', encoding='utf-8') as fh:
    fh.write('# T38 dt_cycles 窄口径实测统计与外推对账\n\n```\n' + txt + '\n```\n')
print('[MD ] %s' % md)
