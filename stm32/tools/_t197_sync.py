# -*- coding: utf-8 -*-
"""_t197_sync.py -- 「单步测试」包 ↔ 真源 同步（T197；2026-10-15 由 _tmp_t197_copy.py 转正）

背景：``CodexWKspace\\ads1293_filter\\单步测试\\Sx_*\\`` 是**自包含复盘包**（学生作业：
``复盘.md`` ＋ ``scripts/`` ＋ ``data/`` ＋ ``_sx_run.ps1``）。其中脚本来自真源
``tools/`` 与 ``tools/_t169/``，但**不是裸 cp** —— 必须按「[单步测试适配]」范式改写
路径解析（包内无 ``Filter/`` 源码；依赖与主脚本同放 ``scripts/``）。

与 ``_mirror_sync.py`` 的分工（两者产物规则不同，别混用）：

* ``_mirror_sync.py`` —— 纯**文档逐字节镜像**（``PAIRS`` 里只有 T194 一对）；
* 本工具            —— **脚本（适配改写）＋ data（原样拷贝）**。

用法::

    python tools/_t197_sync.py --check      # 只体检（exit 1 = 有缺口）
    python tools/_t197_sync.py --push       # 源 -> 包（目标先备份到 _bak_t197_sync/）

判据（--check）：

1. **脚本**：把真源文件按改写规则**在内存里渲染**，md5 应等于包内实际文件 —— 否则报「滞后」；
2. **data**：包内应与源逐字节相同 —— 否则报「滞后 / 缺失」；
3. **留痕** ``_s2*_out.txt``：包内**既有**的 3 份由 ``_s2_run.ps1`` 重跑生成（CRLF）；
   其余按 ``TRACES`` 清单从真源拷**运算结果副本**（落包转 CRLF，与既有形态对齐）。
   只统计缺口，**不作硬判据**（重跑会覆盖，数字不必与真源相同）；
4. ``_s2_run.ps1`` / ``复盘.md``：人工维护项，只报存在性，不判内容。

纪律：源侧一律 LF（含 CRLF 直接判失败）；改写后走 ``tmp + os.replace`` 原子替换；
目标先备份；**缩水拒写**（新内容 < 旧长 50 % 拒绝）。
"""
import argparse
import hashlib
import os
import shutil
import sys
from datetime import datetime

SRC_T169 = r'C:\work2026\VsPro\ads1293_demo\tools\_t169'
SRC_TOOLS = r'C:\work2026\VsPro\ads1293_demo\tools'
REC = r'C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\bin\Debug\net10.0-windows\recordings'
PKG = r'C:\work2026\CodexWKspace\ads1293_filter\单步测试\S2_一阶高通dc_block复刻'
DST_SCRIPTS = os.path.join(PKG, 'scripts')
DST_DATA = os.path.join(PKG, 'data')

BAK_ROOT = r'C:\work2026\VsPro\ads1293_demo\tools\_bak_t197_sync'

# ---- 「[单步测试适配]」改写素材 ------------------------------------------------
NOTE_TOOLS = '   # [单步测试适配] 依赖与本脚本同放 scripts/（原取 tools/ 上级目录）'
NOTE_LOOP = '# [单步测试适配] 依赖与本脚本同放 scripts/（原为 os.path.dirname(_HERE) 取 tools/ 上级目录）'
NOTE_ROOT1 = '# 🛑 [单步测试适配] 本包内无 Filter/ 源码 ⇒「从源码解析 16 节系数」这一支在包内不可用；'
NOTE_ROOT2 = '#    归档目的是保留复核方法；需重跑请回工程根 tools/_t169/ 执行本脚本。'

K_TOOLS = b'_TOOLS = os.path.dirname(_HERE)'
K_LOOP = b'for _p in (os.path.dirname(_HERE), _HERE):'
K_ROOT = b'_ROOT = os.path.dirname(os.path.dirname(_HERE))'

# ---- 待同步清单 --------------------------------------------------------------
# (源目录, 文件名, 改写模式)   模式: tools_dirname / loop_dirname / loop_only / none
JOBS = [
    (SRC_T169, '_s2d_fit2exp.py', 'tools_dirname'),
    (SRC_T169, '_s2e_raw_tau.py', 'tools_dirname'),
    (SRC_T169, '_s2f_blank_scan.py', 'none'),
    (SRC_T169, '_s2g_replicate_152232.py', 'loop_dirname'),
    (SRC_T169, '_s2e_baseline_noise.py', 'loop_only'),
    (SRC_TOOLS, '_safe_write.py', 'none'),
    (SRC_TOOLS, '_t51_yy0885_pulse.py', 'none'),
]

# 原样拷贝项（源 = WinApp recordings/，目标 = 包内 data/）
DATA = [
    'ecg_20261014_152232_SOP-0.15mv.txt',
    'ecg_20261014_152232_SOP-0.15mv.meta.json',
    'ecg_20261014_161448_IN1_IN2.txt',
    'ecg_20261014_161448_IN1_IN2.meta.json',
]

# 留痕副本（真源 tools/_t169/ -> 包内 scripts/；源为 LF，落包转 CRLF 对齐既有形态）。
# 说明：包内既有 3 份（_t142b_offline_out / _s2_pole_out / _s2c_qrs_start_out）由
#       _s2_run.ps1 重跑生成；本清单是**真源运算结果副本**，用于补齐包内空缺。
TRACES = [
    '_s2d_fit2exp_out.txt',
    '_s2e_baseline_noise_161448_out.txt',
    '_s2e_caliber_152232_out.txt',
    '_s2e_decim_skip_1522_out.txt',
    '_s2e_nullcal_152232_out.txt',
    '_s2e_raw_tau_out.txt',
    '_s2e_raw_tau_ref_out.txt',
    '_s2e_sop1336_out.txt',
    '_s2e_sop1522_out.txt',
    '_s2e_sop_1014_out.txt',
    '_s2e_stim_skip_1522_out.txt',
    '_s2f_blank_1336_out.txt',
    '_s2f_blank_1522_out.txt',
    '_s2f_blank_scan_out.txt',
    '_s2g_replicate_152232_out.txt',
]

# 人工维护项（只报存在性）
MANUAL = ['_s2_run.ps1', 'S2_一阶高通dc_block复刻_复盘.md']

NEED = {'tools_dirname': ('tools', 1), 'loop_dirname': ('loop', 1),
        'loop_only': ('loop', 1), 'none': (None, 0)}


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def render(srcdir, name, mode):
    """把真源脚本按 [单步测试适配] 规则渲染成包内版；返回 bytes。命中数不自洽即判失败。"""
    src = os.path.join(srcdir, name)
    if not os.path.isfile(src):
        sys.exit('!! 源不存在: %s' % src)
    with open(src, 'rb') as f:
        raw = f.read()
    if raw.count(b'\r\n'):
        sys.exit('!! %s 源含 CRLF（%d 处），异常' % (name, raw.count(b'\r\n')))
    out = []
    hits = {'tools': 0, 'loop': 0, 'root': 0}
    for ln in raw.split(b'\n'):
        if mode == 'tools_dirname' and ln.startswith(K_TOOLS):
            out.append(('_TOOLS = _HERE' + NOTE_TOOLS).encode('utf-8'))
            hits['tools'] += 1
            continue
        if mode in ('loop_dirname', 'loop_only') and ln.startswith(K_LOOP):
            out.append(NOTE_LOOP.encode('utf-8'))
            out.append(b'for _p in (_HERE,):')
            hits['loop'] += 1
            continue
        if mode == 'loop_dirname' and ln.startswith(K_ROOT):
            out.append(ln)
            out.append(NOTE_ROOT1.encode('utf-8'))
            out.append(NOTE_ROOT2.encode('utf-8'))
            hits['root'] += 1
            continue
        out.append(ln)
    k, n = NEED[mode]
    if k is not None and hits[k] != n:
        sys.exit('!! %s: %s 命中 %d 次（期望 %d）' % (name, k, hits[k], n))
    if mode == 'loop_dirname' and hits['root'] != 1:
        sys.exit('!! %s: _ROOT 命中 %d 次（期望 1）' % (name, hits['root']))
    return b'\n'.join(out)


def backup(dst):
    stamp = datetime.now().strftime('%Y%m%d')
    bak = os.path.join(BAK_ROOT, stamp)
    os.makedirs(bak, exist_ok=True)
    target = os.path.join(bak, os.path.basename(dst))
    shutil.copy2(dst, target)
    return target


def place(new, dst, name):
    """落盘（**幂等**：内容已一致则跳过）。目标已存在则先备份；缩水拒写。返回 (字节数, 是否写入)。"""
    if os.path.isfile(dst):
        with open(dst, 'rb') as f:
            if f.read() == new:
                return len(new), False
        old_len = os.path.getsize(dst)
        if len(new) < old_len * 0.5:            # 缩水拒写
            sys.exit('!! %s: 新内容 %d B < 旧长一半 %d B，拒写' % (name, len(new), old_len))
        print('   备份 %s' % backup(dst))
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = dst + '.tmp_t197'
    with open(tmp, 'wb') as f:
        f.write(new)
    os.replace(tmp, dst)
    return len(new), True


def push_one(srcdir, name, mode):
    return place(render(srcdir, name, mode), os.path.join(DST_SCRIPTS, name), name)


def check_scripts():
    bad = 0
    for srcdir, name, mode in JOBS:
        want = md5_bytes(render(srcdir, name, mode))
        dst = os.path.join(DST_SCRIPTS, name)
        if not os.path.isfile(dst):
            print('   ✗ %-30s 包内缺失' % name)
            bad += 1
            continue
        got = md5_file(dst)
        if want == got:
            print('   ✓ %-30s 一致' % name)
        else:
            print('   ✗ %-30s 滞后（源渲染 %s / 包内 %s）' % (name, want[:8], got[:8]))
            bad += 1
    return bad


def check_data():
    bad = 0
    for name in DATA:
        src = os.path.join(REC, name)
        dst = os.path.join(DST_DATA, name)
        if not os.path.isfile(src):
            print('   ! %-46s 源不存在（跳过）' % name)
            continue
        if not os.path.isfile(dst):
            print('   ✗ %-46s 包内缺失' % name)
            bad += 1
            continue
        if md5_file(src) == md5_file(dst):
            print('   ✓ %-46s 一致' % name)
        else:
            print('   ✗ %-46s 不一致' % name)
            bad += 1
    return bad


def hint_traces():
    src = sorted(n for n in os.listdir(SRC_T169)
                 if n.startswith('_s2') and n.endswith('_out.txt'))
    dst = set(n for n in os.listdir(DST_SCRIPTS) if n.endswith('_out.txt'))
    miss = [n for n in src if n not in dst]
    print('   真源留痕 %d 份 / 包内 %d 份 ⇒ 缺 %d 份（应齐 TRACES 清单；数字不必与真源相同）'
          % (len(src), len(dst), len(miss)))
    for n in miss:
        print('      · %s' % n)
    return 0


def check_manual():
    for name in MANUAL:
        print('   %s %s' % ('✓' if os.path.isfile(os.path.join(PKG, name)) else '✗',
                            name))


def main():
    ap = argparse.ArgumentParser(description='单步测试包 ↔ 真源 同步（判等 / 推平）')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true', help='只体检')
    g.add_argument('--push', action='store_true', help='源 -> 包（目标先备份）')
    args = ap.parse_args()

    print('== 包 %s' % PKG)
    if args.push:
        print('-- 脚本（适配改写）')
        for srcdir, name, mode in JOBS:
            n, wrote = push_one(srcdir, name, mode)
            print('  %s %-30s %7d B' % ('✓' if wrote else '=', name, n))
        print('-- data（原样拷贝）')
        for name in DATA:
            src = os.path.join(REC, name)
            if not os.path.isfile(src):
                print('  ! %-46s 源不存在（跳过）' % name)
                continue
            with open(src, 'rb') as f:
                n, wrote = place(f.read(), os.path.join(DST_DATA, name), name)
            print('  %s %-46s %7d B' % ('✓' if wrote else '=', name, n))
        print('-- 留痕（真源副本，LF -> CRLF）')
        for name in TRACES:
            src = os.path.join(SRC_T169, name)
            if not os.path.isfile(src):
                print('  ! %-42s 源不存在（跳过）' % name)
                continue
            with open(src, 'rb') as f:
                raw = f.read()
            if raw.count(b'\r\n'):
                sys.exit('!! %s 源含 CRLF，异常' % name)
            n, wrote = place(raw.replace(b'\n', b'\r\n'),
                             os.path.join(DST_SCRIPTS, name), name)
            print('  %s %-42s %7d B' % ('✓' if wrote else '=', name, n))

    bad = 0
    print('-- 体检 · 脚本')
    bad += check_scripts()
    print('-- 体检 · data')
    bad += check_data()
    print('-- 提示 · 留痕')
    hint_traces()
    print('-- 人工维护项')
    check_manual()

    head = '推送后' if args.push else ''
    print('\n结论：%s' % ('全部一致 OK' if bad == 0 else '%s有 %d 项不一致' % (head, bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
