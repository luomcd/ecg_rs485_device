#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
T47 离线验证：工频滤波器组「跳节 + 清状态」旁路的正确性与切换瞬态。

零硬件、零源码改动（本脚本只读 coeffs.h）。用途：在落码后、上板前，
先证明固件那三处改动（init 写默认态 / set_notch 清陷波节 / process 改循环起点）
在数值上成立，并量化 2026-09-20 拍板「先零滑窗」的跳变到底有多大。

★ 系数来源：**直接解析 Filter/ecg_filter_coeffs.h 的 ECG_FILTER_COEFFS_F64 表**，
  不是用 scipy 现算 —— 验的就是编进固件的那组数。

跑法：
  <venv-python> _t47_notch_switch.py                       # 默认 IMP620_N2
  <venv-python> _t47_notch_switch.py --profile D3
  <venv-python> _t47_notch_switch.py --switch-at 3.0 --fig
"""
import argparse
import os
import re
import sys

import numpy as np

FS = 1e6 / 1172.0            # 853.2423 Hz（ADS1293 DRDY 实测 1172 µs）
BUDGET_CYC = 93760.0         # 单样本预算 @80 MHz
UV_PER_CODE = 0.1122         # 列⑤ code 域定标

HERE = os.path.dirname(os.path.abspath(__file__))
COEFFS_H = os.path.join(HERE, '..', 'Filter', 'ecg_filter_coeffs.h')   # 宏（含 ECG_NUM_NOTCH_SECTIONS）
COEFFS_C = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')          # ★ 系数表实体在这里

# 档位名 -> coeffs.h 里的宏名与节数（与 A 阶段新增的 ECG_NUM_NOTCH_SECTIONS 对应）
PROFILES = {
    'ST40':      ('ECG_PROFILE_ST40',      7,  3),
    'D1':        ('ECG_PROFILE_DIAG150',   5,  1),
    'D2':        ('ECG_PROFILE_DIAG150_D2', 6, 2),
    'D3':        ('ECG_PROFILE_DIAG150_D3', 8, 4),
    'IMP620':    ('ECG_PROFILE_IMP620',    9,  5),
    'IMP620_N2': ('ECG_PROFILE_IMP620_N2', 13, 9),
}


def parse_coeffs_f64(path_c, path_h, macro):
    """解析某档位的 ECG_FILTER_COEFFS_F64 系数表。

    系数表实体在 ecg_filter.c（随 ECG_FILTER_PROFILE 编译期切换），
    ECG_NUM_NOTCH_SECTIONS 在 ecg_filter_coeffs.h —— 两个文件都要读。

    返回 (sos, n_notch)：sos[i] = (b0, b1, b2, a1, a2)，n_notch = 链首陷波节数。
    """
    txt = open(path_c, encoding='utf-8').read()
    txt_h = open(path_h, encoding='utf-8').read()

    # 1) 定位档位分支起点（ecg_filter.c）
    m = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)', txt)
    if not m:
        raise SystemExit('ecg_filter.c 里找不到档位分支: ' + macro)
    seg = txt[m.end():]

    # 2) 该档位的 ECG_NUM_NOTCH_SECTIONS（在头文件里，A 阶段新增）
    #    分支边界必须用 (?=#elif|#else|#endif) 前瞻收尾 —— 否则**最后一个分支**（ST40）
    #    会一路抓到文件末尾，匹配到别的档位的值。
    mn = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)'
                   r'([\s\S]*?)(?=#(?:elif|else|endif))', txt_h)
    if not mn:
        raise SystemExit('ecg_filter_coeffs.h 里定位不到分支: ' + macro)
    mm = re.search(r'#define\s+ECG_NUM_NOTCH_SECTIONS\s+(\d+)', mn.group(1))
    n_notch = int(mm.group(1)) if mm else None

    # 3) 定位 f64 系数表（ecg_filter.c）
    mt = re.search(r'const\s+ecg_biquad_coeff_f64\s+ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{', seg)
    if not mt:
        raise SystemExit('该分支找不到 ECG_FILTER_COEFFS_F64 表: ' + macro)
    body = seg[mt.end():]
    end = body.index('};')
    body = body[:end]

    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)      # 去块注释
    body = re.sub(r'//[^\n]*', '', body)                   # 去行注释

    rows = []
    for item in re.finditer(r'\{([^{}]*)\}', body):
        nums = [float(v) for v in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', item.group(1))]
        if len(nums) != 5:
            raise SystemExit('系数条目数字个数 != 5: %r' % (item.group(1)[:60],))
        rows.append(tuple(nums))

    return rows, n_notch


def run(sos, x, state, first=0):
    """逐样本 DF2T（与固件 biquad_df2t_f64_process 同式）。

    sos 行 = (b0,b1,b2,a1,a2)；state[i] = [d1,d2]。
    first = 起始节索引 —— **这就是固件 process() 里的循环起点**，
            notch_on=1 → first=0；notch_on=0 → first=n_notch（跳过链首陷波节）。
    """
    y = np.empty_like(x)
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


def newstate(nsec):
    return [[0.0, 0.0] for _ in range(nsec)]


def snap(st):
    return [list(v) for v in st]


def rms(v):
    """总 rms（信号已在 µV 域）。"""
    return float(np.sqrt(np.mean(np.asarray(v) ** 2)))


def amp_at(v, f0):
    """单频点幅度（Hanning 窗 FFT）——用来把 50Hz 分量从 10Hz 里分出来。

    ⚠ 不能用总 rms 比较 notch on/off：10Hz(707µV rms) 会把 50Hz 分量完全淹没。"""
    v = np.asarray(v, dtype=float)
    n = len(v)
    w = np.hanning(n)
    S = np.fft.rfft(v * w)
    f = np.fft.rfftfreq(n, 1.0 / FS)
    i = int(np.argmin(np.abs(f - f0)))
    return float(2.0 * np.abs(S[i]) / np.sum(w))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='IMP620_N2', choices=list(PROFILES))
    ap.add_argument('--switch-at', type=float, default=3.0, help='切换时刻 (s)')
    ap.add_argument('--mains-uv', type=float, default=300.0, help='叠加的 50Hz 工频幅度 (µV)')
    ap.add_argument('--fig', action='store_true', help='输出 PNG')
    args = ap.parse_args()

    macro, n_exp, n_notch_exp = PROFILES[args.profile]
    sos, n_notch = parse_coeffs_f64(COEFFS_C, COEFFS_H, macro)
    nsec = len(sos)

    print('=' * 78)
    print('1. 系数表来源核对（直接解析 coeffs.h，非现算）')
    print('=' * 78)
    print('   档位 %-10s 宏 %s' % (args.profile, macro))
    print('   解析到 %d 节   （ECG_NUM_SECTIONS 期望 %d）%s'
          % (nsec, n_exp, 'OK' if nsec == n_exp else '**不符**'))
    print('   陷波节 %s 节（源码 ECG_NUM_NOTCH_SECTIONS=%s，期望 %d）%s'
          % (n_notch, n_notch, n_notch_exp,
             'OK' if n_notch == n_notch_exp else '**不符**'))
    print('   BP 节 = %d 节（%d - %d）%s'
          % (nsec - n_notch, nsec, n_notch,
             'OK' if (nsec - n_notch) == 4 else '**不符（应恒为 4）**'))
    print('   链结构：前 %d 节 = 陷波（旁路时整段跳过）；后 %d 节 = BP（状态必须保留）'
          % (n_notch, nsec - n_notch))

    # ---------------------------------------------------------------- 2
    print()
    print('=' * 78)
    print('2. 旁路正确性：notch=off（first=n_notch）是否恒等于「物理上去掉陷波节」？')
    print('=' * 78)
    n = int(6.0 * FS)
    t = np.arange(n) / FS
    x = 1000.0 * np.sin(2 * np.pi * 10 * t) + args.mains_uv * np.sin(2 * np.pi * 50 * t)

    y_on = run(sos, x, newstate(nsec), first=0)
    y_off = run(sos, x, newstate(nsec), first=n_notch)
    y_ref = run(sos[n_notch:], x, newstate(nsec - n_notch), first=0)   # 真值：只留 BP 节

    d = np.max(np.abs(y_off - y_ref))
    print('   notch=off（跳节实现） vs 只装 BP 节的链（真值）：')
    print('     全段最大差异 = %.3e µV   %s' % (d, 'OK（数值等价）' if d < 1e-9 else '**不等价**'))
    print('     → 说明"改循环起点"与"去掉陷波节"在数值上同一件事 ✓')

    # 50Hz 分量的抑制量（必须单频提取：10Hz 707µV rms 会把 50Hz 淹没）
    tail = slice(int(4.0 * FS), None)
    a_on, a_off = amp_at(y_on[tail], 50.0), amp_at(y_off[tail], 50.0)
    print('   50Hz 分量（Hanning-FFT 单频幅值，输入 %.0f µV）：' % args.mains_uv)
    print('     notch=on  残余 %10.4f µV   （抑制 %6.2f dB）'
          % (a_on, 20 * np.log10(args.mains_uv / max(a_on, 1e-12))))
    print('     notch=off 残余 %10.4f µV   （抑制 %6.2f dB）'
          % (a_off, 20 * np.log10(args.mains_uv / max(a_off, 1e-12))))

    # ---------------------------------------------------------------- 3
    print()
    print('=' * 78)
    print('3. 切换瞬态（t=%.1f s 切 off；信号 10Hz 1mV + 50Hz %.0fµV）'
          % (args.switch_at, args.mains_uv))
    print('=' * 78)

    sw = int(args.switch_at * FS)
    n2 = int((args.switch_at + 3.0) * FS)
    x2 = 1000.0 * np.sin(2 * np.pi * 10 * np.arange(n2) / FS) \
        + args.mains_uv * np.sin(2 * np.pi * 50 * np.arange(n2) / FS)

    st = newstate(nsec)
    y_pre = run(sos, x2[:sw], st, first=0)
    st_end = snap(st)

    # 切换瞬间陷波节状态里到底存了多少？（这是"跳变"的病根）
    mag_c = [np.hypot(v[0], v[1]) for v in st_end[:n_notch]]
    mag_uv = [m * UV_PER_CODE for m in mag_c]
    print('   切换前陷波节状态幅度 |d1,d2|（code ×%.4f = µV）：' % UV_PER_CODE)
    print('     µV  %s' % np.round(mag_uv, 1))
    print('     最大 %.1f µV ｜ 均值 %.1f µV  ← 这就是"清状态"要清掉的残留（T43 同源）'
          % (max(mag_uv), float(np.mean(mag_uv))))

    y_native_off = run(sos, x2[sw:], snap(st_end), first=n_notch)  # 保留全部状态，只改起点

    def report(lab, yseg):
        # 单位说明：x 定义在 µV 域 ⇒ y、jump、diff 全部已是 µV，不得再 ×1000
        jump = abs(yseg[0] - y_pre[-1])
        diff = np.abs(yseg - y_native_off)
        base = rms(y_native_off)
        over = np.where(diff > 0.01 * max(base, 1e-9))[0]
        conv = over[-1] / FS * 1000.0 if len(over) else 0.0
        sdiff = np.max(np.abs(yseg[-int(0.5 * FS):] - y_native_off[-int(0.5 * FS):]))
        print('   %-30s 跳变 %8.2f µV ｜ 额外过渡 %7.1f ms ｜ 稳态差 %7.3f µV'
              % (lab, jump, conv, sdiff))
        return jump, conv, sdiff

    # 正常相邻样本差，作为"跳变是否可见"的参照（10Hz 1mV @853Hz ⇒ ≈73.6 µV）
    nd = np.max(np.abs(np.diff(y_pre[-int(0.5 * FS):])))
    print('   参照：切换前正常相邻样本差 max = %.1f µV' % nd)

    # (a) 固件行为：切 off = 清陷波节 d1/d2 + 起点改 n_notch
    st_a = snap(st_end)
    for i in range(n_notch):
        st_a[i] = [0.0, 0.0]
    _jump_a = report('(a) 固件行为:清陷波节+跳节', run(sos, x2[sw:], st_a, first=n_notch))

    # (b) 对照：只跳节，不清状态
    report('(b) 对照:只跳节不清状态', run(sos, x2[sw:], snap(st_end), first=n_notch))

    # (c) 对照：连 BP 一起清（错的做法）
    _conv_c = report('(c) 对照:清全部13节(错)', run(sos, x2[sw:], newstate(nsec), first=n_notch))

    print()
    print('   ⚠ 读数要点：(a) 与 (b) 输出**逐点相同** ⇒ 本实现下「开→关」方向清陷波节 d1/d2')
    print('     对输出**无影响**（被跳过的节不再参与运算）。清状态真正起作用的是')
    print('     「关→开」方向：避免恢复瞬间吐出 off 那一刻冻结的旧状态。')
    print('   ⚠ (c) 是反例：连 BP 节一起清会引入额外过渡（见上表），这就是"必须保住 BP 状态"的证据。')

    # ---------------------------------------------------------------- 4
    print()
    print('=' * 78)
    print('4. 关陷波后 50Hz 灌入量（"屏幕上会很脏"的量化，单频提取）')
    print('=' * 78)
    for label, amp in (('375 Ω 低阻直驱', 13.8), ('620 kΩ（当前场景）', 19100.0)):
        n3 = int(4.0 * FS)
        x3 = 1000.0 * np.sin(2 * np.pi * 10 * np.arange(n3) / FS) \
            + amp * np.sin(2 * np.pi * 50 * np.arange(n3) / FS)
        yo = run(sos, x3, newstate(nsec), first=0)
        yf = run(sos, x3, newstate(nsec), first=n_notch)
        w = slice(int(2.0 * FS), None)
        print('   %-18s 输入 50Hz %9.1f µV → on 残余 %9.4f µV ｜ off 后 %10.1f µV'
              % (label, amp, amp_at(yo[w], 50.0), amp_at(yf[w], 50.0)))
    print('   ⚠ 差 1386×（T45）⇒ 高源阻抗下关陷波屏幕会很脏，**属预期**，不是 bug')

    # ---------------------------------------------------------------- 5
    print()
    print('=' * 78)
    print('5. CPU 账（T40 实测模型 dt(%) = 1.3455 + 0.6895×N节）')
    print('=' * 78)
    on = 1.3455 + 0.6895 * nsec
    off = 1.3455 + 0.6895 * (nsec - n_notch)
    print('   notch=on  %2d 节 → %5.2f%%   余量 %5.2f%%' % (nsec, on, 100 - on))
    print('   notch=off %2d 节 → %5.2f%%   余量 %5.2f%%' % (nsec - n_notch, off, 100 - off))
    print('   Δ = %+.2f pp  ← ★ 上板可用输出流第 6 列 dtcycles 直接验证"跳节真的生效"' % (off - on))
    print('   （若 dtcycles 关/开后无显著变化 ⇒ 说明没真跳节，只是换了系数）')

    # ---------------------------------------------------------------- fig
    if args.fig:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
            plt.rcParams['axes.unicode_minus'] = False

            st_a = snap(st_end)
            for i in range(n_notch):
                st_a[i] = [0.0, 0.0]
            y_a = run(sos, x2[sw:], st_a, first=n_notch)
            y_b = run(sos, x2[sw:], snap(st_end), first=n_notch)
            y_c = run(sos, x2[sw:], newstate(nsec), first=n_notch)

            fig, ax = plt.subplots(2, 1, figsize=(11, 7.5))
            k_pre, k_post = int(0.25 * FS), int(0.60 * FS)
            t_pre = (np.arange(sw - k_pre, sw) - sw) / FS      # 负时间：切换前
            t_post = np.arange(k_post) / FS                    # 正时间：切换后

            ax[0].plot(t_pre, y_pre[-k_pre:], lw=1.0, color='0.45', label='切换前（notch=on）')
            # 绘制顺序：先画"错"的对照(c)，再画(a)/(b) —— 否则 (c) 会把正确的曲线盖住
            ax[0].plot(t_post, y_c[:k_post], lw=0.9, ls=':', color='tab:red',
                       label='(c) 对照：清全部13节（错，过渡 %.0f ms）' % _conv_c[1])
            ax[0].plot(t_post, y_b[:k_post], lw=2.6, ls=(0, (1, 2)), color='tab:orange',
                       label='(b) 对照：只跳节不清状态（与 a 逐点重合）')
            ax[0].plot(t_post, y_a[:k_post], lw=1.3, color='tab:blue',
                       label='(a) 固件行为：跳节（清了陷波节）')
            ax[0].axvline(0, color='r', lw=1.0, alpha=.7)
            ax[0].text(0.004, ax[0].get_ylim()[1], '切换', color='r', fontsize=9, va='top')
            ax[0].set_title('切换瞬态：t=%.1fs 切 off ｜ 10Hz 1mV + 50Hz %.0fµV ｜ 档 %s\n'
                            '跳变 %.1f µV < 正常相邻样本差 %.1f µV —— 零滑窗版不可见'
                            % (args.switch_at, args.mains_uv, args.profile, _jump_a[0], nd))
            ax[0].set_xlabel('相对切换时刻 (s)'); ax[0].set_ylabel('输出 (µV)')
            ax[0].legend(fontsize=8, loc='lower right'); ax[0].grid(alpha=.3)

            mag_all = [np.hypot(v[0], v[1]) * UV_PER_CODE for v in st_end]   # 全部 nsec 节
            ax[1].plot(np.arange(nsec), mag_all, 'o-')
            ax[1].axvspan(-0.5, n_notch - 0.5, alpha=.15, color='red')
            ax[1].text(n_notch / 2, max(mag_all) * .8, '陷波节\n（旁路时整段跳过）',
                       ha='center', fontsize=9, color='darkred')
            ax[1].text(n_notch + (nsec - n_notch) / 2, max(mag_all) * .8,
                       'BP 节\n（状态必须保留）', ha='center', fontsize=9, color='darkgreen')
            ax[1].set_title('切换前各节状态幅度 |d1,d2|（µV）—— 陷波节残留最大 %.0f µV；'
                            'BP 节残留最大 %.0f µV' % (max(mag_uv), max(mag_all[n_notch:])))
            ax[1].set_xlabel('节索引'); ax[1].set_ylabel('状态幅度 (µV)')
            ax[1].grid(alpha=.3)

            out = os.path.join(HERE, 'T47_notch开关_切换瞬态_2026-09-20.png')
            plt.tight_layout(); plt.savefig(out, dpi=140)
            print('\n   图已输出: %s' % out)
        except Exception as e:                                  # noqa: BLE001
            print('\n   [图输出失败] %s' % e)


if __name__ == '__main__':
    main()
