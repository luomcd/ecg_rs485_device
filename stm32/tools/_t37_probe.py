# -*- coding: utf-8 -*-
"""_t37_probe.py —— 细查 B 3mV in 的 150Hz 屏幕残留异常"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D="C:/Users/luom/Desktop/ADS1293/"; UV4,UV2,ZERO=0.1526,0.1122,6075000.0; FS=500.0

def load(fn):
    a={k:[] for k in ("raw","i16","f5")}
    for ln in io.open(D+fn,encoding="utf-8",errors="ignore"):
        p=ln.split()
        if len(p)!=7: continue
        try:
            a["raw"].append(int(p[1])); a["i16"].append(int(p[3])); a["f5"].append(float(p[4]))
        except ValueError: pass
    return {k:np.array(v) for k,v in a.items()}

def spec(x,f0,half=2.0):
    y=np.asarray(x,float); y=y-y.mean(); N=len(y)
    if N<64: return 0.0,0.0
    w=np.hanning(N); X=np.abs(np.fft.rfft(y*w))/(w.sum()/2.0)
    f=np.fft.rfftfreq(N,1/FS); m=(f>=f0-half)&(f<=f0+half); i=np.where(m)[0]
    if len(i)==0: return 0.0,0.0
    k=i[np.argmax(X[i])]; return X[k], f[k]

F={"A3":"3mV-4Hz-620k-in-2026-09-18 093952.txt",
   "B3":"3mv-4hz-620k-in-2026-09-18 111023.txt",
   "A5":"5mV-10Hz-620k-in-2026-09-18 094441.txt",
   "B5":"5mv-10hz-620k-in-2026-09-18 111248.txt"}
S={k:load(v) for k,v in F.items()}

print("="*104)
print("[A] 逐 5s 段：150Hz 峰值（列④ 显示 vs 列⑤ 滤波原值）")
print("="*104)
for k in ("B3","B5","A3","A5"):
    i4=S[k]["i16"].astype(float)*UV4; i5=S[k]["f5"]*UV2
    n=int(5*FS); print("  --- %s (%d 行 = %.1f s) ---"%(k,len(i5),len(i5)/FS))
    print("    %-8s %22s %22s"%("段起点","列④ 150Hz@频率","列⑤ 150Hz@频率"))
    for s in range(0,len(i5)-n,n):
        p4,f4=spec(i4[s:s+n],150.0); p5,f5_=spec(i5[s:s+n],150.0)
        print("    %-8.0f %10.3f @ %7.2f Hz %10.3f @ %7.2f Hz"%(s/FS,p4,f4,p5,f5_))
    p4,f4=spec(i4,150.0,half=2.0); p5,f5_=spec(i5,150.0,half=2.0)
    print("    %-8s %10.3f @ %7.2f Hz %10.3f @ %7.2f Hz"%("全段",p4,f4,p5,f5_))
    print()

print("="*104)
print("[B] 全谱扫描 100~200 Hz（列⑤，620k 接入组，找峰位）")
print("="*104)
for k in ("B3","B5","A3","A5"):
    x=S[k]["f5"]*UV2; y=x-x.mean(); N=len(y); w=np.hanning(N)
    X=np.abs(np.fft.rfft(y*w))/(w.sum()/2.0); f=np.fft.rfftfreq(N,1/FS)
    m=(f>=100)&(f<=200); i=np.where(m)[0]
    top=i[np.argsort(X[i])[::-1][:5]]
    print("  %-3s : "%k + "   ".join("%.2fHz:%.3fµV"%(f[j],X[j]) for j in sorted(top,key=lambda z:f[z])))

print()
print("="*104)
print("[C] 列④ 与 列⑤ 的差（去均值）谱峰 —— 看两者差在哪一段频率")
print("="*104)
for k in ("B3","B5"):
    a=S[k]["i16"].astype(float)*UV4; b=S[k]["f5"]*UV2
    n=min(len(a),len(b)); d=a[:n]-b[:n]; d=d-d.mean()
    w=np.hanning(n); X=np.abs(np.fft.rfft(d*w))/(w.sum()/2.0); f=np.fft.rfftfreq(n,1/FS)
    i=np.argsort(X)[::-1][:6]
    print("  %-3s 差值rms=%.3fµV  前6谱峰: "%(k,np.sqrt(np.mean(d**2))) +
          "   ".join("%.2fHz:%.4f"%(f[j],X[j]) for j in sorted(i,key=lambda z:f[z])))
    print("      (0~250Hz 内 150Hz 处差值 = %.4f µV)"%X[np.argmin(np.abs(f-150.0))])
