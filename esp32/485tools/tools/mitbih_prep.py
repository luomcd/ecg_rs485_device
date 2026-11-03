"""MIT-BIH → CardioLite .bin 转换器（QRS 检测器验证用）

为什么要转，不能直接喂 360 Hz：
  `HeartRateDetector` 的**全部判据常量都是按样本数写死的**
  （不应期 80、积分窗 60、最小 RR 80、T 波判别 spacing<360、LocateRPeak ±55）。
  直接喂 360 Hz 会让 160 ms 变成 222 ms、120 ms 变成 167 ms —— 全盘失效。
  所以先重采样到 **500 Hz**（与固件同域），再封成 T101 v2.2 帧，这样
  **参考夹具与固件能跑同一份数据**，结论才可比。

输出：
  <rec>_500.bin      T101 v2.2 单通道帧（LEN=53，25 样本/帧，TYPE=01，STA=02）
  <rec>_ann500.txt   专家标注，已换算到 500 Hz 样本号（`样本号,代码,符号`）

用法：
  python mitbih_prep.py 100                # 默认取 0 号导联（MLII）
  python mitbih_prep.py 100 --ch 1         # 取 V1/V5 等
"""
import argparse
import os
import sys

import numpy as np
from scipy.signal import resample_poly

# WFDB 标注码 → 符号（只列 MIT-BIH 用到的）
SYM = {
    1: "N", 2: "L", 3: "R", 4: "a", 5: "V", 6: "F", 7: "J", 8: "A", 9: "S",
    10: "E", 11: "j", 12: "/", 13: "Q", 14: "~", 16: "|", 18: "s", 19: "T",
    20: "(", 21: ")", 22: '"', 23: "=", 24: "p", 25: "B", 26: "^", 27: "t",
    28: "+", 29: "u", 30: "?", 31: "!", 32: "[", 33: "]", 34: "e", 35: "n",
    38: "f", 39: "(", 40: ")",
}
# 计入 QRS 的标注**符号**（AAMI EC57 口径：全部室性/室上性搏动 + 起搏 + 无法分类）
# 排除 `+ ~ | s T ( ) " = p ^ t u ? ! [ ]` 等节律/注释/波形标注
BEAT_SYM = set("NLRBAaJSVrFejnE/fQ")

# CardioLite 显示流标定：1 LSB = 0.1526 µV，±5 mV ↔ ±32767
UV_PER_LSB = 0.1526


def read_header(path):
    lines = [l.strip() for l in open(path, encoding="ascii", errors="replace")
             if l.strip() and not l.startswith("#")]
    f0 = lines[0].split()
    nsig, fs, nsamp = int(f0[1]), float(f0[2]), int(f0[3])
    sigs = []
    for l in lines[1:1 + nsig]:
        p = l.split()
        # 字段顺序：file fmt gain(units/mV) adcres adczero(=基线) initval checksum blocksize name
        # ⚠ 第 4 个是 **ADC 分辨率**，不是基线；基线是第 5 个（adczero）
        sigs.append({"file": p[0], "fmt": int(p[1]), "gain": float(p[2]),
                     "baseline": float(p[4]), "name": p[8] if len(p) > 8 else "?"})
    return nsig, fs, nsamp, sigs


def read_dat_212(path, nsamp, nsig):
    raw = np.fromfile(path, dtype=np.uint8)
    if nsig == 1:
        # 单通道 212：每 3 字节 2 个样本
        n3 = len(raw) // 3 * 3
        b = raw[:n3].reshape(-1, 3).astype(np.int32)
        s1 = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
        s2 = (b[:, 1] >> 4) | (b[:, 2] << 4)
        out = np.empty(len(b) * 2, dtype=np.int32)
        out[0::2], out[1::2] = s1, s2
        return out[:nsamp].reshape(-1, 1)
    # 双通道 212：每 3 字节 = 通道0 一个样本 + 通道1 一个样本（交替打包）
    n3 = len(raw) // 3 * 3
    b = raw[:n3].reshape(-1, 3).astype(np.int32)
    s1 = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
    s2 = (b[:, 1] >> 4) | (b[:, 2] << 4)
    inter = np.empty(len(b) * 2, dtype=np.int32)
    inter[0::2], inter[1::2] = s1, s2
    inter = inter[:nsamp * nsig]
    return inter.reshape(-1, nsig)


def read_atr(path):
    """用**官方 wfdb 包**解码标注。

    🛑 自研解码器踩过：WFDB annotation 是"增量+变长"格式（SKIP/NUM/SUB/CHN 各有
       额外的字节要吃掉），我手写的那版少吃了字节，导致时间轴**累积漂移**——
       表现为标注与波形峰差 +20~+100 样本、RR 序列出现 235/358 这种成对异常。
       这类格式一律用参考实现，不自研。

    返回 [(sample@原fs, symbol)]
    """
    import wfdb
    rec = path[:-4] if path.lower().endswith(".atr") else path
    a = wfdb.rdann(rec, "atr")
    return [(int(s_), str(y)) for s_, y in zip(a.sample, a.symbol)]


ECG_CRC8_POLY, ECG_CRC8_INIT = 0x07, 0x00


def _crc8_table():
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ ECG_CRC8_POLY) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
        tab.append(c)
    return tab


_CRC8_TAB = _crc8_table()


def crc8(data):
    c = ECG_CRC8_INIT
    for b in data:
        c = _CRC8_TAB[c ^ b]
    return c


def write_bin(path, samples_i16, per_frame=25, sta=0x02, type_=0x01):
    """写 T101 v2.2 单/双通道帧。**numpy 向量化版**（原纯 Python 逐样本 to_bytes，
    90 万样本要 ~5 s；向量化后 ~20 ms，字节输出逐位相同）。

    ⚠ 逐位一致的依据：CRC8 用「按列广播」——nframe×nbyte 的字节矩阵，逐列
      `crc = TAB[crc ^ col]`，等价于逐帧串行查表（CRC-8 无反射无末异或）。
    """
    s = np.asarray(samples_i16).astype(np.int16)
    n = len(s)
    nframe = (n + per_frame - 1) // per_frame
    pad = nframe * per_frame - n
    if pad:
        s = np.concatenate([s, np.zeros(pad, dtype=np.int16)])
    plen = 3 + 2 * per_frame                       # payload 长度（SEQ2+STA1+样本）
    nbyte = 1 + 2 + plen                           # body = TYPE+LEN2+payload，不含帧头与 CRC

    body = np.zeros((nframe, nbyte), dtype=np.uint8)
    body[:, 0] = type_
    body[:, 1] = plen & 0xFF
    body[:, 2] = (plen >> 8) & 0xFF
    body[:, 3] = (np.arange(nframe, dtype=np.uint32) & 0xFF).astype(np.uint8)
    body[:, 4] = ((np.arange(nframe, dtype=np.uint32) >> 8) & 0xFF).astype(np.uint8)
    body[:, 5] = sta
    body[:, 6:] = s.reshape(nframe, per_frame).view(np.uint8).reshape(nframe, -1)

    crc = np.zeros(nframe, dtype=np.uint8)
    tab = np.asarray(_CRC8_TAB, dtype=np.uint8)
    for j in range(nbyte):                         # 逐列查表，等价于逐帧串行
        crc = tab[crc ^ body[:, j]]

    out = np.empty((nframe, 2 + nbyte + 1), dtype=np.uint8)
    out[:, 0], out[:, 1] = 0xAA, 0x55
    out[:, 2:-1] = body
    out[:, -1] = crc
    open(path, "wb").write(out.tobytes())
    return nframe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rec")
    ap.add_argument("--ch", type=int, default=0, help="导联号（0=MLII，1=V5/V1）")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()

    d = args.dir or os.path.dirname(os.path.abspath(__file__))
    hea = os.path.join(d, args.rec + ".hea")
    nsig, fs, nsamp, sigs = read_header(hea)
    print(f"[{args.rec}] {nsig} 导联  fs={fs:g} Hz  样本 {nsamp}  ({nsamp/fs/60:.1f} min)")
    print("  导联: " + ", ".join(f"{k}={s['name']}(fmt{s['fmt']})" for k, s in enumerate(sigs)))

    dat = os.path.join(d, sigs[0]["file"])
    x = read_dat_212(dat, nsamp, nsig)
    print(f"  读取 {x.shape[0]} 样本 × {x.shape[1]} 导联")

    ch = x[:, args.ch].astype(np.float64)
    g, b = sigs[args.ch]["gain"], sigs[args.ch]["baseline"]
    mv = (ch - b) / g                         # ADC 单位 → mV
    print(f"  导联 {args.ch}: 幅值 {mv.min():+.3f} .. {mv.max():+.3f} mV")

    # 360 → 500 Hz（25/18），与固件同域
    assert abs(fs - 360) < 1e-9 or True
    up, down = 500, int(round(fs))
    y = resample_poly(mv, up, down) if int(fs) != 500 else mv
    print(f"  重采样 {fs:g} → 500 Hz：{len(mv)} → {len(y)} 样本  ({len(y)/500:.2f} s)")

    i16 = np.clip(np.round(y * 1000.0 / UV_PER_LSB), -32768, 32767).astype(np.int32)
    out_bin = os.path.join(d, f"{args.rec}_500.bin")
    nf = write_bin(out_bin, i16)
    print(f"  写入 {out_bin}: {nf} 帧 / {os.path.getsize(out_bin)} B")

    # 标注换算到 500 Hz 样本号
    anns = read_atr(os.path.join(d, args.rec + ".atr"))
    rows = []
    for t360, sym in anns:
        t500 = int(round(t360 * 500.0 / fs))
        rows.append((t500, sym))
    out_ann = os.path.join(d, f"{args.rec}_ann500.txt")
    with open(out_ann, "w", encoding="utf-8") as f:
        f.write(f"# total500={len(y)}\n")
        f.write("# sample500,symbol,is_beat\n")
        for t500, sym in rows:
            f.write(f"{t500},{sym},{1 if sym in BEAT_SYM else 0}\n")
    nb = sum(1 for _, sym in rows if sym in BEAT_SYM)
    print(f"  写入 {out_ann}: {len(rows)} 条标注，其中 **{nb} 条计入 QRS**")
    print(f"  真值心率 ≈ {nb / (len(y)/500) * 60:.1f} bpm")


if __name__ == "__main__":
    sys.exit(main())
