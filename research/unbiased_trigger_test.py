"""
Rebuild the delta-divergence test on an UNBIASED trigger.

Why: the barrier race used the entry minute's VWAP as the reference price.
But the VWAP of a minute is not the price at the end of that minute, and the
gap between them depends on the delta sign (buying lifts price into the ask,
so a buying minute ends nearer its high).  That mechanically shortens the
distance to the upper barrier for exactly the minutes the signal selects.

Fix: use the TRUE minute close from the 1-minute OHLC bar file
(MNQZ26 - 1 min - RTH.csv, Rome time -> ET) as the reference and start the race
on the next minute.  Both barriers are then equidistant from an observed price.

Reference bar file timestamps are Europe/Rome; 15:30 Rome == 09:30 ET.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
HORIZON = 60
BARRIER = 5.25
rng = np.random.default_rng(3)

# ---- footprint minutes (RTH, ET) ----
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
mb['vwap'] = mb.pv / mb.vol
mb['delta'] = mb.ask - mb.bid
print(f"footprint minutes {len(mb):,}  {mb.date.min()} .. {mb.date.max()}")

# ---- 1-min OHLC bars ----
bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = (bars.ts.dt.tz_localize('Europe/Rome')
              .dt.tz_convert('America/New_York'))
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'open', 'high', 'low', 'close']]
print(f"bars {len(bars):,}  {bars.date.min()} .. {bars.date.max()}")

m = mb.merge(bars, on=['date', 'minute'], how='inner')
print(f"matched minutes {len(m):,}")
chk = (m.high >= m.hi - 0.26).mean()
print(f"bars' high >= footprint high (loose check): {chk:.3%}")

# ---- how big is the VWAP-vs-close offset, by delta sign? ----
m['off'] = m.close - m.vwap
print("\n=== close minus VWAP within the same minute ===")
for lab, sel in [('delta > 0', m.delta > 0), ('delta < 0', m.delta < 0)]:
    o = m.off[sel].dropna()
    print(f"  {lab}: mean {o.mean():+.3f} pts  median {o.median():+.3f}  n={len(o):,}")
print("  -> if these differ in sign, the VWAP trigger was mechanically slanted.")
print(f"  inches to upper barrier from VWAP vs from close:")
mu = (m.high - m.vwap)[m.delta > 0].mean()
md = (m.vwap - m.low)[m.delta > 0].mean()
mu2 = (m.high - m.close)[m.delta > 0].mean()
md2 = (m.close - m.low)[m.delta > 0].mean()
print(f"    delta>0 minutes: from VWAP  up {mu:5.2f} / down {md:5.2f}")
print(f"    delta>0 minutes: from close up {mu2:5.2f} / down {md2:5.2f}")


def race(H, L, i0, ref, B, n):
    for k in range(i0 + 1, min(n, i0 + HORIZON)):
        hu, hd = H[k] >= ref + B, L[k] <= ref - B
        if hu and hd:
            continue
        if hu:
            return 'up'
        if hd:
            return 'down'
    return None


rows = []
for d, g in m.groupby('date'):
    g = g.reset_index(drop=True)
    H, L, C = g.high.values, g.low.values, g.close.values
    D = g.delta.values
    n = len(g)
    if n < 80:
        continue
    for i0 in range(11, n - 2):
        pm = C[i0 - 1] - C[i0 - 11]
        if pm == 0:
            continue
        up = pm > 0
        sgn = int(np.sign(D[i0]))
        if sgn == 0:
            continue
        div = bool(np.sign(pm) != sgn)
        for tag, ref in [('vwap', g.vwap.values[i0]), ('close', C[i0])]:
            if not np.isfinite(ref):
                continue
            oc = race(H, L, i0, ref, BARRIER, n)
            if oc is None:
                continue
            rev = 1 if ((oc == 'up' and not up) or (oc == 'down' and up)) else 0
            rows.append(dict(tag=tag, div=div, rev=rev, dvg=int(div), event_date=str(d)))

r = pd.DataFrame(rows)
print(f"\ntriggered {len(r):,}")
print("\n=== reversal rate: divergence vs with-trend ===")
print(f"{'reference':>10} | {'group':>12} | {'n':>7} | {'reversal':>9}")
for tag in ['vwap', 'close']:
    s = r[r.tag == tag]
    a = s[s.dvg == 1].rev
    b = s[s.dvg == 0].rev
    print(f"{tag:>10} | {'divergence':>12} | {len(a):>7} | {a.mean():>9.2%}")
    print(f"{tag:>10} | {'with trend':>12} | {len(b):>7} | {b.mean():>9.2%}")
    print(f"{tag:>10} | {'DIFFERENCE':>12} | {'':>7} | {a.mean()-b.mean():>+9.2%}")
r.to_csv('results/unbiased_trigger_test.csv', index=False)
print("\nwrote results/unbiased_trigger_test.csv")
