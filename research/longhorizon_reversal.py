"""
Does price reverse at an LVN when you give it time?

The short-horizon study used a ~5pt barrier and 60 minutes. If the reversal the
user sees on a chart is a 50-200pt move over the following day or two, that test
cannot see it.  This re-measures the SAME 528 zone entries with wider barriers
and multi-session horizons, and compares them against random control triggers
in the same sessions.

Event   : price reaches the zone midpoint (approach side from the prior 3 min).
Outcome : symmetric race from the midpoint -> which side is reached first,
          the entry side (REVERSAL) or the far side (CONTINUATION).
Control : random minutes, reversal side = opposite of the prior 3-min drift.
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
TICK = 0.25
W_PTS, F_RATIO, MIN_H = 50.0, 0.7, 4.0
rng = np.random.default_rng(41)

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
mb = mb.sort_values(['date', 'minute']).reset_index(drop=True)

day_of = mb.date.values
uniq, first_occ = np.unique(day_of, return_index=True)
day_start = dict(zip(uniq, first_occ))
day_end = dict(zip(uniq, list(first_occ[1:]) + [len(mb)]))
day_pos = {d: i for i, d in enumerate(uniq)}
days = list(uniq)
HI, LO, W = mb.hi.values, mb.lo.values, mb.vwap.values
print(f"sessions {len(days)} | minute bars {len(mb):,}")

# ---- zones (same definition as the v4 study) ----
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

# ---- reuse the exact event set from the v4 study ----
ev = pd.read_csv('results/lvn_entry_events_v4.csv')
ev = ev[ev.resolved == 1].reset_index(drop=True)
ev['event_date'] = pd.to_datetime(ev.event_date).dt.date
ev['zone_date'] = pd.to_datetime(ev.zone_date).dt.date
ev['age'] = [day_pos[e] - day_pos[z] for e, z in zip(ev.event_date, ev.zone_date)]
print(f"zone events {len(ev)} | age (sessions): median {ev.age.median():.0f} "
      f"max {ev.age.max()} | 3+ days old: {(ev.age >= 3).sum()}")

g0 = np.array([day_start[e] + int(i) for e, i in zip(ev.event_date, ev.i0)])
approach_up = (ev.approach.values == 'up')       # price was rising INTO the zone
mid = ((ev.zlo.values + ev.zhi.values) / 2.0)
ed_pos = np.array([day_pos[e] for e in ev.event_date])


def race(gidx, dpos, mids, up_in, barrier, nsess):
    """which barrier is first touched: entry side (reversal) or far side."""
    out = []
    for g, dp, m, up in zip(gidx, dpos, mids, up_in):
        end_day = min(dp + nsess, len(days) - 1)
        g_end = day_end[days[end_day]] if end_day > dp else day_end[days[dp]]
        h = HI[g + 1:g_end]
        l = LO[g + 1:g_end]
        if len(h) == 0:
            out.append(np.nan)
            continue
        up_bar = m + barrier
        dn_bar = m - barrier
        uh = h >= up_bar
        dh = l <= dn_bar
        a = np.argmax(uh) if uh.any() else 10 ** 9
        b = np.argmax(dh) if dh.any() else 10 ** 9
        if a == b:
            out.append(np.nan)
            continue
        first = 'up' if a < b else 'down'
        # entering from below (rising) -> reversal means going back down
        rev = (first == 'down') if up else (first == 'up')
        out.append(1 if rev else 0)
    return np.array(out, dtype=float)


# ---- control triggers ----
ctrl = []
for dp, d in enumerate(days):
    if dp == 0:
        continue
    n_from = day_start[d]
    n_to = day_end[d]
    n = n_to - n_from
    if n < 30:
        continue
    for _ in range(20):
        k = int(rng.integers(3, n - 2))
        g = n_from + k
        prev = W[g - 3:g].mean()
        if W[g] > prev:
            up = True
        elif W[g] < prev:
            up = False
        else:
            continue
        ctrl.append((g, dp, W[g], up))
ctrl = pd.DataFrame(ctrl, columns=['g', 'dp', 'mid', 'up'])

BARRIERS = [25, 50, 100, 200]
HORIZONS = [1, 2, 3, 5]

print("\n=== reversal rate: LVN zone entries vs random control ===")
print("(symmetric barrier from the entry midpoint; reversal = price returns to the")
print(" side it came from before travelling the same distance the other way)")
print(f"{'barrier':>8} | {'sessions':>9} | {'zones':>18} | {'control':>18} | {'diff':>8}")
print(f"{'':>8} | {'ahead':>9} | {'n':>6} {'rev':>11} | {'n':>6} {'rev':>11} | ")
for B in BARRIERS:
    for K in HORIZONS:
        z = race(g0, ed_pos, mid, approach_up, B, K)
        zn = np.sum(~np.isnan(z))
        zr = np.nanmean(z) if zn else np.nan
        c = race(ctrl.g.values, ctrl.dp.values, ctrl.mid.values, ctrl.up.values, B, K)
        cn = np.sum(~np.isnan(c))
        cr = np.nanmean(c) if cn else np.nan
        print(f"{B:>8} | {K:>9} | {zn:>6} {zr:>11.2%} | {cn:>6} {cr:>11.2%} | {zr-cr:>+8.2%}")

print("\n=== does zone AGE matter? (barrier 100, 3 sessions) ===")
B, K = 100, 3
z = race(g0, ed_pos, mid, approach_up, B, K)
ev2 = ev.copy()
ev2['z'] = z
for lo, hi, lab in [(1, 1, '1 session old'), (2, 3, '2-3 sessions'), (4, 10, '4-10 sessions'),
                    (11, 999, '11+ sessions')]:
    sel = (ev2.age >= lo) & (ev2.age <= hi) & ev2.z.notna()
    if sel.sum() < 10:
        print(f"  {lab:>15}: n={int(sel.sum())} too few")
        continue
    print(f"  {lab:>15}: n={int(sel.sum()):>4}  reversal {ev2.z[sel].mean():.2%}")
