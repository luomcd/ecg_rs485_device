# -*- coding: utf-8 -*-
"""T192 · MEMORY.md 结构重构（拆 §8 + 搬 dc_block + 补两条铁律）

纪律（memory-distill §8.8）：
  · 保留行一律按**唯一前缀**从原文逐字取出（不手抄）；
  · 断言在写盘前（超限/缩水/关键值缺失 → 拒绝写入）；
  · 临时文件 + 回读校验 + 备份 + os.replace 原子替换。
"""
import io, os, hashlib, shutil, sys

MEM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   '.workbuddy', 'memory')
P = os.path.join(MEM, 'MEMORY.md')

raw = open(P, 'rb').read()
s = raw.decode('utf-8')
print('原 EOL: CRLF=%d  LF=%d  (应 CRLF=0)  字符=%d'
      % (raw.count(b'\r\n'), raw.count(b'\n'), len(s)))
assert raw.count(b'\r\n') == 0, 'MEMORY.md 不是纯 LF —— 中止'
L = s.split('\n')


def G(pref):
    h = [l for l in L if l.startswith(pref)]
    assert len(h) == 1, '前缀非唯一 %r -> %d' % (pref, len(h))
    return h[0]


def block(tag):
    i0 = next(i for i, l in enumerate(L) if l.startswith(tag))
    i1 = i0 + 1
    while i1 < len(L) and not L[i1].startswith('## '):
        i1 += 1
    out = L[i0:i1]
    while out and out[-1].strip() == '':
        out.pop()
    return out


# ---- 逐字取出保留行 ----
b = {}
for k, pref in [
    ('zero', '- 零点**6075000**'), ('grid', '- **行网格**'), ('cols', '- **列位**'),
    ('arccos', '- ★陷波中心'), ('iff', '- 🛑**判词类if'), ('c5', '- ⚠**列⑤=code'),
    ('bad', '- 🛑**读txt坏行**'), ('build', '- 🛑**构建＝EIDE'), ('quota', '- **置换制'),
    ('t3', '- 三条测试'), ('safe', '- 🛑**改脚本走'), ('calc', '- **算力**'),
    ('crit', '- ★★★**判据＝YY0885'), ('root', '- ★**三条根因**'),
    ('aim', '- ★★**达标两事**'), ('win', '- ★**滑窗驱动量**'),
    ('fr', '- ★★**频响↔输入阻抗**'), ('pole', '- ★★**读极点用相位'),
]:
    b[k] = G(pref)

# ---- 三处外科手术（含命中断言）----
def rep(cur, old, new, label):
    assert cur.count(old) == 1, '%s 未命中(%d): %s' % (label, cur.count(old), old)
    return cur.replace(old, new)

b['zero'] = rep(b['zero'], 'fs=**853.2423**。',
                'fs=**853.2423**；S1b伪迹阈**445633**code(`fast_restore_snippet.c:43`)。',
                'S1b 阈值 445633')
b['bad'] = rep(b['bad'], '列②24bit；⑤两位小数。',
               '列②24bit；⑤两位小数。★**两版ascii-1**：新①=`0`·⑦数值；旧①=`idxraw`·⑦**token**(`fcbd`)⇒勿假设①恒0·⑦数值。',
               '两版 ascii-1')
b['quota'] = rep(b['quota'], '配额§1=7·§4~7=1·§8=8·§9=5。',
                 '配额§1=7·§4~7=1·§8=3·§9=5·§11=4·§12=2。', '配额行')

# ---- 新 §12（dc_block 铁律摘要 + 指针；全文在 tools/T148~T156_*.md）----
dcb1 = ('- ★★**dc_block＝差分式**(常数⇒0)；τ=**7.96s**；脉冲 `D=−A·w·f/2`；统一式 '
        '**`D=−mean(x源)`**⇒正弦/三角/对称方波均值=0⇒**D=0**(⚠单极脉冲偏)。'
        '★唯一波形无关量＝**启动b₀**(ⓐ扰动＋ⓑ相位)⇒**时变fc优先**。')
dcb2 = ('- 🛑**`a1·τ≡A·T`⇒fc上限0.05306Hz**⇒唯一解＝时变fc(已落码T160)。'
        '板态=源码=`SKIP=8`＋`SETTLE=0`〔须全名〕。🛑扣D须**相位门控/回溯置位**'
        '(全时估计`bl/bls`无效)；🛑**新状态须START复位**；★偏移＝共模平移⇒**两点之差不变⇒判读不变**。'
        '明细→`tools/T148~T156_*.md`。')

NEW = []
NEW += ['# MEMORY.md — ADS1293 ECG 铁律',
        '> ads1293_demo｜设备事实→`DEVICE.md`｜结案→`SETTLED.md`'
        '｜基线偏移/dc_block 明细→`tools/T148~T156_*.md`', '']
NEW += ['## 1 口径'] + [b[k] for k in ('zero', 'grid', 'cols', 'arccos', 'iff', 'c5', 'bad')] + ['']
NEW += ['## 6 结构与维护'] + [b[k] for k in ('build', 'quota', 't3', 'safe')] + ['']
NEW += ['## 8 滤波链 · 算力 · 极点'] + [b[k] for k in ('calc', 'fr', 'pole')] + ['']
NEW += block('## 9 开关与输出通道') + ['']
NEW += block('## 10 上位机·协议') + ['']
NEW += ['## 11 判据 · 达标 · 根因'] + [b[k] for k in ('crit', 'root', 'aim', 'win')] + ['']
NEW += ['## 12 dc_block · 基线偏移', dcb1, dcb2, '']
out = '\n'.join(NEW)
if not out.endswith('\n'):
    out += '\n'

# ---- 断言（写盘前）----
assert len(out) <= 3000, '超限 %d' % (len(out) - 3000)
assert len(out) >= len(s) * 0.5, '缩水过多，拒写'
keys = ['6075000', '0.1122', '853.2423', '445633', '0.7353', '1.3455', '0.6895',
        '0.05306', '7.96', '2.1.5', '100µV', '0.3mV/s', '8.912', '24bit',
        'fcbd', 'idxraw', 'SKIP=8', 'SETTLE=0', '37.7', '4.85', 'T86', 'T84',
        'T58', 'T160', 'D=−mean', '相位门控', 'START复位', '共模平移']
miss = [k for k in keys if k not in out]
assert not miss, '关键值丢失: %s' % miss

# 分节占比 <40%
import re as _re
parts = _re.split(r'^(#{1,3} .*)$', out, flags=_re.M)
print('--- 新分节 ---')
tot = len(out)
for i in range(1, len(parts), 2):
    h = parts[i].strip()
    sz = len(parts[i]) + len(parts[i + 1])
    n = sum(1 for l in parts[i + 1].split('\n') if l.strip().startswith('-'))
    flag = '  <<< 超 40%!' if sz > tot * 0.4 else ''
    print('%5d 字符 %2d 条 %-28s %.0f%%%s' % (sz, n, h, 100.0 * sz / tot, flag))
    assert sz <= tot * 0.4, '单节超 40%: %s' % h

# ---- 原子写 ----
tmp = P + '.tmp_t192'
with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
    f.write(out)
assert io.open(tmp, encoding='utf-8').read() == out, '回读不一致'
shutil.copy2(P, P + '.bak_t192')
os.replace(tmp, P)
nb = open(P, 'rb').read()
print('MEMORY.md: %d -> %d 字符 | 字节 %d -> %d | md5 %s -> %s'
      % (len(s), len(out), len(raw), len(nb),
         hashlib.md5(raw).hexdigest()[:8], hashlib.md5(nb).hexdigest()[:8]))
print('余量 %d /3000' % (3000 - len(out)))
