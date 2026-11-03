# -*- coding: utf-8 -*-
"""_t42_plots.py —— T42 诊断/监护参数化配图（4 panel）
panel 1: 诊断(2-25) vs 监护(2-27) 频率轴分工与容差带
panel 2: 4 阶 BP 的 8 个槽位归属（哪几节能"保状态"）
panel 3: 三种切档方式的瞬态对比（诊断 -> 监护）
panel 4: 3mV/100ms 脉冲偏移 vs 低端配置  ← 核心发现
"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal as sps

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FS = 1e6 / 1172.0
OUT = "C:/work2026/VsPro/ads1293_demo/tools/T42_诊断监护参数化_2026-09-18.png"


def nk(f0, Q):
    b, a = sps.iirnotch(f0, Q, fs=FS)
    return sps.tf2sos(b, a)[0]


def bp(fu, fl=0.05, order=4):
    return sps.butter(order, [fl, fu], btype='bandpass', fs=FS, output='sos')


def run(sos, x, state):
    y = np.empty_like(x)
    for k in range(len(x)):
        v = x[k]
        for i in range(sos.shape[0]):
            r = sos[i]
            d1, d2 = state[i]
            w = r[0] * v + d1
            state[i][0] = r[1] * v - r[4] * w + d2
            state[i][1] = r[2] * v - r[5] * w
            v = w
        y[k] = v
    return y


def build(kind):
    if kind == 'D3':
        return np.vstack([nk(49.9, 20), nk(50, 20), nk(50.1, 20), nk(100, 15)] +
                         [bp(150.0)[i] for i in range(4)])
    rows = [nk(50, 30), nk(100, 30), nk(150, 30), np.array([1., 0, 0, 0, 0, 0])] + \
           [bp(40.0)[i] for i in range(4)]
    return np.vstack(rows)


def main():
    fig = plt.figure(figsize=(15, 10))
    gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.22)

    # ---------- panel 1: 频率轴分工 ----------
    ax = fig.add_subplot(gs[0, 0])
    f = np.exp(np.linspace(np.log(0.3), np.log(600), 500))
    Hd = sps.sosfreqz(bp(150.0), worN=f, fs=FS)[1]
    Hs = sps.sosfreqz(bp(40.0), worN=f, fs=FS)[1]
    rd = np.abs(Hd) / np.abs(sps.sosfreqz(bp(150.0), worN=np.array([10.0]), fs=FS)[1][0])
    rs = np.abs(Hs) / np.abs(sps.sosfreqz(bp(40.0), worN=np.array([5.0]), fs=FS)[1][0])
    ax.semilogx(f, 100 * np.abs(Hd), lw=2, color='#c0392b', label='诊断档 D3  0.05–150 Hz（参考 10 Hz）')
    ax.semilogx(f, 100 * np.abs(Hs), lw=2, color='#2471a3', label='监护档 ST40 0.05–40 Hz')
    ax.axvspan(0.67, 40, color='#2471a3', alpha=0.10)
    ax.axvspan(40, 150, color='#c0392b', alpha=0.10)
    ax.axvline(10, ls='--', lw=1, color='#c0392b')
    ax.axvline(5, ls='--', lw=1, color='#2471a3')
    ax.text(10, 108, '诊断参考 10Hz', color='#c0392b', fontsize=8, ha='center')
    ax.text(5, 96, '监护参考 5Hz', color='#2471a3', fontsize=8, ha='center')
    ax.text(2.2, 30, '监护带宽要求\n0.67–40 Hz', fontsize=9, ha='center', color='#2471a3')
    ax.text(80, 30, '诊断额外带宽\n40–150 Hz', fontsize=9, ha='center', color='#c0392b')
    ax.set_xlim(0.5, 400); ax.set_ylim(0, 118)
    ax.set_xlabel('频率 (Hz)'); ax.set_ylabel('相对增益 (%)')
    ax.set_title('① 两档的频率轴分工（正弦法，GB 9706.225 表201.107 / GB 9706.227）', fontsize=11)
    ax.grid(True, which='both', alpha=0.25); ax.legend(fontsize=9, loc='lower left')

    # ---------- panel 2: 槽位归属 ----------
    ax = fig.add_subplot(gs[0, 1])
    labels, vals, cols = [], [], []
    for tag, fu in (('诊断 BP', 150.0), ('监护 BP', 40.0)):
        s = bp(fu)
        for i in range(4):
            z, p, k = sps.sos2zpk(s[i:i + 1, :])
            fz = np.abs(np.angle(z)) * FS / (2 * np.pi)
            labels.append(f'{tag} 槽{i}')
            vals.append(fz.max())
            cols.append('#c0392b' if fz.max() < 1 else '#2471a3')
    ypos = np.arange(len(labels))
    ax.barh(ypos, vals, color=cols, alpha=0.85)
    ax.set_yticks(ypos); ax.set_yticklabels(labels, fontsize=9)
    ax.axvline(1.0, color='k', ls='--', lw=1)
    ax.text(2, -0.6, '← 零点在 DC = 高通节', fontsize=9)
    ax.text(200, 5.2, '零点在 Nyquist = 低通节 →', fontsize=9)
    for i, v in enumerate(vals):
        ax.text(v + 8, i, f'{v:.0f} Hz', va='center', fontsize=8)
    ax.set_xlim(0, 520); ax.set_xlabel('二阶节零点频率 (Hz)')
    ax.set_title('② 4 阶 BP 的槽位归属：两档低端同构 → 槽2/3 可保状态', fontsize=11)
    ax.grid(axis='x', alpha=0.25)

    # ---------- panel 3: 切档瞬态 ----------
    ax = fig.add_subplot(gs[1, 0])
    n = int(8.0 * FS); t = np.arange(n) / FS
    x = 1.0 * np.sin(2 * np.pi * 10 * t); sw = int(5.0 * FS)
    sD, sS = build('D3'), build('ST40')
    st = [[0.0, 0.0] for _ in range(8)]
    yD = run(sD, x[:sw], st)
    st_end = [list(v) for v in st]
    yA = run(sS, x[sw:], [[0.0, 0.0] for _ in range(8)])
    yB = run(sS, x[sw:], [list(v) for v in st_end])
    stc = [list(v) for v in st_end]
    for i in range(4):
        stc[i] = [0.0, 0.0]
    yC = run(sS, x[sw:], stc)
    tw = 0.35
    m = int((5.0 - tw) * FS)
    ax.plot(t[m:sw] * 1000, yD[m:sw] * 1000, color='k', lw=1.2, label='切换前（诊断 D3）')
    for lab, y, c in (('A 完全重置', yA, '#c0392b'), ('B 保全 8 槽', yB, '#e67e22'),
                      ('C 保高通节/清陷波', yC, '#27ae60')):
        ys = np.concatenate([yD[m:], y[:int(tw * FS)]])
        ax.plot(t[m:sw + int(tw * FS)] * 1000, ys * 1000, color=c, lw=1.6, label=f'{lab}')
    ax.axvline(5000, color='k', ls=':', lw=1.5)
    ax.text(5005, 620, '切档点', fontsize=9)
    ax.set_xlabel('时间 (ms)'); ax.set_ylabel('输出 (µV)')
    ax.set_title('③ 诊断→监护 切档瞬态（10 Hz 1 mV，t=5 s）\n'
                 '跳变: A 267 µV | B 73 µV | C 73 µV（正常相邻差 74 µV）', fontsize=11)
    ax.grid(alpha=0.25); ax.legend(fontsize=8, loc='lower right', ncol=2)

    # ---------- panel 4: 脉冲偏移 vs 低端配置 ----------
    ax = fig.add_subplot(gs[1, 1])
    r05 = 1 - 2 * np.pi * 0.05 / FS
    r067 = 1 - 2 * np.pi * 0.67 / FS
    lo2_150 = sps.butter(2, 150.0, btype='lowpass', fs=FS, output='sos')
    lo4_40 = sps.butter(4, 40.0, btype='lowpass', fs=FS, output='sos')
    cands = [
        ('现状监护 ST40\n(5阶0.05+BP40)', np.vstack([nk(50, 30), nk(100, 30), nk(150, 30)] +
                                                [bp(40.0)[i] for i in range(4)])),
        ('现状诊断 D3\n(5阶0.05+BP150)', np.vstack([nk(49.9, 20), nk(50, 20), nk(50.1, 20),
                                                nk(100, 15)] + [bp(150.0)[i] for i in range(4)])),
        ('1阶0.05+2阶高通\n(=去掉低通)', np.vstack([[1., -1., 0., 1., -r05, 0.]] +
                                              [bp(150.0)[i] for i in (2, 3)])),
        ('纯 1 阶 0.05\n(dc_block 单独)', np.array([[1., -1., 0., 1., -r05, 0.]])),
        ('候选诊断\n1阶0.05+低通150', np.vstack([[1., -1., 0., 1., -r05, 0.]] + [lo2_150[i] for i in range(lo2_150.shape[0])])),
        ('候选监护\n1阶0.05+低通40', np.vstack([[1., -1., 0., 1., -r05, 0.]] + [lo4_40[i] for i in range(lo4_40.shape[0])])),
        ('对照: 1阶0.67+低通40\n(纯监护型)', np.vstack([[1., -1., 0., 1., -r067, 0.]] + [lo4_40[i] for i in range(lo4_40.shape[0])])),
    ]
    vals = []
    for lab, sos in cands:
        nn = int(42.0 * FS); xx = np.zeros(nn)
        i0 = int(30.0 * FS); ip = int(0.1 * FS); xx[i0:i0 + ip] = 3.0
        yy = run(sos, xx, [[0., 0.] for _ in range(sos.shape[0])])
        base = np.mean(yy[i0 - int(FS):i0])
        vals.append(np.max(np.abs(yy[i0 + ip + int(0.2 * FS):] - base)) * 1000)
    cols = ['#c0392b' if v > 100 else '#27ae60' for v in vals]
    ypos = np.arange(len(vals))
    ax.barh(ypos, vals, color=cols, alpha=0.88)
    ax.axvline(100, color='k', ls='--', lw=1.5)
    ax.text(104, 6.4, '判限 0.1 mV', fontsize=9)
    for i, v in enumerate(vals):
        ax.text(v + 8, i, f'{v:.0f} µV', va='center', fontsize=8.5)
    ax.set_yticks(ypos); ax.set_yticklabels([c[0] for c in cands], fontsize=8.5)
    ax.set_xlim(0, 560); ax.set_xlabel('3 mV/100 ms 脉冲外基线偏移 (µV RTI)')
    ax.set_title('④ 核心发现：脉冲偏移由【低端阶数】决定，不是截止频率\n'
                 '（红=超标 / 绿=通过；口径待上板钉死）', fontsize=11)
    ax.grid(axis='x', alpha=0.25)

    fig.suptitle('T42  诊断模式 vs 监护模式：滤波差异 & 命令参数化可行性（离线仿真，2026-09-18）',
                 fontsize=13, y=0.975)
    fig.savefig(OUT, dpi=130, bbox_inches='tight')
    print('已保存:', OUT)
    for lab, v in zip([c[0] for c in cands], vals):
        print(f'  {lab.replace(chr(10)," "):44s} {v:8.1f} µV')


if __name__ == '__main__':
    main()
