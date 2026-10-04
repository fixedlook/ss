"""
Signed order flow: does it move price (Kyle) AND persist (Cont-Kukanov-Stoikov)?

The divergence framing we killed asked "does flow DISAGREEING with price predict a
reversal".  The quant literature uses flow the other way round: contemporaneous
signed flow moves price (dp = lambda*q), and if q is autocorrelated then q predicts
the NEXT return.  Tested here, 1-min bars, Fama-MacBeth across sessions, no bands
and no levels involved.  Signal at the close of minute t, returns measured from
t+1 onward, so there is no lookahead.

Run:  /tmp/venv/bin/python research/flow_persistence.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['delta'] = df.ask_volume - df.bid_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
day = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
day = {d: g for d, g in day.items() if len(g) >= 200}

ac1, ac5, p1, p5, tr_net, tr_n, tr_win = [], [], [], [], [], [], []
for d, b in day.items():
    sv = b.delta.values.astype(float)
    vol = b.flow.values.astype(float)
    C = b.close.values.astype(float)
    r = np.r_[np.nan, np.diff(C)]
    ok = np.isfinite(r) & (vol > 0)
    if ok.sum() < 150:
        continue
    s, rr, vv = sv[ok], r[ok], vol[ok]
    if np.std(s) > 0:
        ac1.append(np.corrcoef(s[:-1], s[1:])[0, 1])
        if len(s) > 6:
            ac5.append(np.corrcoef(s[:-5], s[5:])[0, 1])
    # predictive: flow today -> return over the next 1 and next 5 minutes
    f = s / vv                                   # OFI-style normalised imbalance
    if np.std(f) > 0 and len(f) > 20:
        fwd1 = np.r_[rr[1:], np.nan]
        fwd5 = np.full(len(rr), np.nan)
        for t in range(len(rr) - 6):          # return from close t+1 to close t+6
            fwd5[t] = C[t + 6] - C[t + 1]
        m1 = np.isfinite(fwd1)
        m5 = np.isfinite(fwd5)
        if m1.sum() > 50:
            p1.append(np.corrcoef(f[m1], fwd1[m1])[0, 1])
        if m5.sum() > 50:
            p5.append(np.corrcoef(f[m5], fwd5[m5])[0, 1])
    # and can you trade it: enter on a top-third imbalance, hold 5 min, 1 pt cost
    thr = np.quantile(np.abs(f), 2 / 3)
    for i in range(len(f) - 6):
        if abs(f[i]) < thr or not np.isfinite(rr[i + 1]):
            continue
        side = 1.0 if f[i] > 0 else -1.0
        pnl = side * (C[i + 6] - C[i + 1]) - 1.0
        tr_net.append(pnl)
        tr_n.append(1)
        tr_win.append(pnl > 0)


def fm(x, lab):
    v = np.array(x)
    v = v[np.isfinite(v)]
    mu, sd = v.mean(), v.std(ddof=1)
    print(f"  {lab:<52} {mu:>+8.4f}   t = {mu/(sd/np.sqrt(len(v))):>+6.1f}   n={len(v):,}")


print(f"sessions {len(day):,}")
print("\n" + "=" * 100)
print("PERSISTENCE OF SIGNED FLOW  (1-min net ask-bid volume)")
print("=" * 100)
fm(ac1, "autocorrelation lag 1")
fm(ac5, "autocorrelation lag 5")

print("\n" + "=" * 100)
print("PREDICTIVE REGRESSION  corr( imbalance_t , return from t+1 )")
print("=" * 100)
fm(p1, "imbalance -> next 1-minute return")
fm(p5, "imbalance -> next 5-minute return")

print("\n" + "=" * 100)
print("TRADE IT: enter on |imbalance| in the top third, hold 5 minutes, 1 pt cost")
print("=" * 100)
t = np.array(tr_net)
print(f"  trades {len(t):,}   net {t.mean():+.3f} pts/trade  (${2*t.mean():+.2f}/MNQ)   "
      f"win rate {(t>0).mean():.1%}   t = {t.mean()/(t.std(ddof=1)/np.sqrt(len(t))):+.1f}")
print(f"  gross {t.mean()+1:+.3f} pts/trade")
