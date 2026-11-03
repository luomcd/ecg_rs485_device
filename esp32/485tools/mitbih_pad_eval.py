"""实验：MIT-BIH 记录前后预填充「基线」对首拍检出与总体指标的影响

背景（2026-10-29 猛哥提出，供探讨）：
  WinApp 抓数据的流程是「先点 START（此时无输入＝基线）→ AECG100 输出 → 停止记录」，
  所以真机链路的输入**天然是「基线 → 信号 → 基线」三段**。而 `mit-run` / 批处理直接喂
  文件，「上电就撞第一拍」，`signalPeak/noisePeak` 还没建立 ⇒ 首拍容易漏。
  本脚本用 PC 宿主编译的固件同一份 `ecg_rpeak.c`（`rpeak_host.exe`）验证这个猜想。

四组对照（每组都只在**记录段**上评分，pad 段不参与真值）：
  orig     原样喂（无填充）
  pad0     前拼 **全 0**（理想静息）
  padconst 前拼 **常量**（= 静息均值）—— 与 pad0 的差别只有拼接处的一个台阶
  padreal  前拼 **真实静息样本**（WinApp 录制尾部，保留 50 Hz 残余与本底抖动）

⚠ 为什么必须有 padconst 这一组：`ecg_rpeak.c` 第 0 级是 `baseline += 0.002*(x-baseline)`
   的慢速去基线（截止 ≈0.16 Hz），直流常量几十毫秒就被吃掉 ⇒ padconst 在数学上几乎
   等价于 pad0。若只跑 padconst 就下结论「喂基线没用」，那是把「喂了个直流」当成了
   「喂了基线」。

用法：
  python tools/mitbih_pad_eval.py                       # 48 条，pad 5 s（只加头）
  python tools/mitbih_pad_eval.py --pad-tail            # 头尾各 5 s（复刻 WinApp 形态）
  python tools/mitbih_pad_eval.py --pad-s 1,2,5,10      # 长度扫描
  python tools/mitbih_pad_eval.py --recs 100,105,108
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

from mitbih_eval import read_truth, score          # noqa: E402
from mitbih_prep import write_bin                 # noqa: E402

FS = 500
REC_DIR = os.path.join(ROOT, "mit_bih", "records")
HOST_EXE = os.path.join(HERE, "rpeak_host", "rpeak_host.exe")
WINAPP = os.path.join(ROOT, "recordings_winapp")
GROUPS = ("orig", "pad0", "padconst", "padreal")


# ---------------------------------------------------------------- 帧解析
def read_t101_bin(path):
    """读 T101 v2.2 帧 → int16 样本数组（与 rpeak_host.c 同一套解析规则）"""
    d = open(path, "rb").read()
    out, i = [], 0
    while i + 6 <= len(d):
        if d[i] != 0xAA or d[i + 1] != 0x55:
            i += 1
            continue
        ln = d[i + 3] | (d[i + 4] << 8)
        if i + 6 + ln > len(d):
            break
        if d[i + 2] == 0x01:
            p = d[i + 5:i + 5 + ln]
            off = 3 if ln % 2 == 1 else 2
            body = p[off:]
            k = len(body) // 2
            if k:
                out.append(np.frombuffer(body[:k * 2], dtype="<i2"))
        i += 6 + ln
    return np.concatenate(out) if out else np.zeros(0, np.int16)


def load_quiet_segment(path, n):
    """从 WinApp 录制**尾部**截 n 个样本（尾部＝输出结束后，真静息）。

    取尾部而不是头部：实测头部 std 已 1000+ LSB（录的时候信号就在），
    尾部 std ≈ 26 LSB ≈ 4.0 µV RMS，与本底噪声量级一致。
    """
    v = read_t101_bin(path)
    if len(v) < n:
        raise SystemExit(f"{path} 只有 {len(v)} 样本，不够 {n}")
    return v[-n:].astype(np.float64)


# ---------------------------------------------------------------- 出峰
def run_host(samples, tmpdir, tag):
    """样本 → T101 v2.2 bin → rpeak_host.exe → 绝对峰号列表"""
    binf = os.path.join(tmpdir, f"{tag}.bin")
    txtf = os.path.join(tmpdir, f"{tag}_peaks.txt")
    write_bin(binf, np.asarray(samples, dtype=np.int16))
    r = subprocess.run([HOST_EXE, binf, txtf], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"rpeak_host 失败: {r.stderr.strip()}")
    pk = []
    for line in open(txtf, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("index"):
            continue
        pk.append(int(line.split(",")[0]))
    os.remove(binf)
    os.remove(txtf)
    return sorted(pk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad-s", default="5", help="预填充秒数，可逗号列表做扫描（默认 5）")
    ap.add_argument("--pad-tail", action="store_true", help="尾部也拼同样长度基线")
    ap.add_argument("--recs", default="", help="逗号分隔，默认全部 48 条")
    ap.add_argument("--quiet-src", default="ecg_20261028_144146_MIT108-seg02.bin",
                    help="取静息样本的 WinApp 录制文件（默认取该文件尾部）")
    ap.add_argument("--tol", type=int, default=75, help="匹配窗 ±样本（默认 75 = 150 ms）")
    ap.add_argument("--csv", default="", help="逐条结果另存 CSV（可选）")
    args = ap.parse_args()

    pad_secs = [float(s) for s in str(args.pad_s).split(",") if s.strip()]
    max_n = int(round(max(pad_secs) * FS))
    recs = ([s.strip().zfill(3) for s in args.recs.split(",") if s.strip()]
            if args.recs else
            sorted(os.path.basename(p)[:3]
                   for p in glob.glob(os.path.join(REC_DIR, "*_500.bin"))))

    # ---- 静息样本（取最长，长 pad 直接切成短的用）
    quiet_full = load_quiet_segment(os.path.join(WINAPP, args.quiet_src), max_n)
    q_mean = float(quiet_full.mean())
    q_std = float(quiet_full.std(ddof=0))
    print(f"静息样本来源 {args.quiet_src} 尾部 {max_n} 点："
          f"均值 {q_mean:.1f} LSB = {q_mean * 0.1526:.1f} µV，"
          f"std {q_std:.1f} LSB = {q_std * 0.1526:.1f} µV RMS")
    print(f"评分口径：只在**记录段**上算（pad 段无真值，单独统计其中的误检）")
    print(f"尾填充：{'开（头尾各 N s，复刻 WinApp 形态）' if args.pad_tail else '关（只填充头部）'}")

    tmpdir = tempfile.mkdtemp(prefix="pad_eval_")
    all_rows = []
    for ps in pad_secs:
        pad_n = int(round(ps * FS))
        quiet = quiet_full[-pad_n:]
        pads = {"orig": None}
        pads["pad0"] = np.zeros(pad_n, np.float64)
        pads["padconst"] = np.full(pad_n, q_mean, np.float64)
        pads["padreal"] = quiet

        rows = []
        for rec in recs:
            src = os.path.join(REC_DIR, f"{rec}_500.bin")
            ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
            if not (os.path.exists(src) and os.path.exists(ann)):
                print(f"⚠ 跳过 {rec}（缺文件）")
                continue
            base = read_t101_bin(src).astype(np.float64)
            nb = len(base)
            truth_rows, _ = read_truth(ann)
            truth = [(s, sym) for s, sym, b in truth_rows if b]

            for gname in GROUPS:
                pad = pads[gname]
                if pad is None:                   # orig：不加任何填充（尾填充模式下也一样）
                    seq, head = base, 0
                elif args.pad_tail:
                    seq = np.concatenate([pad, base, pad])
                    head = pad_n
                else:
                    seq = np.concatenate([pad, base])
                    head = pad_n
                pk = run_host(seq, tmpdir, f"{rec}_{gname}_{pad_n}")

                pre_fp = [p for p in pk if p < head]
                post_fp = [p for p in pk if p >= head + nb]
                det = [p - head for p in pk if head <= p < head + nb]

                r = score(truth, det)
                # score() 已经返回「没被任何 TP 吃掉的检出样本号」= fp_idx，直接复用
                fp_pos = r["fp_idx"]
                # 拼接处 200 ms 内的孤立误检（离记录起点 ≤100 样本，或离记录末尾 ≤100 样本）
                splice_fp = sum(1 for d in fp_pos if d <= 2 * args.tol
                                or d >= nb - 2 * args.tol)
                first_ok = bool(truth) and any(abs(d - truth[0][0]) <= args.tol for d in det)
                last_ok = bool(truth) and any(abs(d - truth[-1][0]) <= args.tol for d in det)

                rows.append(dict(rec=rec, pad_s=ps, group=gname, n=r["n"], ndet=r["ndet"],
                                 tp=r["tp"], fp=r["fp"], fn=r["fn"], se=r["se"], pp=r["pp"],
                                 der=r["der"], first_ok=first_ok, last_ok=last_ok,
                                 pre_fp=len(pre_fp), post_fp=len(post_fp),
                                 splice_fp=splice_fp, nb=nb))
        all_rows.extend(rows)

        # ------------------------------------------------------------ 该 pad 长度的小结
        hdr = (f"{'组':<9}{'真值':>8}{'检出':>8}{'TP':>8}{'FP':>7}{'FN':>7}"
               f"{'Se%':>7}{'+P%':>7}{'DER%':>7}{'首拍':>7}{'末拍':>7}"
               f"{'头段误检':>9}{'尾段误检':>9}{'拼接FP':>8}")
        print(f"\n===== 前置基线 {ps:g} s"
              f"{'（头尾各一段）' if args.pad_tail else '（只加头）'} =====")
        print(hdr)
        print("-" * 84)
        for g in GROUPS:
            sub = [r for r in rows if r["group"] == g]
            if not sub:
                continue
            n = sum(r["n"] for r in sub)
            tp = sum(r["tp"] for r in sub)
            fp = sum(r["fp"] for r in sub)
            fn = sum(r["fn"] for r in sub)
            nd = sum(r["ndet"] for r in sub)
            ok1 = sum(1 for r in sub if r["first_ok"])
            ok2 = sum(1 for r in sub if r["last_ok"])
            print(f"{g:<9}{n:>8}{nd:>8}{tp:>8}{fp:>7}{fn:>7}"
                  f"{tp / n * 100:>7.2f}{tp / (tp + fp) * 100:>7.2f}"
                  f"{(fp + fn) / n * 100:>7.2f}"
                  f"{ok1:>3}/{len(sub):<3}{ok2:>3}/{len(sub):<3}"
                  f"{sum(r['pre_fp'] for r in sub):>9}"
                  f"{sum(r['post_fp'] for r in sub):>9}"
                  f"{sum(r['splice_fp'] for r in sub):>8}")

    # ---------------------------------------------------------------- 首拍漏检名单
    print(f"\n首拍漏检名单（{len(recs)} 条）：")
    for ps in pad_secs:
        for g in GROUPS:
            sub = [r for r in all_rows if r["group"] == g and r["pad_s"] == ps]
            if not sub:
                continue
            bad = [r["rec"] for r in sub if not r["first_ok"]]
            print(f"  pad={ps:<4g} {g:<9} {len(bad):>2} 条  {', '.join(bad) if bad else '—'}")

    if args.csv and all_rows:
        cols = ["rec", "pad_s", "group", "n", "ndet", "tp", "fp", "fn", "se", "pp", "der",
                "first_ok", "last_ok", "pre_fp", "post_fp", "splice_fp", "nb"]
        with open(args.csv, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(",".join(cols) + "\n")
            for r in all_rows:
                fp.write(",".join(str(r[c]) for c in cols) + "\n")
        print(f"\n逐条结果 → {args.csv}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
