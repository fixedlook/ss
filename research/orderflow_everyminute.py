"""
ORDER FLOW on EVERY MINUTE - the honest version of the divergence test.

History: orderflow_study_v2 tested every 10th minute (STEP=10) and produced the
"+3.09% CVD divergence" headline. grid_check.py then showed that effect lives
entirely in bar-index phase (i mod 10 == 0 -> +3.09%, other phases ~0, full grid
+0.21%). So the 10-minute grid is a confirmed artifact - this script has no grid.

Data: the new per-minute export (mnq_flow.csv.gz), validated minute-for-minute
against the older footprint export (68,894/68,895 minutes identical).
      1,024 sessions with flow, 433,063 flow minutes, 2019-08 -> 2026-09.

Races are still symmetric +/-100pt from the true observed close, run to the end of
the session, and every statistic is bootstrapped BY SESSION (clusters), which is
the right unit of independence.

Run:  /tmp/venv/bin/python research/orderflow_everyminute.py
"""
import numpy as np
import pandas as pd

BARRIER = 100.0
N_BOOT = 2000
rng = np.random.default_rng(20261004)

df = pd.read_parquet('/tmp/flow/flow_good.parquet')
df['flow'] = df['bid_volume'] + df['ask_volume']
df = df[df.flow > 0].copy()
df['delta_r'] = df.ask_volume - df.bid_volume          # never trust the file's own delta
df['buy_ratio'] = df.ask_volume / df.flow
df = df.sort_values('timestamp').reset_index(drop=True)
days = sorted(df.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in df.groupby('date')}
print(f"sessions {len(days):,} | flow minutes {len(df):,}")
print(f"date range {df.ny.min()}  ->  {df.ny.max()}")


def epct(arr, i, min_n=25):
    past = arr[:i]
    if len(past) < min_n or not np.isfinite(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


rows = []
for d in days:
    b = gday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    V, D, BR = b.flow.values, b.delta_r.values, b.buy_ratio.values
    TR = b.trades.values.astype(float)
    n = len(b)
    if n < 60:
        continue
    cvd = np.cumsum(D)
    RG = H - L
    med_r = pd.Series(RG).expanding(20).median().values
    med_ad = pd.Series(np.abs(D)).expanding(20).median().values
    med_v = pd.Series(V).expanding(20).median().values
    med_br = pd.Series(BR).expanding(20).median().values

    for i in range(40, n - 15):                      # EVERY minute, no grid
        ref = C[i]
        seg_h, seg_l = H[i + 1:], L[i + 1:]
        if len(seg_h) == 0:
            continue
        uh = seg_h >= ref + BARRIER
        dh = seg_l <= ref - BARRIER
        a = np.argmax(uh) if uh.any() else 10 ** 9
        bb = np.argmax(dh) if dh.any() else 10 ** 9
        if a == bb:                                  # unresolved - drop, same as v2
            continue
        outcome_up = a < bb
        pslope = C[i] - C[i - 10]
        if pslope == 0:
            continue
        approach_up = pslope > 0
        reversal = int(outcome_up != approach_up)
        cvd_slope = cvd[i] - cvd[i - 10]
        fwd_h, fwd_l = H[i + 1:i + 31], L[i + 1:i + 31]
        mv30 = float(fwd_h.max() - fwd_l.min()) if len(fwd_h) else np.nan
        rows.append(dict(
            date=str(d), i=i, mod=int(i), reversal=reversal, outcome_up=int(outcome_up),
            approach_up=int(approach_up), mv30=mv30,
            delta=D[i], delta_ratio=D[i] / V[i] if V[i] else np.nan,
            absdelta_pct=epct(np.abs(D), i),
            cvd_slope_norm=cvd_slope / med_ad[i] if med_ad[i] else np.nan,
            cvd_div=int(cvd_slope != 0 and np.sign(cvd_slope) != np.sign(pslope)),
            delta_opposes=int(D[i] != 0 and np.sign(D[i]) != (1 if approach_up else -1)),
            absorption=int(abs(D[i]) > 1.5 * med_ad[i] and RG[i] < 0.7 * med_r[i])
            if med_ad[i] and med_r[i] else 0,
            buy_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) > 0.9),
            sell_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) < 0.1),
            trades_pct=epct(TR, i),
            vol_pct=epct(V, i),
            vol_rel=V[i] / med_v[i] if med_v[i] else np.nan,
            rng_rel=RG[i] / med_r[i] if med_r[i] else np.nan,
            br_dev=BR[i] - med_br[i] if med_br[i] else np.nan,
        ))

e = pd.DataFrame(rows)
print(f"\nevents {len(e):,} | sessions {e.date.nunique()}")
print(f"base reversal {e.reversal.mean():.2%}   base P(up) {e.outcome_up.mean():.2%}")
e.to_parquet('/tmp/flow/events_everyminute.parquet', index=False)

# ---- sanity: does the OLD artifact reproduce on this file? ----
print("\n=== the dead grid, re-checked on the new file (expect flat) ===")
for m in range(10):
    s = e[e['mod'] == m]
    print(f"  i mod 10 == {m}: n={len(s):>6}  reversal {s.reversal.mean():.2%}")
print(f"  full grid      : n={len(e):>6}  reversal {e.reversal.mean():.2%}")

CANDS = [
    ('CVD divergence (CVD vs price)', e.cvd_div == 1),
    ('delta opposes approach', e.delta_opposes == 1),
    ('absorption (big delta, small range)', e.absorption == 1),
    ('extreme BUY aggression (top 10%)', e.buy_extreme == 1),
    ('extreme SELL aggression (bottom 10%)', e.sell_extreme == 1),
    ('big |delta| (top 20%)', e.absdelta_pct > 0.8),
    ('tiny |delta| (bottom 20%)', e.absdelta_pct < 0.2),
    ('volume spike (top 10%)', e.vol_pct > 0.9),
    ('high trade count (top 20%)', e.trades_pct > 0.8),
    ('buy_ratio deviates from session median', e.br_dev.abs() > 0.05),
]


def boot_diff(cl, mask, out, n_boot=N_BOOT, rng=rng):
    """session-clustered bootstrap of mean(out|mask) - mean(out|~mask)."""
    mask = np.asarray(mask, bool)
    cod, _ = pd.factorize(cl)
    K = cod.max() + 1
    yes = np.bincount(cod, weights=(mask * out).astype(float), minlength=K)
    ny = np.bincount(cod, weights=mask.astype(float), minlength=K)
    no = np.bincount(cod, weights=((~mask) * out).astype(float), minlength=K)
    nn = np.bincount(cod, weights=(~mask).astype(float), minlength=K)
    W = rng.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    sy, sny = W @ yes, W @ ny
    sN, snn = W @ no, W @ nn
    with np.errstate(invalid='ignore', divide='ignore'):
        d = sy / sny - sN / snn
    d = d[np.isfinite(d)]
    return np.percentile(d, [2.5, 97.5]), d


cl = e.date.values
out_r = e.reversal.values.astype(float)
out_u = e.outcome_up.values.astype(float)

print("\n" + "=" * 92)
print("REVERSAL RATE by order-flow feature - every minute, session-clustered CIs")
print("=" * 92)
print(f"{'feature':>40} | {'with':>7} | {'without':>7} | {'diff':>7} | {'n':>7} | {'95% CI':>17} | sig")
summary = []
for lab, mask in CANDS:
    mask = np.asarray(mask, bool)
    if mask.sum() < 100:
        print(f"{lab:>40} | too few (n={mask.sum()})")
        continue
    a, b_ = out_r[mask].mean(), out_r[~mask].mean()
    (lo, hi), _ = boot_diff(cl, mask, out_r)
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{lab:>40} | {a:>6.2%} | {b_:>6.2%} | {a-b_:>+6.2%} | {mask.sum():>7} | "
          f"[{lo:>+6.2%},{hi:>+6.2%}] | {sig}")
    summary.append(dict(feature=lab, with_=a, without=b_, diff=a - b_, n=int(mask.sum()),
                        ci_lo=lo, ci_hi=hi, significant=bool(sig)))

print("\n" + "=" * 92)
print("DIRECTIONAL: P(up) by feature (ignoring the approach)")
print("=" * 92)
for lab, mask in CANDS:
    mask = np.asarray(mask, bool)
    if mask.sum() < 100:
        continue
    a, b_ = out_u[mask].mean(), out_u[~mask].mean()
    (lo, hi), _ = boot_diff(cl, mask, out_u, n_boot=800)
    print(f"{lab:>40} | {a:>6.2%} | {b_:>6.2%} | {a-b_:>+6.2%} | {mask.sum():>7} | "
          f"[{lo:>+6.2%},{hi:>+6.2%}]")

print("\n" + "=" * 92)
print("ERA STABILITY (reversal-rate difference, by year)")
print("=" * 92)
e['year'] = pd.to_datetime(e.date).dt.year
yrs = sorted(e.year.unique())
hdr = f"{'feature':>40} | " + " | ".join(f"{y:>6}" for y in yrs)
print(hdr)
for lab, mask in CANDS:
    mask = np.asarray(mask, bool)
    if mask.sum() < 100:
        continue
    cells = []
    for y in yrs:
        sel = (e.year.values == y)
        s = mask & sel
        if s.sum() < 50 or (~s & sel).sum() < 50:
            cells.append('     --')
        else:
            cells.append(f"{out_r[s].mean()-out_r[~s & sel].mean():>+6.1%}")
    print(f"{lab:>40} | " + " | ".join(cells))

print("\n" + "=" * 92)
print("HOW FAR DOES IT MOVE?  30-minute range after the event (points), by feature")
print("=" * 92)
e['mv30'] = e['mv30'].astype(float)
print(f"baseline 30-min range: mean {e.mv30.mean():.1f} pts   median {e.mv30.median():.1f}")
print(f"{'feature':>40} | {'with':>7} | {'without':>7} | {'ratio':>6} | {'hit100':>7} | {'base':>7} | {'n':>7}")
mv_summary = []
for lab, mask in CANDS:
    mask = np.asarray(mask, bool)
    if mask.sum() < 100:
        continue
    a, b_ = e.mv30.values[mask].mean(), e.mv30.values[~mask].mean()
    p_hit = (e.mv30.values[mask] >= 100).mean()
    p_base = (e.mv30.values[~mask] >= 100).mean()
    print(f"{lab:>40} | {a:>6.1f} | {b_:>6.1f} | {a/b_:>5.2f}x | {p_hit:>6.1%} | {p_base:>6.1%} | {mask.sum():>7}")
    mv_summary.append(dict(feature=lab, mv_with=a, mv_without=b_, ratio=a / b_,
                           hit_with=p_hit, hit_without=p_base, n=int(mask.sum())))
pd.DataFrame(mv_summary).to_csv('results/orderflow_everyminute_move.csv', index=False)

pd.DataFrame(summary).to_csv('results/orderflow_everyminute_summary.csv', index=False)
print("\nwrote results/orderflow_everyminute_summary.csv + results/orderflow_everyminute_move.csv")
