# -*- coding: utf-8 -*-
"""
_t163_ascii2.py —— **ascii-2（T163 精简档）抓数文件的统一判读/体检入口**
================================================================================

为什么单独一个入口：
  · 现有 26 个 txt 解析脚本都带「**7 列**」硬门（`len(p) != 7` 或按列号取值）
    ⇒ 喂 ascii-2 的 2~5 列文件，**不是报错就是静默取错列**（T162 §六 已预判）。
  · ascii-2 的**列组合随开关变**（列数 2/3/4/5），正文里**没有自描述字段**
    ⇒ 判读前必须先**把映射钉死**，且**不许猜**。

本脚本做三件事：
  ① **判档**：按「token 数 + 各列模态宽度」反推 raw/idx/cpu 三个开关（8 种组合全覆盖）；
     推不出来（例如 ③ 溢出到 7 位）就 🛑 **拒绝判读**并列出观测到的宽度模式，要求 `--cols`。
  ② **体检**：T163 报告 §五 里可离线算的 6 项 —— 行长恒定 / 字段宽度恒定 / 定点反解量程 /
     **丢包 Δ 分布**（列① 853 域、列③ 500 域）/ 行率（列③）与序号率（列①）/ 时长 /
     列①⨯列③ 互印 / 列⑥ 值域；再报文件的**行尾**。
  ③ **对档**：给一份同段信号的 `ascii-1` 抓取（`--ref`），**按列② 序列自动对齐**后
     逐点比 `⑤/10.0` vs `%.02f` ⇒ 报 `max|Δ|`（判据 ≤ 0.05 + 1e-6）。

用法
----
  # 自检（合成数据，含阴性对照；先跑这个）
  python tools/_t163_ascii2.py --selftest

  # 判读一份 ascii-2 抓数
  python tools/_t163_ascii2.py --name "tools/xxx.txt"

  # 显式声明列组合（推定不出时）
  python tools/_t163_ascii2.py --name "tools/xxx.txt" --cols raw,idx

  # 与 ascii-1 同段对档（步骤 3）
  python tools/_t163_ascii2.py --name "tools/a2.txt" --ref "tools/a1.txt"

  # 落 sidecar（T161 方案 B 的 `.meta.json` 雏形）
  python tools/_t163_ascii2.py --name "tools/a2.txt" --emit-meta "tools/a2.meta.json"

口径（与固件同源，改一处必须两边一起改）
----------------------------------------
  · 列序恒 `[①] ② [③] ⑤ [⑥]`；**关掉的列整列消失**，不留占位。
  · 宽度：① `%06u`=6 ／② `%08d`=8 ／③ `%06d`=6 ／⑤ `%08d`=8 ／⑥ `%05u`=5
    （② 在已知粘包伪迹行会变 9 位 ⇒ 判据用**模态宽度**，不用逐行相等。）
  · 行长 = 1(`\\n`) + 首列宽 + Σ(1 + 后续列宽)  ⇒  默认 18、全开 38。
  · ⑤ = 链后值 × 10（**0.1 code**/LSB —— ⚠ 不是 µV，见 §口径注释）；钳制 ±`ASCII2_FIX_OVF`(9,999,999)。
  · 丢包判据 **按列的域定合法 Δ**（★T173 订正；原写"相邻序号差应恒 1"是**错的**）：
    列③（`ascii-idx`，500 域）＝ 1:1 列 ⇒ Δ 应恒 1；
    列①（`ascii-raw`，853 域）在 `raw853 off`（**行节拍 500 Hz**）下**天然抽稀**
    ⇒ 每行理论 Δ = `853.2423 / 500 = 1.7065` ⇒ **合法域 {1,2}**；**Δ=2 是设计行为、不是丢样**
    （旧判据把它当跳号：一份**干净**文件被误报"跳号 2877 处"，与同一份输出的"缺样 0.00%"自相矛盾）。
    **Δ ≥ 3 才算跳号**。**① 是 `%06u` ⇒ 会出现回绕**（Δ 为约 −10⁶ 的负值），回绕单列、不算丢包。
  · 行率口径：**只有 1:1 列（列③）的号率＝行率**；列① 报的是**序号率 853.24 Hz**
    （＝853.2423，每行跨 1.7065 个计数）。🛑 不可用"域频率 ÷ 中位 Δ"当行率 ——
    对抽稀列会算出 853.2423/2 = **426.6 Hz 的假值**（旧行为）。
"""
from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import os
import sys

try:                                              # 重定向到文件时 stdout 会退 cp936
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/ 的上一层

W = {1: 6, 2: 8, 3: 6, 5: 8, 6: 5}                # 典型/设计字段宽
FIX_OVF = 9_999_999                               # 固件 ASCII2_FIX_OVF
# ★T172：⑤ 列是 **code 域**（`Filter/ecg_out.h` 的 ECG_UV_PER_CODE）。本脚本曾把
#   ⑤/10 直接标成 "µV" —— 那是**错标签**（差 8.9×，同 `firmware-change-verify §4.123`）。
#   ⇒ 一律写作 `code`，要 µV 就显式乘 UV_PER_CODE 并把换算率打出来。
UV_PER_CODE = 0.1122                              # µV / code（`Filter/ecg_out.h`）
FS853, FS500 = 853.2423, 500.0                    # 两个采样域（判丢包时的时长基准）
CYCLES_PER_PERIOD = 93_760                        # 1.172 ms × 80 MHz（T84 口径，仅作量级参考）
CPU_RANGE = (1500, 3000)                          # 列⑥ 历史值域（T85 中位 1970）

# 宽度模式 -> (raw, idx, cpu)。**8 种组合全覆盖；表外一律拒绝。**
PAT2COLS = {
    (8, 8):                   (0, 0, 0),
    (6, 8, 8):                (1, 0, 0),
    (8, 6, 8):                (0, 1, 0),
    (8, 8, 5):                (0, 0, 1),
    (6, 8, 6, 8):             (1, 1, 0),
    (6, 8, 8, 5):             (1, 0, 1),
    (8, 6, 8, 5):             (0, 1, 1),
    (6, 8, 6, 8, 5):          (1, 1, 1),
}


def rowspec(raw, idx, cpu):
    """按固件 sprintf 顺序返回 [(列号, 宽, 是否带前导分隔)]。"""
    seq = []
    if raw:
        seq.append((1, W[1], False))
    seq.append((2, W[2], bool(seq)))
    if idx:
        seq.append((3, W[3], True))
    seq.append((5, W[5], True))
    if cpu:
        seq.append((6, W[6], True))
    return seq


def expect_bytes(raw, idx, cpu):
    """设计行长（字节，含结尾 `\\n`）。"""
    return 1 + sum(w + (1 if sep else 0) for _, w, sep in rowspec(raw, idx, cpu))


def read_lines(path):
    """按**二进制**读，返回 [(裸字节, 行尾类型)]；行尾类型 'lf'/'crlf'/'cr'/'none'。"""
    with open(path, 'rb') as f:
        blob = f.read()
    out = []
    for raw in blob.split(b'\n'):
        if raw == b'' and out and blob.endswith(b'\n'):
            continue                                   # 末尾空段
        if raw.endswith(b'\r'):
            out.append((raw[:-1], 'crlf'))
        else:
            out.append((raw, 'lf'))
    return out


def tokenize(bline):
    return [t.decode('ascii', 'replace') for t in bline.split() if t != b'']


def isint(tok):
    """★T172：只认**纯十进制整数**（可带一个前导符号）。

    ⚠ 为什么不能只数 token 数：尾部垃圾块（NUL / ≥0x80）**粘在最后一列**上
    （NUL 不是空白 ⇒ `split()` 不切它）⇒ 行仍是 4 个 token，但末列是
    `-0006998\\x00\\x00…` ⇒ 后续 `int()` 当场 ValueError。
    ⇒ 判"这行能不能用"必须同时看**个数**与**可解析性**。
    """
    if not tok:
        return False
    s = tok[1:] if tok[0] in '+-' else tok
    return s.isascii() and s.isdigit()


def flag(tok):
    return {True: '✅', False: '🛑'}[bool(tok)]


# ----------------------------------------------------------------------------- 判档

def infer(bline_list, nhead=300):
    """返回 (raw, idx, cpu, 证据串)。推不出返回 (None,None,None,原因)。"""
    pats = collections.Counter()
    for b, _ in bline_list[:nhead]:
        toks = tokenize(b)
        if not toks:
            continue
        widths = tuple(len(t) for t in toks)
        pats[widths] += 1
    if not pats:
        return None, None, None, '文件里没有可解析的数据行'
    modal, cnt = pats.most_common(1)[0]
    ev = '模态宽度模式 %s（%d/%d 行）' % (str(modal), cnt, sum(pats.values()))
    if modal in PAT2COLS:
        r, i, c = PAT2COLS[modal]
        return r, i, c, ev
    if len(pats) > 1:
        ev += '；另有 %d 种其它模式（例 %s）' % (len(pats) - 1, str(pats.most_common(2)[-1][0]))
    return None, None, None, ev


# ----------------------------------------------------------------------------- 体检

def check(name, raw, idx, cpu, max_rows=None, ref=None, dtc_range=CPU_RANGE):
    # ★T172：**先定列型，再收行**。token 数 ≠ len(seq) 的行是**退化行**（粘包/垃圾块撕裂），
    #   必须**单独收集**而不是当普通行喂进下面按列号取值的代码 —— 否则 IndexError 当场崩，
    #   而且崩在"体检"里比崩在"判读"里更坏（体检本来就是用来发现异常的）。
    seq = rowspec(raw, idx, cpu)
    ncol = len(seq)
    exp_b = expect_bytes(raw, idx, cpu)

    bl = read_lines(name)
    rows = []
    degen = []
    salv = []              # ★T172b：**截断救回**的行（尾部垃圾块把"上一行"连坐了，但正文前 exp_chars 字符是好的）
    all_lens = []          # ★T172：**全部**非空行（含退化行）的行长，供"行长恒定"判据用
    binline = []           # ★T172：含**不可打印字节**（<0x20 或 ≥0x80）的行号
    exp_chars = exp_b - 1  # 设计行长（不含 \n）＝ 一行的"正文字符数"
    for lineno, (b, eol) in enumerate(bl, 1):
        toks = tokenize(b)
        if not toks:
            continue
        all_lens.append(len(b))
        if any(c < 0x20 or c >= 0x80 for c in b):
            binline.append(lineno)
        if len(toks) != ncol or not all(isint(t) for t in toks):
            # ★T172b **截断救回**：垃圾块是**追加**在上一行末尾的（NUL 不是空白 ⇒ split 不切），
            #   所以那行"前 exp_chars 个字符"往往仍是**一条完好数据行**。先截断再判一次：
            #   ⇒ 救得回 ⇒ 这行**数据没丢**，只是被垃圾连坐（丢的是**被顶掉的那一行**）。
            #   🛑 这一步只影响"丢样账"的口径，不改任何取值逻辑（救回的行照样过 4 列整数校验）。
            head = b[:exp_chars] if len(b) > exp_chars else b
            th = tokenize(head)
            if len(th) == ncol and all(isint(t) for t in th):
                salv.append((lineno, head, th, eol))
            else:
                degen.append((lineno, len(toks), len(b)))
            continue
        rows.append((lineno, b, toks, eol))
        if max_rows and len(rows) >= max_rows:
            break

    n = len(rows)
    # 救回口径：把"被连坐但正文完好"的行放回原位（按行号排序 ⇒ 与 Δ 检查的"顺序"前提一致）
    rows_all = sorted(rows + salv, key=lambda r: r[0]) if salv else rows

    rep = {'file': os.path.basename(name), 'rows': n, 'rows_salvaged': len(rows_all),
           'cols': {'raw': raw, 'idx': idx, 'cpu': cpu},
           'col_order': [c for c, _, _ in seq], 'expect_bytes': exp_b,
           'degenerate': {'n': len(degen), 'samples': degen[:10]},
           'salvaged': {'n': len(salv), 'samples': [s[0] for s in salv[:10]]},
           'binlines': {'n': len(binline), 'samples': binline[:10]}}

    print('=' * 78)
    print('文件 %s' % name)
    order = ''.join(('%d' % c) for c, _, _ in seq)
    print('  有效行 %d ｜ 列序（仅列号）%s ｜ 设计行长 %d B（含 \\n）' % (n, order, exp_b))
    if salv:
        print('  ★ **截断救回 %d 行**（行号 %s%s）：这些行**数据完好**，只是行内混进了 NUL '
              '**而被整行连坐** ⇒ 已按"前 %d 字符"取回，并计入下面「救回口径」的统计。'
              % (len(salv), [s[0] for s in salv[:5]], ' …' if len(salv) > 5 else '', exp_chars))
    if degen:
        print('  ⚠ **退化行 %d 行**（不可救：token 数 ≠ %d 或含非整数 token）：%s%s' %
              (len(degen), ncol, degen[:5], ' …' if len(degen) > 5 else ''))
        print('     ⇒ 已从统计中剔除（否则按列号取值会取错）。'
              '若数量可观 ⇒ 是**链路问题**，见 T172（尾部垃圾块）。')
    if binline:
        print('  ⚠ **不可打印字节 %d 行**（NUL / ≥0x80）：%s%s'
              % (len(binline), binline[:5], ' …' if len(binline) > 5 else ''))
        print('     ⇒ 正文里混进了**二进制垃圾** ⇒ 先当链路缺陷查，别当解析问题（见 T172）。')
    if n == 0:
        print('  🛑 0 行有效数据 ⇒ **拒绝判读**（0 样本不得当通过）')
        rep['verdict'] = 'REFUSE'
        return rep

    # ---- 1 行尾
    eols = collections.Counter(e for _, _, _, e in rows)
    crlf = eols.get('crlf', 0)
    eol_txt = ('全 LF' if crlf == 0 else ('全 CRLF' if crlf == n else '**混合** %s' % dict(eols)))
    print('  · 行尾       : %s%s' % (eol_txt, '' if crlf == 0 else
          '  ⚠ 行长按字节算，CRLF 会让每行多 1 B'))
    rep['eol'] = eol_txt

    # ---- 2 字段宽度恒定（逐列，含模态与越界行）
    print('  · 字段宽度   :')
    wdist = {}
    for pos, (cid, w, _sep) in enumerate(seq):
        dist = collections.Counter(len(r[2][pos]) for r in rows)
        wdist[cid] = dict(dist)
        modal = dist.most_common(1)[0][0]
        bad = [r[0] for r in rows if len(r[2][pos]) != w]
        mark = '✅' if not bad else ('⚠' if len(bad) <= max(3, n // 1000) else '🛑')
        print('      列%d 设计 %d 位 ｜ 模态 %d ｜ 非 %d 位 %d 行%s'
              % (cid, w, modal, w, len(bad), ('（例 %s）' % bad[:5]) if bad else ''))
        rep['width_col%d' % cid] = {'design': w, 'modal': modal, 'off': len(bad),
                                    'samples': bad[:10]}
    # ---- 3 行长恒定（⚠ read_lines 已剥掉行尾 ⇒ 比的是「不含 \n」的字符数 = 设计值 − 1）
    #   ★T172：口径 = **全部非空行**（含退化行）—— 只统计"干净行"会让这道判据**恒 ✅**，
    #   把 354 行带垃圾的正文判成"行长恒定"（＝判据失去意义）。
    exp_chars = exp_b - 1
    ldist = collections.Counter(all_lens)
    modal_len = ldist.most_common(1)[0][0]
    off = len(all_lens) - ldist.get(exp_chars, 0)
    print('  · 行长       : 模态 %d 字符（＝%d B 含 \\n）｜ 设计 %d 字符（%d B）｜ %s（偏离 %d / %d 行%s）'
          % (modal_len, modal_len + 1, exp_chars, exp_b,
             '✅' if off == 0 else ('⚠' if off <= max(3, len(all_lens) // 100) else '🛑'),
             off, len(all_lens), '' if off == 0 else '，分布 %s' % dict(ldist)))
    rep['rowlen'] = {'modal_chars': modal_len, 'design_chars': exp_chars, 'design_bytes': exp_b,
                     'dist': {str(k): v for k, v in ldist.items()}, 'off': off,
                     'total_lines': len(all_lens)}

    # ---- 4 ⑤ 定点量程 / 钳制
    p = seq.index(next(s for s in seq if s[0] == 5))
    fq = [int(r[2][p]) for r in rows]
    clamped = [rows[i][0] for i, v in enumerate(fq) if abs(v) >= FIX_OVF]
    print('  · ⑤ 定点     : fq ∈ [%d, %d] ＝ **%.1f ~ %.1f code**'
          '（⇒ %.1f ~ %.1f µV @ %.4f µV/code）｜ 触钳制 %d 行%s'
          % (min(fq), max(fq), min(fq) / 10.0, max(fq) / 10.0,
             min(fq) / 10.0 * UV_PER_CODE, max(fq) / 10.0 * UV_PER_CODE, UV_PER_CODE,
             len(clamped), '' if not clamped else '（例 %s）' % clamped[:5]))
    rep['fix'] = {'min': min(fq), 'max': max(fq),
                  'code_min': min(fq) / 10.0, 'code_max': max(fq) / 10.0,
                  'uv_min': min(fq) / 10.0 * UV_PER_CODE, 'uv_max': max(fq) / 10.0 * UV_PER_CODE,
                  'uv_per_code': UV_PER_CODE, 'clamped': len(clamped)}

    # ---- 5 丢包 Δ（列① 853 域 / 列③ 500 域）
    #   ★T172b：**两套口径都要报**，缺一就会把结论说错一倍：
    #     ①「救回口径」＝ 把"被垃圾连坐、正文完好"的行放回 ⇒ **物理丢样**
    #     ②「原样口径」＝ 连被连坐的行一起丢（工具原行为）⇒ 缺样数**虚高约一倍**
    #   🛑 报"缺样率"必须**分子分母同域**：缺样 ＝（本列序号跨度 ＋ 1）− 实得行数。
    rep['delta'] = {}
    # ★「行率参考域」＝ 那份**1:1**的列（每行一个计数）：本项目＝列③(500 Hz)。
    #   853 域的列要靠它换算"每行理论 Δ"，才能把缺样从抽稀里分出来。
    ref_fs = FS500 if any(s[0] == 3 for s in seq) else None
    views = ([('救回口径（含被连坐行）', rows_all), ('原样口径（丢弃退化行）', rows)]
             if len(rows_all) != len(rows) else [('', rows)])
    for vi, (vlabel, vrows) in enumerate(views):
        tgt = rep['delta'] if vi == 0 else rep.setdefault('delta_asis', {})
        if vlabel:
            print('  ── 丢包体检口径：%s（%d 行）' % (vlabel, len(vrows)))
        for cid in (1, 3):
            if cid not in [c for c, _, _ in seq]:
                continue
            pos = [i for i, s in enumerate(seq) if s[0] == cid][0]
            v = [int(r[2][pos]) for r in vrows]
            d = [v[i] - v[i - 1] for i in range(1, len(v))]
            dist = collections.Counter(d)
            wraps = [i + 1 for i, x in enumerate(d) if x < -1000]
            # ★T173(2026-10-09) 订正：**合法 Δ 必须按列的域算**，不能一律要求 ＝1。
            #   列③（500 域，1:1）⇒ {1}；列①（853 域，`raw853 off` 时行节拍 500 Hz）⇒ 抽稀 {1,2}。
            #   证据：一份**干净**文件 Δ① 分布 {2:2877, 1:1196}、均值 1.7064 ≈ 理论 1.7065，
            #   旧判据却报"🛑 跳号 2877 处" —— 与同一份输出里"缺样 0.00%"**自相矛盾**。
            theory_c = (FS853 / FS500) if (cid == 1 and ref_fs) else 1.0
            hi_c = int(theory_c) + (0 if abs(theory_c - int(theory_c)) < 1e-9 else 1)  # 1.7065⇒2 / 1.0⇒1
            lo_c = max(1, int(theory_c))
            bad_i = [i for i, x in enumerate(d) if x >= -1000 and not (lo_c <= x <= hi_c)]  # ⚠ 存**索引**
            bad = [i + 1 for i in bad_i]
            ok = (not bad)
            print('  · 丢包体检列%d: Δ 分布 %s ｜ %s%s%s'
                  % (cid, dict(sorted(dist.items(), key=lambda kv: -kv[1])[:6]),
                     '✅ 无跳号' if ok else '🛑 跳号 %d 处' % len(bad),
                     '，回绕(①%06u 溢出) %d 处' % len(wraps) if wraps else '',
                     ('（例：行 %s，Δ=%s）' % (bad[:5], [d[i] for i in bad_i[:5]]))
                     if bad else ''))
            tgt[cid] = {'dist': {str(k): v2 for k, v2 in dist.items()},
                        'jumps': [{'line': i + 1, 'd': d[i]} for i in bad_i[:50]],
                        'wraps': wraps[:10], 'ok': ok, 'kept': len(v)}
            # 行率/时长（★T173 订正：**不再用"域频率 ÷ 中位 Δ"** —— 对抽稀列会算出假值）
            mid = sorted(x for x in d if x > 0)
            step = mid[len(mid) // 2] if mid else 1
            fs = FS853 if cid == 1 else FS500
            dense = (hi_c <= 1)      # True ⇒ 1:1 列（每行 1 个计数 ⇒ 本列号率**即**行率）
            # ★T172b 缺样账：🛑 **只有 1:1 的列（每行一个计数，中位 Δ=1）才能直读**；
            #   853 域的列本身每行就跨 ~1.7 个计数 ⇒ 它的"应有−实得"里混着**抽稀**，
            #   直接当缺样会报出 45%（假数）。这类列改用「Δ 均值反推」口径。
            if step == 1:
                span = v[-1] - v[0] + 1
                miss = span - len(v)
                print('        缺样账列%d   : 序号域应有 %d 个 ｜ 实得 %d ｜ **缺 %d ＝ %.2f%%**%s'
                      % (cid, span, len(v), miss, 100.0 * miss / span,
                         '  ⚠ 有回绕 ⇒ 首尾跨度法会低估，需分段算' if wraps else ''))
                tgt[cid]['span'] = span
                tgt[cid]['missing'] = miss
                tgt[cid]['missing_pct'] = round(100.0 * miss / span, 4)
            else:
                mean_d = sum(d) / len(d) if d else 0.0
                nom = FS853 if cid == 1 else FS500
                if ref_fs and nom and mean_d > 0:
                    theory = nom / ref_fs
                    print('        该列非 1:1（每行理论 Δ=%.4f＝%g/%g）⇒ 缺样账**不可直读**；'
                          '实测均值 Δ=%.4f ⇒ **反推缺样 %.2f%%**'
                          % (theory, nom, ref_fs, mean_d, 100.0 * (1.0 - theory / mean_d)))
                    tgt[cid]['theory_step'] = round(theory, 6)
                tgt[cid]['mean_delta'] = round(mean_d, 4)
            if mid:
                span_c = v[-1] - v[0]
                if dense:
                    print('        行率 ≈ %.2f Hz（1:1 列：每行 1 个计数 ⇒ 本列号率**即**行率）'
                          '｜ 序号跨度 %d ⇒ 约 %.3f s' % (fs, span_c, span_c / fs))
                else:
                    print('        序号率 ≈ %.2f Hz（第 %d 域计数，每行理论 %.4f 个 ⇒ 本列**不是**行率）'
                          '｜ 序号跨度 %d ⇒ 约 %.3f s'
                          % (fs, (853 if cid == 1 else 500), theory_c, span_c, span_c / fs))
                tgt[cid]['rate_hz'] = fs        # ★T173：恒＝本列**域频率**（不再 ÷ 中位 Δ）
                tgt[cid]['is_rowrate'] = bool(dense)
                tgt[cid]['theory_step'] = round(theory_c, 6)
                tgt[cid]['mean_delta'] = round(sum(d) / len(d), 4) if d else 0.0
                tgt[cid]['span'] = span_c
                tgt[cid]['span_s'] = span_c / fs

    # ★T173 新增：**列① 的体检用列③ 反推**（猛哥 2026-10-09 要求）。
    #   列③ 是 1:1 列（每行一个 500 域计数、缺样可直读）⇒ 它给出的**时长最可信**；
    #   拿它反推列① 的"应有跨度"（时长 × 853.2423），再与列① 实测跨度对账 ⇒ 两条独立路径互印。
    #   ⚠ 这是"多源互印"而不是"再算一遍"：列① 抽稀（每行 1.7065 个计数）⇒ 它**自己**判不出
    #     丢样，必须借列③ 的行节拍换算，这正是原判据写"应恒 1"时踩的坑。
    _d1, _d3 = rep['delta'].get(1), rep['delta'].get(3)
    if _d1 and _d3 and _d1.get('span') is not None and not _d1.get('wraps'):
        _t3 = _d3['span_s']                        # 列③ 反推的时长（最可信）
        _exp1 = _t3 * FS853                        # 列① 应有跨度（853.2423 个计数 / 秒）
        _act1 = float(_d1['span'])
        _dev = (_act1 - _exp1) / _exp1 * 100.0 if _exp1 else 0.0
        print('  · 列①⨯列③ 互印 : 列③ 反推时长 %.3f s ⇒ 列① 应有跨度 %.0f ｜ 实测 %d ⇒ %s'
              % (_t3, _exp1, _d1['span'],
                 '✅ 一致' if abs(_dev) <= 0.1 else '⚠ 偏差 %.2f%%' % _dev))
        rep['cross'] = {'t_s': _t3, 'span_exp': _exp1, 'span_act': _d1['span'],
                        'dev_pct': round(_dev, 4)}

    # ---- 6 列⑥ 值域
    if cpu:
        pos = [i for i, s in enumerate(seq) if s[0] == 6][0]
        dv = [int(r[2][pos]) for r in rows]
        med = sorted(dv)[len(dv) // 2]
        inrange = dtc_range[0] <= med <= dtc_range[1]
        print('  · 列⑥ dtc    : 中位 %d（∈[%d, %d] %s）｜ 值域 [%d, %d] ｜ dtc/%d = %.2f%%（量级参考）'
              % (med, dtc_range[0], dtc_range[1], flag(inrange), min(dv), max(dv),
                 CYCLES_PER_PERIOD, 100.0 * med / CYCLES_PER_PERIOD))
        rep['dtc'] = {'median': med, 'min': min(dv), 'max': max(dv), 'in_range': inrange}

    # ---- 7 与 ascii-1 对档
    if ref:
        rep['ref'] = cmp_ref(name, ref, rows, seq)
    return rep


def _load_a1(path):
    """读 ascii-1（7 列），返回 [(ecgraw:int, filterraw:float)]。列号按 T162 定的 7 列。"""
    out = []
    bl = read_lines(path)
    for b, _ in bl:
        t = tokenize(b)
        if len(t) != 7:
            continue
        try:
            out.append((int(t[1]), float(t[4])))
        except ValueError:
            continue
    return out


def cmp_ref(a2_path, a1_path, rows, seq):
    """按列② 序列对齐后，逐点比 ⑤/10.0 vs ascii-1 的 %.02f。"""
    a1 = _load_a1(a1_path)
    p2 = [i for i, s in enumerate(seq) if s[0] == 2][0]
    p5 = [i for i, s in enumerate(seq) if s[0] == 5][0]
    a2c2 = [int(r[2][p2]) for r in rows]
    a2c5 = [int(r[2][p5]) for r in rows]
    print('  · 与 ascii-1 对档 %s' % os.path.basename(a1_path))
    if not a1:
        print('      🛑 参考文件 0 行可解析（需 **7 列** ascii-1 文件）⇒ 拒绝给结论')
        return {'ok': False, 'why': 'ref empty'}
    # 粗对齐：在 a1 的 col② 里找 a2 首行值（前 2000 行内）
    tgt = a2c2[0]
    cands = [i for i, (c2, _) in enumerate(a1[:2000]) if c2 == tgt]
    best, best_off, best_ov = 0, None, 0
    for i in cands:
        ov = min(len(a1) - i, len(a2c2))
        if ov <= 50:
            continue
        hit = sum(1 for k in range(0, ov, max(1, ov // 200)) if i + k < len(a1) and a1[i + k][0] == a2c2[k])
        if hit > best:
            best, best_off, best_ov = hit, i, ov
    if best_off is None:
        print('      🛑 找不到对齐点（列② 不匹配）⇒ 两份不是同一段 ⇒ 拒绝给结论')
        return {'ok': False, 'why': 'no align'}
    ov = min(len(a1) - best_off, len(a2c2))
    d = [abs(a2c5[k] / 10.0 - a1[best_off + k][1]) for k in range(ov)]
    m = max(d)
    tol = 0.05 + 1e-6
    print('      对齐偏移 %d ｜ 重叠 %d 行 ｜ max|Δ| = %.6f code ｜ 判据 ≤ %.6f ⇒ %s'
          % (best_off, ov, m, tol, '✅' if m <= tol else '🛑'))
    print('      （对照：两档 ② 逐点相同 ⇒ %s）'
          % ('✅' if all(a2c2[k] == a1[best_off + k][0] for k in range(ov)) else '🛑 ② 不一致'))
    return {'ok': m <= tol, 'align_off': best_off, 'overlap': ov, 'max_abs_dev_uv': m}


def simulate_from_a1(src, dst, cols=(0, 0, 0)):
    """把一份真实 ascii-1（7 列）**投影**成 ascii-2，用于上板前预演判读链。

    投影规则 = 固件那一串 sprintf 的忠实复刻：① %06u（默认关）② %08d ⑤ round(v*10) %08d ⑥ %05u。
    返回 (写出行数, 非 7 列被跳过的行数)。
    """
    raw, idx, cpu = cols
    out, skip = [], 0
    iraw = 0
    for b, _ in read_lines(src):
        t = tokenize(b)
        if len(t) != 7:
            skip += 1
            continue
        try:
            c1, c2, c3, c5 = int(t[0]), int(t[1]), int(t[2]), float(t[4])
        except ValueError:
            skip += 1
            continue
        fv = c5
        fq = int(fv * 10 + 0.5) if fv >= 0 else int(fv * 10 - 0.5)
        fq = max(-FIX_OVF, min(FIX_OVF, fq))
        tk = []
        if raw:
            tk.append('%06u' % (iraw % 1000000))
        tk.append('%08d' % c2)
        if idx:
            tk.append('%06d' % (c3 % 1000000))
        tk.append('%08d' % fq)
        if cpu:
            tk.append('%05u' % (int(t[5]) % 100000))
        out.append(' '.join(tk))
        iraw += 1
    _write(dst, out)
    return len(out), skip


# ----------------------------------------------------------------------------- 自检

def _write(path, lines):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def selftest(tmp):
    os.makedirs(tmp, exist_ok=True)
    fails = []

    def chk(cond, msg):
        print('   %s %s' % ('✅' if cond else '🛑', msg))
        if not cond:
            fails.append(msg)

    def mk(raw, idx, cpu, n=400, drop_raw=None, drop_idx=None, clamp_at=None, widen=None):
        iraw, iidx, dtc = 100, 1000, 1970
        L = []
        for k in range(n):
            if drop_raw and k == drop_raw:
                iraw += 3
            if drop_idx and k == drop_idx:
                iidx += 21
            fq = -2825022 + k * 7000
            if clamp_at is not None and k == clamp_at:
                fq = FIX_OVF
            toks = []
            if raw:
                toks.append('%06u' % iraw)
            toks.append('%08d' % (3000000 + k * 11))
            if idx:
                toks.append('%06d' % iidx)
            toks.append('%08d' % fq)
            if cpu:
                toks.append('%05u' % dtc)
            if widen is not None and k == widen:
                toks[-1] = '%09d' % fq                 # 阴性对照：把 ⑤ 撑宽一位（⑤ 恒为**末列**）
            L.append(' '.join(toks))
            iraw += 1
            iidx += 1
        return L

    print('== 自检 A：8 种列组合的自动判档')
    for (raw, idx, cpu), exp in [((0, 0, 0), (0, 0, 0)), ((1, 0, 0), (1, 0, 0)), ((0, 1, 0), (0, 1, 0)),
                                 ((0, 0, 1), (0, 0, 1)), ((1, 1, 0), (1, 1, 0)), ((1, 0, 1), (1, 0, 1)),
                                 ((0, 1, 1), (0, 1, 1)), ((1, 1, 1), (1, 1, 1))]:
        p = os.path.join(tmp, 'a2_%d%d%d.txt' % (raw, idx, cpu))
        _write(p, mk(raw, idx, cpu))
        g = infer(read_lines(p))
        chk(g[:3] == exp, '组合 raw=%d idx=%d cpu=%d ⇒ 判定 %s｜行长 %d B'
            % (raw, idx, cpu, g[:3], expect_bytes(*exp)))

    print('== 自检 B：行长公式（默认 18 / 全开 38，与固件注释一致）')
    chk(expect_bytes(0, 0, 0) == 18, '默认组合 = 18 B')
    chk(expect_bytes(1, 1, 1) == 38, '全开组合 = 38 B')
    chk(expect_bytes(1, 0, 0) == 25 and expect_bytes(0, 1, 0) == 25 and expect_bytes(0, 0, 1) == 24,
        '单开组合 = 25 / 25 / 24 B')

    print('== 自检 C：丢包体检（列① Δ=4、列③ Δ=22 必须被抓到）')
    p = os.path.join(tmp, 'a2_drop.txt')
    _write(p, mk(1, 1, 0, drop_raw=100, drop_idx=250))
    r = check(p, 1, 1, 0)
    chk(not r['delta'][1]['ok'] and r['delta'][1]['jumps'][0]['d'] == 4,
        '列① 跳号被检出且 Δ=4（例：行 %s）' % r['delta'][1]['jumps'][0]['line'])
    chk(not r['delta'][3]['ok'] and r['delta'][3]['jumps'][0]['d'] == 22,
        '列③ 跳号被检出且 Δ=22')

    print('== 自检 D：钳制与宽度（阴性对照）')
    p = os.path.join(tmp, 'a2_clamp.txt')
    _write(p, mk(0, 0, 0, clamp_at=200))
    r = check(p, 0, 0, 0)
    chk(r['fix']['clamped'] == 1, '⑤ 触钳制 1 行被检出')
    chk(r['rowlen']['off'] == 0, '该文件行长仍恒定')
    p = os.path.join(tmp, 'a2_wide.txt')
    _write(p, mk(0, 0, 0, widen=300))
    r = check(p, 0, 0, 0)
    chk(r['rowlen']['off'] == 1 and r['width_col5']['off'] == 1,
        '阴性对照：⑤ 撑宽一位 ⇒ 行长与字段宽度**都**报偏离')

    print('== 自检 E：与 ascii-1 对档（定点反解误差应恰为半个台阶内）')
    n = 300
    a1, a2 = [], []
    for k in range(n):
        fq = -999999 + k * 13337
        a1.append('0 %08d %06d %05d %.02f %05d %d' % (3000000 + k * 11, 1000 + k, 0, fq / 10.0, 1970, 0))
        a2.append('%08d %08d' % (3000000 + k * 11, fq))
    p1, p2 = os.path.join(tmp, 'a1.txt'), os.path.join(tmp, 'a2.txt')
    _write(p1, a1)
    _write(p2, a2)
    r = check(p2, 0, 0, 0, ref=p1)
    chk(r['ref']['ok'] and r['ref']['overlap'] == n,
        '对档 overlap=%d 行、max|Δ|=%.6f code ≤ 0.05' % (r['ref']['overlap'], r['ref']['max_abs_dev_uv']))
    # 反向：把 a2 整体右移 20 行 ⇒ 对齐应找到偏移 20 而不是硬比
    _write(p2 + '.shift', a2[20:] )
    r2 = check(p2 + '.shift', 0, 0, 0, ref=p1)
    chk(r2['ref']['ok'] and r2['ref']['overlap'] == n - 20, '错位 20 行仍能对齐（overlap=%d）'
        % r2['ref']['overlap'])

    print('== 自检 F：判档失败必须**拒绝**而不是猜')
    p = os.path.join(tmp, 'a2_weird.txt')
    _write(p, ['%07u %08d %06d %08d' % (1500000 + k, 3000000, 1000, 12345678) for k in range(50)])
    g = infer(read_lines(p))
    chk(g[0] is None, '表外宽度模式 ⇒ 返回 None（调用方拒绝判读）：%s' % g[3])

    print('== 自检 G：尾部垃圾块（T172）⇒ 截断救回 ＋ 两套口径的缺样账')
    #   造法与真实缺陷同构：每 15 行后**追加** 31 B NUL ＋ 1 B 0xB7（无 \n）。
    #   ⇒ 那一行被"连坐"（前 31 字符仍是好行）、且被顶掉一行（本行序号跳 1）。
    nbatch, blk = 15, 4
    rowsB, iraw, iidx = [], 0, 1000
    #   ⚠ 末尾多留 5 行干净行：否则**最后一块**的下标跳号落在文件之外 ⇒ 跨度法看不见它（自检会假红）。
    for k in range(nbatch * blk + 5):
        s = ('%06u %08d %06d %08d' % (iraw, 3000000 + k * 11, iidx, -999999 + k * 7000)).encode('ascii')
        iraw += 1
        iidx += 1
        if (k + 1) % nbatch == 0:
            s += b'\x00' * 15 + b'\xb7' + b'\x00' * 15
            iidx += 1                       # 被垃圾顶掉的那一行
        rowsB.append(s)
    p = os.path.join(tmp, 'a2_garbage.txt')
    with open(p, 'wb') as f:
        f.write(b'\n'.join(rowsB) + b'\n')
    r = check(p, 1, 1, 0)
    chk(r['salvaged']['n'] == blk, '垃圾块 %d 个 ⇒ 截断救回 %d 行' % (blk, r['salvaged']['n']))
    chk(r['degenerate']['n'] == 0, '可救回的行**不算**不可救退化行（实测 %d）' % r['degenerate']['n'])
    chk(r['delta'][3]['missing'] == blk,
        '救回口径 列③：缺 %.0f（＝垃圾块数 ⇒ **真实丢样**）' % r['delta'][3]['missing'])
    chk(r['delta_asis'][3]['missing'] == 2 * blk,
        '原样口径 列③：缺 %.0f（＝2× ⇒ 这就是"虚高一倍"）' % r['delta_asis'][3]['missing'])
    chk(r['rowlen']['off'] == blk, '行长偏离仍报出（%d 行），不许因为能救回就藏起来' % blk)

    print('== 自检 H：真机形态（raw853 off ⇒ 列① 抽稀 {1,2}）不得把 Δ=2 当跳号')
    #   ★T173(2026-10-09) 立此用例的原因：mk() 造的是列① **恒 +1**（1:1 形态），
    #   **与真机不符** ⇒ 旧判据"自检全绿、真机误报 2877 处"（守卫挂在合成物上、没挂在产物上）。
    #   这里按真机节拍造：列① 每行跨 round(k·853.2423/500) 个计数 ⇒ Δ∈{1,2}。
    def mkH(drop_at=None, n=600):
        L, iidx = [], 5000
        for k in range(n):
            ir = 100000 + int(round(k * FS853 / FS500)) + (3 if (drop_at is not None and k >= drop_at) else 0)
            L.append('%06u %08d %06d %08d' % (ir % 1000000, 3000000 + k * 11, iidx, -999999 + k * 7000))
            iidx += 1
        return L

    p = os.path.join(tmp, 'a2_thinned.txt')
    _write(p, mkH())
    r = check(p, 1, 1, 0)
    d1 = {int(k): v for k, v in r['delta'][1]['dist'].items()}
    chk(all(x <= 2 for x in d1) and r['delta'][1]['jumps'] == [],
        '列① 抽稀：Δ∈{1,2} **不算**跳号（旧判据在此会报 %d 处假跳号）'
        % sum(v for x, v in d1.items() if x == 2))
    chk(r['delta'][1]['rate_hz'] == FS853 and not r['delta'][1]['is_rowrate'],
        '列① 报**序号率** %.2f Hz（不是 853.2423/2 = 426.6 Hz 的假行率）' % r['delta'][1]['rate_hz'])
    chk(r['delta'][3]['rate_hz'] == FS500 and r['delta'][3]['is_rowrate'],
        '列③ 报**行率** %.2f Hz（1:1 列 ⇒ 号率即行率）' % r['delta'][3]['rate_hz'])
    chk(abs(r['cross']['dev_pct']) <= 0.1,
        '列①⨯列③ 互印（干净）：列③ 反推时长 ⇒ 列① 应有跨度与实测一致（偏差 %.3f%%，阈 0.1%%）'
        % r['cross']['dev_pct'])
    _write(p, mkH(drop_at=300))
    r = check(p, 1, 1, 0)
    jd1 = [j['d'] for j in r['delta'][1]['jumps']]
    chk(len(jd1) == 1 and jd1[0] >= 3,
        '列① 真丢样（人为 Δ≈5）仍被检出：jumps=%s（Δ∈{1,2} 的 %d 行**未**混进来）'
        % (jd1, sum(v for x, v in {int(k): v for k, v in r['delta'][1]['dist'].items()}.items() if x == 2)))

    print('')
    print('== 自检结果：%s' % ('全部通过 ✅' if not fails else '🛑 %d 项未过' % len(fails)))
    for f in fails:
        print('   🛑 %s' % f)
    return 0 if not fails else 1


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description='ascii-2 抓数文件判读/体检')
    ap.add_argument('--name', help='ascii-2 抓数 txt')
    ap.add_argument('--ref', help='同段信号的 ascii-1 抓数 txt（7 列）')
    ap.add_argument('--cols', default='', help='显式列组合，如 raw,idx,cpu（缺省自动判档）')
    ap.add_argument('--max-rows', type=int, default=0)
    ap.add_argument('--emit-meta', default='')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--simulate-from', metavar='A1_TXT',
                    help='把一份真实 ascii-1（7 列）投影成 ascii-2 到 --out（上板前预演）')
    ap.add_argument('--out', default='', help='--simulate-from 的输出路径')
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest(os.path.join(ROOT, 'tools', '_t163', '_tmp_ascii2_selftest'))

    if a.simulate_from:
        if not a.out:
            print('🛑 --simulate-from 需要 --out')
            return 2
        n, skip = simulate_from_a1(a.simulate_from, a.out)
        print('[预演] 由 %s 投影出 %s：%d 行（跳过非 7 列 %d 行）'
              % (os.path.basename(a.simulate_from), a.out, n, skip))
        if n == 0:
            print('🛑 投影出 0 行 ⇒ 拒绝继续（源文件可能不是 ascii-1）')
            return 3
        rep = check(a.out, 0, 0, 0, max_rows=a.max_rows or None, ref=a.simulate_from)
        if a.emit_meta:
            with io.open(a.emit_meta, 'w', encoding='utf-8') as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
            print('  · sidecar  : 已写 %s' % a.emit_meta)
        return 0

    if not a.name:
        ap.print_help()
        return 2
    if not os.path.exists(a.name):
        print('🛑 文件不存在：%s' % a.name)
        return 2

    raw = idx = cpu = None
    if a.cols:
        s = set(x.strip().lower() for x in a.cols.split(',') if x.strip())
        raw, idx, cpu = int('raw' in s), int('idx' in s), int('cpu' in s)
        print('[列组合] 来自 --cols：raw=%d idx=%d cpu=%d' % (raw, idx, cpu))
    else:
        raw, idx, cpu, ev = infer(read_lines(a.name))
        if raw is None:
            print('🛑 **拒绝判读**：无法自动判定列组合。\n   观测：%s\n'
                  '   允许的 8 种：%s\n   ⇒ 请用 `--cols raw,idx,cpu` 显式声明，'
                  '或改抓一份 `test asciistat` 回显一起留档。'
                  % (ev, [''.join(str(c) for c in k) for k in PAT2COLS]))
            return 3
        print('[列组合] 自动判定：raw=%d idx=%d cpu=%d（依据：%s）' % (raw, idx, cpu, ev))

    rep = check(a.name, raw, idx, cpu, max_rows=a.max_rows or None, ref=a.ref)
    if a.emit_meta:
        with io.open(a.emit_meta, 'w', encoding='utf-8') as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print('  · sidecar  : 已写 %s' % a.emit_meta)
    return 0


if __name__ == '__main__':
    sys.exit(main())
