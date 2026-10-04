"""
ORDER FLOW AT THE LVN BOXES - the user's actual thesis.

"the boxes should only be a help to know when to trust the big trades and not
just use it blindly"

So: does order flow predict a reversal better AT a box than away from one?

Boxes are built from the FOOTPRINT itself (the calibrated definition:
volume <= 0.7x the median of a +/-50pt window, min 4pt) - the highest quality
zone source available, not a bar approximation.

Event  : bar close crosses a box midpoint (real time, observable)
Control: the same order-flow features sampled at non-box minutes
Outcome: symmetric +/-100pt race from the true close; reversal = came-back side
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
BARRIER = 100.0
TOL = 5.0
TICK = 0.25
WIN = int(round(50.0 / TICK / 2)) * 2 + 1
N_BOOT = 2000
STEP = 10
rng = np.random.default_rng(31337)

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

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['minute'] = bars.dt.dt.floor('min')
bars['date'] = bars.dt.dt.date
bars = bars[['date', 'minute', 'high', 'low', 'close']]

m = bars.merge(fm, on=['date', 'minute'], how='inner').sort_values(['date', 'minute'])
m = m.reset_index(drop=True)
days = sorted(m.date.unique())
gday = {d: g.reset_index(drop=True) for d, g in m.groupby('date')}
print(f"sessions {len(days)}")

# ---- footprint LVN boxes (calibrated definition) ----
LV = {}
for i, d in enumerate(days):
    if i == 0:
        LV[d] = []
        continue
    prev = days[i - 1]
    g = fp[fp.date == prev]
    v = g.groupby('price').volume.sum().sort_index()
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    mask = (v.values <= 0.7 * med) & ~np.isnan(med)
    out, s, p_ = [], None, None
    for p, mm in zip(v.index.values, mask):
        if mm and s is None:
            s = p
        elif not mm and s is not None:
            if p_ - s >= 4.0:
                out.append((s, p_))
            s = None
        p_ = p
    if s is not None and p_ - s >= 4.0:
        out.append((s, p_))
    LV[d] = out
print(f"total boxes {sum(len(LV[d]) for d in days)}")


def epct(arr, i, min_n=25):
    past = arr[:i]
    if len(past) < min_n or not np.isfinite(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


def race(H, L, i, ref):
    uh = H[i + 1:] >= ref + BARRIER
    dh = L[i + 1:] <= ref - BARRIER
    a = np.argmax(uh) if uh.any() else 10 ** 9
    b = np.argmax(dh) if dh.any() else 10 ** 9
    if a == b:
        return None
    return a < b


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
    med_ad = pd.Series(np.abs(D)).expanding(20).median().values
    med_r = pd.Series(H - L).expanding(20).median().values

    boxes = LV[d] or []
    in_box = np.zeros(n, bool)
    for zl, zh in boxes:
        in_box |= (C >= zl - TOL) & (C <= zh + TOL)

    # ---- box-entry events ----
    for zl, zh in boxes:
        mid = 0.5 * (zl + zh)
        for i in range(40, n - 15):
            crossed = (H[i] >= mid >= L[i]) and not (H[i - 1] >= mid >= L[i - 1])
            if not crossed:
                continue
            up = race(H, L, i, C[i])
            if up is None:
                break
            approach_up = C[i - 1] < mid
            pslope = C[i] - C[i - 10]
            cvd_slope = cvd[i] - cvd[i - 10]
            rows.append(dict(
                date=str(d), i=i, at_box=1,
                reversal=int(up != approach_up), outcome_up=int(up), mod=int(i),
                cvd_div=int(cvd_slope != 0 and pslope != 0 and
                            np.sign(cvd_slope) != np.sign(pslope)),
                absorption=int(abs(D[i]) > 1.5 * med_ad[i] and (H[i] - L[i]) < 0.7 * med_r[i])
                if med_ad[i] and med_r[i] else 0,
                vol_pct=epct(V, i), trades_pct=epct(TR, i),
                absdelta_pct=epct(np.abs(D), i),
                buy_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) > 0.9),
                sell_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) < 0.1),
            ))
            break

    # ---- control events: same sampling, not in a box ----
    for i in range(40, n - 15, STEP):
        if in_box[i]:
            continue
        up = race(H, L, i, C[i])
        if up is None:
            continue
        pslope = C[i] - C[i - 10]
        if pslope == 0:
            continue
        approach_up = pslope > 0
        cvd_slope = cvd[i] - cvd[i - 10]
        rows.append(dict(
            date=str(d), i=i, at_box=0,
            reversal=int(up != approach_up), outcome_up=int(up), mod=int(i),
            cvd_div=int(cvd_slope != 0 and np.sign(cvd_slope) != np.sign(pslope)),
            absorption=int(abs(D[i]) > 1.5 * med_ad[i] and (H[i] - L[i]) < 0.7 * med_r[i])
            if med_ad[i] and med_r[i] else 0,
            vol_pct=epct(V, i), trades_pct=epct(TR, i),
            absdelta_pct=epct(np.abs(D), i),
            buy_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) > 0.9),
            sell_extreme=int(np.isfinite(epct(BR, i)) and epct(BR, i) < 0.1),
        ))

e = pd.DataFrame(rows)
print(f"\nevents {len(e):,}   at boxes {int(e.at_box.sum()):,}   elsewhere {int((~e.at_box.astype(bool)).sum()):,}")
print(f"base reversal at boxes  {e[e.at_box==1].reversal.mean():.2%}")
print(f"base reversal elsewhere {e[e.at_box==0].reversal.mean():.2%}")
e.to_csv('results/orderflow_at_box.csv', index=False)

FEATS = ['cvd_div', 'absorption', 'buy_extreme', 'sell_extreme']
print("\n" + "=" * 84)
print("THE 2x2 :  order flow  x  location")
print("=" * 84)
print(f"{'':>26} | {'n':>5} | {'reversal':>9} | {'95% CI':>18}")
ucl = np.unique(e.date.values)
idx = {u: np.where(e.date.values == u)[0] for u in ucl}


def cell(mask, label):
    mask = np.asarray(mask, bool)
    if mask.sum() < 20:
        print(f"{label:>26} | {mask.sum():>5} | too few")
        return
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        s = mask[ii]
        if s.sum() > 3:
            boot.append(e.reversal.values[ii][s].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"{label:>26} | {mask.sum():>5} | {e.reversal.values[mask].mean():>8.2%} | "
          f"[{lo:>+7.2%},{hi:>+7.2%}]")


for f in FEATS:
    on = (e[f] == 1).values
    print(f"--- {f} ---")
    cell(on & (e.at_box == 1).values, 'flow + BOX')
    cell(on & (e.at_box == 0).values, 'flow, no box')
    cell(~on & (e.at_box == 1).values, 'no flow + BOX')
    cell(~on & (e.at_box == 0).values, 'no flow, no box')
    a = e.reversal.values[on & (e.at_box == 1).values].mean() if (on & (e.at_box == 1).values).sum() > 5 else np.nan
    b_ = e.reversal.values[on & (e.at_box == 0).values].mean() if (on & (e.at_box == 0).values).sum() > 5 else np.nan
    if np.isfinite(a) and np.isfinite(b_):
        print(f"    box effect given flow : {a-b_:+.2%}")
    c = e.reversal.values[on & (e.at_box == 1).values].mean() if (on & (e.at_box == 1).values).sum() > 5 else np.nan
    dd = e.reversal.values[~on & (e.at_box == 1).values].mean()
    if np.isfinite(c):
        print(f"    flow effect given box : {c-dd:+.2%}")

print("\n" + "=" * 84)
print("flow x box interaction, directly")
print("=" * 84)
for f in FEATS:
    on = (e[f] == 1).values
    bx = (e.at_box == 1).values
    if on.sum() < 20 or (~on).sum() < 20:
        continue
    add = (e.reversal.values[on].mean() - e.reversal.values[~on].mean())
    atb = (e.reversal.values[bx].mean() - e.reversal.values[~bx].mean())
    both = (e.reversal.values[on & bx].mean() - e.reversal.values[~on & ~bx].mean()) if (on & bx).sum() > 10 else np.nan
    print(f"{f:>14}: flow alone {add:+.2%} | box alone {atb:+.2%} | both {both:+.2%}")
    if np.isfinite(both):
        print(f"{'':>14}  (additive would predict {add+atb:+.2%}, so interaction is {both-(add+atb):+.2%})")
print("\nwrote results/orderflow_at_box.csv")
