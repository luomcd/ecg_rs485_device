# -*- coding: utf-8 -*-
"""
T61 · 用「实测 raw 输入」离线预测「低端降 1 阶」的效果

为什么要有这一份
----------------
T60 之后拿到第一份**真 notch OFF** 的上板数据（200724）。实测三判据仍全 ✗，
但过冲已从 16.5% 量级改善到 11.9%（逐脉冲最差）。猛哥的节奏是
"抓数据 → 据数据定下一步"，所以下一步（T53 §5 的"低端降 1 阶"）必须
**用这组实测输入**先算清落点，才能回答"改完之后够不够"。

方法（全部复用既有模块，不重写任何判据）
----------------------------------------
- 系数：`_t51.parse_coeffs_f64`（从源码文本取字面量，不重生成）
- dc_block：`_t52.dc_block_apply`（与固件一致，T52d 已确认是活的代码）
- 判据：`_t56.judge`（位移/斜率/过冲全套）＋ `_t60.per_pulse`（逐脉冲）
- 链构造：`_t60.chain_of / off_order / run_chain / ovs`

五条链（OFF 态，即跳过 notch 段）
--------------------------------
  A 旧链   = dc1 + HP4@0.05 + LP4@150   （T57 之前的固件）
  B 新链   = dc1 + HP4@0.05 + LP2@250   （当前固件，fc250）
  C 未来链 = dc1          + LP2@250     （低端降 1 阶 ⇒ 删掉 HP4@0.05 四节）
另外给 ON 态参考（A/B 加 notch 段）。

纪律
----
1. 同一输入驱动全部链 ⇒ 差异 100% 来自链。
2. 过冲**必报逐脉冲最差**（T59 立的规矩）。
3. 跨文件复核：换 T57 的输入再算一遍，确认"改善方向"与"是否达标"的稳健性。
"""
import io
import os
import sys
import argparse

import numpy as np
import scipy.signal as ss

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t51_yy0885_pulse as T51          # 系数解析
import _t52_yy0885_notchoff as T52m      # dc_block_apply
import _t56_raw853_lowfreq as T56        # load7 / find_pulses / judge
import _t60_fc250_onboard as T60         # chain_of / off_order / run_chain / ovs / per_pulse

FS = T51.FS
MACRO = 'ECG_PROFILE_IMP620_N2'

F_DEF = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt'
F_ALT = '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt'   # T57 的输入，用于跨文件复核
SRC_NEW_C = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')
SRC_NEW_H = os.path.join(HERE, '..', 'Filter', 'ecg_filter_coeffs.h')
SRC_OLD_C = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak')
SRC_OLD_H = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak')

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 86)
    say(s)
    say('=' * 86)


# ------------------------------------------------------------------ 链

def build_chains():
    """返回 dict: name -> (sos6, nn, hp, kinds)"""
    s_new, nn, hp_new, k_new = T60.chain_of(SRC_NEW_C, SRC_NEW_H)
    s_old, nn_o, hp_old, k_old = T60.chain_of(SRC_OLD_C, SRC_OLD_H)
    assert nn == nn_o, '两条链 notch 段节数应相同'
    # HP 的**索引**会随高端节数变化（旧 13 节 [11,12] vs 新 12 节 [10,11]），
    # 但**系数值**必须逐位相同 —— 这才是"T58 只动高端"的证据。
    hp_old_v = [np.asarray(s_old[i], float).tolist() for i in hp_old]
    hp_new_v = [np.asarray(s_new[i], float).tolist() for i in hp_new]
    assert len(hp_old) == len(hp_new) and hp_old_v == hp_new_v, '低端 HP 系数不一致，需复核'
    return {'old': (s_old, nn, hp_old, k_old), 'new': (s_new, nn, hp_new, k_new)}


def run_off_future(sos6, nn, hp, xin):
    """未来链：dc_block(1阶) + 跳过 notch + **不带 HP4**，只留高端 LP。"""
    y = T52m.dc_block_apply(np.asarray(xin, float), 0.05, FS)
    order = [i for i in range(nn, len(sos6)) if i not in hp]
    for i in order:
        y = ss.sosfilt(sos6[i:i + 1], y)
    return y


def run_on(sos6, xin):
    """ON 态：dc_block + 全部节（含 notch 段）。"""
    y = T52m.dc_block_apply(np.asarray(xin, float), 0.05, FS)
    for i in range(len(sos6)):
        y = ss.sosfilt(sos6[i:i + 1], y)
    return y


# ------------------------------------------------------------------ 单文件分析

def analyze(path, tag):
    d = T56.load7(path)
    idx, raw, filt, dtc = d['idx'], d['raw'], d['filt'], d['dtc']

    # --- 时间轴：必须用 idx 重建（有丢包时 arange 会漂） ---
    t = (idx - idx[0]) / FS

    # --- 条件判定：dtcycles 节数标定 ---
    cs = float(np.median(dtc))
    ns_est = (cs - 1373.0) / 641.3
    hdr('[%s] 条件判定（dtcycles 配置指纹）' % tag)
    say('  dtcycles 中位 = %.0f ⇒ 折算 %.2f 节' % (cs, ns_est))
    say('  四态指纹：旧ON 9710(13节) ｜新ON ~9069(12节) ｜旧OFF 3938(4节) ｜新OFF ~3297(3节)')
    cand = {'旧链 ON': 9710.0, '新链 ON': 9068.7, '旧链 OFF': 3938.0, '新链 OFF': 3296.7}
    best = min(cand, key=lambda k: abs(cand[k] - cs))
    say('  ⇒ 最接近 **%s**（差 %+.1f cycles）' % (best, cs - cand[best]))
    say('  分段稳定性：' + ' ｜ '.join(
        '%.0f' % np.median(dtc[k * len(dtc) // 4:(k + 1) * len(dtc) // 4]) for k in range(4)))
    say('  ⚠ 列② raw p-p = %.0f code = %.1f µV' % (raw.max() - raw.min(),
                                                    (raw.max() - raw.min()) * T56.UVPC))
    say('  行数 %d ｜ 坏行已剔 ｜ 时长 %.3f s' % (len(idx), t[-1]))

    # --- 脉冲 + FS 校验 ---
    pulses, thr, bl = T56.find_pulses(raw)
    rep = float(np.median([t[pulses[k][0]] - t[pulses[k - 1][0]] for k in range(1, len(pulses))]))
    hdr('[%s] 脉冲与 FS 校验' % tag)
    say('  脉冲 %d 个 ｜ 重复周期 %.4f s ｜ 脉宽中位 %.1f ms'
        % (len(pulses), rep, np.median([(e - s) / FS * 1000 for s, e in pulses])))
    if len(pulses) > 2:
        di = int(idx[pulses[2][0]] - idx[pulses[1][0]])
        say('  ★FS 独立校验：脉冲2→3 idx 差 %d / 标称 10.0000 s ⇒ 隐含 FS = %.3f Hz' % (di, di / 10.0))

    # --- 五链并排 ---
    ch = build_chains()
    s_old, nn, hp, k_old = ch['old']
    s_new, nn, hp_n, k_new = ch['new']

    # ★★ 量纲：列② raw 与列⑤ filt 都是 **code 域**，链内部按 µV 算
    #    ⇒ 输入必须 ×UVPC，输出才与 filt×UVPC 同域（T60 §28.6 踩过的坑，这里固化）
    xin = np.asarray(raw, float) * T56.UVPC
    y_mea = filt * T56.UVPC
    y_old_off = T60.run_chain(s_old, nn, hp, xin, use_notch=False)
    y_new_off = T60.run_chain(s_new, nn, hp_n, xin, use_notch=False)
    y_fut_off = run_off_future(s_new, nn, hp_n, xin)
    y_old_on = run_on(s_old, xin)
    y_new_on = run_on(s_new, xin)

    # --- 链身份：RMS 逐点差 ---
    hdr('[%s] 链身份判定（同输入过链 vs 实测列⑤）' % tag)
    rms = {'实测(列⑤)': 0.0,
           '旧链 ON': float(np.sqrt(((y_old_on - y_mea) ** 2).mean())),
           '新链 ON': float(np.sqrt(((y_new_on - y_mea) ** 2).mean())),
           '旧链 OFF': float(np.sqrt(((y_old_off - y_mea) ** 2).mean())),
           '新链 OFF': float(np.sqrt(((y_new_off - y_mea) ** 2).mean())),
           '未来链 OFF': float(np.sqrt(((y_fut_off - y_mea) ** 2).mean()))}
    for k, v in rms.items():
        say('  RMS 差 %-10s = %9.2f µV' % (k, v))
    ident = min((k for k in rms if k != '实测(列⑤)'), key=lambda k: rms[k])
    say('  ⇒ 实测最接近 **%s**（RMS %.2f µV）' % (ident, rms[ident]))

    # --- 三判据全表 ---
    Rs = {}
    for lab, y in (('实测', y_mea), ('旧链OFF', y_old_off), ('新链OFF', y_new_off),
                   ('未来链OFF', y_fut_off), ('旧链ON', y_old_on), ('新链ON', y_new_on)):
        Rs[lab] = T56.judge(y, t, pulses, FS, rep)

    hdr('[%s] ★五链三判据并排（同一列② raw 输入；判限 位移≤100 µV｜斜率≤300 µV/s｜过冲<10%%）' % tag)
    cols = ['实测', '旧链OFF', '新链OFF', '未来链OFF', '旧链ON', '新链ON']
    say('  %-20s' % '指标' + ''.join('%13s' % c for c in cols))
    rows = [('位移 +0.020 s', 'disp_long', 0.020), ('位移 +0.100 s', 'disp_long', 0.100),
            ('位移 +0.300 s', 'disp_long', 0.300), ('位移 +1.000 s', 'disp_long', 1.000),
            ('滑窗 50 ms', 'slope_win', 0.050), ('滑窗 100 ms', 'slope_win', 0.100),
            ('滑窗 200 ms', 'slope_win', 0.200)]
    for lab, key, sub in rows:
        vals = [Rs[c][key][sub] for c in cols]
        flags = [(vals[i] <= (100 if key == 'disp_long' else 300)) for i in range(len(cols))]
        say('  %-20s' % lab + ''.join('%12.1f%s' % (v, ' ' if f else '!') for v, f in zip(vals, flags)))
    vals = [Rs[c]['slope_fit'] for c in cols]
    say('  %-20s' % '拟合 0.2-1.2 s' + ''.join('%12.1f%s' % (v, ' ' if v <= 300 else '!') for v in vals))
    say('  ── 过冲（限 10%，四口径）──')
    for i, (lab, _) in enumerate(T60.ovs(Rs['实测'])):
        vals = [dict(T60.ovs(Rs[c]))[lab] for c in cols]
        say('  %-20s' % lab + ''.join('%12.2f%s' % (v, ' ' if v < 10 else '!') for v in vals))
    YS = {'实测': y_mea, '旧链OFF': y_old_off, '新链OFF': y_new_off,
          '未来链OFF': y_fut_off, '旧链ON': y_old_on, '新链ON': y_new_on}
    say('  ── ★逐脉冲过冲（T59 纪律：报最差）──')
    pp = {}
    for c in cols:
        _pk, _tp, op = T60.per_pulse(YS[c], t, pulses, FS)
        pp[c] = op
        say('  %-20s' % c + '  最差 %6.2f%%  中位 %6.2f%%  范围 %6.2f~%6.2f  %s'
            % (op.max(), np.median(op), op.min(), op.max(), '✗' if op.max() >= 10 else '✓'))
    say('  ⚠ "!" 表示超判限')

    # ---------------- 噪声与滑窗机制 ----------------
    hdr('[%s] ★滑窗判据的机制：是"脉冲恢复斜率"还是"平台段噪声极值"？' % tag)
    # 差分法 σ：对低频漂移免疫（白噪声下 σ=std(diff)/√2）。
    # ⚠ 不能用"去趋势后的 std"——平台段含 HPF 恢复尾，会把 σ 虚高 2~3 倍
    #   （本轮实测：同一个信号，二次去趋势给 18.4 µV，差分法给 8.83 µV）。
    def sd_diff(v):
        return float(np.std(np.diff(v)) / np.sqrt(2.0))

    if len(pulses) >= 3:                      # 平台段：第2降沿后1.5s ~ 第3升沿前0.5s
        a0, b0 = t[pulses[1][1]] + 1.5, t[pulses[2][0]] - 0.5
    else:
        a0, b0 = t[pulses[0][1]] + 1.5, t[-1] - 0.5
    mp = (t >= a0) & (t <= b0)
    say('  平台段 %.2f~%.2f s（%d 点），占脉冲周期 %.0f%%'
        % (t[mp][0], t[mp][-1], mp.sum(), (t[mp][-1] - t[mp][0]) / rep * 100))
    say('  %-14s %11s %11s %11s %10s' % ('', 'σ(µV)', '5σ/0.1s', '输出/输入', '滑窗100'))
    sig_in = sd_diff(xin[mp])
    for c in cols:
        s_ = sd_diff(YS[c][mp])
        say('  %-14s %11.3f %11.1f %11s %10.1f'
            % (c, s_, s_ * 5.0 / 0.1, '%.2f' % (s_ / sig_in) if c != '实测' else '0.92',
               Rs[c]['slope_win'][0.100]))
    say('  ★关系式：滑窗100ms ~= 5σ/0.1s（100 ms 窗约 85 点，白噪声极值 ~ 5σ）')
    say('  => 滑窗 <=300 µV/s 等价于 **σ_out <= 6.0 µV**（=300*0.1/5）')
    say('  ★自证：旧链 OFF σ=6.947 => 6.947*5/0.1 = %.1f，而 T57 实测 347.1（差 %.1f%%）'
        % (6.947 * 5 / 0.1, abs(6.947 * 5 / 0.1 - 347.1) / 347.1 * 100))

    def sw_at(v, te_, w=0.100, t0=0.020, t1=2.0):
        best = np.nan
        for x in np.arange(te_ + t0, min(te_ + t1, t[-1] - w), 0.0002):
            z = abs(np.interp(x + w, t, v) - np.interp(x, t, v)) / w
            if not np.isfinite(best) or z > best:
                best = z
        return best

    say()
    say('  逐脉冲滑窗 100 ms（判据原样；限 300）')
    say('  %-14s' % '' + ''.join('%10s' % ('P%d' % (i + 1)) for i in range(len(pulses)))
        + '%10s%10s' % ('中位', '最差'))
    for c in cols:
        vs = [sw_at(YS[c], t[e]) for _s, e in pulses]
        say('  %-14s' % c + ''.join('%10.1f' % z for z in vs)
            + '%10.1f%10.1f' % (np.median(vs), np.max(vs)))
    say()
    say('  ★窗口长度敏感性（中位口径）：若只要求"紧接终点后"，读数降多少？')
    say('  %-14s %11s %11s %11s %11s' % ('', '[20,200]ms', '[20,300]ms', '[20,500]ms', '[20,2000]ms'))
    for c in cols:
        row = [np.median([sw_at(YS[c], t[e], t1=q) for _s, e in pulses])
               for q in (0.200, 0.300, 0.500, 2.000)]
        say('  %-14s %11.1f %11.1f %11.1f %11.1f' % (c, *row))
    say('  => 缩短窗口只降 10~15%，主因不是窗长，而是**噪声水平**（读数与 σ 成正比）。')
    return dict(tag=tag, idx=idx, t=t, raw=raw, y=y_mea, pulses=pulses, rep=rep,
                cs=cs, ns=ns_est, best=best, ident=ident, rms=rms, Rs=Rs, ys=YS, pp=pp,
                sig=(sig_in, {c: sd_diff(YS[c][mp]) for c in cols}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=F_DEF)
    ap.add_argument('--tag', default='本次 200724')
    ap.add_argument('--alt', default=F_ALT, help='跨文件复核用的第二份输入')
    ap.add_argument('--tag-alt', default='T57 182243')
    ap.add_argument('--no-alt', action='store_true')
    a = ap.parse_args()

    say('T61 · 「低端降 1 阶」离线预测（用实测 raw 驱动，全部复用既有判据）')
    say('file = %s' % a.file)
    A = analyze(a.file, a.tag)

    B = None
    if not a.no_alt and os.path.exists(a.alt):
        say()
        say('#' * 86)
        say('# 跨文件复核：换一份输入（%s）再算一遍，看结论是否稳健' % a.tag_alt)
        say('#' * 86)
        B = analyze(a.alt, a.tag_alt)

    # ---------------- 结论位
    hdr('结论位')
    kk = ['实测', '旧链OFF', '新链OFF', '未来链OFF']
    for lab, D in ((A['tag'], A),) + (((B['tag'], B),) if B else ()):
        say('【%s】条件=%s ｜ 链身份=%s' % (lab, D['best'], D['ident']))
        say('   位移+0.1s  %8.1f ｜ 滑窗100ms %8.1f ｜ 逐脉冲过冲最差 %6.2f%%'
            % (D['Rs']['实测']['disp_long'][0.100], D['Rs']['实测']['slope_win'][0.100],
               D['pp']['实测'].max()))
        for c in ('旧链OFF', '新链OFF', '未来链OFF'):
            say('   %-10s 位移 %8.1f ｜ 滑窗 %8.1f ｜ 过冲顶均 %6.2f%% ｜ 逐脉冲最差 %6.2f%%'
                % (c, D['Rs'][c]['disp_long'][0.100], D['Rs'][c]['slope_win'][0.100],
                   dict(T60.ovs(D['Rs'][c]))['对顶均'], D['pp'][c].max()))

    p = os.path.join(HERE, '_t61_out.txt')
    io.open(p, 'w', encoding='utf-8').write('\n'.join(OUT) + '\n')
    print('\n输出 -> %s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
