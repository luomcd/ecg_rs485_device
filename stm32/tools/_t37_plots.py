# -*- coding: utf-8 -*-
"""_t37_plots.py —— A(IMP620 9节) vs B(IMP620_N2 13节) 正面对比图，只用当天 8 文件"""
import io, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
plt.rcParams["font.sans-serif"]=["Microsoft YaHei"]; plt.rcParams["axes.unicode_minus"]=False

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
    a={k:[] for k in ("raw","i16")}
    for ln in io.open(D+fn,encoding="utf-8",errors="ignore"):
        p=ln.split()
        if len(p)!=7: continue
        try: a["raw"].append(int(p[1])); a["i16"].append(int(p[3]))
        except ValueError: pass
    return {k:np.array(v,dtype=float) for k,v in a.items()}
def pp_med(x,win=1.0,step=0.25):
    n,s=int(win*FS),int(step*FS)
    if len(x)<=n: return float(np.ptp(x))
    return float(np.median([np.ptp(x[i:i+n]) for i in range(0,len(x)-n+1,s)]))
def peak(x,f0,half=1.0):
    y=np.asarray(x,float); y=y-y.mean(); N=len(y); w=np.hanning(N)
    X=np.abs(np.fft.rfft(y*w))/(w.sum()/2.0); f=np.fft.rfftfreq(N,1/FS)
    m=(f>=f0-half)&(f<=f0+half); i=np.where(m)[0]; k=i[np.argmax(X[i])]
    return X[k],f[k]
def scale(x,lo,hi,k):
    y=x-x.mean(); X=np.fft.rfft(y); f=np.fft.rfftfreq(len(y),1/FS)
    X[(f>=lo)&(f<=hi)]*=k
    return np.fft.irfft(X,n=len(y))+x.mean()
S={k:load(v) for k,v in FILES.items()}
s4={k:S[k]["i16"]*UV4 for k in S}
raww={k:(S[k]["raw"]-ZERO)*UV2 for k in S}
C_A,C_B="#C0392B","#1F77B4"; C_AG,C_BG="#E8AFA6","#A9C7E8"

fig=plt.figure(figsize=(15.4,10.0))
gs=fig.add_gridspec(2,2,hspace=0.36,wspace=0.24)

# (a) 主判据
ax=fig.add_subplot(gs[0,0])
labels=["A\n3mV/4Hz","A\n5mV/10Hz","B\n3mV/4Hz","B\n5mV/10Hz"]
noin=[pp_med(s4[("A","3mV/4Hz","noin")]),pp_med(s4[("A","5mV/10Hz","noin")]),
      pp_med(s4[("B","3mV/4Hz","noin")]),pp_med(s4[("B","5mV/10Hz","noin")])]
vin=[pp_med(s4[("A","3mV/4Hz","in")]),pp_med(s4[("A","5mV/10Hz","in")]),
     pp_med(s4[("B","3mV/4Hz","in")]),pp_med(s4[("B","5mV/10Hz","in")])]
x=np.arange(4); w=0.36
ax.bar(x-w/2,noin,w,color=[C_AG,C_AG,C_BG,C_BG],edgecolor="#666",lw=0.7,label="开路 noin")
ax.bar(x+w/2,vin,w,color=[C_A,"#E67E22",C_B,"#17BECF"],edgecolor="#333",lw=0.8,label="620k 接入")
for i in range(4):
    r=vin[i]/noin[i]*100
    ax.text(x[i]+w/2,vin[i]+60,"%.2f%%"%(r),ha="center",fontsize=10.5,fontweight="bold",
            color="#1E8449" if r<=110 else "#C0392B")
    ax.text(x[i]+w/2,240,"Δ%+.0f"%(vin[i]-noin[i]),ha="center",fontsize=9.4,color="#111",fontweight="bold")
ax.axhline(0,color="#888",lw=0.6)
ax.set_xticks(x); ax.set_xticklabels(labels,fontsize=10)
ax.set_ylabel("p-p (uV, 1s 滑窗中位数)",fontsize=11)
ax.set_ylim(0,6000)
ax.set_title("(a) 主判据：两版都达标，比值差 0.76pp 在噪声内",fontsize=12.6,pad=9)
ax.legend(fontsize=9.6,loc="upper left"); ax.grid(axis="y",alpha=0.22)

# (b) raw 域：干扰没变
ax=fig.add_subplot(gs[0,1])
fr=[50.,100.,150.]; x=np.arange(3); w=0.2
vals={("A","3mV/4Hz"):[15345.6,774.7,785.1],("A","5mV/10Hz"):[15799.5,793.5,796.1],
      ("B","3mV/4Hz"):[19129.9,630.7,878.0],("B","5mV/10Hz"):[21127.2,660.0,920.0]}
cols={"A":"#C0392B","B":"#1F77B4"}
for i,(g,sig) in enumerate((("A","3mV/4Hz"),("B","3mV/4Hz"),("A","5mV/10Hz"),("B","5mV/10Hz"))):
    ax.bar(x+(i-1.5)*w,vals[(g,sig)],w,color=cols[g],alpha=1.0 if sig=="3mV/4Hz" else 0.55,
           hatch="" if sig=="3mV/4Hz" else "///",edgecolor="#333",lw=0.6,
           label="%s %s"%(g,sig))
ax.set_xticks(x); ax.set_xticklabels(["50 Hz","100 Hz","150 Hz"],fontsize=11)
ax.set_yscale("log"); ax.set_ylim(100,60000)
ax.set_ylabel("raw 域峰值 (uV, 0.1122uV/code)",fontsize=11)
ax.annotate("B 组 50Hz 反而高 25~34%\n(15346->19130)\n=> 抓包时环境更脏",xy=(0.30,19130),xytext=(0.62,30000),
            fontsize=9.6,color=C_B,fontweight="bold",arrowprops=dict(arrowstyle="->",color=C_B,lw=1.3))
ax.set_title("(b) 原始域：干扰本身没变（B 组反而更强）",fontsize=12.6,pad=9)
ax.legend(fontsize=8.4,ncol=2,loc="lower left"); ax.grid(axis="y",alpha=0.22)

# (c) 屏幕域残余
ax=fig.add_subplot(gs[1,0])
sc={("A","3mV/4Hz"):[16.77,23.96],("B","3mV/4Hz"):[0.13,0.05],
    ("A","5mV/10Hz"):[18.08,25.52],("B","5mV/10Hz"):[0.57,0.48]}
x=np.arange(2); w=0.2
for i,(g,sig) in enumerate((("A","3mV/4Hz"),("B","3mV/4Hz"),("A","5mV/10Hz"),("B","5mV/10Hz"))):
    ax.bar(x+(i-1.5)*w,sc[(g,sig)],w,color=cols[g],alpha=1.0 if sig=="3mV/4Hz" else 0.55,
           hatch="" if sig=="3mV/4Hz" else "///",edgecolor="#333",lw=0.6,label="%s %s"%(g,sig))
ax.set_xticks(x); ax.set_xticklabels(["100 Hz","150 Hz"],fontsize=11)
ax.set_yscale("log"); ax.set_ylim(0.02,95)
ax.axhline(1.0,color="#1E8449",ls=":",lw=1.4)
ax.text(1.42,1.12,"1 uV",ha="right",fontsize=9.2,color="#1E8449",fontweight="bold")
ax.set_ylabel("屏幕域残余 (uV, 列④)",fontsize=11)
ax.text(0.0,58,"压 131.5x",ha="center",fontsize=11,color=C_B,fontweight="bold")
ax.text(1.0,58,"压 447.9x",ha="center",fontsize=11,color=C_B,fontweight="bold")
ax.text(0.0,44,"(16.77 -> 0.13 uV)",ha="center",fontsize=9,color="#444")
ax.text(1.0,44,"(23.96 -> 0.05 uV)",ha="center",fontsize=9,color="#444")
ax.set_title("(c) 屏幕域：新档把 100/150Hz 压到亚 uV",fontsize=12.6,pad=9)
ax.grid(axis="y",alpha=0.22)

# (d) 压力测试
ax=fig.add_subplot(gs[1,1])
ks=np.arange(1.0,8.01,0.25)
for g,sig,c,ls,lab in (("A","3mV/4Hz","#C0392B","-","A 3mV/4Hz"),
                       ("A","5mV/10Hz","#E67E22","--","A 5mV/10Hz"),
                       ("B","3mV/4Hz","#1F77B4","-","B 3mV/4Hz"),
                       ("B","5mV/10Hz","#17BECF","--","B 5mV/10Hz")):
    rr=[pp_med(scale(s4[(g,sig,"in")],145,155,k))/pp_med(scale(s4[(g,sig,"noin")],145,155,k))*100 for k in ks]
    ax.plot(ks,rr,ls,color=c,lw=2.4,label=lab)
ax.axhline(110,color="#C0392B",ls=":",lw=1.6)
ax.text(7.9,110.6,"门限 110%",ha="right",fontsize=10,color="#C0392B",fontweight="bold")
ax.axvspan(1,1.12,color="#1E8449",alpha=0.13)
ax.axvline(1.12,color="#1E8449",lw=1.3,ls="-.")
ax.text(2.1,113.0,"当天 A->B 实测波动 1.12x\n(raw域150Hz 785->878uV)",ha="center",
        fontsize=9.4,color="#1E8449",fontweight="bold")
ax.scatter([3.89],[110],s=130,marker="v",color="#C0392B",zorder=6)
ax.annotate("A 档临界 k*=3.89\n=> 余量仅 3.47x",xy=(3.89,110),xytext=(4.3,122),
            fontsize=10,color="#C0392B",fontweight="bold",
            arrowprops=dict(arrowstyle="->",color="#C0392B",lw=1.4))
ax.text(5.4,101.3,"B 档 k*>17（本图内恒平）",fontsize=10.4,color="#1F77B4",fontweight="bold")
ax.set_xlabel("150Hz 干扰相对实测值放大倍数 k",fontsize=11)
ax.set_ylabel("判决 %",fontsize=11)
ax.set_xlim(1,8); ax.set_ylim(99,132)
ax.set_title("(d) 价值所在：1x 下两版重叠，k 一涨 A 档掉队",fontsize=12.6,pad=9)
ax.legend(fontsize=9,loc="upper left",ncol=2); ax.grid(alpha=0.22)

fig.suptitle("A = IMP620(9节, 09:39~09:44)  vs  B = IMP620_N2(13节, 11:10~11:15)   —— 仅用 2026-09-18 当天 8 个文件",
             fontsize=14.5,fontweight="bold",y=0.985)
OUT="C:/work2026/VsPro/ads1293_demo/tools/T37_AB对比_IMP620vsN2_2026-09-18.png"
fig.savefig(OUT,dpi=140,bbox_inches="tight",facecolor="white")
print("saved",OUT)
