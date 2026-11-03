# -*- coding: utf-8 -*-
"""链路质量体检 —— 回答「坏行/丢行是不是波特率(持续超载)造成的」。

判据（T87 讨论）：
  ① 若为**持续超载** ⇒ 坏处应**成簇、连续**，且丢行成片（缓冲持续溢出）。
  ② 若为**偶发事件**（上位机写盘/GC 停顿、单帧延迟）⇒ 坏处应**稀疏单点**，
     且相邻坏点之间隔很远（数百~数千行）。
  ③ **形态**也要分：粘连(两行合并, token>7) / 分裂(token=8 后跟空行) / 截断(token<7)。

识别数据文件：首 5 行中 ≥3 行为 7 token 且前 3 列可 int 解析（排除脚本输出 txt）。
用法：python tools/_link_quality_survey.py [--dir tools] [--top 15]
"""
from __future__ import print_function
import io, os, re, glob, argparse, collections

# 列义（ads1293.c:679 `%07u %07d %06d %05d %.02f %05d %x`）：
#   0 idxraw(u) 1 ecgraw 2 idxfilter 3 dsrateraw 4 filterraw(**float**) 5 dtcycles 6 gdtover
# ⇒ 整数列只有 0/1/2/3/5；列④ 是 %.02f，不能 int()。列④ 只查"形状"（两位小数）。
LIM = [(0, 0, 4294967295), (1, 0, 16777215), (2, -2147483648, 2147483647),
       (3, -32768, 32767), (5, 0, 65535)]
F4 = re.compile(r'^-?\d+\.\d{2}$')


def is_data(path):
    try:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            ok = 0
            for _ in range(6):
                ln = f.readline()
                if not ln:
                    break
                q = ln.split()
                if len(q) == 7:
                    try:
                        int(q[0]); int(q[1]); int(q[2])
                        ok += 1
                    except ValueError:
                        pass
            return ok >= 3
    except Exception:
        return False


def survey(path):
    """返回 (总行, 坏行列表[(行号, token数, 形态, 原文)], 丢行列表[(行号, d1, d3)])"""
    bad = []
    rows = []            # (lineno, c0, c2, c1, c5)
    with io.open(path, encoding='utf-8', errors='replace') as f:
        prev_blank = False
        for i, ln in enumerate(f, 1):
            q = ln.split()
            if not q:
                prev_blank = True
                continue
            if len(q) != 7:
                if len(q) > 7:
                    kind = '粘连(%d)' % len(q)
                elif prev_blank:
                    kind = '截断(%d)@分裂' % len(q)
                else:
                    kind = '截断(%d)' % len(q)
                bad.append((i, len(q), kind, ln.rstrip('\r\n')[:110]))
                prev_blank = False
                continue
            prev_blank = False
            try:
                c0 = int(q[0]); c1 = int(q[1]); c2 = int(q[2])
                c5 = int(q[5])
            except ValueError:
                bad.append((i, len(q), '非数', ln.rstrip('\r\n')[:110]))
                continue
            if not F4.match(q[4]):
                bad.append((i, 7, '列④形状=%s' % q[4], ln.rstrip('\r\n')[:110]))
                continue
            for ci, lo, hi in LIM:
                try:
                    iv = int(q[ci])
                except ValueError:
                    iv = None
                if iv is None or not (lo <= iv <= hi):
                    bad.append((i, 7, '越界col%d=%s' % (ci, q[ci]), ln.rstrip('\r\n')[:110]))
                    break
            rows.append((i, c0, c2, c1, c5))
    # 丢行判据必须**按文件自适应**（T87）：列① 在 09-18 及更早是**字面常量 0**
    #   ⇒ 该列 Δ 恒 0，与"丢行"无关（老格式无信息）；raw853 域 Δ① 恰为 1、列③ 恒 0。
    #   规则：取列① Δ 的众数 m（忽略 Δ≤0）；m==0 ⇒ 换列③；两列皆常数 ⇒ 声明"本文件无丢行判据"。
    n = len(rows)
    d1 = [rows[k][1] - rows[k - 1][1] for k in range(1, n)]
    pos = [d for d in d1 if d > 0]
    # 合法增量 = 出现频次 ≥5% 的 Δ（500 域＝{1,2} 混合；raw853 域＝{1}）；
    # 单点噪声不会进这个集合 ⇒ Δ 落在集合外或 ≤0 才算丢行。
    legal = set(v for v, c in collections.Counter(pos).items() if n and c >= 0.05 * n)
    src = '列①'
    if not legal:                               # 列① 无信息（老格式恒 0）⇒ 换列③
        d3 = [rows[k][2] - rows[k - 1][2] for k in range(1, n)]
        pos3 = [d for d in d3 if d > 0]
        legal = set(v for v, c in collections.Counter(pos3).items() if n and c >= 0.05 * n)
        src = '列③'
    drop = []
    if legal:
        MODE = max(legal)                       # 用最大合法步长当基准
        for k in range(1, n):
            dv = (rows[k][1] - rows[k - 1][1]) if src == '列①' else (rows[k][2] - rows[k - 1][2])
            if dv not in legal:
                drop.append((rows[k][0], dv, rows[k][2] - rows[k - 1][2], src, MODE))
    return n, bad, drop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='tools')
    ap.add_argument('--top', type=int, default=15)
    a = ap.parse_args()
    pats = [os.path.join(a.dir, '*.txt'), '*.txt']
    files = sorted(set(sum([glob.glob(p) for p in pats], [])))
    files = [f for f in files if os.path.getsize(f) > 20000
             and not os.path.basename(f).startswith('_') and is_data(f)]
    print('数据文件 %d 份\n' % len(files))
    tot = 0; allbad = []; alldrop = []
    per = []
    for p in files:
        n, bad, drop = survey(p)
        tot += n; allbad += [(os.path.basename(p),) + b for b in bad]
        alldrop += [(os.path.basename(p),) + d for d in drop]
        per.append((os.path.basename(p), n, len(bad), len(drop)))
    print('总有效行 %d ｜ 坏行 %d (%.5f%%) ｜ 丢行(列① Δ≤0 或 >5) %d (%.5f%%)'
          % (tot, len(allbad), 100.0 * len(allbad) / max(tot, 1),
             len(alldrop), 100.0 * len(alldrop) / max(tot, 1)))
    print('\n形态分布：')
    kinds = collections.Counter(re.sub(r'\(.*', '', b[3]) for b in allbad)
    for k, v in kinds.most_common():
        print('  %-14s %d' % (k, v))

    # 坏行间隔（同文件内相邻坏行的行距）—— 判「稀疏单点」 vs 「成簇」
    print('\n坏行稀疏度（同文件内相邻坏行的行距）：')
    gaps = []
    byfile = collections.defaultdict(list)
    for b in allbad:
        byfile[b[0]].append(b[1])
    for f, ls in byfile.items():
        ls.sort()
        for k in range(1, len(ls)):
            gaps.append(ls[k] - ls[k - 1])
    if gaps:
        gaps.sort()
        print('  共 %d 个间隔；最小 %d 行 ｜ 中位 %d 行 ｜ 最大 %d 行'
              % (len(gaps), gaps[0], gaps[len(gaps) // 2], gaps[-1]))
        print('  间隔=1（连续成簇）的个数：%d  (%.1f%%)'
              % (sum(1 for g in gaps if g == 1), 100.0 * sum(1 for g in gaps if g == 1) / len(gaps)))
    else:
        print('  无（每文件最多 1 个坏点）')

    print('\n按文件明细（前 %d，按坏行数降序）：' % a.top)
    print('  %-54s %7s %5s %5s %9s' % ('文件', '行数', '坏行', '丢行', '坏率%'))
    for f, n, nb, nd in sorted(per, key=lambda r: -r[2])[:a.top]:
        print('  %-54s %7d %5d %5d %8.4f%%' % (f[:54], n, nb, nd, 100.0 * nb / max(n, 1)))

    print('\n全部坏行（含形态）：')
    for f, i, tk, kind, txt in allbad:
        print('  %-50s 行%6d %-12s %s' % (f[:50], i, kind, txt[:88]))

    if alldrop:
        print('\n丢行位置（Δ ≠ 本文件众数；判据列已按文件自适应）：')
        cnt = collections.Counter((d[2], d[4], d[5]) for d in alldrop)
        for (dv, src, m), c in sorted(cnt.items(), key=lambda kv: -kv[1])[:14]:
            print('  %s Δ=%d（众数 %d）: %d 处' % (src, dv, m, c))
        print('  ---- 明细（前 30）----')
        for f, i, dv, d3, src, m in alldrop[:30]:
            print('  %-48s 行%6d %s Δ=%d（众数 %d）' % (f[:48], i, src, dv, m))
        print('  共 %d 处' % len(alldrop))


if __name__ == '__main__':
    main()
