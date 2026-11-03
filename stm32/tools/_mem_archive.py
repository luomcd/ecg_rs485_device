#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
_mem_archive.py — ADS1293 项目「记忆归档」统一入口（参数化，取代每轮手写一次性脚本）

动机：一趟正常的归档原本要手写 3~5 个 python - <<'EOF' 片段（改 NOW、插表行、追加日报、
复测字符数），每轮 5~8 次工具调用。收敛成本脚本后 = 1 条命令。

用法（按序执行：backup -> daily -> status -> next -> row -> check）

  # 只体检
  python tools/_mem_archive.py --check

  # 完整归档（推荐：内容走文件，避免命令行中文/换行转义问题）
  python tools/_mem_archive.py --backup \
      --append-daily _t68_daily.md \
      --status-file   _t68_status.md \
      --next-file     _t68_next.md \
      --row-file      _t68_row.md \
      --check

参数
  --check              只体检：NOW 行数/最长行、MEMORY 余量、SETTLED/DEVICE 体量、当日日报
  --backup             先把 NOW/MEMORY/SETTLED 备份到 tools/_bak_mem_YYYYMMDD/
  --append-daily F     把文件 F 内容追加到当日日报 .workbuddy/memory/YYYY-MM-DD.md（不存在则创建）
  --status-file F      用 F 内容替换 NOW.md「## 一句话现状」整段（**含标题行**，标题里写日期）
  --next-file F        用 F 内容替换 NOW.md「## 下一步」整段（**含标题行**）
  --row-file F         把 F 内容（一行表格行，以 | 开头）插到「净增量」表首，只保留最近 --keep 行
  --keep N             净增量表保留行数（默认 2）——更早的行请确认已进日报
  --dry                只打印将做什么，不写盘

上限（体检会告警，不阻塞）
  NOW.md ≤60 行、单行 ≤400 字符；MEMORY.md ≤3000；SETTLED.md ≤5000
"""
import argparse, datetime, io, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MEMDIR = os.path.join(ROOT, '.workbuddy', 'memory')

LIM_NOW_LINES = 60
LIM_NOW_COLLEN = 400
LIM_MEMORY = 3000
LIM_SETTLED = 5000

TAG_NOW = '## 一句话现状'
TAG_NEXT = '## 下一步'
TAG_ROWHDR = '| # | 结论 | 关键数字 |'


def rd(p):
    with io.open(p, encoding='utf-8') as f:
        return f.read()


def wr(p, s):
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(s)


def today():
    return datetime.date.today().isoformat()


def report(msg):
    print('  ' + msg)


# ---------------------------------------------------------------- 段落替换
def splice_section(text, tag, new_body, path):
    """用 new_body 替换以 tag 开头的整段（含标题行，到下一个 '## ' 前）

    🛑 T119 新增守卫：new_body 的**第一行必须是本段标题**。否则替换会把标题一起吞掉
    （T118 就发生过：传进来的 next 文件没带 `## 下一步`，标题消失而 `--check` 不报，
    要等下一轮 `splice_section` 找不到段才炸）。守卫放在**写盘前**，当场报错。"""
    nb = [l for l in new_body.split('\n') if l.strip()]
    if not nb or not nb[0].startswith(tag):
        sys.exit('!! %s 的第一行必须是**以「%s」开头**的段落标题（否则会把标题吞掉）——实得: %r'
                 % (path, tag, (nb[0] if nb else '')))
    lines = text.split('\n')
    # 🛑 T119 修正：必须用 **前缀匹配** `l.startswith(tag)`，不能用 `tag in l`。
    #    T119 踩过：状态行正文里写了字面串「`## 下一步`」⇒ 子串匹配把**上一段的标题行**
    #    当成了本段起点 ⇒ 一次替换吞掉了「一句话现状 ~ 净增量」整片（且守卫查不出来）。
    i0 = next((i for i, l in enumerate(lines) if l.startswith(tag)), None)
    if i0 is None:
        sys.exit('!! 在 %s 里找不到段落: %s' % (path, tag))
    i1 = len(lines)
    for i in range(i0 + 1, len(lines)):
        if lines[i].startswith('## '):
            i1 = i
            break
    body = new_body.rstrip('\n').split('\n')
    # 🛑 T121 新增：**打印段落行数变化**（T119／T121 **连续两次**栽在同一个坑上 ——
    #    整段替换把段落里的**尾随正文行**一起吞掉；「一句话现状」段按设计 **2 行非空**：
    #    ① 标题行（「一句话」就写在标题里）② 1 行文档串 `T101_协议v2.2…`，
    #    而归档件只带 ① ⇒ 文档串静默消失）。
    #    减少时给 ⚠ 提醒（**不阻断**：合法精简如「下一步」段从 7 条减到 3 条也会减少）。
    _old = len([l for l in lines[i0:i1] if l.strip()])
    _new = len([l for l in body if l.strip()])
    _warn = ('' if _new >= _old else
             '   ⚠ 少了 %d 行 —— 确认没漏带该段原有的正文行（如「一句话现状」段的文档串）'
             % (_old - _new))
    print('  · 段「%s」非空行 %d → %d%s' % (tag, _old, _new, _warn))
    out = lines[:i0] + body + [''] + lines[i1:]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out))


def insert_row(text, row, keep, path):
    """把 row 插到净增量表首，只保留 keep 行"""
    lines = text.split('\n')
    hi = next((i for i, l in enumerate(lines) if l.startswith(TAG_ROWHDR)), None)
    if hi is None:
        sys.exit('!! 在 %s 里找不到净增量表头: %s' % (path, TAG_ROWHDR))
    di = hi + 1                                  # 分隔行 |---|---|
    j = di + 1
    while j < len(lines) and lines[j].startswith('|'):
        j += 1
    data = lines[di + 1:j]
    fresh = [row.strip()] + [x for x in data if x.strip()]
    fresh = fresh[:keep]
    out = lines[:di + 1] + fresh + lines[j:]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out))


# ---------------------------------------------------------------- 体检
def check():
    print('\n=== 记忆体检（%s） ===' % today())
    bad = 0
    p = os.path.join(MEMDIR, 'NOW.md')
    if os.path.exists(p):
        s = rd(p)
        lines = s.rstrip('\n').split('\n')
        long = [(i + 1, len(l)) for i, l in enumerate(lines) if len(l) > LIM_NOW_COLLEN]
        f1 = len(lines) > LIM_NOW_LINES
        f2 = bool(long)
        bad += f1 + f2
        report('NOW.md      %2d 行 / %d 字符   行数%s  单行≤%d%s'
               % (len(lines), len(s),
                  '✗ 超 %d' % LIM_NOW_LINES if f1 else '✓',
                  LIM_NOW_COLLEN, ' ✗ %d 行超长' % len(long) if f2 else ' ✓'))
        for ln, cl in long:
            report('            行 %d 有 %d 字符' % (ln, cl))
        # 🛑 T119 新增：段落标题必须齐（缺了不会报超限，但下一轮 splice 会炸）
        miss = [tg for tg in (TAG_NOW, TAG_NEXT, TAG_ROWHDR)
                if not any(l.startswith(tg) for l in lines)]
        if miss:
            bad += 1
            report('            ✗ 缺段落标题 %s（splice_section 会吞标题，务必补回）' % miss)
        else:
            report('            段落标题 ✓（一句话现状／下一步／净增量表头 齐）')
        # 🛑 T121 新增：「一句话现状」段的**正文行数下限** —— 该段按设计 **2 行非空**
        #    （标题行 —— 「一句话」就写在标题里 —— ＋ 1 行文档串 `T101_协议v2.2…`）。
        #    T119／T121 两次都是归档件只带了「标题行」⇒ 整段替换把**文档串**吞掉，
        #    而上面那两条（标题齐／行数上限）**都查不出来**。
        _i0 = next((i for i, l in enumerate(lines) if l.startswith(TAG_NOW)), None)
        if _i0 is not None:
            _j = _i0 + 1
            while _j < len(lines) and not lines[_j].startswith('## '):
                _j += 1
            _nb = len([l for l in lines[_i0:_j] if l.strip()])
            if _nb < 2:
                bad += 1
                report('            ✗ 「一句话现状」段只有 %d 行非空（应 ≥2：标题行 ＋ 文档串）'
                       ' —— 文档串被整段替换吞掉了，务必补回' % _nb)
            else:
                report('            一句话现状段 %d 行非空 ✓（标题行 ＋ 文档串）' % _nb)
    for name, lim in (('MEMORY.md', LIM_MEMORY), ('SETTLED.md', LIM_SETTLED)):
        p = os.path.join(MEMDIR, name)
        if os.path.exists(p):
            c = len(rd(p))
            ok = c <= lim
            bad += (not ok)
            report('%-11s %5d 字符   限 %d   余 %d %s' % (name, c, lim, lim - c, '✓' if ok else '✗'))
        else:
            report('%-11s 缺失' % name)
    d = os.path.join(MEMDIR, today() + '.md')
    report('当日日报    %s %s' % (today() + '.md',
                                  ('%d 字符' % len(rd(d))) if os.path.exists(d) else '（尚未创建）'))
    print('  ==> %s' % ('全部合规' if bad == 0 else '有 %d 项超限，请压缩' % bad))
    return bad


# ---------------------------------------------------------------- backup
def backup():
    dst = os.path.join(HERE, '_bak_mem_' + today().replace('-', ''))
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in ('NOW.md', 'MEMORY.md', 'SETTLED.md', 'DEVICE.md'):
        src = os.path.join(MEMDIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, f))
            n += 1
    print('已备份 %d 个文件 -> %s' % (n, os.path.relpath(dst, ROOT)))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--backup', action='store_true')
    ap.add_argument('--append-daily', metavar='F')
    ap.add_argument('--status-file', metavar='F')
    ap.add_argument('--next-file', metavar='F')
    ap.add_argument('--row-file', metavar='F')
    ap.add_argument('--keep', type=int, default=2)
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    did = False
    if a.backup:
        did = True
        (print('[dry] 备份 NOW/MEMORY/SETTLED/DEVICE') if a.dry else backup())

    nowp = os.path.join(MEMDIR, 'NOW.md')
    now = rd(nowp) if os.path.exists(nowp) else None

    if a.append_daily:
        did = True
        body = rd(a.append_daily)
        d = os.path.join(MEMDIR, today() + '.md')
        old = rd(d) if os.path.exists(d) else '# %s — ADS1293 工作日志\n' % today()
        if a.dry:
            print('[dry] 追加 %d 字符 -> %s' % (len(body), os.path.basename(d)))
        else:
            wr(d, old.rstrip('\n') + '\n\n' + body.rstrip('\n') + '\n')
            print('日报已追加 %d 字符 -> %s' % (len(body), os.path.basename(d)))

    for opt, tag in (('status_file', TAG_NOW), ('next_file', TAG_NEXT)):
        f = getattr(a, opt)
        if not f:
            continue
        did = True
        if now is None:
            sys.exit('!! NOW.md 不存在: ' + nowp)
        if a.dry:
            print('[dry] 替换 NOW.md「%s」段 <- %s' % (tag, f))
            # 🛑 T121：dry **也要真跑一遍**（结果丢弃）—— 否则 splice_section 里那条
            #    「非空行 N → M」的 ⚠ 提示在 dry 时不打印，**风险最高的一步反而看不到**。
            splice_section(now, tag, rd(f), nowp)
            continue
        now = splice_section(now, tag, rd(f), nowp)
        print('NOW.md「%s」段已替换' % tag)

    if a.row_file:
        did = True
        if now is None:
            sys.exit('!! NOW.md 不存在: ' + nowp)
        row = rd(a.row_file).strip().split('\n')[0]
        if a.dry:
            print('[dry] 插入净增量行并截到 %d 行: %s' % (a.keep, row[:60]))
        else:
            now = insert_row(now, row, a.keep, nowp)
            print('净增量表已插入新行（保留最近 %d 行）' % a.keep)

    if now is not None and not a.dry and (a.status_file or a.next_file or a.row_file):
        wr(nowp, now)

    if a.check:
        did = True
        check()

    if not did:
        ap.print_help()


if __name__ == '__main__':
    main()
