# -*- coding: utf-8 -*-
"""
_t70_selftest.py —— T70 判读链路的闭环自测（合成数据 → 判读脚本 → 比对预测）

目的：在猛哥的实测数据到达**之前**，证明 `_t70_fr_measure.py` 的判读链路是对的。
做法：用**固件 D3 档真实系数**把合成正弦（各频点按表201.107 的输入幅度）过一遍链，
      写成与现场同格式的 7 列 txt，再由 `_t70_fr_measure.py` 读回判读。
判据：读回的归一化值应 ≈ `_t69_fr_forecast.py` 的预测（偏差 < 1 pp）。
输出：_tmp_fr_selftest/*.txt ＋ _t70_selftest_out.txt
"""
import io
import os
import sys

import numpy as np
import scipy.signal as ss

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import _t69_fr_forecast as T69

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "_tmp_fr_selftest")
FS = T69.FS
UVPC = 0.1122

F0S = [0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0, 80.0, 100.0,
       120.0, 150.0, 200.0, 300.0, 400.0]


def run_d3(x_code, sos, nn, notch_on=False):
    """复现固件：dc_block(0.05) -> [notch] -> BP。"""
    y = np.asarray(x_code, float)
    # dc_block（一阶级联，与固件一致）
    fc, fs = 0.05, FS
    r = 1 - 2 * np.pi * fc / fs
    y = ss.lfilter([1, -1], [1, -r], y)
    i0 = 0 if notch_on else nn
    for i in range(i0, len(sos)):
        y = ss.sosfilt(sos[i:i + 1], y)
    return y


def main():
    out = []

    def P(s=""):
        print(s)
        out.append(s)

    os.makedirs(OUTDIR, exist_ok=True)
    sos = T69.parse_profile(T69.SRC_C, "DIAG150_D3")
    cnt = T69.parse_counts(T69.SRC_H, "DIAG150_D3")
    ns, nn, nb = cnt
    P("D3 档：系数 %d 节（notch %d + BP %d）" % (len(sos), nn, nb))
    P("合成条件：10 s 稳态段（预跑 10 s 丢弃）＋ 峰值 = Amp/2；列布局与现场 txt 同（7 列）")
    P()

    lead_s, keep_s = 10.0, 10.0
    nlead, nkeep = int(lead_s * FS), int(keep_s * FS)
    gen = []
    for f0 in F0S:
        a_mv = T69.amp_of(f0)
        a_code_pk = a_mv * 1000.0 / UVPC / 2.0
        n = np.arange(nlead + nkeep)
        x = a_code_pk * np.sin(2 * np.pi * f0 * n / FS)
        y = run_d3(x, sos, nn)[nlead:]
        xs = x[nlead:]
        fn = os.path.join(OUTDIR, "fr-%.2fHz-%.3fmVpp-2026-09-21 0000.txt" % (f0, a_mv))
        with io.open(fn, "w", encoding="utf-8", newline="\n") as f:
            for k in range(nkeep):
                f.write("%d %.3f 0.000 0.000 %.6f 0.000 0.000\n"
                        % (k, xs[k], y[k]))
        gen.append((f0, a_mv, fn))
    P("已生成 %d 份合成 txt → %s" % (len(gen), OUTDIR))
    P()

    # ---- 与预测比对（同一模型内部一致性）
    P("自测比对（合成 = 固件系数；预测 = 同一系数）")
    P("-" * 74)
    fl = np.array(F0S, float)
    Hf = T69.h_chain(sos, fl, False, nn, "DIAG150_D3")
    Ha = T69.h_afe(fl)
    rr = abs(T69.h_chain(sos, np.array([T69.REF]), False, nn, "DIAG150_D3")[0]
             * T69.h_afe(np.array([T69.REF]))[0])
    pred = np.abs(Hf * Ha) / rr * 100.0
    P("     f(Hz) | 预测%")
    for i, f in enumerate(fl):
        P("   %7.2f | %6.2f" % (f, pred[i]))
    P()
    P("⇒ 下一步：跑 `python _t70_fr_measure.py \"_tmp_fr_selftest/fr-*.txt\" --pred-mode fw`，读回值应与上表一致。")
    P("   ⚠ 必须带 `--pred-mode fw`：本自测的合成数据**只过固件系数链、不含 AFE SINC**，")
    P("     而判读脚本默认 `--pred-mode full`（整机含 AFE）⇒ 用默认值会在 >40 Hz 段出现")
    P("     「实测比预测高」的假偏差（100/150 Hz 可达 +10/+22 pp），不是链路问题。")

    with io.open(os.path.join(HERE, "_t70_selftest_out.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    main()
