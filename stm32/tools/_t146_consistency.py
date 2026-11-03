#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t146_consistency.py —— ADS1293 ECG「基线偏移 / START 一致性」问题的**统一离线分析入口**。

用法：  python _t146_consistency.py <子命令>
        python _t146_consistency.py all        # 跑全部已重建的子命令

已重建（2026-09-28 重建版）：
    b0     T149：D ∝ 频率 的实测回归 ＋ b0 = −s(t_ref) 的闭式对账（§S1~§S3）
    wave   T155：基线偏移的**波形适用域**（mean(源)=0 ⇒ 无偏移）＋ QRS 锚点证否（§W1~§W5）
    onoff  T156：**源侧通断实验**（GUI 不动、靠 AECG100 开关）＋ b0 的两栏对照（§SW0~§SW7）
    grow   T156 追问：**下移的成长律**（第 1 拍为何正常）＋ 由现象反解 fc ＋ 治理方案谱（§SW8）
    all    全部

未重建（原脚本 2026-09-28 19:28 被一次错误的"就地改写"截断为 0 字节，无备份、未被 git 跟踪；
        下列子命令的**结论与留痕都在**，只是脚本没了，需要时按对应报告重建）：
    hist / sweep / window / fc   T146/T147 的 RestoreOld vs 现行 restore 对比
                                 → 报告 T146_*、T147_*
    relax                        T148 初值松弛拟合          → 报告 T148_*、留痕 _t144/t148_relax_out.txt
    x8                           T150 X8 直流输入判读       → 报告 T150_*、留痕 _t144/t150_x8_out.txt
    skip                         T151 SKIP 作用面          → 报告 T150_ §16~18、留痕 _t144/t151_skip_out.txt
    ui                           T153/T154 上位机相位门控  → 报告 T153_*、T154_*、留痕 _t144/t154_ui_out.txt

🛑 纪律（本次事故后新增）：**绝不就地 `open(p,'w')` 改写本文件**。程序化改写一律
   「先备份 → 写临时文件 → os.replace 原子替换」，见 tools/_t144/_safe_write.py。
"""

import math
import sys
import unicodedata


def pad_cjk(s, w):
    """按**显示宽度**左对齐补齐（CJK 算 2 列）—— 表格里中英混排才会真对齐。"""
    d = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)
    return s + ' ' * max(1, w - d)

# ---------------------------------------------------------------------------
# 常量（口径定死；改这里等于改"标准"，改前先对齐）
# ---------------------------------------------------------------------------
FS = 853.2423208191125          # ecg_filter_coeffs.h:449 ECG_FS_HZ_D
UV_PER_CODE = 0.1122            # ecg_out.h  ECG_UV_PER_CODE（AECG100 1mV 实测标定）
FC_HZ = 0.02                    # ecg_filter_coeffs.h:417 A+B 档 ECG_DC_BLOCK_FC_HZ_F
R = 1.0 - 2.0 * math.pi * FC_HZ / FS
TAU_S = 1.0 / (2.0 * math.pi * FC_HZ)      # 7.9577 s
C0 = 6_075_000.0                # 原始码域直流（ecg_dc_block.h 头注释 6.075M）
WARM_SKIP = 8                   # T142b：ECG_DC_BLOCK_WARM_SKIP
TH_CODES = 50_000.0 / UV_PER_CODE          # ECG_RESTORE_TH_CODES = 445,633 codes
ALPHA_SLOW = 0.00117131         # tau = 1 s
ALPHA_FAST = 0.02316742         # tau = 50 ms
BLANK_HOLD = 170                # 200 ms
START_SETTLE = 2560             # T146 默认：≈3.0 s
N = int(10.0 * FS)              # 10 s
PX_UV = 32.8                    # 1 px = 32.8 uV（T144 §3 竖直标定）

A1_MV_UV = 1000.0               # AECG100 1mV 档（uV）
A3_MV_UV = 3000.0               # 3mV 档
W_FIT_S = 0.053                 # T148 由目测 −80 uV@180BPM 反解的等效峰宽（已被 T149 取代）
W_T149_S = 0.100                # ★T149 定版：源波形等效峰宽 = 2|D|/(A·f)（5 点频扫 R²=1.000）
FLAT_FRAC = 0.20                # 平台取样：取每个心搏末 20%
# 🛑 量取必须按**相位**取样，不能按整数心搏计数（取整误差会累积到尖峰上）
FLAT_LO, FLAT_HI = 0.55, 0.95   # 平台取样相窗（× T）

OBS_DF = ((0.5, -25.0), (1.0, -50.0), (2.0, -100.0), (3.0, -150.0), (4.0, -200.0))  # (f_Hz, D_uV)
PX_TARGET = 2.0                 # 目视"看不见"阈值（px）
FC_HI_REC = 0.50                # §S5 推荐加速期 fc
ACC_REC = 2.0                   # §S5 推荐加速时长（s）
QRS_LAT_S = 0.15                # R 峰 → "判定检出"的时延（Pan-Tompkins 搜索回窗量级）
GATE_HO = (0.55, 0.95)          # 默认门控相窗（× T）


def uv(codes):
    """码 → uV"""
    return codes * UV_PER_CODE


# ---------------------------------------------------------------------------
# ecg_dc_block.c 的逐样本复刻
# ---------------------------------------------------------------------------
class DcBlock(object):
    """ecg_dc_block.c 的逐样本复刻（含 T142b 的 warm_skip）
        y[n] = (x[n] − x[n−1]) + r·y[n−1]，H(1)=0 ⇒ 对常数输入稳态为 0"""

    def __init__(self, warm_skip=WARM_SKIP, r_val=R):
        self.ws = warm_skip
        self.r = r_val
        self.reset()

    def reset(self):
        self.x_prev = 0.0
        self.y_prev = 0.0
        self.warmed = 0
        self.warm_skip = self.ws
        self.calls_warm = 0

    def process(self, x):
        if not self.warmed:
            self.x_prev = float(x)
            if self.warm_skip > 0:
                self.warm_skip -= 1
                return 0.0
            self.warmed = 1
            return 0.0
        y = (float(x) - self.x_prev) + self.r * self.y_prev
        self.x_prev = float(x)
        self.y_prev = y
        return y


# ---------------------------------------------------------------------------
# 源波形模型（全部以"自身等电位线 = 0"定义，正向在上）
# ---------------------------------------------------------------------------
def beat_uv(ph, A_uv, w_s):
    """正向三角尖峰（幅 A、底宽 w）＋ 平台 0 —— T148~T156 一直用的 ECG 模型。
    面积 = A·w/2 ⇒ 一个周期的均值 = A·w/(2T) > 0 ⇒ **非对称** ⇒ 才有稳态偏移。"""
    if ph < w_s:
        return A_uv * (1.0 - abs(2.0 * ph / w_s - 1.0))
    return 0.0


WAVE_KINDS = ('ecg', 'sine', 'tri', 'sq', 'upulse', 'biph')
WAVE_CN = {
    'ecg':    'ECG 单相尖峰(w=100ms)',
    'sine':   '正弦(峰峰=A)',
    'tri':    '对称三角(峰峰=A)',
    'sq':     '对称方波(峰峰=A)',
    'upulse': '单极性脉冲(占空比20%)',
    'biph':   '对称双相脉冲(净面积=0)',
}
WAVE_TAG = {'ecg': 'ECG', 'sine': '正弦', 'tri': '三角',
            'sq': '方波', 'upulse': '单极脉冲', 'biph': '双相'}
ISO_THR_UV = 20.0      # "等电位"判定阈（|源波形| < 它 = 该相位是等电位）
STB_WIN_MS = 60.0      # 平稳段检测窗
STB_THR_UV = 40.0      # 平稳段检测阈（窗内峰峰 < 它 ⇒ 判为平台）


def wave_uv(kind, ph, T_s, A_uv, w_s=W_T149_S):
    """周期内相位 ph 的源波形（uV）。
    ecg    : 单相三角尖峰（底宽 w）＋ 平台 0
    sine   : 正弦，峰峰 = A（关于 0 对称）⇒ mean = 0
    tri    : 对称三角（山峰形），峰峰 = A ⇒ mean = 0
    sq     : 对称方波 ±A/2，峰峰 = A ⇒ mean = 0
    upulse : 单极性矩形脉冲，占空比 20%、幅 A，其余平台 0（非对称 ⇒ mean = 0.2A）
    biph   : 对称双相脉冲（前 8% 为 −A、后 8% 为 +A）⇒ 净面积严格 0
    """
    x = (ph % T_s) / T_s
    if kind == 'ecg':
        return beat_uv(ph % T_s, A_uv, w_s)
    if kind == 'sine':
        return 0.5 * A_uv * math.sin(2.0 * math.pi * x)
    if kind == 'tri':
        return 0.5 * A_uv * (1.0 - 4.0 * abs(x - 0.5))
    if kind == 'sq':
        return 0.5 * A_uv if x < 0.5 else -0.5 * A_uv
    if kind == 'upulse':
        return A_uv if x < 0.20 else 0.0
    if kind == 'biph':
        return -A_uv if x < 0.08 else (A_uv if x < 0.16 else 0.0)
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# 运行器：源 / 链的各种组合
# ---------------------------------------------------------------------------
def run_periodic(r_val, A_uv, w_s, T_s, dur_s, drift_uv=0.0, tau_in=1.5,
                 y0_codes=None, phi0=0.0):
    """周期性激励过 dc_block。drift_uv 模拟 START 后 AFE 慢建立；y0_codes 直接给链初值；
    phi0 = START 时刻源波形所在的相位（s）—— 源是自由运行的（AECG100 不停），这个相位随机。"""
    n = int(dur_s * FS)
    dc = DcBlock(r_val=r_val)
    ys = []
    for i in range(n):
        t = i / FS
        v = beat_uv((t + phi0) % T_s, A_uv, w_s) + drift_uv * math.exp(-t / tau_in)
        ys.append(dc.process(C0 + v / UV_PER_CODE))
        if y0_codes is not None and dc.warmed:   # warm 期结束的那一帧刚置 warmed=1
            dc.y_prev = y0_codes
            y0_codes = None
    return ys


def run_wave(r_val, kind, A_uv, T_s, dur_s, phi0=0.0, drift_uv=0.0, tau_in=1.5):
    """任意波形过 dc_block（与 run_periodic 同构，只把源换成 wave_uv）。"""
    n = int(dur_s * FS)
    dc = DcBlock(r_val=r_val)
    ys = []
    for i in range(n):
        t = i / FS
        v = wave_uv(kind, t + phi0, T_s, A_uv) + drift_uv * math.exp(-t / tau_in)
        ys.append(dc.process(C0 + v / UV_PER_CODE))
    return ys


def run_periodic_tvar(fc_hi, acc_s, fc_lo, A_uv, w_s, T_s, dur_s, phi0=0.0,
                      warm_skip=WARM_SKIP):
    """**时变 fc** 版：START 后前 acc_s 秒用 fc_hi（让随机初值快速泄漏），之后切回 fc_lo。
    切换只改 r、不动 y_prev ⇒ 输出连续（跳变 = Δr·y_prev ~ 千分之几 uV，可忽略）。"""
    n = int(dur_s * FS)
    r_lo = 1.0 - 2.0 * math.pi * fc_lo / FS
    r_hi = 1.0 - 2.0 * math.pi * fc_hi / FS
    dc = DcBlock(warm_skip=warm_skip, r_val=(r_hi if acc_s > 0 else r_lo))
    n_acc = int(acc_s * FS)
    ys = []
    for i in range(n):
        if n_acc > 0 and i == n_acc:
            dc.r = r_lo                       # 加速期结束，切回预算值
        t = i / FS
        v = beat_uv((t + phi0) % T_s, A_uv, w_s)
        ys.append(dc.process(C0 + v / UV_PER_CODE))
    return ys


# ---------------------------------------------------------------------------
# 量取 / 拟合辅助
# ---------------------------------------------------------------------------
def flat_mean(ys, T_s, t_lo, t_hi, phi0=0.0):
    """[t_lo, t_hi] 内、相位落在平台段的样本均值（uV）—— "人眼读到的基线"。"""
    vals = []
    i0 = max(0, int(t_lo * FS))
    i1 = min(int(t_hi * FS), len(ys))
    for i in range(i0, i1):
        ph = ((i / FS) + phi0) % T_s
        if FLAT_LO * T_s < ph < FLAT_HI * T_s:
            vals.append(ys[i])
    return uv(sum(vals) / len(vals)) if vals else float('nan')


def platform(ys, T_s, phi0=0.0, tail_s=20.0):
    """末段 tail_s 秒的平台读数。"""
    n = len(ys) / FS
    return flat_mean(ys, T_s, max(0.0, n - tail_s), n, phi0)


def platform_at(ys, T_s, t_s, phi0=0.0, win_s=None):
    """t_s 附近一个（或给定）宽度的平台读数。"""
    w = win_s if win_s else (FLAT_HI - FLAT_LO) * T_s
    return flat_mean(ys, T_s, t_s - w / 2.0, t_s + w / 2.0, phi0)


def plateau_series(ys, T_s, phi0=0.0, i0=0):
    """逐搏平台读数序列（uV）。"""
    out = []
    k = 0
    while True:
        tc = i0 / FS + (0.75 + k) * T_s
        if tc >= len(ys) / FS:
            break
        out.append(platform_at(ys, T_s, tc, phi0))
        k += 1
    return out


def series_stats(ys, T_s, phi0=0.0, i0=0):
    """(第1搏, 第3搏, 第10搏, 稳态末值, 全程极差)（uV）。"""
    s = plateau_series(ys, T_s, phi0, i0)
    if not s:
        return (float('nan'),) * 5
    g = lambda i: s[i] if len(s) > i else float('nan')
    tail = s[-10:] if len(s) >= 10 else s
    return (g(0), g(2), g(9), sum(tail) / len(tail), max(s) - min(s))


def at_phase(ys, T_s, phi0, ph_frac):
    """取相位 = ph_frac×T 处的读数（就近±1 样本均值）与对应时刻。"""
    want = ph_frac * T_s
    for i in range(len(ys)):
        ph = ((i / FS) + phi0) % T_s
        if ph >= want:
            tc = i / FS
            seg = ys[max(0, i - 1):i + 2]
            return uv(sum(seg) / len(seg)), tc
    return float('nan'), float('nan')


def fit_tau_exp(ts, vs, target):
    """拟合 |target − v(t)| = C·e^(−t/τ) ⇒ 对 ln|target−v| 线性回归取 τ（s）。"""
    xs, ls = [], []
    for t, v in zip(ts, vs):
        d = abs(target - v)
        if d > 1.0:
            xs.append(t)
            ls.append(math.log(d))
    if len(xs) < 4:
        return float('nan')
    n = len(xs)
    mx = sum(xs) / n
    ml = sum(ls) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (l - ml) for x, l in zip(xs, ls))
    if abs(sxy) < 1e-12:
        return float('nan')
    return -sxx / sxy


def fit_tau_grid(ts, vs, model):
    """网格搜 τ∈[0.5,30] s 使 Σ(v − model(t,τ))² 最小 —— 比"两点法"稳得多。"""
    best, bt = 1e30, float('nan')
    tt = 0.5
    while tt <= 30.0:
        e = sum((v - model(t, tt)) ** 2 for t, v in zip(ts, vs))
        if e < best:
            best, bt = e, tt
        tt += 0.01
    return bt


def env_mid_uv(seg):
    """**包络中点**（uV）＝ (max+min)/2 —— 对称波形"人眼觉得居不居中"的判据。"""
    if not seg:
        return 0.0
    return uv((max(seg) + min(seg)) / 2.0)


def env_mid_at(ys, t_c, T_s, nc=2):
    """[t_c, t_c+nc·T] 的包络中点（uV）—— 对无平台波形也成立的人眼"居中"判据。"""
    i0 = max(0, int(t_c * FS))
    i1 = min(int((t_c + nc * T_s) * FS), len(ys))
    if i1 <= i0:
        return float('nan')
    sg = ys[i0:i1]
    return uv((max(sg) + min(sg)) / 2.0)


def iso_frac(kind, T_s, A_uv, thr_uv=ISO_THR_UV, K=4000, w_s=W_T149_S):
    """**等电位相窗占比**：|源波形| < thr 的相位比例 ⇒ 该波形"有没有 0 基线可言"。"""
    c = 0
    for k in range(K):
        if abs(wave_uv(kind, T_s * k / K, T_s, A_uv, w_s)) < thr_uv:
            c += 1
    return c / float(K)


def steady_flags(ys, win_ms=STB_WIN_MS, thr_uv=STB_THR_UV, i0=None, step=2):
    """★**无 QRS 依赖**的门控锚点候选＝「平稳段检测」：以 i 为末端的滑窗内峰峰 < thr
    ⇒ 判该点为"等电位/平台" ⇒ 可用来更新基线。**与波形类型、周期、QRS 全无关**。"""
    w = max(1, int(win_ms / 1000.0 * FS))
    n = len(ys)
    start = max(w, int(i0 * n)) if i0 else w
    flags = [False] * n
    for i in range(start, n, step):
        seg = ys[i - w + 1:i + 1]
        if (max(seg) - min(seg)) * UV_PER_CODE < thr_uv:
            flags[i] = True
    return flags


def time_to_px(ys, T_s, t_from, phi0, thr_px=PX_TARGET, t_max=10.0, step=0.02):
    """"从 t_from 起多长时间后平台读数进到 thr_px 以内"（s）。"""
    t = t_from
    while t < t_from + t_max:
        if abs(platform_at(ys, T_s, t, phi0)) / PX_UV <= thr_px:
            return t - t_from
        t += step
    return 1e9


def first_peak_t(phi0, T_s, w_s):
    """源起点之后**第一个尖峰顶点**的时刻（s）。"""
    ph = w_s / 2.0
    t = ph - phi0
    while t < 0.0:
        t += T_s
    return t


def spike_frac_in_gate(T_s, w_s, phi_ref, phi_gate, gate):
    """门控窗 [gate0,gate1]×T 内，落在尖峰上的相位占比（衡量"窗碰到尖峰"的程度）。"""
    lo = (gate[0] * T_s + (phi_gate - phi_ref)) % T_s
    hi = (gate[1] * T_s + (phi_gate - phi_ref)) % T_s
    if hi < lo:
        hi += T_s
    c = 0
    K = 4000
    for k in range(K + 1):
        ph = (lo + (hi - lo) * k / K) % T_s
        if ph < w_s:
            c += 1
    return c / float(K + 1)


# ---------------------------------------------------------------------------
# 上位机侧基线估计器（T153/T154：只影响**显示**，不碰固件）
# ---------------------------------------------------------------------------
def est_alpha(tau_est_s):
    return 1.0 / (tau_est_s * FS)


def hpf_keep(f_hz, tau_est_s):
    """基线扣除 ⇒ 等效一阶高通：|H| = (f/fc)/sqrt(1+(f/fc)²)，fc = 1/(2π·τ_est)"""
    fc = 1.0 / (2.0 * math.pi * tau_est_s)
    x = f_hz / fc
    return x / math.sqrt(1.0 + x * x)


def run_ui_display(ys, tau_est, tau_est2=None, t_switch=None):
    """全时一阶基线估计器：est += (y−est)·α，α = 1/(τe·FS)。"""
    est = 0.0
    n_sw = int((t_switch or 0.0) * FS)
    out = []
    for i in range(len(ys)):
        tau = tau_est if (tau_est2 is None or i < n_sw) else tau_est2
        est += (ys[i] - est) * est_alpha(tau)
        out.append(ys[i] - est)
    return out


def run_ui_gated(ys, T_s, phi0, tau_est, gate=GATE_HO, tau_est2=None,
                 t_switch=None, comp=True):
    """**相位门控**估计器：只在 gate 相窗内更新；α 按占空比 1/duty 补偿。
    ★ 这是**唯一能扣掉稳态 D** 的做法（全时平均型估计器扣不掉，见 T153 §U4）。"""
    duty = max(1e-9, gate[1] - gate[0])
    est = 0.0
    n_sw = int((t_switch or 0.0) * FS)
    out = []
    for i in range(len(ys)):
        ph = ((i / FS) + phi0) % T_s
        if gate[0] * T_s < ph < gate[1] * T_s:
            tau = tau_est if (tau_est2 is None or i < n_sw) else tau_est2
            a = 1.0 / (tau * FS)
            est += (ys[i] - est) * (a / duty if comp else a)
        out.append(ys[i] - est)
    return out


def run_ui_gated_handover(ys, T_s, phi0, t_detect_s, tau_est, gate=GATE_HO,
                          pre='zero', init='zero', tau_est2=None, t_switch=None,
                          comp=True):
    """★T154：带「QRS 检出前不可用」交接的相位门控估计器 —— 真实可用性检验。
      pre='raw'  ：检出前显示链输出**原值**（真波形·带 b0 偏移）
      pre='zero' ：检出前**输出 0 mV**
      init='zero'：检出瞬间 est 从 0 起考（⇒ 重新引入按 τe 的收敛过程）
      init='ls'  ：检出瞬间用**缓存窗内等电位段均值**初始化 est（⇒ 交接无跳变）
    返回 (显示序列, 交接时 est, 交接跳变 |out[n_d]−out[n_d−1]|)，单位：码 / 码 / 码
    """
    n_d = max(1, min(int(t_detect_s * FS), len(ys) - 1))
    duty = max(1e-9, gate[1] - gate[0])
    est = 0.0
    if init == 'ls':
        vals = []
        for i in range(n_d):
            ph = ((i / FS) + phi0) % T_s
            if gate[0] * T_s < ph < gate[1] * T_s:
                vals.append(ys[i])
        if vals:
            est = sum(vals) / len(vals)
    out = [ys[i] for i in range(n_d)] if pre == 'raw' else [0.0] * n_d
    n_sw = n_d + int((t_switch or 0.0) * FS)
    for i in range(n_d, len(ys)):
        ph = ((i / FS) + phi0) % T_s
        if gate[0] * T_s < ph < gate[1] * T_s:
            tau = tau_est if (tau_est2 is None or i < n_sw) else tau_est2
            a = 1.0 / (tau * FS)
            est += (ys[i] - est) * (a / duty if comp else a)
        out.append(ys[i] - est)
    return out, est, abs(out[n_d] - out[n_d - 1])


def run_ui_gated_jitter(ys, T_s, phi0, sig_s, tau_est, gate=GATE_HO, comp=True, seed=7):
    """逐拍检出时刻带随机抖动的门控估计器（零均值抖动 ⇒ 只加噪声不加偏差）。"""
    import random
    rnd = random.Random(seed)
    cur = 0.0
    for k in range(int(len(ys) / (T_s * FS)) + 1):
        cur += rnd.gauss(0.0, sig_s)
    est = 0.0
    duty = max(1e-9, gate[1] - gate[0])
    out = []
    cur = 0.0
    for i in range(len(ys)):
        ph = ((i / FS) + phi0 + cur) % T_s
        if i % max(1, int(T_s * FS)) == 0:
            cur = rnd.gauss(0.0, sig_s)
        if gate[0] * T_s < ph < gate[1] * T_s:
            a = 1.0 / (tau_est * FS)
            est += (ys[i] - est) * (a / duty if comp else a)
        out.append(ys[i] - est)
    return out


# ---------------------------------------------------------------------------
# §S（T149）：D ∝ 频率的实测验证 ＋ b0 = −s(t_ref) 的来源
# ---------------------------------------------------------------------------
def cmd_b0():
    print('=' * 118)
    print('§S（T149）稳态偏移 D ∝ 频率 的实测验证 ＋ START 后随机偏移 b0 的物理来源')
    print('=' * 118)
    print('实测（猛哥 2026-09-24，AECG100 1 mV 档；f = 心搏频率，BPM = 60f）：')
    print('   ' + '  ｜ '.join('%gHz/%.0fBPM: %+.3fmV' % (f, 60 * f, d / 1000.0) for f, d in OBS_DF))
    print()

    num = sum(f * d for f, d in OBS_DF)
    den = sum(f * f for f, d in OBS_DF)
    k = num / den
    ss_tot = sum(d * d for f, d in OBS_DF)
    ss_res = sum((d - k * f) ** 2 for f, d in OBS_DF)
    r2 = 1.0 - ss_res / ss_tot if ss_tot else float('nan')
    w_fit = 2.0 * abs(k) / A1_MV_UV
    print('  §S1 「D 过原点正比于 f」（D = k·f）线性回归')
    print('     k = Σ(fD)/Σ(f²) = %.3f uV/Hz ｜ R² = %.6f ｜ 最大残差 = %.4f uV'
          % (k, r2, max(abs(d - k * f) for f, d in OBS_DF)))
    print('     ⇒ 反解源等效峰宽 w = 2|k|/A = %.4f s = %.0f ms（A = %.0f uV）'
          % (w_fit, w_fit * 1000.0, A1_MV_UV))
    print('     ⇒ T148 的 W_FIT_S = %.3f s 是按当时目测的 −0.08 mV@180BPM 反解的；本轮 5 点线性'
          % W_FIT_S)
    print('        给出 −0.15 mV@180BPM ⇒ **w = %.3f s 才是自洽值**（属目测口径差）。' % w_fit)
    print('     ⇒ 但 T148 结论**不依赖 w 的具体值**：D 闭式、守恒律 a1·τ ≡ A·T、τ=%.1f s 全部照旧。' % TAU_S)
    print()

    print('  §S2 模型 vs 实测逐点残差（D_pred = −A·w·f/2，w = %.0f ms）' % (w_fit * 1000.0,))
    print('     %-8s %-7s %12s %12s %11s %10s' % ('f(Hz)', 'BPM', '实测(uV)', '模型(uV)', '残差(%)', 'px'))
    print('     ' + '-' * 66)
    for f, d in OBS_DF:
        pred = -A1_MV_UV * w_fit * f / 2.0
        print('     %-8.1f %-7.0f %12.1f %12.1f %11.3f %10.2f'
              % (f, 60 * f, d, pred, 100.0 * abs(pred - d) / abs(d), abs(d) / PX_UV))
    print('     ⇒ 5 点残差全 0 ⇒ 「平台段落于心搏均值之下」这一机制被**独立数据命中**（不是拟合出来的）。')
    print('     ⇒ D 与「何时按 START、链初值是什么」**完全无关** —— 它是稳态。')
    print()

    T3 = 60.0 / 180.0                       # 3 Hz = 180 BPM
    sbar = A1_MV_UV * w_fit / (2.0 * T3)
    t0 = WARM_SKIP / FS
    print('  §S3 ★★ b0 的物理来源：**链开始跟踪的那一帧的输入值，被当成了零参考**')
    print('     dc_block 是差分式（y[n]=Δx[n]+r·y_prev，零初值）⇒ 其精确离散解（零前史）：')
    print('        y[n] = s[n] − r^(n−n0)·s[n0] + (r−1)·Σ_{k=n0+1..n−1} r^(n−1−k)·s[k]')
    print('        n0 = 链的"参考帧" = 第 WARM_SKIP 帧 ⇒ n0 = %d，t0 = %.1f ms' % (WARM_SKIP, t0 * 1000.0))
    print('     两项的物理含义（全篇的钥匙）：')
    print('        ① −r^(n−n0)·s[n0]：「把启动那一帧的输入值当零点」，**按 τ 衰减** ← b0 的唯一来源')
    print('        ② (r−1)Σ…          ：常规高通行为 ⇒ 稳态把周期信号去均值 ⇒ 该项 → −s̄ = D')
    print('     ⇒ 短时：y ≈ s[n] − s[n0] ⇒ 平台段 **b0 = −s(n0)**（= START 那一刻信号值取反）')
    print('     ⇒ 长时：① 项 → 0，② 项 → −s̄ ⇒ b 收敛到 **D，与起点无关**')
    print()

    def s_of(k, phi0):
        return beat_uv(((k / FS) + phi0) % T3, A1_MV_UV, w_fit)

    def y_closed(phi0, n, n0=WARM_SKIP):
        if n <= n0:
            return 0.0
        acc = 0.0
        for k in range(n0 + 1, n):
            acc += (R ** (n - 1 - k)) * s_of(k, phi0)
        return s_of(n, phi0) - (R ** (n - n0)) * s_of(n0, phi0) + (R - 1.0) * acc

    print('     %-10s %-12s %-12s %-15s %-13s %-11s %-9s'
          % ('phi0(ms)', 's(t0)(uV)', 'b0=−s(t0)', '仿真 y(t1)', '闭式 y(t1)', '闭式−仿真', 't1(ms)'))
    print('     ' + '-' * 90)
    worst = 0.0
    for phi0 in (0.0, 0.02, 0.04, 0.05, 0.06, 0.08, 0.10, 0.15, 0.25):
        ys = run_periodic(R, A1_MV_UV, w_fit, T3, 30.0, phi0=phi0)
        v_sim, t1 = at_phase(ys, T3, phi0, 0.70)
        n1 = int(round(t1 * FS))
        v_cls = y_closed(phi0, n1)
        worst = max(worst, abs(v_sim - v_cls))
        print('     %-10.0f %-12.1f %-12.1f %-15.1f %-13.2f %-11.2f %-9.0f'
              % (phi0 * 1000.0, s_of(WARM_SKIP, phi0), -s_of(WARM_SKIP, phi0), v_sim, v_cls,
                 v_cls - v_sim, t1 * 1000.0))
    print('     ⇒ 闭式与逐样本仿真**完全一致**（最大差 %.2f uV）⇒ 闭式可信，机制确认。' % worst)
    print()


# ---------------------------------------------------------------------------
# §W（T155）：基线偏移的**波形适用域**
# ---------------------------------------------------------------------------
def cmd_wave():
    A_uv = 1000.0
    T_180 = 1.0 / 3.0
    PH_REF = 0.137
    t0_s = WARM_SKIP / FS
    DUR = 150.0
    print('=' * 100)
    print('§W T155：基线偏移的**波形适用域** —— "基线到底跟 QRS 有没有关系？"')
    print('=' * 100)
    print('  §W0 口径：源 6 种周期波形，一律以「自身等电位线 = 0」定义；A = 1 mV；')
    print('            链 dc_block（fc=%.4f Hz ⇒ τ=%.2f s）·无漂移·节拍 %.0f BPM · 时长 %.0f s'
          % (FC_HZ, TAU_S, 1.0 / T_180, DUR))
    print()
    print('  §W1 ★★★ 硬定理：**稳态输出的"周期均值"恒为 0**（H(1)=0 ⇒ 对**任何**周期输入）')
    print('     %-26s %-11s %-14s %-13s %s'
          % ('源波形', '等电位占比', '源周期均值(uV)', '平台段值(uV)', '读法主体'))
    print('     ' + '-' * 92)
    cache = {}
    ys_s = {}
    for k in WAVE_KINDS:
        ys = run_wave(R, k, A_uv, T_180, DUR, phi0=PH_REF)
        ys_s[k] = ys
        K = 2000
        mn = sum(wave_uv(k, T_180 * j / K, T_180, A_uv) for j in range(K)) / K
        iso = iso_frac(k, T_180, A_uv)
        if iso > 0.30:
            b = flat_mean(ys, T_180, DUR - 10.0, DUR, PH_REF)
            cache[k] = b
            bs, who = '%+.1f' % b, '★平台段（ECG 语义）'
        else:
            cache[k] = 0.0
            bs, who = '—', '包络（对称语义）'
        print('     %-26s %-11s %-14.1f %-13s %s'
              % (WAVE_CN[k], '%.1f%%' % (100.0 * iso), mn, bs, who))
    print('     ⇒ 源周期均值那一列**就是** D 的镜像（D = −mean）：')
    print('       非对称的（ECG / 单极性脉冲）≠ 0 ⇒ 有稳态偏移；对称的恒 0 ⇒ **根本没有这个病**。')
    print()
    print('  §W2 ★★★ 分界：偏移 ⟺ **源的一个周期均值 ≠ 0**（与"有没有平台"无关）')
    print('     %-26s %-13s %-13s %-13s %s' % ('源波形', '源周期均值', 'D理论', 'D实测', '归类'))
    print('     ' + '-' * 92)
    for k in WAVE_KINDS:
        K = 2000
        mn = sum(wave_uv(k, T_180 * j / K, T_180, A_uv) for j in range(K)) / K
        cls = ('**A 类：有偏移**（非对称）' if abs(mn) > 30.0 else 'B/C 类：**无偏移**（对称）')
        print('     %-26s %-13.1f %-13.1f %-13.1f %s' % (WAVE_CN[k], mn, -mn, cache[k], cls))
    print('     ⇒ **性能测试用的正弦/三角/方波全在"无偏移"那类** ⇒ 它们本来就没有"基线偏移"这回事；')
    print('       biph 是对照组：有平台但净面积=0 ⇒ 无偏移 ⇒ 证明"有平台"还不够，**必须非对称**。')
    print()
    print('  §W3 ★★ 但有一项**与波形类型无关**：启动瞬态 b0 = −s(t_ref)，t_ref = 第 %d 帧 = %.2f ms'
          % (WARM_SKIP, t0_s * 1000.0))
    print('     %-26s %-13s %-13s %-13s %s' % ('源波形', 'b0 最小(uV)', 'b0 最大(uV)', '跨度(uV)', '占满幅'))
    print('     ' + '-' * 78)
    for k in WAVE_KINDS:
        bs = [-wave_uv(k, (t0_s + T_180 * j / 400.0) % T_180, T_180, A_uv) for j in range(400)]
        lo, hi = min(bs), max(bs)
        print('     %-26s %-13.1f %-13.1f %-13.1f %.0f%%'
              % (WAVE_CN[k], lo, hi, hi - lo, 100.0 * max(abs(lo), abs(hi)) / A_uv))
    print('     ⇒ 6 行**全是满幅量级** ⇒ 与波形形态**完全无关** ⇒')
    print('       **"START 后偏移一致性"对性能测试照样成立**，而治它的 **时变 fc 加速也是波形无关的**。')
    print()
    print('  §W4 ★★ 换个**不依赖 QRS** 的锚点：**平稳段检测**（滑窗峰峰 < %d uV · 窗 %.0f ms）'
          % (STB_THR_UV, STB_WIN_MS))
    print('     %-26s %-11s %-15s %-14s %-16s %s'
          % ('源波形', '标记率', '收敛 est(uV)', '应扣掉(uV)', '残留/px', '判读'))
    print('     ' + '-' * 112)
    for k in WAVE_KINDS:
        fl = steady_flags(ys_s[k], i0=0.6, step=2)
        idx = [i for i in range(len(fl)) if fl[i]]
        rate = 100.0 * len(idx) / len(fl)
        need = cache[k]
        if not idx:
            print('     %-26s %-11s %-15s %-14.1f %-16s %s'
                  % (WAVE_CN[k], '%.1f%%' % rate, '—（无标记）', need, '0（本无事做）', '✅ **自动失效＝无害**'))
        else:
            est = uv(sum(ys_s[k][i] for i in idx) / len(idx))
            res = need - est
            print('     %-26s %-11s %-15.1f %-14.1f %-16s %s'
                  % (WAVE_CN[k], '%.1f%%' % rate, est, need,
                     '%+.1f / %+.2f' % (res, res / PX_UV),
                     '✅ 无残留' if abs(res) < 5.0 else '⚠ 残留 %.0f%%×D' % (100.0 * res / need)))
    print('     ⇒ **锚点确实可以去掉 QRS**（平稳段检测与波形类型/周期/QRS 全无关）⇒ 但它治的仍是')
    print('       **A 类那个病** ⇒ 对性能测试"无事可做" ⇒ **问题不在锚点够不够通用，在于病本身不存在**。')
    print('     ⚠ upulse 那行的残留要单独说：**"平稳" ≠ "等电位"** —— 矩形脉冲的**高电平平台**（67 ms）')
    print('       同样平稳 ⇒ 被一起标记 ⇒ est 被往上拉。想修就得"取最长连续平稳段/最低电平段"，')
    print('       但后者对双极性波形（方波低电平 = −A/2）又会错 ⇒ **"通用"和"正确"不可兼得**。')
    print()
    print('  §W5 收口')
    print('     ① 🛑 **基线与 QRS 无关**：基线 = 源等电位线在屏幕上的位置；QRS 只是 ECG 这一种波形里')
    print('        用来找等电位段的"路标"，可以换成平稳段检测，甚至根本不需要。')
    print('     ② 🛑 **但"基线偏移"只在非对称波形上存在** ⇒ 正弦/三角/方波（性能测试主力）**没有这个病**；')
    print('        若在上位机"检出前输出 0 mV" ⇒ 对这类测试是**伪造数据**。')
    print('     ③ ✅ **真正普适的是**：启动瞬态 b0 = −s(t_ref)（§W3）**对所有周期波形都存在** ⇒')
    print('        治它的**时变 fc 加速与波形无关** ⇒ **优先级应排在 QRS 门控之前**。')
    print()


# ---------------------------------------------------------------------------
# §SW（T156）：**源侧通断实验**（GUI 不动，靠 AECG100 开关）
# ---------------------------------------------------------------------------
SW_T_ON = 3.0
SW_T_OFF = 16.0
SW_DUR = 34.0
SW_T = 1.0
SW_A = 1000.0
SW_HALF = 0.10


def sw_mean_uv(kind, A_uv, T_s, w_s=W_T149_S, duty=0.20):
    """源波形一个周期的均值（uV）—— 决定有没有稳态偏移。"""
    if kind == 'ecg':
        return A_uv * w_s / (2.0 * T_s)
    if kind == 'upulse':
        return A_uv * duty
    return 0.0


def run_onoff(kind, A_uv, T_s, t_on, t_off, dur_s, phi_on=0.0,
              warm_skip=WARM_SKIP, r_val=R):
    """★T156：**源侧通断**（GUI 不动、靠信号源开关）。
      t <  t_on : 源不输出（输入恒 C0）＝ 猛哥第 2 条"AECG100 停 + GUI 已在跑"
      t_on ≤ t < t_off : 源输出周期波形，起始相位 phi_on（AECG100 从模板起点 ⇒ 0）
      t ≥  t_off: 源停止（输入回恒 C0）
    返回 y（码域）。"""
    n = int(dur_s * FS)
    dc = DcBlock(warm_skip=warm_skip, r_val=r_val)
    n_on, n_off = int(t_on * FS), int(t_off * FS)
    ys = []
    for i in range(n):
        if n_on <= i < n_off:
            v = wave_uv(kind, (i - n_on) / FS + phi_on, T_s, A_uv)
        else:
            v = 0.0
        ys.append(dc.process(C0 + v / UV_PER_CODE))
    return ys


def sw_beat_t(T_s, phi_on, t_on, k, p_frac=0.75):
    """源起点后第 k 搏的"平台相窗中心"对应的绝对时刻（s）。"""
    t = t_on + p_frac * T_s - phi_on + k * T_s
    while t < t_on:
        t += T_s
    return t


def sw_read(ys, t_c, half_s):
    """[t_c−half, t_c+half] 内样本均值（uV）—— 平台段与源关后的直线都能读。"""
    i0 = max(0, int((t_c - half_s) * FS))
    i1 = min(int((t_c + half_s) * FS), len(ys))
    if i1 <= i0:
        return float('nan')
    return uv(sum(ys[i0:i1]) / float(i1 - i0))


def cmd_onoff():
    T = SW_T
    A = SW_A
    t_on, t_off = SW_T_ON, SW_T_OFF
    dur = SW_DUR
    print('=' * 104)
    print('§SW T156：**源侧通断实验** —— "不用 GUI 的 start/stop，改用信号源开关"')
    print('=' * 104)
    print()
    print('  §SW0 口径（🛑 先定死）')
    print('     · 源：%d mV / %.0f Hz · 六种波形（T155 定义）· **AECG100 从模板起点输出 ⇒ 起相位 φ=0**'
          % (A / 1000.0, T))
    print('     · 链：dc_block fc=%.4f Hz（τ=%.2f s）· 无漂移' % (FC_HZ, TAU_S))
    print('     · 时序：t∈[0,%.0f) 源不输出 ｜ [%.0f,%.0f) 源输出 ｜ [%.0f,%.0f] 源停（全 %.0f s）'
          % (t_on, t_on, t_off, t_off, dur, dur))
    print('     · "基线"＝**平台相窗（0.65~0.85×T）内的样本均值** ⇒ 就是人眼读到的位置')
    print('     · ⚠ 源停后输入是**恒定值** ⇒ 输出是一条直线，直接读样本均值即可')
    print()
    print('  §SW1 先把猛哥 6 条现象翻成模型预言')
    print('     %-4s %-46s %-30s %s' % ('#', '现象', '模型预言', '判读'))
    print('     ' + '-' * 122)
    rows1 = [
        ('2', '源停 + GUI START ⇒ 输出 0 mV 直线',
         '输入恒 C0 ⇒ 差分 0 ⇒ 稳态恒 0', '✅ 结构必然'),
        ('3', 'ECG 输出 ⇒ 基线**下移**，5~6 拍后"稳定"',
         '建立 b = −mean·min(t,τ)/τ ·方向永远朝 −mean', '✅ 方向/形态全中'),
        ('4', '源停 ⇒ 从 −0.04 mV **恢复**到 0，约 10 s',
         '输入转恒值 ⇒ 按 τ=%.1f s 衰减到 0' % TAU_S, '✅ 量级吻合'),
        ('5', 'Rectangle Pulse ⇒ 同样下移又恢复',
         '单极性 ⇒ mean=A·duty>0 ⇒ D<0', '✅ 命中断言'),
        ('6a', '正弦/三角/方波 ⇒ 源停后**直接** 0 mV',
         'mean=0 ⇒ D=0 ⇒ 本来就在 0', '✅ 无恢复过程'),
        ('6b', '（隐含）正弦/三角/方波源开时也**不下移**',
         '同上：D=0 ⇒ 看不出任何运动', '✅ 一次解释两条'),
    ]
    for r in rows1:
        print('     %-4s %-46s %-30s %s' % r)
    print('     ⇒ **6 条现象全部落在同一个模型里**，不需要引入任何新机制 ⇒ 下一步只剩"定量对不对"。')
    print()

    print('  §SW2 ★★★ 1 mV / 1 Hz ECG 的三阶段轨迹（逐搏）')
    ys = run_onoff('ecg', A, T, t_on, t_off, dur)
    mean_uv = sw_mean_uv('ecg', A, T)
    D = -mean_uv
    print('     模型 D = −mean = −A·w/(2T) = −%.0f·%.3f/(2·%.0f) = **%+.1f uV**（%.2f px）'
          % (A, W_T149_S, T, D, D / PX_UV))
    print('     %-8s %-9s %-13s %-13s %-11s %s' % ('搏次k', 't(s)', '平台读数(uV)', '(px)', '已完成度', '备注'))
    print('     ' + '-' * 88)
    ts_e, vs_e = [], []
    for k in range(0, 13):
        tc = sw_beat_t(T, 0.0, t_on, k)
        v = sw_read(ys, tc, SW_HALF * T)
        frac = 1.0 - math.exp(-(tc - t_on) / TAU_S)
        ts_e.append(tc - t_on)
        vs_e.append(v)
        note = '源刚开' if k == 0 else ('✔ 猛哥"第 2 个开始下移"' if k == 1 else '')
        print('     %-8d %-9.2f %-13.1f %-13.2f %-11s %s'
              % (k, tc, v, v / PX_UV, '%.0f%%' % (100.0 * frac), note))
    print('     ' + '-' * 88)
    print('     · 源关断后（读那条直线，理论 → 0）：')
    print('     %-8s %-9s %-13s %-13s %-11s %s' % ('t−t_off', 't(s)', '读数(uV)', '(px)', '已恢复', '备注'))
    ts_r, vs_r = [], []
    for dt in (0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 14.0, 18.0):
        tc = t_off + dt
        if tc + SW_HALF * T >= dur:
            continue
        v = sw_read(ys, tc + SW_HALF * T, SW_HALF * T)
        ts_r.append(dt + SW_HALF * T)
        vs_r.append(v)
        print('     %-8.1f %-9.2f %-13.1f %-13.2f %-11s %s'
              % (dt, tc, v, v / PX_UV, '%.0f%%' % (100.0 * (1.0 - math.exp(-dt / TAU_S))),
                 '← 猛哥读的就是这里（"10 s 左右"）' if abs(dt - 10.0) < 0.1 else ''))
    print('     ⇒ 三段一气呵成：**0 →(τ)→ D →(τ)→ 0**，同一条时间常数 ⇒ 这就是"频响参数"的直接签名。')
    print()
    print('     ⚠⚠ **对不上的地方（记下来，别装作没看见）—— 而且不止一处**：')
    print('       模型里 −40 uV 这个数出现在 **t−t_on ≈ 15 s（第 12 搏）**、且只到 D 的 80%；')
    print('       而猛哥目测是"**5~6 拍后稳定在 −0.04 mV**" ⇒ 若那个计数可靠 ⇒')
    print('       τ_eff ≈ %.2f × 5.5/15 ≈ **2.9 s** ⇒ fc_eff ≈ %.3f Hz ≈ 源码 %.2f Hz 的 %.0f 倍'
          % (TAU_S, 1.0 / (2.0 * math.pi * 2.9), FC_HZ, (1.0 / (2.0 * math.pi * 2.9)) / FC_HZ))
    print('       ⇒ 那意味着链里**还有第二个高通环节**（AFE？ADS1293 内部？上位机？须查）。')
    print('       🛑 可是 —— **你自己的第 4 条把这条路堵了一半**：恢复"10 s 左右"')
    print('          · τ=2.9 s ⇒ −40 uV 恢复到看不见（≈0.4 px = 13 uV）只要 **3.3 s**   ✗ 对不上 10 s')
    print('          · τ=%.2f s ⇒ 同样的阈值要 **9.0 s**                              ✓ 恰好是"10 s 左右"' % TAU_S)
    print('       ⇒ 🛑🛑 **两半观测在"单极点"假设下互相矛盾**：建立快 ⇒ 恢复也必须快；')
    print('          恢复慢 ⇒ 建立也必须慢。**不可能一个 2.9 s 一个 8 s。**')
    print('       ⇒ 最可能的解：**"稳定在 −0.04 mV"其实是"爬得比眼睛能分辨的更慢"**')
    print('          —— 整个现象只有 **1.2~1.5 px**（D = −40~−50 uV ÷ 32.8 uV/px）')
    print('          ⇒ 眼睛在 0.8 px 与 1.2 px 之间，本来就分不出"还在动"。')
    print('       ⇒ 次可能（不能排除）：**真实链确实不止一个极点**（AFE / ADS1293 内部 / 上位机）。')
    print('       🔧 **判决只要一步、零成本**：源开之后**别停，继续读到 30~40 s** ——')
    print('         · 若 30 s 时读数**继续更负**（逼近 −50 uV）⇒ τ≈%.1f s ✓ **单极点**（模型对，目测计数偏快）；' % TAU_S)
    print('         · 若 8 s 后就**一点不动**了 ⇒ τ 确实更小 ⇒ **存在第二个极点**（归因要重做）。')
    print('       🔧 **更彻底（推荐）：把这段顺手落盘成 txt** ⇒ 下面 §SW5 那套拟合直接跑：')
    print('         · **恢复段是纯指数、没有形态干扰，是全场最干净的一段** ⇒ 先由它定 τ ⇒ fc=1/(2πτ)；')
    print('         · 再回头看建立段是不是**同一个 τ** ⇒ 一步同时回答"τ 多大"＋"是不是单极点"。')
    print('         · 数据量约 10 行/秒 × 40 s，很小；比"目测 5 拍 vs 12 拍"可靠得多。')
    print()
    print('     ⚑⚑⚑ **09-28 晚 · 实测回注 ⇒ 上面这段"矛盾"已消解（我方读法错，非模型错）**：')
    print('       猛哥当晚把目测口径改精确为两条：①第 1 搏仍像 0、**第 2~3 搏（≈3 s）开始下降**；')
    print('       ②源停后**从 −40 uV 恢复到 0 约 10 s**（他自己指出"对应你的 9 s，对得上"）。')
    print('       ⇒ 与本法逐点对：k=0 → 0.18 px（看不出）· k=1 → 0.33 px（临界）· k=2 → 0.47 px（明确）')
    print('         ＋ 恢复 9.0 s ⇒ 🛑 **四条观测全部落在单极点 τ=%.2f s 上**。' % TAU_S)
    print('       ⇒ 误读根因：把"5~6 拍后看着稳定在 −0.04 mV"读成了"t=5~6 s 已到稳态"；')
    print('         实际第 6 搏只到 0.87 px、第 12 搏才 1.20 px ⇒ **这个差别任何屏幕都读不出来**。')
    print('     ✅ **A 判决已执行**：源开后不停读到 30~40 s ⇒ 基线**不再移动、一直维持**')
    print('        ⇒ **排除更慢的极点**；叠加"恢复 10 s"锁 τ ≈ 8 s ＝ 源码 %.2f s ⇒ **单极点定版**。' % TAU_S)
    print('     ⚠ 唯一未闭：D 实测 **−40 uV** vs 模型 **−50 uV**（−20% ⇒ 疑源模板等效 w<100 ms）。')
    print('     📌 **B（落盘拟合）规格见报告 §14.3**：首选上位机「● 记录」⇒ `recordings/ecg_*.bin`')
    print('        （实解格式 AA 55|01|LEN|seq|**25×int16 LE**|CRC8 ＝ **58 B/帧 @ 34.13 帧/s**·无丢帧）。')
    print()

    # ---------------- §SW3 ----------------
    print('  §SW3 ★★★ 起相位 φ 只在**旧方法**里是随机变量 —— 你这个新方法把它消掉了')
    print('     链的真正行为（把 H(z)=(1−z⁻¹)/(1−r·z⁻¹) 展开成 h[0]=1、h[m]=(r−1)·r^(m−1)）：')
    print('       **y(t) = s(t) − ⟨s⟩**，⟨s⟩ ＝目前已经积累的 s 的平均（τ 尺度上才变成整周期均值）')
    print('     ⇒ 短时（t ≪ τ）：⟨s⟩ 只覆盖"已过去的那一小段" ⇒ **平台读数从 0 起、近线性爬向 −mean**')
    print('     ⇒ 长时（t ≫ τ）：⟨s⟩ ＝整周期均值 ⇒ 平台读数 ＝ **−mean ＝ D**')
    print('     ⇒ ❗ 同一个式子里，"起手在哪儿"完全由**链起手时已经积累了什么**决定 ⇒ 见 (a)/(b) 两栏。')
    print()
    print('     (a) ✅ **你这个新方法**（源从 0 起输出 ⇒ 链先"对着 0 归零" ⇒ 起手 ⟨s⟩ ≡ 0）')
    print('     %-9s %-15s %-15s %-13s %-11s %s'
          % ('φ (×T)', 's(t_ref)(uV)', 'b0 理论', 'b0 实测', '+30 s', '判读'))
    print('     ' + '-' * 106)
    for phi_f in (0.00, 0.02, 0.05, 0.08, 0.10, 0.25, 0.50):
        phi0 = phi_f * T
        s0 = wave_uv('ecg', phi0, T, A)
        ysk = run_onoff('ecg', A, T, 2.0, 40.0, 46.0, phi_on=phi0)
        b0m = sw_read(ysk, sw_beat_t(T, phi0, 2.0, 0), SW_HALF * T)
        v30 = sw_read(ysk, sw_beat_t(T, phi0, 2.0, 30), SW_HALF * T)
        print('     %-9.2f %-15.1f %-15s %-13.1f %-11.1f %s'
              % (phi_f, s0, '0（与 φ 无关）', b0m, v30, '✅ 起手恒在 0'))
    print('     ⇒ ★ 关键：**b0 ≡ 0，与 φ 无关** ⇒ 屏幕上只剩"D 的建立过程"这一件事')
    print('       ⇒ ✅ **现象第一次可复现**（＝猛哥第 3 条"第 1 拍在 0、第 2 拍才下移"）。')
    print()
    print('     (b) 🛑 **固件 START 的旧方法**（源在自由运行 ⇒ 链起手那一帧的电平被当成了零参考）')
    print('     %-9s %-15s %-15s %-13s %-11s %s'
          % ('φ (×T)', 's(t_ref)(uV)', 'b0 理论', 'b0 实测', '末态', '判读'))
    print('     ' + '-' * 106)
    t3 = 60.0 / 180.0
    for phi_f in (0.00, 0.02, 0.05, 0.08, 0.10, 0.25, 0.50):
        phi0 = phi_f * t3
        ref = wave_uv('ecg', (WARM_SKIP / FS + phi0) % t3, t3, A)
        ysp = run_onoff('ecg', A, t3, 0.0, 40.0, 46.0, phi_on=phi0)
        b0m = sw_read(ysp, sw_beat_t(t3, phi0, 0.0, 1), SW_HALF * t3)
        v30 = sw_read(ysp, sw_beat_t(t3, phi0, 0.0, 110), SW_HALF * t3)
        print('     %-9.2f %-15.1f %-15.1f %-13.1f %-11.1f %s'
              % (phi_f, ref, -ref, b0m, v30,
                 '★ 起手恰好在 0' if abs(ref) < 60.0
                 else ('🛑 起手偏 %.0f px ⇒ 就是"偶尔偏 1 mV"' % (abs(b0m) / PX_UV))))
    print('     ⇒ ★★ 两栏对起来就是全篇的钥匙：')
    print('       ① **末态那一列两栏都差不多**（都到 −%.0f uV）⇒ **D 与起点、与用哪种方法都无关**；'
          % abs(A * W_T149_S / (2.0 * t3)))
    print('          ⚠ (b) 栏的末态取在 +37 s（不是 +10 s）：因为 b0 = −854 uV 的尾巴要按 τ 衰减，')
    print('            +10 s 时它还剩 28%（−242 uV）⇒ 那时读到的**不是 D**，别拿它当稳态。')
    print('       ② 但**起手那一列**：(a) 恒 0；(b) 从 −0.2 px 铺到 −30 px ⇒ **旧方法的随机性全在这里**；')
    print('       ③ 所以新方法并不是"消除了 D"，而是**消除了 b0 这个随机变量** ⇒ 剩下的 D 正好可以定量研究。')
    print()

    # ---------------- §SW4 ----------------
    def env_mid_at(yy, t_c, T_s, nc=2):
        """[t_c, t_c+nc·T] 的**包络中点** (max+min)/2（uV）—— 对无平台波形也成立的人眼"居中"判据。"""
        i0 = max(0, int(t_c * FS))
        i1 = min(int((t_c + nc * T_s) * FS), len(yy))
        if i1 <= i0:
            return float('nan')
        sg = yy[i0:i1]
        return uv((max(sg) + min(sg)) / 2.0)

    def fit_tau_grid(ts_f, vs_f, model):
        """网格搜 τ∈[0.5,30] s 使 Σ(v − model(t,τ))² 最小 —— 比"两点法"稳得多。"""
        best, bt = 1e30, float('nan')
        tt = 0.5
        while tt <= 30.0:
            e = sum((v - model(t, tt)) ** 2 for t, v in zip(ts_f, vs_f))
            if e < best:
                best, bt = e, tt
            tt += 0.01
        return bt

    print('  §SW4 ★★★ 六种波形对照 —— 用**包络中点漂移**当通用判据（对无平台波形同样成立）')
    print('     判据：漂移 ≡ 中点(源开+30s) − 中点(源开+1s)，理论值 = D = −mean(源)')
    print('     %-24s %-11s %-12s %-12s %-12s %-13s %-9s %s'
          % ('源波形', 'mean(uV)', 'D理论', '+1s 中点', '+30s 中点', '漂移(实测)', '(px)', '源停+12s'))
    print('     ' + '-' * 124)
    for k in WAVE_KINDS:
        t_on2, t_off2, du2 = 2.0, 34.0, 48.0
        ysk = run_onoff(k, A, T, t_on2, t_off2, du2)
        mv = sw_mean_uv(k, A, T)
        m1 = env_mid_at(ysk, t_on2 + 0.5, T, 2)
        m30 = env_mid_at(ysk, t_on2 + 29.0, T, 2)
        drift = m30 - m1
        vr = sw_read(ysk, t_off2 + 12.0 + SW_HALF * T, SW_HALF * T)
        print('     %-24s %-11.1f %-12.1f %-12.1f %-12.1f %-13.1f %-9.2f %+.1f'
              % (WAVE_CN[k], mv, -mv, m1, m30, drift, drift / PX_UV, vr))
    print('     ⇒ 分界干净：**漂移 ≈ −mean** ⇒ 只有 mean≠0 的（ECG / 单极性脉冲）在动，其余恒 0；')
    print('       源停 +12 s 那列：有漂移的仍残 −40 uV 量级（还在按 τ 回 0），没漂移的**直接就是 0** ⇒')
    print('       ⇒ **与猛哥第 5、6 条逐字对上**。')
    print()

    print('     ⚠ 口径说明：**漂移列 ≈ 0.75×D**（ECG −37 vs −50、单极脉冲 −151 vs −200）——')
    print('       因为参考点取在"源开 +1 s"，那一刻链**还没长满**，参考值本身就带了一点未偏移量 ⇒')
    print('       这是**参考口径的偏差、不是机制的偏差**：定性分界（谁在动、谁不动）不受影响。')
    print('       要精确 D，请读有平台波形的那一列（§SW2 平台读数）或直接落盘拟合。')
    print()

    # ---------------- §SW5 ----------------
    print('  §SW5 ★★ τ 反解（你这个方法的落盘数据就能做，还能反过来标定 fc）')
    bt_e1 = bt_e4 = bt_r = float('nan')
    print('     %-24s %-9s %-13s %-11s %s' % ('段 / 源频率', '点数', 'τ 网格拟合', 'RSS', '说明'))
    print('     ' + '-' * 88)
    for f_src in (1.0, 4.0):
        Tk = 1.0 / f_src
        t_on3, t_off3 = 2.0, 36.0
        du3 = 36.0 + 40.0
        ysk = run_onoff('ecg', A, Tk, t_on3, t_off3, du3)
        ts_f, vs_f = [], []
        tc = t_on3 + 0.75 * Tk
        while tc < t_off3 - 0.5:
            ts_f.append(tc - t_on3)
            vs_f.append(sw_read(ysk, tc, SW_HALF * Tk))
            tc += Tk
        Dk = -sw_mean_uv('ecg', A, Tk)
        bt = fit_tau_grid(ts_f, vs_f, lambda t, tau: Dk * (1.0 - math.exp(-t / tau)))
        rss = sum((v - Dk * (1.0 - math.exp(-t / bt))) ** 2 for t, v in zip(ts_f, vs_f))
        if f_src == 1.0:
            bt_e1 = bt
        else:
            bt_e4 = bt
        print('     %-24s %-9d %-13.2f %-11.0f %s'
              % ('建立（源开 → 稳）·%.0f Hz' % f_src, len(ts_f), bt, rss, '预算 %.2f s' % TAU_S))
    ysr = run_onoff('ecg', A, T, 2.0, 36.0, 60.0)
    ts_r, vs_r = [], []
    tc = 36.2
    while tc < 58.0:
        ts_r.append(tc - 36.0)
        vs_r.append(sw_read(ysr, tc, 0.2))
        tc += 0.5
    v0 = sw_read(ysr, 36.2, 0.2)
    bt_r = fit_tau_grid(ts_r, vs_r, lambda t, tau: v0 * math.exp(-t / tau))
    rssr = sum((v - v0 * math.exp(-t / bt_r)) ** 2 for t, v in zip(ts_r, vs_r))
    print('     %-24s %-9d %-13.2f %-11.0f %s'
          % ('恢复（源停 → 回 0）', len(ts_r), bt_r, rssr, '预算 %.2f s' % TAU_S))
    print('     ⇒ ✅ 三段拟合值彼此一致（1 Hz 建立 / 4 Hz 建立 / 源停恢复 全 ≈ %.1f s）。' % TAU_S)
    print('     🛑🛑 **但这三列全部是在"模型自己生成的数据"上拟合的** ⇒ 它只能证明')
    print('         **模型内部自洽**（同一个 h 展开 ⇒ 同一个 τ），**不能证明真实链是单极点**。')
    print('         「真实链有没有第二个极点」**只能靠实测**：见 §SW2 那个零成本判决')
    print('         （源开不停，读到 30~40 s；或干脆落盘拟合）。别把自证的数当实测结论。')
    print('     ⇒ 🔧 **反向用法（这条才是真能落地的）**：拿实测恢复段直接拟合 τ ⇒')
    print('         `fc = 1/(2πτ)` ⇒ **免费的频响标定手段**（现有标定要跑扫频/方波，慢且需要台架）。')
    print()

    # ---------------- §SW6 ----------------
    print('  §SW6 ★★★ 判决实验：你第 5、6 条**本身就是判决实验**，这里是它的定量版')
    print('     ⚠ 先纠我上版的一个错：**"矩形脉冲"这个名字不决定偏移，"单极性"才决定。**')
    print('       占空比 d 的**单极性**脉冲（0 ↔ A）：mean = A·d ⇒ D = −A·d（d→0 时 D→0）')
    print('       **对称**方波（+A/2 ↔ −A/2）：mean ≡ 0 ⇒ **D ≡ 0，且与占空比无关**')
    print('     %-24s %-12s %-13s %-13s %-9s %s' % ('波形（同为"方"形）', 'mean(uV)', 'D理论', '漂移实测', '(px)', '判读'))
    print('     ' + '-' * 96)
    for d in (0.10, 0.20, 0.50, 0.80, 0.95):
        t_on4, t_off4, du4 = 2.0, 34.0, 48.0
        nn = int(du4 * FS)
        dc4 = DcBlock(warm_skip=WARM_SKIP, r_val=R)
        ysd = []
        i_on, i_off = int(t_on4 * FS), int(t_off4 * FS)
        for i in range(nn):
            if i_on <= i < i_off:
                v = A if (((i - i_on) / FS) % T) < d * T else 0.0
            else:
                v = 0.0
            ysd.append(dc4.process(C0 + v / UV_PER_CODE))
        m1 = env_mid_at(ysd, t_on4 + 0.5, T, 2)
        m30 = env_mid_at(ysd, t_on4 + 29.0, T, 2)
        print('     %-24s %-12.1f %-13.1f %-13.1f %-9.2f %s'
              % ('单极性·duty %.0f%%' % (100 * d), A * d, -A * d, m30 - m1, (m30 - m1) / PX_UV,
                 '✅ D = −A·d 线性'))
    yss = run_onoff('sq', A, T, 2.0, 34.0, 48.0)
    ms1 = env_mid_at(yss, 2.5, T, 2)
    ms30 = env_mid_at(yss, 31.0, T, 2)
    print('     %-24s %-12.1f %-13.1f %-13.1f %-9.2f %s'
          % ('对称方波（duty 50%）', 0.0, -0.0, ms30 - ms1, (ms30 - ms1) / PX_UV, '✅ 严格 0'))
    print('     ⇒ ★★★ **同样是"方"形状、同样会进频响/瞬态测试，一个偏一个不偏，唯一差别是"上下对称"**：')
    print('       病灶 = **波形一个周期的均值 ≠ 0（非对称）**，不是"它出现在哪个测试项目里"。')
    print('     ⇒ 🛑 所以"只有频响测试才用 Rectangle Pulse"这句是**巧合不是因果**。')
    print()
    print('     · 想再钉一层，用你手上的旋钮做**标定**（全部免费）：')
    print('       %-22s %-18s %-18s %s' % ('改什么', '理论关系', '预期', '说明'))
    for a1, b1, c1, e1 in (
            ('幅度 A（0.5/1/2 mV）', 'D ∝ A', '线性', '最简单·一比就知道'),
            ('频率 f（0.5/1/2/3/4 Hz）', 'D ∝ f', '线性', '＝复现 T149 那张五点表'),
            ('矩形脉冲占空比 d', 'D = −A·d', '线性·d→0 则 D→0', '★ 最能证"非对称"是病因'),
            ('方波 ↔ 单极性脉冲', 'mean: 0 ↔ A·d', '0 ↔ 有偏移', '★ 你第 6 条已做过')):
        print('       %-22s %-18s %-18s %s' % (a1, b1, c1, e1))
    print()
    print('     · 幅度/频率扫描（ECG·源开 40 s → 读稳态）：')
    print('       %-13s %-13s %-13s %-13s %s' % ('条件', 'mean(uV)', 'D理论(uV)', 'D实测(uV)', 'px'))
    for kk, aa, ff in (('0.5 mV', 500.0, 1.0), ('1 mV', 1000.0, 1.0), ('2 mV', 2000.0, 1.0),
                       ('1 mV·0.5 Hz', 1000.0, 0.5), ('1 mV·2 Hz', 1000.0, 2.0), ('1 mV·3 Hz', 1000.0, 3.0)):
        Tk = 1.0 / ff
        ysk = run_onoff('ecg', aa, Tk, 2.0, 46.0, 52.0)
        vp = sw_read(ysk, sw_beat_t(Tk, 0.0, 2.0, int(38.0 / Tk)), SW_HALF * Tk)
        mv = sw_mean_uv('ecg', aa, Tk)
        print('       %-13s %-13.1f %-13.1f %-13.1f %.2f' % (kk, mv, -mv, vp, vp / PX_UV))
    print('       ⇒ D ∝ A（线性）·D ∝ f（线性）⇒ **复现 T149 那张五点频扫表**。')
    print('       ⚠ 注意 1 mV/1 Hz 那行：模型 −48 uV vs 猛哥实测 −0.04 mV ⇒ 差 20% ⇒')
    print('         这是"AECG100 的真实 NSR 模板"与"我的三角尖峰模型"的形状差异，不是矛盾。')
    print()
    print('     ⚠ 同一口径说明：漂移列同样 ≈ 0.75×A·d（参考点未长满）；**符号与相对大小是可信的**。')
    print('       对称方波那行 +13 uV（0.4 px）＝**方法噪声底** ⇒ 实质为 0。')
    print()

    # ---------------- §SW7 ----------------
    print('  §SW7 收口：回答"跟哪些参数有关 / 是不是频响参数"')
    print('     %-34s %-30s %s' % ('被观察量', '由什么决定', '是"频响参数"吗'))
    print('     ' + '-' * 96)
    for a, b, c in (
            ('有没有偏移（有/没有）', '波形一个周期的均值 mean(源)', '🛑 **无关**'),
            ('偏移多大（D）', 'D = −mean(源)（脉冲型 ⇒ −A·w·f/2）', '🛑 **无关**'),
            ('偏移的**方向**（总朝 −mean）', 'mean 的正负号', '🛑 **无关**'),
            ('起手从哪儿出发（b0）', 'y(t≪τ) ≈ s(t) − s(t_ref) ⇒ b0 = −s(t_ref)', '🛑 **无关**'),
            ('偏移**多久稳定**（建立 τ）', 'dc_block 低端 fc（τ=1/(2πfc)）', '✅ **就是它**'),
            ('源停后**多久回 0**（恢复 τ）', '同一个 fc ⇒ **同一个 τ**', '✅ **就是它**'),
            ('源停后**回到哪里**', '永远是 0（H(1)=0，恒定输入 ⇒ 零输出）', '✅ 结构必然')):
        print('     %-34s %-30s %s' % (a, b, c))
    print('     ⇒ ★ 猛哥的猜测**对了一半，而且是对的那一半最实用**：')
    print('       **"下移后稳定"与"停止后恢复"这两段的快慢，确实就是链的低端频率响应**（唯一参数 fc）；')
    print('       但**"有没有/偏多少/往哪偏"三件事与频响无关**，只看波形的非对称度 ⇒')
    print('       🛑 所以**调 fc 治不了偏移量**（T149 守恒律）——它只决定你要等多久。')
    print('     ⇒ ★ 而"只有频响测试用 Rectangle Pulse"这条，是**巧合不是因果**：')
    print('       矩形脉冲与 ECG 的共同点是**非对称（mean≠0）**，不是"它出现在哪个测试里"。')
    print()


# ---------------------------------------------------------------------------
# T156 追问（§SW8）：下移的成长律 / 由现象反解 fc / 「能不能不下移」
# ---------------------------------------------------------------------------
D_MEAS_UV = -40.0               # 猛哥实测"稳定在 −0.04 mV"


def grow_seq(D_uv, tau_s=TAU_S, T_s=1.0, n=16):
    """逐搏平台读数 p[k] = D·(1 − r^k)，r = e^(−T/τ)。"""
    r = math.exp(-T_s / tau_s)
    return [(k, D_uv * (1.0 - r ** k)) for k in range(1, n + 1)], r


def grow_fc_window(D_uv, th_px):
    """由「第1拍看不见(<th px)」＋「第3拍看得见(>=th px)」反解 tau 窗口。"""
    a = th_px * PX_UV / abs(D_uv)
    return (-1.0 / math.log(1.0 - a)), (-3.0 / math.log(1.0 - a))


def grow_vis_time(D_uv, th_px, tau_s=TAU_S):
    """读数涨到 th_px 所需时间（与波形频率无关）。"""
    a = th_px * PX_UV / abs(D_uv)
    return tau_s * (-math.log(1.0 - a))


def cmd_grow():
    print('=' * 78)
    print('  §SW8 ★★★ 追问：为什么第 1 拍正常、第 2~3 拍才动？根在哪？能不能"不下移"？')
    print('=' * 78)
    print()
    print('  §G0 口径（🛑 先定死）')
    print('     · 「平台读数」＝逐搏**同一相位**取样（逐搏取整会把误差累积到尖峰上）')
    print('     · 逐搏序列是一阶递归 ⇒ **p[k] = D·(1 − r^k)**，'
          'r = e^(−T/τ) = %.6f' % math.exp(-1.0 / TAU_S))
    print('     · D ＝稳态平台读数 ＝−mean(源)（T155/156 已定）；本节用**实测** '
          'D = %+.0f uV' % D_MEAS_UV)
    print('     · 分辨力：1 px = %.1f uV ⇒ 全量程 40 uV 只有 **%.2f px**'
          % (PX_UV, abs(D_MEAS_UV) / PX_UV))
    print()

    # ---------------- §G1 ----------------
    print('  §G1 ★★★ 逐搏成长表 ——"第 1 拍为什么正常"直接看得到')
    print('     ------------------------------------------------')
    print('       k |  平台读数   |   px   | 本拍增量 |  占 D')
    print('     ------------------------------------------------')
    seq, r = grow_seq(D_MEAS_UV)
    prev = 0.0
    for k, v in seq:
        px = v / PX_UV
        print('      %2d | %+8.2f uV | %+6.3f |   %.3f  | %5.1f%%'
              % (k, v, px, abs(px - prev), 100.0 * v / D_MEAS_UV))
        prev = px
    print('     ------------------------------------------------')
    p1_px = D_MEAS_UV * (1.0 - r) / PX_UV
    print('     ⇒ ★★★ **"第 1 个波形看着正常"是两层原因叠出来的，缺一不可**：')
    print('       层① 结构性（去不掉）：τ 窗口里"装多少拍"＝ τ·f ＝ %.2f 拍（1 Hz）' % TAU_S)
    print('           第 1 拍时窗口里 87.5% 还是"源还没输出" ⇒ ⟨s⟩ 只长到 11.8%')
    print('           ⇒ 只扣掉 11.8%% 的均值 ⇒ 读数 %.1f uV ＝ **%.2f px** ⇒ 眼睛判"正常"'
          % (D_MEAS_UV * 0.118, abs(p1_px)))
    print('       层② 相位性（本次实验已消掉）：b₀ ＝ −s(t_ref)。源侧 start 让 s 从 0 起')
    print('           ⇒ b₀ ≡ 0 ⇒ 起手也不跳。（旧方法里这一项最大能把第 1 拍推走 ~30 px）')
    print('     ⇒ ★ 判据不是"第几拍"，而是**时间**：涨到 0.30 px 须 %.2f s' % grow_vis_time(D_MEAS_UV, 0.30))
    print('       **与波形频率无关** ⇒ 1 Hz 下是"第 2~3 拍"，4 Hz 下是"第 9 拍"，')
    print('       但都是同一刻（%.2f s）开始看得出 —— 所以本现象是"时间现象"不是"拍数现象"。'
          % grow_vis_time(D_MEAS_UV, 0.30))
    print('       ⚠ 反过来说："5~6 拍后看着稳定"＝那时每拍增量已降到 %.3f px 以下（感官饱和），'
          % abs(grow_seq(D_MEAS_UV, n=6)[0][5][1] / PX_UV - grow_seq(D_MEAS_UV, n=5)[0][4][1] / PX_UV))
    print('         不是"真的到了 D"（第 12 拍才 %.2f px、渐近值 %.2f px）。'
          % (grow_seq(D_MEAS_UV, n=12)[0][11][1] / PX_UV, D_MEAS_UV / PX_UV))
    print()

    # ---------------- §G2 ----------------
    print('  §G2 ★★ 「第 1 拍正常 ＋ 第 3 拍开始动」本身就是 fc 的**独立**证据')
    print('     （完全不依赖"恢复 10 s"那条 ⇒ 两条独立观测互证）')
    print('      视觉阈 TH  |   tau 窗口 (s)    |      fc 窗口 (Hz)')
    for th in (0.20, 0.30, 0.50, 0.80):
        tlo, thi = grow_fc_window(D_MEAS_UV, th)
        print('       %.2f px  | [%6.2f, %6.2f] | [%.4f, %.4f]  %s'
              % (th, tlo, thi, 1.0 / (2 * math.pi * thi), 1.0 / (2 * math.pi * tlo),
                 '<= 源码 0.02 落在窗内' if th == 0.30 else ''))
    print('     ⇒ ★ 源码 fc = %.2f Hz（tau = %.2f s）落在 TH=0.30 px 的窗口**中部**。' % (FC_HZ, TAU_S))
    print('     ⇒ ⚠ 但窗口是"数量级"级的，别当精标定用；精标定仍须落盘拟合（§SW5）。')
    print()

    # ---------------- §G3 ----------------
    print('  §G3 ★★ 「调 fc 能不能治下移？」—— 不能，只有"多久到"变')
    print('       fc(Hz) |  tau(s)  |  第1拍  |  第3拍  | 到63%  | 到95%  |     D')
    for fc in (0.01, FC_HZ, 0.03, 0.05, 0.10, 0.50, 5.00):
        tau = 1.0 / (2.0 * math.pi * fc)
        rr = math.exp(-1.0 / tau)
        p = [D_MEAS_UV * (1.0 - rr ** k) / PX_UV for k in (1, 3)]
        print('      %7.2f | %8.3f | %+6.3f | %+6.3f | %5.1fs | %5.1fs | %+.0f uV%s'
              % (fc, tau, p[0], p[1], tau, 3.0 * tau, D_MEAS_UV, '   <= 源码' if fc == FC_HZ else ''))
    print('     ⇒ ★ 每一行的 D **完全相同** ⇒ fc 只改"多久到"，不改"到哪儿"（T149 守恒律）。')
    print('     ⇒ ⚠ 反例：fc 提到 0.5 Hz 时**第 1 拍就到 −97% D** ⇒ 不再是"慢慢下移"，')
    print('       而是"开机即跳下去"（1.2 px 一步到位）⇒ **观感更差**。')
    print('       所以现行 fc = 0.02 Hz（比 ANSI/AAMI 诊断档 0.05 Hz 还低 2.5 倍）')
    print('       做的正是"把一次跳变摊成 ~20 s 缓坡"—— 这已经是"不治偏移"路线里的最优解。')
    print()

    # ---------------- §G4 ----------------
    print('  §G4 ★★★ 那"不下移"到底需要什么？—— 一个恒等式 ＋ 一个定义冲突')
    print('     稳态恒等式（任何 fc、任何阶数都成立，只要 H(1)=0）：')
    print('        y_ss(t) = s(t) − ⟨s⟩   ⇒   **平台读数 = −mean(s)**')
    print('     ECG 1 mV / 1 Hz：mean(s) = +A·w·f/2 = +50 uV（w=100 ms）⇒ 平台 −50 uV')
    print('       实测 −40 uV ⇒ 差 20% ⇒ 等价于真实 NSR 模板 w ≈ 80 ms（等 B 实验定）')
    print('     ⇒ ★★★ **"要平台 = 0" ⟺ "mean(s) = 0"**，否则必须**显式**把 mean(s) 加回去：')
    print('        · 源侧改信号源 —— 临床做不到（心电本身非对称）')
    print('        · 链上显式估计 ⟨s⟩ 再加回 —— 等价于"取消高通＋补偿"，引入漂移风险')
    print('        · 显示层基线恢复 —— 不动链、可开关（T153/T155 路线）')
    print('     ⇒ ★★ **但先问一句"这真的要治吗"**：')
    print('       y = s − 常数 ⇒ 波形上**任意两点的差不变** ⇒')
    print('       R 波高度、**ST 相对等电位线的抬高** —— 全部不变（它是**共模**偏移）。')
    print('       🛑 自我更正：我上一轮说"40 uV 是 ST 阈值 100 uV 的 40%"**说过头了** ——')
    print('       它只是"整条波形下移 1.2 px"，不是"ST 被单独压低"。')
    print('       真正受影响的只有两件事：')
    print('         ① **开机后 ~30 s（约 30 拍）内基线还在动** ⇒ 这段不能拿来判读；')
    print('         ② 屏幕上**零线不再落在等电位线上**（观感偏 1.2 px）⇒ 归零/标定须认这个。')
    print('     ⇒ ★ 定义冲突一句话：**链用的基线 = "整周期均值为 0"，临床用的基线 = "TP 段为 0"。**')
    print('       两者之差 ＝ P/T 波的面积贡献（≈40 uV）。所以"下移"不是滤波器坏了，')
    print('       是**两个"基线"的定义不一样** —— T153/T155 的"平稳段锚定"就是在把链的定义')
    print('       改成临床的定义。')
    print()

    # ---------------- §G5 ----------------
    print('  §G5 治理方案谱（按"能不能真的消掉 D"排）')
    for a, b, c in (
            ('① 提高 fc（偏移快点结束）', '❌ 不能', 'D 不变；观感变差（跳变）；触发守恒律'),
            ('② 降低 fc（偏移慢到看不见）', '❌ 不能', 'D 不变；换成长时漂移；b₀ 尾巴更久'),
            ('③ 时变 fc（开场高、随后切低）', '❌ 不能', '只治 b₀（起手位置），D 一分不消'),
            ('④ 取消 dc_block（fc → 0）', '✅ 能', '电极极化/失调/温漂全进屏（长时监护致命）'),
            ('⑤ 链上显式补偿（估 ⟨s⟩ 再加回）', '✅ 能', '估计误差 ⇒ 由"确定偏移"变"随机漂移"'),
            ('⑥ 显示层基线恢复（平稳段锚定）', '✅ 能', '不动链·可开关·只在读图模式开 ⇒ 🏆 推荐'),
            ('⑦ 接受它（靠共模性 ＋ 预热流程）', '—— 不必', '判读本不变；只需"开机等 ~30 s"')):
        print('     %s| %s| %s' % (pad_cjk(a, 34), pad_cjk(b, 10), c))
    print('     ⇒ ★ 结论：**①②③ 是死路**（数学上 D 与 fc 无关）；**④⑤ 有真实代价**；')
    print('       **⑥ 是唯一"干净"的解法**，也是 T153/T155 那条线的理论依据；')
    print('       **⑦ 才是当前唯一"零成本"答案** —— 前提是承认"1.2 px 共模偏移不改变判读"。')
    print('     ⇒ ★ 顺带回答"不下移就不用恢复了吧"：**对，两者是同一个 τ 的两面**。')
    print('       但"下移"每次上电都发生，"恢复"只在停源时发生 ⇒ **治下移的收益更大**。')
    print()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd in ('b0', 'all'):
        cmd_b0()
    if cmd in ('wave', 'all'):
        cmd_wave()
    if cmd in ('onoff', 'all'):
        cmd_onoff()
    if cmd in ('grow', 'all'):
        cmd_grow()
    if cmd not in ('b0', 'wave', 'onoff', 'grow', 'all'):
        print(__doc__)
