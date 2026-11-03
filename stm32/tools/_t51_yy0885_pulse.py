#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
T51 离线判读与 what-if：YY 0885-2013（动态心电图系统）2.1.5 频率响应

判据（原文摘录，出处见报告 §1）：
  a) 3 mV / 100 ms 方波脉冲
     a1) 脉冲区外位移      <= 0.1 mV      (100 µV)
     a2) 脉冲终点后斜率    <= 0.30 mV/s   (300 µV/s)
     a3) 脉冲边缘过冲      <  10%
  b) 0.67~40 Hz 正弦，幅度在 5 Hz 响应的 140%~70%（+3/-3 dB）
  c) 1.5 mV/40 ms 三角波群响应 在 1.5 mV/200 ms 三角波群响应的 60%~110%

★ 系数来源：--profile 走**直接解析** Filter/ecg_filter.c + ecg_filter_coeffs.h 的 F64 表
  （验的就是编进固件的那组数）；--whatif 才用 scipy 现设计备选链（探索用，非固件现状）。

跑法：
  <venv-python> _t51_yy0885_pulse.py --profile IMP620_N2
  <venv-python> _t51_yy0885_pulse.py --profile IMP620_N2 --whatif --fig
"""
import argparse
import os
import re

import numpy as np

FS = 1e6 / 1172.0            # 853.2423208191125 Hz（ADS1293 DRDY 实测 1172 µs）
FS_TXT = 500.0               # ⚠ 实测 txt 时间轴；永不与 FS 混用

HERE = os.path.dirname(os.path.abspath(__file__))
COEFFS_H = os.path.join(HERE, '..', 'Filter', 'ecg_filter_coeffs.h')
COEFFS_C = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')

PROFILES = {
    'ST40':      ('ECG_PROFILE_ST40',       7,  3),
    'D1':        ('ECG_PROFILE_DIAG150',    4,  1),
    'D2':        ('ECG_PROFILE_DIAG150_D2', 6,  2),
    'D3':        ('ECG_PROFILE_DIAG150_D3', 7,  4),
    'IMP620':    ('ECG_PROFILE_IMP620',     9,  5),
    'IMP620_N2': ('ECG_PROFILE_IMP620_N2', 13,  9),
    # T79 补：T76/T77 新增档（此前缺失 ⇒ 无法对"当前默认档"做判据 a 评估）
    'DUAL_A':      ('ECG_PROFILE_DUAL_A',       12,  9),
    'DUAL_A_PLUS': ('ECG_PROFILE_DUAL_A_PLUS',  18, 15),
    # ★ T176(2026-10-09) 补：T84 投产档 —— 16 节 = 15 陷波 + LP2@250；
    #   低端**只有 dc_block@0.02 一阶**（BP 里连 HP 都没有）⇒ 见 PROFILE_DC_FC 的警告。
    'DUAL_A_PLUS_B': ('ECG_PROFILE_DUAL_A_PLUS_B', 16, 15),
}

# ★ T176(2026-10-09)：dc_block（1 阶 HP）此前在 [1] 判据 a 的链里**完全缺失**
#   （`run()` 无、`band_mag()` 无 —— 只在提示行印了"＋dc_block"，属"说了没做"）。
#   对 A+B 档是**致命**的：A+B 的 BP 只有 LP2@250、**没有任何 HP** ⇒ 缺 dc_block 时
#   低端阶数 = 0 ⇒ LP2@250 直通直流 ⇒ a1 = +0.0 µV、a2 = 0.0 µV/s 的**满分假通过**
#   （T174 §5-⑥ / 补采作业卡 §6 / S6 补测 §4.4 —— 三处都已登记此缺口）。
#   真值表逐条取自 Filter/ecg_filter_coeffs.h 的 `ECG_DC_BLOCK_FC_HZ_F`（:367-424）：
#     九档里**只有 A+B 是 0.02 Hz**，其余 8 档全 0.05 Hz。
#   🛑 只在 [1] 的信号路径上施加，**绝不改 `run()`** —— `run()` 被 `_t80_faithful.py`
#      复用（它 `m.run(bp, x853, first=0)` 之后自己又调了一次 dc_block），
#      改 `run()` 会**二次施加**、把 `_t80` 的读数带偏。
PROFILE_DC_FC = {
    'ECG_PROFILE_ST40':          0.05,
    'ECG_PROFILE_DIAG150':       0.05,
    'ECG_PROFILE_DIAG150_D2':    0.05,
    'ECG_PROFILE_DIAG150_D3':    0.05,
    'ECG_PROFILE_IMP620':        0.05,
    'ECG_PROFILE_IMP620_N2':     0.05,
    'ECG_PROFILE_DUAL_A':        0.05,
    'ECG_PROFILE_DUAL_A_PLUS':   0.05,
    'ECG_PROFILE_DUAL_A_PLUS_B': 0.02,
}


# ---------------------------------------------------------------- 系数读取

def parse_coeffs_f64(path_c, path_h, macro):
    """解析某档位 ECG_FILTER_COEFFS_F64 表。返回 (sos, n_notch)。"""
    txt = open(path_c, encoding='utf-8').read()
    txt_h = open(path_h, encoding='utf-8').read()

    m = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)', txt)
    if not m:
        raise SystemExit('ecg_filter.c 里找不到档位分支: ' + macro)
    seg = txt[m.end():]

    mn = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)'
                   r'([\s\S]*?)(?=#(?:elif|else|endif))', txt_h)
    mm = re.search(r'#define\s+ECG_NUM_NOTCH_SECTIONS\s+(\d+)', mn.group(1)) if mn else None
    n_notch = int(mm.group(1)) if mm else None

    mt = re.search(r'const\s+ecg_biquad_coeff_f64\s+ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{', seg)
    if not mt:
        raise SystemExit('该分支找不到 ECG_FILTER_COEFFS_F64 表: ' + macro)
    body = seg[mt.end():]
    body = body[:body.index('};')]
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
    body = re.sub(r'//[^\n]*', '', body)

    rows = []
    for item in re.finditer(r'\{([^{}]*)\}', body):
        nums = [float(v) for v in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', item.group(1))]
        if len(nums) != 5:
            raise SystemExit('系数条目数字个数 != 5')
        rows.append(tuple(nums))
    return rows, n_notch


# ---------------------------------------------------------------- 运行环境

def newstate(nsec):
    return [[0.0, 0.0] for _ in range(nsec)]


def run(sos, x, state=None, first=0):
    """逐样本 DF2T（与固件 biquad_df2t_f64_process 同式）。first = 循环起点。"""
    if state is None:
        state = newstate(len(sos))
    y = np.empty_like(np.asarray(x, dtype=float))
    nsec = len(sos)
    for k in range(len(x)):
        v = x[k]
        for i in range(first, nsec):
            b0, b1, b2, a1, a2 = sos[i]
            d = state[i]
            w = b0 * v + d[0]
            d[0] = b1 * v - a1 * w + d[1]
            d[1] = b2 * v - a2 * w
            v = w
        y[k] = v
    return y


def section_kind(sos_row):
    """按零点位置判节类型：HP / LP / NOTCH。"""
    b0, b1, b2, a1, a2 = sos_row
    if abs(b0) < 1e-15:
        return 'ZERO'
    z = np.roots([b0, b1, b2])
    if len(z) == 0:
        return '?'
    zang = np.abs(np.angle(z))
    if np.all(zang < 1e-2):
        return 'HP'
    if np.all(zang > np.pi - 1e-2):
        return 'LP'
    return 'NOTCH'


def band_mag(sos, f, first=0):
    z = np.exp(1j * 2 * np.pi * f / FS)
    H = 1.0 + 0j
    for i in range(first, len(sos)):
        b0, b1, b2, a1, a2 = sos[i]
        H *= (b0 + b1 / z + b2 / z ** 2) / (1.0 + a1 / z + a2 / z ** 2)
    return H


def dc_block_f64(x, fc, fs=FS):
    """固件 `ecg_dc_block` 同式的一阶高通：H(z) = (1 − z⁻¹)/(1 − r·z⁻¹)，r = 1 − 2π·fc/fs。

    ★ T176(2026-10-09) 新增。与 `_t80_faithful.dc_block()` **逐位同式**（同用
      `scipy.signal.lfilter`、同系数）⇒ 两工具的低端模型一致，交叉核对才有意义。

    🛑 **串联顺序说明（别改错位置）**：固件生效链是
        `Filter/fast_restore_snippet.c:186`
        `return ecg_filter_f64_process(&f64, (double)ecg_dc_block_process(&g_dc, (float)x_raw));`
      ⇒ **raw → dc_block（float32）→ biquad 节（f64）**，dc_block 在**链首**。
      本函数在 [1] 里被放在 `run()` **之后**（链尾）—— 两者都是 LTI、级联**可交换**
      ⇒ 传递函数与稳态响应**逐点等价**（`_t80` 同样是链尾口径，故两工具仍可比）。
    """
    from scipy.signal import lfilter
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], np.asarray(x, dtype=float))


def hdc_mag(f, fc, fs=FS):
    """dc_block（一阶 HP）在频率 f 的频响模值 —— 供频域判据 b 用。

    与 `dc_block_f64` 同一 H(z)；LTI 级联 ⇒ 频域可**直接相乘**，无需时域仿真。
    """
    if not fc:
        return 1.0
    r = 1.0 - 2.0 * np.pi * fc / fs
    z = np.exp(-1j * 2 * np.pi * f / fs)
    return abs((1.0 - z) / (1.0 - r * z))


# ---------------------------------------------------------------- 激励

def pulse_train(n, fs, amp, width, t0, period):
    x = np.zeros(n)
    idx = []
    t = t0
    while t < n / fs:
        i0 = int(round(t * fs))
        x[i0:min(int(round((t + width) * fs)), n)] = amp
        idx.append(i0)
        t += period
    return x, idx


def triangle_train(n, fs, amp, base, t0, period):
    x = np.zeros(n)
    idx = []
    half = max(2, int(round(base / 2 * fs)))
    up = np.linspace(0, amp, half, endpoint=False)
    dn = np.linspace(amp, 0, half, endpoint=False)
    seg = np.concatenate([up, dn])
    t = t0
    while t < n / fs:
        i0 = int(round(t * fs))
        i1 = min(i0 + len(seg), n)
        x[i0:i1] = seg[:i1 - i0]
        idx.append(i0)
        t += period
    return x, idx


# ---------------------------------------------------------------- 判据读数

def read_all(y, fs, idx, amp, width, y0_ref=None):
    """返回每条脉冲的判据读数 dict。y 单位 µV。"""
    w = int(round(width * fs))
    out = []
    for i0 in idx:
        i1 = i0 + w
        if i1 + int(3.5 * fs) >= len(y) or i0 < int(0.6 * fs):
            continue
        base = float(np.median(y[i0 - int(0.5 * fs):i0]))
        u = y - base
        # ---- a3 过冲 / 下冲（上升沿）
        seg_up = u[i0:i0 + int(0.05 * fs)]
        peak = float(np.max(seg_up))
        plat_near = float(np.median(u[i0 + int(0.030 * fs):i0 + int(0.050 * fs)]))
        plat_far = float(np.median(u[i1 - int(0.02 * fs):i1]))
        ovs = dict(
            near=(peak - plat_near) / plat_near * 100.0 if plat_near > 0 else np.nan,
            nom=(peak - amp) / amp * 100.0,
            far=(peak - plat_far) / plat_far * 100.0 if plat_far > 0 else np.nan,
        )
        # ---- 下降沿下冲
        seg_dn = u[i1:i1 + int(0.05 * fs)]
        valley = float(np.min(seg_dn))
        dns = dict(near=-valley / plat_near * 100.0 if plat_near > 0 else np.nan,
                   nom=-valley / amp * 100.0,
                   far=-valley / plat_far * 100.0 if plat_far > 0 else np.nan)
        # ---- a1 位移（脉冲区外，从降沿后 X s 起算，1 s 窗内最大 |u|）
        #      ⚠ 窗口末端必须避开下一段脉冲（脉冲间隔可能只有 2 s）
        #      ⚠⚠ T79：max|u| 是**极值统计**，会被噪声上抬约 3~4σ。本例实测 σ≈20 µV
        #         ⇒ 上抬 ≈65 µV，对一个限 100 µV 的判据是致命的。
        #         ⇒ 同时算**中位口径** `dispm`（对极值免疫），两者并列报出；若 max 超限
        #           而中位不超，应判"极值口径在此信噪比下不可用"，而不是"不达标"。
        nxt = len(y)
        for j0 in idx:
            if j0 > i1:
                nxt = j0
                break
        guard = max(0, nxt - int(0.3 * fs))
        disp, dispm = {}, {}
        for xv in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0):
            j = i1 + int(xv * fs)
            jend = min(j + int(1.0 * fs), guard)
            if jend > j + int(0.2 * fs):
                seg = np.abs(u[j:jend])
                disp[xv] = float(np.max(seg))
                dispm[xv] = float(np.median(seg))
        # ---- a2 斜率（窗口末端同样要避开下一段脉冲）
        cap = guard
        #   (i) 滑动 100 ms 窗平均斜率的最大值（从降沿后 50 ms 起，避开边沿过渡）
        j0 = i1 + int(0.05 * fs)
        j1_ = min(i1 + int(3.0 * fs), cap)
        sw = int(round(0.1 * fs))
        sl_win = float(np.max(np.abs(u[j0 + sw:j1_] - u[j0:j1_ - sw]))) / 0.1 if j1_ - sw > j0 else np.nan
        #   (ii) 线性拟合斜率（降沿后 0.2~1.2 s）
        j0b, j1b = i1 + int(0.2 * fs), min(i1 + int(1.2 * fs), cap)
        sl_fit = float(abs(np.polyfit(np.arange(j1b - j0b) / fs, u[j0b:j1b], 1)[0])) if j1b - j0b > 10 else np.nan
        #   (iii) 含边沿的最大单样本斜率（仅参照，必然爆表）
        sl_raw = float(np.max(np.abs(np.diff(u[i1:i1 + int(0.5 * fs)])))) * fs
        # ---- ★★ a1 **稳态口径**（T81 主口径）
        #  GB 9706.247 a)「脉冲后的**基线**与脉冲出现前基线之间的偏移」⇒ 这是**基线量**
        #  ⇒ 必须在**恢复到平稳之后**读。与 a2 分工：a1 管落点（永久/长时漂移）、a2 管恢复速率；
        #     若在脉冲后瞬时取 max|u|，则与下冲/a2 完全重复，a1 条款失去独立意义（T78/T79 的错）。
        #  操作定义：恢复窗（i1 ~ guard）**尾段 30%（≥0.4 s 窗才启用）的中位值**。
        #  另给 a1_inlim_t = |u| 最后一次超 ±100 µV 的时刻 ⇒ "多久恢复"的量化指标。
        wlen = guard - i1
        # 稳态口径单独用更宽的窗（避开下一脉冲只需留 0.08 s，比 disp/slope 的 0.3 s 更贴恢复末端）
        guard_s = max(i1 + 1, nxt - int(0.08 * fs))
        wl_s = guard_s - i1
        stead, stead_from = np.nan, np.nan
        if wl_s >= int(0.4 * fs):
            st = i1 + int(0.70 * wl_s)
            stead = float(np.median(u[st:guard_s]))
            stead_from = (st - i1) / fs
        bad1 = np.where(np.abs(u[i1:guard_s]) > 100.0)[0] if wl_s > 0 else np.array([], dtype=int)
        a1_t = 0.0 if len(bad1) == 0 else float((bad1[-1] + 1) / fs)
        out.append(dict(i0=i0, base=base, peak=peak, valley=valley,
                        plat_near=plat_near, plat_far=plat_far,
                        ovs=ovs, dns=dns, disp=disp, dispm=dispm,
                        stead=stead, stead_from=stead_from, a1_inlim_t=a1_t,
                        slope=dict(win=sl_win, fit=sl_fit, raw=sl_raw)))
    return out


def med(rs, path):
    v = []
    for r in rs:
        o = r
        for k in path:
            o = o.get(k, np.nan) if isinstance(o, dict) else np.nan
        if isinstance(o, (int, float)) and not np.isnan(o):
            v.append(o)
    return float(np.median(v)) if v else float('nan')


def verdict(v, lim, ok_is_low=True, name=''):
    if np.isnan(v):
        return '  n/a'
    ok = (v <= lim) if ok_is_low else (v >= lim)
    return '✓' if ok else '✗ %s超 %.1f×' % (name, v / lim if ok_is_low else lim / v)


# ---------------------------------------------------------------- what-if 设计

def design(hp_order, hp_fc, lp_order, lp_fc, dc_fc=0.05, fs=FS):
    """dc_block(1 阶 HP @dc_fc) + Butterworth HP(hp_order@hp_fc) + LP(lp_order@lp_fc)。"""
    from scipy.signal import butter, sosfilt
    secs = []
    if dc_fc:
        # 一阶高通：y = a*(y[-1] + x - x[-1])
        a = 1.0 / (1.0 + 2 * np.pi * dc_fc / fs)
        secs.append(('dc1', a))
    if hp_order:
        sos = butter(hp_order, hp_fc, btype='high', fs=fs, output='sos')
        for r in sos:
            secs.append(('sos', r))
    if lp_order:
        sos = butter(lp_order, lp_fc, btype='low', fs=fs, output='sos')
        for r in sos:
            secs.append(('sos', r))
    return secs


def run_design(secs, x):
    y = np.array(x, dtype=float)
    for kind, p in secs:
        if kind == 'dc1':
            a = p
            z = np.empty_like(y)
            yv = 0.0
            xp = 0.0
            for k in range(len(y)):
                yv = a * (yv + y[k] - xp)
                xp = y[k]
                z[k] = yv
            y = z
        else:
            y = _sosfilt_one(p, y)
    return y


def _sosfilt_one(row, x):
    if len(row) == 6:                      # scipy sos: [b0,b1,b2,a0,a1,a2]
        b0, b1, b2, a0, a1, a2 = row
        b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
    else:
        b0, b1, b2, a1, a2 = row
    y = np.empty_like(x)
    d1 = d2 = 0.0
    for k in range(len(x)):
        v = x[k]
        w = b0 * v + d1
        d1 = b1 * v - a1 * w + d2
        d2 = b2 * v - a2 * w
        y[k] = w
    return y


# ---------------------------------------------------------------- main

def load_txt(path):
    """读 7 列实测 txt。返回 (uv_col5, uv_col4, uv_col2, fs)。
    c5 = 列⑤ filterraw   = **链输出**（滤波后，code 域 0.1122 µV/code，不削顶）
    c4 = 列④ dsrateraw   = int16 插值产物 (0.1526 µV/LSB，±32767 会削顶)
    c2 = 列② ecgraw      = **链输入**（AFE 原始码，同 code 域 0.1122 µV/code）
                          ⇒ 用它做"链前"对照：形状/幅度/tail 是否在链前就存在（T79 归因法）
    ⚠ 该文本约 0.03% 行格式错乱（多一列/粘连），必须容错解析，不能直接 loadtxt。
    ⚠ 第⑦列 gdtover 固件以 %x 输出 ⇒ **绝不要对整行 float()**（会静默丢行，见 T78）；
      本函数只取 p[:5] ⇒ 天然安全。
    """
    rows = []
    bad = 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for ln in f:
            p = ln.split()
            if len(p) < 5:
                bad += 1
                continue
            try:
                rows.append([float(p[0]), float(p[1]), float(p[2]),
                             float(p[3]), float(p[4])])
            except ValueError:
                bad += 1
    arr = np.array(rows)
    if bad:
        print('  ⚠ 容错跳过的坏行: %d (%.3f%%)' % (bad, 100.0 * bad / (len(rows) + bad)))
    return arr[:, 4] * 0.1122, arr[:, 3] * 0.1526, arr[:, 1] * 0.1122, FS_TXT


def detect_pulses(uv, fs, thr_frac=0.5, min_gap=0.5):
    """阈值穿越法找脉冲起点。返回 (起点样本列表, 实测脉宽 s)。"""
    hi = float(np.percentile(uv, 98.0))
    lo = float(np.median(uv))
    thr = lo + thr_frac * (hi - lo)
    up = uv > thr
    starts, ends = [], []
    n = len(uv)
    k = 1
    while k < n:
        if up[k] and not up[k - 1]:
            starts.append(k)
        elif up[k - 1] and not up[k]:
            ends.append(k)
        k += 1
    # 起点与终点配对
    pairs = []
    for s in starts:
        e = next((x for x in ends if x > s), None)
        if e and (e - s) / fs >= 0.02:
            pairs.append((s, e))
    out, ws = [], []
    for s, e in pairs:
        if out and (s - out[-1]) / fs < min_gap:
            continue
        out.append(s)
        ws.append((e - s) / fs)
    width = float(np.median(ws)) if ws else 0.1
    return out, width


def analyse_file(path, amp_nom=3000.0):
    uv, uv_lsb, uv_pre, fs = load_txt(path)
    print('  样本 %d ｜ 时长 %.2f s ｜ FS_TXT=%.0f Hz' % (len(uv), len(uv) / fs, fs))
    print('  列⑤(code域) p-p %.1f µV ｜ 列④(LSB) p-p %.1f µV ｜ 削顶率 %.2f%%'
          % (np.ptp(uv), np.ptp(uv_lsb),
             100.0 * np.mean(np.abs(uv_lsb) > 32767 * 0.1526 * 0.999)))
    idx, width = detect_pulses(uv, fs)
    print('  检出脉冲 %d 段，起点间隔 %s 样本'
          % (len(idx), np.round(np.diff(idx)[:5]).astype(int) if len(idx) > 1 else '-'))
    if not idx:
        return None
    gaps = float(np.median(np.diff(idx))) / fs if len(idx) > 1 else 2.0
    print('  推得脉宽 %.1f ms ｜ 间隔 %.2f s' % (width * 1e3, gaps))
    rs = read_all(uv, fs, idx, amp_nom, width)
    if not rs:
        # 用实测平台当标称
        pk = float(np.percentile(uv[idx[0]:idx[0] + int(width * fs)], 90))
        print('  用实测平台 %.1f µV 重算' % pk)
        rs = read_all(uv, fs, idx, pk, width)
    # ---- ★ 链前对照（T79 归因法）：同口径跑 列②(链输入)，看 tail/形状是否在链前就存在。
    #      链前也超限 ⇒ 源/AFE 侧；链前干净而链后超限 ⇒ 100% 固件链。零成本，必做。
    print('  ---- 链前对照（列②＝AFE 原始码，链输入）----')
    rp = read_all(uv_pre, fs, idx, amp_nom, width)
    if rp:
        print('     平台近 %8.1f µV ｜ 平台远 %8.1f µV ｜ droop %+7.1f µV ｜ 过冲 %6.2f %%'
              % (med(rp, ['plat_near']), med(rp, ['plat_far']),
                 med(rp, ['plat_far']) - med(rp, ['plat_near']), med(rp, ['ovs', 'near'])))
        for xv in (0.05, 0.2, 1.0):
            print('     a1 位移 降沿后+%.2fs:  max|u| %7.1f  中位|u| %7.1f'
                  % (xv, med(rp, ['disp', xv]), med(rp, ['dispm', xv])))
    else:
        print('     （列②样本不足，跳过）')
    return rs, width, idx, uv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='IMP620_N2', choices=list(PROFILES))
    ap.add_argument('--file', action='append', default=None,
                    help='实测 7 列 txt（可多次，逐个判读）')
    ap.add_argument('--amp', type=float, default=3000.0)
    ap.add_argument('--width', type=float, default=0.1)
    ap.add_argument('--period', type=float, default=20.0)
    ap.add_argument('--dc-fc', type=float, default=None,
                    help='dc_block 截止频率 Hz；缺省＝按 --profile 查表（A+B=0.02、其余 0.05）')
    ap.add_argument('--no-dc', action='store_true',
                    help='🛑 关闭 dc_block（**仅用于复现 T176 之前的旧读数**；A+B 会满分假通过）')
    ap.add_argument('--whatif', action='store_true')
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    if a.file:
        for p in a.file:
            print()
            print('=' * 80)
            print('[F] 实测判读：%s' % os.path.basename(p))
            print('=' * 80)
            r = analyse_file(p, a.amp)
            if not r:
                continue
            rs, width, idx, uv = r
            print('   近端平台 %8.1f µV ｜ 远端平台 %8.1f µV ｜ 标称 %.0f µV'
                  % (med(rs, ['plat_near']), med(rs, ['plat_far']), a.amp))
            print('   a3 上升沿过冲：对近端平台 %6.2f %% ｜ 对理想幅值 %6.2f %% ｜ 对远端平台 %6.2f %%   %s'
                  % (med(rs, ['ovs', 'near']), med(rs, ['ovs', 'nom']), med(rs, ['ovs', 'far']),
                     '✓' if med(rs, ['ovs', 'near']) < 10 else '✗ 超 10%'))
            print('      下降沿下冲：对近端平台 %6.2f %% ｜ 对理想幅值 %6.2f %%'
                  % (med(rs, ['dns', 'near']), med(rs, ['dns', 'nom'])))
            for xv in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0):
                v = med(rs, ['disp', xv])
                vm = med(rs, ['dispm', xv])
                print('   a1 位移（降沿后 %+4.2f s 起算）max|u| %8.1f µV %s ｜ 中位|u| %7.1f µV %s'
                      % (xv, v, verdict(v, 100.0), vm, verdict(vm, 100.0)))
            print('      ⚠ max|u| 是极值统计（≈尾量＋3~4σ）**且是波形瞬态** ⇒ 非 a1 本义（T79/T81）')
            print('   ★a1 稳态口径（主）：脉冲后恢复平台 %+8.1f µV  %s   （平台＝恢复窗尾段 30%% 中位，自降沿后 +%.2f s 起）'
                  % (med(rs, ['stead']), verdict(med(rs, ['stead']), 100.0),
                     med(rs, ['stead_from'])))
            print('      ↳ 恢复时间：|u| 最后一次超 ±100 µV 在降沿后 +%.2f s' % med(rs, ['a1_inlim_t']))
            print('   a2 斜率：滑窗100ms %7.1f µV/s %s ｜ 拟合0.2~1.2s %7.1f %s'
                  % (med(rs, ['slope', 'win']), verdict(med(rs, ['slope', 'win']), 300.0),
                     med(rs, ['slope', 'fit']), verdict(med(rs, ['slope', 'fit']), 300.0)))
            print('      ⚠ 滑窗口径＝两点差取最大，同样被噪声极值放大（T79 实测 552 vs 拟合 184）')
        if not a.whatif and not a.fig:
            return

    macro, _, _ = PROFILES[a.profile]
    sos, n_notch = parse_coeffs_f64(COEFFS_C, COEFFS_H, macro)

    # ★ T176：dc_block 截止频率（按档查表；--no-dc ⇒ 0.0 ＝ 关闭，仅复现旧读数用）
    dc_fc = 0.0 if a.no_dc else (a.dc_fc if a.dc_fc is not None
                                 else PROFILE_DC_FC.get(macro, 0.05))

    print('=' * 80)
    print('[0] 系数审计  profile=%s  节数=%d  链首陷波=%d  FS=%.4f Hz' %
          (a.profile, len(sos), n_notch, FS))
    print('=' * 80)
    kinds = [section_kind(s) for s in sos]
    n_hp = sum(1 for i, k in enumerate(kinds) if k == 'HP' and i >= n_notch)
    n_lp = sum(1 for i, k in enumerate(kinds) if k == 'LP' and i >= n_notch)
    for i, s in enumerate(sos):
        tag = '陷波' if i < n_notch else 'BP '
        print('  节%2d [%s] %-5s' % (i, tag, kinds[i]))
    print('  ⇒ 陷波 %d 节 ｜ BP %d 节 = HP %d 阶 + LP %d 阶 ｜ ＋dc_block(1 阶 HP @%.3f Hz)%s'
          % (n_notch, len(sos) - n_notch, 2 * n_hp, 2 * n_lp, dc_fc,
             '  ⚠ 已用 --no-dc 关闭' if dc_fc == 0 else ''))
    print('  ⇒ **低端总阶数 = %d，高端总阶数 = %d**'
          % (2 * n_hp + (0 if dc_fc == 0 else 1), 2 * n_lp))
    if n_hp == 0 and dc_fc == 0:
        print('  🛑 **警告**：本档 BP 无 HP 节且 dc_block 已关 ⇒ 低端阶数 = 0，'
              '链直通直流 ⇒ a1/a2 必为 +0.0/0.0 的**满分假通过**（不是真结论）。')
    print('  ✓ 判据 b/c **已含链首 dc_block**（T181 补链）：'
          '判据 b @0.67 Hz 影响 −0.27 pp（fc=0.05）／−0.04 pp（fc=0.02），'
          '判据 c 比值 −0.5~−0.8 pp —— 判限均不翻转，但旧自陈"<0.05%"低估 6~16×。')

    # ⚠ 原式 n = period*2*FS 在 period 小时算不出脉冲（t0=5.0 已超出 n/fs）
    #   ⇒ 改为"起拍 5 s + 至少 4 个周期或 6 s 尾窗"，小间隔也能跑（T79）
    n = int((5.0 + max(4.0 * a.period, 6.0)) * FS)
    x, idx = pulse_train(n, FS, a.amp, a.width, 5.0, a.period)
    print('  [激励] 样本 %d ＝ %.1f s ｜ 起拍 5.0 s ｜ 间隔 %.2f s ｜ 脉冲 %d 段'
          % (n, n / FS, a.period, len(idx)))

    print()
    print('=' * 80)
    print('[1] 判据 a —— 3 mV / %.0f ms 方波（间隔 %.0f s）' % (a.width * 1e3, a.period))
    print('=' * 80)
    res = {}
    for lab, first in (('notch ON', 0), ('notch OFF', n_notch), ('全旁路', len(sos))):
        if first > len(sos):
            continue
        y = run(sos, x, first=first)
        # ★ T176：低端 dc_block（1 阶 HP）在此施加 —— `run()` 不含它（见 PROFILE_DC_FC 警告）。
        if dc_fc:
            y = dc_block_f64(y, dc_fc, FS)
        rs = read_all(y, FS, idx, a.amp, a.width)
        if not rs:
            continue
        res[lab] = (y, rs)
        print()
        print('  ---- %s ----' % lab)
        print('   近端平台 %8.1f µV ｜ 远端平台 %8.1f µV ｜ 输入 %.0f µV'
              % (med(rs, ['plat_near']), med(rs, ['plat_far']), a.amp))
        print('   a3 上升沿过冲：对近端平台 %6.2f %%  ｜ 对理想幅值 %6.2f %%  ｜ 对远端平台 %6.2f %%   %s'
              % (med(rs, ['ovs', 'near']), med(rs, ['ovs', 'nom']), med(rs, ['ovs', 'far']),
                 '✓' if med(rs, ['ovs', 'near']) < 10 else '✗ 超 10%'))
        print('      下降沿下冲：对近端平台 %6.2f %%  ｜ 对理想幅值 %6.2f %%   %s'
              % (med(rs, ['dns', 'near']), med(rs, ['dns', 'nom']),
                 '✓' if max(med(rs, ['dns', 'near']), med(rs, ['dns', 'nom'])) < 10 else '✗ 超 10%'))
        for xv in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0):
            v = med(rs, ['disp', xv])
            print('   a1 位移（降沿后 %+4.2f s 起算）%8.1f µV   %s   [旧口径·瞬态峰值]'
                  % (xv, v, verdict(v, 100.0)))
        print('   ★a1 稳态口径（主）：脉冲后恢复平台 %+8.1f µV  %s   （恢复窗尾段 30%% 中位）'
              % (med(rs, ['stead']), verdict(med(rs, ['stead']), 100.0)))
        print('      ↳ 恢复时间：|u| 最后一次超 ±100 µV 在降沿后 +%.2f s' % med(rs, ['a1_inlim_t']))
        print('   a2 斜率：滑窗100ms %7.1f µV/s %s ｜ 拟合0.2~1.2s %7.1f %s ｜ [参照]含边沿 %9.0f %s'
              % (med(rs, ['slope', 'win']), verdict(med(rs, ['slope', 'win']), 300.0),
                 med(rs, ['slope', 'fit']), verdict(med(rs, ['slope', 'fit']), 300.0),
                 med(rs, ['slope', 'raw']), '(口径非法)'))

    # ------------------------------------------------------------ 判据 b / c
    print()
    print('=' * 80)
    print('[2] 判据 b —— 0.67~40 Hz 正弦，以 5 Hz 响应为参考（判限 70%~140%；含 dc_block）')
    print('=' * 80)
    for lab, first in (('notch ON', 0), ('notch OFF', n_notch)):
        # ★ 补链（T181 拍板）：频域判据 b 也含链首 dc_block（LTI 级联 ⇒ 模值直接相乘）。
        H5 = abs(band_mag(sos, 5.0, first)) * hdc_mag(5.0, dc_fc)
        line = []
        for f in (0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0):
            line.append('%6.1f%%' % (abs(band_mag(sos, f, first)) * hdc_mag(f, dc_fc) / H5 * 100))
        worst = max(abs(abs(band_mag(sos, f, first)) * hdc_mag(f, dc_fc) / H5 * 100 - 100)
                    for f in (0.67, 40.0))
        print('  %-9s 0.67/1/2/5/10/20/40 Hz = %s   %s'
              % (lab, ' '.join(line), '✓' if worst <= 40 else '✗'))

    print()
    print('=' * 80)
    print('[3] 判据 c —— 1.5 mV/40 ms 三角波 vs 1.5 mV/200 ms（判限 60%~110%，猛哥抄件写 70%；含 dc_block）')
    print('=' * 80)
    n2 = int(6.0 * FS)
    for lab, first in (('notch ON', 0), ('notch OFF', n_notch)):
        amps = {}
        for base in (0.2, 0.04):
            xt, idt = triangle_train(n2, FS, 1500.0, base, 1.0, 2.0)
            yt = run(sos, xt, first=first)
            if dc_fc:                      # ★ 补链（T181 拍板）：链首 dc_block 也在信号路径上
                yt = dc_block_f64(yt, dc_fc, FS)
            pv = []
            for i0 in idt:
                j0, j1_ = i0 - int(0.1 * FS), i0 + int(2 * base * FS) + int(0.3 * FS)
                if j1_ < len(yt) and j0 > 0:
                    pv.append(float(np.max(yt[j0:j1_]) - np.min(yt[j0:j1_])))
            amps[base] = float(np.median(pv)) if pv else float('nan')
        r = amps[0.04] / amps[0.2] * 100
        print('  %-9s 200ms %7.1f µV ｜ 40ms %7.1f µV ｜ 比值 %6.1f %%   %s'
              % (lab, amps[0.2], amps[0.04], r, '✓' if 60 <= r <= 110 else '✗'))

    # ------------------------------------------------------------ what-if
    if a.whatif:
        print()
        print('=' * 80)
        print('[4] what-if —— 换链结构（scipy 现设计）：对判据 a1 / a3 的影响')
        print('=' * 80)
        print('  %-34s %9s %9s %9s %9s' % ('链结构', '过冲%', '平台µV', '位移µV', '斜率µV/s'))
        variants = [
            ('现状 dc1@0.05 + BW4[0.05,150]', 4, 0.05, 4, 150.0, 0.05),
            ('dc1@0.05 + BW3[0.05,150]', 3, 0.05, 3, 150.0, 0.05),
            ('dc1@0.05 + BW2[0.05,150]', 2, 0.05, 2, 150.0, 0.05),
            ('dc1@0.05 + HP0 + LP2@150', 0, 0.05, 2, 150.0, 0.05),
            ('dc1@0.05 + HP0 + LP3@150', 0, 0.05, 3, 150.0, 0.05),
            ('dc1@0.04 + HP0 + LP3@150', 0, 0.04, 3, 150.0, 0.04),
            ('dc1@0.03 + HP0 + LP3@150', 0, 0.03, 3, 150.0, 0.03),
            ('dc1@0.05 + HP0 + LP3@100', 0, 0.05, 3, 100.0, 0.05),
            ('dc1@0.05 + HP0 + LP2@100', 0, 0.05, 2, 100.0, 0.05),
            ('BW4[0.05,150] 仅（无dc1）', 4, 0.05, 4, 150.0, None),
            ('BW2[0.05,150] 仅（无dc1）', 2, 0.05, 2, 150.0, None),
        ]
        rows = []
        for name, o_hp, f_hp, o_lp, f_lp, f_dc in variants:
            secs = design(o_hp, f_hp, o_lp, f_lp, f_dc if f_dc else 0.0)
            y = run_design(secs, x)
            rs = read_all(y, FS, idx, a.amp, a.width)
            if not rs:
                continue
            ov = med(rs, ['ovs', 'near'])
            pl = med(rs, ['plat_near'])
            dp = med(rs, ['disp', 0.2])
            sl = med(rs, ['slope', 'win'])
            rows.append((name, ov, pl, dp, sl))
            print('  %-34s %8.2f%s %9.1f %9.1f%s %9.1f%s'
                  % (name[:34], ov, '✗' if ov >= 10 else '✓', pl,
                     dp, '✗' if dp > 100 else '✓', sl, '✗' if sl > 300 else '✓'))
        ok = [r for r in rows if r[1] < 10 and r[3] <= 100 and r[4] <= 300]
        print()
        print('  ⇒ 三条判据同时通过的链结构：%s'
              % (' ／ '.join(r[0] for r in ok) if ok else '**无**'))

    # ------------------------------------------------------------ 理论参照
    print()
    print('=' * 80)
    print('[4b] 理论参照：Butterworth 阶跃响应过冲（与截止频率无关，只与阶数有关）')
    print('=' * 80)
    try:
        from scipy.signal import butter as _bt, sosfilt as _sf
        for n in (1, 2, 3, 4, 5, 6):
            s = _bt(n, 100.0, btype='low', fs=FS, output='sos')
            xs = np.zeros(int(FS)); xs[int(FS * 0.2):] = 1.0
            ys = _sf(s, xs)
            ov = (ys.max() - ys[-1]) * 100
            print('  %d 阶: 过冲 %6.2f %%  = 3 mV × %.2f%% = %4.0f µV   %s'
                  % (n, ov, ov, ov * 30, '✗ 超 10%' if ov >= 10 else '✓'))
        print('  ⇒ **高端低通 ≤3 阶**才能满足 (a3)；本例高端为 4 阶 ⇒ 过冲天然 ≥10%。')
    except ImportError:
        print('  (缺 scipy)')

    if a.fig:
        make_fig(res, a, n_notch)


def make_fig(res, a, n_notch):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for _f in ('Microsoft YaHei', 'SimHei', 'DengXian', 'SimSun'):
        try:
            matplotlib.font_manager.findfont(_f, fallback_to_default=False)
            plt.rcParams['font.sans-serif'] = [_f]
            break
        except Exception:
            continue
    plt.rcParams['axes.unicode_minus'] = False

    if 'notch OFF' not in res:
        return
    y_off, r_off = res['notch OFF']
    y_on = res['notch ON'][0] if 'notch ON' in res else None
    i0 = r_off[0]['i0']
    w = int(a.width * FS)

    fig, ax = plt.subplots(2, 2, figsize=(15, 8.5))
    tt = (np.arange(i0 - int(.3 * FS), i0 + int(2.2 * FS)) - i0) / FS * 1000
    dd = np.arange(i0 - int(.3 * FS), i0 + int(2.2 * FS))
    ax[0, 0].plot(tt, y_off[dd] / 1000, lw=1.4, label='notch OFF')
    if y_on is not None:
        ax[0, 0].plot(tt, y_on[dd] / 1000, lw=1.0, alpha=.65, label='notch ON')
    ax[0, 0].axhline(0, c='k', lw=.5); ax[0, 0].axhline(3, c='gray', lw=.5, ls=':')
    ax[0, 0].axvline(0, c='r', lw=.6, ls='--'); ax[0, 0].axvline(100, c='r', lw=.6, ls='--')
    ax[0, 0].set_title('判据a 3mV/100ms 全脉冲'); ax[0, 0].set_xlabel('ms'); ax[0, 0].set_ylabel('mV')
    ax[0, 0].legend(); ax[0, 0].grid(alpha=.3)

    lo, hi = i0 - int(.01 * FS), i0 + int(.08 * FS)
    ax[0, 1].plot((np.arange(lo, hi) - i0) / FS * 1000, y_off[lo:hi], lw=1.8, label='notch OFF')
    if y_on is not None:
        ax[0, 1].plot((np.arange(lo, hi) - i0) / FS * 1000, y_on[lo:hi], lw=1.0, alpha=.65, label='notch ON')
    pk = r_off[0]['peak']
    ax[0, 1].axhline(r_off[0]['plat_near'], c='g', lw=.8, ls='--', label='近端平台 %.0fµV' % r_off[0]['plat_near'])
    ax[0, 1].axhline(a.amp, c='gray', lw=.8, ls=':', label='理想 3000µV')
    ax[0, 1].plot(6, pk, 'rv'); ax[0, 1].annotate('峰 %.0fµV' % pk, (6, pk), textcoords='offset points', xytext=(8, -12))
    ax[0, 1].set_title('上升沿放大（过冲来源）'); ax[0, 1].set_xlabel('ms'); ax[0, 1].set_ylabel('µV')
    ax[0, 1].legend(fontsize=8); ax[0, 1].grid(alpha=.3)

    lo, hi = i0 + w - int(.01 * FS), i0 + w + int(0.6 * FS)
    ax[1, 0].plot((np.arange(lo, hi) - (i0 + w)) / FS * 1000, y_off[lo:hi], lw=1.5)
    ax[1, 0].axhline(0, c='k', lw=.5); ax[1, 0].axhline(-100, c='r', lw=.6, ls='--')
    ax[1, 0].set_title('下降沿后 0.6 s（位移判限 ±100µV 红线）')
    ax[1, 0].set_xlabel('ms（0 = 脉冲终点）'); ax[1, 0].set_ylabel('µV'); ax[1, 0].grid(alpha=.3)

    lo, hi = i0 + w, min(i0 + w + int(4 * FS), len(y_off))
    ax[1, 1].plot((np.arange(lo, hi) - (i0 + w)) / FS, y_off[lo:hi], lw=1.3)
    ax[1, 1].axhline(0, c='k', lw=.5)
    ax[1, 1].axvline(3.18, c='b', lw=.8, ls='--', label='1 阶 τ=3.18s')
    ax[1, 1].set_title('下降沿后 4 s（恢复）'); ax[1, 1].set_xlabel('s'); ax[1, 1].set_ylabel('µV')
    ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3)

    fig.suptitle('T51  YY0885-2013 2.1.5 判据a  离线预测  profile IMP620_N2', fontsize=13)
    out = os.path.join(HERE, 'T51_YY0885判据a_离线预测_2026-09-20.png')
    fig.tight_layout(rect=[0, 0, 1, .96]); fig.savefig(out, dpi=130)
    print('\n[fig] ' + out)


if __name__ == '__main__':
    main()
