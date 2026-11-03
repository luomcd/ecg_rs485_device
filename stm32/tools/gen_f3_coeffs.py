# -*- coding: utf-8 -*-
"""
gen_f3_coeffs.py —— ADS1293 ECG 滤波链 "IMP620"（输入阻抗测试）档系数生成与校验

背景（2026-09-17 T24/T26/T27/T28）
--------------------------------
猛哥定的新口径 = **屏幕总波形 p-p(620kΩ) / p-p(直连 3mVpp) <= 1.10**，
数据源 = txt 第④列（协议上报 = 上位机屏幕，500Hz, 0.1526µV/LSB）。

列④ 上实测：
  | 工况    | 现状 D2  | D3(已烧录) | IMP620(=F3) |
  | 620k A⑧ | 141.6%   | 127.7%     | 104.6%  达标 |
  | 620k B① | 129.7%   | 128.0%     | 103.4%  达标 |
病因 = 150Hz(工频 3 次谐波，被 620k∥4.7nF 放大 165×) + 50Hz 簇漂移(49.4~50.6)。

本档 = D3 的工频簇(±0.1Hz,Q20) 换成 **±0.2Hz/Q12**（覆盖实测漂移带），
       并补上 **150Hz 陷波 Q30**。其余（100Hz Q15、BP 0.05~150、dc_block 0.05）
       与 D3 逐位相同。

口径（与固件一致，勿改）
----------------------
fs   = 1e6/1172 = 853.2423208191125 Hz
f64 表 C 字面量 = %.17g
f32 表 C 字面量 = %.9g + "f"
sos 行 = (b0,b1,b2,a1,a2)，a0 恒 1
必须先过第 1 步"逐位复现 D3 表"，0 ULP 才允许生成新档。
"""
import io
import numpy as np
from scipy import signal as sps

FS = 1e6 / 1172.0
UV_PER_CODE = 0.1122
ZERO = 6075000.0


# ---------------------------------------------------------------- 构造
def notch(f0, Q, fs=FS):
    b, a = sps.iirnotch(f0, Q, fs=fs)
    row = sps.tf2sos(b, a)[0]
    return (row[0], row[1], row[2], row[4], row[5])


def bp():
    return [(r[0], r[1], r[2], r[4], r[5]) for r in
            sps.butter(4, [0.05, 150.0], btype="bandpass", fs=FS, output="sos")]


D3 = [notch(49.90, 20.0), notch(50.00, 20.0), notch(50.10, 20.0), notch(100.0, 15.0)] + bp()
IMP620 = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
          notch(100.0, 15.0), notch(150.0, 30.0)] + bp()

# --- IMP620_N2（N2a，2026-09-18 猛哥拍板）---
# 50 簇不动（IMP620 原样 ±0.2/Q12），100 与 150 各由"单节"升为"三节 Q20 簇"。
# 依据：T31（漂移带真实宽 0.0981Hz ⇒ 3x 外推 150Hz 带 0.2944Hz）+ T32（节数/Q/间距扫描）。
# 铁证：同为 ~15Hz 合并带宽，单节 Q10 = -32.3dB vs 三节 Q20 = -84.7dB（差 52dB）。
N2A = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
       notch(99.80, 20.0), notch(100.00, 20.0), notch(100.20, 20.0),
       notch(149.80, 20.0), notch(150.00, 20.0), notch(150.20, 20.0)] + bp()

# ---------------------------------------------------------------- 字面量格式
def row_f64(c):
    """对齐固件风格：3 个系数在首行，a1/a2 折到次行"""
    return "{ %s, %s, %s,\n      %s, %s }" % tuple("%.17g" % v for v in c)


def g9(v):
    s = "%.9g" % np.float32(v)
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def row_f32(c):
    return "{ %s, %s, %s, %s, %s }" % tuple(g9(v) for v in c)


def dump(name, secs, comment=None):
    print("/* ---- %s ---- */" % name)
    print("const ecg_biquad_coeff_f32 ECG_FILTER_COEFFS_F32[ECG_NUM_SECTIONS] = {")
    for i, c in enumerate(secs):
        cm = (" /* %s */" % comment[i]) if comment else ""
        print("    %s%s%s" % (row_f32(c), "," if i < len(secs) - 1 else "", cm))
    print("};")
    print()
    print("const ecg_biquad_coeff_f64 ECG_FILTER_COEFFS_F64[ECG_NUM_SECTIONS] = {")
    for i, c in enumerate(secs):
        cm = ("  /* %s */" % comment[i]) if comment else ""
        print("    %s%s%s" % (row_f64(c), "," if i < len(secs) - 1 else "", cm))
    print("};")


# ================================================================
# 第 1 步：生成器自证 —— 用 tf2sos 逐位复现固件里的 D3 表
# ================================================================
FW_D3_F64 = [
    "{ 0.99089691230496768, -1.8494961296987589, 0.99089691230496812,\n"
    "      -1.8494961296987587, 0.98179382460993547 }",
    "{ 0.99087883501849228, -1.8489376254839556, 0.99087883501849217,\n"
    "      -1.848937625483956, 0.98175767003698489 }",
    "{ 0.99086075838545884, -1.8483781390316931, 0.99086075838545884,\n"
    "      -1.8483781390316931, 0.98172151677091735 }",
    "{ 0.97603707781331417, -1.4462885774993446, 0.97603707781331439,\n"
    "      -1.4462885774993446, 0.95207415562662823 }",
    "{ 0.030899520236696225, 0.06179904047339245, 0.030899520236696225,\n"
    "      -0.49305028725906408, 0.095879018845577341 }",
    "{ 1, 2, 1, -0.67018117413271905, 0.49069657728421018 }",
    "{ 1, -2, 1, -1.9993194733745707, 0.99931960903409622 }",
    "{ 1, -2, 1, -1.9997182191996452, 0.99971835477155957 }",
]
FW_D3_F32 = [
    "{ 0.99089694f, -1.84949613f, 0.99089694f, -1.84949613f, 0.981793821f }",
    "{ 0.99087882f, -1.84893763f, 0.99087882f, -1.84893763f, 0.981757641f }",
    "{ 0.99086076f, -1.84837818f, 0.99086076f, -1.84837818f, 0.98172152f }",
    "{ 0.976037085f, -1.44628859f, 0.976037085f, -1.44628859f, 0.95207417f }",
    "{ 0.030899521f,  0.0617990419f, 0.030899521f, -0.493050277f, 0.0958790183f }",
    "{ 1.0f,          2.0f,          1.0f,         -0.670181155f, 0.490696579f  }",
    "{ 1.0f,         -2.0f,          1.0f,         -1.99931943f,  0.999319613f  }",
    "{ 1.0f,         -2.0f,          1.0f,         -1.99971819f,  0.999718368f  }",
]


def toks(s):
    """把一行 C 字面量拆成数值 token（容忍 1f / 1.0f / 1 三种写法）"""
    out = []
    for t in s.replace("{", " ").replace("}", " ").replace(",", " ").split():
        t = t.rstrip("fF")
        try:
            out.append(float(t))
        except ValueError:
            out.append(t)
    return out


def selfcheck():
    ok = True
    print("=" * 96)
    print("第 1 步：生成器自证 —— 逐位复现固件 D3 表（8 节）")
    print("=" * 96)
    for nm, gen, fw in (("f64", [row_f64(c) for c in D3], FW_D3_F64),
                        ("f32", [row_f32(c) for c in D3], FW_D3_F32)):
        nbad = 0
        for i in range(len(gen)):
            ta, tb = toks(gen[i]), toks(fw[i])
            bad = [(x, y) for x, y in zip(ta, tb) if x != y]
            if bad or len(ta) != len(tb):
                nbad += 1
                ok = False
                print("  [%s] 节 %d 不一致：" % (nm, i))
                print("      固件: %s" % " ".join(str(v) for v in tb))
                print("      生成: %s" % " ".join(str(v) for v in ta))
        print("  %s：%d/%d 节逐位一致%s" % (nm, len(gen) - nbad, len(gen),
                                          "" if nbad == 0 else "  ← 有差异"))
    print("  自证结果：%s" % ("全部 0 ULP —— 通过 ✓" if ok else "★不一致，禁止生成新档★"))
    return ok


if __name__ == "__main__":
    if not selfcheck():
        raise SystemExit(1)

    print()
    print("=" * 96)
    print("第 2 步：IMP620 档（9 节）系数 —— 可直接粘贴进 ecg_filter.c")
    print("=" * 96)
    IMP620_CM = ["49.80 Hz notch Q12", "50.00 Hz notch Q12", "50.20 Hz notch Q12",
                 "100 Hz notch Q15", "150 Hz notch Q30",
                 "BP 0.05-150 sec0", "BP sec1", "BP sec2", "BP sec3"]
    dump("IMP620/F3: 0.05~150Hz + 宽50簇(±0.2,Q12) + 100(Q15) + 150(Q30)", IMP620, IMP620_CM)

    print()
    print("=" * 96)
    print("第 3 步：单节频响核验")
    print("=" * 96)
    print("%-22s %12s %10s %10s" % ("节", "notch中心", "设计Q", "中心衰减dB"))
    for i, c in enumerate(IMP620[:5]):
        b0, b1, b2, a1, a2 = c
        f0 = [49.80, 50.00, 50.20, 100.0, 150.0][i]
        f = np.linspace(f0 - 8, f0 + 8, 200001)
        z = np.exp(-1j * 2 * np.pi * f / FS)
        H = (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
        A = np.abs(H); k = int(np.argmin(A))
        print("%-22s %12.3f %10.2f %10.1f" % (
            IMP620_CM[i], f[k], [12, 12, 12, 15, 30][i], 20 * np.log10(A[k])))

    print()
    print("级联在 620k 关心的落点上的总衰减（含 BP）：")
    fchk = [49.4, 49.6, 49.8, 50.0, 50.2, 50.4, 50.6, 100.0, 150.0, 203.2]
    def casc(secs, f):
        z = np.exp(-1j * 2 * np.pi * np.array(f, dtype=float) / FS)
        H = np.ones(len(f), dtype=complex)
        for (b0, b1, b2, a1, a2) in secs:
            H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
        return 20 * np.log10(np.abs(H))
    hd3 = casc(D3, fchk); himp = casc(IMP620, fchk)
    print("%-10s %12s %12s %10s" % ("f(Hz)", "D3 dB", "IMP620 dB", "增益"))
    for i, fv in enumerate(fchk):
        print("%-10.1f %12.1f %12.1f %9.1f" % (fv, hd3[i], himp[i], himp[i] - hd3[i]))

    print()
    print("=" * 96)
    print("第 4 步：IMP620_N2（N2a，13 节）系数 —— 2026-09-18 猛哥拍板 Q20 / 3 节 / 50簇不动")
    print("=" * 96)
    N2A_CM = ["49.80 Hz notch Q12", "50.00 Hz notch Q12", "50.20 Hz notch Q12",
              "99.80 Hz notch Q20", "100.00 Hz notch Q20", "100.20 Hz notch Q20",
              "149.80 Hz notch Q20", "150.00 Hz notch Q20", "150.20 Hz notch Q20",
              "BP 0.05-150 sec0", "BP sec1", "BP sec2", "BP sec3"]
    assert len(N2A) == 13
    dump("IMP620_N2: 0.05~150Hz + 50簇(±0.2,Q12) + 100簇(±0.2,Q20) + 150簇(±0.2,Q20)", N2A, N2A_CM)

    print()
    print("=" * 96)
    print("第 5 步：IMP620(P0) vs IMP620_N2(N2a) 级联频响对比")
    print("=" * 96)
    fchk2 = [4, 40, 44, 46, 50, 60, 90, 95, 98, 100, 102, 120, 140, 145, 147, 148, 149, 150, 203]
    hp = casc(IMP620, fchk2); hn = casc(N2A, fchk2)
    print("%-8s %14s %14s %12s" % ("f(Hz)", "P0 IMP620 dB", "N2a dB", "N2a-P0 dB"))
    for i, fv in enumerate(fchk2):
        print("%-8d %14.2f %14.2f %12.2f" % (fv, hp[i], hn[i], hn[i] - hp[i]))
    print()
    print("开销账（锚点：D2 6节=4.884%、D3 8节=6.507% 实测 ⇒ 0.8115 pp/节、68 B Flash/节、56 B RAM/节）：")
    print("  口径一（从实测锚点 D3 8 节外推 5 节）：6.507% → %.2f%%（+%.2f pp）"
          % (6.507 + 5 * 0.8115, 5 * 0.8115))
    print("  口径二（从 IMP620 9 节外推 4 节）    ：7.32% → %.2f%%（+%.2f pp）"
          % (7.32 + 4 * 0.8115, 4 * 0.8115))
    print("  Flash 常量区 9→13 节（+4 节）：f32 表 +4×20=80 B，f64 表 +4×40=160 B ⇒ **+240 B**")
    print("  RAM 运行态（biquad_df2t_f32=28 B/节、f64=56 B/节）：f32 链 +112 B；f64 链 +224 B")
    print("  ⚠ 9 节以上为线性外推，必须上板用 DWT->CYCCNT 实测。")
