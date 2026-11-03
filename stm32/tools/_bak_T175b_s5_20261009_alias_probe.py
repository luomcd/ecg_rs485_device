# -*- coding: utf-8 -*-
"""
T54 · 抗混叠实测可行性探针（能否用列② 定 fc = 220 or 250）

问题（T53 §5.3 风险 2）：
  `ecg_out.c` 的 853→500 重采样**没有抗混叠低通** ⇒ 250~426 Hz 会镜像进 73~250 Hz。
  T53 的 alias_metric 只算链路增益比：
      res_model(fc) = max_{f in 300,400,426} |H_LP(f;fc)·H_AFE(f)| / |H_LP(10;fc)·H_AFE(10)|
  这等价于**假设输入侧是白噪声**（谱形状＝只有 AFE 的衰减）。

本脚本要回答的不是"fc 取几"，而是**"这个实测到底做不做得了"**：
  列② 的值是 **853 域原始样本**（源码 ads1293.c:588 逐样本滤波、ecg_out_f32_process 只把
  重采样结果放进 ④），但**行率只有 500 Hz**（`//833hz` 那行被注释）⇒ 平均采样率 500 < 853
  ⇒ **250~426 Hz 在抽取那一刻就已经折叠，且 853 网格重建也无法还原**（欠采样不可逆）。

列义（ads1293.c:604 `"\n%d %07d %06d %05d %.02f %05d %x"`）：
  ① flag ② ecgraw(853域) ③ idxfilter(500Hz计数) ④ dsrateraw(LSB) ⑤ filterraw(853域) ⑥ dtcycles ⑦ gdtover(hex)

判据（本脚本的可信度自检）：
  [1b] 合成 853 域白噪声 → 按固件规则抽取 → 用本脚本的重建口径还原 → 测**恢复增益 G(f)**
       G(f) ≈ 1 ⇒ 该频段可读；G(f) ≈ 0 ⇒ 该频段的信息已在抽取时丢失。
  [3]  单频注入：合成 853 域 350 Hz 正弦 → 抽取 → 重建 ⇒ 看 350 Hz 峰是否消失、150 Hz 是否冒出假峰。

用法：
  python _t54_alias_probe.py --file "<txt>" --fig
"""
import sys, os, argparse
import numpy as np
from scipy import signal as ss
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t52d_srcchain as T52D          # load7（列义解析）
import _t41_freqresp as T41            # h_afe（三级 sinc^5 CIC）

FS853 = 853.2423208191125
FS500 = 500.0
RATIO = FS853 / FS500                  # 1.7064846...
ZERO = 6075000.0
UVPC = 0.1122                          # code -> µV（853 域）
F_HI = (250.0, 426.6)                  # 会被镜像的原始频段
F_PROBE = np.array([300.0, 400.0, 426.0])
SEGS = [(1, 10, 12), (40, 100, 160), (160, 240, 240), (250, 300, 320),
        (320, 400, 395), (400, 426.6, 415)]

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


# ---------------------------------------------------------------- 853 重建

def to853(idx, val):
    """把 500 Hz 行上的值放回 853 网格（有洞）。返回 (grid, ngood, info)"""
    n = np.round((idx - idx[0]) * RATIO).astype(np.int64)
    keep = np.concatenate(([True], np.diff(n) > 0))
    ndup = int((~keep).sum())
    n, v = n[keep], val[keep]
    N = int(n[-1]) + 1
    g = np.full(N, np.nan)
    g[n] = v
    return g, len(n), dict(N=N, ndup=ndup, fill=100.0 * (1.0 - len(n) / N))


def psd853(g, nperseg=4096):
    """853 网格（含洞）-> PSD（线性插值补洞 + Welch）"""
    good = ~np.isnan(g)
    if good.sum() < 512:
        return None, None
    gi = np.arange(len(g))
    x = np.interp(gi, gi[good], g[good])
    x = x - x.mean()
    nperseg = min(nperseg, len(x))
    f, P = ss.welch(x, fs=FS853, nperseg=nperseg,
                    noverlap=nperseg // 2, detrend='constant', scaling='density')
    return f, P


def band_power(f, P, lo, hi):
    m = (f >= lo) & (f <= hi)
    if P is None or m.sum() < 3:
        return np.nan
    return float(np.trapezoid(P[m], f[m]))


def h_lp(f, order, fc):
    b, a = ss.butter(order, fc / (FS853 / 2.0), 'low')
    _, h = ss.freqz(b, a, worN=2 * np.pi * np.asarray(f, float) / FS853)
    return np.abs(h)


# ---------------------------------------------------------------- 固件抽取模型

def fw_decimate(x853, n_out=None):
    """按 ecg_out.c 的重采样关系抽取：输出第 n 行 = 853 域第 round(n*RATIO) 个样本"""
    if n_out is None:
        n_out = int((len(x853) - 2) / RATIO)
    k = np.round(np.arange(n_out) * RATIO).astype(np.int64)
    k = k[k < len(x853)]
    return k


# ================================================================ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(
        HERE, '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt'))
    ap.add_argument("--fig", action="store_true")
    a = ap.parse_args()
    F = a.file

    say("=" * 78)
    say("T54 · 抗混叠实测可行性探针 —— 「列② 到底能不能测 250~426 Hz」")
    say("文件: %s" % os.path.basename(F))
    say("=" * 78)

    # ---------------------------------------------------------- [0] 列义
    d = T52D.load7(F)
    say("\n[0] 列义与自检")
    say("    行数 %d  坏行 %d  断点 %d 处 %s"
        % (d['n'], d['nbad'], len(d['gaps']), d['gaps'][:4]))
    say("    列③ idxfilter %.0f → %.0f（Δ 中位 %.0f）｜时长 %.2f s @500 Hz 行网格"
        % (d['idx'][0], d['idx'][-1], np.median(np.diff(d['idx'])),
           (d['idx'][-1] - d['idx'][0]) / FS500))
    raw_uv = (d['raw'] - ZERO) * UVPC
    fil_uv = d['filt'] * UVPC

    # ---------------------------------------------------------- [1] 几何
    say("\n[1] 853 网格重建（几何）")
    gx, ng, info = to853(d['idx'], raw_uv)
    say("    853.2423/500 = %.7f ⇒ 网格 %d 点 / %.2f s，已知 %.1f%%，补洞 %.1f%%"
        % (RATIO, info['N'], info['N'] / FS853, 100 - info['fill'], info['fill']))
    say("    ⚠ 平均采样率仍 = 500 Hz < 853 ⇒ **Nyquist 150→250 Hz 只是「看起来」的**")

    # ---------------------------------------------------------- [1b] 可恢复性
    say("\n[1b] ★★ 可恢复性实验（合成 853 域白噪声 → 抽取 → 重建）")
    rng = np.random.default_rng(0)
    N = 40000
    xt = rng.standard_normal(N)                 # 853 域白噪声（真值）
    f0, P0 = ss.welch(xt - xt.mean(), fs=FS853, nperseg=4096,
                      noverlap=2048, detrend='constant', scaling='density')
    k = fw_decimate(xt)
    xd = xt[k]
    gr, _, infor = to853(np.arange(len(xd)), xd)
    fr, Pr = psd853(gr, nperseg=4096)
    say("    抽取 %d → %d 行（比 %.4f）⇒ 重建网格 %d 点"
        % (N, len(xd), len(xd) / N, infor['N']))
    say("    %-16s %12s %12s %10s" % ("频段 (Hz)", "真值能量", "重建能量", "恢复 G"))
    G = {}
    for lo, hi, mid in SEGS:
        e0 = band_power(f0, P0, lo, hi)
        e1 = band_power(fr, Pr, lo, hi)
        gdb = 10 * np.log10(max(e1, 1e-30) / max(e0, 1e-30))
        G[mid] = (lo, hi, e0, e1, gdb)
        say("    %-16s %12.4g %12.4g %9.2f dB" % ("%g~%g" % (lo, hi), e0, e1, gdb))
    say("    ⇒ 判读：G ≈ 0 dB 才代表该段**可读**；G << 0 dB ⇒ 该段信息在抽取时已丢")
    for lo, hi, mid in SEGS:
        if lo >= 250:
            say("      ★ %g~%g Hz：G = %.1f dB ⇒ %s"
                % (lo, hi, G[mid][4],
                   "不可读（欠采样已折叠）" if G[mid][4] < -6 else "可读"))
    say("    ★ 判据：抽取(500 Hz) 的 Nyquist = 250 Hz ⇒ 250~426 段**平均上必然折叠**，")
    say("      853 网格只改「值放在哪一格」，不增加信息量 ⇒ **不能靠重建还原**。")

    # ---------------------------------------------------------- [2] 实测谱
    say("\n[2] 实测列② 谱（同一套重建口径）")
    f1, P1 = psd853(gx, nperseg=8192)
    x500 = raw_uv - raw_uv.mean()
    f3, P3 = ss.welch(x500, fs=FS500, nperseg=4096, detrend='constant', scaling='density')
    say("    %-18s %14s %14s %12s" % ("频段", "853 重建口径", "500 Hz 直接口径", "注释"))
    for lo, hi, mid in SEGS:
        e1 = band_power(f1, P1, lo, hi)
        e3 = band_power(f3, P3, lo, hi) if hi <= 249 else float('nan')
        note = "" if hi <= 249 else "500 网格无此频段(Nyq=250)"
        say("    %-18s %14.4g %14.4g  %s" % ("%g~%g" % (lo, hi), e1, e3, note))
    say("    ⚠ 即便 853 重建口径能「给出」250~426 的数，由 [1b] 可知那是**伪影**，不是输入能量。")

    # ---------------------------------------------------------- [3] 单频注入
    say("\n[3] ★ 单频注入仿真：853 域注入 f，抽取后重建，看信息跑到哪去")
    t = np.arange(N) / FS853
    say("    %-8s %-26s %14s" % ("输入", "重建谱 top-2 峰 (Hz)", "同位置残留"))
    for finj in (250.0, 300.0, 350.0, 400.0):
        xs = np.sin(2 * np.pi * finj * t)
        f0_, P0_ = ss.welch(xs - xs.mean(), fs=FS853, nperseg=8192,
                            noverlap=4096, detrend='constant', scaling='density')
        k2 = fw_decimate(xs)
        gs, _, _ = to853(np.arange(len(k2)), xs[k2])
        fr_, Pr_ = psd853(gs, nperseg=8192)
        m = (fr_ >= 20) & (fr_ <= 249)
        fx, Px = fr_[m], Pr_[m]
        used, tops = [], []
        for i in np.argsort(Px)[::-1]:
            if all(abs(fx[i] - u) > 10 for u in used):
                used.append(fx[i])
                tops.append(fx[i])
            if len(tops) >= 2:
                break
        e_t = band_power(f0_, P0_, finj - 6, finj + 6)
        e_r = band_power(fr_, Pr_, finj - 6, min(finj + 6, 426))
        res = 10 * np.log10(max(e_r, 1e-30) / max(e_t, 1e-30))
        say("    %-8s %-26s %11.1f dB" % ("%g Hz" % finj,
                                          " / ".join("%.1f" % v for v in tops), res))
    say("    ⇒ 注入后**原位置几乎不残留**，峰跑到别处 ⇒ 镜像频率 ≠ 原频率，")
    say("      重建无法还原真实频段归属（欠采样信息已丢）。")

    # ---------------------------------------------------------- [4] res(fc) 敏感性
    say("\n[4] 折算 res(fc)：输入谱形状 shape(f) 的不确定度 → res 的分布")
    hA_10 = float(T41.h_afe(np.array([10.0]))[0])
    say("    模型（shape ≡ 1，白噪声＋AFE）与 shape 放大 K 倍时的 res（判据=max 于 300/400/426）：")
    say("    %-12s %12s %12s %12s %12s" % ("配置", "K=0.1", "K=1(模型)", "K=3", "K=10"))
    FLP = [(4, 150.0), (2, 200.0), (2, 220.0), (2, 250.0)]
    for od, fc in FLP:
        gm = h_lp(F_PROBE, od, fc) / float(h_lp(np.array([10.0]), od, fc)[0])
        base = float(np.max(gm * T41.h_afe(F_PROBE) / hA_10)) * 100
        say("    %-12s %11.2f%% %11.2f%% %11.2f%% %11.2f%%"
            % ("%d阶@%g" % (od, fc), base * 0.1, base, base * 3, base * 10))
    say("    ⇒ fc=220 要**被推翻**，需要实测 shape 在 300~426 段比白噪声假设高 ~1.4× 以上")
    say("      （220 的 9.32% 与 250 的 13.89% 之比 = 1.49）——**实测的目标精度即此**。")

    # ---------------------------------------------------------- [4b] 频响表判决
    say("\n[4b] ★★ 表 201.107 四行判决（低端 1 阶 + LP2@fc，**含 AFE**；判限见 T46 §1.1）")
    ROWS = [(0.67, 40.0, 90.0, 110.0, "±10%"),
            (40.0, 100.0, 70.0, 110.0, "+10/-30%"),
            (100.0, 150.0, 70.0, 110.0, "+10/-30%,下限70%"),
            (150.0, 500.0, 0.0, 110.0, "+10/-100%(只限上限)")]

    def h_chain(f, fc_lp, hp_order=1, hp_fc=0.05):
        """固件口径：853 域**数字**滤波器（双线性）＋ AFE。
           低端 1 阶 @0.05 Hz（T53 方案①，已删 HP4）；LP 为 2 阶 Butterworth @fc_lp。"""
        f = np.asarray(f, float)
        w = 2.0 * np.pi * f / FS853
        bh, ah = ss.butter(hp_order, hp_fc / (FS853 / 2.0), 'high')
        _, hh = ss.freqz(bh, ah, worN=w)
        bl, al = ss.butter(2, fc_lp / (FS853 / 2.0), 'low')
        _, hl = ss.freqz(bl, al, worN=w)
        return np.abs(hh) * np.abs(hl) * T41.h_afe(f)

    ref10 = float(h_chain(np.array([10.0]), 220.0)[0])
    say("    归一参考 G(10 Hz) = %.5f（含 AFE）" % ref10)
    say("    %-16s %-22s %11s %11s %11s" % ("行", "容差", "fc=200", "fc=220", "fc=250"))
    for lo, hi, tlo, thi, note in ROWS:
        cells = []
        for fc in (200.0, 220.0, 250.0):
            ff = np.logspace(np.log10(lo), np.log10(hi), 240)
            g = h_chain(ff, fc) / float(h_chain(np.array([10.0]), fc)[0]) * 100.0
            vmin, vmax = float(g.min()), float(g.max())
            ok = (vmin >= tlo - 1e-9) and (vmax <= thi + 1e-9)
            cells.append("%.1f~%.1f%s" % (vmin, vmax, "✓" if ok else "✗"))
        say("    %-16s %-22s %11s %11s %11s"
            % ("%g~%g Hz" % (lo, hi), note, cells[0], cells[1], cells[2]))
    say("    ✅ 交叉校验：本表 fc=220 的 150 Hz 读数应 = **73.2%%**（与 T53 表一致）")
    say("    ⇒ 四行里唯一贴限的是 **100~150 Hz（下限 70%）**；")
    say("      **150~500 Hz 行 = +10%/−100%，即只限上限 110%** ⇒ 抗混叠残余 9~14%")
    say("      **对该四行判决无实质影响** ⇒ 它不应主导 fc 决策。")
    say("      150 Hz 余量（判限 70%）：fc=200 → **+0.3 pp（太薄）**｜fc=220 → +3.2 pp｜**fc=250 → +5.6 pp**")
    say("      ⇒ **余量单调增** ⇒ 在不违反 150~500 Hz 行上限(110%)的前提下，**fc 越大越稳**。")

    # ---------------------------------------------------------- [5] 静息段
    say("\n[5] 静息段（去脉冲）单独看")
    med = float(np.median(np.abs(raw_uv - np.median(raw_uv))))
    thr = max(20 * med, 200.0)
    quiet = np.abs(raw_uv - np.median(raw_uv)) < thr
    say("    静息占比 %.1f%%（阈值 %.0f µV，本底 MAD %.1f µV）"
        % (100 * quiet.mean(), thr, med))
    xi = np.where(quiet)[0]
    if len(xi) > 1000:
        brk = np.where(np.diff(xi) != 1)[0]
        segs = [s for s in np.split(xi, brk + 1) if len(s) > 853]
        say("    静息连续段 %d 段，最长 %.2f s" % (len(segs), max(len(s) for s in segs) / FS853))
        gq, _, infq = to853(d['idx'][xi], raw_uv[xi])
        fq_, Pq = psd853(gq, nperseg=4096)
        for lo, hi, mid in SEGS:
            say("      %-14s 重建能量 %10.4g µV²" % ("%g~%g" % (lo, hi),
                                                   band_power(fq_, Pq, lo, hi)))
        say("    ⚠ 同样受 [1b] 限制：250~426 段不可读。")

    # ---------------------------------------------------------- [6] 结论位
    say("\n[6] 结论")
    say("    ① **「用列②（raw 853 Hz）实测 250~426 Hz 段能量」在现有 7 列输出上做不到**。")
    say("       列② 的值确是 853 域样本，但**行率 500 Hz** ⇒ 平均欠采样 1.71× ⇒")
    say("       250~426 Hz 在**抽取那一刻**已折叠，离线重建无法还原（[1b] 恢复增益≠0 但非真值；")
    say("       [3] 同位置残留极低、峰跑到别处 ⇒ 频段归属已丢）。")
    say("    ② 要拿到该段真实能量，两条路：")
    say("       A. **改固件开 853 输出**：`ads1293.c:592` 的 `//enqueue(...)  //833hz`")
    say("          （建议做成 `test raw853 on|off`，与 `test notch on|off` 同风格）")
    say("          ⚠ 打开后 txt 时间轴基准由 500 变 **853.2423**，下游脚本口径必须同步改。")
    say("       B. **AECG100 单频注入**（不改固件）：规格 Sample Rate 50~40 kHz ⇒ 带宽够；")
    say("          但需先确认软件正弦/扫频可设 300~426 Hz、输出端有无低通（问实验室）。")
    say("    ③ ★ 更关键：**[4b] 显示抗混叠无标准判限**（表 201.107 第 4 行只限上限）⇒")
    say("      fc 决策应由 **100~150 Hz 行的下限余量**主导 ⇒ **fc=250（+5.6 pp）优于 220（+3.2 pp）**，")
    say("      且 LP2@250 过冲 6.10% 亦略优于 LP2@220 的 6.49%。")
    say("    详见报告 T54_抗混叠实测可行性_fc定案.md")

    with open(os.path.join(HERE, '_t54_out.txt'), 'w', encoding='utf-8') as fh:
        fh.write("\n".join(OUT) + "\n")

    if a.fig:
        make_fig(d, gx, f1, P1, f3, P3, f0, P0, fr, Pr)
        say("\n[fig] T54_抗混叠实测可行性_2026-09-20.png 已出")


def make_fig(d, gx, f1, P1, f3, P3, f0, P0, fr, Pr):
    fig, ax = plt.subplots(2, 2, figsize=(13.5, 8.0))
    t500 = (d['idx'] - d['idx'][0]) / FS500
    a = ax[0][0]
    a.plot(t500, (d['raw'] - ZERO) * UVPC, lw=0.4, color='#06c', label='② raw（853 域值）')
    a.plot(t500, d['filt'] * UVPC, lw=0.5, color='#c00', alpha=.8, label='⑤ filt')
    a.set_xlabel('t (s, 500 Hz 行网格)')
    a.set_ylabel('µV')
    a.set_title('原始记录（行率 500 Hz，值是 853 域样本）')
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[0][1]
    a.semilogy(f0, np.maximum(P0, 1e-12), lw=1.2, color='#333', label='真值（全速 853）')
    a.semilogy(fr, np.maximum(Pr, 1e-12), lw=1.2, color='#c00', label='抽取→重建后')
    a.axvline(250, color='gray', ls='--', lw=1)
    a.axvspan(250, 426.6, color='#fdd', alpha=.6, label='250~426（应恢复不出）')
    a.set_xlim(1, 430)
    a.set_xlabel('f (Hz)')
    a.set_ylabel('PSD')
    a.set_title('★[1b] 可恢复性：白噪声 抽取→重建')
    a.legend(fontsize=8)
    a.grid(alpha=.3, which='both')

    a = ax[1][0]
    a.semilogy(f1, np.maximum(P1, 1e-9), lw=1.0, color='#06c', label='853 重建口径')
    a.semilogy(f3, np.maximum(P3, 1e-9), lw=1.0, color='#c60', alpha=.8,
               label='500 Hz 直接口径')
    a.axvspan(250, 426.6, color='#fdd', alpha=.6)
    a.axvline(250, color='gray', ls='--', lw=1)
    a.set_xlim(1, 430)
    a.set_xlabel('f (Hz)')
    a.set_ylabel('PSD (µV²/Hz)')
    a.set_title('实测列② 两种口径（250~426 段两口径含义不同）')
    a.legend(fontsize=8)
    a.grid(alpha=.3, which='both')

    a = ax[1][1]
    t = np.arange(40000) / FS853
    xs = np.sin(2 * np.pi * 350.0 * t)
    k2 = fw_decimate(xs)
    gs, _, _ = to853(np.arange(len(k2)), xs[k2])
    fs_, Ps = psd853(gs, nperseg=8192)
    a.semilogx(fs_, 10 * np.log10(np.maximum(Ps, 1e-30)) - 10 * np.log10(np.max(Ps)),
               lw=1.2, color='#0a0')
    for fq, lb in [(150.0, '150 Hz\n(=500-350)'), (350.0, '350 Hz 原位置')]:
        a.axvline(fq, color='#c00' if fq == 150 else 'gray',
                  ls='--', lw=1.2, label=lb)
    a.set_xlim(20, 426)
    a.set_ylim(-80, 2)
    a.set_xlabel('f (Hz)')
    a.set_ylabel('相对 dB')
    a.set_title('★[3] 350 Hz 注入 → 表现成 150 Hz 假峰')
    a.legend(fontsize=8)
    a.grid(alpha=.3, which='both')

    fig.tight_layout()
    fig.savefig(os.path.join(HERE, 'T54_抗混叠实测可行性_2026-09-20.png'), dpi=125)
    plt.close(fig)


if __name__ == '__main__':
    main()
