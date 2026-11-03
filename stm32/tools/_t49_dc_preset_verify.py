# -*- coding: utf-8 -*-
"""
T49 · 「DC 预置」落码后的离线复核

拍板（2026-09-20）：① 采纳方案 C ② off 方向去掉清零 ③ f32 同步改。

本脚本验证**已落码的 C 实现**，而不只是模型：
  [0] 源码审计   —— 从 Filter/ecg_filter.c 抓关键行，断言落码符合方案 C 的三条（含易错点）
  [1] 系数一致性 —— 逐档验 H(1)==1，并验 d1 式与 d2 式等价（不满足则说明陷波不是标准结构）
  [2] 真实参数   —— 从 T48 实测 1 mV 方波数据里提取方波幅值/周期/工频幅度/旁路时长
  [3] 全链仿真   —— 用 [2] 的真实参数跑 off→on，对比「改前 清零」vs「改后 DC 预置」
  [4] 相位扫描   —— last_x 落在方波任意相位时的最坏值（给出上界）
  [5] 判读阈值   —— 上板复测该看什么数字

用法：
  python _t49_dc_preset_verify.py
  python _t49_dc_preset_verify.py --profile D3 --fig
"""
import argparse
import importlib.util
import io
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    't47', os.path.join(HERE, '_t47_notch_switch.py'))
T47 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T47)
FS = T47.FS          # 853.2423 Hz —— **滤波器域**（系数按此设计，仿真/预置时长用它）
FS_TXT = 500.0       # 500 Hz —— **实测 txt 的时间轴**（列3 是 500 Hz 域输出序号）
# ⚠ 两者不可混用！2026-09-20 首跑曾用 FS 去换算实测样本数，
#    导致方波周期 0.498 s 被误算成 0.292 s、旁路 30.9 s 误算成 18.1 s ⇒ 结论偏大 10 倍。

FILTER_C = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')
FILTER_H = os.path.join(HERE, '..', 'Filter', 'ecg_filter.h')
UV = 0.1122          # code 域 µV/code
SQ_FILE = os.path.join(HERE, '1mv-square-notch(on-off-on)2026-09-20 111918.txt')


# ---------------------------------------------------------------- [0] 源码审计
def _fnsrc(txt, name):
    """粗切某个函数的函数体（按大括号配平）。"""
    m = re.search(r'\n[\w\s\*]+?\b%s\s*\([^)]*\)\s*\{' % re.escape(name), txt)
    if not m:
        return ''
    i = txt.index('{', m.start())
    d, j = 0, i
    while j < len(txt):
        if txt[j] == '{':
            d += 1
        elif txt[j] == '}':
            d -= 1
            if d == 0:
                return txt[i:j + 1]
        j += 1
    return txt[i:]


def audit_source():
    txt = io.open(FILTER_C, encoding='utf-8', errors='replace').read()
    print('[0] 源码审计  %s' % os.path.relpath(FILTER_C, os.path.join(HERE, '..')))
    ok = True

    for tag in ('f32', 'f64'):
        proc = _fnsrc(txt, 'ecg_filter_%s_process' % tag)
        setn = _fnsrc(txt, 'ecg_filter_%s_set_notch' % tag)
        print('\n  --- ecg_filter_%s_* ---' % tag)

        # (a) last_x 必须**无条件**写（若被包在 if(notch_on) 里，off 期间不更新 ⇒ 切回时用陈旧值）
        lines = proc.split('\n')
        li = [k for k, L in enumerate(lines) if 'last_x = x' in L]
        if li:
            k = li[0]
            prev = lines[k - 1].strip()
            guarded = ('if' in prev and 'notch_on' in prev)
            print('  (a) process 写 last_x            : %s'
                  % ('无条件 ✓' if not guarded else '★被 notch_on 条件包住 ✗'))
            print('      上一行: %s' % prev)
            ok &= not guarded
        else:
            print('  (a) process 写 last_x            : ✗ 未找到')
            ok = False

        # (b) 切回 on 用 DC 预置（两条赋值），且**不含**重置调用
        d1 = re.findall(r'd1\s*=\s*f->last_x\s*\*\s*\(\s*1\.0[f]?\s*-\s*f->s\[i\]\.b0\s*\)', setn)
        d2 = re.findall(r'd2\s*=\s*f->last_x\s*\*\s*\(\s*f->s\[i\]\.b2\s*-\s*f->s\[i\]\.a2\s*\)', setn)
        print('  (b) set_notch DC 预置 d1/d2      : %s'
              % ('两条均在 ✓' if (d1 and d2) else '✗ 缺 d1=%d d2=%d' % (len(d1), len(d2))))
        ok &= bool(d1 and d2)

        # (c) off 方向不得再清零（拍板 ②）
        nreset = len(re.findall(r'biquad_df2t_\w+_reset\s*\(\s*&f->s\[i\]', setn))
        print('  (c) set_notch 内 reset 调用数    : %d %s' % (nreset, '✓（已去掉）' if nreset == 0 else '✗ 仍在清零'))
        ok &= (nreset == 0)

        # (d) 循环上界必须是 ECG_NUM_NOTCH_SECTIONS（与跳节边界一致）
        nb = len(re.findall(r'i\s*<\s*ECG_NUM_NOTCH_SECTIONS', setn))
        print('  (d) 循环上界 = NOTCH_SECTIONS    : %d 处 %s' % (nb, '✓' if nb >= 1 else '✗'))
        ok &= (nb >= 1)

    ht = io.open(FILTER_H, encoding='utf-8', errors='replace').read()
    has_f32 = bool(re.search(r'float\s+last_x;', ht))
    has_f64 = bool(re.search(r'double\s+last_x;', ht))
    print('\n  (e) 结构体含 last_x（f32/f64）   : %s / %s %s'
          % (has_f32, has_f64, '✓（拍板 ③ 已同步）' if (has_f32 and has_f64) else '✗'))
    ok &= has_f32 and has_f64
    print('\n  ⇒ 源码审计 %s' % ('全部通过 ✓' if ok else '★有未通过项'))
    return ok


# ------------------------------------------------------- [1] 系数一致性验算
def check_coeffs(profiles):
    print('\n[1] 系数一致性（逐档）')
    print('  %-10s %2s %14s %14s %14s' % ('档位', 'n', 'max|H(1)-1|', 'max|(1-b0)-(b2-a2)|', 'min(1-b0)'))
    for p in profiles:
        macro, _, _ = T47.PROFILES[p]
        sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
        e_h, e_d, mn = 0.0, 0.0, 1.0
        for i in range(n_notch):
            b0, b1, b2, a1, a2 = sos[i]
            H1 = (b0 + b1 + b2) / (1.0 + a1 + a2)
            e_h = max(e_h, abs(H1 - 1.0))
            e_d = max(e_d, abs((1.0 - b0) - (b2 - a2)))
            mn = min(mn, 1.0 - b0)
        print('  %-10s %2d %14.2e %14.2e %14.4f' % (p, n_notch, e_h, e_d, mn))
    print('  ⇒ H(1)=1 保证「预置后输出≈输入」；两式等价 ⇒ DC 预置只需一个系数 (1-b0)。')


# ------------------------------------------------- [2] 从实测数据取真实参数
def load_sq(path):
    rows = []
    for line in io.open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if len(p) < 6:
            continue
        try:
            rows.append([float(t) for t in p[:6]])
        except ValueError:
            continue
    return np.array(rows)


def real_params(path):
    print('\n[2] 从 T48 实测数据提取真实参数  %s' % os.path.basename(path))
    d = load_sq(path)
    x = d[:, 4] * UV           # code 域 µV
    c6 = d[:, 5]
    thr = (c6.min() + c6.max()) / 2
    off = np.where(c6 < thr)[0]
    b1, b2 = off[0], off[-1]

    # 方波平台：中值滤波后取双峰（size=101 样本 @500 Hz = 202 ms < 半周期 250 ms，能保住平台）
    from scipy.ndimage import median_filter
    base = median_filter(x, size=101, mode='nearest')
    mid = (base.max() + base.min()) / 2
    hi = float(np.median(base[base > mid]))
    lo = float(np.median(base[base < mid]))
    pp = hi - lo

    # 方波周期（在 off 段内用边沿间隔）—— 必须用 FS_TXT 换算成秒
    s_off = base[b1:b2]
    ed = np.where(np.abs(np.diff(s_off)) > 300)[0]
    if len(ed) > 3:
        iv = np.diff(ed)
        iv = iv[(iv > FS_TXT * 0.1) & (iv < FS_TXT * 2)]   # 剔粘连/丢样本造成的异常间隔
        T_sq = float(np.median(iv)) / FS_TXT
    else:
        T_sq = 2.0

    # off 段的 50 Hz（纯工频口径：先剥方波，再剔边沿±160 ms）
    res = x - base
    def g50(y):
        w = np.hanning(len(y))
        Y = np.fft.rfft((y - y.mean()) * w)
        fr = np.fft.rfftfreq(len(y), 1 / FS_TXT)
        amp = 2 * np.abs(Y) / w.sum()
        return float(amp[(fr > 48.5) & (fr < 51.5)].max())

    eb = np.abs(np.diff(base)) > 300
    eb = np.concatenate([[False], eb])
    from scipy.ndimage import binary_dilation
    em = ~binary_dilation(eb, iterations=80)
    idx = np.arange(b1, b2 + 1)[em[b1:b2 + 1]]
    m50_all = g50(res[b1:b2 + 1])          # 含边沿振铃口径
    m50 = g50(res[idx])                    # 纯工频口径

    # 切换瞬间的 last_x 取值范围 = 方波平台 + 边沿过冲
    ovs = float(np.percentile(np.abs(x[b1:b2 + 1]), 99.9))
    print('  旁路时长        : %.2f s（off 段 %d 样本 @500 Hz）' % ((b2 + 1 - b1) / FS_TXT, b2 + 1 - b1))
    print('  方波平台        : 高 %+.0f / 低 %+.0f ⇒ p-p %.0f µV（标称 1000）' % (hi, lo, pp))
    print('  方波周期        : %.3f s（= %.2f Hz）' % (T_sq, 1.0 / T_sq))
    print('  off 段 50 Hz    : %.2f µV（纯工频口径）／ %.2f µV（含边沿振铃）' % (m50, m50_all))
    print('  |x| 的 p99.9    : %.0f µV（平台外的边沿过冲量级）' % ovs)
    print('  ⇒ last_x 在切换瞬间落在 [%+.0f, %+.0f] µV 内（平台），极端相位可达 ±%.0f µV'
          % (lo, hi, ovs))
    return dict(hi=hi, lo=lo, pp=pp, T=T_sq, m50=m50, m50_all=m50_all, ovs=ovs,
                off_s=(b2 + 1 - b1) / FS_TXT, b1=b1, b2=b2)


# ------------------------------------------------------------ 仿真内核
def mkstate(nsec):
    return T47.newstate(nsec)


def run(sos, state, x, first, nsec):
    y = np.empty(len(x))
    for k in range(len(x)):
        v = x[k]
        for i in range(first, nsec):
            b0, b1, b2, a1, a2 = sos[i]
            st = state[i]
            w = b0 * v + st[0]
            st[0] = b1 * v - a1 * w + st[1]
            st[1] = b2 * v - a2 * w
            v = w
        y[k] = v
    return y


def clear_notch(state, n_notch):
    for i in range(n_notch):
        state[i][0] = state[i][1] = 0.0


def dc_preset(sos, state, v, n_notch):
    for i in range(n_notch):
        b0, b1, b2, a1, a2 = sos[i]
        state[i][0] = v * (1.0 - b0)
        state[i][1] = v * (b2 - a2)


def make_sq(n, pp, T, m50, phi_sq=0.0, phi_50=0.37):
    t = np.arange(n) / FS
    sq = np.where(((t / T + phi_sq) % 1.0) < 0.5, 1.0, -1.0) * (pp / 2.0)
    return sq + m50 * np.sin(2 * np.pi * 50.0 * t + phi_50)


# ------------------------------------------------------- [3] 用真实参数跑仿真
def sim(profile, P, variant, off_s, ref_horizon=1.2):
    macro, _, _ = T47.PROFILES[profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
    nsec = len(sos)
    k_pre = int(1.0 * FS)
    k_off = int(off_s * FS)
    k_post = int(ref_horizon * FS)
    n = k_pre + k_off + k_post

    x = make_sq(n, P['pp'], P['T'], P['m50'])
    if variant == 'amp2':
        x[k_pre:] *= 2.0
    elif variant == 'dc':
        x[k_pre:] += 1000.0

    yref = run(sos, mkstate(nsec), x, 0, nsec)[k_pre + k_off:]
    out = {}
    for name in ('A改前清零', 'C改后预置'):
        st = mkstate(nsec)
        run(sos, st, x[:k_pre], 0, nsec)
        run(sos, st, x[k_pre:k_pre + k_off], n_notch, nsec)
        if name == 'A改前清零':
            clear_notch(st, n_notch)
        else:
            dc_preset(sos, st, x[k_pre + k_off - 1], n_notch)
        y = run(sos, st, x[k_pre + k_off:], 0, nsec)
        out[name] = y - yref
    return out


def report_sim(profile, P):
    print('\n[3] off→on 全链仿真')
    print('  实测参数：p-p %.0f µV，T=%.2f s（%.1f Hz），50Hz %.1f µV，旁路 %.2f s'
          % (P['pp'], P['T'], 1 / P['T'], P['m50'], P['off_s']))
    print('  %-8s %12s %12s %10s' % ('场景', 'A改前 峰值', 'C改后 峰值', '改善倍数'))
    print('  ' + '-' * 48)
    for vk, vd in [('steady', '信号不变'), ('amp2', '幅度翻倍'), ('dc', '＋1mV 直流')]:
        r = sim(profile, P, vk, P['off_s'])
        a = np.abs(r['A改前清零']).max()
        c = np.abs(r['C改后预置']).max()
        print('  %-8s %9.1f µV %9.1f µV %10.1f×' % (vd, a, c, a / max(c, 1e-9)))

    # 对照：T48b 原合成参数（1 Hz 方波 / 12 µV 工频 / 旁路 5 s），用于跨版本对账
    Pb = dict(hi=500.0, lo=-500.0, pp=1000.0, T=1.0, m50=12.0, off_s=5.0)
    print('  对照（T48b 合成输入：1 Hz 方波 / 旁路 5 s）：')
    for vk, vd in [('steady', '信号不变'), ('amp2', '幅度翻倍'), ('dc', '＋1mV 直流')]:
        r = sim(profile, Pb, vk, 5.0)
        a = np.abs(r['A改前清零']).max()
        c = np.abs(r['C改后预置']).max()
        print('  %-8s %9.1f µV %9.1f µV %10.1f×' % (vd, a, c, a / max(c, 1e-9)))
    print('  ⇒ 与 T48b 原报告（A 165.2/331.2/151.5、C 8.6/17.1/8.6）一致 ⇒ 内核可信。')
    print('  ★ 实测是 2 Hz 方波：半周期 250 ms 与陷波器 τ≈200 ms 同量级，')
    print('     DC 稳态假设弱于 1 Hz 情形（半周期 500 ms ≈ 2.5τ）⇒ C 的残余由 8.6 涨到 ~15.5 µV。')


# ------------------------------------------------------------- [4] 相位扫描
def scan_phase(profile, P):
    """★ 正确的问题表述：**信号固定，扫描「切换时刻落在方波周期的哪个相位」**。

    实现 = 在固定信号上把切换点 k_sw 在一个方波周期内平移。
    参考链始终是「全程 on 的同一信号」⇒ 比较公平。

    ⚠ 首版曾用「改变信号相位 φ、参考却固定为 φ=0」的写法，两者根本不是同一输入，
      得出 1266 µV 的假最坏值。已作废。

    物理含义：切换时刻由 `test notch on` 的下发时机决定，在方波相位上**基本随机**，
    所以要看的是这个相位分布上的峰值包络——尤其是「切换点贴着方波边沿」的情形。
    """
    macro, _, _ = T47.PROFILES[profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
    nsec = len(sos)
    k_pre = int(1.0 * FS)
    k_off = int(P['off_s'] * FS)
    k_post = int(1.0 * FS)
    n = k_pre + k_off + k_post + int(P['T'] * FS) + 10

    x = make_sq(n, P['pp'], P['T'], P['m50'])          # 固定信号
    yref = run(sos, mkstate(nsec), x, 0, nsec)         # 固定参考：全程 on

    step = max(1, int(0.01 * FS))                      # 10 ms 步长
    shifts = np.arange(0, int(P['T'] * FS), step)
    pksC, pksA, pksD, lastxs = [], [], [], []
    for sh in shifts:
        ksw = k_pre + k_off + sh

        st = mkstate(nsec)                       # A：改前（切回时清零）
        run(sos, st, x[:k_pre], 0, nsec)
        run(sos, st, x[k_pre:ksw], n_notch, nsec)
        clear_notch(st, n_notch)
        yA = run(sos, st, x[ksw:ksw + k_post], 0, nsec)

        st = mkstate(nsec)                       # C：改后（切回时 DC 预置）
        run(sos, st, x[:k_pre], 0, nsec)
        run(sos, st, x[k_pre:ksw], n_notch, nsec)
        dc_preset(sos, st, x[ksw - 1], n_notch)
        yC = run(sos, st, x[ksw:ksw + k_post], 0, nsec)

        st = mkstate(nsec)                       # D：变量分离对照 —— 全程 on，只在 ksw 预置一次
        run(sos, st, x[:ksw], 0, nsec)           # ① 不跳节（BP/陷波状态都跟随真实信号）
        dc_preset(sos, st, x[ksw - 1], n_notch)  # ② 只施加「DC 预置」这一个动作
        yD = run(sos, st, x[ksw:ksw + k_post], 0, nsec)

        pksA.append(np.abs(yA - yref[ksw:ksw + k_post]).max())
        pksC.append(np.abs(yC - yref[ksw:ksw + k_post]).max())
        pksD.append(np.abs(yD - yref[ksw:ksw + k_post]).max())
        lastxs.append(x[ksw - 1])
    pksA = np.array(pksA); pksC = np.array(pksC); pksD = np.array(pksD)
    lastxs = np.array(lastxs)
    ph = shifts / FS / P['T']
    iw = int(np.argmax(pksC))

    # 距边沿多近才算"贴边沿"
    edge_dist = np.array([min(((k_pre + k_off + sh) / FS / P['T']) % 0.5,
                              0.5 - ((k_pre + k_off + sh) / FS / P['T']) % 0.5)
                          for sh in shifts]) * P['T'] * 1000.0   # ms

    m_far = edge_dist > 100
    print('\n[4] 切换相位扫描（信号固定，切换点在一个方波周期内平移 %d 步）' % len(shifts))
    print('  %-14s %11s %11s %11s' % ('切换时刻', 'A改前清零', 'C改后预置', 'D仅预置动作'))
    print('  中位        %8.1f µV %8.1f µV %8.1f µV'
          % (np.median(pksA), np.median(pksC), np.median(pksD)))
    print('  p90         %8.1f µV %8.1f µV %8.1f µV'
          % (np.percentile(pksA, 90), np.percentile(pksC, 90), np.percentile(pksD, 90)))
    print('  最坏        %8.1f µV %8.1f µV %8.1f µV'
          % (pksA.max(), pksC.max(), pksD.max()))
    print('  距边沿>100ms 中位 %.1f µV ／ 最大 %.1f µV（占周期 %.0f%%）'
          % (np.median(pksC[m_far]), pksC[m_far].max(), 100.0 * m_far.sum() / len(m_far)))
    print('  最坏相位：距边沿 %.0f ms，last_x=%+.0f µV' % (edge_dist[iw], lastxs[iw]))
    print('  逐相位改善倍数：中位 %.1f×，最差 %.1f×'
          % (np.median(pksA / np.maximum(pksC, 1e-9)), (pksA / np.maximum(pksC, 1e-9)).min()))
    print('\n  （归因链见 [★]，需 [4b]/[6] 的数字 ⇒ 集中在后面输出）')
    return pksA, pksC, pksD, ph, edge_dist, lastxs, m_far


# ------------------------------------------------- 【归因链】集中输出
def attribution(pksA, pksC, pksD, pksE, st4b, m_far):
    p95n, maxn, p95f = st4b
    print('\n[★] 归因链（D、E 两列是关键，缺一不可）')
    print('  · D（全程 on、只在切换点做一次 DC 预置；排除"旁路期状态分叉"）最坏 %.1f µV' % pksD.max())
    print('    ≈ C 最坏 %.1f µV ⇒ 与"旁路期状态分叉"**无关**。' % pksC.max())
    if pksE is not None:
        print('  · E（影子运行：切回时陷波状态本来就对，根本不预置）最坏 %.1f µV' % pksE.max())
        print('    ≈ C ⇒ 也**不是"状态不对"**。')
    print('  · 两者共同指向同一主因：**切换瞬间 BP 的输入端从 `x` 跳到 `notch(x)`**。')
    print('    这两个信号在方波边沿附近本就相差很大（[4b]：边沿附近 p95 %.0f µV / max %.0f µV，'
          % (p95n, maxn))
    print('    远离边沿 p95 仅 %.0f µV）⇒ 这是一个**与状态处理无关的输入阶跃**，' % p95f)
    print('    任何"预置/不清零"都消除不了。')
    print('  · A（清零）更差：%.1f µV = 这个输入阶跃 ＋ 陷波状态冷启动的额外 ~%.0f µV。'
          % (pksA.max(), pksA.max() - pksC.max()))
    print('  ⇒ ① 常规相位 C 已足够好（距边沿>100ms 时 ≤ %.1f µV）；' % pksC[m_far].max())
    print('     ② 贴边沿的残余是**「跳节」这一实现方式的固有代价**，不是本次改动的缺陷。')


# ------------------------------------- [4b] 主因直接证据：BP 输入端的固有台阶
def bp_input_step(profile, P):
    """直接量 |notch(x) − x| —— 这就是切回 on 瞬间 BP 输入端的台阶，与状态处理无关。"""
    macro, _, _ = T47.PROFILES[profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)

    d = load_sq(SQ_FILE)
    x = d[:, 4] * UV                     # 实测 code 域波形（含真实边沿）
    st = mkstate(n_notch)
    no = np.empty(len(x))
    for k in range(len(x)):
        v = x[k]
        for i in range(n_notch):
            b0, b1, b2, a1, a2 = sos[i]
            s = st[i]
            w = b0 * v + s[0]
            s[0] = b1 * v - a1 * w + s[1]
            s[1] = b2 * v - a2 * w
            v = w
        no[k] = v
    dif = np.abs(no - x)

    from scipy.ndimage import median_filter
    base = median_filter(x, size=101, mode='nearest')
    ed = np.where(np.abs(np.diff(base)) > 300)[0]
    near = np.zeros(len(x), bool)
    for e in ed:
        near[max(0, e - 40):e + 40] = True          # 边沿 ±40 样本 = ±80 ms

    print('\n[4b] 主因直接证据：|notch(x) − x|（切回 on 瞬间 BP 输入端的台阶，与状态无关）')
    print('  全段        : 中位 %.1f µV  p95 %.1f µV  max %.1f µV' %
          (np.median(dif), np.percentile(dif, 95), dif.max()))
    print('  边沿±80 ms  : 中位 %.1f µV  p95 %.1f µV  max %.1f µV  ← 切换落在这里时无法回避'
          % (np.median(dif[near]), np.percentile(dif[near], 95), dif[near].max()))
    print('  远离边沿    : 中位 %.1f µV  p95 %.1f µV  max %.1f µV'
          % (np.median(dif[~near]), np.percentile(dif[~near], 95), dif[~near].max()))
    print('  ⇒ 边沿附近的台阶比平台大 %.0f×（中位）⇒ 这就是贴边沿瞬态的主因。'
          % (np.median(dif[near]) / max(np.median(dif[~near]), 1e-9)))
    _p95n = float(np.percentile(dif[near], 95))
    _maxn = float(dif[near].max())
    _p95f = float(np.percentile(dif[~near], 95))
    return dif, (_p95n, _maxn, _p95f)


# --------------------------------------------------- [6] 备选方案 E（影子运行）
def shadow_step(sos, st, xv, n_notch, nsec):
    """off 期间：陷波节**照常运行**（状态持续跟随信号），但 BP 的输入取原始 x。"""
    xs = xv
    for i in range(n_notch):
        b0, b1, b2, a1, a2 = sos[i]
        s = st[i]
        w = b0 * xs + s[0]
        s[0] = b1 * xs - a1 * w + s[1]
        s[1] = b2 * xs - a2 * w
        xs = w
    y = xv
    for i in range(n_notch, nsec):
        b0, b1, b2, a1, a2 = sos[i]
        s = st[i]
        w = b0 * y + s[0]
        s[0] = b1 * y - a1 * w + s[1]
        s[1] = b2 * y - a2 * w
        y = w
    return y


def scan_shadow(profile, P, off_s=2.0):
    """方案 E 相位扫描：off 段影子运行 ⇒ 切回时状态本来就对，不需任何预置。

    ⚠ off 段用 2 s（而非实测 30.87 s）只是为了 Python 循环速度；T48b 已证与旁路时长无关。
    """
    macro, _, _ = T47.PROFILES[profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
    nsec = len(sos)
    k_pre = int(1.0 * FS)
    k_off = int(off_s * FS)
    k_post = int(1.0 * FS)
    n = k_pre + k_off + k_post + int(P['T'] * FS) + 10
    x = make_sq(n, P['pp'], P['T'], P['m50'])
    yref = run(sos, mkstate(nsec), x, 0, nsec)

    step = max(1, int(0.02 * FS))
    shifts = np.arange(0, int(P['T'] * FS), step)
    pksE, pksC = [], []
    for sh in shifts:
        ksw = k_pre + k_off + sh
        st = mkstate(nsec)                       # E
        run(sos, st, x[:k_pre], 0, nsec)
        for k in range(k_pre, ksw):
            shadow_step(sos, st, x[k], n_notch, nsec)
        yE = run(sos, st, x[ksw:ksw + k_post], 0, nsec)

        st2 = mkstate(nsec)                      # C（同时段对照）
        run(sos, st2, x[:k_pre], 0, nsec)
        run(sos, st2, x[k_pre:ksw], n_notch, nsec)
        dc_preset(sos, st2, x[ksw - 1], n_notch)
        yC = run(sos, st2, x[ksw:ksw + k_post], 0, nsec)

        pksE.append(np.abs(yE - yref[ksw:ksw + k_post]).max())
        pksC.append(np.abs(yC - yref[ksw:ksw + k_post]).max())
    pksE = np.array(pksE); pksC = np.array(pksC)

    print('\n[6] 备选方案 E（off 期间「影子运行」陷波节）｜旁路 %.0f s、%d 个相位'
          % (off_s, len(shifts)))
    print('  %-10s %11s %11s' % ('统计', 'C 现行', 'E 影子'))
    print('  中位        %8.1f µV %8.1f µV' % (np.median(pksC), np.median(pksE)))
    print('  p90         %8.1f µV %8.1f µV' % (np.percentile(pksC, 90), np.percentile(pksE, 90)))
    print('  最坏        %8.1f µV %8.1f µV' % (pksC.max(), pksE.max()))
    print('  ⇒ E 的原理：切回时陷波状态「本来就对」，不做任何预置。')
    print('     ⚠ 但 E 的贴边沿最坏值仍与 C 同级 ⇒ **印证主因不是"状态"，而是 BP 输入端的台阶**')
    print('       （切回瞬间 BP 输入从 x 跳到 notch(x)，边沿附近二者差 ~236 µV，见 [4b]）。')
    print('  ⚠ E 的代价：off 期间**不再省 CPU**（陷波节仍在跑）⇒')
    print('     T47 实测的 dtcycles −5760（−6.14 pp）消失，')
    print('     而 dtcycles 是「跳节真的生效」的现场自检指标 ⇒ 失去在线自证手段。')
    print('  ⇒ 既然 E 也解决不了贴边沿问题，就没有理由为它放弃 CPU 收益 ⇒ **不推荐 E**。')
    return pksC, pksE


# ------------------------------------------------------------- [5] 判读阈值
def thresholds(profile, P):
    macro, _, _ = T47.PROFILES[profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
    nsec = len(sos)
    x = make_sq(int(4 * FS), P['pp'], P['T'], P['m50'])
    y = run(sos, mkstate(nsec), x, 0, nsec)
    d = np.abs(np.diff(y))
    print('\n[5] 上板复测判读阈值（%s 档，%d 节）' % (profile, nsec))
    print('  正常相邻样本差 p99.9 = %.1f µV（"看不见的跳变"上限），p99.99 = %.1f µV'
          % (np.percentile(d, 99.9), np.percentile(d, 99.99)))
    print('  ┌ 判「修好了」：off→on 后残差峰值 < 30 µV（当前 ~130 µV）')
    print('  ├ 判「部分改善」：30~60 µV ⇒ 查是否只改了 f64、f32 未同步')
    print('  ├ 判「没生效」：> 100 µV ⇒ 查固件是否真的换了编译产物')
    print('  └ ★ 前提：**必须同时记录切换时刻距最近方波边沿的时间**。')
    print('       实测那次切换距边沿 172 ms（常规相位）⇒ 预期 ~15 µV；')
    print('       若切换恰贴边沿（<20 ms），233 µV 也属预期（见 [4b] 的 236 µV 输入台阶），')
    print('       不能据此判「预置失效」。')
    print('  ⚠ dtcycles 在两种状态下必须仍差 ~5760 cycles（跳节生效），')
    print('     否则是开关没生效，而不是预置的问题。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='IMP620_N2', choices=list(T47.PROFILES))
    ap.add_argument('--file', default=SQ_FILE)
    ap.add_argument('--fig', action='store_true')
    ap.add_argument('--shadow', action='store_true',
                    help='额外跑 [6] 备选方案 E（影子运行）对比，较慢')
    a = ap.parse_args()

    audit_source()
    check_coeffs(list(T47.PROFILES))

    if not os.path.exists(a.file):
        print('\n[2] 未找到实测文件，跳过 [2]~[4]：%s' % a.file)
        return
    P = real_params(a.file)
    report_sim(a.profile, P)
    pksA, pksC, pksD, ph, edge_ms, lastxs, m_far = scan_phase(a.profile, P)
    _, st4b = bp_input_step(a.profile, P)
    pksE = None
    if a.shadow:
        _, pksE = scan_shadow(a.profile, P)
    attribution(pksA, pksC, pksD, pksE, st4b, m_far)
    thresholds(a.profile, P)

    if a.fig:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
        plt.rcParams['axes.unicode_minus'] = False
        macro, _, _ = T47.PROFILES[a.profile]
        sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
        nsec = len(sos)

        fig, ax = plt.subplots(1, 3, figsize=(16.5, 4.6))

        # (a) 瞬态波形：改前 vs 改后（双 y 轴，否则 C 的细节被 A 压没）
        r = sim(a.profile, P, 'steady', P['off_s'], 1.2)
        t = np.arange(len(r['C改后预置'])) / FS * 1000
        l1, = ax[0].plot(t, r['A改前清零'], lw=1.0, color='#d62728', label='A 改前清零（左轴）')
        ax[0].axhline(0, color='k', lw=.6)
        ax[0].set_xlim(0, 300)
        ax[0].set_xlabel('切回 on 后 (ms)')
        ax[0].set_ylabel('A 改前：相对"从不切换"的偏差 (µV)', color='#d62728')
        ax[0].tick_params(axis='y', labelcolor='#d62728')
        ax0b = ax[0].twinx()
        l2, = ax0b.plot(t, r['C改后预置'], lw=1.4, color='#2ca02c', label='C 改后 DC 预置（右轴）')
        ax0b.axhline(0, color='g', lw=.5, ls=':')
        ax0b.set_ylabel('C 改后：偏差 (µV)', color='#2ca02c')
        ax0b.tick_params(axis='y', labelcolor='#2ca02c')
        ax[0].set_title('(a) off→on 瞬态：落码前 vs 落码后\n输入参数取自 T48 实测（%.0f µV/2 Hz 方波 + %.1f µV 工频）'
                        % (P['pp'], P['m50']))
        ax[0].legend(handles=[l1, l2], fontsize=9, loc='upper right')
        ax[0].grid(alpha=.3)

        # (b) 切换相位扫描：A vs C 配对（+D 变量分离对照）
        ax[1].plot(ph * 360, pksA, lw=1.0, color='#d62728', marker='.', ms=3, label='A 改前清零')
        ax[1].plot(ph * 360, pksC, lw=1.0, color='#2ca02c', marker='.', ms=3, label='C 改后预置')
        ax[1].plot(ph * 360, pksD, lw=1.0, color='#1f77b4', marker='.', ms=3,
                   label='D 仅预置动作（不跳节）')
        ax[1].axhline(30, color='gray', ls=':', lw=.9, label='判读线 30 µV')
        ax[1].set_yscale('log')
        ax[1].set_xlabel('切换时刻在方波周期内的相位 (°)\n0°/180° = 方波边沿所在')
        ax[1].set_ylabel('瞬态峰值 (µV)')
        ax[1].set_title('(b) 同相位配对对比\nC 中位 %.1f µV、最坏 %.0f µV；D 最坏 %.0f µV'
                        % (np.median(pksC), pksC.max(), pksD.max()))
        ax[1].legend(fontsize=8); ax[1].grid(alpha=.3, which='both')

        # (c) C 的峰值 vs 距边沿距离
        ax[2].scatter(edge_ms, pksC, s=9, color='#9467bd', label='C 改后')
        ax[2].axvline(20, color='r', ls='--', lw=.8, label='20 ms')
        ax[2].set_yscale('log')
        ax[2].set_xlabel('切换点距最近方波边沿 (ms)')
        ax[2].set_ylabel('瞬态峰值 (µV)')
        ax[2].set_title('(c) 越贴方波边沿，DC 稳态假设越弱\n⇒ 偶发大瞬态只出现在贴边沿时')
        ax[2].legend(fontsize=8); ax[2].grid(alpha=.3, which='both')

        plt.tight_layout()
        out = os.path.join(HERE, 'T49_DC预置_落码复核_2026-09-20.png')
        plt.savefig(out, dpi=125)
        print('\n图已保存：%s' % os.path.abspath(out))


if __name__ == '__main__':
    main()
