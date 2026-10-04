"""
Valid reversals, defined the way the user defines them.

USER DEFINITION:
  market was bearish, then it turned bullish and went up ~100 pts
  100 points is the MINIMUM for the reversal to be valid

So a reversal = a point where price reverses direction AND both the incoming leg
and the outgoing leg are at least 100 points. Absolute threshold, not a fraction
of the day's range. A V, not a wiggle.

This is NOT what the earlier study measured (25% of range, single biggest per day,
which biased toward the day's outright extremes).

This script finds every such reversal and asks what they have in common.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
MIN_LEG = 100.0
rng = np.random.default_rng(17)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['min_od'] = bars.dt.dt.hour * 60 + bars.dt.dt.minute - 570
bars['date'] = bars.dt.dt.date
bars = bars.sort_values(['date', 'min_od']).reset_index(drop=True)
gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}
days = [d for d in sorted(gday) if len(gday[d]) >= 200]
print(f"sessions {len(days):,}")
print(f"minimum leg length: {MIN_LEG:.0f} points (absolute, per the user)")

# minute-of-day volume baseline, for time-controlled comparison
vol_clock = bars.groupby('min_od').volume.median()

piv_vol = bars.groupby('min_od').volume.median()


def zigzag_abs(H, Lo, thr):
    """alternating pivots, each leg at least `thr` points"""
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


rows = []
for d in days:
    b = gday[d]
    H, Lo, V = b.high.values, b.low.values, b.volume.values
    C = b.close.values
    n = len(b)
    rng_ = H.max() - Lo.min()
    piv = zigzag_abs(H, Lo, MIN_LEG)
    if len(piv) < 2:
        continue
    day_hi, day_lo = H.max(), Lo.min()
    for k in range(1, len(piv) - 1):
        pi, pp, kind = piv[k]
        in_leg = abs(piv[k][1] - piv[k - 1][1])
        out_leg = abs(piv[k + 1][1] - piv[k][1])
        if in_leg < MIN_LEG or out_leg < MIN_LEG:
            continue
        mod = int(b.min_od.values[pi])
        base = piv_vol.get(mod, np.nan)
        terminal = (kind == 'H' and pp >= day_hi - 0.25) or \
                   (kind == 'L' and pp <= day_lo + 0.25)
        prev_i = piv[k - 1][0]
        bars_in = max(1, pi - prev_i)
        rows.append(dict(
            date=str(d), i=pi, kind=kind,          # H = topped out then fell, L = bottomed then rose
            in_leg=in_leg, out_leg=out_leg,
            in_leg_pts=in_leg, out_leg_pts=out_leg,
            minute_of_day=mod,
            pos_in_range=(pp - day_lo) / rng_ if rng_ > 0 else np.nan,
            terminal=bool(terminal),
            vol=float(V[pi]),
            vol_ratio_clock=float(V[pi] / base) if base and not np.isnan(base) else np.nan,
            bars_in_leg=bars_in,
            pts_per_min_in=in_leg / bars_in,
            day_range=rng_,
            n_pivots=len(piv),
        ))

e = pd.DataFrame(rows)
print(f"\nVALID REVERSALS FOUND: {len(e):,} across {e.date.nunique():,} sessions")
print(f"  per session: {len(e)/e.date.nunique():.2f}")

if len(e) == 0:
    raise SystemExit("none found - check threshold")

print(f"\n=== shape of the found reversals ===")
print(f"  median incoming leg : {e.in_leg.median():.0f} pts")
print(f"  median outgoing leg : {e.out_leg.median():.0f} pts")
print(f"  top-out (H) vs bottom-out (L): {np.mean(e.kind=='H'):.1%} / {np.mean(e.kind=='L'):.1%}")

print("\n" + "=" * 74)
print("1. ARE THESE THE DAY'S EXTREMES? (i.e. unknowable in advance)")
print("=" * 74)
print(f"  the reversal IS the session high or low : {e.terminal.mean():.1%}")
print(f"  intermediate (tradeable in principle)   : {(~e.terminal).mean():.1%}")

print("\n" + "=" * 74)
print("2. WHEN IN THE DAY")
print("=" * 74)
h = 9 + (e.minute_of_day // 60)
for hh in range(9, 17):
    s = (h == hh).mean()
    print(f"  {hh:02d}:00-{hh+1:02d}:00 ET  {s:>6.1%}  {'#' * int(s * 120)}")

print("\n" + "=" * 74)
print("3. POSITION IN THE DAY'S RANGE at the reversal point")
print("=" * 74)
for lo_, hi_, lab in [(0, .2, 'bottom fifth'), (.2, .4, 'lower mid'), (.4, .6, 'middle'),
                      (.6, .8, 'upper mid'), (.8, 1.01, 'top fifth')]:
    s = ((e.pos_in_range >= lo_) & (e.pos_in_range < hi_)).mean()
    print(f"  {lab:>12}: {s:>6.1%}  {'#' * int(s * 100)}")

print("\n" + "=" * 74)
print("4. VOLUME AT THE TURNING BAR (time-controlled)")
print("=" * 74)
print(f"  median volume ratio vs same minute-of-day: {e.vol_ratio_clock.median():.2f}x")
print(f"  percentile within its own minute-of-day  : "
      f"{(e.vol_ratio_clock < e.vol_ratio_clock.median()).mean():.0%} (median)")
for lab, s in [('terminal reversals', e[e.terminal]),
               ('intermediate reversals', e[~e.terminal])]:
    print(f"  {lab:>24}: {s.vol_ratio_clock.median():.2f}x  (n={len(s)})")

print("\n" + "=" * 74)
print("5. SPEED - how fast did price arrive at the turn?")
print("=" * 74)
print(f"  median incoming leg     : {e.in_leg.median():.0f} pts")
print(f"  median bars in that leg : {e.bars_in_leg.median():.0f} min")
print(f"  median speed            : {e.pts_per_min_in.median():.2f} pts/min")
fast = e[e.pts_per_min_in > e.pts_per_min_in.median()]
slow = e[e.pts_per_min_in <= e.pts_per_min_in.median()]
print(f"  fast arrivals (n={len(fast)}): outgoing leg median {fast.out_leg.median():.0f} pts, "
      f"terminal {fast.terminal.mean():.1%}")
print(f"  slow arrivals (n={len(slow)}): outgoing leg median {slow.out_leg.median():.0f} pts, "
      f"terminal {slow.terminal.mean():.1%}")

e.to_csv('results/valid_reversals.csv', index=False)
print(f"\nwrote results/valid_reversals.csv  ({len(e):,} reversals)")
