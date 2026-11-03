#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断档 D1 / D2 系数生成器（2026-09-10）

用途
----
为 STM32L476 板上滤波链生成 **诊断档** 的 C 系数表：

    D1 = dc_block(fc=0.05Hz, 内部 double)          # 板上已有，不变
         -> notch 50Hz (Q=30)                      # 仅保留基波陷波
         -> butter(4, [0.05, 150]) bandpass sos    # 高端 40 -> 150Hz        [5 节]

    D2 = D1 + notch 100Hz (Q=30)                   # 加回 2 次谐波陷波         [6 节]

对比 v3（监护/ST 档）：
    v3 = dc_block(0.05) -> notch 50/100/150 -> butter(4, [0.05, 40])   # 7 节
    D1 = dc_block(0.05) -> notch 50         -> butter(4, [0.05, 150])  # 5 节
    D2 = dc_block(0.05) -> notch 50/100     -> butter(4, [0.05, 150])  # 6 节

为什么 D2（2026-09-10 D1 装板实测）
----------------------------------
D1 装板抓包 134928 后发现 P/T/基线出现"毛虫"纹波。控制变量分析（同一 raw 分别
重放 v3/D1 链）定位到毛虫主源 = **100Hz 工频 2 次谐波 9.0µV rms**（D1 去掉 100Hz
notch 后从 -0.10dB 的通带完整漏入），宽带 40-150Hz 扩展为次要贡献：
    TP 段 σ: v3 11.8µV -> D1 15.4µV（+31%），√(11.8²+9²)=14.8 ≈ 15.4 ✓
D2 加回 100Hz notch 可把 TP σ 压回 ~12.5µV（v3 水平），而 R-S 保持 951µV 不变
（notch 只挖 100Hz 窄谷，不碰 QRS 主频段）。150Hz notch 收益仅 1.3µV，可不加。

为什么高端要放到 150Hz（2026-09-09 离线验证结论）
--------------------------------------------------
123316 数据（AECG100 60bpm 1mV）27 拍模板分析显示：
  * R-S 幅度压缩 -33% 的**主因是 40Hz 低通 4 阶把 S 谷磨浅**，不是 0.05Hz 低端；
  * 高端 40 -> 150Hz 后 R-S 996 -> 978 uV（-2%，恢复 98%）、R -8%、T -18%（优于 v3 的 -43%）；
  * ST 段只取决于低端，高端改动几乎无影响；
  * 100Hz 陷波对形态贡献 <5uV，诊断档可去掉（D1 vs D2 差异可忽略）。
硬件 (4,5,6) 抽取率 BW=175Hz 已覆盖诊断 150Hz，无需改 R1/R2/R3。

运行
----
  C:\\Users\\luom\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe gen_diag_coeffs.py
"""

import numpy as np
from scipy import signal

FS = 1e6 / 1172.0          # 853.2423208191125 Hz（ADS1293 DRDY 实测周期 1172us）
F_LOW = 0.05
F_HIGH_D1 = 150.0
F_HIGH_V3 = 40.0
NOTCH_Q = 30.0


def fmt_f32(x):
    """按 9 位有效数字输出 float32 字面量（与现有系数表风格一致）。"""
    v = np.float32(x)
    s = f"{float(v):.9g}"
    if "e" in s or "E" in s:
        s = f"{float(v):.9g}"
    return s + "f"


def fmt_f64(x):
    return f"{float(np.float64(x)):.17g}"


def notch_sos(hz, q=NOTCH_Q):
    b, a = signal.iirnotch(hz, q, fs=FS)
    return signal.tf2sos(b, a)


def build_bp(f_high):
    return signal.butter(4, [F_LOW, f_high], btype="bandpass", fs=FS, output="sos")


def build_d1():
    """D1: notch 50Hz only + butter(4, [0.05, 150]) -> 5 节 sos。"""
    sos50 = notch_sos(50.0)
    sos_bp = build_bp(F_HIGH_D1)
    return np.vstack([sos50, sos_bp]), sos50, sos_bp


def build_d2():
    """D2: notch 50 + 100Hz + butter(4, [0.05, 150]) -> 6 节 sos（= D1 + 100Hz 陷波）。"""
    return np.vstack([notch_sos(50.0), notch_sos(100.0), build_bp(F_HIGH_D1)])


def build_v3():
    """v3: notch 50/100/150 + butter(4, [0.05, 40]) -> 7 节（用于对照校验）。"""
    secs = []
    for hz in (50.0, 100.0, 150.0):
        b, a = signal.iirnotch(hz, NOTCH_Q, fs=FS)
        secs.append(signal.tf2sos(b, a)[0])
    sos_bp = signal.butter(4, [F_LOW, F_HIGH_V3], btype="bandpass", fs=FS, output="sos")
    return np.vstack([np.asarray(secs), sos_bp])


def report_response(sos, name):
    w, h = signal.sosfreqz(sos, worN=200000, fs=FS)
    mag_db = 20 * np.log10(np.abs(h) + 1e-300)
    print(f"\n[{name}] 节数 = {len(sos)}")
    # 通带参考：1Hz 处增益（带通中心附近平坦区）
    ref = mag_db[np.argmin(np.abs(w - 1.0))]
    print(f"  1Hz 参考增益 = {ref:+.4f} dB")
    rel = mag_db - ref
    # -3dB 点（相对 1Hz 参考）
    #   低端：1Hz 以下最后一个掉出 -3dB 的点；
    #   高端：**不能用"第一个 <=-3dB 的点"**——50Hz 陷波谷(48.3~51.7Hz)会假报 49Hz，
    #         改为"通带内(rel>-3dB)最高频点之后，第一个掉出 -3dB 的频率"。
    idx = np.where((w >= 0.001) & (w <= 1.0))[0]
    cross = idx[np.where(rel[idx] <= -3.0)[0]]
    if len(cross):
        print(f"  -3dB 低端 = {w[cross[-1]]:.4f} Hz")
    pk = np.where((w >= 1.0) & (rel > -3.0))[0]
    if len(pk):
        k = pk[-1]
        nxt = np.where((w > w[k]) & (rel <= -3.0))[0]
        print(f"  -3dB 高端 = {w[nxt[0]] if len(nxt) else w[k]:.4f} Hz")
    # 关键点增益
    for f0 in (0.05, 0.5, 1.0, 40.0, 50.0, 100.0, 150.0, 175.0):
        if f0 < FS / 2:
            i = np.argmin(np.abs(w - f0))
            print(f"    {f0:7.2f} Hz : {rel[i]:+8.2f} dB")


def tags_for(sos, name):
    """给每一节起个注释 tag：陷波节按中心频率、带通节按序号。

    陷波节判别：iirnotch 的 tf2sos 输出满足 b0==b2、b1==-a1（对称零点在单位圆上），
    且 b0 明显大于带通节（带通的 b0 是 1e-2 量级）。"""
    tags, k = [], 0
    for s in sos:
        # scipy sos 是 6 列 [b0,b1,b2,a0,a1,a2]（a0==1）。iirnotch 的 tf2sos 输出
        # 满足 b0==b2 且 b1==a1（零点/极点同角频率）；带通节的 b1 != a1。
        is_notch = (abs(s[0] - s[2]) < 1e-12) and (abs(s[1] - s[4]) < 1e-9) and s[0] > 0.5
        if is_notch:
            # 陷波中心频率由**零点**决定：b1/b0 = -2cos(w0) → w0 = arccos(-b1/(2*b0))
            f0 = np.arccos(-s[1] / (2.0 * s[0])) / (2 * np.pi) * FS
            tags.append(f"{f0:.0f} Hz notch")
        else:
            tags.append(f"BP {name} sec{k} (0.05-150)")
            k += 1
    return tags


def print_c_table(sos, name):
    tags = tags_for(sos, name)
    print(f"\n/* --- {name}: float32 版（{len(sos)} 节）--- */")
    for s, tag in zip(sos, tags):
        print(f"    {{ {fmt_f32(s[0])}, {fmt_f32(s[1])}, {fmt_f32(s[2])}, "
              f"{fmt_f32(s[4])}, {fmt_f32(s[5])} }},  /* {tag} */")
    print(f"\n/* --- {name}: float64 版（{len(sos)} 节）--- */")
    for s, tag in zip(sos, tags):
        print(f"    {{ {fmt_f64(s[0])}, {fmt_f64(s[1])}, {fmt_f64(s[2])},")
        print(f"      {fmt_f64(s[4])}, {fmt_f64(s[5])} }},  /* {tag} */")


def f32_error(sos, f_lo=0.05, f_hi=150.0):
    sos_f32 = sos.astype(np.float32).astype(np.float64)
    w, h64 = signal.sosfreqz(sos, worN=8192, fs=FS)
    _, h32 = signal.sosfreqz(sos_f32, worN=8192, fs=FS)
    band = (w >= f_lo) & (w <= f_hi)
    d = (20 * np.log10(np.abs(h32[band]) + 1e-300)
         - 20 * np.log10(np.abs(h64[band]) + 1e-300))
    return float(np.max(np.abs(d)))


def main():
    print("=" * 78)
    print("诊断档 D1/D2 系数生成  fs = %.6f Hz" % FS)
    print("=" * 78)

    sos_d1, sos50, sos_bp_d1 = build_d1()
    sos_d2 = build_d2()
    sos_v3 = build_v3()

    report_response(sos_d1, "D1  diag 0.05-150Hz + notch50")
    report_response(sos_d2, "D2  diag 0.05-150Hz + notch50/100")
    report_response(sos_v3, "v3  ST/monitor 0.05-40Hz + notch50/100/150")

    print("\n" + "=" * 78)
    print("C 系数表")
    print("=" * 78)
    print_c_table(sos_d1, "D1")
    print_c_table(sos_d2, "D2")

    # ---- 保存 ----
    np.savez("diag_d1_coeffs.npz",
             sos_d1=sos_d1, sos_d2=sos_d2, sos_v3=sos_v3, fs=FS,
             f_low=F_LOW, f_high=F_HIGH_D1)
    print("\n已保存 diag_d1_coeffs.npz")

    # ---- f32 量化误差检查 ----
    print(f"D1 f32 通带内相对 f64 最大偏差 = {f32_error(sos_d1):.4f} dB")
    print(f"D2 f32 通带内相对 f64 最大偏差 = {f32_error(sos_d2):.4f} dB")


if __name__ == "__main__":
    main()
