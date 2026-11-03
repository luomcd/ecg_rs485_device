# -*- coding: utf-8 -*-
"""
T75 · 双态可切换方案：可行域评估
================================
问题：猛哥提出「用 notch on/off 切换两套配置」——notch on 满足输入阻抗/线性/动态，
      notch off 满足频率响应。本脚本量化这个方案的可行域。

四件事：
  [1] 各档 notch OFF 态频响（= 标准规定的频响试验条件），看谁过 表 201.107 行3 的 70%
  [2] 各档 notch ON 态在 149.87 Hz（市电3次谐波）的抑制量 —— 输入阻抗的命门
  [3] 输入阻抗多口径汇总（引用 T66 的 853 域离线重放实测预测，唯一能评估 fc 的数据）
  [4] 两态方案矩阵 + 出图

数据来源（不可替换）：
  - 频响：从 Filter/ecg_filter.c 源码直读系数，经 _t69_fr_forecast 的 h_chain/h_afe
  - 输入阻抗：T66 §4 表（853 域离线重放，源 620kin-raw835on-2026-09-20 215252.txt）
    ⚠ 620k 数据若是 raw853 off（500 域）已过 fc，无法评估 BP 上角差异

用法：
  python _t75_dual_mode.py            # 全部
  python _t75_dual_mode.py --fig      # 额外出图 T75_*.png
"""
import sys, os, io, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t69_fr_forecast as F

C_FILE = os.path.join(HERE, "..", "Filter", "ecg_filter.c")
H_FILE = os.path.join(HERE, "..", "Filter", "ecg_filter_coeffs.h")

# ---- 档位库（名 → 展示标签） ----
PROFILES = [
    ("DIAG150",    "D1"),
    ("DIAG150_D2", "D2"),
    ("DIAG150_D3", "D3"),
    ("DUAL_A",     "DUAL_A"),
    ("DUAL_A_PLUS", "A+"),
    ("IMP620",     "IMP620"),
    ("IMP620_N2",  "N2"),
    ("ST40",       "ST40"),
]

# ---- 判据 ----
FR_LOWPCT = 70.0        # 表 201.107 行3（100~150Hz）相对 10Hz 下限
FR_POINTS = (100.0, 120.0, 150.0)
ZIN_LIMIT = 110.0       # T50/T66 判限：1s 窗 p-p 比值 ≤110%

# ---- 输入阻抗：**动态算**（不再硬编码 T66 的数——那批是另一数据集，混用会误导） ----
ZIN_DATA = "3mv-10hz-sine-620kin-2026-09-21 104047.txt"   # D3 档实测：3 mV / 10 Hz / 620 kΩ
ZIN_NOM = 3000.0
ZIN_TGT = [
    ("LP2@250 + 15 节陷波（= A+，T77 默认档）", "DUAL_A_PLUS"),
    ("LP2@250 + 9 节陷波（= DUAL_A）",    "DUAL_A"),
    ("LP4@150 + 9 节陷波（= IMP620_N2）", "IMP620_N2"),
    ("LP2@250 + 4 节陷波（= D3，现状）",  "DIAG150_D3"),
]
ZIN_T66_NOTE = ("参照（**另一数据集，不可与本批直接比**）：T66 §4 853 域离线重放 — "
                "LP2@250+9陷波 105.51%/111.15%；LP4@150+9陷波 99.36%/102.53%")


def zin_dynamic():
    """在 ZIN_DATA 这批数据上，预测各目标链的 1 s 窗 p-p 中位 / p95（%）。

    ⚠ 这是**唯一正确**的候选 A/B 输入阻抗读数口径：必须"同一批数据 × 换链"。
      引用别的批次数值（如 T66 的 105.51%）会导致结论偏差 ~4 pp（T76 已更正）。
    """
    import _t73_input_z as T
    d, _bad, _ = T.load(os.path.join(HERE, ZIN_DATA))
    c4 = (d[:, 3] * T.UV4)[int(.5 * T.FS):-int(.5 * T.FS)]
    pks = T.peaks(c4, T.FS)
    soc = F.parse_profile(C_FILE, "DIAG150_D3")     # 数据取自 D3 档
    out = {}
    for lab, prof in ZIN_TGT:
        son = F.parse_profile(C_FILE, prof)
        med, p95, _ = T.predict_target(c4, pks, soc, son, ZIN_NOM)
        out[prof] = (med, p95)
    return out, [f for f, _ in pks]



def H1(sos, f, notch, nn, prof):
    """整机（固件链 × AFE）幅度。"""
    x = np.abs(F.h_chain(sos, f, notch, nn, prof) * F.h_afe(f))
    return float(np.ravel(x)[0])


def load_prof(name):
    sos = F.parse_profile(C_FILE, name)
    cnt = F.parse_counts(H_FILE, name)
    return sos, cnt          # cnt = (总节, 陷波节, BP节)


# ============================================================ [1] [2]
def scan():
    print("=" * 96)
    print("[1] 各档 notch OFF 态频响（标准规定的频响试验条件；相对 10 Hz，%）")
    print("=" * 96)
    print("%-8s %6s %6s %9s %9s %9s %9s %9s   %s"
          % ("档", "总节", "BP节", "100Hz", "120Hz", "150Hz", "200Hz", "250Hz", "判定"))
    fr_rows = {}
    for name, lab in PROFILES:
        sos, cnt = load_prof(name)
        nn = cnt[1]
        ref = H1(sos, 10.0, False, nn, name)
        r = {f: H1(sos, f, False, nn, name) / ref * 100 for f in FR_POINTS + (200.0, 250.0)}
        ok = "✓ OK" if min(r[f] for f in FR_POINTS) >= FR_LOWPCT else "✗ FAIL"
        fr_rows[lab] = r
        print("%-8s %6d %6d %9.1f %9.1f %9.1f %9.1f %9.1f   %s"
              % (lab, cnt[0], cnt[2], r[100.0], r[120.0], r[150.0], r[200.0], r[250.0], ok))
    print()
    print("  ⇒ 只有 BP=LP2@250 的档（D1/D2/D3）过；BP=LP4@150 的档（IMP620/N2）150Hz 仅 54.8% ✗")
    print()

    print("=" * 96)
    print("[2] 各档 notch ON 态：市电谐波线的抑制量（dB，相对 10 Hz）")
    print("=" * 96)
    lines = [(49.87, "50Hz"), (99.87, "100Hz"), (149.87, "150Hz*"),
             (199.74, "200Hz"), (249.73, "250Hz")]
    print("%-8s %s" % ("档", " ".join("%9s" % n for _, n in lines)))
    for name, lab in PROFILES:
        sos, cnt = load_prof(name)
        nn = cnt[1]
        ref = H1(sos, 10.0, True, nn, name)
        out = []
        for f, _ in lines:
            v = H1(sos, f, True, nn, name) / ref
            out.append(20 * np.log10(max(v, 1e-12)))
        print("%-8s %s" % (lab, " ".join("%9.1f" % x for x in out)))
    print()
    print("  * 149.87 Hz = 市电 3 次谐波 = 输入阻抗测试的命门（T73 实测占 63% 超差）")
    print("  ⇒ D3 对 149.87 Hz 只有 −2.4 dB（= 等于不压）⇒ 光靠 notch 开关在 D3 内翻不出输入阻抗")
    print()
    return fr_rows


# ============================================================ [3]
def zin():
    print("=" * 96)
    print("[3] 输入阻抗（多口径）—— 同批数据换链预测（%s）" % ZIN_DATA)
    print("=" * 96)
    res, lines = zin_dynamic()
    print("  数据中的带外干扰线：%s" % "  ".join("%.2f Hz" % f for f in lines))
    print()
    print("%-34s %9s %5s %9s %5s" % ("目标链", "中位%", "", "p95%", ""))
    for lab, prof in ZIN_TGT:
        med, p95 = res[prof]
        print("%-34s %8.2f%s %5s %8.2f%s"
              % (lab, med, "✓" if med <= ZIN_LIMIT else "✗", "",
                 p95, "✓" if p95 <= ZIN_LIMIT else "✗"))
    print()
    print("  ⇒ 判据口径（中位 / p95）**单一决定**方案要不要切成两态")
    print("  ⇒ %s" % ZIN_T66_NOTE)
    print()
    return res


# ============================================================ [4]
def matrix():
    print("=" * 96)
    print("[4] 方案矩阵：两态 × 各档")
    print("=" * 96)
    res, _ = zin_dynamic()

    def m(prof):
        return res[prof][0]

    def p(prof):
        return res[prof][1]

    rows = [
        ("D3",     "LP2@250（3节）", "（无 150x3）",     "75.64%", "✓", "−2.4 dB",
         "✗ %.2f%%（本批）" % m("DIAG150_D3")),
        ("N2",     "LP4@150（4节）", "含 150x3（9节）", "54.76%", "✗", "−90.2 dB",
         "✓ %.2f%%（本批）" % m("IMP620_N2")),
        ("DUAL_A", "LP2@250（3节）", "含 150x3（9节）", "75.64%", "✓", "−87.4 dB",
         "%.2f / %.2f%%（本批）" % (m("DUAL_A"), p("DUAL_A"))),
        ("A+",     "LP2@250（3节）", "含 150/200/250 x3（15节）", "75.64%", "✓",
         "−85/−86/−92 dB",
         "%.2f / %.2f%%（本批）✓✓" % (m("DUAL_A_PLUS"), p("DUAL_A_PLUS"))),
    ]
    print("%-6s %-18s %-18s %9s %4s %10s %-24s"
          % ("档", "notch OFF 链", "notch ON 追加", "频响150", "判定", "150Hz抑制", "输入阻抗"))
    for r in rows:
        print("%-6s %-18s %-18s %9s %4s %10s %-24s" % r)
    print()
    print("  需求组合（猛哥方案）：notch OFF 要 75.64% ✓（抓 150Hz 频响）"
          "+ notch ON 要压 149.87Hz（N2 的陷波组）")
    print("  ⇒ 两者不在同一档 ⇒ 必须『连 BP 一起切』，不能只切陷波")
    print()
    print("-" * 96)
    print("候选 A【一套参数双过】= LP2@250（D3 的 BP） + 9 节陷波（N2 的陷波组）")
    print("    ★ 2026-09-21 已落码为 **DUAL_A 档**（12 节，T76）——切换：`--set duala`")
    print("    notch OFF → 3 节 BP        → 频响 150Hz 75.64% ✓   （带宽恒 0.05-250Hz，不构成可选带宽设置）")
    print("    notch ON  → +9 节陷波      → 输入阻抗 中位 %.2f%% %s ／ p95 %.2f%% %s"
          % (m("DUAL_A"), "✓" if m("DUAL_A") <= ZIN_LIMIT else "✗",
             p("DUAL_A"), "✓" if p("DUAL_A") <= ZIN_LIMIT else "✗"))
    print("    ⚠⚠ **中位余量只有 %.2f pp**（限 110%%）⇒ 贴限运行，环境稍差即击穿（T76 更正本条）"
          % (ZIN_LIMIT - m("DUAL_A")))
    print("    ⚠ 本档 ON 态带宽仍 250 Hz ⇒ 200/250 Hz 市电谐波全放行（这是与候选 B 的唯一差别）")
    print()
    print("-" * 96)
    print("候选 A+【★ 2026-09-21 已落码为 **A+ 档**（18 节，T77）= **当前默认档**】")
    print("    = 候选 A 的链首再补 200 簇 + 250 簇（各三节 Q20，±0.2 Hz）")
    print("    ★ 依据：陷波节恒在链首 + `notch OFF` 整段跳过链首 ⇒ **往链首加陷波对 OFF 态零代价**")
    print("    notch OFF → 3 节 BP（**与 DUAL_A / D3 逐位等价**）→ 频响 150Hz 75.64% ✓")
    print("    notch ON  → +15 节陷波    → 输入阻抗 中位 %.2f%% %s ／ p95 %.2f%% %s"
          % (m("DUAL_A_PLUS"), "✓" if m("DUAL_A_PLUS") <= ZIN_LIMIT else "✗",
             p("DUAL_A_PLUS"), "✓" if p("DUAL_A_PLUS") <= ZIN_LIMIT else "✗"))
    print("    ⇒ **八档里唯一「两个口径 × 两种状态」全过**；中位余量 %.2f pp（候选 A 只有 0.24 pp）"
          % (ZIN_LIMIT - m("DUAL_A_PLUS")))
    print("    ⚠ 代价 +6 节：CPU 按 0.8115 pp/节外推 ≈ 14.6%（12 节 9.75%）⇒ 🛑 必须上板 DWT 实测")
    print("    ⚠ 250 属市电 5 次谐波、漂移 ±0.2453 Hz；三节 Q20 簇深覆盖 1.300 Hz ⇒ 余量 2.65×（已核）")
    print()
    print("候选 B【双态切换】= notch OFF 用 D3 的链 + notch ON 用 N2 的全链")
    print("    notch OFF → LP2@250（3节） → 频响 75.64% ✓")
    print("    notch ON  → LP4@150+9陷波  → 输入阻抗 中位 %.2f%% ✓ ／ p95 %.2f%% %s"
          % (m("IMP620_N2"), p("IMP620_N2"), "✓" if p("IMP620_N2") <= ZIN_LIMIT else "✗"))
    print("    ⚠ 合规代价：notch ON 态带宽变 150Hz ⇒ 构成『可选带宽设置』")
    print("      ⇒ GB 9706.227 §201.12.1.101.8 a)「每种设置按制造商规定带宽试验」可能被触发")
    print("      ⇒ 对策：声明为两个独立工作模式，各自标注适用条款与带宽（勿绑在陷波开关上）")
    print("    ⚠ 工程代价：需**运行期切 BP**（现在只有编译期宏切）⇒ 要做 BP 段 DC 预置，工作量大于候选 A")
    print()


# ============================================================ [5] A+ 概念验证
def aplus_chain():
    """**A+** = DUAL_A 的陷波组再补 200/250 各一簇（Q20、±0.2 Hz），共 18 节 = 15 陷波 + 3 BP。

    ★★ T76 核心洞察：**陷波节恒在链首**，而 `notch OFF` = 跳过链首**全部**陷波节
      ⇒ 往链首加陷波对 OFF 态频响**零影响**（OFF 只剩那 3 节 BP）。
      ⇒ "用陷波换输入阻抗余量"是一笔**无 OFF 代价**的交易 —— 这才是"一套参数双过"的真正通路。
    ★ T77 状态：**已落码为真实档位** `ECG_PROFILE_DUAL_A_PLUS`（值 7，18 节，当前默认档）——
      本函数优先**从固件读表**（单一真源）；只有固件里没有该档时才退回 scipy 现场设计。
      200/250 簇的参数定案见 `tools/_t77_design_aplus.py`：
        · 250 Hz 漂移 ±0.2453 Hz（5 × 0.0981）⇒ 三节 Q20 深覆盖 1.300 Hz ⇒ 余量 2.65×（无需加宽）
        · 200 Hz 漂移 ±0.1962 Hz ⇒ 深覆盖 1.059 Hz ⇒ 余量 2.70×
    """
    # ★ T77：A+ 已**落码为真实档位**（ECG_PROFILE_DUAL_A_PLUS，18 节）⇒ 优先读固件，
    #   读不到（更早的仓库状态）才退回"现场设计"的概念验证路径。
    try:
        s_all = F.parse_profile(C_FILE, "DUAL_A_PLUS")
        return s_all, 15
    except Exception:
        print("  ⚠ 固件里没有 DUAL_A_PLUS 档 ⇒ 退回 scipy 现场设计（概念验证）")
    from scipy import signal
    sa = F.parse_profile(C_FILE, "DUAL_A")
    nz, bp = sa[:9], sa[9:]

    def cluster(fc, Q=20, df=0.2):
        return np.array([[b[0], b[1], b[2], 1.0, a[1], a[2]]
                         for b, a in (signal.iirnotch((fc + k * df) / (F.FS / 2), Q)
                                      for k in (-1, 0, 1))])
    extra = np.vstack([cluster(200.0), cluster(250.0)])
    return np.vstack([nz, extra, bp]), len(nz) + len(extra)


def aplus():
    print("=" * 96)
    print("[5] A+ 档（T77 已落码 = 当前默认档，18 节）：往链首补 200/250 陷波（OFF 态零代价）")
    print("=" * 96)
    import _t73_input_z as T
    d, _b, _c = T.load(os.path.join(HERE, ZIN_DATA))
    c4 = (d[:, 3] * T.UV4)[int(.5 * T.FS):-int(.5 * T.FS)]
    pks = T.peaks(c4, T.FS)
    soc = F.parse_profile(C_FILE, "DIAG150_D3")
    sa = F.parse_profile(C_FILE, "DUAL_A")
    sap, nn_ap = aplus_chain()

    print("%-26s %9s %6s %9s %6s  %s" % ("链", "中位%", "", "p95%", "", "OFF 态 150 Hz"))
    rows = [("DUAL_A（12 节＝9+3）", sa, 9),
            ("A+（18 节＝15+3）", sap, nn_ap),
            ("IMP620_N2（13 节＝9+4）", F.parse_profile(C_FILE, "IMP620_N2"), 9)]
    for lab, sos, nn in rows:
        med, p95, _ = T.predict_target(c4, pks, soc, sos, ZIN_NOM)
        ref = H1(sos, 10.0, False, nn, "x")
        fr150 = H1(sos, 150.0, False, nn, "x") / ref * 100
        print("%-26s %8.2f%s %6s %8.2f%s %6s  %8.4f%%"
              % (lab, med, "✓" if med <= ZIN_LIMIT else "✗", "",
                 p95, "✓" if p95 <= ZIN_LIMIT else "✗", "", fr150))
    print()
    print("  ⇒ A+ 的 ON 态**两个口径都过**（105.76 / 109.93），而 OFF 态频响与 DUAL_A **逐位相同**")
    print("  ⇒ 代价：+6 节（CPU/Flash）；收益：ON 态余量 0.24 pp → 4.24 pp")
    print()
    return sap, nn_ap


# ============================================================ 出图
def figure(fr_rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for fam in ("Microsoft YaHei", "SimHei", "DejaVu Sans"):
        plt.rcParams["font.sans-serif"] = [fam]
        try:
            from matplotlib.font_manager import FontProperties
            FontProperties(family=fam).get_name()
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(1, 3, figsize=(16.5, 5.0))

    # ---- P1 频响曲线（notch off） ----
    ff = np.logspace(np.log10(0.7), np.log10(300), 500)
    styles = {"D3": ("-", "#c0392b", 2.2), "N2": ("-", "#2471a3", 2.2),
              "D2": (":", "#e67e22", 1.4), "IMP620": (":", "#7fb3d5", 1.4),
              "DUAL_A": ("--", "#27ae60", 1.5)}
    for lab, (ls, c, lw) in styles.items():
        name = dict((l, n) for n, l in PROFILES)[lab]
        sos, cnt = load_prof(name)
        nn = cnt[1]
        ref = H1(sos, 10.0, False, nn, name)
        y = [H1(sos, f, False, nn, name) / ref * 100 for f in ff]
        lab_txt = ("DUAL_A / A+（OFF 态与 D3 逐位重合）" if lab == "DUAL_A"
                   else "%s（%s）" % (lab, "BP=LP2@250" if lab in ("D3", "D2") else "BP=LP4@150"))
        ax[0].semilogx(ff, y, ls, color=c, lw=lw, label=lab_txt)
    ax[0].axhline(FR_LOWPCT, color="#c0392b", ls="--", lw=1.2, label="判限 70%")
    for p in FR_POINTS:
        ax[0].axvline(p, color="gray", ls=":", lw=.8)
    ax[0].plot([150], [75.64], "o", color="#c0392b", ms=7, zorder=5)
    ax[0].plot([150], [54.76], "o", color="#2471a3", ms=7, zorder=5)
    ax[0].annotate("75.6% OK", (150, 75.64), textcoords="offset points", xytext=(8, 6), color="#c0392b", fontsize=10)
    ax[0].annotate("54.8% NG", (150, 54.76), textcoords="offset points", xytext=(8, -14), color="#2471a3", fontsize=10)
    ax[0].set_xlim(0.7, 300); ax[0].set_ylim(0, 112)
    ax[0].set_xlabel("频率 (Hz)"); ax[0].set_ylabel("相对 10 Hz (%)")
    ax[0].set_title("P1 · notch OFF 态频响（标准规定的频响试验条件）", fontsize=11)
    ax[0].grid(alpha=.3, which="both"); ax[0].legend(fontsize=8, loc="lower left")

    # ---- P2 输入阻抗两口径（本批数据 × 换链，动态） ----
    import _t73_input_z as T
    zres, _zl = zin_dynamic()
    ap_med, ap_p95 = zres["DUAL_A_PLUS"]        # T77：A+ 已是真实档 ⇒ 与其它档同一条动态路径
    names = ["D3\n(LP2@250\n+4陷波)", "DUAL_A\n(LP2@250\n+9陷波)",
             "A+\n(LP2@250\n+15陷波)", "N2\n(LP4@150\n+9陷波)"]
    med = [zres["DIAG150_D3"][0], zres["DUAL_A"][0], ap_med, zres["IMP620_N2"][0]]
    p95 = [zres["DIAG150_D3"][1], zres["DUAL_A"][1], ap_p95, zres["IMP620_N2"][1]]
    x = np.arange(len(names)); w = 0.36
    b1 = ax[1].bar(x - w/2, med, w, label="1s窗 p-p 中位", color="#2471a3")
    b2 = ax[1].bar(x + w/2, p95, w, label="1s窗 p-p p95", color="#e67e22")
    ax[1].axhline(ZIN_LIMIT, color="#c0392b", ls="--", lw=1.5, label="判限 110%")
    for bars in (b1, b2):
        for b in bars:
            h = b.get_height()
            ax[1].text(b.get_x() + b.get_width()/2, h + 0.5, "%.1f" % h,
                       ha="center", fontsize=7.5, color="#c0392b" if h > ZIN_LIMIT else "#1e8449")
    ax[1].set_xticks(x)
    ax[1].set_xticklabels(names, fontsize=8)
    ax[1].set_ylim(98, 122); ax[1].set_ylabel("接入/直连 比值 (%)")
    ax[1].set_title("P2 · 输入阻抗（同批数据换链 · %s）" % ZIN_DATA[:24], fontsize=10)
    ax[1].grid(alpha=.3, axis="y"); ax[1].legend(fontsize=8, loc="upper left")
    ax[1].annotate("p95 超差", (1 + w/2, p95[1]), textcoords="offset points",
                   xytext=(4, 6), fontsize=8.5, color="#c0392b")
    ax[1].annotate("A+ 两口径\n都过", (2, max(med[2], p95[2])), textcoords="offset points",
                   xytext=(0, 10), fontsize=8.5, color="#1e8449", ha="center")

    # ---- P3 方案矩阵 ----
    ax[2].axis("off")
    txt = (
        "方案矩阵（两态 x 各档）\n"
        "=============================\n"
        "需求组合（你的方案）：\n"
        "  notch OFF -> 频响 75.64% OK\n"
        "  notch ON  -> 压 149.87Hz OK\n"
        "  => 两者的链不在同一档\n"
        "  => 必须连 BP/陷波一起改\n\n"
        "候选 A = DUAL_A  [已落码 12节]\n"
        "  LP2@250 + 9节陷波\n"
        "  OFF 75.64% OK\n"
        "  ON  109.8 OK / 114.1 NG\n"
        "  余量只有 0.24pp -> 贴限!\n\n"
        "候选 A+  [★已落码 18节 = 当前默认]\n"
        "  LP2@250 + 15节陷波\n"
        "  OFF 75.64% OK  <- 与 D3 逐位相同\n"
        "  ON  105.8 OK / 109.9 OK  <- 双过\n"
        "  余量 4.24pp\n"
        "  关键：陷波恒在链首，\n"
        "        OFF 时整段跳过\n"
        "        => 零 OFF 代价\n\n"
        "候选 B 双态切 BP\n"
        "  ON 105.9 OK / 110.3 NG\n"
        "  ! ON 态带宽变 150Hz\n"
        "    => 构成[可选带宽设置]"
    )
    ax[2].text(0.0, 1.0, txt, va="top", ha="left", fontsize=9.2,
               bbox=dict(boxstyle="round,pad=0.6", fc="#fdf6e3", ec="#b58900", lw=1.2))
    ax[2].set_title("P3 · 方案权衡", fontsize=11)

    plt.tight_layout()
    out = os.path.join(HERE, "T75_双态方案_可行域_2026-09-21.png")
    plt.savefig(out, dpi=140, bbox_inches="tight")
    print("[fig] %s" % out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fig", action="store_true")
    a = ap.parse_args()
    fr_rows = scan()
    zin()
    matrix()
    aplus()
    if a.fig:
        figure(fr_rows)


if __name__ == "__main__":
    main()
