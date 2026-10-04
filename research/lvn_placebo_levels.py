"""
PLACEBO: do the same test with levels that mean nothing.

The question the real test cannot answer by itself: does the +3.14pp at LVN
arrivals come from the LEVELS, or would divergence look just as good at any price
a trader happened to draw on the chart?

Two placebos, both run through the identical arrival + race machinery:

  A. RANDOM-LEVEL placebo. For each session, take the real bands' count and their
     exact widths, but throw the centres uniformly into the price range that the
     last 5 completed sessions actually traded. Same geometry, zero information.
     Repeated over many seeds, so we get a null distribution rather than a point.

  B. ROUND-NUMBER placebo. Levels every 100 points (and every 50), i.e. the prices
     traders watch that have nothing to do with volume. +/-5pt arrival band as usual.

Everything else (events, features, outcomes, clustered bootstrap) is taken from
the already-computed event table, so the placebo differs ONLY in where the levels
are.

Run:  /tmp/venv/bin/python research/lvn_placebo_levels.py
"""
import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
EV = '/tmp/flow/lvn_cvd_events.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5
N_SEED = 60
N_BOOT = 1200
rng = np.random.default_rng(20261004)

# ---------------- profiles / real bands ----------------
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
PRANGE = {d: (float(pv.loc[d].index.min()), float(pv.loc[d].index.max())) for d in prof_days}

# ---------------- close series per session ----------------
df = pd.read_parquet(FLOW)
df['flow'] = df.bid_volume + df.ask_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
CLOSE = {d: g.close.values for d, g in flowday.items()}

E = pd.read_parquet(EV)
E = E[['date', 'i', 'arrival', 'in_band', 'reversal', 'cvd_div10']].copy()
cod, ucl = pd.factorize(E.date.values)
K = cod.max() + 1
out_r = E.reversal.values.astype(float)


def bootstrap_interaction(cells, out, n_boot=N_BOOT, seed=1):
    r = np.random.default_rng(seed)
    N = np.stack([np.bincount(cod[cells == k], minlength=K) for k in range(4)])
    S = np.stack([np.bincount(cod[cells == k], weights=out[cells == k], minlength=K)
                  for k in range(4)])
    W = r.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        M = (W @ S.T) / (W @ N.T)
    d = (M[:, 0] - M[:, 1]) - (M[:, 2] - M[:, 3])
    d = d[np.isfinite(d)]
    return np.percentile(d, [2.5, 97.5])


def cells_from(arr_flags, feat):
    at = np.asarray(arr_flags, bool)
    dv = np.asarray(feat, bool)
    return np.where(at & dv, 0, np.where(at & ~dv, 1, np.where(~at & dv, 2, 3)))


def report(tag, arr_flags, feat, booted=True):
    cells = cells_from(arr_flags, feat)
    r = [out_r[cells == k].mean() for k in range(4)]
    n = [int((cells == k).sum()) for k in range(4)]
    inter = (r[0] - r[1]) - (r[2] - r[3])
    ci = bootstrap_interaction(cells, out_r) if booted else [np.nan, np.nan]
    print(f"{tag:>34} | {r[0]:>7.2%} | {r[1]:>7.2%} | {r[0]-r[1]:>+7.2%} | {inter:>+7.2%} | "
          f"[{ci[0]:>+6.2%},{ci[1]:>+6.2%}] | {n[0]:>6,}")
    return inter, ci, r, n


# ================= REFERENCE: the real thing =================
print("=" * 108)
print("REAL LVN BANDS")
print("=" * 108)
print(f"{'':>34} | {'lv&div':>7} | {'lv':>7} | {'div@lv':>7} | {'INTERACT':>7} | "
      f"{'95% CI':>16} | {'n(lv&div)':>9}")
real_inter, real_ci, real_r, real_n = report("arrival x CVD div 10m", E.arrival == 1, E.cvd_div10 == 1)
report("in band  x CVD div 10m", E.in_band == 1, E.cvd_div10 == 1)

# ================= PLACEBO A: random levels, matched geometry =================
print("\n" + "=" * 108)
print(f"PLACEBO A - random levels, same count and widths, {N_SEED} seeds")
print("=" * 108)

first = sorted(E.date.unique())
gidx = {d: np.where(E.date.values == d)[0] for d in first}
ev_i = {d: E.i.values[gidx[d]] for d in first}

inter_seeds, lv_effect_seeds, arr_rate_seeds = [], [], []
for s in range(N_SEED):
    rr = np.random.default_rng(1000 + s)
    arr_all = np.zeros(len(E), bool)
    for d in first:
        dts = pd.Timestamp(d).date()
        prior = [x for x in prof_days if x < dts][-N_PRIOR:]
        if not prior:
            continue
        widths = [hi - lo for x in prior for lo, hi in BAND[x]]
        pmin = min(PRANGE[x][0] for x in prior)
        pmax = max(PRANGE[x][1] for x in prior)
        if not widths or pmax - pmin < 20:
            continue
        C = CLOSE.get(d)
        if C is None:
            continue
        inb = np.zeros(len(C), bool)
        for w in widths:
            if pmax - pmin - w <= 0:
                continue
            c = rr.uniform(pmin + w / 2, pmax - w / 2)
            inb |= (C >= c - w / 2 - TOL) & (C <= c + w / 2 + TOL)
        a = inb & ~np.r_[False, inb[:-1]]
        arr_all[gidx[d]] = a[np.clip(ev_i[d], 0, len(a) - 1)]
    arr_rate_seeds.append(arr_all.mean())
    cells = cells_from(arr_all, E.cvd_div10 == 1)
    r = [out_r[cells == k].mean() for k in range(4)]
    inter_seeds.append((r[0] - r[1]) - (r[2] - r[3]))
    lv_effect_seeds.append(r[0] - r[1])

inter_seeds = np.array(inter_seeds)
lv_effect_seeds = np.array(lv_effect_seeds)
lo, hi = np.percentile(inter_seeds, [2.5, 97.5])
print(f"placebo INTERACTION   mean {inter_seeds.mean():+.2%}   sd {inter_seeds.std():.2%}   "
      f"central 95% [{lo:+.2%},{hi:+.2%}]")
print(f"placebo div-at-level  mean {lv_effect_seeds.mean():+.2%}   sd {lv_effect_seeds.std():.2%}")
print(f"placebo arrival rate  mean {np.mean(arr_rate_seeds):.1%}   (real arrivals: {E.arrival.mean():.1%})")
pval = float((inter_seeds >= real_inter).mean())
print(f"\nREAL INTERACTION      {real_inter:+.2%}   CI [{real_ci[0]:+.2%},{real_ci[1]:+.2%}]")
print(f"share of placebo seeds at least this large: {pval:.1%}  "
      f"({int((inter_seeds >= real_inter).sum())}/{N_SEED})")

# ================= PLACEBO B: round numbers =================
print("\n" + "=" * 108)
print("PLACEBO B - round numbers (levels every N points, nothing to do with volume)")
print("=" * 108)
for step in [25, 50, 100]:
    arr_all = np.zeros(len(E), bool)
    for d in first:
        C = CLOSE.get(d)
        if C is None:
            continue
        lv = np.round(C / step) * step
        inb = np.abs(C - lv) <= TOL
        a = inb & ~np.r_[False, inb[:-1]]
        arr_all[gidx[d]] = a[np.clip(ev_i[d], 0, len(a) - 1)]
    report(f"round {step}pt arrivals x div 10m", arr_all, E.cvd_div10 == 1)

print("\n(columns: reversal rate when level AND divergence / level alone / jump in reversal "
      "from divergence at that level / interaction / n of the level-and-divergence cell)")
