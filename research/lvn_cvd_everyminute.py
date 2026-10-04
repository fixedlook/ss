"""
LVN x CVD - the definitive rebuild of the test that once read 54.29%.

Old reading: divergence + arrival at an LVN = 54.29% reversal (n=326) on 164
sessions, arrivals found on a 10-minute sampling grid. The grid was a confirmed
artifact generator, so the number has to be rebuilt on clean data.

Definitions are taken verbatim from the old lvn_cvd_full.py so the comparison is
like-for-like:
    band    = level volume <= 0.7 x median of a +/-50pt window, contiguous levels
              merged, >= 4 pts, from the union of the last 5 COMPLETED sessions
    arrival = first minute whose CLOSE is inside a band (+-5pt), having been
              outside the minute before  (no retirement - same as the old run)
    div     = sign(CVD 10-min change) != sign(price 10-min change)
    race    = +/-100 pts from the true close to the end of the session

New in this version:
    * the per-minute flow export (1,024 sessions) instead of the 164-session
      footprint, so the cells are 6x bigger,
    * EVERY minute, no grid,
    * a second, stricter arrival column that DOES retire bands once a bar trades
      clean through them, as a robustness check,
    * the 'in band' state frame as well, which has far more events,
    * the same 2x2 with activity (volume / trade-count spike) instead of div,
    * session-clustered bootstrap CIs on every cell and on the interaction.

Run:  /tmp/venv/bin/python research/lvn_cvd_everyminute.py
"""
import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK = 0.7, 50.0, 4.0, 0.25
N_PRIOR = 5
BARRIER = 100.0
TOL = 5.0
N_BOOT = 2000
rng = np.random.default_rng(31337)

# ------------------------------------------------------------------ profiles
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
print(f"profiles {len(prof_days)} sessions | "
      f"{np.mean([len(b) for b in BAND.values()]):.1f} LVN bands per session")

# ------------------------------------------------------------------ flow
df = pd.read_parquet(FLOW)
df['flow'] = df.bid_volume + df.ask_volume
df['delta'] = df.ask_volume - df.bid_volume            # never trust the file's delta
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
allday = {d: g for d, g in df.groupby('dstr')}
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)
trade_daysD = [pd.Timestamp(x).date() for x in trade_days]
print(f"trading sessions with flow {len(trade_days):,} | "
      f"{sum(len(g) for g in flowday.values()):,} RTH minutes")

# ------------------------------------------------------------------ events
rows = []
stat_tot = dict(arr5=0, arrR=0, in5=0, minutes=0)
for d in trade_days:
    dts = pd.Timestamp(d).date()
    prior = [x for x in prof_days if x < dts][-N_PRIOR:]
    if not prior:
        continue
    cand = []
    for x in prior:
        for lo, hi in BAND[x]:
            cand.append([lo, hi, x])
    cand.sort()
    merged = []
    for lo, hi, src in cand:
        if merged and lo <= merged[-1][1] + TICK:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi, src])
    active = [(lo, hi) for lo, hi, _ in merged]
    if not active:
        continue

    b = flowday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    V, D = b.flow.values, b.delta.values
    TR = b.trades.values.astype(float)
    BR = b.ask_volume.values / V
    n = len(b)
    cvd = np.cumsum(D)
    stat_tot['minutes'] += n

    # --- OLD definition: close inside the band (+-TOL), fresh entry, no retirement
    in5 = np.zeros(n, bool)
    for lo, hi in active:
        in5 |= (C >= lo - TOL) & (C <= hi + TOL)
    arr5 = in5 & ~np.r_[False, in5[:-1]]
    stat_tot['in5'] += int(in5.sum())
    stat_tot['arr5'] += int(arr5.sum())

    # --- STRICTER definition: same, but a band dies once a bar trades clean through
    day = allday[d]
    g0 = float(np.floor(min(day.low.min(), min(lo for lo, _ in active)) / TICK) * TICK)
    g1 = float(np.ceil(max(day.high.max(), max(hi for _, hi in active)) / TICK) * TICK)
    ng = int(round((g1 - g0) / TICK)) + 2
    bandid = -np.ones(ng, dtype=int)
    for k, (lo, hi) in enumerate(active):
        i0 = int(round((lo - g0) / TICK)); i1 = int(round((hi - g0) / TICK))
        bandid[i0:i1 + 1] = np.where(bandid[i0:i1 + 1] < 0, k, bandid[i0:i1 + 1])

    def ix(px):
        return int(np.clip(round((px - g0) / TICK), 0, ng - 1))

    arrR = np.zeros(n, bool)
    prev_touch = False
    for i in range(n):
        seg = bandid[ix(L[i]):ix(H[i]) + 1]
        hit = seg[seg >= 0]
        t = len(hit) > 0
        if t and not prev_touch:
            arrR[i] = True
        prev_touch = t
        for k in np.unique(hit):
            lo, hi = active[k]
            if L[i] <= lo and H[i] >= hi:
                s2, e2 = ix(lo), ix(hi)
                bandid[s2:e2 + 1] = np.where(bandid[s2:e2 + 1] == k, -2, bandid[s2:e2 + 1])
    stat_tot['arrR'] += int(arrR.sum())

    for i in range(40, n - 1):
        ref = C[i]
        seg_h, seg_l = H[i + 1:], L[i + 1:]
        uh, dh = seg_h >= ref + BARRIER, seg_l <= ref - BARRIER
        a = np.argmax(uh) if uh.any() else 10 ** 9
        bb = np.argmax(dh) if dh.any() else 10 ** 9
        if a == bb:
            continue
        outcome_up = a < bb
        pslope = C[i] - C[i - 10]
        if pslope == 0:
            continue
        approach_up = pslope > 0
        s10 = cvd[i] - cvd[i - 10]
        s30 = cvd[i] - cvd[i - 30] if i >= 30 else 0.0
        fwd_h, fwd_l = H[i + 1:i + 31], L[i + 1:i + 31]
        mv30 = float(fwd_h.max() - fwd_l.min()) if len(fwd_h) >= 5 else np.nan
        rows.append(dict(
            date=d, i=i, arrival=int(arr5[i]), arrivalR=int(arrR[i]), in_band=int(in5[i]),
            reversal=int(outcome_up != approach_up), outcome_up=int(outcome_up),
            approach_up=int(approach_up), mv30=mv30,
            cvd_div10=int(s10 != 0 and np.sign(s10) != np.sign(pslope)),
            cvd_div30=int(s30 != 0 and np.sign(s30) != np.sign(pslope)),
            delta_opposes=int(D[i] != 0 and np.sign(D[i]) != (1 if approach_up else -1)),
            delta_ratio=D[i] / V[i] if V[i] else np.nan,
            vol_pct=float((V[:i] < V[i]).mean()),
            trades_pct=float((TR[:i] < TR[i]).mean())))

e = pd.DataFrame(rows)
e['spike'] = (e.vol_pct > 0.9).astype(int)
e['tspike'] = (e.trades_pct > 0.9).astype(int)
ns = e.date.nunique()
print(f"\nresolved-race events {len(e):,} | sessions {ns:,}")
print(f"LVN arrivals (old def, no retirement) {int(e.arrival.sum()):,} "
      f"({e.arrival.sum()/ns:.1f}/session)   "
      f"with retirement {int(e.arrivalR.sum()):,} ({e.arrivalR.sum()/ns:.1f}/session)")
print(f"minutes spent inside a band {int(e.in_band.sum()):,} ({e.in_band.mean():.1%} of events)")
print(f"base reversal {e.reversal.mean():.2%}   base P(up) {e.outcome_up.mean():.2%}")
print(f"divergence rate {e.cvd_div10.mean():.1%} (10m) / {e.cvd_div30.mean():.1%} (30m)")
e.to_parquet('/tmp/flow/lvn_cvd_events.parquet', index=False)

cod, ucl = pd.factorize(e.date.values)
K = cod.max() + 1
out_r = e.reversal.values.astype(float)
out_u = e.outcome_up.values.astype(float)


def boot_cells(cells, out, n_boot=N_BOOT):
    N = np.stack([np.bincount(cod[cells == k], minlength=K) for k in range(4)])
    S = np.stack([np.bincount(cod[cells == k], weights=out[cells == k], minlength=K)
                  for k in range(4)])
    W = rng.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        M = (W @ S.T) / (W @ N.T)
        return dict(interaction=(M[:, 0] - M[:, 1]) - (M[:, 2] - M[:, 3]),
                    row_effect=M[:, 0] - M[:, 2],
                    col_effect=M[:, 0] - M[:, 1],
                    simple=M[:, 0] - M[:, 1])


T = []
print("\n" + "#" * 94)
print("# THE 2x2   (cell = reversal rate, race +/-100pt, every minute, no grid)")
print("#" * 94)


def run_2x2(title, flag, feat, out=out_r, bench=None):
    at = (e[flag] == 1).values
    dv = (e[feat] == 1).values
    cells = np.where(at & dv, 0, np.where(at & ~dv, 1, np.where(~at & dv, 2, 3)))
    r = [out[cells == k].mean() for k in range(4)]
    nn = [int((cells == k).sum()) for k in range(4)]
    br = boot_cells(cells, out)
    lo, hi = np.percentile(br['interaction'], [2.5, 97.5])
    lo2, hi2 = np.percentile(br['row_effect'], [2.5, 97.5])
    lo3, hi3 = np.percentile(br['col_effect'], [2.5, 97.5])
    print(f"\n--- {title} ---")
    print(f"{'':>24} | {'yes':>8} | {'no':>8} | {'effect':>8} | {'n yes/no':>13}")
    print(f"{'at a level':>24} | {r[0]:>7.2%} | {r[1]:>7.2%} | {r[0]-r[1]:>+7.2%} | "
          f"{nn[0]:>6,}/{nn[1]:<6,}")
    print(f"{'elsewhere':>24} | {r[2]:>7.2%} | {r[3]:>7.2%} | {r[2]-r[3]:>+7.2%} | "
          f"{nn[2]:>6,}/{nn[3]:<6,}")
    print(f"  feature AT a level : {r[0]:.2%} vs {r[1]:.2%} = {r[0]-r[1]:+.2%}   "
          f"CI [{lo3:+.2%},{hi3:+.2%}]")          # col_effect = M0 - M1
    print(f"  level when feature : {r[0]:.2%} vs {r[2]:.2%} = {r[0]-r[2]:+.2%}   "
          f"CI [{lo2:+.2%},{hi2:+.2%}]")          # row_effect = M0 - M2
    print(f"  INTERACTION        : {(r[0]-r[1])-(r[2]-r[3]):+.2%}   CI [{lo:+.2%},{hi:+.2%}]")
    T.append(dict(test=title, a=r[0], b=r[1], c=r[2], dd=r[3],
                  effect_at_level=r[0]-r[1], level_effect=r[0]-r[2],
                  interaction=(r[0]-r[1])-(r[2]-r[3]),
                  n_a=nn[0], n_b=nn[1], n_c=nn[2], n_d=nn[3]))


run_2x2("arrival (old def) x CVD divergence 10m", 'arrival', 'cvd_div10')
run_2x2("arrival (old def) x CVD divergence 30m", 'arrival', 'cvd_div30')
run_2x2("arrival (retiring bands) x CVD divergence 10m", 'arrivalR', 'cvd_div10')
run_2x2("IN BAND (state) x CVD divergence 10m", 'in_band', 'cvd_div10')
run_2x2("IN BAND (state) x CVD divergence 30m", 'in_band', 'cvd_div30')

print("\n" + "#" * 94)
print("# same 2x2 with ACTIVITY instead of divergence")
print("#" * 94)
run_2x2("arrival (old def) x volume top 10%", 'arrival', 'spike')
run_2x2("arrival (old def) x trade count top 10%", 'arrival', 'tspike')

print("\n" + "#" * 94)
print("# directional check at levels")
print("#" * 94)
for flag in ['arrival', 'in_band']:
    at = (e[flag] == 1).values
    m = at & (e.delta_ratio.values > 0)
    mm = at & ~(e.delta_ratio.values > 0)
    print(f"  {flag:>8}: buy-dominated {out_u[m].mean():.2%} (n={m.sum():,})   "
          f"sell-dominated {out_u[mm].mean():.2%} (n={mm.sum():,})   "
          f"diff {out_u[m].mean()-out_u[mm].mean():+.2%}")

print("\n" + "#" * 94)
print("# 30-minute range (points) at levels vs elsewhere")
print("#" * 94)
for flag in ['arrival', 'in_band']:
    at = (e[flag] == 1).values
    print(f"  {flag:>8}: {e.mv30.values[at].mean():>6.1f} vs elsewhere "
          f"{e.mv30.values[~at].mean():>6.1f}  ratio "
          f"{e.mv30.values[at].mean()/e.mv30.values[~at].mean():.2f}x")
for lab, col in [('volume top 10%', 'spike'), ('trade count top 10%', 'tspike')]:
    m = (e.arrival == 1).values & (e[col] == 1).values
    base = (e.arrival == 1).values & (e[col] == 0).values
    print(f"  arrivals, {lab}: {e.mv30.values[m].mean():>6.1f} vs {e.mv30.values[base].mean():>6.1f}"
          f"  ratio {e.mv30.values[m].mean()/e.mv30.values[base].mean():.2f}x  (n={m.sum():,})")

print("\n" + "#" * 94)
print("# by year: arrival reversal rate and divergence-at-arrival")
print("#" * 94)
e['year'] = pd.to_datetime(e.date).dt.year
print(f"{'year':>6} | {'arrivals':>9} | {'arr rate':>9} | {'div@arr':>9} | {'n_div':>7} | "
      f"{'all div':>9} | {'n':>7}")
for y in sorted(e.year.unique()):
    s = e[(e.year == y) & (e.arrival == 1)]
    if len(s) < 100:
        continue
    sp = s[s.cvd_div10 == 1]
    alld = e[(e.year == y) & (e.cvd_div10 == 1)]
    print(f"{y:>6} | {len(s):>9,} | {s.reversal.mean():>8.2%} | "
          f"{(sp.reversal.mean() if len(sp) > 30 else float('nan')):>8.2%} | {len(sp):>7,} | "
          f"{alld.reversal.mean():>8.2%} | {len(alld):>7,}")

pd.DataFrame(T).to_csv('results/lvn_cvd_everyminute.csv', index=False)
print("\nwrote results/lvn_cvd_everyminute.csv")
