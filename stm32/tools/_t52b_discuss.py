# -*- coding: utf-8 -*-
"""T52b · 判据 a 口径讨论的补充量化

回答三个讨论问题：
  Q1-a2  滑窗口径 vs 拟合口径：现状矛盾，降阶后是否收敛？噪声对两者的污染各多大？
  Q1-a3  a3 四种基准在降阶后是否自动收敛？
  Q2    脉冲间隔 2 s(0.5 Hz) vs ≥10 s：位移读数差多少？降阶后是否出现"假通过"？

链条口径（与固件一致，FS=853.2423 域）：
  raw -> ecg_dc_block(1 阶 HP@0.05) -> [HP4@0.05] -> [LPn@150]
  hp_order=1 => 仅 dc_block；hp_order=5 => dc_block + HP4
"""
import os
import sys

import numpy as np
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t52_yy0885_notchoff as T52          # noqa: E402

FS = 853.2423
A_IN = 3000.0
PW = 0.100
FC_HP = 0.05
FC_LP = 150.0


def run(interval, n=12, hp_order=5, lp_order=4, lead=6.0, tail=4.0, pre=0):
    N = int((lead + interval * (n - 1) + tail) * FS)
    t = np.arange(N) / FS
    x = np.zeros(N)
    last = None
    for j in range(n):
        i0 = int(round((lead + j * interval) * FS))
        i1 = i0 + int(round(PW * FS))
        x[i0:i1] = A_IN
        last = (i0, i1)
    if pre > 1:                      # AFE 边沿圆化（L 样本移动平均，T52 §4.3 标定 L=3）
        x = np.convolve(x, np.ones(pre) / pre, mode='same')
    y = T52.dc_block_apply(x, FC_HP, FS)
    if hp_order >= 5:
        y = sosfilt(butter(4, FC_HP / (FS / 2), btype='high', output='sos'), y)
    if lp_order:
        y = sosfilt(butter(lp_order, FC_LP / (FS / 2), output='sos'), y)
    return t, y, last


def metrics(t, y, i1):
    te = t[i1 - 1]
    m0 = (t >= te - 0.5) & (t <= te - 0.02)
    b0 = float(np.median(y[m0]))
    out = {}
    for dx in (0.02, 0.20, 0.50):
        out['disp+%.2f' % dx] = float(np.interp(te + dx, t, y)) - b0
    for w in (0.05, 0.10, 0.20):
        q = np.arange(te + 0.02, te + 1.85 - w, 0.002)
        v = np.abs(np.interp(q + w, t, y) - np.interp(q, t, y)) / w
        out['slwin%d' % int(w * 1000)] = float(v.max()) if len(v) else np.nan
    sel = (t >= te + 0.20) & (t <= te + 1.20)
    out['slfit'] = abs(float(np.polyfit(t[sel] - t[sel][0], y[sel], 1)[0]))
    out['valley'] = float(np.min(y[(t >= te) & (t <= te + 0.05)])) - b0
    if te + 0.05 <= t[-1]:
        pk_sel = (t >= te - 0.1) & (t <= te + 0.1)
        out['absmax_fall'] = float(np.max(np.abs(y[(t >= te) & (t <= te + 0.5)] - b0)))
    j = te + 0.02
    sub = t >= j
    idx = np.where(np.abs(y[sub] - b0) <= 100.0)[0]
    out['t_back100'] = (t[sub][idx[0]] - te) if len(idx) else np.nan
    return out


def noise_floor(hp_order=5, lp_order=4, dur=6.0):
    """白噪声 5 µV RMS 过同一链后的输出 RMS（评估微分型口径的噪声污染）"""
    rng = np.random.default_rng(7)
    N = int(dur * FS)
    x = rng.normal(0, 5.0, N)
    y = T52.dc_block_apply(x, FC_HP, FS)
    if hp_order >= 5:
        y = sosfilt(butter(4, FC_HP / (FS / 2), btype='high', output='sos'), y)
    if lp_order:
        y = sosfilt(butter(lp_order, FC_LP / (FS / 2), output='sos'), y)
    y = y[int(2 * FS):]
    w = y[:-50] - y[50:]
    return float(np.sqrt(np.mean(w ** 2))) / 0.05, float(np.std(y))


def main():
    print('=' * 76)
    print('  T52b · 判据 a 口径讨论的补充量化（仿真域 FS=%.4f）' % FS)
    print('=' * 76)
    cfgs = [
        ('现状 低端5阶 + LP4', 5, 4),
        ('降阶 低端1阶 + LP4', 1, 4),
        ('降阶 低端1阶 + LP2', 1, 2),
    ]
    print()
    print('[1] 脉冲间隔敏感性（0.5 Hz = 2 s / 10 s / 隔离 20 s）')
    print('  %-22s %8s %11s %11s %11s %11s %11s %11s'
          % ('配置', '间隔s', '位移+0.02', '位移+0.20', '滑窗50', '滑窗100', '滑窗200', '拟合.2~1.2'))
    for lab, hp, lp in cfgs:
        for iv in (2.0, 10.0, 20.0):
            n = 12 if iv <= 3 else (6 if iv <= 12 else 3)
            t, y, (i0, i1) = run(iv, n, hp, lp)
            m = metrics(t, y, i1)
            print('  %-22s %8.0f %11.1f %11.1f %11.1f %11.1f %11.1f %11.1f'
                  % (lab, iv, abs(m['disp+0.02']), abs(m['disp+0.20']),
                     m['slwin50'], m['slwin100'], m['slwin200'], m['slfit']))
    print('  （位移单位 µV，限 100；斜率单位 µV/s，限 300）')

    print()
    print('[2] 2 s 间隔相对隔离态(20 s)的读数偏差（"假通过 / 假超标"风险）')
    print('  %-22s %13s %15s %15s' % ('配置', '位移 偏差', '滑窗50 偏差', '滑窗200 偏差'))
    for lab, hp, lp in cfgs:
        t2, y2, e2 = run(2.0, 12, hp, lp)
        t9, y9, e9 = run(20.0, 3, hp, lp)
        m2, m9 = metrics(t2, y2, e2[1]), metrics(t9, y9, e9[1])
        print('  %-22s %12.1f%% %14.1f%% %14.1f%%'
              % (lab, abs(m2['disp+0.02']) / abs(m9['disp+0.02']) * 100 - 100,
                 m2['slwin50'] / m9['slwin50'] * 100 - 100,
                 m2['slwin200'] / m9['slwin200'] * 100 - 100))
    print('  （正 = 读数偏大，负 = 读数偏小）')

    print()
    print('[3] 噪声对"微分型口径"的污染（白噪 5 µV RMS 过同一链）')
    print('  %-22s %14s %14s' % ('配置', '滑窗50 等效抖动', '输出 RMS'))
    for lab, hp, lp in cfgs:
        wj, rms = noise_floor(hp, lp)
        print('  %-22s %12.1f µV/s %12.2f µV' % (lab, wj, rms))
    print('  ⇒ 判限 300 µV/s；若抖动占判限比例高，滑窗口径的读数不可复现。')

    print()
    print('[4] a3 各基准在降阶后是否收敛（仿真，含 L=3 AFE 边沿预平滑，2 s 间隔）')
    print('  %-22s %9s %9s %9s %9s %8s' % ('配置', '峰值', '标称3000', '近平台', '末端平台', '过冲/标称'))
    for lab, hp, lp in cfgs:
        t, y, (i0, i1) = run(2.0, 12, hp, lp, pre=3)
        y = y - float(np.median(y[(t >= t[i0] - 0.4) & (t <= t[i0] - 0.02)]))
        seg = (t >= t[i0] - 0.002) & (t <= t[i0] + 0.050)
        tt, yy = t[seg], y[seg]
        tu = np.arange(tt[0], tt[-1], 0.0002)
        pk = float(np.interp(tu, tt, yy).max())
        near = float(np.median(y[(t >= t[i0] + 0.030) & (t <= t[i0] + 0.050)]))
        far = float(np.median(y[(t >= t[i1 - 1] - 0.020) & (t <= t[i1 - 1])]))
        print('  %-22s %9.1f %9.1f %9.1f %9.1f %7.2f%%'
              % (lab, pk, 3000.0, near, far, (pk - 3000.0) / 3000.0 * 100))
    print('  ※ 近平台 = 上升沿后 +30~50 ms（顶部靠前段）；末端平台 = 降沿前 20 ms。')
    print('  ⇒ 若降阶后"近平台/末端平台"向"标称"收敛，则 a3 的口径之争自动消解。')


if __name__ == '__main__':
    main()
