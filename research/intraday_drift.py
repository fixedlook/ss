"""
Where can an edge even live?  Optional stopping: for a +/-barrier structure with a
1 pt round turn, E[P&L] = -cost for EVERY entry/exit rule unless the conditional
drift E[r | information] != 0.  Barrier geometry, exit rules and volatility
(magnitude) prediction cannot change the expectation - only the variance.
So every remaining hope is a genuine conditional DRIFT.  Six angles came back
null.  One channel was never examined: MINUTE-OF-DAY drift - does price have a
systematic tendency at particular clock times?

Two reasons it matters:
  1. it is the last untested drift channel
  2. our two 'best' variants (V2/V5b) earned their whole gain on trades still
     unresolved at 16:59 - if the close carries a one-way drift, that 'gain' is a
     time-of-day artefact, not a signal.

Alignment is by CLOCK TIME, not row order: zero-volume minutes are dropped from
the export, so a positional pivot silently mixes 11:00 prices into 09:40 slots.
Minute slots are built per session, forward-filled, and diffed.

Run:  /tmp/venv/bin/python research/intraday_drift.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
df = df[df.flow > 0]
df['m'] = df['ny'].dt.hour * 60 + df['ny'].dt.minute - 570      # 09:30 -> 0
df = df[(df.m >= 0) & (df.m <= 449)]

NS = 450
rows, dates, bad = [], [], 0
for d, g in df.groupby('dstr'):
    g = g.drop_duplicates('m').sort_values('m')
    if g.m.iloc[0] != 0 or g.m.iloc[-1] != 449:                  # need 09:30 and 16:59 bars
        bad += 1
        continue
    px = np.full(NS, np.nan)
    px[g.m.values] = g.close.values
    px = pd.Series(px).ffill().values                            # zero-volume minutes = flat
    rows.append(px)
    dates.append(d)

PX = np.array(rows)
R = np.full_like(PX, np.nan)
R[:, 1:] = np.diff(PX, axis=1)                                   # return realized in slot m
dates = np.array(dates)
n = len(dates)
print(f"sessions with a 09:30 open AND 16:59 close: {n:,}   (dropped {bad} partial/half days)")
print(f"price path complete in every slot for {int(np.isfinite(R[:,1:]).all(axis=1).sum()):,} of them\n")

print("=" * 104)
print("A) MEAN 1-MIN RETURN BY CLOCK TIME - 15 half-hour blocks  (pts)")
print("=" * 104)
print(f"{'block':>13} | {'mean/min':>9} {'t':>6} | {'30-min sum':>11} {'t':>6} | {'median 30m':>11} | {'% up':>6} | {'trim 30m':>9}")
for b in range(15):
    X = R[:, b * 30:(b + 1) * 30]
    s = np.nansum(X, axis=1)
    s = s[np.isfinite(X).sum(axis=1) >= 25]
    m = X[np.isfinite(X)]
    tb = m.mean() / (m.std(ddof=1) / np.sqrt(len(m)))
    ts = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    h0, m0 = divmod(570 + b * 30, 60)
    h1, m1 = divmod(570 + (b + 1) * 30, 60)
    lo, hi = np.percentile(s, [2.5, 97.5])
    print(f"{h0:02d}:{m0:02d}-{h1:02d}:{m1:02d} | {m.mean():>+9.3f} {tb:>+6.1f} | {s.mean():>+11.2f} "
          f"{ts:>+6.1f} | {np.median(s):>+11.2f} | {100*np.mean(s>0):>5.1f}% | "
          f"{s[(s>lo)&(s<hi)].mean():>+9.2f}")
allday = np.nansum(R[:, 1:], axis=1)
print(f"\nfull RTH day 09:30-16:59: mean {allday.mean():+.2f} pts "
      f"(t {allday.mean()/(allday.std(ddof=1)/np.sqrt(n)):+.1f}), "
      f"median {np.median(allday):+.2f}, {100*np.mean(allday>0):.1f}% of days up")
print("  (15 blocks tested -> a real clock-time effect needs |t| > ~3.5 to survive multiplicity)")

print("\n" + "=" * 104)
print("B) THE CLOSING HOUR - would the 16:59 mark carry a free tailwind?")
print("=" * 104)
for lab, a, b in [("last 60 min 15:00-16:59", 360, 450), ("last 30 min 15:30-16:59", 420, 450),
                  ("last 15 min 15:45-16:59", 435, 450), ("first 30 min 09:30-09:59", 0, 30),
                  ("first 60 min 09:30-10:29", 0, 60)]:
    s = np.nansum(R[:, a:b], axis=1)
    s = s[np.isfinite(R[:, a:b]).sum(axis=1) >= (b - a) - 5]
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    print(f"  {lab:<26} mean {s.mean():>+7.2f} pts   median {np.median(s):>+7.2f}   "
          f"t {t:>+5.1f}   {100*np.mean(s>0):>5.1f}% up   n {len(s)}")

print("\n" + "=" * 104)
print("C) FADE OR CONTINUATION INTO THE CLOSE?  (move to 15:30 vs last 30 min)")
print("=" * 104)
ok = np.isfinite(R).sum(axis=1) >= 440
prior = np.nansum(R[ok, 1:420], axis=1)
last = np.nansum(R[ok, 420:450], axis=1)
q = np.quantile(prior, [0, .1, .33, .67, .9, 1.0])
print(f"{'prior move to 15:30':>24} | {'n':>5} | {'last 30 min':>12} {'t':>6} | {'% up':>6}")
for i in range(5):
    hi_ok = prior <= q[i + 1] if i == 4 else prior < q[i + 1]
    m = (prior >= q[i]) & hi_ok
    if m.sum() < 30:
        continue
    s = last[m]
    t = s.mean() / (s.std(ddof=1) / np.sqrt(m.sum()))
    print(f"{q[i]:>+11.1f} to {q[i+1]:>+9.1f} | {m.sum():>5} | {s.mean():>+12.2f} {t:>+6.1f} | "
          f"{100*np.mean(s>0):>5.1f}%")
print(f"\n  corr(prior move, last 30 min) = {np.corrcoef(prior, last)[0,1]:+.3f}   "
      f"slope {np.polyfit(prior, last, 1)[0]:+.4f} pts per pt")

print("\n" + "=" * 104)
print("D) ERA STABILITY across the data hole (2024-10 -> 2025-11)")
print("=" * 104)
era = np.where(dates[ok] < '2024-10-01', 'A 2019-07..2024-09', 'B 2025-11..2026-09')
for e in np.unique(era):
    m = era == e
    s60 = np.nansum(R[ok][m, 360:450], axis=1)
    s30 = np.nansum(R[ok][m, 420:450], axis=1)
    t60 = s60.mean() / (s60.std(ddof=1) / np.sqrt(m.sum()))
    t30 = s30.mean() / (s30.std(ddof=1) / np.sqrt(m.sum()))
    print(f"  {e:<22} n {m.sum():>4} | last 60 min {s60.mean():>+7.2f} pts (t {t60:>+5.1f}) | "
          f"last 30 min {s30.mean():>+7.2f} pts (t {t30:>+5.1f})")
print("""
READING IT
  A block with |t| above ~3.5 (15 blocks tested) AND the same sign in both eras is
  a real clock-time drift.  Anything below that is noise, and then the '16:59 mark'
  channel is not a free option - it is where unresolved trades take their last
  random draw, which is exactly what the V2/V5b gains looked like.
""")
