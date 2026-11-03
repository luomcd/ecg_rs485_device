# -*- coding: utf-8 -*-
"""T45 关陷波风险配图：把"关掉工频陷波会付出什么代价"量化成六面板。
锚点数据全部来自实测（T35 报告 + Desktop/ADS1293 原始抓包），非模型推算。
"""
import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ---- 实测锚点 ----
# raw 域（未滤波，AFE 直出）工频峰值 µV
direct = {'50Hz': 13.8, '100Hz': 11.8, '150Hz': 7.7}        # 低阻直驱 (noin)
hi620 = {'50Hz': 19129.9, '100Hz': 630.7, '150Hz': 878.0}   # 620kΩ 接入
# 实测抑制深度 dB（raw -> 屏幕，IMP620_N2 档）
supp = {'50Hz': 75.9, '100Hz': 73.7, '150Hz': 84.3}

fig = plt.figure(figsize=(16.5, 10.5))

# ① 关陷波后工频进入量 vs 判限
ax = fig.add_subplot(2, 3, 1)
ks = ['50Hz', '100Hz', '150Hz']; x = np.arange(3); w = 0.36
b1 = ax.bar(x - w / 2, [direct[k] for k in ks], w, label='低阻直驱 (标准试验条件)', color='#2ca02c')
b2 = ax.bar(x + w / 2, [hi620[k] for k in ks], w, label='620 kΩ 高阻接入', color='#d62728')
ax.set_yscale('log'); ax.set_ylim(1, 8e5)
ax.axhline(100, color='k', ls='--', lw=1.3); ax.axhline(30, color='gray', ls=':', lw=1.3)
ax.text(1.0, 6e4, '虚线：脉冲位移判限 100 µV\n点线：噪声判限 30 µV', fontsize=8, ha='center',
        bbox=dict(fc='white', ec='#bbb', alpha=.85, pad=2.5))
for b, v in zip(b1, [direct[k] for k in ks]):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.18, f'{v:.0f}', ha='center', fontsize=9, color='#2ca02c')
for b, v in zip(b2, [hi620[k] for k in ks]):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.18, f'{v:.0f}', ha='center', fontsize=9, color='#d62728')
ax.set_xticks(x); ax.set_xticklabels(ks); ax.set_ylabel('工频进入量 (µV 峰值, 对数轴)')
ax.set_title('① 关掉陷波后工频直接进输出\n同日实测：源阻抗决定一切（差 1386×）', fontsize=11)
ax.legend(fontsize=8.5, loc='upper left'); ax.grid(alpha=.3, axis='y', which='both')

# ② 源阻抗 -> 工频进入量（核心：可用域）
ax = fig.add_subplot(2, 3, 2)
Z = np.logspace(np.log10(300), np.log10(700000), 400)
I = 13.8 * (Z / 375.0) ** 0.976
ax.loglog(Z, I, color='#1f77b4', lw=2)
ax.set_ylim(8, 40000)
ax.axhline(100, color='r', ls='--', lw=1.4); ax.text(330, 118, '判限 100 µV', fontsize=8.5, color='r')
ax.axvspan(300, 3000, color='#2ca02c', alpha=.13)
ax.axvspan(100000, 700000, color='#d62728', alpha=.13)
for z, i, lab, c, off in ((375, 13.8, '375 Ω 直驱\n13.8 µV', '#2ca02c', (16, 4)),
                          (51000, 1630, '51 kΩ\n(CMRR试验用)\n≈1.6 mV', '#ff7f0e', (8, 6)),
                          (620000, 19130, '620 kΩ\n19.1 mV', '#d62728', (-72, 6))):
    ax.plot(z, i, 'o', ms=8, color=c)
    ax.annotate(lab, (z, i), textcoords='offset points', xytext=off, fontsize=8.5, color=c)
ax.set_xlabel('信号源阻抗 Z_src (Ω)'); ax.set_ylabel('工频进入量 (µV)')
ax.set_title('② ★ 开关的可用域极窄\n"关陷波"仅在低阻直驱下安全', fontsize=11)
ax.grid(alpha=.3, which='both')

# ③ 实测抑制深度
ax = fig.add_subplot(2, 3, 3)
b = ax.bar(ks, [supp[k] for k in ks], color='#4c72b0')
for bb, v in zip(b, [supp[k] for k in ks]):
    ax.text(bb.get_x() + bb.get_width() / 2, v + 1.5, f'−{v:.1f} dB', ha='center', fontsize=10)
ax.axhline(0, color='r', lw=2.5)
ax.text(1.0, 94, '关掉 → 这条保护整条归零', ha='center', fontsize=9.5, color='r')
ax.set_ylim(0, 112); ax.set_ylabel('实测抑制深度 (dB)')
ax.set_title('③ 陷波在保护什么（raw→屏幕, N2 实测）\n50 Hz −75.9 dB ｜ 100/150 Hz −73.7/−84.3 dB', fontsize=11)
ax.grid(alpha=.3, axis='y')

# ④ CMRR 余量
ax = fig.add_subplot(2, 3, 4)
vals = [10.0 * 10 ** (-d / 20) * 1e6 * 2 * np.sqrt(2) for d in (100, 85, 80)]
b = ax.bar(['100 dB\n(datasheet typ)', '85 dB\n(大信号退化)', '80 dB\n(最坏)'], vals,
           color=['#2ca02c', '#ff7f0e', '#d62728'])
for bb, v in zip(b, vals):
    ax.text(bb.get_x() + bb.get_width() / 2, v * 1.04, f'{v:.0f} µV pp', ha='center', fontsize=9.5)
ax.axhline(1000, color='r', ls='--', lw=1.4)
ax.text(2.45, 1045, '判据 10 mm = 1 mV pp', ha='right', fontsize=8.5, color='r')
ax.set_ylabel('等效差模 (µV pp)'); ax.set_ylim(0, 1700)
ax.set_title('④ CMRR 试验（关陷波后测真功夫）\n10 V rms 共模 → 等效差模 vs 1 mV 判据', fontsize=11)
ax.grid(alpha=.3, axis='y')

# ⑤ 风险分级矩阵
ax = fig.add_subplot(2, 3, 5)
ax.set_xlim(0.5, 5.5); ax.set_ylim(0.5, 5.5)
for (x0, y0, w0, h0, c) in [(0.5, 3.5, 2.5, 2.0, '#ff7f0e'), (3.0, 3.5, 2.5, 2.0, '#d62728'),
                            (0.5, 1.5, 2.5, 2.0, '#2ca02c'), (3.0, 1.5, 2.5, 2.0, '#ff7f0e'),
                            (0.5, 0.5, 2.5, 1.0, '#2ca02c'), (3.0, 0.5, 2.5, 1.0, '#2ca02c')]:
    ax.add_patch(plt.Rectangle((x0, y0), w0, h0, color=c, alpha=.15))
pts = [(4.75, 5.15, '高阻下误关→前端饱和', (6, -16)),
       (4.10, 4.30, 'CMRR 试验暴露真性能', (6, 5)),
       (3.55, 3.80, '"可选设置"触发全项复测', (-130, -4)),
       (4.30, 2.45, 'ST段噪声差 >50 µV', (-118, 4)),
       (2.00, 3.85, '报告未标示状态', (-100, -14)),
       (2.25, 2.25, '切换爆音/基线跳', (6, -14)),
       (1.20, 1.15, '命令解析失败落错态', (6, 4))]
for xx, yy, lab, off in pts:
    c = '#d62728' if yy > 3.5 else ('#ff7f0e' if yy > 1.5 else '#2ca02c')
    ax.plot(xx, yy, 'o', ms=9, color=c)
    ax.annotate(lab, (xx, yy), textcoords='offset points', xytext=off, fontsize=7.8, color=c)
ax.set_xlabel('发生概率 →'); ax.set_ylabel('后果严重度 →'); ax.set_xticks([]); ax.set_yticks([])
ax.set_title('⑤ 风险分级（红=须先管控）', fontsize=11)

# ⑥ 缓解措施
ax = fig.add_subplot(2, 3, 6); ax.axis('off')
ax.set_title('⑥ 缓解：把"关"做成试验模式，不是用户选项', fontsize=11)
txt = [('可用域', '仅低阻直驱(≤2 kΩ)允许关；高阻接线强制开', '#d62728'),
       ('默认态', '默认=开；关须显式命令 + 超时自动回开', '#ff7f0e'),
       ('合规', '"关"声明为试验模式，非"可选滤波器设置"', '#ff7f0e'),
       ('报告', '输出流带 notch 状态位，报告须标示', '#ff7f0e'),
       ('切换', '跳过节 + 清零 d1/d2 + 5~10 ms 滑窗', '#2ca02c'),
       ('环境', '关陷波的试验须在低干扰环境先验工频', '#2ca02c'),
       ('待测', '开/关 ST 段噪声差 ≤50 µV（ANE20000）', '#1f77b4')]
for i, (k, v, c) in enumerate(txt):
    y = 0.90 - i * 0.128
    ax.text(0.0, y, f'● {k}', fontsize=9.2, color=c, fontweight='bold', transform=ax.transAxes)
    ax.text(0.20, y, v, fontsize=8.0, color='#333', transform=ax.transAxes)

plt.tight_layout()
plt.savefig('T45_关陷波风险_2026-09-18.png', dpi=115)
print('图已存: T45_关陷波风险_2026-09-18.png')
print('CMRR 等效差模 pp:', [round(v, 1) for v in vals])
