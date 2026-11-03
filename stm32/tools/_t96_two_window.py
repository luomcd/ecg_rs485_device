# -*- coding: utf-8 -*-
"""T96 —— 判据 b) 两窗并列评估（7 频点 × 两档）＋ 判据 a) 两档一致性论证

背景（猛哥 2026-09-22 12:3x 拍板）：
  ① 判据窗**两窗并列出**：2-27（GB 9706.227）[71%, 110%] 与 2-47（YY 9706.247）[70%, 140%]
  ② 判据 c) 三角波**不再抓数**（原文结构 = a) 且（b) 或 c)）⇒ 走 b 路径，
     但 **a) 仍是并列必过项**，故本脚本一并评估 a)。

口径（与 `_t92_mon_measure.py` 同模型，避免两套模型漂移）：
  - 固件链：A+B 档 16 节，mode='mon' 时第 15 节换成 LP2@45
  - **notch OFF**（判据 b 要求；T67 定）
  - **含 AFE**（三级 5 阶 CIC，输入率 102400→25600→5120）
  - 归一到 **5 Hz**（原文参考点）

🛑 无源码改动；纯离线预测。
"""
import importlib.util
import io
import os
import sys

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '_t96_out.txt')

# 复用 T92 判读脚本的模型（同一口径）
_spec = importlib.util.spec_from_file_location(
    't92mon', os.path.join(HERE, '_t92_mon_measure.py'))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

FREQS = [0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0]   # 原文 b) 七频点
REF = 5.0
WINDOWS = [('2-27 (GB 9706.227) 监护', 71.0, 110.0),
           ('2-47 (YY 9706.247) 动态', 70.0, 140.0)]
MODES = [('diag', 'LP2@250'), ('mon', 'LP2@45')]

_buf = []


def p(s=''):
    print(s)
    _buf.append(s)


def main():
    ab = M.read_ab_sos()
    dc_fc = M.read_ab_dc_fc()
    p('=== T96 判据 b) 两窗并列评估（notch OFF / 含 AFE / 对 5 Hz 归一） ===')
    p('固件链：A+B 档 %d 节；dc_block fc = %.4g Hz；两档仅第 15 节不同'
      % (len(ab), dc_fc))
    p()

    # ---------------------------------------------------------- 1 两窗矩阵
    p('【1】7 频点 × 两档 × 两窗（数值＝占 5 Hz 的百分比）')
    p('-' * 116)
    hdr = '  %-6s' % 'f(Hz)'
    for tag, _c in MODES:
        hdr += '| %14s ' % (tag + ' %')
    p(hdr)
    p('-' * 116)

    rows = {}
    for mode, topname in MODES:
        rows[mode] = [float(M.predict(ab, mode, f, REF, with_afe=True,
                                      notch_off=True, dc_fc=dc_fc)[0]) for f in FREQS]

    for i, f in enumerate(FREQS):
        line = '  %-6g' % f
        for mode, _c in MODES:
            line += '| %14.3f ' % rows[mode][i]
        p(line)

    p()
    p('【1b】两窗判定矩阵（✓ / ✗，括号内为距最近限的余量 pp）')
    p('-' * 116)
    p('  %-6s' % 'f(Hz)' + ''.join('| %-26s' % (m + ' · ' + w[0][:5])
                                   for m, _c in MODES for w in WINDOWS))
    p('-' * 116)
    allpass = True
    for i, f in enumerate(FREQS):
        line = '  %-6g' % f
        for mode, _c in MODES:
            for wtag, lo, hi in WINDOWS:
                v = rows[mode][i]
                ok = lo <= v <= hi
                if not ok:
                    allpass = False
                marg = min(v - lo, hi - v)
                line += '| %-26s' % ('%s %+6.2f' % ('✓' if ok else '✗', marg))
        p(line)
    p('-' * 116)
    p('  ==> 两档 × 两窗 × 7 频点：%s' % ('**全部通过**' if allpass else '**存在越限**'))
    p()

    # ---------------------------------------------------- 2 余量最小点
    p('【2】余量最小的三个点（决定上角能否再动）')
    flat = []
    for i, f in enumerate(FREQS):
        for mode, _c in MODES:
            v = rows[mode][i]
            for wtag, lo, hi in WINDOWS:
                flat.append((min(v - lo, hi - v), f, mode, wtag, v))
    flat.sort()
    for marg, f, mode, wtag, v in flat[:3]:
        p('  %-6s @ %-6g Hz：%8.3f%%（%s）⇒ 余 %+.2f pp' % (mode, f, v, wtag[:14], marg))
    p()

    # ------------------------------------------- 3 上角旋钮扫描（mon 档）
    p('【3】★ mon 档「上角」旋钮扫描 —— 40 Hz 处占 5 Hz 的百分比')
    p('    （原文要求 40 Hz ∈ [70%, 140%]；下限 70% ⇒ 上角存在**不可再低**的硬边界）')
    p('-' * 116)
    p('  %-10s %12s %12s %12s' % ('上角(Hz)', '40 Hz %', 'vs 下限70%', 'vs 上限140%'))
    p('-' * 116)
    from scipy.signal import butter
    for fc in (30.0, 35.0, 38.0, 40.0, 42.0, 45.0, 50.0, 55.0, 60.0, 80.0, 120.0, 250.0):
        sos = np.array(ab, dtype=float, copy=True)
        sec = butter(2, fc, 'low', fs=M.FS, output='sos')[0]      # (b0,b1,b2,a0,a1,a2)
        sos[15] = [float(sec[0]), float(sec[1]), float(sec[2]),
                   float(sec[4]), float(sec[5])]
        num = float(np.abs(M.h_chain_notch_off(sos, 'diag', np.array([40.0]), dc_fc))[0]) \
            * float(M.afe_gain(40.0)[0])
        den = float(np.abs(M.h_chain_notch_off(sos, 'diag', np.array([REF]), dc_fc))[0]) \
            * float(M.afe_gain(REF)[0])
        v = num / den * 100.0
        p('  %-10g %12.3f %+12.2f %+12.2f' % (fc, v, v - 70.0, v - 140.0))
    p('-' * 116)
    p('  ★ 结论：上角 ≈45 Hz ⇒ 40 Hz 仅剩 ~7 pp；**再低就会撞 70% 下限**。')
    p('    上角 = 40 Hz 时 40 Hz 处恰为 −3 dB（70.7%）⇒ 45 Hz 已是「贴着标准下限」的设计。')
    p()

    # ----------------------------------------- 4 判据 a) 两档对比（时域）
    p('【4】判据 a)（3 mV / 100 ms 方波；notch OFF）两档对比 —— 论证 mon 档不劣于 diag')
    p('    口径：a1 基线位移 mV（限 ≤0.1）｜a2 脉冲终点后 **线性拟合 +0.2~1.2 s** mV/s（限 ≤0.3，T52 §10.2 定论）')
    p('          ｜a3 边沿过冲 %（限 <10）')
    p('-' * 116)
    p('  %-10s %-12s %12s %12s %12s' % ('mode', '上角', 'a1(mV)', 'a2(mV/s)', 'a3(%)'))
    p('-' * 116)
    for mode, topname in MODES:
        a1, a2, a3 = pulse_metrics(ab, mode)
        p('  %-10s %-12s %12.4f %12.4f %12.3f' % (mode, topname, a1, a2, a3))
    p('-' * 116)
    p('  ★ 机理：a1/a2 由**低端高通（dc_block @%.4g Hz）**决定 ⇒ 两档**同一节、逐位相同**；'
      % dc_fc)
    p('    a3（边沿过冲）**两档不同**：AFE 已把边沿圆化，LP2@250 近乎跟随输入（过冲小），')
    p('    而 LP2@45 对圆化后的边沿仍保有 2 阶 Butterworth 过冲特性 ⇒ **mon 档过冲更大**，')
    p('    但仍远离 10% 限（余量 ~6.7 pp）⇒ 方向安全。')
    p('    ⇒ **a1/a2 两档等价；a3 mon 略大但过**。diag 已在 T84/T88 验收 ✓；')
    p('      上板时建议复测 a1/a2/a3 各一次（改动了第 15 节，属结构性改动）。')
    p()

    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(_buf) + '\n')
    print('[输出] %s' % OUT)


def pulse_metrics(ab, mode, amp=3.0, width_ms=100.0, tr_ms=2.4):
    """3 mV/100 ms 方波 → a1 位移 / a2 斜率 / a3 过冲。

    tr_ms：边沿圆化（一阶低通，模拟 AFE 把理想边沿变缓；T52 标定 AFE 边沿 ~2~3 点）。
    """
    fs = M.FS
    n_pre = int(round(2.0 * fs))
    n_w = int(round(width_ms / 1000.0 * fs))
    n_post = int(round(4.0 * fs))
    x = np.zeros(n_pre + n_w + n_post)
    x[n_pre:n_pre + n_w] = amp
    if tr_ms > 0:
        a = float(np.exp(-1.0 / (tr_ms / 1000.0 * fs)))
        y = np.zeros_like(x)
        yp = 0.0
        for i, v in enumerate(x):
            yp = v * (1.0 - a) + a * yp
            y[i] = yp
        x = y
    y = M._sim_chain(ab, mode, x)
    t = np.arange(len(x)) / fs
    te = (n_pre + n_w) / fs                      # 脉冲终点
    base_pre = float(y[n_pre - int(1.0 * fs):n_pre].mean())
    base_post = float(y[-int(2.0 * fs):].mean())
    a1 = abs(base_post - base_pre)
    # a2 ★ 口径按 T52 §10.2 定论：**线性拟合、区间写死 +0.20~1.20 s**
    #    （滑窗口径不可复现：窗宽 50→200 ms 读数变 1.67×；且被噪声污染；
    #      且必须**排除降沿本身**——含则必然爆表）
    sel = (t >= te + 0.20) & (t <= te + 1.20)
    a2 = float(abs(np.polyfit(t[sel] - t[sel][0], y[sel], 1)[0]))
    plateau = float(y[n_pre + int(0.02 * fs): n_pre + n_w].mean())
    peak = float(y[n_pre:n_pre + n_w].max())
    a3 = (peak - plateau) / amp * 100.0
    return a1, a2, a3


if __name__ == '__main__':
    main()
