"""W2 硬件闭环对账统一入口：解析 → 互相关定界 → 测漂移率 → 检测器 → 补偿对账。

背景（2026-10-28）：AECG100 播 MIT → ADS1293 EVM 采集 → WinApp 录制 .bin 的链路，
录件时间轴相对数字基线有 **稳定的采样率漂移 0.01218%**（105/108 两条独立录件逐位一致，
源自 ADS1293 采样时钟 ppm 级偏差）。长录件逐峰对账若不补偿，漂移累积会越过匹配窗
（±75 样本）造成大量假 FN——这跟「模拟链路吃不吃峰」无关，必须先把时间轴拉齐再比。

本工具一条命令完成：
  1. 解析 .bin（T101 v2.2 帧）
  2. 互相关定位波形段起点（自动）
  3. 段首/段尾互相关测漂移率 k
  4. 调 rpeak_ref 跑检测器（--all-peaks）
  5. 峰序列做 (1-k) 时间轴补偿后，与真值逐峰对账
  6. 输出「未补偿 vs 补偿」两组 Se/+P/DER，判定 W2 判据（Se 差 ≤0.1 pp）

用法：
  python tools/w2_reconcile.py --bin recordings_winapp/ecg_xxx_MIT108.bin --rec 108

可选：
  --base-off N   基线段起点样本（分段录制时用，如 105 的 seg02 传 300000）
  --base-len N   基线段长度样本（默认 902800 = 全条）
  --drift X      手动指定漂移率（跳过自动测量），如 --drift 0.0001218
  --skip-det     跳过检测器（用已有 {out}_det.txt）
  --outdir DIR   中间产物目录（默认 test_logs）
"""
import argparse
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mitbih_eval as ev  # noqa: E402

REC_DIR = os.path.join(ROOT, "mit_bih", "records")
CSREF_DIR = os.path.join(HERE, "rpeak_ref")
FS = 500


def load_samples(path):
    """解析 T101 v2.2 帧，返回 float 样本数组。"""
    data = open(path, "rb").read()
    samp = []
    i = 0
    while i + 6 <= len(data):
        if data[i] != 0xAA or data[i + 1] != 0x55:
            break
        ln = data[i + 3] | (data[i + 4] << 8)
        total = 6 + ln
        if i + total > len(data):
            break
        off = 3 if ln % 2 == 1 else 2
        n = (ln - off) // 2
        for k in range(n):
            samp.append(struct.unpack_from("<h", data, i + 5 + off + k * 2)[0])
        i += total
    return np.array(samp, dtype=np.float64)


def xcorr_offset(ref, sig, maxlag=200):
    """返回 sig 相对 ref 的偏移（样本数），即 sig 对齐 ref 需要平移的量。"""
    a = ref - ref.mean()
    b = sig - sig.mean()
    best_c, best_lag = -1.0, 0
    for lag in range(-maxlag, maxlag + 1):
        if lag >= 0:
            aa, bb = a[:len(a) - lag], b[lag:]
        else:
            aa, bb = a[-lag:], b[:len(b) + lag]
        if len(aa) < 100:
            continue
        c = np.corrcoef(aa, bb)[0, 1]
        if c > best_c:
            best_c, best_lag = c, lag
    return best_lag, best_c


def find_start(rec, base, base_off, probe_len=5000):
    """互相关定位波形段起点：录件里与基线 base[base_off:] 前 probe_len 最像的位置。"""
    probe = base[base_off:base_off + probe_len]
    probe_n = probe - probe.mean()
    pn = np.sqrt((probe_n ** 2).sum())
    best_c, best_lag = -1.0, -1
    maxlag = min(8000, len(rec) - probe_len)
    for lag in range(0, maxlag):
        seg = rec[lag:lag + probe_len]
        seg_n = seg - seg.mean()
        sn = np.sqrt((seg_n ** 2).sum())
        if sn == 0:
            continue
        c = (probe_n * seg_n).sum() / (pn * sn)
        if c > best_c:
            best_c, best_lag = c, lag
    return best_lag, best_c


def measure_drift(rec, base, start, base_off, seg_len, probe=5000):
    """段首/段尾互相关，返回漂移率 k（录件采样率相对基线的相对偏差）。"""
    lag0, c0 = xcorr_offset(base[base_off:base_off + probe],
                            rec[start:start + probe])
    tail = base_off + seg_len - probe
    lag1, c1 = xcorr_offset(base[tail:tail + probe],
                            rec[start + seg_len - probe:start + seg_len])
    k = (lag1 - lag0) / (seg_len - probe)
    return k, (lag0, c0, lag1, c1)


def run_detector(bin_path, det_path):
    """调 rpeak_ref 跑 C# 检测器，输出 --all-peaks 峰序列。"""
    r = subprocess.run(
        ["dotnet", "run", "--project", CSREF_DIR, "--", bin_path,
         det_path.replace("_det.txt", "_ref.txt"), "--all-peaks", det_path],
        capture_output=True, text=True, cwd=CSREF_DIR)
    if r.returncode != 0:
        raise RuntimeError(f"rpeak_ref 失败: {r.stderr.strip()[:300]}")


def write_clean_bin(rec, start, seg_len, out_path):
    """截取波形段写标准帧格式（复用 mitbih_prep 的 write_bin）。"""
    from mitbih_prep import write_bin
    start_f = int(np.ceil(start / 25)) * 25
    wave_len = int(np.ceil(seg_len / 25)) * 25
    wave = rec[start_f:start_f + wave_len]
    write_bin(out_path, wave.astype(np.int16))
    return start_f, wave_len


def write_truth(rec_id, base_off, seg_len, out_path):
    """从 {rec}_ann500.txt 提取基线段真值，映射到局部样本号。"""
    ann = [l.strip().split(",") for l in
           open(os.path.join(REC_DIR, f"{rec_id}_ann500.txt"), encoding="utf-8")
           if not l.startswith("#")]
    truth = [(int(p[0]) - base_off, p[1]) for p in ann
             if len(p) >= 3 and p[2] == "1"
             and base_off <= int(p[0]) < base_off + seg_len]
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# total500={seg_len}\n# sample500,symbol,is_beat\n")
        for s, sym in truth:
            f.write(f"{s},{sym},1\n")
    return truth


def reconcile(truth, det):
    """det 是补偿后的局部样本号，返回 score。"""
    return ev.score(truth, sorted(det))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="录件 .bin 路径")
    ap.add_argument("--rec", required=True, help="记录号，如 108")
    ap.add_argument("--base-off", type=int, default=0,
                    help="基线段起点样本（分段录制时用）")
    ap.add_argument("--base-len", type=int, default=902800,
                    help="基线段长度样本（默认全条）")
    ap.add_argument("--drift", type=float, default=None,
                    help="手动指定漂移率 k（跳过自动测量）")
    ap.add_argument("--skip-det", action="store_true",
                    help="跳过检测器（用已有 det 文件）")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "test_logs"))
    args = ap.parse_args()

    tag = os.path.basename(args.bin).replace(".bin", "")
    os.makedirs(args.outdir, exist_ok=True)
    clean = os.path.join(args.outdir, f"{tag}_clean_500.bin")
    truth_path = os.path.join(args.outdir, f"{tag}_truth.txt")
    det_path = os.path.join(args.outdir, f"{tag}_det.txt")

    print(f"[1/5] 解析录件 {args.bin}")
    rec = load_samples(args.bin)
    base = load_samples(os.path.join(REC_DIR, f"{args.rec}_500.bin"))
    print(f"      录件 {len(rec)} 样本 = {len(rec)/FS:.2f}s；"
          f"基线 {len(base)} 样本")

    print(f"[2/5] 互相关定界（基线段起点 {args.base_off}）")
    start, c0 = find_start(rec, base, args.base_off)
    print(f"      波形段起点样本 {start}（corr {c0:.4f}）")

    print("[3/5] 测漂移率")
    if args.drift is None:
        k, (l0, cc0, l1, cc1) = measure_drift(
            rec, base, start, args.base_off, args.base_len)
        print(f"      段首偏移 {l0} (corr {cc0:.4f})，"
              f"段尾偏移 {l1} (corr {cc1:.4f})")
        print(f"      漂移率 k = {k*100:.5f}%  "
              f"({k*1e6:.1f} ppm)")
    else:
        k = args.drift
        print(f"      手动指定 k = {k*100:.5f}%")

    print("[4/5] 截波形段 + 跑检测器")
    start_f, wave_len = write_clean_bin(rec, start, args.base_len, clean)
    truth = write_truth(args.rec, args.base_off, args.base_len, truth_path)
    if not args.skip_det:
        run_detector(clean, det_path)
    det = ev.read_peaks(det_path)
    print(f"      波形段 {wave_len} 样本；真值 {len(truth)} 搏动；"
          f"检出 {len(det)} 峰")

    print("[5/5] 对账（未补偿 vs 补偿）")
    r_raw = reconcile(truth, det)

    # 漂移累积量 = k × 段长。只有当它越过匹配窗（±TOL）时，补偿才有意义；
    # 否则逐峰取整误差反而制造边界 FN（短段实测：36 样本漂移 < 75 窗，补偿后 Se 反降 0.33 pp）。
    drift_span = abs(k) * args.base_len
    need_cmp = drift_span > ev.TOL
    if need_cmp:
        det_c = [int(round(d * (1 - k))) for d in det]
        r_cmp = reconcile(truth, det_c)
    else:
        det_c = det
        r_cmp = r_raw

    print(f"\n{'='*66}")
    print(f"{'口径':<12}{'TP':>6}{'FP':>6}{'FN':>6}{'Se%':>9}{'+P%':>9}{'DER%':>8}")
    print(f"{'-'*66}")
    print(f"{'未补偿':<12}{r_raw['tp']:>6}{r_raw['fp']:>6}{r_raw['fn']:>6}"
          f"{r_raw['se']:>9.2f}{r_raw['pp']:>9.2f}{r_raw['der']:>8.2f}")
    if need_cmp:
        print(f"{'补偿后':<12}{r_cmp['tp']:>6}{r_cmp['fp']:>6}{r_cmp['fn']:>6}"
              f"{r_cmp['se']:>9.2f}{r_cmp['pp']:>9.2f}{r_cmp['der']:>8.2f}")
    print(f"{'='*66}")

    print(f"\n漂移率 k = {k*100:.5f}%（录件采样率相对基线偏差）")
    print(f"漂移累积量 = {drift_span:.1f} 样本，匹配窗 ±{ev.TOL} 样本")
    if need_cmp:
        dse = r_cmp['se'] - r_raw['se']
        print(f"漂移已越窗 ⇒ 做 (1-k) 时间轴补偿，Se 提升 {dse:+.2f} pp")
        print(f"W2 判据（模拟链路 Se 差 ≤0.1 pp）：补偿后 Se {r_cmp['se']:.2f}%")
    else:
        print(f"漂移未越窗 ⇒ 不补偿（补偿反而引入取整误差）")
        print(f"W2 判据（模拟链路 Se 差 ≤0.1 pp）：Se {r_raw['se']:.2f}%")

    # 落盘（补偿后）峰序列，供后续核对
    det_out = det_path.replace("_det.txt", "_det_cmp.txt")
    with open(det_out, "w", encoding="utf-8", newline="\n") as f:
        f.write("index,time_s\n")
        for d in det_c:
            f.write(f"{d},{d/FS:.4f}\n")
    print(f"峰序列 -> {det_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
