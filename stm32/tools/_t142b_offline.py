# -*- coding: utf-8 -*-
"""T142b 离线数值验证：预热跳帧(SKIP) 是否真能消除「首帧异常 ⇒ 冲激 ⇒ 长尾偏移」

复刻对象（Filter/ecg_dc_block.c，改动后）：
    if (!warmed) {
        x_prev = x;
        if (warm_skip > 0) { warm_skip--; return 0; }
        warmed = 1; return 0;
    }
    y = (x - x_prev) + r * y_prev;  x_prev = x;  y_prev = y;

四节：
  §0 回归对照：SKIP=0 的新逻辑 vs 旧逻辑 ⇒ 输出必须**逐位相等**（证明未动路径零风险）
  §1 单帧异常（前 1 帧偏高 δ）—— 扫描 δ
  §2 建立期异常（前 5 帧偏高 δ，对应 datasheet ts-ECG = 5 样本）
  §3 叠加噪声（检验 SKIP 不会把噪声放大）
  §4 斜坡输入（稳态项）—— 验证 SKIP **不改变稳态**（只影响预热期）
"""
import math
import random

FS = 853.2423208191125
FC = 0.02                       # 当前 A+B 档 ECG_DC_BLOCK_FC_HZ_F
R = 1.0 - 2.0 * math.pi * FC / FS
TAU_S = 1.0 / (2.0 * math.pi * FC)
UVPC = 0.1122                   # uV / code
A_CENTER = 6075000.0            # ADS1293 24-bit 中心码（dc_block.h 注释）


def mv2code(mv):
    return mv * 1000.0 / UVPC


def code2mv(c):
    return c * UVPC / 1000.0


def old_block(xs):
    """旧逻辑：仅 1 帧预热"""
    x_prev = 0.0
    y_prev = 0.0
    warmed = False
    out = []
    for x in xs:
        if not warmed:
            x_prev = x
            warmed = True
            out.append(0.0)
            continue
        y = (x - x_prev) + R * y_prev
        x_prev = x
        y_prev = y
        out.append(y)
    return out


def new_block(xs, skip):
    """新逻辑：SKIP 帧预热"""
    x_prev = 0.0
    y_prev = 0.0
    warmed = False
    warm_skip = skip
    out = []
    for x in xs:
        if not warmed:
            x_prev = x
            if warm_skip > 0:
                warm_skip -= 1
                out.append(0.0)
                continue
            warmed = True
            out.append(0.0)
            continue
        y = (x - x_prev) + R * y_prev
        x_prev = x
        y_prev = y
        out.append(y)
    return out


def flat(v, n):
    return [v] * n


def stat(ys, label):
    """返回 (前1s均值, 5s时均值, 20s时均值, 全局最大绝对值)"""
    def win(a, b):
        seg = ys[a:b]
        return sum(seg) / len(seg) if seg else 0.0
    n1 = int(1 * FS)
    w1 = win(0, n1)
    w5 = win(int(5 * FS) - n1 // 2, int(5 * FS) + n1 // 2)
    w20 = win(int(20 * FS) - n1 // 2, int(20 * FS) + n1 // 2)
    return w1, w5, w20, max(abs(v) for v in ys)


def show(rows, title):
    print('-' * 82)
    print(title)
    print('%-26s %12s %12s %12s %12s' % ('场景', '前1s偏移', '5s偏移', '20s偏移', '峰值'))
    print('%-26s %12s %12s %12s %12s' % ('', '(mV)', '(mV)', '(mV)', '(mV)'))
    for name, ys in rows:
        w1, w5, w20, pk = stat(ys, name)
        print('%-26s %12.4f %12.4f %12.4f %12.4f'
              % (name, code2mv(w1), code2mv(w5), code2mv(w20), code2mv(pk)))


N30 = int(30 * FS)
print('=' * 82)
print('[T142b 离线验证]  FS=%.4f Hz  fc=%.2f Hz  r=%.9f  tau=%.4f s' % (FS, FC, R, TAU_S))
print('=' * 82)

# ---------------- §0 回归对照：SKIP=0 新旧必须逐位相等 ----------------
print('\n§0 回归对照：SKIP=0 时新逻辑 vs 旧逻辑（要求逐位相等）')
random.seed(20260928)
probe = [A_CENTER + random.uniform(-500, 500) for _ in range(N30)]
a = old_block(probe)
b = new_block(probe, 0)
same = (a == b)
n_diff = sum(1 for p, q in zip(a, b) if p != q)
print('   样本数 %d，逐位比较：%s（不同 %d 个）'
      % (len(a), '完全相等 ✓' if same else 'X 不相等', n_diff))
assert same, 'SKIP=0 未与旧逻辑等价！'

# ---------------- §1 单帧异常 ----------------
print('\n§1 单帧异常（仅第 1 帧偏高 δ，其余正常）')
rows = []
for mv in (0.05, 0.10, 0.20, 0.80):
    d = mv2code(mv)
    xs = flat(A_CENTER + d, 1) + flat(A_CENTER, N30 - 1)
    rows.append(('δ=%.2fmV | SKIP=0' % mv, old_block(xs)))
    rows.append(('δ=%.2fmV | SKIP=8' % mv, new_block(xs, 8)))
show(rows, '  表 1（前 1 帧异常）')

# ---------------- §2 建立期异常（前 5 帧）----------------
print('\n§2 建立期异常（前 5 帧 = ts-ECG 未建立，偏高 δ）')
rows = []
for mv in (0.10, 0.80):
    d = mv2code(mv)
    xs = flat(A_CENTER + d, 5) + flat(A_CENTER, N30 - 5)
    rows.append(('δ=%.2fmV x5帧 | SKIP=0' % mv, old_block(xs)))
    rows.append(('δ=%.2fmV x5帧 | SKIP=8' % mv, new_block(xs, 8)))
    rows.append(('δ=%.2fmV x5帧 | SKIP=12' % mv, new_block(xs, 12)))
show(rows, '  表 2（前 5 帧异常；SKIP=8 覆盖 5 帧，SKIP=12 为过覆盖对照）')

# ---------------- §3 噪声场景 ----------------
print('\n§3 叠加噪声（raw 单样本 σ=50 codes，δ=0.2mV 单帧异常）')
rows = []
for tag in ('无异常', '有异常'):
    xs = []
    random.seed(777)
    for i in range(N30):
        base = A_CENTER + (mv2code(0.2) if (tag == '有异常' and i == 0) else 0.0)
        xs.append(base + random.gauss(0, 50))
    rows.append(('σ50 %s | SKIP=0' % tag, old_block(xs)))
    rows.append(('σ50 %s | SKIP=8' % tag, new_block(xs, 8)))
show(rows, '  表 3（噪声场景）')

# ---------------- §4 斜坡（稳态项）----------------
print('\n§4 斜坡输入（检验 SKIP 不改变稳态 —— 只动预热期）')
s_mv_min = 0.08                     # 本次实测反解漂移率
s_code_per_sample = mv2code(s_mv_min) / 60.0 / FS
print('   漂移率 %.2f mV/min = %.6f code/样本' % (s_mv_min, s_code_per_sample))
xs = [A_CENTER + s_code_per_sample * i for i in range(N30)]
rows = [('斜坡 | SKIP=0', old_block(xs)), ('斜坡 | SKIP=8', new_block(xs, 8))]
show(rows, '  表 4（斜坡；稳态项 y_ss = s*(1/(1-r)) 应两版一致）')
y_ss_theory = s_code_per_sample / (1.0 - R)
print('   理论稳态 y_ss = s/(1-r) = %.2f code = %.4f mV = %.3f 小格'
      % (y_ss_theory, code2mv(y_ss_theory), code2mv(y_ss_theory) / 0.2))

# ---------------- §5 汇总判据 ----------------
print('\n§5 汇总：SKIP=8 对「首帧异常」的抑制比')
print('%-14s %18s %18s %10s' % ('异常幅度', 'SKIP=0 前1s(mV)', 'SKIP=8 前1s(mV)', '抑制比'))
for mv in (0.05, 0.10, 0.20, 0.80):
    d = mv2code(mv)
    xs = flat(A_CENTER + d, 1) + flat(A_CENTER, N30 - 1)
    w0 = stat(old_block(xs), '')[0]
    w8 = stat(new_block(xs, 8), '')[0]
    rat = (abs(w0) / abs(w8)) if abs(w8) > 1e-9 else float('inf')
    print('%-14s %18.4f %18.4f %10s'
          % ('%.2f mV' % mv, code2mv(w0), code2mv(w8),
             ('%.0fx' % rat) if rat != float('inf') else '>1e6x'))
print('=' * 82)
