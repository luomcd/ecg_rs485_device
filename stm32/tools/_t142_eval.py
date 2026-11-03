# -*- coding: utf-8 -*-
"""T142 §17.11 判别实验结果评估（只读分析）
输入：猛哥 15 次实测偏移（mV，含方向）
口径：屏幕 1 小格 = 0.2 mV；中心线以上为正
"""
import statistics as st

FS, UVPC, UVLSB = 853.2423208191125, 0.1122, 0.1526
TAU = 7.9577

# 按循环法填入的原始表（行 = 轮次，列 = 该轮第 1/2/3 次）
rows = [
    (-0.01, 0.00, 0.04),   # 轮1:  5s, 30s, 60s
    ( 0.04, 0.00, 0.00),   # 轮2: 30s, 60s,  5s
    ( 0.04, 0.00, 0.00),   # 轮3: 60s,  5s, 30s
    ( 0.00, 0.00, 0.00),   # 轮4:  5s, 60s, 30s
    ( 0.00, 0.05, 0.10),   # 轮5: 30s,  5s, 60s
]
layout = [['5','30','60'], ['30','60','5'], ['60','5','30'], ['5','60','30'], ['30','5','60']]

flat = [v for r in rows for v in r]
print("="*76)
print("[0] 原样列出（时间序，共 15 次）")
print("   ", "  ".join(f"{v:+.2f}" for v in flat))
nz = sum(1 for v in flat if abs(v) >= 0.005)
print(f"  零值 {15-nz}/15 = {(15-nz)/15*100:.0f}%   非零 {nz}/15")
print(f"  范围 [{min(flat):+.2f}, {max(flat):+.2f}] 极差 {max(flat)-min(flat):.2f} mV")

def group_rows():
    g = {'5':[], '30':[], '60':[]}
    for r, lay in zip(rows, layout):
        for v, k in zip(r, lay):
            g[k].append(v)
    return g

def group_cols():
    g = {'5':[], '30':[], '60':[]}
    for ci, k in enumerate(['5','30','60']):
        g[k] = [rows[ri][ci] for ri in range(5)]
    return g

def report(name, g):
    print("="*76)
    print(f"[{name}] 按 Stop 时长分组")
    print(f"{'组':>5} {'n':>3} {'均值':>8} {'中位':>8} {'最大':>8} {'非零数':>7} {'正/负':>7}")
    for k in ('5','30','60'):
        v = g[k]
        pos = sum(1 for x in v if x > 0.005); neg = sum(1 for x in v if x < -0.005)
        print(f"{k+'s':>5} {len(v):>3} {st.mean(v):>8.3f} {st.median(v):>8.3f} {max(v):>8.3f}"
              f" {sum(1 for x in v if abs(x)>=0.005):>7} {pos:>3}/{neg:<3}")
    m5, m30, m60 = st.median(g['5']), st.median(g['30']), st.median(g['60'])
    print(f"  -> 中位差 60s-5s = {m60-m5:.3f} mV = {(m60-m5)/0.2:.2f} 小格")
    return g

g1 = report("解读1 行=轮次（推荐，与循环法一致）", group_rows())
g2 = report("解读2 列=分组（另一种可能）", group_cols())

print("="*76)
print("[1] A 形态（δ = 漂移率 × Stop 时长）预期 vs 实测")
s_prev = 0.68/60.0     # 上一轮假设漂移率 0.68 mV/min 换算 mV/s
print(f"{'Stop':>6} {'A预期(mV)':>11} {'A预期(小格)':>12} {'实测中位(mV)':>13} {'差值倍数':>9}")
for k in ('5','30','60'):
    t = float(k)
    exp = s_prev*t
    obs = st.median(g1[k])
    ratio = exp/obs if obs > 0.005 else float('inf')
    rs = f"{ratio:.0f}x" if ratio != float('inf') else "inf"
    print(f"{k+'s':>6} {exp:>11.3f} {exp/0.2:>12.2f} {obs:>13.3f} {rs:>9}")
print("  -> 60s 处 A 预期 0.68mV(=3.4小格)，实测中位 0.04mV(=0.2小格) => 差 17 倍 => A 不成立")

print("="*76)
print("[2] 反解：δ(60s) 上界 -> 今日有效漂移率上界")
for k, v in (('60', max(g1['60'])), ('60', st.median(g1['60']))):
    s = v/float(k)
    print(f"  若 {k}s 的偏移 {v:.2f}mV 全由漂移贡献: s = {s:.5f} mV/s"
          f" = {s*60:.3f} mV/min  (上一轮假设 0.68 mV/min 的 {s*60/0.68*100:.0f}%)")

print("="*76)
print("[3] 分辨力体检：这批数据能分辨什么？")
px_per_mv = 1.0/0.1      # 由 §16 换算表：1px ≈ 0.1mV
print(f"  屏幕垂直分辨力 ~1px ≈ 0.10 mV = 0.5 小格")
print(f"  本批最小非零读数 0.04 mV = {0.04*px_per_mv:.2f} px = {0.04/0.2:.2f} 小格")
print("  -> 0.01/0.04/0.05 级读数 细于 1px，若为目视估读则落在估读噪声内")
print("  -> 必须确认读数来源（数值显示 or 目视估读），否则 [1][2] 的判读精度不成立")

print("="*76)
print("[4] 与历史量级对比")
print(f"{'日期/场景':>22} {'偏移(mV)':>10} {'小格':>7} {'px':>6}")
for name, mv in (("§16 早前 典型低值", 0.09), ("§16 早前 高值", 0.80), ("本次 15 次最大", max(flat))):
    print(f"{name:>22} {mv:>10.2f} {mv/0.2:>7.2f} {mv*px_per_mv:>6.1f}")
print(f"  -> 本次最大 {max(flat):.2f}mV 仅为早前高值 0.80mV 的 {max(flat)/0.80*100:.0f}%")
print("  -> 早前 0.8mV 属【偶发事件】，非常规表现")
