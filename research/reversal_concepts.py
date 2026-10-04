"""
Which order-flow condition most reliably precedes a REVERSAL at a zone?

Zones ("boxes") are built prior-only from volume-at-price profiles:
    LVN        low-volume node  (local volume minimum)
    SP         single print     (price traded in only one minute)
    HVN_EDGE   boundary of a high-volume node

For every entry into a zone we look at what order flow was doing at that
minute, then measure whether price reversed or continued using a symmetric
barrier (first passage): +K x ATR in the reversal direction before
-M x ATR in the continuation direction, within H bars.

Every concept is then compared against (a) zone entries WITHOUT that
condition, and (b) all non-zone minutes (the base rate). Inference is
clustered by session via bootstrap, because entries to the same zone in the
same session are not independent.

No look-ahead: zones use only prior sessions, and the outcome window starts
on the bar AFTER the entry bar.
"""

from __future__ import annotations

import os
import sys
import zipfile
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

TICK = 0.25
H_BARS = 30          # outcome horizon, minutes
K_ATR = 0.75         # barrier size in ATR, both directions (symmetric)
ATR_LEN = 14
W_LVN = 4            # +/- ticks for local-minimum test
R_LVN = 0.5          # level must be below this fraction of neighbourhood mean
HVN_MULT = 1.2       # level counts as HVN if above this x mean


# ----------------------------------------------------------------------
# data
# ----------------------------------------------------------------------
def load_minutes(fp_parquet: str, ohlc_zip: str) -> pd.DataFrame:
    """Merge footprint order flow with true OHLC, keyed by minute (ET)."""
    fp = pd.read_parquet(fp_parquet)
    fp['dt_et'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
    fp['mkey'] = fp.timestamp
    flow = fp.groupby('mkey').agg(volume=('bid_volume', 'sum'), bid_volume=('bid_volume', 'sum'),
                                  ask_volume=('ask_volume', 'sum'), delta=('delta', 'sum'),
                                  trades=('trades', 'sum')).reset_index()
    flow['volume'] = flow.bid_volume + flow.ask_volume

    with zipfile.ZipFile(ohlc_zip) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            o = pd.read_csv(f, header=None, names=['ts', 'open', 'high', 'low', 'close', 'volume'])
    o['dt'] = (pd.to_datetime(o.ts, format='%Y%m%d %H%M%S')
               .dt.tz_localize('Europe/Rome', ambiguous='NaT', nonexistent='NaT')
               .dt.tz_convert('America/New_York'))
    o = o.dropna(subset=['dt'])
    # resolution-independent epoch-ms (pandas 3 defaults to us, not ns)
    o['mkey'] = ((o.dt.dt.tz_convert('UTC') - pd.Timestamp('1970-01-01', tz='UTC'))
                 // pd.Timedelta('1ms')).astype('int64')
    o = o[['mkey', 'dt', 'open', 'high', 'low', 'close']]

    m = o.merge(flow, on='mkey', how='inner')
    m = m.sort_values('dt').reset_index(drop=True)
    m['date'] = m.dt.dt.date
    m['mday'] = m.dt.dt.hour * 60 + m.dt.dt.minute
    return m


def atr(df: pd.DataFrame, n: int = ATR_LEN) -> np.ndarray:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().values


# ----------------------------------------------------------------------
# zones (prior-only)
# ----------------------------------------------------------------------
def build_profile_matrices(fp: pd.DataFrame, sessions):
    """Volume-at-price matrices (session x price) straight from the footprint."""
    fp = fp[fp.date.isin(sessions)].copy()
    fp['volume'] = fp.bid_volume + fp.ask_volume
    fp = fp[fp.volume > 0]
    grid = np.round(np.sort(fp.price.unique()), 2)
    sidx = {d: i for i, d in enumerate(sessions)}
    pid = pd.Series(np.arange(len(grid)), index=grid)

    g = fp.groupby(['date', 'price']).volume.sum()
    rows = g.index.get_level_values(0).map(sidx).values
    cols = pid.reindex(g.index.get_level_values(1)).values
    vol_mat = np.zeros((len(sessions), len(grid)))
    np.add.at(vol_mat, (rows.astype(int), cols.astype(int)), g.values)

    n = fp.groupby(['date', 'price']).size()
    rows2 = n.index.get_level_values(0).map(sidx).values
    cols2 = pid.reindex(n.index.get_level_values(1)).values
    min_mat = np.zeros((len(sessions), len(grid)), dtype=np.int16)
    np.add.at(min_mat, (rows2.astype(int), cols2.astype(int)), n.values.astype(np.int16))
    return grid, vol_mat, min_mat


def lvn_mask(v: np.ndarray, w: int = W_LVN, ratio: float = R_LVN) -> np.ndarray:
    n = len(v)
    if v.sum() <= 0:
        return np.zeros(n, bool)
    pad = np.pad(v, (w, w), mode='edge')
    win = sliding_window_view(pad, 2 * w + 1)
    return (v <= win.min(axis=1)) & (v < ratio * win.mean(axis=1))


def hvn_edge_mask(v: np.ndarray, mult: float = HVN_MULT) -> np.ndarray:
    if v.sum() <= 0:
        return np.zeros(len(v), bool)
    hi = v >= mult * v.mean()
    edge = np.zeros(len(v), bool)
    d = np.diff(hi.astype(int))
    starts = np.where(d == 1)[0] + 1
    ends = np.where(d == -1)[0]
    if hi[0]:
        starts = np.r_[0, starts]
    if hi[-1]:
        ends = np.r_[ends, len(v) - 1]
    for s, e in zip(starts, ends):
        edge[s] = True
        edge[e] = True
    return edge


def mask_to_zones(mask: np.ndarray, grid: np.ndarray):
    out, s = [], None
    for i, x in enumerate(mask):
        if x and s is None:
            s = i
        elif not x and s is not None:
            out.append((grid[s], grid[i - 1], s, i - 1))
            s = None
    if s is not None:
        out.append((grid[s], grid[-1], s, len(mask) - 1))
    return out


def merge_zones(zones):
    """Merge overlapping zones, keeping type flags."""
    if not zones:
        return []
    zones = sorted(zones, key=lambda z: z[0])
    out = [dict(lo=zones[0][0], hi=zones[0][1],
                lvn=bool(zones[0][4]), sp=bool(zones[0][5]), hvn=bool(zones[0][6]))]
    for lo, hi, _, _, lvn, sp, hvn in zones[1:]:
        last = out[-1]
        if lo <= last["hi"] + TICK:
            last['hi'] = max(last['hi'], hi)
            last['lvn'] |= lvn
            last['sp'] |= sp
            last['hvn'] |= hvn
        else:
            out.append(dict(lo=lo, hi=hi, lvn=lvn, sp=sp, hvn=hvn))
    return out


# ----------------------------------------------------------------------
# outcomes
# ----------------------------------------------------------------------
def barrier_outcome(high, low, entry_idx, entry_px, up_lvl, dn_lvl, h=H_BARS):
    """+1 if up barrier hit first, -1 if down barrier hit first, 0 otherwise."""
    n = len(high)
    for k in range(1, h + 1):
        j = entry_idx + k
        if j >= n:
            return 0
        u, d = high[j] >= up_lvl, low[j] <= dn_lvl
        if u and d:
            return 0            # ambiguous bar - excluded
        if u:
            return 1
        if d:
            return -1
    return 0


# ----------------------------------------------------------------------
# concepts
# ----------------------------------------------------------------------
def concept_flags(s: pd.DataFrame) -> pd.DataFrame:
    """Order-flow state at every bar. All rolling stats use only past bars."""
    d = s['delta'].values.astype(float)
    v = s['volume'].values.astype(float)
    tr = s['trades'].values.astype(float)
    h, l, c = s['high'].values, s['low'].values, s['close'].values
    o = s['open'].values
    ats = np.divide(v, np.maximum(tr, 1))

    med_v = pd.Series(v).rolling(60, min_periods=20).median().values
    med_ats = pd.Series(ats).rolling(60, min_periods=20).median().values
    p70_d = pd.Series(np.abs(d)).rolling(60, min_periods=20).quantile(0.70).values
    p30_r = pd.Series(h - l).rolling(60, min_periods=20).quantile(0.30).values
    med_absd = pd.Series(np.abs(d)).rolling(60, min_periods=20).median().values

    rng = np.maximum(h - l, TICK)
    out = pd.DataFrame(index=s.index)
    out['delta'] = d
    out['vol_ratio'] = v / np.maximum(med_v, 1)
    out['ats_ratio'] = ats / np.maximum(med_ats, 1)
    out['absdelta_ratio'] = np.abs(d) / np.maximum(med_absd, 1)
    out['big_delta_small_range'] = (np.abs(d) > p70_d) & ((h - l) < p30_r)
    out['two_way'] = (np.abs(d) / np.maximum(v, 1)) < 0.15
    out['close_pos'] = (c - l) / rng                      # 1 = closed on high
    out['cvd'] = np.cumsum(d)
    out['delta_flip_up'] = (d > 0) & (np.r_[np.nan, d[:-1]] <= 0)
    out['delta_flip_dn'] = (d < 0) & (np.r_[np.nan, d[:-1]] >= 0)
    # new 10-bar extreme in price with non-confirming delta (divergence)
    roll_lo = pd.Series(l).rolling(10).min().values
    roll_hi = pd.Series(h).rolling(10).max().values
    out['new_low'] = l <= roll_lo + 1e-9
    out['new_high'] = h >= roll_hi - 1e-9
    out['delta_less_neg'] = d > np.r_[np.full(1, np.nan), d[:-1]]
    out['delta_less_pos'] = d < np.r_[np.full(1, np.nan), d[:-1]]
    return out


def main():
    fp_parquet = '/tmp/newfp/full_fp.parquet'
    ohlc_zip = 'MNQZ26 - 1 min - RTH.zip'
    m = load_minutes(fp_parquet, ohlc_zip)
    print(f"merged minutes: {len(m):,}  sessions: {m.date.nunique()}")

    # drop partial / half sessions
    cnt = m.groupby('date').mday.nunique()
    keep = sorted(cnt[cnt >= 400].index)
    m = m[m.date.isin(keep)].copy()
    sessions = keep
    print(f"usable sessions: {len(sessions)}  ({sessions[0]} .. {sessions[-1]})")

    grid, vol_mat, min_mat = build_profile_matrices(
        pd.read_parquet(fp_parquet).assign(
            date=lambda x: pd.to_datetime(x.timestamp, unit='ms', utc=True)
                              .dt.tz_convert('America/New_York').dt.date),
        sessions)
    cum_v = np.cumsum(vol_mat, axis=0)
    print(f"price grid: {len(grid):,} levels")

    # ---- build zones per session (prior-only) and collect entry events ----
    events = []
    for i, d in enumerate(sessions):
        if i < 6:
            continue
        prior_day = cum_v[i - 1] - (cum_v[i - 2] if i - 2 >= 0 else 0)
        prior_week = cum_v[i - 1] - (cum_v[i - 6] if i - 6 >= 0 else 0)
        sp_day = (min_mat[i - 1] == 1).astype(float) * prior_day   # single prints in prior day

        z = []
        for lo, hi, a, b in mask_to_zones(lvn_mask(prior_week), grid):
            z.append((lo, hi, a, b, True, False, False))
        for lo, hi, a, b in mask_to_zones(sp_day > 0, grid):
            z.append((lo, hi, a, b, False, True, False))
        for lo, hi, a, b in mask_to_zones(hvn_edge_mask(prior_week), grid):
            z.append((lo, hi, a, b, False, False, True))
        zones = merge_zones(z)

        s = m[m.date == d].sort_values('dt').reset_index(drop=True)
        cf = concept_flags(s)
        a = atr(s)
        H, L, C = s.high.values, s.low.values, s.close.values
        n = len(s)

        for zn in zones:
            lo, hi = zn['lo'], zn['hi']
            inside = (L <= hi) & (H >= lo)
            ent = np.where(inside & ~np.r_[False, inside[:-1]])[0]
            for t in ent:
                if t < 5 or t + 2 >= n or np.isnan(a[t]):
                    continue
                ref = C[t - 3]
                if ref > hi:
                    rev = +1              # came from above -> expect bounce up
                elif ref < lo:
                    rev = -1              # came from below -> expect rejection down
                else:
                    continue
                entry_px = C[t]
                k = K_ATR * a[t]
                up_lvl, dn_lvl = entry_px + k, entry_px - k
                raw = barrier_outcome(H, L, t, entry_px, up_lvl, dn_lvl)
                if raw == 0:
                    continue
                outcome = raw * rev      # +1 = reversed, -1 = continued
                r = cf.iloc[t]
                events.append(dict(
                    date=d, t=t, zone=f"LVN{'/SP' if zn['sp'] else ''}" if zn['lvn']
                          else ("SP" if zn['sp'] else "HVN_EDGE"),
                    lvn=zn['lvn'], sp=zn['sp'], hvn=zn['hvn'],
                    rev=rev, outcome=outcome, reversed=int(outcome > 0),
                    delta=r['delta'], vol_ratio=r['vol_ratio'], ats_ratio=r['ats_ratio'],
                    absdelta_ratio=r['absdelta_ratio'],
                    big_delta_small_range=bool(r['big_delta_small_range']),
                    two_way=bool(r['two_way']), close_pos=r['close_pos'],
                    delta_flip_up=bool(r['delta_flip_up']), delta_flip_dn=bool(r['delta_flip_dn']),
                    new_low=bool(r['new_low']), new_high=bool(r['new_high']),
                    delta_less_neg=bool(r['delta_less_neg']), delta_less_pos=bool(r['delta_less_pos']),
                    atr=a[t],
                ))
    ev = pd.DataFrame(events)
    print(f"\nzone-entry events with a resolved outcome: {len(ev):,}")
    print(f"base reversal rate at zones: {ev.reversed.mean():.2%}")
    ev.to_parquet('/tmp/zone_events.parquet', index=False)

    # ---- baselines: all non-zone minutes ----
    print("\n=== BASELINE (all non-zone minutes) ===")
    base_rows = []
    for d in sessions:
        s = m[m.date == d].sort_values('dt').reset_index(drop=True)
        a = atr(s)
        cf = concept_flags(s)
        H, L, C = s.high.values, s.low.values, s.close.values
        for t in range(5, len(s) - 2):
            if np.isnan(a[t]):
                continue
            for rev in (+1, -1):
                k = K_ATR * a[t]
                raw = barrier_outcome(H, L, t, C[t], C[t] + k, C[t] - k)
                if raw:
                    base_rows.append(int(raw * rev > 0))
    base = np.mean(base_rows)
    print(f"  non-zone minutes: {len(base_rows):,}   reversal rate {base:.2%}")

    # ---- concept comparison ----
    print("\n=== CONCEPT TEST (at zones) ===")
    print(f"{'concept':>26} | {'n_with':>7} | {'rate_with':>9} | {'rate_without':>12} | {'diff':>7} | {'vs non-zone':>11}")
    print("-" * 96)

    def apply_concept(df, name):
        if name == 'delta_flip_rev':
            return np.where(df.rev > 0, df.delta_flip_up, df.delta_flip_dn)
        if name == 'delta_with_rev':
            return (df.delta.values * df.rev.values) > 0
        if name == 'delta_against_rev':
            return (df.delta.values * df.rev.values) < 0
        if name == 'delta_flip_strong':
            return np.where(df.rev > 0, df.delta_flip_up, df.delta_flip_dn) & (df.absdelta_ratio > 1.0)
        if name == 'absorption_bigD_smallR':
            return df.big_delta_small_range.values
        if name == 'two_way_tape':
            return df.two_way.values
        if name == 'volume_climax':
            return (df.vol_ratio > 2.0).values
        if name == 'volume_dry_up':
            return (df.vol_ratio < 0.5).values
        if name == 'big_trades':        # few trades, lots of volume
            return ((df.ats_ratio > 1.4) & (df.vol_ratio > 1.0)).values
        if name == 'small_trades':
            return (df.ats_ratio < 0.7).values
        if name == 'rejection_close':   # closed back out of the zone side
            return np.where(df.rev > 0, df.close_pos > 0.66, df.close_pos < 0.34)
        if name == 'delta_divergence':
            return np.where(df.rev > 0, df.new_low.values & df.delta_less_neg.values,
                            df.new_high.values & df.delta_less_pos.values)
        raise ValueError(name)

    concepts = ['delta_flip_rev', 'delta_flip_strong', 'delta_with_rev', 'delta_against_rev',
                'absorption_bigD_smallR', 'two_way_tape', 'volume_climax', 'volume_dry_up',
                'big_trades', 'small_trades', 'rejection_close', 'delta_divergence']

    results = []
    # precompute session -> row indices so the bootstrap is cheap
    sess = ev.date.values
    usess = np.unique(sess)
    groups = {x: np.where(sess == x)[0] for x in usess}
    rev_vals = ev.reversed.values

    for cname in concepts:
        with_m = apply_concept(ev, cname)
        w, wo = rev_vals[with_m], rev_vals[~with_m]
        if len(w) < 30 or len(wo) < 30:
            continue
        # session-clustered bootstrap on the difference
        rng = np.random.default_rng(7)
        diffs = []
        for _ in range(1000):
            pick = rng.choice(usess, size=len(usess), replace=True)
            take = np.concatenate([groups[x] for x in pick])
            wm = with_m[take]
            nw = wm.sum()
            if nw < 10 or (len(wm) - nw) < 10:
                continue
            rv = rev_vals[take]
            diffs.append(rv[wm].mean() - rv[~wm].mean())
        diffs = np.array(diffs)
        lo, hi_ = (np.percentile(diffs, [2.5, 97.5]) if len(diffs) else (np.nan, np.nan))
        results.append(dict(concept=cname, n_with=int(with_m.sum()), rate_with=w.mean(),
                            n_without=int((~with_m).sum()), rate_without=wo.mean(),
                            diff=w.mean() - wo.mean(), ci_lo=lo, ci_hi=hi_,
                            vs_base=w.mean() - base))
        print(f"{cname:>26} | {int(with_m.sum()):>7} | {w.mean():>9.2%} | {wo.mean():>12.2%} "
              f"| {w.mean()-wo.mean():>+7.2%} | {w.mean()-base:>+11.2%}  [{lo:+.2%},{hi_:+.2%}]")

    # ---- zone types ----
    print("\n=== ZONE TYPE (no trigger) ===")
    for zt, lab in [(ev.lvn & ~ev.sp, 'LVN only'), (ev.sp & ~ev.lvn, 'Single print only'),
                    (ev.lvn & ev.sp, 'LVN + SP (both)'), (ev.hvn, 'HVN edge')]:
        if zt.sum() < 30:
            continue
        print(f"  {lab:>20}: n={int(zt.sum()):>5}  reversal rate {ev.reversed.values[zt].mean():.2%}"
              f"   (vs non-zone {base:.2%})")

    # ---- in/out of sample ----
    print("\n=== IN-SAMPLE (first half) vs OUT-OF-SAMPLE (second half) ===")
    mid = len(sessions) // 2
    is_dates, oos_dates = set(sessions[:mid]), set(sessions[mid:])
    print(f"  IS: {sessions[0]} .. {sessions[mid-1]}   OOS: {sessions[mid]} .. {sessions[-1]}")
    print(f"  {'concept':>26} | {'IS diff':>9} | {'OOS diff':>9} | {'consistent':>10}")
    print("-" * 66)
    for cname in concepts:
        with_m = apply_concept(ev, cname)
        sub = ev.assign(w=with_m)
        a = sub[sub.date.isin(is_dates)]
        b = sub[sub.date.isin(oos_dates)]
        def dd(x):
            if x.w.sum() < 15 or (~x.w).sum() < 15:
                return np.nan
            return x.reversed[x.w].mean() - x.reversed[~x.w].mean()
        da, db = dd(a), dd(b)
        ok = '' if np.isnan(da) or np.isnan(db) else ('yes' if np.sign(da) == np.sign(db) else 'NO')
        print(f"  {cname:>26} | {da:>+9.2%} | {db:>+9.2%} | {ok:>10}")

    res = pd.DataFrame(results).sort_values('diff', ascending=False)
    res.to_csv('results/concept_comparison.csv', index=False)
    print("\nwrote results/concept_comparison.csv")


if __name__ == '__main__':
    main()
