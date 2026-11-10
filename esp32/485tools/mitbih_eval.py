"""MIT-BIH QRS 检测器评估（AAMI EC57 / PhysioNet bxb 口径）

口径（**写死，别改**，否则不能与文献比）：
  · 匹配窗 **±150 ms**（@500 Hz = ±75 样本）—— QRS 检测器评估的行业惯例
  · 真值只计 AAMI 搏动符号集 `NLRBAaJSVrFejnE/fQ`，排除 `+ ~ | s T ( ) " = p ^ t u ?` 等
    节律/注释/波形标注
  · 指标：Se = TP/(TP+FN)、+P = TP/(TP+FP)、DER = (FP+FN)/N

  · **排除首尾各 5 s**（2026-10-26 猛哥拍板）
    依据：算法上电要若干拍才把自适应阈值 `signalPeak/noisePeak` 建立起来，末尾 EOF 处
    积分窗填不满 —— 这两段必然出现边界性漏检/误检，与"检测能力"无关。业界通行做法就是
    掐掉首尾若干秒。评估**同时给出"全段"与"排除首尾后"两组数字**，不藏。

参考基准（同库、近似口径，非严格可比）：
  · Se 99.3 % —— Pan J, Tompkins WJ. IEEE TBME 1985, 32(3):230-236（DOI 10.1109/TBME.1985.325532）**原文数字**
  · +P 99.5 % —— 后世复现口径取整（复现值 Se 99.75 / +P 99.53，116,137 拍）；原文无 +P
  口径差异：拍数基数（复现 116,137 vs 本方案 AAMI 符号集+首尾 5s 裁剪后 108,887）、
  匹配容差、导联选择均未统一 ⇒ 只作量级对照，不作精确差距。

用法：
  python mitbih_eval.py <ann500.txt> <peaks.txt>
  peaks.txt：每行 `index,time_s` 或纯索引（tools/rpeak_ref --all-peaks / rpeak_host 输出）
"""
import sys

TOL = 75          # ±75 样本 @500 Hz = ±150 ms
SKIP_EDGE_S = 5   # 排除首尾各 5 s
FS = 500


def read_truth(path):
    """读 mitbih_prep 产出的标注：`# total500=<n>` + `# sample500,symbol,is_beat`"""
    rows, total = [], None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "total500=" in line:
                total = int(line.split("total500=")[1].split()[0])
            continue
        p = line.split(",")
        rows.append((int(p[0]), p[1], len(p) > 2 and p[2].strip() == "1"))
    return rows, total


def read_peaks(path):
    """读峰序列。**四种格式都能直接吃**（2026-11-10 扩）：

    1. `index,time_s`（rpeak_host / WinApp 导出，逐行一个峰）
    2. 纯索引逐行（`12345`）
    3. **`P 12345 12380 …`**（板端 batch / `rpeak rec` / `mit-run` 的结果文件，16 峰一行）
    4. **`W 12345 v-3 … v+3`**（板端 `rpeak wide on` 的宽格式，取第 2 列）

    🛑 为什么要扩：板端结果文件里除 `P` 行还夹着 `BATCH-BEGIN` / `FILE …` / `# …`
       这些行，旧实现会把它们丢给 `int()` → 直接 ValueError。
       于是"板上跑完 → 直接喂本脚本"这条路走不通，只能先手工转一道格式。
    """
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0]
        if head in ("P", "W"):                      # 板端 16 峰/行 或宽格式
            toks = line.split()[1:]
            if head == "W":
                toks = toks[:1]                     # 宽格式只取峰号，后面是 ±3 原始值
            out.extend(int(t) for t in toks)
            continue
        if head.startswith("index") or head in ("BATCH-BEGIN", "BATCH-END", "FILE", "PROGRESS"):
            continue
        # 🛑 最后一层容错：串口抓下来的文件**首行是命令回显**（`result-dump xxx.txt`）、
        #    末行是 `cardiolite>` 提示符，直接 int() 会炸。凡是解析不出数字的行一律跳过，
        #    不做白名单 —— 白名单永远漏（下次换个命令回显就又不认识了）。
        try:
            out.append(int(line.split(",")[0]))
        except ValueError:
            continue
    return sorted(out)


def score(truth, det):
    """truth: [(样本, 符号)]；det: 排好序的样本号"""
    used = [False] * len(det)
    tp = 0
    fn_codes = {}
    fn_list = []
    for s, sym in truth:
        lo, hi = 0, len(det)
        while lo < hi:                      # 二分定位窗口起点
            mid = (lo + hi) // 2
            if det[mid] < s - TOL:
                lo = mid + 1
            else:
                hi = mid
        best, bestd = -1, TOL + 1
        j = lo
        while j < len(det) and det[j] <= s + TOL:
            if not used[j]:
                d = abs(det[j] - s)
                if d < bestd:
                    bestd, best = d, j
            j += 1
        if best >= 0:
            used[best] = True
            tp += 1
        else:
            fn_codes[sym] = fn_codes.get(sym, 0) + 1
            fn_list.append((s, sym))

    fp_idx = [det[i] for i in range(len(det)) if not used[i]]
    n = len(truth)
    fn = n - tp
    fp = len(fp_idx)
    se = tp / n * 100 if n else 0.0
    pp = tp / (tp + fp) * 100 if (tp + fp) else 0.0
    der = (fp + fn) / n * 100 if n else 0.0
    return dict(n=n, tp=tp, fp=fp, fn=fn, se=se, pp=pp, der=der, ndet=len(det),
                fn_codes=fn_codes, fn_list=fn_list, fp_idx=fp_idx)


def show(label, r):
    print(f"[{label}] 真值 {r['n']}  检出 {r['ndet']}  "
          f"TP {r['tp']}  FP {r['fp']}  FN {r['fn']}")
    print(f"        Se {r['se']:.2f} %   +P {r['pp']:.2f} %   DER {r['der']:.2f} %"
          f"   (Pan-Tompkins 1985: Se 99.3 原文 / +P 99.5 复现口径)")
    if r["fn_codes"]:
        print("        漏检按标注类型:", ", ".join(f"{k}×{v}" for k, v in sorted(r["fn_codes"].items())))
        print("        前 8 个漏检:", ", ".join(f"{s}({y})" for s, y in r["fn_list"][:8]))
    if r["fp_idx"]:
        print("        前 8 个误检样本号:", r["fp_idx"][:8])


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    rows, total = read_truth(sys.argv[1])
    det = read_peaks(sys.argv[2])
    truth = [(s, sym) for s, sym, b in rows if b]
    excluded = len(rows) - len(truth)

    print(f"标注 {len(rows)} 条（排除 {excluded} 条非搏动）  匹配窗 ±{TOL} 样本"
          f" = ±{TOL * 1000 // FS} ms")
    print()

    show("全段", score(truth, det))

    if total:
        lo, hi = SKIP_EDGE_S * FS, total - SKIP_EDGE_S * FS
        t2 = [(s, sym) for s, sym in truth if lo <= s <= hi]
        d2 = [v for v in det if lo <= v <= hi]
        print(f"\n---- 排除首尾各 {SKIP_EDGE_S} s（窗口 [{lo}, {hi}] 样本 / 总长 "
              f"{total} = {total / FS:.1f} s）----")
        show(f"排除首尾 {SKIP_EDGE_S}s", score(t2, d2))
    else:
        print("\n（标注缺 total500，未做首尾排除）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
