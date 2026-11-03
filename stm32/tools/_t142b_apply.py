# -*- coding: utf-8 -*-
"""T142b: ecg_dc_block 预热跳帧(SKIP) —— 三元组锚点幂等补丁

为什么用脚本而不是编辑器：Filter/ 下两个文件**全 CRLF**（.h 45/45、.c 40/40）。
用 newline='\\n' 写盘会把整份转成 LF（编译/自检都抓不到，只有 diff 会发现）。
本脚本全程 newline='' 读写，逐字节保留原行尾。

锚点纪律（技能 4.90）：每个替换用 (old, new, marker) 三元组；
  命中数 != 1 直接报错退出，不写盘；已含 marker 则跳过（幂等）。
"""
import io
import hashlib
import os
import sys

H = os.path.join('Filter', 'ecg_dc_block.h')
C = os.path.join('Filter', 'ecg_dc_block.c')
MARK_H = 'ECG_DC_BLOCK_WARM_SKIP'
MARK_C = 'T142b'
DRY = '--dry' in sys.argv


def rd(p):
    with io.open(p, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def wr(p, s):
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()[:12]


def crlf_stat(s):
    lines = s.split('\n')
    body = [l for l in lines[:-1]]
    n_crlf = sum(1 for l in body if l.endswith('\r'))
    return n_crlf, len(body)


# ============================ .h 改动 ============================
H1_OLD = (
    '#ifdef __cplusplus\n'
    'extern "C" {\n'
    '#endif\n'
    '\n'
    '/*\n'
    ' * \u76f4\u6d41\u6d88\u9664\u5668\uff08DC blocker\uff0c\u4e00\u9636\u9ad8\u901a\uff09\uff0c2026-09-05 \u65b0\u589e\u3002'
)

H1_NEW = (
    '#ifdef __cplusplus\n'
    'extern "C" {\n'
    '#endif\n'
    '\n'
    '/* ---- T142b (2026-09-28)\uff1a\u9884\u70ed\u8df3\u5e27 SKIP --------------------------------\n'
    ' * \u80cc\u666f\uff1aADS1293 ECG \u901a\u9053 SINC \u5efa\u7acb\u65f6\u95f4 ts = 5 \u00d7 R1\u00b7R2\u00b7R3 / fS\n'
    ' *       = 5 \u4e2a\u8f93\u51fa\u6837\u672c\uff08datasheet SNAS602C \u00a78.3.8\uff1b\u672c\u673a ODR 853.24 Hz\n'
    ' *       \u21d2 5.86 ms\uff09\u21d2 START \u540e**\u524d 5 \u5e27\u6570\u636e\u672a\u5efa\u7acb**\u3002\n'
    ' *       \u800c process() \u7684\u9884\u70ed\u5206\u652f\u4f1a\u628a\u9996\u5e27\u5f53 x_prev\uff1b\u82e5\u9996\u5e27\u6070\u662f\n'
    ' *       \u672a\u5efa\u7acb\u7684\u5f02\u5e38\u70b9\uff08\u504f\u9ad8/\u504f\u4f4e \u03b4\uff09\uff0c\u5dee\u5206\u5f0f (x[1]-x[0]) = \u2213\u03b4\n'
    ' *       **\u76f4\u63a5\u4ea7\u751f\u4e00\u4e2a\u51b2\u6fc0**\uff0c\u4e4b\u540e\u6309 r^(n-1) \u4ee5 \u03c4 = 1/(2\u03c0\u00b7fc) \u2248 7.96 s \u8870\u51cf\n'
    ' *       \u21d2 \u8868\u73b0\u4e3a\u300c\u5c4f\u5e55\u57fa\u7ebf\u6574\u6761\u5e73\u79fb\u300120 s \u624d\u56de\u722c\u5b8c\u300d\u3002\n'
    ' * \u505a\u6cd5\uff1a\u9884\u70ed\u671f\u4ece 1 \u5e27\u5ef6\u957f\u4e3a (SKIP + 1) \u5e27 \u2014\u2014 \u524d SKIP \u5e27\u53ea\u63a8\u8fdb x_prev\u3001\n'
    ' *       \u4e0d\u4ea7\u751f\u5dee\u5206\uff0c\u7b2c (SKIP+1) \u5e27\u624d\u5b9a\u57fa\u51c6\u3002\n'
    ' *       SKIP = 0 \u21d2 \u4e0e\u65e7\u884c\u4e3a**\u9010\u5b57\u8282\u7b49\u4ef7**\uff08\u7528\u4e8e A/B \u5bf9\u6bd4\uff09\u3002\n'
    ' * \u4f9d\u636e\uff1aprocess() \u662f\u5dee\u5206\u5f0f\uff0c\u5bf9\u5e38\u6570\u8f93\u5165\u8f93\u51fa\u6052 0 \u21d2 x_prev \u53ea\u9700\u53d6\u5230\n'
    ' *       "\u5df2\u7a33\u5b9a\u7684\u503c"\uff0c\u65e0\u9700\u5747\u503c\uff08\u5747\u503c\u53ea\u6297\u566a\u3001\u4e0d\u6539\u53d8\u672c\u673a\u5236\uff09\u3002\n'
    ' * \u9009\u5740\uff1adc_block \u662f\u6ee4\u6ce2\u94fe**\u7b2c\u4e00\u7ea7**\uff08ads1293.c:1098-1099\uff09\u21d2 \u6b64\u5904\u4e22\u5f03\u7684\u6837\u672c\n'
    ' *       \u4e0d\u4f1a\u6c61\u67d3\u540e\u7eed ecg_filter_f64\u3002\n'
    ' * \u6210\u672c\uff1a\u7ed3\u6784\u4f53\u65b0\u589e int \u843d\u5728\u539f padding \u5185 \u21d2 sizeof \u4e0d\u53d8\uff0832 B\uff0c\u00b10 RAM\uff09\uff1b\n'
    ' *       \u7a33\u6001\u8def\u5f84\u96f6\u989d\u5916\u8ba1\u7b97\uff1b\u9884\u70ed\u671f\u6bcf\u6837\u672c 1 \u6b21\u6bd4\u8f83\u3002\n'
    ' */\n'
    '#ifndef ECG_DC_BLOCK_WARM_SKIP\n'
    '#define ECG_DC_BLOCK_WARM_SKIP   8    /* 0 = \u5173\u95ed(\u65e7\u884c\u4e3a) / 8 = 5 \u5e27\u5efa\u7acb\u671f\u7684 1.60 \u500d */\n'
    '#endif\n'
    '#if (ECG_DC_BLOCK_WARM_SKIP < 0) || (ECG_DC_BLOCK_WARM_SKIP > 64)\n'
    '#error "ECG_DC_BLOCK_WARM_SKIP \u53d6\u503c\u975e\u6cd5\uff08\u5141\u8bb8 0 ~ 64\uff09"\n'
    '#endif\n'
    '\n'
    '/*\n'
    ' * \u76f4\u6d41\u6d88\u9664\u5668\uff08DC blocker\uff0c\u4e00\u9636\u9ad8\u901a\uff09\uff0c2026-09-05 \u65b0\u589e\u3002'
)

H2_OLD = ' * \u9996\u6837\u672c\u81ea\u52a8"\u9884\u70ed"\uff1a\u8bb0\u4f4f x[0] \u4f5c\u4e3a\u76f4\u6d41\u8d77\u70b9\u5e76\u8f93\u51fa 0\uff0c\u907f\u514d y[0]=x[0]=6M \u7684\u5c16\u5cf0\u3002\n */'
H2_NEW = (' * \u9884\u70ed\uff1a\u524d (ECG_DC_BLOCK_WARM_SKIP + 1) \u4e2a\u6837\u672c\u53ea\u63a8\u8fdb x_prev \u5e76\u8f93\u51fa 0\uff1b\n'
          ' * \u907f\u514d y[0]=x[0]=6M \u7684\u5c16\u5cf0\uff0c\u540c\u65f6\u8df3\u8fc7 START \u540e\u7684 SINC \u672a\u5efa\u7acb\u6bb5\n'
          ' * \uff08T142b\uff0c\u8be6\u89c1\u6587\u4ef6\u5934\u90e8 SKIP \u8bf4\u660e\uff09\u3002\n */')

H3_OLD = '    int    warmed;   /* \u9996\u6837\u672c\u9884\u70ed\u6807\u8bb0 */\n} ecg_dc_block;'
H3_NEW = ('    int    warmed;   /* \u9884\u70ed\u6807\u8bb0\uff08\u8df3\u5e27\u7528\u5c3d\u540e\u7f6e 1\uff09*/\n'
          '    int    warm_skip; /* T142b\uff1a\u9884\u70ed\u671f\u5269\u4f59\u8df3\u5e27\u6570\uff08\u843d\u5728\u539f padding \u5185\uff0csizeof \u4e0d\u53d8\uff09*/\n'
          '} ecg_dc_block;')

# ============================ .c 改动 ============================
C1_OLD = '    b->warmed = 0;\n}\n\nvoid ecg_dc_block_reset'
C1_NEW = ('    b->warmed = 0;\n'
          '    b->warm_skip = ECG_DC_BLOCK_WARM_SKIP;   /* T142b */\n'
          '}\n\nvoid ecg_dc_block_reset')

C2_OLD = '    b->warmed = 0;\n}\n\nfloat ecg_dc_block_process'
C2_NEW = ('    b->warmed = 0;\n'
          '    b->warm_skip = ECG_DC_BLOCK_WARM_SKIP;   /* T142b */\n'
          '}\n\nfloat ecg_dc_block_process')

C3_OLD = ('    if (!b->warmed) {                 /* \u9996\u6837\u672c: \u8bb0\u76f4\u6d41\u8d77\u70b9, \u8f93\u51fa 0 \u800c\u975e 6M */\n'
          '        b->x_prev = (double)x;\n'
          '        b->warmed = 1;\n'
          '        return 0.0f;\n'
          '    }')
C3_NEW = ('    if (!b->warmed) {                 /* \u9884\u70ed\u671f: \u53ea\u63a8\u8fdb x_prev, \u4e0d\u4ea7\u751f\u5dee\u5206 */\n'
          '        b->x_prev = (double)x;\n'
          '        if (b->warm_skip > 0) {       /* T142b: \u8df3\u5e27\u672a\u7528\u5c3d \u21d2 \u7ee7\u7eed\u8df3(\u8f93\u51fa 0 = \u6b63\u786e\u57fa\u7ebf) */\n'
          '            b->warm_skip--;\n'
          '            return 0.0f;\n'
          '        }\n'
          '        b->warmed = 1;\n'
          '        return 0.0f;\n'
          '    }')

PATCHES = [
    (H, 'H1 \u63d2 SKIP \u5b8f\u5757\u4e0e\u5934\u90e8\u8bf4\u660e', H1_OLD, H1_NEW, MARK_H),
    (H, 'H2 \u66f4\u65b0\u9884\u70ed\u8bf4\u660e\u6ce8\u91ca', H2_OLD, H2_NEW, None),
    (H, 'H3 \u7ed3\u6784\u4f53\u52a0 warm_skip(padding \u5185)', H3_OLD, H3_NEW, None),
    (C, 'C1 init \u521d\u59cb\u5316 warm_skip', C1_OLD, C1_NEW, MARK_C),
    (C, 'C2 reset \u521d\u59cb\u5316 warm_skip', C2_OLD, C2_NEW, None),
    (C, 'C3 process \u63d2\u8df3\u5e27\u5206\u652f', C3_OLD, C3_NEW, None),
]

print('=' * 76)
print('[T142b] \u9884\u70ed\u8df3\u5e27(SKIP) \u8865\u4e01  ' + ('DRY-RUN\uff08\u4e0d\u5199\u76d8\uff09' if DRY else '\u771f\u5199\u76d8'))
print('=' * 76)

texts = {}
for p in (H, C):
    texts[p] = rd(p)
    a, b = crlf_stat(texts[p])
    print('[read] %-28s md5=%s  CRLF %d/%d' % (p, md5(texts[p]), a, b))

applied = 0
skipped = 0
failed = []

for path, name, old, new, marker in PATCHES:
    s = texts[path]
    if marker and marker in s and old not in s:
        print('  [skip] %-46s \u2192 \u5df2\u5e94\u7528\uff08\u5e42\u7b49\uff09' % name)
        skipped += 1
        continue
    n = s.count(old)
    if n != 1:
        failed.append('%s\uff1a\u951a\u70b9\u547d\u4e2d %d \u6b21\uff08\u8981\u6c42 1\uff09' % (name, n))
        print('  [FAIL] %-46s \u2192 \u547d\u4e2d %d \u6b21' % (name, n))
        continue
    texts[path] = s.replace(old, new, 1)
    applied += 1
    print('  [ok]   %-46s \u2192 +%d B' % (name, len(new.encode('utf-8')) - len(old.encode('utf-8'))))

print('-' * 76)
print('\u5e94\u7528 %d \u9879\uff0c\u8df3\u8fc7 %d \u9879\uff0c\u5931\u8d25 %d \u9879' % (applied, skipped, len(failed)))
if failed:
    print('\u26d0 \u5931\u8d25\u8be6\u60c5\uff1a')
    for f in failed:
        print('   - ' + f)
    sys.exit(1)

if DRY:
    print('\u9884\u6f14\u7ed3\u675f\uff08\u672a\u5199\u76d8\uff09')
    sys.exit(0)

for p in (H, C):
    before = rd(p)
    wr(p, texts[p])
    after = rd(p)
    a, b = crlf_stat(after)
    flag = '\u2713' if (a == b and b > 0) else '\u26a0 \u884c\u5c3e\u5f02\u5e38'
    print('[write] %-28s md5=%s \u2192 %s  CRLF %d/%d %s'
          % (p, md5(before), md5(after), a, b, flag))

print('=' * 76)
