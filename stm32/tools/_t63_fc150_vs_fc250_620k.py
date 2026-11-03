# -*- coding: utf-8 -*-
"""
T63 · fc150 vs fc250：同一份 620kΩ 实测 raw 上的离线对比

背景
----
T58(09-20 19:05) 把**六档全部**的 BP 从
    LP4@150（2 节，旧）+ HP4@0.05（2 节）
改成
    LP2@250（1 节，新）+ HP4@0.05（2 节）
⇒ 各档节数 -1。IMP620_N2 由 13 节 → 12 节（9 陷波节一字未动）。
diff 已确认 ecg_filter.c 的 87 行改动 100% 是注释与系数，**无任何代码逻辑改动**。

问题
----
猛哥报告：fc250 后 620 kΩ 输入阻抗测试不达标，且表现有两种：
    (a) p-p **超标**（干扰叠加）
    (b) p-p **低于设定值**（幅度衰减）
(a) 有现成机理（LP2@250 对 200 Hz 以上只压 -0.89 dB，LP4@150 压 -13.6 dB）。
(b) **没有**机理——因为两条链在 1 Hz 处直流增益都是 0 dB、分压也只由模拟侧决定。
本脚本用**同一份实测 raw（fc150 时代抓的 620k 接入数据）**驱动两条链，逐项比。

口径纪律
--------
- 列② raw 与列⑤ filt **都是 code 域**，链内部按 µV 算 ⇒ 输入必须 `(raw - 零点6075000) × UVPC`。
- 853 域跑完整链后才 resample500（复现固件 ecg_out.c 的纯线性插值）。
- "屏幕 p-p"用 09-18 口径：**1 s 窗 p-p 的中位数**。

🛑🛑 本脚本的适用前提（2026-09-21 实测踩到）
------------------------------------------
**输入 txt 必须是 `test raw853 on` 抓的（853 域，每样本直接入队）。**
`raw853 off`（默认）抓的是 **500 域**，此时 `853→500` 的重采样**已经把 250~426 Hz
折叠进 73.4~250 Hz**，而 fc250 的全部影响恰恰就在那一段 ⇒ 折叠**不可逆** ⇒
本脚本会给出**量级错、方向也不可信**的结果（实测：输出 p-p 差 36×、逐 bin 法差 35×）。

判定数据是不是 500 域的**可靠办法**（别猜）：
    对候选 fs 按 1 个信号周期折叠，取组内方差最小的那个。
    本批 620k 数据：fs=500 ⇒ 周期 499 样本、方差 7.6e5；fs=853.24 ⇒ 周期 831、方差 1.8e7
    ⇒ 差 24 倍 ⇒ 是 500 Hz。

另外两条硬约束：
- **段长 ≥30 s**：dc_block(0.05 Hz) 的 τ=3.18 s，段太短暂态不收敛（实测残留 mean 8753 µV）。
- 级联每节 `sosfilt` 都用零初值 ⇒ 9 节高 Q 陷波在 620k 的 27 mV 工频下要数秒才收敛，
  实测列⑤没有这段暂态 ⇒ 必须丢弃前几秒（脚本内 BURN=4 s）。

用法
----
    python _t63_fc150_vs_fc250_620k.py [--file <txt>] [--seg-start 2 --seg-len 8]
输出
----
    _t63_out.txt
"""
import io
import os
import re
import sys

import numpy as np
import scipy.signal as ss

import _t56_raw853_lowfreq as T56
import _t52_yy0885_notchoff as T52m

HERE = os.path.dirname(os.path.abspath(__file__))
FS = 853.2423208191125
UVPC = 0.1122

F_CUR = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')
F_BAK = os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak')
F_MEAS = os.path.join(HERE, '1mv-square-notch-620k(in)-2026-09-20 134612.txt')

OUT = []
def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 78)
    say(s)
    say('=' * 78)


# --------------------------------------------------------------- 系数解析

def parse_profile(path, prof='IMP620_N2'):
    """从 ecg_filter.c 里取指定档的 F64 系数表 -> sos 数组 [b0,b1,b2,1,a1,a2]。"""
    s = io.open(path, encoding='utf-8', errors='replace').read()
    m = re.search(r'#elif \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)(.*?)(?=\n#elif )' % prof,
                  s, re.S)
    if not m:
        raise RuntimeError('未找到档位 %s in %s' % (prof, path))
    body = m.group(1)
    m2 = re.search(r'ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{(.*?)\n\};', body, re.S)
    if not m2:
        raise RuntimeError('未找到 F64 表 in %s' % path)
    txt = re.sub(r'/\*.*?\*/', '', m2.group(1), flags=re.S)
    txt = re.sub(r'//[^\n]*', '', txt)
    sos = []
    for g in re.findall(r'\{([^{}]*)\}', txt):
        v = [float(x) for x in g.split(',') if x.strip()]
        if len(v) == 5:
            sos.append([v[0], v[1], v[2], 1.0, v[3], v[4]])
        elif len(v) == 6:
            sos.append(v)
    return np.asarray(sos, float)


def n_notch(path, prof='IMP620_N2'):
    s = io.open(path, encoding='utf-8', errors='replace').read()
    m = re.search(r'ECG_PROFILE_%s\)\n#define ECG_NUM_SECTIONS (\d+)\n#define ECG_NUM_NOTCH_SECTIONS (\d+)'
                  % prof, s, re.S)
    if m:
        return int(m.group(1)), int(m.group(2))
    # coeffs.h 里才有宏
    return None, None


# --------------------------------------------------------------- 链

def run_chain(x_uv, sos, nn, notch_on=True, fs=FS):
    """dc_block(1 阶 0.05) -> [notch 节] -> [BP 节]。"""
    y = T52m.dc_block_apply(np.asarray(x_uv, float), 0.05, fs)
    i0 = 0 if notch_on else nn
    for i in range(i0, len(sos)):
        y = ss.sosfilt(sos[i:i + 1], y)
    return y


def Hdb(sos, f, fs=FS):
    w = 2 * np.pi * np.asarray(f, float) / fs
    z = np.exp(-1j * w)
    H = np.ones(len(np.atleast_1d(f)), complex)
    for s in sos:
        H *= (s[0] + s[1] * z + s[2] * z ** 2) / (s[3] + s[4] * z + s[5] * z ** 2)
    return 20 * np.log10(np.abs(H))


# --------------------------------------------------------------- 指标

def winpp_med(y, fs, w=1.0, step=0.25):
    """1 s 窗 p-p 的中位数（09-18 屏幕口径）。"""
    n = int(w * fs)
    st = max(1, int(step * fs))
    v = [float(y[i:i + n].max() - y[i:i + n].min())
         for i in range(0, len(y) - n, st)]
    return float(np.median(v)) if v else float('nan')


def plat_amp(y, fs, period=1.0):
    """1 Hz 方波平台幅度：按周期折叠，取高/低电平各自中位之差。"""
    n = int(round(period * fs))
    if len(y) < 2 * n:
        return float('nan')
    k = len(y) // n
    z = y[:k * n].reshape(k, n)
    prof = np.median(z, axis=0)
    lo = np.median(prof[prof < np.median(prof)])
    hi = np.median(prof[prof >= np.median(prof)])
    return float(hi - lo)


def spec_amp(y, fs, freqs, fs_out=None):
    """连续段加窗 FFT 幅度（µV）。"""
    seg = np.asarray(y, float)
    seg = seg - seg.mean()
    w = np.hanning(len(seg))
    F = np.fft.rfft(seg * w)
    f = np.fft.rfftfreq(len(seg), 1.0 / (fs_out or fs))
    return {q: float(2 * abs(F[int(np.argmin(np.abs(f - q)))]) / w.sum()) for q in freqs}


def longest_on(d, thr=6000):
    on = d['dtc'] > thr
    best = (0, 0)
    i = 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        else:
            i += 1
    return best


# --------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    f_meas = F_MEAS
    if '--file' in args:
        f_meas = args[args.index('--file') + 1]

    hdr('T63 · fc150 vs fc250：同一份 620kΩ 实测 raw 的离线对比')
    say('输入数据 : %s' % os.path.basename(f_meas))
    say('旧链系数 : %s' % os.path.relpath(F_BAK, HERE))
    say('新链系数 : %s' % os.path.relpath(F_CUR, HERE))
    say('FS = %.4f Hz（853 域）｜UVPC = %.4f µV/code' % (FS, UVPC))

    sos_old = parse_profile(F_BAK)
    sos_new = parse_profile(F_CUR)
    say()
    say('系数表：旧链 %d 节 ｜ 新链 %d 节' % (len(sos_old), len(sos_new)))

    # 结构自检：BP 之外（即 9 个陷波节）必须逐位相同
    nn = 9
    assert len(sos_old) == nn + 4 and len(sos_new) == nn + 3, \
        '节数不符：old=%d new=%d' % (len(sos_old), len(sos_new))
    same = all(np.allclose(sos_old[i], sos_new[i], rtol=0, atol=1e-12) for i in range(nn))
    say('陷波 9 节逐位相同：%s' % ('是 ✓' if same else '否 ✗（需复核）'))
    # 低端 HP 2 节（链尾）也必须相同
    hp_old = [sos_old[i].tolist() for i in (nn + 2, nn + 3)]
    hp_new = [sos_new[i].tolist() for i in (nn + 1, nn + 2)]
    say('HP4@0.05 两节逐位相同：%s' % ('是 ✓' if hp_old == hp_new else '否 ✗'))

    # ---- 频响差
    hdr('A · 频响差（853 域，含 9 陷波 + HP4@0.05）')
    fq = np.array([1, 5, 10, 40, 50, 100, 150, 160, 180, 200, 203, 220, 250, 300, 350, 400])
    d_old = Hdb(sos_old, fq)
    d_new = Hdb(sos_new, fq)

    def chain_db(sos, f):
        """含 dc_block(0.05) 的整链（dc 用解析式）。"""
        z = np.exp(-2j * np.pi * f / FS)
        a = 1 - z  # dc_block 近似（0.05Hz 一阶），低频段用精确值更稳
        Hdc = 1 - z
        return None

    say('%8s %12s %12s %10s %10s' % ('f(Hz)', '旧LP4@150', '新LP2@250', '差(dB)', '倍数'))
    for i, q in enumerate(fq):
        say('%8.1f %12.2f %12.2f %10.2f %10.2f'
            % (q, d_old[i], d_new[i], d_new[i] - d_old[i],
               10 ** ((d_new[i] - d_old[i]) / 20)))
    say()
    say('★ 判读：f ≤ 150 Hz 两链几乎同值；差异全部在 150 Hz 以上 —— 这段是 620k∥4.7nF')
    say('  网络注入与 853→500 折叠的共同来源。')

    # ---- 读实测 raw
    hdr('B · 实测输入（fc150 时代抓的 620k 接入数据）')
    d = T56.load7(f_meas)
    t = (d['idx'] - d['idx'][0]) / FS
    i0, i1 = longest_on(d)
    say('样本 %d ｜ 时长 %.2f s ｜ 坏行 %d' % (d['n'], t[-1], d['nbad']))
    say('最长 ON 段：idx[%d:%d]  dtc 中位 %.0f  ⇒ 折合 %.2f 节（(dtc-1373)/641.3）'
        % (i0, i1, np.median(d['dtc'][i0:i1]),
           (np.median(d['dtc'][i0:i1]) - 1373) / 641.3))
    # ★★ 量纲：列② 是原始 ADC 码，零点 6075000（T50 §1）。不减零点 ⇒ 68 万 µV 的 DC
    #    会让 dc_block 的 3.18 s 暂态污染全部统计。
    ZERO = 6075000.0
    raw_uv = (d['raw'][i0:i1] - ZERO) * UVPC
    say('段内 raw 均值 %.1f code ⇒ 相对零点 %+.1f µV' % (d['raw'][i0:i1].mean(),
                                                        (d['raw'][i0:i1].mean() - ZERO) * UVPC))
    say('该段 raw：p-p %.1f µV ｜ 1 s 窗 p-p 中位 %.1f µV' % (raw_uv.max() - raw_uv.min(),
                                                             winpp_med(raw_uv, FS)))
    fr = spec_amp(raw_uv, FS, [1, 40, 49.8, 50, 50.2, 100, 150, 203, 250, 300, 350])
    say('raw 段谱线(µV)：' + '  '.join('%g:%.0f' % (k, v) for k, v in fr.items()))
    # 自证锚点：T50 §4 说 620k 接入选 on1 段的**列⑤**方波幅度 = 1199 µV、列⑤ p-p = 1824 µV
    y5_meas = d['filt'][i0:i1] * UVPC
    say('★ 自证锚点（T50 §4 on1 段）：列⑤实测 方波幅度 %.1f µV ／ p-p %.1f µV'
        % (plat_amp(y5_meas, FS), y5_meas.max() - y5_meas.min()))
    say('                    （T50 记录值：1199 µV ／ 1824 µV）')

    # ---- 两链输出
    hdr('C · 同一 raw 过两条链（notch 全开）')
    # ★★ 必须丢弃启动暂态：ss.sosfilt 每节 zi=0，而链首 9 个陷波节在 620k 接入的
    #    27 mV 工频输入下要 ~1 s 才把输出拉到与输入匹配；9 节级联后暂态可长达数秒。
    #    实测列⑤是固件连续运行的产物，**没有这段暂态**，不丢就没法比。
    BURN = int(4.0 * FS)
    y_old_full = run_chain(raw_uv, sos_old, nn, True)
    y_new_full = run_chain(raw_uv, sos_new, nn, True)
    y_old = y_old_full[BURN:]
    y_new = y_new_full[BURN:]
    say('启动暂态已丢弃：前 %.1f s（%d 点）' % (BURN / FS, BURN))
    say('稳态段时长 %.2f s' % (len(y_old) / FS))
    say('★ 自证：旧链稳态段 p-p %.1f µV  ←→ 列⑤实测该段 p-p %.1f µV'
        % (y_old.max() - y_old.min(), y5_meas[BURN:].max() - y5_meas[BURN:].min()))

    # resample 到 500 Hz（复现固件）
    def res(y):
        try:
            r = T56.fw_resample500(y)
            return np.asarray(r[0] if isinstance(r, tuple) else r, float)
        except Exception as e:
            say('  [warn] resample 失败：%s' % e)
            return np.asarray(y, float)

    y_old5 = res(y_old)
    y_new5 = res(y_new)
    say('853 域 %d 点 → 500 域 %d 点（旧）/ %d 点（新）' % (len(y_old), len(y_old5), len(y_new5)))

    rows = [
        ('输出 p-p（整段）', y_old.max() - y_old.min(), y_new.max() - y_new.min(), 'µV'),
        ('输出 p-p（1 s 窗中位）', winpp_med(y_old, FS), winpp_med(y_new, FS), 'µV'),
        ('输出 rms', float(np.sqrt(np.mean(y_old ** 2))), float(np.sqrt(np.mean(y_new ** 2))), 'µV'),
        ('平台幅度（1 Hz 折叠）', plat_amp(y_old, FS), plat_amp(y_new, FS), 'µV'),
        ('500 域 p-p（1 s 窗中位）', winpp_med(y_old5, 500.0), winpp_med(y_new5, 500.0), 'µV'),
        ('500 域平台幅度', plat_amp(y_old5, 500.0), plat_amp(y_new5, 500.0), 'µV'),
    ]
    say()
    say('%-26s %12s %12s %10s %8s' % ('指标', '旧链', '新链', '新/旧', '单位'))
    for nm, a, b, u in rows:
        r = (b / a) if a not in (0, float('nan')) else float('nan')
        say('%-26s %12.2f %12.2f %10.4f %8s' % (nm, a, b, r, u))

    say()
    say('输出谱线对比(µV)：')
    so = spec_amp(y_old, FS, [1, 40, 49.8, 50, 100, 150, 203, 250, 300, 350])
    sn = spec_amp(y_new, FS, [1, 40, 49.8, 50, 100, 150, 203, 250, 300, 350])
    say('  %8s %12s %12s %10s' % ('f(Hz)', '旧链', '新链', '新/旧'))
    for k in so:
        r = sn[k] / so[k] if so[k] else float('nan')
        say('  %8g %12.2f %12.2f %10.3f' % (k, so[k], sn[k], r))

    # ---- 理想方波对照（无噪声无干扰）
    hdr('D · 对照：理想 1 mV / 1 Hz 方波（零噪声零干扰）')
    n_per = int(round(1.0 * FS))
    x_ideal = np.where((np.arange(10 * n_per) % n_per) < n_per // 2, 500.0, -500.0)
    yo = run_chain(x_ideal, sos_old, nn, True)
    yn = run_chain(x_ideal, sos_new, nn, True)
    say('%-26s %12s %12s %10s' % ('指标', '旧链', '新链', '新/旧'))
    for nm, a, b in (('p-p（整段）', yo.max() - yo.min(), yn.max() - yn.min()),
                     ('平台幅度', plat_amp(yo, FS), plat_amp(yn, FS))):
        say('%-26s %12.3f %12.3f %10.5f' % (nm, a, b, b / a if a else float('nan')))
    say()
    say('★ 若理想方波（零干扰）下两链 p-p 也几乎同值 ⇒ "p-p 低于设定值"不可能由 LP 阶数解释。')

    # ---- 单个注入分量：203 Hz（620k 网络主注入）
    hdr('E · 对照：620k∥4.7nF 注入主分量（203 Hz / 150 Hz / 300 Hz）')
    tt = np.arange(8 * FS) / FS
    say('%-10s %14s %14s %10s' % ('注入(Hz)', '旧链残余(µV)', '新链残余(µV)', '新/旧'))
    for q in (150.0, 203.0, 250.0, 300.0, 350.0):
        xi = 100.0 * np.sin(2 * np.pi * q * tt)
        a = float(np.sqrt(np.mean(run_chain(xi, sos_old, nn, True)[-4 * int(FS):] ** 2)))
        b = float(np.sqrt(np.mean(run_chain(xi, sos_new, nn, True)[-4 * int(FS):] ** 2)))
        say('%-10g %14.4f %14.4f %10.3f' % (q, a, b, b / a if a else float('nan')))

    # ---- 结论位
    hdr('F · 结论位')
    r_pp = winpp_med(y_new, FS) / winpp_med(y_old, FS)
    say('· 同一 raw 下 1 s 窗 p-p：旧 %.1f → 新 %.1f µV（比值 %.4f）'
        % (winpp_med(y_old, FS), winpp_med(y_new, FS), r_pp))
    say('· 平台幅度：旧 %.2f → 新 %.2f µV（比值 %.5f）'
        % (plat_amp(y_old, FS), plat_amp(y_new, FS),
           plat_amp(y_new, FS) / plat_amp(y_old, FS)))
    say('· 结论口径：本脚本是**链模型离线重放**，不是 fc250 上板实测。')
    say('  它能判"fc250 能不能造成某现象"，不能替代现场数据。')

    io.open(os.path.join(HERE, '_t63_out.txt'), 'w', encoding='utf-8').write('\n'.join(OUT))
    print('\n[written] _t63_out.txt')


if __name__ == '__main__':
    main()
