# -*- coding: utf-8 -*-
"""
_t70_fr_measure.py —— T70 频响实测判读（GB 9706.225-2021 表201.107）

🛑 为什么不用 p-p（T41 §5.2 定案，必读）
    100~150 Hz 行信号只有 **0.25 mVpp**，而该段宽带残留 ≈ 20 µV rms
    ⇒ 1 s 窗 p-p 被噪声抬 ≈ 2×3σ ≈ **+120 µV（+48%）** ⇒ **直接吃掉 ±10% 容差**。
    ⇒ 本任务判据 = **基波幅度**（单频 DFT / 正弦最小二乘），p-p 只作交叉验证。

🛑 三条必须钉死的口径（T41 §1.1）
    ① **按行归一化**：四行输入幅度不同（1 / 0.5 / 0.25 / 0.5 mVpp）
       ⇒ 归一化分母 = `A(f)/Amp_in(f)`，分子 = `A(10Hz)/Amp_in(10Hz)`
       （**不是**直接 A(f)/A(10Hz)——那会把输入幅度差算进频响里）
    ② **参考点 10 Hz**（1 mVpp 行内）
    ③ notch **必须 OFF**（D3 的 100 Hz Q15 陷波会打死 100 Hz 点 ⇒ 假失败）

采样域与列
    fs = 853.2423 Hz（`raw853 on`）。此模式下**列③/④恒 0** ⇒ 读 **列⑤**（滤波后 code）。
    归一化是比值 ⇒ 列⑤ 的 code 量纲不影响判据；但若要报绝对值需 ×0.1122 → µV。

用法
    # 文件名自带频率与幅度：fr-150Hz-0.25mVpp-2026-09-21 1030.txt
    python _t70_fr_measure.py "fr-*.txt"
    # 文件名不带幅度时用 --amp 统一下发
    python _t70_fr_measure.py "fr-*.txt" --amp 1.0
    # 指定列与丢弃秒数
    python _t70_fr_measure.py "fr-*.txt" --col 5 --burn 2.0
输出
    _t70_out.txt ＋ T70_频响实测判读_2026-09-21.png
"""
import argparse
import glob
import io
import os
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import _t69_fr_forecast as T69          # 复用系数解析 / AFE 模型 / 判限表

HERE = os.path.dirname(os.path.abspath(__file__))
FS = T69.FS
UVPC = 0.1122                            # code → µV（列⑤ 为 code 域）
BANDS = T69.BANDS
REF = T69.REF
Y_AMP = T69.Y_AMP


# ------------------------------------------------------------------ 基波幅度
def tone_amp(x, f0, fs=FS, win="hann"):
    """单频幅度（Hann 窗 DFT，幅度按窗增益归一）。对非整数周期不敏感。"""
    x = np.asarray(x, float)
    n = np.arange(len(x))
    w = np.hanning(len(x)) if win == "hann" else np.ones(len(x))
    ph = np.exp(-2j * np.pi * f0 * n / fs)
    return 2.0 * abs(np.sum(x * w * ph)) / np.sum(w)


def tone_amp_ls(x, f0, fs=FS):
    """正弦最小二乘拟合（交叉验证用，不需窗）。"""
    x = np.asarray(x, float)
    n = np.arange(len(x))
    c = np.cos(2 * np.pi * f0 * n / fs)
    s = np.sin(2 * np.pi * f0 * n / fs)
    M = np.vstack([c, s]).T
    coef = np.linalg.lstsq(M, x, rcond=None)[0]
    return float(np.hypot(*coef))


def pp(x):
    x = np.asarray(x, float)
    return float(x.max() - x.min())


# ------------------------------------------------------------------ 读 txt
def _f7(t):
    """第⑦列 gdtover：**固件以 %x 输出（十六进制）**；但合成/历史数据可能给十进制或浮点。
    ⇒ 先试 hex，失败退回 float（两种都收，绝不因该列丢行）。
    教训：整行 float() 会在 gdtover 长到含 a~f 时静默丢行（A+ 档 dtcycles>12000 ⇒ 每样本 ++ 即触发，
    实测丢掉 71% 的行，且是系统性抽稀 ⇒ 统计失真）。"""
    try:
        return float(int(t, 16))
    except ValueError:
        return float(t)


def load_txt(path, col=5):
    """读 7 列 txt。列序：1 idx 2 raw 3 dsr0 4 dsr 5 filt 6 ? 7 ?。返回 dict。"""
    idx, raw, c3, c4, c5 = [], [], [], [], []
    nbad = 0
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln[0] in "#;":
                continue
            p = ln.replace(",", " ").split()
            if len(p) != 7:
                nbad += 1
                continue
            try:
                v = [float(t) for t in p[:6]] + [_f7(p[6])]
            except ValueError:
                nbad += 1
                continue
            idx.append(v[0]); raw.append(v[1]); c3.append(v[2]); c4.append(v[3]); c5.append(v[4])
    d = {"n": len(idx), "nbad": nbad,
         "idx": np.asarray(idx), "raw": np.asarray(raw),
         "c3": np.asarray(c3), "c4": np.asarray(c4), "filt": np.asarray(c5)}
    d["col"] = {3: d["c3"], 4: d["c4"], 5: d["filt"]}.get(int(col), d["filt"])
    return d


def band_of(f):
    for lo, hi, dlo, dhi in BANDS:
        if lo <= f <= hi:
            return dlo, dhi
    return None, None


def amp_of(f):
    for lo, hi, a in Y_AMP:
        if lo <= f <= hi:
            return a
    return None


def parse_name(path):
    """从文件名取频率与输入幅度：fr-150Hz-0.25mVpp-xxxx.txt"""
    b = os.path.basename(path)
    mf = re.search(r'(\d+(?:\.\d+)?)\s*Hz', b, re.I)
    ma = re.search(r'(\d+(?:\.\d+)?)\s*mV', b, re.I)
    return (float(mf.group(1)) if mf else None,
            float(ma.group(1)) if ma else None)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--col", type=int, default=5, help="信号列（raw853 on ⇒ 5）")
    ap.add_argument("--burn", type=float, default=2.0, help="丢弃开头秒数")
    ap.add_argument("--amp", type=float, default=None, help="文件名无幅度时的统一下发值 (mVpp)")
    ap.add_argument("--prof", default="DIAG150_D3", help="用于预测对比的档位")
    ap.add_argument("--pred-mode", default="full", choices=["full", "fw"],
                    help="full=整机(含AFE SINC，现场用)；fw=仅固件链(合成数据自测用)")
    ap.add_argument("--tag", default="T70_频响实测判读_2026-09-21")
    a = ap.parse_args()

    out = []

    def P(s=""):
        print(s)
        out.append(s)

    paths = []
    for pat in a.files:
        g = glob.glob(pat)
        paths.extend(g if g else [pat])
    paths = sorted(set(paths))
    if not paths:
        P("！！ 没找到文件"); return

    P("=" * 100)
    P("T70 频响实测判读（GB 9706.225-2021 表201.107；参考 = 10 Hz；判据 = 基波幅度，非 p-p）")
    P("=" * 100)
    P()

    # ---- 逐文件取基波幅度
    rows = []
    for p in paths:
        f0, amp_n = parse_name(p)
        d = load_txt(p, a.col)
        y = d["col"]
        burn = int(a.burn * FS)
        if len(y) <= burn + int(FS):
            P("  [跳过] %s 太短（%d 样本）" % (os.path.basename(p), len(y)))
            continue
        ys = y[burn:]
        A = tone_amp(ys, f0) if f0 else float('nan')
        Als = tone_amp_ls(ys, f0) if f0 else float('nan')
        rows.append({
            "file": os.path.basename(p), "f": f0,
            "amp_in": amp_n if amp_n else a.amp,
            "A_code": A, "A_ls": Als,
            "A_uv": A * UVPC,
            "pp_code": pp(ys), "pp_uv": pp(ys) * UVPC,
            "n": d["n"], "nbad": d["nbad"], "dur": len(ys) / FS,
            "idx_step": float(np.diff(d["idx"].astype(np.int64)).mean()) if len(d["idx"]) > 1 else float('nan'),
        })

    # ---- 身份自检
    P("[0] 数据身份自检")
    for r in rows:
        dom = "853域" if abs(r["idx_step"] - 1.0) < 0.05 else \
              ("500域" if abs(r["idx_step"] - 1.7077) < 0.03 else "?")
        P("    %-44s f=%7s Hz  %5.1f s  n=%6d 坏行%2d  idx步长=%.4f → %s"
          % (r["file"][:44], r["f"], r["dur"], r["n"], r["nbad"], r["idx_step"], dom))
    P("    ⇒ idx 步长应 ≈1.0000（853 域）。若 ≈1.7077 ⇒ 那是 500 域数据，250~426 Hz 已折叠！")
    P()

    # ---- 参考点
    refs = [r for r in rows if r["f"] and abs(r["f"] - REF) < 0.05 and r["amp_in"]]
    if not refs:
        P("🛑 缺 10 Hz 参考点 ⇒ 无法归一化。请补抓 fr-10Hz-1mVpp-*.txt")
        with io.open(os.path.join(HERE, "_t70_out.txt"), "w", encoding="utf-8", newline="\n") as fp:
            fp.write("\n".join(out))
        return
    rref = refs[0]
    denom = rref["A_code"] / rref["amp_in"]
    P("[1] 参考点 10 Hz：文件 %s" % rref["file"])
    P("    基波幅度 %.1f code（%.1f µV）／输入 %.3f mVpp ⇒ %.2f code/mVpp"
      % (rref["A_code"], rref["A_uv"], rref["amp_in"], denom))
    P("    （p-p 交叉验证：%.1f code = %.1f µV）" % (rref["pp_code"], rref["pp_uv"]))
    P()

    # ---- 归一化 + 判定
    P("[2] 归一化频响与判定（g = [A/Amp_in] ÷ [A_ref/Amp_in_ref] × 100%）")
    P("-" * 100)
    P("     f(Hz) | 输入mVpp |  基波code | 最小二乘 | p-p code | 归一化% |  判限     | 判决 | 列④? ")
    graded = []
    for r in sorted(rows, key=lambda z: (z["f"] or 1e9)):
        if not r["f"] or not r["amp_in"]:
            continue
        g = (r["A_code"] / r["amp_in"]) / denom * 100.0
        dlo, dhi = band_of(r["f"])
        ok = "✓" if (dlo <= g <= dhi) else "✗"
        lstsq_dif = abs(r["A_ls"] - r["A_code"]) / r["A_code"] * 100 if r["A_code"] else float('nan')
        r["g"] = g; r["ok"] = (dlo <= g <= dhi)
        graded.append(r)
        P("   %7.2f | %7.3f | %9.1f | %8.1f | %8.1f | %7.2f | %3.0f~%3.0f%% |  %s  | DFTvsLS %.1f%%"
          % (r["f"], r["amp_in"], r["A_code"], r["A_ls"], r["pp_code"], g, dlo, dhi, ok, lstsq_dif))
    P()

    # ---- 与预测对比
    mode_lab = "整机(含AFE SINC)" if a.pred_mode == "full" else "仅固件链(无AFE)"
    P("[3] 与离线预测对比（%s；偏差 > ±3 pp 需查第三变量）" % mode_lab)
    P("-" * 100)
    g150_pred = None
    try:
        sos = T69.parse_profile(T69.SRC_C, a.prof)
        cnt = T69.parse_counts(T69.SRC_H, a.prof)
        nn = cnt[1]
        fl = np.array([r["f"] for r in graded], float)
        Hf = T69.h_chain(sos, fl, False, nn, a.prof)
        rr = abs(T69.h_chain(sos, np.array([REF]), False, nn, a.prof)[0])
        if a.pred_mode == "full":
            pred = np.abs(Hf * T69.h_afe(fl)) / (rr * T69.h_afe(np.array([REF]))[0]) * 100.0
        else:
            pred = np.abs(Hf) / rr * 100.0
        P("     f(Hz) | 预测%  | 实测%  | 偏差pp | 评价")
        for i, r in enumerate(graded):
            dv = r["g"] - pred[i]
            ev = "✓一致" if abs(dv) <= 3 else ("⚠偏大" if dv > 0 else "⚠偏小")
            if r["f"] and abs(r["f"] - 150) < 0.05:
                g150_pred = pred[i]
            P("   %7.2f | %6.2f | %6.2f | %+6.2f | %s" % (r["f"], pred[i], r["g"], dv, ev))
        P()
    except Exception as e:
        P("    （预测对比失败：%s）" % e)
        P()

    # ---- 现场自检提示
    P("[4] 现场自检提示")
    g100 = [r["g"] for r in graded if r["f"] and abs(r["f"] - 100) < 0.05]
    g150 = [r["g"] for r in graded if r["f"] and abs(r["f"] - 150) < 0.05]
    if g100:
        P("    · 100 Hz 实测 %.2f%% —— D3 档 notch **ON** 时此点被 Q15 陷波打到 ≈0%%" % g100[0])
        P("      ⇒ 若这里 ≈0 ⇒ **notch 没关**，全部数据重抓（`test notch off`，注意 init 会强制回开）")
        if abs(g100[0]) < 5:
            P("      🛑 实测 %.2f%% ⇒ 高度怀疑 notch 仍为 ON！" % g100[0])
    else:
        P("    · 未测 100 Hz —— 建议补抓（它是「notch 关没关」的唯一现场判据）")
    if g150:
        p150 = ("%.2f%%" % g150_pred) if g150_pred is not None else "见[3]"
        P("    · 150 Hz 实测 %.2f%% —— 预测 %s（%s）" % (g150[0], p150, mode_lab))
        P("      参照：D3(notchOFF) 整机 75.64%；N2(BP@150) 整机 54.76%")
        if g150[0] > 68:
            P("      ⇒ 与 D3 一致 ✓ 档位正确")
        elif g150[0] < 62:
            P("      🛑 更接近 54.8%% ⇒ **烧的不是 D3 档**（BP 上角是 150 Hz）")
    else:
        P("    · 未测 150 Hz —— 它是唯一的贴限点（下限 70%），必测")
    P()

    # ---- 图
    if graded:
        fig, ax = plt.subplots(figsize=(12.2, 5.6), dpi=130)
        for lo, hi, dlo, dhi in BANDS:
            ax.fill_between([lo, hi], dlo, dhi, color="#EAF3DE", zorder=1)
            ax.plot([lo, hi], [dhi, dhi], color="#639922", lw=1.1, zorder=2)
            ax.plot([lo, hi], [dlo, dlo], color="#639922", lw=1.1, zorder=2)
        try:
            gx = np.exp(np.linspace(np.log(0.67), np.log(520), 500))
            sos = T69.parse_profile(T69.SRC_C, a.prof)
            nn = T69.parse_counts(T69.SRC_H, a.prof)[1]
            if a.pred_mode == "full":
                def _p(fa):
                    return np.abs(T69.h_chain(sos, fa, False, nn, a.prof)
                                  * T69.h_afe(fa))
                rp = _p(np.array([REF]))[0]
            else:
                def _p(fa):
                    return np.abs(T69.h_chain(sos, fa, False, nn, a.prof))
                rp = _p(np.array([REF]))[0]
            yp = _p(gx) / rp * 100
            ax.plot(gx, yp, "-", color="#185FA5", lw=1.8,
                    label="离线预测 %s / notch OFF / %s" % (a.prof, mode_lab))
        except Exception:
            pass
        xs = [r["f"] for r in graded]
        ys = [r["g"] for r in graded]
        cs = ["#1F9D6B" if r["ok"] else "#C0392B" for r in graded]
        ax.scatter(xs, ys, s=58, c=cs, zorder=6, edgecolors="white", linewidths=1.1,
                   label="实测（绿=合格 / 红=不合格）")
        for r in graded:
            ax.annotate("%.1f" % r["g"], (r["f"], r["g"]), fontsize=7.4,
                        xytext=(0, 8), textcoords="offset points", ha="center",
                        color="#1F9D6B" if r["ok"] else "#C0392B")
        ax.axvline(REF, color="#888780", ls=":", lw=1)
        ax.axvline(T69.F_NYQ, color="#B23B3B", ls="--", lw=1.2)
        ax.text(T69.F_NYQ * 0.965, 8, "Nyquist 426.6 Hz", fontsize=7.6, color="#B23B3B",
                ha="right")
        ax.set_xscale("log")
        ax.set_xlim(0.67, 520)
        ax.set_ylim(0, 128)
        ax.set_xticks([0.67, 1, 2, 5, 10, 20, 40, 60, 100, 150, 200, 300, 400, 500])
        ax.set_xticklabels(["0.67", "1", "2", "5", "10", "20", "40", "60", "100", "150",
                            "200", "300", "400", "500"], fontsize=8.5)
        ax.set_xlabel("频率 (Hz)")
        ax.set_ylabel("相对 10 Hz 的归一化增益 (%)")
        ax.set_title("T70 频响实测 vs 表201.107（判据 = 基波幅度，非 p-p）", fontsize=11)
        ax.grid(alpha=0.22, which="both")
        ax.legend(fontsize=8.6, loc="lower left")
        fig.tight_layout()
        png = os.path.join(HERE, a.tag + ".png")
        fig.savefig(png)
        P("[图] %s" % png)

    with io.open(os.path.join(HERE, "_t70_out.txt"), "w", encoding="utf-8", newline="\n") as fp:
        fp.write("\n".join(out))


if __name__ == "__main__":
    main()
