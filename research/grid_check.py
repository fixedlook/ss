"""
IS THE CVD-DIVERGENCE EFFECT REAL, OR AN ARTIFACT OF THE SAMPLING GRID?

v2 (every 10th minute, i = 40,50,60,...)  ->  divergence +3.09%, IS +2.83 / OOS +3.41
full grid (every minute)                  ->  divergence +0.21%, IS -2.31 / OOS +2.95

Same minutes, nested samples. They cannot both be right. The 10-minute grid
always lands on the SAME minutes-of-session in every session, so if the effect
varies across i mod 10 the grid estimate is biased by construction.

Tests:
  1. divergence effect separately for each residue class i mod 10
  2. 200 random 10% subsamples -> sampling distribution of the estimate
  3. the effect on the full grid with a cluster-robust interval
  4. the arrival@union x divergence cell under random 50% splits
"""
import numpy as np
import pandas as pd

f = pd.read_csv('results/lvn_cvd_full.csv')
rng = np.random.default_rng(99)
y = f['reversal'].values.astype(float)
div = (f['div_any'] == 1).values
ucl = np.unique(f['date'].values)

print(f"n={len(f):,} minutes, {f['date'].nunique()} sessions")
print(f"base {y.mean():.2%} | div {div.mean():.2%} | div reversal {y[div].mean():.2%} "
      f"vs nodiv {y[~div].mean():.2%} = {y[div].mean()-y[~div].mean():+.2%}")

print("\n1. DIVERGENCE EFFECT BY RESIDUE CLASS  i mod 10")
print(f"{'i mod 10':>9} | {'n div':>6} | {'div rev':>8} | {'nodiv n':>7} | {'nodiv rev':>9} | {'effect':>8}")
for r in range(10):
    mm = (f['i'].values % 10) == r
    d1, d0 = mm & div, mm & ~div
    print(f"{r:>9} | {d1.sum():>6} | {y[d1].mean():>8.2%} | {d0.sum():>7} | "
          f"{y[d0].mean():>9.2%} | {y[d1].mean()-y[d0].mean():>+8.2%}")
print("   (v2 used r=0 only)")

print("\n2. RANDOM 10% SUBSAMPLES (200 draws, no clustering)")
eff = []
for _ in range(200):
    mm = rng.random(len(f)) < 0.10
    d1, d0 = mm & div, mm & ~div
    if d1.sum() > 20 and d0.sum() > 50:
        eff.append(y[d1].mean() - y[d0].mean())
eff = np.array(eff)
print(f"mean {eff.mean():+.2%} | sd {eff.std():.2%} | "
      f"p2.5 {np.percentile(eff,2.5):+.2%} | p97.5 {np.percentile(eff,97.5):+.2%}")
print(f"share of random 10% draws above the v2 estimate (+3.09%): {(eff >= 0.0309).mean():.1%}")


def creg(y, X, g):
    XtX = X.T @ X
    XtXi = np.linalg.pinv(XtX)
    bb = XtXi @ (X.T @ y)
    u = y - X @ bb
    meat = np.zeros_like(XtX)
    for gg in np.unique(g):
        mm = (g == gg)
        sc = X[mm].T @ u[mm]
        meat += np.outer(sc, sc)
    G = len(np.unique(g))
    return bb, np.sqrt(np.diag(XtXi @ meat @ XtXi * (G / (G - 1))))


print("\n3. FULL GRID, cluster-robust")
for lab, mm in [('div_any', div), ('arr5 & div', (f['arr5'].values == 1) & div),
                ('arr1 & div', (f['arr1'].values == 1) & div)]:
    X = np.column_stack([np.ones(len(f)), mm.astype(float)])
    bb, se = creg(y, X, f['date'].values)
    print(f"{lab:>12}: {bb[1]:+.2%}  SE {se[1]:.2%}  t {bb[1]/se[1]:+.2f}  "
          f"95% CI [{bb[1]-1.96*se[1]:+.2%},{bb[1]+1.96*se[1]:+.2%}]")

print("\n4. ARRIVAL@UNION x DIVERGENCE under random 50% splits (50 draws)")
cell = ((f['arr5'].values == 1) & div)
out = []
for _ in range(50):
    mm = rng.random(len(f)) < 0.5
    a, b = cell & mm, cell & ~mm
    out.append((y[a].mean() if a.sum() > 20 else np.nan,
                y[b].mean() if b.sum() > 20 else np.nan))
out = np.array(out)
print(f"cell reversal across random halves: mean {np.nanmean(out):.2%} | "
      f"half-to-half sd {np.nanstd(out):.2%}")
print(f"min half-estimate {np.nanmin(out):.2%} | max {np.nanmax(out):.2%}")

print("\n5. SAME, but comparing against the no-divergence side of the SAME half")
out2 = []
for _ in range(50):
    mm = rng.random(len(f)) < 0.5
    a = (f['arr5'].values == 1) & div & mm
    b = (f['arr5'].values == 1) & ~div & mm
    if a.sum() > 20:
        out2.append(y[a].mean() - y[b].mean())
out2 = np.array(out2)
print(f"divergence-at-level effect across random halves: mean {out2.mean():+.2%} | "
      f"sd {out2.std():.2%} | range [{out2.min():+.2%},{out2.max():+.2%}] | "
      f"share positive {(out2 > 0).mean():.0%}")
