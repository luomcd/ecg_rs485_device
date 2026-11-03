#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AECG100 Waveform Raw 格式合规自检

手册 §4.8 (p66-67) 的四条硬约束，逐条查：
  1. 4 行头：fs / n_samples / n_signals / signal_desc
  2. n_samples 必须等于其后实际数据行数（少一行 AECG100 就报错或截断）
  3. 每行一个采样点，单位 mV，ECG 范围 **[-5, +5]**
  4. 每行必须能解析成数（空行、NaN、inf 都会让播放器卡住）

用法:
    python tools/aecg_verify.py aecg_raw/                  # 整目录
    python tools/aecg_verify.py aecg_raw/116.txt           # 单文件
    python tools/aecg_verify.py aecg_raw/ --strict-range   # 越界即失败(而非警告)

退出码: 0 全通过 / 1 有错误
"""
import argparse
import os
import sys

MV_LIMIT = 5.0


def verify_file(path, strict_range=False):
    """返回 (errs, warns, info)"""
    errs, warns = [], []
    info = {}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return ["打不开: %s" % e], [], info

    # --- 行尾 ---
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    info["eol"] = "CRLF" if crlf and crlf == lf else ("LF" if lf else "none")
    if crlf not in (0, lf):
        errs.append("行尾混用（CRLF %d / LF %d）—— 播放器按行解析会错位" % (crlf, lf))

    if not raw:
        return ["空文件"], [], info

    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as e:
        return ["非 ASCII 内容: %s" % e], [], info

    lines = text.splitlines()
    # 去掉可能的末尾空行
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) < 5:
        return ["行数 %d < 5，连头都放不下" % len(lines)], [], info

    # --- 4 行头 ---
    try:
        fs = float(lines[0])
        n_decl = int(lines[1])
        n_sig = int(lines[2])
    except ValueError:
        return ["头 3 行不是数字: %r" % lines[:3]], [], info
    info.update(fs=fs, n_decl=n_decl, n_sig=n_sig, desc=lines[3])

    if fs <= 0:
        errs.append("fs=%g 非法" % fs)
    if n_sig != 1:
        errs.append("n_signals=%d，本工具只产单通道" % n_sig)

    data = lines[4:]
    n_act = len(data)
    info["n_act"] = n_act

    if n_decl != n_act:
        errs.append("n_samples 声明 %d 但实际 %d 行（差 %+d）"
                    % (n_decl, n_act, n_act - n_decl))

    # --- 逐点解析 + 范围 ---
    lo, hi = float("inf"), float("-inf")
    over = 0
    bad = []
    decs = set()
    for i, s in enumerate(data):
        t = s.strip()
        if not t:
            bad.append((i + 5, "<空行>"))
            if len(bad) > 5:
                break
            continue
        try:
            v = float(t)
        except ValueError:
            bad.append((i + 5, t[:24]))
            if len(bad) > 5:
                break
            continue
        if v != v or v in (float("inf"), float("-inf")):
            bad.append((i + 5, t[:24]))
            if len(bad) > 5:
                break
            continue
        if v < lo:
            lo = v
        if v > hi:
            hi = v
        if abs(v) > MV_LIMIT + 1e-9:
            over += 1
        if "." in t:
            decs.add(len(t.split(".")[1]))

    if bad:
        errs.append("前 %d 条坏数据行: %s" % (len(bad), bad))
    if n_act:
        info.update(lo=lo, hi=hi, over=over)
        if over:
            msg = "超 ±5 mV %d 点（%.4f%%），AECG100 会削顶" % (over, 100.0 * over / n_act)
            (errs if strict_range else warns).append(msg)
        if decs:
            info["decimals"] = sorted(decs)
            if max(decs) > 4:
                warns.append("小数字数到 %d 位，文件会偏大" % max(decs))

    info["dur_s"] = n_act / fs if fs else 0
    return errs, warns, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="目录或单个 .txt")
    ap.add_argument("--strict-range", action="store_true", help="越界算错误")
    ap.add_argument("--quiet", action="store_true", help="只打印问题与汇总")
    args = ap.parse_args()

    if os.path.isdir(args.target):
        files = sorted(os.path.join(args.target, f)
                       for f in os.listdir(args.target) if f.endswith(".txt"))
    else:
        files = [args.target]
    if not files:
        raise SystemExit("没找到 .txt")

    n_err = n_warn = 0
    tot_pt = 0
    tot_s = 0.0
    tot_b = 0
    for fp in files:
        errs, warns, info = verify_file(fp, args.strict_range)
        n_err += len(errs)
        n_warn += len(warns)
        tot_pt += info.get("n_act", 0)
        tot_s += info.get("dur_s", 0.0)      # 按时长累加，点数是另一回事
        tot_b += os.path.getsize(fp)
        tag = "OK  " if not errs else "FAIL"
        if not args.quiet or errs:
            print("[%s] %-16s fs=%-6g 声明=%-7s 实际=%-7s %7.2f s  %+.3f..%+.3f mV%s"
                  % (tag, os.path.basename(fp), info.get("fs", 0),
                     info.get("n_decl", "?"), info.get("n_act", "?"),
                     info.get("dur_s", 0), info.get("lo", 0), info.get("hi", 0),
                     ("  缩放/超限 %d" % info["over"]) if info.get("over") else ""))
        for e in errs:
            print("        ✗ %s" % e)
        for w in warns:
            print("        ! %s" % w)

    print("\n合计 %d 文件  %d 点  %.2f 小时  %.1f MB"
          % (len(files), tot_pt, tot_s / 3600.0, tot_b / 1048576.0))
    print("错误 %d 条，警告 %d 条 ⇒ %s" % (n_err, n_warn, "通过" if not n_err else "**不通过**"))
    return 0 if not n_err else 1


if __name__ == "__main__":
    sys.exit(main())
