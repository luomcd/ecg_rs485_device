# -*- coding: utf-8 -*-
"""
T62b · 验证「ON 态的 822 µV 窗内振铃来自 notch 段」

推理链
------
T62 [J]/[K] 实测发现：notch ON 的数据（195208/180458）滑窗读数 5361/6890 µV/s，
而 notch OFF 的（200724/182243）只有 536/347 —— 差 10~13 倍。
[K] 的窗内原始采样显示 ON 态是「+211 → −611 → −53 → −468 …」的确定性振荡。

必须证明：这个振荡是**陷波节**造成的，而不是 AFE 在 ON 模式下硬件行为不同。

做法（离线复现）
----------------
取 **OFF 数据的列② raw**（未滤波，与 notch 无关），分别过：
  · ON 链  = dc_block + 全部节（含 9 节 notch）
  · OFF 链 = dc_block + 跳过节 notch（= 现场 OFF 固件）
看 ON 链是否**凭空**产生同样的量级振铃。
"""
import io
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t51_yy0885_pulse as T51          # noqa: E402
import _t52_yy0885_notchoff as T52m      # noqa: E402
import _t56_raw853_lowfreq as T56        # noqa: E402
import _t60_fc250_onboard as T60         # noqa: E402
import _t62_sw_mechanism as T62          # noqa: E402

FS = T51.FS
UVPC = T56.UVPC
OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


F_OFF = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt'
F_MEAS_ON = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 195208.txt'

s_n, nn, hp_n, k_n = T60.chain_of(T51.COEFFS_C, T51.COEFFS_H)
say('链：%s（notch 段 %d 节）' % (T60.descr(k_n, nn), nn))

d = T56.load7(os.path.join(HERE, F_OFF))
t = (d['idx'] - d['idx'][0]) / FS
pulses, _thr, bl = T56.find_pulses(d['raw'])
rep = float(np.median([t[pulses[k][0]] - t[pulses[k - 1][0]] for k in range(1, len(pulses))]))
xin = np.asarray(d['raw'], float) * UVPC

y_on = T60.run_chain(s_n, nn, hp_n, xin, use_notch=True)
y_off = T60.run_chain(s_n, nn, hp_n, xin, use_notch=False)

def win_p2p(y, t, pulses, w=0.100):
    """滑窗 argmax 处的窗内峰谷差（µV）。"""
    te = t[pulses[1][1]]
    xs = np.arange(te + 0.020, min(te + 2.0, t[-1] - w), 0.0002)
    z = np.abs(np.interp(xs + w, t, y) - np.interp(xs, t, y)) / w
    k = int(np.argmax(z))
    return float(z[k] * w)


d2 = T56.load7(os.path.join(HERE, F_MEAS_ON))
t2 = (d2['idx'] - d2['idx'][0]) / FS
p2, _t3, _b4 = T56.find_pulses(d2['raw'])
y2 = d2['filt'] * UVPC
mp2 = (t2 >= t2[p2[1][1]] + 1.5) & (t2 <= t2[p2[2][0]] - 0.5)

say()
say('对照表（同一份 200724 OFF raw 分别过 ON/OFF 链 ＋ 195208 真 ON 上板实测）：')
say('  %-20s %11s %11s %11s' % ('对象', '滑窗100', '窗内p-p', 'σ差分(平台)'))
mp = (t >= t[pulses[1][1]] + 1.5) & (t <= t[pulses[2][0]] - 0.5)
for lab, y, tt, pz, mk in (('离线 ON 链(含notch)', y_on, t, pulses, mp),
                           ('离线 OFF 链(跳notch)', y_off, t, pulses, mp),
                           ('真 ON 上板 195208', y2, t2, p2, mp2)):
    sm = T62.sw_med(y, tt, pz, 0.100)[0]
    say('  %-20s %11.1f %11.1f %11.3f' % (lab, sm, win_p2p(y, tt, pz), T62.sd_diff(y[mk])))


say()
say('第 2 个脉冲降沿后 0~300 ms 波形（每 4 ms 一点，µV）：')
te = t[pulses[1][1]]
g = np.arange(0.0, 0.3001, 0.004)


def r4(v):
    return ' '.join('%7.1f' % q for q in v)


say('  ON  链：')
vv = np.interp(te + g, t, y_on)
say('    ' + r4(vv[:19]))
say('    ' + r4(vv[19:38]))
say('    ' + r4(vv[38:]))
say('  OFF 链：')
vv = np.interp(te + g, t, y_off)
say('    ' + r4(vv[:19]))
say('    ' + r4(vv[19:38]))
say('    ' + r4(vv[38:]))

say()
say('对照：195208 实测列⑤（真 ON 上板）同段：')
te2 = t2[p2[1][1]]
vv = np.interp(te2 + g, t2, y2)
say('    ' + r4(vv[:19]))
say('    ' + r4(vv[19:38]))
say('    ' + r4(vv[38:]))

say()
say('结论判定：')
say('  · 若"ON 链"在 OFF raw 上凭空产生与 195208 同量级的振荡 ⇒ 振铃来自 **notch 节**；')
say('  · 若"ON 链"仍然平滑 ⇒ 195208 的振荡另有来源（需查固件/硬件）。')

io.open(os.path.join(HERE, '_t62b_out.txt'), 'w', encoding='utf-8').write('\n'.join(OUT) + '\n')
print('\n输出 -> _t62b_out.txt')
