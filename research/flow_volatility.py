"""
Order flow predicts the SIZE of the next move, not its direction.

Clean version: EVERY minute (no race-resolution filter, no 10-min grid, no
future conditioning of any kind). At minute i we know only past information:
  - vol_pct   = expanding within-session percentile of volume  (no lookahead)
  - trades_pct= same for trade count
Outcome: range of the next 30 minutes (max high - min low), i.e. how far it travels.

Controls for the two obvious confounds:
  1. cross-day differences  -> normalise by the session's OWN median 30-min range
  2. time-of-day U-shape    -> repeat the comparison inside each hour bucket

Run:  /tmp/venv/bin/python research/flow_volatility.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['flow'] = df['bid_volume'] + df['ask_volume']
df['buy_ratio'] = df['ask_volume'] / df['flow']
df = df[df.flow > 0].sort_values('timestamp').reset_index(drop=True)
gday = {d: g.reset_index(drop=True) for d, g in df.groupby('date')}
print(f"sessions {df.date.nunique():,} | flow minutes {len(df):,}")

rows = []
for d, b in gday.items():
    H, L, V = b.high.values, b.low.values, b.flow.values
    TR = b.trades.values.astype(float)
    n = len(b)
    if n < 80:
        continue
    med_v = pd.Series(V).expanding(30).median().values
    med_tr = pd.Series(TR).expanding(30).median().values
    hod = pd.to_datetime(b.timestamp, unit='ms', utc=True).dt.tz_convert(
        'America/New_York').dt.hour.values

    mvs = np.full(n, np.nan)
    for i in range(30, n - 20):
        mvs[i] = H[i + 1:i + 31].max() - L[i + 1:i + 31].min()
    sess_med = np.nanmedian(mvs)

    for i in range(40, n - 20):
        if not np.isfinite(mvs[i]) or not med_v[i] or not med_tr[i]:
            continue
        vp = float((V[:i] < V[i]).mean())
        tp = float((TR[:i] < TR[i]).mean())
        rows.append(dict(date=str(d), i=i, hod=int(hod[i]), mv30=float(mvs[i]),
                         mv_rel=float(mvs[i] / sess_med) if sess_med else np.nan,
                         vols=float(V[i] / med_v[i]), trs=float(TR[i] / med_tr[i]),
                         vol_pct=vp, trades_pct=tp, mod=int(i % 10)))

e = pd.DataFrame(rows)
print(f"events {len(e):,} | sessions {e.date.nunique()}")
e.to_parquet('/tmp/flow/events_vol.parquet', index=False)

print("\nbaseline 30-min range: mean %.1f  median %.1f points" % (e.mv30.mean(), e.mv30.median()))

FEAT = [('volume in top 10% of session', e.vol_pct > 0.9),
        ('volume in top 20% of session', e.vol_pct > 0.8),
        ('volume in bottom 50% of session', e.vol_pct < 0.5),
        ('trade count top 10%', e.trades_pct > 0.9),
        ('trade count top 20%', e.trades_pct > 0.8),
        ('trade count bottom 50%', e.trades_pct < 0.5)]

print("\n" + "=" * 88)
print("A. UNCONDITIONAL - every minute, outcome = next 30-min range")
print("=" * 88)
print(f"{'feature':>34} | {'with':>8} | {'without':>8} | {'ratio':>6} | {'>=100pt':>8} | {'base':>7} | {'n':>8}")
for lab, m in FEAT:
    m = np.asarray(m, bool)
    a, b_ = e.mv30.values[m].mean(), e.mv30.values[~m].mean()
    pa = (e.mv30.values[m] >= 100).mean()
    pb = (e.mv30.values[~m] >= 100).mean()
    print(f"{lab:>34} | {a:>7.1f} | {b_:>7.1f} | {a/b_:>5.2f}x | {pa:>7.1%} | {pb:>6.1%} | {m.sum():>8,}")

print("\n" + "=" * 88)
print("B. WITHIN-SESSION NORMALISED (each event divided by that session's median 30-min range)")
print("=" * 88)
print(f"{'feature':>34} | {'with':>8} | {'without':>8} | {'ratio':>6}")
for lab, m in FEAT:
    m = np.asarray(m, bool)
    a, b_ = e.mv_rel.values[m].mean(), e.mv_rel.values[~m].mean()
    print(f"{lab:>34} | {a:>7.2f} | {b_:>7.2f} | {a/b_:>5.2f}x")

print("\n" + "=" * 88)
print("C. TIME-OF-DAY CONTROL - 'volume top 10%' ratio inside each hour of the session")
print("=" * 88)
m = np.asarray(e.vol_pct > 0.9, bool)
print(f"{'hour (NY)':>10} | {'with':>8} | {'without':>8} | {'ratio':>6} | {'n_with':>7}")
for h in sorted(e.hod.unique()):
    sel = e.hod.values == h
    s = m & sel
    if s.sum() < 50 or (~s & sel).sum() < 50:
        continue
    a, b_ = e.mv30.values[s].mean(), e.mv30.values[~s & sel].mean()
    print(f"{h:>10} | {a:>7.1f} | {b_:>7.1f} | {a/b_:>5.2f}x | {s.sum():>7,}")

print("\n" + "=" * 88)
print("D. BY YEAR - 'volume top 10%' vs rest (next 30-min range, points)")
print("=" * 88)
e['year'] = pd.to_datetime(e.date).dt.year
print(f"{'year':>6} | {'with':>8} | {'without':>8} | {'ratio':>6} | {'n_with':>7}")
for y in sorted(e.year.unique()):
    sel = e.year.values == y
    s = m & sel
    if s.sum() < 50 or (~s & sel).sum() < 50:
        continue
    a, b_ = e.mv30.values[s].mean(), e.mv30.values[~s & sel].mean()
    print(f"{y:>6} | {a:>7.1f} | {b_:>7.1f} | {a/b_:>5.2f}x | {s.sum():>7,}")

# ---- cluster bootstrap on the headline ratio ----
rng = np.random.default_rng(7)
cod, _ = pd.factorize(e.date.values)
K = cod.max() + 1
for lab, m in [('vol top 10%', e.vol_pct > 0.9), ('trades top 10%', e.trades_pct > 0.9)]:
    m = np.asarray(m, bool)
    wy = np.bincount(cod, weights=(m * e.mv30.values).astype(float), minlength=K)
    ny = np.bincount(cod, weights=m.astype(float), minlength=K)
    wo = np.bincount(cod, weights=((~m) * e.mv30.values).astype(float), minlength=K)
    no = np.bincount(cod, weights=(~m).astype(float), minlength=K)
    r = []
    for _ in range(500):
        W = rng.multinomial(K, np.full(K, 1.0 / K)).astype(float)
        r.append((W @ wy) / (W @ ny) / ((W @ wo) / (W @ no)))
    lo, hi = np.percentile(r, [2.5, 97.5])
    print(f"\n{lab}: ratio 95% CI [{lo:.2f}x, {hi:.2f}x]  (500 session-bootstraps)")
