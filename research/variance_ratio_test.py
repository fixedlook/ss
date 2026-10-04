"""
Is the 1-minute price process a martingale?  Randomisation test on the variance ratio.

VR(q) = Var(q-minute return) / (q * Var(1-minute return)) = 1 + 2*sum_k (1-k/q) rho_k
    VR < 1  mean reversion (moves get undone - a reversal edge can exist)
    VR > 1  trending       (moves extend  - reversal bets lose by construction)
    VR = 1  martingale     (no barrier structure can have positive expectancy)

A random walk cannot be rejected by comparing VR with 1: the finite-sample
estimator is biased downward for long q.  The correct null is a within-session
PERMUTATION of the same returns, which keeps the marginal distribution (fat tails),
the volatility level and the exact minute spacing, and destroys only the order.
Everything is paired by session, so quiet and wild sessions cancel.

Run:  /tmp/venv/bin/python research/variance_ratio_test.py
"""
import numpy as np
import pandas as pd

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
day = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
day = {d: g for d, g in day.items() if len(g) >= 200}
QS = [2, 5, 10, 30, 60]
NPERM = 10
rng = np.random.default_rng(7)


def vr_direct(r, q):
    rq = np.convolve(r, np.ones(q), 'valid')
    return np.var(rq) / (q * np.var(r))


def vr_acf(r, qmax=60):
    x = r - r.mean()
    den = x @ x
    rho = np.array([(x[:-k] @ x[k:]) / den for k in range(1, qmax)])
    return {q: 1 + 2 * sum((1 - k / q) * rho[k - 1] for k in range(1, q)) for q in QS}


rows = []
for d, b in day.items():
    r = np.diff(b.close.values.astype(float))
    if len(r) < 200 or np.std(r) == 0:
        continue
    real_d = {q: vr_direct(r, q) for q in QS}
    real_a = vr_acf(r)
    null_d = {q: [] for q in QS}
    null_a = {q: [] for q in QS}
    for _ in range(NPERM):
        rp = rng.permutation(r)
        for q in QS:
            null_d[q].append(vr_direct(rp, q))
        ai = vr_acf(rp)
        for q in QS:
            null_a[q].append(ai[q])
    rows.append((d, [real_d[q] for q in QS], [np.mean(null_d[q]) for q in QS],
                 [real_a[q] for q in QS], [np.mean(null_a[q]) for q in QS]))

print(f"sessions {len(rows):,}   (null = {NPERM} within-session permutations each)")
print("\n" + "=" * 104)
print("VARIANCE RATIO: REAL vs PERMUTED NULL (paired by session)")
print("=" * 104)
hdr = (f"{'q':>4} | {'real':>8} {'null':>8} {'diff':>9} {'t':>7} | "
       f"{'real':>8} {'null':>8} {'diff':>9} {'t':>7}")
print(f"{'':>4} | {'--- direct estimator ---':^36} | {'--- ACF-implied ---':^36}")
print(hdr)
print("-" * len(hdr))
for j, q in enumerate(QS):
    out = f"{q:>4} |"
    for R, N in ((1, 2), (3, 4)):
        rv = np.array([x[R][j] for x in rows])
        nv = np.array([x[N][j] for x in rows])
        dif = rv - nv
        t = dif.mean() / (dif.std(ddof=1) / np.sqrt(len(dif)))
        out += f" {rv.mean():>8.4f} {nv.mean():>8.4f} {dif.mean():>+9.4f} {t:>+7.1f} |"
    print(out)

print(f"""
Interpretation
  diff is VR(real) - VR(permuted).  A permuted series is a martingale WITH the real
  data's fat tails, volatility level and minute spacing.  So:
    diff < 0 with |t| > 3  -> genuine mean reversion exists and can be sized up.
    diff ~ 0               -> the price path is ordered like a random shuffle of its
                              own returns.  No entry rule can beat a barrier
                              structure, which is exactly what the 63-cell SL/TP grid
                              and the fitted k ~ 0 both said.
    diff > 0               -> momentum.""")

q = 60
j = QS.index(q)
lvl = np.median([np.diff(g.close.values.astype(float)).std() for g in day.values()])
print(f"\nfor scale: median 1-min sigma {lvl:.2f} pts -> a 60-minute move is "
      f"~{lvl*np.sqrt(60):.0f} pts under a random walk")
for q in QS:
    j = QS.index(q)
    d_ = np.mean([x[1][j] - x[2][j] for x in rows])
    print(f"  VR({q:>2}) shortfall {d_:+.4f}  ->  60-min-equivalent sigma is "
          f"{100*np.sqrt(max(1e-9,1+d_)):.1f}% of the random-walk value")
