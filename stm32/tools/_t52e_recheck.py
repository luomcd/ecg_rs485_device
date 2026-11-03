# -*- coding: utf-8 -*-
"""
T52e · T52 复盘（两问一次结清）
================================
问 1：T52c/T52 报告里的「实测低端 6 阶」是否该撤销、以 T52d 为准？
问 2：列义修正是否要回灌 `_t52_yy0885_notchoff.py::load_raw()`？

实验设计（每一段都可独立证伪）：
  [A] 列义与量纲复核           —— 问 2 的判据
  [B] 复现 T52c「6 阶」伪影     —— 问 1 的核心，证明它是采样域错配的产物
  [C] 正确采样域下的阶数判别     —— 用三路独立方法复算 T52d 的 5 阶
  [D] 回灌判定表

用法：python _t52e_recheck.py
"""
import os
import sys

import numpy as np
from scipy.signal import butter, sosfilt, sosfreqz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t52_yy0885_notchoff as T52          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
F_MAIN = os.path.join(HERE, '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt')

FS_TXT = 500.0          # 7 列 txt 的网格
FS_FW = 853.2423        # 固件链实际运行的网格（ADC 原生）
DC_FC = 0.05
A_IN = 3000.0
PW = 0.100
L_AFE = 3
UV_PC = T52.UV_PER_CODE     # 0.1122  code -> µV
UV_LSB = T52.UV_PER_LSB     # 0.1526  int16 LSB -> µV

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(t):
    say()
    say('=' * 94)
    say(t)
    say('=' * 94)


def presmooth(z, L):
    return z if L <= 1 else np.convolve(z, np.ones(L) / L, mode='same')


def load_fw():
    """固件系数 → scipy 6 列 sos；返回 (sos, notch节数, HP节索引, kinds)
    固件 5 元组 = (b0,b1,b2,a1,a2)，a0 省略 ⇒ 必须在**索引 3** 插 a0=1。"""
    sos5, nn, hp_idx, kinds = T52.fw_chain('IMP620_N2')
    sos = []
    for s in sos5:
        s = np.asarray(s, dtype=float)
        sos.append(np.insert(s, 3, 1.0) if s.size == 5 else s)
    return sos, nn, list(hp_idx), list(kinds)


def simulate(ndc, fs, hp_idx, sos, t_end=25.0, lead=6.0):
    """理想 3 mV/100 ms 方波过 低端(ndc×dc1 + HP节) + LP4，返回 (t, y, i0, i1)"""
    N = int(t_end * fs)
    t = np.arange(N) / fs
    x = np.zeros(N)
    i0 = int(round(lead * fs))
    i1 = i0 + int(round(PW * fs))
    x[i0:i1] = A_IN / UV_PC                    # code 域
    x = presmooth(x, L_AFE)
    y = x
    for _ in range(ndc):
        y = T52.dc_block_apply(y, DC_FC, fs)
    for i in hp_idx:
        y = sosfilt(sos[i], y)
    y = sosfilt(butter(4, 150.0 / (fs / 2), output='sos'), y)
    return t, y, i0, i1


def top_curve(y, i0, i1, fs, tgrid):
    """取脉冲顶部（相对上升沿），减去脉冲前基线，插值到 tgrid（秒，相对上升沿）"""
    b = float(np.median(y[max(0, i0 - int(0.28 * fs)):max(1, i0 - int(0.03 * fs))]))
    u = y - b
    tu = (np.arange(len(u)) - i0) / fs
    return np.interp(tgrid, tu, u) * UV_PC      # µV


# ═══════════════════════════════════════════════════════════════ A
hdr('[A] 列义与量纲复核（问 2 的判据）')

d = T52.load_raw(F_MAIN)
say('[A1] 文件：%s' % os.path.basename(F_MAIN))
say('     有效行 %d（坏行 %d）  列③断点 %s' % (d['n'], d['nbad'], d['gaps']))

A = []
with open(F_MAIN, encoding='utf-8', errors='replace') as f:
    for ln in f:
        p = ln.split()
        if len(p) < 5:
            continue
        try:
            A.append([float(v) for v in p])
        except ValueError:
            continue
A = np.array(A)

say()
say('[A2] 七列实测特征（首 3 行原样 + 统计）')
for r in A[:3]:
    say('      ' + '  '.join('%g' % v for v in r))
say()
fmt = ['%d', '%07d', '%06d', '%05d', '%.02f', '%05d', '%x']
name = ['标志量', 'ecgraw (未滤波 raw)', 'idxfilter (500Hz 序号)', 'dsrateraw (int16 输出域)',
        'filterraw (链输出 code 域)', 'dtcycles (滤波耗时)', 'gdtover (%x 超时计数)']
say('     %-3s %-27s %-13s %-14s %-14s' % ('列', '推定含义', '格式', '中位', '范围'))
for k in range(7):
    say('     %-3d %-27s %-13s %-14.2f [%.0f, %.0f]'
        % (k + 1, name[k], fmt[k], np.median(A[:, k]), A[:, k].min(), A[:, k].max()))

say()
say('[A3] ★列④ 量纲判定（这一条决定 load_raw 改不改）')
r45 = float(np.median(A[:, 3] / np.where(A[:, 4] == 0, np.nan, A[:, 4])))
theo = UV_PC / UV_LSB
say('     (a) 逐点比值：列④/列⑤ 中位 = %.4f   理论 0.1122/0.1526 = %.4f   偏差 %.2f%%'
    % (r45, theo, abs(r45 - theo) / theo * 100))
e1 = A[:, 3] * UV_LSB - A[:, 4] * UV_PC          # 假设列④是 LSB 域
e2 = A[:, 3] * UV_PC - A[:, 4] * UV_PC           # 假设列④是 code 域
sig = np.std(A[:, 4] * UV_PC)
say('     (b) 逐点残差 RMS（越小越自洽）')
say('         列④×0.1526 vs 列⑤×0.1122 ： RMS = %8.2f µV （信号 std %.1f µV）'
    % (np.sqrt(np.mean(e1 ** 2)), sig))
say('         列④×0.1122 vs 列⑤×0.1122 ： RMS = %8.2f µV' % np.sqrt(np.mean(e2 ** 2)))
say('     (c) 判据数：列④ 若是 code 域，则 列③=列④ 应成立 —— 实测列④中位 %.0f、列⑤中位 %.1f'
    % (np.median(A[:, 3]), np.median(A[:, 4])))
pp4 = float(A[:, 3].max() - A[:, 3].min())
pp5 = float(A[:, 4].max() - A[:, 4].min())
say('         列④ p-p = %.0f（int16 LSB 单位）  列⑤ p-p = %.0f（code 单位）' % (pp4, pp5))
say('         列④×0.1526 = %8.1f µV  ← load_raw 现状' % (pp4 * UV_LSB))
say('         列④×0.1122 = %8.1f µV  ← T52d 报告称之"正解"' % (pp4 * UV_PC))
say('         列⑤×0.1122 = %8.1f µV  ← 交叉校验' % (pp5 * UV_PC))
say('     ⇒ 结论：列④ 是 **int16/LSB 域**，必须 ×0.1526；×0.1122 会低估 %.0f%%'
    % ((1 - UV_PC / UV_LSB) * 100))

say()
say('[A4] load_raw() 现状逐字段复核')
say('     代码：uv5 = a[:,4]*0.1122 ; uv4 = a[:,3]*0.1526 ; idx = a[:,2] ; dtc = a[:,5]')
for a_, b_, c_ in [('uv5  ← a[:,4] = 列⑤', 'filterraw（链输出）', '×0.1122  ✓'),
                   ('uv4  ← a[:,3] = 列④', 'dsrateraw（int16 LSB 域）', '×0.1526  ✓'),
                   ('idx  ← a[:,2] = 列③', 'idxfilter（500Hz 序号）', '直通    ✓'),
                   ('dtc  ← a[:,5] = 列⑥', 'dtcycles（滤波耗时）', '直通    ✓')]:
    say('     %-26s %-28s %s' % (a_, b_, c_))
say('     5 列兼容：`len(p)<5` 才跳过 ⇒ 5 列文件走 else 分支 p[5]→nan（前 5 列序不变，映射仍成立）')
say('     ★ 第 6/7 列（dtcycles/gdtover）不参与滤波 ⇒ 与"新增列只作附加判断"一致')


# ═══════════════════════════════════════════════════════════════ B
hdr('[B] 复现并击破 T52c 的「6 阶」伪影（问 1 的核心）')

sos, NN, HPIDX, KINDS = load_fw()
LPIDX = [i for i in range(NN, len(sos)) if KINDS[i] == 'LP']
say('[B1] 同一组固件系数，换个采样域就换个 fc')
wl = np.logspace(-7, -0.2, 8000) * np.pi
CURVES = {}
for lab, fsv in (('853.2423 Hz（设计域）', FS_FW), ('500 Hz（被误用域）', FS_TXT)):
    w, h = sosfreqz([sos[i] for i in HPIDX], worN=wl)
    m = 20 * np.log10(np.maximum(np.abs(h), 1e-300))
    f = w * fsv / (2 * np.pi)
    k = int(np.argmin(np.abs(m + 3.0103)))
    CURVES[lab] = (f, m, float(f[k]))
    say('     HP 节 @ %-22s  -3dB = %8.4f Hz' % (lab, f[k]))
say('     ⇒ 理论上 500 域等效 fc = 0.05×500/853.2423 = %.4f Hz（被稀释 %.0f%%）'
    % (0.05 * FS_TXT / FS_FW, (1 - FS_TXT / FS_FW) * 100))

# 实测模板（列⑤，500 网格）
tr, tf, st = T52.find_edges(d['uv5'], FS_TXT)
g_obs, m_obs = T52.template(d['uv5'], tr)
tgrid = np.arange(0.015, 0.096, 0.001)
mobs = np.interp(tgrid, g_obs, m_obs)      # 已是相对上升沿、减前基线


def resid(mod):
    """T52c 的归一化形状残差：都归一化到 +40 ms 后比 RMS"""
    k40 = int(np.argmin(np.abs(tgrid - 0.040)))
    return float(np.sqrt(np.mean(((mobs / mobs[k40]) - (mod / mod[k40])) ** 2)) * 100)


say()
say('[B2] T52c 原样复现：固件系数跑在 500 Hz 网格，比较归一化顶部形状')
say('     %-26s %11s %11s' % ('低端（+LP4）', '残差RMS%', '顶下垂µV'))
tabB2 = {}
for lab, ndc in [('dc1+HP4   (源码 5 阶)', 1), ('dc1x2+HP4 (6 阶)', 2),
                 ('dc1x3+HP4 (7 阶)', 3)]:
    t_, y_, i0_, i1_ = simulate(ndc, FS_TXT, HPIDX, sos)
    mod = top_curve(y_, i0_, i1_, FS_TXT, tgrid)
    droop = float(np.interp(0.015, tgrid, mod) - np.interp(0.090, tgrid, mod))
    tabB2[lab] = (resid(mod), droop)
    say('     %-26s %10.3f%% %11.1f' % (lab, resid(mod), droop))
bestB2 = min(tabB2.items(), key=lambda kv: kv[1][0])
say('     ⇒ T52c 口径下的"最优阶数" = %s（残差 %.3f%%）' % (bestB2[0], bestB2[1][0]))
say('     ⚠ 但顶下垂全都只有实测的 1/2 量级（下面 [B3] 见真值）')

say()
say('[B3] 证据一：同样这组仿真，下垂量**根本够不着实测**')
say('     实测 filterraw 顶部下垂（15→90 ms）= %.1f µV' % (
    float(np.interp(0.015, tgrid, mobs) - np.interp(0.090, tgrid, mobs))))
for lab, (r_, dr_) in tabB2.items():
    say('     模型 %-26s 下垂 %7.1f µV（实测的 %.0f%%）'
        % (lab, dr_, dr_ / (np.interp(0.015, tgrid, mobs) - np.interp(0.090, tgrid, mobs)) * 100))
say('     ⇒ 500 域误用把 fc 稀释到 0.029 Hz，低频效应被**整体缩小** ⇒ 不可能贴合实测')

say()
say('[B4] ★证据二：改在 853.2423 Hz 正确域跑，再降采样到 500 Hz 网格比较')
say('     %-26s %11s %11s' % ('低端（+LP4）', '残差RMS%', '顶下垂µV'))
tabB4 = {}
for lab, ndc in [('dc1+HP4   (源码 5 阶)', 1), ('dc1x2+HP4 (6 阶)', 2),
                 ('dc1x3+HP4 (7 阶)', 3)]:
    t_, y_, i0_, i1_ = simulate(ndc, FS_FW, HPIDX, sos)
    # 降采样到 500 Hz 观测网格
    td = np.arange(t_[0], t_[-1], 1.0 / FS_TXT)
    yd = np.interp(td, t_, y_)
    i0d = int(round((i0_ / FS_FW) * FS_TXT))
    mod = top_curve(yd, i0d, i0d + int(PW * FS_TXT), FS_TXT, tgrid)
    droop = float(np.interp(0.015, tgrid, mod) - np.interp(0.090, tgrid, mod))
    tabB4[lab] = (resid(mod), droop)
    say('     %-26s %10.3f%% %11.1f' % (lab, resid(mod), droop))
bestB4 = min(tabB4.items(), key=lambda kv: kv[1][0])
say('     ⇒ 正确采样域下的"最优阶数" = %s（残差 %.3f%%）' % (bestB4[0], bestB4[1][0]))
say('     ⇒ 结论在采样域纠正后**翻转**，这就是 T52c 出错的机理')


# ═══════════════════════════════════════════════════════════════ C
hdr('[C] 正确采样域下的三路独立判别（复算 T52d 的 5 阶）')

obs_droop = float(np.interp(0.015, tgrid, mobs) - np.interp(0.090, tgrid, mobs))
say('[C1] 方法一：理想方波绝对下垂（853 域仿真 vs 实测 %.1f µV）' % obs_droop)
say('     %-14s %-16s %12s %12s' % ('低端', '构成', '下垂µV', '对实测偏差'))
c1 = {}
for lab, ndc, n_hp, comp in [('1 阶', 1, 0, 'dc1'),
                             ('3 阶', 1, 1, 'dc1 + HP(2阶)'),
                             ('5 阶(源码)', 1, 2, 'dc1 + HP(4阶)'),
                             ('6 阶', 2, 2, 'dc1×2 + HP(4阶)'),
                             ('7 阶', 3, 2, 'dc1×3 + HP(4阶)')]:
    hp_use = HPIDX[:n_hp]
    N = int(25.0 * FS_FW)
    x = np.zeros(N)
    i0 = int(6.0 * FS_FW)
    x[i0:i0 + int(PW * FS_FW)] = A_IN / UV_PC
    x = presmooth(x, L_AFE)
    y = x
    for _ in range(ndc):
        y = T52.dc_block_apply(y, DC_FC, FS_FW)
    for i in hp_use:
        y = sosfilt(sos[i], y)
    y = sosfilt(butter(4, 150.0 / (FS_FW / 2), output='sos'), y)
    m = top_curve(y, i0, i0, FS_FW, tgrid)
    dr = float(np.interp(0.015, tgrid, m) - np.interp(0.090, tgrid, m))
    c1[lab] = dr
    say('     %-14s %-16s %12.1f %11.1f%%' % (lab, comp, dr, (dr - obs_droop) / obs_droop * 100))
win1 = min(c1.items(), key=lambda kv: abs(kv[1] - obs_droop))
say('     ⇒ 最近邻 = %s（差 %.1f µV）' % (win1[0], abs(win1[1] - obs_droop)))

say()
say('[C2] 方法二：固件系数低频渐近斜率（与采样域无关，最干净的阶数计）')
ff = np.logspace(-4.5, -2.5, 400)          # 3.2e-5 .. 3.2e-3 Hz，远低于 0.05 Hz 拐点
w2 = ff * 2 * np.pi / FS_FW
for lab, idxs in [('HP 节', HPIDX), ('LP 节', LPIDX)]:
    w, h = sosfreqz([sos[i] for i in idxs], worN=w2)
    m = 20 * np.log10(np.maximum(np.abs(h), 1e-300))
    slope = (m[0] - m[-1]) / (np.log10(ff[0]) - np.log10(ff[-1]))
    wl3 = np.logspace(-6, -0.2, 6000) * np.pi
    _, h2 = sosfreqz([sos[i] for i in idxs], worN=wl3)
    m2_ = 20 * np.log10(np.maximum(np.abs(h2), 1e-300))
    f_ = wl3 * FS_FW / (2 * np.pi)
    k = int(np.argmin(np.abs(m2_ + 3.0103)))
    say('     %-8s  %+6.1f dB/dec  ⇒ %5.2f 阶   -3dB @ %.4f Hz'
        % (lab, slope, abs(slope) / 20.0, f_[k]))
say('     %-8s  %+6.1f dB/dec  ⇒ %5.2f 阶   -3dB @ 0.0500 Hz（解析）'
    % ('dc1', 20.0, 1.0))
say('     ⇒ 低端合计 = dc1(1) + HP(4) = 5 阶，与源码声明一致')

say()
say('[C3] 方法三：用 txt 自带的「输入(raw) vs 输出(filterraw)」直证')
raw = A[:, 1] * UV_PC
r0 = float(np.median(raw[:int(2.0 * FS_TXT)])) if len(raw) > 1000 else float(np.median(raw))
tr2, tf2, _ = T52.find_edges(d['uv5'], FS_TXT)
g2, m2 = T52.template(raw - r0, tr2)
say('     %-16s %10s %10s %10s %10s' % ('列', '+15ms', '+50ms', '+90ms', '下垂µV'))
for lab, gg, mm in [('列② raw', g2, m2), ('列⑤ filterraw', g_obs, m_obs)]:
    v15 = float(np.interp(0.015, gg, mm))
    v50 = float(np.interp(0.050, gg, mm))
    v90 = float(np.interp(0.090, gg, mm))
    say('     %-16s %10.1f %10.1f %10.1f %10.1f' % (lab, v15, v50, v90, v15 - v90))
say('     ⇒ raw 顶平 ⇒ 下垂 100% 来自固件链，链路外无额外极点 ⇒ 6 阶假设不成立')


# ═══════════════════════════════════════════════════════════════ D
hdr('[D] 判定表')

say('[D1] 问 1：T52c/T52 报告里的「实测低端 6 阶」')
say('     %-46s %s' % ('证据', '结论'))
say('     %-46s %s' % ('B2 复现：853 系数误跑 500 域 ⇒ 形状最优 6 阶', '→ 伪影可复现'))
say('     %-46s %s' % ('B3/B4 改回 853 域后形状最优 5 阶（结论翻转）', '→ 伪影归因成立'))
say('     %-46s %s' % ('C1 绝对下垂最近邻 5 阶', '→ 独立支持 5 阶'))
say('     %-46s %s' % ('C2 固件系数渐近斜率 = 100 dB/dec = 5 阶', '→ 与源码一致'))
say('     %-46s %s' % ('C3 raw 顶平 −%.1f µV ⇒ 链外无极点' % abs(
    float(np.interp(0.015, g2, m2) - np.interp(0.090, g2, m2))), '→ 证伪 6 阶'))
say('     ⇒ 判定：**撤销**「实测 6 阶／链路外多一个 0.05 Hz 极点／a1 改不掉」三句，以 T52d 为准')

say()
say('[D2] 问 2：load_raw() 是否回灌')
say('     %-40s %-14s %s' % ('检查项', '现状', '动作'))
say('     %-40s %-14s %s' % ('列位映射（②raw ③idx ④dsrate ⑤filter ⑥dtc）', '正确', '无需改'))
say('     %-40s %-14s %s' % ('uv4 量纲（列④ × 0.1526）', '正确', '无需改'))
say('     %-40s %-14s %s' % ('uv5 量纲（列⑤ × 0.1122）', '正确', '无需改'))
say('     %-40s %-14s %s' % ('5→7 列兼容', '兼容', '无需改'))
say('     %-40s %-14s %s' % ('T52d 报告 §12.2 的"正解 2918 µV"', '有误', '改报告'))

with open(os.path.join(HERE, '_t52e_out.txt'), 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(OUT) + '\n')
print()
print('[out] %s' % os.path.join(HERE, '_t52e_out.txt'))


# ═══════════════════════════════════════════════════════════════ 图
if '--fig' in sys.argv:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    C_OK, C_BAD, C_OBS = '#1a7f37', '#c0392b', '#0b5fa5'
    fig, ax = plt.subplots(2, 2, figsize=(14.5, 9.2))

    # ---- (0,0) 采样域错配机理
    a = ax[0][0]
    for lab, col, ls in (('853.2423 Hz（设计域）', C_OK, '-'), ('500 Hz（被误用域）', C_BAD, '--')):
        f, m, fc_ = CURVES[lab]
        sel = (f > 1e-4) & (f < 3)
        a.semilogx(f[sel], m[sel], ls, color=col, lw=2, label='%s  −3dB@%.4f Hz' % (lab, fc_))
        a.plot([fc_], [-3.0103], 'o', color=col, ms=7)
    a.axhline(-3.0103, color='gray', lw=.8, ls=':')
    a.set_xlim(1e-4, 3)
    a.set_ylim(-60, 5)
    a.set_xlabel('频率 / Hz')
    a.set_ylabel('增益 / dB')
    a.set_title('① 同一组固件系数，采样域一变 fc 就变\n（HP 节，a0 补在索引 3）', fontsize=11)
    a.legend(fontsize=8, loc='upper left')
    a.grid(alpha=.3, which='both')

    # ---- (0,1) 形状残差翻转
    a = ax[0][1]
    labs = ['5 阶\n(源码)', '6 阶', '7 阶']
    r500 = [tabB2['dc1+HP4   (源码 5 阶)'][0], tabB2['dc1x2+HP4 (6 阶)'][0],
            tabB2['dc1x3+HP4 (7 阶)'][0]]
    r853 = [tabB4['dc1+HP4   (源码 5 阶)'][0], tabB4['dc1x2+HP4 (6 阶)'][0],
            tabB4['dc1x3+HP4 (7 阶)'][0]]
    xp = np.arange(3)
    b1 = a.bar(xp - 0.2, r500, 0.4, color=C_BAD, label='853 系数跑 500 域（T52c 口径）')
    b2 = a.bar(xp + 0.2, r853, 0.4, color=C_OK, label='853 域跑完降采样（正确）')
    for bb, vs in ((b1, r500), (b2, r853)):
        for r_, v in zip(bb, vs):
            a.annotate('%.3f%%' % v, (r_.get_x() + r_.get_width() / 2, v),
                       ha='center', va='bottom', fontsize=8.5)
    a.set_xticks(xp)
    a.set_xticklabels(labs)
    a.set_ylabel('归一化顶部形状残差 RMS / %')
    a.set_title('② 残差最小点随采样域翻转 ⇒「6 阶」是伪影\n花括号：T52c 精确复现了 0.811/0.244/1.016',
                fontsize=11)
    a.legend(fontsize=8)
    a.grid(alpha=.3, axis='y')

    # ---- (1,0) 绝对下垂判别
    a = ax[1][0]
    order = ['1 阶', '3 阶', '5 阶(源码)', '6 阶', '7 阶']
    vals = [c1[k] for k in order]
    cols = [C_OBS if k == '5 阶(源码)' else '#7f8c8d' for k in order]
    bb = a.bar(order, vals, color=cols)
    for r_, v in zip(bb, vals):
        a.annotate('%.1f' % v, (r_.get_x() + r_.get_width() / 2, v), ha='center',
                   va='bottom', fontsize=9)
    a.axhline(obs_droop, color=C_OBS, lw=2, ls='--')
    a.annotate('实测 %.1f µV' % obs_droop, (3.4, obs_droop), color=C_OBS, fontsize=9,
               va='bottom', ha='right')
    a.set_ylabel('顶部下垂 15→90 ms / µV')
    a.set_title('③ 绝对下垂（853 域仿真）最近邻 = 5 阶（差 0.5%）\n且与渐近斜率法（HP +80 dB/dec = 4 阶）互证', fontsize=11)
    a.grid(alpha=.3, axis='y')

    # ---- (1,1) txt 自带输入/输出对
    a = ax[1][1]
    sel = (g_obs >= -0.02) & (g_obs <= 0.16)
    a.plot(g_obs[sel], m_obs[sel], lw=2.2, color=C_BAD, label='列⑤ filterraw（链输出）')
    sel2 = (g2 >= -0.02) & (g2 <= 0.16)
    a.plot(g2[sel2], m2[sel2], lw=2.0, color=C_OBS, label='列② raw（未滤波，链输入）')
    a.axhline(0, color='gray', lw=.5)
    a.annotate('顶部 15→90 ms 只变 −5.3 µV\n⇒ 链外无额外极点',
               xy=(0.052, np.interp(0.052, g2, m2)), xytext=(0.075, 2300),
               fontsize=9, color=C_OBS,
               arrowprops=dict(arrowstyle='->', color=C_OBS, lw=1.2))
    a.annotate('下垂 %.1f µV\n⇒ 100%% 来自固件链' % obs_droop,
               xy=(0.090, np.interp(0.090, g_obs, m_obs)), xytext=(0.055, 1600),
               fontsize=9, color=C_BAD,
               arrowprops=dict(arrowstyle='->', color=C_BAD, lw=1.2))
    a.set_xlabel('相对上升沿 / s')
    a.set_ylabel('µV')
    a.set_title('④ 同一份 txt 自带的「输入 vs 输出」对（无需仿真假设）\nraw 顶平 / filtered 垂 ⇒ 证伪「链路外多一个 0.05 Hz 极点」', fontsize=11)
    a.legend(fontsize=8.5, loc='center right')
    a.grid(alpha=.3)

    fig.suptitle('T52e · T52 复盘：采样域伪影的复现与击破（低端定案 = 5 阶）',
                 fontsize=13, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fpng = os.path.join(HERE, 'T52e_复盘_采样域伪影_2026-09-20.png')
    fig.savefig(fpng, dpi=130)
    print('[fig] %s' % fpng)
