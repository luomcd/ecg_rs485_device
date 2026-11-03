"""expected_regs.json 全仓体检 —— 哪份是「源」、哪份在「真跑」、有没有漂移。

起因（2026-09-24 · T141b 上板复测 C3）：猛哥问「修改的 json 文件的路径是哪一个？有两个」。
根因 ＝ .NET 的经典坑：工程里一份**源**，exe 同目录一份**构建副本**；程序运行时读的是后者。

    源   : WinApp_ADSECG/ADS1293_Ecg/Config/expected_regs.json              ← 人改这份
    产物 : WinApp_ADSECG/ADS1293_Ecg/bin/<cfg>/net10.0-windows/Config/...  ← 程序读这份

依据（两处，均可 `grep` 复核）:
    Config/ConfigValidator.cs:197-199
        public const string DefaultRelativePath = @"Config\\expected_regs.json";
        public static string DefaultPath => Path.Combine(AppContext.BaseDirectory, DefaultRelativePath);
    ADS1293_Ecg.csproj:17-19
        <None Update="Config\\expected_regs.json">
          <CopyToOutputDirectory>PreserveNewest</CopyToOutputDirectory>

🛑 纪律: 只改**源** + **重新生成**; 绝不手改 bin 下那份（下次生成按时间戳 `PreserveNewest`
   覆盖, 你看到的行为与"改了没生效"完全一样 —— 那正是本工具要一次说清的事）。

用法（纯只读）:
    python tools/_json_where.py                  # 退出码 0 = 源与产物一致; 1 = 有漂移
    python tools/_json_where.py --root C:\\work2026\\VsPro
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

DEFAULT_ROOT = r'C:\work2026\VsPro'
FNAME = 'expected_regs.json'
SRC_REL = os.path.join('ADS1293_Ecg', 'Config', FNAME)
BIN_RE = re.compile(r'ADS1293_Ecg[\\/]bin[\\/][^\\/]+[\\/]net[0-9.]+-windows[\\/]Config[\\/]expected_regs\.json$', re.I)


def md5(b):
    return hashlib.md5(b).hexdigest()


def mtime(p):
    return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%m-%d %H:%M')


def probe(p):
    """返回 (size, md5, mtime, version, n_writable, n_readonly, err)。"""
    try:
        b = open(p, 'rb').read()
    except OSError as e:
        return 0, '-', '-', '?', 0, 0, str(e)
    try:
        d = json.loads(b.decode('utf-8-sig'))
        v = str(d.get('version', '?'))
        w = len(d.get('writable', []))
        r = len(d.get('readonly_expect', []))
        err = ''
    except Exception as e:                                    # noqa: BLE001
        v, w, r, err = '?', 0, 0, 'json 解析失败: %s' % e
    return len(b), md5(b), mtime(p), v, w, r, err


def role(p, src_abs):
    a = os.path.abspath(p)
    if a == os.path.abspath(src_abs):
        return '★源（人改这份）'
    if BIN_RE.search(a.replace('/', '\\')):
        return '运行时读取（exe 同目录副本）'
    return '自检工程拷贝（与主程序无关）'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=DEFAULT_ROOT)
    a = ap.parse_args()

    src_abs = os.path.join(a.root, 'WinApp_ADSECG', SRC_REL)
    if not os.path.exists(src_abs):
        sys.exit('!! 找不到源 json: %s' % src_abs)

    src = probe(src_abs)
    src_md5 = src[1]

    rows = []
    for dp, dn, fn in os.walk(a.root):
        if os.sep + 'obj' + os.sep in dp + os.sep:
            continue
        for f in fn:
            if f.lower() == FNAME:
                p = os.path.join(dp, f)
                rows.append((role(p, src_abs), p, probe(p)))

    order = {'★源（人改这份）': 0, '运行时读取（exe 同目录副本）': 1, '自检工程拷贝（与主程序无关）': 2}
    rows.sort(key=lambda r: (order.get(r[0], 9), r[1]))

    print('=' * 118)
    print('expected_regs.json 全仓体检    root = %s' % a.root)
    print('=' * 118)
    print('%-30s %-6s %-12s %-9s %-8s %-9s %s' % ('角色', 'ver', 'w/r 条数', 'md5', '大小', 'mtime', '路径'))
    print('-' * 118)
    for r, p, (size, m, mt, ver, w, rr, err) in rows:
        rel = os.path.relpath(p, a.root)
        mark = '' if m == src_md5 else '  ⚠与源不一致'
        print('%-30s %-6s %-12s %-9s %-8s %-9s %s%s'
              % (r, ver, '%d/%d' % (w, rr), m[:8], size, mt, rel, mark if r != '★源（人改这份）' else ''))
        if err:
            print('%-30s   !! %s' % ('', err))

    print('-' * 118)
    src_size, _, src_mt, src_ver, src_w, src_r, _ = src
    print('源        : %s' % src_abs)
    print('            v%s · writable %d 条 · readonly_expect %d 条 · %d B · %s · md5 %s'
          % (src_ver, src_w, src_r, src_size, src_mt, src_md5[:8]))

    live = [r for r in rows if r[0].startswith('运行时')]
    if not live:
        print('运行时    : ⚠ 尚未生成（bin 下无副本）⇒ 程序还跑不起来，先「重新生成」')
        return 1

    drift = False
    for _, p, pr in live:
        same = pr[1] == src_md5
        drift = drift or not same
        print('运行时    : %s' % os.path.relpath(p, a.root))
        print('            %s（源 md5 %s vs 产物 md5 %s）'
              % ('✅ 与源逐字节一致' if same else '🛑 与源不一致 ⇒ 你现在跑的是旧表！',
                 src_md5[:8], pr[1][:8]))

    print()
    if drift:
        print('==> 🛑 有漂移：手改的源没进 bin。请「重新生成」后再测（不要手改 bin 下那份）。')
        return 1
    print('==> ✅ 一致：程序实际加载的就是源表 v%s。改 json ⇒ 改上面那份「源」，然后**重新生成**。' % src_ver)
    return 0


if __name__ == '__main__':
    sys.exit(main())
