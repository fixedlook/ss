"""
ORDER FLOW - using the footprint data's bid/ask split for the first time.

The footprint file carries, at every price and minute:
    bid_volume  = volume traded at the bid  (SELLER aggression)
    ask_volume  = volume traded at the ask  (BUYER aggression)
    delta       = ask - bid                 (net aggression)

Every previous test used only total volume, which is direction-agnostic. This is
the first test that uses the direction.

MEASUREMENT RULES (every mistake from this project applied):
  * reference price = the observed close of the event minute (never a VWAP)
  * barriers symmetric: close +/- 100 pts (user's own threshold)
  * every feature computed from data available AT the event minute only
  * percentiles use expanding windows within the session, no lookahead
  * inference clustered by session (blocks bootstrap)
  * out-of-sample split by date reported for anything that looks positive

Event: a minute sampled every 10 minutes through the session, so events do not
       heavily overlap in time.
Outcome: which barrier is hit first, up or down.
Reversal: the barrier opposite to the prior 10-minute price move.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARRIER = 100.0
STEP = 10                     # sample one event every 10 minutes
N_BOOT = 2000
rng = np.random.default_rng(777)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')

mb = fp.groupby(['date', 'minute']).agg(
    hi=('price', 'max'), lo=('price', 'min'), last=('price', 'last'),
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum'),
    trades=('trades', 'sum'), npx=('price', 'nunique'),
).reset_index()
mb['delta'] = mb.ask - mb.bid
mb['rng'] = mb.hi - mb.lo
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)
days = sorted(mb.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in mb.groupby('date')}
print(f"sessions {len(days)} | minutes {len(mb):,}")


def expanding_pct(arr, i):
    past = arr[:i]
    if len(past) < 25 or not np.isfinite(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


rows = []
for d in days:
    b = gday[d]
    H, L, V = b.hi.values, b.lo.values, b.vol.values
    D, BID, ASK = b.delta.values, b.bid.values, b.ask.values
    C = b.last.values
    n = len(b)
    if n < 60:
        continue
    cvd = np.cumsum(D)                     # cumulative delta (known at each bar)
    med_v = pd.Series(V).expanding(20).median().values
    med_r = pd.Series(b.rng.values).expanding(20).median().values
    med_ad = pd.Series(np.abs(D)).expanding(20).median().values

    # ABSOLUTE price extremes so far in the session (for CVD-at-extreme tests)
    run_hi = np.maximum.accumulate(H)
    run_lo = np.minimum.accumulate(L)
    cvd_at_hi = np.full(n, np.nan)
    cvd_at_lo = np.full(n, np.nan)
    last_hi, last_lo = -np.inf, np.inf
    hi_i = lo_i = 0
    for i in range(n):
        if H[i] > last_hi:
            last_hi, hi_i = H[i], i
        if L[i] < last_lo:
            last_lo, lo_i = L[i], i
        cvd_at_hi[i] = cvd[hi_i]
        cvd_at_lo[i] = cvd[lo_i]

    for i in range(40, n - 15, STEP):
        ref = C[i]
        # ---- outcome: symmetric race ----
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
        buy_ratio = ASK[i] / V[i] if V[i] else np.nan

        rows.append(dict(
            date=str(d), i=i, mod=int(i), reversal=reversal, outcome_up=int(outcome_up),
            approach_up=int(approach_up),
            # ---- ORDER FLOW FEATURES ----
            delta=D[i],
            delta_ratio=D[i] / V[i] if V[i] else np.nan,
            absdelta_pct=expanding_pct(np.abs(D), i),
            delta_sign=int(np.sign(D[i])),
            buy_ratio=buy_ratio,
            cvd_slope=cvd_slope,
            cvd_slope_norm=cvd_slope / med_ad[i] if med_ad[i] else np.nan,
            cvd_div=int(np.sign(cvd_slope) != np.sign(pslope) and cvd_slope != 0),
            cvd_vs_price=(cvd[i] / (i + 1)) / med_ad[i] if med_ad[i] else np.nan,
            # absorption: big net delta, small range
            absorption=int(abs(D[i]) > 1.5 * med_ad[i] and b.rng.values[i] < 0.7 * med_r[i])
            if med_ad[i] and med_r[i] else 0,
            # exhaustion: extreme one-sided aggression
            extreme_buy=int(buy_ratio > 0.75) if np.isfinite(buy_ratio) else 0,
            extreme_sell=int(buy_ratio < 0.25) if np.isfinite(buy_ratio) else 0,
            # new price low but CVD higher than at the previous price low (bullish divergence)
            bull_div=int(L[i] <= run_lo[i] - 0.25 and cvd[i] > cvd_at_lo[i]) if i > 0 else 0,
            bear_div=int(H[i] >= run_hi[i] - 0.25 and cvd[i] < cvd_at_hi[i]) if i > 0 else 0,
            # context
            vol_rel=V[i] / med_v[i] if med_v[i] else np.nan,
            rng_rel=b.rng.values[i] / med_r[i] if med_r[i] else np.nan,
            vol_pct=expanding_pct(V, i),
            trades=b.trades.values[i],
            trades_pct=expanding_pct(b.trades.values.astype(float), i),
            # distance from session VWAP
            dist_vwap=abs(ref - (C[:i + 1] * V[:i + 1]).sum() / V[:i + 1].sum()),
        ))

e = pd.DataFrame(rows)
print(f"\nevents {len(e):,} | sessions with events {e.date.nunique()}")
print(f"base reversal rate {e.reversal.mean():.2%}  (symmetric race -> 50% expected)")

print("\n" + "=" * 78)
print("SINGLE FEATURES - does the order flow say anything?")
print("=" * 78)


def test(mask, label, col='reversal'):
    mask = np.asarray(mask, bool)
    if mask.sum() < 40 or (~mask).sum() < 40:
        print(f"  {label:>34}: too few ({mask.sum()})")
        return None
    a = e[col].values[mask].mean()
    b_ = e[col].values[~mask].mean()
    return a, b_, int(mask.sum())


print(f"{'feature':>34} | {'with':>8} | {'without':>8} | {'diff':>8} | {'n':>6}")
cands = [
    ('cvd_div (CVD vs price disagree)', e.cvd_div == 1),
    ('absorption (big delta, small rng)', e.absorption == 1),
    ('extreme buy aggression (>75% ask)', e.extreme_buy == 1),
    ('extreme sell aggression (<25% ask)', e.extreme_sell == 1),
    ('bullish CVD divergence at a low', e.bull_div == 1),
    ('bearish CVD divergence at a high', e.bear_div == 1),
    ('delta opposes prior price move', np.sign(e.delta) != np.where(e.approach_up == 1, 1, -1)),
    ('big |delta| (top 20% of session)', e.absdelta_pct > 0.8),
    ('tiny |delta| (bottom 20%)', e.absdelta_pct < 0.2),
    ('volume spike (top 10% of session)', e.vol_pct > 0.9),
    ('high trade count (top 20%)', e.trades_pct > 0.8),
]
res = []
for lab, m in cands:
    r = test(m.values, lab)
    if r:
        a, b_, n = r
        print(f"{lab:>34} | {a:>7.2%} | {b_:>7.2%} | {a-b_:>+7.2%} | {n:>6}")
        res.append(dict(feature=lab, with_=a, without=b_, diff=a - b_, n=n))

print("\n" + "=" * 78)
print("DOES THE ORDER FLOW PREDICT DIRECTION? (ignoring approach)")
print("=" * 78)
print(f"{'feature':>34} | {'P(up)':>8} | {'P(up) without':>13} | {'diff':>8} | {'n':>6}")
for lab, m in cands:
    r = test(m.values if hasattr(m, 'values') else m, lab, col='outcome_up')
    if r:
        a, b_, n = r
        print(f"{lab:>34} | {a:>7.2%} | {b_:>12.2%} | {a-b_:>+7.2%} | {n:>6}")

print("\n" + "=" * 78)
print("CLUSTERED CONFIDENCE INTERVALS (session bootstrap)")
print("=" * 78)
ucl = np.unique(e.date.values)
idx = {u: np.where(e.date.values == u)[0] for u in ucl}
print(f"{'feature':>34} | {'diff':>8} | {'95% CI':>18}")
for lab, m in cands:
    m = np.asarray(m.values if hasattr(m, 'values') else m, bool)
    if m.sum() < 40 or (~m).sum() < 40:
        continue
    diff = e.reversal.values[m].mean() - e.reversal.values[~m].mean()
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        mm = m[ii]
        if mm.sum() > 5 and (~mm).sum() > 5:
            boot.append(e.reversal.values[ii][mm].mean() - e.reversal.values[ii][~mm].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{lab:>34} | {diff:>+7.2%} | [{lo:>+7.2%},{hi:>+7.2%}] {sig}")

e.to_csv('results/orderflow_events.csv', index=False)
print(f"\nwrote results/orderflow_events.csv  ({len(e):,} events)")
