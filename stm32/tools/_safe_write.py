# -*- coding: utf-8 -*-
"""_safe_write.py —— 程序化改写文件的安全封装（2026-09-28 事故后新增）。

事故经过：一次 `io.open(p, 'w')` 就地改写 `tools/_t146_consistency.py`（约 2360 行、未被 git 跟踪、
无任何备份）时，写入阶段抛 UnicodeEncodeError。**`open(p,'w')` 会先截断文件**，于是 2360 行的
分析工具被清成 0 字节，无法恢复。

用法：
    from _safe_write import safe_write
    safe_write(path, text, check_python=True)

约定（🛑 必守）：
  1. **绝不 `open(path, 'w')`**：一律写同目录临时文件 → 校验 → `os.replace` 原子替换；
  2. 替换前把原文件复制到 `<目标目录>/_t144/_bak/<name>_<时间戳>.<ext>`
     （**该目录不存在时退回 `<目标目录>/_bak/`** —— 例 `tools/_t169/x.py` ⇒ `tools/_t169/_bak/`；保留最后 N 份，默认 10）；
  3. `.py` 目标做 **py_compile 语法自检**，失败则**不替换**并抛异常；
  4. 新内容若明显比旧内容短（默认 < 50%）⇒ **拒绝**并要人确认（防御"写一半"）。

⚠ 本封装只能防"写坏"，防不了"想错"。改写别人的分析脚本前，先问自己：
   **这条改写真的需要脚本来做吗？** 逐段 Edit/Write 更慢但可审。
"""
import io
import os
import py_compile
import shutil
import time

KEEP_BAK = 10
MIN_RATIO = 0.5


def _bak_dir(path):
    d = os.path.join(os.path.dirname(os.path.abspath(path)), '_t144', '_bak')
    if not os.path.isdir(os.path.dirname(d)):
        d = os.path.join(os.path.dirname(os.path.abspath(path)), '_bak')
    os.makedirs(d, exist_ok=True)
    return d


def safe_write(path, text, check_python=True, allow_shrink=False):
    path = os.path.abspath(path)
    old_size = os.path.getsize(path) if os.path.exists(path) else 0

    new_bytes = text.encode('utf-8')
    if (old_size > 0 and not allow_shrink
            and len(new_bytes) < old_size * MIN_RATIO):
        raise RuntimeError('🛑 新内容只有旧的 %.0f%%（%d → %d 字节），疑似"写一半"，已拒绝。'
                           '确实要大幅缩短请显式 allow_shrink=True'
                           % (100.0 * len(new_bytes) / old_size, old_size, len(new_bytes)))

    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(text)

    if check_python and path.endswith('.py'):
        try:
            py_compile.compile(tmp, doraise=True)
        except Exception as e:
            os.remove(tmp)
            raise RuntimeError('🛑 语法自检失败，**未替换**原文件：%s' % e)

    if old_size > 0:
        bd = _bak_dir(path)
        name = os.path.basename(path)
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        shutil.copy2(path, os.path.join(bd, '%s_%s' % (os.path.splitext(name)[0], stamp)
                                        + os.path.splitext(name)[1]))
        olds = sorted(f for f in os.listdir(bd) if f.startswith(os.path.splitext(name)[0]))
        for f in olds[:-KEEP_BAK]:
            os.remove(os.path.join(bd, f))

    os.replace(tmp, path)
    return True


if __name__ == '__main__':
    print(__doc__)
