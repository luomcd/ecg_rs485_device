# -*- coding: utf-8 -*-
"""T66 · 620 kΩ × fc250：**实测代价**取证 ＋ 回退预测（数据源＝T65-0 四份 215252）

任务（承接 T63 的「按档分离」方案，猛哥令：给出可上板的固件参数）
----
S1 判「目测 p-p 低于设定值」真伪
S2 量化 fc250 在 **真 853 域** 的实际代价（首次有非折叠数据）
S3 复现 fc250 下的**输入阻抗判据**读数（T50 口径：列④ 1 s 窗 p-p 中位）
S4 给出回退 fc150 后的预期读数 ⇒ 供拍板

口径纪律（全部为踩过的坑）
------------------------
1. 列② raw / 列⑤ filt **都是 code 域**；链内部按 µV 算 ⇒ 输入必 `(raw-6075000)*0.1122`。
2. `raw853 on` ⇒ 列③/列④ 恒 0，**输入阻抗判据拿不到**（必须用 off 那份）。
3. 段长 ≥30 s（dc_block τ=3.18 s）；每节 sosfilt 零初值 ⇒ 丢前 4 s 暂态。
4. 判采样域用 **idx 增量均值**（中位会落在 2）。
5. 620 kΩ 接入时列④ 会削顶（off 段 87.7%）⇒ **幅度分析走列⑤**。
6. 方波幅度用**周期折叠模板**（T50 法），否则 13 mV 工频会把 1 mV 方波淹没。

输出：`_t66_out.txt`
"""
import io
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t56_raw853_lowfreq as T56
import _t50_620k_onoffon as T50
import _t63_fc150_vs_fc250_620k as T63   # 复用 parse_profile / run_chain / Hdb

FS = 853.2423208191125
FS500 = 500.0
UVPC = 0.1122
ZERO = 6075000.0
ONTH = 6000.0
BURN = 4.0          # s，丢启动暂态

F_IN_ON = '620kin-raw835on-2026-09-20 215252.txt'
F_IN_OFF = '620kin-raw835off(on-off-on)-2026-09-20 215252.txt'
F_NO_ON = '620knotin-raw835on-2026-09-20 215252.txt'
F_NO_OFF = '620knotin-raw835off(on-off-on)-2026-09-20 215252.txt'

BANDS = [(0.3, 2, '0.3~2'), (2, 45, '2~45'), (45, 55, '45~55'),
         (55, 145, '55~145'), (145, 155, '145~155'), (155, 245, '155~245'),
         (245, 340, '245~340'), (340, 426, '340~426')]

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def hdr(s):
    say()
    say('=' * 84)
    say(s)
    say('=' * 84)


# ------------------------------------------------------------ 基础工具

def segs(d):
    """按 dtc 分段 -> [(state, a, b), ...]"""
    on = d['dtc'] > ONTH
    sw = np.where(np.diff(on.astype(np.int8)) != 0)[0]
    bounds = [0] + list(sw + 1) + [len(on)]
    return [('ON' if on[bounds[k]] else 'OFF', bounds[k], bounds[k + 1])
            for k in range(len(bounds) - 1)]


def band_rms_uv(y, fs, bands=BANDS):
    """带内积分 rms（µV）。输入 y 已是 code；输出乘 UVPC。"""
    y = np.asarray(y, float)
    y = y - y.mean()
    w = np.hanning(len(y))
    F = np.fft.rfft(y * w)
    f = np.fft.rfftfreq(len(y), 1.0 / fs)
    rmsb = (2.0 * np.abs(F) / w.sum()) / np.sqrt(2.0)
    out = {}
    for lo, hi, nm in bands:
        m = (f >= lo) & (f < hi)
        out[nm] = float(np.sqrt(np.sum(rmsb[m] ** 2))) * UVPC if m.sum() else float('nan')
    return out


def pp1s_med(y, fs, w=1.0, step=0.25):
    """1 s 窗 p-p 的中位数（09-18 / T50 屏幕口径）。"""
    n = int(w * fs)
    st = max(1, int(step * fs))
    v = [float(np.max(y[i:i + n]) - np.min(y[i:i + n]))
         for i in range(0, len(y) - n, st)]
    return float(np.median(v)) if v else float('nan')


def fold_amp(x, fs, lo, hi):
    """周期折叠模板法测方波幅度（µV）。
    返回 (幅度µV, 周期样本, 模板, 相位)。
    ★ 幅度 = 高电平中位 − 低电平中位（不受单点毛刺影响）。"""
    x = np.asarray(x, float)
    x = x - x.mean()
    P = T50.est_period(x, lo=lo, hi=hi)
    n = int(np.floor(len(x) / P))
    ii = np.arange(n * int(round(P)))
    bi = np.floor((ii % P)).astype(int)
    nb = int(np.ceil(P))
    s = np.zeros(nb)
    c = np.zeros(nb)
    np.add.at(s, bi, x[:len(ii)])
    np.add.at(c, bi, 1.0)
    tpl = s / np.maximum(c, 1.0)
    med = np.median(tpl[c > 0])
    hi_lv = np.median(tpl[tpl >= med])
    lo_lv = np.median(tpl[tpl < med])
    i0 = int(np.argmax(tpl))
    return float(hi_lv - lo_lv), float(P), tpl, i0


def flat_rms(y, fs, P, i0, guard=40):
    """模板平段（离边沿 guard 样本以上）在原序列上的 rms（µV）—— 避开边沿振铃。"""
    y = np.asarray(y, float)
    ph = np.floor(np.arange(len(y)) % P).astype(int)
    e1 = int(i0)
    e2 = int((i0 + P / 2) % P)
    m = np.ones(len(y), bool)
    for e in (e1, e2):
        dd = np.abs(ph - e)
        dd = np.minimum(dd, P - dd)
        m &= dd > guard
    if m.sum() < 100:
        return float('nan')
    z = y[m]
    return float(np.sqrt(np.mean((z - z.mean()) ** 2)) * UVPC)


# ------------------------------------------------------------ 主流程

def main():
    hdr('T66 · 620 kΩ × fc250 —— 实测代价与回退预测')
    say('数据源（T65-0 已校验全过）：四份 215252')
    say('  接入 853on : %s' % F_IN_ON)
    say('  接入 500off: %s' % F_IN_OFF)
    say('  直连 853on : %s' % F_NO_ON)
    say('  直连 500off: %s' % F_NO_OFF)
    say('链：fc250（当前，12 节）／fc150（T58 前，13 节，来自 _bak_T58_20260920）')
    say('FS=%.4f ｜ UVPC=%.4f ｜ 零点 %.0f ｜ 信号＝1 Hz 方波' % (FS, UVPC, ZERO))

    sos250 = T63.parse_profile(T63.F_CUR)
    sos150 = T63.parse_profile(T63.F_BAK)
    nn = 9
    say('系数表：fc250 %d 节 ｜ fc150 %d 节 ｜ notch 段 %d 节'
        % (len(sos250), len(sos150), nn))
    assert len(sos250) == nn + 3 and len(sos150) == nn + 4

    # ---------------- 频响差（先给"数学上限"）
    hdr('A · 两链频响差（含 notch；决定了哪些频段可能变）')
    fq = np.array([1, 5, 10, 40, 50, 100, 150, 155, 160, 180, 200, 250, 300, 350, 400, 426])
    d150 = T63.Hdb(sos150, fq)
    d250 = T63.Hdb(sos250, fq)
    say('%8s %12s %12s %10s %12s' % ('f(Hz)', 'fc150(dB)', 'fc250(dB)', '差(dB)', '放行倍数'))
    for i, q in enumerate(fq):
        say('%8.1f %12.2f %12.2f %+10.2f %12.2f'
            % (q, d150[i], d250[i], d250[i] - d150[i], 10 ** ((d250[i] - d150[i]) / 20)))
    say('★ 判读：≤150 Hz 逐点相同（差 0.00）⇒ fc250 **不可能**改变 1 Hz 方波的平台幅度；')
    say('   差异 100% 落在 >150 Hz ⇒ 只有"通带外的干扰"才会被放进来。')

    # ---------------- B · 现场数据：直连 vs 接入（实测列⑤ ＝ fc250 链真输出）
    hdr('B · 实测（853 域，列⑤＝fc250 链输出）：直连 vs 接入')
    dat = {}
    for key, fn, exp in (('in-on', F_IN_ON, 'on'), ('no-on', F_NO_ON, 'on'),
                         ('in-off', F_IN_OFF, 'off'), ('no-off', F_NO_OFF, 'off')):
        p = os.path.join(HERE, fn)
        d = T56.load7(p)
        dat[key] = d
        st = np.diff(d['idx'].astype(np.int64)).mean()
        say('  %-7s n=%d 坏行=%d idx步长均值=%.4f ⇒ %s'
            % (key, d['n'], d['nbad'], st, '853 域' if abs(st - 1) < .02 else '500 域'))

    say()
    say('测量一律取「最长段 + 去首尾 5 s」')
    for key in ('no-on', 'in-on'):
        d = dat[key]
        sg = segs(d)
        lab, a, b = max(sg, key=lambda s: s[2] - s[1])
        a2, b2 = a + int(5 * FS), b - int(5 * FS)
        raw = (d['raw'][a2:b2] - ZERO) * UVPC
        y5 = d['filt'][a2:b2] * UVPC
        amp_raw, P, _, i0 = fold_amp(raw, FS, 851.0, 856.0)
        amp5, _, _, _ = fold_amp(y5, FS, 851.0, 856.0)
        bl = flat_rms(y5, FS, P, i0)
        say('  [%s] 段 %s 长 %.1f s' % (key, lab, (b2 - a2) / FS))
        say('        列② raw p-p %9.1f µV ｜ 折叠方波幅度 %8.1f µV ｜ rms %9.1f µV'
            % (raw.max() - raw.min(), amp_raw, np.sqrt(np.mean((raw - raw.mean()) ** 2))))
        say('        列⑤ out p-p %9.1f µV ｜ 折叠方波幅度 %8.1f µV ｜ 1s窗p-p中位 %9.1f µV ｜ 平段rms %7.2f µV'
            % (y5.max() - y5.min(), amp5, pp1s_med(y5, FS), bl))
        say('        ⇒ 链增益(方波) = 列⑤/列② = %.4f  ←「幅度偏低」判据位'
            % (amp5 / amp_raw if amp_raw else float('nan')))
        say('        列⑤ 带内 rms(µV)：' +
            '  '.join('%s:%.1f' % (k, v) for k, v in band_rms_uv(d['filt'][a2:b2], FS).items()))

    # ---------------- C · 853 域离线重放（首次！无折叠）
    hdr('C · 853 域离线重放：同一份列② raw 过 fc150 / fc250 两链')
    say('★ 这是 T63 缺失的一步（当时 620k 数据是 500 域 ⇒ 折叠不可逆）。')
    d = dat['in-on']
    sg = segs(d)
    lab, a, b = max(sg, key=lambda s: s[2] - s[1])
    a2, b2 = a + int(5 * FS), b - int(5 * FS)
    raw_uv = (d['raw'][a2:b2] - ZERO) * UVPC
    y5_meas = d['filt'][a2:b2] * UVPC
    say('段：%s ｜ %.1f s ｜ 列② raw：p-p %.1f µV ｜ rms %.1f µV'
        % (lab, (b2 - a2) / FS, raw_uv.max() - raw_uv.min(),
           np.sqrt(np.mean((raw_uv - raw_uv.mean()) ** 2))))

    burn = int(BURN * FS)
    y150 = T63.run_chain(raw_uv, sos150, nn, True)[burn:]
    y250 = T63.run_chain(raw_uv, sos250, nn, True)[burn:]
    ym = y5_meas[burn:]
    say('启动暂态已丢 %.0f s ｜ 稳态 %.1f s' % (BURN, len(ym) / FS))

    P, i0 = None, None
    rows = [('列⑤ 实测(fc250)', ym), ('离线 fc250', y250), ('离线 fc150', y150)]
    say()
    say('%-20s %12s %12s %12s %12s %12s' %
        ('链', 'p-p(µV)', '1s窗p-p中位', '方波幅度', 'rms', '平段rms'))
    amp_ref = None
    for nm, y in rows:
        am, P_, _, i0_ = fold_amp(y, FS, 851.0, 856.0)
        say('%-20s %12.1f %12.1f %12.1f %12.1f %12.2f'
            % (nm, y.max() - y.min(), pp1s_med(y, FS), am,
               np.sqrt(np.mean((y - y.mean()) ** 2)), flat_rms(y, FS, P_, i0_)))
        if nm.startswith('列⑤'):
            amp_ref = am
    say()
    say('★ 模型自证：离线 fc250 应与"列⑤ 实测"逐项接近（同一固件链 + 同一输入）')
    say('★ 结论位：fc250 相对 fc150 的**实测代价** = 见上表两行之比')

    say()
    say('带内 rms 分解（µV）：')
    say('  %-10s %10s %10s %10s %10s %10s' %
        ('链', '2~45', '45~55', '55~145', '145~155', '155~245')) 
    for nm, y in rows:
        b_ = band_rms_uv(y, FS)
        say('  %-10s %10.1f %10.1f %10.1f %10.1f %10.1f'
            % (nm, b_['2~45'], b_['45~55'], b_['55~145'], b_['145~155'], b_['155~245']))
    say()
    say('  %-10s %10s %10s %10s %10s' % ('链', '245~340', '340~426', '0.3~2', '全带'))
    for nm, y in rows:
        b_ = band_rms_uv(y, FS)
        tot = float(np.sqrt(np.mean((y - y.mean()) ** 2)))
        say('  %-10s %10.1f %10.1f %10.1f %10.1f'
            % (nm, b_['245~340'], b_['340~426'], b_['0.3~2'], tot))

    # ---------------- D · 500 域（与 T50 同口径）
    hdr('D · 500 域（`raw853 off`，与 T50 的 98.98% 同域）')
    for key in ('no-off', 'in-off'):
        d = dat[key]
        sg = segs(d)
        onseg = [(s, a, b) for s, a, b in sg if s == 'ON']
        say('  [%s] 段数 %d ｜ %s' % (key, len(sg),
                                    ' → '.join(s for s, _, _ in sg)))
        for k, (s, a, b) in enumerate(onseg):
            a2, b2 = a + int(4 * FS500), b - int(1 * FS500)
            if b2 - a2 < int(2 * FS500):
                say('      ON#%d 太短（%.1f s）⇒ 跳过' % (k, (b - a) / FS500))
                continue
            y4raw = d['dsr'][a2:b2]
            y4 = y4raw * 0.1526          # int16 LSB → µV（0.1526 µV/LSB）
            y5 = d['filt'][a2:b2] * UVPC
            say('      ON#%d %.1f s ｜ 列④ 1s窗p-p中位 %10.1f µV ｜ 列⑤ 同名口径 %10.1f µV ｜ 列⑤/列④=%.4f'
                % (k, (b2 - a2) / FS500, pp1s_med(y4, FS500), pp1s_med(y5, FS500),
                   pp1s_med(y5, FS500) / pp1s_med(y4, FS500) if pp1s_med(y4, FS500) else float('nan')))
            say('            列④ 削顶率 %.2f%% ｜ 列④ p-p %.0f LSB（满量程 65534）'
                % (100.0 * np.mean(np.abs(y4raw) >= 32767), y4raw.max() - y4raw.min()))

    # ---------------- E · 输入阻抗判据（T50 口径）
    hdr('E · 输入阻抗判据（T50 口径：ON 段 列④ 1 s 窗 p-p 中位，比值 = 接入/直连）')
    say('★ 分母=直连、分子=接入；判据 ≤ 1.10（＝110%）。T50（fc150）读数 = 98.98%。')
    for src, nm in (('dsr', '列④（屏幕口径）'), ('filt', '列⑤（×1.3601 等价，不削顶）')):
        vals = {}
        for key in ('no-off', 'in-off'):
            d = dat[key]
            sg = [s for s in segs(d) if s[0] == 'ON']
            vv = []
            for s, a, b in sg:
                a2, b2 = a + int(4 * FS500), b - int(1 * FS500)
                if b2 - a2 < int(2 * FS500):
                    continue
                y = d[src][a2:b2] * (UVPC if src == 'filt' else 1.0)
                vv.append(pp1s_med(y, FS500))
            vals[key] = float(np.mean(vv)) if vv else float('nan')
        r = vals['in-off'] / vals['no-off'] if vals['no-off'] else float('nan')
        say('  %s：直连 %10.1f µV ｜ 接入 %10.1f µV ⇒ 比值 **%.4f = %.2f%%** %s'
            % (nm, vals['no-off'], vals['in-off'], r, 100 * r,
               '达标 ✓' if r <= 1.10 else '**不达标 ✗**'))

    # 853 域同口径（用列⑤；域不同，仅供趋势）
    vals = {}
    for key in ('no-on', 'in-on'):
        d = dat[key]
        sg = [s for s in segs(d) if s[0] == 'ON']
        vv = []
        for s, a, b in sg:
            a2, b2 = a + int(5 * FS), b - int(5 * FS)
            y = d['filt'][a2:b2] * UVPC
            vv.append(pp1s_med(y, FS))
        vals[key] = float(np.mean(vv)) if vv else float('nan')
    r = vals['in-on'] / vals['no-on'] if vals['no-on'] else float('nan')
    say('  列⑤ 853 域（趋势用）：直连 %10.1f ｜ 接入 %10.1f ⇒ 比值 **%.4f = %.2f%%** %s'
        % (vals['no-on'], vals['in-on'], r, 100 * r,
           '达标 ✓' if r <= 1.10 else '**不达标 ✗**'))

    # ---------------- E2 · 输入阻抗：多口径 × 两链（离线预测回退）
    hdr('E2 · 输入阻抗判据的多口径 × 两链（★离线预测「回退 fc150」后会读多少）')
    say('方法：用 **853 域真 raw**（无折叠）驱动两条链 → 丢暂态 → resample500（复现 ecg_out.c）')
    say('      → 链输出本就在 µV 域 ⇒ 与「列⑤」同量纲（列④ = 列⑤×0.7353，比值不受常数影响）。')
    say('🛑 为什么必须离线：500 域数据已被 fc250 处理过，**无法回退**；853 域 raw 是唯一无折叠原料。')

    def chain_to_500(raw_uv, sos, burn_s=5.0):
        y = T63.run_chain(raw_uv, sos, nn, True)
        y = y[int(burn_s * FS):]
        y5, _ = T56.fw_resample500(y)
        return np.asarray(y5, float)

    def collect(key):
        d = dat[key]
        sg = segs(d)
        lab, a, b = max(sg, key=lambda s: s[2] - s[1])
        a2, b2 = a + int(5 * FS), b - int(5 * FS)
        return (d['raw'][a2:b2] - ZERO) * UVPC, (b2 - a2) / FS

    raw_no, Lno = collect('no-on')
    raw_in, Lin = collect('in-on')
    say()
    say('直连段 %.1f s ｜ 接入段 %.1f s' % (Lno, Lin))

    def metrics(y_uv):
        n = int(1.0 * FS500)
        w = [float(np.max(y_uv[i:i + n]) - np.min(y_uv[i:i + n]))
             for i in range(0, len(y_uv) - n, int(0.25 * FS500))]
        w = np.array(w)
        amp, _, _, _ = fold_amp(y_uv, FS500, 499.0, 502.0)
        return dict(pp_full=float(y_uv.max() - y_uv.min()),
                    pp_med=float(np.median(w)),
                    pp_p95=float(np.percentile(w, 95)),
                    pp_max=float(w.max()),
                    amp=amp)

    res = {}
    for tag, sos in (('fc250(当前)', sos250), ('fc150(回退)', sos150)):
        m_no = metrics(chain_to_500(raw_no, sos))
        m_in = metrics(chain_to_500(raw_in, sos))
        res[tag] = (m_no, m_in)

    say()
    say('%-12s %-10s %12s %12s %12s %12s' %
        ('链', '口径', '直连', '接入', '比值', '判决(≤110%)'))
    for tag in res:
        m_no, m_in = res[tag]
        for k, nm in (('pp_full', '全段 p-p'), ('pp_med', '1s窗p-p中位'),
                      ('pp_p95', '1s窗p-p p95'), ('pp_max', '1s窗最大'),
                      ('amp', '方波幅度')):
            r = m_in[k] / m_no[k] if m_no[k] else float('nan')
            say('%-12s %-10s %12.0f %12.0f %11.2f%%  %s'
                % (tag, nm, m_no[k], m_in[k], 100 * r,
                   '✓' if r <= 1.10 else '✗'))
        say()

    say('★ 对照实测（fc250 上板，500 域，见 D/E 段）：')
    say('   · 列④ 1 s 窗 p-p 中位比值 = **102.94%** ⇒ 离线 fc250 应复现同一量级（模型自证）')
    say('   · 若离线 fc250 与实测差得远 ⇒ 说明还有第三变量，须先查再回退。')

    # ---------------- B2 · 逐窗 p-p 分布（S1：判「低于设定值」真伪）
    hdr('B2 · 逐 1 s 窗 p-p 分布（S1：接入后是否存在「低于设定值」的时段）')
    say('都在 853 域、都取列⑤（固件真输出）、段内去首尾 5 s。')
    for key in ('no-on', 'in-on'):
        d = dat[key]
        sg = segs(d)
        lab, a, b = max(sg, key=lambda s: s[2] - s[1])
        a2, b2 = a + int(5 * FS), b - int(5 * FS)
        y = d['filt'][a2:b2] * UVPC
        n = int(1.0 * FS)
        w = np.array([float(y[i:i + n].max() - y[i:i + n].min())
                      for i in range(0, len(y) - n, n)])
        amp, _, _, _ = fold_amp(y, FS, 851.0, 856.0)
        say('  [%s] 窗数 %d ｜ min %.0f / p5 %.0f / p25 %.0f / 中位 %.0f / p75 %.0f / p95 %.0f / max %.0f µV'
            % (key, len(w), w.min(), np.percentile(w, 5), np.percentile(w, 25),
               np.median(w), np.percentile(w, 75), np.percentile(w, 95), w.max()))
        say('        折叠方波幅度 %.1f µV ｜ **低于直连方波幅度 977.0 µV 的窗占比 %.1f%%**'
            % (amp, 100.0 * np.mean(w < 977.0)))

    # ---------------- F · 结论位
    hdr('F · 结论位')
    say('（详细判词见配套报告 T66）')

    io.open(os.path.join(HERE, '_t66_out.txt'), 'w', encoding='utf-8').write('\n'.join(OUT))
    print('\n[written] _t66_out.txt')


if __name__ == '__main__':
    main()
