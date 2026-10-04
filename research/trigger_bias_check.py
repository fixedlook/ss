"""
Is the delta-divergence effect real, or an artifact of the VWAP trigger?

The race starts from minute i0's VWAP.  But a minute's VWAP is not the price at
the END of that minute, and its offset from the true end-of-minute price may
depend on the delta sign (buys print at the ask = higher, sells at the bid =
lower).  That would make the two barriers unequal in practice.

Test: re-run the same race, but start the barrier clock from the FIRST price of
the NEXT minute (i0+1), which is observed after i0 has finished and is therefore
unbiased with respect to i0's delta.

Two minutes of reference are compared:
   A) VWAP of i0            (the original, possibly biased)
   B) first price of i0+1   (unbiased)
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
HORIZON = 60
BARRIER = 5.25
rng = np.random.default_rng(3)
N_PER_SESSION = 400

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
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)

# first traded price of each minute = the price row that comes first in the file
head = fp.sort_values(['date', 'minute']).groupby(['date', 'minute']).price.first().rename('first_px')
mb = mb.merge(head, on=['date', 'minute'], how='left')

days = sorted(mb.date.unique())
print(f"sessions {len(days)}")

print("\n=== how far is a minute's VWAP from the next minute's opening price, by delta sign? ===")
mb['next_open'] = mb.groupby('date').first_px.shift(-1)
mb['off'] = mb.next_open - mb.vwap
for lab, sel in [('delta > 0', mb.delta > 0), ('delta < 0', mb.delta < 0)]:
    o = mb.off[sel].dropna()
    print(f"  {lab}: mean offset {o.mean():+.3f} pts   median {o.median():+.3f}   n={len(o):,}")
print("  (a non-zero mean offset means the VWAP trigger is systematically off-centre)")


def race(ref, hi, lo, i0, up_bar, dn_bar, n):
    for k in range(i0 + 1, min(n, i0 + HORIZON)):
        hu, hd = hi[k] >= up_bar, lo[k] >= 0 and lo[k] <= dn_bar
        hu, hd = hi[k] >= up_bar, lo[k] <= dn_bar
        if hu and hd:
            continue
        if hu:
            return 'up'
        if hd:
            return 'down'
    return None


out = []
for d in days:
    b = mb[mb.date == d].reset_index(drop=True)
    W, H, L = b.vwap.values, b.hi.values, b.lo.values
    D = b.delta.values
    nxt = b.first_px.values
    n = len(b)
    for i0 in rng.choice(np.arange(11, n - 2), size=min(N_PER_SESSION, n - 13), replace=False):
        i0 = int(i0)
        if np.isnan(nxt[i0]):
            continue
        pm = W[i0 - 1] - W[i0 - 11]
        if pm == 0:
            continue
        up = pm > 0
        sgn = int(np.sign(D[i0]))
        if sgn == 0:
            continue
        div = (np.sign(pm) != sgn)
        for tag, ref in [('vwap', W[i0]), ('next_open', nxt[i0])]:
            oc = race(ref, H, L, i0, ref + BARRIER, ref - BARRIER, n)
            if oc is None:
                continue
            rev = 1 if ((oc == 'up' and not up) or (oc == 'down' and up)) else 0
            out.append(dict(tag=tag, div=div, rev=rev, event_date=str(d)))

r = pd.DataFrame(out)
print("\n=== reversal rate by trigger reference and divergence ===")
print(f"{'reference':>10} | {'group':>14} | {'n':>6} | {'reversal':>9}")
for tag in ['vwap', 'next_open']:
    for dv, lab in [(True, 'divergence'), (False, 'with trend')]:
        s = r[(r.tag == tag) & (r.div == dv)]
        print(f"{tag:>10} | {lab:>14} | {len(s):>6} | {s.rev.mean():>9.2%}")
    a = r[(r.tag == tag) & r.div].rev.mean()
    bb = r[(r.tag == tag) & ~r.div].rev.mean()
    print(f"{tag:>10} | {'DIFFERENCE':>14} | {'':>6} | {a-bb:>+9.2%}")
print("\nIf the effect collapses under 'next_open', the VWAP trigger was biased.")
r.to_csv('results/trigger_bias_check.csv', index=False)
