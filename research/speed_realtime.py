"""
REAL-TIME APPROACH SPEED - no hindsight.

The earlier finding used a zigzag, which only confirms a pivot AFTER price has
moved away. Not tradeable. This rebuilds it so every input is known at the
moment of the event:

  event    : price crosses the midpoint of an LVN box (or another level),
             which is observable in real time
  speed    : |close[i] - close[i-N]| / N  over the N minutes BEFORE the event
  normalise: percentile of that speed among all N-minute windows so far
             THIS session  -> self-normalising, no cross-session volatility bias
  race     : from the event close, +/- 100 pts, whichever is hit first,
             until session end
             reversal     = the side price came from
             continuation = through the level

Everything is computable at the bar close. No future information.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
SPEED_WIN = 10
BARRIER = 100.0
TOL = 5.0
TICK = 0.25
WIN = int(round(50.0 / TICK / 2)) * 2 + 1
rng = np.random.default_rng(55)

bars = pd.read_csv(BARS, header=None,
                   names=['ts', 'open', 'high', 'low', 'close', 'volume'])
bars['ts'] = pd.to_datetime(bars.ts.astype(str), format='%Y%m%d %H%M%S')
bars['dt'] = bars.ts.dt.tz_localize('Europe/Rome').dt.tz_convert('America/New_York')
bars['min_od'] = bars.dt.dt.hour * 60 + bars.dt.dt.minute - 570
bars['date'] = bars.dt.dt.date
bars = bars.sort_values(['date', 'min_od']).reset_index(drop=True)
gday = {d: g.reset_index(drop=True) for d, g in bars.groupby('date')}
days = [d for d in sorted(gday) if len(gday[d]) >= 200]
print(f"sessions {len(days):,}")

# ---- prior-session levels ----
prof = {}
for d in days:
    g = gday[d]
    lo = np.floor(g.low.min() / TICK) * TICK
    hi = np.ceil(g.high.max() / TICK) * TICK
    grid = np.arange(lo, hi + TICK, TICK)
    dens = np.zeros(len(grid))
    a = np.searchsorted(grid, g.low.values, 'left')
    b = np.searchsorted(grid, g.high.values, 'right')
    for x, y, v in zip(a, b, g.volume.values):
        if y > x:
            dens[x:y] += v / (y - x)
    prof[d] = pd.Series(dens, index=grid)

LV = {}
for i, d in enumerate(days):
    if i == 0:
        LV[d] = None
        continue
    v = prof[days[i - 1]]
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    mask = (v.values <= 0.7 * med) & ~np.isnan(med)
    out, s, prev = [], None, None
    for p, m in zip(v.index.values, mask):
        if m and s is None:
            s = p
        elif not m and s is not None:
            if prev - s >= 4.0:
                out.append((s, prev))
            s = None
        prev = p
    if s is not None and prev - s >= 4.0:
        out.append((s, prev))
    LV[d] = out
print("LVN boxes built")

SPEED_WINS = [5, 10, 20]


def speeds(C, n):
    """absolute speed over each window, for every bar, from prior data only"""
    out = {}
    for w in SPEED_WINS:
        s = np.full(n, np.nan)
        s[w:] = np.abs(C[w:] - C[:-w]) / w
        out[w] = s
    return out


def norm_pct(arr, i):
    """percentile of arr[i] among arr[:i] (expanding, no lookahead)"""
    past = arr[:i]
    past = past[~np.isnan(past)]
    if len(past) < 30 or np.isnan(arr[i]):
        return np.nan
    return float((past < arr[i]).mean())


def race(H, L, i, ref, approach_up):
    """+/-BARRIER from ref; returns 1 reversal, 0 continuation, nan unresolved"""
    n = len(H)
    up_b, dn_b = ref + BARRIER, ref - BARRIER
    for k in range(i + 1, n):
        hu, hd = H[k] >= up_b, L[k] <= dn_b
        if hu and hd:
            continue
        if hu:
            return 1 if approach_up is False else 0
        if hd:
            return 1 if approach_up is True else 0
    return np.nan


recs = []
for d in days:
    b = gday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    n = len(b)
    if n < 60:
        continue
    sp = speeds(C, n)
    Sp = {w: np.full(n, np.nan) for w in SPEED_WINS}
    for w in SPEED_WINS:
        for i in range(w + 30, n):
            Sp[w][i] = norm_pct(sp[w], i)

    # ---- event: cross the MIDPOINT of an LVN box ----
    boxes = LV[d] or []
    for (zl, zh) in boxes:
        mid = 0.5 * (zl + zh)
        for i in range(SPEED_WIN + 31, n - 30):
            crossed = (H[i] >= mid >= L[i]) and not (H[i - 1] >= mid >= L[i - 1])
            if not crossed:
                continue
            approach_up = bool(C[i - 1] < mid)
            r = race(H, L, i, C[i], approach_up)
            if np.isnan(r):
                continue
            recs.append(dict(date=str(d), i=i, kind='LVN', rev=int(r),
                             sp5=Sp[5][i], sp10=Sp[10][i], sp20=Sp[20][i],
                             mod=int(b.min_od.values[i]),
                             pts_covered=abs(C[i] - C[i - SPEED_WIN])))
            break

    # ---- control: random bars, same construction ----
    for j in rng.choice(np.arange(SPEED_WIN + 31, n - 30), size=min(6, n - 61), replace=False):
        j = int(j)
        if np.isnan(Sp[10][j]):
            continue
        # avoid counting a bar inside a box
        ref = C[j]
        if any(zl - TOL <= ref <= zh + TOL for zl, zh in boxes):
            continue
        approach_up = bool(C[j - 1] < ref)
        r = race(H, L, j, ref, approach_up)
        if np.isnan(r):
            continue
        recs.append(dict(date=str(d), i=j, kind='NOWHERE', rev=int(r),
                         sp5=Sp[5][j], sp10=Sp[10][j], sp20=Sp[20][j],
                         mod=int(b.min_od.values[j]),
                         pts_covered=abs(C[j] - C[j - SPEED_WIN])))

e = pd.DataFrame(recs)
e = e[e.sp10.notna()].reset_index(drop=True)
print(f"\nevents {len(e):,}")
print(e.kind.value_counts().to_string())
print(f"overall reversal rate {e.rev.mean():.2%}  (symmetric 100pt race -> expect 50%)")

print("\n" + "=" * 78)
print("2x2: APPROACH SPEED x LOCATION")
print("=" * 78)
e['fast'] = e.sp10 >= 0.90          # top decile within its own session
print(f"{'':>16} | {'n':>7} | {'reversal':>10} | {'vs 50%':>8}")
for kind in ['LVN', 'NOWHERE']:
    for f in [True, False]:
        s = e[(e.kind == kind) & (e.fast == f)]
        lab = f"{kind} / {'FAST' if f else 'normal'}"
        print(f"{lab:>16} | {len(s):>7} | {s.rev.mean():>9.2%} | {s.rev.mean()-0.5:>+8.2%}")

print("\n" + "=" * 78)
print("THE QUESTION: does the box help when speed is present?")
print("=" * 78)
lv_f = e[(e.kind == 'LVN') & e.fast]
lv_s = e[(e.kind == 'LVN') & ~e.fast]
nw_f = e[(e.kind == 'NOWHERE') & e.fast]
nw_s = e[(e.kind == 'NOWHERE') & ~e.fast]
print(f"  fast @ box      : {lv_f.rev.mean():.2%}  (n={len(lv_f):,})")
print(f"  fast elsewhere  : {nw_f.rev.mean():.2%}  (n={len(nw_f):,})")
print(f"  slow @ box      : {lv_s.rev.mean():.2%}  (n={len(lv_s):,})")
print(f"  slow elsewhere  : {nw_s.rev.mean():.2%}  (n={len(nw_s):,})")
print(f"\n  box effect, given fast   : {lv_f.rev.mean()-nw_f.rev.mean():+.2%}")
print(f"  speed effect, given box  : {lv_f.rev.mean()-lv_s.rev.mean():+.2%}")
print(f"  speed effect, elsewhere  : {nw_f.rev.mean()-nw_s.rev.mean():+.2%}")

print("\n" + "=" * 78)
print("CLUSTERED CI on each cell (session bootstrap)")
print("=" * 78)
ucl = np.unique(e.date.values)
idx = {u: np.where(e.date.values == u)[0] for u in ucl}
for lab, mask in [('fast @ box', ((e.kind == 'LVN') & e.fast).values),
                  ('fast elsewhere', ((e.kind == 'NOWHERE') & e.fast).values),
                  ('slow @ box', ((e.kind == 'LVN') & ~e.fast).values),
                  ('slow elsewhere', ((e.kind == 'NOWHERE') & ~e.fast).values)]:
    boot = []
    for _ in range(1500):
        pick = rng.choice(ucl, len(ucl), True)
        ii = np.concatenate([idx[u] for u in pick])
        mm = mask[ii]
        if mm.sum() > 10:
            boot.append(e.rev.values[ii][mm].mean())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"  {lab:>16}: {e.rev.values[mask].mean():>6.2%}  95% CI [{lo:>6.2%}, {hi:>6.2%}]")

print("\n" + "=" * 78)
print("DOES SPEED ALONE WORK? (all events)")
print("=" * 78)
print(f"{'speed percentile':>18} | {'n':>7} | {'reversal':>10}")
for lo_, hi_, lab in [(0, .5, 'bottom 50%'), (.5, .75, '50-75th'),
                      (.75, .9, '75-90th'), (.9, .95, '90-95th'), (.95, 1.01, 'top 5%')]:
    s = e[(e.sp10 >= lo_) & (e.sp10 < hi_)]
    if len(s) < 50:
        continue
    print(f"{lab:>18} | {len(s):>7} | {s.rev.mean():>9.2%}")

print("\n" + "=" * 78)
print("TRIGGER FREQUENCY")
print("=" * 78)
ns = e.date.nunique()
print(f"  sessions                  : {ns:,}")
print(f"  fast LVN entries / session: {len(lv_f)/ns:.2f}")
print(f"  fast events / session     : {len(nw_f)/ns:.2f}  (elsewhere)")
print(f"  all LVN entries / session : {len(e[e.kind=='LVN'])/ns:.2f}")

print("\n" + "=" * 78)
print("ERA STABILITY")
print("=" * 78)
e['year'] = pd.to_datetime(e.date).dt.year
print(f"{'era':>12} | {'fast@box':>18} | {'fast elsewhere':>18}")
for lab, s in [('2019-2021', e[e.year <= 2021]), ('2022-2023', e[(e.year > 2021) & (e.year <= 2023)]),
               ('2024-2025', e[(e.year > 2023) & (e.year <= 2025)]), ('2026', e[e.year == 2026])]:
    a = s[(s.kind == 'LVN') & s.fast]
    b_ = s[(s.kind == 'NOWHERE') & s.fast]
    fa = f"{a.rev.mean():.1%} n={len(a):>5}" if len(a) > 20 else '--'
    fb = f"{b_.rev.mean():.1%} n={len(b_):>5}" if len(b_) > 20 else '--'
    print(f"{lab:>12} | {fa:>18} | {fb:>18}")

e.to_csv('results/speed_realtime.csv', index=False)
print("\nwrote results/speed_realtime.csv")
