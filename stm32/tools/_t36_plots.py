# -*- coding: utf-8 -*-
"""_t36_plots.py —— 「今天这版 vs 最后验收那版」价值对比图（3 面板）"""
import io, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

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
def bandamp(x,f0,half=1.0):
    xx=x-x.mean(); N=len(xx); w=np.hanning(N)
    X=np.abs(np.fft.rfft(xx*w))/(w.sum()/2.0); f=np.fft.rfftfreq(N,1/FS)
    m=(f>=f0-half)&(f<=f0+half); i=np.where(m)[0]
    return X[i[np.argmax(X[i])]]
S={k:load(v) for k,v in F.items()}
C_A,C_B,C_NG,C_OK,C_G="鼎".replace("鼎","#C0392B"),"#1F77B4","#D62728","#1E8449","#7F8C8D"

fig=plt.figure(figsize=(15.2,9.6))
gs=fig.add_gridspec(2,2,height_ratios=[1,1],hspace=0.34,wspace=0.22)

# ── (a) 判决 vs 恶化倍数 k ─────────────────────────────────────────
ax=fig.add_subplot(gs[0,:])
ks=np.arange(1.0,9.01,0.25)
for g,sig,c,ls,lab in (("A","3mV/4Hz","#C0392B","-","A IMP620(9节) 3mV/4Hz"),
                       ("A","5mV/10Hz","#E67E22","--","A IMP620(9节) 5mV/10Hz"),
                       ("B","3mV/4Hz","#1F77B4","-","B N2(13节) 3mV/4Hz"),
                       ("B","5mV/10Hz","#17BECF","--","B N2(13节) 5mV/10Hz")):
    rr=[pp_med(scale(S[(g,sig,"in")],145,155,k))/pp_med(scale(S[(g,sig,"noin")],145,155,k))*100 for k in ks]
    ax.plot(ks,rr,ls,color=c,lw=2.3,label=lab)
    ax.scatter([ks[-1]],[rr[-1]],s=26,color=c,zorder=5)
ax.axhline(110,color="#C0392B",lw=1.6,ls=":")
ax.text(8.92,110.5,"验收门限 110%",ha="right",fontsize=10,color="#C0392B",fontweight="bold")
ax.axvspan(1,2.46,color="#1E8449",alpha=0.10)
ax.axvline(2.46,color="#1E8449",lw=1.3,ls="-.")
ax.text(1.72,113.2,"实测跨天波动 2.46x\n(356.7 -> 878.0 uV, raw域150Hz)",ha="center",fontsize=9.2,color="#1E8449",fontweight="bold")
ax.scatter([3.89],[110],s=130,marker="v",color="#C0392B",zorder=6)
ax.annotate("A 档临界 k* = 3.89\n(A 档只剩 1.58x 余量)",xy=(3.89,110),xytext=(4.35,124),
            fontsize=10,color="#C0392B",fontweight="bold",
            arrowprops=dict(arrowstyle="->",color="#C0392B",lw=1.4))
ax.text(6.5,101.6,"B 档 k*>17（本图范围内恒平）",fontsize=10.5,color="#1F77B4",fontweight="bold")
ax.set_xlabel("150 Hz 干扰相对实测值恶化倍数 k",fontsize=11.5)
ax.set_ylabel("判决  p-p(620k) / p-p(开路)  %",fontsize=11.5)
ax.set_title("(a) 价值就在这张图：1x 下两条线重叠（没差别），k 一涨 A 档掉队",fontsize=13,pad=9)
ax.set_xlim(1,9); ax.set_ylim(99,136)
ax.grid(alpha=0.22); ax.legend(fontsize=10,loc="upper left",ncol=2)

# ── (b) Δ 的成分分解 ──────────────────────────────────────────────
ax=fig.add_subplot(gs[1,0])
cats=["A IMP620\n(9节)","B N2\n(13节)"]
res150=[bandamp(S[("A","3mV/4Hz","in")],150.0),bandamp(S[("B","3mV/4Hz","in")],150.0)]
res100=[bandamp(S[("A","3mV/4Hz","in")],100.0),bandamp(S[("B","3mV/4Hz","in")],100.0)]
delt=[pp_med(S[(g,"3mV/4Hz","in")])-pp_med(S[(g,"3mV/4Hz","noin")]) for g in ("A","B")]
rest=[max(d-0.0,0) for d in delt]
other=[max(rest[i]-res150[i]-res100[i],0) for i in range(2)]
x=np.arange(2); w=0.42
ax.bar(x,res150,w,color="#C0392B",label="150 Hz 残余（N2 压掉的就是它）")
ax.bar(x,res100,w,bottom=res150,color="#E67E22",label="100 Hz 残余")
ax.bar(x,other,w,bottom=np.array(res150)+np.array(res100),color="#95A5A6",label="其余 = 宽带地板 + Δ 抖动")
for i in range(2):
    ax.text(x[i],rest[i]+4,"Δ = %.1f uV"%delt[i],ha="center",fontsize=10.5,fontweight="bold")
    ax.text(x[i],res150[i]/2,"%.1f"%res150[i],ha="center",va="center",fontsize=10,color="white",fontweight="bold")
ax.annotate("N2 只把这一块从 23.96 压到 0.13 uV\n= 总 Δ 的 15%；另 85% 是未解的地板",
            xy=(1.21,other[1]*0.62),xytext=(0.32,86),fontsize=9.6,color="#444",
            arrowprops=dict(arrowstyle="->",color="#444",lw=1.1))
ax.set_xticks(x); ax.set_xticklabels(cats,fontsize=11)
ax.set_ylabel("Δ 绝对量 (uV)",fontsize=11.5)
ax.set_ylim(0,232)
ax.set_title("(b) 但 Δ 的大头不是 150 Hz —— 是那条 0.22~0.47 mV 地板",fontsize=12.6,pad=9)
ax.legend(fontsize=9.4,loc="upper left",bbox_to_anchor=(0.015,0.99),framealpha=0.95); ax.grid(axis="y",alpha=0.22)

# ── (c) 真实信号尺度外推 ─────────────────────────────────────────
ax=fig.add_subplot(gs[1,1])
amp=np.linspace(0.5,5,120)
for g,c,lab in (("A","#C0392B","A IMP620(9节)"),("B","#1F77B4","B N2(13节)")):
    d=pp_med(S[(g,"3mV/4Hz","in")])-pp_med(S[(g,"3mV/4Hz","noin")])
    ax.plot(amp,100+d/amp/1000*100,"-",color=c,lw=2.4,label=lab)
ax.axhline(110,color="#C0392B",lw=1.6,ls=":")
ax.axvline(1.0,color="#555",lw=1.2,ls="--")
ax.text(1.05,122,"真实 R 波\n~1 mV",fontsize=9.6,color="#555",fontweight="bold")
ax.text(4.75,110.6,"110%",ha="right",fontsize=10,color="#C0392B",fontweight="bold")
ax.text(3.0,116,"两档都超标 -> 门槛不在陷波，在地板",fontsize=10.2,color="#C0392B",fontweight="bold",ha="center")
ax.set_xlabel("ECG 信号幅度（mV pp）",fontsize=11.5)
ax.set_ylabel("判决 %",fontsize=11.5)
ax.set_xlim(0.5,5); ax.set_ylim(100,128)
ax.set_title("(c) Δ 是绝对量 -> 信号越小越危险（与档位无关）",fontsize=12.6,pad=9)
ax.grid(alpha=0.22); ax.legend(fontsize=10,loc="upper right")

fig.suptitle("IMP620_N2（今天）vs IMP620（最后验收版）：价值只存在于「150 Hz 恶化」这一条轴上",
             fontsize=15,fontweight="bold",y=0.985)
OUT="C:/work2026/VsPro/ads1293_demo/tools/T36_价值对比_N2vsIMP620_2026-09-18.png"
fig.savefig(OUT,dpi=140,bbox_inches="tight",facecolor="white")
print("saved",OUT)
