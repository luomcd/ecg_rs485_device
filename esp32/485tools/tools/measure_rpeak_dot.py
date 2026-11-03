"""量测 R 峰红点与波形顶点的位置偏差（猛哥 2026-10-26 实机照片，v2）。

v1 的红点阈值太严（JPEG 把红色压成 (255,109,145)），改成 R-(G+B)/2 > 40。
比例用**心搏间距**标定：60 bpm ⇒ 1 拍 = 1.000 s = 25 mm = 150.4 屏幕px。
"""
import sys
import numpy as np
from PIL import Image

path = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Users\luom\.workbuddy\clipboard-images\clipboard-2026-10-26T01-48-30-170Z-d940286a.jpg"

a = np.asarray(Image.open(path).convert("RGB")).astype(int)
H, W, _ = a.shape
R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
print(f"image {W}x{H}")

# ---------- 1. 红点 ----------
score = R - (G + B) / 2.0
mask = (score > 40) & (R > 180)
ys, xs = np.nonzero(mask)
print(f"red-ish pixels: {len(xs)}")

blobs = []
if len(xs):
    pts = sorted(zip(xs, ys))
    cur = [pts[0]]
    for x, y in pts[1:]:
        # 与当前团任一像素距离 < 40 就归并
        if any(abs(x - cx) < 40 and abs(y - cy) < 40 for cx, cy in cur):
            cur.append((x, y))
        else:
            blobs.append(cur)
            cur = [(x, y)]
    blobs.append(cur)

dots = []
print("\n=== 红点团（≥30 像素才算）===")
for bl in blobs:
    if len(bl) < 30:
        continue
    bx = np.array([p[0] for p in bl])
    by = np.array([p[1] for p in bl])
    dots.append((bx.mean(), by.mean(), len(bl),
                 bx.min(), bx.max(), by.min(), by.max()))
    print(f"  cx={bx.mean():7.1f} cy={by.mean():7.1f} n={len(bl):4d} "
          f"x[{bx.min()}-{bx.max()}] y[{by.min()}-{by.max()}] "
          f"=> 直径约 {max(bx.max()-bx.min(), by.max()-by.min())+1} px")

# ---------- 2. 白色波形顶边 ----------
bright = (R > 165) & (G > 165) & (B > 165)
top = np.full(W, np.nan)
for x in range(W):
    col = np.nonzero(bright[:, x])[0]
    if len(col) > 2:
        top[x] = col.min()

# 平滑后找 R 顶点（top 的局部极小）
sm = top.copy()
k = 7
for x in range(k, W - k):
    seg = top[x - k:x + k + 1]
    if not np.all(np.isnan(seg)):
        sm[x] = np.nanmin(seg)

apexes = []
x = k
while x < W - k:
    v = sm[x]
    if not np.isnan(v) and abs(v - np.nanmax(top)) > 1:
        lo, hi = max(0, x - 25), min(W, x + 26)
        win = sm[lo:hi]
        if not np.all(np.isnan(win)) and v <= np.nanmin(win) + 0.5:
            apexes.append((x, v))
            x += 30
            continue
    x += 1

# 只保留明显的（比中位数高 20 px 以上）
if apexes:
    med = np.nanmedian(top)
    apexes = [p for p in apexes if med - p[1] > 20]

print(f"\n=== 波形 R 顶点（高于中位 {np.nanmedian(top):.0f} 至少 20 px）===")
for x, y in apexes:
    print(f"  x={x:4d}  y={y:6.1f}")

# ---------- 3. 比例标定 = 心搏间距 ----------
scale = None
if len(apexes) >= 2:
    d = apexes[-1][0] - apexes[0][0]
    nbeat = len(apexes) - 1
    beat_px = d / nbeat
    # 1 拍 = 1.000 s = 25.0 mm = 25 * 6.016 = 150.4 屏幕px
    scale = beat_px / 150.4
    print(f"\n心搏间距 = {beat_px:.1f} 照片px / 拍（{nbeat} 拍共 {d} px）")
    print(f"1 拍 = 25 mm = {25*6.016:.1f} 屏幕px  ⇒  比例 {scale:.3f} 照片px/屏幕px")

# ---------- 4. 逐个红点找最近的顶点 ----------
PX_PER_SAMPLE = 6.016 * 25.0 / 500.0   # 0.3008 屏幕px/样本 @25mm/s
print("\n=== 红点 vs 最近的 R 顶点 ===")
if scale:
    print("  点cx   点cy   顶点x  顶点y   Δx(照片) Δy(照片) | Δx(屏幕) Δy(屏幕) | Δ样本")
    for cx, cy, *_ in dots:
        if not apexes:
            break
        ax, ay = min(apexes, key=lambda p: abs(p[0] - cx))
        dxp, dyp = cx - ax, cy - ay
        dxs, dys = dxp / scale, dyp / scale
        print(f"{cx:7.1f} {cy:6.1f} {ax:6d} {ay:6.1f} {dxp:8.1f} {dyp:8.1f} | "
              f"{dxs:8.1f} {dys:8.1f} | {dxs / PX_PER_SAMPLE:7.1f}")

print(f"\n参考：R 峰标记应画在顶点**上方 10 屏幕px**、x 与顶点重合")
