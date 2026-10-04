"""
Re-run with the user's rule: zones are NEVER retired.

Correction from the user: a zone formed Monday stays valid the next day even if
price runs through it.  So every zone stays live from the moment it forms.

This differs from v4, which dropped a zone once price traded through it.

Event  : first time on each later session that price reaches the zone midpoint.
Outcome: symmetric race from the midpoint. barrier = zone edge +/- buffer.
         reversal = the barrier on the side price came from is touched first.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
HORIZON = 60
EDGE_BUFFER = 2.0
N_BOOT = 1000
rng = np.random.default_rng(77)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fp['pv'] = fp.price * fp.volume

mb = fp.groupby(['date', 'minute']).agg(
    hi=('price', 'max'), lo=('price', 'min'), pv=('pv', 'sum'),
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum'),
).reset_index()
mb['delta'] = mb.ask - mb.bid
mb['rng'] = mb.hi - mb.lo
mb['vwap'] = mb.pv / mb.vol
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)
days = sorted(mb.date.unique())
gday = {d: s.reset_index(drop=True) for d, s in mb.groupby('date')}
srng = {d: float(gday[d].hi.max() - gday[d].lo.min()) for d in days}
print(f"sessions {len(days)} | minute bars {len(mb):,}")

# --- zones ---
win = int(round(W_PTS / TICK / 2)) * 2 + 1
zones = {}
for d in days:
    v = fp[fp.date == d].groupby('price').volume.sum().sort_index()
    med = pd.Series(v.values).rolling(win, center=True, min_periods=win // 3).median().values
    mask = (v.values <= F_RATIO * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, m in zip(v.index.values, mask):
        if m and s is None:
            s = p
        elif not m and s is not None:
            if prev - s >= MIN_H:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= MIN_H:
        out.append((s, prev))
    zones[d] = out
print(f"zones {sum(len(zones[d]) for d in days)}")

# --- build the full live set: (zone_date, zlo, zhi) ---
zlist = [(d, lo, hi) for d in days for lo, hi in zones[d]]
zlo_a = np.array([z[1] for z in zlist])
zhi_a = np.array([z[2] for z in zlist])
zdate_idx = np.array([days.index(z[0]) for z in zlist])
zmid = 0.5 * (zlo_a + zhi_a)
print(f"live zone instances {len(zlist)}")

rows = []
coverage = []
for di, d in enumerate(days):
    live = zdate_idx < di                     # NEVER RETIRED - all past zones valid
    if not live.any():
        continue
    b = gday[d]
    H, L, V, Wv = b.hi.values, b.lo.values, b.vwap.values, b.vwap.values
    n = len(b)
    dlt = b.delta.values
    vol = b.vol.values
    rge = b.rng.values
    zl = zlo_a[live]
    zh = zhi_a[live]
    zm = zmid[live]
    # where is the midpoint inside this session?
    inside = (L[:, None] <= zm[None, :]) & (H[:, None] >= zm[None, :])
    anyhit = inside.any(axis=0)
    coverage.append(float(((H[:, None] >= zl[None, :]) & (L[:, None] <= zh[None, :])).any(axis=0).sum()
                          / len(zl)))
    first = np.where(anyhit, inside.argmax(axis=0), -1)
    med_v, med_r = np.median(vol), np.median(rge)
    med_ad = np.median(np.abs(dlt))
    sr = srng[d]
    for j in np.where(first > 0)[0]:
        i0 = int(first[j])
        if i0 < 3 or i0 >= n - 2:
            continue
        prev = Wv[i0 - 3:i0].mean()
        if prev > zh[j]:
            approach = 'down'
        elif prev < zl[j]:
            approach = 'up'
        else:
            continue
        up_bar, dn_bar = zh[j] + EDGE_BUFFER, zl[j] - EDGE_BUFFER
        outcome = None
        for kk in range(i0 + 1, min(n, i0 + HORIZON)):
            hu, hd = H[kk] >= up_bar, L[kk] <= dn_bar
            if hu and hd:
                continue
            if hu:
                outcome = 'up'
                break
            if hd:
                outcome = 'down'
                break
        if outcome is None:
            continue
        rev = 1 if ((outcome == 'up' and approach == 'down') or
                    (outcome == 'down' and approach == 'up')) else 0
        sgn = int(np.sign(dlt[i0]))
        pm = Wv[i0 - 1] - Wv[max(0, i0 - 11)]
        rows.append(dict(
            event_date=str(d), zone_date=str(zlist[j][0]), age=di - zdate_idx[j],
            approach=approach, rev=rev,
            vol_ratio=vol[i0] / med_v if med_v else np.nan,
            rng_ratio=rge[i0] / med_r if med_r else np.nan,
            absdelta_ratio=abs(dlt[i0]) / med_ad if med_ad else np.nan,
            approach_speed=abs(pm) / sr,
            two_way=bool(b.bid.values[i0] > 0.35 * vol[i0] and b.ask.values[i0] > 0.35 * vol[i0]),
            with_trend=bool(np.sign(pm) == sgn and sgn != 0),
            against_trend=bool(np.sign(pm) != sgn and sgn != 0),
        ))

e = pd.DataFrame(rows)
print(f"\nentry events {len(e):,}")
print(f"base reversal rate {e.rev.mean():.2%}")
print(f"mean live zones per session {len(zlist)/len(days):.0f}")
e.to_csv('results/lvn_neverretire_events.csv', index=False)

print("\nage of zone at entry:")
e['age_b'] = pd.cut(e.age, [0, 1, 3, 10, 30, 1000],
                    labels=['1 session', '2-3', '4-10', '11-30', '31+'])
print(e.groupby('age_b', observed=True).agg(n=('rev', 'size'), reversal=('rev', 'mean')).to_string())

CONCEPTS = {
    'delta_divergence': e.against_trend,
    'volume_climax': e.vol_ratio > 2.0,
    'big_range': e.rng_ratio > 1.5,
    'low_abs_delta': e.absdelta_ratio < 0.5,
    'two_way_tape': e.two_way,
    'fast_approach': e.approach_speed > e.approach_speed.median(),
}
print("\n=== concepts (zones never retired) ===")
print(f"{'concept':>18} | {'n':>6} | {'with':>7} | {'without':>7} | {'diff':>8} | {'95% CI':>18}")
ucl = np.unique(e.event_date.values)
idx = {u: np.where(e.event_date.values == u)[0] for u in ucl}
out = []
for name, m in CONCEPTS.items():
    m = m.values.astype(bool)
    if m.sum() < 25 or (~m).sum() < 25:
        print(f"{name:>18} | {int(m.sum()):>6} | too few")
        continue
    w_, wo = e.rev.values[m].mean(), e.rev.values[~m].mean()
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), replace=True)
        ii = np.concatenate([idx[u] for u in pick])
        mm = m[ii]
        if mm.sum() > 2 and (~mm).sum() > 2:
            boot.append(e.rev.values[ii][mm].mean() - e.rev.values[ii][~mm].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{name:>18} | {int(m.sum()):>6} | {w_:>7.2%} | {wo:>7.2%} | {w_-wo:>+8.2%} | "
          f"[{lo:>+7.2%},{hi:>+7.2%}] {sig}")
    out.append(dict(concept=name, n=int(m.sum()), with_=w_, without=wo, diff=w_-wo,
                    ci_lo=lo, ci_hi=hi))
pd.DataFrame(out).to_csv('results/concept_comparison_neverretire.csv', index=False)
print("\nwrote results/lvn_neverretire_events.csv")
