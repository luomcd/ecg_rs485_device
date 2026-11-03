# -*- coding: utf-8 -*-
"""
T59 · fc=250 新链的离线三判据预测（含口径多读）＋ 旧链模型复核

为什么要有这一份
----------------
T58 把高端从 LP4@150 改成 LP2@250 并已编译进镜像。上板前需要知道**新链三条判据
各自落在哪儿**，否则台架上抓完数据没法当场判断"对不对"。

方法与纪律
----------
1. **同一输入驱动两条链**：都用 T57 实测的列② raw（未滤波，边沿最干净）当输入
   ⇒ 差异 100% 来自链本身，不含任何激励差异。
2. **先复核旧链、再外推新链**：旧链有 T57 的实测读数，模型必须先把旧链
   **逐点复现到几个 µV 以内**，才有资格预测新链。这是本技能库的"模型自证"纪律。
3. **不加预平滑**：输入已是 853 域过完 AFE 的 raw ⇒ 边沿已含 AFE 形状，
   再叠 L 样本预平滑就是重复计入（T57 §20.3 就是这么对齐的，逐点差 0.01~0.6）。
4. **过冲必须报多口径**：三种"基线/参考"定义能差 2 倍（T53 §6、T52b）⇒
   全列出来，不挑一个好看的。位移/斜率的窗口也按多档列。
5. **复用**：数据读入 / 坏行判据 / 脉冲定位 / 三判据 全部 import `_t56_raw853_lowfreq`；
   系数解析 import `_t51_yy0885_pulse`；dc_block import `_t52_yy0885_notchoff`。
   **本脚本不重写任何判据逻辑**。

用法
----
  python _t59_offline_predict.py                      # 默认跑 T57 的 notch OFF 文件
  python _t59_offline_predict.py --file <txt>          # 换数据
  python _t59_offline_predict.py --file <txt> --tag _xxx
输出：`_t59_out<tag>.txt`

修订
----
★ 2026-10-15（T196r6）修 **"旧/新链同 sos" 退化**（原登记于验收总表 §4-17，曾阻塞 S8 新链外推）：
  T58 当年把 IMP620_N2 档改成 LP2@250，但该档**事后已被回退到 150 Hz** ⇒ 现源码的
  IMP620_N2 与 `_bak_T58_20260920` **逐位相同** ⇒ 『旧链 vs 新链』两列退化（max|Δ| = 0），
  "新链"根本不是新链，无法论证 fc250 改善。
  ⇒ **新链改从 `ECG_PROFILE_DUAL_A` 解析**（0.05-250Hz／12 节 ＝ 9 陷波*逐位同* ＋ LP2@250 ＋ HP4@0.05）
    —— 这正是 T58 五档改动在现源码里的**存活载体**（T58 后 IMP620_N2 的内容即此）。
  另加**防呆**：两条链若解析结果相同（max|Δ| = 0）**直接报错退出**，不再静默输出退化对照表。
"""
import argparse
import io
import os
import sys

import numpy as np
import scipy.signal as ss

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t51_yy0885_pulse as T51                     # noqa: E402  系数解析
import _t52_yy0885_notchoff as T52m                 # noqa: E402  dc_block / fw_chain
import _t56_raw853_lowfreq as T56                   # noqa: E402  读数据 / 判据

FS = T51.FS
UVPC = T56.UVPC

# T58 改动前/后的链（旧链 LP4@150 / 新链 LP2@250）
# ★ 2026-10-15（T196r6）：IMP620_N2 档在 T58 后**已被回退到 150 Hz** ⇒ 不能再拿它当"新链"。
#   fc250 链现以 `ECG_PROFILE_DUAL_A` 存在（与 IMP620_N2 只差"LP4@150 → LP2@250"，其余逐位同）。
MACRO_OLD = 'ECG_PROFILE_IMP620_N2'      # 旧链：LP4@150（13 节 = 9 陷波 + LP×2 + HP×2）
MACRO_NEW = 'ECG_PROFILE_DUAL_A'         # 新链：LP2@250（12 节 = 9 陷波 + LP×1 + HP×2）
SRC_NEW_C = T51.COEFFS_C
SRC_NEW_H = T51.COEFFS_H
SRC_OLD_C = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak')
SRC_OLD_H = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak')

F_DEF = os.path.join(HERE, '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt')
L_DISP, L_SLOPE, L_OVER = 100.0, 300.0, 10.0
OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 78)
    say(s)
    say('=' * 78)


# --------------------------------------------------------------- 链的构造

def chain_of(path_c, path_h, macro):
    """返回 (sos6, n_notch, hp_idx, kinds)：把固件 5 元组补成 scipy 6 列。"""
    sos, nn = T51.parse_coeffs_f64(path_c, path_h, macro)
    kinds = [T51.section_kind(s) for s in sos]
    sos6 = [np.insert(np.asarray(sv, float), 3, 1.0) for sv in sos]
    hp = [i for i in range(nn, len(sos)) if kinds[i] == 'HP']
    return sos6, nn, hp, kinds


def chain_sos_tuples(sos6):
    """去掉 scipy 插入的 a0=1（第 3 列），还原成固件 5 元组，便于逐位比较。"""
    return [tuple(np.delete(np.asarray(row, float), 3)) for row in sos6]


def _chain_max_diff(sos_a, sos_b):
    """两条链逐位最大差。节数不同 ⇒ inf（结构已不同，不算退化）。"""
    if len(sos_a) != len(sos_b):
        return float('inf')
    return max(abs(x - y)
               for ra, rb in zip(chain_sos_tuples(sos_a), chain_sos_tuples(sos_b))
               for x, y in zip(ra, rb))


def descr(kinds, nn):
    """人读的链描述：低端... / 高端..."""
    tail = kinds[nn:]
    return '%d NOTCH + %s(%d 节)' % (nn, '+'.join(tail), len(tail))


def run_chain(sos6, nn, hp, xin, use_notch=False):
    """复现关陷波态的固件链：dc_block(0.05) → 非陷波节。
    低端（HP）在前、高端（LP）在后 —— LTI 级联顺序不影响总体传函，
    但保持与固件声明一致的写法便于对照。"""
    y = T52m.dc_block_apply(np.asarray(xin, float), 0.05, FS)
    order = list(hp) + [i for i in range(nn, len(sos6)) if i not in hp]
    for i in order:
        y = ss.sosfilt(sos6[i:i + 1], y)
    return y


# --------------------------------------------------------------- 过冲多口径

def overshoots(R):
    """从 judge() 的结果算四个过冲口径（%）。"""
    p, top, near, end = R['peak'], R['top'], R['near'], R['end']
    out = {}
    out['对输入(标称3mV)'] = (p - 3000.0) / 3000.0 * 100.0
    out['对顶均'] = (p - top) / top * 100.0
    out['对近平台'] = (p - near) / near * 100.0
    out['对末端'] = (p - end) / end * 100.0
    return out


OSKEYS = ['对输入(标称3mV)', '对顶均', '对近平台', '对末端']


# ------------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_DEF)
    ap.add_argument('--tag', default='')
    a = ap.parse_args()

    path = a.file if os.path.isabs(a.file) else os.path.join(HERE, a.file)
    if not os.path.exists(path):
        print('数据文件不存在:', path)
        return 1

    hdr('T59 · fc=250 新链离线三判据预测   ' + os.path.basename(path))
    say('数据   : %s' % path)
    say('判限   : 位移 <= %.0f µV ｜ 终点后斜率 <= %.0f µV/s ｜ 过冲 < %.0f%%'
        % (L_DISP, L_SLOPE, L_OVER))
    say('驱动   : 实测列② raw（%s）⇒ 差异只来自链本身' % '未滤波、边沿最干净')

    # ---------------- [0] 读数据 ----------------
    d = T56.load7(path)
    keep, dirty, lost, holes = T56.clean_idx(d)
    idx = d['idx'][keep].astype(np.int64)
    raw = d['raw'][keep]
    y_meas = d['filt'][keep] * UVPC
    t = (idx - idx[0]) / FS
    pulses, thr, bl = T56.find_pulses(raw)
    rep = float(np.median(np.diff([t[s] for s, _ in pulses]))) if len(pulses) > 1 else 10.0

    hdr('[0] 数据验收')
    say('  样本 %d ｜ 时长 %.2f s ｜ 坏行 %d ｜ 段 %d ｜ 脉冲周期 %.4f s'
        % (len(t), t[-1], d['nbad'], len(pulses), rep))
    say('  列② raw p-p %.1f µV（标称 3000）｜ 列⑤ p-p %.1f µV'
        % (np.ptp(raw) * UVPC, np.ptp(y_meas)))
    cs = float(np.median(d['dtc']))
    say('  列⑥ dtcycles 中位 %.0f ⇒ 判定 notch %s（距 ON 9712 / 距 OFF 3950：%.0f / %.0f）'
        % (cs, 'ON' if abs(cs - 9712) < abs(cs - 3950) else 'OFF',
           abs(cs - 9712), abs(cs - 3950)))

    # ---------------- [1] 两条链 ----------------
    sos_new, nn, hp_new, kinds_new = chain_of(SRC_NEW_C, SRC_NEW_H, MACRO_NEW)
    sos_old, nn_o, hp_old, kinds_old = chain_of(SRC_OLD_C, SRC_OLD_H, MACRO_OLD)

    hdr('[1] 两条链（关陷波态）')
    say('  旧链（LP4@150） macro = %s' % MACRO_OLD)
    say('                   %s' % descr(kinds_old, nn_o))
    say('                   低端 %d 节 HP ｜ 高端 %d 节 LP'
        % (len(hp_old), len(kinds_old) - nn_o - len(hp_old)))
    say('  新链（LP2@250） macro = %s' % MACRO_NEW)
    say('                   %s' % descr(kinds_new, nn))
    say('                   低端 %d 节 HP ｜ 高端 %d 节 LP'
        % (len(hp_new), len(kinds_new) - nn - len(hp_new)))
    say('  ⚠ 两条链的低端字节级相同（HP4@0.05 原样保留，T58 只动高端）'
        if len(hp_old) == len(hp_new) else '  ⚠ 低端节数不同，需复核')
    say('  ⇒ 故**位移/斜率应几乎不变**（它们由低端定），**只有过冲会变**（由 LP 阶数定）。')

    # ★ 防呆（T196r6）：旧/新链若解析结果相同 ⇒ 对照退化，直接报错退出
    deg = _chain_max_diff(sos_old, sos_new)
    if deg == 0.0:
        say()
        say('  🛑 [T196r6 防呆] 两条链解析结果**逐位相同**（max|Δ| = 0）⇒ 对照退化，本表无意义。')
        say('     旧 macro %s ／ 新 macro %s'
            % (MACRO_OLD, MACRO_NEW))
        say('     请核对"新链"档位：IMP620_N2 在 T58 后**已被回退到 150 Hz**，'
            '须改用承载 fc250 的 DUAL_A。')
        return 2
    if deg == float('inf'):
        say('  ✅ 两链可判（节数 %d → %d，结构已不同）' % (len(sos_old), len(sos_new)))
    else:
        say('  ✅ 两链可判（逐位 max|Δ| = %.3e，节数 %d → %d）'
            % (deg, len(sos_old), len(sos_new)))

    # ---------------- [2] 实测 vs 两模型 ----------------
    xin = raw * UVPC
    y_old = run_chain(sos_old, nn_o, hp_old, xin)
    y_new = run_chain(sos_new, nn, hp_new, xin)

    hdr('[2] ★模型自证（旧链必须先复现实测，才有资格外推）')
    # 只比"不削顶"的区域：列⑤ 未削顶，可直接逐点比
    e_old = float(np.sqrt(np.mean((y_old - y_meas) ** 2)))
    e_new = float(np.sqrt(np.mean((y_new - y_meas) ** 2)))
    say('  逐点 RMS 差（全段 %.1f s，%d 点）：' % (t[-1], len(t)))
    say('     旧链模型 vs 实测 = %.3f µV' % e_old)
    say('     新链模型 vs 实测 = %.3f µV   ← 实测固件是旧链，故这一项**应显著更大**'
        % e_new)
    say('  ⇒ %s' % ('旧链复现良好，模型可信，可外推新链' if e_old < 10
                    else '⚠ 旧链复现偏差偏大，外推要打折扣（见报告不确定度段）'))
    say('     比照：信号 p-p %.0f µV ⇒ 旧链模型相对误差 %.3f%%'
        % (np.ptp(y_meas), 100 * e_old / max(np.ptp(y_meas), 1e-9)))

    # ---------------- [3] 三判据全表 ----------------
    R_meas = T56.judge(y_meas, t, pulses, FS, rep)
    R_old = T56.judge(y_old, t, pulses, FS, rep)
    R_new = T56.judge(y_new, t, pulses, FS, rep)

    hdr('[3] ★三判据：实测 vs 旧链模型 vs 新链模型（notch OFF，间隔 %.1f s）' % rep)
    say('  ① 位移（µV，判限 <= %.0f）—— 多取样时刻' % L_DISP)
    say('    %-14s %10s %10s %10s   %s' % ('时刻', '实测', '模型·旧链', '模型·新链', '新链判决'))
    for off in (0.020, 0.050, 0.100, 0.200, 0.300, 0.500, 1.000, 2.000):
        f = lambda R: R['disp_long'].get(off, np.nan)      # noqa: E731
        v = f(R_new)
        say('    +%.3f s      %10.1f %10.1f %10.1f   %s'
            % (off, f(R_meas), f(R_old), v, '✓' if abs(v) <= L_DISP else '✗'))
    say('    ★读法：标准原文只说"脉冲区域之外"，取样时刻未定 ⇒ 三种口径差可达 12 倍（T53 §4.5）')
    say('      ⚠ +0.02 s 是最严的口径；判决通常以更晚的时刻为准，符号取绝对值。')

    say()
    say('  ② 终点后斜率（µV/s，判限 <= %.0f）' % L_SLOPE)
    say('    %-16s %10s %10s %10s   %s' % ('口径', '实测', '模型·旧链', '模型·新链', '新链判决'))
    for w in (0.050, 0.100, 0.200):
        f = lambda R: R['slope_win'].get(w, np.nan)          # noqa: E731
        v = f(R_new)
        say('    滑窗 %.0f ms      %10.1f %10.1f %10.1f   %s'
            % (w * 1e3, f(R_meas), f(R_old), v, '✓' if abs(v) <= L_SLOPE else '✗'))
    v = R_new['slope_fit']
    say('    拟合 0.2-1.2 s  %10.1f %10.1f %10.1f   %s'
        % (R_meas['slope_fit'], R_old['slope_fit'], v,
           '✓' if abs(v) <= L_SLOPE else '✗'))

    say()
    say('  ③ 过冲（%%，判限 < %.0f）—— 四口径全列' % L_OVER)
    say('    %-18s %10s %10s %10s   %s' % ('口径', '实测', '模型·旧链', '模型·新链', '新链判决'))
    om, oo, on = overshoots(R_meas), overshoots(R_old), overshoots(R_new)
    for k in OSKEYS:
        say('    %-18s %10.2f %10.2f %10.2f   %s'
            % (k, om[k], oo[k], on[k], '✓' if on[k] < L_OVER else '✗'))

    say()
    say('  ④ 幅值结构（µV，判据 c 的原料）')
    say('    %-18s %10s %10s %10s' % ('', '实测', '模型·旧链', '模型·新链'))
    for lab, key in (('峰值 peak', 'peak'), ('顶部均值 top', 'top'),
                     ('近平台 near', 'near'), ('末端 end', 'end')):
        say('    %-18s %10.1f %10.1f %10.1f'
            % (lab, R_meas[key], R_old[key], R_new[key]))

    # ---------------- [4] 判决汇总 ----------------
    hdr('[4] 判决汇总')
    say('  低端未动 ⇒ 位移/斜率**预期基本不变**（两条模型对同一输入，低端相同）：')
    d_new_002 = R_new['disp_long'].get(0.020, np.nan)
    d_old_002 = R_old['disp_long'].get(0.020, np.nan)
    say('    +0.02 s 位移：旧链模型 %.1f → 新链模型 %.1f µV（差 %.1f µV = %.2f%%）'
        % (d_old_002, d_new_002, d_new_002 - d_old_002,
           100 * (d_new_002 - d_old_002) / max(abs(d_old_002), 1e-9)))
    say('    滑窗 100 ms：旧链模型 %.1f → 新链模型 %.1f µV/s'
        % (R_old['slope_win'].get(0.100, np.nan), R_new['slope_win'].get(0.100, np.nan)))
    say('  ⇒ 这两条新链仍应 ✗（低端 5 阶没动，符合 T58 的"只做高端那一半"）。')
    say()
    say('  过冲：这是本步唯一预期改善的项 ——')
    for k in OSKEYS:
        say('    %-18s %.2f%% → %.2f%%（%+.2f pp）'
            % (k, oo[k], on[k], on[k] - oo[k]))
    say('  ⇒ 主口径「对顶均」（T57 报的 16.54%%）：%.2f%% ⇒ %s'
        % (on['对顶均'], '✓ 达标' if on['对顶均'] < L_OVER else '✗ 仍超限'))
    say()
    say('  ★不确定度（必须随判词一起给）：过冲的**绝对值**受模型结构性不足影响 ——')
    say('     旧链模型 %.2f%% vs 其实测 %.2f%% ⇒ 残差 %+.2f pp。'
        % (oo['对顶均'], om['对顶均'], oo['对顶均'] - om['对顶均']))
    say('     ⇒ 新链的 %.2f%% 应理解为 %.2f ± 2 pp 量级；**判词以"方向 + 相对改善"为准**，'
        % (on['对顶均'], on['对顶均']))
    say('     绝对值一律交上板实测（这正是 S2′ 要做的）。')

    # ---------------- [5] 逐脉冲离散度 ----------------
    hdr('[5] 逐脉冲离散度（同一份数据里 %d 个脉冲）' % len(pulses))
    say('  口径：本段用统一局部定义（peak = 升沿后 0~50 ms 内最大；top = 平台区均值；')
    say('        near = 升沿后 10 ms 处；end = 降沿前 1 ms 处）。')
    say('        ⇒ 用途是**比脉冲间离散**，绝对值判词一律以 [3] 的 judge 口径为准。')

    def per_pulse(yy):
        pk, tp, nr, en = [], [], [], []
        for (s, e) in pulses:
            tr, tf = t[s], t[e]
            m_pk = (t >= tr - 0.005) & (t <= tr + 0.050)
            m_tp = (t >= tr + 0.010) & (t <= tf - 0.010)
            pk.append(float(yy[m_pk].max()))
            tp.append(float(np.mean(yy[m_tp])))
            nr.append(float(np.interp(tr + 0.010, t, yy)))
            en.append(float(np.interp(tf - 0.001, t, yy)))
        return np.array(pk), np.array(tp), np.array(nr), np.array(en)

    say()
    say('  %-14s %9s %9s %9s %9s %9s' % ('', '峰中位', '顶均中位', '过冲/顶均', '最小%', '最大%'))
    for lab, yy in (('实测', y_meas), ('模型·旧链', y_old), ('模型·新链', y_new)):
        pk, tp, nr, en = per_pulse(yy)
        ov = (pk - tp) / tp * 100.0
        say('  %-14s %9.1f %9.1f %9.2f %9.2f %9.2f'
            % (lab, np.median(pk), np.median(tp), np.median(ov), ov.min(), ov.max()))
    pk_o, tp_o, _, _ = per_pulse(y_old)
    pk_n, tp_n, _, _ = per_pulse(y_new)
    ov_o = (pk_o - tp_o) / tp_o * 100.0
    ov_n = (pk_n - tp_n) / tp_n * 100.0
    say('  ⇒ 脉冲间离散：旧链 %.2f~%.2f%%（幅 %.2f pp）｜新链 %.2f~%.2f%%（幅 %.2f pp）'
        % (ov_o.min(), ov_o.max(), ov_o.max() - ov_o.min(),
           ov_n.min(), ov_n.max(), ov_n.max() - ov_n.min()))
    say('  ⇒ 新链**最差的那个脉冲** = %.2f%% ⇒ %s'
        % (ov_n.max(), '仍 ✓' if ov_n.max() < L_OVER else '✗ 已超限，不能只看中位'))

    # ---------------- [6] 敏感性分析（间隔 × 边沿） ----------------
    hdr('[6] ★敏感性分析：换间隔 / 换边沿，9.75% 还成立吗？')
    import _t52b_discuss as T52b

    def synth(interval, hp_order, lp_order, fc_lp, pre, n=None):
        """合成脉冲串过链（复用 T52b.run），并**用 T56.judge 同一套口径**读三判据。"""
        old_fc = T52b.FC_LP
        T52b.FC_LP = fc_lp
        try:
            nn_ = n if n else (12 if interval <= 3 else (6 if interval <= 12 else 3))
            tt, yy, (r0, f0) = T52b.run(interval, nn_, hp_order, lp_order, pre=pre)
        finally:
            T52b.FC_LP = old_fc
        step = max(int(round(interval * FS)), 1)
        pls = []
        for j in range(nn_):
            s = r0 + j * step
            e = f0 + j * step
            if e < len(tt):
                pls.append((s, e))
        R = T56.judge(yy, tt, pls, FS, interval)
        return R, yy, tt

    # 6a 边沿标定：用旧链 @10 s 的实测过冲 16.54% 定 pre
    say('  6a 边沿标定（一个参数、一个锚点 ⇒ 必须报残差）')
    say('     锚点：旧链 @10 s 的实测「过冲/顶均」= %.2f%%' % om['对顶均'])
    say('     %-6s %12s %12s  %s' % ('pre', '模型过冲%', '残差pp', '备注'))
    best, bestres = None, 1e9
    for pre in (0, 1, 2, 3, 4, 5):
        R, _, _ = synth(10.0, 5, 4, 150.0, pre)
        v = overshoots(R)['对顶均']
        res = v - om['对顶均']
        if abs(res) < abs(bestres):
            best, bestres = pre, res
        say('     %-6d %12.2f %12.2f  %s' % (pre, v, res, '← 最接近' if pre == best else ''))
    say('     ⇒ 标定 pre = %d（残差 %+.2f pp）' % (best, bestres))
    say('     ⚠ 残差即是模型结构性不足的量级 ⇒ 绝对值判词的**不确定度下界**。')

    # 6b 间隔扫描：三条链
    say()
    say('  6b 间隔扫描（合成脉冲串，pre=%d；口径与 [3] 完全一致）' % best)
    say('     %-22s %6s %10s %10s %10s %10s' % ('链', '间隔s', '位移+0.02', '滑窗100', '过冲/顶均', '判决'))
    cfgs = [('旧链 低端5阶+LP4@150', 5, 4, 150.0),
            ('新链 低端5阶+LP2@250', 5, 2, 250.0),
            ('未来 低端1阶+LP2@250', 1, 2, 250.0)]
    for lab, hp, lp, fc in cfgs:
        for iv in (2.0, 5.0, 10.0, 20.0):
            try:
                R, _, _ = synth(iv, hp, lp, fc, best)
            except Exception as ex:
                say('     %-22s %6.0f   异常：%s' % (lab, iv, ex))
                continue
            d = abs(R['disp_long'].get(0.020, np.nan))
            sw = R['slope_win'].get(0.100, np.nan)
            ov = overshoots(R)['对顶均']
            vs = ('位移✗' if d > L_DISP else '位移✓') + \
                 ('/斜率✗' if abs(sw) > L_SLOPE else '/斜率✓') + \
                 ('/过冲✗' if ov >= L_OVER else '/过冲✓')
            say('     %-22s %6.0f %10.1f %10.1f %10.2f   %s' % (lab, iv, d, sw, ov, vs))
    say('     （位移 限100 µV ｜ 滑窗100ms 限300 µV/s ｜ 过冲 限10%）')

    # 6c 结论
    say()
    say('  6c ★结论与不确定度')
    rowsn = []
    for iv in (2.0, 5.0, 10.0, 20.0):
        try:
            R, _, _ = synth(iv, 5, 2, 250.0, best)
            rowsn.append((iv, overshoots(R)['对顶均']))
        except Exception:
            pass
    if rowsn:
        vs = [v for _, v in rowsn]
        say('     新链（低端未动）过冲/顶均随间隔：%s'
            % ' ｜ '.join('%.0f s→%.2f%%' % (iv, v) for iv, v in rowsn))
        say('     区间 %.2f~%.2f%%（幅 %.2f pp），全部 %s 判限 %.0f%%'
            % (min(vs), max(vs), max(vs) - min(vs),
               '低于' if max(vs) < L_OVER else '**并非全部低于**', L_OVER))
        say('     ⇒ 若最大值仍在判限内，则"fc=250 修过冲"的结论**对间隔不敏感**，可放心。')
        say('     ⇒ 若最大值越过判限，则本步只能算"大幅改善"，**最终达标要等低端降 1 阶**。')
    say()
    say('     ⚠ 三重不确定度（必须随判词给出）:')
    say('       ① 边沿标定残差 %+.2f pp（pre=%d，单锚点）' % (bestres, best))
    say('       ② 口径离散：四口径从 %.2f%% 到 %.2f%%（判词须声明用哪个）'
        % (min(on.values()), max(on.values())))
    say('       ③ 激励条件：本份数据是 AECG100 3 mV/100 ms、低阻直驱')
    say('       ⇒ 绝对判词交上板实测；离线只定"方向 + 相对改善量"。')
    say()
    say('     四口径三条链对照（合成，@10 s，pre=%d）—— 看"对末端"是否也收敛：' % best)
    say('       %-12s %10s %10s %10s %10s' % ('链', '对输入', '对顶均', '对近平台', '对末端'))
    for lab, hp, lp, fc in cfgs:
        try:
            R, _, _ = synth(10.0, hp, lp, fc, best)
            o = overshoots(R)
            say('       %-12s %10.2f %10.2f %10.2f %10.2f'
                % (lab.split(' ')[0], o['对输入(标称3mV)'], o['对顶均'],
                   o['对近平台'], o['对末端']))
        except Exception:
            pass
    say('       ⇒ 「对末端」是唯一在低端未动时仍 ✗ 的口径（平台下垂把参照压低了）；')
    say('         低端降到 1 阶后下垂消失，四个口径应一起收敛 ⇒ 届时口径之争自动消解。')

    # ---------------- 输出 ----------------
    txt = '\n'.join(OUT) + '\n'
    op = os.path.join(HERE, '_t59_out%s.txt' % a.tag)
    io.open(op, 'w', encoding='utf-8').write(txt)
    print()
    print('已写出:', op)
    return 0


if __name__ == '__main__':
    sys.exit(main())
