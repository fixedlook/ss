"""
DEFINITIVE TEST: does the LVN filter add anything on top of CVD divergence?

The arrival-frame run answered "divergence at the level" with +3.83% (n=326,
5-day union). But divergence AWAY from a level is +3.52% in the state frame.
If those are the same number, the LVN is doing nothing and the +3.83% is just
the divergence effect showing up again.

So: build the 2x2 on ONE universe (every minute of every session), so the four
cells are directly comparable:

                   div_any        no div
    arrival        a              b
    not arrival    c              d

    divergence effect at a level  = a - b
    divergence effect elsewhere   = c - d
    INTERACTION                   = (a-b) - (c-d)

Reported for both band sets, plus the same 2x2 with "in band" (state) instead
of "arrival", plus divergence crossed with thinness quartiles. Cluster-robust
SEs by session throughout. Nothing selected after the fact.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
BARRIER = 100.0
TOL = 5.0
W_PTS = 50.0
F_LVN = 0.7
MIN_H = 4.0
TICK = 0.25
N_BOOT = 2000
SPLIT = pd.Timestamp('2026-06-10').date()
rng = np.random.default_rng(4242)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'),
    ask=('ask_volume', 'sum')).reset_index()
fm['delta'] = fm['ask'] - fm['bid']

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'high', 'low', 'close']]

m = bars.merge(fm, on=['date', 'minute'], how='inner').sort_values(['date', 'minute'])
m = m.reset_index(drop=True)
days = sorted(m.date.unique())
fp_days = sorted(fp.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in m.groupby('date')}

pv = fp.groupby(['date', 'price'])['volume'].sum()
WIN = int(round(W_PTS / TICK / 2)) * 2 + 1


def make_profile(d):
    v = pv.loc[d].sort_index()
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = v.values / med
    mask = (ratio <= F_LVN) & ~np.isnan(med)
    bands, start, prev = [], None, None
    for p, mm in zip(v.index.values, mask):
        if mm and start is None:
            start = p
        elif not mm and start is not None:
            if prev - start >= MIN_H:
                bands.append((start, prev))
            start = None
        prev = p
    if start is not None and prev - start >= MIN_H:
        bands.append((start, prev))
    return bands, v.index.values, ratio


PROF = {d: make_profile(d) for d in fp_days}
B1, B5 = {}, {}
for d in days:
    prev = [x for x in fp_days if x < d]
    B1[d] = PROF[prev[-1]][0] if prev else []
    B5[d] = sorted(set(sum([PROF[x][0] for x in prev[-5:]], [])))


def mask_in(C, bands, tol=TOL):
    out = np.zeros(len(C), bool)
    for zl, zh in bands:
        out |= (C >= zl - tol) & (C <= zh + tol)
    return out


def ratio_at(price, prices, ratio):
    if not np.isfinite(price) or not np.isfinite(prices[0]):
        return np.nan
    j = np.searchsorted(prices, price)
    j = min(max(j, 0), len(prices) - 1)
    if j > 0 and abs(prices[j - 1] - price) < abs(prices[j] - price):
        j -= 1
    return ratio[j]


rows = []
for d in days:
    b = gday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    D = b.delta.values
    n = len(b)
    if n < 60:
        continue
    cvd = np.cumsum(D)
    psl = C - np.roll(C, 10)
    csl = cvd - np.roll(cvd, 10)
    cscale = pd.Series(np.abs(csl)).rolling(20, min_periods=10).median().values
    cscale = np.where((cscale == 0) | ~np.isfinite(cscale), np.nan, cscale)
    in1m, in5m = mask_in(C, B1[d]), mask_in(C, B5[d])
    arr1 = in1m & ~np.r_[False, in1m[:-1]]
    arr5 = in5m & ~np.r_[False, in5m[:-1]]
    prev_fp = [x for x in fp_days if x < d]
    pr_prices, pr_ratio = PROF[prev_fp[-1]][1:] if prev_fp else (np.array([np.nan]), np.array([np.nan]))

    for i in range(40, n - 15):
        ref = C[i]
        sv = psl[i]
        if sv == 0:
            continue
        uh = H[i + 1:] >= ref + BARRIER
        dh = L[i + 1:] <= ref - BARRIER
        a = np.argmax(uh) if uh.any() else 10 ** 9
        bb = np.argmax(dh) if dh.any() else 10 ** 9
        if a == bb:
            continue
        cs = csl[i]
        rows.append((str(d), i, int((a < bb) != (sv > 0)), int((sv > 0)),
                     int(cs != 0 and np.sign(cs) != np.sign(sv)),
                     int(np.isfinite(cscale[i]) and abs(cs / cscale[i]) >= 1),
                     int(in1m[i]), int(in5m[i]), int(arr1[i]), int(arr5[i]),
                     ratio_at(ref, pr_prices, pr_ratio)))

f = pd.DataFrame(rows, columns=['date', 'i', 'reversal', 'outcome_up', 'div_any',
                                'div_big', 'in1', 'in5', 'arr1', 'arr5', 'thin'])
f['is_oos'] = [1 if pd.Timestamp(x).date() >= SPLIT else 0 for x in f['date']]
f.to_csv('results/lvn_cvd_full.csv', index=False)
print(f"full grid: {len(f):,} minutes with a resolved race in {f['date'].nunique()} sessions")
print(f"base reversal {f['reversal'].mean():.2%} | base P(up) {f['outcome_up'].mean():.2%}")
print(f"div_any {f['div_any'].mean():.2%} | arrivals: prior-day {int(f['arr1'].sum()):,} "
      f"| union {int(f['arr5'].sum()):,}")

ucl = np.unique(f['date'].values)
idx = {u: np.where(f['date'].values == u)[0] for u in ucl}


def ci(mask):
    mask = np.asarray(mask, bool)
    if mask.sum() < 12:
        return None, None
    out = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        ss = mask[ii]
        if ss.sum() > 3:
            out.append(f['reversal'].values[ii][ss].mean())
    lo, hi = np.percentile(out, [2.5, 97.5])
    return lo, hi


def show(label, mask):
    mask = np.asarray(mask, bool)
    n = int(mask.sum())
    if n < 12:
        print(f"{label:>40} | {n:>6} | too few")
        return np.nan, n
    r = f['reversal'].values[mask].mean()
    lo, hi = ci(mask)
    print(f"{label:>40} | {n:>6} | {r:>8.2%} | [{lo:>+6.2%},{hi:>+6.2%}]")
    return r, n


print("\n" + "=" * 92)
print("2x2 :  CVD DIVERGENCE  x  ARRIVAL AT AN LVN      (one universe: every minute)")
print("=" * 92)
print(f"{'':>40} | {'n':>6} | {'reversal':>8} | {'95% CI':>17}")
for col, lab in [('arr1', 'PRIOR-DAY BAND'), ('arr5', '5-DAY UNION BAND')]:
    print(f"--- arrival at {lab} ---")
    a_, na = show('divergence + arrival', (f['div_any'] == 1) & (f[col] == 1))
    b_, nb = show('divergence, no arrival', (f['div_any'] == 1) & (f[col] == 0))
    c_, nc = show('no divergence + arrival', (f['div_any'] == 0) & (f[col] == 1))
    d_, nd = show('no divergence, no arrival', (f['div_any'] == 0) & (f[col] == 0))
    if all(np.isfinite(x) for x in [a_, b_, c_, d_]):
        print(f"   divergence AT the level {a_-c_:+.2%} | divergence elsewhere {b_-d_:+.2%}")
        print(f"   INTERACTION {(a_-c_)-(b_-d_):+.2%}    (additive would give "
              f"{(a_-c_)+(b_-d_):+.2%} on top of {d_:.2%})")

print("\n" + "=" * 92)
print("2x2 :  CVD DIVERGENCE  x  IN A BAND (state, not arrival)")
print("=" * 92)
print(f"{'':>40} | {'n':>6} | {'reversal':>8} | {'95% CI':>17}")
for col, lab in [('in1', 'PRIOR-DAY BAND'), ('in5', '5-DAY UNION BAND')]:
    print(f"--- in {lab} ---")
    a_, _ = show('divergence + in band', (f['div_any'] == 1) & (f[col] == 1))
    b_, _ = show('divergence, not in band', (f['div_any'] == 1) & (f[col] == 0))
    c_, _ = show('no divergence + in band', (f['div_any'] == 0) & (f[col] == 1))
    d_, _ = show('no divergence, not in band', (f['div_any'] == 0) & (f[col] == 0))
    if all(np.isfinite(x) for x in [a_, b_, c_, d_]):
        print(f"   divergence AT the level {a_-c_:+.2%} | divergence elsewhere {b_-d_:+.2%}")
        print(f"   INTERACTION {(a_-c_)-(b_-d_):+.2%}")

print("\n" + "=" * 92)
print("THINNESS: divergence effect by quartile of the local volume ratio")
print("(<1 = thinner than its surroundings = LVN-like, >1 = thicker = HVN-like)")
print("=" * 92)
sub = f[np.isfinite(f['thin'])].copy()
sub['q'] = pd.qcut(sub['thin'], 4, labels=False, duplicates='drop')
print(f"{'quartile':>10} | {'ratio':>14} | {'div n':>6} | {'div rev':>8} | "
      f"{'nodiv n':>7} | {'nodiv rev':>9} | {'effect':>8}")
for q in sorted(sub['q'].dropna().unique()):
    g = sub[sub['q'] == q]
    gd = g[g['div_any'] == 1]
    gn = g[g['div_any'] == 0]
    if len(gd) < 12 or len(gn) < 12:
        continue
    print(f"{int(q)+1:>10} | {g['thin'].min():.2f}-{g['thin'].max():.2f} | {len(gd):>6} | "
          f"{gd['reversal'].mean():>8.2%} | {len(gn):>7} | {gn['reversal'].mean():>9.2%} | "
          f"{gd['reversal'].mean()-gn['reversal'].mean():>+8.2%}")


def creg(y, X, g):
    XtX = X.T @ X
    XtXi = np.linalg.pinv(XtX)
    bb = XtXi @ (X.T @ y)
    u = y - X @ bb
    meat = np.zeros_like(XtX)
    for gg in np.unique(g):
        mm = (g == gg)
        sc = X[mm].T @ u[mm]
        meat += np.outer(sc, sc)
    G = len(np.unique(g))
    return bb, np.sqrt(np.diag(XtXi @ meat @ XtXi * (G / (G - 1))))


print("\n" + "=" * 92)
print("REGRESSIONS  (cluster-robust by session, n=" f"{len(f):,})")
print("=" * 92)
y = f['reversal'].values.astype(float)
for col, lab in [('arr1', 'arrival @ prior-day'), ('arr5', 'arrival @ 5-day union'),
                 ('in1', 'in band @ prior-day'), ('in5', 'in band @ 5-day union')]:
    X = np.column_stack([np.ones(len(f)), (f['div_any'].values == 1).astype(float),
                         f[col].values.astype(float),
                         (f['div_any'].values == 1).astype(float) * f[col].values])
    bb, se = creg(y, X, f['date'].values)
    print(f"{lab:>22} : div {bb[1]:+.4f} (t {bb[1]/se[1]:+.2f}) | LVN {bb[2]:+.4f} "
          f"(t {bb[2]/se[2]:+.2f}) | INTERACTION {bb[3]:+.4f} (t {bb[3]/se[3]:+.2f})")

print("\n" + "=" * 92)
print("IS / OOS  for the two cells that looked interesting")
print("=" * 92)
for col, lab in [('arr5', 'arrival @ union'), ('arr1', 'arrival @ prior-day')]:
    for nm, mm_ in [('div', f['div_any'] == 1), ('no div', f['div_any'] == 0)]:
        mm_ = np.asarray(mm_, bool) & (f[col].values == 1)
        a_is = f['reversal'].values[mm_ & (f['is_oos'].values == 0)]
        a_oos = f['reversal'].values[mm_ & (f['is_oos'].values == 1)]
        print(f"{lab:>18} {nm:>7} @LVN | IS {a_is.mean():>7.2%} (n={len(a_is):>4}) "
              f"| OOS {a_oos.mean():>7.2%} (n={len(a_oos):>4})")
for nm, mm_ in [('div', f['div_any'] == 1), ('no div', f['div_any'] == 0)]:
    mm_ = np.asarray(mm_, bool)
    a_is = f['reversal'].values[mm_ & (f['is_oos'].values == 0)]
    a_oos = f['reversal'].values[mm_ & (f['is_oos'].values == 1)]
    print(f"{'ALL MINUTES':>18} {nm:>7}      | IS {a_is.mean():>7.2%} (n={len(a_is):>5}) "
          f"| OOS {a_oos.mean():>7.2%} (n={len(a_oos):>5})")
print("\nwrote results/lvn_cvd_full.csv")
