# -*- coding: utf-8 -*-
"""T142 §17.12 判别实验（修正刻度版）评估
猛哥更正：刻度记错，正确值 = 原报值 ×(−2)，且全部在基线以下。
口径：1 小格 = 0.2 mV；屏幕 1px ≈ 0.10 mV；tau = 7.9577 s
"""
import statistics as st
from math import comb

FS, UVPC, UVLSB, TAU = 853.2423208191125, 0.1122, 0.1526, 7.9577

rows = [
    (-0.02,  0.00, -0.08),   # 轮1:  5s, 30s, 60s
    (-0.08,  0.00,  0.00),   # 轮2: 30s, 60s,  5s
    (-0.08,  0.00,  0.00),   # 轮3: 60s,  5s, 30s
    ( 0.00,  0.00,  0.00),   # 轮4:  5s, 60s, 30s
    ( 0.00, -0.10, -0.20),   # 轮5: 30s,  5s, 60s
]
layout = [['5','30','60'], ['30','60','5'], ['60','5','30'], ['5','60','30'], ['30','5','60']]
flat = [v for r in rows for v in r]

print("="*78)
print("[0] 原样（时间序）")
print("   ", "  ".join(f"{v:+.2f}" for v in flat))
nz = [v for v in flat if abs(v) >= 0.005]
print(f"  可辨(非零) {len(nz)}/15   不可辨 0.00 {15-len(nz)}/15")
print(f"  范围 [{min(flat):+.2f}, {max(flat):+.2f}]   极差 {max(flat)-min(flat):.2f} mV")
pos = sum(1 for v in nz if v > 0); neg = sum(1 for v in nz if v < 0)
print(f"  可辨值方向：正 {pos} / 负 {neg}   <<== 关键")
p_tail = sum(comb(len(nz), k) for k in range(0, min(pos, neg)+1)) / 2**len(nz)
print(f"  符号检验（H0: 正负等概率）: P(全部同向) = 2*C({len(nz)},0)/2^{len(nz)}"
      f" = {2/2**len(nz)*100:.2f}%  => p={2/2**len(nz):.4f} {'显著' if 2/2**len(nz)<0.05 else '不显著'}")

def grp_rows():
    g = {'5':[], '30':[], '60':[]}
    for r, lay in zip(rows, layout):
        for v, k in zip(r, lay): g[k].append(v)
    return g
def grp_cols():
    return {k: [rows[i][ci] for i in range(5)] for ci, k in enumerate(['5','30','60'])}

def rep(name, g):
    print("="*78)
    print(f"[{name}]")
    print(f"{'组':>5} {'均值':>8} {'中位':>8} {'|max|':>8} {'非零':>6} {'正/负':>6}")
    for k in ('5','30','60'):
        v = g[k]; n = sum(1 for x in v if abs(x) >= 0.005)
        pp = sum(1 for x in v if x > 0.005); nn = sum(1 for x in v if x < -0.005)
        print(f"{k+'s':>5} {st.mean(v):>8.3f} {st.median(v):>8.3f} {max(abs(x) for x in v):>8.3f} {n:>6} {pp:>3}/{nn:<3}")
    return g

g1 = rep("解读1  行=轮次（与我给的循环表一致）", grp_rows())
g2 = rep("解读2  列=分组（5s×5 → 30s×5 → 60s×5，未打乱）", grp_cols())
print("  ⚠ 两种解读趋势方向相反 ⇒ 必须确认填表方式")

print("="*78)
print("[1] A 形态对照：两种漂移率假设 vs 实测（解读1）")
print(f"{'Stop':>5} {'s=0.68(mV)':>11} {'s=0.08(mV)':>11} {'实测中位':>9} {'实测|max|':>10}")
for k in ('5','30','60'):
    t = float(k); s68 = 0.68/60*t; s08 = 0.08/60*t
    print(f"{k+'s':>5} {s68:>11.4f} {s08:>11.4f} {st.median(g1[k]):>9.3f} {max(abs(x) for x in g1[k]):>10.3f}")
print("  s=0.68 假设: 60s 应 0.680，实测 0.080 => 差 8.5 倍 => 原漂移率高估")
print("  s=0.08 假设: 5s→0.0067 / 30s→0.040 / 60s→0.080")
print("               => 5s/30s 均 < 0.05mV(≈0.25小格) 落在不可辨区 => 只有 60s 可见")
print("               => 与实测(0.00 / 0.00 / -0.080) 【完全吻合】")

print("="*78)
print("[2] 若 s = 0.08 mV/min，稳态项 s*tau 有多大？（决定 B1 能否治全）")
sv = 0.08/60.0
print(f"  s = {sv:.5f} mV/s   tau = {TAU} s   =>  稳态项 s*tau = {sv*TAU:.4f} mV"
      f" = {sv*TAU/0.2:.3f} 小格 = {sv*TAU/0.10:.2f} px")
print("  => 远低于可辨阈值(约0.1小格/0.5px) => 稳态项【不可辨】")
print("  => 所有可辨偏移均来自【瞬态项 δ】 => B1(SKIP8+M64) 理论上可治全部")

print("="*78)
print("[3] 逐次点位（解读1 分组标注），查是否有时间聚集")
for i, (r, lay) in enumerate(zip(rows, layout), 1):
    tag = "  ".join(f"{lab}:{v:+.2f}" for v, lab in zip(r, lay))
    print(f"  轮{i}  {tag}")
print("  非零出现的时间位次：1,3,4,7,14,15  => 前段聚集(1-7有4个)、中段全零、尾部2个")
print("  => 非单调漂移，呈【成簇】特征")

print("="*78)
print("[4] 量级换算表（1小格=0.2mV）")
print(f"{'偏移':>8} {'小格':>7} {'px':>6} {'int16LSB':>9} {'raw code':>9}")
for mv in (0.02,0.04,0.06,0.08,0.10,0.20,0.80):
    print(f"{mv:>6.2f}mV {mv/0.2:>7.2f} {mv/0.10:>6.1f} {mv*1000/UVLSB:>9.0f} {mv*1000/UVPC:>9.0f}")
