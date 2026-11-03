# -*- coding: utf-8 -*-
"""
T48 · 「工频滤波器组旁路」开关的**实测数据**验证
输入：1 mV 方波，notch on -> off -> on（2026-09-20 11:19:18 录制）
用法：
  python _t48_square_verify.py --file "1mv-square-notch(on-off-on)2026-09-20 111918.txt"
  python _t48_square_verify.py --file ... --fig      # 出图

数据列义（7 列文本，实测确认）：
  列1  t（强制 0，不用）
  列2  ADS1293 原始 code（含 ~6.07M 偏移）
  列3  输出样本序号（500 Hz 域，起始 512450）
  列4  输出 int16 LSB（0.1526 µV/LSB）
  列5  code 域值（0.1122 µV/code）   ← 列5/列4 = 1.3601 = 0.1526/0.1122
  列6  dtcycles（每样本滤波+重采样耗时，80 MHz 主频）
  列7  忽略（重新 start 后清 0）

判据：
  - on->off 切换跳变应 <= 正常相邻样本差（离线预算 54~70 µV）
  - dtcycles 在 off 后应显著下降（跳节生效的自检指标）
"""
import argparse
import io
import os

import numpy as np
from scipy.ndimage import median_filter, binary_dilation

UV = 0.1122          # code 域 µV
LSB = 0.1526         # 输出 LSB µV
FS = 500.0           # 输出采样率
CLK = 80e6           # 主频，用于 cycles -> %
PERIOD_CYC = CLK * 1172e-6   # 一个样本周期对应的 cycles (≈93760)


def load(path):
    rows = []
    bad = 0
    for line in io.open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if len(p) < 6:
            bad += 1
            continue
        try:
            rows.append([float(t) for t in p[:6]])
        except ValueError:
            bad += 1
    return np.array(rows), bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default='1mv-square-notch(on-off-on)2026-09-20 111918.txt')
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    d, nbad = load(a.file)
    x = d[:, 4] * UV            # µV
    c6 = d[:, 5]
    n = len(d)
    print('样本 %d（丢弃文本坏行 %d）｜时长 %.2f s' % (n, nbad, n / FS))

    # ---- 分段：用 dtcycles 台阶 ----
    thr = (c6.min() + c6.max()) / 2
    off = np.where(c6 < thr)[0]
    b1, b2 = off[0], off[-1]
    segs = {'on1': (0, b1), 'off': (b1, b2 + 1), 'on2': (b2 + 1, n)}

    print('\n[1] 开关自检：dtcycles')
    for k, (s, e) in segs.items():
        m = c6[s:e].mean()
        print('   %-4s %8.1f cycles = %5.2f%% CPU   (%.2f s)'
              % (k, m, 100 * m / PERIOD_CYC, (e - s) / FS))
    e_on = (c6[:b1].mean() + c6[b2 + 1:].mean()) / 2
    print('   ⇒ 跳节省 %.1f cycles = %.2f pp (9 节, %.0f cycles/节)'
          % (e_on - c6[b1:b2 + 1].mean(),
             100 * (e_on - c6[b1:b2 + 1].mean()) / PERIOD_CYC,
             (e_on - c6[b1:b2 + 1].mean()) / 9))

    # ---- 剥方波看残差 ----
    base = median_filter(x, size=101, mode='nearest')
    res = x - base
    mid = (base.max() + base.min()) / 2
    hi = np.median(base[base > mid]); lo = np.median(base[base < mid])

    def spec(y):
        w = np.hanning(len(y))
        Y = np.fft.rfft((y - y.mean()) * w)
        fr = np.fft.rfftfreq(len(y), 1 / FS)
        amp = 2 * np.abs(Y) / w.sum()
        g = lambda fc, bw=1.5: amp[(fr > fc - bw) & (fr < fc + bw)].max()
        return g(50), g(100), g(150), fr, amp

    print('\n[2] 残差（已剥方波）｜方波平台 高%+.0f 低%+.0f p-p %.1f µV（标称 1000）'
          % (hi, lo, hi - lo))
    print('   %-4s %10s %10s %10s %10s' % ('段', 'RMS µV', '50Hz', '100Hz', '150Hz'))
    S = {}
    for k, (s, e) in segs.items():
        y = res[s:e]
        r50, r100, r150, fr, amp = spec(y)
        S[k] = (y, fr, amp)
        print('   %-4s %10.2f %10.2f %10.2f %10.2f'
              % (k, np.sqrt(np.mean(y ** 2)), r50, r100, r150))
    print('   ⇒ 含边沿振铃口径：50Hz ×%.1f  100Hz ×%.1f'
          % (spec(res[b1:b2 + 1])[0] / spec(res[:b1])[0],
             spec(res[b1:b2 + 1])[1] / spec(res[:b1])[1]))

    # 口径二：剔除方波边沿（含其后振铃区）→ 纯工频灌入口径
    # 注意：中值滤波保护边沿（不像线性滤波会抹平），所以 res 本身在边沿处并不异常；
    #       这里改用 diff(base) 定位边沿，再剔除 ±100 ms（振铃区）。
    eb = np.abs(np.diff(base)) > 300
    eb = np.concatenate([[False], eb])
    eb = binary_dilation(eb, iterations=80)      # 用形态学膨胀，避免同数组扩张
    em = ~eb
    print('\n[2b] 剔除边沿±80 样本（160 ms）后（纯工频口径，无振铃污染）')
    print('   %-4s %8s %10s %10s %10s %10s' % ('段', 'n', 'RMS µV', '50Hz', '100Hz', '150Hz'))
    for k, (s, e) in segs.items():
        idx = np.arange(s, e)[em[s:e]]
        r50, r100, r150, _, _ = spec(res[idx])
        print('   %-4s %8d %10.2f %10.2f %10.2f %10.2f'
              % (k, len(idx), np.sqrt(np.mean(res[idx] ** 2)), r50, r100, r150))

    # ---- 切换瞬态 ----
    print('\n[3] 切换瞬态')
    for tag, sw in [('on->off', b1), ('off->on', b2 + 1)]:
        jump = x[sw] - x[sw - 1]
        w = 60
        after = np.abs(np.diff(x[sw:sw + w]))
        print('   %-8s 单步跳变 %+7.2f µV ｜ 随后 %d 样本 |Δ|max %7.2f µV'
              % (tag, jump, w, after.max()))

    # ---- 边沿响应（对齐平均，去平台/droop 趋势）----
    ed = np.where(np.abs(np.diff(base)) > 300)[0]
    iv = np.diff(ed); g = iv > 150
    ed = ed[:-1][g][np.append(True, iv[g][1:] < 400)]

    def ring(s, e, up, W=240):
        acc = []
        for p in ed:
            if p < s + 20 or p + W > e:
                continue
            if abs(p - b1) < 300 or abs(p - b2) < 300:
                continue
            dr = base[p + 1] - base[p]
            if (up and dr < 0) or ((not up) and dr > 0):
                continue
            acc.append(x[p:p + W])
        if not acc:
            return None, 0
        m = np.mean(acc, axis=0); t = np.arange(W)
        pf = np.polyfit(t[60:220], m[60:220], 1)
        return (m - np.polyval(pf, t)) * np.sign(m[12] - np.polyval(pf, 12)), len(acc)

    print('\n[4] 方波边沿响应（对齐平均，去 droop 趋势）µV')
    JJ = [0, 8, 16, 24, 40, 60, 80, 100, 120, 160, 200]
    print('   %-4s %-4s %4s %s' % ('段', '方向', 'n', ' '.join('%5d' % (j * 2) for j in JJ)) + '  ← ms')
    R = {}
    for k, (s, e) in segs.items():
        for up, nm in [(True, '正'), (False, '负')]:
            m, c = ring(s, e, up)
            if m is None:
                continue
            R[(k, nm)] = m
            print('   %-4s %-4s %4d %s' % (k, nm, c, ' '.join('%5.0f' % m[j] for j in JJ)))
    print('\n   边沿后 20~240 ms 残余 RMS：')
    for k in ['on1', 'off', 'on2']:
        for nm in ['正', '负']:
            if (k, nm) in R:
                print('      %-4s %s跳 %6.1f µV' % (k, nm, np.sqrt(np.mean(R[(k, nm)][10:120] ** 2))))

    # ---- 数据质量 ----
    seq = d[:, 2]
    gp = np.where(np.diff(seq) != 1)[0]
    print('\n[5] 数据质量：序号跳变 %d 处 %s'
          % (len(gp), [(int(seq[p]), int(seq[p + 1])) for p in gp]))
    print('    （文本粘连导致，与滤波无关）')

    if not a.fig:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(2, 2, figsize=(14.5, 9))
    t = np.arange(n) / FS
    # (a) dtcycles
    ax[0, 0].plot(t, c6, lw=.6, color='#1f77b4')
    for k, (s, e) in segs.items():
        ax[0, 0].axvspan(s / FS, e / FS, alpha=.09,
                         color={'on1': 'tab:green', 'off': 'tab:red', 'on2': 'tab:green'}[k])
        ax[0, 0].text((s + e) / 2 / FS, c6.max() * .95, k, ha='center', fontsize=9)
    ax[0, 0].set_title('(a) dtcycles 时程 —— 跳节生效的自检指标\n'
                       'on %.0f → off %.0f cycles（−%.0f%%）'
                       % (e_on, c6[b1:b2 + 1].mean(),
                          100 * (1 - c6[b1:b2 + 1].mean() / e_on)))
    ax[0, 0].set_xlabel('时间 (s)'); ax[0, 0].set_ylabel('cycles / 样本')
    ax[0, 0].grid(alpha=.3)

    # (b) 两个切换点放大 —— 画**残差**（已剥方波），切换瞬态才看得清
    for tag, sw, col in [('on→off', b1, '#2ca02c'), ('off→on', b2 + 1, '#d62728')]:
        w = 120
        ax[0, 1].plot(np.arange(-w, w) / FS * 1000, res[sw - w:sw + w],
                      lw=1.0, color=col, label='%s (idx %d)' % (tag, sw))
    ax[0, 1].axvline(0, color='k', lw=.8, ls=':', alpha=.6)
    ax[0, 1].set_ylim(-200, 200)
    ax[0, 1].set_title('(b) 切换点放大（±240 ms，已剥方波）\n'
                       'on→off 单步 11 µV；off→on 有 ~130 µV 陷波器启动瞬态')
    ax[0, 1].set_xlabel('相对切换时刻 (ms)'); ax[0, 1].set_ylabel('残差 (µV)')
    ax[0, 1].legend(fontsize=8); ax[0, 1].grid(alpha=.3)

    # (c) 边沿响应（跳过边沿本身的 ~700 µV 跳变，只看振铃细节）
    for k, c, ls in [('on1', '#d62728', '-'), ('off', '#2ca02c', '-'), ('on2', '#ff7f0e', '--')]:
        if (k, '正') in R:
            ax[1, 0].plot(np.arange(3, 240) / FS * 1000, R[(k, '正')][3:], lw=1.4, color=c, ls=ls, label=k)
    ax[1, 0].set_xlim(0, 200); ax[1, 0].set_ylim(-250, 250)
    ax[1, 0].axhline(0, color='k', lw=.6)
    ax[1, 0].set_title('(c) 方波边沿后振铃（对齐平均；已跳过边沿本身）\n'
                       '陷波开：16 ms 处 −160 µV，残余 RMS 29.5 µV；关掉只剩 ~10 µV / 5.6 µV')
    ax[1, 0].set_xlabel('边沿后 (ms)'); ax[1, 0].set_ylabel('µV'); ax[1, 0].legend(fontsize=9)
    ax[1, 0].grid(alpha=.3)

    # (d) 频谱（含边沿振铃口径；两段长度不同，各用各自频率轴）
    _, fr1, amp1 = S['on1']; _, fr2, amp2 = S['off']
    m1 = fr1 <= 250; m2 = fr2 <= 250
    ax[1, 1].semilogy(fr1[m1], amp1[m1] + 1e-3, lw=.8, color='#2ca02c', label='notch on')
    ax[1, 1].semilogy(fr2[m2], amp2[m2] + 1e-3, lw=.8, color='#d62728', label='notch off')
    for f0 in (50, 100, 150):
        ax[1, 1].axvline(f0, color='gray', lw=.6, ls=':')
    ax[1, 1].set_title('(d) 残差频谱（剥方波；含边沿振铃）\n'
                       'on 的 50 Hz 主要来自边沿振铃，off 的才是工频灌入')
    ax[1, 1].set_xlabel('频率 (Hz)'); ax[1, 1].set_ylabel('幅度 (µV)')
    ax[1, 1].legend(fontsize=9); ax[1, 1].grid(alpha=.3, which='both')

    plt.tight_layout()
    out = 'T48_notch开关_实测验证_2026-09-20.png'
    plt.savefig(out, dpi=125)
    print('\n图已保存：%s' % os.path.abspath(out))


if __name__ == '__main__':
    main()
