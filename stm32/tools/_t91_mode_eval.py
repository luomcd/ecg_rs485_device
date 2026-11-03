# -*- coding: utf-8 -*-
"""
_t91_mode_eval.py —— T91「监护/诊断运行期可选」最小改动方案评估

问题
----
猛哥：监护和诊断可选，在**现在的参数基础上**以**改动最小的代价**来做。

核心思路（本脚本要证明的三件事）
--------------------------------
A. **只换 1 节**：A+B 档 16 节里，第 15 节是 LP2@250（诊断上角）。
   监护档只需把它换成 **LP2@45**（1 节，同型同尺寸）⇒ 其余 15 节（15 陷波）
   与 dc_block@0.02 **逐位不动**。⇒ 表长不变、结构体尺寸不变、热路径循环不变。
B. **切换无长漂**：低端（dc@0.02）与陷波组都不动 ⇒ 不存在"重建 15~18 s"。
   只需对第 15 节做 DC 预置（复用现有 set_notch 同款公式）。
C. **判据 a 不受影响**：低端极点仍由 dc@0.02 单独决定（LP 在 0.1 Hz 增益 ≈ 1）。

输出
----
[0] 系数自证（butter(2,250) 逐位复现固件）＋ 新 LP2@45 系数
[1] 频响表（固件口径 + 整机口径，含 AFE 级联 CIC）
[2] 监护判据判定（0.67/40 Hz ∈ 5 Hz 的 71%~110%）
[3] 陷波节数敏感性（15 节 vs 9 节，≤40 Hz 有无差异）
[4] 切换瞬态（三种预置策略 vs 全链重置）
[5] 改动量清单

用法
----
  python _t91_mode_eval.py            # 控制台
  python _t91_mode_eval.py --fig      # 另出四面板图
"""
import io
import os
import sys
import argparse

import numpy as np
from scipy import signal as ss

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FS = 1e6 / 1172.0                     # 853.2423208191125（与 _t58_gen_bp250.py 同口径）
FSD = 102400.0                        # AFE SDM 时钟
R123 = (4, 5, 6)                      # ADS1293 三级 5 阶 SINC 抽取比
DC_FC_DIAG = 0.02                     # A+B 档 dc_block 截止
HW = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HW)
OUT = []


def p(s=""):
    OUT.append(s)
    print(s)


def g17(v):
    return "%.17g" % v


# ------------------------------------------------------------------ AFE 模型
def h_afe(f):
    """ADS1293 三级 5 阶级联 CIC（与 _t41/_t69 同口径）。
    ⚠ 每级输入率递减（102400 → 25600 → 5120），不是固定 102400。"""
    f = np.asarray(f, dtype=float)
    H = np.ones_like(f)
    fin = FSD
    for R in R123:
        x = np.pi * f / fin
        H *= np.abs(np.sin(R * x) / (R * np.sin(x))) ** 5
        fin /= R
    return H


def db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-300))


# ------------------------------------------------------------------ 固件系数
def read_fw_sos(prof, fname="Filter/ecg_filter.c"):
    """从 ecg_filter.c 的 #if 分支读取 ECG_FILTER_COEFFS_F64 字面量（5 元组）。"""
    import re
    s = io.open(os.path.join(ROOT, fname), encoding="utf-8").read()
    m = re.search(r"#(?:if|elif) \(ECG_FILTER_PROFILE == " + prof + r"\)(.*?)"
                  r"(?=\n#(?:elif|else))", s, re.S)
    body = m.group(1)
    m2 = re.search(r"ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{(.*?)\n\};", body, re.S)
    txt = re.sub(r"/\*.*?\*/", "", m2.group(1), flags=re.S)
    txt = re.sub(r"//[^\n]*", "", txt)
    rows = []
    for g in re.findall(r"\{([^{}]*)\}", txt):
        v = [float(x) for x in g.split(",") if x.strip()]
        if len(v) == 6:                       # scipy 风格（含 a0=1）→ 去掉 a0
            rows.append((v[0], v[1], v[2], v[4], v[5]))
        elif len(v) == 5:
            rows.append(tuple(v))
    return rows


# ------------------------------------------------------------------ 传函
def H_firmware(f, sos, dc_fc=DC_FC_DIAG, notch_n=None):
    """固件链：dc_block -> [sos 前 notch_n 节] -> [sos 其余]。
    notch_n=None ⇒ 全链；notch_n=0 ⇒ 跳过链首全部陷波（notch OFF）。"""
    f = np.asarray(f, dtype=float)
    w = np.exp(-2j * np.pi * f / FS)
    H = np.ones_like(w, dtype=complex)
    for (b0, b1, b2, a1, a2) in sos:
        H *= (b0 + b1 * w ** -1 + b2 * w ** -2) / (1 + a1 * w ** -1 + a2 * w ** -2)
    r = 1.0 - 2.0 * np.pi * dc_fc / FS
    H *= (1 - w ** -1) / (1 - r * w ** -1)
    return H


def H_sos_only(f, sos):
    f = np.asarray(f, dtype=float)
    w = np.exp(-2j * np.pi * f / FS)
    H = np.ones_like(w, dtype=complex)
    for (b0, b1, b2, a1, a2) in sos:
        H *= (b0 + b1 * w ** -1 + b2 * w ** -2) / (1 + a1 * w ** -1 + a2 * w ** -2)
    return H


def resp(f_list, sos, dc_fc, ref_f):
    """返回 (固件归一%, 整机归一%)。ref_f = 该口径的参考频率（诊断 10 Hz / 监护 5 Hz）。"""
    fl = np.asarray(f_list, dtype=float)
    hf = H_firmware(fl, sos, dc_fc)
    hr = H_firmware([ref_f], sos, dc_fc)[0]
    g = np.abs(hf) / abs(hr) * 100.0
    a = (np.abs(hf) * h_afe(fl)) / (abs(hr) * h_afe([ref_f])[0]) * 100.0
    return g, a


# ================================================================== [0]
def sec0():
    p("=" * 88)
    p("[0] 系数自证与设计")
    p("=" * 88)
    ab = read_fw_sos("ECG_PROFILE_DUAL_A_PLUS_B")
    st = read_fw_sos("ECG_PROFILE_ST40")
    p("  读得 A+B 档 %d 节；ST40 档 %d 节" % (len(ab), len(st)))

    lp250 = ss.butter(2, 250.0, btype="low", fs=FS, output="sos")[0]
    lp250_5 = (float(lp250[0]), float(lp250[1]), float(lp250[2]),
               float(lp250[4]), float(lp250[5]))
    p("")
    p("  --- 自证：butter(2,250,'low',fs=853.2423) vs 固件 A+B[15] ---")
    d = [abs(lp250_5[i] - ab[15][i]) for i in range(5)]
    p("     重算: " + " ".join(g17(v) for v in lp250_5))
    p("     固件: " + " ".join(g17(v) for v in ab[15]))
    p("     max|Δ| = %.3g  ⇒ %s" % (max(d), "逐位相同 ✓（口径确认）"
                                    if max(d) == 0 else "有差异 ✗"))

    # 候选监护 LP（同口径生成）
    cand = {}
    for fc in (42.0, 45.0, 48.0):
        r = ss.butter(2, fc, btype="low", fs=FS, output="sos")[0]
        cand[fc] = (float(r[0]), float(r[1]), float(r[2]), float(r[4]), float(r[5]))
    # 对照：ST40 的 LP4@40（两节）
    cand["LP4@40"] = (st[3], st[4])

    p("")
    p("  --- 候选监护上角 LP2（同口径生成；1 节，与 LP2@250 同型同尺寸）---")
    for fc in (42.0, 45.0, 48.0):
        p("     LP2@%g  f64: %s" % (fc, ", ".join(g17(v) for v in cand[fc])))
        p("              f32: %s" % ", ".join("%.9gf" % np.float32(v) for v in cand[fc]))
    p("     对照 ST40 的 LP4@40（2 节）: %d 节，需额外槽位 ⇒ 不属最小改动"
      % len(cand["LP4@40"]))
    return ab, cand


# ================================================================== [1]
def sec1(ab, cand):
    p("")
    p("=" * 88)
    p("[1] 频响表：诊断（现状 A+B）vs 监护（只换第 15 节）")
    p("    ★ 口径 = notch OFF（GB 9706.225 201.12.4.105.3 ／ 227 附录AA：频响试验必须关陷波）")
    p("=" * 88)
    pts = [0.1, 0.67, 5.0, 10.0, 40.0, 50.0, 100.0, 150.0]
    diag_off = ab[15:]                      # notch OFF ⇒ 只剩 LP2@250（1 节）

    gd, ad = resp(pts, diag_off, DC_FC_DIAG, 10.0)
    p("")
    p("  --- 诊断 A+B（现状；notch OFF；链 = dc@0.02 -> LP2@250，1 节；归一 10 Hz）---")
    for i, f in enumerate(pts):
        p("     %8.2f Hz : 固件 %7.2f%%   整机 %7.2f%%" % (f, gd[i], ad[i]))
    p("     ★ 自检：0.1 Hz 应 ≈98.1%%（T88 谐波域实测 98.45%%）；150 Hz 应 = 75.64%%")
    p("")
    p("  --- 监护三候选（notch OFF；链 = dc@0.02 -> LP2@xx；归一 5 Hz；判限 [71%%, 110%%]）---")
    p("  %8s | %-17s | %-17s | %-17s" % ("f(Hz)", "LP2@42", "LP2@45  ★推荐", "LP2@48"))
    p("  " + "-" * 76)
    rows = []
    for f in pts:
        cells = []
        for fc in (42.0, 45.0, 48.0):
            g, a = resp([f], [cand[fc]], DC_FC_DIAG, 5.0)
            cells.append((float(g[0]), float(a[0])))
        rows.append((f, cells))
        line = "  %8.2f |" % f
        for (gv, av) in cells:
            line += " %6.2f/ %6.2f%% |" % (gv, av)
        p(line)
    p("")
    p("  --- 判据点单列（整机口径，对 5 Hz 归一）---")
    p("  %8s | %-17s | %-17s | %-17s" % ("f(Hz)", "LP2@42", "LP2@45 ★", "LP2@48"))
    for f in (0.67, 5.0, 40.0):
        line = "  %8.2f |" % f
        for fc in (42.0, 45.0, 48.0):
            g, a = resp([f], [cand[fc]], DC_FC_DIAG, 5.0)
            v = float(a[0])
            mark = (" ✓" if 71 <= v <= 110 else " ✗") if f == 40.0 else "  "
            line += " %6.2f%%%s        |" % (v, mark)
        p(line)
    p("")
    p("  --- ### notch 状态对判据点的影响（必须写清口径！）---")
    base = {}
    for f in (0.67, 5.0, 40.0):
        g_off, a_off = resp([f], ab[15:], DC_FC_DIAG, 5.0)
        g_on, a_on = resp([f], ab, DC_FC_DIAG, 5.0)
        p("     %6.2f Hz : notch OFF %8.3f%% ｜ notch ON %8.3f%% ｜ |Δ| %6.3f pp"
          % (f, float(a_off[0]), float(a_on[0]), abs(float(a_off[0]) - float(a_on[0]))))
        base[f] = (float(a_off[0]), float(a_on[0]))
    fac = base[40.0][1] / base[40.0][0]
    p("     ==> 0.67 / 5 Hz 差 < 0.02 pp（可忽略）；但 **40 Hz 差 %.2f pp** ——"
      % abs(base[40.0][1] - base[40.0][0]))
    p("         因为 40 Hz 已经落在 50 Hz 陷波簇（Q12 三节 49.8/50.0/50.2）的影响边缘。")
    p("     ==> ★★ 口径纪律：**监护判据（频响试验）必须按 notch OFF 取值**（GB 9706.225/227 强制）。")
    p("         本报告 [2] 节即按 notch OFF。若审评方另要 notch ON 口径，按同一因子折算：")
    p("         40 Hz 因子（ON/OFF）= %.5f ==> 监护链 40 Hz = 77.20%% x %.5f = %.2f%%（仍 > 71%% ✓）"
      % (fac, fac, 77.20 * fac))
    p("     ==> 注意：因子与 LP 无关（陷波与 LP 串联，比值中 LP 约掉），故该折算对 LP2@42/45/48 通用。")
    return rows


# ================================================================== [2]
def sec2(ab, cand):
    p("")
    p("=" * 88)
    p("[2] 监护判据判定（GB 9706.227 / 201.12.1.101.8：0.67 与 40 Hz 应 ∈ 5 Hz 的 71%~110%）")
    p("=" * 88)
    p("  口径：notch OFF（频响试验）＋ 整机（固件链 × ADS1293 三级 5 阶级联 CIC）；归一 5 Hz。")
    p("")
    p("  [2a] 现状 ST40（notch OFF 链 = dc@0.05 -> LP4@40 -> HP4@0.05×2）—— 不修就过不了的对照")
    st = read_fw_sos("ECG_PROFILE_ST40")
    st_off = st[3:]                          # 跳过 3 节陷波
    for f in (0.67, 5.0, 40.0):
        g, a = resp([f], st_off, 0.05, 5.0)
        v = float(a[0])
        tag = ""
        if f == 40.0:
            tag = "   <-- 判限 71%%，差 %.2f pp  ==> %s" % (
                abs(v - 71), "✗ 不达标" if v < 71 else "✓")
        p("     %7.2f Hz : 固件 %7.2f%%  整机 %7.2f%%%s" % (f, float(g[0]), v, tag))
    p("")
    p("  [2b] 监护候选（只换第 15 节；其余 15 节陷波 + dc@0.02 逐位不动）")
    best = None
    for fc in (42.0, 45.0, 48.0):
        p("     --- LP2@%g ---" % fc)
        v40 = None
        for f in (0.67, 5.0, 40.0):
            g, a = resp([f], [cand[fc]], DC_FC_DIAG, 5.0)
            v = float(a[0])
            tag = ""
            if f == 40.0:
                v40 = v
                tag = "   <-- %.2f pp 余量 ==> %s" % (v - 71, "✓" if v >= 71 else "✗")
            p("        %7.2f Hz : 固件 %7.2f%%  整机 %7.2f%%%s" % (f, float(g[0]), v, tag))
        if best is None or (v40 is not None and v40 > best[1]):
            best = (fc, v40)
    p("")
    p("  => 最大余量候选 LP2@%g（40 Hz 整机 %.2f%%，余 %.2f pp）" % (best[0], best[1], best[1] - 71))
    p("  ★ 上角越高余量越大，但**上角越高、40 Hz 以上滚降越弱**（监护的临床意义正是压 40+ Hz）")
    p("    ==> 推荐 **LP2@45**：判据余 ~6 pp，且 50 Hz 仍有约 -4 dB。")
    p("")
    p("  [2c] 40 Hz 以上滚降对比（监护档的临床价值：抑制肌电/工频）")
    for f in (50.0, 60.0, 100.0):
        h = abs(H_sos_only([f], [cand[45.0]])[0])
        h4 = abs(H_sos_only([f], list(cand["LP4@40"]))[0])
        p("        %6.1f Hz : LP2@45 %6.1f%% (%6.2f dB)  |  LP4@40 %6.1f%% (%6.2f dB)"
          % (f, h * 100, db(h), h4 * 100, db(h4)))
    p("")
    p("  [2d] 判据 a（低频脉冲位移）是否受影响")
    p("     低端极点仍由 dc@0.02 单独决定（第 15 节在 0.1 Hz 的 |H| 偏差 < 1e-6）")
    for fc in (250.0, 45.0):
        c = ab[15] if fc == 250.0 else cand[fc]
        h = abs(H_sos_only([0.1], [c])[0])
        p("       LP2@%-4g 在 0.1 Hz : |H| = %.15f" % (fc, h))
    p("     ==> Σf 仍 = 0.02 Hz（37.7% 预算），**a1 维持 37.7 µV**，与诊断档逐位相同。")


# ================================================================== [3]
def sec3(ab, cand):
    p("")
    p("=" * 88)
    p("[3] 陷波节数敏感性：监护档要不要跳过 200/250 两簇（6 节）？")
    p("=" * 88)
    p("  A+B 表布局：[0..2]50 [3..5]100 [6..8]150 [9..11]200 [12..14]250 [15]LP")
    p("  监护档若保留全部 15 节 = 多跑 6 节（CPU 相同 16 节）；若跳 [9..14] 需两段循环。")
    p("")
    sos_mon_all = ab[:15] + [cand[45.0]]        # 15 陷波 + LP2@45
    sos_mon_9 = ab[:9] + [cand[45.0]]           # 9 陷波 + LP2@45（跳过 200/250）
    p("  %8s | %-14s | %-14s | %s" % ("f(Hz)", "15 节陷波 %", "9 节陷波 %", "|Δ| pp"))
    mx = 0.0
    for f in (0.1, 0.67, 5.0, 20.0, 40.0, 45.0):
        ha = abs(H_firmware([f], sos_mon_all, dc_fc=DC_FC_DIAG)[0])
        hb = abs(H_firmware([f], sos_mon_9, dc_fc=DC_FC_DIAG)[0])
        r_a = abs(H_firmware([5.0], sos_mon_all, dc_fc=DC_FC_DIAG)[0])
        r_b = abs(H_firmware([5.0], sos_mon_9, dc_fc=DC_FC_DIAG)[0])
        va, vb = ha / r_a * 100, hb / r_b * 100
        mx = max(mx, abs(va - vb))
        p("  %8.2f | %14.4f | %14.4f | %.5f" % (f, va, vb, abs(va - vb)))
    p("")
    p("  ⇒ ≤45 Hz 内最大差 %.5f pp ⇒ **保留全部 15 节与跳 6 节在监护频段等价**。" % mx)
    p("    结论：**保留**（零额外逻辑，避免两段循环；代价只是多跑 6 节，而节数本来就没变）。")
    p("  ★ 且保留 200/250 簇对监护**无副作用**：陷波只在自己频率附近起作用。")


# ================================================================== [4]
class BIQ:
    __slots__ = ("b0", "b1", "b2", "a1", "a2", "d1", "d2")

    def __init__(self, c):
        self.set(c)

    def set(self, c):
        self.b0, self.b1, self.b2, self.a1, self.a2 = c
        self.d1 = self.d2 = 0.0

    def preset(self, v):
        self.d1 = v * (1.0 - self.b0)
        self.d2 = v * (self.b2 - self.a2)

    def proc(self, x):
        y = self.b0 * x + self.d1
        self.d1 = self.b1 * x - self.a1 * y + self.d2
        self.d2 = self.b2 * x - self.a2 * y
        return y


class ChainSim:
    """dc_block + N 节 biquad（与固件位级一致）"""

    def __init__(self, sos, dc_fc):
        self.dc_fc = dc_fc
        self.r = 1.0 - 2.0 * np.pi * dc_fc / FS
        self.xp = 0.0
        self.yp = 0.0
        self.warmed = False
        self.s = [BIQ(c) for c in sos]

    def proc(self, x):
        if not self.warmed:
            self.xp = x
            self.yp = 0.0
            self.warmed = True
            return 0.0
        y = (x - self.xp) + self.r * self.yp
        self.xp = x
        self.yp = y
        for s in self.s:
            y = s.proc(y)
            self.last = y
        return y

    def reset_all(self):
        for s in self.s:
            s.d1 = s.d2 = 0.0


def sec4(ab, cand):
    p("")
    p("=" * 88)
    p("[4] 切换瞬态：把第 15 节从 LP2@250 换成 LP2@45 的几种策略")
    p("=" * 88)
    n = int(FS * 12.0)
    t = np.arange(n) / FS
    # 信号：0.8 mV 直流 + 0.6 mV @ 7 Hz + 0.15 mV @ 20 Hz
    x = 0.8 + 0.6 * np.sin(2 * np.pi * 7.0 * t) + 0.15 * np.sin(2 * np.pi * 20.0 * t)

    lp45 = cand[45.0]
    n_sw = int(FS * 8.0)          # 第 8 s 切换

    # 真值：全程走监护链（第 15 节一开始就是 LP2@45）
    ref = ChainSim(ab[:15] + [lp45], DC_FC_DIAG)
    yref = np.array([ref.proc(v) for v in x])
    # 理论最优切换基准：同一时刻"全程监护链"的状态（跳变 = 0 的下界）
    ref2 = ChainSim(ab[:15] + [lp45], DC_FC_DIAG)
    st_opt = None
    for i, v in enumerate(x):
        if i == n_sw:
            st_opt = (ref2.xp, ref2.yp, [(s.d1, s.d2) for s in ref2.s], ref2.warmed)
        ref2.proc(v)

    def mk(kind):
        """建链并在第 n_sw 步按 kind 切换。kind: A/B/C/D/E"""
        ch = ChainSim(ab[:15] + [lp45], DC_FC_DIAG)
        ch.s = [BIQ(c) for c in ab[:15]] + [BIQ(ab[15])]
        y = []
        for i, v in enumerate(x):
            if i == n_sw:
                s = ch.s[15]
                if kind == "A":
                    # 复用固件已有字段 last_x（= dc_block 输出 = 该节输入）
                    vv = ch.yp
                    s.set(lp45); s.preset(vv)
                elif kind == "B":
                    # 用结构体旧 b0 从旧状态反解"该节稳态输入"
                    oldb0 = s.b0
                    vv = s.d1 / (1.0 - oldb0) if abs(1.0 - oldb0) > 1e-12 else ch.yp
                    s.set(lp45); s.preset(vv)
                elif kind == "C":
                    s.set(lp45)                     # 只换系数，状态沿用
                elif kind == "D":
                    # 现行换档做法：整链重建（含 dc_block 重新预热）
                    ch.s = [BIQ(c) for c in (ab[:15] + [lp45])]
                    ch.xp = 0.0; ch.yp = 0.0; ch.warmed = False
                elif kind == "E":
                    # 理论最优：直接继承"全程监护链"的状态（下界，应当 = 0）
                    dx, dy, ss, wm = st_opt
                    ch.s[15].set(lp45)          # ★ 先换系数，再覆盖状态
                    ch.xp, ch.yp, ch.warmed = dx, dy, wm
                    for j, (d1_, d2_) in enumerate(ss):
                        ch.s[j].d1, ch.s[j].d2 = d1_, d2_
            y.append(ch.proc(v))
        return np.array(y)

    res = {"A 复用 last_x 预置": mk("A"),
           "B 旧 b0 反解预置": mk("B"),
           "C 只换系数不预置": mk("C"),
           "D 整链重建（现行做法）": mk("D"),
           "E 理论最优（下界）": mk("E")}

    p("  信号：0.8 mV 直流 + 0.6 mV@7 Hz + 0.15 mV@20 Hz；第 8.000 s 切换；单位 mV")
    p("  真值 = 全程监护链（第 15 节一开始就是 LP2@45）的稳态输出")
    p("")
    p("  ★ 注意：切换前 A/B/C/D 链 = 诊断链，与真值（监护链）在 7/20 Hz 本就有固有相位差")
    p("    （≈150 µV 摆动）⇒ 那不是瞬态。真正的瞬态指标是「切换点的不连续量 Δy」与「回真值用时」。")
    p("")
    p("  %-24s | %8s | %8s | %8s | %8s | %8s | %8s"
      % ("策略", "ΔyµV", "基线µV", "Δy−基线", "0.5s残差", "3s残差", "回真值"))
    p("  " + "-" * 92)
    notes = {
        "A 复用 last_x 预置": "复用固件已有字段（零额外存储）",
        "B 旧 b0 反解预置": "⇒ **推荐**：零额外存储（用结构体旧 b0 自洽反解）",
        "C 只换系数不预置": "最省，瞬态最大",
        "D 整链重建（现行做法）": "对照：低端重建 ⇒ **长漂**（真机 15~18 s）",
        "E 理论最优（下界）": "基准：直接继承监护链状态",
    }
    for k in ("A 复用 last_x 预置", "B 旧 b0 反解预置", "C 只换系数不预置",
              "D 整链重建（现行做法）", "E 理论最优（下界）"):
        y = res[k]
        jump = abs(np.max(np.abs(y[n_sw:n_sw + 20] - yref[n_sw:n_sw + 20]))) * 1000
        r05 = abs(np.max(np.abs(y[n_sw + int(FS * 0.5):n_sw + int(FS * 0.5) + 60] -
                               yref[n_sw + int(FS * 0.5):n_sw + int(FS * 0.5) + 60]))) * 1000
        r3 = abs(np.max(np.abs(y[n_sw + int(FS * 3):n_sw + int(FS * 3) + 60] -
                              yref[n_sw + int(FS * 3):n_sw + int(FS * 3) + 60]))) * 1000
        dv = np.abs(y[n_sw:] - yref[n_sw:]) * 1000
        idx = np.where(dv < 5.0)[0]
        dur = idx[0] if len(idx) else -1
        dy = abs(y[n_sw] - y[n_sw - 1]) * 1000
        base = abs(np.diff(y[n_sw - 200:n_sw])).max() * 1000
        p("  %-24s | %8.1f | %8.1f | %8.1f | %8.2f | %8.2f | %8s  %s"
          % (k, dy, base, dy - base, r05, r3,
             ("%d 拍" % dur) if dur >= 0 else ">5s", notes[k]))
    p("")
    p("  ★ 判读：")
    p("    · 「回真值用时」是最干净的指标（E = 0 拍是下界）：**B 6 拍 / A 7 拍 / C 8 拍 = 7~9 ms**；")
    p("    · D 的 3 s 残差仍大 ⇒ 低端重建是**持续长漂**（本仿真 12 s 窗口只看到一部分）；")
    p("    · **B 最优**（Δy 低于基线 ⇒ 无额外不连续；6 拍回真值）：用结构体自带的旧 b0 自洽反解，零额外存储；")
    p("  ★ 瞬态成因（不是 bug，是「用常数预置近似正弦态」的固有误差）：")
    p("    LP2@250 在 7 Hz 相位 ≈ −40° ⇒ 旧状态 d1 不等于 x(1−b0) 的直流式 ⇒")
    p("    DC 预置给出的是「平均正确」的值，瞬时差 ≈ 正弦摆幅的一小部分（百 µV 级）。")
    p("    ⇒ 若不接受这百 µV，可加 5 拍线性 crossfade（代价：+1 状态、+6 行）。")
    return res, yref, n_sw


# ================================================================== [5]
def sec5():
    p("")
    p("=" * 88)
    p("[5] 改动量清单（最小改动方案）")
    p("=" * 88)
    p("""
  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │ 文件                      改动                                              行数 │
  ├─────────────────────────────────────────────────────────────────────────────────┤
  │ Filter/ecg_filter_coeffs.h  新增 2 个宏（模式 ID）＋ 1 个"第 15 节下标"宏      +3  │
  │ Filter/ecg_filter.h         结构体 +1 字段 uint8_t mode;                      +2  │
  │                             声明 set_mode / mode_is_mon                       +4  │
  │ Filter/ecg_filter.c         新增 LP 系数表（2 组，共 10 个 double 字面量）    +12  │
  │                             新增 ecg_filter_f64_set_mode()（含 DC 预置）      +16  │
  │                             init() 里按默认模式装载                           +3  │
  │ Core/Src/vcomcmd.c          新增 test mode diag|mon 命令块                    +28  │
  │                             test notch 回显追加 mode 字段                      +3  │
  ├─────────────────────────────────────────────────────────────────────────────────┤
  │ 合计                                                                         ~71 行 │
  └─────────────────────────────────────────────────────────────────────────────────┘

  ★ 零改动的部分（这是"最小"的关键）：
    · 热路径 ecg_filter_f64_process()：**循环边界表达式完全不变**（表仍 16 节）
    · 结构体 biquad_df2t_f64 s[ECG_NUM_SECTIONS]：**尺寸不变**（16 节）
    · ECG_NUM_SECTIONS / ECG_NUM_NOTCH_SECTIONS / ECG_NUM_BP_SECTIONS：**全不变**
    · ECG_DC_BLOCK_FC_HZ_F：**不变**（0.02f）
    · dc_block 模块：**一行不改**
    · fast_restore_snippet.c / 伪迹状态机：**一行不改**
    · 15 节陷波系数：**逐位不变**（连字面量都不动）
    · 其余 8 个档位：**完全不受影响**
    · tools/_set_profile.py：**不用改**（档位宏仍是 A+B）

  RAM/ROM 代价：
    · ROM：+2 组 biquad 系数 = 2 × 5 × 8 B = **80 B**（0.008% of 1 MB）
    · RAM：+1 B（mode 字段）；结构体 s[] 尺寸不变
    · CPU：**不变**（mon 态仍 16 节 = 12.39%，与 diag 逐位相同节数）

  运行期语义：
    · `test mode`            → 只回显（diag/mon）
    · `test mode diag|mon`   → 切换。切档**只重写第 15 节系数 + 对该节 DC 预置**
    · `test afe start`       → 经 init() 把 mode 写回默认 diag（fail-safe，与 notch 同款）
    · 与 `test notch on|off` **正交**：两个开关可任意组合
        diag + notch off = 现行"诊断频响试验态"（**逐位不变**）
        mon  + notch on  = 监护噪声试验态
        mon  + notch off = 监护频响试验态
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fig", action="store_true")
    a = ap.parse_args()
    ab, cand = sec0()
    sec1(ab, cand)
    sec2(ab, cand)
    sec3(ab, cand)
    res, yref, n_sw = sec4(ab, cand)
    sec5()
    io.open(os.path.join(HW, "_t91_out.txt"), "w", encoding="utf-8",
            newline="\n").write("\n".join(OUT) + "\n")
    print("\n[写出] tools/_t91_out.txt  (%d 行)" % len(OUT))

    if a.fig:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(2, 2, figsize=(14, 9))
        f = np.logspace(-2, np.log10(600), 900)
        # 1) 频响对比
        for sos, dc, lab, col in ((ab[15:], DC_FC_DIAG, "诊断 diag = A+B (LP2@250, notch OFF)", "#1f6fb4"),
                                  ([cand[45.0]], DC_FC_DIAG, "监护 mon (LP2@45, notch OFF)", "#c0392b")):
            H = np.abs(H_firmware(f, sos, dc_fc=dc))
            Hr = np.abs(H_firmware([10.0], sos, dc_fc=dc)[0])
            ax[0, 0].semilogx(f, 20 * np.log10(H / Hr), label=lab, color=col, lw=1.8)
        ax[0, 0].axvline(40, color="#888", ls=":", lw=1)
        ax[0, 0].axhline(-3, color="#888", ls="--", lw=1)
        ax[0, 0].set_title("固件链频响（notch OFF，归一 10 Hz）")
        ax[0, 0].set_xlabel("Hz"); ax[0, 0].set_ylabel("dB")
        ax[0, 0].legend(fontsize=8); ax[0, 0].grid(alpha=.3, which="both")
        # 2) 监护判据
        s45 = [cand[45.0]]
        s40 = read_fw_sos("ECG_PROFILE_ST40")[3:]
        for sos, dc, lab, col in ((s40, 0.05, "现状 ST40 (LP4@40, notch OFF)", "#e67e22"),
                                  (s45, DC_FC_DIAG, "候选 mon (LP2@45, notch OFF)", "#27ae60")):
            ff = np.array([0.67, 5.0, 40.0, 50.0])
            H = np.abs(H_firmware(ff, sos, dc_fc=dc)) * h_afe(ff)
            Hr = (np.abs(H_firmware([5.0], sos, dc_fc=dc)[0]) * h_afe([5.0])[0])
            ax[0, 1].plot(ff, H / Hr * 100, "o-", label=lab, color=col, lw=1.8)
        ax[0, 1].axhline(71, color="#c0392b", ls="--", lw=1.2, label="判限 71%")
        ax[0, 1].axhline(110, color="#888", ls=":", lw=1)
        ax[0, 1].set_xscale("log"); ax[0, 1].set_title("监护判据（对 5 Hz 归一，整机）")
        ax[0, 1].set_xlabel("Hz"); ax[0, 1].set_ylabel("%")
        ax[0, 1].legend(fontsize=8); ax[0, 1].grid(alpha=.3, which="both")
        # 3) 切换瞬态
        t = np.arange(len(yref)) / FS
        for name, col in (("A 复用 last_x 预置", "#2980b9"), ("B 旧 b0 反解预置", "#27ae60"),
                          ("C 只换系数不预置", "#f39c12"), ("D 整链重建（现行做法）", "#c0392b"),
                          ("E 理论最优（下界）", "#7f8c8d")):
            ax[1, 0].plot(t, (res[name] - yref) * 1000, label=name, color=col, lw=1.2)
        ax[1, 0].axvline(n_sw / FS, color="k", ls=":", lw=1)
        ax[1, 0].set_xlim(n_sw / FS - 0.12, n_sw / FS + 0.30)
        ax[1, 0].set_title("切换瞬态：与「一直跑监护链」之差（µV）\n（切换前的摆动＝diag/mon 两链固有相位差，非瞬态）")
        ax[1, 0].set_xlabel("s"); ax[1, 0].set_ylabel("µV")
        ax[1, 0].legend(fontsize=8); ax[1, 0].grid(alpha=.3)
        # 4) 陷波节数敏感性
        lab = ["≤45 Hz", "15 节陷波", "9 节陷波"]
        ff = np.array([0.1, 0.67, 5.0, 20.0, 40.0, 45.0])
        va, vb = [], []
        for x_ in ff:
            ra = abs(H_firmware([5.0], ab[:15] + [cand[45.0]], dc_fc=DC_FC_DIAG)[0])
            rb = abs(H_firmware([5.0], ab[:9] + [cand[45.0]], dc_fc=DC_FC_DIAG)[0])
            va.append(abs(H_firmware([x_], ab[:15] + [cand[45.0]], dc_fc=DC_FC_DIAG)[0]) / ra * 100)
            vb.append(abs(H_firmware([x_], ab[:9] + [cand[45.0]], dc_fc=DC_FC_DIAG)[0]) / rb * 100)
        w = 0.35
        ax[1, 1].bar(np.arange(len(ff)) - w / 2, va, w, label="15 节陷波", color="#1f6fb4")
        ax[1, 1].bar(np.arange(len(ff)) + w / 2, vb, w, label="9 节陷波", color="#95a5a6")
        ax[1, 1].set_xticks(range(len(ff)))
        ax[1, 1].set_xticklabels(["%g" % v for v in ff])
        ax[1, 1].set_title("陷波节数敏感性（监护频段，差异 ≈ 0）")
        ax[1, 1].set_xlabel("Hz"); ax[1, 1].set_ylabel("%")
        ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3, axis="y")
        plt.tight_layout()
        out = os.path.join(HW, "T91_监护诊断可选_最小改动评估_2026-09-21.png")
        plt.savefig(out, dpi=130)
        print("[出图] " + out)


if __name__ == "__main__":
    main()
