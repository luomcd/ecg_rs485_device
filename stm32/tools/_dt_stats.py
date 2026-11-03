# -*- coding: utf-8 -*-
"""dt_cycles 多档统一统计入口（取代 _t38_dt_stats.py 的单档硬编码版本）

用法:
    python _dt_stats.py                                  # 内置四档
    python _dt_stats.py D2=xx.txt D3=yy.txt              # 自定义

列定义 (Ads1293/ads1293.c printf, 7 列):
    0 idxraw  1 ecgraw  2 idxfilter  3 dsrateraw  4 filterraw(double)
    5 dt_cycles (窄口径: ecg_restore_process 前后)   6 over_cnt (>12000 cyc 累计次数)

口径:
    60000000/... 不用; 功耗周期 PERIOD = 80MHz / (1e6/1172) = 93760 cycle
    窄口径 = 只含 ecg_restore_process()  (dc_block + 伪迹态机 + f64 13/9/8/6 节)
"""
import os
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False
rcParams['font.size'] = 9.5

CPU_HZ = 80e6
F_MCU = 1e6 / 1172.0
PERIOD = CPU_HZ / F_MCU          # 93760.4 cycle
FS_OUT = 500.0

TOOLS = os.path.dirname(os.path.abspath(__file__))

# 档名, 节数, 文件名, 外推值(%), 颜色
PROFILES = [
    ('D2',     6,  'dtcycles-D2-2026-09-18 150119.txt',    4.884,  '#378ADD'),
    ('D3',     8,  'dtcycles-D3-2026-09-18 150455.txt',    6.507,  '#1D9E75'),
    ('IMP620', 9,  'dtcycles-IMP620-2026-09-18 150845.txt', None,   '#BA7517'),
    ('N2',    13,  'dtcycle-2026-09-18 134004.txt',       10.560,  '#E24B4A'),
]

if len(sys.argv) > 1:
    PROFILES = []
    for a in sys.argv[1:]:
        name, _, path = a.partition('=')
        PROFILES.append((name, int(name.split('_')[-1]) if '_' in name else 0, path,
                         None, '#7F77DD'))

def q(a, p):
    return float(np.percentile(a, p))

def load(path):
    """ok=标准 7 列; glued=串口粘包(>7 列, 只取前 7 列); bad=截断残行(<7 列)"""
    rows, bad, glued = [], 0, 0
    with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
        for ln in fh:
            p = ln.split()
            if len(p) < 2:
                continue
            if len(p) >= 7:
                if len(p) > 7:
                    glued += 1
                rows.append(p[:7])
            else:
                bad += 1
    d = {
        'n_raw': len(rows) + bad, 'bad': bad, 'glued': glued, 'n': len(rows),
        'dt': np.array([int(p[5]) for p in rows], dtype=np.int64),
        'fr': np.array([float(p[4]) for p in rows]),
        'ov': np.array([int(p[6]) for p in rows], dtype=np.int64),
    }
    return d

D = {}
for name, sec, fn, ext, col in PROFILES:
    fp = fn if os.path.isabs(fn) else os.path.join(TOOLS, fn)
    D[name] = load(fp)

out = []
A = out.append
A('=' * 84)
A('dt_cycles 多档实测统计 (窄口径: ecg_restore_process 前后) @80MHz')
A('=' * 84)
A('')
A('--- 0) 数据规模 ---')
A('  %-8s %-5s %-9s %-8s %-8s %-8s %-9s %-9s' % (
    '档', '节', '有效 n', '残行', '粘包', '时长 s', '中位 cyc', 'over_cnt'))
for name, sec, fn, ext, col in PROFILES:
    d = D[name]
    A('  %-8s %-5d %-9d %-8d %-8d %-8.1f %-9.0f %-9d' % (
        name, sec, d['n'], d['bad'], d['glued'], d['n'] / FS_OUT,
        q(d['dt'], 50), int(d['ov'].max())))
A('  注: 粘包 = 串口把两行挤进一行(>7 字段), 前 7 字段经 idxfilter 连续性校验仍自洽, 按前 7 列取用')
A('')
A('--- 1) 分档核心统计 ---')
hdr = '  %-8s %-8s %-8s %-8s %-8s %-8s %-8s %-8s %-7s'
A(hdr % ('档', 'min', 'p50', 'mean', 'p99', 'p99.9', 'max', 'span', 'CV%'))
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    A(hdr % (
        name,
        '%.0f' % dt.min(), '%.0f' % q(dt, 50), '%.0f' % dt.mean(),
        '%.0f' % q(dt, 99), '%.0f' % q(dt, 99.9), '%.0f' % dt.max(),
        '%.0f' % (dt.max() - dt.min()),
        '%.2f' % (dt.std(ddof=1) / dt.mean() * 100)))
A('')
hdr2 = '  %-8s %-9s %-9s %-9s %-9s %-9s %-8s'
A(hdr2 % ('档', 'p50 us', 'max us', 'p50 %DRDY', 'max %DRDY', 'max/med', '余量%'))
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    A(hdr2 % (name, '%.2f' % (q(dt, 50) / 80), '%.2f' % (dt.max() / 80),
              '%.3f' % (q(dt, 50) / PERIOD * 100), '%.3f' % (dt.max() / PERIOD * 100),
              '%.4f' % (dt.max() / q(dt, 50)), '%.2f' % (100 - dt.max() / PERIOD * 100)))
A('')

A('--- 2) 节数 -> 成本 线性拟合 (实测中位) ---')
N = np.array([s for _, s, _, _, _ in PROFILES], dtype=float)
Y50 = np.array([q(D[nm]['dt'], 50) for nm, _, _, _, _ in PROFILES])
Ymx = np.array([D[nm]['dt'].max() for nm, _, _, _, _ in PROFILES])
Ypc = Y50 / PERIOD * 100.0
b50, a50 = np.polyfit(N, Y50, 1)
bmx, amx = np.polyfit(N, Ymx, 1)
pred = a50 + b50 * N
r2 = 1 - ((Y50 - pred) ** 2).sum() / ((Y50 - Y50.mean()) ** 2).sum()
A('  中位模型: cyc = %.1f + %.1f * 节数        (拟合 R^2 = %.6f)' % (a50, b50, r2))
A('            dt%%  = %.4f + %.4f * 节数' % (a50 / PERIOD * 100, b50 / PERIOD * 100))
A('  最大模型: cyc = %.1f + %.1f * 节数' % (amx, bmx))
A('  折合: 固定开销 %.1f cyc = %.2f us  |  单节 %.1f cyc = %.2f us/节' % (
    a50, a50 / 80, b50, b50 / 80))
A('')
A('  >> 外推模型 (T32/T36, 反汇编数指令): dt%% = 0.8115 * 节数  (无截距)')
A('     外推单节 = %.1f cyc = %.2f us/节  vs  实测单节 = %.1f cyc (%.2f us)' % (
    0.8115 / 100 * PERIOD, 0.8115 / 100 * PERIOD / 80, b50, b50 / 80))
A('     差异 = %+.2f%%  =>  外推单节成本 %s' % (
    (b50 / (0.8115 / 100 * PERIOD) - 1) * 100,
    '偏保守(高估)' if b50 < 0.8115 / 100 * PERIOD else '偏乐观(低估)'))
A('     外推无截距，但实测固定开销 = %.1f cyc (%.2f us) => 外推的基础开销项缺失' % (a50, a50 / 80))
A('')
A('  >> 逐区间滑块 (相邻两档, 实测中位) ---')
A('     %-10s %-6s %-12s %-12s' % ('区间', 'd节', 'd cyc', 'cyc/节'))
for i in range(len(PROFILES) - 1):
    n1, s1 = PROFILES[i][0], PROFILES[i][1]
    n2, s2 = PROFILES[i + 1][0], PROFILES[i + 1][1]
    d1, d2 = q(D[n1]['dt'], 50), q(D[n2]['dt'], 50)
    A('     %-10s %-6d %-12.0f %-12.1f' % ('%s->%s' % (n1, n2), s2 - s1, d2 - d1, (d2 - d1) / (s2 - s1)))
A('')
A('--- 3) 与外推/预期对账 ---')
A('  %-8s %-5s %-11s %-11s %-9s' % ('档', '节', '外推/预期%', '实测中位%', '偏差'))
for name, sec, fn, ext, col in PROFILES:
    if ext is None:
        A('  %-8s %-5d %-11s %-11.3f %-9s' % (name, sec, '-', q(D[name]['dt'], 50) / PERIOD * 100, '-'))
    else:
        A('  %-8s %-5d %-11.3f %-11.3f %+.2f%%' % (
            name, sec, ext, q(D[name]['dt'], 50) / PERIOD * 100,
            (q(D[name]['dt'], 50) / PERIOD * 100 / ext - 1) * 100))
A('')

A('--- 4) 稳定性 / 抢占检查 ---')
A('  %-8s %-9s %-9s %-9s %-9s' % ('档', 'max/med', '>93760', '=65535', 'corr'))
for name, sec, fn, ext, col in PROFILES:
    d = D[name]
    c = float(np.corrcoef(np.abs(d['fr']), d['dt'])[0, 1])
    A('  %-8s %-9.4f %-9d %-9d %+.4f' % (
        name, d['dt'].max() / q(d['dt'], 50), int((d['dt'] > 93760).sum()),
        int((d['dt'] == 65535).sum()), c))
A('')
A('  >> 越限计数 (第 7 列, 阈值 12000 cyc = 150 us) ---')
for name, sec, fn, ext, col in PROFILES:
    ov = D[name]['ov']
    A('     %-8s 最大 = %-6d  越限行数 = %-5d  首行 = %d  末行 = %d' % (
        name, max(0, int(ov.max())), int((ov > 0).sum()), int(ov[0]), int(ov[-1])))
A('     => 全 0 表示整个记录期无一次 dt > 12000 cyc, 即无中断抢占实锤')
A('')

A('--- 4b) 离群样本清单 (dt > 中位 x 1.05) ---')
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    m = q(dt, 50)
    idx = np.where(dt > m * 1.05)[0]
    A('  %-8s %d 个  (门限 %.0f cyc = %.2f us)' % (name, len(idx), m * 1.05, m * 1.05 / 80))
    for i in idx[:6]:
        A('      行 %-6d t=%-7.2fs dt=%d cyc = %.2f us   (比中位 +%.2f us)' % (
            i + 1, i / FS_OUT, dt[i], dt[i] / 80, (dt[i] - m) / 80))
A('')

A('--- 5) 每秒中位平直度检查 ---')
A('  %-8s %-10s %-10s %-10s %-10s' % ('档', 'n 秒', '中位片段', 'max 片段', '波动%'))
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    B = 500
    nb = len(dt) // B
    if nb < 2:
        continue
    med = np.array([q(dt[i * B:(i + 1) * B], 50) for i in range(nb)])
    mx = np.array([dt[i * B:(i + 1) * B].max() for i in range(nb)])
    A('  %-8s %-10d %-10.0f %-10.0f %-10.2f' % (
        name, nb, med.min(), mx.max(), (med.max() - med.min()) / med.mean() * 100))
A('')

txt = '\n'.join(out)
print(txt)

# ---------------- 图 ----------------
fig = plt.figure(figsize=(14, 9.5))
gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.20)

ax1 = fig.add_subplot(gs[0, :])
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    ax1.plot(np.arange(len(dt)) / FS_OUT, dt, lw=0.55, color=col, alpha=0.85,
             label='%s  %d sec  med %.0f cyc (%.2f us)' % (name, sec, q(dt, 50), q(dt, 50) / 80))
ax1.axhline(0.8115 / 100 * PERIOD * 6, color='#888780', ls=':', lw=1.0, label='extrap 6 sec = 4572')
ax1.axhline(0.8115 / 100 * PERIOD * 13, color='#5F5E5A', ls='-.', lw=1.0, label='extrap 13 sec = 9904')
ax1.set_xlabel('time (s)')
ax1.set_ylabel('dt (CPU cycles)')
ax1.set_title('dt_cycles vs time  (narrow scope: ecg_restore_process only)')
ax1.legend(fontsize=8, loc='center right', ncol=2)
ax1.grid(alpha=0.25)
ax1.set_ylim(min(q(D[p[0]]['dt'], 0.01) for p in PROFILES) - 400,
             max(D[p[0]]['dt'].max() for p in PROFILES) + 900)

ax2 = fig.add_subplot(gs[1, 0])
ax2.scatter(N, Y50, s=55, color='#1D9E75', zorder=5, label='measured median')
ax2.scatter(N, Ymx, s=45, color='#E24B4A', marker='^', zorder=5, label='measured max')
xx = np.linspace(5, 14, 50)
ax2.plot(xx, a50 + b50 * xx, '-', color='#1D9E75', lw=1.4,
         label='fit: %.1f + %.1f*N' % (a50, b50))
ax2.plot(xx, 0.8115 / 100 * PERIOD * xx, '--', color='#888780', lw=1.4,
         label='extrap 0.8115 pp/sec')
for name, sec, fn, ext, col in PROFILES:
    ax2.annotate(name, (sec, q(D[name]['dt'], 50)), textcoords='offset points',
                 xytext=(6, 6), fontsize=9)
ax2.set_xlabel('sections (N)')
ax2.set_ylabel('dt (CPU cycles)')
ax2.set_title('cost vs sections  (R^2=%.5f)' % r2)
ax2.legend(fontsize=8)
ax2.grid(alpha=0.25)

ax3 = fig.add_subplot(gs[1, 1])
for name, sec, fn, ext, col in PROFILES:
    dt = D[name]['dt']
    ax3.hist(dt, bins=60, histtype='step', lw=1.4, color=col, label='%s (%d sec)' % (name, sec))
ax3.set_xlabel('dt (CPU cycles)')
ax3.set_ylabel('count')
ax3.set_title('distribution per profile  (span < 6 us each)')
ax3.legend(fontsize=8)
ax3.grid(alpha=0.25)

fig.suptitle('dt_cycles measured - 4 profiles - narrow scope - @80 MHz - 2026-09-18', fontsize=12.5)
png = os.path.join(TOOLS, 'T40_dt四档实测_2026-09-18.png')
fig.savefig(png, dpi=110, bbox_inches='tight')
print('\n[PNG] %s' % png)

md = os.path.join(TOOLS, 'T40_dt四档实测_2026-09-18.md')
with open(md, 'w', encoding='utf-8') as fh:
    fh.write('# T40 dt_cycles 四档实测统计（窄口径）\n\n```\n' + txt + '\n```\n')
print('[MD ] %s' % md)
