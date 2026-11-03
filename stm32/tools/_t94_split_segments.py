#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t94_split_segments.py —— 把「多频点串接」的抓数文件按空档自动切成单频点文件。

背景
----
现场为省事，把 5 Hz / 0.67 Hz / 40 Hz 连着抓成**一个** txt（中间把 AECG100 输出停掉，
留下一段接近 0 mV 的隔离带）。判读脚本 `_t92_mon_measure.py` 是「一文件一频点」口径，
所以先切分再判读。

切分原理
--------
1) 取 **列②（ecgraw，链前 raw24b）** 减中位 —— 它在固件滤波链**之前**，无群延迟，
   是最干净的分段依据（列⑤ 有滤波延迟，边界会糊）。
2) `--win`（默认 0.1 s）滑窗 RMS → 阈值 `--thr`（默认 15%）× 最大值 ⇒ 布尔「低能量」掩码。
3) **形态学填回**：长度 < `--min-gap`（默认 2 s）的低区填回信号。
   ★ 这一步是必须的：0.67 Hz 正弦在过零点附近 RMS 会掉到阈值以下，
     不加这一步会把 0.67 Hz 段切成一堆 0.7 s 的碎片（实测踩过）。
4) 剩下的低区 = 空档 ⇒ 切出信号段；每段两侧各内缩 `--trim`（默认 1.0 s）避开开/停机瞬态。
5) 每段用 Hann 窗 FFT 扫峰自证**主频与幅度**（供人工核对，不参与切分）。

输出
----
每段一个标准命名文件：`fr-<f>Hz-<amp>mVpp-<tag>-<stem>-s<i>.txt`
（★ **逐行原样复制**，不改任何数据位；坏行仍剔除并在报告里计数）
默认写到 `<源文件去扩展名>_seg/` **子目录** —— 刻意不写在本目录，
否则会被判读命令的 `"fr-*.txt"` 通配误抓进来（跨批混读 = 结论作废）。

用法
----
    P="C:/Users/luom/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    $P tools/_t94_split_segments.py "tools/mon(5&0.67&40hz)-3mv-sine-2026-09-22 110306.txt"
    $P tools/_t92_mon_measure.py "tools/mon(...)_seg/fr-*.txt" --mode mon --ref 5 --norm col2

频率与幅度优先从**源文件名**解析（`(5&0.67&40hz)`、`3mv`）；解析不到用 `--freqs/--amp` 指定；
再解析不到就退回**实测频率**。段与频点按**时间顺序**一一对应。
"""
import argparse
import io
import os
import re
import sys

import numpy as np

FS_DEF = 500.0


# ----------------------------------------------------------------- 读
def read_txt(path):
    """返回 (原始行, 数值矩阵, 坏行数, 总行数)。坏行 = 列数≠7 或列②越 24 bit。"""
    raw, vals, nbad, nrow = [], [], 0, 0
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for ln in fh:
            s = ln.rstrip('\r\n')
            if not s.strip():
                continue
            nrow += 1
            q = s.split()
            if len(q) != 7:
                nbad += 1
                continue
            try:
                v = [float(t) for t in q[:6]]
            except ValueError:
                nbad += 1
                continue
            if not (0.0 <= v[1] < 16777216.0):   # 列② 是 24 bit
                nbad += 1
                continue
            raw.append(s)
            vals.append(v)
    return raw, np.asarray(vals, float), nbad, nrow


# ------------------------------------------------------------ 分段
def low_mask(x, fs, win_s, thr_frac):
    """滑窗 RMS 低于 thr 的位置 ⇒ True。端点做边缘延拓，避免边界假低。"""
    w = max(3, int(round(win_s * fs)))
    k = np.ones(w) / w
    rms = np.sqrt(np.convolve(x * x, k, mode='same'))
    rms[:w] = rms[w]
    rms[-w:] = rms[-w - 1]
    return rms < (thr_frac * rms.max()), rms


def signal_segments(mask, fs, min_gap_s):
    """按掩码切出信号段 [(s,e),...]；低区长度 < min_gap_s 的填回信号。"""
    lo = mask.copy()
    n = len(lo)
    d = np.diff(lo.astype(np.int8))
    st = list(np.where(d == 1)[0] + 1)
    en = list(np.where(d == -1)[0] + 1)
    if lo[0]:
        st = [0] + st
    if lo[-1]:
        en = en + [n]
    filled = 0
    for s, e in zip(st, en):
        if (e - s) < min_gap_s * fs:
            lo[s:e] = False
            filled += 1

    sig = ~lo
    d2 = np.diff(sig.astype(np.int8))
    a = list(np.where(d2 == 1)[0] + 1)
    b = list(np.where(d2 == -1)[0] + 1)
    if sig[0]:
        a = [0] + a
    if sig[-1]:
        b = b + [n]
    return list(zip(a, b)), filled


# -------------------------------------------------------- 单频点自证
def tone(x, fs, fmin=0.3, fmax=80.0):
    """Hann 加窗 → FFT 粗扫带内峰值 → ±0.15 Hz 细化 DFT ⇒ (f, 峰 code)。"""
    xx = x - x.mean()
    N = len(xx)
    if N < 200:
        return 0.0, 0.0
    ww = np.hanning(N)
    X = np.abs(np.fft.rfft(xx * ww))
    fr = np.fft.rfftfreq(N, 1.0 / fs)
    band = (fr >= fmin) & (fr <= fmax)
    if not band.any():
        return 0.0, 0.0
    k = int(np.argmax(np.where(band, X, 0.0)))
    f0 = fr[k]
    # 细化：粗分辨率的 ±0.5 bin 内细扫（DFT 直接算，点数少、够快）
    df = max(0.002, float(fr[1] - fr[0]))
    ff = np.linspace(max(fmin, f0 - df), f0 + df, 61)
    nn = np.arange(N)
    AA = np.array([2.0 * abs(np.sum(xx * np.exp(-2j * np.pi * f * nn / fs))) / N for f in ff])
    j = int(AA.argmax())
    return float(ff[j]), float(AA[j])


# ------------------------------------------------------------ 名字
def freqs_from_name(b):
    m = re.search(r'\(([\d.&]+)hz\)', b, re.I)
    if m:
        out = [float(t) for t in m.group(1).split('&') if t.strip()]
        if out:
            return out
    return None


def amp_from_name(b):
    m = re.search(r'(\d+(?:\.\d+)?)\s*mv(?:\s*pp)?', b, re.I)
    return float(m.group(1)) if m else None


def stem_from_name(b):
    m = re.search(r'(\d{4}-\d{2}-\d{2})[ _]+(\d{6})', b)
    if m:
        return '%s %s' % (m.group(1), m.group(2))
    return os.path.splitext(b)[0]


def fmt_f(f):
    s = ('%.2f' % f).rstrip('0').rstrip('.')
    return s if s else '0'


# ------------------------------------------------------------ 主
def run(src, args, out_root):
    b = os.path.basename(src)
    raw, V, nbad, nrow = read_txt(src)
    if len(V) < 1000:
        print('  ✗ 有效行太少（%d），跳过' % len(V))
        return []
    fs = args.fs
    x = V[:, 1] - np.median(V[:, 1])          # 列② 链前（数值索引 1）
    mask, rms = low_mask(x, fs, args.win, args.thr)
    segs, nfill = signal_segments(mask, fs, args.min_gap)

    freqs = args.freqs if args.freqs else freqs_from_name(b)
    amp = args.amp if args.amp is not None else amp_from_name(b)

    print('=' * 96)
    print('源文件 : %s' % b)
    print('  有效行 %d / 总行 %d（坏行 %d）｜fs=%.1f Hz（标称）｜时长 %.2f s'
          % (len(V), nrow, nbad, fs, len(V) / fs))
    print('  滑窗 %.2f s，阈值 %.1f%%×峰RMS = %.1f code；填回短低区 **%d** 段（低频过零伪影）'
          % (args.win, args.thr * 100, args.thr * rms.max(), nfill))
    if freqs:
        print('  频点（源文件名）: %s ｜标称幅度: %s mVpp'
              % (' / '.join(fmt_f(f) for f in freqs), amp))
    print('  内缩 trim = %.2f s' % args.trim)
    print('-' * 96)
    print('  %-3s %9s %9s %8s %8s %10s %11s %12s' %
          ('#', 't0(s)', 't1(s)', 'dur(s)', '行数', '实测Hz', '峰(code)', '幅(mVpp)'))

    trim = int(round(args.trim * fs))
    out = []
    for i, (s, e) in enumerate(segs):
        dur = (e - s) / fs
        if dur < args.min_dur:
            print('  %-3d %9.3f %9.3f %8.3f  —— 太短（< %.2f s），跳过'
                  % (i + 1, s / fs, e / fs, dur, args.min_dur))
            continue
        s2 = s + trim
        e2 = e - trim
        if e2 - s2 < 200:                    # 内缩后太短 ⇒ 放弃内缩
            s2, e2 = s, e
        fmeas, amp_code = tone(x[s2:e2], fs)
        mvpp = amp_code * 2.0 * 0.1122 / 1000.0     # 0.1122 µV/code
        fname = freqs[i] if (freqs and i < len(freqs)) else fmeas
        print('  %-3d %9.3f %9.3f %8.3f %8d %10.3f %11.1f %12.3f%s'
              % (i + 1, s2 / fs, e2 / fs, (e2 - s2) / fs, e2 - s2, fmeas, amp_code, mvpp,
                 '' if not freqs else '   (标称 %s Hz)' % fmt_f(freqs[i])))
        out.append({'i': i + 1, 's': s2, 'e': e2, 'dur': (e2 - s2) / fs,
                    'f': fname, 'fmeas': fmeas, 'mvpp': mvpp, 'raw': raw})

    # ---- 写文件
    if args.dry_run:
        print('  （--dry-run：不写文件）')
        return out
    if not out:
        return out
    outdir = args.outdir or os.path.join(out_root, '%s_seg' % os.path.splitext(b)[0])
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    print('-' * 96)
    for seg in out:
        fn = 'fr-%sHz-%smVpp-%s-%s-s%d.txt' % (
            fmt_f(seg['f']),
            fmt_f(amp) if amp else 'NA',
            args.tag, stem_from_name(b), seg['i'])
        fp = os.path.join(outdir, fn)
        with io.open(fp, 'w', encoding='utf-8', newline='\n') as fh:
            for ln in seg['raw'][seg['s']:seg['e']]:
                fh.write(ln + '\n')
        print('  → %s  (%d 行 / %.3f s)' % (fp, seg['e'] - seg['s'], seg['dur']))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description='把多频点串接的抓数文件按静默带切成单频点文件')
    ap.add_argument('src', nargs='+', help='源 txt（可多个）')
    ap.add_argument('--outdir', default=None, help='输出目录（默认 <源名>_seg/）')
    ap.add_argument('--freqs', default=None, help='逗号分隔的频点，按时间顺序对应各段')
    ap.add_argument('--amp', type=float, default=None, help='标称幅度 mVpp')
    ap.add_argument('--fs', type=float, default=FS_DEF, help='显示域采样率（默认 500）')
    ap.add_argument('--win', type=float, default=0.1, help='滑窗秒数（默认 0.1）')
    ap.add_argument('--thr', type=float, default=0.15, help='阈值 = 该比例×峰RMS（默认 0.15）')
    ap.add_argument('--min-gap', type=float, default=2.0, help='短于该秒数的低区填回（默认 2.0）')
    ap.add_argument('--min-dur', type=float, default=1.5, help='短于该秒数的信号段丢弃（默认 1.5）')
    ap.add_argument('--trim', type=float, default=1.0, help='每段两端内缩秒数（默认 1.0）')
    ap.add_argument('--tag', default='sine', help='文件名里的波形标记（默认 sine）')
    ap.add_argument('--dry-run', action='store_true', help='只体检不写文件')
    a = ap.parse_args()
    if a.freqs:
        a.freqs = [float(t) for t in re.split(r'[,&]+', a.freqs) if t.strip()]
    tot = []
    for s in a.src:
        if not os.path.isfile(s):
            print('✗ 找不到：%s' % s)
            continue
        tot += run(s, a, os.path.dirname(os.path.abspath(s)))
    print('=' * 96)
    print('合计切出 %d 段' % len(tot))
    return 0


if __name__ == '__main__':
    sys.exit(main())
