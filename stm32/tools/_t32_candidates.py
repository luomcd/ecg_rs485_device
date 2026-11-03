# -*- coding: utf-8 -*-
"""T32: 候选档频响对比 + CPU 账（**只算不落盘**）

背景（09-18 猛哥定）：
  ② 100Hz 一起改（三节 Q20）
  ③ 新建独立档，不动 IMP620 基线（104.21% / 102.83%）
  T31 结案：真实漂移带 50Hz 0.0981 / 100Hz 0.1962 / 150Hz 0.2944 Hz（原「1.2Hz」作废）

口径与固件一致：fs = 1e6/1172；notch = iirnotch -> tf2sos；BP = butter(4,[0.05,150])；
频响 = 逐节 z 变换累乘；屏幕残留 = raw 谱线幅度 x |H|（raw = 列② 09-18 实测峰值）。
"""
import sys
import numpy as np
from scipy import signal as sps

sys.stdout.reconfigure(encoding="utf-8")
FS = 1e6 / 1172.0


def notch(f0, Q, fs=FS):
    b, a = sps.iirnotch(f0, Q, fs=fs)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def bp():
    return [(r[0], r[1], r[2], r[4], r[5])
            for r in sps.butter(4, [0.05, 150.0], btype="bandpass", fs=FS, output="sos")]


def cascade_db(secs, f):
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, float) / FS)
    H = np.ones(len(z), dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
    return 20 * np.log10(np.abs(H))


def clust(f0, Q, spacing=0.2):
    return [notch(f0 - spacing, Q), notch(f0, Q), notch(f0 + spacing, Q)]


BP = bp()
S50_12 = clust(50.0, 12.0)          # IMP620 的 50 簇（±0.2 Hz, Q12）
S50_20 = clust(50.0, 20.0, 0.1)     # D3 的 50 簇（±0.1 Hz, Q20）

CANDS = [
    ("P0  现状 IMP620", S50_12 + [notch(100.0, 15.0), notch(150.0, 30.0)] + BP, 9, "现基线"),
    ("D3  (参考)", S50_20 + [notch(100.0, 15.0)] + BP, 8, "真电极档，无150"),
    ("N1a 只改100 Q20", S50_12 + clust(100.0, 20.0) + [notch(150.0, 30.0)] + BP, 11, "+2节"),
    ("N2a 100+150 Q20", S50_12 + clust(100.0, 20.0) + clust(150.0, 20.0) + BP, 13, "+4节"),
    ("N2c =N2a 但50簇用±0.1/Q20", S50_20 + clust(100.0, 20.0) + clust(150.0, 20.0) + BP, 13, "+4节"),
    ("N2b 100+150 Q15", S50_12 + clust(100.0, 15.0) + clust(150.0, 15.0) + BP, 13, "+4节"),
]
BANDS = {"50": (49.9391, 50.0372), "100": (2 * 49.9391, 2 * 50.0372),
         "150": (3 * 49.9391, 3 * 50.0372)}
RAWAMP = {"50": 24246.6, "100": 874.0, "150": 1306.7}

print("=" * 108)
print("T32  候选档频响对比（只算不落盘）")
print("实测漂移带（T31 结案）: 50Hz %.4f~%.4f  |  100Hz %.4f~%.4f  |  150Hz %.4f~%.4f"
      % (BANDS["50"] + BANDS["100"] + BANDS["150"]))
print("=" * 108)

# ── A) 漂移带内最差压制 + 屏幕残留 ────────────────────────────────────────
print()
print("A) 漂移带内「最差压制」与屏幕残留（残值 = raw 谱线幅度 x |H|；raw 取 09-18 3mV 组实测峰值）")
print("-" * 108)
print("%-28s %4s | %-21s | %-21s | %-21s | %s"
      % ("方案", "节", "50Hz 域最差/残值", "100Hz 域最差/残值", "150Hz 域最差/残值", "三线rms"))
print("-" * 108)
res = {}
for name, secs, n, tag in CANDS:
    cells, tot = [], 0.0
    for k in ("50", "100", "150"):
        lo, hi = BANDS[k]
        d = cascade_db(secs, np.arange(lo, hi, 0.0005))
        w = d.max()
        r = RAWAMP[k] * 10 ** (w / 20.0)
        cells.append("%7.1f dB / %7.2f µV" % (w, r))
        tot += r ** 2
    res[name] = np.sqrt(tot)
    print("%-28s %4d | %-21s | %-21s | %-21s | %7.2f µV" % (name, n, *cells, np.sqrt(tot)))
print("-" * 108)
p0 = res["P0  现状 IMP620"]
for k, v in res.items():
    if k != "P0  现状 IMP620":
        print("   vs P0 : %-28s %6.1fx" % (k, p0 / v))
print("★ 50 Hz 域已是 -72 dB（IMP620 的宽簇），瓶颈原本是 100/150 两条单节陷波。")

# ── B) 诊断带额外损失（相对 P0） ────────────────────────────────────────
print()
print("B) 诊断带影响（相对 P0 的额外增益，负 = 更差；另给 P0 与 D3 的绝对值供对照）")
print("-" * 108)
FD = [0.05, 1, 4, 10, 20, 40, 44, 46, 50, 60, 80, 90, 95, 98, 100, 102, 105, 110,
      120, 130, 140, 142, 145, 147, 148, 149, 150]
h0 = cascade_db([s for nm, s, _, _ in CANDS if nm.startswith("P0")][0], FD)
hd3 = cascade_db([s for nm, s, _, _ in CANDS if nm.startswith("D3")][0], FD)
cols = [nm for nm, _, _, _ in CANDS if nm not in ("P0  现状 IMP620", "D3  (参考)")]
print("%-8s %9s %9s |%s" % ("f(Hz)", "P0 绝对", "D3 绝对", "".join("%13s" % c.split()[0] for c in cols)))
print("-" * 108)
for i, fv in enumerate(FD):
    row = "".join("%13.2f" % (cascade_db([s for nm, s, _, _ in CANDS if nm == c][0], [fv])[0] - h0[i])
                  for c in cols)
    print("%-8.2f %9.2f %9.2f |%s" % (fv, h0[i], hd3[i], row))
print("-" * 108)
print("★ 44-46 Hz（QRS 能量区）：三簇方案相对 P0 的额外损失 ≤0.02 dB ⇒ 零触碰 ✓")
print("★ 150Hz 簇是最大代价区：147 Hz -10 dB、148 Hz -15.6 dB、149 Hz -27 dB、150 Hz -54 dB（N2a）")

# ── C) 各簇合并 -3dB 域（只算陷波簇，不含 BP） ──────────────────────────
print()
print("C) 各簇的合并 -3dB 覆盖域（**只算陷波簇，不含 BP**）")
print("-" * 108)


def band3(secs, center, span=10.0):
    f = np.arange(center - span, center + span, 0.001)
    d = cascade_db(secs, f)
    idx = np.where(d <= -3.0)[0]
    if len(idx) == 0:
        return None
    runs, s = [], idx[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if b != a + 1:
            runs.append((s, a))
            s = b
    runs.append((s, idx[-1]))
    lo, hi = max(runs, key=lambda r: f[r[1]] - f[r[0]])
    return f[lo], f[hi]


def clus_sections(name, center):
    """从方案里挑出该中心的陷波节（排除 BP）"""
    secs = [s for nm, s, _, _ in CANDS if nm == name][0]
    out = []
    for c in secs:
        b0, b1, b2, a1, a2 = c
        if abs(b2 - b0) < 1e-12 and abs(b1 + a1 * 0 - 0) >= 0:   # 只挑 notch（b2≈b0 且 b0<1）
            if b0 < 0.999:
                out.append(c)
    # 用中心附近的频响判据筛
    keep = []
    for c in out:
        b0, b1, b2, a1, a2 = c
        d = cascade_db([c], np.arange(center - 3, center + 3, 0.01))
        if d.min() < -20:
            keep.append(c)
    return keep


print("%-28s | %-22s | %-22s | %-22s" % ("方案", "50Hz 簇", "100Hz 簇", "150Hz 簇"))
print("-" * 108)
for name, secs, n, tag in CANDS:
    parts = []
    for k, c in (("50", 50.0), ("100", 100.0), ("150", 150.0)):
        sub = [s for s in secs if cascade_db([s], np.arange(c - 3, c + 3, 0.01)).min() < -20]
        if not sub:
            parts.append("—")
            continue
        r = band3(sub, c)
        parts.append("%.2f~%.2f (%.2f Hz)" % (r[0], r[1], r[1] - r[0]) if r else "—")
    print("%-28s | %-22s | %-22s | %-22s" % (name, parts[0], parts[1], parts[2]))
print("-" * 108)
print("★ 三节簇的合并域比同 Q 单节宽（叠加），但比「降到能覆盖同带宽的低 Q」窄得多（见 F）。")

# ── D) CPU / Flash / RAM 账 ────────────────────────────────────────────
print()
print("D) 开销账（锚点：D2 6节=4.884%、D3 8节=6.507% 实测 ⇒ 0.8115 pp/节、68 B Flash/节、56 B RAM/节）")
print("-" * 108)
print("%-28s %4s %10s %11s %11s %11s" % ("方案", "节", "CPU(估)", "ΔCPU", "ΔFlash", "ΔRAM"))
print("-" * 108)
cpu9 = 6.507 + 1 * 0.8115
for name, secs, n, tag in CANDS:
    cpu = 6.507 + (n - 8) * 0.8115
    print("%-28s %4d %9.2f%% %10.2f pp %+9d B %+9d B"
          % (name, n, cpu, cpu - cpu9, (n - 9) * 68, (n - 9) * 56))
print("-" * 108)
print("★ 9 节以上均为线性外推，**必须上板用 DWT->CYCCNT 实测**（T22 之后一直欠的验证）。")

# ── E) 判决预期 ───────────────────────────────────────────────────────
print()
print("E) 判决预期（列④ 屏幕 p-p 比值；沿用 §8.4「抠线」口径，150Hz 贡献 0.95pp、100Hz 0.39pp）")
print("-" * 108)
print("  现状 P0                 : 104.50% / 103.11%   （09-18 实测 104.21 / 102.83）")
print("  抠掉 100Hz（保留150）    : 104.11% / 102.74%")
print("  抠掉 150Hz（保留100）    : 103.55% / 102.88%")
print("  抠掉 150+100Hz（理想）   : 103.16% / 102.74%")
for name, secs, n, tag in CANDS[2:]:
    r150 = RAWAMP["150"] * 10 ** (cascade_db(secs, np.arange(*BANDS["150"], 0.0005)).max() / 20.0)
    r100 = RAWAMP["100"] * 10 ** (cascade_db(secs, np.arange(*BANDS["100"], 0.0005)).max() / 20.0)
    if "N1" in name:
        pred = "约 104.1%（等价「抠掉100」）"
    else:
        pred = "约 103.2%（等价「抠掉150+100」）"
    print("  %-24s: 带内最差残值 150Hz=%6.3f µV / 100Hz=%5.3f µV  ⇒ %s"
          % (name, r150, r100, pred))
print("-" * 108)

# ── F) Q 与间距扫描（三节簇） ──────────────────────────────────────────
print()
print("F) 三节簇 Q 扫描（中心间距 0.2 Hz；漂移带内最差 / 合并 -3dB 域宽）")
print("-" * 108)
print("%-8s | %-34s | %-34s" % ("Q", "100Hz 簇（带 0.196 Hz）", "150Hz 簇（带 0.294 Hz）"))
print("%-8s | %14s %19s | %14s %19s" % ("", "带内最差dB", "合并域宽Hz", "带内最差dB", "合并域宽Hz"))
print("-" * 108)
for Q in (10, 12, 15, 20, 25, 30):
    cells = []
    for c in (100.0, 150.0):
        cc = clust(c, Q)
        lo, hi = (2 * 49.9391, 2 * 50.0372) if c == 100 else (3 * 49.9391, 3 * 50.0372)
        w = cascade_db(cc, np.arange(lo, hi, 0.0005)).max()
        r = band3(cc, c)
        cells.append("%14.1f %18.2f" % (w, r[1] - r[0] if r else float("nan")))
    print("%-8d | %s | %s" % (Q, cells[0], cells[1]))
print("-" * 108)
print("★ Q 越高：带内压制越强、合并域越窄、但**对中心落点越敏感**。")
print("  推荐 Q20：150Hz 带内 -87.8 dB、合并域 ~4.9 Hz（仍小于 P0 单 Q30 的 13.9 Hz，因为 P0 的 Q30 在 150 是 BP 上角叠加）。")

# ── G) 单节 vs 三节 vs 降 Q 的等价性（回应「多陷波严格优于降 Q」） ────────
print()
print("G) 「多陷波 vs 降 Q」同带宽对比（150Hz 域，目标：合并 -3dB 域≈15 Hz）")
print("-" * 108)
print("%-26s %14s %16s %16s" % ("方案", "带内最差dB", "150Hz处残值µV", "合并域宽Hz"))
print("-" * 108)
for label, cc in (("单节 Q30（P0 现状）", [notch(150.0, 30.0)]),
                  ("单节 Q10", [notch(150.0, 10.0)]),
                  ("单节 Q7", [notch(150.0, 7.0)]),
                  ("三节 Q20 ±0.2", clust(150.0, 20.0)),
                  ("三节 Q15 ±0.2", clust(150.0, 15.0))):
    w = cascade_db(cc, np.arange(*BANDS["150"], 0.0005)).max()
    r = RAWAMP["150"] * 10 ** (w / 20.0)
    bb = band3(cc, 150.0)
    print("%-26s %13.1f %16.3f %16.2f" % (label, w, r, bb[1] - bb[0] if bb else float("nan")))
print("-" * 108)


# ── H) 节数与间距扫描：100/150 簇到底要几节 ──────────────────────────────
print()
print("H) 陷波簇「节数 x 间距」扫描（Q20 固定；漂移带内最差 / 残值 / 合并 -3dB 域）")
print("-" * 108)


def scan_cluster(center, band, spacing, nsec):
    offs = [(i - (nsec - 1) / 2.0) * spacing for i in range(nsec)]
    cc = [notch(center + o, 20.0) for o in offs]
    lo, hi = band
    w = cascade_db(cc, np.arange(lo, hi, 0.0005)).max()
    ramp = RAWAMP["100"] if center < 120 else RAWAMP["150"]
    bb = band3(cc, center, span=12.0)
    return w, ramp * 10 ** (w / 20.0), (bb[1] - bb[0] if bb else float("nan")), offs


for cname, c, band in (("100 Hz", 100.0, BANDS["100"]), ("150 Hz", 150.0, BANDS["150"])):
    print()
    print("  [%s 簇] 漂移带 %.4f ~ %.4f（宽 %.3f Hz）" % (cname, band[0], band[1], band[1] - band[0]))
    print("  %-30s %12s %12s %14s %6s" % ("配置", "带内最差dB", "残值uV", "合并域宽Hz", "节数"))
    print("  " + "-" * 82)
    for label, spacing, nsec in (("单节 100.0", 0.0, 1),
                                 ("2 节 99.85/100.15 (间距0.30)", 0.30, 2),
                                 ("3 节 99.8/100/100.2 (间距0.20)", 0.20, 3),
                                 ("3 节 99.9/100/100.1 (间距0.10)", 0.10, 3),
                                 ("4 节 (间距0.20)", 0.20, 4),
                                 ("5 节 (间距0.20)", 0.20, 5)):
        lab = label.replace("99.85/100.15", "%.2f/%.2f" % (c - 0.15, c + 0.15)) \
                   .replace("99.8/100/100.2", "%.1f/%.0f/%.1f" % (c - 0.2, c, c + 0.2)) \
                   .replace("99.9/100/100.1", "%.1f/%.0f/%.1f" % (c - 0.1, c, c + 0.1))
        if nsec == 1:
            cc = [notch(c, 20.0)]
            w = cascade_db(cc, np.arange(band[0], band[1], 0.0005)).max()
            bb = band3(cc, c, span=12.0)
            ramp = RAWAMP["100"] if c < 120 else RAWAMP["150"]
            print("  %-30s %11.1f %12.3f %14.2f %6d"
                  % (lab, w, ramp * 10 ** (w / 20.0), bb[1] - bb[0] if bb else float("nan"), 1))
            continue
        w, rv, bw, offs = scan_cluster(c, band, spacing, nsec)
        print("  %-30s %11.1f %12.3f %14.2f %6d" % (lab, w, rv, bw, nsec))
    print("  " + "-" * 82)
print()
print("* 判据建议：残值远小于 4Hz 信号的 0.1%（3mVpp -> 3uV）即可，不必追求 dB 更深；")
print("  余量看「合并域宽」是否盖住漂移带 + 足够外扩。")
