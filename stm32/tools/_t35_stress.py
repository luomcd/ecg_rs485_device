# -*- coding: utf-8 -*-
"""_t35_stress.py —— 鲁棒性压力测试：把实测屏幕波形里的 150Hz(与100Hz) 分量放大 k 倍，
看 A 档(IMP620 9节) vs B 档(IMP620_N2 13节) 的判决会怎么走。
依据：输入侧干扰涨 k 倍 -> 线性系统 -> 屏幕对应分量也涨 k 倍。"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D="C:/Users/luom/Desktop/ADS1293/"; UV4=0.1526; FS=500.0
F={("A","3mV/4Hz","in"):"3mV-4Hz-620k-in-2026-09-18 093952.txt",
   ("A","3mV/4Hz","noin"):"3mV-4Hz-620k-noin-2026-09-18 094253.txt",
   ("A","5mV/10Hz","in"):"5mV-10Hz-620k-in-2026-09-18 094441.txt",
   ("A","5mV/10Hz","noin"):"5mV-10Hz-620k-noin-2026-09-18 094358.txt",
   ("B","3mV/4Hz","in"):"3mv-4hz-620k-in-2026-09-18 111023.txt",
   ("B","3mV/4Hz","noin"):"3mv-4hz-620k-noin-2026-09-18 111133.txt",
   ("B","5mV/10Hz","in"):"5mv-10hz-620k-in-2026-09-18 111248.txt",
   ("B","5mV/10Hz","noin"):"5mv-10hz-620k-noin-2026-09-18 111214.txt"}
def load(fn):
    v=[]
    for ln in io.open(D+fn,encoding="utf-8",errors="ignore"):
        p=ln.split()
        if len(p)!=7: continue
        try: v.append(int(p[3]))
        except ValueError: pass
    return np.array(v,float)*UV4
def pp_med(x,win=1.0,step=0.25):
    n,s=int(win*FS),int(step*FS)
    if len(x)<=n: return np.ptp(x)
    return float(np.median([np.ptp(x[i:i+n]) for i in range(0,len(x)-n+1,s)]))
def scale(x,lo,hi,k):
    m=x.mean(); y=x-m
    X=np.fft.rfft(y); f=np.fft.rfftfreq(len(y),1/FS)
    X[(f>=lo)&(f<=hi)]*=k
    return np.fft.irfft(X,n=len(y))+m
data={k:load(v) for k,v in F.items()}
print("="*104)
print("鲁棒性压力测试：把屏幕波形的 150Hz 分量放大 k 倍（模拟环境干扰涨 k 倍）")
print("="*104)
print("  %-3s %-9s %8s %10s %10s %9s %8s"%("档","信号","k(倍)","in p-p","noin p-p","比值","判决"))
print("  "+"-"*76)
for g,label in (("A","A IMP620"),("B","B N2")):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        for k in (1,2,4,8):
            a=scale(data[(g,sig,"in")],145,155,k); b=scale(data[(g,sig,"noin")],145,155,k)
            pa,pb=pp_med(a),pp_med(b); r=pa/pb*100
            print("  %-3s %-9s %8d %10.1f %10.1f %8.2f%% %8s"%(label,sig,k,pa,pb,r,"达标" if r<=110 else "★超标"))
        print("  "+"-"*76)
print()
print("同样做 100Hz 分量放大 k 倍：")
print("  %-3s %-9s %8s %10s %10s %9s %8s"%("档","信号","k(倍)","in p-p","noin p-p","比值","判决"))
print("  "+"-"*76)
for g,label in (("A","A IMP620"),("B","B N2")):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        for k in (1,4,8):
            a=scale(data[(g,sig,"in")],95,105,k); b=scale(data[(g,sig,"noin")],95,105,k)
            pa,pb=pp_med(a),pp_med(b); r=pa/pb*100
            print("  %-3s %-9s %8d %10.1f %10.1f %8.2f%% %8s"%(label,sig,k,pa,pb,r,"达标" if r<=110 else "★超标"))
        print("  "+"-"*76)
print()
print("双线同时恶化（150Hz x4 且 100Hz x4）：")
print("  %-3s %-9s %10s %10s %9s %8s"%("档","信号","in p-p","noin p-p","比值","判决"))
print("  "+"-"*68)
for g,label in (("A","A IMP620"),("B","B N2")):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        a=scale(scale(data[(g,sig,"in")],145,155,4),95,105,4)
        b=scale(scale(data[(g,sig,"noin")],145,155,4),95,105,4)
        pa,pb=pp_med(a),pp_med(b); r=pa/pb*100
        print("  %-3s %-9s %10.1f %10.1f %8.2f%% %8s"%(label,sig,pa,pb,r,"达标" if r<=110 else "★超标"))
    print("  "+"-"*68)
