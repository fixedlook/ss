"""
Verify the rejection-anatomy findings before believing them.

Three things need checking:

 A. The terminal-extreme null was WRONG. I required a random bar to contain
    both the day high and the day low, which is impossible. The right null is
    "does a random bar's high equal the day's high".

 B. The time-of-day concentration (61% in the first hour) could be nothing more
    than "the day's high and low usually form early". Need the baseline: when
    DOES the day's extreme form?

 C. The 3x volume on the pivot bar could be selection: big moves need big bars.
    Compare against bars that are also local extremes but not the biggest move.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
ZIG = 0.25
rng = np.random.default_rng(11)

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

e = pd.read_csv('results/rejection_anatomy.csv')
print(f"rejections loaded {len(e):,}")

# ---------------------------------------------------------------- B baseline
print("\n" + "=" * 74)
print("B. WHEN DOES THE DAY'S HIGH / LOW ACTUALLY FORM?  (the baseline)")
print("=" * 74)
hi_min, lo_min = [], []
for d in days:
    b = gday[d]
    hi_min.append(int(b.min_od.values[int(np.argmax(b.high.values))]))
    lo_min.append(int(b.min_od.values[int(np.argmin(b.low.values))]))
hi_min = np.array(hi_min)
lo_min = np.array(lo_min)
print(f"  {'bucket':>14} | {'day HIGH forms':>14} | {'day LOW forms':>14} | {'rejection is here':>18}")
for a in range(0, 390, 30):
    b_ = a + 30
    lab = f"{9 + (a+30)//60:02d}:{(30+a) % 60:02d}"
    hh = ((hi_min >= a) & (hi_min < b_)).mean()
    ll = ((lo_min >= a) & (lo_min < b_)).mean()
    rej = ((e.minute_of_day >= a) & (e.minute_of_day < b_)).mean()
    print(f"  {lab:>14} | {hh:>13.1%} | {ll:>13.1%} | {rej:>17.1%}")

early = ((hi_min < 60) | (lo_min < 60)).mean()
print(f"\n  day high OR low inside the first hour: {early:.1%}")
print(f"  biggest rejection inside the first hour: {(e.minute_of_day < 60).mean():.1%}")
print("  -> if these are similar, the time concentration is just 'extremes form early'")

# ------------------------------------------------------------- A fixed null
print("\n" + "=" * 74)
print("A. TERMINAL EXTREME - correct null")
print("=" * 74)
null_hit_hi, null_hit_lo, n_bars = 0, 0, 0
for d in days:
    b = gday[d]
    H, Lo = b.high.values, b.low.values
    void = (H >= H.max() - 0.25)
    lo_oid = (Lo <= Lo.min() + 0.25)
    null_hit_hi += void.sum()
    null_hit_lo += lo_oid.sum()
    n_bars += len(b)
print(f"  a RANDOM bar is the day's high: {null_hit_hi/n_bars:.1%}")
print(f"  a RANDOM bar is the day's low : {null_hit_lo/n_bars:.1%}")
print(f"  a random bar is either        : {(null_hit_hi+null_hit_lo)/n_bars:.1%}")
print(f"  the biggest rejection is either: {e.terminal.mean():.1%}")
print(f"  lift                           : {e.terminal.mean()/((null_hit_hi+null_hit_lo)/n_bars):.1f}x")

# ------------------------------------------------------- C volume selection
print("\n" + "=" * 74)
print("C. IS THE 3x VOLUME JUST SELECTION?")
print("=" * 74)
print("  comparing the pivot bar against OTHER local extremes in the same session")
rows = []
for d in days:
    b = gday[d]
    H, Lo, V = b.high.values, b.low.values, b.volume.values
    n = len(b)
    medv = np.median(V)
    if medv == 0:
        continue
    # every local extreme bar (high lower than both neighbours, etc)
    ext = np.zeros(n, bool)
    ext[1:-1] = ((H[1:-1] > H[:-2]) & (H[1:-1] > H[2:])) | \
                ((Lo[1:-1] < Lo[:-2]) & (Lo[1:-1] < Lo[2:]))
    e_d = e[e.date == str(d)]
    if len(e_d) == 0:
        continue
    i = int(e_d.i.iloc[0])
    rows.append(dict(pick_vol=V[i] / medv,
                     max_ext_vol=np.max(V[ext] / medv) if ext.sum() else np.nan,
                     mean_ext_vol=np.mean(V[ext] / medv) if ext.sum() else np.nan,
                     median_ext_vol=np.median(V[ext] / medv) if ext.sum() else np.nan,
                     pctile=V[i] / medv,
                     rank_of_ext=(V[ext] < V[i]).mean() if ext.sum() else np.nan))
r = pd.DataFrame(rows)
print(f"  median volume (x day median) of:")
print(f"    the biggest-rejection bar      : {r.pick_vol.median():.2f}x")
print(f"    the MEAN local extreme         : {r.mean_ext_vol.median():.2f}x")
print(f"    the MEDIAN local extreme       : {r.median_ext_vol.median():.2f}x")
print(f"    the LOUDEST local extreme      : {r.max_ext_vol.median():.2f}x")
print(f"\n  the rejection bar's volume rank among the day's extremes: "
      f"{r.rank_of_ext.median():.2f} (0=quietest, 1=loudest)")

# --------------------------------------------- D terminal vs intermediate
print("\n" + "=" * 74)
print("D. TERMINAL vs INTERMEDIATE - the split that matters")
print("=" * 74)
for lab, sub in [('TERMINAL (the day high/low)', e[e.terminal]),
                 ('INTERMEDIATE (day moved away)', e[~e.terminal])]:
    print(f"\n  {lab}   n={len(sub):,}  ({len(sub)/len(e):.0%})")
    print(f"    median time         : {sub.minute_of_day.median():.0f} min after open "
          f"({9+(30+int(sub.minute_of_day.median()))//60:02d}:"
          f"{(30+int(sub.minute_of_day.median()))%60:02d} ET)")
    print(f"    in first hour       : {(sub.minute_of_day < 60).mean():.1%}")
    print(f"    median vol x        : {sub.vol_rel.median():.2f}x")
    print(f"    median approach spd : {sub.prior_speed.median():.4f}")
    print(f"    median |p - VWAP|   : {sub.dist_vwap_frac.median():.1%} of range")
    print(f"    median departing mv : {sub.move_frac.median():.1%} of range")

e.to_csv('results/rejection_anatomy.csv', index=False)
print("\nsaved")
