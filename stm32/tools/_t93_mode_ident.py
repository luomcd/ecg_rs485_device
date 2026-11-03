# -*- coding: utf-8 -*-
"""
T93 · 监护/诊断档位「一眼识别」方案  (2026-09-22)

背景
----
T92 落码后 mon / diag 两档**只差第 15 节上角**（LP2@45 vs LP2@250），
notch OFF 时整条链只有 2 个元件：dc_block(fc=0.02) + 1 节 LP2。
⇒ 低端（0.05 / 0.67 / 5 Hz）两档**逐位相同**；差别全在高端。
所以"看指标是否达标"（0.67 / 5 / 40 Hz）**判不出档位**，必须换激励与观测量。

本脚本回答：**上位机屏幕上出现什么波形 ⇒ 当前就是哪个 mode。**

三套手段
--------
① 正弦看幅度：150 Hz 时 mon 5.7% vs diag 75.6%（**13×**）
② 方波看边沿：25 Hz 方波 ⇒ diag 是方波、mon 近似正弦；
               1 Hz 方波边沿过渡点数 diag 0~1 点 vs mon 3~4 点（500 域）
③ 无源看噪声：悬空本底噪声带宽 mon 明显窄（RMS 比 ≈ 0.42）

输出：_t93_mode_ident_out.txt（文本报告） + _t93_mode_ident.png（对比图）
用法：python tools/_t93_mode_ident.py
"""
import io
import os
import sys
import argparse
import importlib.util

import numpy as np

sys.dont_write_bytecode = True          # 复用 T92 模型时不落 __pycache__（避免污染 tools/）

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TXT = os.path.join(HERE, '_t93_mode_ident_out.txt')
OUT_PNG = os.path.join(HERE, '_t93_mode_ident.png')
FS_TXT = 500.0                     # 上位机显示域（列⑤ 行率）
MVPP = 1.0                         # 现场默认注入幅度（1 mVpp = 5 mm @ 0.2 mV/mm）

_buf = []


def p(s=''):
    print(s)
    _buf.append(s)


def _load_model():
    """复用 T92 判读脚本的整机频响模型（避免两套实现漂移）。"""
    spec = importlib.util.spec_from_file_location(
        '_t92mm', os.path.join(HERE, '_t92_mon_measure.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------- 时域仿真
def gain(ab, mode, f, dc_fc=None):
    """整机绝对增益 |H(f)|（notch OFF 口径，标量）。"""
    return float(np.abs(M.h_chain_notch_off(ab, mode, float(f), dc_fc))[0]) * float(M.afe_gain(float(f))[0])


def sim(ab, mode, kind, f, mvpp=MVPP, cycles=4):
    """生成 code 域输入 → 过 `_sim_chain`(853 域) → 最近邻抽取到 500 域（= 上位机）。"""
    code_pp = mvpp * 1000.0 / M.UV_PER_CODE      # mVpp → code 峰峰
    A = code_pp / 2.0
    if f <= 0.5:
        dur = 1.0 / f * 1.2
    else:
        dur = cycles / f
    n = int(round(dur * M.FS))
    t = np.arange(n) / M.FS
    if kind == 'sine':
        x = A * np.sin(2 * np.pi * f * t)
    elif kind == 'square':
        x = A * np.sign(np.sin(2 * np.pi * f * t))
    else:
        raise ValueError(kind)
    y = M._sim_chain(ab, mode, x)
    k = int(round(dur * FS_TXT))
    idx = np.clip(np.round(np.arange(k) * M.FS / FS_TXT).astype(int), 0, n - 1)
    return t[idx] * 1000.0, y[idx], x[idx]      # ms, 输出code, 输入code


def edge_points(t_ms, y, lo_frac=0.1, hi_frac=0.9):
    """方波第一条上升沿：返回（10-90% 过渡点数, 过渡时间ms, 平台Vpp code）。"""
    n = len(y)
    tail = y[n // 2:]
    ylo, yhi = np.percentile(tail, 5), np.percentile(tail, 95)
    vpp = yhi - ylo
    thr_lo = ylo + lo_frac * vpp
    thr_hi = ylo + hi_frac * vpp
    # 找第一个下穿→上穿
    up = np.where((y[:-1] <= thr_lo) & (y[1:] > thr_lo))[0]
    if len(up) == 0:
        return 0, 0.0, vpp
    i0 = up[0] + 1
    j = i0
    while j < n - 1 and y[j] < thr_hi:
        j += 1
    npts = j - i0 + 1
    dt = (t_ms[min(j, n - 1)] - t_ms[i0])
    return npts, dt, vpp


def thd_ratio(t_ms, y, f, fs=FS_TXT):
    """基波与 3 次谐波幅度比（判"方波 vs 正弦"）：3f/f 的幅度比。"""
    def amp(f0):
        x = y - y.mean()
        w = np.hanning(len(x))
        nn = np.arange(len(x))
        return 2.0 * abs(np.sum(x * w * np.exp(-2j * np.pi * f0 * nn / fs))) / np.sum(w)
    return amp(3 * f) / amp(f) if amp(f) > 0 else 0.0, amp(f), amp(3 * f)


# --------------------------------------------------------------- 主流程
def main():
    global M
    ap = argparse.ArgumentParser(description='T93 监护/诊断档位一眼识别')
    ap.add_argument('--mvpp', type=float, default=MVPP, help='注入幅度 mVpp（默认 1.0）')
    ap.add_argument('--no-png', action='store_true', help='不画图')
    args = ap.parse_args()
    mvpp = args.mvpp

    M = _load_model()
    ab = M.read_ab_sos()
    dc_fc = M.read_ab_dc_fc()
    p('=' * 78)
    p('T93 · 监护/诊断档位「一眼识别」   (注入幅度 %.2f mVpp｜notch OFF 为判据口径)' % mvpp)
    p('=' * 78)
    p('A+B 表 %d 节｜dc_block fc = %.4g Hz（两档相同）' % (len(ab), dc_fc))
    p('★ notch OFF 时链上只有 2 个元件：dc_block + 第 15 节 LP2 ⇒ 差别全在高端')
    p('')

    # ---------------- §1 绝对幅频：两档差在哪
    p('─' * 78)
    p('§1 两档绝对幅频响应（整机 = AFE 三级5阶 × dc_block × LP2，NOTCH OFF）')
    p('─' * 78)
    p('  %-9s | %10s | %10s | %9s | %s' % ('f(Hz)', 'mon %', 'diag %', 'diag/mon', '可辨度'))
    p('  ' + '-' * 70)
    FL = [0.05, 0.15, 0.5, 0.67, 1, 2, 5, 10, 20, 25, 30, 40, 45, 50, 60, 75,
          100, 125, 150, 175, 200, 225, 245]
    for f in FL:
        gm = gain(ab, 'mon', f, dc_fc)
        gd = gain(ab, 'diag', f, dc_fc)
        r = gd / gm if gm > 1e-12 else float('inf')
        tag = ('同（判不出）' if r < 1.10 else
               ('★ 可辨' if r < 1.6 else ('★★ 极易辨' if r < 3 else '★★★ 一眼可辨')))
        p('  %-9g | %10.2f | %10.2f | %9.2f× | %s' % (f, gm * 100, gd * 100, r, tag))
    p('')
    g40m, g40d = gain(ab, 'mon', 40, dc_fc), gain(ab, 'diag', 40, dc_fc)
    g150m, g150d = gain(ab, 'mon', 150, dc_fc), gain(ab, 'diag', 150, dc_fc)
    p('  ⇒ 40 Hz : mon %.2f%% vs diag %.2f%% ⇒ **%.2f×**（差 %.1f pp）'
      % (g40m * 100, g40d * 100, g40d / g40m, (g40d - g40m) * 100))
    p('    150 Hz: mon %.2f%% vs diag %.2f%% ⇒ **%.1f×**（差 %.1f pp）'
      % (g150m * 100, g150d * 100, g150d / g150m, (g150d - g150m) * 100))
    p('')
    p('  ⚠⚠ 陷波影响（on/off 折算因子）—— 决定"判档频点"与"要不要先关陷波"：')
    p('     %-8s | %9s | %14s | %8s | %s' % ('f(Hz)', 'on/off', 'notch ON 幅度', '比值', '结论'))
    p('     ' + '-' * 78)
    for f in (40, 75, 100, 125, 150, 175, 200, 225):
        nfm = float(M.notch_factor(ab, 'mon', f, dc_fc))
        nfd = float(M.notch_factor(ab, 'diag', f, dc_fc))
        on_m = gain(ab, 'mon', f, dc_fc) * nfm
        on_d = gain(ab, 'diag', f, dc_fc) * nfd
        r_off = gain(ab, 'diag', f, dc_fc) / gain(ab, 'mon', f, dc_fc)
        use = ('✘ 不可用（幅度→0，两档都看不见）' if min(nfm, nfd) < 0.05
               else '✔ 可用（幅度 ×%.2f，两档同比 ⇒ 比值仍 %.1f×）' % (nfd, r_off))
        p('     %-8g | %9.4f | %6.2f%%/%7.2f%% | %7.1f× | %s' % (f, nfd, on_m * 100, on_d * 100, r_off, use))
    p('')
    p('     ★ 陷波因子对**两档是同一个**（前 15 节相同）⇒ 非零点上"比值"不变，只是绝对幅度缩水；')
    p('       但 **100 / 150 / 200 Hz 恰是工频 2/3/4 次谐波 = 陷波零点** ⇒ notch ON 时两档全归零，')
    p('       **这三个频点判档无效**（想用 150 Hz 必须先 `test notch off`）。')
    p('')

    # ---------------- §2 时域仿真
    p('─' * 78)
    p('§2 时域仿真（%0.2f mVpp 注入｜时间轴 = 上位机 500 Hz 显示域）' % mvpp)
    p('─' * 78)

    p('  [A] 方波 — 看"形状"与"边沿过渡点数"')
    p('      %-7s | %-4s | %9s | %9s | %8s | %s' %
      ('波形', '档', 'Vpp(mVpp)', '过渡点数', '过渡ms', '3次/基波'))
    p('      ' + '-' * 68)
    sq_rows = {}
    for f in (1.0, 5.0, 25.0):
        for mode in ('mon', 'diag'):
            t, y, x = sim(ab, mode, 'square', f, mvpp)
            npts, dt, vpp = edge_points(t, y)
            r3, a1, a3 = thd_ratio(t, y, f)
            sq_rows[(f, mode)] = (t, y, x, vpp, npts, dt, r3)
            p('      %-7s | %-4s | %9.3f | %9d | %8.2f | %8.3f' %
              ('%gHz方波' % f, mode, vpp * M.UV_PER_CODE / 1000.0, npts, dt, r3))
    p('')
    nm, dm = sq_rows[(1.0, 'mon')], sq_rows[(1.0, 'diag')]
    p('      ⇒ 1 Hz 方波：10-90%% 过渡 mon=%d 点(%.1f ms) vs diag=%d 点(%.1f ms) ——'
      % (nm[4], nm[5], dm[4], dm[5]))
    p('        diag 在**数字域就是"单点跳变"**（0 过渡点），mon 被削成 %d 点斜坡。' % nm[4])
    p('        **把时间轴放大到 ~10 ms/格 时最醒目**（不加放大则只占屏幕 4/1000 宽度）。')
    p('      ⇒ 25 Hz 方波：3 次/基波 mon=%.3f vs diag=%.3f（差 %.1f×）⇒ mon 看着像正弦、diag 看着像方波，'
      % (sq_rows[(25.0, 'mon')][6], sq_rows[(25.0, 'diag')][6],
         sq_rows[(25.0, 'diag')][6] / max(sq_rows[(25.0, 'mon')][6], 1e-9)))
    p('        且**不需要放大时间轴**（20 点/周期），且 Vpp 几乎不变 ⇒ 只有"形状"变了。')
    p('')

    p('  [B] 正弦 — 看"幅度"（屏幕上高度）')
    p('      %-7s | %9s | %9s | %6s | %9s | %9s | %s' %
      ('f(Hz)', 'mon解析', 'diag解析', '比值', 'mon屏显', 'diag屏显', '屏幕观感'))
    p('      ' + '-' * 84)
    for f in (10.0, 40.0, 100.0, 150.0):
        th = {'mon': mvpp * gain(ab, 'mon', f, dc_fc), 'diag': mvpp * gain(ab, 'diag', f, dc_fc)}
        row = {}
        for mode in ('mon', 'diag'):
            t, y, x = sim(ab, mode, 'sine', f, mvpp)
            half = y[len(y) // 2:]
            row[mode] = (half.max() - half.min()) * M.UV_PER_CODE / 1000.0
        ratio = th['diag'] / max(th['mon'], 1e-9)
        look = ('★ mon 几乎平线 / diag 明显正弦' if ratio >= 8 else
                ('★ mon 明显更矮' if ratio >= 3 else
                 ('★ 可辨（矮约 1/4）' if ratio >= 1.15 else '需并排对比才可辨')))
        p('      %-7g | %9.3f | %9.3f | %5.1f× | %9.3f | %9.3f | %s'
          % (f, th['mon'], th['diag'], ratio, row['mon'], row['diag'], look))
    p('      （解析＝|H(f)|×注入幅度；屏显＝经 853→500 最近邻抽取后的 Vpp。150 Hz 在 500 域')
    p('        只有 3.33 点/周期 ⇒ 屏显幅度有 ±20% 抖动：**现场看"有没有摆动"，别抠高度**。）')
    p('')

    # ---------------- §3 无源判别
    p('─' * 78)
    p('§3 无信号源时（悬空 / 贴人体）——看"毛刺"与"工频残留"')
    p('─' * 78)
    # 白噪声等效带宽：∫|H|²df
    fgrid = np.linspace(0.05, 400.0, 40001)
    def nbw(mode, upto=400.0):
        g = np.abs(M.h_chain_notch_off(ab, mode, fgrid, dc_fc))
        m = fgrid <= upto
        return float(np.trapezoid(np.abs(g[m]) ** 2, fgrid[m]))
    bm, bd = nbw('mon'), nbw('diag')
    p('  白噪声等效带宽（∫|H|²df，0.05~400 Hz）：mon %.2f Hz vs diag %.2f Hz ⇒ RMS 比 %.3f（%.1f dB）'
      % (bm, bd, np.sqrt(bm / bd), 20 * np.log10(np.sqrt(bm / bd))))
    p('  ⚠ 只有**宽带白噪声占主导**时这条才成立；若现场由 1/f 噪声或工频主导，两档差别会小很多。')
    p('')
    p('  工频残留（50 Hz 及 150 Hz 三次谐波）：')
    for f in (50.0, 150.0):
        gm = gain(ab, 'mon', f, dc_fc)
        gd = gain(ab, 'diag', f, dc_fc)
        p('    %-4g Hz : mon %.1f%%  vs  diag %.1f%%  ⇒ %.2f×' % (f, gm * 100, gd * 100, gd / gm))
    p('  ⇒ 工频干扰（50 Hz 基波 + 150 Hz 三次谐波）在 mon 档被大幅压低 ⇒ 波形明显更干净、更矮。')
    p('    但 notch ON 时 50 / 150 Hz 都落在陷波零点 ⇒ **这条只在 `test notch off` 下成立**。')
    p('')

    # ---------------- §4 结论
    p('=' * 78)
    p('§4 现场速查（判"当前是哪个 mode"）')
    p('=' * 78)
    p('  首选（3 秒出结论）：信号源 **25 Hz 方波 1 mVpp** —— 不需要放大时间轴')
    p('      → 屏幕上"有棱角的方波"        = diag')
    p('      → 屏幕上"圆滑的近似正弦"      = mon')
    p('      ★ 方波型激励**对 notch 不敏感**（25 Hz 的 3/5/7 次谐波 = 75/125/175 Hz 全避开陷波零点）')
    p('  次选（最硬，需要正弦）：1 mVpp 正弦，取 100~200 Hz 之间**避开 100/150/200 Hz** 的点')
    p('      → `test notch off` 时用 **150 Hz**：mon 5.7% vs diag 75.6%（**13.3×**）')
    p('      → 不关 notch  时用 **175 Hz**：mon 3.1% vs diag 60.3%（**19.2×**）')
    p('      → 观感：mon 几乎一条平线 ／ diag 明显摆动')
    p('  再选（现成波形不换）：40 Hz 正弦 —— mon 矮 21 pp（约 1/4 高度，1.27×），需并排对比')
    p('  ⚠ 只出低频（≤10 Hz）或只出 DC/阶跃 **判不出来** —— 两档在低端逐位相同。')
    p('  ⚠ 最省事的其实是命令行：`test mode` 直接回显 `mode:diag|mon`（不依赖信号源、不受 notch 影响）。')
    p('')

    io.open(OUT_TXT, 'w', encoding='utf-8', newline='\n').write('\n'.join(_buf) + '\n')
    p('[文本报告] %s' % OUT_TXT)

    if not args.no_png:
        make_png(ab, dc_fc, mvpp)
    return 0


# --------------------------------------------------------------- 绘图
def make_png(ab, dc_fc, mvpp):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
    except Exception as e:                                   # pragma: no cover
        p('[跳过绘图] %s' % e)
        return

    C_MON, C_DIA = '#d62728', '#1f77b4'      # mon=红 diag=蓝（与项目配色一致）
    fig, ax = plt.subplots(3, 2, figsize=(13.0, 11.0))
    fig.suptitle('T93 监护 / 诊断 档位识别 · %.2f mVpp 注入 · NOTCH OFF 口径 · 上位机 500 Hz 显示域' % mvpp,
                 fontsize=13, fontweight='bold')

    # (0,0) 幅频响应
    fg = np.logspace(np.log10(0.05), np.log10(250), 900)
    for mode, c, lb in (('mon', C_MON, 'mon (LP2@45)'), ('diag', C_DIA, 'diag (LP2@250)')):
        g = np.abs(M.h_chain_notch_off(ab, mode, fg, dc_fc)) * M.afe_gain(fg)
        ax[0, 0].semilogx(fg, 20 * np.log10(np.maximum(g, 1e-6)), color=c, lw=2, label=lb)
    ax[0, 0].axvline(40, color='#888', ls=':', lw=1)
    ax[0, 0].axvline(150, color='#888', ls=':', lw=1)
    ax[0, 0].text(40, -46, ' 40 Hz\n 差21pp', fontsize=8, color='#555')
    ax[0, 0].text(150, -46, ' 150 Hz\n 13×', fontsize=8, color='#555')
    ax[0, 0].set_xlabel('Hz'); ax[0, 0].set_ylabel('增益 (dB)')
    ax[0, 0].set_title('① 整机幅频：低端两档重合，差别全在高端')
    ax[0, 0].grid(alpha=.3, which='both'); ax[0, 0].legend(fontsize=9)
    ax[0, 0].set_ylim(-50, 6)

    # (0,1) 比值
    gm = np.abs(M.h_chain_notch_off(ab, 'mon', fg, dc_fc)) * M.afe_gain(fg)
    gd = np.abs(M.h_chain_notch_off(ab, 'diag', fg, dc_fc)) * M.afe_gain(fg)
    ax[0, 1].semilogx(fg, gd / np.maximum(gm, 1e-9), color='#2ca02c', lw=2)
    ax[0, 1].axhline(1.0, color='#888', ls='--', lw=1)
    for f, txt in ((40, '40 Hz: %.2f×' % (float(np.interp(40, fg, gd / np.maximum(gm, 1e-9))))),
                   (150, '150 Hz: %.1f×' % (float(np.interp(150, fg, gd / np.maximum(gm, 1e-9)))))):
        r = float(np.interp(f, fg, gd / np.maximum(gm, 1e-9)))
        ax[0, 1].plot([f], [r], 'o', color='#2ca02c')
        ax[0, 1].annotate(txt, (f, r), textcoords='offset points', xytext=(6, 8), fontsize=9)
    ax[0, 1].set_xlabel('Hz'); ax[0, 1].set_ylabel('diag / mon')
    ax[0, 1].set_title('② 两档幅度比：<1.05× 判不出，>8× 一眼可辨')
    ax[0, 1].grid(alpha=.3, which='both')

    # (1,0) 25 Hz 方波
    for mode, c, lb in (('mon', C_MON, 'mon'), ('diag', C_DIA, 'diag')):
        t, y, x = sim(ab, mode, 'square', 25.0, mvpp, cycles=3)
        ax[1, 0].plot(t - t[0], (y - y.mean()) * M.UV_PER_CODE / 1000.0, color=c, lw=1.8, label=lb)
    ax[1, 0].plot(t - t[0], (x - x.mean()) * M.UV_PER_CODE / 1000.0, color='#999', lw=0.9,
                  ls='--', label='输入 25 Hz 方波')
    ax[1, 0].set_xlabel('ms'); ax[1, 0].set_ylabel('mV')
    ax[1, 0].set_title('③ 25 Hz 方波：diag=方波 ／ mon≈正弦  （不用放大时间轴）')
    ax[1, 0].grid(alpha=.3); ax[1, 0].legend(fontsize=9)

    # (1,1) 1 Hz 方波边沿放大
    t, ym, xm = sim(ab, 'mon', 'square', 1.0, mvpp, cycles=2)
    t2, yd, xd = sim(ab, 'diag', 'square', 1.0, mvpp, cycles=2)
    for tt, yy, c, lb in ((t, ym, C_MON, 'mon'), (t2, yd, C_DIA, 'diag')):
        thr = yy[len(yy) // 2:].max()
        i_cross = np.argmax(yy > thr * 0.5)
        sl = slice(max(0, i_cross - 12), min(len(yy), i_cross + 40))
        ax[1, 1].plot(tt[sl] - tt[i_cross], yy[sl] * M.UV_PER_CODE / 1000.0, color=c, lw=1.8,
                      marker='o', ms=3, label=lb)
    ax[1, 1].set_xlabel('ms（相对边沿）'); ax[1, 1].set_ylabel('mV')
    ax[1, 1].set_title('④ 1 Hz 方波边沿放大：mon 5 点斜坡 ／ diag 近乎直跳')
    ax[1, 1].grid(alpha=.3); ax[1, 1].legend(fontsize=9)

    # (2,0) 150 Hz 正弦
    for mode, c, lb in (('mon', C_MON, 'mon'), ('diag', C_DIA, 'diag')):
        t, y, x = sim(ab, mode, 'sine', 150.0, mvpp, cycles=8)
        ax[2, 0].plot(t - t[0], (y - y.mean()) * M.UV_PER_CODE / 1000.0, color=c, lw=1.6,
                      marker='o', ms=2.5, label=lb)
    ax[2, 0].set_xlabel('ms'); ax[2, 0].set_ylabel('mV')
    ax[2, 0].set_title('⑤ 150 Hz 正弦：diag 明显摆动 ／ mon 几乎平线（13×）')
    ax[2, 0].grid(alpha=.3); ax[2, 0].legend(fontsize=9)

    # (2,1) 40 Hz 正弦
    for mode, c, lb in (('mon', C_MON, 'mon'), ('diag', C_DIA, 'diag')):
        t, y, x = sim(ab, mode, 'sine', 40.0, mvpp, cycles=3)
        ax[2, 1].plot(t - t[0], (y - y.mean()) * M.UV_PER_CODE / 1000.0, color=c, lw=1.6, label=lb)
    ax[2, 1].set_xlabel('ms'); ax[2, 1].set_ylabel('mV')
    ax[2, 1].set_title('⑥ 40 Hz 正弦：mon 矮 21 pp（约 1/4 高度）')
    ax[2, 1].grid(alpha=.3); ax[2, 1].legend(fontsize=9)

    fig.tight_layout(rect=(0, 0, 1, 0.975))
    fig.savefig(OUT_PNG, dpi=115)
    plt.close(fig)
    p('[对比图] %s' % OUT_PNG)


if __name__ == '__main__':
    sys.exit(main())
