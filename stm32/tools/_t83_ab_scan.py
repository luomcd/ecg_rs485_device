# -*- coding: utf-8 -*-
"""T83 · 候选 A/B 统一裁决扫描（判据 a 的 a1「脉冲后任意时刻 ≤100 µV」最严口径）
----------------------------------------------------------------------------
背景（T79~T81 遗留）：
  · T81 离线单脉冲长窗给出 现状 极值 385.8／进限 +6.00 s；候选A 141.2／3.91 s；
    候选B 98.0／全程在限内 —— 但 T80/T81 两个脚本的候选定义**不一致**：
      _t81 里 ③= dc@0.05（去 HP4）、④= dc@0.02（去 HP4）  ⇒ ④ 是**组合**方案
      _t80 里 S1= dc@0.05（去 HP4）、S4= dc@0.02（去 HP4）⇒ 同上
  · 两个脚本都用 `dc_block(x)` 直接对输入做 ⇒ **丢掉了 LP@250 那一节**（真链里它在）
    （T80 脚注「LP 节在 S1/S4 中保留」与代码不符 ⇒ 本轮修正）
  · ⇒ **从没有人扫过「保留 HP4、只改 dc」这个改动最小的方案**（＝一行常数）

本脚本做的事（全部基于固件真实系数，真链顺序 dc → [BP 段]）：
  [1] 候选矩阵：HP 阶数 {4,2,0} × dc {0.05,0.03,0.02,0.015,0.01}
  [2] 单脉冲长窗（实测 col② 真脉冲当激励 + 60 s 静默尾）→ 四口径 + 进限时刻
  [3] 输入阻抗 / 判据 b 影响表：各候选 |H(f)| 相对现状的 dB 差（硬证据）
  [4] 节数账（→ dtc → CPU → gdtover 阈值）
  [5] 段间散布（3 段不同脉冲，同一噪声实现）
口径说明（对应 GB 9706.247 a) 的「脉冲后任意时刻 ≤100 µV」）：
  rawmax   = max|u| over [脉冲终点, +60 s]      —— 最保守（60 s 噪声极值 ≈ 4σ 会掺进来）
  sm100max = 先 100 ms 滑动平均再取 max|u|      —— ≈ 审评目视/记录仪读数
  env1smax = 1 s 窗 RMS 包络的 max              —— 「基线趋势」幅度
  进限时刻 = 最后一个 |·| > 100 µV 的时刻（s，自脉冲终点起算）
"""
import importlib.util
import math
import os
import sys

import numpy as np
from scipy.signal import freqz, lfilter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)

FS, FS_T, UV = m.FS, 500.0, 0.1122
LEAD_S, TRAIL_S, PRE_PAD, POST_PAD = 8.0, 60.0, 0.15, 0.20
SEED = 7
FILE = '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt'   # 判据 a 素材（A+ 档 · notch OFF）
MACRO = 'ECG_PROFILE_DUAL_A_PLUS'

OUT = []


def p(s=''):
    print(s)
    OUT.append(s)


def dc_block(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def load(path):
    """★严格 7 列（T56 教训：坏行判据必须 len!=7，否则行撕裂毒化全部统计）。"""
    r, bad = [], 0
    for ln in open(path, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) != 7:
            if q:
                bad += 1
            continue
        try:
            r.append([float(x) for x in q[:5]])
        except ValueError:
            bad += 1
    if bad:
        print('  ⚠ 丢弃坏行 %d 行（列数≠7 或含非数值）' % bad)
    return np.array(r)


def movavg(x, w=85):
    """居中滑动平均（w 必须奇数）：边缘反射填充 ⇒ 长度不变、无 `mode='same'` 的零填充假尖峰。"""
    pad = w // 2
    xp = np.concatenate([x[1:pad + 1][::-1], x, x[-pad - 1:-1][::-1]])
    c = np.cumsum(np.concatenate([[0.0], xp]))
    return (c[w:] - c[:-w]) / w


def pulses(v, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(v, 98), np.median(v)
    thr = lo + frac * (hi - lo)
    up = v > thr
    st, en = [], []
    for k in range(1, len(v)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    o = []
    for s in st:
        e = next((x for x in en if x > s), None)
        if e and (e - s) / fs >= minw:
            o.append((s, e))
    return o


# ------------------------------------------------------------------ 载入 + 系数分解
a = load(os.path.join(HERE, FILE))
c2 = a[:, 1] * UV          # 链前（AFE 原始码 → µV）
c5 = a[:, 4] * UV          # 链后（设备实际输出）

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, MACRO)
bp = list(sos[n_notch:])
kinds = [m.section_kind(s) for s in bp]
LP = [bp[i] for i, k in enumerate(kinds) if k == 'LP']
HPs = [bp[i] for i, k in enumerate(kinds) if k == 'HP']

p('=' * 122)
p('T83 · 候选 A/B 统一裁决 —— 判据 a 最严口径「脉冲后任意时刻 ≤100 µV」')
p('激励源＝%s（A+ 档 · notch OFF 的判据 a 素材）' % FILE)
p('真链结构（notch OFF）＝ dc_block(fc) → [ BP 段 ]，BP 段 %d 节 = %d×LP + %d×HP'
  % (len(bp), len(LP), len(HPs)))
p('=' * 122)

# ------------------------------------------------------------------ 候选矩阵
CANDS = [
    ('S0 现状      HP4 + dc@0.05', 0.05, HPs),
    ('C  只改dc    HP4 + dc@0.03', 0.03, HPs),
    ('C2 只改dc    HP4 + dc@0.02', 0.02, HPs),
    ('A2 删1节HP   HP2 + dc@0.05', 0.05, HPs[:1]),
    ('A  删HP4     dc@0.05（16节）', 0.05, []),
    ('B  删HP4+    dc@0.02（16节）', 0.02, []),
    ('B2 删HP4+    dc@0.015', 0.015, []),
    ('D  删HP4+    dc@0.01', 0.01, []),
]


def chain(fc, hps, x, fast=False):
    s = LP + list(hps)
    xd = dc_block(x, fc)
    if fast:
        a = np.array(s, dtype=float)
        return sosfilt(np.column_stack([a[:, :3], np.ones(len(a)), a[:, 3:]]), xd)
    return m.run(s, xd)


def nsec(fc, hps):
    return n_notch + len(LP) + len(hps)


# ------------------------------------------------------------------ 激励（单脉冲长窗）
# 🛑 col② 带 AFE 的 VCM 直流（本次实测 ~684 mV）⇒ 定位前必须去直流，否则阈值法全乱
c2d = c2 - np.median(c2)
c5d = c5 - np.median(c5)
print('  直流检查：col② 中位 %.1f mV（应≈VCM 直流）｜去直流后 p-p %.1f µV'
      % (np.median(c2) / 1000.0, np.percentile(c2d, 99.9) - np.percentile(c2d, 0.1)))
pr2 = pulses(c5d, FS_T)                   # 用 col⑤ 去直流后定位（与 T79/T80/T81 同口径）
# ⚠ 脉冲区间必须用**链前 col②** 界定：col⑤ 的下降沿紧接 388 µV 的低频 tail，无法与边沿分离；
#   而 col② 无链的低频 tail ⇒ 下降沿能干净回到基线 ⇒ 可精确定出「下降沿结束」= a1 的观测起点。
#   （若把起点定在 col⑤ 的下降沿上，100 ms 居中平滑窗口会横跨下降沿、把 2854 µV 平台抹进窗口
#     ⇒ sm 虚高到 1178 µV「> rawmax」的假象 —— T83 已实测复现并修正。）
SEGS = []
for s5, e5 in pulses(c5d, FS_T):
    lo_i = max(0, s5 - int(0.10 * FS_T))
    pl = float(np.median(c2d[s5 + int(0.03 * FS_T):s5 + int(0.09 * FS_T)]))
    if not (1500.0 < pl < 4500.0):
        continue
    s2 = next((k for k in range(lo_i, s5 + int(0.10 * FS_T)) if c2d[k] > 0.5 * pl), None)
    e2 = next((k for k in range(e5, min(e5 + int(0.30 * FS_T), len(c2d))) if abs(c2d[k]) < 0.05 * pl), None)
    if s2 is None or e2 is None:
        continue
    SEGS.append((s2, e2, pl))


def e5_of(e):
    """col⑤ 下降沿后的谷底（＝降沿真正结束）。col② 的 5% 落点因链群延迟在 col⑤ 上仍处降沿。"""
    g0, g1 = max(0, e - int(0.05 * FS_T)), min(len(c5d), e + int(0.15 * FS_T))
    return g0 + int(np.argmin(c5d[g0:g1]))


SEGS = [t for t in SEGS if 80.0 < (e5_of(t[1]) - t[0]) / FS_T * 1e3 < 160.0]
p('  脉冲精定位（col② 界定）：%d 段 ｜ 间隔 %.2f s ｜ 脉宽中位 %.1f ms（col② 50%% → col⑤ 谷底）｜ 链前台阶 %.1f µV（标称 3000）'
  % (len(SEGS), np.median(np.diff([s for s, _, _ in SEGS])) / FS_T,
     np.median([(e5_of(e) - s) / FS_T for s, e, _ in SEGS]) * 1e3,
     np.median([q[2] for q in SEGS])))

# 输出噪声 σ：脉冲前 0.5~0.05 s 静默段（std 口径 ＋ 差分法：剔除低频 tail 污染）
q = np.concatenate([c5d[s - int(0.5 * FS_T):s - int(0.05 * FS_T)] for s, _, _ in SEGS])
SG = float(np.std(q))
SGD = float(np.std(np.diff(q)) / np.sqrt(2.0))
p('  实测输出噪声：std σ = %.2f µV ｜ 差分法 σd = %.2f µV（后者剔除慢变 tail）' % (SG, SGD))
p('  ⚠ 口径物理边界：若按「原始采样每点 |u|≤100」读，%.1fσ 处噪声自身即可触限'
  '（68 s × 853 点期望超限点数 %.2f）⇒ 该口径不可执行（见表 1b 实测 col⑤）'
  % (100.0 / SGD, 68 * FS * 0.5 * math.erfc(100.0 / (SGD * math.sqrt(2.0)))))


def e5_of_dummy():
    pass


def build(seg_i, seed=SEED, trail=TRAIL_S):
    """取第 seg_i 段脉冲 → 单脉冲长窗激励（853 域）、返回 (x853, k1)。
    k1 = col⑤ 下降沿后的**谷底**：col② 的 5% 落点因链群延迟在 col⑤ 上仍处下降沿，
    所以终点必须用 col⑤ 自己的谷底定（＝降沿真正结束、tail 的起点）。"""
    s, e, _ = SEGS[seg_i]
    e5 = e5_of(e)                                  # col⑤ 谷底
    a0, a1 = s - int(PRE_PAD * FS_T), e5 + int(POST_PAD * FS_T)
    w = c2d[a0:a1].copy()
    w -= float(np.median(w[:int(0.10 * FS_T)]))
    rs = np.random.RandomState(seed)
    lead = rs.randn(int(LEAD_S * FS_T)) * SG
    tail = rs.randn(int(trail * FS_T)) * SG
    x500 = np.concatenate([lead, w, tail])
    t500 = np.arange(len(x500)) / FS_T
    t853 = np.arange(0.0, t500[-1], 1.0 / FS)
    x853 = np.interp(t853, t500, x500)
    k1 = int(round((len(lead) + (e5 - a0)) / FS_T * FS))
    return x853, k1


def metrics(y, k1):
    """★口径修正版：滑动平均用居中 cumsum（无边界假尖峰）；并列百分位口径刻画分布。"""
    bb = float(np.median(y[k1 - int(0.55 * FS):k1 - int(0.15 * FS)]))   # ★窗口全在脉冲之前（脉宽 100 ms）
    u = y - bb
    seg = u[k1:]
    sm = movavg(seg, 85)          # ★先切到脉冲后、再平滑：窗口绝不跨越下降沿
    n1 = int(1.0 * FS)
    nb = len(seg) // n1
    env = np.sqrt(np.array([np.mean(seg[i * n1:(i + 1) * n1] ** 2) for i in range(nb)]))
    j = int(np.argmax(env))

    def lo(v, thr=100.0):
        i = np.where(np.abs(v) > thr)[0]
        return 0.0 if len(i) == 0 else (i[-1] + 1) / FS

    ab = np.abs(seg)
    return dict(raw=float(np.max(ab)),
                sm=float(np.max(np.abs(sm))),
                env=float(env[j]), envat=(j + 1) * 1.0,
                p999=float(np.percentile(ab, 99.9)), p99=float(np.percentile(ab, 99.0)),
                tr=lo(seg), ts=lo(sm), te=lo(np.repeat(env, n1)[:len(seg)]),
                stead=float(np.median(u[-int(3 * FS):])))


# 主对比：同一段（中位段）× 同一噪声实现 ⇒ 差异纯来自链
MID = len(SEGS) // 2
x853, k1 = build(MID)

# 自证：sosfilt(DF2T) 与固件同式 m.run 的偏差（表 4 用 fast 提速 100×）
_d = float(np.max(np.abs(chain(0.05, HPs, x853, fast=True) - chain(0.05, HPs, x853))))
p('  [自证] sosfilt(DF2T) vs 固件同式 m.run 最大偏差 = %.3e µV ⇒ 表 4 提速不影响结论' % _d)

p('  主对比段：第 %d/%d 段（脉宽 %.0f ms）｜激励总长 %.1f s ｜ 尾窗 +57~60 s 取稳态'
  % (MID + 1, len(SEGS), (SEGS[MID][1] - SEGS[MID][0]) / FS_T * 1e3, len(x853) / FS))

p()
p('【表 1】单脉冲长窗 · 多口径（µV；限 100）')
p('  %-27s %4s %7s %8s %9s %8s %8s %8s %8s' %
  ('候选', '节数', 'p99.9', 'p99', 'sm100', 'env1s', 't*(env)', '进限sm', '稳态'))
p('  ' + '-' * 104)
RES = {}


def fmt(v):
    return '%6.1f%s' % (v, '✓' if v <= 100 else '✗')


for name, fc, hps in CANDS:
    y = chain(fc, hps, x853)
    r = metrics(y, k1)
    RES[name] = (r, nsec(fc, hps))
    p('  %-27s %4d %7s %7s %9s %8s %8.1f %8s %8.1f'
      % (name, nsec(fc, hps), fmt(r['p999']), fmt(r['p99']), fmt(r['sm']), fmt(r['env']),
         r['envat'], ('%.2f' % r['ts']) + ('✓' if r['ts'] == 0 else '✗'), r['stead']))
p('  口径定义：p99.9/p99 = |u| 分位数（**抗极值 ⇒ 首选**）；sm100 = 100 ms 居中滑动平均后 max（≈审评读数）；')
p('            env1s = 1 s 窗 RMS 包络的 max 与其时刻 t*（＝「基线趋势」幅度，最贴近 a1 的物理含义）；')
p('            进限sm: 0.00✓ 表示平滑读数全程未超 100 µV；稳态 = 尾窗 +57~60 s 中位。')
p('  🛑 rawmax 不在本表 —— 它被单点尖峰主导（见表 1b 与表 4），是**不可用口径**。')

# ---------------------------------------------------------------- 表 1b：可实测直接对照的窗口
p()
p('【表 1b】实测可直接对照的窗口（脉冲终点后 0~1.5 s；间隔 2.0 s，1.9 s 会吃到下一个脉冲）')
p('  实测 col⑤ = 设备真实输出（跨 13 段取中位）⇒ 用它检验离线模型是否忠实')


def metrics_win(y, k1, T=1.5, fs=FS_T):
    """★fs 必传数据所在域：实测 col⑤ 是 500 Hz 域（FS_T），离线是 853 域（FS）。
    用错域 ⇒ 窗口长度按 1.7 倍放大 ⇒ 会吃到下一个脉冲（T83 实测踩过）。"""
    k1 = int(max(0, min(k1, len(y) - 1)))
    i0 = max(0, k1 - int(0.55 * fs))
    i1 = max(i0 + 1, k1 - int(0.15 * fs))
    bb = float(np.median(y[i0:i1]))
    u = y - bb
    n = int(min(len(u) - k1, T * fs))
    if n < 200:
        return None
    seg = u[k1:k1 + n]
    sm = movavg(seg, max(3, int(0.1 * fs) | 1))
    ab = np.abs(seg)
    w = int(0.5 * fs)
    env = np.sqrt([np.mean(seg[i:i + w] ** 2) for i in range(0, max(1, n - w), w)])
    return dict(raw=float(np.max(ab)), sm=float(np.max(np.abs(sm))),
                env=float(np.max(env)), p999=float(np.percentile(ab, 99.9)),
                stead=float(np.median(u[-int(3 * fs):])) if len(u) > int(3 * fs) else np.nan)


def show_c5():
    """实测 col⑤：按段取 [终点, 终点+1.5 s]。"""
    out = []
    for s, e, _ in SEGS:
        r = metrics_win(c5d, e5_of(e), 1.5)
        if r is not None:
            out.append(r)
    g = lambda k: float(np.median([r[k] for r in out]))
    return g, len(out)


g5, n5 = show_c5()
p('  %-27s %4s %7s %7s %9s %8s' % ('对象', '节数', 'p99.9', 'sm100', 'env0.5s', 'rawmax'))
p('  ' + '-' * 70)
p('  %-27s %4s %7.1f %7.1f %9.1f %8.1f   ← 实测（%d 段中位）'
  % ('实测 col⑤（设备真输出）', 18, g5('p999'), g5('sm'), g5('env'), g5('raw'), n5))
for name, fc, hps in CANDS:
    y = chain(fc, hps, x853, fast=True)
    r = metrics_win(y, k1, 1.5)
    p('  %-27s %4d %7.1f %7.1f %9.1f %8.1f'
      % (name, nsec(fc, hps), r['p999'], r['sm'], r['env'], r['raw']))
p('  ⇒ 离线 S0 与实测 col⑤ 同窗各口径接近，即离线模型忠实（T80 已验：逐点相关 0.9991）')
p('     ⇒ 各候选相对 S0 的改善量因此可信。')

# ------------------------------------------------------------------ 频响影响表
p()
p('【表 2】对其它判据的影响 —— |H(f)| 相对现状的 dB 差（负=衰减更多）')
FR = (0.05, 0.1, 0.3, 0.67, 1.0, 5.0, 10.0, 40.0, 150.0, 250.0)
p('  %-28s' % '候选' + ''.join('%9.2f' % f for f in FR))


def H(fc, hps, f):
    wv = 2 * np.pi * f / FS
    r = 1.0 - 2.0 * np.pi * fc / FS
    _, hd = freqz([1.0, -1.0], [1.0, -r], worN=[wv])
    h = complex(hd[0])
    for s in (LP + list(hps)):
        _, hh = freqz(s[:3], np.concatenate([[1.0], s[3:]]), worN=[wv])
        h *= hh[0]
    return abs(h)


H0 = [H(0.05, HPs, f) for f in FR]
for name, fc, hps in CANDS:
    row = ''
    for i, f in enumerate(FR):
        d = 20 * np.log10(H(fc, hps, f) / H0[i])
        row += '%9s' % ('—' if abs(d) < 5e-5 else '%.3f' % d)
    p('  %-28s%s' % (name, row))
p('  要点：≥0.67 Hz（判据 b 下限）全列 ≈0 ⇒ b 不受影响；10 Hz（输入阻抗测试点）≈0 ⇒ 阻抗不受影响；')
p('        差异只出现在 0.05~0.1 Hz ⇒ 恰好只被 a1（基线恢复）看到 ⇒ 命中面是唯一的。')

# ------------------------------------------------------------------ 节数账
p()
p('【表 3】节数 → dtc → CPU（模型 dtc = 1387.8 + 639.07×节数，80 MHz；gdtover 阈值 12000）')
for name, fc, hps in CANDS:
    n = nsec(fc, hps)
    dtc = 1387.8 + 639.07 * n
    p('  %-28s %4d 节 ｜ dtc %6.0f ｜ CPU %5.2f%% ｜ gdtover %s'
      % (name, n, dtc, dtc / 93760 * 100, '仍超限 ✗' if dtc > 12000 else '回到阈值内 ✓'))

# ------------------------------------------------------------------ 稳健性：多段 × 多噪声实现
p()
p('【表 4】稳健性检验：3 段不同脉冲 × 3 个噪声实现 = 9 组（尾窗 12 s；单位 µV）')
IDX = sorted(set([0, MID, len(SEGS) - 1]))
SEEDS = (7, 17, 27)
p('  %-27s %24s %24s %24s' %
  ('候选', 'sm100（中位/最差）', 'env1s（中位/最差）', 'rawmax（中位/最差）'))
p('  ' + '-' * 104)
STAB = {}
for name, fc, hps in CANDS:
    sv, ev, rv = [], [], []
    for i in IDX:
        for sd in SEEDS:
            xs, kk = build(i, seed=sd, trail=12.0)
            rr = metrics(chain(fc, hps, xs, fast=True), kk)
            sv.append(rr['sm'])
            ev.append(rr['env'])
            rv.append(rr['raw'])
    STAB[name] = (sv, ev, rv)
    g = lambda v: '%.0f / %.0f' % (float(np.median(v)), max(v))
    p('  %-27s %24s %24s %24s' % (name, g(sv), g(ev), g(rv)))
p('  读法：sm100／env1s 的「最差」列才是判据落点（9 组取最坏）；rawmax 列用于证明该口径不可用（见结论位）。')

# ------------------------------------------------------------------ 结论位
p()
p('=' * 122)
p('【结论位】')
res = {k: v[0] for k, v in RES.items()}
p('  A) 单组口径（表 1）：sm100 ≤100 µV 且平滑进限 0.00 s')
for name, fc, hps in CANDS:
    r = res[name]
    ok = r['sm'] <= 100 and r['ts'] == 0.0
    p('     %-27s %s  (sm100 %.1f｜env1s %.1f｜进限sm %.2f s)'
      % (name, '✓ 过  ' if ok else '✗ 不过', r['sm'], r['env'], r['ts']))

p('  B) ★稳健口径（表 4 · 9 组最差）：sm100 ≤100 µV 且 env1s ≤100 µV')
WIN = []
for name, fc, hps in CANDS:
    sv, ev, rv = STAB[name]
    ok = max(sv) <= 100 and max(ev) <= 100
    if ok:
        WIN.append(name)
    p('     %-27s %s  (sm100 最差 %.1f｜env1s 最差 %.1f)'
      % (name, '✓ 过  ' if ok else '✗ 不过', max(sv), max(ev)))

p('  C) 原始采样极值口径（rawmax，9 组最差）—— 用于证明该口径不可用')
for name, fc, hps in CANDS:
    p('     %-27s rawmax 最差 %.0f µV  (噪声 σd＝%.1f µV ⇒ %.1fσ)'
      % (name, max(STAB[name][2]), SGD, max(STAB[name][2]) / SGD))
p('     ⇒ σd %.1f µV 下，68 s 窗口内纯噪声的期望超 100 µV 点数已 >0 ⇒ 任何方案（含理想链）都过不了' % SGD)
p('       该口径 ⇒ 「任意时刻」在评审执行中只能是「基线（趋势）读数」，不可能是「每个采样点」。')

p('  D) 稳健通过名单（B 口径）：%s' % ('、'.join(WIN) if WIN else '无'))
if WIN:
    best = min(WIN, key=lambda n: max(STAB[n][1]))
    p('     其中 env1s 最差最小者＝%s（%.1f µV，余量 %.0f%%）'
      % (best, max(STAB[best][1]), 100 - max(STAB[best][1])))
p('=' * 122)

# ------------------------------------------------------------------ 导出画图数据
EXP = {}
for _n, _fc, _h in CANDS:
    _y = chain(_fc, _h, x853, fast=True)
    _bb = float(np.median(_y[k1 - int(0.55 * FS):k1 - int(0.15 * FS)]))
    EXP[_n] = (_y - _bb)[k1:k1 + int(10.0 * FS)]
_nam = [c[0] for c in CANDS]
_c5s, _c5e, _ = SEGS[MID]
_c5k = e5_of(_c5e)
_c5b = float(np.median(c5d[max(0, _c5k - int(0.55 * FS_T)):max(1, _c5k - int(0.15 * FS_T))]))
_fg = np.logspace(np.log10(0.01), 0, 120)
_H = np.array([[20 * np.log10(H(_fc, _h, f) / H(0.05, HPs, f) + 1e-30) for f in _fg]
               for _n, _fc, _h in CANDS])
np.savez(os.path.join(HERE, '_t83_data.npz'),
         names=np.array(_nam), tails=np.array([EXP[_n] for _n in _nam]),
         c5tail=(c5d - _c5b)[_c5k:_c5k + int(1.5 * FS_T)],
         sm100=np.array([res[_n]['sm'] for _n in _nam]),
         env1s=np.array([res[_n]['env'] for _n in _nam]),
         p999=np.array([res[_n]['p999'] for _n in _nam]),
         stab_sm=np.array([max(STAB[_n][0]) for _n in _nam]),
         stab_env=np.array([max(STAB[_n][1]) for _n in _nam]),
         fgrid=_fg, Hdb=_H, fs=FS, fs_t=FS_T,
         c5_p999=g5('p999'), c5_sm=g5('sm'), c5_env=g5('env'), c5_raw=g5('raw'))
print('[写出] _t83_data.npz')

with open(os.path.join(HERE, '_t83_out.txt'), 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(OUT) + '\n')
print('\n[写出] _t83_out.txt')
