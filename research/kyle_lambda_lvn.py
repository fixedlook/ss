"""
Kyle's lambda inside vs outside the LVN bands - is a thin zone really a slip zone?

Kyle (1985): the price response to signed order flow is linear,
        dp = lambda * signed_volume + noise
and lambda is the market's price-impact coefficient (points per contract).
If an LVN is a place where liquidity is thin, lambda must be HIGHER inside the
band.  That is a textbook, assumption-light test of the mechanism we inferred
from the SL/TP grid: price crosses an LVN quickly, so a stop placed inside the
crossing gets hit by the pass-through rather than by a rejection.

Fama-MacBeth: one regression per session, then the cross-session mean and t-stat,
so the daily level of volatility/impact does not dominate the standard errors.

Run:  /tmp/venv/bin/python research/kyle_lambda_lvn.py
"""
import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5

p = pd.read_csv(PROF, header=None, names=['s', 'price', 'bid', 'ask', 'trades']).dropna()
p = p[p.s > 1e12]
p['dt'] = pd.to_datetime(p.s, unit='ms', utc=True).dt.tz_convert('America/New_York')
p['date'] = p.dt.dt.date
p['volume'] = p.bid + p.ask
pv = p.groupby(['date', 'price'])['volume'].sum()
prof_days = sorted(p.date.unique())
WIN = int(round(W_PTS / TICK / 2)) * 2 + 1


def make_bands(d):
    v = pv.loc[d].sort_index()
    if len(v) < 50:
        return []
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = v.values / med
    mask = (ratio <= F_LVN) & ~np.isnan(med)
    bands, start, prev = [], None, None
    for price, m in zip(v.index.values, mask):
        if m and start is None:
            start = price
        elif not m and start is not None:
            if prev - start >= MIN_H:
                bands.append((start, prev))
            start = None
        prev = price
    if start is not None and prev - start >= MIN_H:
        bands.append((start, prev))
    return bands


BAND = {d: make_bands(d) for d in prof_days}

df = pd.read_parquet(FLOW)
df['delta'] = df.ask_volume - df.bid_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)

rows = []
for d in trade_days:
    dts = pd.Timestamp(d).date()
    prior = [x for x in prof_days if x < dts][-N_PRIOR:]
    if not prior:
        continue
    bands = []
    for x in prior:
        bands += BAND[x]
    if not bands:
        continue
    b = flowday[d]
    C, SV = b.close.values, b.delta.values
    n = len(b)
    inb = np.zeros(n, bool)
    for lo, hi in bands:
        inb |= (C >= lo - TOL) & (C <= hi + TOL)
    dp = np.r_[np.nan, np.diff(C)]
    m = np.isfinite(dp)
    if m.sum() < 80:
        continue
    x, y, ib = SV[m], dp[m], inb[m]
    sig = np.nanstd(y)
    X = np.c_[np.ones(len(x)), x]
    try:
        coef = np.linalg.lstsq(X, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        continue
    lam = coef[1]
    lam_n = lam / sig if sig > 0 else np.nan
    li = lo_ = np.nan
    if (ib).sum() >= 30:
        Xi = np.c_[np.ones(ib.sum()), x[ib]]
        li = np.linalg.lstsq(Xi, y[ib], rcond=None)[0][1]
    if (~ib).sum() >= 30:
        Xo = np.c_[np.ones((~ib).sum()), x[~ib]]
        lo_ = np.linalg.lstsq(Xo, y[~ib], rcond=None)[0][1]
    rows.append(dict(date=d, n=len(x), vol=np.median(np.abs(y)),
                     lam=lam, lam_n=lam_n, lam_in=li, lam_out=lo_,
                     absdp_in=np.median(np.abs(y[ib])) if ib.sum() > 10 else np.nan,
                     absdp_out=np.median(np.abs(y[~ib])) if (~ib).sum() > 10 else np.nan,
                     vol_in=np.median(b.flow.values[inb]) if inb.sum() > 10 else np.nan,
                     vol_out=np.median(b.flow.values[~inb]) if (~inb).sum() > 10 else np.nan))

e = pd.DataFrame(rows)
print(f"sessions with a fitted lambda: {len(e):,}")


def fm(col, lo=None, hi=None):
    v = e[col].dropna().values
    if lo is not None:
        v = v[(v > lo) & (v < hi)]
    n = len(v)
    mu, sd = v.mean(), v.std(ddof=1)
    return mu, sd, mu / (sd / np.sqrt(n)), n


print("\n" + "=" * 100)
print("FAMA-MACBETH KYLE LAMBDA  (points per 1,000 net contracts; t vs zero across sessions)")
print("=" * 100)
for col, lab in [('lam', 'all minutes'), ('lam_in', 'INSIDE an LVN band'),
                 ('lam_out', 'outside the bands'), ('lam_n', 'all, vol-normalised (dp/sigma per contract)')]:
    mu, sd, t, n = fm(col)
    print(f"  {lab:<44} lambda = {mu*1000:>8.4f} pts/1000  (sd {sd*1000:>7.4f})  t = {t:>+5.1f}   n={n:,}")

pin, pout = e.lam_in.dropna().values, e.lam_out.dropna().values
both = e.dropna(subset=['lam_in', 'lam_out'])
diff = both.lam_in - both.lam_out
print(f"\n  paired sessions (both sides): {len(both):,}")
print(f"  lambda_in - lambda_out = {diff.mean()*1000:>+8.4f} pts/1000 "
      f"  t = {diff.mean()/(diff.std(ddof=1)/np.sqrt(len(diff))):>+5.1f}")
print(f"  ratio in/out = {both.lam_in.mean()/both.lam_out.mean():.3f}"
      f"   (median ratio {np.median((both.lam_in/both.lam_out).replace([np.inf,-np.inf],np.nan).dropna()):.2f})")

print("\n" + "=" * 100)
print("WHY: the same test in plain magnitudes")
print("=" * 100)
for a, b_, lab in [('absdp_in', 'absdp_out', 'median |1-min move| (pts)'),
                   ('vol_in', 'vol_out', 'median 1-min volume (contracts)')]:
    x, y = e[a].dropna().values, e[b_].dropna().values
    d = (e[a] - e[b_]).dropna()
    print(f"  {lab:<32} inside {np.median(x):>9.2f}   outside {np.median(y):>9.2f}   "
          f"ratio {np.median(x)/np.median(y):>5.2f}   (paired median diff t = "
          f"{d.mean()/(d.std(ddof=1)/np.sqrt(len(d))):>+5.1f})")
print("""
Reading it
  * lambda_in > lambda_out with a decent t means a given amount of aggressive flow
    moves price further inside the zone: thin book, fast pass-through.  That is the
    mechanical reason a stop inside an LVN gets taken out by the traverse.
  * It does NOT say the direction reverses - it says the market is less able to
    absorb flow there, so any move is bigger and any rejection has to come from
    someone posting size, i.e. from absorption, not from the zone itself.""")
e.to_csv('results/kyle_lambda_lvn.csv', index=False)
print("\nwrote results/kyle_lambda_lvn.csv")
