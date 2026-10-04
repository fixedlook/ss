"""
The critical control: is the rejection bar really unusually heavy, or is it just
in the first hour (where all bars are heavy)?

Method: compare each rejection bar's volume against OTHER BARS AT THE SAME
MINUTE OF DAY across all sessions. That removes the intraday volume shape
(the U) entirely.

Also: compare against local extremes at the same minute of day, to separate
"heavy bar" from "heavy bar at a swing point".
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
rng = np.random.default_rng(5)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['min_od'] = bars.dt.dt.hour * 60 + bars.dt.dt.minute - 570
bars['date'] = bars.dt.dt.date
bars = bars.sort_values(['date', 'min_od']).reset_index(drop=True)
bars['range'] = bars.high - bars.low
gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}
days = [d for d in sorted(gday) if len(gday[d]) >= 200]
print(f"sessions {len(days):,}")

e = pd.read_csv('results/rejection_anatomy.csv')
print(f"rejections {len(e):,}")

# ---- build the minute-of-day baseline across ALL sessions ----
print("\nbuilding minute-of-day volume baseline ...")
piv = bars.groupby('min_od').volume.agg(['median', 'mean', 'count'])
rg = bars.groupby('min_od').range.median()
print(f"  minute-of-day buckets: {len(piv)}")

# ---- each rejection vs its own minute-of-day ----
rows = []
for _, r in e.iterrows():
    d = pd.Timestamp(r.date).date()
    b = gday[d]
    i = int(r.i)
    mod = int(r.minute_of_day)
    if mod not in piv.index:
        continue
    V, RG = b.volume.values, b.range.values
    base_v = piv.loc[mod, 'median']
    base_r = rg.loc[mod]
    # what fraction of ALL bars at this minute of day are quieter
    allv = bars[bars.min_od == mod].volume.values
    rows.append(dict(date=str(d), mod=mod, terminal=bool(r.terminal),
                     vol=float(V[i]), vol_base=float(base_v),
                     ratio_to_clock=float(V[i] / base_v) if base_v else np.nan,
                     pctile_within_clock=float((allv < V[i]).mean()),
                     range_base=float(base_r),
                     range_ratio_to_clock=float(RG[i] / base_r) if base_r else np.nan,
                     vol_vs_daymedian=float(r.vol_rel)))
c = pd.DataFrame(rows)
print(f"matched {len(c):,}")

# ---- same baseline for a control: local extremes ----
print("\nbuilding local-extreme control ...")
ext_rows = []
for d in days:
    b = gday[d]
    H, Lo = b.high.values, b.low.values
    n = len(b)
    if n < 5:
        continue
    ext = np.zeros(n, bool)
    ext[1:-1] = ((H[1:-1] > H[:-2]) & (H[1:-1] > H[2:])) | \
                ((Lo[1:-1] < Lo[:-2]) & (Lo[1:-1] < Lo[2:]))
    idx = np.where(ext)[0]
    for j in rng.choice(idx, size=min(5, len(idx)), replace=False):
        j = int(j)
        mod = int(b.min_od.values[j])
        if mod not in piv.index:
            continue
        base_v = piv.loc[mod, 'median']
        if base_v:
            ext_rows.append(dict(mod=mod, ratio_to_clock=float(b.volume.values[j] / base_v)))
x = pd.DataFrame(ext_rows)
print(f"local extremes sampled {len(x):,}")

print("\n" + "=" * 74)
print("VOLUME, CONTROLLED FOR TIME OF DAY")
print("=" * 74)
print(f"{'group':>28} | {'n':>7} | {'ratio to same-minute median':>28}")
print(f"{'biggest rejection (all)':>28} | {len(c):>7} | {c.ratio_to_clock.median():>28.2f}")
print(f"{'  terminal (day high/low)':>28} | {int(c.terminal.sum()):>7} | "
      f"{c[c.terminal].ratio_to_clock.median():>28.2f}")
print(f"{'  intermediate':>28} | {int((~c.terminal).sum()):>7} | "
      f"{c[~c.terminal].ratio_to_clock.median():>28.2f}")
print(f"{'ordinary local extreme':>28} | {len(x):>7} | {x.ratio_to_clock.median():>28.2f}")
print(f"{'any random bar':>28} | {'-':>7} | {1.00:>28.2f}")

print(f"\n  percentile of the rejection bar within its own minute-of-day:")
print(f"    all rejections      : {c.pctile_within_clock.median():.1%}")
print(f"    terminal            : {c[c.terminal].pctile_within_clock.median():.1%}")
print(f"    intermediate        : {c[~c.terminal].pctile_within_clock.median():.1%}")

print("\n" + "=" * 74)
print("RANGE, CONTROLLED FOR TIME OF DAY")
print("=" * 74)
print(f"  rejection bar range / same-minute median range: "
      f"{c.range_ratio_to_clock.median():.2f}x")

print("\n" + "=" * 74)
print("BY HOUR - does it hold outside the open?")
print("=" * 74)
print(f"{'window':>16} | {'n':>6} | {'vol ratio':>10} | {'pctile':>8}")
c['win'] = pd.cut(c['mod'], [-1, 30, 60, 120, 240, 400],
                  labels=['09:30-10:00', '10:00-10:30', '10:30-11:30',
                          '11:30-13:30', '13:30-16:00'])
for w, s in c.groupby('win', observed=True):
    if len(s) < 20:
        continue
    print(f"{str(w):>16} | {len(s):>6} | {s.ratio_to_clock.median():>10.2f} | "
          f"{s.pctile_within_clock.median():>7.1%}")

c.to_csv('results/volume_control.csv', index=False)
print("\nwrote results/volume_control.csv")
