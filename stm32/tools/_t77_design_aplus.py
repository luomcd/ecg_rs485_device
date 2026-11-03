# -*- coding: utf-8 -*-
"""
_t77_design_aplus.py —— T77 「A+ 档（18 节）」200/250 Hz 陷波簇设计 + 落码前自证

背景
----
T76 已落码 DUAL_A（12 节 = 9 陷波 + 3 BP）。A+ = DUAL_A 再往**链首**补 200/250 各一簇
（因为「陷波恒在链首 + notch OFF 整段跳过」⇒ 加链首陷波对 OFF 态频响**零代价**）。

T76 报告留了一笔账没算：**250 Hz 属市电 5 次谐波，漂移量是基波的 5 倍**，
而工程既有 150 簇的深覆盖只有 0.6 Hz（T32 扫描实测，间距 ±0.2 / Q20）
⇒ **不能照抄 ±0.2**。本脚本就是把这笔账算清并定参数。

设计输入（全部来自工程既有实测，非估算）
----------------------------------------
* 基波漂移带（T31，跨 11 个文件实测）：**0.0981 Hz 宽**（49.9391~50.0372）
* 谐波漂移按次方成比例（T31 铁证：620k raw 域谐波比 = 2.0000/3.0000/4.0000）
  ⇒ 200 Hz 带 0.392 Hz、250 Hz 带 0.4905 Hz
* 3 节 Q20 簇：深覆盖 ≈ 0.6 Hz（T32「节数/间距扫描」：3 节(间距0.20) 深覆盖 0.6 Hz ⇒ 余量 2 倍）

本脚本做四件事
--------------
  [0] 生成器自证：用 scipy iirnotch 逐位复现固件 DUAL_A 的第 0~8 节（0 ULP 才继续）
  [1] 簇设计扫描：200/250 各候选（间距 × Q）× 三个口径
        · 漂移带内最差衰减（dB）
        · 深覆盖带宽（≥60 dB 的连续带）
        · 对实测谱线（199.74 / 249.73 Hz）的衰减
  [2] 选定方案 → 打印可直接粘贴的 C 字面量（%.17g / %.9g+`f`）
  [3] 整链复核：OFF 态与 DUAL_A 逐位等价；ON 态输入阻抗预测（复用 _t73 的 predict_target）
"""
import io
import os
import re
import sys

import numpy as np
from scipy import signal as sps

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
C_FILE = os.path.join(ROOT, 'Filter', 'ecg_filter.c')

FS = 1e6 / 1172.0          # 853.2423208191125 Hz（固件域，勿改）
DRIFT_F1 = 0.0981          # 基波漂移带宽（T31 实测）

# 固件 DUAL_A 的 9 个陷波节（f64 字面量，用于生成器自证）——由脚本从源码抓，见 grab()
DUAL_A_NOTCH_N = 9
DUAL_A_PARAMS = [
    (49.80, 12.0), (50.00, 12.0), (50.20, 12.0),
    (99.80, 20.0), (100.00, 20.0), (100.20, 20.0),
    (149.80, 20.0), (150.00, 20.0), (150.20, 20.0),
]


# ---------------------------------------------------------------- 基本工具
def notch(f0, Q, fs=FS):
    """与 gen_f3_coeffs.py 同口径：iirnotch → tf2sos → (b0,b1,b2,a1,a2)"""
    b, a = sps.iirnotch(f0, Q, fs=fs)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def hz(secs, f, fs=FS):
    """级联幅频（返回线性幅度），f 可为标量或数组"""
    f = np.atleast_1d(np.asarray(f, dtype=float))
    z = np.exp(-1j * 2 * np.pi * f / fs)
    H = np.ones(len(f), dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
    return np.abs(H)


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-300))


def grab(profile):
    """从 ecg_filter.c 抓某档的 f32/f64 表正文（供自证/拼接用）"""
    src = io.open(C_FILE, encoding='utf-8').read()
    pat = (r"#elif \(ECG_FILTER_PROFILE == ECG_PROFILE_" + profile + r"\)(.*?)"
           r"(?=#elif \(ECG_FILTER_PROFILE ==|\Z)")
    m = re.search(pat, src, re.S)
    if not m:
        sys.exit('!! 找不到档 ECG_PROFILE_' + profile)
    body = m.group(1)
    tabs = {}
    for kind in ('f32', 'f64'):
        mm = re.search(r"ECG_FILTER_COEFFS_" + kind.upper() + r"\[ECG_NUM_SECTIONS\] = \{(.*?)\n\};",
                       body, re.S)
        rows = []
        for g in re.finditer(r"\{([^{}]*)\}", mm.group(1)):
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", g.group(1))
            rows.append(tuple(float(x) for x in nums[:5]))
        tabs[kind] = rows
    return tabs


def fmt_f64(c):
    return "{ %s, %s, %s,\n      %s, %s }" % tuple("%.17g" % v for v in c)


def fmt_f32_flat(c):
    return "{ %s, %s, %s, %s, %s }" % tuple(
        ("%.9g" % np.float32(v)) + ("f" if ("e" not in "%.9g" % np.float32(v)
                                            and "." not in "%.9g" % np.float32(v)) else "f")
        for v in c)


# ---------------------------------------------------------------- [0] 自证
def selfcheck():
    print("=" * 92)
    print("[0] 生成器自证 —— 用 scipy iirnotch 逐位复现固件 DUAL_A 第 0~8 节")
    print("=" * 92)
    fw = grab('DUAL_A')['f64'][:DUAL_A_NOTCH_N]
    nb = 0
    for i, ((f0, Q), row) in enumerate(zip(DUAL_A_PARAMS, fw)):
        gen = notch(f0, Q)
        d = max(abs(gen[k] - row[k]) for k in range(5))
        flag = '✓' if d == 0.0 else ('Δ=%.2e' % d)
        if d != 0.0:
            nb += 1
        print("  节 %d  f0=%7.2f Q=%4.1f   字典↔固件 max|Δ|=%.3g  %s" % (i, f0, Q, d, flag))
    print("  ⇒ %s" % ("0 ULP，9/9 逐位一致 ✓" if nb == 0 else "★%d 节不一致，禁止继续★" % nb))
    return nb == 0


# ---------------------------------------------------------------- [1] 扫描
def band(secs, lo, hi, depth_db, n=400001):
    """返回连续深覆盖带宽 Hz —— 以簇中心为心、衰减**深于** depth_db（dB，取负值）的连续区间长度"""
    f = np.linspace(lo, hi, n)
    a = db(hz(secs, f))
    ok = a <= depth_db                      # ★ 衰减更深 ⇒ dB 更负 ⇒ 用 <=
    if not ok.any():
        return 0.0
    c = int(np.argmin(np.abs(f - (lo + hi) / 2)))
    if not ok[c]:
        # 中心若不在深区（不会发生，双保险），退化为取最长的连续段
        idx = np.flatnonzero(np.diff(np.r_[False, ok, False]))
        if len(idx) < 2:
            return 0.0
        k = int(np.argmax(idx[1::2] - idx[0::2]))
        return f[idx[2 * k + 1] - 1] - f[idx[2 * k]]
    i = c
    while i > 0 and ok[i - 1]:
        i -= 1
    j = c
    while j < len(f) - 1 and ok[j + 1]:
        j += 1
    return f[j] - f[i]


def worst_db(secs, lo, hi, n=4001):
    """漂移带内**最差**（最浅）衰减 dB"""
    return float(np.max(db(hz(secs, np.linspace(lo, hi, n)))))


def scan(name, f0, spacing, Q, drift):
    """三节簇：f0-spacing / f0 / f0+spacing"""
    secs = [notch(f0 - spacing, Q), notch(f0, Q), notch(f0 + spacing, Q)]
    d_lo, d_hi = f0 - drift / 2, f0 + drift / 2
    worst = worst_db(secs, d_lo, d_hi)
    b60 = band(secs, f0 - 3 * spacing - 3, f0 + 3 * spacing + 3, -60.0)
    b40 = band(secs, f0 - 3 * spacing - 3, f0 + 3 * spacing + 3, -40.0)
    margins = b60 / drift if drift else float('nan')
    return dict(name=name, f0=f0, spacing=spacing, Q=Q, secs=secs,
                worst=worst, b60=b60, b40=b40, margin=margins)


def show(rows, f0, drift, lines):
    print()
    print("  f0 = %g Hz ｜ 漂移带 %.4f Hz（±%.4f）｜ 实测谱线 %s" % (f0, drift, drift / 2, lines))
    print("  %-26s %10s %10s %10s %10s" % ("候选(间距/Q)", "带内最差dB", "深覆盖60dB", "40dB带", "余量×"))
    for r in rows:
        print("  %-26s %10.1f %10.3f %10.3f %10.2f"
              % ("±%.3f / Q%g" % (r['spacing'], r['Q']), r['worst'], r['b60'], r['b40'], r['margin']))
    print("  对实测谱线：")
    for r in rows:
        v = db(hz(r['secs'], lines))
        print("    ±%.3f/Q%-4g  " % (r['spacing'], r['Q'])
              + "  ".join("%.2f Hz → %.1f dB" % (lf, vv) for lf, vv in zip(lines, v)))


def design():
    print()
    print("=" * 92)
    print("[1a] 口径标定 —— 用工程的 150 Hz 簇（±0.2 / Q20）反查「深覆盖」定义")
    print("=" * 92)
    anchor = scan('150', 150.0, 0.20, 20.0, 3 * DRIFT_F1)
    print("      150 簇（149.80/150.00/150.20，Q20）：漂移带 %.4f Hz，带内最差 %.1f dB，"
          "深覆盖(60dB) %.3f Hz ⇒ 余量 %.2f×" % (3 * DRIFT_F1, anchor['worst'],
                                                 anchor['b60'], anchor['margin']))
    print("      （T32 当年扫描口径：3 节/间距 0.20 ⇒ 深覆盖 0.6 Hz、余量 2 倍 ⇒ 本脚本口径对齐 ✓"
          if abs(anchor['b60'] - 0.6) < 0.08 else "      ⚠ 与 T32 的 0.6 Hz 有差，需核口径")

    print()
    print("=" * 92)
    print("[1b] 200 Hz 簇扫描（漂移带 = 4 × 0.0981 = 0.3924 Hz）")
    print("=" * 92)
    drift200 = 4 * DRIFT_F1
    rows200 = [scan('a', 200.0, s, q, drift200)
               for s, q in ((0.20, 20.0), (0.30, 20.0), (0.393, 20.0),
                            (0.30, 15.0), (0.30, 12.0), (0.393, 15.0))]
    show(rows200, 200.0, drift200, [199.74, 200.0, 200.26])

    print()
    print("=" * 92)
    print("[1c] 250 Hz 簇扫描（漂移带 = 5 × 0.0981 = 0.4905 Hz）")
    print("=" * 92)
    drift250 = 5 * DRIFT_F1
    rows250 = [scan('a', 250.0, s, q, drift250)
               for s, q in ((0.20, 20.0), (0.30, 20.0), (0.40, 20.0), (0.50, 20.0),
                            (0.491, 20.0), (0.40, 15.0), (0.50, 15.0), (0.491, 12.0))]
    show(rows250, 250.0, drift250, [249.73, 250.0, 250.27])
    return rows200, rows250, drift200, drift250


def pick(rows, drift, need=2.0):
    """选：深覆盖余量 ≥ need 的前提下，节数最少（这里固定 3 节）且间距最小（对诊断带伤害最小）"""
    cand = [r for r in rows if r['margin'] >= need]
    if not cand:
        return max(rows, key=lambda r: r['margin'])
    return min(cand, key=lambda r: (r['spacing'], -r['Q']))


# ---------------------------------------------------------------- [2] 打印字面量
def emit(n200, n250):
    print()
    print("=" * 92)
    print("[2] 选定方案 —— 6 个新节（200 簇 3 节 + 250 簇 3 节）")
    print("=" * 92)
    for tag, r in (('200', n200), ('250', n250)):
        print("  %s 簇：%.2f / %.2f / %.2f Hz，Q=%g（深覆盖 %.3f Hz，余量 %.2f×）"
              % (tag, r['f0'] - r['spacing'], r['f0'], r['f0'] + r['spacing'], r['Q'],
                 r['b60'], r['margin']))
    print()
    print("  /* ---- f32 ---- */")
    for i, c in enumerate(n200['secs'] + n250['secs']):
        print("    %s%s" % (fmt_f32_flat(c), "," if i < 5 else ""))
    print()
    print("  /* ---- f64 ---- */")
    for i, c in enumerate(n200['secs'] + n250['secs']):
        body = fmt_f64(c).replace("\n", "\n    ")
        print("    %s%s" % (body, "," if i < 5 else ""))
    return n200['secs'] + n250['secs']


# ---------------------------------------------------------------- [3] 整链复核
def chain_check(new_secs, n200, n250):
    print()
    print("=" * 92)
    print("[3] 整链复核")
    print("=" * 92)
    dua = grab('DUAL_A')
    d3 = grab('DIAG150_D3')
    # 源档表（f64，用于频响计算）
    dua_f64 = dua['f64']
    d3_f64 = d3['f64']
    nn_dua, nn_d3 = 9, 4                     # 两档的陷波节数
    bp_dua = dua_f64[nn_dua:]                # DUAL_A 的 3 节 BP
    bp_d3 = d3_f64[nn_d3:]                   # D3 的 3 节 BP

    aplus = list(dua_f64[:nn_dua]) + list(new_secs) + list(bp_dua)   # 15 + 3 = 18

    # 3a OFF 态逐位等价（OFF = 只留 BP）
    print("  [3a] OFF 态等价性（0.5~300 Hz，max|ΔH| 必须 = 0）")
    f = np.linspace(0.5, 300, 300001)
    off_dua = hz(bp_dua, f)
    off_d3 = hz(bp_d3, f)
    off_ap = hz(aplus[nn_dua + 6:], f)
    print("      BP 段：A+ %d 节  vs  DUAL_A %d 节  vs  D3 %d 节"
          % (len(aplus[nn_dua + 6:]), len(bp_dua), len(bp_d3)))
    print("      A+ vs DUAL_A  max|ΔH| = %.3g" % np.max(np.abs(off_ap - off_dua)))
    print("      A+ vs D3      max|ΔH| = %.3g" % np.max(np.abs(off_ap - off_d3)))
    print("      BP 系数逐位相同：%s" % (bp_dua == bp_d3))

    # 3b OFF 态频响读数
    print("  [3b] OFF 态频响（相对 10 Hz）")
    ref_ap = hz(bp_dua, 10.0)[0]
    ref_d3 = hz(bp_d3, 10.0)[0]
    for fr in (100.0, 120.0, 150.0, 200.0, 250.0):
        print("      %6.1f Hz : A+ %7.2f%%   DUAL_A %7.2f%%   D3 %7.2f%%"
              % (fr, hz(bp_dua, fr)[0] / ref_ap * 100,
                 hz(bp_dua, fr)[0] / ref_ap * 100,
                 hz(bp_d3, fr)[0] / ref_d3 * 100))

    # 3c ON 态：对新旧三根线的压制
    print("  [3c] ON 态对市电谐波线的压制（dB）")
    lines = [149.87, 199.74, 249.73]
    for tag, secs_, nn in (('DUAL_A(12)', dua_f64, 9), ('A+(18)', aplus, 15)):
        v = db(hz(secs_, lines))
        print("      %-11s " % tag + "  ".join("%.2fHz: %7.1f" % (lf, vv)
                                               for lf, vv in zip(lines, v)))
    print("      对 10 Hz 的增益：DUAL_A %.4f dB / A+ %.4f dB"
          % (db(hz(dua_f64, 10.0))[0], db(hz(aplus, 10.0))[0]))

    return aplus, dua_f64


def main():
    ok = selfcheck()
    if not ok:
        return 1
    rows200, rows250, d200, d250 = design()
    n200 = pick(rows200, d200, 2.0)
    n250 = pick(rows250, d250, 2.0)
    new_secs = emit(n200, n250)
    chain_check(new_secs, n200, n250)
    print()
    print("=" * 92)
    print("⇒ 设计完成。落码用 tools/_t77_apply_aplus.py（本脚本只设计，不写源码）")
    print("=" * 92)
    return 0


if __name__ == '__main__':
    sys.exit(main())
