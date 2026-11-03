"""解析固件 S7 批处理的串口输出 → 出 Se/+P 与心率阶梯对照表

固件输出格式（`main/ecg/ecg_batch.c`）：
```
BATCH-BEGIN total=<n>
P <idx> <idx> ...            ← 某文件的所有峰索引（每行最多 16 个）
FILE <name> frames=.. samples=.. bad=.. peaks=.. bpm=<x.y>
PENV <name>
...
BATCH-END ok=<n> fail=<m> elapsed_ms=<ms>
```

派生的两类结果：
  · `mit_<rec>_500.bin`  → 与 `<rec>_ann500.txt` 对 AAMI 口径（±150 ms、排除首尾 5 s）
  · `sr_<bpm>bpm.bin`    → 检出 BPM 与设定值比、拍数对账、RR 稳定性

用法：
  python ecg_batch_eval.py <串口日志.txt>
"""
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import mitbih_eval as ev          # noqa: E402

REC_DIR = os.path.join(ROOT, "mit_bih", "records")
FS = 500


# 🛑 不能只认行首 `P `：CLI 回放（`mit-run`）时 esp_console 的 REPL prompt
# （`cardiolite> `）会粘在第一行 P 前面 ⇒ 按行首判断会整行丢掉 16 个峰。
# 改为行内搜「空白 + P + 一个以上整数」，prompt 前缀不影响。
PEAK_RE = re.compile(r"(?:^|\s)P((?:\s+\d+)+)")


def parse(log_path):
    """返回 [(name, peaks, info_dict)]"""
    out = []
    pend = []
    for raw in open(log_path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        m = PEAK_RE.search(line)
        if m is not None:
            for tok in m.group(1).split():
                try:
                    pend.append(int(tok))
                except ValueError:
                    pass
        elif line.startswith("FILE "):
            parts = line.split()
            name = parts[1]
            info = {}
            for p in parts[2:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    info[k] = v
            out.append((name, pend, info))
            pend = []
        elif line.startswith("BATCH-BEGIN") or line.startswith("BATCH-END"):
            print(f"# {line}")
    return out


def eval_mit(name, peaks):
    # mit_100_500.bin → 100
    try:
        rec = name.split("mit_")[1].split("_")[0]
    except IndexError:
        return None
    ann = os.path.join(REC_DIR, f"{rec}_ann500.txt")
    if not os.path.exists(ann):
        return f"  ⚠ {name}: 找不到标注 {rec}_ann500.txt"
    rows, total = ev.read_truth(ann)
    truth = [(s, sym) for s, sym, b in rows if b]
    if not total:
        total = max([s for s, _ in truth] + peaks) + 1

    lo, hi = ev.SKIP_EDGE_S * FS, total - ev.SKIP_EDGE_S * FS
    r = ev.score([(s, y) for s, y in truth if lo <= s <= hi],
                 [v for v in peaks if lo <= v <= hi])
    return (f"  MIT {rec}:  真值 {r['n']:>5}  检出 {r['ndet']:>5}  "
            f"TP {r['tp']:>5} FP {r['fp']:>3} FN {r['fn']:>3}  "
            f"Se {r['se']:6.2f}%  +P {r['pp']:6.2f}%  DER {r['der']:5.2f}%"
            + (f"   漏检 {dict(list(r['fn_codes'].items())[:4])}" if r['fn_codes'] else ""))


def eval_ladder(name, peaks, info):
    try:
        set_bpm = int(name.split("sr_")[1].split("bpm")[0])
    except (IndexError, ValueError):
        return f"  ⚠ {name}: 文件名里没有 sr_<bpm>bpm"
    nsamp = int(info.get("samples", "0"))
    dur = nsamp / FS if nsamp else 0
    exp_beats = round(set_bpm * dur / 60) if dur else 0

    if len(peaks) >= 3:
        rr = [b - a for a, b in zip(peaks, peaks[1:])]
        med = statistics.median(rr)
        det_bpm = 60 * FS / med
        rr_min, rr_max = min(rr), max(rr)
    else:
        det_bpm, med, rr_min, rr_max = 0, 0, 0, 0

    # 定位偏差：峰索引 vs 原始波形局部极大（真值，不用检测器自己的输出）
    dev = "n/a"
    return (f"  阶梯 {set_bpm:>3}bpm: 时长 {dur:6.1f}s  检出 {len(peaks):>4} 拍"
            f"（理论 {exp_beats:>4}）  检出 BPM {det_bpm:6.2f}  "
            f"偏差 {det_bpm - set_bpm:+6.2f}  RR {med:.0f}±{rr_max-rr_min} 样本")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    items = parse(sys.argv[1])
    if not items:
        print("没解析到任何 FILE 段 —— 检查日志里有没有 'FILE ' / 'P ' 行")
        return 1

    print(f"# 共 {len(items)} 个文件\n")
    ladder, mit, other = [], [], []
    for name, peaks, info in items:
        if name.startswith("mit_"):
            mit.append((name, peaks, info))
        elif "sr_" in name and "bpm" in name:
            ladder.append((name, peaks, info))
        else:
            other.append((name, peaks, info))

    if ladder:
        print("=== 心率阶梯 ===")
        for name, peaks, info in ladder:
            print(eval_ladder(name, peaks, info))
        print()
    if mit:
        print("=== MIT-BIH（AAMI 口径，排除首尾 5 s）===")
        for name, peaks, info in mit:
            r = eval_mit(name, peaks)
            if r:
                print(r)
        print()
    if other:
        print("=== 其它 ===")
        for name, peaks, info in other:
            print(f"  {name}: {len(peaks)} 拍, {info.get('samples','?')} 样本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
