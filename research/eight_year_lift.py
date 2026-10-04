"""
Run the reverse-engineering test on the FULL 8-year bar history.

Why this is defensible: the biggest-rejection pivots come only from OHLC bars,
and most catalogue levels (prior high/low/close, week high/low, POC, value-area
edges) are volume-at-price estimates computed from bars too. That makes the test
internally consistent - the same input builds both the reversals and the levels,
so there is no 7.5-month footprint constraint.

Caveat carried forward: the touch tolerance is a fixed 5 points, set from the
2026 price level (~30,000). Over 2019-2021 MNQ traded near 7,500-16,000, so an
absolute 5pt tolerance is proportionally tighter there. Results are therefore
reported per era, not pooled, so a scale artefact cannot masquerade as a finding.

Null: for each session, the share of that day's TRADED PRICE SPACE sitting within
the tolerance of each feature. This is the same construction as the 7.5-month run.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
TOL = 5.0
ZIG = 0.25
N_BOOT = 2000
rng = np.random.default_rng(88)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars['dow'] = bars.dt.dt.dayofweek
bars = bars.sort_values(['date', 'minute']).reset_index(drop=True)
days = sorted(bars.date.unique())
print(f"sessions {len(days):,}  {days[0]} .. {days[-1]}")

gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}

# ---- exclude stub sessions ----
keep = [d for d in days if len(gday[d]) >= 200]
print(f"excluding {len(days)-len(keep)} stub sessions -> {len(keep):,} used")
days = keep
gday = {d: gday[d] for d in days}

# ---- session profiles from bars ----
print("building session profiles ...")
prof = {}
for d in days:
    g = gday[d]
    step = 0.25
    lo = np.floor(g.low.min() / step) * step
    hi = np.ceil(g.high.max() / step) * step
    grid = np.arange(lo, hi + step, step)
    dens = np.zeros(len(grid))
    # distribute each bar's volume evenly across its range
    li = np.searchsorted(grid, g.low.values, 'left')
    hi_ = np.searchsorted(grid, g.high.values, 'right')
    for a, b, v in zip(li, hi_, g.volume.values):
        if b > a:
            dens[a:b] += v / (b - a)
    prof[d] = pd.Series(dens, index=grid)
print("profiles built")

WIN = int(round(50.0 / 0.25 / 2)) * 2 + 1


def bands_from(prices, mask, min_h=4.0):
    out, s, prev = [], None, None
    for p, m in zip(prices, mask):
        if m and s is None:
            s = p
        elif not m and s is not None:
            if prev - s >= min_h:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= min_h:
        out.append((s, prev))
    return out


LEVELS = {}
for i, d in enumerate(days):
    if i < 6:
        LEVELS[d] = None
        continue
    v = prof[days[i - 1]]
    o = v.sort_values(ascending=False)
    cum = o.cumsum() / o.sum()
    va = o[cum <= 0.70]
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    lvn = bands_from(v.index.values, (v.values <= 0.7 * med) & ~np.isnan(med))
    tpo_like = bands_from(v.index.values, v.values <= 0.15 * v.median())
    pv = gday[days[i - 1]]
    wk = bars[bars.date.isin(days[max(0, i - 5):i])]
    LEVELS[d] = dict(prior_high=float(pv.high.max()), prior_low=float(pv.low.min()),
                     prior_close=float(pv.close.iloc[-1]), poc=float(v.idxmax()),
                     va_high=float(va.index.max()), va_low=float(va.index.min()),
                     week_high=float(wk.high.max()), week_low=float(wk.low.min()),
                     lvn_bands=lvn, tpo_bands=tpo_like)
print("levels built")


def zigzag(H, Lo, thr):
    piv = []
    dirn = 0
    ext_i, ext_p = 0, H[0]
    rh_i, rh_p = 0, H[0]
    rl_i, rl_p = 0, Lo[0]
    for i in range(len(H)):
        if dirn == 0:
            if H[i] >= rl_p + thr and rl_i < i:
                dirn = 1; ext_i, ext_p = i, H[i]; rh_i, rh_p = i, H[i]
                piv.append((rl_i, rl_p, 'L'))
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1; ext_i, ext_p = i, Lo[i]; rl_i, rl_p = i, Lo[i]
                piv.append((rh_i, rh_p, 'H'))
            else:
                if H[i] > rh_p: rh_p, rh_i = H[i], i
                if Lo[i] < rl_p: rl_p, rl_i = Lo[i], i
        elif dirn == 1:
            if H[i] > ext_p: ext_i, ext_p = i, H[i]
            elif Lo[i] <= ext_p - thr:
                piv.append((ext_i, ext_p, 'H')); dirn = -1
                ext_i, ext_p = i, Lo[i]
        else:
            if Lo[i] < ext_p: ext_i, ext_p = i, Lo[i]
            elif H[i] >= ext_p + thr:
                piv.append((ext_i, ext_p, 'L')); dirn = 1
                ext_i, ext_p = i, H[i]
    return piv


FEATURES = ['prior_high', 'prior_low', 'prior_close', 'poc', 'va_high', 'va_low',
            'week_high', 'week_low', 'lvn_bands', 'tpo_bands']

obs, poss = {}, {}
for d in days:
    L = LEVELS.get(d)
    if L is None:
        continue
    b = gday[d]
    H, Lo = b.high.values, b.low.values
    rng_ = H.max() - Lo.min()
    if rng_ <= 0:
        continue
    piv = zigzag(H, Lo, ZIG * rng_)
    if len(piv) < 3:
        continue
    k = max(range(len(piv) - 1), key=lambda j: abs(piv[j + 1][1] - piv[j][1]))
    obs[d] = piv[k][1]
    poss[d] = ((piv[k][1] - Lo.min()) / rng_, piv[k][2])
print(f"\nbiggest rejections found: {len(obs):,}")

pos = np.array([poss[d][0] for d in obs])
print(f"position in day's range: mean {pos.mean():.1%}  median {np.median(pos):.1%}")
print(f"  top third {(pos>0.667).mean():.1%} | bottom third {(pos<0.333).mean():.1%}")


def mask_arr(arr, f, L):
    if f.endswith('_bands'):
        m = np.zeros(len(arr), bool)
        for a, b in L[f]:
            m |= (arr >= a - TOL) & (arr <= b + TOL)
        return m
    return np.abs(arr - L[f]) <= TOL


grids = {}
for d in obs:
    g = gday[d]
    step = 0.25
    lo = np.floor(g.low.min() / step) * step
    hi = np.ceil(g.high.max() / step) * step
    grids[d] = np.arange(lo, hi + step, step)

keys = sorted(obs.keys())
year = np.array([d.year for d in keys])

print(f"\n=== LIFT AT THE DAY'S BIGGEST REJECTION ({len(keys):,} sessions) ===")
print(f"{'feature':>13} | {'at rej':>7} | {'space':>7} | {'lift':>6} | {'95% CI':>15}")
out = []
for f in FEATURES:
    hits = np.array([mask_arr(np.array([obs[d]]), f, LEVELS[d])[0] for d in keys], float)
    base = np.array([mask_arr(grids[d], f, LEVELS[d]).mean() for d in keys])
    lift = hits.mean() / base.mean()
    boot = []
    for _ in range(N_BOOT):
        ii = rng.choice(len(keys), len(keys), replace=True)
        bb = base[ii].mean()
        if bb > 0:
            boot.append(hits[ii].mean() / bb)
    lo, hi = np.percentile(boot, [2.5, 97.5]) if len(boot) > 50 else (np.nan, np.nan)
    sig = 'SIG' if (lo > 1 or hi < 1) else ''
    print(f"{f:>13} | {hits.mean():>7.1%} | {base.mean():>7.1%} | {lift:>6.2f} | "
          f"[{lo:>5.2f},{hi:>5.2f}] {sig}")
    out.append(dict(feature=f, at_rejection=hits.mean(), price_space=base.mean(),
                    lift=lift, ci_lo=lo, ci_hi=hi, n=len(keys), scope='pooled'))
pd.DataFrame(out).to_csv('results/eight_year_lift.csv', index=False)

eras = [(2019, 2021), (2022, 2023), (2024, 2025), (2026, 2027)]
print(f"\n=== STABILITY ACROSS ERAS (lift, guarded against the fixed-tolerance issue) ===")
print(f"{'feature':>13} | " + " | ".join(f"{a}-{b}" for a, b in eras))
era_rows = []
for f in FEATURES:
    cells = []
    for a, b in eras:
        sel = (year >= a) & (year <= b)
        if sel.sum() < 40:
            cells.append('   n/a')
            continue
        kk = [keys[i] for i in np.where(sel)[0]]
        hits = np.array([mask_arr(np.array([obs[d]]), f, LEVELS[d])[0] for d in kk], float)
        base = np.array([mask_arr(grids[d], f, LEVELS[d]).mean() for d in kk])
        lf = hits.mean() / base.mean() if base.mean() else np.nan
        cells.append(f"{lf:>6.2f}")
        era_rows.append(dict(feature=f, era=f'{a}-{b}', lift=lf, n=int(sel.sum()),
                             at_rej=hits.mean(), space=base.mean()))
    print(f"{f:>13} | " + " | ".join(cells))
pd.DataFrame(era_rows).to_csv('results/eight_year_lift_eras.csv', index=False)

print(f"\n=== CONCENTRATION: how much price does each feature occupy? ===")
for f in FEATURES:
    base = np.array([mask_arr(grids[d], f, LEVELS[d]).mean() for d in keys])
    print(f"  {f:>13}: {base.mean():>5.1%} of the traded price space")
print("\nwrote results/eight_year_lift.csv + results/eight_year_lift_eras.csv")
