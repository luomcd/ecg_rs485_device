"""实验：把「启动预热不计数」从**时长触发**换成**数据触发**（计数闸门）是否可行

前一个实验（`mitbih_warmup_eval.py`）已证：固定时长预热无效 —— 假峰在 5 s 基线里
均匀分布（6/4/4/6/4），预热 1 s 只切掉 6/24，窗后 18 个照来。
真正的问题不是"开多久"，是"怎么知道输入还不是心电图"。

本实验试**数据触发的计数闸门**：
    开闸条件 = signal_peak >= K * noise_peak      （K=0 表示关闸门，即现状）
  · 纯噪声时 signal_peak ≈ noise_peak（实测比值 0.4~1.1）⇒ 闸门关 ⇒ 不计数不上报
  · 真 QRS 到来时 signal_peak 跳 3 个数量级（15 → 1.2e4）⇒ 比值 20~200 ⇒ 立即开闸

🛑 **固件源码一个字节都不改**：本脚本把 `ecg_rpeak.c` 读进内存、打上 4 处补丁、
   写进临时目录再编译成独立 exe。补丁落点做「必须命中恰好 1 次」校验，命中数不对就中止。
   固件编译永远走原文件，闸门宏 `ECG_RP_COUNT_GATE_K` 默认为 0（关）。

用法：
  python tools/mitbih_gate_eval.py                    # K = 0/2/3/5/10/20
  python tools/mitbih_gate_eval.py --ks 0,3,5 --recs 100,105,108
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from mitbih_eval import read_truth, score, TOL               # noqa: E402
from mitbih_pad_eval import read_t101_bin, load_quiet_segment  # noqa: E402
from mitbih_prep import write_bin                           # noqa: E402

FS = 500
REC_DIR = os.path.join(ROOT, "mit_bih", "records")
SRC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg", "ecg_rpeak.c")
INC = os.path.join(ROOT, "cardiolite-ecg", "main", "ecg")
WINAPP = os.path.join(ROOT, "recordings_winapp")

PATCHES = [
    # (说明, 原文, 新文)
    ("加宏",
     "#define TINY        1e-12f",
     "#define TINY        1e-12f\n"
     "\n"
     "/* 【实验补丁】计数闸门系数，0 = 关（固件默认） */\n"
     "#ifndef ECG_RP_COUNT_GATE_K\n"
     "#define ECG_RP_COUNT_GATE_K 0.0f\n"
     "#endif"),

    ("accept_peak 改返回值",
     "static void accept_peak(ecg_rpeak_t *rp, int64_t peak, float value, float slope,",
     "static bool accept_peak(ecg_rpeak_t *rp, int64_t peak, float value, float slope,"),

    ("accept_peak 内加闸门",
     "    rp->last_accepted_slope = slope;\n"
     "    rp->peak_total++;\n"
     "    peak_push(rp, peak);\n"
     "\n"
     "    update_bpm(rp);\n"
     "\n"
     "    if (out_index != NULL) {\n"
     "        *out_index = peak;\n"
     "    }\n"
     "}",
     "    rp->last_accepted_slope = slope;\n"
     "\n"
     "    /* 【实验补丁】闸门：signal_peak 还在噪声量级 ⇒ 不计数、不推进 RR、不上报 */\n"
     "    if (ECG_RP_COUNT_GATE_K > 0.0f && rp->noise_peak > 0.0f &&\n"
     "        GATE_LHS < ECG_RP_COUNT_GATE_K * rp->noise_peak) {\n"
     "        return false;\n"
     "    }\n"
     "\n"
     "    rp->peak_total++;\n"
     "    peak_push(rp, peak);\n"
     "\n"
     "    update_bpm(rp);\n"
     "\n"
     "    if (out_index != NULL) {\n"
     "        *out_index = peak;\n"
     "    }\n"
     "    return true;\n"
     "}"),

    ("主路径调用点",
     "                    accept_peak(rp, locate_rpeak(rp, peak), prev_integ, slope, out_index);\n"
     "                    detected = true;",
     "                    detected = accept_peak(rp, locate_rpeak(rp, peak), prev_integ,\n"
     "                                           slope, out_index);"),

    ("回溯路径调用点",
     "                    accept_peak(rp, locate_rpeak(rp, c->index), c->value, c->slope, out_index);\n"
     "                    detected = true;",
     "                    detected = accept_peak(rp, locate_rpeak(rp, c->index), c->value,\n"
     "                                           c->slope, out_index);"),
]


def make_patched(tmp, K, mode="ema"):
    """打补丁 → 编译，返回 exe 路径。mode: ema（慢 EMA signal_peak）/ inst（当拍 integ）"""
    lhs = "rp->signal_peak" if mode == "ema" else "value"
    src = open(SRC, encoding="utf-8").read()
    for name, old, new in PATCHES:
        if name == "accept_peak 内加闸门":
            new = new.replace("GATE_LHS", lhs)
        cnt = src.count(old)
        if cnt != 1:
            raise SystemExit(f"🛑 补丁落点「{name}」命中 {cnt} 次（应恰好 1 次）—— 源码变了，先对齐")
        src = src.replace(old, new)
    cpath = os.path.join(tmp, f"ecg_rpeak_gate_{mode}_{K:g}.c")
    open(cpath, "w", encoding="utf-8", newline="\n").write(src)
    exe = os.path.join(tmp, f"rpeak_gate_{mode}_{K:g}.exe")
    cmd = ["gcc", "-O2", f"-DECG_RP_COUNT_GATE_K={K}f", "-o", exe,
           os.path.join(HERE, "rpeak_host", "main.c"), cpath, "-I", INC]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"编译失败 K={K}:\n{r.stderr[:2000]}")
    return exe


def peaks_of(exe, samples, tmp, tag):
    binf = os.path.join(tmp, f"{tag}.bin")
    txtf = os.path.join(tmp, f"{tag}_pk.txt")
    write_bin(binf, np.asarray(samples, dtype=np.int16))
    r = subprocess.run([exe, binf, txtf], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    out = []
    for line in open(txtf, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("index"):
            out.append(int(line.split(",")[0]))
    os.remove(binf)
    if os.path.exists(txtf):
        os.remove(txtf)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="0,2,3,5,10,20", help="闸门系数列表，0=关")
    ap.add_argument("--gate-mode", default="ema",
                    help="ema = 用慢 EMA signal_peak 判；inst = 用当拍 integ 判")
    ap.add_argument("--pad-s", type=float, default=5.0)
    ap.add_argument("--recs", default="")
    args = ap.parse_args()

    ks = [float(s) for s in args.ks.split(",") if s.strip()]
    modes = [s.strip() for s in args.gate_mode.split(",") if s.strip()]
    pad_n = int(round(args.pad_s * FS))
    recs = ([s.strip().zfill(3) for s in args.recs.split(",") if s.strip()]
            if args.recs else
            sorted(os.path.basename(p)[:3]
                   for p in glob.glob(os.path.join(REC_DIR, "*_500.bin"))))

    quiet = load_quiet_segment(os.path.join(WINAPP, "ecg_20261028_144146_MIT108-seg02.bin"), pad_n)
    tmp = tempfile.mkdtemp(prefix="gate_eval_")

    data = []
    for rec in recs:
        src = os.path.join(REC_DIR, f"{rec}_500.bin")
        ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
        if not (os.path.exists(src) and os.path.exists(ann)):
            continue
        base = read_t101_bin(src).astype(np.float64)
        rows, _ = read_truth(ann)
        truth = [(s, sym) for s, sym, b in rows if b]
        data.append((rec, base, truth))

    print(f"记录 {len(data)} 条  闸门 = signal_peak >= K*noise_peak（K=0 即现状）")
    print(f"补丁生成于 {tmp}\n")

    hdr = (f"{'模式':>5}{'K':>6}{'场景':>7}{'真值':>8}{'TP':>8}{'FP':>7}{'FN':>7}"
           f"{'Se%':>7}{'+P%':>7}{'DER%':>7}{'首拍':>8}{'基线假峰':>9}")
    print(hdr)
    print("-" * len(hdr))
    for mode in modes:
        for K in ks:
            exe = make_patched(tmp, K, mode)
            for scene in ("orig", "pad"):
                n = tp = fp = fn = 0
                ok1 = 0
                fake = 0
                for rec, base, truth in data:
                    if scene == "orig":
                        seq, head = base, 0
                    else:
                        seq, head = np.concatenate([quiet, base]), pad_n
                    pk = peaks_of(exe, seq, tmp, f"{rec}_{scene}_{mode}_{K:g}")
                    fake += sum(1 for p in pk if p < head)
                    det = [p - head for p in pk if p >= head]
                    r = score(truth, det)
                    n += r["n"]; tp += r["tp"]; fp += r["fp"]; fn += r["fn"]
                    ok1 += 1 if (truth and any(abs(d - truth[0][0]) <= TOL for d in det)) else 0
                print(f"{mode:>5}{K:>6g}{scene:>7}{n:>8}{tp:>8}{fp:>7}{fn:>7}"
                      f"{tp / n * 100:>7.2f}{tp / (tp + fp) * 100:>7.2f}"
                      f"{(fp + fn) / n * 100:>7.2f}{ok1:>4}/{len(data):<3}{fake:>9}")

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
