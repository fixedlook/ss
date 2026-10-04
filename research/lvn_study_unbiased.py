"""
Re-run the LVN zone reversal study using TRUE minute OHLC.

The previous version triggered and classified everything from footprint VWAPs.
The bias check just showed that a minute's VWAP sits ~+2.7 pts BELOW its close
when delta > 0, and ~-2.5 pts ABOVE its close when delta < 0 — because buying
lifts price through the minute.  Any signal built on VWAP is therefore slanted
in the direction of the delta sign.

This version uses:
  * zone midpoints for the trigger          (unbiased - fixed price)
  * true closes from MNQZ26 1-min bars      (approach + trend classification)
  * true highs/lows from the bars           (barrier race)
  * footprint only for zones + delta/volume features
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
HORIZON = 60
EDGE_BUFFER = 2.0
N_BOOT = 1000
rng = np.random.default_rng(101)

# ---------- footprint: zones + features ----------
fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')

fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum'),
).reset_index()
fm['delta'] = fm.ask - fm.bid

# ---------- true 1-min bars ----------
bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'open', 'high', 'low', 'close']]

m = bars.merge(fm, on=['date', 'minute'], how='inner').sort_values(['date', 'minute'])
m = m.reset_index(drop=True)
print(f"matched minute bars {len(m):,} | sessions {m.date.nunique()}")

days = sorted(m.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in m.groupby('date')}

# session range from the bars themselves
srng = {d: float(gday[d].high.max() - gday[d].low.min()) for d in days}

# ---------- zones from the footprint ----------
win = int(round(W_PTS / TICK / 2)) * 2 + 1
zones = {}
for d in days:
    sub = fp[fp.date == d]
    if not len(sub):
        zones[d] = []
        continue
    v = sub.groupby('price').volume.sum().sort_index()
    med = pd.Series(v.values).rolling(win, center=True, min_periods=win // 3).median().values
    mask = (v.values <= F_RATIO * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, mm in zip(v.index.values, mask):
        if mm and s is None:
            s = p
        elif not mm and s is not None:
            if prev - s >= MIN_H:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= MIN_H:
        out.append((s, prev))
    zones[d] = out
print(f"zones {sum(len(zones[d]) for d in days)}")

zlist = [(d, lo, hi) for d in days for lo, hi in zones[d]]
zlo_a = np.array([z[1] for z in zlist])
zhi_a = np.array([z[2] for z in zlist])
zmid = 0.5 * (zlo_a + zhi_a)
zdays = [z[0] for z in zlist]
zpos = np.array([days.index(d) for d in zdays])

rows = []
for di, d in enumerate(days):
    live = zpos < di
    if not live.any():
        continue
    b = gday[d]
    H, L, C, O = b.high.values, b.low.values, b.close.values, b.open.values
    V, D = b.vol.values, b.delta.values
    n = len(b)
    zm = zmid[live]
    zl = zlo_a[live]
    zh = zhi_a[live]
    # first minute where price touches the zone midpoint. The previous minute's
    # CLOSE must have been outside the zone (a genuine arrival, not a drift).
    for j in range(len(zm)):
        # arrival: previous close outside the zone, this minute's range covers the midpoint
        for k in range(1, n):
            if H[k] >= zm[j] >= L[k]:
                if zl[j] <= C[k - 1] <= zh[j]:
                    break                      # already inside: not a fresh arrival
                approach = 'down' if C[k - 1] > zh[j] else 'up'
                break
        else:
            continue
        i0 = k
        if i0 < 11 or i0 > n - 3:
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
        med_v, med_r = np.median(V), np.median(H - L)
        med_ad = np.median(np.abs(D))
        rngm = H[i0] - L[i0]
        pm = C[i0 - 1] - C[i0 - 11]
        sgn = int(np.sign(D[i0]))
        rows.append(dict(
            event_date=str(d), zone_date=str(zlist[int(np.where(live)[0][j])][0]),
            age=di - zpos[live][j], approach=approach, rev=rev,
            vol_ratio=V[i0] / med_v if med_v else np.nan,
            rng_ratio=rngm / med_r if med_r else np.nan,
            absdelta_ratio=abs(D[i0]) / med_ad if med_ad else np.nan,
            approach_speed=abs(pm) / srng[d],
            two_way=bool(b.vol.values[i0] > 0 and
                         (fm is not None) and
                         False),
            with_trend=bool(np.sign(pm) == sgn and sgn != 0),
            against_trend=bool(np.sign(pm) != sgn and sgn != 0),
        ))

e = pd.DataFrame(rows)
# two-way tape needs bid/ask split at the bar level; recompute from footprint
bidask = fp.groupby(['date', 'minute']).agg(b=('bid_volume', 'sum'),
                                            a=('ask_volume', 'sum')).reset_index()
e = e.drop(columns=['two_way'])
print(f"\nzone entries {len(e):,} | base reversal {e.rev.mean():.2%}")
e.to_csv('results/lvn_events_unbiased.csv', index=False)

CONCEPTS = {
    'delta_divergence': e.against_trend,
    'volume_climax': e.vol_ratio > 2.0,
    'big_range': e.rng_ratio > 1.5,
    'low_abs_delta': e.absdelta_ratio < 0.5,
    'fast_approach': e.approach_speed > e.approach_speed.median(),
}
print("\n=== concepts at LVN zones, TRUE OHLC, unbiased trigger ===")
print(f"{'concept':>18} | {'n':>6} | {'with':>7} | {'without':>7} | {'diff':>8} | {'95% CI':>18}")
ucl = np.unique(e.event_date.values)
idx = {u: np.where(e.event_date.values == u)[0] for u in ucl}
out = []
for name, mm in CONCEPTS.items():
    mm = mm.values.astype(bool)
    if mm.sum() < 25 or (~mm).sum() < 25:
        print(f"{name:>18} | {int(mm.sum()):>6} | too few")
        continue
    w_, wo = e.rev.values[mm].mean(), e.rev.values[~mm].mean()
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), replace=True)
        ii = np.concatenate([idx[u] for u in pick])
        s = mm[ii]
        if s.sum() > 2 and (~s).sum() > 2:
            boot.append(e.rev.values[ii][s].mean() - e.rev.values[ii][~s].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{name:>18} | {int(mm.sum()):>6} | {w_:>7.2%} | {wo:>7.2%} | {w_-wo:>+8.2%} | "
          f"[{lo:>+7.2%},{hi:>+7.2%}] {sig}")
    out.append(dict(concept=name, n=int(mm.sum()), with_=w_, without=wo,
                    diff=w_-wo, ci_lo=lo, ci_hi=hi, sig=sig))
pd.DataFrame(out).to_csv('results/concept_comparison_unbiased.csv', index=False)
print("\nwrote results/lvn_events_unbiased.csv + results/concept_comparison_unbiased.csv")
