"""
What do the day's biggest rejections have IN COMMON - ignoring external levels?

Everything tested so far asked "was a level there". This asks about the
rejections themselves and their context:

  * WHERE in the day - time of day, position in range
  * IS IT THE TERMINAL EXTREME - is the pivot the day's actual high/low?
    (if so it is not tradeable: you cannot know the high is the high)
  * ITS OWN SHAPE - bar range and volume at the pivot vs the session
  * WHAT PRECEDED IT - approach distance, speed, duration
  * WHERE WAS VWAP - distance from the session's running VWAP

Every measurement carries a null: the same statistic computed on random bars
from the same sessions.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
ZIG = 0.25
rng = np.random.default_rng(7)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['min_od'] = bars.dt.dt.hour * 60 + bars.dt.dt.minute - 570   # minutes since 09:30 ET
bars['date'] = bars.dt.dt.date
bars = bars.sort_values(['date', 'minute']).reset_index(drop=True)
gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}
days = [d for d in sorted(gday) if len(gday[d]) >= 200]
print(f"sessions {len(days):,}")


def zigzag(H, Lo, thr):
    piv = []
    dirn = 0
    ext_i, ext_p = 0, H[0]
    rh_i, rh_p = 0, H[0]
    rl_i, rl_p = 0, Lo[0]
    for i in range(len(H)):
        if dirn == 0:
            if H[i] >= rl_p + thr and rl_i < i:
                dirn = 1; ext_i, ext_p = i, H[i]; rh_i, rh_p = i, H[i]
                piv.append((rl_i, rl_p, 'L'))
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1; ext_i, ext_p = i, Lo[i]; rl_i, rl_p = i, Lo[i]
                piv.append((rh_i, rh_p, 'H'))
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
null_recs = []
for d in days:
    b = gday[d]
    H, Lo, C, V = b.high.values, b.low.values, b.close.values, b.volume.values
    OP, RG = b.open.values, (b.high - b.low).values
    n = len(b)
    rng_ = H.max() - Lo.min()
    if rng_ <= 0:
        continue
    tp = (H + Lo) / 2
    vwap = np.cumsum(tp * V) / np.cumsum(V)
    piv = zigzag(H, Lo, ZIG * rng_)
    if len(piv) < 3:
        continue
    k = max(range(len(piv) - 1), key=lambda j: abs(piv[j + 1][1] - piv[j][1]))
    i, p, kind = piv[k]
    dep = piv[k + 1][1]
    day_hi_i, day_lo_i = int(np.argmax(H)), int(np.argmin(Lo))

    # terminal-extreme test
    at_high = (kind == 'H' and p >= H.max() - 0.25)
    at_low = (kind == 'L' and p <= Lo.min() + 0.25)

    # shape of the pivot bar
    vpct = (V[:i + 1] < V[i]).mean()
    rpct = (RG[:i + 1] < RG[i]).mean()

    # what preceded it
    look = max(0, i - 30)
    prior_move = abs(p - C[look]) if i > look else np.nan
    prior_bars = i - look
    speed = prior_move / max(1, prior_bars)

    recs.append(dict(date=str(d), i=i, kind=kind, price=p,
                     minute_of_day=int(b.min_od.values[i]),
                     pos_in_range=(p - Lo.min()) / rng_,
                     terminal=bool(at_high or at_low),
                     at_high=bool(at_high), at_low=bool(at_low),
                     move=abs(dep - p), move_frac=abs(dep - p) / rng_,
                     vol_pct=vpct, rng_pct=rpct,
                     vol_rel=V[i] / np.median(V) if np.median(V) else np.nan,
                     rng_rel=RG[i] / np.median(RG) if np.median(RG) else np.nan,
                     prior_move=prior_move, prior_move_frac=prior_move / rng_ if prior_move else np.nan,
                     prior_speed=speed / rng_ if not np.isnan(speed) else np.nan,
                     dist_vwap=abs(p - vwap[i]),
                     dist_vwap_frac=abs(p - vwap[i]) / rng_,
                     bars_from_open=i, frac_of_session=i / n))

    # null: 3 random bars per session, same statistics
    for j in rng.choice(np.arange(2, n - 1), size=min(3, n - 2), replace=False):
        j = int(j)
        vj = (V[:j + 1] < V[j]).mean()
        rj = (RG[:j + 1] < RG[j]).mean()
        lk = max(0, j - 30)
        pm = abs(tp[j] - C[lk]) if j > lk else np.nan
        null_recs.append(dict(date=str(d), i=j,
                              minute_of_day=int(b.min_od.values[j]),
                              pos_in_range=(tp[j] - Lo.min()) / rng_,
                              terminal=bool(H[j] >= H.max() - 0.25 and Lo[j] <= Lo.min() + 0.25),
                              vol_pct=vj, rng_pct=rj,
                              vol_rel=V[j] / np.median(V) if np.median(V) else np.nan,
                              rng_rel=RG[j] / np.median(RG) if np.median(RG) else np.nan,
                              prior_speed=(pm / max(1, j - lk)) / rng_ if not np.isnan(pm) else np.nan,
                              dist_vwap_frac=abs(tp[j] - vwap[j]) / rng_,
                              bars_from_open=j, frac_of_session=j / n))

e = pd.DataFrame(recs)
u = pd.DataFrame(null_recs)
print(f"\nbiggest rejections: {len(e):,} over {e.date.nunique():,} sessions")
print(f"null bars          : {len(u):,}")

print("\n" + "=" * 74)
print("1. IS THE DAY'S BIGGEST REJECTION JUST THE DAY'S HIGH OR LOW?")
print("=" * 74)
print(f"  at the session high or low : {e.terminal.mean():.1%}")
print(f"    at the high              : {e.at_high.mean():.1%}")
print(f"    at the low               : {e.at_low.mean():.1%}")
print(f"  random bars                : {u.terminal.mean():.1%}")
print(f"  lift                       : {e.terminal.mean()/u.terminal.mean():.1f}x")
print(f"\n  -> {1-e.terminal.mean():.1%} are INTERMEDIATE pivots the day moved away from")

print("\n" + "=" * 74)
print("2. SHAPE OF THE PIVOT BAR (vs the session and vs null)")
print("=" * 74)
print(f"{'statistic':>26} | {'rejections':>12} | {'null':>12} | {'diff':>8}")
for col, lab in [('vol_pct', 'volume percentile in day'),
                 ('rng_pct', 'range percentile in day'),
                 ('vol_rel', 'volume / day median vol'),
                 ('rng_rel', 'range / day median range')]:
    a, b_ = e[col].median(), u[col].median()
    print(f"{lab:>26} | {a:>12.3f} | {b_:>12.3f} | {a-b_:>+8.3f}")

print("\n" + "=" * 74)
print("3. WHAT PRECEDED IT")
print("=" * 74)
print(f"{'statistic':>26} | {'rejections':>12} | {'null':>12} | {'diff':>8}")
a, b_ = e.prior_speed.median(), u.prior_speed.median()
print(f"{'30-bar approach speed':>26} | {a:>12.4f} | {b_:>12.4f} | {a-b_:>+8.4f}")
a, b_ = e.dist_vwap_frac.median(), u.dist_vwap_frac.median()
print(f"{'distance from VWAP':>26} | {a:>12.4f} | {b_:>12.4f} | {a-b_:>+8.4f}")

print("\n" + "=" * 74)
print("4. WHEN IN THE DAY")
print("=" * 74)
e['hour'] = 9 + (e.minute_of_day // 60)
u['hour'] = 9 + (u.minute_of_day // 60)
for h in range(9, 17):
    a = (e.hour == h).mean()
    b_ = (u.hour == h).mean()
    bar = '#' * int(a * 120)
    print(f"  {h:02d}:00 ET  {a:>6.1%}  null {b_:>5.1%}  lift {a/b_ if b_ else 0:>5.2f}x  {bar}")

print("\n" + "=" * 74)
print("5. POSITION IN THE DAY'S RANGE")
print("=" * 74)
for lo_, hi_, lab in [(0, .2, 'bottom fifth'), (.2, .4, 'lower mid'), (.4, .6, 'middle'),
                      (.6, .8, 'upper mid'), (.8, 1.01, 'top fifth')]:
    a = ((e.pos_in_range >= lo_) & (e.pos_in_range < hi_)).mean()
    b_ = ((u.pos_in_range >= lo_) & (u.pos_in_range < hi_)).mean()
    print(f"  {lab:>12}: {a:>6.1%}   null {b_:>5.1%}   lift {a/b_ if b_ else 0:>5.2f}x")

print("\n" + "=" * 74)
print("6. SPLIT BY TERMINAL vs INTERMEDIATE - are they different animals?")
print("=" * 74)
for lab, sub in [('terminal (day high/low)', e[e.terminal]),
                 ('intermediate', e[~e.terminal])]:
    print(f"\n  {lab}  n={len(sub):,}")
    print(f"    departing move   : median {sub.move_frac.median():>6.1%} of range")
    print(f"    minute of day    : median {sub.minute_of_day.median():>6.0f}")
    print(f"    pos in range     : median {sub.pos_in_range.median():>6.1%}")
    print(f"    vol percentile   : median {sub.vol_pct.median():>6.3f}")
    print(f"    approach speed   : median {sub.prior_speed.median():>6.4f}")

e.to_csv('results/rejection_anatomy.csv', index=False)
u.to_csv('results/rejection_anatomy_null.csv', index=False)
print("\nwrote results/rejection_anatomy.csv + _null.csv")
