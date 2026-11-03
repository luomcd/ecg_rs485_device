# -*- coding: utf-8 -*-
"""_t36_margin.py —— 精确求 A/B 两档的「临界恶化倍数 k*」(判决达到 110%)，
并把它与「实测跨天波动带」放在一起，回答「价值在哪、有没必要替代」。"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D = "C:/Users/luom/Desktop/ADS1293/"; UV4 = 0.1526; FS = 500.0
F = {("A","3mV/4Hz","in"):"3mV-4Hz-620k-in-2026-09-18 093952.txt",
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
    return float(np.median([np.ptp(x[i:i+n]) for i in range(0,len(x)-n+1,s)])) if len(x)>n else float(np.ptp(x))
def scale(x,lo,hi,k):
    y=x-x.mean(); X=np.fft.rfft(y); f=np.fft.rfftfreq(len(y),1/FS)
    X[(f>=lo)&(f<=hi)]*=k
    return np.fft.irfft(X,n=len(y))+x.mean()
S={k:load(v) for k,v in F.items()}
def ratio_at(g,sig,k,lo=145,hi=155):
    a=scale(S[(g,sig,"in")],lo,hi,k); b=scale(S[(g,sig,"noin")],lo,hi,k)
    return pp_med(a)/pp_med(b)*100
print("="*104)
print("临界恶化倍数 k*（判决恰好 = 110%）—— 越大的 k* 越鲁棒")
print("="*104)
print("  %-16s %10s %12s %12s %12s"%("档/信号","k*(临界)","余量(k*/1)","1x判决","8x判决"))
print("  "+"-"*70)
out={}
for g,lab in (("A","A IMP620(9节)"),("B","B N2(13节)")):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        ks=np.arange(1.0,17.01,0.25)
        rr=[ratio_at(g,sig,k) for k in ks]
        ks_ = None
        for i in range(1,len(ks)):
            if rr[i-1]<110<=rr[i]:
                ks_ = ks[i-1]+(110-rr[i-1])*(ks[i]-ks[i-1])/(rr[i]-rr[i-1]); break
        out[(g,sig)]=(ks_,rr[0],rr[-1])
        print("  %-16s %10s %12.2f %11.2f%% %11.2f%%"
              %(lab+"/"+sig, ("%.2f"%ks_) if ks_ else ">17", (ks_ or 99), rr[0], rr[-1]))
    print("  "+"-"*70)
print()
print("【对照】实测跨天波动（raw 域 150Hz，同一测台、同 620k 条件）：")
for lab,v in (("09-17 那批", 356.7),("09-18 09:39 (A组)", 785.1),("09-18 11:10 (B组)", 878.0)):
    print("   %-18s %8.1f µV"% (lab, v))
lo,hi=356.7,878.0
print("   ⇒ 观测跨度 = %.1f / %.1f = **%.2f x**"%(hi,lo,hi/lo))
print()
kA=out[("A","3mV/4Hz")][0]; kB=out[("B","3mV/4Hz")][0]
print("★ A 档 3mV 组：k* = %.2f，而实测已见 %.2f x ⇒ 剩余余量 = %.2f x（再恶化 %.0f%% 即击穿）"
      %(kA,hi/lo,(kA/(hi/lo)),100*(kA/(hi/lo)-1)))
print("★ B 档 3mV 组：k* = %s，实测 %.2f x ⇒ 余量 = %s"
      %(("%.2f"%kB) if kB else ">17",hi/lo,("%.1f x"%(kB/(hi/lo))) if kB else ">17x"))
print()
print("【真实 ECG 尺度的外推】R 波 1mV 时允许 Δ 仅 100µV（现测 3mV 允许 302µV）")
print("   Δ 是绝对量（含 0.22~0.47mV 宽带地板 + 陷波残余），不随信号幅度缩放")
for g,lab in (("A","A"),("B","B")):
    d = pp_med(S[(g,"3mV/4Hz","in")]) - pp_med(S[(g,"3mV/4Hz","noin")])
    print("   %s 档：实测 Δ = %7.1f µV ⇒ 若信号降到 1mV，比值 ≈ 100%% + Δ/1000µV = **%.1f%%** %s"
          %(lab, d, 100+d/1000*100, "达标" if 100+d/10<=110 else "★超标"))
