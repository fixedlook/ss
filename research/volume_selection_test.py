"""
Is the 93.5th-percentile volume at a valid reversal real, or selection?

The worry: a swing extreme (the highest high of a leg) is more likely to land on
a bar with a WIDE RANGE, and wide-range bars tend to have high volume. So the
extreme bar being heavy could be purely mechanical, not a reversal signal.

Decisive test - condition on being AT a swing extreme, then ask whether volume
predicts the size of the reversal that follows:

  for every zigzag pivot in every session:
      volume percentile of that bar, within its own minute of day
      size of the move that departs from that pivot
  then: is a HIGH-volume pivot followed by a BIGGER move than a LOW-volume pivot?

If yes -> volume carries information about the reversal.
If no  -> the 93.5th percentile is the selection artifact, and volume at the
          turning bar tells you nothing you could use in advance.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
THR = 100.0
rng = np.random.default_rng(23)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['min_od'] = bars.dt.dt.hour * 60 + bars.dt.dt.minute - 570
bars['date'] = bars.dt.dt.date
bars['rng'] = bars.high - bars.low
bars = bars.sort_values(['date', 'min_od']).reset_index(drop=True)
gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}
days = [d for d in sorted(gday) if len(gday[d]) >= 200]
print(f"sessions {len(days):,}")

byclock = {m: g.volume.values for m, g in bars.groupby('min_od')}
byclock_r = {m: g.rng.values for m, g in bars.groupby('min_od')}


def zigzag(H, Lo, thr):
    piv = []
    dirn = 0
    ext_i, ext_p = 0, H[0]
    rh_i, rh_p = 0, H[0]
    rl_i, rl_p = 0, Lo[0]
    for i in range(len(H)):
        if dirn == 0:
            if H[i] >= rl_p + thr and rl_i < i:
                dirn = 1
                piv.append((rl_i, rl_p, 'L'))
                ext_i, ext_p = i, H[i]
                rh_i, rh_p = i, H[i]
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1
                piv.append((rh_i, rh_p, 'H'))
                ext_i, ext_p = i, Lo[i]
                rl_i, rl_p = i, Lo[i]
            else:
                if H[i] > rh_p:
                    rh_p, rh_i = H[i], i
                if Lo[i] < rl_p:
                    rl_p, rl_i = Lo[i], i
        elif dirn == 1:
            if H[i] > ext_p:
                ext_i, ext_p = i, H[i]
            elif Lo[i] <= ext_p - thr:
                piv.append((ext_i, ext_p, 'H'))
                dirn = -1
                ext_i, ext_p = i, Lo[i]
        else:
            if Lo[i] < ext_p:
                ext_i, ext_p = i, Lo[i]
            elif H[i] >= ext_p + thr:
                piv.append((ext_i, ext_p, 'L'))
                dirn = 1
                ext_i, ext_p = i, H[i]
    return piv


recs = []
for d in days:
    b = gday[d]
    H, Lo, V, RG = b.high.values, b.low.values, b.volume.values, b.rng.values
    piv = zigzag(H, Lo, THR)
    if len(piv) < 3:
        continue
    for k in range(len(piv) - 1):
        pi, pp, kind = piv[k]
        mod = int(b.min_od.values[pi])
        if mod not in byclock or len(byclock[mod]) < 50:
            continue
        vp = (byclock[mod] < V[pi]).mean()      # percentile of volume, same clock
        rp = (byclock_r[mod] < RG[pi]).mean()   # percentile of range, same clock
        move = abs(piv[k + 1][1] - pp)
        recs.append(dict(date=str(d), mod=mod, kind=kind, pivot_vol_pct=vp,
                         pivot_rng_pct=rp, move=move,
                         valid=int(move >= THR)))

r = pd.DataFrame(recs)
print(f"\nzigzag pivots examined: {len(r):,}")
print(f"  of which a >=100pt move departs: {r.valid.mean():.1%} ({int(r.valid.sum()):,})")
print(f"  median volume percentile of ALL pivots: {r.pivot_vol_pct.median():.1%}")
print(f"  a random bar would be 50.0%")

print("\n" + "=" * 74)
print("TEST: does volume at the pivot predict a big move away?")
print("=" * 74)
r['vbin'] = pd.cut(r.pivot_vol_pct, [0, .25, .5, .75, .9, 1.0],
                   labels=['0-25', '25-50', '50-75', '75-90', '90-100'])
print(f"{'volume pctile':>14} | {'n':>7} | {'median move':>12} | {'P(move>=100)':>13}")
for b_, s in r.groupby('vbin', observed=True):
    print(f"{str(b_):>14} | {len(s):>7} | {s.move.median():>11.0f}p | {s.valid.mean():>12.1%}")

print("\n=== but RANGE also matters - the two are confounded ===")
r['rbin'] = pd.cut(r.pivot_rng_pct, [0, .5, .75, .9, 1.0],
                   labels=['0-50', '50-75', '75-90', '90-100'])
print(f"{'range pctile':>14} | {'n':>7} | {'median move':>12} | {'P(move>=100)':>13}")
for b_, s in r.groupby('rbin', observed=True):
    print(f"{str(b_):>14} | {len(s):>7} | {s.move.median():>11.0f}p | {s.valid.mean():>12.1%}")

print("\n=== volume effect WITHIN range buckets (does volume still matter?) ===")
print(f"{'range':>8} | " + " | ".join(f"vol {c:>7}" for c in ['0-25', '25-50', '50-75', '75-90', '90-100']))
for rb, s in r.groupby('rbin', observed=True):
    cells = []
    for vb in ['0-25', '25-50', '50-75', '75-90', '90-100']:
        t = s[s.vbin == vb]
        cells.append(f"{t.valid.mean():>10.1%}" if len(t) >= 40 else f"{'--':>10}")
    print(f"{str(rb):>8} | " + " | ".join(cells))

print("\n=== the reversal bar's volume vs OTHER pivots (like for like) ===")
print(f"  pivot that DID produce >=100pt : {r[r.valid==1].pivot_vol_pct.median():.1%}")
print(f"  pivot that did NOT             : {r[r.valid==0].pivot_vol_pct.median():.1%}")
print(f"  difference                     : "
      f"{(r[r.valid==1].pivot_vol_pct.median()-r[r.valid==0].pivot_vol_pct.median())*100:+.1f}pp")

r.to_csv('results/volume_selection.csv', index=False)
print("\nwrote results/volume_selection.csv")
