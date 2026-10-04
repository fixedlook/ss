"""
ORDER FLOW study - corrected.

BUG FOUND IN THE FIRST ATTEMPT: the footprint file stores rows sorted by PRICE
within each minute, so `.last()` returns the minute's HIGH, not its close.
Racing symmetric barriers from the high mechanically favours the downside, which
was visible as P(up) = 42% instead of 50%.

FIX: take PRICE from the 1-minute OHLC bar file (true open/high/low/close) and
ORDER FLOW from the footprint (bid/ask split, delta, trades). Merge on date+minute.
The reference is then a genuine observed close, and a delta-driven bias cannot
enter through the reference price.

Features are normalised as expanding within-session percentiles, so no lookahead.
The bar/ask split is tight (median 0.499, sd 0.068), so "extreme aggression" is
defined at the 10th/90th percentile of the session, not at fixed 0.75/0.25.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
BARRIER = 100.0
STEP = 10
N_BOOT = 2000
rng = np.random.default_rng(777)

# ---------- order flow from the footprint ----------
fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'),
    ask=('ask_volume', 'sum'), trades=('trades', 'sum')).reset_index()
fm['delta'] = fm.ask - fm.bid
fm['buy_ratio'] = fm.ask / fm.vol

# ---------- true price from the bars ----------
bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'open', 'high', 'low', 'close']]

m = bars.merge(fm, on=['date', 'minute'], how='inner').sort_values(['date', 'minute'])
m = m.reset_index(drop=True)
days = sorted(m.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in m.groupby('date')}
print(f"sessions {len(days)} | minutes {len(m):,}")

# ---- validate the merge and the reference price ----
samp = m.sample(min(4000, len(m)), random_state=1)
bad = ((samp.close > samp.high + 1e-6) | (samp.close < samp.low - 1e-6)).sum()
print(f"close inside [low, high]: {'OK' if bad == 0 else f'{bad} violations'}")
print(f"mean ask/total ratio   : {m.buy_ratio.mean():.4f}  (0.5 = balanced)")


def epct(arr, i, min_n=25):
    past = arr[:i]
    if len(past) < min_n or not np.isfinite(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


rows = []
for d in days:
    b = gday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    V, D, BR = b.vol.values, b.delta.values, b.buy_ratio.values
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

    for i in range(40, n - 15, STEP):
        ref = C[i]
        seg_h, seg_l = H[i + 1:], L[i + 1:]
        if len(seg_h) == 0:
            continue
        uh = seg_h >= ref + BARRIER
        dh = seg_l <= ref - BARRIER
        a = np.argmax(uh) if uh.any() else 10 ** 9
        bb = np.argmax(dh) if dh.any() else 10 ** 9
        if a == bb:
            continue
        outcome_up = a < bb
        pslope = C[i] - C[i - 10]
        if pslope == 0:
            continue
        approach_up = pslope > 0
        reversal = int(outcome_up != approach_up)
        cvd_slope = cvd[i] - cvd[i - 10]
        br_pct = epct(BR, i)
        tr_pct = epct(TR, i)
        rows.append(dict(
            date=str(d), i=i, reversal=reversal, outcome_up=int(outcome_up),
            approach_up=int(approach_up), mod=int(i),
            delta=D[i], delta_ratio=D[i] / V[i] if V[i] else np.nan,
            absdelta_pct=epct(np.abs(D), i),
            cvd_slope_norm=cvd_slope / med_ad[i] if med_ad[i] else np.nan,
            cvd_div=int(cvd_slope != 0 and np.sign(cvd_slope) != np.sign(pslope)),
            delta_opposes=int(D[i] != 0 and np.sign(D[i]) != (1 if approach_up else -1)),
            absorption=int(abs(D[i]) > 1.5 * med_ad[i] and RG[i] < 0.7 * med_r[i])
            if med_ad[i] and med_r[i] else 0,
            buy_extreme=int(np.isfinite(br_pct) and br_pct > 0.9),
            sell_extreme=int(np.isfinite(br_pct) and br_pct < 0.1),
            trades_pct=tr_pct,
            vol_pct=epct(V, i),
            vol_rel=V[i] / med_v[i] if med_v[i] else np.nan,
            rng_rel=RG[i] / med_r[i] if med_r[i] else np.nan,
            br_dev=BR[i] - med_br[i] if med_br[i] else np.nan,
        ))

e = pd.DataFrame(rows)
print(f"\nevents {len(e):,} | sessions {e.date.nunique()}")
print(f"base reversal {e.reversal.mean():.2%}    base P(up) {e.outcome_up.mean():.2%}")
print("  (both should sit near 50% - the reference bug is gone if they do)")

e.to_csv('results/orderflow_events_v2.csv', index=False)

CANDS = [
    ('CVD divergence (CVD vs price)', e.cvd_div == 1),
    ('delta opposes approach (divergence)', e.delta_opposes == 1),
    ('absorption (big delta, small range)', e.absorption == 1),
    ('extreme BUY aggression (top 10%)', e.buy_extreme == 1),
    ('extreme SELL aggression (bottom 10%)', e.sell_extreme == 1),
    ('big |delta| (top 20% of session)', e.absdelta_pct > 0.8),
    ('tiny |delta| (bottom 20%)', e.absdelta_pct < 0.2),
    ('volume spike (top 10%)', e.vol_pct > 0.9),
    ('high trade count (top 20%)', e.trades_pct > 0.8),
]

print("\n" + "=" * 80)
print("REVERSAL RATE by order-flow feature")
print("=" * 80)
ucl = np.unique(e.date.values)
idx = {u: np.where(e.date.values == u)[0] for u in ucl}
print(f"{'feature':>36} | {'with':>7} | {'without':>7} | {'diff':>7} | {'n':>6} | {'95% CI':>16}")
out = []
for lab, mm in CANDS:
    mm = np.asarray(mm.values if hasattr(mm, 'values') else mm, bool)
    if mm.sum() < 40 or (~mm).sum() < 40:
        print(f"{lab:>36} | too few (n={mm.sum()})")
        continue
    a = e.reversal.values[mm].mean()
    b_ = e.reversal.values[~mm].mean()
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        s = mm[ii]
        if s.sum() > 5 and (~s).sum() > 5:
            boot.append(e.reversal.values[ii][s].mean() - e.reversal.values[ii][~s].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{lab:>36} | {a:>6.2%} | {b_:>6.2%} | {a-b_:>+6.2%} | {mm.sum():>6} | "
          f"[{lo:>+6.2%},{hi:>+6.2%}] {sig}")
    out.append(dict(feature=lab, with_=a, without=b_, diff=a - b_, n=int(mm.sum()),
                    ci_lo=lo, ci_hi=hi))

print("\n" + "=" * 80)
print("DIRECTIONAL: does order flow predict UP vs DOWN (ignoring approach)?")
print("=" * 80)
print(f"{'feature':>36} | {'P(up)':>7} | {'without':>7} | {'diff':>7} | {'n':>6}")
for lab, mm in CANDS:
    mm = np.asarray(mm.values if hasattr(mm, 'values') else mm, bool)
    if mm.sum() < 40 or (~mm).sum() < 40:
        continue
    a = e.outcome_up.values[mm].mean()
    b_ = e.outcome_up.values[~mm].mean()
    print(f"{lab:>36} | {a:>6.2%} | {b_:>6.2%} | {a-b_:>+6.2%} | {mm.sum():>6}")

print("\n" + "=" * 80)
print("OUT OF SAMPLE (split by date)")
print("=" * 80)
ds = np.sort(np.unique(e.date.values))
half = ds[len(ds) // 2]
e['half'] = np.where(e.date.values < half, 'IS', 'OOS')
print(f"split at {half}")
print(f"{'feature':>36} | {'IS diff':>9} | {'OOS diff':>9} | {'IS n':>6} | {'OOS n':>6} | cons")
for lab, mm in CANDS:
    mm = np.asarray(mm.values if hasattr(mm, 'values') else mm, bool)
    cells = []
    for h in ['IS', 'OOS']:
        sel = e.half.values == h
        s = mm & sel
        if s.sum() < 15 or (~s & sel).sum() < 15:
            cells.append((np.nan, int(s.sum())))
        else:
            cells.append((e.reversal.values[s].mean() - e.reversal.values[~s & sel].mean(),
                          int(s.sum())))
    d1, d2 = cells[0][0], cells[1][0]
    ok = 'yes' if (not np.isnan(d1) and not np.isnan(d2) and np.sign(d1) == np.sign(d2)) else 'NO'
    f1 = f"{d1:>+8.2%}" if not np.isnan(d1) else f"{'--':>9}"
    f2 = f"{d2:>+8.2%}" if not np.isnan(d2) else f"{'--':>9}"
    print(f"{lab:>36} | {f1} | {f2} | {cells[0][1]:>6} | {cells[1][1]:>6} | {ok}")

pd.DataFrame(out).to_csv('results/orderflow_concepts.csv', index=False)
print("\nwrote results/orderflow_events_v2.csv + results/orderflow_concepts.csv")
