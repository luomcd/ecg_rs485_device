# -*- coding: utf-8 -*-
"""
T60 · fc=250 上板实测（2026-09-20 195208）—— 链身份判定 ＋ 新旧链同输入并排

为什么要有这一份
----------------
T58 把高端 LP4@150 改成 LP2@250 并编进镜像；猛哥烧录后抓了这份数据。
台架命名写的是 `notchoff`，但读数与 T57（真 OFF）差一个量级 ⇒ 必须先用数据
判定**这份数据到底在什么条件下**，再谈效果。判两件事：

  (1) notch 是 ON 还是 OFF？   → dtcycles 三点标定 ＋ 离线链模型 RMS 双证据
  (2) 板子上跑的是新链还是旧链？ → 同上两条，独立

方法与纪律
----------
1. **同一输入驱动多条链**：都用本次实测的列② raw（未滤波）当输入
   ⇒ 差异 100% 来自链本身，不含激励差异。
2. **链身份靠两条独立证据**：① `dtcycles` 节数标定（与波形无关）；
   ② 链模型 vs 实测列⑤ 的逐点 RMS。**两者必须同向才算定案。**
3. **不给结论贴标签**：dtcycles 的 9710/3950 是历史读数，本脚本**现场重读**
   T56/T57 两份数据取 median，不沿用报告里的二手数字。
4. **过冲报四口径 ＋ 逐脉冲最差**：T59 §4 的纪律 —— 只看中位会假乐观。
5. **复用**：读数据/坏行判据/脉冲定位/三判据 全部 import `_t56_raw853_lowfreq`；
   系数解析 import `_t51_yy0885_pulse`；dc_block import `_t52_yy0885_notchoff`。
   **本脚本不重写任何判据逻辑。**

用法
----
  python _t60_fc250_onboard.py                      # 默认跑 195208 文件
  python _t60_fc250_onboard.py --file <txt> --tag _x
输出：`_t60_out<tag>.txt`
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
import _t52_yy0885_notchoff as T52m                 # noqa: E402  dc_block
import _t56_raw853_lowfreq as T56                   # noqa: E402  读数据 / 判据

FS = T51.FS
UVPC = T56.UVPC
MACRO = 'ECG_PROFILE_IMP620_N2'

SRC_NEW_C, SRC_NEW_H = T51.COEFFS_C, T51.COEFFS_H
SRC_OLD_C = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak')
SRC_OLD_H = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak')

F_DEF = os.path.join(HERE, '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 195208.txt')
F_T56 = os.path.join(HERE, '3mv-0.1hz-raw853on-2026-09-20 180458.txt')        # 旧链 ON
F_T57 = os.path.join(HERE, '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt')  # 旧链 OFF

# 历史两个已知点（节数由本脚本动态解析，不写死）
REF = [(F_T56, '旧链 ON'), (F_T57, '旧链 OFF')]

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 84)
    say(s)
    say('=' * 84)


# ------------------------------------------------------------------ 链构造

def chain_of(path_c, path_h):
    """返回 (sos6, n_notch, hp_idx, kinds)。5 元组补成 scipy 6 列。"""
    sos, nn = T51.parse_coeffs_f64(path_c, path_h, MACRO)
    kinds = [T51.section_kind(s) for s in sos]
    sos6 = [np.insert(np.asarray(sv, float), 3, 1.0) for sv in sos]
    hp = [i for i in range(nn, len(sos)) if kinds[i] == 'HP']
    return sos6, nn, hp, kinds


def off_order(sos6, nn, hp):
    """关陷波时固件实际要跑的节（低端 HP 在前，与 _t59 一致）。"""
    return list(hp) + [i for i in range(nn, len(sos6)) if i not in hp]


def run_chain(sos6, nn, hp, xin, use_notch):
    """dc_block(0.05) → 节级联。use_notch=False 时跳过前 nn 节（真 OFF）。"""
    y = T52m.dc_block_apply(np.asarray(xin, float), 0.05, FS)
    order = range(len(sos6)) if use_notch else off_order(sos6, nn, hp)
    for i in order:
        y = ss.sosfilt(sos6[i:i + 1], y)
    return y


def descr(kinds, nn):
    return '%d NOTCH + %s' % (nn, '+'.join(kinds[nn:]))


# ------------------------------------------------------------------ 过冲口径

def ovs(R, amp_nom=3000.0):
    p, top, near, end = R['peak'], R['top'], R['near'], R['end']
    return [('对输入(标称 3000)', (p - amp_nom) / amp_nom * 100.0),
            ('对顶均',            (p - top) / top * 100.0),
            ('对近平台',          (p - near) / near * 100.0),
            ('对末端',            (p - end) / end * 100.0)]


def per_pulse(y, t, pulses, fs):
    """T59 §4 的统一局部口径：peak = 升沿后 0~50 ms 最大；top = 平台均值。"""
    pk, tp = [], []
    for (s, e) in pulses:
        i0 = max(0, s - int(0.005 * fs))
        i1 = min(len(y), s + int(0.050 * fs))
        pk.append(float(np.max(y[i0:i1])))
        j0 = s + int(0.010 * fs)
        j1 = max(j0 + 3, e - int(0.005 * fs))
        tp.append(float(np.mean(y[j0:j1])))
    pk, tp = np.asarray(pk), np.asarray(tp)
    return pk, tp, (pk - tp) / tp * 100.0


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_DEF)
    ap.add_argument('--tag', default='')
    args = ap.parse_args()

    say('T60 · fc=250 上板实测 —— 条件判定 ＋ 链身份 ＋ 新旧链并排')
    say('file = %s' % os.path.basename(args.file))

    # ---------------- [0] 数据验收
    hdr('[0] 数据验收（V1~V7）')
    d = T56.load7(args.file)
    keep, dirty, lost, holes = T56.clean_idx(d)
    idx = d['idx'][keep].astype(np.int64)
    raw = d['raw'][keep]
    # ★列⑤ 是 **code**，须 ×UVPC；时间轴用 idx 重建（丢包 53 个 ⇒ 与 arange 差 62 ms）
    y = d['filt'][keep] * UVPC
    dtc = d['dtc'][keep]
    t = (idx - idx[0]) / FS
    n = len(idx)
    say('  行数 %d（坏 %d）｜剔脏 %d 行｜真丢包 %d 个 %s' % (n, d['nbad'], len(dirty), lost, holes))
    say('  列① idx %d -> %d（相邻差恒 1）' % (idx[0], idx[-1]))
    say('  列③ max=%.0f  列④ max=%.0f  %s' % (d['ifilt'][keep].max(), d['dsr'][keep].max(),
                                                'PASS' if d['ifilt'][keep].max() == 0 else 'FAIL'))
    pp = raw.max() - raw.min()
    say('  列② raw p-p = %.0f code = %.1f µV（3 mV 脉冲）' % (pp, pp * UVPC))
    say('  列⑦ gdtover 唯一值 %s' % np.unique(d['over'][keep]))
    say('  时长 %.3f s' % (n / FS))

    # ---------------- [1] notch 状态：dtcycles 三点标定
    hdr('[1] ★notch 状态判定：dtcycles 节数标定（与波形无关的独立证据）')
    sos6_new, nn_new, hp_new, kinds_new = chain_of(SRC_NEW_C, SRC_NEW_H)
    sos6_old, nn_old, hp_old, kinds_old = chain_of(SRC_OLD_C, SRC_OLD_H)
    n_new_on, n_new_off = len(sos6_new), len(off_order(sos6_new, nn_new, hp_new))
    n_old_on, n_old_off = len(sos6_old), len(off_order(sos6_old, nn_old, hp_old))
    say('  旧链 %s ⇒ ON %d 节 / OFF %d 节' % (descr(kinds_old, nn_old), n_old_on, n_old_off))
    say('  新链 %s ⇒ ON %d 节 / OFF %d 节' % (descr(kinds_new, nn_new), n_new_on, n_new_off))

    pts = []
    for path, lab in REF:
        dd = T56.load7(path)
        kk, _, _, _ = T56.clean_idx(dd)
        m = float(np.median(dd['dtc'][kk]))
        pts.append((m, lab))
        say('  参照 %-9s dtcycles median = %8.1f   （%s）' % (lab, m, os.path.basename(path)[:34]))
    say('  本次          dtcycles median = %8.1f   （中位口径，全程稳定）' % float(np.median(dtc)))

    # 标定：用历史两点解 a·节数 + c（节数取各自条件）
    m_on, m_off = pts[0][0], pts[1][0]
    a = (m_on - m_off) / (n_old_on - n_old_off)
    c = m_off - a * n_old_off
    say('  ★标定：a = %.1f cycles/节，c = %.0f（固定开销）；样本 2 点，线性假设需声明' % (a, c))
    for nn_, lab in ((n_old_on, '旧链 ON'), (n_old_off, '旧链 OFF'),
                     (n_new_on, '新链 ON'), (n_new_off, '新链 OFF')):
        say('     预测 %-9s %2d 节 ⇒ %8.1f' % (lab, nn_, a * nn_ + c))
    cur = float(np.median(dtc))
    n_est = (cur - c) / a
    cand = [('旧链 ON', n_old_on), ('旧链 OFF', n_old_off),
            ('新链 ON', n_new_on), ('新链 OFF', n_new_off)]
    best = min(cand, key=lambda x: abs(a * x[1] + c - cur))
    for lab, nn_ in cand:
        say('     本次距 %-9s（%2d 节）差 %+8.1f cycles' % (lab, nn_, cur - (a * nn_ + c)))
    say('  ⇒ 本次 dtcycles %.0f ⇒ 折算 %.2f 节 ⇒ 最接近 **%s**' % (cur, n_est, best[0]))

    # ---------------- [2] 脉冲定位
    hdr('[2] 脉冲定位（列② raw）')
    pulses, thr, bl0 = T56.find_pulses(raw)
    say('  阈值 = 基线 %.0f + 50%% 幅度 = %.0f' % (bl0, thr))
    say('  #   升沿 t(s)   降沿 t(s)   脉宽 ms    周期 s')
    for k, (s, e) in enumerate(pulses, 1):
        per = (t[s] - t[pulses[k - 2][0]]) if k > 1 else np.nan
        say('  %-3d %10.4f %11.4f %10.2f %9.4f' % (k, t[s], t[e], (t[e] - t[s]) * 1e3, per))
    if len(pulses) > 2:
        di = int(idx[pulses[2][0]] - idx[pulses[1][0]])
        say('  ★FS 独立校验：脉冲2->3 idx 差 %d，标称 10.0000 s ⇒ 隐含 FS = %.3f Hz（设计 %.3f，%+.4f%%）'
            % (di, di / 10.0, FS, (di / 10.0 - FS) / FS * 100))
    rep = float(np.median([t[pulses[k][0]] - t[pulses[k - 1][0]] for k in range(1, len(pulses))]))
    say('  重复周期 %.4f s' % rep)

    # ---------------- [3] 实测三判据
    hdr('[3] 实测三判据（853 域直出；判限 位移≤100 µV｜斜率≤300 µV/s｜过冲<10%）')
    R_mea = T56.judge(y, t, pulses, FS, rep)
    say('  基线：near = %+.1f µV（升沿前[280,30]ms 中位）｜long = %+.1f µV（降沿前[5.0,0.05]s 中位）'
        % (R_mea['base_near'], R_mea['base_long']))
    say('  (a1) 位移（long 口径）')
    for a_ in (0.020, 0.050, 0.100, 0.200, 0.300, 0.500, 1.000, 2.000, 5.000):
        v = R_mea['disp_long'][a_]
        say('       +%.3f s  %9.1f µV  %s' % (a_, v, 'OK' if abs(v) <= 100 else 'NG x%.2f' % (abs(v) / 100)))
    say('  (a2) 终点后斜率')
    for w in (0.050, 0.100, 0.200):
        v = R_mea['slope_win'][w]
        say('       滑窗 %3.0f ms  %10.1f µV/s  %s' % (w * 1e3, v, 'OK' if v <= 300 else 'NG x%.2f' % (v / 300)))
    say('       拟合 0.2-1.2 s %8.1f µV/s  %s'
        % (R_mea['slope_fit'], 'OK' if R_mea['slope_fit'] <= 300 else 'NG'))
    say('  (a3) 过冲（四口径，限 10%）')
    for lab, v in ovs(R_mea):
        say('       %-16s %7.2f%%  %s' % (lab, v, 'OK' if v < 10 else 'NG x%.2f' % (v / 10)))
    say('       结构：峰 %.1f / 谷 %.1f / 平台 %.1f µV' % R_mea['struct'])
    pk, tp, op = per_pulse(y, t, pulses, FS)
    say('  ★逐脉冲（N=%d，T59 纪律：必报最差）' % len(pk))
    say('       峰    中位 %.1f  最小 %.1f  最大 %.1f' % (np.median(pk), pk.min(), pk.max()))
    say('       顶均  中位 %.1f  最小 %.1f  最大 %.1f' % (np.median(tp), tp.min(), tp.max()))
    say('       过冲  中位 %.2f%%  最小 %.2f%%  **最大 %.2f%%**  ⇒ %s'
        % (np.median(op), op.min(), op.max(), 'NG' if op.max() >= 10 else 'OK'))

    # ---------------- [4] 链身份判定：模型 RMS
    hdr('[4] ★链身份判定：同输入过链，与实测列⑤ 逐点比')
    xin = raw * UVPC
    y_on_new = run_chain(sos6_new, nn_new, hp_new, xin, True)
    y_off_new = run_chain(sos6_new, nn_new, hp_new, xin, False)
    y_on_old = run_chain(sos6_old, nn_old, hp_old, xin, True)
    y_off_old = run_chain(sos6_old, nn_old, hp_old, xin, False)
    cands = [('旧链 ON ', y_on_old), ('旧链 OFF', y_off_old),
             ('新链 ON ', y_on_new), ('新链 OFF', y_off_new)]
    best_lab, best_rms = None, 1e18
    for lab, yy in cands:
        e = float(np.sqrt(np.mean((yy - y) ** 2)))
        if e < best_rms:
            best_lab, best_rms = lab, e
        say('  RMS 差  %-9s = %8.2f µV' % (lab, e))
    say('  ⇒ 实测最接近 **%s**（RMS %.2f µV）' % (best_lab, best_rms))

    # ---------------- [5] 新旧链同输入并排
    hdr('[5] ★新旧链同输入并排（同一列② raw 驱动；notch ON 与实际条件一致）')
    R_on_new = T56.judge(y_on_new, t, pulses, FS, rep)
    R_on_old = T56.judge(y_on_old, t, pulses, FS, rep)
    R_off_new = T56.judge(y_off_new, t, pulses, FS, rep)
    R_off_old = T56.judge(y_off_old, t, pulses, FS, rep)

    say('  %-22s %12s %12s %12s %12s %12s' % ('指标', '实测', '旧链ON', '新链ON', '旧链OFF', '新链OFF'))
    for lab, key, sub in (('位移 +0.100 s (L)', 'disp_long', 0.100),
                          ('位移 +0.300 s (L)', 'disp_long', 0.300),
                          ('位移 +1.000 s (L)', 'disp_long', 1.000),
                          ('滑窗 50 ms', 'slope_win', 0.050),
                          ('滑窗 100 ms', 'slope_win', 0.100),
                          ('滑窗 200 ms', 'slope_win', 0.200),
                          ('拟合 0.2-1.2 s', 'slope_fit', None)):
        g = lambda R: R[key][sub] if sub is not None else R[key]
        say('  %-22s %12.1f %12.1f %12.1f %12.1f %12.1f'
            % (lab, g(R_mea), g(R_on_old), g(R_on_new), g(R_off_old), g(R_off_new)))
    say('  %-22s %12.1f %12.1f %12.1f %12.1f %12.1f'
        % ('峰值 µV', R_mea['peak'], R_on_old['peak'], R_on_new['peak'],
           R_off_old['peak'], R_off_new['peak']))
    say('  %-22s %12.1f %12.1f %12.1f %12.1f %12.1f'
        % ('顶部均值 µV', R_mea['top'], R_on_old['top'], R_on_new['top'],
           R_off_old['top'], R_off_new['top']))
    say('  %-22s %12.1f %12.1f %12.1f %12.1f %12.1f'
        % ('谷 µV', R_mea['struct'][1], R_on_old['struct'][1], R_on_new['struct'][1],
           R_off_old['struct'][1], R_off_new['struct'][1]))
    for lab, v in ovs(R_mea):
        say('  过冲 %-17s %7.2f%%     %7.2f%%     %7.2f%%     %7.2f%%     %7.2f%%'
            % (lab, v, dict(ovs(R_on_old))[lab], dict(ovs(R_on_new))[lab],
               dict(ovs(R_off_old))[lab], dict(ovs(R_off_new))[lab]))

    rows = []
    for lab, yy in (('实测', y), ('旧链ON', y_on_old), ('新链ON', y_on_new),
                    ('旧链OFF', y_off_old), ('新链OFF', y_off_new)):
        _pk, _tp, _op = per_pulse(yy, t, pulses, FS)
        rows.append((lab, _op.max(), np.median(_op)))
    say('  ★逐脉冲过冲   ' + ' ｜ '.join('%s 最差 %.2f%% / 中位 %.2f%%' % r for r in rows))

    # ---------------- [5b] 峰从哪来
    hdr('[5b] ★峰来源分解：notch 振铃（ON 独有）vs LP 自身阶跃过冲（OFF 主导）')
    z = T52m.dc_block_apply(xin, 0.05, FS)
    for i in range(nn_new):
        z = ss.sosfilt(sos6_new[i:i + 1], z)
    z4 = z.copy()
    for i in (nn_old, nn_old + 1):
        z4 = ss.sosfilt(sos6_old[i:i + 1], z4)
    z2 = ss.sosfilt(sos6_new[nn_new:nn_new + 1], z)
    s2 = pulses[1][0]

    def pk_of(yv, s, span=0.050):
        i0 = max(0, s - int(0.005 * FS))
        i1 = min(len(yv), s + int(span * FS))
        return float(np.max(yv[i0:i1]))

    say('  同一输入的第2脉冲窗内峰：')
    say('    (0) notch 段输出 z（= 两链共同前端）   峰 %8.1f µV' % pk_of(z, s2))
    say('    (1) z → LP4@150（旧链高端）            峰 %8.1f µV' % pk_of(z4, s2))
    say('    (2) z → LP2@250（新链高端）            峰 %8.1f µV' % pk_of(z2, s2))
    say('    ⇒ LP 段对峰的单独作用：旧→新 %+.1f µV' % (pk_of(z2, s2) - pk_of(z4, s2)))

    i0 = max(0, s2 - int(0.010 * FS))
    i1 = min(len(z), s2 + int(0.100 * FS))
    seg = z[i0:i1] - float(np.mean(z[i0:i1]))
    F = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1.0 / FS)
    m = (fr > 20) & (fr < 400)
    f0 = float(fr[m][int(np.argmax(F[m]))])
    top3 = np.argsort(F[m])[::-1][:3]
    say('    z 的边沿振铃主频 = %.1f Hz（次峰 %s）'
        % (f0, ', '.join('%.0f' % v for v in np.sort(fr[m][top3]))))

    def Hlp(secs, idxs, f):
        """(dB, 相位度) —— 相位才是 ON 下差异的来源：46 Hz 处两链增益都在 −0.00 dB。"""
        h = 1.0 + 0j
        for i in idxs:
            w, q = ss.freqz(secs[i][:3], secs[i][3:], worN=[2 * np.pi * f / FS])
            h *= q[0]
        return 20 * np.log10(abs(h)), float(np.degrees(np.angle(h)))

    say('  两链高端 LP 段频响／相位（LP4@150 vs LP2@250）—— 谁更能压 notch 振铃：')
    say('    %-8s %12s %12s %12s %12s %10s' % ('f(Hz)', 'LP4 dB', 'LP2 dB', 'LP4 °', 'LP2 °', '相位差 °'))
    for f_ in (40, 46, 50, 86, 100, 150, 167, 200, 250, 300):
        a4, p4 = Hlp(sos6_old, (nn_old, nn_old + 1), f_)
        a2, p2 = Hlp(sos6_new, (nn_new,), f_)
        say('    %-8g %12.2f %12.2f %12.1f %12.1f %10.1f' % (f_, a4, a2, p4, p2, p4 - p2))

    nn2 = int(0.5 * FS)
    step = np.zeros(nn2)
    step[10:] = 1.0
    say('  理想阶跃单独过 LP 段（OFF 下没有 notch 振铃，峰由它决定）：')
    for lab, secs, idxs in (('LP4@150', sos6_old, (nn_old, nn_old + 1)),
                            ('LP2@250', sos6_new, (nn_new,))):
        yy = step.copy()
        for i in idxs:
            yy = ss.sosfilt(secs[i:i + 1], yy)
        say('    %-9s 阶跃过冲 = %6.2f%%（峰 %.4f）' % (lab, (yy.max() - 1.0) * 100, yy.max()))

    # ---------------- [6] 结论位
    hdr('[6] 结论位')
    say('  ① 条件：dtcycles 折算 %.2f 节 ⇒ %s' % (n_est, best[0]))
    say('  ② 链身份：波形 RMS ⇒ %s' % best_lab.strip())
    say('  ③ 实测过冲（逐脉冲最差）%.2f%% ｜ 滑窗 100 ms %.1f µV/s ｜ 位移 +0.1 s %.1f µV'
        % (op.max(), R_mea['slope_win'][0.100], R_mea['disp_long'][0.100]))

    out = os.path.join(HERE, '_t60_out%s.txt' % args.tag)
    with io.open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
    say()
    say('  输出 -> %s' % os.path.basename(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
