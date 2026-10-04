"""
Does the past hour predict the next hour?

E[ next H-min block return | last H-min block return ].  Slope < 0 (and bigger
than cost) would be a contrarian hourly trade.

Estimator A (Fama-MacBeth: per-session demeaned OLS of next block on last block)
looks like it finds one: slope -0.045/-0.081/-0.110, t -7.0/-9.5/-9.0 at
H = 15/30/60.  It is a trap.  Demeaning the block series inside a session forces
the demeaned blocks to sum to ~zero, which every random sequence must "undo" with
a negative lag-1 autocorrelation of order -1/m (m = blocks per session).  It is a
property of the estimator, not of the market.

Three controls, so the conclusion does not rest on any one of them:

  A) Fama-MacBeth               - the suspect estimator
  B1) permutation null          - shuffle the session's 1-min returns (destroys
                                  all dependence; fixes the session total)
  B2) i.i.d. block null         - fresh Gaussian blocks matched to each session's
                                  block count and block-return sd (no fixed total)
  C) pooled regression, cluster-robust SEs by session - ONE intercept, no
     within-session demeaning of the slope, so no -1/m artefact.

Run:  /tmp/venv/bin/python research/momentum_vs_reversion.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
day = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
day = {d: g for d, g in day.items() if len(g) >= 200}
rng = np.random.default_rng(7)


def blocks_from(C, H, min_blocks=4):
    """Non-overlapping block returns of a session close path."""
    n = len(C) - 1
    nb = (n - 1) // H
    if nb < min_blocks:
        return None
    P = C[:nb * H + 1:H]
    b = np.diff(P)
    if not np.all(np.isfinite(b)):
        return None
    return b


def demeaned_slope(b):
    """Per-session AR(1) on the demeaned block series = estimator A's slope."""
    if len(b) < 4:
        return None
    x, y = b[:-1], b[1:]
    xx, yy = x - x.mean(), y - y.mean()
    den = xx @ xx
    if den <= 0:
        return None
    return (xx @ yy) / den


HOR = [15, 30, 60, 120]
print(f"sessions {len(day):,}\n")
print("=" * 108)
print("SAME ESTIMATOR (per-session demeaned) ON REAL DATA AND ON TWO NULLS")
print("=" * 108)
print(f"{'H':>5} | {'REAL':>9} {'t':>6} | {'PERM null':>9} {'t':>6} | {'iid null':>9} {'t':>6} | "
      f"{'real-perm':>10} {'t':>6} | {'real-iid':>10} {'t':>6}")
for H in HOR:
    real, perm, iid, d_perm, d_iid = [], [], [], [], []
    for d, b in day.items():
        C = b.close.values.astype(float)
        rb = blocks_from(C, H)
        if rb is None:
            continue
        s = demeaned_slope(rb)
        if s is None:
            continue
        # B1: permute the 1-min returns, rebuild the path
        r = np.diff(C)
        Cs = C[0] + np.r_[0, np.cumsum(rng.permutation(r))]
        pb = blocks_from(Cs, H)
        # B2: fresh iid Gaussian blocks, same count and sd as the session's
        gb = rng.normal(0.0, rb.std(ddof=1), len(rb))
        sp, sg = demeaned_slope(pb), demeaned_slope(gb)
        if sp is None or sg is None:
            continue
        real.append(s); perm.append(sp); iid.append(sg)
        d_perm.append(s - sp); d_iid.append(s - sg)

    def stat(a):
        a = np.array(a)
        return a.mean(), (a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std(ddof=1) > 0 else np.nan)

    if len(real) < 20:
        print(f"{H:>5} | {'-- too few sessions (needs 4+ blocks) --':>80}")
        continue
    mr, tr = stat(real)
    mp, tp = stat(perm)
    mg, tg = stat(iid)
    md, td = stat(d_perm)
    md2, td2 = stat(d_iid)
    print(f"{H:>5} | {mr:>+9.4f} {tr:>+6.1f} | {mp:>+9.4f} {tp:>+6.1f} | {mg:>+9.4f} {tg:>+6.1f} | "
          f"{md:>+10.4f} {td:>+6.1f} | {md2:>+10.4f} {td2:>+6.1f}")

print("""
  Both nulls use the SAME estimator on data with no exploitable dependence.
  If they come out as negative as the real column, estimator A is reporting its
  own -1/m artefact; the only usable numbers are the two difference columns.""")

print("\n" + "=" * 108)
print("C) POOLED regression, cluster-robust SEs by session  (no within-session demeaning)")
print("=" * 108)
print(f"{'H':>5} | {'slope':>9} {'se':>8} {'t':>6} | {'pairs':>8} | {'fade P&L':>12} {'t':>6}")
for H in HOR:
    X, Y, G = [], [], []
    for gi, (d, b) in enumerate(day.items()):
        rb = blocks_from(b.close.values.astype(float), H, min_blocks=3)
        if rb is None or len(rb) < 2:
            continue
        X.extend(rb[:-1])
        Y.extend(rb[1:])
        G.extend([gi] * (len(rb) - 1))
    if len(X) < 200 or len(np.unique(G)) < 50:
        print(f"{H:>5} | {'-- too few pairs --':>50}")
        continue
    X = np.array(X)
    Y = np.array(Y)
    G = np.array(G)
    Xd = np.c_[np.ones(len(X)), X]
    beta = np.linalg.lstsq(Xd, Y, rcond=None)[0]
    u = Y - Xd @ beta
    XtX_inv = np.linalg.inv(Xd.T @ Xd)
    meat = np.zeros((2, 2))
    for g in np.unique(G):
        m = G == g
        s = Xd[m].T @ u[m]
        meat += np.outer(s, s)
    se = np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv))
    tr = -np.sign(X) * Y
    tp = tr.mean() / (tr.std(ddof=1) / np.sqrt(len(tr)))
    print(f"{H:>5} | {beta[1]:>+9.4f} {se[1]:>8.4f} {beta[1]/se[1]:>+6.1f} | {len(X):>8,} | "
          f"{tr.mean():>+9.3f} pts {tp:>+6.1f}")

print("""
READING IT  (result, 2026-10-04)
  A)  slope -0.0446 (t=-7.0) / -0.0805 (t=-9.5) / -0.1103 (t=-9.1)
  B1) permutation null  -0.0370 (t=-6.2) / -0.0896 (t=-10.0) / -0.1656 (t=-12.4)
  B2) i.i.d. block null -0.0448 (t=-7.4) / -0.0870 (t=-10.1) / -0.1747 (t=-13.2)
      The i.i.d. null reproduces the real column to two decimals at H=15/30, i.e.
      the demeaned estimator reports -1/m on ANY sequence (m = blocks per session).
      Since real-minus-null changes sign (-0.008, +0.009, +0.055) and at H=60 the
      null is MORE reversionary than the market, there is no fade to find.
  C)  pooled, cluster-robust: -0.0145 (t=-1.4), +0.0045 (t=+0.3),
      +0.0214 (t=+1.1), +0.0281 (t=+1.2); fade-the-block P&L
      +0.24 / -0.10 / -1.04 / -1.50 pts (t +1.2 / -0.2 / -1.3 / -0.8).

  => The "hour-scale fade" candidate is RETRACTED.  It was the -1/m bias of
     demeaning 6-29 overlapping numbers inside a session, dressed up as a t-stat.

  This is consistent with the rest of the project: the 1-min process is a
  martingale, the variance ratio's shortfall is about variance not drift, and no
  entry rule can beat the barrier structure.  The only robust effect found is
  flow -> RANGE, not flow -> direction.
""")
