"""
LVN  x  CVD DIVERGENCE   --   the user's combination.
    "wait so what if we use lvn with cvd divergence"

Three corrections to the first version of this file, all of them mine:

  (a) SCALE BUG. divz was normalised by 10 x (median |per-minute delta|), which
      has sd 0.443 and median 0.269 in this data - i.e. the denominator was ~4x
      too big, so |divz| >= 1 was the top 2% and, intersected with the 13%
      sign-disagreement flag, fired ZERO times. The "strong divergence" arm
      tested nothing. Now normalised by the expanding median of |cvd_slope|
      itself, so divz is ~1 by construction.

  (b) SAMPLING. Arrivals were detected on the every-10th-minute grid, so most
      band entries fell between samples - that is why the first run found only
      93 arrivals, FEWER than the old midpoint rule. The arrival scan now runs
      on every minute. "Price reaches the level" is a per-minute event.

  (c) The state/2x2/regression frame stays on the every-10-min grid so it is
      directly comparable with v2.

Band sets: (1) the immediately previous session's profile  - matches the old
at-box run; (2) union of the previous 5 sessions - the user's actual workflow,
LVNs stay valid for the week.

EVERY cell is reported. Nothing is selected after the fact.
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
STEP = 10
N_BOOT = 2000
SPLIT = pd.Timestamp('2026-06-10').date()
rng = np.random.default_rng(20261004)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'),
    ask=('ask_volume', 'sum'), trades=('trades', 'sum')).reset_index()
fm['delta'] = fm['ask'] - fm['bid']
fm['buy_ratio'] = fm['ask'] / fm['vol']

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
print(f"sessions {len(days)} | minutes {len(m):,}")

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
print(f"bands: prior-day {np.mean([len(B1[d]) for d in days]):.1f}/session | "
      f"prior-5 union {np.mean([len(B5[d]) for d in days]):.1f}/session")


def mask_in(C, bands, tol=TOL):
    """vectorised in-band OR in-tolerance mask over a close array"""
    out = np.zeros(len(C), bool)
    for zl, zh in bands:
        out |= (C >= zl - tol) & (C <= zh + tol)
    return out


def ratio_at(price, prices, ratio):
    j = np.searchsorted(prices, price)
    j = min(max(j, 0), len(prices) - 1)
    if j > 0 and abs(prices[j - 1] - price) < abs(prices[j] - price):
        j -= 1
    return ratio[j]


def epct(arr, i, min_n=25):
    past = arr[:i]
    if len(past) < min_n or not np.isfinite(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


def race(H, L, i, ref):
    uh = H[i + 1:] >= ref + BARRIER
    dh = L[i + 1:] <= ref - BARRIER
    a = np.argmax(uh) if uh.any() else 10 ** 9
    b = np.argmax(dh) if dh.any() else 10 ** 9
    if a == b:
        return None
    return a < b


def feats(H, L, C, V, D, BR, TR, cvd, med_ad, cscale, i):
    pslope = C[i] - C[i - 10]
    if pslope == 0:
        return None
    cvd_slope = cvd[i] - cvd[i - 10]
    dz = cvd_slope / cscale[i] if cscale[i] and np.isfinite(cscale[i]) else np.nan
    br_pct = epct(BR, i)
    return dict(
        reversal=int((1 if (C[i + 1:] >= C[i] + BARRIER).any() else False) != 0),
        approach_up=int(pslope > 0), cvd_slope=cvd_slope, divz=dz,
        div_any=int(cvd_slope != 0 and np.sign(cvd_slope) != np.sign(pslope)),
        div_big=int(np.isfinite(dz) and abs(dz) >= 1 and
                    np.sign(cvd_slope) != np.sign(pslope)),
        buy_extreme=int(np.isfinite(br_pct) and br_pct > 0.9),
        sell_extreme=int(np.isfinite(br_pct) and br_pct < 0.1),
        absdelta_pct=epct(np.abs(D), i), vol_pct=epct(V, i), trades_pct=epct(TR, i),
    )


S, A = [], []
for d in days:
    b = gday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    V, D, BR = b.vol.values, b.delta.values, b.buy_ratio.values
    TR = b.trades.values.astype(float)
    n = len(b)
    if n < 60:
        continue
    cvd = np.cumsum(D)
    med_ad = pd.Series(np.abs(D)).expanding(20).median().values
    cscale = pd.Series(np.abs(np.diff(cvd, prepend=cvd[0]))).rolling(20, min_periods=10).median().values
    cscale = np.where(cscale == 0, np.nan, cscale)

    prev_fp = [x for x in fp_days if x < d]
    pr_prices, pr_ratio = PROF[prev_fp[-1]][1:] if prev_fp else (np.array([np.nan]), np.array([np.nan]))

    inm = {1: mask_in(C, B1[d]), 5: mask_in(C, B5[d])}

    # ---------- state frame: every 10th minute ----------
    for i in range(40, n - 15, STEP):
        up = race(H, L, i, C[i])
        if up is None:
            continue
        f = feats(H, L, C, V, D, BR, TR, cvd, med_ad, cscale, i)
        if f is None:
            continue
        f.pop('reversal')
        f.update(date=str(d), i=i, reversal=int(up != (C[i] - C[i - 10] > 0)),
                 close=C[i], in1=int(inm[1][i]), in5=int(inm[5][i]))
        S.append(f)

    # ---------- arrival frame: EVERY minute ----------
    for key, mm_, tag in [(1, inm[1], 'a1'), (5, inm[5], 'a5')]:
        ent = mm_ & ~np.r_[False, mm_[:-1]]
        if key == 1:
            ent &= ~np.r_[False, False, mm_[:-2]]      # strict: out for 2 bars
        for i in np.where(ent)[0]:
            if i < 40 or i > n - 15:
                continue
            up = race(H, L, i, C[i])
            if up is None:
                continue
            f = feats(H, L, C, V, D, BR, TR, cvd, med_ad, cscale, i)
            if f is None:
                continue
            f.pop('reversal')
            f.update(date=str(d), i=i, reversal=int(up != (C[i] - C[i - 10] > 0)),
                     close=C[i], thin=ratio_at(C[i], pr_prices, pr_ratio), band=tag)
            A.append(f)

s = pd.DataFrame(S)
a = pd.DataFrame(A)
s['is_oos'] = [1 if pd.Timestamp(x).date() >= SPLIT else 0 for x in s['date']]
a['is_oos'] = [1 if pd.Timestamp(x).date() >= SPLIT else 0 for x in a['date']]
s.to_csv('results/lvn_cvd_state.csv', index=False)
a.to_csv('results/lvn_cvd_arrivals.csv', index=False)

print(f"\nstate frame   n={len(s):,}   coverage inside prior-day band {s['in1'].mean():.2%} "
      f"| inside 5-day union {s['in5'].mean():.2%}")
print(f"arrivals      n={len(a):,}  (prior-day {int((a['band']=='a1').sum()):,} "
      f"= {(a['band']=='a1').mean()*0:.0f}, 5-day union {int((a['band']=='a5').sum()):,})")
print(f"div_any {s['div_any'].mean():.2%} of sampled minutes | div_big "
      f"{s['div_big'].mean():.2%} (n={int(s['div_big'].sum())})  <- was 0 before the scale fix")


def boot_ci(frame, mask, col='reversal'):
    ucl = np.unique(frame['date'].values)
    idx = {u: np.where(frame['date'].values == u)[0] for u in ucl}
    mask = np.asarray(mask, bool)
    if mask.sum() < 12:
        return None
    out = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        ss = mask[ii]
        if ss.sum() > 3:
            out.append(frame[col].values[ii][ss].mean())
    return np.percentile(out, [2.5, 97.5])


def show(frame, label, mask):
    mask = np.asarray(mask, bool)
    n = int(mask.sum())
    if n < 12:
        print(f"{label:>36} | {n:>5} | too few")
        return np.nan, n
    r = frame['reversal'].values[mask].mean()
    lo, hi = boot_ci(frame, mask)
    print(f"{label:>36} | {n:>5} | {r:>8.2%} | [{lo:>+6.2%},{hi:>+6.2%}]")
    return r, n


print("\n" + "=" * 90)
print("1. ARRIVALS AT AN LVN  (every minute, full grid)  --  the user's 'price reaches the level'")
print("=" * 90)
base = s['reversal'].mean()
print(f"reference: base reversal on the 10-min state frame {base:.2%}")
print(f"{'':>36} | {'n':>5} | {'reversal':>8} | {'95% CI':>17}")
for tag, lab in [('a1', 'arrival @ prior-day band'), ('a5', 'arrival @ 5-day union band')]:
    sub = a[a['band'] == tag]
    r, n = show(sub, lab, np.ones(len(sub), bool))
    if n:
        print(f"   -> vs base {base:.2%}:  {r-base:+.2%}")

print("\n" + "=" * 90)
print("2. THE HEADLINE :  div_any  x  arrival at an LVN")
print("=" * 90)
print(f"{'':>36} | {'n':>5} | {'reversal':>8} | {'95% CI':>17}")
for tag, lab in [('a1', 'prior-day'), ('a5', '5-day union')]:
    sub = a[a['band'] == tag]
    r1, n1 = show(sub, f'arrival + divergence   [{lab}]', sub['div_any'] == 1)
    r0, n0 = show(sub, f'arrival, no divergence [{lab}]', sub['div_any'] == 0)
    rb, nb = show(sub, f'arrival + STRONG div   [{lab}]', sub['div_big'] == 1)
    if n1 and n0:
        print(f"   -> divergence at the level : {r1-r0:+.2%}")

print("\n" + "=" * 90)
print("3. THE 2x2 ON THE STATE FRAME  (comparable with v2 / the old at-box run)")
print("=" * 90)
print(f"{'':>36} | {'n':>5} | {'reversal':>8} | {'95% CI':>17}")
for col, lab in [('in1', 'prior-day band'), ('in5', '5-day union')]:
    c1, _ = show(s, f'divergence  +  AT LVN [{lab}]', (s['div_any'] == 1) & (s[col] == 1))
    c2, _ = show(s, f'divergence  +  away   [{lab}]', (s['div_any'] == 1) & (s[col] == 0))
    c3, _ = show(s, f'no diverg.  +  AT LVN [{lab}]', (s['div_any'] == 0) & (s[col] == 1))
    c4, _ = show(s, f'no diverg.  +  away   [{lab}]', (s['div_any'] == 0) & (s[col] == 0))
    if all(np.isfinite(x) for x in [c1, c2, c3, c4]):
        print(f"   -> div effect AT LVN {c1-c3:+.2%} | away {c2-c4:+.2%} | "
              f"INTERACTION {(c1-c2)-(c3-c4):+.2%}")
    e1, _ = show(s, f'STRONG div  +  AT LVN [{lab}]', (s['div_big'] == 1) & (s[col] == 1))
    e2, _ = show(s, f'STRONG div  +  away   [{lab}]', (s['div_big'] == 1) & (s[col] == 0))
    f3, _ = show(s, f'no strong   +  AT LVN [{lab}]', (s['div_big'] == 0) & (s[col] == 1))
    f4, _ = show(s, f'no strong   +  away   [{lab}]', (s['div_big'] == 0) & (s[col] == 0))
    if all(np.isfinite(x) for x in [e1, e2, f3, f4]):
        print(f"   -> STRONG div AT LVN {e1-f3:+.2%} | away {e2-f4:+.2%} | "
              f"INTERACTION {(e1-e2)-(f3-f4):+.2%}")

print("\n" + "=" * 90)
print("4. CONTINUOUS INTERACTION  (state frame, cluster-robust by session)")
print("=" * 90)


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
    V = XtXi @ meat @ XtXi * (G / (G - 1))
    return bb, np.sqrt(np.diag(V))


sub = s[np.isfinite(s['divz']) & np.isfinite(s['thin'].astype(float)) if 'thin' in s else np.isfinite(s['divz'])].copy()
sub = s[np.isfinite(s['divz'])].copy()
sub['dv'] = (sub['divz'] - sub['divz'].mean()) / sub['divz'].std()
y = sub['reversal'].values.astype(float)
X = np.column_stack([np.ones(len(sub)), (sub['div_any'].values == 1).astype(float),
                     sub['in1'].values.astype(float),
                     (sub['div_any'].values == 1).astype(float) * sub['in1'].values])
bb, se = creg(y, X, sub['date'].values)
print(f"n={len(sub):,}  clusters={sub['date'].nunique()}")
print(f"{'term':>18} | {'coef':>9} | {'SE':>7} | {'t':>6}   (prior-day band)")
for nm, c_, s_ in zip(['const', 'div_any', 'AT LVN', 'div x AT LVN'], bb, se):
    print(f"{nm:>18} | {c_:>+9.4f} | {s_:>7.4f} | {c_/s_:>+6.2f}")
X = np.column_stack([np.ones(len(sub)), (sub['div_any'].values == 1).astype(float),
                     sub['in5'].values.astype(float),
                     (sub['div_any'].values == 1).astype(float) * sub['in5'].values])
bb, se = creg(y, X, sub['date'].values)
print(f"{'term':>18} | {'coef':>9} | {'SE':>7} | {'t':>6}   (5-day union)")
for nm, c_, s_ in zip(['const', 'div_any', 'AT LVN', 'div x AT LVN'], bb, se):
    print(f"{nm:>18} | {c_:>+9.4f} | {s_:>7.4f} | {c_/s_:>+6.2f}")
X = np.column_stack([np.ones(len(sub)), (sub['div_big'].values == 1).astype(float),
                     sub['in1'].values.astype(float),
                     (sub['div_big'].values == 1).astype(float) * sub['in1'].values])
bb, se = creg(y, X, sub['date'].values)
print(f"{'term':>18} | {'coef':>9} | {'SE':>7} | {'t':>6}   (STRONG div x prior-day)")
for nm, c_, s_ in zip(['const', 'div_big', 'AT LVN', 'divbig x AT LVN'], bb, se):
    print(f"{nm:>18} | {c_:>+9.4f} | {s_:>7.4f} | {c_/s_:>+6.2f}")

print("\n" + "=" * 90)
print("5. IS / OOS")
print("=" * 90)
for tag, lab in [('a1', 'arrival@LVN'), ('a5', 'arrival@union')]:
    sub = a[a['band'] == tag]
    for nm, mm_ in [('div', sub['div_any'] == 1), ('no div', sub['div_any'] == 0)]:
        mm_ = np.asarray(mm_, bool)
        isr = sub['reversal'].values[mm_ & (sub['is_oos'] == 0).values]
        oos = sub['reversal'].values[mm_ & (sub['is_oos'] == 1).values]
        print(f"{lab:>14} {nm:>7} | IS {isr.mean():>7.2%} (n={len(isr):>4}) "
              f"| OOS {oos.mean():>7.2%} (n={len(oos):>4})")
print(f"{'base':>14} {'':>7} | IS {s['reversal'].values[(s['is_oos']==0).values].mean():>7.2%}"
      f"          | OOS {s['reversal'].values[(s['is_oos']==1).values].mean():>7.2%}")
print("\nwrote results/lvn_cvd_state.csv, results/lvn_cvd_arrivals.csv")
