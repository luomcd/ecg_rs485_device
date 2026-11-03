#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
T42 离线评估：诊断档(D3) <-> 监护档(ST40) 运行期切档的瞬态与资源账。

零硬件、零源码改动。用途：判断"把诊断/监护做成命令切换"的可行性边界。
判据/口径见 tools/T42_诊断监护参数化对齐_2026-09-18.md。

跑法： <venv-python> _t42_mode_switch.py
"""
import numpy as np
from scipy import signal as sps

FS = 1e6 / 1172.0          # 853.2423 Hz (ADS1293 DRDY 实测 1172us)
TIN_US = 1172.0
BUDGET_CYC = 93760.0       # 单样本预算 @80MHz
UV_PER_CODE = 0.1122


def nk(f0, Q):
    b, a = sps.iirnotch(f0, Q, fs=FS)
    return sps.tf2sos(b, a)[0]


def bp(fu, fl=0.05):
    return sps.butter(4, [fl, fu], btype='bandpass', fs=FS, output='sos')


def build(kind):
    """返回 (names, sos[8,6])，8 个槽位固定，便于运行期逐槽换系数。"""
    if kind == 'D3':      # 诊断 8 节：notch 49.9/50/50.1 + 100 + BP[0.05,150]
        rows = [nk(49.9, 20), nk(50, 20), nk(50.1, 20), nk(100, 15)] + [bp(150.0)[i] for i in range(4)]
        names = ['n49.9', 'n50', 'n50.1', 'n100', 'bp-lo0', 'bp-lo1', 'bp-hi0', 'bp-hi1']
    elif kind == 'ST40':  # 监护 7 节：notch 50/100/150 + BP[0.05,40]
        rows = [nk(50, 30), nk(100, 30), nk(150, 30), np.array([1., 0, 0, 0, 0, 0])] + \
               [bp(40.0)[i] for i in range(4)]
        names = ['n50', 'n100', 'n150', 'bypass', 'bp-lo0', 'bp-lo1', 'bp-hi0', 'bp-hi1']
    return names, np.vstack(rows)


def run(sos, x, state):
    """逐样本 DF2T（与固件 biquad_df2t_f64_process 同式）。sos 行=[b0,b1,b2,a0,a1,a2]"""
    y = np.empty_like(x)
    for k in range(len(x)):
        v = x[k]
        for i in range(sos.shape[0]):
            r = sos[i]
            b0, b1, b2, a1, a2 = r[0], r[1], r[2], r[4], r[5]
            d1, d2 = state[i]
            w = b0 * v + d1
            state[i][0] = b1 * v - a1 * w + d2
            state[i][1] = b2 * v - a2 * w
            v = w
        y[k] = v
    return y


def main():
    namesD, sD = build('D3')
    namesS, sS = build('ST40')

    print('=' * 78)
    print('第 0 节 BP 分块归属（判断哪几节可以"保状态"）')
    print('=' * 78)
    for tag, fu in (('诊断 BP[0.05,150]', 150.0), ('监护 BP[0.05,40]', 40.0)):
        s = bp(fu)
        print(f'{tag}:')
        for i in range(4):
            z, p, k = sps.sos2zpk(s[i:i + 1, :])
            fz = np.abs(np.angle(z)) * FS / (2 * np.pi)
            fp = np.angle(p) * FS / (2 * np.pi)
            kind = '高通节(零点在DC)' if fz.max() < 1.0 else '低通节(零点在Nyquist)'
            print(f'   槽位{i}: {kind:22s} 极点 f={np.round(fp,3)}  |p|={np.round(np.abs(p),6)}')
        print()
    # 两档的高通节是否逐位接近
    lo_d, lo_s = bp(150.0)[2:4], bp(40.0)[2:4]
    print('两档高通节系数最大相对差 = %.3e  (0 表示低端结构完全一致 ⇒ 可保状态)'
          % np.max(np.abs(lo_d - lo_s) / np.maximum(np.abs(lo_d), 1e-30)))

    print()
    print('=' * 78)
    print('第 1 节 切档瞬态（诊断 D3 -> 监护 ST40，10Hz 1mV 正弦，t=5s 切换）')
    print('=' * 78)
    n = int(8.0 * FS)
    t = np.arange(n) / FS
    x = 1.0 * np.sin(2 * np.pi * 10 * t)
    sw = int(5.0 * FS)
    st = [[0.0, 0.0] for _ in range(8)]
    yD = run(sD, x[:sw], st)
    st_end = [list(v) for v in st]
    ref = run(sD, x, [[0.0, 0.0] for _ in range(8)])[sw:]   # 参考：一直用诊断链
    base = np.mean(np.abs(yD[-int(0.5 * FS):]))
    print(f'切换前稳态幅度 = {base*1000:.1f} µV   正常相邻样本差 max = '
          f'{np.max(np.abs(np.diff(yD[-int(0.5 * FS):])))*1000:.1f} µV')

    def report(lab, yseg):
        jump = abs(yseg[0] - yD[-1]) * 1000
        seg1 = np.abs(yseg[:int(1.0 * FS)])
        dev = np.abs(seg1 - base)
        idx = np.where(dev > 0.03 * base)[0]
        rec = idx[-1] / FS * 1000 if len(idx) else 0.0
        print(f'  {lab:28s} 切换跳变 {jump:8.2f} µV   偏离>3% 持续 {rec:9.1f} ms')

    yS_native = run(sS, x, [[0.0, 0.0] for _ in range(8)])[sw:]   # 原生监护链（从未切换）

    report('A 完全重置(单链从零)', run(sS, x[sw:], [[0.0, 0.0] for _ in range(8)]))
    yB = run(sS, x[sw:], [list(v) for v in st_end])
    report('B 保全 8 槽状态换系数', yB)
    st_c = [list(v) for v in st_end]
    for i in range(4):
        st_c[i] = [0.0, 0.0]
    report('C 保高通节(4~7)/清陷波', run(sS, x[sw:], st_c))
    yS2 = run(sS, x[sw:], [list(v) for v in st_end])
    al = np.clip(np.arange(len(yS2)) / (0.2 * FS), 0, 1)
    report('D 双链交叉淡化 200ms', al * yS2 + (1 - al) * ref)

    # ---- 正确定义：与"原生监护链"的差异收敛时间 ----
    print()
    print('  --- 与原生监护链(从头就用 ST40)的差异 |y_switch - y_native|，看多久收敛到 1% 稳态 ---')
    cands = [('A 完全重置', run(sS, x[sw:], [[0.0, 0.0] for _ in range(8)])),
             ('B 保全状态', yB),
             ('C 保高通节/清陷波', run(sS, x[sw:], st_c)),
             ('D 双链交叉淡化200ms', al * yS2 + (1 - al) * ref)]
    for lab, y in cands:
        diff = np.abs(y - yS_native)
        over = np.where(diff > 0.01 * base)[0]
        conv = over[-1] / FS * 1000 if len(over) else 0.0
        print(f'  {lab:22s} 最大差异 {diff.max()*1000:8.2f} µV(@{diff.argmax()/FS*1000:.0f}ms)  '
              f'收敛到<1% 用 {conv:8.1f} ms')

    # ---- 标准低频脉冲判据（切档后立刻打一个 3mV/100ms）----
    print()
    print('  --- 切档后立刻输入 3mV/100ms 脉冲（标准低频判据: 脉冲外位移<=0.1mV, 后斜率<=0.30mV/s）---')
    npulse = int(25.0 * FS)
    xp = np.zeros(npulse)
    xp[:int(0.1 * FS)] = 3.0
    for lab, stt in (('A 完全重置', [[0.0, 0.0] for _ in range(8)]),
                     ('B 保全状态', [list(v) for v in st_end])):
        yp = run(sS, xp, stt)
        tail = yp[int(0.2 * FS):]
        pk = np.max(np.abs(tail))
        w = int(0.05 * FS)
        ys_ = np.convolve(tail, np.ones(w) / w, 'same')
        sl = np.max(np.abs(np.diff(ys_) * FS))
        print(f'  {lab:14s} 脉冲外峰值 {pk*1000:8.1f} µV [{"超标" if pk > 1e-4 else "通过"}]   '
              f'后斜率 {sl:6.3f} mV/s [{"超标" if sl > 0.30 else "通过"}]')

    print()
    print('=' * 78)
    print('第 2 节 资源账')
    print('=' * 78)
    print('CPU: dt(%%) = 1.3455 + 0.6895*N   [T40 实测 R2=0.999993]')
    for name, N in (('ST40 监护', 7), ('D1', 5), ('D2', 6), ('D3 诊断', 8), ('IMP620', 9), ('IMP620_N2', 13)):
        pct = 1.3455 + 0.6895 * N
        print(f'  {name:12s} {N:2d} 节 -> {pct:5.2f}%   余量 {100-pct:5.2f}%')
    print()
    print('常量表（系数）：f32 节 20B / f64 节 40B（rodata）')
    print('  仅编 2 档(诊断8+监护7=15 节): f32+f64 = %d B' % (15 * 20 + 15 * 40))
    print('  六档全编(48 节)            : f32+f64 = %d B   (现单档 780 B)' % (48 * 20 + 48 * 40))
    print('状态 RAM：f64 节 56B -> 8 槽链 448 B (按最大槽位数分配，切档不加 RAM)')
    print('  若双链并行(交叉淡化方案D)  : 2 x 448 = 896 B')
    print('  运行态现状 f64 符号 728 B (13 节) — 按 13 槽分配则兼容全部档位')


if __name__ == '__main__':
    main()
