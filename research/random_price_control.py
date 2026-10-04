"""
Is delta divergence a zone effect, or does it work everywhere?

If divergence predicts reversals at random prices as well as it does at LVN
zones, then the zones are decoration and the whole signal is just "divergence".

Control : random minutes, NEVER classified by zone membership.
          Same trigger rule (midpoint = the minute's own VWAP),
          same barrier (a fixed distance), same reversal definition.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
HORIZON = 60
BARRIER = 5.25            # same as the mean zone half-height + buffer used before
rng = np.random.default_rng(99)
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
mb['rng'] = mb.hi - mb.lo
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)
days = sorted(mb.date.unique())
print(f"sessions {len(days)}")

rec = []
for d in days:
    b = mb[mb.date == d].reset_index(drop=True)
    W, H, L = b.vwap.values, b.hi.values, b.lo.values
    D, V, R = b.delta.values, b.vol.values, b.rng.values
    n = len(b)
    med_v, med_r = np.median(V), np.median(R)
    med_ad = np.median(np.abs(D))
    for i0 in rng.choice(np.arange(3, n - 2), size=min(N_PER_SESSION, n - 5), replace=False):
        i0 = int(i0)
        pm = W[i0 - 1] - W[max(0, i0 - 11)]
        if pm == 0:
            continue
        up = pm > 0
        up_bar, dn_bar = W[i0] + BARRIER, W[i0] - BARRIER
        outcome = None
        for k in range(i0 + 1, min(n, i0 + HORIZON)):
            hu, hd = H[k] >= up_bar, L[k] <= dn_bar
            if hu and hd:
                continue
            outcome = 'up' if hu else ('down' if hd else None)
            if outcome:
                break
        if outcome is None:
            continue
        rev = 1 if ((outcome == 'up' and not up) or (outcome == 'down' and up)) else 0
        sgn = int(np.sign(D[i0]))
        rec.append(dict(
            event_date=str(d), rev=rev,
            vol_ratio=V[i0] / med_v, rng_ratio=R[i0] / med_r,
            absdelta_ratio=abs(D[i0]) / med_ad,
            two_way=bool(b.bid.values[i0] > 0.35 * V[i0] and b.ask.values[i0] > 0.35 * V[i0]),
            with_trend=bool(np.sign(pm) == sgn and sgn != 0),
            against_trend=bool(np.sign(pm) != sgn and sgn != 0),
        ))

c = pd.DataFrame(rec)
print(f"control events {len(c):,}   base reversal {c.rev.mean():.2%}")

CONCEPTS = {
    'delta_divergence': c.against_trend,
    'delta_with_trend': c.with_trend,
    'volume_climax': c.vol_ratio > 2.0,
    'big_range': c.rng_ratio > 1.5,
    'low_abs_delta': c.absdelta_ratio < 0.5,
    'two_way_tape': c.two_way,
}
print("\n=== RANDOM PRICES, no zone filter at all ===")
print(f"{'concept':>18} | {'n':>6} | {'with':>7} | {'without':>7} | {'diff':>8}")
for name, m in CONCEPTS.items():
    m = m.values.astype(bool)
    if m.sum() < 25 or (~m).sum() < 25:
        print(f"{name:>18} | {int(m.sum()):>6} | too few")
        continue
    w_, wo = c.rev.values[m].mean(), c.rev.values[~m].mean()
    print(f"{name:>18} | {int(m.sum()):>6} | {w_:>7.2%} | {wo:>7.2%} | {w_-wo:>+8.2%}")

print("\n=== compare with the LVN zone result ===")
z = pd.read_csv('results/lvn_neverretire_events.csv')
zm = z.against_trend.values.astype(bool)
print(f"  at LVN zones    : {z.rev.values[zm].mean():.2%} vs {z.rev.values[~zm].mean():.2%} "
      f"= {z.rev.values[zm].mean()-z.rev.values[~zm].mean():+.2%}  (n={int(zm.sum())})")
cm = c.against_trend.values.astype(bool)
print(f"  at random prices: {c.rev.values[cm].mean():.2%} vs {c.rev.values[~cm].mean():.2%} "
      f"= {c.rev.values[cm].mean()-c.rev.values[~cm].mean():+.2%}  (n={int(cm.sum())})")
c.to_csv('results/random_price_control.csv', index=False)
print("\nwrote results/random_price_control.csv")
