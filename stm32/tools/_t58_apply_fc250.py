# -*- coding: utf-8 -*-
"""
_t58_apply_fc250.py —— 把 fc=250 (方案 B) 落到 Filter/ecg_filter.c

改动（仅五档 D1/D2/D3/IMP620/IMP620_N2；ST40 上角 40 Hz 不动）
----------------------------------------------------------------
  每档的 BP 由 4 节 → 3 节：
    删  旧 LP4@150 的 2 节（sec0/sec1）
    插  新 LP2@250 的 1 节
    留  旧 HP4@0.05 的 2 节（sec2/sec3，字面量原样不动）
  ⇒ 总行数 -10（f32 每档 -1，f64 每档 -2），ECG_NUM_SECTIONS 各档 -1。

安全
----
- 每类替换都**计数自检**，数量不符即报错退出（不写盘）。
- ST40 的 BP（0.05~40 Hz）字面量与本模板不同 ⇒ 天然不会被误伤（脚本再显式校验）。
- 换行风格自动检测保持。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')

NEW_F32 = "    { 0.37672776f, 0.75345552f, 0.37672776f, 0.317465425f, 0.18944566f },"
NEW_F64 = ("    { 0.37672776742306063, 0.75345553484612127, 0.37672776742306063,\n"
           "      0.31746541217881613, 0.18944565751342665 },")

NOTE_BP = ("    /* ★T58(2026-09-20)：BP 上角 150→250 Hz ＋ LP 4 阶→2 阶。本节＝LP2@250；\n"
           "       其后 2 节＝HP4@0.05（原样保留，本轮不动） */")
NOTE_SEC0 = "/* LP2@250 (T58 fc 150→250) */"

OLD_F32 = ("    { 0.030899521f,  0.0617990419f, 0.030899521f, -0.493050277f, 0.0958790183f },\n"
           "    { 1.0f,          2.0f,          1.0f,         -0.670181155f, 0.490696579f  },\n")
OLD_F64_NUM = ("    { 0.030899520236696225, 0.06179904047339245, 0.030899520236696225,\n"
               "      -0.49305028725906408, 0.095879018845577341 }")
NEW_F64_NUM = ("    { 0.37672776742306063, 0.75345553484612127, 0.37672776742306063,\n"
               "      0.31746541217881613, 0.18944565751342665 }")
RE_SEC1 = re.compile(
    r"    \{ 1, 2, 1, -0\.67018117413271905, 0\.49069657728421018 \},[ \t]*"
    r"/\* [^\n]*sec1[^\n]*\*/\n")
RE_NOTE32 = re.compile(r"/\* 第 \d+~\d+ 节：0\.05~150 Hz Butterworth 带通（[^\n]*?\*/")
RE_NOTE64 = re.compile(r"/\* BP [^\n]*sec0 \(0\.05-150\) \*/")

# ST40 的 BP 特征（必须保持不变，用于反证没被误伤）
ST40_MARKS = ["0.0003275869f", "0.05~40 Hz Butterworth",
              "-1.5101440540600901", "0.05-40 v3"]


def main():
    raw = io.open(P, 'rb').read()
    crlf = b'\r\n' in raw
    s = raw.decode('utf-8')
    if crlf:
        s = s.replace('\r\n', '\n')
    n0 = s.count('\n')

    n_f32 = s.count(OLD_F32)
    s = s.replace(OLD_F32, NEW_F32 + "\n")

    n_f64 = s.count(OLD_F64_NUM)
    s = s.replace(OLD_F64_NUM, NEW_F64_NUM)

    n_sec1 = len(RE_SEC1.findall(s))
    s = RE_SEC1.sub("", s)

    n_note32 = len(RE_NOTE32.findall(s))
    s = RE_NOTE32.sub(NOTE_BP, s)

    n_note64 = len(RE_NOTE64.findall(s))
    s = RE_NOTE64.sub(NOTE_SEC0, s)

    print("替换计数：")
    for lab, n in (("f32 LP4 两行", n_f32), ("f64 LP4 sec0 数值块", n_f64),
                   ("f64 sec1 整行", n_sec1), ("f32 BP 注释", n_note32),
                   ("f64 sec0 注释", n_note64)):
        flag = "OK" if n == 5 else "!! 期望 5"
        print("  %-22s %d  %s" % (lab, n, flag))
    if not (n_f32 == n_f64 == n_sec1 == n_note32 == n_note64 == 5):
        print("⇒ 计数不符，**未写盘**，请检查模板")
        return 1

    for mk in ST40_MARKS:
        if mk not in s:
            print("⇒ ST40 标记丢失（%s），**未写盘**" % mk)
            return 1
    print("  ST40 标记完好 ✓")

    n1 = s.count('\n')
    # 行数推导：f32 每档 2→1 行 = -1；f64 sec1 整行删除 = -1；f32 注释 1→2 行 = +1
    #           f64 sec0 数值块 2→2 行 = 0；f64 注释 1→1 行 = 0
    #           ⇒ 每档净 -1，五档共 -5
    exp = -5
    print("行数：%d → %d（Δ%+d，期望 %+d）" % (n0, n1, n1 - n0, exp))
    if n1 - n0 != exp:
        print("⇒ 行数变动不符，**未写盘**")
        return 1

    if crlf:
        s = s.replace('\n', '\r\n')
    io.open(P, 'wb').write(s.encode('utf-8'))
    print("⇒ 已写入 %s（换行风格 %s）" % (os.path.relpath(P), "CRLF" if crlf else "LF"))
    return 0


if __name__ == '__main__':
    sys.exit(main())
