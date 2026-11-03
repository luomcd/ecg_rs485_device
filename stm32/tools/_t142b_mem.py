# -*- coding: utf-8 -*-
"""T142b 记忆置换：MEMORY.md 腾位 + 写入 dc_block 机制与 T142b 落码

背景：MEMORY.md 2964/3000（余 36），需写入约 190 字符的新条目。
处置顺序（技能 memory-distill §4）：合并重复 → 压缩措辞 → 才轮到退役。
本脚本只用前两手：4 处压缩（内容已在 SETTLED.md，属零信息损失）＋ 1 处新增。

纪律：
  · 逐条**顺序**校验（后一处可能依赖前一处结果），命中数 != 1 立即 abort
  · 上限断言放在 **write 之前**（超限就别写，避免半压缩状态）
  · 行尾**二进制探测**继承（MEMORY.md = LF；🛑 别用 grep $'\\r$' 判，会误报）
"""
import io
import os
import hashlib
import sys

MEM = os.path.join('.workbuddy', 'memory', 'MEMORY.md')
CAP = 3000
BAKDIR = os.path.join('tools', '_bak_mem_20260928')
DRY = '--dry' in sys.argv

# (old, new, 标签)  —— 顺序敏感
REPS = [
    # ① §4·5·7 指针：明细全在 SETTLED，压成一行
    ('- 输入阻抗·工频 ⇒ `§S1/S3`；档位结构(节数/上角·`ECG_NUM_BP_SECTIONS`按档·宏名打错静默·ST40 `#error`已修) ⇒ `§S2`。',
     '- ⇒ `SETTLED §S1/S2/S3`（输入阻抗·工频·档位结构）。',
     '§4·5·7 指针压缩'),

    # ② §8 频响↔阻抗：具体数值(75.64/105.76/109.93)已在 SETTLED §S6，属重复
    ('；陷波恒在链首⇒**对OFF零代价**⇒`DUAL_A` OFF 与 D3 **逐点0差**(75.64%✓)，**A+ ON 105.76✓/109.93✓** ⇒ **✅T84 `A+B`＝默认档**。',
     '；陷波恒在链首⇒**对OFF零代价**⇒**✅T84 `A+B`＝默认档**。',
     '§8 频响条去重'),

    # ③ §8 达标两事：动机链指针可省（同段已有 §S7 引用）
    ('- ★★**达标两事**：①低端→**1阶**(✅T84 `A+B`)；②高端→LP2@250(**✅T58，⚠仅D档**)。★动机链(T83)见`§S7`。',
     '- ★★**达标两事**：①低端→**1阶**(`A+B`)；②高端→LP2@250(**✅T58，⚠仅D档**)。',
     '§8 达标两事压缩'),

    # ④ §8 相位条：删与本行紧邻重复的短语
    ('⇒fc **±3e-4Hz**(线谱比=真频响)。',
     '⇒fc **±3e-4Hz**。',
     '§8 相位条压缩'),

    # ⑤ 新增：dc_block 差分机制 + T142b 落码 + 行尾规则
    (' **37.7µV／6.4µV·s⁻¹／4.85%** ✅定版。\n',
     ' **37.7µV／6.4µV·s⁻¹／4.85%** ✅定版。\n'
     '- ★★**dc_block＝差分式**⇒**常数输入输出恒0**(基准≠偏移源)；'
     '偏移←**首帧异常**(START后SINC未建立**ts=5样本**)⇒✅T142b '
     '`ECG_DC_BLOCK_WARM_SKIP=8`(0=旧行为逐位等价)。'
     '`Filter/`**全LF**、`ads1293.c`**CRLF**。\n',
     '★ 新增 T142b 条'),
]


def detect_eol(b):
    return '\r\n' if b.count(b'\r\n') > 0 else '\n'


raw = io.open(MEM, 'rb').read()
eol = detect_eol(raw)
s = raw.decode('utf-8')
if eol == '\r\n':
    s = s.replace('\r\n', '\n')

print('=' * 78)
print('[T142b 记忆置换] %s%s' % (MEM, '  DRY-RUN' if DRY else '  真写盘'))
print('=' * 78)
print('行尾 = %s ｜ 改前 %d 字符 / %d 行 ｜ 上限 %d ｜ 余量 %d'
      % (eol, len(s), s.count('\n'), CAP, CAP - len(s)))
print('-' * 78)

# ---- 逐条顺序校验 + 应用（全部在内存里，写盘统一在最后）----
net = 0
for old, new, tag in REPS:
    c = s.count(old)
    if c != 1:
        print('  [ABORT] %-22s 命中 %d 次（要求 1）' % (tag, c))
        sys.exit(1)
    s = s.replace(old, new, 1)
    d = len(new) - len(old)
    net += d
    print('  %-22s %+6d 字符' % (tag, d))

print('-' * 78)
print('净增量 %+d ｜ 改后 %d 字符 ｜ 余量 %d' % (net, len(s), CAP - len(s)))
if len(s) > CAP:
    print('🛑 超限 %d 字符 —— 未写盘' % (len(s) - CAP))
    sys.exit(1)
if DRY:
    print('预演结束（未写盘）')
    sys.exit(0)

# ---- 备份（只加不改）----
os.makedirs(BAKDIR, exist_ok=True)
bak = os.path.join(BAKDIR, 'MEMORY.md.before_T142b')
io.open(bak, 'wb').write(raw)
print('备份 → %s' % bak)

# ---- 按原行尾二进制写回 ----
out = s.replace('\n', eol).encode('utf-8')
io.open(MEM, 'wb').write(out)

# ---- 复核 ----
chk = io.open(MEM, 'rb').read()
print('md5 %s → %s ｜ 写后 CR=%d LF=%d ｜ 字符=%d 行=%d'
      % (hashlib.md5(raw).hexdigest()[:12], hashlib.md5(chk).hexdigest()[:12],
         chk.count(b'\r'), chk.count(b'\n'), len(s), s.count('\n')))
t = chk.decode('utf-8')
for k in ('ECG_DC_BLOCK_WARM_SKIP', 'ts=5样本', '全LF', 'autocrlf', '差分式'):
    print('  关键 token %-24s %s' % (k, '✓' if k in t else '✗ 丢失'))
print('=' * 78)
