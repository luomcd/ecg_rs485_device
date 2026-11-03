#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T85 · 低频方波/窄脉冲实测判读（统一入口，可参数化复用）

用途
----
判读「低频方波 / 窄脉冲」这一类采集（典型：0.5 Hz rectangle pulse，3 mV），回答四件事：

  [0] 数据身份      —— 行率、时长、坏行、列型审计
  [1] 档位自证      —— 列⑥ dtcycles ⇒ 实际执行节数 ⇒ 反推档位（**板内固件是否为新档**）
  [2] 激励表征      —— 周期 / 脉宽 / 幅度（**用列② 链前零成本自证源幅度**）
  [3] 链在 f0 处的传递 —— 脉冲峰传函 col⑤/col② vs 设计 |H(f0)|
  [4] 脉冲后尾      —— 相位对齐包络（LP 40 Hz），多脉冲取中位（判据 a 的靶心区）
  [5] 离线复放      —— **同输入换链**：用本份实测 col② 当激励，分别过 {本档 / 对照档} 链，
                       与实测 col⑤ 逐点比 ⇒ 谁在跑一目了然（T80/T83 的忠实复放法）

为什么要「同输入换链」而不是「拿历史文件比」
--------------------------------------------
不同批次的**激励可能被改过**（本脚本 [2] 实测脉宽就是判据），跨批不可同表。
把本份的 col② 拿去跑两条链，才是唯一的可比口径（技能 §4.15）。

口径纪律（本脚本强制打印）
--------------------------
- 判据 a（GB 9706.247 a)）的激励是 **3 mV / 100 ms 方波**。本类数据若脉宽 ≠ 100 ms，
  **不得**输出 a1/a2/a3 —— 脚本会显式判「激励不符」并拒绝出判据值。
- 周期 2 s ⇒ 脉冲前/后基线窗重合 ⇒ **a1 循环定义**（T81）⇒ 即便脉宽对，也须 ≥10 s 间隔。

用法
----
  python _t85_lowfreq_square.py --file "xxx.txt" [选项]
  --prof MACRO    被测档位宏（默认 ECG_PROFILE_DUAL_A_PLUS_B）
  --dc FC         被测档 dc_block 截止频率（默认：从 coeffs.h 该档分支读 ECG_DC_BLOCK_FC_HZ_F）
  --ref MACRO     对照档（默认 ECG_PROFILE_DUAL_A_PLUS；给 none 关闭）
  --ref-dc FC     对照档 dc fc（默认同法从源码读）
  --f0 HZ         传递/尾判读的激励基频（默认 0.5）
  --fig           出图
  --out FILE      同时写文本
"""

import argparse
import importlib.util
import math
import os
import re
import sys

import numpy as np
from scipy.signal import butter, filtfilt, freqz

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)

FS = m.FS                 # 853.2423208191125 算法域
FS_T = 500.0              # 实测 txt 行率
UV = 0.1122               # 列码 → µV
DTC_K, DTC_B = 639.07, 1387.8   # T78 双锚点标定：dtc = b + k·节数

OUT = []


def p(s=''):
    print(s)
    OUT.append(s)


# ---------------------------------------------------------------- 载入

def load(path):
    """★双重坏行判据（T85 新增第 ② 条）：
      ① 列数 != 7（T56 立的规矩）
      ② **逐列量程** 合法（T85 新立）—— 串口丢/多字符会让某一列数值越出物理量程，
         但 **token 数仍是 7** ⇒ 只看列数**完全抓不到**（本份第 2458 行列②＝607372993，
         而 AFE 是 24 bit ⇒ 上限 8388607 ⇒ 该值物理不可能）。
      量程依据：①uint32 ②**24 bit 无符号 AFE 码 [0, 16777215]**（★实测：全仓 30+ 份采集里
                列② 的合法极值 ≤ 9.0e6，唯一样本越界者就是本份第 2458 行 607372993）
                ③int32 ④int16（−32768 是合法的"削顶哨兵"）⑤double ⑥uint16 ＋ ⑤ 形状须两位小数。
    列⑦ gdtover 是 %x ⇒ 只取前 6 列，绝不整行 float()（T78 曾静默丢 71% 行）。"""
    LIM = [(0, 0, 4294967295), (1, 0, 16777215), (2, -2147483648, 2147483647),
           (3, -32768, 32767), (4, -1e9, 1e9), (5, 0, 65535)]
    r, ncol, bad_rc, bad_w, where = [], {}, 0, 0, []
    for i, ln in enumerate(open(path, encoding='utf-8', errors='replace'), 1):
        q = ln.split()
        ncol[len(q)] = ncol.get(len(q), 0) + 1
        if len(q) != 7:   # ★T86：不只 <7 —— **>7 同样坏**（两行粘连/字段移位，T86 实证）
            if q:
                bad_rc += 1
            continue
        bad = not re.match(r'^-?\d+\.\d{2}$', q[4])       # ⑤ 形状
        v = [0.0] * 6
        if not bad:
            try:
                v = [float(t) for t in q[:6]]
            except ValueError:
                bad = True
        if not bad:
            for ci, lo, hi in LIM:                        # ② 量程
                if not (lo <= v[ci] <= hi):
                    bad = True
                    break
        if bad:
            bad_w += 1
            if len(where) < 8:
                where.append(i)
            continue
        r.append(v)
    return np.array(r), ncol, bad_rc, bad_w, where


def read_dc_fc(macro):
    """从 coeffs.h 该档分支读 ECG_DC_BLOCK_FC_HZ_F（没有则 None）。"""
    txt = open(m.COEFFS_H, encoding='utf-8').read()
    mm = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)'
                   r'([\s\S]*?)(?=#(?:elif|else|endif))', txt)
    if not mm:
        return None
    d = re.search(r'#define\s+ECG_DC_BLOCK_FC_HZ_F\s+([\d.]+)f', mm.group(1))
    return float(d.group(1)) if d else None


# ---------------------------------------------------------------- 链

def dc_block(x, fc, fs=FS):
    """与固件同式的一阶 HP：y[n]=x[n]-x[n-1]+r·y[n-1]，r=1-2πfc/fs。"""
    r = 1.0 - 2.0 * np.pi * fc / fs
    from scipy.signal import lfilter
    return lfilter([1.0, -1.0], [1.0, -r], x)


def build_chain(macro):
    """返回 (sections_after_notch, n_notch, n_bp)。notch 恒在链首。"""
    sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, macro)
    return sos[n_notch:], n_notch, len(sos) - n_notch


def replay(x500, secs, dc_fc):
    """col②(500Hz) → 上采样 853 → dc_block → BP 段(固件同式) → 下采样 500。
    ⚠ 直接跑会在**慢极点未收敛**上栽跟头（dc_block τ=7.96 s，21.8 s 文件跑不完 3τ）
      ⇒ 请用 replay_periodic()。本函数保留给"看形状"用。"""
    n = len(x500)
    t500 = np.arange(n) / FS_T
    t853 = np.arange(int(n * FS / FS_T)) / FS
    x853 = np.interp(t853, t500, x500 - np.median(x500))       # ★必去 VCM 直流（T83 坑②）
    y853 = m.run(secs, dc_block(x853, dc_fc, FS))
    return np.interp(t500, t853, y853)


def replay_periodic(x500, secs, dc_fc, P=1000, warm=40):
    """★严格周期激励 ⇒ 平铺 N 个周期先热身，再取稳态段。
    P = 每周期样本数（0.5 Hz @500 Hz ⇒ 1000）。warm=40 ⇒ A+B 热身 80 s = 10τ、A+ 25τ。
    返回与 x500 前 n_rep*P 个样本**同相位对齐**的稳态输出。"""
    n_rep = len(x500) // P
    if n_rep < 1:
        return replay(x500, secs, dc_fc)
    base = x500[:P] - np.median(x500)          # 取一个周期的模板（严格周期 ⇒ 平铺无缝）
    xx = np.tile(base, warm + n_rep)
    t500 = np.arange(len(xx)) / FS_T
    t853 = np.arange(int(len(xx) * FS / FS_T)) / FS
    x853 = np.interp(t853, t500, xx)
    y853 = m.run(secs, dc_block(x853, dc_fc, FS))
    y = np.interp(t500, t853, y853)
    return y[warm * P:warm * P + n_rep * P]


def H_chain(secs, dc_fc, f):
    """链在 f 处的复数增益（dc 项解析 ＋ BP 段 freqz）。"""
    w = 2.0 * np.pi * f / FS
    z = np.exp(1j * w)
    r = 1.0 - 2.0 * np.pi * dc_fc / FS
    Hd = (1.0 - z ** -1) / (1.0 - r * z ** -1)
    H = Hd
    for s in secs:
        b = s[:3]
        a = [1.0, s[3], s[4]]
        _, h = freqz(b, a, worN=[w])
        H = H * h[0]
    return H


# ---------------------------------------------------------------- 工具

def lp(x, fc, fs=FS_T, order=4):
    b, a = butter(order, fc / (fs / 2.0), 'low')
    return filtfilt(b, a, x)


def find_pulses(v, fs, frac=0.5, minw=0.005, min_gap=0.5):
    """阈值穿越法。阈值取「峰位分位」⇒ 对窄脉冲（占空比 <2%）仍落得进去。"""
    hi = float(np.percentile(v, 99.5))
    lo = float(np.median(v))
    thr = lo + frac * (hi - lo)
    up = v > thr
    st, en = [], []
    for k in range(1, len(v)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    pairs = []
    for s in st:
        e = next((x for x in en if x > s), None)
        if e is None:
            continue
        if (e - s) / fs < minw:
            continue
        if pairs and (s - pairs[-1][0]) / fs < min_gap:
            continue
        pairs.append((s, e))
    return pairs


def width_above(v, fs, fracs=(0.1, 0.5, 0.9)):
    """脉宽：取峰值 ±0.2 s 窗内，按峰值的 frac 阈值量宽（多脉冲中位）。"""
    pr = find_pulses(v, fs)
    out = {f: [] for f in fracs}
    for s, e in pr:
        sl = slice(max(0, s - 2), min(len(v), e + int(0.2 * fs)))
        seg = v[sl]
        pk = seg.max()
        lo = np.median(v)
        for f in fracs:
            thr = lo + f * (pk - lo)
            idx = np.where(seg > thr)[0]
            if len(idx) > 1:
                out[f].append((idx[-1] - idx[0] + 1) / fs)
    return {f: (float(np.median(v_)) if v_ else float('nan')) for f, v_ in out.items()}, pr


def phase_profile(v_env, kpk, fs, phases):
    """按峰值对齐取包络值（多脉冲求中位由调用方做）。"""
    return np.array([v_env[kpk + int(round(ph * fs))] if 0 <= kpk + int(round(ph * fs)) < len(v_env)
                     else np.nan for ph in phases])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--prof', default='ECG_PROFILE_DUAL_A_PLUS_B')
    ap.add_argument('--dc', type=float, default=None)
    ap.add_argument('--ref', default='ECG_PROFILE_DUAL_A_PLUS')
    ap.add_argument('--ref-dc', type=float, default=None, dest='ref_dc')
    ap.add_argument('--f0', type=float, default=0.5)
    ap.add_argument('--fig', action='store_true')
    ap.add_argument('--tag', default='T85', help='批次标签（图标题/默认文件名）')
    ap.add_argument('--fig-out', default=None, dest='fig_out', help='图输出路径（默认 <tag>_低频方波判读_<日期>.png）')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(HERE, a.file)
    name = os.path.basename(path)

    dc = a.dc if a.dc is not None else read_dc_fc(a.prof)
    ref_dc = a.ref_dc if a.ref_dc is not None else read_dc_fc(a.ref)
    secs, n_notch, n_bp = build_chain(a.prof)
    kinds = [m.section_kind(s) for s in secs]
    n_hp = kinds.count('HP')
    n_lp = kinds.count('LP')
    n_sec_tot = n_notch + n_bp
    if a.ref and a.ref.lower() != 'none':
        rsecs, rn, rbp = build_chain(a.ref)
        rkinds = [m.section_kind(s) for s in rsecs]
        r_hp = rkinds.count('HP')
        r_tot = rn + rbp
    else:
        rsecs, r_hp, r_tot = None, 0, 0

    arr, ncol, bad, bad_w, where = load(path)
    n = len(arr)
    t = np.arange(n) / FS_T
    c1, c2, c3, c4, c5, c6 = (arr[:, i] for i in range(6))
    x = c2 * UV                     # ★链前（AFE 原始码）
    y = c5 * UV                     # ★链后（设备实际输出）

    p('=' * 126)
    p('T85 · 低频方波/窄脉冲实测判读   file = %s' % name)
    p('  被测档 %s：%d 节（陷波 %d ＋ BP %d：%d×LP ＋ %d×HP）  dc_block fc = %s Hz'
      % (a.prof, n_sec_tot, n_notch, n_bp, n_lp, n_hp, dc))
    if rsecs is not None:
        p('  对照档 %s：%d 节（BP %d 节：%d×HP）  dc_block fc = %s Hz'
          % (a.ref, r_tot, rbp, r_hp, ref_dc))
    p('=' * 126)

    # ---------------- [0] 数据身份
    p('')
    p('[0] 数据身份')
    p('  行数 %d ｜ 时长 %.2f s ｜ 列数分布 %s' % (n, t[-1], dict(sorted(ncol.items()))))
    p('  坏行：列数≠7 计 %d（★**>7 也是坏行**：两行粘连/字段移位，T86 实证第 2267 行＝'
      '11 token ⇒ 列⑥ 被读成 20）｜ **列宽/量级异常计 %d**（★串口丢/多字符 ⇒ token 数仍＝7，'
      '只查列数抓不到）' % (bad, bad_w))
    if where:
        p('    列宽异常行号（前 8）：%s' % where)
        with open(path, encoding='utf-8', errors='replace') as fh:
            for i, ln in enumerate(fh, 1):
                if i in where[:3]:
                    p('      行%-6d %s' % (i, ln.rstrip()))
    # ★T88 时钟列自适应：列① 一旦被固件置成**字面 0**（`"%d ...", 0 /* idxraw */`），
    #   就毫无判据力 ⇒ 自动换用**列③ idxfilter（500 Hz 域，正常 Δ=1）**。
    #   ★源码注释自己写明：列① 在 **500 模式**增量 1~2 混合、**本就不能**当丢包判据
    #     （只有 raw853 模式 Δ 恒 1 才是首选判据）⇒ 500 模式下换列③ 是**零损失**的。
    #   🛑 反面：**`raw853 on` 时列③ 恒 0** ⇒ 那时两条判据**同时失效**（见 [0] 末提示）。
    clk = clk_fs = clk_nm = clk_max = None
    for cv, cfs, cnm, cmax in ((c1, FS, '列①', 5), (c3, FS_T, '列③', 3)):
        if float(cv.max()) - float(cv.min()) > 0:
            clk, clk_fs, clk_nm, clk_max = cv, cfs, cnm, cmax
            break
    if clk is None:
        clk_nm, row_rate, dclk = '无(全常数)', float('nan'), np.zeros(0, dtype=int)
        p('  🛑 列①/列③ **恒为常数** ⇒ 无任何时钟列 ⇒ 行率/丢行**不可判**（时间轴只能按标称 500 Hz 假定）')
    else:
        span = float(clk[-1] - clk[0])
        row_rate = clk_fs * n / span
        dclk = np.diff(clk).astype(int)
        u, cu = np.unique(dclk, return_counts=True)
        p('  时钟列 = **%s**（%s 域）｜ 跨度 %.0f ｜ **行率 = %.0f × %d / %.0f = %.1f Hz**'
          % (clk_nm, 'DRDY 853' if clk_nm == '列①' else 'idxfilter 500', span, clk_fs, n, span, row_rate))
        p('    增量分布 %s ｜ 中位 %.0f ｜ 单调不减 %s ｜ 增量 ≤0 或 >%g 处 %d'
          % (dict(zip(u.tolist()[:6], cu.tolist()[:6])), np.median(dclk),
             '✓' if bool(np.all(dclk >= 0)) else '✗',
             clk_max, int(np.sum((dclk <= 0) | (dclk > clk_max)))))
        p('    ⚠ 行率**只能用总跨度**求，不能用增量中位（列① 1/2 混合 ⇒ 中位=2 ⇒ 得 426.6 Hz 的错值）')
        if clk_nm == '列③':
            p('    ⚠ 本份**列① 恒 0**（固件已置字面 0）⇒ 判据已自动换列③；'
              '🛑 `raw853 on` 时列③ 恒 0 ⇒ 两条判据**同时失效**，那是唯一会「看不见丢行」的工况')
    p('  列② 基线(VCM) %.1f µV ｜ 列④ 削顶率 %.2f%% ｜ 列⑦ gdtover 为 hex，本脚本不解析（见 T78 坑）'
      % (np.median(c2) * UV, 100.0 * np.mean(np.abs(c4) >= 32767)))

    # ---------------- [1] 档位自证
    p('')
    p('[1] 档位自证（列⑥ dtcycles ⇒ 实际执行节数）')
    dtc = float(np.median(c6))
    n_exec = (dtc - DTC_B) / DTC_K
    p('  dtc 中位 %.0f（范围 %.0f~%.0f）｜ 模型 节数 = (dtc − %.0f)/%.2f = %.2f'
      % (dtc, c6.min(), c6.max(), DTC_B, DTC_K, n_exec))
    p('  ⇒ 参照：本档 notch OFF 应执行 BP **%d** 节（dtc≈%.0f）；对照档应为 **%d** 节（dtc≈%.0f）'
      % (n_bp, DTC_B + DTC_K * n_bp, rbp if rsecs is not None else 0,
         DTC_B + DTC_K * (rbp if rsecs is not None else 0)))
    verdict_prof = a.prof if abs(n_exec - n_bp) < abs(n_exec - (rbp if rsecs is not None else 999)) else a.ref
    p('  ⇒ 判定：实测最接近 **%s**（偏差 %.2f 节）—— %s'
      % (verdict_prof, abs(n_exec - (n_bp if verdict_prof == a.prof else rbp)),
         '板内跑的就是落码后的档 ✓' if verdict_prof == a.prof else '⚠ 板内不是预期档！'))

    # ---------------- [2] 激励表征
    p('')
    p('[2] 激励表征（列② ＝ 链前，零成本自证源幅度）')
    w2, pr2 = width_above(c2 * UV, FS_T)
    w5, pr5 = width_above(c5 * UV, FS_T)
    per = float(np.median(np.diff([s for s, e in pr2])) / FS_T) if len(pr2) > 1 else float('nan')
    base2, base5 = float(np.median(c2 * UV)), float(np.median(c5 * UV))
    amp2 = float(np.median([x[s:e + 1].max() for s, e in pr2])) - base2 if pr2 else float('nan')
    amp5 = float(np.median([y[s:e + 1].max() for s, e in pr5])) - base5 if pr5 else float('nan')
    p('  脉冲段数 %d ｜ 周期 %.4f s（%.4f Hz）' % (len(pr2), per, 1.0 / per))
    p('  脉宽（10%%/50%%/90%% 峰值口径, 列②） %.1f / %.1f / %.1f ms'
      % (1e3 * w2[0.1], 1e3 * w2[0.5], 1e3 * w2[0.9]))
    p('  脉宽（10%%/50%%/90%% 峰值口径, 列⑤） %.1f / %.1f / %.1f ms'
      % (1e3 * w5[0.1], 1e3 * w5[0.5], 1e3 * w5[0.9]))
    p('  幅度：列②峰 %+.1f µV（占标称 3 mV 的 %.1f%%）｜列⑤峰 %+.1f µV' % (amp2, 100 * amp2 / 3000.0, amp5))
    p('  基线：列② %+.1f µV ｜ 列⑤ %+.1f µV（差 %+.1f µV ⇒ 链的直流落点）'
      % (base2, base5, base5 - base2))
    P_PRD = int(round(per * FS_T))            # 每周期样本数（严格周期 ⇒ 复放热身用）
    NLIM = len(x) // P_PRD * P_PRD            # 复放产物长度（截到整周期）
    # ★丢行检查（T85 新立）：列①/列③ 是否连续；脉冲间隔是否均匀
    d3 = np.diff(c3).astype(int)
    kst = np.array([s for s, e in pr2])
    dk = np.diff(kst)
    odd = np.where(np.abs(dk - np.median(dk)) > 5)[0]
    p('  ★丢行/撕裂检查（T85 新立 / T88 自适应）：列③ 增量≠1 处 %d 个（%s）'
      '｜时钟列 %s 增量 ≤0 或 >%g 处 %d 个'
      % (int(np.sum(d3 != 1)), np.unique(d3[d3 != 1])[:8].tolist(), clk_nm, clk_max,
         int(np.sum((dclk <= 0) | (dclk > clk_max))) if clk is not None else -1))
    p('     （时钟列必须**单调不减**；增量 ≤0 = 该列被串口截断过，增量超限 = 主循环没跟上丢样本）')
    p('    脉冲间隔（样本）%s ⇒ 非均匀处 %s'
      % (dk.tolist(), (odd + 1).tolist() if len(odd) else '无'))
    if len(odd):
        p('    🛑 第 %s 个间隔比中位少 %d 样本（%.0f ms）⇒ **该处丢了 ~%d 行数据**；'
          '⇒ 任何"等间隔脉冲""按固定网格对齐"的假设在此失效（本轮复放曾因此被抹平）'
          % ((odd + 1).tolist(), int(np.median(dk) - dk[odd[0]]),
             1000 * (np.median(dk) - dk[odd[0]]) / FS_T,
             int((np.median(dk) - dk[odd[0]]) / (np.median(dclk) if clk is not None else 1))))

    # ---------------- [3] 传递
    p('')
    p('[3] 链在 %.3f Hz 处的传递' % a.f0)
    r_meas = amp5 / amp2 if amp2 else float('nan')
    Hs = H_chain(secs, dc, a.f0)
    # ★平台比（脉顶 +5~+15 ms 中位）——比"峰比"稳：不受 500 Hz 欠采样与 AFE 边沿平滑影响
    PLA = [0.005, 0.010, 0.015]
    kk_pk = [s + int(np.argmax(y[s:e + 1])) for s, e in pr5]
    pl_x = np.median([np.median([x[k + int(round(q * FS_T))] for q in PLA]) - np.median(x) for k in kk_pk])
    pl_y = np.median([np.median([y[k + int(round(q * FS_T))] for q in PLA]) for k in kk_pk])
    p('  ★平台比（脉顶 +5/+10/+15 ms 中位）col⑤/col② = %.1f/%.1f = **%.2f%%** ← 主口径'
      % (pl_y, pl_x, 100 * pl_y / pl_x))
    p('  峰比（受 500 Hz 欠采样 ＋ AFE 边沿平滑限制，标称 ±3%%）col⑤/col② = %.1f/%.1f = %.2f%%'
      % (amp5, amp2, 100 * r_meas))
    p('  设计 |H(%.3f Hz)|（本档）= %.2f%%（%.4f dB）' % (a.f0, 100 * abs(Hs), 20 * math.log10(abs(Hs))))
    if rsecs is not None:
        Hr = H_chain(rsecs, ref_dc, a.f0)
        p('  设计 |H(%.3f Hz)|（对照档）= %.2f%%（%.4f dB）⇒ 两档在 f0 差 %.4f dB'
          % (a.f0, 100 * abs(Hr), 20 * math.log10(abs(Hr)),
             20 * math.log10(abs(Hs) / abs(Hr))))
    p('  ⇒ 平台比与设计差 %.2f pp（🛑 **含占空比效应，不是链路损耗**，见下）'
      % (100 * pl_y / pl_x - 100 * abs(Hs)))
    # ★T86 新立：周期脉冲经直流阻断链 ⇒ 平台期必须分摊基线期的负偏置
    #   平台 ≈ +A(1−d)|H| ／ 基线 ≈ −A·d|H| ／ 峰谷比 → (1−d)/d  （d ＝占空比）
    #   ⇒ 「平台比 col⑤/col②」天然被挤掉 ≈ d（本份 d=5% ⇒ −4.39 pp 里 5.0 pp 是它）
    #   ⇒ **正确净传递口径 ＝ 峰—基线峰差**：(平台 − 脉后稳态)/源幅度（d 与直流落点自动抵消）
    b_line = float(np.median(y))
    d_cyc = w2[0.5] / per
    pb = pl_y - b_line
    p('  ★★占空比 d = %.4f（%.0f ms / %.0f ms）⇒ 周期脉冲的**必然**落点：平台 ≈ +A(1−d)|H| = %.1f'
      '（实测 %.1f）／基线 ≈ −A·d|H| = %.1f（实测 %.1f）｜峰谷比 %.2f vs (1−d)/d = %.2f'
      % (d_cyc, 1000 * w2[0.5], 1000 * per, pl_x * (1 - d_cyc) * abs(Hs), pl_y,
         -pl_x * d_cyc * abs(Hs), b_line, pl_y / abs(b_line), (1 - d_cyc) / d_cyc))
    p('  ★★峰—基线峰差口径（T86 修正 · **不受占空比影响**的净传递）：'
      '(%.1f − (%.1f)) = %.1f µV / 源 %.1f ⇒ **%.2f%%** vs 设计 %.2f%% ⇒ 差 %+.2f pp'
      % (pl_y, b_line, pb, amp2, 100 * pb / amp2, 100 * abs(Hs), 100 * pb / amp2 - 100 * abs(Hs)))
    p('  频响抽样（本档 |H| %%）：' + '  '.join(
        '%.2fHz %.2f' % (f, 100 * abs(H_chain(secs, dc, f)))
        for f in (0.05, 0.1, 0.2, 0.5, 0.67, 1, 5, 10, 40, 100, 150, 250)))

    # ---------------- [4] 脉冲后尾
    p('')
    PHS = [0.000, 0.005, 0.010, 0.015, 0.020, 0.024, 0.030, 0.040, 0.060, 0.080,
           0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 0.80, 1.00, 1.20, 1.50, 1.80, 1.95]
    yenv = lp(y, 40.0)
    xenv = lp(x, 40.0)
    kpk = [s + int(np.argmax(y[s:e + 1])) for s, e in pr5]
    kpk = [k for k in kpk if k - int(0.05 * FS_T) > 0 and k + int(2.0 * FS_T) < NLIM]
    # ★窄脉冲下 LP40 包络在脉沿附近有 ~±10% Gibbs ⇒ 尾表一律取**原始值跨脉冲中位**（不用包络）
    RY = np.array([phase_profile(y, k, FS_T, PHS) for k in kpk])
    RX_ = np.array([phase_profile(x, k, FS_T, PHS) for k in kpk])
    MY, MX = np.median(RY, axis=0), np.median(RX_, axis=0)
    YQ1, YQ3 = np.percentile(RY, 25, axis=0), np.percentile(RY, 75, axis=0)
    p('[4] 脉冲后尾（★原始值跨脉冲中位，不用包络 —— 窄脉冲(<30 ms)下 LP40 包络有 ±10%% Gibbs）')
    p('     相位以 col⑤ 峰为 0；%d 段取中位（列② 先减自身中位去 VCM）' % len(kpk))
    p('  %-9s %13s %13s %13s' % ('相位 (s)', 'col② 链前', 'col⑤ 链后', 'col⑤ IQR 半宽'))
    for i, ph in enumerate(PHS):
        p('  %+9.3f %+13.1f %+13.1f %13.1f'
          % (ph, MX[i] - np.median(x), MY[i], (YQ3[i] - YQ1[i]) / 2.0))
    p('  ⇒ 关键读数：脉冲前一秒基线 %+.1f µV ｜ 脉后 +1.5~+1.95 s 稳态 %+.1f µV ｜ 脉后最大下冲 %+.1f µV'
      % (MY[PHS.index(1.95)], np.median([MY[PHS.index(1.50)], MY[PHS.index(1.80)]]),
         MY[PHS.index(0.040)]))
    p('  ⇒ ★链前后尾幅对比（判据 a 的靶心：±100 µV）：'
      'col② 下冲 %+.1f µV ／ col⑤ 下冲 %+.1f µV ⇒ 链自身只贡献 %+.1f µV'
      % (MX[PHS.index(0.040)] - np.median(x), MY[PHS.index(0.040)],
         MY[PHS.index(0.040)] - (MX[PHS.index(0.040)] - np.median(x))))

    # ---------------- [5] 离线复放（同输入换链）
    p('')
    p('[5] 离线复放 —— 用本份实测 col② 当激励，同输入换链（唯一可比口径）')
    ym = y[:NLIM]
    p('  复放法＝严格周期平铺热身（周期 %d 样本＝%.3f s，热身 40 周期=%.0f s）⇒ 慢极点已收敛'
      % (P_PRD, P_PRD / FS_T, 40 * P_PRD / FS_T))
    yb = replay_periodic(x, secs, dc, P=P_PRD)
    p('  本档 %s（dc=%s） vs 实测 col⑤：相关 %.4f ｜ RMS %.1f µV ｜ 峰 %.1f / 实测 %.1f µV（%.1f%%）'
      % (a.prof, dc, float(np.corrcoef(yb, ym)[0, 1]), float(np.sqrt(np.mean((yb - ym) ** 2))),
         yb.max(), ym.max(), 100 * yb.max() / ym.max()))
    rows = [(a.prof, dc, yb)]
    if rsecs is not None:
        yr = replay_periodic(x, rsecs, ref_dc, P=P_PRD)
        p('  对照档 %s（dc=%s） vs 实测 col⑤：相关 %.4f ｜ RMS %.1f µV ｜ 峰 %.1f µV（%.1f%%）'
          % (a.ref, ref_dc, float(np.corrcoef(yr, ym)[0, 1]),
             float(np.sqrt(np.mean((yr - ym) ** 2))), yr.max(), 100 * yr.max() / ym.max()))
        rows.append((a.ref, ref_dc, yr))
    p('')
    p('  同输入换链的「尾」对比（原始值跨**各自脉冲**中位，µV）')
    p('  ⚠ 每条曲线都按**自己的脉冲峰**对齐 —— 本文件有丢行（脉冲间隔非均匀），'
      '若共用同一套峰位会把模型脉冲抹成一片（T85 坑）')
    envs = [(n_, lp(yv, 40.0)) for n_, d_, yv in rows]
    hdr = '  %-9s %12s %12s' % ('相位 (s)', '实测 col⑤', a.prof[:12])
    spec = '  %+9.3f %+12.1f %+12.1f'
    if rsecs is not None:
        hdr += ' %12s' % a.ref[:12]
        spec += ' %+12.1f'
    p(hdr)

    def own_profile(v, smooth=False):
        """按 v 自己的脉冲峰对齐取相位剖面（每条曲线自洽）。
        smooth=True 先过 LP 40 Hz —— 消掉 250 Hz 交替纹（该纹与 2 ms 相位网格相干，
        取中位消不掉），**实测与模型同样处理** ⇒ 公平；只用于尾段（远离 3 mV 脉沿，无 Gibbs）。"""
        w = lp(v, 40.0) if smooth else v
        pr = find_pulses(v, FS_T)
        kk = [s + int(np.argmax(v[s:e + 1])) for s, e in pr]
        kk = [k for k in kk if k - int(0.05 * FS_T) > 0 and k + int(2.0 * FS_T) < len(v)]
        if not kk:
            return np.full(len(PHS), np.nan)
        return np.median(np.array([phase_profile(w, k, FS_T, PHS) for k in kk]), axis=0)

    profs = [own_profile(y)] + [own_profile(yv) for _, _, yv in rows]
    for i, ph in enumerate(PHS):
        p(spec % tuple([ph] + [float(pr_[i]) for pr_ in profs]))
    # 幅值/振铃指标
    p('')
    p('  ★★ 尾段逐点差（判据 a 的靶心区；越像者即板内真链）')
    # ★尾窗必须**按本份自己的脉宽**定起点（24 ms 与 96 ms 两份不能共用一个固定相位窗）
    t0 = 1.15 * w5[0.5]
    TAIL = [i for i, q in enumerate(PHS) if t0 <= q <= 0.400]
    PLAT = [i for i, q in enumerate(PHS) if 0.005 <= q <= 0.020]
    sp = [own_profile(y, smooth=True)] + [own_profile(yv, smooth=True) for _, _, yv in rows]
    p('  尾窗 = [+%.0f ms, +400 ms]（＝脉宽 %.0f ms ×1.15 起，避开脉沿对齐误差）'
      % (1000 * t0, 1000 * w5[0.5]))
    p('  %-30s %13s %13s' % ('', '尾段 RMS µV', '尾段 max|Δ| µV'))
    for (nm_, _, _), pf in zip(rows, sp[1:]):
        dd = np.array([pf[i] - sp[0][i] for i in TAIL], dtype=float)
        p('  %-30s %13.1f %13.1f' % (nm_[:30], float(np.sqrt(np.mean(dd ** 2))), float(np.max(np.abs(dd)))))
    p('    （口径＝实测与模型**同过 LP 40 Hz**；判读：尾段 RMS 小者胜 —— 4 阶 HP 的额外下冲'
      '≈ −A·w·2π·Σf，A+（Σf 0.1807）比 A+B（0.02）大 9 倍）')
    p('  平台区（+5~+20 ms）逐点差：' + '  '.join(
        '%s %.1f µV' % (nm_[:14], float(np.sqrt(np.mean([(pf[i] - profs[0][i]) ** 2 for i in PLAT]))))
        for (nm_, _, _), pf in zip(rows, profs[1:])))
    rows2 = [('实测 col⑤', y)] + [(n_, yv) for n_, d_, yv in rows]
    def metrics(v):
        pr = find_pulses(v, FS_T)
        kk = [s + int(np.argmax(v[s:e + 1])) for s, e in pr]
        kk = [k for k in kk if k + int(2.0 * FS_T) < len(v)]
        if not kk:
            return (float('nan'),) * 3
        mins = [float(np.min(v[k + int(0.03 * FS_T):k + int(0.5 * FS_T)])) for k in kk]
        st = [float(np.median(v[k + int(1.5 * FS_T):k + int(1.95 * FS_T)])) for k in kk]
        pks = [float(v[k]) for k in kk]
        return np.median(pks), np.median(mins), np.median(st)
    p('  %-30s %12s %14s %12s' % ('', '峰 µV', '脉后最小 µV', '尾稳态 µV'))
    for nm_, v in rows2:
        pk, mn_, st_ = metrics(v)
        p('  %-30s %12.1f %14.1f %12.1f' % (nm_[:30], pk, mn_, st_))

    # ---------------- [6] 口径纪律
    p('')
    p('[6] 判据口径纪律（本脚本强制）')
    w50 = 1e3 * w2[0.5]
    if abs(w50 - 100.0) > 15.0:
        p('  🛑 激励脉宽 %.0f ms ≠ 判据 a 规定的 **100 ms** ⇒ 本份**不得**用于判 a1/a2/a3。' % w50)
        p('     本份的用途：低频传递 / 脉冲后尾形状 / 档位自证 —— 不是判据 a 的合格素材。')
    else:
        p('  脉宽 %.0f ms ≈ 100 ms ⇒ 激励符合判据 a；但仍须查周期：' % w50)
    if per < 10.0:
        p('  🛑 周期 %.2f s < 10 s ⇒ 脉冲前/后基线窗重合 ⇒ a1 循环定义（T81）⇒ 只能读「形状/尾」，'
          '不能读 a1 数值。' % per)
        p('      ★T85/T86 定理：周期脉冲下两链的**周期均值都必须为 0**（HP 硬约束）⇒ 尾**面积**恒为'
          ' −A·w、**与 Σf 无关** ⇒ 判别量在**尾起点深度 ∝ Σf**。')
        p('      拿 2 s 数据读「脉冲后稳态基线」会把 A+/A+B 的优劣**读反**（A+ 尾终点反而更小 ——'
          '因为它回归更快、但下冲峰更深）。本份的**合格用途**：档位自证／传递／同输入换链。')
    else:
        p('  ✅ 周期 %.2f s ≥ 10 s ⇒ a1 稳态口径可用。' % per)

    if a.fig:
        make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, a.prof, a.ref, PHS, MY, envs, a.f0,
                 a.tag, a.fig_out)

    if a.out:
        with open(os.path.join(HERE, a.out), 'w', encoding='utf-8') as f:
            f.write('\n'.join(OUT) + '\n')
        print('\n[写入 %s]' % a.out)


def make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, pname, rname, PHS, MY, envs, f0,
             tag='T85', fig_out=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    k0 = kpk[len(kpk) // 2]
    sl = slice(k0 - int(0.1 * FS_T), k0 + int(0.5 * FS_T))
    tt = (np.arange(k0 - int(0.1 * FS_T), k0 + int(0.5 * FS_T)) - k0) * 1000.0 / FS_T
    ax[0, 0].plot(tt, x[sl], lw=1.0, color='#888', label='列② 链前(AFE 原码)')
    ax[0, 0].plot(tt, y[sl], lw=1.6, color='#c00', label='列⑤ 链后(输出)')
    ax[0, 0].axvline(0, color='k', ls=':', lw=0.8)
    ax[0, 0].set_title('脉冲全景（对齐峰值）'); ax[0, 0].set_xlabel('ms'); ax[0, 0].set_ylabel('µV')
    ax[0, 0].legend(fontsize=8); ax[0, 0].grid(alpha=.3)

    ax[0, 1].plot(tt, x[sl], 'o-', ms=3, lw=1.0, color='#888', label='列②')
    ax[0, 1].plot(tt, y[sl], 's-', ms=3, lw=1.4, color='#c00', label='列⑤')
    ax[0, 1].set_xlim(-10, 45); ax[0, 1].set_title('上升/下降沿放大（±45 ms）')
    ax[0, 1].set_xlabel('ms'); ax[0, 1].grid(alpha=.3); ax[0, 1].legend(fontsize=8)

    ax[1, 0].plot([p_ for p_ in PHS], MY, 'o-', color='#c00', label='实测 col⑤ 包络')
    for (nm_, ev) in envs:
        ax[1, 0].plot(PHS, [np.median([ev[k + int(round(p_ * FS_T))] for k in kpk]) for p_ in PHS],
                      '--', lw=1.2, label=nm_[:22])
    ax[1, 0].axhline(100, color='k', ls=':', lw=.8); ax[1, 0].axhline(-100, color='k', ls=':', lw=.8)
    ax[1, 0].set_xlabel('相位 s'); ax[1, 0].set_ylabel('µV'); ax[1, 0].set_title('脉冲后尾（±100 µV 判限参考）')
    ax[1, 0].legend(fontsize=8); ax[1, 0].grid(alpha=.3)

    fs_ = np.logspace(-2, 2.45, 400)
    Hs = np.array([abs(H_chain(secs, dc, f)) for f in fs_])
    ax[1, 1].semilogx(fs_, 20 * np.log10(Hs), lw=1.6, color='#c00', label=pname[:24])
    if rsecs is not None:
        Hr = np.array([abs(H_chain(rsecs, ref_dc, f)) for f in fs_])
        ax[1, 1].semilogx(fs_, 20 * np.log10(Hr), '--', lw=1.4, color='#06c', label=rname[:24])
    ax[1, 1].axvline(f0, color='k', ls=':', lw=.8)
    ax[1, 1].set_xlabel('Hz'); ax[1, 1].set_ylabel('dB'); ax[1, 1].set_title('整链 |H| 低频段（设计值）')
    ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3, which='both')
    fig.suptitle('%s 低频方波判读 · %s' % (tag, name[:70]), fontsize=11)
    fig.tight_layout()
    o = fig_out or os.path.join(HERE, '%s_低频方波判读_2026-09-21.png' % tag)
    fig.savefig(o, dpi=130)
    print('[图 %s]' % os.path.basename(o))


if __name__ == '__main__':
    main()
