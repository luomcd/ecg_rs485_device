# -*- coding: utf-8 -*-
"""T142 §17.13 口径纠正 + 重算
纠正点：纵向 px 换算。
  代码依据 Rendering/EcgWaveformControl.cs:120-129
    满量程毫米数 = 2×5mV×5mm/mV = 50 mm；mmY = plotHeight/50；
    注释 :129 「1280 宽窗口下 纵向 6.1 px / 横向 6.9 px」
  => 1 小格(=1mm=0.2mV) = 6.1 px  =>  1 px = 0.0328 mV   (≠ 上轮误用的 0.10 mV/px)
  => 0.02mV = 0.61px(亚像素,不可读) / 0.08mV = 2.44px(勉强) / 0.10mV = 3.05px / 0.20mV = 6.10px(1小格,清楚)
"""
from math import comb
import statistics as st

PX_PER_MM   = 6.1          # 纵向（代码注释实测值）
MV_PER_MM   = 0.2          # 1 小格 = 0.2 mV
MV_PER_PX   = MV_PER_MM / PX_PER_MM

print("="*78)
print("[0] 纵向分辨力口径（纠正后）")
print(f"  1 小格 = {MV_PER_MM} mV = {PX_PER_MM} px  =>  1 px = {MV_PER_PX:.4f} mV")
print(f"{'读数(mV)':>10} {'小格':>7} {'px':>7} {'目视可读性':>12}")
for mv, tag in ((0.02,'亚像素 ✗'),(0.08,'勉强 △'),(0.10,'可读 ✓'),(0.20,'清楚 ✓✓'),(0.80,'极明显 ✓✓✓')):
    print(f"{mv:>10.2f} {mv/MV_PER_MM:>7.2f} {mv/MV_PER_PX:>7.2f} {tag:>12}")
print("  => 目视可靠下限 ≈ 0.5 小格 = 0.10 mV；0.02 mV 落在估读噪声内，应视作 ≈0")

rows = [
    (-0.02,  0.00, -0.08),
    (-0.08,  0.00,  0.00),
    (-0.08,  0.00,  0.00),
    ( 0.00,  0.00,  0.00),
    ( 0.00, -0.10, -0.20),
]
layout = [['5','30','60'], ['30','60','5'], ['60','5','30'], ['5','60','30'], ['30','5','60']]
THR = 0.05     # = 0.25 小格，低于此视为不可辨

print("="*78)
print("[1] 剔除「亚像素读数(0.02)」后重算 —— 行=轮次（已确认）")
g = {'5':[], '30':[], '60':[]}
for r, lay in zip(rows, layout):
    for v, k in zip(r, lay): g[k].append(v)
print(f"{'组':>5} {'全部5次':>26} {'可信(>=0.05)':>16}")
for k in ('5','30','60'):
    v = g[k]
    cred = [x for x in v if abs(x) >= THR]
    print(f"{k+'s':>5} {str(v):>26} n={len(cred)} {cred}")
    print(f"      中位={st.median(v):+.3f}  可信中位={st.median(cred) if cred else 0:+.3f}"
          f"  可信均值={st.mean(cred) if cred else 0:+.3f}")

cred_all = [x for x in (v for v in sum((g[k] for k in ('5','30','60')), [])) if abs(x) >= THR]
pos = sum(1 for x in cred_all if x > 0); neg = sum(1 for x in cred_all if x < 0)
print("="*78)
print("[2] 符号检验（只用可信读数，剔除 0.02 亚像素点）")
print(f"  可信非零 n={len(cred_all)}  正 {pos} / 负 {neg}   值 = {cred_all}")
print(f"  P(全部同向) = 2/2^{len(cred_all)} = {2/2**len(cred_all)*100:.2f}%  => p={2/2**len(cred_all):.4f}"
      f"  {'显著' if 2/2**len(cred_all)<0.05 else '边缘/不显著'}")

print("="*78)
print("[3] A 形态（δ = s × Stop 时长），s 由 60s 组反解")
s_obs = 0.08/60.0
print(f"  60s 组可信中位 0.08 mV  =>  s = {s_obs:.5f} mV/s = {s_obs*60:.3f} mV/min")
print(f"{'Stop':>5} {'预期 δ(mV)':>11} {'= 小格':>8} {'= px':>7} {'实测可信非零':>16}")
for k in ('5','30','60'):
    d = s_obs*float(k)
    cred = [x for x in g[k] if abs(x) >= THR]
    print(f"{k+'s':>5} {d:>11.4f} {d/MV_PER_MM:>8.2f} {d/MV_PER_PX:>7.2f} {str(cred):>16}")
print("  => 5s/30s 预期 0.007/0.040 mV = 0.6/1.2 px => 均落在不可辨区")
print("  => 与实测「5s/30s 基本为 0、仅 60s 可见」一致")

print("="*78)
print("[4] 现象2 重判：0.09 mV「看起来不回零」能否与模型相容？")
TAU = 7.9577
for t in (5, 10, 15, 20, 30):
    v = 0.09 * pow(2.718281828, -t/TAU)
    print(f"  t={t:>2}s 残留 = 0.09*exp(-t/7.9577) = {v:.4f} mV = {v/MV_PER_PX:>4.2f} px"
          f" {'← 已不可辨' if v/MV_PER_PX < 0.5 else ''}")
v10 = 0.09 * pow(2.718281828, -10/TAU)
print(f"  => 10 s 内就从 2.74 px 掉到 {v10/MV_PER_PX:.2f} px")
print("  => 若猛哥只看了十几秒『感觉没动』，与瞬态模型【不矛盾】")
print("  => 但若确认真不衰减 => 稳态项 0.09 => s=0.68mV/min，与本次 0.08 差 8.5 倍 => 需轨迹实验分辨")
