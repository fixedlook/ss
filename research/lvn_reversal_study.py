"""
Reversal concept study on the CALIBRATED LVN zones.  v4

Fixes:
  * the footprint file orders rows by PRICE within each minute, so `.last()`
    was not the minute's close.  We now use the minute's VWAP as the price
    proxy (and say so as a limitation: true intrabar path is unavailable).
  * approach side is judged from the previous 3 minutes' VWAPs, not one bar.
  * added a CONTROL group: random minutes that are not inside any live zone,
    measured with the identical barrier rule.

Event  : price reaches the zone MIDPOINT -> the near-edge and far-edge barriers
         are equidistant, so there is no momentum bias.
Outcome: over the next 60 minutes, whichever barrier is touched first.
         reversal = the barrier on the side price came from.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
HORIZON = 60
EDGE_BUFFER = 2.0
N_BOOT = 1000
N_CONTROL = 4000
rng = np.random.default_rng(23)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fp['pv'] = fp.price * fp.volume

mb = fp.groupby(['date', 'minute']).agg(
    hi=('price', 'max'), lo=('price', 'min'), pv=('pv', 'sum'),
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum'),
).reset_index()
mb['vwap'] = mb.pv / mb.vol
mb['delta'] = mb.ask - mb.bid
mb['rng'] = mb.hi - mb.lo
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)

days = sorted(mb.date.unique())
gday = {d: s.reset_index(drop=True) for d, s in mb.groupby('date')}
srng = {d: float(gday[d].hi.max() - gday[d].lo.min()) for d in days}
print(f"minute bars {len(mb):,} | sessions {len(days)}")

# ---------------- zones ----------------
win = int(round(W_PTS / TICK / 2)) * 2 + 1
zones = {}
for d in days:
    v = fp[fp.date == d].groupby('price').volume.sum().sort_index()
    med = pd.Series(v.values).rolling(win, center=True, min_periods=win // 3).median().values
    mask = (v.values <= F_RATIO * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, m in zip(v.index.values, mask):
        if m and s is None:
            s = p
        elif not m and s is not None:
            if prev - s >= MIN_H:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= MIN_H:
        out.append((s, prev))
    zones[d] = out
print(f"zones {sum(len(zones[d]) for d in days)}")

FEATS = ['vol_ratio', 'rng_ratio', 'absdelta_ratio', 'approach_speed', 'two_way',
         'with_trend', 'against_trend']


def features(b, i0, med_v, med_r, med_ad, sr):
    d_ = b.delta.values
    v_ = b.vol.values
    r_ = b.rng.values
    w_ = b.vwap.values
    sgn = int(np.sign(d_[i0]))
    pm = w_[i0-1] - w_[max(0, i0 - 11)]
    return dict(
        vol_ratio=v_[i0] / med_v if med_v else np.nan,
        rng_ratio=r_[i0] / med_r if med_r else np.nan,
        absdelta_ratio=abs(d_[i0]) / med_ad if med_ad else np.nan,
        prev_move=pm,
        approach_speed=abs(pm) / sr,
        two_way=bool(b.bid.values[i0] > 0.35 * v_[i0] and b.ask.values[i0] > 0.35 * v_[i0]),
        with_trend=bool(np.sign(pm) == sgn and sgn != 0),
        against_trend=bool(np.sign(pm) != sgn and sgn != 0),
    )


def stats(b):
    v_ = b.vol.values
    return (np.median(v_), np.median(b.rng.values), np.median(np.abs(b.delta.values)))


CTRL_B = float(np.median([(h - l) / 2 + EDGE_BUFFER for d in days for l, h in zones[d]]))
print(f"control barrier distance = {CTRL_B:.2f} pts (matches zone half-height + buffer)")

rows = []
control = []
for zi, zd in enumerate(days):
    if not zones[zd]:
        continue
    live = list(zones[zd])
    for ed in days[zi + 1:]:
        if not live:
            break
        b = gday[ed]
        H, L, W = b.hi.values, b.lo.values, b.vwap.values
        n = len(b)
        med_v, med_r, med_ad = stats(b)
        sr = srng[ed]
        still = []
        for (zlo, zhi) in live:
            mid = 0.5 * (zlo + zhi)
            hit = np.where((L <= mid) & (H >= mid))[0]
            if len(hit) == 0:
                still.append((zlo, zhi))
                continue
            i0 = int(hit[0])
            if i0 < 3 or i0 >= n - 2:
                still.append((zlo, zhi))
                continue
            prev = W[i0 - 3:i0].mean()
            approach = 'down' if prev > zhi else ('up' if prev < zlo else None)
            if approach is None:
                still.append((zlo, zhi))
                continue
            up_bar, dn_bar = zhi + EDGE_BUFFER, zlo - EDGE_BUFFER
            outcome = None
            for kk in range(i0 + 1, min(n, i0 + HORIZON)):
                hu, hd = H[kk] >= up_bar, L[kk] <= dn_bar
                if hu and hd:
                    continue
                if hu:
                    outcome = 'up'
                    break
                if hd:
                    outcome = 'down'
                    break
            rev = None
            if outcome == 'up':
                rev = 1 if approach == 'down' else 0
            elif outcome == 'down':
                rev = 1 if approach == 'up' else 0
            f = features(b, i0, med_v, med_r, med_ad, sr)
            f.update(event_date=str(ed), zone_date=str(zd), approach=approach,
                     outcome=outcome, rev=rev, resolved=int(rev is not None),
                     zone_h=zhi - zlo, i0=i0, zlo=zlo, zhi=zhi)
            rows.append(f)
            # control: same session, a random minute not inside any live zone
            cand = [k for k in range(3, n - 2)]
            if cand:
                k = int(rng.choice(cand))
                inz = any(l <= W[k] <= h for l, h in live)
                if not inz:
                    mid_c = W[k]
                    app = 'down' if W[max(0, k - 3):k].mean() > mid_c else 'up'
                    b_up, b_dn = mid_c + CTRL_B, mid_c - CTRL_B
                    oc = None
                    for kk in range(k + 1, min(n, k + HORIZON)):
                        hu, hd = H[kk] >= b_up, L[kk] <= b_dn
                        if hu and hd:
                            continue
                        if hu:
                            oc = 'up'
                            break
                        if hd:
                            oc = 'down'
                            break
                    rv = None
                    if oc == 'up':
                        rv = 1 if app == 'down' else 0
                    elif oc == 'down':
                        rv = 1 if app == 'up' else 0
                    control.append(dict(event_date=str(ed), rev=rv,
                                        resolved=int(rv is not None)))
        live = still

ev = pd.DataFrame(rows)
ctl = pd.DataFrame(control)
ev.to_csv('results/lvn_entry_events_v4.csv', index=False)

e = ev[ev.resolved == 1].reset_index(drop=True)
cc = ctl[ctl.resolved == 1].reset_index(drop=True)
print(f"zone entries {len(ev):,} | resolved {len(e):,} | base reversal {e.rev.mean():.2%}")
print(f"control minutes {len(cc):,} | base reversal {cc.rev.mean():.2%}")
print(f"zone minus control: {e.rev.mean()-cc.rev.mean():+.2%}")

CONCEPTS = {
    'volume_climax':       e.vol_ratio > 2.0,
    'volume_dry_up':       e.vol_ratio < 0.5,
    'small_range':         e.rng_ratio < 0.5,
    'big_range':           e.rng_ratio > 1.5,
    'absorption':          (e.absdelta_ratio > 1.5) & (e.rng_ratio < 0.8),
    'low_abs_delta':       e.absdelta_ratio < 0.5,
    'delta_with_trend':    e.with_trend,
    'delta_against_trend': e.against_trend,
    'two_way_tape':        e.two_way,
    'fast_approach':       e.approach_speed > e.approach_speed.median(),
}
print("\n=== concept vs reversal at LVN zones ===")
print(f"{'concept':>20} | {'n':>5} | {'with':>7} | {'without':>7} | {'diff':>8} | {'95% CI':>18}")
ucl = np.unique(e.event_date.values)
idx = {u: np.where(e.event_date.values == u)[0] for u in ucl}
out = []
for name, m in CONCEPTS.items():
    m = m.values.astype(bool)
    if m.sum() < 25 or (~m).sum() < 25:
        print(f"{name:>20} | {int(m.sum()):>5} | too few")
        continue
    with_, without = e.rev.values[m].mean(), e.rev.values[~m].mean()
    diff = with_ - without
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), replace=True)
        ii = np.concatenate([idx[u] for u in pick])
        mm = m[ii]
        if mm.sum() > 2 and (~mm).sum() > 2:
            boot.append(e.rev.values[ii][mm].mean() - e.rev.values[ii][~mm].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{name:>20} | {int(m.sum()):>5} | {with_:>7.2%} | {without:>7.2%} | "
          f"{diff:>+8.2%} | [{lo:>+7.2%},{hi:>+7.2%}] {sig}")
    out.append(dict(concept=name, n=int(m.sum()), with_=with_, without=without,
                    diff=diff, ci_lo=lo, ci_hi=hi, sig=sig))
print("\n=== in-sample vs out-of-sample (split by event date) ===")
dates_sorted = np.sort(np.unique(e.event_date.values))
half = dates_sorted[len(dates_sorted)//2]
e2 = e.copy()
e2['half'] = np.where(e2.event_date.values < half, 'IS', 'OOS')
print(f"split at {half}")
print(f"{'concept':>20} | {'IS diff':>9} | {'OOS diff':>9} | {'IS n':>5} | {'OOS n':>6} | consistent?")
for name, m in CONCEPTS.items():
    m = m.values.astype(bool)
    r = []
    for h in ['IS','OOS']:
        sel = (e2.half.values == h)
        mm = m & sel
        if mm.sum() < 10 or (~mm & sel).sum() < 10:
            r.append((np.nan, int(mm.sum()))); continue
        r.append((e2.rev.values[mm].mean() - e2.rev.values[~mm & sel].mean(), int(mm.sum())))
    d1,d2 = r[0][0], r[1][0]
    ok = 'yes' if (not np.isnan(d1) and not np.isnan(d2) and np.sign(d1)==np.sign(d2)) else 'NO'
    print(f"{name:>20} | {d1:>+9.2%} | {d2:>+9.2%} | {r[0][1]:>5} | {r[1][1]:>6} | {ok}")

pd.DataFrame(out).to_csv('results/concept_comparison_lvn_v4.csv', index=False)
print("\nwrote results/lvn_entry_events_v4.csv + results/concept_comparison_lvn_v4.csv")


# ---------------- sensitivity: barrier width x horizon ----------------
print("\n=== sensitivity of the delta-divergence effect ===")
print("(event = midpoint touch, unchanged. only the exit rule varies.)")
print(f"{'buffer':>7} | " + " | ".join(f"h={h:<4}" for h in [30, 60, 120]))
for buf in [1.0, 2.0, 4.0, 8.0]:
    cells = []
    for hz in [30, 60, 120]:
        recs = []
        for r in rows:
            b = gday[pd.Timestamp(r['event_date']).date()]
            H, L = b.hi.values, b.lo.values
            i0, zlo, zhi = r['i0'], r['zlo'], r['zhi']
            up_bar, dn_bar = zhi + buf, zlo - buf
            oc = None
            for kk in range(i0 + 1, min(len(b), i0 + hz)):
                hu, hd = H[kk] >= up_bar, L[kk] <= dn_bar
                if hu and hd:
                    continue
                if hu:
                    oc = 'up'
                    break
                if hd:
                    oc = 'down'
                    break
            if oc is None:
                continue
            rev = 1 if ((oc == 'up' and r['approach'] == 'down') or
                        (oc == 'down' and r['approach'] == 'up')) else 0
            recs.append((r['against_trend'], rev))
        arr = np.array(recs, dtype=float)
        if len(arr) < 30:
            cells.append('  n/a  ')
            continue
        m = arr[:, 0].astype(bool)
        if m.sum() < 8 or (~m).sum() < 8:
            cells.append(' few   ')
            continue
        d = arr[m, 1].mean() - arr[~m, 1].mean()
        cells.append(f"{d:>+6.1%}")
    print(f"{buf:>6.0f}p | " + " | ".join(cells))
print("\n(cell = reversal rate with divergence minus without; positive = divergence helps)")
print("n with divergence ranges 60-100 depending on how many resolve in time.")
