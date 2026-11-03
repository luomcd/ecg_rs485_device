# -*- coding: utf-8 -*-
"""
_t58_gen_bp250.py —— 生成 BP 上角 150→250 Hz + LP 4 阶→2 阶 的系数（方案 B）

改动语义（五档 D1/D2/D3/IMP620/IMP620_N2 共用）
------------------------------------------------
  旧 BP = butter(4, [0.05, 150], 'bandpass')  = [LP4 sec0, LP4 sec1, HP4 sec2, HP4 sec3]  4 节
  新 BP = HP4@0.05 的 2 节（**原样保留固件字面量，不重生成**）
        + LP2@250   的 1 节（**本次新增**）                                               3 节
  ⇒ 各档 ECM_NUM_SECTIONS 减 1；ST40（上角 40 Hz）不动。

纪律
----
1) **自证**：先从 butter(4,[0.05,150],'bandpass') 重算 4 节，与固件 `ecg_filter.c` 的
   sec0/sec1/sec2/sec3 字面量**逐位比对**（0 ULP 才算通过）——只有口径一致，
   才有资格用同一口径生成新节。
2) f64 字面量 = %.17g；f32 = %.9g + "f"（与 gen_d3_coeffs.py 同口径）。
3) 输出可直接粘贴的 C 代码块。
"""
import io
import os

import numpy as np
from scipy import signal as ss

FS = 1e6 / 1172.0                     # 853.2423208191125
OUT = []


def say(s=''):
    OUT.append(s)
    print(s)


def g17(v):
    return "%.17g" % v


def g9f(v):
    return ("%.9gf" % np.float32(v))


# ---- 固件现有字面量（D1/D2/D3/IMP620/IMP620_N2 五档的 BP 4 节完全相同）----
FW = [
    ((0.030899520236696225, 0.06179904047339245, 0.030899520236696225,
      -0.49305028725906408, 0.095879018845577341), "sec0 LP4@150"),
    ((1.0, 2.0, 1.0, -0.67018117413271905, 0.49069657728421018), "sec1 LP4@150"),
    ((1.0, -2.0, 1.0, -1.9993194733745707, 0.99931960903409622), "sec2 HP4@0.05"),
    ((1.0, -2.0, 1.0, -1.9997182191996452, 0.99971835477155957), "sec3 HP4@0.05"),
]


def main():
    say("=" * 78)
    say("[0] 自证：butter(4,[0.05,150],'bandpass','sos') 逐位复现固件 BP 4 节")
    say("=" * 78)
    sos_old = ss.butter(4, [0.05, 150.0], btype='bandpass', fs=FS, output='sos')
    allok = True
    for i, (row, (fwl, name)) in enumerate(zip(sos_old, FW)):
        # ⚠ scipy sos 是 6 列 [b0,b1,b2,a0,a1,a2]（a0 恒 1）；固件是 5 元组 (b0,b1,b2,a1,a2)
        row5 = (float(row[0]), float(row[1]), float(row[2]),
                float(row[4]), float(row[5]))
        diffs = [abs(row5[j] - fwl[j]) for j in range(5)]
        ulp0 = all(d == 0.0 for d in diffs)
        allok &= ulp0
        say("  %s  %s   max|Δ| = %.3g" % (name, "逐位相同 ✓" if ulp0 else "有差异 ✗",
                                          max(diffs)))
        say("      重算: " + " ".join("%.17g" % v for v in row5))
        say("      固件: " + " ".join("%.17g" % v for v in fwl))
    say("")
    say("  ⇒ 自证 %s" % ("通过 ✓（可放心用同一口径生成新节）" if allok else "失败 ✗ 停下"))

    say("")
    say("=" * 78)
    say("[1] 新节：LP2@250（butter(2, 250, 'low', fs=853.2423)）")
    say("=" * 78)
    lp_new = ss.butter(2, 250.0, btype='low', fs=FS, output='sos')
    say("  节数 = %d" % len(lp_new))
    r = lp_new[0]
    r5 = (float(r[0]), float(r[1]), float(r[2]), float(r[4]), float(r[5]))
    say("  f64: %.17g, %.17g, %.17g, %.17g, %.17g" % r5)
    say("  f32: %s, %s, %s, %s, %s" % tuple(g9f(v) for v in r5))
    # f32 量化误差
    say("  f32 量化 |Δ| max = %.3g（相对系数）" % max(
        abs(float(np.float32(r5[j])) - r5[j]) for j in range(5)))

    say("")
    say("=" * 78)
    say("[2] 校验：新 BP（HP4@0.05 + LP2@250）的频响与过冲（对照旧 BP）")
    say("=" * 78)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _t41_freqresp as T41
    hp = ss.butter(4, 0.05, btype='high', fs=FS, output='sos')

    def hbp(f, sos):
        w = 2.0 * np.pi * np.asarray(f, float) / FS
        h = np.ones(len(w), complex)
        for row in sos:
            # ⚠ scipy sos 行 = [b0,b1,b2,a0,a1,a2] ⇒ a = row[3:6]
            _, hh = ss.freqz(row[:3], row[3:6], worN=w)
            h *= hh
        return np.abs(h) * T41.h_afe(np.asarray(f, float))

    for lab, sos_ in (("旧 BP 4 节", sos_old),
                      ("新 BP 3 节", np.vstack([hp, lp_new]))):
        ref = float(hbp([10.0], sos_)[0])
        v150 = float(hbp([150.0], sos_)[0]) / ref * 100.0
        say("  %-12s 150 Hz = %6.2f%%  （余量 vs 70%% = %+.2f pp）"
            % (lab, v150, v150 - 70.0))

    say("")
    say("=" * 78)
    say("[3] 待粘贴的 C 代码（新 LP2@250 节）")
    say("=" * 78)
    say("  f64 行:")
    say("    { %.17g, %.17g, %.17g," % (r5[0], r5[1], r5[2]))
    say("      %.17g, %.17g },  /* LP2@250 */" % (r5[3], r5[4]))
    say("  f32 行:")
    say("    { %s, %s, %s, %s, %s },  /* LP2@250 */"
        % tuple(g9f(v) for v in r5))

    say("")
    with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '_t58_gen_out.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
    say("（已写 _t58_gen_out.txt）")


if __name__ == '__main__':
    main()
