"""
Stress-test the three candidates from the case-control study.

  1. INCOMING SPEED - cases arrive at 17.6 pts/min vs 10.7 for controls.
     RISK: this is the volatility confound again. Fast markets produce fast
     legs AND big moves. Must control for the developing range.

  2. TIME OF DAY - 1.39x at the open, 1.34x in the last half hour.
     RISK: none obvious, but check it is not just "more swings early".

  3. RETEST OF AN EARLIER REVERSAL - 1.26x.
     RISK: self-defining levels are partly mechanical (price is near its own
     history). Check against a matched control.

Each is tested on a holdout: 2019-2023 vs 2024-2026.
"""
import numpy as np
import pandas as pd

r = pd.read_csv('results/case_control_pivots.csv')
r['year'] = pd.to_datetime(r.date).dt.year
base = r.case.mean()
print(f"samples {len(r):,}  base rate {base:.2%}")

print("\n" + "=" * 78)
print("1. INCOMING SPEED, CONTROLLED FOR VOLATILITY")
print("=" * 78)
r['speed_bin'] = pd.qcut(r.in_speed, [0, .25, .5, .75, .9, 1.0],
                         labels=['slowest 25%', '25-50%', '50-75%', '75-90%', 'fastest 10%'])
r['rng_bin'] = pd.qcut(r.dev_range, [0, .25, .5, .75, 1.0],
                       labels=['rng Q1', 'Q2', 'Q3', 'Q4'])
print("case rate by incoming speed, WITHIN developing-range quartiles")
print(f"{'range':>8} | " + " | ".join(f"{c:>11}" for c in
      ['slowest 25%', '25-50%', '50-75%', '75-90%', 'fastest 10%']))
for rb, s in r.groupby('rng_bin', observed=True):
    cells = []
    for sb in ['slowest 25%', '25-50%', '50-75%', '75-90%', 'fastest 10%']:
        t = s[s.speed_bin == sb]
        cells.append(f"{t.case.mean():>10.1%}" if len(t) >= 100 else f"{'--':>11}")
    print(f"{str(rb):>8} | " + " | ".join(cells))
print("\n  if the fast columns stay high in EVERY range row, speed is real.")

print("\n" + "=" * 78)
print("2. TIME OF DAY")
print("=" * 78)
r['tod'] = pd.cut(r.min_od, [-1, 30, 60, 120, 240, 330, 390, 1000],
                  labels=['09:30-10:00', '10:00-10:30', '10:30-11:30', '11:30-13:30',
                          '13:30-15:00', '15:00-16:00', '16:00+'])
print(f"{'window':>14} | {'n':>7} | {'case rate':>10} | {'lift':>6} | {'2019-23':>9} | {'2024-26':>9}")
for w, s in r.groupby('tod', observed=True):
    old = s[s.year <= 2023]
    new = s[s.year >= 2024]
    fo = f"{old.case.mean():>8.1%}" if len(old) >= 100 else f"{'--':>9}"
    fn = f"{new.case.mean():>8.1%}" if len(new) >= 100 else f"{'--':>9}"
    print(f"{str(w):>14} | {len(s):>7} | {s.case.mean():>9.1%} | "
          f"{s.case.mean()/base:>5.2f}x | {fo} | {fn}")

print("\n" + "=" * 78)
print("3. RETEST OF AN EARLIER REVERSAL TODAY")
print("=" * 78)
print(f"  overall: retest {r[r.retest_self==1].case.mean():.1%} "
      f"(n={int((r.retest_self==1).sum())}) vs not "
      f"{r[r.retest_self==0].case.mean():.1%} "
      f"(n={int((r.retest_self==0).sum())})")
print(f"  lift {(r[r.retest_self==1].case.mean()/r[r.retest_self==0].case.mean()):.2f}x")
print(f"\n  {'split':>16} | {'retest':>18} | {'no retest':>18} | {'lift':>6}")
for lab, s in [('2019-2023', r[r.year <= 2023]), ('2024-2026', r[r.year >= 2024])]:
    y = s[s.retest_self == 1]
    n_ = s[s.retest_self == 0]
    print(f"  {lab:>16} | {y.case.mean():>9.1%} n={len(y):>5} | "
          f"{n_.case.mean():>9.1%} n={len(n_):>5} | {y.case.mean()/n_.case.mean():>5.2f}x")
print("\n  by time of day (is it just 'later in the day'?)")
for w, s in r.groupby('tod', observed=True):
    y = s[s.retest_self == 1]
    n_ = s[s.retest_self == 0]
    if len(y) < 80:
        continue
    print(f"  {str(w):>14} | {y.case.mean():>9.1%} n={len(y):>5} | "
          f"{n_.case.mean():>9.1%} | {y.case.mean()/n_.case.mean():>5.2f}x")

print("\n" + "=" * 78)
print("COMBINED: does stacking them help?")
print("=" * 78)
r['fast'] = r.in_speed > r.in_speed.quantile(.75)
r['open'] = r.min_od <= 60
combos = [('neither', ~r.fast & ~r.open & (r.retest_self == 0)),
          ('fast only', r.fast & ~r.open & (r.retest_self == 0)),
          ('open only', ~r.fast & r.open & (r.retest_self == 0)),
          ('fast+open', r.fast & r.open & (r.retest_self == 0)),
          ('fast+retest', r.fast & (r.retest_self == 1)),
          ('fast+open+retest', r.fast & r.open & (r.retest_self == 1))]
print(f"{'combination':>20} | {'n':>6} | {'case rate':>10} | {'lift':>6}")
for lab, m in combos:
    s = r[m]
    if len(s) < 50:
        print(f"{lab:>20} | {len(s):>6} | too few")
        continue
    print(f"{lab:>20} | {len(s):>6} | {s.case.mean():>9.1%} | {s.case.mean()/base:>5.2f}x")
r.to_csv('results/case_control_pivots.csv', index=False)
print("\nsaved")
