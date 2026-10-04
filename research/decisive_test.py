"""
DECISIVE TEST.

Every earlier version measured the barrier race from a reference that is not an
observed price at the moment the race starts:

    VWAP of minute i0   -> off by +/-2.7 pts, slanted by the delta sign
    zone midpoint       -> the moment of touch is unobserved; by the end of the
                           minute price has drifted, and it drifts WITH the
                           delta sign (buying minutes close near their high)

Both slant the two barriers unequally in the direction the signal predicts.

This version uses ONLY observed prices:
    trigger   : the first minute whose CLOSE is inside the zone and whose
                previous CLOSE was outside  (a genuine arrival, observed)
    reference : that close - an actual traded price
    barriers  : reference +/- D, symmetric about an observed price
    race      : from the next minute onward
    reversal  : the barrier on the side price came from is touched first

D is fixed so the test is identical at zones and at random prices.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
HORIZON = 60
D = 8.0                 # symmetric barrier distance, in points
N_BOOT = 1000
rng = np.random.default_rng(2024)

# footprint -> zones + delta/volume
fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum')).reset_index()
fm['delta'] = fm.ask - fm.bid

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'open', 'high', 'low', 'close']]
m = bars.merge(fm, on=['date', 'minute'], how='inner').sort_values(['date', 'minute'])
m = m.reset_index(drop=True)
days = sorted(m.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in m.groupby('date')}
print(f"matched bars {len(m):,} | sessions {len(days)}")

win = int(round(W_PTS / TICK / 2)) * 2 + 1
zones = {}
for d in days:
    v = fp[fp.date == d].groupby('price').volume.sum().sort_index()
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
zlist = [(d, lo, hi) for d in days for lo, hi in zones[d]]
zlo_a = np.array([z[1] for z in zlist])
zhi_a = np.array([z[2] for z in zlist])
zpos = np.array([days.index(z[0]) for z in zlist])
print(f"zones {len(zlist)}")


def run(gidx, ref_price, approach_up, tag, dates, zlo=None, zhi=None):
    """symmetric race from an observed reference price"""
    out = []
    for k, (g, r, up) in enumerate(zip(gidx, ref_price, approach_up)):
        d = dates[k]
        b = gday[pd.Timestamp(d).date()]
        H, L = b.high.values, b.low.values
        n = len(b)
        if g + 1 >= n - 1:
            continue
        up_bar, dn_bar = r + D, r - D
        oc = None
        for kk in range(g + 1, min(n, g + 1 + HORIZON)):
            hu, hd = H[kk] >= up_bar, L[kk] <= dn_bar
            if hu and hd:
                continue
            if hu:
                oc = 'up'
                break
            if hd:
                oc = 'down'
                break
        if oc is None:
            continue
        rev = 1 if ((oc == 'up' and not up) or (oc == 'down' and up)) else 0
        out.append(dict(tag=tag, event_date=str(d), rev=rev))
    return out


# ---------------- zone arrivals ----------------
zone_rows = []
for j in range(len(zlist)):
    zd = days[int(zpos[j])]
    lo, hi = zlo_a[j], zhi_a[j]
    for di in range(int(zpos[j]) + 1, len(days)):
        d = days[di]
        b = gday[d]
        C, H, L = b.close.values, b.high.values, b.low.values
        n = len(b)
        for k in range(1, n - 1):
            inside = lo <= C[k] <= hi
            prev_in = lo <= C[k - 1] <= hi
            if inside and not prev_in:
                approach_up = C[k - 1] < lo
                zone_rows.append(dict(g=k, r=C[k], up=approach_up, d=d))
                break     # first fresh arrival only
zr = pd.DataFrame(zone_rows)
print(f"zone arrivals {len(zr):,}")

# ---------------- random-price control ----------------
ctrl = []
for d in days:
    b = gday[d]
    C = b.close.values
    n = len(b)
    if n < 80:
        continue
    for k in rng.choice(np.arange(11, n - 2), size=min(600, n - 13), replace=False):
        k = int(k)
        prev = C[k - 11]
        if C[k] == prev:
            continue
        ctrl.append(dict(g=k, r=C[k], up=bool(C[k] > prev), d=d))
cr = pd.DataFrame(ctrl)
print(f"control triggers {len(cr):,}")


res = []
for df, lab in [(zr, 'AT LVN ZONES'), (cr, 'AT RANDOM PRICES')]:
    recs = []
    for (g, r, up, d) in zip(df.g.values, df.r.values, df.up.values, df.d.values):
        b = gday[pd.Timestamp(d).date()]
        C, H, L, DEL = b.close.values, b.high.values, b.low.values, b.delta.values
        n = len(b)
        if g < 11 or g + 1 >= n - 1:
            continue
        up_bar, dn_bar = r + D, r - D
        oc = None
        for kk in range(g + 1, min(n, g + 1 + HORIZON)):
            hu, hd = H[kk] >= up_bar, L[kk] <= dn_bar
            if hu and hd:
                continue
            if hu:
                oc = 'up'
                break
            if hd:
                oc = 'down'
                break
        if oc is None:
            continue
        rev = 1 if ((oc == 'up' and not up) or (oc == 'down' and up)) else 0
        pm = C[g - 1] - C[g - 11]
        sgn = int(np.sign(DEL[g]))
        if sgn == 0 or pm == 0:
            continue
        recs.append(dict(event_date=str(d), rev=rev,
                         dvg=bool(np.sign(pm) != sgn)))
    o = pd.DataFrame(recs)
    print(f"\n=== {lab}  (symmetric {D:.0f}pt barriers from an observed close) ===")
    print(f"  overall reversal {o.rev.mean():.2%}  (n={len(o):,})")
    a = o[o.dvg].rev
    bb = o[~o.dvg].rev
    print(f"  divergence  : n={len(a):>6}  reversal {a.mean():.2%}")
    print(f"  with trend  : n={len(bb):>6}  reversal {bb.mean():.2%}")
    print(f"  DIFFERENCE  : {a.mean()-bb.mean():+.2%}")
    res.append(dict(group=lab, n=len(o), base=o.rev.mean(),
                    div_n=len(a), div=a.mean(), wtr=bb.mean(),
                    diff=a.mean() - bb.mean()))

pd.DataFrame(res).to_csv('results/decisive_test.csv', index=False)
print("\nwrote results/decisive_test.csv")
