# -*- coding: utf-8 -*-
"""
_t29_rodata_verify.py —— 从编译产物 ELF 的 .rodata 里逐位回读 IMP620 系数表，
与 scipy 生成器（gen_f3_coeffs.py）的值做 bit-exact 比对。

判据：f64 全部 45 个 double 逐位相同；f32 全部 45 个 float 逐位相同。
"""
import io
import os
import subprocess
import struct
import sys
import numpy as np
from scipy import signal as sps

GCCDIR = "C:/Nordic/Toolchian/gcc-arm-none-eabi-10-2020-q4-major/bin"
OBJCOPY = GCCDIR + "/arm-none-eabi-objcopy.exe"
NM = GCCDIR + "/arm-none-eabi-nm.exe"
OBJ = "C:/work2026/VsPro/ads1293_demo/tools/_build/ecg_filter_imp620.o"
BIN = "C:/work2026/VsPro/ads1293_demo/tools/_build/rodata.bin"

FS = 1e6 / 1172.0
NSEC = 9


def gen_imp620():
    def notch(f0, Q):
        b, a = sps.iirnotch(f0, Q, fs=FS)
        row = sps.tf2sos(b, a)[0]
        return (row[0], row[1], row[2], row[4], row[5])
    bp = [(r[0], r[1], r[2], r[4], r[5]) for r in
          sps.butter(4, [0.05, 150.0], btype="bandpass", fs=FS, output="sos")]
    return [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
            notch(100.0, 15.0), notch(150.0, 30.0)] + bp


def nm_syms():
    out = subprocess.run([NM, "--print-size", OBJ], capture_output=True, text=True).stdout
    syms = {}
    for ln in out.splitlines():
        p = ln.split()
        # 形如: 00000000 000000b4 R ECG_FILTER_COEFFS_F32
        if len(p) >= 4 and p[-1].startswith("ECG_FILTER_COEFFS") and len(p[0]) == 8 and len(p[1]) == 8:
            syms[p[-1]] = (int(p[0], 16), int(p[1], 16))
    return syms


def dump_section():
    r = subprocess.run([OBJCOPY, "-O", "binary", "--only-section=.rodata", OBJ, BIN],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("objcopy 失败:", r.stderr)
        sys.exit(1)
    return io.open(BIN, "rb").read()


sec = dump_section()
syms = nm_syms()
print("=" * 92)
print("ELF 逐位回读验证 —— %s" % os.path.basename(OBJ))
print("=" * 92)
print("  .rodata 长度 = %d B" % len(sec))
for k, v in syms.items():
    print("  %-28s off=0x%04x  size=%d B (= %d 节 × %d B)" %
          (k, v[0], v[1], v[1] // (20 if "F32" in k else 40), 20 if "F32" in k else 40))

want = gen_imp620()
assert len(want) == NSEC

bad = 0

# ---- f32 ----
o, n = syms["ECG_FILTER_COEFFS_F32"]
got32 = np.frombuffer(sec[o:o + n], dtype="<f4").reshape(NSEC, 5)
want32 = np.array([[np.float32(x) for x in row] for row in want], dtype="<f4")
d32 = (got32.view(np.uint32) != want32.view(np.uint32)).sum()
bad += int(d32)
print()
print("  f32 表：%d/%d 个 float 逐位一致 %s" % (NSEC * 5 - d32, NSEC * 5, "✓" if d32 == 0 else "✗"))

# ---- f64 ----
o, n = syms["ECG_FILTER_COEFFS_F64"]
got64 = np.frombuffer(sec[o:o + n], dtype="<f8").reshape(NSEC, 5)
want64 = np.array(want, dtype="<f8")
d64 = (got64.view(np.uint64) != want64.view(np.uint64)).sum()
bad += int(d64)
print("  f64 表：%d/%d 个 double 逐位一致 %s" % (NSEC * 5 - d64, NSEC * 5, "✓" if d64 == 0 else "✗"))

print()
if bad == 0:
    print("  ⇒ 结果：ELF 里的系数与生成器**逐位相同**，固件烧的就是设计的那条链 ✓")
else:
    print("  ⇒ ★不一致 %d 处★" % bad)
    for i in range(NSEC):
        for j in range(5):
            if got64[i, j].tobytes() != want64[i, j].tobytes():
                print("     f64 节%d 系数%d: elf=%.17g  gen=%.17g" % (i, j, got64[i, j], want64[i, j]))

# ---- 级联频响复述（固件同款系数，非设计值） ----
print()
print("  用 ELF 里的系数复算级联响应（dB）：")
fchk = [49.4, 49.8, 50.0, 50.2, 50.6, 100.0, 150.0, 203.2]
z = np.exp(-1j * 2 * np.pi * np.array(fchk) / FS)
H = np.ones(len(fchk), dtype=complex)
for (b0, b1, b2, a1, a2) in got64:
    H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
print("    " + "  ".join("%.1fHz:%.0f" % (f, 20 * np.log10(abs(h))) for f, h in zip(fchk, H)))
