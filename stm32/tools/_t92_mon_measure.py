# -*- coding: utf-8 -*-
"""_t92_mon_measure.py —— 监护/诊断「运行期可选」上板判读（T92，2026-09-22）

为什么需要这个脚本（而不是直接用 `_t70_fr_measure.py`）
--------------------------------------------------------
1) `_t70_fr_measure.py` 的预期值来自 `_t69_fr_forecast.py` 的**档位宏解析**，而该解析仍是旧格式
   （要求 `#elif (...)\n#define ECG_NUM_SECTIONS` 紧邻），读 A+/A+B 会得 `SECTIONS=None`
   ⇒ **它的 notch OFF 列对 A+B 不可信**（T90 §9 已记）。
2) 监护态**不是一个档位**，而是「A+B 档 + 运行期把第 15 节 LP2@250 换成 LP2@45」
   —— 没有哪个既有脚本能表达"只换一节"。
⇒ 本脚本**自包含**：自己直读源码系数、自己做"只换第 15 节"、自己带 AFE 模型、自己算判据。

判据（★ T96 起：**两窗并列**；★ T97 起：**diag 档单独一套**）
--------------------------------------------------------------
【mon 档】★ 猛哥 2026-09-22 12:3x 拍板：**两窗并列出**。两窗**参考点同为 5 Hz**，仅上界不同：
    · **GB 9706.227-2021（2-27 监护）**    ：0.67 与 40 Hz ∈ **71%~110%**
    · **YY 9706.247-2021（2-47 动态心电）** ：原文 **201.12.4.4.108 b)** —— 频率 0.67~40 Hz
      的正弦信号，响应幅度应 ∈ 5 Hz 时的 **70%~140%（+3 dB~−3 dB）**
  ★ 原文 b) 要求测**七频点**：**0.67 / 1 / 2 / 5 / 10 / 20 / 40 Hz**（5 Hz 为参考点）。
  ⚠ 两标准**适用范围互斥**（2-47 scope 原文排除 2-25/2-27 覆盖的设备）⇒
    报哪个窗由**产品注册走哪条**决定；本脚本**两个都印**，免得注册口径变了要重跑。
  ⚠ 同条 a)（3 mV/100 ms 方波：位移 ≤0.1 mV、终点后斜率 ≤0.3 mV/s、过冲 <10%）
    是**并列必过项**（判据结构＝ a) 且（b) 或 c)）⇒ 走 b 路径不代表 a 可免。
    a) 离线评估见 `tools/_t96_two_window.py`（两档 a1/a2 相同、a3 mon 略大但远离 10% 限）。
  ★ 口径三条（缺一即错）：
    a) 归一化参考点 = **5 Hz**（两窗相同；10 Hz 是诊断档的参考点）
    b) **必须 notch OFF**（标准要求频响试验关陷波）
    c) 报数**必须标注 notch 状态** —— 40 Hz 距 50 Hz 陷波簇只有 10 Hz，
       开关使该点差 **5.03 pp**（T91 §2.4）

【diag 档】★ T97：判据是 **GB 9706.225-2021 表 201.107**（相对 **10 Hz** 归一、**按行归一化**）
    —— 一律 **+10%/−30% ⇒ 70%~110%**，只是**每行输入幅度不同**（标准规定，逐行对账）：

    | 行 | 频段 | 该行输入 | 判限 |
    |---|---|---|---|
    | 1 | 0.67~40 Hz | 1 mV | 70~110% |
    | 2 | 40~100 Hz | 0.5 mV | 70~110% |
    | 3 | **100~150 Hz** | **0.25 mV** | 70~110%（**唯一贴限行，决定性点 100/120/150 Hz**）|

  ⇒ 150 Hz（第 3 行下限点）**09-22 实测 75.581%**（预期 75.639%，−0.057 pp）✓ 余 5.58 pp
  🛑 **必须 notch OFF** —— 否则 150 Hz 落进陷波簇 ⇒ 读成 0% 的**假失败**。
  ⚠ 超出 150 Hz 的点不属表 201.107 ⇒ 脚本标"仅参考"。

用法
----
  # 0) 先跑自测（离线端到端，确认脚本本身可信；不需要硬件）
  python tools/_t92_mon_measure.py --selftest

  # 1) ★ 实测判读（现场首选 `--norm col2`：用列② 实测幅度归一 ⇒ 免疫「源幅度设错」）
  python tools/_t92_mon_measure.py "fr-*.txt" --mode mon --norm col2
  #    ★ col2 默认折进 AFE 因子 ⇒ 报「**整机**」传递比，与表格「预期(整机)%」栏同口径可比。
  #    `--afe off` ⇒ 退回「**固件链**」口径（复算 T94 历史数据／分离 AFE 贡献用，别对照标准判据）。

  # 2) ★ 诊断档判读（表 201.107，参考点 10 Hz）
  python tools/_t92_mon_measure.py "diag-*.txt" --mode diag --ref 10 --norm col2

  # 3) 文件名不带幅度时统一下发；指定参考频率
  python tools/_t92_mon_measure.py "fr-*.txt" --mode mon --amp 1.0 --ref 5

🛑 读数前**必看「实测f」列**（T97 起）
--------------------------------------
  脚本会**先在标称频率 ±3% 内扫峰求真频、再在该真频处读幅**。为什么必须如此：
    · 本项目 AECG100 实测有频偏：10 Hz 档实际输出 **10.0533 Hz（+0.53%）**，
      Hann 窗下按标称读会**泄漏低估约 22%** ⇒ 「列②自证」会假报"源设错了三分之一"。
    · 列② 与列⑤ **用同一个 f_use 读** ⇒ 频偏/泄漏在比值里共模抵消（比值判据本就免疫）。
  ⇒ 若「实测f」与标称差 >2%，先怀疑源；`f_ok=False`（信噪比不足）时自动回退标称频率。

输出
----
  控制台表格 ＋ `tools/_t92_out.txt`（留痕）

归一口径（three ways，别混）
----------------------------
  · `--norm name`（默认）：列⑤ ÷ **文件名标称**幅度。**源幅度设错就会误判**（现场高发）。
  · `--norm col2`（★ 推荐）：列⑤ ÷ **列② 实测**幅度。
      列② = ecgraw，位置在 **AFE 之后、固件滤波链之前**
      ⇒ 裸比值只是「固件链传递比」；★T96b 起**默认再乘 AFE(f)/AFE(ref)** ⇒ 「整机传递比」。
      ⚠ 漏乘 AFE 的后果（T94 坑 #13）：40 Hz 报 **78.59%** 而非 **77.20%**，会把
        「与设计差 1.39 pp」误读成问题 —— 那是**回归判据**（±1.0 pp）的事，**不是超国标限**。
  · `--norm col2 --afe off`：显式回到固件链口径（仅用于复算历史 / 拆 AFE 贡献）。

「列②自证 / vs标称」栏的口径（★ T97 修正，两个坑都堵上了）
----------------------------------------------------------
  `列②自证` = 列② 换算出的**实际输入 mVpp** = `2·a_in·UV_PER_CODE / afe_gain(f)`。
  🛑 **坑 A（缺 AFE 除数）**：列② 取自 **AFE 之后**（ecgraw）⇒ 不除 `afe_gain(f)`
     就把 AFE 的衰减算成"源少了"：本例 150 Hz 会显示 **−30.1%**，除完只剩 **−9.5%**。
  🛑 **坑 B（源频偏）**：按标称频率读 ⇒ Hann 窗泄漏 ⇒ 本例 10 Hz 档（实际 **10.0533 Hz**）
     显示 **−30.4%**，扫真频后只剩 **−10.2%**。
  ⇒ 修完后两点都是 **−10% 左右**＝**共模**（源实际输出偏低 或 定标系数偏高；
     与方波 DC 平台的 +1.4% 口径不一致，待独立标定）
     ⇒ **它在比值判据里完全约掉，不影响任何结论**；别当"源设错"去重抓。
  ⚠ 该栏与「实测f」列**配合使用**：先看频率对不对，再看幅度差多少。

列口径
------
  txt 列（1-based）：①idxraw(本轮=0) ②raw24b ③idxfilter ④dsrateraw ⑤filterraw ⑥dtc ⑦hex
  ⇒ 默认读 **列⑤**（滤波后 code）。500 Hz 域（未开 raw853）即可，**监护判据不需要 raw853**。
"""
import argparse
import glob
import io
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, '..'))
C_FILE = os.path.join(PROJ, 'Filter', 'ecg_filter.c')
H_FILE = os.path.join(PROJ, 'Filter', 'ecg_filter_coeffs.h')
OUT = os.path.join(HERE, '_t92_out.txt')

# 滤波器域采样率（T92 §0：固件链恒在 853 域；500 Hz 只影响输出密度）
FS = 1e6 / 1172.0            # 853.2423208191126
FS_TXT_500 = 500.0           # 未开 raw853 时的输出行率（标称；实测判读用行率反算更稳）

# ★ 监护态上角节（LP2@45）—— 与固件落码常量**必须逐位相同**
#    生成口径：scipy.signal.butter(2, 45, 'low', fs=1e6/1172, output='sos')
#    （同 `tools/_t58_gen_bp250.py`；`--selftest` 会再算一遍对账）
LP2_45 = np.array([0.02211456900210405, 0.0442291380042081, 0.02211456900210405,
                   -1.5374882597180526, 0.6259465357264689])

DC_FC_DIAG = 0.02            # A+B 档 ECG_DC_BLOCK_FC_HZ_F（Σf = 0.02 Hz）

# 单板定标（µV/code）。⚠ 它在**相对比值**里会约掉（分子分母同系数）⇒ 监护判据不受其影响；
#   它只影响「绝对 µV」列，以及 `--norm col2` 时把列② 幅度换算成 mVpp。
#   ★T96b：`--norm col2` 会**再乘 afe_gain(f)/afe_gain(ref)**（AFE 在列② 之后、列⑤ 之前，
#   裸比值缺这一段）⇒ 报「整机」传递比；`--afe off` 可关掉。
UV_PER_CODE = 0.1122
AFE_FSD = 102400.0           # ADS1293 数字滤波器输入率
AFE_R = (4, 5, 6)            # 三级 sinc 的抽取比（与 T70/T90 同口径）

_buf = []


def p(s=''):
    print(s)
    _buf.append(s)


# --------------------------------------------------------------- 源码直读系数
def read_ab_sos():
    """从 `Filter/ecg_filter.c` 的 A+B 分支直读 f64 系数表（16 节）。"""
    src = io.open(C_FILE, encoding='utf-8').read()
    m = re.search(r'#elif \(ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS_B\)'
                  r'(.*?)(?=\n#elif )', src, re.S)
    if not m:
        raise SystemExit('未找到 A+B 分支（检查 ecg_filter.c）')
    body = m.group(1)
    m2 = re.search(r'ECG_FILTER_COEFFS_F64\s*\[[^\]]*\]\s*=\s*\{(.*?)\n\};', body, re.S)
    txt = re.sub(r'/\*.*?\*/', '', m2.group(1), flags=re.S)
    txt = re.sub(r'//[^\n]*', '', txt)
    out = []
    for g in re.findall(r'\{([^{}]*)\}', txt):
        v = [float(x) for x in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', g)]
        if len(v) >= 5:
            out.append(v[:5])
    return np.asarray(out, float)


def read_ab_dc_fc():
    """从 coeffs.h 的 A+B 分支读 dc_block 截止频率（防落码后与脚本常量脱钩）。"""
    hdr = io.open(H_FILE, encoding='utf-8').read()
    m = re.search(r'#elif \(ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS_B\)'
                  r'(.*?)(?=\n#elif |\n#else)', hdr, re.S)
    if not m:
        return DC_FC_DIAG
    m2 = re.search(r'#define ECG_DC_BLOCK_FC_HZ_F\s+([\d.]+)f', m.group(1))
    return float(m2.group(1)) if m2 else DC_FC_DIAG


# --------------------------------------------------------------- 频响模型
def h_bq(sos, f):
    """级联 biquad（5 系数：b0,b1,b2,a1,a2）在 f 处的复频响。"""
    f = np.atleast_1d(np.asarray(f, float))
    z = np.exp(-2j * np.pi * f / FS)
    h = np.ones_like(z, dtype=complex)
    for b0, b1, b2, a1, a2 in sos:
        h *= (b0 + b1 * z + b2 * z ** 2) / (1.0 + a1 * z + a2 * z ** 2)
    return h


def h_dc_block(fc, f):
    """dc_block（一阶 HP：y[n] = (x[n]−x[n−1]) + r·y[n−1]，r = 1 − 2π fc/FS）。"""
    f = np.atleast_1d(np.asarray(f, float))
    r = 1.0 - 2.0 * np.pi * fc / FS
    z = np.exp(-2j * np.pi * f / FS)
    return (1.0 - z) / (1.0 - r * z)


def afe_gain(f):
    """ADS1293 AFE（三级 **5 阶**级联 sinc/CIC）幅度响应。

    ⚠ 两个易错点（T92 自测踩过第一版）：
      ① **每级是 5 阶**（`|sin(Rx)/(R sin x)|^5`），不是 1 阶 —— 漏掉阶次会把 40 Hz
         算成 −0.0001 dB（应为 −0.16 dB），从而高估监护判据余量约 1.4 pp；
      ② **每级输入率递减** 102400 → 25600 → 5120，不是固定 102400。
    口径与 `_t91_mode_eval.h_afe` / `_t70_fr_measure` / `_t69_fr_forecast` 一致。
    """
    f = np.atleast_1d(np.asarray(f, float))
    h = np.ones_like(f)
    fin = AFE_FSD
    for R in AFE_R:
        x = np.pi * f / fin
        h = h * np.abs(np.sin(R * x) / (R * np.sin(x))) ** 5
        fin /= R
    return h


def chain_sos(ab, mode):
    """A+B 全表；mode='mon' 时把第 15 节（上角节）换成 LP2@45。"""
    sos = np.array(ab, dtype=float, copy=True)
    if mode == 'mon':
        sos[15] = LP2_45
    return sos


def h_chain(ab, mode, f, dc_fc=None):
    """固件链复频响（dc_block × 16 节，**含 15 节陷波** ⇒ 即 notch ON 口径）。"""
    dc = DC_FC_DIAG if dc_fc is None else dc_fc
    return h_dc_block(dc, f) * h_bq(chain_sos(ab, mode), f)


def h_chain_notch_off(ab, mode, f, dc_fc=None):
    """notch OFF 口径（跳过前 15 节，只剩 1 节 LP）——判据必须用这个。"""
    dc = DC_FC_DIAG if dc_fc is None else dc_fc
    return h_dc_block(dc, f) * h_bq(chain_sos(ab, mode)[15:], f)


def predict(ab, mode, f, ref, with_afe=True, notch_off=True, dc_fc=None):
    """预期归一化百分比（%），参考点 ref（Hz）。"""
    fn = (lambda x: h_chain_notch_off(ab, mode, x, dc_fc)) if notch_off \
        else (lambda x: h_chain(ab, mode, x, dc_fc))
    num = np.abs(fn(f))
    den = np.abs(fn(ref))
    if with_afe:
        num = num * afe_gain(f)
        den = den * afe_gain(ref)
    return num / den * 100.0


def notch_factor(ab, mode, f, dc_fc=None):
    """notch ON / OFF 在该点的比值（口径提醒用：40 Hz 约 0.94876）。"""
    off = np.abs(h_chain_notch_off(ab, mode, f, dc_fc))
    on = np.abs(h_chain(ab, mode, f, dc_fc))
    return (on / off)[0]


# ------------------------------------------------------- 判据 b) 窗（两窗并列）
#  ★ T96：原文 b) 的七频点 ＋ 两窗（参考点同为 5 Hz，仅上界不同）
B_FREQS = (0.67, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0)
WIN_227 = (71.0, 110.0)      # GB 9706.227-2021（2-27 监护）
WIN_247 = (70.0, 140.0)      # YY 9706.247-2021 · 201.12.4.4.108 b)（2-47 动态心电）

# ------------------------------------------------- 判据（诊断档）GB 9706.225-2021 表 201.107
#  ★ 诊断心电（diag）频响：**相对 10 Hz 归一**、**按行归一化**，判限一律 **+10%/−30%（70%~110%）**。
#  ★ 各行**输入幅度不同**（这是标准规定的，不是现场随意设的）⇒ 文件名标称应逐行对得上。
#  🛑 100~150 Hz 行是唯一贴限行（下限 70%），且**必须 notch off**（否则 150 Hz 被陷波打死 ⇒ 假失败）。
WIN_225 = (70.0, 110.0)
T107_ROWS = ((0.67, 40.0, 1.00),      # 第 1 行：1 mV
             (40.0, 100.0, 0.50),     # 第 2 行：0.5 mV
             (100.0, 150.0, 0.25))    # 第 3 行：0.25 mV


def t107_row(f):
    """f 落在表 201.107 哪一行；返 (该行要求输入 mV, lo, hi) 或 None（超出 150 Hz）。"""
    for lo_f, hi_f, amp in T107_ROWS:
        if lo_f - 1e-9 <= f <= hi_f + 1e-9:
            return amp, WIN_225[0], WIN_225[1]
    return None


def is_b_freq(f):
    """是否原文 b) 的判定频点（容差 0.02 Hz，吸收文件名/实测频偏）。"""
    return any(abs(f - x) < 0.02 for x in B_FREQS)


def win_verdict(v, lo, hi):
    """窗内判定串：✓+余量 / ✗带方向与越限量（pp）。"""
    if lo <= v <= hi:
        return '✓%+.2f' % min(v - lo, hi - v)
    return '✗%+.2f' % (v - lo if v < lo else v - hi)


# --------------------------------------------------------------- 实测侧
def scan_peak_freq(x, fs, f0, span=0.03):
    """在 f0 ±span 内扫 DFT 峰求**真实频率**（抛物线细化）。

    🛑 为什么必须做（T94 坑 #14 / T97 实证）：AECG100 的设定频率与实际输出有偏
    （本项目实测 10 Hz 档实际 **10.0533 Hz**＝+0.53%）。Hann 窗下 0.53% 的频偏会带来
    显著 DFT 泄漏 ⇒ **按标称频率读幅会低估 ~22%**，对「列②自证」这类绝对幅度读数是致命的
    （会假报"源设错了三分之一"）。对比值判据影响小（分子分母同频读 ⇒ 泄漏共模抵消），
    但用真频读更贴近标准的"该频点传递比"。
    返 (f_peak, ok)；ok=False 时调用方应回退标称频率。
    """
    x = np.asarray(x, float)
    N = len(x)
    if N < 64:
        return f0, False
    X = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(N)))
    fr = np.fft.rfftfreq(N, 1.0 / fs)
    idx = np.flatnonzero((fr >= f0 * (1 - span)) & (fr <= f0 * (1 + span)))
    if idx.size == 0:
        return f0, False
    k = idx[np.argmax(X[idx])]
    fpk = fr[k]
    if 0 < k < len(X) - 1:                      # 抛物线插值（bin 内细化）
        a, b, c = X[k - 1], X[k], X[k + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-30:
            fpk = fr[k] + 0.5 * (a - c) / den * (fr[1] - fr[0])
    m = np.ones(len(X), bool)                   # 噪底：排除主瓣 ±3 bin 后取中位
    m[max(0, k - 3):k + 4] = False
    m &= (fr > 0.5) & (fr < 0.98 * fs / 2.0)
    floor = float(np.median(X[m])) if m.any() else 0.0
    snr = X[k] / floor if floor > 1e-30 else 1e9
    return float(fpk), bool(snr > 8.0)


def tone_amp(x, f0, fs=FS, win='hann'):
    """单频幅度（Hann 窗 DFT，按窗增益归一）——与 `_t70_fr_measure.tone_amp` 同口径。"""
    x = np.asarray(x, float)
    n = np.arange(len(x))
    w = np.hanning(len(x)) if win == 'hann' else np.ones(len(x))
    ph = np.exp(-2j * np.pi * f0 * n / fs)
    return 2.0 * abs(np.sum(x * w * ph)) / np.sum(w)


def load_txt(path, col=5):
    """按空白分列读第 col 列（1-based）；含坏行防护：列数必须 ==7 且列②在 24 bit 量程内。"""
    xs, nbad, nrow = [], 0, 0
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for ln in fh:
            q = ln.split()
            if not q:
                continue
            nrow += 1
            if len(q) != 7:
                nbad += 1
                continue
            try:
                raw = int(q[1])
                if not (0 <= raw <= 16777215):
                    nbad += 1
                    continue
                xs.append(float(q[col - 1]))
            except ValueError:
                nbad += 1
    return np.asarray(xs, float), nbad, nrow


def row_rate_of(path):
    """行率：用列③（500 域 idxfilter，Δ=1）反算 —— 比"假设 500.00"稳。
    ⚠ 若该列为常量 0（raw853 on），返回 None（此时应改用列①，但本轮列①已被置字面 0）。"""
    v = []
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for ln in fh:
            q = ln.split()
            if len(q) == 7:
                try:
                    v.append(int(q[2]))
                except ValueError:
                    pass
    if len(v) < 100:
        return None
    v = np.asarray(v, dtype=np.int64)
    if v[-1] == v[0]:
        return None
    return FS_TXT_500 * len(v) / float(v[-1] - v[0])


def parse_name(path):
    """从文件名解析频率(Hz)与输入幅度(mVpp)：fr-40Hz-1mVpp-....txt"""
    b = os.path.basename(path)
    mf = re.search(r'(\d+(?:\.\d+)?)\s*Hz', b, re.I)
    ma = re.search(r'(\d+(?:\.\d+)?)\s*mV(?:\s*pp)?', b, re.I)
    return (float(mf.group(1)) if mf else None,
            float(ma.group(1)) if ma else None)


# --------------------------------------------------------------- 自测
def selftest(ab):
    """离线端到端：合成"伪 txt"（列⑤ = 链输出 code）→ 走完整判读路径 → 对账 T91 设计值。

    期望（T91 §2.2，notch OFF）：
        40 Hz 固件口径 78.63% ／ 整机口径 77.20%（余 6.20 pp）
    """
    p('=' * 92)
    p('[自测] 合成 5 Hz / 40 Hz / 0.67 Hz 正弦 → 过「dc@0.02 + LP2@45」→ 写成 7 列伪 txt → 判读')
    p('=' * 92)
    ok = True

    # 0) 生成口径自证（scipy 可用时）
    try:
        from scipy import signal as _sg
        so = _sg.butter(2, 45.0, 'low', fs=FS, output='sos')[0]   # 6 值：b0,b1,b2,a0,a1,a2
        idx = (0, 1, 2, 4, 5)                                     # ⇒ 固件 5 值映射（丢 a0）
        d = max(abs(float(so[idx[i]]) - LP2_45[i]) for i in range(5))
        p('  [0] scipy butter(2,45,fs=1e6/1172) vs 脚本内置常量：max|Δ| = %.3g  %s'
          % (d, '✓ 逐位一致' if d < 1e-15 else '✗ 不一致（改脚本常量！）'))
        ok &= (d < 1e-12)
    except ImportError:
        p('  [0] scipy 不可用 ⇒ 跳过生成口径自证（判读本身不依赖 scipy）')

    # 1) 合成
    tmp = os.path.join(HERE, '_t92_selftest')
    if not os.path.isdir(tmp):
        os.makedirs(tmp)
    dur = 10.0
    n = int(FS * dur)
    t = np.arange(n) / FS
    # ★ 合成幅度取 3 mVpp（现场实际量级）。若用 1 mVpp，输入只有 ~56 code 峰，
    #   而列② 是**整数** code（±0.5 量化）⇒ 幅度误差 ~0.73% ⇒ 两种归一会假差 0.5+ pp。
    amp_mVpp = 3.0
    A = amp_mVpp * 1000.0 / 2.0 * UV_PER_CODE   # mVpp → µV 峰 → code（与列⑤同量纲）
    # 输出侧抽取到 500 Hz —— 真实工况（未开 raw853），这样自测覆盖的是**明天的实际路径**
    n_out = int(dur * FS_TXT_500)
    pick = np.round(np.arange(n_out) * (FS / FS_TXT_500)).astype(int)
    pick = pick[pick < n]

    files = []
    for f in (0.67, 5.0, 40.0):
        x = A * np.sin(2 * np.pi * f * t)
        y = _sim_chain(ab, 'mon', x) * afe_gain(f)[0]   # 折进 AFE ⇒ 模拟"整机实测"
        yo = y[pick]
        xo = x[pick] * afe_gain(f)[0]
        # ★T96b：列② = 「**AFE 之后、固件链之前**」（真实数据里 = 正弦 + VCM，且已被 AFE 着色）。
        #   🛑 必须乘 afe_gain：旧版写成纯输入 ⇒ 合成列② 不含 AFE、真实列② 含 AFE
        #   ⇒ 自测里 col2 会自动"多带回一个 AFE"而显得与 name 一致，**恰好掩盖坑 #13**。
        #   同构之后，col2 需靠上面的 AFE 校正项才能对齐整机 ⇒ 自测才真正锁住该口径。
        fn = os.path.join(tmp, 'fr-%gHz-3mVpp-SELFTEST.txt' % f)
        with io.open(fn, 'w', encoding='utf-8', newline='\n') as fh:
            for i in range(len(yo)):
                # 7 列：①0 ②raw24b(链前输入＋VCM 偏置) ③idxfilter(Δ=1 ⇒ 行率可反算) ④ds ⑤filter ⑥dtc ⑦hex
                # ③ 故意每隔 500 行跳 20 步（模拟**丢行**）⇒ 锁死回归：fs 必须仍按 500 读，
                #   不能被列③ 反算（会得 ~482）带偏（否则 40 Hz 幅度会被大幅低估）
                fh.write('0 %07d %06d %05d %.02f %05d %03x\n'
                         % (int(6000000 + xo[i]), i + (i // 500) * 20, i % 100000,
                            yo[i], 1960 + (i % 3), i % 4096))
        files.append(fn)

    # 2) 判读（★ 两种归一口径都应给出同一**整机**传递比 ⇒ 互相印证）
    #    name = 列⑤/标称输入 = 整机（含 AFE）；col2 = 列⑤/列② × AFE校正 ⇒ 整机 ⇒ **两者必须一致**。
    #    不一致 ⇒ 列② 通路（加载/DFT）或 AFE 因子有问题，而不是固件。
    #    🛑 另外单列一栏 col2(off) = 固件链口径（--afe off）⇒ 用于显式展示坑 #13 的 1.34 pp 差。
    rows = measure(files, 'mon', ref=5.0, amp=None, col=5, burn=1.0, quiet=True)
    rows_c2 = measure(files, 'mon', ref=5.0, amp=None, col=5, burn=1.0, quiet=True, norm='col2')
    rows_c2_off = measure(files, 'mon', ref=5.0, amp=None, col=5, burn=1.0, quiet=True,
                          norm='col2', afe=False)   # ★T96b：固件链口径（显式展示坑 #13）
    p('')
    p('  [1] 判读回读结果（合成数据 ⇒ 应与设计值逐位接近）')
    p('      %-8s | %11s | %11s | %13s | %10s | %s'
      % ('f(Hz)', '实测%(name)', '实测%(col2)', '预期(整机)%', '偏差', '判决'))
    d_c2 = {r[0]: r[1] for r in rows_c2}
    d_c2_off = {r[0]: r[1] for r in rows_c2_off}   # ★T96b：固件链口径（缺 AFE）
    exp40 = None
    for f, got, exp, d, verdict in rows:
        g2 = d_c2.get(f, float('nan'))
        p('      %-8g | %11.3f | %11.3f | %13.3f | %+10.3f | %s'
          % (f, got, g2, exp, d, verdict))
        if abs(f - 40.0) < 1e-9:
            exp40 = (got, exp, g2)
            p('      ⇒ 两种归一差 = %+.4f pp（**name 口径含「853→500 最近邻抽取」的共模偏差**，'
              '与 col2 无关；col2 用同一次采集的列② 归一 ⇒ 该偏差被消掉）' % (got - g2))
    if exp40:
        g, e, g2 = exp40
        g2o = d_c2_off.get(40.0, float('nan'))
        p('')
        p('  [2] 40 Hz 关键点对账（对 T91 设计 77.20%）')
        # ★ col2 口径（列⑤/列②×AFE校正，同一次采集）⇒ 源幅度与抽取链路**共模抵消** ⇒ 应贴设计值 ⇒ 容差 ±0.5 pp
        #   name 口径（文件名标称）⇒ 含「853→500 最近邻抽取」共模偏差（本例 −0.53 pp）＋列⑤ 量化
        #   ⇒ 容差放宽到 ±1.0 pp。**现场以 col2 为准。**
        p('      · col2 口径（列⑤/列②×AFE ⇒ **整机**）实测 %.3f%%  ⇒ 偏差 %+.3f pp  %s'
          % (g2, g2 - 77.20, '✓' if abs(g2 - 77.20) < 0.5 else '✗ 偏设计'))
        p('      · col2(off) 口径（列⑤/列② ⇒ **固件链**，T94 坑 #13）%.3f%%  ⇒ 偏差 %+.3f pp'
          % (g2o, g2o - 77.20))
        p('        ⇒ ★ 两者差 %+.3f pp ＝ **AFE 在 40 Hz 相对 5 Hz 的衰减**；'
          '报"整机"必须用上面那行' % (g2 - g2o))
        p('      · name 口径（文件名标称）实测 %.3f%%  ⇒ 偏差 %+.3f pp  %s'
          % (g, g - 77.20, '✓' if abs(g - 77.20) < 1.0 else '✗ 偏设计'))
        good = (abs(g2 - 77.20) < 0.5) and (abs(g - 77.20) < 1.0)
        ok &= good
        p('      ⇒ %s（★ 现场判读以 **col2 口径** 为准：不受源幅度设错、也不受抽取共模偏差影响）'
          % ('✓ 通过' if good else '**失败 ✗**'))
    p('')
    p('  ⇒ 自测 %s' % ('通过 ✓（脚本可信，明天可直接用）' if ok else '**失败 ✗**'))
    # 🛑 必须清掉：否则明天的 `fr-*.txt` 通配会把伪数据一起抓进来
    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)
    p('  （已清掉临时伪数据目录 %s，避免污染 fr-*.txt 通配）' % os.path.basename(tmp))
    return ok


def _sim_chain(ab, mode, x):
    """DF2T 逐拍复算「dc@0.02 + 1×LP」（notch OFF）。返回滤波输出（code）。"""
    fc = DC_FC_DIAG
    r = 1.0 - 2.0 * np.pi * fc / FS
    xp, yp = 0.0, 0.0
    sos = chain_sos(ab, mode)[15:]
    st = [[0.0, 0.0] for _ in sos]
    out = np.zeros(len(x))
    for i, xv in enumerate(x):
        y = xv - xp + r * yp
        xp, yp = xv, y
        for k, (b0, b1, b2, a1, a2) in enumerate(sos):
            d1, d2 = st[k]
            o = b0 * y + d1
            d1 = b1 * y - a1 * o + d2
            d2 = b2 * y - a2 * o
            st[k] = [d1, d2]
            y = o
        out[i] = y
    return out


# --------------------------------------------------------------- 判读主流程
def measure(files, mode, ref=5.0, amp=None, col=5, burn=1.0, quiet=False, fs_override=None,
            norm='name', afe=True):
    ab = read_ab_sos()
    dc_fc = read_ab_dc_fc()
    recs = []
    for path in files:
        f, fa = parse_name(path)
        if f is None:
            p('  !! 跳过（文件名无频率）：%s' % os.path.basename(path))
            continue
        x, nbad, nrow = load_txt(path, col=col)
        if len(x) < 100:
            p('  !! 跳过（有效样本不足 %d）：%s' % (len(x), os.path.basename(path)))
            continue
        rr = row_rate_of(path)
        # fs 优先级：显式 --fs > **500 标称**（raw853 on 时必须显式给 853.2423）
        # 🛑 绝不能拿列③ 反算值当 fs！（T92 实测纠正）
        #   丢行会让反算值**系统性偏低**（本例 10 Hz 数据 497.686、4 Hz 数据 499.563，真值都是 500），
        #   而列③ 里留下的每个样本**仍代表 1/500 s** ⇒ 用偏低 fs 做 DFT = 假设了错误的采样率，
        #   相位误差随 f·N 累积 ⇒ **高频点幅度被大幅低估**（实测 10 Hz 低估 26%，40 Hz 更甚）。
        #   列③ 反算只作**丢行体检**（下方打印），不参与判读。
        fs_used = fs_override if fs_override else FS_TXT_500
        if rr is not None and abs(rr - FS_TXT_500) > 1.0:
            p('  ⚠ 行率反算 %.2f Hz ≠ %.1f ⇒ 存在丢行（仅体检；DFT 仍按 %.1f 读）'
              % (rr, FS_TXT_500, FS_TXT_500))
        nb = int(burn * fs_used)
        if nb < len(x) - 100:
            x = x[nb:]
        x = x - x.mean()
        fa = fa if fa is not None else amp
        # ★ 先扫峰求**真实频率**再读幅（否则源频偏 ⇒ DFT 泄漏低估；T94 坑 #14 实证 +0.53% ⇒ −22%）
        f_real, f_ok = scan_peak_freq(x, fs_used, f)
        f_use = f_real if f_ok else f
        a = tone_amp(x, f_use, fs=fs_used)    # 单位：code（峰）
        # ★ 列②（链前 raw24b）同频幅度 —— 用来自证「实际输入幅度」，与文件名标称对账。
        #   真实 txt 的列② = VCM 偏置 + 输入正弦 ⇒ 减均值后即为输入（与列⑤ 同量纲）。
        #   🛑 列② 必须与列⑤ **用同一个 f_use** 读 ⇒ 泄漏/频偏在比值里共模抵消。
        a_in = float('nan')
        x2, _, _ = load_txt(path, col=2)
        if nb < len(x2) - 100:
            x2 = x2[nb:]
        if len(x2) > 100:
            x2 = x2 - x2.mean()
            if len(x2) > 100:
                a_in = tone_amp(x2, f_use, fs=fs_used)
        recs.append({'f': f, 'f_real': f_use, 'f_ok': f_ok,
                     'amp_mVpp': fa, 'a': a, 'n': len(x), 'a_in': a_in,
                     # ★ 列② 取自 **AFE 之后**（ecgraw）⇒「列② code × UV_PER_CODE」得到的是
                     #   输入幅 **再乘 AFE(f)**。要还原"实际输入幅度"必须 **除 afe_gain(f)**。
                     #   🛑 不除 ⇒ 高频点假报"源设错"：本例 150 Hz 显示 −30%，除完只差 −9%（T97 坑）。
                     'in_mVpp': (2.0 * a_in * UV_PER_CODE / float(afe_gain(f)[0]) / 1000.0)
                                if a_in == a_in else float('nan'),
                     'bad': nbad, 'rows': nrow, 'fs': fs_used,
                     'file': os.path.basename(path)})
    if not recs:
        p('  无可用数据')
        return []

    # 归一：a_norm = 列⑤幅度 / 输入幅度（输入幅度的取法由 norm 决定）
    #   norm='name'（默认）：用**文件名标称**幅度换算 —— 标称错了结果就错（现场源设错很常见）
    #   norm='col2'        ：用**列② 实测链前**幅度 ⇒ 得到真传递比，源设错也不影响
    # ★T96b(2026-09-22)：col2 默认**折进 AFE 因子**，使其与「预期(整机)」栏**同口径可比**。
    #   起因（T94 坑 #13）：列② = ecgraw 位于 **AFE 之后、固件滤波链之前**，而列⑤ 在固件链之后
    #   ⇒ 裸 列⑤/列② 只是「**固件链**传递比」，40 Hz 处比整机**高 1.34 pp**（78.54% vs 77.20%），
    #   会被 ±1.0 pp 的**回归判据**误报成问题（**不是超国标限**，见技能库术语纪律）。
    #   AFE 因子**只依赖频率**、与源幅度无关 ⇒ 乘上后仍免疫「源幅度设错」这一核心优点。
    #   --afe off ⇒ 退回固件链口径（复算 T94 历史数据／单独分离 AFE 贡献时用）。
    _kref = float(afe_gain(ref)[0])
    for r in recs:
        if norm == 'col2' and r['a_in'] == r['a_in'] and r['a_in'] > 1e-9:
            k = (float(afe_gain(r['f'])[0]) / _kref) if afe else 1.0
            r['a_norm'] = r['a'] / r['a_in'] * k
        elif r['amp_mVpp']:
            r['a_norm'] = r['a'] / (r['amp_mVpp'] * 1000.0 / 2.0 * UV_PER_CODE)
        else:
            r['a_norm'] = r['a']

    # 参考点
    refs = [r for r in recs if abs(r['f'] - ref) < 1e-9]
    if not refs:
        p('  ⚠ 数据里没有参考点 %g Hz ⇒ 判读缺分母，请补抓该点' % ref)
        return []
    r0 = refs[0]
    rows = []
    if not quiet:
        p('  数据：%d 个文件｜列%d｜参考 %g Hz＝%.4f（code，已按输入幅度归一）｜归一口径＝%s'
          % (len(recs), col, ref, r0['a_norm'],
             ('列⑤/列②×AFE（**整机**传递比）' if afe else '列⑤/列②（**固件链**传递比，未折 AFE）')
             if norm == 'col2' else '文件名标称幅度'))
        nb = sum(r['bad'] for r in recs)
        p('  坏行（列数≠7 或列②越 24 bit）：%d 行 / 共 %d 行  %s'
          % (nb, sum(r['rows'] for r in recs),
             '✓ 无' if nb == 0 else '⚠ 已自动剔除（检丢行/粘连用）'))
        for r in recs:
            if r['bad']:
                p('     - %s：坏 %d 行，有效 %d' % (r['file'], r['bad'], r['n']))
        p('')
        p('  %-7s | %9s | %10s | %12s | %9s | %10s | %9s | %s'
          % ('f(Hz)', '实测f', '实测%', '预期(整机)%', '偏差pp', '列②自证', 'vs标称', '判决'))
        p('  ' + '-' * 120)
    for r in sorted(recs, key=lambda z: z['f']):
        got = r['a_norm'] / r0['a_norm'] * 100.0
        exp = float(predict(ab, mode, r['f'], ref, with_afe=True, notch_off=True, dc_fc=dc_fc)[0])
        d = got - exp
        verdict = ''
        if mode == 'diag':
            # 诊断档判据 = GB 9706.225-2021 表 201.107（相对 10 Hz、按行归一、限 70~110%）
            row = t107_row(r['f'])
            if row:
                amp_req, lo, hi = row
                verdict = '225表201.107[%g,%g] %s' % (lo, hi, win_verdict(got, lo, hi))
                if r['f'] > 40.0:
                    same = (r['amp_mVpp'] is not None
                            and abs(r['amp_mVpp'] - amp_req) < 1e-6)
                    verdict += '（该行输入 %g mV%s）' % (
                        amp_req, '' if same else '；本次 %.4g ⚠ 不符标准行' % (r['amp_mVpp'] or 0))
                if abs(r['f'] - 10.0) < 0.02:
                    verdict += '（10 Hz＝参考点，恒 100%）'
            else:
                verdict = '（表 201.107 只到 150 Hz ⇒ 仅参考）'
        elif is_b_freq(r['f']):
            v27 = win_verdict(got, WIN_227[0], WIN_227[1])
            v47 = win_verdict(got, WIN_247[0], WIN_247[1])
            verdict = '2-27[71,110] %s ｜ 2-47[70,140] %s' % (v27, v47)
            if abs(r['f'] - 5.0) < 0.02:
                verdict += '（5 Hz＝参考点，恒 100%）'
        else:
            verdict = '（非原文 b) 判据频点，仅供参考）'
        rows.append((r['f'], got, exp, d, verdict))
        if not quiet:
            im = r['in_mVpp']
            vs = ('%+7.1f%%' % ((im / r['amp_mVpp'] - 1.0) * 100.0)) \
                if (im == im and r['amp_mVpp']) else '      —'
            p('  %-7g | %9.4f | %10.3f | %12.3f | %+9.3f | %9.3f | %9s | %s'
              % (r['f'], r.get('f_real', r['f']), got, exp, d, im, vs, verdict))
    return rows


# --------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='监护/诊断运行期可选 —— 上板判读（T92）')
    ap.add_argument('files', nargs='*', help='实测 txt（支持通配；文件名须带 Hz 与 mVpp）')
    ap.add_argument('--mode', default='mon', choices=['mon', 'diag'],
                    help='预期用哪条链（mon=第15节LP2@45｜diag=LP2@250）')
    ap.add_argument('--ref', type=float, default=5.0, help='归一化参考频率（监护=5 Hz）')
    ap.add_argument('--col', type=int, default=5, help='信号列（1-based；默认 5=列⑤滤波后 code）')
    ap.add_argument('--amp', type=float, default=None, help='文件名无幅度时的统一下发值 (mVpp)')
    ap.add_argument('--norm', default='name', choices=['name', 'col2'],
                    help='归一方式：name=文件名标称幅度（默认；**源设错就会误判**）｜'
                         'col2=列②（AFE 后/固件链前）实测幅度 ⇒ 免疫源幅度设错；'
                         '默认再折 AFE ⇒ **整机**传递比（是否折由 --afe 控制）')
    ap.add_argument('--afe', default='on', choices=['on', 'off'],
                    help='col2 口径是否折进 AFE 因子：on（默认）⇒ **整机**传递比，与"预期(整机)"栏可比｜'
                         'off ⇒ 退回**固件链**口径（复算 T94 历史数据／分离 AFE 贡献用；'
                         '40 Hz 会比整机**高 1.34 pp**，别拿它对照标准判据）')
    ap.add_argument('--burn', type=float, default=1.0, help='丢弃开头秒数（默认 1.0）')
    ap.add_argument('--fs', type=float, default=None,
                    help='采样/行率覆盖（Hz）。默认用列③反算（≈500）；raw853 on 时必须给 853.2423')
    ap.add_argument('--notch', default='off', choices=['off', 'on'],
                    help='实测时的 notch 状态（**仅用于标注与告警**，判据按 off 口径）')
    ap.add_argument('--selftest', action='store_true', help='离线端到端自测（不需硬件）')
    args = ap.parse_args()

    ab = read_ab_sos()
    p('T92 监护/诊断判读 ｜ A+B 档直读：%d 节｜dc_block fc = %.3f Hz｜FS = %.4f Hz'
      % (len(ab), read_ab_dc_fc(), FS))
    p('口径：参考 %g Hz｜notch %s（判据按 OFF 口径；**40 Hz 报数须标注 notch 状态**）'
      % (args.ref, args.notch))
    if args.notch == 'on':
        k = notch_factor(ab, args.mode, 40.0)
        p('⚠ 实测为 notch ON ⇒ 40 Hz 会额外被压：折算因子 ON/OFF = %.5f（即比 OFF 低 %.2f pp）'
          % (k, (1 - k) * 100))
    # 口径提醒：notch 因子
    for f in (0.67, 5.0, 40.0):
        p('   notch 折算因子 @ %-6g Hz = %.6f' % (f, notch_factor(ab, args.mode, f)))
    p('')

    if args.selftest:
        ok = selftest(ab)
        io.open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(_buf) + '\n')
        print('\n[留痕] %s' % OUT)
        return 0 if ok else 1

    files = []
    for pat in args.files:
        hit = glob.glob(pat)
        files.extend(hit if hit else [pat])
    if not files:
        p('未指定文件。用法：python tools/_t92_mon_measure.py "fr-*.txt" --mode mon')
        p('      或先跑自测：python tools/_t92_mon_measure.py --selftest')
        return 1

    rows = measure(files, args.mode, ref=args.ref, amp=args.amp,
                   col=args.col, burn=args.burn, fs_override=args.fs, norm=args.norm,
                   afe=(args.afe == 'on'))
    if rows:
        p('')
        if args.mode == 'diag':
            nd = [r for r in rows if '✗' in r[4]]
            p('⇒ 判据（**诊断档**）＝ GB 9706.225-2021 **表 201.107**'
              '（对 %g Hz 归一；判限一律 70~110%% ＝ +10%%/−30%%）' % args.ref)
            p('    · 行：0.67~40 Hz（输入 1 mV）｜40~100 Hz（0.5 mV）'
              '｜**100~150 Hz（0.25 mV）＝ 唯一贴限行**')
            p('    · 本次：%s' % ('全部通过 ✓' if not nd else '**不通过 ✗**：%s'
                                   % ', '.join('%g Hz' % r[0] for r in nd)))
            p('   提醒：① 🛑 **必须 notch OFF**（否则 150 Hz 被陷波簇打死 ⇒ 假失败）'
              ' ② 参考点＝10 Hz ③ 其余点超出表 201.107 ⇒ 仅参考')
        else:
            n27 = [r for r in rows if '2-27[71,110] ✗' in r[4]]
            n47 = [r for r in rows if '2-47[70,140] ✗' in r[4]]
            p('⇒ 判据 b)（对 %g Hz 归一；★ 原文七频点 0.67/1/2/5/10/20/40 Hz；★ 两窗并列）：'
              % args.ref)
            p('    · 2-27 [71,110]（GB 9706.227 监护）  ：%s'
              % ('全部通过 ✓' if not n27 else '**不通过 ✗**：%s'
                 % ', '.join('%g Hz' % r[0] for r in n27)))
            p('    · 2-47 [70,140]（YY 9706.247 · 108 b)）：%s'
              % ('全部通过 ✓' if not n47 else '**不通过 ✗**：%s'
                 % ', '.join('%g Hz' % r[0] for r in n47)))
        p('   提醒：① 必须 notch OFF ② 报数须标注 notch 状态 ③ 本判读用列⑤、500 域即可')
        p('   ④ 🛑 判据 a)（3 mV/100 ms 方波）是**并列必过项**，不在此表 ⇒ 见 `_t96_two_window.py`')
    io.open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(_buf) + '\n')
    print('\n[留痕] %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
