# -*- coding: utf-8 -*-
"""
T58 端到端数值核对：源码系数表 -> 链接产物（ELF 内的字节块）

为什么需要这一层
----------------
"改宏 / 改系数文本" 只证明源码写对了。真正决定板子行为的是**镜像里的字节**。
本脚本把验证推到 ELF，做三件事：

  A. 结构账：map 里 ECG_FILTER_COEFFS_F64 的段大小 == 活动档节数 × 40 B
  B. 数值账：把该段**逐字节** dump 出来，与源码字面量逐位比对（不是近似）
  C. 残留账：T58 删掉的旧系数（LP4@150）在整份 ELF 里命中数必须为 0

三个必须知道的坑（都是踩过的）
------------------------------
1. **只有活动档被编译**：`ecg_filter.c` 用 `#if/#elif` 链切档，六套表只编一套。
   拿全部六档的字面量去搜 ELF ⇒ 大量假"未命中"。必须先读 `ECG_FILTER_PROFILE`。
2. **f32 表根本不在镜像里**：`ECG_FILTER_COEFFS_F32` 在 map 里**查无此符号** ——
   链接器把未被引用的 f32 初始化路径整体剔除了（改动前 pre-T58 版本也如此，
   非 T58 引入）。所以 f32 搜索必然 0 命中，不算缺陷，只需在报告里说明。
3. **通用值没有判别力**：0 / ±1 / ±2 在 ELF 二进制里到处都是（HP4 节的 b 就是
   `{1,-2,1}`）。这类值不参与"残留"判定，单独列为 COMMON。

用法
----
  python _t58_verify_elf.py                     # 默认：查 T58 镜像 + 对比 T58 前的备份
  python _t58_verify_elf.py --elf <path> --map <path>
  python _t58_verify_elf.py --src <ecg_filter.c> --old <bak.c>
  python _t58_verify_elf.py --quiet             # 只打印结论
"""
import argparse
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BUILD = os.path.join(PROJ, "EWARM", "demo_ads1293", "build", "demo_ads1293")
DEF_ELF = os.path.join(BUILD, "demo_ads1293.elf")
DEF_MAP = os.path.join(BUILD, "demo_ads1293.map")
DEF_SRC = os.path.join(PROJ, "Filter", "ecg_filter.c")
DEF_HDR = os.path.join(PROJ, "Filter", "ecg_filter_coeffs.h")
DEF_OLD = os.path.join(HERE, "_bak_T58_20260920", "ecg_filter.c.bak")
OBJDUMP = r"C:\Nordic\Toolchian\gcc-arm-none-eabi-10-2020-q4-major\bin\arm-none-eabi-objdump.exe"

COMMON = {0.0, 1.0, -1.0, 2.0, -2.0, 0.5, -0.5}
SEC_BYTES = 5 * 8  # f64 节：5 个 double


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def strip_comments(txt):
    txt = re.sub(r"/\*.*?\*/", " ", txt, flags=re.S)
    return re.sub(r"//[^\n]*", " ", txt)


def active_profile(hdr):
    """读 ecg_filter_coeffs.h 里 ECG_FILTER_PROFILE 的默认取值。"""
    txt = strip_comments(open(hdr, encoding="utf-8", errors="replace").read())
    m = re.search(r"#define\s+ECG_FILTER_PROFILE\s+(\w+)", txt)
    if not m:
        raise SystemExit("hdr 里找不到 ECG_FILTER_PROFILE 默认值: " + hdr)
    return m.group(1)


def profile_segment(src, prof):
    """切出 `#if (ECG_FILTER_PROFILE == prof)` 到下一个分支之前的那一段。"""
    txt = open(src, encoding="utf-8", errors="replace").read()
    marks = [(m.start(), m.group(1))
             for m in re.finditer(r"#(?:if|elif)\s*\(\s*ECG_FILTER_PROFILE\s*==\s*(\w+)\s*\)", txt)]
    if not marks:
        raise SystemExit("src 里找不到 ECG_FILTER_PROFILE 分支: " + src)
    end_all = txt.find("#endif /* ECG_FILTER_PROFILE */")
    for i, (pos, name) in enumerate(marks):
        if name == prof:
            nxt = marks[i + 1][0] if i + 1 < len(marks) else end_all
            return txt[pos:nxt]
    raise SystemExit("活动档 %s 在 %s 里没有对应分支" % (prof, src))


def parse_table(seg, kind):
    """从一段源码里取 ECG_FILTER_COEFFS_<KIND> 初始化器里的浮点字面量，按出现顺序返回。

    kind='F64' 只要不带 f 后缀的；kind='F32' 只要带 f 后缀的。
    """
    m = re.search(r"ECG_FILTER_COEFFS_%s\s*\[[^\]]*\]\s*=\s*\{(.*?)\n\};" % kind, seg, re.S)
    if not m:
        return None
    body = strip_comments(m.group(1))
    out = []
    # ★ 必须接受**整数字面量**：HP4 节的 b 写作 `{ 1, -2, 1, ... }`（f64 表）或
    #   `{ 1.0f, -2.0f, 1.0f, ... }`（f32 表）。只认带小数点的写法会漏掉这两个系数，
    #   导致节数算少、后面所有节错位（实测：10 节而非 12 节）。
    pat = re.compile(r"[-+]?(?:\d+\.\d*(?:[eE][-+]?\d+)?"
                     r"|\.\d+(?:[eE][-+]?\d+)?"
                     r"|\d+(?:[eE][-+]?\d+)?)(f?)")
    for mm in pat.finditer(body):
        is_f32 = bool(mm.group(1))
        if kind == "F64" and is_f32:
            continue
        if kind == "F32" and not is_f32:
            continue
        out.append(float(mm.group(0).rstrip("fF")))
    return out


def map_symbol(mapfile, sym):
    """从 IAR map 里取符号的 (地址, 大小)；找不到返回 None。"""
    txt = open(mapfile, encoding="utf-8", errors="replace").read()
    m = re.search(r"^%s\s+(0x[0-9a-fA-F']+)\s+(0x[0-9a-fA-F]+)\s" % re.escape(sym), txt, re.M)
    if not m:
        return None
    return int(m.group(1).replace("'", ""), 16), int(m.group(2), 16)


def dump_bytes(elf, addr, size):
    """用 arm-none-eabi-objdump 取 [addr, addr+size) 的原始字节。"""
    p = subprocess.run([OBJDUMP, "-s", "--start-address=0x%x" % addr,
                        "--stop-address=0x%x" % (addr + size), elf],
                       capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        m = re.match(r"\s*([0-9a-f]{4,})\s((?:[0-9a-f]{8}\s){1,4})", line)
        if not m:
            continue
        for w in m.group(2).split():
            out.append(bytes.fromhex(w))
    return b"".join(out)


def hits(blob, val, kind):
    try:
        pat = struct.pack("<d" if kind == "d" else "<f", val)
    except (OverflowError, struct.error):
        return 0
    n, i = 0, 0
    while True:
        j = blob.find(pat, i)
        if j < 0:
            return n
        n += 1
        i = j + 1


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elf", default=DEF_ELF)
    ap.add_argument("--map", dest="mapf", default=DEF_MAP)
    ap.add_argument("--src", default=DEF_SRC)
    ap.add_argument("--hdr", default=DEF_HDR)
    ap.add_argument("--old", default=DEF_OLD)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    for p in (a.elf, a.mapf, a.src, a.hdr):
        if not os.path.exists(p):
            print("文件不存在:", p)
            return 1

    prof = active_profile(a.hdr)
    seg = profile_segment(a.src, prof)
    f64 = parse_table(seg, "F64") or []
    f32 = parse_table(seg, "F32")
    nsec = len(f64) // 5
    print("镜像     :", a.elf)
    print("活动档   :", prof)
    print("f64 表   : %d 个系数 = %d 节" % (len(f64), nsec))

    ok = True

    # ---- A 结构账 -------------------------------------------------------- #
    print()
    print("=" * 76)
    print("A. 结构账 —— map 里 ECG_FILTER_COEFFS_F64 段大小")
    print("=" * 76)
    ent = map_symbol(a.mapf, "ECG_FILTER_COEFFS_F64")
    f32_ent = map_symbol(a.mapf, "ECG_FILTER_COEFFS_F32")
    if not ent:
        print("  ✗ map 里没有 ECG_FILTER_COEFFS_F64")
        return 2
    addr, size = ent
    exp = nsec * SEC_BYTES
    print("  地址 0x%X  段大小 %d B (0x%X)" % (addr, size, size))
    print("  期望 = %d 节 × %d B = %d B  ->  %s" % (nsec, SEC_BYTES, exp,
                                                 "✓" if size == exp else "✗ 不符"))
    ok &= (size == exp)
    print("  ECG_FILTER_COEFFS_F32 符号 :",
          "不在 map 里（链接器已剔除整个 f32 初始化路径；改动前版本亦如此）" if f32_ent is None
          else "存在，大小 %d B" % f32_ent[1])

    # ---- B 数值账 -------------------------------------------------------- #
    print()
    print("=" * 76)
    print("B. 数值账 —— 段内逐字节 vs 源码字面量（struct.pack('<d')）")
    print("=" * 76)
    raw = dump_bytes(a.elf, addr, size)
    if len(raw) != size:
        print("  ✗ dump 到的字节数 %d != 段大小 %d（objdump 分节边界问题）" % (len(raw), size))
        return 2
    bad = []
    tags = ("b0", "b1", "b2", "a1", "a2")
    for i, v in enumerate(f64):
        want = struct.pack("<d", v)
        got = raw[i * 8:(i + 1) * 8]
        if want != got:
            bad.append((i // 5, tags[i % 5], v, got.hex(), want.hex()))
    if not bad:
        print("  %d 个系数 × 8 字节 = %d 字节，**逐位全等** ✓" % (len(f64), len(raw)))
    else:
        print("  ✗ %d 个系数不符（前 10 条）：" % len(bad))
        for sec, t, v, got, want in bad[:10]:
            print("     sec%d.%s 源码 %.17g  镜像 %s  期望 %s" % (sec, t, v, got, want))
    ok &= (not bad)

    # 分段打印，便于人眼抽查
    if not a.quiet:
        print()
        print("  段内按节展开（供抽查）:")
        for s in range(nsec):
            vals = f64[s * 5:(s + 1) * 5]
            print("    sec%-2d  %s" % (s, "  ".join("%+.10f" % v for v in vals)))

    if f32:
        print()
        print("  i 注：源码里 f32 表共 %d 个系数，但如 A 段所示其符号不在 map 中，"
              "故不参与镜像比对。" % len(f32))

    # ---- C 残留账 -------------------------------------------------------- #
    print()
    print("=" * 76)
    print("C. 残留账 —— 被删掉的旧系数在整份 ELF 里的命中数")
    print("=" * 76)
    if not os.path.exists(a.old):
        print("  跳过（没有改动前源码备份）")
    else:
        blob = open(a.elf, "rb").read()
        old_all = []
        try:
            old_seg = profile_segment(a.old, prof)
            for k in ("F64", "F32"):
                t = parse_table(old_seg, k)
                if t:
                    old_all += t
        except SystemExit:
            print("  跳过（备份里没有活动档 %s 的分支）" % prof)
        newset = set(f64) | set(f32 or [])
        deleted = [v for v in dict.fromkeys(old_all) if v not in newset]
        if not deleted:
            print("  没有仅存在于旧源码的系数（值集合相同）")
        res = 0
        for v in deleted:
            if v in COMMON:
                print("  %-24s 通用值，跳过（在 ELF 里必然命中，无判别力）" % repr(v))
                continue
            hd, hf = hits(blob, v, "d"), hits(blob, v, "f")
            if hd or hf:
                res += 1
            print("  %-24s d64=%-4d f32=%-4d  %s" % (repr(v), hd, hf,
                                                     "✗ 有残留" if (hd or hf) else "✓"))
        print()
        print("  旧系数零残留 :", "✓" if res == 0 else "✗ %d 个仍有残留" % res)
        ok &= (res == 0)

    print()
    print("=" * 76)
    print("结论：", "✓ 结构账 + 数值账 + 残留账 全部通过" if ok else "✗ 有未通过项")
    print("=" * 76)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
