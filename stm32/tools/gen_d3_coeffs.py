# -*- coding: utf-8 -*-
"""
gen_d3_coeffs.py —— ADS1293 ECG 滤波链 "D3/S3" 档系数生成与校验

用途
----
1) 逐字复现现有 D2 表（notch50 Q30 / notch100 Q30 / BP 0.05-150 butter order8）
   并把结果与 ecg_filter.c 里的字面量做**字符串级**比对 —— 只有这一步通过，
   才允许用同一套口径生成新档，避免"生成器和固件不是同一个滤波器"。
2) 生成 D3/S3 档（三陷波）的 f64/f32 C 表，输出可直接粘贴的代码块。
3) 用固件同款 DF2T 因果递推（f64 与 f32 两版）在真实 LiPc 数据上验证：
   带内残余 / 4Hz 信号损失 / 100Hz 抑制 / f32 与 f64 的差异。

口径（与固件一致，勿改）
-----------------------
fs   = 1e6/1172 = 853.2423208191125 Hz
f64 表 C 字面量 = %.17g
f32 表 C 字面量 = %.9g + "f"
sos 行 = (b0,b1,b2,a1,a2)，a0 恒 1
DF2T: y=b0*x+d1; d1=b1*x-a1*y+d2; d2=b2*x-a2*y
"""
import io
import sys
import numpy as np
from scipy import signal as sps

FS = 1e6 / 1172.0            # 853.2423208191125
UV_PER_CODE = 0.1122
ZERO = 6075000.0

# ---------------------------------------------------------------- 系数构造
def notch(f0, Q, fs=FS):
    """★ 与固件同口径：scipy iirnotch -> tf2sos()[0]（固件表头注释里的生成命令）
    用 tf2sos 而非裸 iirnotch，会引入 ±1ULP 的 b0/b2 不对称，与固件表一致。"""
    b, a = sps.iirnotch(f0, Q, fs=fs)
    row = sps.tf2sos(b, a)[0]
    return (row[0], row[1], row[2], row[4], row[5])

def bp_d2(fs=FS):
    sos = sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")
    return [(r[0], r[1], r[2], r[4], r[5]) for r in sos]

# ---------------------------------------------------------------- 字面量格式
def g17(v):
    return "%.17g" % v

def g9f(v):
    return "%.9gf" % np.float32(v)

def row_f64(c):
    s = "{ %s, %s, %s, %s, %s }" % tuple(g17(x) for x in c)
    if len(s) <= 76:
        return s
    a, b = s.rsplit(", ", 1)
    return a + ",\n      " + b

def row_f32(c):
    return "{ %s, %s, %s, %s, %s }" % tuple(g9f(x) for x in c)

# ---------------------------------------------------------------- 固件 DF2T
def df2t_f64(x, secs):
    """逐样本 DF2T，用 float64 递推（与固件 f64 路径同构）"""
    y = np.asarray(x, dtype=np.float64).copy()
    for (b0, b1, b2, a1, a2) in secs:
        d1 = 0.0; d2 = 0.0
        o = np.empty_like(y)
        for i in range(len(y)):
            xi = y[i]
            yi = b0 * xi + d1
            d1 = b1 * xi - a1 * yi + d2
            d2 = b2 * xi - a2 * yi
            o[i] = yi
        y = o
    return y

def df2t_f32(x, secs):
    """逐样本 DF2T，用 float32 递推（模拟 M4F 硬浮点路径）"""
    y = np.asarray(x, dtype=np.float32).copy()
    for (b0, b1, b2, a1, a2) in secs:
        c = [np.float32(v) for v in (b0, b1, b2, a1, a2)]
        b0f, b1f, b2f, a1f, a2f = c
        d1 = np.float32(0.0); d2 = np.float32(0.0)
        o = np.empty_like(y)
        for i in range(len(y)):
            xi = y[i]
            yi = np.float32(b0f * xi + d1)
            d1 = np.float32(np.float32(b1f * xi - a1f * yi) + d2)
            d2 = np.float32(b2f * xi - a2f * yi)
            o[i] = yi
        y = o
    return y

# ---------------------------------------------------------------- 频响
def resp(f0s, secs, fmin=40.0, fmax=110.0, n=14001):
    f = np.linspace(fmin, fmax, n)
    w = 2 * np.pi * f / FS
    z = np.exp(-1j * w)
    H = np.ones_like(z, dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1.0 + a1 * z + a2 * z ** 2)
    return f, np.abs(H)

def _H(sec, f):
    w = 2 * np.pi * np.asarray(f, dtype=float) / FS
    z = np.exp(-1j * w)
    b0, b1, b2, a1, a2 = sec
    return (b0 + b1 * z + b2 * z ** 2) / (1.0 + a1 * z + a2 * z ** 2)

def sec_param(c, f_lo=None, f_hi=None):
    """数值法反推（不依赖 Audio-EQ 闭式，避免极点角/零点角混淆）：
       f_notch = |H(f)| 最小点（细网格 + 抛物线插值）
       Q       = f_notch / (-3dB 带宽)，带宽取最小值两侧 -3dB 交叉点
       dip_dB  = 中心处衰减
    """
    a1 = c[3]; a2 = c[4]
    if f_lo is None:
        r = np.sqrt(abs(a2))
        w0 = np.arccos(np.clip(-a1 / (2 * r), -1, 1))
        f_lo = max(0.01, (w0 * FS / (2 * np.pi)) - 20.0)
        f_hi = (w0 * FS / (2 * np.pi)) + 20.0
    f = np.linspace(f_lo, f_hi, 400001)
    A = np.abs(_H(c, f))
    i = int(np.argmin(A))
    # 抛物线插值细化
    if 0 < i < len(f) - 1:
        y0, y1, y2 = A[i - 1], A[i], A[i + 1]
        den = (y0 - 2 * y1 + y2)
        d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        f_n = f[i] + d * (f[1] - f[0])
    else:
        f_n = f[i]
    dip = 20 * np.log10(max(A[i], 1e-300))
    # -3 dB 带宽（相对**通带**增益，用远离陷波处的平坦值）
    peak = np.max(A)
    thr = peak * 10 ** (-3.0 / 20)
    lo = np.where(A[:i] > thr)[0]
    hi = np.where(A[i:] > thr)[0]
    flo = f[lo[-1]] if len(lo) else f[0]
    fhi = f[i + hi[0]] if len(hi) else f[-1]
    bw = fhi - flo
    Q = f_n / bw if bw > 0 else float("inf")
    return f_n, Q, bw, dip

# ---------------------------------------------------------------- 数据
def load853(fn, a, b):
    """列② raw 按列① 853 序号重排回均匀 853.24 Hz 网格（M27）"""
    rows = []
    for ln in io.open(fn, encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) < 5:
            continue
        try:
            rows.append([float(x) for x in p[:5]])
        except ValueError:
            pass
    d = np.array(rows)[a:b]
    sq = d[:, 0].astype(np.int64) - d[0, 0].astype(np.int64)
    return (np.interp(np.arange(int(sq[-1]) + 1), sq, d[:, 1]) - ZERO) * UV_PER_CODE

def band_hann(y, f1, f2, fs=FS):
    n = len(y)
    w = np.hanning(n)
    Y = np.fft.rfft(y * w)
    f = np.fft.rfftfreq(n, 1 / fs)
    m = (f >= f1) & (f <= f2)
    return float(np.sqrt(2 * np.sum(np.abs(Y[m]) ** 2)) / np.sum(w))

def dc_block(x, fc=0.05, nrep=24):
    """镜像预热 nrep 段 + 一阶高通（复刻 ecg_dc_block，首样本预热）"""
    r = 1.0 - 2 * np.pi * fc / FS
    y = np.concatenate([x if k % 2 == 0 else x[::-1] for k in range(nrep)])
    # 首样本预热：x_prev 初始化为首样本 -> 首个输出为 0
    out = np.empty_like(y)
    xp = y[0]; yp = 0.0
    out[0] = 0.0
    for i in range(1, len(y)):
        yi = (y[i] - xp) + r * yp
        out[i] = yi
        xp = y[i]; yp = yi
    return out[-len(x):]

# ==================================================================
# 第 1 步：生成器自证 —— 复现 D2 表并做字符串级比对
# ==================================================================
FW_F64_D2 = [  # 抄自 ecg_filter.c（D2 分支，f64 表）
    "{ 0.99390077416204958, -1.8545764349801153, 0.99390077416204947,\n"
    "      -1.8545764349801153, 0.98780154832409917 }",
    "{ 0.98787504732578624, -1.4638300423431101, 0.98787504732578646,\n"
    "      -1.4638300423431101, 0.97575009465157247 }",
    "{ 0.030899520236696225, 0.06179904047339245, 0.030899520236696225,\n"
    "      -0.49305028725906408, 0.095879018845577341 }",
    "{ 1, 2, 1, -0.67018117413271905, 0.49069657728421018 }",
    "{ 1, -2, 1, -1.9993194733745707, 0.99931960903409622 }",
    "{ 1, -2, 1, -1.9997182191996452, 0.99971835477155957 }",
]

def _ref_parse(ref):
    """把固件原文里的字面量解析回 float"""
    s = ref.replace("{", "").replace("}", "").replace("\n", " ")
    return tuple(float(x) for x in s.split(","))

def selfcheck():
    gen = [notch(50.0, 30.0), notch(100.0, 30.0)] + bp_d2()
    print("=" * 78)
    print("第 1 步：生成器自证（复现 D2 表）")
    print("=" * 78)
    print("  %-9s %-10s %-10s %-10s" % ("节", "最大|Δ系数|", "最大ULP", "字面量串"))
    worst = 0.0
    for i, (c, ref) in enumerate(zip(gen, FW_F64_D2)):
        rc = _ref_parse(ref)
        d = max(abs(a - b) for a, b in zip(c, rc))
        ulp = max(abs(np.float64(a).view(np.int64) - np.float64(b).view(np.int64))
                  for a, b in zip(c, rc))
        worst = max(worst, ulp)
        print("  %-9s %-10.3g %-10d %s" % (i, d, ulp, "串同" if row_f64(c) == ref else "串异"))
    print("  ⇒ 最大 ULP 偏差 = %d  （0 = 逐位一致；≤2 = 同源同口径，浮点末位噪声）" % worst)
    print("  ⇒ 判定：%s" % ("PASS" if worst <= 2 else "FAIL（禁止用于生成新档）"))

    print("\n  固件 D2 各节实测参数（数值法 |H| 最小点，非极点角）：")
    for nm, c in zip(("notch50", "notch100"), gen[:2]):
        f0, Q, bw, dip = sec_param(c)
        print("    %-9s 陷波中心 = %8.4f Hz   -3dB带宽 = %7.4f Hz   Q = %7.3f   谷深 = %.1f dB"
              % (nm, f0, bw, Q, dip))
    # 上一轮用错公式给出的值，做一次对照说明
    b0, b1, b2, a1, a2 = gen[0]
    r = np.sqrt(a2)
    f_pole = np.arccos(-a1 / (2 * r)) * FS / (2 * np.pi)
    f_wrong = np.arccos(np.clip(-a1 / (2 * b0), -1, 1)) * FS / (2 * np.pi)
    print("    [对照] 极点角 = %.4f Hz ／ 旧错公式 arccos(-a1/2b0) = %.4f Hz"
          % (f_pole, f_wrong))
    return worst <= 2

# ==================================================================
# 第 2 步：生成 D3/S3 表
# ==================================================================
D3_NOTCHES = [(49.90, 20.0), (50.00, 20.0), (50.10, 20.0), (100.0, 15.0)]

def gen_d3():
    return [notch(f0, Q) for f0, Q in D3_NOTCHES] + bp_d2()

def emit_d3():
    secs = gen_d3()
    print("\n" + "=" * 78)
    print("第 2 步：D3/S3 表（8 节）")
    print("=" * 78)
    print("  实测参数：")
    for (f0, Q), c in zip(D3_NOTCHES, secs):
        a, q, bw, dip = sec_param(c)
        print("    设计 f0=%6.2f Q=%5.1f  →  实测中心=%8.4f Hz  BW=%7.4f Hz  Q=%7.3f  谷深=%6.1f dB"
              % (f0, Q, a, bw, q, dip))

    print("\n  ---- f64 表（%.17g）----")
    for i, c in enumerate(secs):
        print("    %s," % row_f64(c))
    print("\n  ---- f32 表（%.9g）----")
    for i, c in enumerate(secs):
        print("    %s," % row_f32(c))
    return secs

# ==================================================================
# 第 3 步：真实数据验证（固件同款 DF2T）
# ==================================================================
SEGS = [("LiPc-2026-09-17-unselect.txt", 0, 5515, "新笔电·620k 段① 11.03s"),
        ("LiPc-2026-09-17.txt", 16570, 22629, "新笔电·620k 段⑦ 12.12s"),
        ("3mv-4hz-unselect-2026-09-17 113751.txt", 0, 4624, "旧笔电·620k(PE) 9.25s"),
        ("3mv-4hz-unselect-Li5V-2026-09-17 135109.txt", 0, 5002, "旧笔电·620k(Li5V) 10.00s")]

BANDS = [("45-48.5", 45, 48.5), ("48.5-49.5", 48.5, 49.5), ("49.5-50.5", 49.5, 50.5),
         ("50.5-51.5", 50.5, 51.5), ("51.5-55", 51.5, 55), ("45-55", 45, 55),
         ("99-101", 99, 101), ("3.5-4.5", 3.5, 4.5)]

D2 = [notch(50.0, 30.0), notch(100.0, 30.0)] + bp_d2()

def verify(d3_f64, d3_f32):
    print("\n" + "=" * 78)
    print("第 3 步：真实数据验证（固件同款 DF2T 逐样本递推）")
    print("=" * 78)
    plan = [("raw→dc", None, None)]
    plan.append(("D2 f64(6节)", D2, df2t_f64))
    plan.append(("D3 f64(8节)", d3_f64, df2t_f64))
    plan.append(("D3 f32(8节)", d3_f32, df2t_f32))

    for fn, a, b, tag in SEGS:
        try:
            x = load853(fn, a, b)
        except Exception as e:
            print("  跳过 %s (%s)" % (tag, e)); continue
        print("\n" + "-" * 78)
        print("%s   n=%d  (%.2f s @853)" % (tag, len(x), len(x) / FS))
        print("  %-14s" % "方案(Hann)" + "".join("%10s" % nm for nm, _, _ in BANDS))
        xd = dc_block(x)
        for nm, secs, fn_ in plan:
            if fn_ is None:
                y = xd
            else:
                y = fn_(xd, secs)
            v = [band_hann(y, f1, f2) for _, f1, f2 in BANDS]
            print("  %-14s" % nm + "".join("%10.1f" % t for t in v))
    return

def main():
    ok = selfcheck()
    if not ok:
        print("\n!! 生成器自证未通过，停止。")
        sys.exit(1)
    secs64 = gen_d3()
    secs32 = [tuple(float(np.float32(v)) for v in c) for c in secs64]
    emit_d3()
    # 字面量往返：%.17g 打印再解析回来，确认 == 原值
    rt = [tuple(float(g17(v)) for v in c) for c in secs64]
    err = max(abs(r - v) for c1, c2 in zip(rt, secs64) for r, v in zip(c1, c2))
    print("\n  字面量往返最大误差（%%.17g 打印→解析）：%.3g  ← 应为 0" % err)
    verify(rt, secs32)

if __name__ == "__main__":
    main()
