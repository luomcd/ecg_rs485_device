#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rp_compare.py —— C 固件实现 vs Python 重放，逐峰对账（T225 §4.4 / §6.2 的回归 oracle）

为什么必须有这个工具
--------------------
P3 要改 `ecg_rpeak.c`。判断「改对了没有」不能只看 σ(e)，必须证明两件事：
  ① **主路径逐峰完全不变**（改动的边界不能溢出）；
  ② 回溯路径的 C 输出与 Python `RP_FID_MODE=1` **逐峰相同**（重放确实是 oracle）。

对账口径
--------
· C 侧：编译**同一份** `ecg_rpeak.c`，用 `-DECG_RP_USE_STORED_FID=0/1` 生成两个 exe；
· Python 侧：`RP_FID_MODE=0/1`；
· 逐峰比较峰**索引序列**（不是峰数），报告首个分歧位置与最大偏移。

用法
----
    py rp_compare.py                       # 跑内置 11 份 + 两种模式
    py rp_compare.py --bins _raw_90bpm_2mv.bin
    py rp_compare.py --skip-build          # 复用已编好的 exe
"""
import argparse
import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.path.abspath(os.path.join(HERE, ".."))
RP_C = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg", "ecg_rpeak.c")
RP_INC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg")
HOST_C = os.path.join(HERE, "rpeak_host", "main.c")
OUTDIR = os.path.join(HERE, "_rp_cmp")

DEFAULT_BINS = [
    ("60 bpm A", "_raw_60bpm_2mv.bin", 60),
    ("60 bpm B", "_raw_60bpm_2mv_b.bin", 60),
    ("60 bpm C", "_raw_60bpm_2mv_c.bin", 60),
    ("90 bpm A", "_raw_90bpm_2mv.bin", 90),
    ("90 bpm B", "_raw_90bpm_2mv_b.bin", 90),
    ("120 bpm", "_raw_120bpm_2mv.bin", 120),
    ("150 bpm", "_raw_150bpm_2mv.bin", 150),
    ("180 bpm", "_raw_180bpm_2mv.bin", 180),
    ("210 bpm", "_raw_210bpm_2mv.bin", 210),
    ("270 bpm", "_raw_270bpm_2mv.bin", 270),
    ("300 bpm", "_raw_300bpm_2mv.bin", 300),
]


def build(fid):
    os.makedirs(OUTDIR, exist_ok=True)
    exe = os.path.join(OUTDIR, "rp_fid%d.exe" % fid)
    cmd = ["gcc", "-O2", "-Wall", "-o", exe, HOST_C, RP_C, "-I" + RP_INC, "-lm",
           "-DECG_RP_USE_STORED_FID=%d" % fid]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("编译失败（fid=%d）：\n%s\n%s" % (fid, r.stdout, r.stderr))
    if r.stderr.strip():
        print("  [gcc 告警 fid=%d] %s" % (fid, r.stderr.strip()[:400]))
    return exe


def run_c(exe, bin_path, tag):
    os.makedirs(OUTDIR, exist_ok=True)
    csv = os.path.join(OUTDIR, "%s_%s.csv" % (tag, os.path.basename(exe).replace(".exe", "")))
    subprocess.run([exe, bin_path, csv], capture_output=True, text=True)
    peaks = []
    with open(csv, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("index"):
                continue
            peaks.append(int(line.split(",")[0]))
    return peaks


def run_py(bin_path, fid):
    os.environ["RP_FILT_HIST"] = "140"
    os.environ["RP_FID_MODE"] = str(fid)
    import replay_rpeak as R
    importlib.reload(R)
    samples, seglens, bad = R.load_frames(bin_path)
    rp = R.RPeak()
    peaks, srcs = [], []
    for x in samples:
        for idx, dg in rp.process(x):
            if idx is not None:
                peaks.append(idx)
                srcs.append(dg.get("src", 0))
    return peaks, srcs


def change_scope(c_off, c_on, srcs):
    """改动面：C(fid=0) vs C(fid=1) 的差异**是否全部落在回溯峰上**

    返回 (差异个数, 其中 src=1 的个数, 最大偏移)。
    🛑 这是 P3 的核心验收口径（T225 §6.2「主路径输出 fid 开关前后逐峰完全相同」）：
       差异必须 100% 来自回溯峰；只要有 1 个主路径峰被动了，就是改动溢出。
    """
    n = min(len(c_off), len(c_on))
    ndiff = 0
    nbt = 0
    maxd = 0
    for k in range(n):
        if c_off[k] != c_on[k]:
            ndiff += 1
            if k < len(srcs) and srcs[k] == 1:
                nbt += 1
            maxd = max(maxd, abs(c_off[k] - c_on[k]))
    return ndiff, nbt, maxd


def sigma(peaks, bpm, skip=10):
    """与 replay_rpeak.stats 同口径的 σ(e)（Δ²RR / √6）"""
    import statistics as st
    p = peaks[skip:]
    if len(p) < 20:
        return float("nan"), 0
    rr = [int(p[k]) - int(p[k - 1]) for k in range(1, len(p))]
    med = sorted(rr)[len(rr) // 2]
    thr = max(3.0, 0.02 * med)
    anom = sum(1 for v in rr if abs(v - med) > thr)
    d2 = [int(p[k + 1]) - 2 * int(p[k]) + int(p[k - 1]) for k in range(1, len(p) - 1)]
    return st.pstdev(d2) / (6 ** 0.5), anom


def diff(a, b):
    """返回 (是否完全一致, 首个分歧描述, 长度差, 共有部分最大绝对偏移)"""
    if a == b:
        return True, "", 0, 0
    n = min(len(a), len(b))
    for k in range(n):
        if a[k] != b[k]:
            md = max([abs(a[j] - b[j]) for j in range(n)] or [0])
            return False, "#%d  C=%d Py=%d（本次及之后可能整体错位）" % (k, a[k], b[k]), \
                len(a) - len(b), md
    return False, "前 %d 个相同，长度不同" % n, len(a) - len(b), 0


def main():
    ap = argparse.ArgumentParser(description="C 固件 vs Python 重放 逐峰对账")
    ap.add_argument("--bins", nargs="*", help="只跑这些 bin（默认内置 11 份）")
    ap.add_argument("--skip-build", action="store_true")
    a = ap.parse_args()

    bins = DEFAULT_BINS
    if a.bins:
        bins = [(os.path.basename(b), b, 0) for b in a.bins]

    if a.skip_build:
        exes = {0: os.path.join(OUTDIR, "rp_fid0.exe"),
                1: os.path.join(OUTDIR, "rp_fid1.exe")}
    else:
        print("编译 C 夹具（ECG_RP_USE_STORED_FID=0 / 1）…")
        exes = {0: build(0), 1: build(1)}

    print()
    hdr = "%-10s %6s %6s %6s %6s | %-22s | %-22s | %s" % (
        "数据", "C_off", "Py_off", "C_on", "Py_on",
        "① C_off vs Py_off", "② C_on vs Py_on", "③ 改动面（C_off→C_on）")
    print(hdr)
    print("-" * (len(hdr) + 22))
    all_ok = True
    rows = []
    for tag, fname, bpm in bins:
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            print("%-10s  缺文件 %s" % (tag, fname))
            all_ok = False
            continue
        base = os.path.basename(fname).replace("_raw_", "").replace(".bin", "")
        c0 = run_c(exes[0], path, base)
        c1 = run_c(exes[1], path, base)
        p0, s0 = run_py(path, 0)
        p1, _ = run_py(path, 1)

        ok1, d1, n1, m1 = diff(c0, p0)
        ok2, d2, n2, m2 = diff(c1, p1)
        nd, nbt, maxd = change_scope(c0, c1, s0)
        all_ok = all_ok and ok1 and ok2 and (nd == nbt)

        s1 = "一致" if ok1 else ("✗ %s" % d1)
        s2 = "一致" if ok2 else ("✗ %s" % d2)
        s3 = ("只动回溯 %d 个（Δmax=%d）" % (nd, maxd)) if (nd and nd == nbt) else \
             ("无变化" if nd == 0 else "⚠ 溢出：动 %d 个，其中仅 %d 个是回溯" % (nd, nbt))
        print("%-10s %6d %6d %6d %6d | %-22s | %-22s | %s"
              % (tag, len(c0), len(p0), len(c1), len(p1), s1, s2, s3))

        sig0, an0 = sigma(c0, bpm)
        sig1, an1 = sigma(c1, bpm)
        rows.append((tag, bpm, sig0, an0, sig1, an1))

    print()
    print("%-10s %6s %10s %8s %10s %8s" % ("数据", "bpm", "σ(e)现状", "异常", "σ(e)fid", "异常"))
    for tag, bpm, s0, a0, s1, a1 in rows:
        print("%-10s %6s %10.2f %8d %10.2f %8d"
              % (tag, ("%g" % bpm) if bpm else "-", s0, a0, s1, a1))

    print()
    if all_ok:
        print("结论：① 夹具与固件逐峰一致 ✓　② C 实现与已验证模型逐峰一致 ✓　③ 改动 100% 落在回溯峰 ✓")
    else:
        print("结论：存在分歧 ✗ —— 先查 Python 状态机/改动是否溢出主路径，再谈进固件")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
