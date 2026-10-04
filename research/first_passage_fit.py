"""
The SL/TP grid read as an empirical FIRST-PASSAGE surface.

Theory.  If the log-price near an entry behaves like Brownian motion with drift
mu and variance sigma^2, the probability that the target (+TP) is touched before
the stop (-SL) is

        p(SL,TP) = (1 - e^{-k*SL}) / (1 - e^{-k*(SL+TP)}),      k = 2*mu/sigma^2

For k = 0 (no drift, a martingale) this collapses to the barrier ratio

        p0 = SL / (SL + TP)          <- the null every grid cell must beat

so k is one number that says how much drift the market has PER POINT of travel.
Its reciprocal 1/k is the distance over which the drift becomes visible: at
barrier distances << 1/k the market is indistinguishable from a coin flip, no
matter how good the setup is.  That is the mathematical reason a 30-point stop
cannot harvest an edge that only shows up over 100+ points.

We fit k to every arm's 63 cells (resolved races only, so session-end censoring
drops out), with inverse-variance weights, and check whether k is stable across
barrier scales (a crude scale-invariance test of the Brownian assumption).

Run:  /tmp/venv/bin/python research/first_passage_fit.py
"""
import numpy as np
import pandas as pd

o = pd.read_csv('results/lvn_cvd_rr_grid.csv')
o = o[(o.win + o.lose) > 0].copy()
o['nres'] = o.win + o.lose
o['pres'] = o.win / o.nres                      # win share of RESOLVED trades
o['p0'] = o.sl / (o.sl + o.tp)                  # martingale null

NAMES = dict(A='A  LVN arrival + 10-min divergence',
             L='L  LVN arrival, no divergence',
             M='M  same entries, momentum side',
             B='B  divergence turns on in the level',
             C='C  control: every minute, reversal side')


def pm(sl, tp, k):
    if abs(k) < 1e-9:
        return sl / (sl + tp)
    return (1 - np.exp(-k * sl)) / (1 - np.exp(-k * (sl + tp)))


def fit(sub, wmode='iv'):
    ks = np.linspace(-0.02, 0.02, 4001)
    p = sub.pres.values
    w = (sub.nres / (p * (1 - p) + 1e-12)).values if wmode == 'iv' else sub.nres.values
    err = None
    kk = None
    for k in ks:
        e = np.sum(w * (p - pm(sub.sl.values, sub.tp.values, k)) ** 2)
        if err is None or e < err:
            err, kk = e, k
    return kk


print("=" * 104)
print("FITTED DRIFT-TO-VARIANCE RATIO k (1/points) AND ITS CHARACTERISTIC DISTANCE 1/k")
print("  k > 0 : price drifts toward the reversal target    k < 0 : toward the stop")
print("  p = SL/(SL+TP) at k = 0, i.e. the martingale null")
print("=" * 104)
print(f"{'arm':<38} | {'n cells':>7} | {'k (1/pt)':>10} | {'1/k (pts)':>10} | {'rw. RMSE':>9} | {'null RMSE':>9}")
print("-" * 104)
for arm in ['A', 'L', 'M', 'B', 'C']:
    sub = o[o.set == arm]
    if len(sub) < 5:
        continue
    k = fit(sub)
    pred = pm(sub.sl.values, sub.tp.values, k)
    rmse = np.sqrt(np.mean((sub.pres.values - pred) ** 2))
    rmse0 = np.sqrt(np.mean((sub.pres.values - sub.p0.values) ** 2))
    inv = f"{1/k:>10.0f}" if abs(k) > 1e-6 else f"{'-':>10}"
    print(f"{NAMES[arm]:<38} | {len(sub):>7} | {k:>+10.5f} | {inv} | {rmse:>8.2%} | {rmse0:>8.2%}")

print("\nSCALE STABILITY of k - fit on sub-grids of arm A and of the control C")
print("-" * 104)
print(f"{'subset':<38} | {'k(A)':>10} | {'k(C)':>10} | {'n cells':>7}")
for lab, m in [('tight  SL+TP <= 60', o.sl + o.tp <= 60),
               ('medium 60 < SL+TP <= 130', (o.sl + o.tp > 60) & (o.sl + o.tp <= 130)),
               ('wide   SL+TP > 130', o.sl + o.tp > 130),
               ('SL <= 20 only', o.sl <= 20),
               ('SL >= 40 only', o.sl >= 40)]:
    sa, sc = o[(o.set == 'A') & m], o[(o.set == 'C') & m]
    print(f"{lab:<38} | {fit(sa):>+10.5f} | {fit(sc):>+10.5f} | {len(sa):>7}")

print("\nHEADLINE CELLS - resolved win share vs the martingale null")
print("-" * 104)
print(f"{'arm':>3} {'SL/TP':>8} | {'n':>7} {'resolved':>9} {'win|res':>8} {'null':>7} {'model':>7} | {'implied k':>10}")
for arm, sl, tp in [('A', 30, 60), ('A', 10, 50), ('A', 20, 40), ('A', 50, 100),
                    ('A', 15, 30), ('A', 50, 50),
                    ('C', 30, 60), ('C', 10, 50), ('C', 50, 100),
                    ('M', 30, 60), ('M', 50, 100), ('L', 30, 60)]:
    r = o[(o.set == arm) & (o.sl == sl) & (o.tp == tp)]
    if not len(r):
        continue
    r = r.iloc[0]
    ks = np.linspace(-0.02, 0.02, 2001)
    errs = [abs(r.pres - pm(sl, tp, k)) for k in ks]
    kimp = ks[int(np.argmin(errs))]
    ka = fit(o[o.set == arm])
    print(f"{arm:>3} {sl:>4}/{tp:<3} | {r['n']:>7,} {r.nres/r['n']:>8.1%} {r.pres:>7.1%} "
          f"{r.p0:>6.1%} {pm(sl,tp,ka):>6.1%} | {kimp:>+10.5f}")

print("""
How to read it
  * 1/k is the distance over which drift matters.  If 1/k >> your stop distance,
     the setup cannot pay for the asymmetry: you are paying a real spread to take
     a coin flip.  That is what the 10-30 pt stops in this grid are.
  * k is nearly the same for A and for the every-minute control C, so what little
     drift exists is a property of the MARKET at that scale, not of the setup.
  * A single k with a small RMSE would say the whole 63-cell surface is one
     number; a drifting k across scales means the process is NOT Brownian and the
     barrier geometry has to be modelled directly (jumps, vol clustering).""")

print("\n" + "=" * 104)
print("CENSORING CHECK - the resolved-only win share is biased by the session bell")
print("=" * 104)
print("  cells whose races get cut off by 16:59 load on the NEAR barrier, so they")
print("  understate p.  If so, (win|res - null) must fall as the open share rises.")
for arm in ['A', 'L', 'M', 'C']:
    sub = o[o.set == arm]
    d = sub.pres.values - sub.p0.values
    r = np.corrcoef(sub.open.values, d)[0, 1]
    sub2 = sub[sub.open <= 0.20]
    print(f"  {arm}: corr(open, win|res - null) = {r:>+5.2f}   "
          f"k(all) = {fit(sub):>+9.5f}   k(open<=20%) = {fit(sub2):>+9.5f} "
          f"(n={len(sub2)})")
print("""
  A negative correlation confirms the bias: the wider the barriers, the more the
  bell truncates the race, and the worse the resolved-only share looks.  The k
  fitted on low-censoring cells is the trustworthy one.  The P&L grid itself is
  NOT affected by this - it prices every trade, marks the open ones at 16:59 and
  is the ground truth the theory has to match.""")
