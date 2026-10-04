"""
Does a volume spike at an LVN box predict a bigger reversal than one elsewhere?

This is the user's actual thesis: "the boxes should only be a help to know when
to trust the big trades and not just use it blindly".

So: at a fine-zigzag swing point with a volume spike in the top 5% of its minute,
does it matter whether that price sits inside an LVN box?

P(reversal >= 100 points) is the outcome, matching the user's own definition of
a valid reversal.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
FINE = 25.0
BIG = 100.0
TOL = 5.0
TICK = 0.25
WIN = int(round(50.0 / TICK / 2)) * 2 + 1
rng = np.random.default_rng(31)

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

# ---- prior-session LVN per session, from bar-estimated profiles ----
prof = {}
for d in days:
    g = gday[d]
    step = TICK
    lo = np.floor(g.low.min() / step) * step
    hi = np.ceil(g.high.max() / step) * step
    grid = np.arange(lo, hi + step, step)
    dens = np.zeros(len(grid))
    li = np.searchsorted(grid, g.low.values, 'left')
    hh = np.searchsorted(grid, g.high.values, 'right')
    for a, b, v in zip(li, hh, g.volume.values):
        if b > a:
            dens[a:b] += v / (b - a)
    prof[d] = pd.Series(dens, index=grid)
print("profiles built")

LV = {}
for i, d in enumerate(days):
    if i < 1:
        LV[d] = None
        continue
    v = prof[days[i - 1]]
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    mask = (v.values <= 0.7 * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, m in zip(v.index.values, mask):
        if m and s is None:
            s = p
        elif not m and s is not None:
            if prev - s >= 4.0:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= 4.0:
        out.append((s, prev))
    LV[d] = out
print("prior-session LVN bands built")


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


byclock = {m: g.volume.values for m, g in bars.groupby('min_od')}
recs = []
for d in days:
    if LV[d] is None:
        continue
    b = gday[d]
    H, Lo, V = b.high.values, b.low.values, b.volume.values
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
        at_lvn = any(a - TOL <= pp <= c + TOL for a, c in LV[d])
        recs.append(dict(date=str(d), vp=vp, move=move, at_lvn=bool(at_lvn)))
r = pd.DataFrame(recs)
print(f"\npivots analysed: {len(r):,}")
print(f"  share at a prior-session LVN: {r.at_lvn.mean():.1%}")

print("\n" + "=" * 74)
print("P(reversal >= 100 pts)")
print("=" * 74)
print(f"{'volume':>18} | {'at LVN':>20} | {'elsewhere':>20}")
for lo_, hi_, lab in [(0, .5, 'below median'), (.5, .9, '50-90th'),
                      (.9, .95, '90-95th'), (.95, 1.01, 'top 5%')]:
    s = r[(r.vp >= lo_) & (r.vp < hi_)]
    a = s[s.at_lvn]
    c = s[~s.at_lvn]
    fa = f"n={len(a):>5} {np.mean(a.move>=BIG):>5.1%}" if len(a) >= 30 else f"{'--':>13}"
    fc = f"n={len(c):>6} {np.mean(c.move>=BIG):>5.1%}" if len(c) >= 30 else f"{'--':>13}"
    print(f"{lab:>18} | {fa:>20} | {fc:>20}")

print("\n" + "=" * 74)
print("median departing move (points)")
print("=" * 74)
print(f"{'volume':>18} | {'at LVN':>12} | {'elsewhere':>12}")
for lo_, hi_, lab in [(0, .5, 'below median'), (.5, .9, '50-90th'),
                      (.9, .95, '90-95th'), (.95, 1.01, 'top 5%')]:
    s = r[(r.vp >= lo_) & (r.vp < hi_)]
    a = s[s.at_lvn].move.median()
    c = s[~s.at_lvn].move.median()
    print(f"{lab:>18} | {a:>11.0f}p | {c:>11.0f}p")

print("\n" + "=" * 74)
print("SUMMARY")
print("=" * 74)
base = np.mean(r.move >= BIG)
top = r[r.vp > .95]
print(f"  all pivots                        : {base:.1%}")
print(f"  top-5% volume pivots              : {np.mean(top.move>=BIG):.1%}")
print(f"  top-5% volume AND at an LVN       : {np.mean(top[top.at_lvn].move>=BIG):.1%}"
      f"  (n={len(top[top.at_lvn])})")
print(f"  top-5% volume, not at an LVN      : {np.mean(top[~top.at_lvn].move>=BIG):.1%}"
      f"  (n={len(top[~top.at_lvn])})")
r.to_csv('results/volume_at_zone.csv', index=False)
print("\nwrote results/volume_at_zone.csv")
