"""
CASE-CONTROL: what distinguishes a swing point that becomes a valid reversal
from one that does not?

Case    = fine (25pt) zigzag pivot from which price travelled >= 100 pts away
Control = fine zigzag pivot from which it did not

This is the right comparison. Earlier tests asked "is a level there", comparing
against the whole price space. This asks the sharper question: given a swing
point, what predicts that THIS one becomes a 100-point reversal?

Ideas tested - including ones never raised by the user:
  TIMING      minute of day, minutes since open, since last swing
  STRUCTURE   incoming leg size/speed, pivot index, same-direction streak
  SESSION     distance from open, from developing VWAP, from developing POC,
              developing range so far, position in developing range
  CONFLUENCE  round numbers (25/50/100), prior-session levels
  SELF        has a reversal already happened at this price today / recently?

Multiple testing: ~25 features tested. At 95% about 1 false positive is expected,
so a single feature clearing p<0.05 is NOT a finding. Look for effect size,
consistency, and a mechanism.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
FINE = 25.0
BIG = 100.0
TOL = 5.0
rng = np.random.default_rng(101)

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


def zigzag(H, Lo, thr):
    piv = []
    dirn = 0
    ext_i, ext_p = 0, H[0]
    rh_i, rh_p = 0, H[0]
    rl_i, rl_p = 0, Lo[0]
    for i in range(len(H)):
        if dirn == 0:
            if H[i] >= rl_p + thr and rl_i < i:
                dirn = 1
                piv.append((rl_i, rl_p, 'L'))
                ext_i, ext_p = i, H[i]; rh_i, rh_p = i, H[i]
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1
                piv.append((rh_i, rh_p, 'H'))
                ext_i, ext_p = i, Lo[i]; rl_i, rl_p = i, Lo[i]
            else:
                if H[i] > rh_p: rh_p, rh_i = H[i], i
                if Lo[i] < rl_p: rl_p, rl_i = Lo[i], i
        elif dirn == 1:
            if H[i] > ext_p: ext_i, ext_p = i, H[i]
            elif Lo[i] <= ext_p - thr:
                piv.append((ext_i, ext_p, 'H')); dirn = -1
                ext_i, ext_p = i, Lo[i]
        else:
            if Lo[i] < ext_p: ext_i, ext_p = i, Lo[i]
            elif H[i] >= ext_p + thr:
                piv.append((ext_i, ext_p, 'L')); dirn = 1
                ext_i, ext_p = i, H[i]
    return piv


# ---- prior-session levels (bar-estimated) ----
prof = {}
for d in days:
    g = gday[d]
    lo = np.floor(g.low.min() / .25) * .25
    hi = np.ceil(g.high.max() / .25) * .25
    grid = np.arange(lo, hi + .25, .25)
    dens = np.zeros(len(grid))
    li = np.searchsorted(grid, g.low.values, 'left')
    hh = np.searchsorted(grid, g.high.values, 'right')
    for a, b, v in zip(li, hh, g.volume.values):
        if b > a:
            dens[a:b] += v / (b - a)
    prof[d] = pd.Series(dens, index=grid)
PRIOR = {}
for i, d in enumerate(days):
    if i == 0:
        PRIOR[d] = None
        continue
    v = prof[days[i - 1]]
    o = v.sort_values(ascending=False)
    cum = o.cumsum() / o.sum()
    va = o[cum <= .70]
    pv = gday[days[i - 1]]
    PRIOR[d] = dict(ph=float(pv.high.max()), pl=float(pv.low.min()),
                    pc=float(pv.close.iloc[-1]), poc=float(v.idxmax()),
                    vah=float(va.index.max()), val=float(va.index.min()))
print("prior levels built")

rows = []
for d in days:
    b = gday[d]
    H, Lo, C, V = b.high.values, b.low.values, b.close.values, b.volume.values
    O = b.open.values
    n = len(b)
    piv = zigzag(H, Lo, FINE)
    if len(piv) < 3:
        continue
    tp = (H + Lo) / 2
    cvol = np.cumsum(V)
    cvwap = np.cumsum(tp * V) / cvol
    P = PRIOR[d]
    # self-defined: prices where a big reversal already happened today
    done = []
    prev_i, prev_p = piv[0][0], piv[0][1]
    for k in range(len(piv) - 1):
        pi, pp, kind = piv[k]
        move = abs(piv[k + 1][1] - pp)
        case = int(move >= BIG)
        inleg = abs(pp - prev_p)
        dt_prev = max(1, pi - prev_i)

        # developing levels up to (but excluding) the last 5 bars
        cut = max(1, pi - 5)
        h_cut, l_cut = H[:cut], Lo[:cut]
        dev_hi, dev_lo = h_cut.max(), l_cut.min()
        dev_rng = dev_hi - dev_lo
        dev_vwap = cvwap[cut - 1]
        # developing POC from bars so far
        try:
            li = np.searchsorted(np.arange(dev_lo, dev_hi + .25, .25), 0)  # placeholder
        except Exception:
            pass
        rr = np.arange(dev_lo, dev_hi + .25, .25)
        dd = np.zeros(len(rr))
        a1 = np.searchsorted(rr, l_cut, 'left')
        b1 = np.searchsorted(rr, h_cut, 'right')
        for a, bb, vv in zip(a1, b1, V[:cut]):
            if bb > a:
                dd[a:bb] += vv / (bb - a)
        dev_poc = rr[int(np.argmax(dd))] if len(dd) else np.nan

        feat = dict(
            date=str(d), i=pi, kind=kind, case=case, move=move,
            min_od=int(b.min_od.values[pi]),
            mins_since_open=int(b.min_od.values[pi]),
            mins_since_prev=dt_prev,
            in_leg=inleg,
            in_speed=inleg / dt_prev,
            pivot_idx=k,
            streak=1,
            dist_open=abs(pp - O[0]),
            dist_vwap=abs(pp - dev_vwap),
            dist_vwap_frac=abs(pp - dev_vwap) / dev_rng if dev_rng > 0 else np.nan,
            pos_in_dev=(pp - dev_lo) / dev_rng if dev_rng > 0 else np.nan,
            dev_range=dev_rng,
            dist_dev_poc_frac=abs(pp - dev_poc) / dev_rng if dev_rng > 0 and not np.isnan(dev_poc) else np.nan,
            at_dev_extreme=int(pp >= dev_hi - .25 or pp <= dev_lo + .25),
            r50=int(abs(pp / 50 - round(pp / 50)) * 50 <= TOL),
            r100=int(abs(pp / 100 - round(pp / 100)) * 100 <= TOL),
        )
        if P:
            feat['d_ph'] = abs(pp - P['ph'])
            feat['d_pl'] = abs(pp - P['pl'])
            feat['d_pc'] = abs(pp - P['pc'])
            feat['d_poc'] = abs(pp - P['poc'])
            feat['d_vah'] = abs(pp - P['vah'])
            feat['d_val'] = abs(pp - P['val'])
            feat['at_prior_any'] = int(min(feat['d_ph'], feat['d_pl'], feat['d_pc'],
                                           feat['d_poc'], feat['d_vah'], feat['d_val']) <= TOL)
        # self-defined level: a previous big reversal today within TOL
        feat['retest_self'] = int(any(abs(pp - q) <= TOL for q in done))
        rows.append(feat)

        if case:
            done.append(pp)
        prev_i, prev_p = pi, pp

r = pd.DataFrame(rows)
print(f"\nsamples {len(r):,}   cases {r.case.sum():,} ({r.case.mean():.1%})   "
      f"controls {(1-r.case).sum():,}")
r.to_csv('results/case_control_pivots.csv', index=False)

print("\n" + "=" * 78)
print("TIMING - the '10 am' hypothesis and the shape of the day")
print("=" * 78)
print(f"{'time (ET)':>12} | {'cases':>7} | {'rate':>7} | {'lift':>6}")
r['bucket'] = (r.min_od // 30) * 30
base = r.case.mean()
for bkt, s in r.groupby('bucket'):
    if len(s) < 200:
        continue
    h = 9 + (bkt + 30) // 60
    mnt = (30 + bkt) % 60
    rate = s.case.mean()
    print(f"{h:02d}:{mnt:02d}       | {len(s):>7} | {rate:>6.1%} | {rate/base:>5.2f}x")

print("\n" + "=" * 78)
print("STRUCTURE")
print("=" * 78)
print(f"{'feature':>22} | {'cases':>10} | {'controls':>10} | {'diff':>9}")
for col, lab in [('in_leg', 'incoming leg (pts)'),
                 ('in_speed', 'incoming speed (pts/min)'),
                 ('mins_since_prev', 'mins since prev swing'),
                 ('pivot_idx', 'swing # in session')]:
    a, b_ = r[r.case == 1][col].median(), r[r.case == 0][col].median()
    print(f"{lab:>22} | {a:>10.2f} | {b_:>10.2f} | {a-b_:>+9.2f}")

print("\n" + "=" * 78)
print("SESSION GEOMETRY")
print("=" * 78)
print(f"{'feature':>26} | {'cases':>10} | {'controls':>10} | {'diff':>9}")
for col, lab in [('dist_vwap_frac', 'dist from dev VWAP (frac)'),
                 ('pos_in_dev', 'position in dev range'),
                 ('dist_dev_poc_frac', 'dist from dev POC (frac)'),
                 ('dev_range', 'developing range (pts)')]:
    a, b_ = r[r.case == 1][col].median(), r[r.case == 0][col].median()
    print(f"{lab:>26} | {a:>10.3f} | {b_:>10.3f} | {a-b_:>+9.3f}")

print("\n" + "=" * 78)
print("BINARY FEATURES - rate of becoming a big reversal")
print("=" * 78)
print(f"{'feature':>24} | {'YES':>18} | {'NO':>18} | {'lift':>6}")
for col, lab in [('at_dev_extreme', 'at dev range extreme'),
                 ('r50', 'at a round 50'),
                 ('r100', 'at a round 100'),
                 ('at_prior_any', 'at any prior level'),
                 ('retest_self', 'retest of an earlier reversal')]:
    s = r[r[col].notna()]
    y = s[s[col] == 1]
    nn = s[s[col] == 0]
    if len(y) < 30:
        continue
    ly = y.case.mean()
    ln = nn.case.mean()
    print(f"{lab:>24} | {ly:>10.1%} n={len(y):>6} | {ln:>10.1%} n={len(nn):>6} | "
          f"{ly/ln if ln else 0:>5.2f}x")

print("\n" + "=" * 78)
print("PRIOR-SESSION LEVELS - distance (not just 'at')")
print("=" * 78)
print(f"{'level':>12} | {'cases':>10} | {'controls':>10} | {'diff':>9}")
for col, lab in [('d_ph', 'prior high'), ('d_pl', 'prior low'), ('d_pc', 'prior close'),
                 ('d_poc', 'prior POC'), ('d_vah', 'prior VA high'), ('d_val', 'prior VA low')]:
    if col not in r.columns:
        continue
    a, b_ = r[r.case == 1][col].median(), r[r.case == 0][col].median()
    print(f"{lab:>12} | {a:>10.1f} | {b_:>10.1f} | {a-b_:>+9.1f}")
print("\nwrote results/case_control_pivots.csv")
