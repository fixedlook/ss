"""
Deep dive on the CVD divergence signal.

The headline cell: divergence at an LVN arrival = 52.58% reversal (n=1,181) vs
49.44% for arrivals without divergence, interaction +3.49pp CI [+0.05,+6.85],
and it survives the random-level placebo (0/60 seeds).

One positive cell can be luck. This script asks whether the signal behaves like
something real:

  A. LOOKBACK SWEEP   - 5/10/15/20/30 minutes. Does only the published window
                        work (suspicious) or is the whole family positive (robust)?
  B. DOSE-RESPONSE    - is a BIGGER divergence followed by a BIGGER reversal
                        effect? Monotone = the signal has substance.
  C. ASYMMETRY        - bullish (price down / CVD up) vs bearish (price up / CVD down).
  D. TIMING           - minutes from arrival to the winning barrier.
  E. ABSORPTION       - divergence accompanied by above-median volume at the level.
  F. ERA STABILITY    - three clean eras, with clustered CIs on each.

Everything is re-derived from /tmp/flow/lvn_cvd_events.parquet (arrival flags,
outcomes) + the per-minute flow file (extra slopes). No re-simulation of the
race except for timing on arrival rows.

Run:  /tmp/venv/bin/python research/lvn_cvd_deep.py
"""
import numpy as np
import pandas as pd

FLOW = '/tmp/flow/flow_good.parquet'
EV = '/tmp/flow/lvn_cvd_events.parquet'
WINDOWS = [5, 10, 15, 20, 30]
BARRIER = 100.0
N_BOOT = 1500
rng = np.random.default_rng(4242)

E = pd.read_parquet(EV)
print(f"events {len(E):,} | sessions {E.date.nunique():,} | arrivals {int(E.arrival.sum()):,}")

flow = pd.read_parquet(FLOW)
flow['flow'] = flow.bid_volume + flow.ask_volume
flow['delta'] = flow.ask_volume - flow.bid_volume
flow['dstr'] = flow['ny'].dt.strftime('%Y-%m-%d')
gday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in flow.groupby('dstr')}
gday = {d: g for d, g in gday.items() if len(g) >= 100}
print(f"flow sessions {len(gday):,}")

# ---------- attach extra features to every event ----------
cols = {f'dC{w}': np.full(len(E), np.nan) for w in WINDOWS}
cols.update({f'dCD{w}': np.full(len(E), np.nan) for w in WINDOWS})
cols.update({f'med{w}': np.full(len(E), np.nan) for w in WINDOWS})
time_to_win = np.full(len(E), np.nan)
E_idx = {d: np.where(E.date.values == d)[0] for d in E.date.unique()}

for d, idxs in E_idx.items():
    g = gday.get(d)
    if g is None:
        continue
    C = g.close.values
    H = g.high.values
    L = g.low.values
    D = g.delta.values
    cvd = np.cumsum(D)
    ii = E.i.values[idxs]

    for w in WINDOWS:
        dC = np.full(len(C), np.nan)
        dCD = np.full(len(C), np.nan)
        if len(C) > w:
            dC[w:] = C[w:] - C[:-w]
            dCD[w:] = cvd[w:] - cvd[:-w]
        med = pd.Series(np.abs(dCD)).expanding(min_periods=25).median().values
        med = np.where((med == 0) | ~np.isfinite(med), np.nan, med)
        cols[f'dC{w}'][idxs] = dC[ii]
        cols[f'dCD{w}'][idxs] = dCD[ii]
        cols[f'med{w}'][idxs] = med[ii]

    # timing: bars from the event to whichever +/-100 barrier is touched first
    for k, i in zip(idxs, ii):
        ref = C[i]
        fh = H[i + 1:] >= ref + BARRIER
        fl = L[i + 1:] <= ref - BARRIER
        a = np.argmax(fh) if fh.any() else 10 ** 9
        b = np.argmax(fl) if fl.any() else 10 ** 9
        if a != b:
            time_to_win[k] = (a if a < b else b) + 1

for k, v in cols.items():
    E[k] = v
E['bars_to_win'] = time_to_win

# divergence flag and normalised magnitude, per window
for w in WINDOWS:
    dc, dcd, med = E[f'dC{w}'].values, E[f'dCD{w}'].values, E[f'med{w}'].values
    with np.errstate(invalid='ignore'):
        E[f'div{w}'] = ((dc != 0) & (dcd != 0) & (np.sign(dc) != np.sign(dcd))).astype(int)
        # signed divergence strength: >0 means CVD moved AGAINST price
        E[f'strength{w}'] = -np.sign(dc) * dcd / med

arr = E.arrival == 1
print(f"arrival rows with features: {int(arr.sum()):,}   "
      f"of which resolved race: {int((arr & E.bars_to_win.notna()).sum()):,}")

cod, ucl = pd.factorize(E.date.values)
K = cod.max() + 1
out_r = E.reversal.values.astype(float)


def cl_mean_ci(vals, mask, n_boot=N_BOOT, seed=7):
    r = np.random.default_rng(seed)
    N = np.bincount(cod[mask], minlength=K)
    S = np.bincount(cod[mask], weights=vals[mask], minlength=K)
    Wm = r.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        m = (Wm @ S) / (Wm @ N)
    m = m[np.isfinite(m)]
    return np.percentile(m, [2.5, 97.5])


def interaction_ci(ma, mb, mc, md, n_boot=N_BOOT, seed=13):
    """cluster bootstrap of (A-B)-(C-D) for the four cells."""
    r = np.random.default_rng(seed)
    agg = lambda m: (np.bincount(cod[m], minlength=K),
                     np.bincount(cod[m], weights=out_r[m], minlength=K))
    Na, Sa = agg(ma); Nb, Sb = agg(mb); Nc, Sc = agg(mc); Nd, Sd = agg(md)
    Wm = r.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        A = (Wm @ Sa) / (Wm @ Na); B = (Wm @ Sb) / (Wm @ Nb)
        Cc = (Wm @ Sc) / (Wm @ Nc); D = (Wm @ Sd) / (Wm @ Nd)
        d = (A - B) - (Cc - D)
    d = d[np.isfinite(d)]
    return np.percentile(d, [2.5, 97.5])


def contrast(mask_a, mask_b, n_boot=N_BOOT, seed=9):
    """cluster bootstrap of mean(a) - mean(b)"""
    r = np.random.default_rng(seed)
    Na = np.bincount(cod[mask_a], minlength=K); Sa = np.bincount(cod[mask_a], weights=out_r[mask_a], minlength=K)
    Nb = np.bincount(cod[mask_b], minlength=K); Sb = np.bincount(cod[mask_b], weights=out_r[mask_b], minlength=K)
    Wm = r.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        d = (Wm @ Sa) / (Wm @ Na) - (Wm @ Sb) / (Wm @ Nb)
    d = d[np.isfinite(d)]
    return np.percentile(d, [2.5, 97.5])


print("\n" + "=" * 100)
print("A. LOOKBACK SWEEP - divergence measured over W minutes (reversal = 'yes' column)")
print("=" * 100)
print(f"{'W':>4} | {'at level +div':>13} | {'at level -div':>13} | {'diff':>7} | {'n div':>7} | "
      f"{'elsewhere +div':>14} | {'INTERACT':>9} | {'95% CI':>17}")
sweep = []
for w in WINDOWS:
    dv = E[f'div{w}'].values == 1
    a_m = arr & dv
    b_m = arr & ~dv
    c_m = ~arr & dv
    d_m = ~arr & ~dv
    if a_m.sum() < 50:
        continue
    A, B_ = out_r[a_m].mean(), out_r[b_m].mean()
    C_, D_ = out_r[c_m].mean(), out_r[d_m].mean()
    inter = (A - B_) - (C_ - D_)
    lo, hi = interaction_ci(a_m, b_m, c_m, d_m)
    print(f"{w:>4} | {A:>12.2%} | {B_:>12.2%} | {A-B_:>+6.2%} | {a_m.sum():>7,} | "
          f"{C_:>13.2%} | {inter:>+8.2%} | [{lo:>+6.2%},{hi:>+6.2%}]")
    sweep.append(dict(W=w, div_level=A, nodiv_level=B_, diff=A - B_, n=int(a_m.sum()),
                      div_away=C_, interaction=inter, ci_lo=lo, ci_hi=hi))
pd.DataFrame(sweep).to_csv('results/lvn_cvd_deep_sweep.csv', index=False)

print("\n" + "=" * 100)
print("B. DOSE-RESPONSE - reversal rate at arrivals by divergence strength decile")
print("   (strength = -sign(price move) x cvd move / median |cvd move|, 10m window)")
print("=" * 100)
s10 = E['strength10'].values
m = arr & np.isfinite(s10)
q = pd.qcut(s10[m], 10, labels=False, duplicates='drop')
print(f"{'decile':>7} | {'strength from':>13} | {'to':>8} | {'reversal':>9} | {'n':>6}")
bins = []
for k in range(q.max() + 1):
    sel = m.copy()
    sub = s10[m]
    lo_s = sub[q == k].min(); hi_s = sub[q == k].max()
    mm = np.zeros(len(E), bool); mm[np.where(m)[0][q == k]] = True
    print(f"{k:>7} | {lo_s:>13.2f} | {hi_s:>8.2f} | {out_r[mm].mean():>8.2%} | {mm.sum():>6,}")
    bins.append(out_r[mm].mean())
print(f"\nlowest decile {bins[0]:.2%}  ->  highest decile {bins[-1]:.2%}   "
      f"(span {bins[-1]-bins[0]:+.2%})")

print("\n" + "=" * 100)
print("C. ASYMMETRY / TIMING / ABSORPTION at arrivals (10m divergence)")
print("=" * 100)
dv = E['div10'].values == 1
a_m = arr & dv
au = a_m & (E.approach_up.values == 1)      # price rising into the level
ad = a_m & (E.approach_up.values == 0)      # price falling into the level
print(f"  bearish setup (price up, CVD down): {out_r[au].mean():.2%}  n={au.sum():,}")
print(f"  bullish setup (price down, CVD up): {out_r[ad].mean():.2%}  n={ad.sum():,}")

bw = E.bars_to_win.values
print(f"\n  median bars to the winning barrier: div at level "
      f"{np.nanmedian(bw[a_m]):.0f}  vs no div {np.nanmedian(bw[arr & ~dv]):.0f}  "
      f"vs elsewhere {np.nanmedian(bw[~arr & ~dv]):.0f}")

vol = E.vol_pct.values
print(f"\n  absorption variant at arrivals:")
for lab, mm in [('div + above-median volume', a_m & (vol > 0.5)),
                ('div + below-median volume', a_m & (vol <= 0.5)),
                ('no div, above-median volume', (arr & ~dv) & (vol > 0.5))]:
    print(f"    {lab:>32}: {out_r[mm].mean():.2%}  n={mm.sum():,}")

print("\n" + "=" * 100)
print("D. ERA STABILITY of the divergence-at-arrival cell (10m)")
print("=" * 100)
E['year'] = pd.to_datetime(E.date).dt.year
eras = [('2021-2022', [2021, 2022]), ('2023-2024', [2023, 2024]), ('2026', [2026])]
print(f"{'era':>12} | {'div rate':>9} | {'n':>6} | {'no-div':>8} | {'n':>7} | {'diff':>7} | {'95% CI':>17}")
for lab, yrs in eras:
    sel = np.isin(E.year.values, yrs)
    mm = sel & a_m
    nn = sel & arr & ~dv
    if mm.sum() < 30:
        continue
    lo, hi = contrast(mm, nn, seed=11)
    print(f"{lab:>12} | {out_r[mm].mean():>8.2%} | {mm.sum():>6,} | {out_r[nn].mean():>7.2%} | "
          f"{nn.sum():>7,} | {out_r[mm].mean()-out_r[nn].mean():>+6.2%} | [{lo:>+6.2%},{hi:>+6.2%}]")

E.to_parquet('/tmp/flow/lvn_cvd_deep.parquet', index=False)
print("\nwrote /tmp/flow/lvn_cvd_deep.parquet + results/lvn_cvd_deep_sweep.csv")
