# -*- coding: utf-8 -*-
"""_t22_rodata_check.py —— 从 ARM .o 的 .rodata 里把系数表解码回来，逐位比对

这是"烧进 flash 的究竟是什么"的最终检查：
  源码字面量 -> 编译器 -> .rodata 二进制 -> 解码 -> 与设计值比 bit pattern
任何一环出错（字面量截断、类型错、表错位）都会在这里暴露。
"""
import io
import os
import struct
import subprocess
import sys

TOOLBIN = r"C:\Nordic\Toolchian\gcc-arm-none-eabi-10-2020-q4-major\bin"
OBJDUMP = os.path.join(TOOLBIN, "arm-none-eabi-objdump.exe")
NM = os.path.join(TOOLBIN, "arm-none-eabi-nm.exe")
OBJCOPY = os.path.join(TOOLBIN, "arm-none-eabi-objcopy.exe")

OUT = r"C:\Users\luom\AppData\Local\Temp\d3chk"
sys.path.insert(0, r"C:\work2026\VsPro\ads1293_demo\tools")

FS = 1e6 / 1172.0


def sym_size_addr(obj, name):
    out = subprocess.run([NM, "-S", obj], capture_output=True, text=True,
                         encoding="utf-8", errors="ignore").stdout
    for ln in out.splitlines():
        p = ln.split()
        if len(p) >= 4 and p[3] == name:
            return int(p[0], 16), int(p[1], 16)
    return None, None


def rodata(obj):
    tmp = os.path.join(OUT, "_rodata.bin")
    subprocess.run([OBJCOPY, "-O", "binary", "--only-section=.rodata",
                    obj, tmp], check=True, capture_output=True)
    with open(tmp, "rb") as f:
        return f.read()


def decode(obj, name, ctype):
    addr, size = sym_size_addr(obj, name)
    if addr is None:
        return None, None, None
    raw = rodata(obj)[addr:addr + size]
    n = 5
    sz = 8 if ctype == "double" else 4
    fmt = "<" + ("d" if ctype == "double" else "f")
    vals = [struct.unpack_from(fmt, raw, i * sz)[0] for i in range(size // sz)]
    return [tuple(vals[i * n:(i + 1) * n]) for i in range(size // sz // n)], addr, size


def main():
    # 期望值（与 gen_d3_coeffs.py 的 gen_d3() 同口径重算）
    from scipy import signal as sps
    import numpy as np

    def notch(f0, Q):
        b, a = sps.iirnotch(f0, Q, fs=FS)
        r = sps.tf2sos(b, a)[0]
        return (r[0], r[1], r[2], r[4], r[5])

    bp = [(r[0], r[1], r[2], r[4], r[5])
          for r in sps.butter(4, [0.05, 150.0], btype="bandpass", fs=FS, output="sos")]
    exp64 = [notch(49.90, 20.0), notch(50.00, 20.0), notch(50.10, 20.0),
             notch(100.0, 15.0)] + bp
    # f32 期望 = 把 f64 值折成 float32
    exp32 = [tuple(float(np.float32(v)) for v in c) for c in exp64]

    obj = os.path.join(OUT, "ef_p3.o")
    print("=" * 80)
    print("从 .rodata 解码 D3(profile=3) 系数表并逐位比对")
    print("=" * 80)
    allok = True
    for name, ctype, exp in (("ECG_FILTER_COEFFS_F64", "double", exp64),
                             ("ECG_FILTER_COEFFS_F32", "float", exp32)):
        got, addr, size = decode(obj, name, ctype)
        print("\n%s  符号@0x%x  大小=%dB  → %d 节" % (name, addr, size, len(got)))
        if len(got) != len(exp):
            print("  ✗ 节数不符（期望 %d）" % len(exp)); allok = False; continue
        worst = 0
        for i, (gc, ec) in enumerate(zip(got, exp)):
            ulp = [abs(int(np.float64(a).view(np.int64)) - int(np.float64(b).view(np.int64)))
                   for a, b in zip(gc, ec)]
            mx = max(ulp)
            worst = max(worst, mx)
            print("  节%d  %s  (最大 ULP=%d)" % (i, "逐位一致 ✓" if mx == 0 else "偏差 %d ULP" % mx, mx))
            if mx:
                for a, b in zip(gc, ec):
                    if a != b:
                        print("      got=%.20g  exp=%.20g" % (a, b))
        allok &= (worst == 0)
        print("  ⇒ %s" % ("PASS（编译器发出的位模式 = 设计值）" if worst == 0 else "FAIL"))

    # 交叉检查：D3 的 BP 4 节是否与 D2 的 BP 4 节逐位相同（不该被扰动）
    o2 = os.path.join(OUT, "ef_p2.o")
    g2, _, _ = decode(o2, "ECG_FILTER_COEFFS_F64", "double")
    g3, _, _ = decode(obj, "ECG_FILTER_COEFFS_F64", "double")
    bp2 = g2[2:6]
    bp3 = g3[4:8]
    same = all(a == b for c1, c2 in zip(bp2, bp3) for a, b in zip(c1, c2))
    print("\n交叉检查：D3 的 BP 4 节 与 D2 的 BP 4 节 %s" % ("逐位相同 ✓" if same else "不同 ✗"))
    allok &= same
    # 交叉检查：D3 的 100Hz 节 与 D2 的 100Hz 节应不同（Q30→Q15）
    d2_100 = g2[1]; d3_100 = g3[3]
    print("交叉检查：100Hz 陷波 D2 与 D3 %s（预期不同，Q30→Q15）"
          % ("不同 ✓" if d2_100 != d3_100 else "相同 ✗"))
    allok &= (d2_100 != d3_100)

    print("\n" + "=" * 80)
    print("总判定：%s" % ("全部 PASS ✓" if allok else "存在 FAIL ✗"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
