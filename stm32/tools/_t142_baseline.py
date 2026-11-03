# -*- coding: utf-8 -*-
"""T142: Start 基线随机偏移 —— 定量核算（只读分析，不改固件）
口径: FS=853.2423208191125 Hz (DRDY 1172us) | UVPC=0.1122 uV/code | UVLSB=0.1526 uV/int16LSB
v2 修正: 上一版 tau_s() 误返回「样本数」当「秒」，本节已改为 1/(2*pi*fc) 秒 / FS/(2*pi*fc) 样本。
"""
import math
FS  = 853.2423208191125
UVPC, UVLSB = 0.1122, 0.1526

tau_s = lambda fc: 1.0/(2*math.pi*fc)        # 秒
tau_n = lambda fc: FS/(2*math.pi*fc)         # 样本
rr    = lambda fc: 1.0-2*math.pi*fc/FS

print("="*78)
print("[1] dc_block 一阶高通常数  y[n]=(x[n]-x[n-1])+r*y[n-1]")
print(f"{'fc(Hz)':>7} {'r':>17} {'alpha':>12} {'tau(s)':>8} {'tau(样本)':>10}")
for fc in (0.02, 0.05, 0.5):
    print(f"{fc:>7} {rr(fc):>17.12f} {2*math.pi*fc/FS:>12.6e} {tau_s(fc):>8.4f} {tau_n(fc):>10.1f}")
print("  A+B 投产档 fc=0.02Hz  ->  tau = 7.9577 s  <<== 本次讨论档")

print("="*78)
print("[2] ADS1293 ECG SINC 建立时间（datasheet SNAS602C 8.3.8；TI E2E 确认 N*M=5）")
t5 = 5.0/FS*1000
print(f"  ts-ECG = 5 输出样本 = {t5:.3f} ms @ ODR={FS:.2f}Hz")
print(f"  SKIP=8 帧 = {8/FS*1000:.3f} ms  =  5 帧的 {8/5:.2f} 倍（余量 {(8/5-1)*100:.0f}%）")

print("="*78)
print("[3] 现象3 校验：瞬态 0.8mV --(tau=7.9577s)--> 0.06mV 需多久")
print(f"  t = tau*ln(A0/A1) = 7.9577*ln({0.8/0.06:.3f}) = {tau_s(0.02)*math.log(0.8/0.06):.2f} s")
print(f"{'t(s)':>6} {'残留(mV)':>10} {'int16LSB':>10} {'小格':>7}")
for tt in (5,10,15,20,25,30,40):
    v = 0.8*math.exp(-tt/tau_s(0.02))
    print(f"{tt:>6} {v:>10.4f} {v*1000/UVLSB:>10.1f} {v/0.2:>7.2f}")
print("  -> 20 s 时残留 0.065 mV，与猛哥观察的「恢复到 0.06mV 附近」吻合")

print("="*78)
print("[4] 现象2 校验：若 0.09mV 纯瞬态，20s 后应剩多少")
v20 = 0.09*math.exp(-20/tau_s(0.02))
print(f"  0.09 * exp(-20/7.9577) = {v20:.5f} mV = {v20*1000/UVLSB:.3f} int16LSB = {v20/0.2:.4f} 小格")
print("  -> 远低于可辨阈值(约 0.1 小格) => 0.09mV 不可能全是瞬态，必含【准持久分量】")

print("="*78)
print("[5] 准持久分量反解：dc_block 对线性漂移的稳态输出 y_ss = s/(1-r) = s*tau_n")
tn = tau_n(0.02)
for mv in (0.06, 0.09, 0.10):
    code = mv*1000.0/UVPC
    s = code/tn
    print(f"  屏幕 {mv:.2f}mV = {code:>5.0f} code <= 漂移率 {s:.4f} code/样本"
          f" = {s*FS*UVPC:.2f} uV/s = {s*FS*UVPC*60/1000:.3f} mV/min")
print("  -> Ag/AgCl 电极极化漂移典型 <1 mV/min  => 量级吻合")

print("="*78)
print("[6] 幅度换算表（屏幕 1 小格=0.2mV / 5 小格=1mV / 纵向 ±5mV）")
print(f"{'偏移':>8} {'int16LSB':>9} {'raw code':>9} {'小格':>7} {'占±5mV':>8}")
for mv in (0.02,0.06,0.09,0.10,0.20,0.80):
    print(f"{mv:>6.2f}mV {mv*1000/UVLSB:>9.0f} {mv*1000/UVPC:>9.0f} {mv/0.2:>7.2f} {mv/10*100:>7.2f}%")

print("="*78)
print("[7] 修法收益：预热窗 = SKIP 8 + M 64 = 72 帧 = %.1f ms" % (72/FS*1000))
print(f"{'M':>5} {'起点sigma(code)':>16} {'(mV)':>8} {'窗内漂移偏(code)':>16}")
for M in (1,16,64,128,256):
    e = 500.0/math.sqrt(M)
    print(f"{M:>5} {e:>16.1f} {e*UVPC/1000:>8.4f} {0.1181*M/2:>16.1f}")
print("  (raw 单样本噪声 500 code 为保守假设；窗内漂移偏 = s*M/2，可忽略)")
