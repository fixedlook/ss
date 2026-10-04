"""
Does the MNQ 1-minute price process mean-revert at all?  The foundation.

Every reversal strategy rests on an assumption nobody has tested here: that the
intraday price process has NEGATIVE short-horizon autocorrelation (VR < 1), i.e.
that moves tend to be undone.  If returns are a martingale (rho = 0, VR = 1) then
no entry rule, however clever, can beat a barrier structure - the barrier payoff
is a martingale functional.  If rho > 0 (momentum) it is worse than a coin flip.

Fama-MacBeth: one number per session, then the cross-session mean and t-stat, so
quiet and wild sessions get equal weight and the daily kernel does not dominate.
Lo-MacKinlay variance ratio VR(q) = Var(q-min ret)/(q*Var(1-min ret)); < 1 is
mean reversion, > 1 is trending.

Run:  /tmp/venv/bin/python research/process_scaling.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
day = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
day = {d: g for d, g in day.items() if len(g) >= 200}
print(f"sessions {len(day):,}")

LAGS = [1, 2, 3, 5, 10, 20, 40, 60]
ac = {k: [] for k in LAGS}
vr = {q: [] for q in [2, 5, 10, 30, 60]}
vr_5m = {k: [] for k in [1, 2, 3]}
w1 = []

for d, b in day.items():
    C = b.close.values.astype(float)
    r = np.diff(C)
    if len(r) < 150 or np.std(r) == 0:
        continue
    w1.append(np.std(r))
    x = r - r.mean()
    den = x @ x
    for k in LAGS:
        if len(x) > k + 60:
            ac[k].append((x[:-k] @ x[k:]) / den)
    for q in [2, 5, 10, 30, 60]:
        if len(r) > q * 4:
            rq = np.convolve(r, np.ones(q), 'valid')
            if np.var(r) > 0:
                vr[q].append(np.var(rq) / (q * np.var(r)))
    # 5-minute bars: the timescale the CvdDivergence study actually runs on
    m = (len(r) // 5) * 5
    if m > 100:
        r5 = r[:m].reshape(-1, 5).sum(1)
        x5 = r5 - r5.mean()
        den5 = x5 @ x5
        for k in [1, 2, 3]:
            if len(x5) > k + 30:
                vr_5m[k].append((x5[:-k] @ x5[k:]) / den5)


def fm(v, lab):
    v = np.array(v)
    v = v[np.isfinite(v)]
    mu, sd = v.mean(), v.std(ddof=1)
    t = mu / (sd / np.sqrt(len(v)))
    return f"  {lab:<44} {mu:>+9.4f}   t = {t:>+7.1f}   n={len(v):,}"


print(f"\nmedian 1-min sigma {np.median(w1):.2f} pts")
print("\n" + "=" * 96)
print("AUTOCORRELATION of 1-minute returns (negative = mean reversion)")
print("=" * 96)
for k in LAGS:
    print(fm(ac[k], f"rho({k})"))

print("\n" + "=" * 96)
print("VARIANCE RATIO of 1-minute returns (< 1 = mean reversion, = 1 = random walk)")
print("=" * 96)
for q in [2, 5, 10, 30, 60]:
    print(fm(vr[q], f"VR({q})"))
print("\n  implied VR(inf) from the ACF: "
      f"{1 + 2*sum(np.mean(ac[k]) for k in LAGS if k in (1,2,3,5,10,20,40,60)):.3f}")

print("\n" + "=" * 96)
print("5-MINUTE BARS - the timescale the CvdDivergence study runs on")
print("=" * 96)
for k in [1, 2, 3]:
    print(fm(vr_5m[k], f"rho({k}) of 5-min returns"))

print("""
How to read it
  rho < 0 and VR < 1  -> the process genuinely undoes moves: a reversal bet has a
                         real (if small) positive expectancy BEFORE costs, and the
                         only question is whether costs eat it.
  rho ~ 0 and VR ~ 1  -> martingale.  No barrier structure can have positive
                         expectancy: every SL/TP combination is a fair game minus
                         the spread, which is exactly what the 63-cell grid showed.
  rho > 0             -> trending: reversal bets lose systematically, which is what
                         the negative k in the first-passage fit suggested.""")
