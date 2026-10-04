"""
FIXED test: does volume at a swing point predict the SIZE of the reversal?

Previous attempt used a 100-point zigzag, so every pivot had a >=100pt move by
construction and the test could not discriminate.

This uses a FINE zigzag (25 points) to find all swing points, then measures how
far price actually travels away from each. Now the departure size varies freely
and volume can be tested against it.

Also answers the actionable version:
  when a bar spikes in volume, does price reverse >=100pts or continue?
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
FINE = 25.0
BIG = 100.0
rng = np.random.default_rng(29)

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
                ext_i, ext_p = i, H[i]; rh_i, rh_p = i, H[i]
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1
                piv.append((rh_i, rh_p, 'H'))
                ext_i, ext_p = i, Lo[i]; rl_i, rl_p = i, Lo[i]
            else:
                if H[i] > rh_p: rh_p, rh_i = H[i], i
                if Lo[i] < rl_p: rl_p, rl_i = Lo[i], i
        elif dirn == 1:
            if H[i] > ext_p: ext_i, ext_p = i, H[i]
            elif Lo[i] <= ext_p - thr:
                piv.append((ext_i, ext_p, 'H')); dirn = -1
                ext_i, ext_p = i, Lo[i]
        else:
            if Lo[i] < ext_p: ext_i, ext_p = i, Lo[i]
            elif H[i] >= ext_p + thr:
                piv.append((ext_i, ext_p, 'L')); dirn = 1
                ext_i, ext_p = i, H[i]
    return piv


recs = []
for d in days:
    b = gday[d]
    H, Lo, V, RG, C = b.high.values, b.low.values, b.volume.values, b.rng.values, b.close.values
    n = len(b)
    piv = zigzag(H, Lo, FINE)
    if len(piv) < 4:
        continue
    for k in range(len(piv) - 1):
        pi, pp, kind = piv[k]
        mod = int(b.min_od.values[pi])
        if mod not in byclock or len(byclock[mod]) < 50:
            continue
        vp = (byclock[mod] < V[pi]).mean()
        move = abs(piv[k + 1][1] - pp)
        recs.append(dict(date=str(d), i=pi, kind=kind, vp=vp,
                         rngpct=(np.searchsorted(np.sort(b.rng.values), RG[pi]) / n),
                         move=move, mod=mod,
                         daymed=np.median(V) if np.median(V) else np.nan))
r = pd.DataFrame(recs)
print(f"fine zigzag pivots: {len(r):,}")
print(f"median volume percentile at these pivots: {r.vp.median():.1%}")

print("\n" + "=" * 74)
print("1. does pivot volume predict how far price reverses away?")
print("=" * 74)
r['vbin'] = pd.cut(r.vp, [0, .5, .75, .9, .95, 1.0],
                   labels=['0-50', '50-75', '75-90', '90-95', '95-100'])
print(f"{'vol pctile':>11} | {'n':>7} | {'median move':>11} | {'mean move':>10} | {'>=100pt':>8}")
for b_, s in r.groupby('vbin', observed=True):
    if len(s) < 30:
        continue
    print(f"{str(b_):>11} | {len(s):>7} | {s.move.median():>10.0f}p | "
          f"{s.move.mean():>9.0f}p | {np.mean(s.move>=BIG):>7.1%}")

print("\n=== same, controlling for bar RANGE ===")
r['rbin'] = pd.cut(r.rngpct, [0, .5, .75, .9, 1.0],
                   labels=['0-50', '50-75', '75-90', '90-100'])
print(f"{'range':>8} | " + " | ".join(f"{c:>9}" for c in ['0-50', '50-75', '75-90', '90-95', '95-100']))
for rb, s in r.groupby('rbin', observed=True):
    cells = []
    for vb in ['0-50', '50-75', '75-90', '90-95', '95-100']:
        t = s[s.vbin == vb].move
        cells.append(f"{t.median():>8.0f}p" if len(t) >= 40 else f"{'--':>9}")
    print(f"{str(rb):>8} | " + " | ".join(cells))
print("  (cell = median departing move. flat across columns => volume adds nothing)")

print("\n" + "=" * 74)
print("2. THE ACTIONABLE VERSION")
print("=" * 74)
print("  at a swing point with a big volume spike, P(reversal >=100pt):")
for lo_, hi_, lab in [(0, .5, 'volume below median'), (.5, .9, 'volume 50-90th'),
                      (.9, .95, 'volume 90-95th'), (.95, 1.01, 'volume top 5%')]:
    s = r[(r.vp >= lo_) & (r.vp < hi_)]
    if len(s) < 30:
        continue
    print(f"    {lab:>22}: n={len(s):>6}  P(>=100pt) = {np.mean(s.move>=BIG):.1%}  "
          f"median move {s.move.median():.0f}p")

print("\n" + "=" * 74)
print("3. and does the pivot bar's volume differ from ordinary bars in its own leg?")
print("=" * 74)
sub = r[r.vp > 0.9]
print(f"  share of fine pivots with volume in the top 10% of their minute: {len(sub)/len(r):.1%}")
print(f"  share of ALL bars with volume in the top 10% of their minute   : 10.0%")
print(f"  -> lift {len(sub)/len(r)/0.10:.2f}x")

r.to_csv('results/volume_predicts.csv', index=False)
print("\nwrote results/volume_predicts.csv")
