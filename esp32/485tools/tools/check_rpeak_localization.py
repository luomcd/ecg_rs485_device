"""R 峰识别准确率核查（离线，PC 侧）。

核心问题：`ecg_rpeak_process()` 输出的峰索引，落在**原始 R 波顶点**上吗？

为什么必须查：BPM 用的是「最近 5 个 RR 的中位数」。若所有峰索引都**一致地滞后**
N 个样本，RR 间隔不变 ⇒ BPM 仍然精确等于 60。所以 "BPM = 60 正确" **不能**证明
峰位置正确。这两件事必须分开验。

判据：
  ① 峰数（检出多少个）
  ② RR 间隔（节律是否准确）
  ③ **定位偏差**：每个峰索引 vs 原始波形局部 |max| 的位置差
"""
import sys
import numpy as np

BIN = r"C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\bin\Debug\net10.0-windows\recordings\ecg_20260929_093408.bin"
PEAKS = r"C:\work2026\CardioLite\tools\rpeak_ref\ref_full.txt"

# ---------- 解帧取 ch1 ----------
data = open(BIN, "rb").read()
samples, i, frames = [], 0, 0
while i + 6 <= len(data):
    if data[i] != 0xAA or data[i + 1] != 0x55:
        break
    ln = data[i + 3] | (data[i + 4] << 8)
    total = 6 + ln
    if i + total > len(data):
        break
    off = 3 if ln % 2 == 1 else 2
    n = (ln - off) // 2 if ln % 2 == 1 else (ln - 2) // 2
    for k in range(n):
        p = i + 5 + off + 2 * k
        v = data[p] | (data[p + 1] << 8)
        if v >= 32768:
            v -= 65536
        samples.append(v)
    frames += 1
    i += total

x = np.array(samples, dtype=np.float64)
fs = 500.0
print(f"frames={frames}  samples={len(x)}  ({len(x)/fs:.2f} s)")
print(f"幅值范围 {x.min():.0f} .. {x.max():.0f} (int16 LSB)")

# ---------- 读参考峰 ----------
peaks = []
for line in open(PEAKS, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("index"):
        continue
    peaks.append(int(line.split(",")[0]))
peaks = np.array(peaks)
print(f"\n参考峰数 = {len(peaks)}   范围 {peaks.min()} .. {peaks.max()}")

# ---------- ② RR ----------
rr = np.diff(peaks)
print(f"\n=== RR 间隔（样本，fs=500 ⇒ 1 样本 = 2 ms）===")
print(f"  n={len(rr)}  min={rr.min()}  max={rr.max()}  mean={rr.mean():.1f}  "
      f"median={np.median(rr):.0f}")
print(f"  对应心率 = {60*fs/np.median(rr):.2f} bpm（中位数口径）")
uniq, cnt = np.unique(rr, return_counts=True)
print("  分布:", ", ".join(f"{u}×{c}" for u, c in zip(uniq, cnt)))

# ---------- ③ 定位偏差：峰索引 vs 原始波形局部最大 ----------
print("\n=== 定位偏差（峰索引 - 原始波形局部最大值的索引）===")
print("  ±40 样本窗内找 max；正数 = 峰索引滞后于真实顶点")
offs, apex_rel, info = [], [], []
for p in peaks:
    lo, hi = max(0, p - 40), min(len(x), p + 41)
    seg = x[lo:hi]
    q = lo + int(np.argmax(seg))
    offs.append(p - q)
    apex_rel.append((q, x[q], x[p]))
    info.append((p, q, p - q, x[p], x[q]))

offs = np.array(offs)
print(f"  n={len(offs)}  min={offs.min()}  max={offs.max()}  "
      f"mean={offs.mean():+.1f}  median={np.median(offs):+.0f}")
uniq, cnt = np.unique(offs, return_counts=True)
print("  分布:", ", ".join(f"{u:+d}×{c}" for u, c in zip(uniq, cnt)))

print("\n  前 10 个明细：")
print("    峰索引   真顶点   偏差   峰处幅值   顶点幅值   差")
for p, q, d, vp, vq in info[:10]:
    print(f"  {p:7d} {q:7d} {d:+6d} {vp:9.0f} {vq:9.0f} {vp-vq:6.0f}")

# 峰索引处的幅值 vs 顶点幅值（看是否落在下降支）
vp = np.array([t[3] for t in info]); vq = np.array([t[4] for t in info])
print(f"\n  峰索引处幅值/顶点幅值 = {np.median(vp/vq)*100:.1f}% "
      f"(100% = 正好在顶点，<80% = 已落在下降支)")

# ---------- 换算成屏幕像素 ----------
PX_PER_SAMPLE = 6.016 * 25.0 / 500.0
print(f"\n=== 换算（25 mm/s：{PX_PER_SAMPLE:.4f} 屏幕px/样本；1 mV = 20 px @gain5）===")
print(f"  中位偏差 {np.median(offs):+.0f} 样本 = {np.median(offs)*PX_PER_SAMPLE:+.1f} 屏幕px")
dv = np.median(vq - vp) * 0.1526 / 1000 * 20
print(f"  峰索引处比顶点低 {np.median(vq-vp):.0f} LSB = {dv:.1f} 屏幕px（1 mV=20px）")
