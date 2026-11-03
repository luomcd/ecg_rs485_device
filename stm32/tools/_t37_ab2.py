# -*- coding: utf-8 -*-
"""_t37_ab2.py —— 修正版 A/B 对比：屏幕域频域一律用列④（重采样后），列⑤ 仅用于时域/低频。
只用 2026-09-18 当天 8 个文件。"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D="C:/Users/luom/Desktop/ADS1293/"; UV4,UV2,ZERO=0.1526,0.1122,6075000.0; FS=500.0
FILES={("A","3mV/4Hz","noin"):"3mV-4Hz-620k-noin-2026-09-18 094253.txt",
       ("A","3mV/4Hz","in"):"3mV-4Hz-620k-in-2026-09-18 093952.txt",
       ("A","5mV/10Hz","noin"):"5mV-10Hz-620k-noin-2026-09-18 094358.txt",
       ("A","5mV/10Hz","in"):"5mV-10Hz-620k-in-2026-09-18 094441.txt",
       ("B","3mV/4Hz","noin"):"3mv-4hz-620k-noin-2026-09-18 111133.txt",
       ("B","3mV/4Hz","in"):"3mv-4hz-620k-in-2026-09-18 111023.txt",
       ("B","5mV/10Hz","noin"):"5mv-10hz-620k-noin-2026-09-18 111214.txt",
       ("B","5mV/10Hz","in"):"5mv-10hz-620k-in-2026-09-18 111248.txt"}
def load(fn):
    a={k:[] for k in ("raw","i16","f5")}
    for ln in io.open(D+fn,encoding="utf-8",errors="ignore"):
        p=ln.split()
        if len(p)!=7: continue
        try: a["raw"].append(int(p[1])); a["i16"].append(int(p[3])); a["f5"].append(float(p[4]))
        except ValueError: pass
    return {k:np.array(v) for k,v in a.items()}
def pp_med(x,win=1.0,step=0.25):
    n,s=int(win*FS),int(step*FS)
    if len(x)<=n: return float(np.ptp(x))
    return float(np.median([np.ptp(x[i:i+n]) for i in range(0,len(x)-n+1,s)]))
def peak(x,f0,half=1.0):
    y=np.asarray(x,float); y=y-y.mean(); N=len(y)
    if N<64: return 0.0,0.0
    w=np.hanning(N); X=np.abs(np.fft.rfft(y*w))/(w.sum()/2.0); f=np.fft.rfftfreq(N,1/FS)
    m=(f>=f0-half)&(f<=f0+half); i=np.where(m)[0]
    k=i[np.argmax(X[i])]; return X[k],f[k]
S={k:load(v) for k,v in FILES.items()}

print("="*114)
print("[1] 主判据（列④ 显示口径 / 0.1526µV/LSB，p-p = 1s 滑窗中位数）")
print("="*114)
print("  %-10s %-9s %10s %10s %10s %10s" % ("组","信号","noin(µV)","in(µV)","比值","Δ(µV)"))
print("  "+"-"*66)
R={}
for g,lab in (("A","A IMP620"),("B","B N2")):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        pn=pp_med(S[(g,sig,"noin")]["i16"].astype(float)*UV4)
        pi=pp_med(S[(g,sig,"in")]["i16"].astype(float)*UV4)
        R[(g,sig)]=(pn,pi,pi/pn*100)
        print("  %-10s %-9s %10.1f %10.1f %9.2f%% %10.1f"%(lab,sig,pn,pi,pi/pn*100,pi-pn))
    print("  "+"-"*66)

print()
print("="*114)
print("[2] 屏幕域工频线（列④，620k 接入组）—— 用列④ 而非列⑤（列⑤ 有抽取杂散，见 [5]）")
print("="*114)
print("  %-8s %18s %18s %10s %18s %18s %10s"%("频点","A 3mV","B 3mV","压制","A 5mV","B 5mV","压制"))
print("  "+"-"*106)
sc={}
for f0 in (50.,100.,150.):
    r=[]
    for g,sig in (("A","3mV/4Hz"),("B","3mV/4Hz"),("A","5mV/10Hz"),("B","5mV/10Hz")):
        a,fa=peak(S[(g,sig,"in")]["i16"].astype(float)*UV4,f0)
        sc[(g,sig,f0)]=(a,fa); r.append((a,fa))
    print("  %-8.0f %8.2f@%4.2fHz %8.2f@%4.2fHz %9.1fx %8.2f@%4.2fHz %8.2f@%4.2fHz %9.1fx"
          %(f0,r[0][0],r[0][1],r[1][0],r[1][1],r[0][0]/max(r[1][0],1e-9),
              r[2][0],r[2][1],r[3][0],r[3][1],r[2][0]/max(r[3][0],1e-9)))
print("  "+"-"*106)

print()
print("="*114)
print("[3] 原始域（列②，未滤波）50/100/150/200 Hz 峰值与峰位 —— 证明干扰本身没变")
print("="*114)
print("  %-14s %-9s %18s %18s %18s %18s"%("组/接线","信号","50Hz","100Hz","150Hz","200Hz"))
print("  "+"-"*112)
ra={}
for g in ("A","B"):
    for wire in ("noin","in"):
        for sig in ("3mV/4Hz","5mV/10Hz"):
            x=(S[(g,sig,wire)]["raw"].astype(float)-ZERO)*UV2
            v=[peak(x,f0) for f0 in (50.,100.,150.,200.)]
            ra[(g,sig,wire)]=[z[0] for z in v]
            print("  %-14s %-9s %s"%(("%s %s"%(g,"620k接入" if wire=="in" else "开路")),sig,
                  " ".join("%9.1f@%5.2f"%(z[0],z[1]) for z in v)))
    print("  "+"-"*112)
print("  【50Hz 峰位 ×3 vs 150Hz 峰位（谐波是否严格成比例）】")
for g in ("A","B"):
    for sig in ("3mV/4Hz","5mV/10Hz"):
        x=(S[(g,sig,"in")]["raw"].astype(float)-ZERO)*UV2
        p50,f50=peak(x,50.); p150,f150=peak(x,150.)
        print("    %s %-9s  50Hz@%.4f  ×3=%.4f   150Hz实测@%.4f   偏差 %+.4f Hz"
              %(g,sig,f50,f50*3,f150,f150-f50*3))

print()
print("="*114)
print("[4] raw -> 屏幕 净衰减 dB（列② -> 列④）")
print("="*114)
print("  %-8s %12s %12s %12s %12s"%("频点","A 3mV","B 3mV","A 5mV","B 5mV"))
print("  "+"-"*62)
for f0 in (50.,100.,150.):
    v=[]
    for g,sig in (("A","3mV/4Hz"),("B","3mV/4Hz"),("A","5mV/10Hz"),("B","5mV/10Hz")):
        pr=ra[(g,sig,"in")][(50.,100.,150.,200.).index(f0)]
        v.append(20*np.log10(sc[(g,sig,f0)][0]/pr))
    print("  %-8.0f %12.1f %12.1f %12.1f %12.1f"%(f0,*v))

print()
print("="*114)
print("[5] ★ 列⑤ 的适用边界（列⑤ vs 列④ 同段对比）")
print("="*114)
print("  %-6s %14s %14s %10s %14s %14s"%("组","列④ 150Hz","列⑤ 150Hz","倍数","列④ 100Hz","列⑤ 100Hz"))
print("  "+"-"*80)
for g,sig in (("A","3mV/4Hz"),("B","3mV/4Hz"),("A","5mV/10Hz"),("B","5mV/10Hz")):
    a4,f4=peak(S[(g,sig,"in")]["i16"].astype(float)*UV4,150.)
    a5,f5=peak(S[(g,sig,"in")]["f5"]*UV2,150.)
    b4,_=peak(S[(g,sig,"in")]["i16"].astype(float)*UV4,100.)
    b5,_=peak(S[(g,sig,"in")]["f5"]*UV2,100.)
    print("  %-6s %9.3f@%4.2fHz %9.3f@%4.2fHz %9.1fx %9.3f %9.3f"%(g,a4,f4,a5,f5,a5/max(a4,1e-9),b4,b5))
print("  "+"-"*80)
print("  ⇒ 100Hz 以下两列一致（<1.05x）；150Hz 处列⑤ 显著偏大 ⇒ 屏幕域频域一律用列④。")
