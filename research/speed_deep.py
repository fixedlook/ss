"""
Nail down the incoming-speed finding.

Survived the volatility control (holds in all four developing-range quartiles).
Now: confidence intervals, era stability, and disentangling SPEED from the
SIZE of the incoming leg (they are correlated).

Also compute what it means in points, not just rates.
"""
import numpy as np
import pandas as pd

r = pd.read_csv('results/case_control_pivots.csv')
r['year'] = pd.to_datetime(r.date).dt.year
rng = np.random.default_rng(2024)
base = r.case.mean()
print(f"samples {len(r):,}  base rate {base:.2%}\n")

print("=" * 78)
print("A. SPEED vs INCOMING-LEG SIZE - which one carries it?")
print("=" * 78)
r['sp'] = pd.qcut(r.in_speed, [0, .5, .75, .9, 1.0], labels=['slow 50%', '50-75', '75-90', 'top 10%'])
r['sz'] = pd.qcut(r.in_leg, [0, .5, .75, .9, 1.0], labels=['small 50%', '50-75', '75-90', 'top 10%'])
print("case rate by SPEED, within incoming-leg-SIZE quartiles")
print(f"{'leg size':>10} | " + " | ".join(f"{c:>9}" for c in ['slow 50%', '50-75', '75-90', 'top 10%']))
for sb, s in r.groupby('sz', observed=True):
    cells = []
    for spb in ['slow 50%', '50-75', '75-90', 'top 10%']:
        t = s[s.sp == spb]
        cells.append(f"{t.case.mean():>8.1%}" if len(t) >= 100 else f"{'--':>9}")
    print(f"{str(sb):>10} | " + " | ".join(cells))
print("  (if the top-10% column stays high in every leg-size row, SPEED is the driver)")

print("\nreverse: case rate by leg SIZE, within speed quartiles")
print(f"{'speed':>10} | " + " | ".join(f"{c:>9}" for c in ['small 50%', '50-75', '75-90', 'top 10%']))
for spb, s in r.groupby('sp', observed=True):
    cells = []
    for sb in ['small 50%', '50-75', '75-90', 'top 10%']:
        t = s[s.sz == sb]
        cells.append(f"{t.case.mean():>8.1%}" if len(t) >= 100 else f"{'--':>9}")
    print(f"{str(spb):>10} | " + " | ".join(cells))

print("\n" + "=" * 78)
print("B. MAGNITUDE - what does a fast arrival actually buy you?")
print("=" * 78)
fast = r[r.in_speed > r.in_speed.quantile(.90)]
slow = r[r.in_speed <= r.in_speed.quantile(.90)]
print(f"{'':>22} | {'fast 10%':>12} | {'slower 90%':>12}")
print(f"{'n':>22} | {len(fast):>12,} | {len(slow):>12,}")
print(f"{'P(reversal >= 100pt)':>22} | {fast.case.mean():>11.1%} | {slow.case.mean():>11.1%}")
print(f"{'median departing move':>22} | {fast.move.median():>10.0f}p | {slow.move.median():>10.0f}p")
print(f"{'mean departing move':>22} | {fast.move.mean():>10.0f}p | {slow.move.mean():>10.0f}p")
print(f"{'  lift on the rate':>22} | {fast.case.mean()/slow.case.mean():>11.2f}x |")

print("\n" + "=" * 78)
print("C. SESSION-CLUSTERED CONFIDENCE INTERVAL")
print("=" * 78)
ucl = np.unique(r.date.values)
idx = {u: np.where(r.date.values == u)[0] for u in ucl}
m = (r.in_speed > r.in_speed.quantile(.90)).values
cv = r.case.values
boot = []
for _ in range(2000):
    pick = rng.choice(ucl, len(ucl), True)
    ii = np.concatenate([idx[u] for u in pick])
    mm = m[ii]
    if mm.sum() > 20 and (~mm).sum() > 20:
        boot.append(cv[ii][mm].mean() / cv[ii][~mm].mean())
lo, hi = np.percentile(boot, [2.5, 97.5])
print(f"  lift, fast 10% vs rest : {cv[m].mean()/cv[~m].mean():.2f}x   95% CI [{lo:.2f}, {hi:.2f}]")

print("\n" + "=" * 78)
print("D. IS IT STABLE? by era")
print("=" * 78)
print(f"{'era':>12} | {'n fast':>8} | {'fast rate':>10} | {'rest rate':>10} | {'lift':>6}")
for lab, s in [('2019-2021', r[r.year <= 2021]), ('2022-2023', r[(r.year > 2021) & (r.year <= 2023)]),
               ('2024-2025', r[(r.year > 2023) & (r.year <= 2025)]), ('2026', r[r.year == 2026])]:
    q = s.in_speed.quantile(.90)
    f = s[s.in_speed > q]
    o = s[s.in_speed <= q]
    if len(f) < 50:
        continue
    print(f"{lab:>12} | {len(f):>8} | {f.case.mean():>9.1%} | {o.case.mean():>9.1%} | "
          f"{f.case.mean()/o.case.mean():>5.2f}x")

print("\n" + "=" * 78)
print("E. HOW OFTEN DOES THIS TRIGGER?")
print("=" * 78)
nsess = r.date.nunique()
print(f"  sessions                    : {nsess:,}")
print(f"  fast-arrival swing points   : {int(m.sum()):,}  "
      f"({m.sum()/nsess:.2f} per session)")
print(f"  of those, P(reversal >=100p): {cv[m].mean():.1%}")
print(f"  -> expected 100pt reversals per session from this filter: "
      f"{m.sum()/nsess*cv[m].mean():.2f}")

print("\n" + "=" * 78)
print("F. DOES IT COMBINE WITH TIME OF DAY?")
print("=" * 78)
r['fast'] = m
r['tod'] = pd.cut(r.min_od, [-1, 30, 60, 120, 240, 390, 1000],
                  labels=['open 30m', '10:00-10:30', '10:30-11:30', '11:30-13:30',
                          '13:30-16:00', '16:00+'])
print(f"{'window':>14} | {'fast n':>7} | {'fast rate':>10} | {'rest rate':>10} | {'lift':>6}")
for w, s in r.groupby('tod', observed=True):
    f = s[s.fast]; o = s[~s.fast]
    if len(f) < 40:
        continue
    print(f"{str(w):>14} | {len(f):>7} | {f.case.mean():>9.1%} | {o.case.mean():>9.1%} | "
          f"{f.case.mean()/o.case.mean():>5.2f}x")
print("\nsaved results to results/case_control_pivots.csv")
r.to_csv('results/case_control_pivots.csv', index=False)
