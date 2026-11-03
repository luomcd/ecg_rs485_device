# -*- coding: utf-8 -*-
"""
T48b · 「off→on 切换瞬态」的成因与对策（离线实验）

背景：T48 实测发现 `test notch off` → `on` 时有一个 ~130 µV、持续数百 ms 的瞬态。
成因：`ecg_filter_*_set_notch()` **双向都清零**陷波节 d1/d2 ⇒ 切回 on 时 9 个 Q20
      陷波节从**零状态**冷启动，其输出从 0 爬到平台值，BP 节（在后 4 节）于是看到
      一个 ≈平台幅度的阶跃 ⇒ 瞬态。Q20 极点半径 ≈0.991 ⇒ τ ≈ 200 ms。

本脚本对比三种「切回 on」策略，并与「从不切换」的理想参考比较：
  A 现行   切回时清陷波节状态（= 固件现状）
  B 不清   保留旁路期间冻结的状态（= set_notch 完全不碰状态）
  C DC预置 切回时把陷波节状态置为「当前输入样本对应的直流稳态」
           d1 = v(1-b0)；d2 = v(b2-a2)   （每节 DC 增益为 1）

并额外测试**旁路期间信号发生变化**时 B 是否仍然安全（这是 B 的唯一风险）：
  steady  旁路期间信号不变（信号的常规工况）
  amp2    旁路期间方波幅度翻倍（电极接触/增益变化）
  dc      旁路期间叠加 +1 mV 直流（极化/漂移）

用法：
  python _t48b_offon_variants.py
  python _t48b_offon_variants.py --mains-uv 12 --off-s 1,5,31 --fig
"""
import argparse
import importlib.util
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# 复用 T47 已验证的系数解析器（避免重复实现 / 重复踩坑）
_spec = importlib.util.spec_from_file_location(
    't47sim', os.path.join(HERE, '_t47_notch_switch.py'))
T47 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T47)
FS = T47.FS


def step(sos, state, v, first, nsec):
    """单样本 DF2T，从第 first 节跑到第 nsec-1 节（first 即固件 process 的循环起点）。"""
    for i in range(first, nsec):
        b0, b1, b2, a1, a2 = sos[i]
        d = state[i]
        w = b0 * v + d[0]
        d[0] = b1 * v - a1 * w + d[1]
        d[1] = b2 * v - a2 * w
        v = w
    return v


def run(sos, state, x, first, nsec):
    y = np.empty(len(x))
    for k in range(len(x)):
        y[k] = step(sos, state, x[k], first, nsec)
    return y


def clear_notch(state, n_notch):
    for i in range(n_notch):
        state[i][0] = 0.0
        state[i][1] = 0.0


def dc_preset(sos, state, v, n_notch):
    """把陷波节置为「直流稳态」：该节 DC 增益为 1，故稳态时输出 == 输入。

    由 DF2T 稳态条件（w==v）解出：d1 = v(1-b0)；d2 = v(b2-a2)。
    """
    for i in range(n_notch):
        b0, b1, b2, a1, a2 = sos[i]
        state[i][0] = v * (1.0 - b0)
        state[i][1] = v * (b2 - a2)


def make_input(n, amp_mv=1.0, mains_uv=12.0, f0=1.0, prf=0.5, phi=0.0):
    """±amp/2 方波（f0 Hz，占空比 prf）+ 50 Hz 工频。单位 µV。"""
    t = np.arange(n) / FS
    sq = np.where((t % (1.0 / f0)) < (prf / f0), 1.0, -1.0) * (amp_mv * 1000.0 / 2.0)
    return sq + mains_uv * np.sin(2 * np.pi * 50.0 * t + phi)


VARIANTS = {
    'steady': '旁路期间信号不变（常规工况）',
    'amp2':   '旁路期间方波幅度翻倍（接触/增益变化）',
    'dc':     '旁路期间叠加 +1 mV 直流（极化/漂移）',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='IMP620_N2', choices=list(T47.PROFILES))
    ap.add_argument('--mains-uv', type=float, default=12.0, help='叠加工频幅度 (µV)；低阻实测≈12')
    ap.add_argument('--amp-mv', type=float, default=1.0, help='方波幅度 (mV)')
    ap.add_argument('--off-s', default='1,5,31', help='旁路时长列表 (s)，逗号分隔')
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    macro, _, _ = T47.PROFILES[a.profile]
    sos, n_notch = T47.parse_coeffs_f64(T47.COEFFS_C, T47.COEFFS_H, macro)
    nsec = len(sos)
    print('档位 %s：%d 节（陷波 %d ＋ BP %d）｜输入 %.2f mV 方波 + %.1f µV 工频'
          % (a.profile, nsec, n_notch, nsec - n_notch, a.amp_mv, a.mains_uv))

    k_pre = int(1.0 * FS)
    k_post = int(1.2 * FS)
    offs = [float(v) for v in a.off_s.split(',')]
    names = ['A现行', 'B不清', 'C DC预置']

    print('\n%-8s %5s %-9s %9s %11s %11s' %
          ('场景', '旁路s', '策略', '峰值µV', '稳@10µV(ms)', '稳@5µV(ms)'))
    print('-' * 62)
    store = {}
    for vkey, vdesc in VARIANTS.items():
        for D in offs:
            k_off = int(D * FS)
            n = k_pre + k_off + k_post
            x = make_input(n, a.amp_mv, a.mains_uv, phi=0.37)
            if vkey == 'amp2':
                x[k_pre:] *= 2.0
            elif vkey == 'dc':
                x[k_pre:] += 1000.0

            ref = run(sos, T47.newstate(nsec), x, 0, nsec)   # 从不切换（理想下界）
            yref = ref[k_pre + k_off:]

            for name in names:
                st = T47.newstate(nsec)
                run(sos, st, x[:k_pre], 0, nsec)             # 切换前（on）
                snap = T47.snap(st)
                if name == 'A现行':                          # off：固件现状（也清）
                    clear_notch(st, n_notch)
                run(sos, st, x[k_pre:k_pre + k_off], n_notch, nsec)
                if name == 'A现行':
                    clear_notch(st, n_notch)
                elif name == 'B不清':
                    for i in range(n_notch):
                        st[i][0], st[i][1] = snap[i][0], snap[i][1]
                else:
                    dc_preset(sos, st, x[k_pre + k_off - 1], n_notch)

                y = run(sos, st, x[k_pre + k_off:], 0, nsec)
                d = np.abs(y - yref)
                pk = d.max()

                def settle(th):
                    idx = np.where(d > th)[0]
                    return (idx[-1] + 1) / FS * 1000.0 if len(idx) else 0.0

                print('%-8s %5.0f %-9s %9.1f %11.1f %11.1f'
                      % (vkey, D, name, pk, settle(10.0), settle(5.0)))
                store[(vkey, D, name)] = (y - yref)[:int(0.6 * FS)]
            print('-' * 62)

    print('参照：不切换时相邻样本差 p99.9 = %.1f µV（判断"可见/不可见"）'
          % np.percentile(np.abs(np.diff(run(sos, T47.newstate(nsec),
                                             make_input(k_post * 2, a.amp_mv, a.mains_uv, phi=.37),
                                             0, nsec))), 99.9))

    if not a.fig:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False

    D = offs[len(offs) // 2]
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.6), sharey=True)
    for j, (vkey, vdesc) in enumerate(VARIANTS.items()):
        for name, c in [('A现行', '#d62728'), ('B不清', '#1f77b4'), ('C DC预置', '#2ca02c')]:
            v = store[(vkey, D, name)]
            ax[j].plot(np.arange(len(v)) / FS * 1000, v, lw=1.1, color=c, label=name)
        ax[j].axhline(0, color='k', lw=.6)
        ax[j].set_title('%s（旁路 %.0f s）' % (vdesc, D), fontsize=10)
        ax[j].set_xlabel('切回 on 后 (ms)'); ax[j].grid(alpha=.3)
        if j == 0:
            ax[j].set_ylabel('相对"从不切换"的偏差 (µV)'); ax[j].legend(fontsize=9)
    plt.suptitle('T48b · off→on 切换瞬态：三种策略 × 三种旁路期信号变化', fontsize=12)
    plt.tight_layout()
    out = 'T48b_offon切换瞬态_方案对比_2026-09-20.png'
    plt.savefig(out, dpi=125)
    print('\n图已保存：%s' % os.path.abspath(out))


if __name__ == '__main__':
    main()
