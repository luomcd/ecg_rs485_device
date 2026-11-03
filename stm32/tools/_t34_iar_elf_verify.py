# -*- coding: utf-8 -*-
"""
_t34_iar_elf_verify.py —— 第 5 道闸门（IAR 版）：从 **IAR 实际编译产出的 ELF** 里
把 `ECG_FILTER_COEFFS_F64` 的 520 字节抠出来，与生成器 N2A 的期望值**逐位**比对。

与 _t33_rodata_verify.py 的区别：
  _t33 验的是 arm-none-eabi-gcc 单独编译的 .o（工具链侧）；
  _t34 验的是 IAR 工程真正链接进 .hex 的那份表（上板侧）—— 防"用了旧 .o / 宏被覆盖"。

不改任何源码，只读 ELF。
"""
import io
import os
import struct
import sys
import contextlib

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
ELF = os.path.join(ROOT, "EWARM/demo_ads1293/build/demo_ads1293/demo_ads1293.elf")
SYM = "ECG_FILTER_COEFFS_F64"

# ---------------------------------------------------------------- 1) 期望值
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_f3", os.path.join(TOOLS, "gen_f3_coeffs.py"))
    gen = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(gen)
    except SystemExit:
        pass
N2A = gen.N2A
assert len(N2A) == 13, len(N2A)

exp = b"".join(struct.pack("<5d", *[(float(v)) for v in sec]) for sec in N2A)
assert len(exp) == 520, len(exp)


# ---------------------------------------------------------------- 2) 解析 ELF
def read_elf(path):
    """支持 ELF32 / ELF64 容器（IAR 9.x 默认把 32 位 ARM 代码装进 ELF64 容器）"""
    d = open(path, "rb").read()
    assert d[:4] == b"\x7fELF", "不是 ELF"
    is64 = d[4] == 2
    if is64:
        e_shoff, = struct.unpack_from("<Q", d, 0x28)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    else:
        e_shoff, = struct.unpack_from("<I", d, 0x20)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x2E)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if is64:
            name, typ = struct.unpack_from("<II", d, off)
            addr, offset, size = struct.unpack_from("<QQQ", d, off + 16)
            link = struct.unpack_from("<I", d, off + 40)[0]
            entsize = struct.unpack_from("<Q", d, off + 56)[0]
        else:
            name, typ, flags, addr, offset, size = struct.unpack_from("<IIIIII", d, off)
            link = struct.unpack_from("<I", d, off + 24)[0]
            entsize = struct.unpack_from("<I", d, off + 36)[0]
        secs.append(dict(name_off=name, type=typ, addr=addr, off=offset,
                         size=size, link=link, entsize=entsize))
    str_off = secs[e_shstrndx]["off"]
    for s in secs:
        e = d.index(b"\0", str_off + s["name_off"])
        s["name"] = d[str_off + s["name_off"]:e].decode("latin1")
    return d, secs, is64


def symtab(path):
    """读 .symtab 拿符号地址/大小"""
    d, secs, is64 = read_elf(path)
    out = {}
    for s in secs:
        if s["type"] != 2:      # SHT_SYMTAB
            continue
        str_off = secs[s["link"]]["off"]
        entsize = s["entsize"] or (24 if is64 else 16)
        n = s["size"] // entsize
        for i in range(n):
            o = s["off"] + i * entsize
            if is64:
                nm, info, other, shndx = struct.unpack_from("<IBBH", d, o)
                val, sz = struct.unpack_from("<QQ", d, o + 8)
            else:
                nm, val, sz, info, other, shndx = struct.unpack_from("<IIIBBH", d, o)
            if nm == 0:
                continue
            e = d.index(b"\0", str_off + nm)
            name = d[str_off + nm:e].decode("latin1")
            out[name] = (val, sz, shndx)
        break
    return d, secs, out


def vaddr_bytes(d, secs, addr, size):
    for s in secs:
        if s["type"] == 1 and s["addr"] <= addr and addr + size <= s["addr"] + s["size"]:
            fo = s["off"] + (addr - s["addr"])
            return d[fo:fo + size], s["name"]
    raise RuntimeError("地址 0x%08x 不在任何 PROGBITS 段内" % addr)


print("=" * 96)
print("IAR ELF 逐位回读验证：%s" % os.path.relpath(ELF, ROOT).replace("\\", "/"))
print("=" * 96)
print("  ELF 修改时间 : %s" % __import__("time").strftime(
    "%Y-%m-%d %H:%M:%S", __import__("time").localtime(os.path.getmtime(ELF))))
print("  ELF 大小     : %.1f KB" % (os.path.getsize(ELF) / 1024.0))

d, secs, syms = symtab(ELF)
if SYM not in syms:
    print("  ✗ ELF 里找不到符号 %s（可能被链接器剔除：F32 链未使用）" % SYM)
    sys.exit(1)

addr, size, shndx = syms[SYM]
blob, secname = vaddr_bytes(d, secs, addr, size)

print("  符号         : %s @ 0x%08X  size=%d B (=%d 节 x 40 B)  段=%s"
      % (SYM, addr, size, size // 40, secname))
print()

nsec = size // 40
ok_all = bool(nsec == 13)
nbad = 0
print("  节  %-26s %-12s %-24s %-24s %s" % ("", "符号 size", "期望(生成器)", "实际(ELF)", "逐位"))
print("  " + "-" * 104)
for i in range(nsec):
    got = struct.unpack_from("<5d", blob, i * 40)
    if i < 13:
        want = tuple(float(v) for v in N2A[i])
    else:
        want = None
    if want is None:
        print("  %2d  %-26s %-12s %-24s %-24s [多余节]" % (i, "", "", "", ""))
        ok_all = False
        continue
    bits_got = struct.pack("<5d", *got)
    bits_want = struct.pack("<5d", *want)
    same = bits_got == bits_want
    nbad += (not same)
    tag = "%s" % ("一致" if same else "!!!! 不一致")
    print("  %2d  %-26s %-12s %-24.17g %-24.17g %s"
          % (i, "", "%d B" % 40, want[0], got[0], tag))
    if not same:
        for j, k in enumerate("b0 b1 b2 a1 a2".split()):
            if bits_got[j * 8:j * 8 + 8] != bits_want[j * 8:j * 8 + 8]:
                print("       └ %s: 期望 %.17g / 实际 %.17g" % (k, want[j], got[j]))
print("  " + "-" * 104)
print("  结果：%d/%d 节逐位一致%s" % (nsec - nbad, nsec, "" if nbad == 0 else "  ← 有 %d 节不符" % nbad))
print()

# 顺带核对 F32 表在不在（固件若只用 f64，F32 会被 IAR 剔除，属正常）
if "ECG_FILTER_COEFFS_F32" in syms:
    a2, s2, _ = syms["ECG_FILTER_COEFFS_F32"]
    print("  旁证：ECG_FILTER_COEFFS_F32 在 ELF 中 @ 0x%08X size=%d B (=%d 节 x 20 B)" % (a2, s2, s2 // 20))
else:
    print("  旁证：ECG_FILTER_COEFFS_F32 不在 ELF 中 —— f32 链未被引用，IAR 链接期剔除了（正常）")

print()
print("  判决：%s" % ("通过 —— 烧进片子的就是 N2a(13 节) 新表" if (ok_all and nbad == 0)
                    else "不通过 —— ELF 里的表不是 N2a，先查是否用了旧 .o / 宏被覆盖"))
print("=" * 96)
