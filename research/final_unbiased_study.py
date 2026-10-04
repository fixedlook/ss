"""
FINAL unbiased study: every order-flow concept, measured correctly.

Design (no reference price that can be slanted by the signal):
  zones    : f=0.7 x median of +/-50pt window, min 4pts, NEVER retired (user rule)
  trigger  : first fresh arrival each session - the minute's CLOSE is inside the
             zone and the previous CLOSE was outside
  reference: that observed close
  barriers : reference +/- D, symmetric about an observed traded price
  race     : from the next minute, 60-minute horizon
  reversal : the barrier on the side price arrived from is touched first
  control  : identical design at random minutes, no zone filter, same D

D is swept so no single choice drives the answer.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
HORIZON = 60
N_BOOT = 1000
rng = np.random.default_rng(2025)

fp = pd.read_parquet(FP)
fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
fp['volume'] = fp.bid_volume + fp.ask_volume
fp = fp[fp.volume > 0].copy()
fp['date'] = fp.dt.dt.date
fp['minute'] = fp.dt.dt.floor('min')
fm = fp.groupby(['date', 'minute']).agg(
    vol=('volume', 'sum'), bid=('bid_volume', 'sum'), ask=('ask_volume', 'sum')).reset_index()
fm['delta'] = fm.ask - fm.bid

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
print(f"bars {len(m):,} | sessions {len(days)}")

win = int(round(W_PTS / TICK / 2)) * 2 + 1
zones = {}
for d in days:
    v = fp[fp.date == d].groupby('price').volume.sum().sort_index()
    med = pd.Series(v.values).rolling(win, center=True, min_periods=win // 3).median().values
    mask = (v.values <= F_RATIO * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, mm in zip(v.index.values, mask):
        if mm and s is None:
            s = p
        elif not mm and s is not None:
            if prev - s >= MIN_H:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= MIN_H:
        out.append((s, prev))
    zones[d] = out
zlist = [(d, lo, hi) for d in days for lo, hi in zones[d]]
zlo_a = np.array([z[1] for z in zlist])
zhi_a = np.array([z[2] for z in zlist])
zpos = np.array([days.index(z[0]) for z in zlist])
print(f"zones {len(zlist)}")

FEATS = ['vol_ratio', 'rng_ratio', 'absdelta_ratio', 'approach_speed', 'two_way']


def collect(rows, D):
    out = []
    for r in rows:
        d = r['d']
        b = gday[d]
        C, H, L = b.close.values, b.high.values, b.low.values
        n = len(b)
        g = r['g']
        if g < 11 or g + 1 >= n - 1:
            continue
        ref, up = r['ref'], r['up']
        oc = None
        for kk in range(g + 1, min(n, g + 1 + HORIZON)):
            hu, hd = H[kk] >= ref + D, L[kk] <= ref - D
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
        rev = 1 if ((oc == 'up' and not up) or (oc == 'down' and up)) else 0
        V = b.vol.values
        DEL = b.delta.values
        med_v = np.median(V)
        med_r = np.median(H - L)
        med_ad = np.median(np.abs(DEL))
        pm = C[g - 1] - C[g - 11]
        sgn = int(np.sign(DEL[g]))
        out.append(dict(
            event_date=str(d), rev=rev, zone_date=str(r.get('zd', '')),
            age=r.get('age', np.nan),
            vol_ratio=V[g] / med_v if med_v else np.nan,
            rng_ratio=(H[g] - L[g]) / med_r if med_r else np.nan,
            absdelta_ratio=abs(DEL[g]) / med_ad if med_ad else np.nan,
            approach_speed=abs(pm) / (H.max() - L.min()) if (H.max() - L.min()) else np.nan,
            with_trend=bool(np.sign(pm) == sgn and sgn != 0),
            against_trend=bool(np.sign(pm) != sgn and sgn != 0),
            zone_h=(r['hi'] - r['lo']) if 'hi' in r else np.nan,
        ))
    return pd.DataFrame(out)


# zone arrivals
zrows = []
for j in range(len(zlist)):
    lo, hi = zlo_a[j], zhi_a[j]
    for di in range(int(zpos[j]) + 1, len(days)):
        d = days[di]
        b = gday[d]
        C = b.close.values
        for k in range(1, len(b) - 1):
            if lo <= C[k] <= hi and not (lo <= C[k - 1] <= hi):
                zrows.append(dict(d=d, g=k, ref=C[k], up=bool(C[k - 1] < lo),
                                  zd=zlist[j][0], age=di - int(zpos[j]), lo=lo, hi=hi))
                break
crows = []
for d in days:
    b = gday[d]
    C = b.close.values
    n = len(b)
    if n < 80:
        continue
    for k in rng.choice(np.arange(11, n - 2), size=min(600, n - 13), replace=False):
        k = int(k)
        if C[k] == C[k - 11]:
            continue
        crows.append(dict(d=d, g=k, ref=C[k], up=bool(C[k] > C[k - 11])))
print(f"zone arrivals {len(zrows):,} | control {len(crows):,}")

print("\n=== barrier sweep: is there ANY edge at zones? ===")
print(f"{'D':>5} | {'zones n':>8} {'rev':>7} | {'control n':>9} {'rev':>7} | {'diff':>8}")
for D in [4, 6, 8, 12, 16, 25]:
    z = collect(zrows, D)
    c = collect(crows, D)
    print(f"{D:>5} | {len(z):>8} {z.rev.mean():>7.2%} | {len(c):>9} {c.rev.mean():>7.2%} | "
          f"{z.rev.mean()-c.rev.mean():>+8.2%}")

D = 8.0
z = collect(zrows, D)
c = collect(crows, D)
z.to_csv('results/final_zone_events.csv', index=False)
c.to_csv('results/final_control_events.csv', index=False)

CONCEPTS = {
    'delta_divergence': z.against_trend,
    'delta_with_trend': z.with_trend,
    'volume_climax': z.vol_ratio > 2.0,
    'volume_dry_up': z.vol_ratio < 0.5,
    'big_range': z.rng_ratio > 1.5,
    'small_range': z.rng_ratio < 0.5,
    'low_abs_delta': z.absdelta_ratio < 0.5,
    'high_abs_delta': z.absdelta_ratio > 2.0,
    'fast_approach': z.approach_speed > z.approach_speed.median(),
    'slow_approach': z.approach_speed <= z.approach_speed.median(),
}
print(f"\n=== concepts at LVN zones, unbiased (D={D}) ===")
print(f"{'concept':>18} | {'n':>5} | {'with':>7} | {'without':>7} | {'diff':>8} | {'95% CI':>18}")
ucl = np.unique(z.event_date.values)
idx = {u: np.where(z.event_date.values == u)[0] for u in ucl}
out = []
for name, mm in CONCEPTS.items():
    mm = mm.values.astype(bool)
    if mm.sum() < 25 or (~mm).sum() < 25:
        print(f"{name:>18} | {int(mm.sum()):>5} | too few")
        continue
    w_, wo = z.rev.values[mm].mean(), z.rev.values[~mm].mean()
    boot = []
    for _ in range(N_BOOT):
        pick = rng.choice(ucl, len(ucl), replace=True)
        ii = np.concatenate([idx[u] for u in pick])
        s = mm[ii]
        if s.sum() > 2 and (~s).sum() > 2:
            boot.append(z.rev.values[ii][s].mean() - z.rev.values[ii][~s].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{name:>18} | {int(mm.sum()):>5} | {w_:>7.2%} | {wo:>7.2%} | {w_-wo:>+8.2%} | "
          f"[{lo:>+7.2%},{hi:>+7.2%}] {sig}")
    out.append(dict(concept=name, n=int(mm.sum()), with_=w_, without=wo, diff=w_ - wo,
                    ci_lo=lo, ci_hi=hi, sig=sig))
pd.DataFrame(out).to_csv('results/final_concepts.csv', index=False)

print("\n=== same concepts at RANDOM PRICES (for comparison) ===")
print(f"{'concept':>18} | {'n':>6} | {'with':>7} | {'without':>7} | {'diff':>8}")
for name, mm in CONCEPTS.items():
    mm = mm.values.astype(bool)
    if name.startswith('delta'):
        cc = c.against_trend if 'against' in name else c.with_trend
    elif name == 'volume_climax':
        cc = c.vol_ratio > 2.0
    elif name == 'volume_dry_up':
        cc = c.vol_ratio < 0.5
    elif name == 'big_range':
        cc = c.rng_ratio > 1.5
    elif name == 'small_range':
        cc = c.rng_ratio < 0.5
    elif name == 'low_abs_delta':
        cc = c.absdelta_ratio < 0.5
    elif name == 'high_abs_delta':
        cc = c.absdelta_ratio > 2.0
    elif name == 'fast_approach':
        cc = c.approach_speed > c.approach_speed.median()
    else:
        cc = c.approach_speed <= c.approach_speed.median()
    cc = cc.values.astype(bool)
    if cc.sum() < 25 or (~cc).sum() < 25:
        continue
    print(f"{name:>18} | {int(cc.sum()):>6} | {c.rev.values[cc].mean():>7.2%} | "
          f"{c.rev.values[~cc].mean():>7.2%} | {c.rev.values[cc].mean()-c.rev.values[~cc].mean():>+8.2%}")

print("\n=== zone age (never retired) ===")
z['age_b'] = pd.cut(z.age, [0, 1, 3, 10, 30, 1000],
                    labels=['1 sess', '2-3', '4-10', '11-30', '31+'])
print(z.groupby('age_b', observed=True).agg(n=('rev', 'size'), reversal=('rev', 'mean')).to_string())
print("\nwrote results/final_*.csv")
