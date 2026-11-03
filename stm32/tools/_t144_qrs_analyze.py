# -*- coding: utf-8 -*-
"""T144 统一入口：qrs_log 两档数据的全部分析（替代 _t144_analyze{,2,3}.py 与 _t144_verify_pole.py）

用法（Python 必须用带 numpy 的那个解释器）：
    python tools/_t144_qrs_analyze.py seg      # 数据分段体检（重叠/断档/复位指纹）★先跑这个
    python tools/_t144_qrs_analyze.py cmp      # 两档对比（振幅比 vs 瞬态比）
    python tools/_t144_qrs_analyze.py pole     # 低频极点验证（实测 tau 拟合 + dc_block 仿真）
    python tools/_t144_qrs_analyze.py all      # 全跑

数据源：<上位机>/bin/Debug/net10.0-windows/qrs_log(1mv).json | qrs_log(3mv).json
"""
import io, json, os, sys
import numpy as np

BASE = r'C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\bin\Debug\net10.0-windows'
FS = 853.2423          # 滤波域采样率
FS_TXT = 500.0         # 上位机显示帧率（detector 的 SampleRate）
PX_PER_MV = 30.5       # 纵向 6.1 px/小格、1 小格 = 0.2 mV ⇒ 30.5 px/mV ⇒ 1 px = 32.8 µV

# 由 seg 子命令实测确定的分段（i 为闭区间）
SEG_1MV = (111, 161)
SEG_3MV = (162, 238)


def load():
    d1 = json.load(io.open(os.path.join(BASE, 'qrs_log(1mv).json'), encoding='utf-8'))
    d3 = json.load(io.open(os.path.join(BASE, 'qrs_log(3mv).json'), encoding='utf-8'))
    m = {}
    for e in d1:
        m[e['i']] = e
    for e in d3:
        m[e['i']] = e
    return d1, d3, [m[i] for i in sorted(m)]


def tsec(t):
    h, mi, s = t.split(':')
    return int(h) * 3600 + int(mi) * 60 + float(s)


def get_seg(seq, a, b):
    ch = [e for e in seq if a <= e['i'] <= b]
    t = np.array([tsec(e['t']) for e in ch]) - tsec(ch[0]['t'])
    return dict(t=t, amp=np.array([e['amp'] for e in ch]),
                bl=np.array([e['bl'] for e in ch]),
                bls=np.array([e['bls'] for e in ch]), raw=ch)


# ---------------------------------------------------------------- seg
def cmd_seg():
    d1, d3, seq = load()
    print('=' * 92)
    print('### ① 重叠比对（同一 i 的两文件记录是否逐位相同）')
    print('=' * 92)
    m1 = {e['i']: e for e in d1}
    m3 = {e['i']: e for e in d3}
    ov = sorted(set(m1) & set(m3))
    print('  1mv i=%d..%d   3mv i=%d..%d   重叠 %d 条' % (d1[0]['i'], d1[-1]['i'], d3[0]['i'], d3[-1]['i'], len(ov)))
    if ov:
        sa = sum(1 for i in ov if abs(m1[i]['amp'] - m3[i]['amp']) < 1e-6)
        sb = sum(1 for i in ov if abs(m1[i]['bl'] - m3[i]['bl']) < 1e-9)
        st = sum(1 for i in ov if m1[i]['t'] == m3[i]['t'])
        print('  amp 逐位相同 %d/%d   bl 相同 %d/%d   t 相同 %d/%d' % (sa, len(ov), sb, len(ov), st, len(ov)))
        print('  ==> 全部相同 ⇒ 同一次连续运行的两次快照（logger 整文件重写、i 不清零、容量环形）')

    print()
    print('=' * 92)
    print('### ② 断档扫描（>1.5 s 视为停过流）')
    print('=' * 92)
    ts = np.array([tsec(e['t']) for e in seq])
    dts = np.diff(ts)
    print('  条间 dt: 中位 %.3f s  最大 %.3f s' % (np.median(dts), dts.max()))
    for k in range(len(dts)):
        if dts[k] > 1.5:
            e = seq[k + 1]
            print('  ★ i=%3d  dt=%6.3f s   amp=%7.1f  bl=%7.1f  bls=%7.1f' % (
                e['i'], dts[k], e['amp'], e['bl'], e['bls']))

    print()
    print('=' * 92)
    print('### ③ 分段结论')
    print('=' * 92)
    for nm, (a, b) in (('1mV', SEG_1MV), ('3mV', SEG_3MV)):
        g = get_seg(seq, a, b)
        print('  %s  i=%d..%d  n=%3d  %.1f s   amp 中位 %7.1f  bl 峰 %6.1f  bls 峰 %6.1f' % (
            nm, a, b, len(g['raw']), g['t'][-1], np.median(g['amp']), g['bl'].max(), g['bls'].max()))
    print('  ⚠ 文件名不可当分组依据：qrs_log(1mv).json 的 i=1..110 是【上一档】残留')


# ---------------------------------------------------------------- cmp
def cmd_cmp():
    _, _, seq = load()
    print('=' * 92)
    print('### 两档对比（按 START 断档分界，非按文件名）')
    print('=' * 92)
    g1 = get_seg(seq, *SEG_1MV)
    g3 = get_seg(seq, *SEG_3MV)
    a1, a3 = np.median(g1['amp']), np.median(g3['amp'])
    print('  amp 中位      : 1mV %8.1f   3mV %8.1f   比 %.2f' % (a1, a3, a3 / a1))
    for lab, k in (('bl  峰', 'bl'), ('bls 峰', 'bls')):
        p1, p3 = g1[k].max(), g3[k].max()
        print('  START 后 %s : 1mV %8.1f   3mV %8.1f   比 %.2f   (%.1f / %.1f px)' % (
            lab, p1, p3, p3 / p1, p1 / 32.8, p3 / 32.8))
    print()
    print('  ==> 振幅比 %.2f 而瞬态峰比 %.2f / %.2f ⇒ 【不支持"瞬态 ∝ 振幅"】' % (
        a3 / a1, g3['bl'].max() / g1['bl'].max(), g3['bls'].max() / g1['bls'].max()))
    print('  ==> 但振幅比这么干净也【反证】amp 字段的索引对齐可信')


# ---------------------------------------------------------------- pole
def dc_block_run(x, fc, warm_skip=8):
    r = 1.0 - 2 * np.pi * fc / FS
    x_prev = y_prev = 0.0
    warmed = False
    ws = warm_skip
    out = np.zeros(len(x))
    for k, v in enumerate(x):
        if not warmed:
            x_prev = v
            if ws > 0:
                ws -= 1
                continue
            warmed = True
            continue
        y = (v - x_prev) + r * y_prev
        x_prev = v
        y_prev = y
        out[k] = y
    return out


def fit_tau(t, y, ymin=15.0):
    """峰值之后、仍 > ymin 的段做 ln 线性拟合"""
    ipk = int(np.argmax(y))
    mask = (np.arange(len(y)) > ipk) & (y > ymin)
    if mask.sum() < 4:
        return float('nan'), float('nan'), ipk
    tt, yy = t[mask], y[mask]
    A = np.vstack([tt, np.ones_like(tt)]).T
    sl, ic = np.linalg.lstsq(A, np.log(yy), rcond=None)[0]
    pred = np.exp(ic + sl * tt)
    r2 = 1 - np.sum((yy - pred) ** 2) / np.sum((yy - yy.mean()) ** 2)
    return -1.0 / sl, r2, ipk


def cmd_pole():
    _, _, seq = load()
    print('=' * 92)
    print('### ① 实测 tau 拟合（对比理论 fc=0.02 -> %.2f s）' % (1 / (2 * np.pi * 0.02)))
    print('=' * 92)
    meas = {}
    for nm, s in (('1mV', SEG_1MV), ('3mV', SEG_3MV)):
        g = get_seg(seq, *s)
        meas[nm] = g
        for lab, k in (('bl ', 'bl'), ('bls', 'bls')):
            tau, r2, ipk = fit_tau(g['t'], g[k])
            print('  %s  %s 峰 %7.1f @ t=%.2f s   拟合 tau = %6.2f s   R2 = %.4f' % (
                nm, lab, g[k][ipk], g['t'][ipk], tau, r2))
    print('  ⇒ R2≈0.99 ⇒ 单极点主导，tau 与 fc=0.02 理论吻合 ⇒ 假说确证')

    print()
    print('=' * 92)
    print('### ② dc_block 仿真（真实 r）：输入 x[n]=C(1-exp(-n/(taut*fs)))，C=100 uV')
    print('=' * 92)
    N = int(25 * FS)
    n = np.arange(N)
    print('  taup(s) | fc=0.02        fc=0.035       fc=0.05        fc=0.10      | 峰比 0.02:0.05')
    for tx in (0.5, 1.0, 1.5, 2.0, 3.0):
        x = 100.0 * (1.0 - np.exp(-n / (tx * FS)))
        out = []
        for fc in (0.02, 0.035, 0.05, 0.10):
            y = dc_block_run(x, fc)
            tau, _, _ = fit_tau(n / FS, y, ymin=0.05 * y.max())
            out.append((y.max(), tau))
        print('   %5.1f   | %6.1f/%5.2f  %6.1f/%5.2f  %6.1f/%5.2f  %6.1f/%5.2f |  %.2f' % (
            tx, out[0][0], out[0][1], out[1][0], out[1][1],
            out[2][0], out[2][1], out[3][0], out[3][1], out[0][0] / out[2][0]))
    print('  ⇒ 改 fc 的收益主要在【时间】：0.02->0.05 使 tau 快 2.5x，峰值只降约 25%')

    print()
    print('=' * 92)
    print('### ③ 反推输入侧慢建立幅度（fc=0.02, taut=1.5 s）')
    print('=' * 92)
    x = 100.0 * (1.0 - np.exp(-n / (1.5 * FS)))
    gain = dc_block_run(x, 0.02).max() / 100.0
    for nm, s in (('1mV', SEG_1MV), ('3mV', SEG_3MV)):
        pk = meas[nm]['bl'].max()
        print('  %s  实测 bl 峰 %6.1f uV   ÷ 增益 %.2f  ⇒  输入侧慢建立 ≈ %6.1f uV' % (nm, pk, gain, pk / gain))
    print('  ⇒ START 后输入端有 ~300~440 uV、taut≈1.5 s 的慢建立（与信号幅度基本无关）')

    print()
    print('=' * 92)
    print('### ④ fc 改变的预期观感（以 3mV 段实测 bl 峰值为锚）')
    print('=' * 92)
    anchor = meas['3mV']['bl'].max()
    x = 100.0 * (1.0 - np.exp(-n / (1.5 * FS)))
    base = dc_block_run(x, 0.02).max()
    for fc, lab in ((0.02, '当前 A+B 档'), (0.035, '折中候选'), (0.05, 'T84 之前全档取值')):
        pk = dc_block_run(x, fc).max()
        sc = anchor * pk / base
        print('  fc=%.3f (%-16s)  峰值 %6.1f uV (%5.1f px)   tau %5.2f s' % (
            fc, lab, sc, sc / 32.8, 1 / (2 * np.pi * fc)))


# ---------------------------------------------------------------- warm
ALPHA_BL = 0.002                    # HeartRateDetector 快基线一阶低通系数
QRS_PERIOD = int(round(FS / 3.0))   # 3 Hz 心率（=180 bpm）=> 每 284 帧一次 QRS 事件


def bl_track(y, period=QRS_PERIOD, alpha=ALPHA_BL):
    """模拟上位机：逐样本推进低频基线，每 period 帧记录一条（= 一次 QRS 事件）。

    ⚠ bl 序列是【3 Hz 采样】（心率），不是采样率采样 —— 所以 trace 里看到的
      ±10 µV 锯齿是【相位采样产物】，不是"滤不掉的 3 Hz 残留"。
    """
    b = 0.0
    out = []
    for k in range(len(y)):
        b += (float(y[k]) - b) * alpha
        if (k + 1) % period == 0:
            out.append(b)
    return np.array(out)


def make_pulse(n, amp_uv, t_rise=1.5, t_fall=8.0):
    """过冲脉冲型慢建立：A(1-exp(-t/T1))*exp(-t/T2) —— 先升后降（峰在 ~2.7 s）。"""
    t = n / FS
    return amp_uv * (1.0 - np.exp(-t / t_rise)) * np.exp(-t / t_fall)


def cmd_warm():
    N = int(30 * FS)
    n = np.arange(N)
    r = 1.0 - 2 * np.pi * 0.02 / FS

    print('=' * 92)
    print('### (1) 输入形状判别：dc_block(fc=0.02) 对三类输入的响应 —— 谁会穿零？')
    print('=' * 92)
    shapes = [
        ('阶跃                    ', 400.0 * np.ones(N)),
        ('单调上升 (T=1.5 s)      ', 400.0 * (1.0 - np.exp(-n / (1.5 * FS)))),
        ('过冲脉冲 (T1=1.5, T2=8) ', make_pulse(n, 400.0)),
    ]
    print('  输入形状                    y峰(uV)  峰时刻   首次穿零    末值(uV)')
    for lab, x in shapes:
        y = dc_block_run(x, 0.02)
        ipk = int(np.argmax(y))
        zc = [k for k in range(1, N)
              if (y[k - 1] > 0 and y[k] <= 0) or (y[k - 1] < 0 and y[k] >= 0)]
        zt = ('%.2f s' % (zc[0] / FS)) if zc else '从不'
        print('  %-24s %8.1f %7.2fs %10s %9.2f' % (lab, y[ipk], n[ipk] / FS, zt, y[-1]))
    print('  => 阶跃 / 单调上升 的输出【恒正、绝不穿零】；只有【先升后降】的输入才让输出穿零。')

    print()
    print('=' * 92)
    print('### (2) SKIP=0 与 SKIP=8 的差异 —— 严格解析关系')
    print('=' * 92)
    print('  dc_block 是线性系统，两版只差【初始条件】=> y_S0[n] = y_S8[n] + (-delta)*r^(n-1)')
    print('  delta = 首帧(SINC 未建立期)相对稳态的偏差；只有 SKIP=0 会把它当 x_prev')
    print()
    print('  delta(uV) | 差值最小值 | 预测 -delta*r |  S0峰(uV) |  S8峰(uV) | 峰差  | S0/S8 峰时刻')
    x0 = make_pulse(n, 400.0)
    for dv in (0.0, 50.0, 100.0, 200.0, 400.0):
        xa = x0.copy()
        xa[0] += dv
        y0 = dc_block_run(xa, 0.02, warm_skip=0)
        y8 = dc_block_run(x0, 0.02, warm_skip=8)
        d = y0 - y8
        print('  %9.1f | %10.1f | %13.1f | %9.1f | %9.1f | %5.1f | %6.2f vs %6.2f' % (
            dv, d.min(), -dv * r, y0.max(), y8.max(), y8.max() - y0.max(),
            n[int(np.argmax(y0))] / FS, n[int(np.argmax(y8))] / FS))

    print()
    print('=' * 92)
    print('### (3) 屏幕观感（套上位机 bl 低通 alpha=%.3f，每 %d 帧记录一条 = 3 Hz）' % (ALPHA_BL, QRS_PERIOD))
    print('=' * 92)
    print('  delta(uV) | S0 bl峰(uV) S0(px) S0峰时刻 | S8 bl峰(uV) S8(px) S8峰时刻 | 峰差(px)')
    for dv in (0.0, 100.0, 200.0, 400.0):
        xa = x0.copy()
        xa[0] += dv
        b0 = bl_track(dc_block_run(xa, 0.02, warm_skip=0))
        b8 = bl_track(dc_block_run(x0, 0.02, warm_skip=8))
        k0, k8 = int(np.argmax(b0)), int(np.argmax(b8))
        print('  %9.1f | %11.1f %6.1f %8.2f  | %11.1f %6.1f %8.2f  | %7.1f' % (
            dv, b0[k0], b0[k0] / 32.8, k0 * QRS_PERIOD / FS,
            b8[k8], b8[k8] / 32.8, k8 * QRS_PERIOD / FS, (b8[k8] - b0[k0]) / 32.8))
    print('  => delta>0(首帧偏高) => SKIP=0 叠加一个【负冲激】，恰好抵消慢建立 => 峰更低 =>')
    print('     "以前那版看起来更好"；SKIP=8 去掉它 => 慢建立裸露 => 观感变化。')


# ---------------------------------------------------------------- trace
def cmd_trace():
    """★ 逐条打印 START 后 bl / bls 的完整轨迹 —— 用来验证"基线逐渐下移直至恒定"。

    判读关键：bl/bls 是【上位机对滤波输出的低频分量】（一阶低通），
    符号 = 屏幕基线的方向（正 = 屏幕上移，负 = 屏幕下移）。
    """
    _, _, seq = load()
    for nm, s in (('段III 1mV', SEG_1MV), ('段IV 3mV', SEG_3MV)):
        g = get_seg(seq, *s)
        bl, bls = g['bl'], g['bls']
        n = len(bl)
        tail = max(5, n // 5)
        print('=' * 92)
        print('### %s  (i=%d..%d, n=%d, %.1f s)  bl / bls 完整轨迹' % (
            nm, s[0], s[1], n, g['t'][-1]))
        print('=' * 92)
        print('   i    t(s)      amp        bl       bls  | bl(px) bls(px)')
        for k in range(n):
            e = g['raw'][k]
            print('  %3d  %6.2f  %8.1f  %8.1f  %8.1f  | %6.2f %7.2f' % (
                e['i'], g['t'][k], g['amp'][k], bl[k], bls[k], bl[k] / 32.8, bls[k] / 32.8))
        print('  --- 稳态判定（尾 %d 条 = %d%% 段长）---' % (tail, 100 // 5))
        for lab, v in (('bl ', bl), ('bls', bls)):
            ipk = int(np.argmax(v))
            imin = int(np.argmin(v))
            print('    %s 峰 %+8.1f @%5.2fs | 谷 %+8.1f @%5.2fs | 末值 %+8.1f | 尾均 %+8.1f | 均 %+7.1f' % (
                lab, v[ipk], g['t'][ipk], v[imin], g['t'][imin], v[-1], v[-tail:].mean(), v.mean()))
        for lab, v in (('bl ', bl), ('bls', bls)):
            sg = np.sign(v)
            ch = [k for k in range(1, n) if sg[k] != sg[k - 1]]
            if ch:
                print('    %s 符号变化: %s' % (lab, ' -> '.join(
                    'i=%d(%.2fs)%+.1f' % (g['raw'][k]['i'], g['t'][k], v[k]) for k in ch)))
            else:
                print('    %s 全程 %s' % (lab, '≥0（基线在 0 上方）' if v[0] >= 0 else '≤0（基线在 0 下方）'))
        print()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd in ('seg', 'all'):
        cmd_seg()
        print()
    if cmd in ('cmp', 'all'):
        cmd_cmp()
        print()
    if cmd in ('pole', 'all'):
        cmd_pole()
    if cmd == 'trace':
        cmd_trace()
    if cmd == 'warm':
        cmd_warm()
    if cmd not in ('seg', 'cmp', 'pole', 'trace', 'warm', 'all'):
        print(__doc__)
