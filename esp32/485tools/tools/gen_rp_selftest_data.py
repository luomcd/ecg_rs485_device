#!/usr/bin/env python3
"""
把 ecg.bin 的前 N 个样本导成 C 数组，供 S7 R 峰自检用（不依赖 SD 卡）。

为什么需要：HeartRateDetector.Peaks 只保留最近 32 个峰，要验证**前段**（阈值爬升期）
就必须让总峰数 < 32，也就是只喂前 ~32 s。把它内嵌进固件后，
自检不再依赖 SD 卡挂载 —— 卡接触不良时也能跑回归。

产出的数组只用于自检，不参与正式功能；验证通过后可删（或留着当回归基准）。

用法:
    python tools/gen_rp_selftest_data.py            # 默认 16000 样本
    python tools/gen_rp_selftest_data.py --max 20000
"""

import argparse
import os
import sys

SRC = (r"C:/work2026/VsPro/WinApp_ADSECG/ADS1293_Ecg/bin/Debug/"
       r"net10.0-windows/recordings/ecg_20260929_093408.bin")
OUT = "C:/work2026/CardioLite/cardiolite-ecg/main/ecg/rp_selftest_data.c"


def load(path, maxn):
    d = open(path, "rb").read()
    out, i, frames = [], 0, 0
    while i + 6 <= len(d) and len(out) < maxn:
        if d[i] != 0xAA or d[i + 1] != 0x55:
            break
        L = d[i + 3] | (d[i + 4] << 8)
        tot = 6 + L
        if i + tot > len(d):
            break
        off = 3 if (L & 1) else 2
        n = (L - off) // 2
        for k in range(n):
            if len(out) >= maxn:
                break
            out.append(int.from_bytes(d[i + 5 + off + k * 2: i + 5 + off + k * 2 + 2],
                                      "little", signed=True))
        frames += 1
        i += tot
    return out, frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=16000)
    ap.add_argument("--src", default=SRC)
    ap.add_argument("-o", "--out", default=OUT)
    a = ap.parse_args()

    samples, frames = load(a.src, a.max)
    if not samples:
        sys.exit("[错误] 没读到样本")
    print(f"源 {a.src}")
    print(f"   取前 {len(samples)} 样本（{len(samples)/500:.2f} s @500Hz），源文件共 {frames} 帧")

    lines = [
        "/**",
        " * @file rp_selftest_data.c",
        " * @brief S7 R 峰自检用的内嵌样本（自动生成，勿手改）",
        " *",
        f" * 来源：{os.path.basename(a.src)} 的前 {len(samples)} 个 ch1 样本",
        f" *       （{len(samples)/500:.2f} s @ 500 Hz）",
        " * 生成：tools/gen_rp_selftest_data.py",
        " *",
        " * 为什么要内嵌：参考实现 HeartRateDetector.Peaks 只保留最近 32 个峰，",
        " * 要验证前段（阈值爬升期）就得让总峰数 < 32，即只喂前 ~32 s。",
        " * 内嵌后自检不依赖 SD 卡挂载。",
        " */",
        "",
        '#include "rp_selftest_data.h"',
        "",
        f"const int16_t rp_selftest_samples[{len(samples)}] = {{",
    ]

    per = 12
    for i in range(0, len(samples), per):
        chunk = ", ".join(f"{v:6d}" for v in samples[i:i + per])
        lines.append("    " + chunk + ",")
    lines.append("};")
    lines.append("")
    lines.append(f"const uint32_t rp_selftest_count = {len(samples)};")
    lines.append("")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8", newline="\n").write("\n".join(lines))

    hdr = a.out.replace("_data.c", "_data.h")
    open(hdr, "w", encoding="utf-8", newline="\n").write("\n".join([
        "/**",
        " * @file rp_selftest_data.h",
        " * @brief S7 R 峰自检内嵌样本（自动生成，勿手改）",
        " */",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        f"#define RP_SELFTEST_N  {len(samples)}",
        "",
        "extern const int16_t  rp_selftest_samples[RP_SELFTEST_N];",
        "extern const uint32_t rp_selftest_count;",
        "",
    ]))

    print(f"写出 {a.out}（{os.path.getsize(a.out)} B）")
    print(f"     {hdr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
