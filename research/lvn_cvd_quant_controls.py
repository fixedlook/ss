import numpy as np, pandas as pd
E = pd.read_parquet('/tmp/flow/quant_box.parquet')
E['year']=E.date.str[:4]
sess={d:k for k,d in enumerate(sorted(E.date.unique()))}
SID=E.date.map(sess).values; K=len(sess)
rng=np.random.default_rng(4); W=rng.multinomial(K,np.full(K,1./K),size=1200).astype(float)
def st(m):
    v=E.pnl100.values[m]; s=SID[m]
    N=np.bincount(s,minlength=K).astype(float); S=np.bincount(s,weights=v,minlength=K)
    with np.errstate(invalid='ignore',divide='ignore'): mm=(W@S)/(W@N)
    mm=mm[np.isfinite(mm)]; lo,hi=np.percentile(mm,[2.5,97.5])
    r=E.res.values[m]; w=E.win.values[m]; rn=int(np.nansum(r))
    return dict(n=int(m.sum()),res=r.mean() if len(r) else np.nan,
                wr=float(np.nanmean(w[r==1])) if rn else np.nan,
                g=v.mean(),lo=lo,hi=hi)
arr=E.arr.astype(bool); div=E.div10.astype(bool); hi=E.fr>=0.70; d20=E.div20.astype(bool)
rows=[('BOX  arr & div',                       arr&div),
      ('ctrl div, NO level',                   div&~arr),
      ('BOX  arr & div & highflow',            arr&div&hi),
      ('ctrl div & highflow, NO level',        div&hi&~arr),
      ('ctrl highflow, no div, NO level',      hi&~div&~arr),
      ('BOX  arr & div & div20',               arr&div&d20),
      ('ctrl div & div20, NO level',           div&d20&~arr),
      ('ctrl every minute',                    pd.Series(True,index=E.index)),
      ('ctrl every minute & highflow',         hi),
      ('ctrl every minute & NOT highflow',     ~hi)]
print(f"{'sample':<36} | {'n':>7} {'res%':>5} {'win|res':>8} | {'gross':>7} {'net':>7} | {'CI(net)':>16}")
for lab,m in rows:
    s=st(m); print(f"{lab:<36} | {s['n']:>7,} {s['res']:>4.0%} {s['wr']:>7.1%} | {s['g']:>+6.2f} {s['g']-1:>+6.2f} | [{s['lo']-1:>+5.2f},{s['hi']-1:>+5.2f}]")
print()
print("EDGE OF THE BOX OVER ITS MATCHED CONTROL (net pts)")
for lab_b, mb, lab_c, mc in [('arr & div', arr&div, 'div only', div&~arr),
                             ('arr & div & highflow', arr&div&hi, 'div & highflow, no level', div&hi&~arr),
                             ('arr & div & div20', arr&div&d20, 'div & div20, no level', div&d20&~arr)]:
    Nb=np.bincount(SID[mb],minlength=K).astype(float); Sb=np.bincount(SID[mb],weights=E.pnl100.values[mb],minlength=K)
    Nc=np.bincount(SID[mc],minlength=K).astype(float); Sc=np.bincount(SID[mc],weights=E.pnl100.values[mc],minlength=K)
    with np.errstate(invalid='ignore',divide='ignore'): d=(W@Sb)/(W@Nb)-(W@Sc)/(W@Nc)
    d=d[np.isfinite(d)]; lo,hi=np.percentile(d,[2.5,97.5])
    print(f"  {lab_b:<24} minus {lab_c:<26} {d.mean():>+6.2f} pts  CI [{lo:>+6.2f},{hi:>+6.2f}]")
print()
print("resolved vs unresolved inside the base box")
ARR=np.asarray(arr); DIV=np.asarray(div); D20=np.asarray(d20)
HI=np.asarray((E.fr>=0.70).fillna(False).astype(bool))
RES=E.res.values; PNL=E.pnl100.values
for lab, m in [('base box', ARR & DIV), ('base + highflow', ARR & DIV & HI),
               ('base + div20', ARR & DIV & D20)]:
    r = m & (RES == 1); u = m & (RES == 0)
    print(f"  {lab:<18} resolved n={int(r.sum()):>5,} P&L {PNL[r].mean():+6.2f} | "
          f"unresolved n={int(u.sum()):>5,} mark {PNL[u].mean():+6.2f} | "
          f"gross {PNL[m].mean():+6.2f}")
