"""
MFFU Builder evaluation simulator, driven by the real LVN x CVD signals.

The RR grid answered "points per trade".  An evaluation is a different question:
P(reach +target before the trailing max-loss floor) under the firm's exact rules.
That is NOT the same as positive expectancy, and NOT the same as MLL/(MLL+target),
because the rules change the barrier geometry.

MFFU Builder rules modelled (help.myfundedfutures.com, 2026):
  $25K : start 25,000  target +1,500  MLL 1,000 EOD-trailing  soft DLL 600   20 micro max
  $50K : start 50,000  target +3,000  MLL 2,000 EOD-trailing  soft DLL 1,000 40 micro max
  - EOD trailing: the floor does NOT move intraday.  At each close:
        floor = min( max(floor, high-water EOD balance - MLL), start + 100 )
    i.e. it locks permanently at breakeven + $100.
  - Breach is intraday: if equity touches the floor the account dies.  Modelled
    with each trade's maximum adverse excursion (MAE).
  - Soft DLL: hitting it pauses the day (cuts the trade); it does NOT fail.
  - No consistency rule, no time limit, 1 trading day minimum.

Cost: 1.0 pt round turn per trade (the $2/MNQ toll), applied unless cost_free.
Size in MNQ micros (1 MNQ = $2/pt).  One trade at a time - a signal is skipped
while a position is open, per cell.  Attempts are bootstrapped by resampling
sessions with replacement.

Run:  /tmp/venv/bin/python research/mffu_eval_sim.py
"""
import time

import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5
COST_PTS = 1.0
CELLS = [(30, 30), (20, 40), (30, 60), (50, 100)]
SIZES = [2, 5, 10, 20]
ACCTS = [
    dict(name='25K', start=25000.0, mll=1000.0, dll=600.0, target=1500.0, maxk=20, fee=103.0),
    dict(name='50K', start=50000.0, mll=2000.0, dll=1000.0, target=3000.0, maxk=40, fee=153.0),
]
# sim-funded stage: starts at $0, floor = -MLL and trails up to $0 (locks there).
# first payout needs buffer + 500 (50K) / buffer + 250 (25K).
FUNDED = [
    dict(name='25K fund', start=0.0, mll=1000.0, dll=600.0, target=1350.0, maxk=20, fee=103.0),
    dict(name='50K fund', start=0.0, mll=2000.0, dll=1000.0, target=2600.0, maxk=40, fee=153.0),
]
DALLAR = 2.0

# ------------------------------------------------------------------ bands
p = pd.read_csv(PROF, header=None, names=['s', 'price', 'bid', 'ask', 'trades']).dropna()
p = p[p.s > 1e12]
p['dt'] = pd.to_datetime(p.s, unit='ms', utc=True).dt.tz_convert('America/New_York')
p['date'] = p.dt.dt.date
p['volume'] = p.bid + p.ask
pv = p.groupby(['date', 'price'])['volume'].sum()
prof_days = sorted(p.date.unique())
WIN = int(round(W_PTS / TICK / 2)) * 2 + 1


def make_bands(d):
    v = pv.loc[d].sort_index()
    if len(v) < 50:
        return []
    med = pd.Series(v.values).rolling(WIN, center=True, min_periods=WIN // 3).median().values
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = v.values / med
    mask = (ratio <= F_LVN) & ~np.isnan(med)
    bands, start, prev = [], None, None
    for price, m in zip(v.index.values, mask):
        if m and start is None:
            start = price
        elif not m and start is not None:
            if prev - start >= MIN_H:
                bands.append((start, prev))
            start = None
        prev = price
    if start is not None and prev - start >= MIN_H:
        bands.append((start, prev))
    return bands


BAND = {d: make_bands(d) for d in prof_days}

df = pd.read_parquet(FLOW)
df['delta'] = df.ask_volume - df.bid_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)
NS = len(trade_days)
print(f"flow sessions {NS:,}")

# ------------------------------------------------- signals -> trade records
TR = {c: [[] for _ in range(NS)] for c in CELLS}
busy = {c: 0 for c in CELLS}
t0 = time.time()
for si, d in enumerate(trade_days):
    dts = pd.Timestamp(d).date()
    prior = [x for x in prof_days if x < dts][-N_PRIOR:]
    if not prior:
        continue
    bands = []
    for x in prior:
        bands += BAND[x]
    if not bands:
        continue
    for c in CELLS:
        busy[c] = 0
    b = flowday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    n = len(b)
    cvd = np.cumsum(b.delta.values)
    inb = np.zeros(n, bool)
    for lo, hi in bands:
        inb |= (C >= lo - TOL) & (C <= hi + TOL)
    arr = inb & ~np.r_[False, inb[:-1]]
    for i in range(40, n - 1):
        ps = C[i] - C[i - 10]
        if ps == 0 or not arr[i]:
            continue
        s10 = cvd[i] - cvd[i - 10]
        if not (s10 != 0 and np.sign(s10) != np.sign(ps)):
            continue
        dirn = -1.0 if ps > 0 else 1.0
        entry = C[i]
        h, l = H[i + 1:], L[i + 1:]
        if dirn > 0:
            fav = np.maximum(h - entry, 0.0)
            adv = np.maximum(entry - l, 0.0)
        else:
            fav = np.maximum(entry - l, 0.0)
            adv = np.maximum(h - entry, 0.0)
        fav = np.maximum.accumulate(fav)
        adv = np.maximum.accumulate(adv)
        for c in CELLS:
            if i < busy[c]:
                continue
            sl, tp = c
            it = np.argmax(fav >= tp) if (fav >= tp).any() else 10 ** 9
            isl = np.argmax(adv >= sl) if (adv >= sl).any() else 10 ** 9
            if it < isl:                       # TP first (GROSS pnl, cost applied later)
                pnl, mae, k = tp, adv[it], it
            elif isl < 10 ** 9:                # SL first, ties = loss
                pnl, mae, k = -sl, sl, isl
            else:                              # neither: marked at 16:59
                pnl, mae, k = dirn * (C[-1] - entry), adv[-1], len(adv) - 1
            TR[c][si].append((float(pnl), float(mae)))
            busy[c] = i + 1 + k + 1
    if si % 250 == 0:
        print(f"  {si}/{NS} sessions {time.time()-t0:.0f}s")

STAT = {}
for c in CELLS:
    g = np.array([t[0] for s in TR[c] for t in s])
    STAT[c] = (len(g), g.mean(), g.mean() - COST_PTS)
    print(f"cell {c}: {len(g):,} trades, gross {g.mean():+.2f} pts, net {g.mean()-COST_PTS:+.2f} pts")


def simulate(cell, cfg, size, n_att, seed, overlay_pp=0.0, cost_free=False, verbose=False):
    """Run n_att bootstrapped evaluations.  Returns (pass rate, median sessions)."""
    sl, tp = cell
    per_sess = TR[cell]
    allp = np.array([t[0] for s in per_sess for t in s])
    p_loss = (allp < 0).mean()
    conv = (overlay_pp / 100.0) / max(p_loss, 1e-9)
    start, mll, dll, tgt = cfg['start'], cfg['mll'], cfg['dll'], cfg['target']
    lock = start + 100.0
    rng = np.random.default_rng(seed)
    npass = 0
    sess_to = []
    for _ in range(n_att):
        bal, hwm, floor = start, start, start - mll
        ns = 0
        outcome = 0
        while outcome == 0:
            si = int(rng.integers(NS))
            day0 = bal
            for pnl_pts, mae_pts in per_sess[si]:
                pnl_pts = pnl_pts + (COST_PTS if cost_free else -COST_PTS)
                if conv > 0 and pnl_pts < 0 and rng.random() < conv:
                    pnl_pts = tp                     # overlay: loser becomes a winner
                pnl = pnl_pts * DALLAR * size
                mae = mae_pts * DALLAR * size
                if bal - mae <= floor:               # intraday breach of the floor
                    outcome = -1
                    break
                room = bal - (day0 - dll)            # remaining daily loss budget
                if mae >= room:                      # soft DLL: cut, pause the day
                    bal -= max(room, 0.0)
                    break
                bal += pnl
                if bal >= start + tgt:               # target reached (realised)
                    outcome = 1
                    break
            ns += 1
            if outcome == 0:
                hwm = max(hwm, bal)
                floor = min(max(floor, hwm - mll), lock)
                if bal <= floor:
                    outcome = -1
                elif ns >= 3000:
                    outcome = -2
        if outcome == 1:
            npass += 1
        sess_to.append(ns)
    return npass / n_att, float(np.median(sess_to))


print("\n" + "=" * 100)
print("P(PASS) of an MFFU Builder evaluation - real LVN x CVD signals, no overlay")
print("=" * 100)
print(f"{'cell':>9} | " + " | ".join(f"{a['name']} {k:>2}MNQ" for a in ACCTS for k in SIZES))
for c in CELLS:
    row = []
    for a in ACCTS:
        for k in SIZES:
            pr, ms = simulate(c, a, k, 500, 1234)
            row.append(f"{100*pr:>8.2f}%")
    print(f"{str(c):>9} | " + " | ".join(row))
print("""
  Reference: a fee-free fair coin in these same barriers passes MLL/(MLL+target)
  = 40% (25K and 50K default) or 33% (50K with the $1,500 add-on); MFFU's floor
  lock at breakeven+100 nudges that a little higher.  Anything far below 40% is
  (a) the $2 toll on every trade and (b) a signal whose own gross is negative.""")

print("\n" + "=" * 100)
print("ISOLATING THE TOLL - same runs with the 1 pt cost deleted (50K, 10 MNQ)")
print("=" * 100)
for c in CELLS:
    pr_n, _ = simulate(c, ACCTS[1], 10, 500, 99)
    pr_f, _ = simulate(c, ACCTS[1], 10, 500, 99, cost_free=True)
    print(f"  {str(c):>9}: with toll {100*pr_n:>6.2f}%   without toll {100*pr_f:>6.2f}%")

print("\n" + "=" * 100)
print("WHAT THE ABSORPTION OVERLAY HAS TO BE WORTH (50K, 10 MNQ)")
print("=" * 100)
print("  overlay = it turns N pp of losing trades into winners (same timing, same")
print("  intraday dip).  Columns are the assumed overlay size.")
print(f"{'cell':>9} | " + " | ".join(f"{d:>2}pp" for d in [0, 1, 2, 3, 4]))
for c in CELLS:
    row = []
    for d in [0, 1, 2, 3, 4]:
        pr, ms = simulate(c, ACCTS[1], 10, 1200, 7, overlay_pp=d)
        row.append(f"{100*pr:>5.1f}%")
    print(f"{str(c):>9} | " + " | ".join(row))
print("""
  Cost per pass = fee / P(pass)  (50K Builder list $153, often discounted).
  The overlay is the user's MotiveWave overlay, assumed here - never measured.""")

print("\n" + "=" * 100)
print("GATE 2 - the sim-funded account: P(reach the first payout)  (10 MNQ, 60 K cap)")
print("=" * 100)
print("  funded starts at $0, floor -MLL trailing up to $0; first payout needs")
print("  buffer+500 = $2,600 (50K) / buffer+250 = $1,350 (25K).  10 MNQ.")
print(f"{'cell':>9} | {'25K fund':>10} | {'50K fund':>10} | {'50K: attempts per PAYOUT':>25}")
for c in CELLS:
    a, _ = simulate(c, FUNDED[0], 10, 800, 21)
    b, _ = simulate(c, FUNDED[1], 10, 800, 21)
    e, _ = simulate(c, ACCTS[1], 10, 800, 22)
    comb = e * b
    print(f"{str(c):>9} | {100*a:>9.1f}% | {100*b:>9.1f}% | "
          f"{('%.1f' % (1/comb)) if comb > 1e-6 else '>1e6':>25}   "
          f"(pass {100*e:.0f}% x payout {100*b:.0f}%)")
print("""
  Two gates must both be cleared, each one paying the $2 toll.  Cost per payout
  = fee x attempts-per-payout.  5 payouts are needed to reach the live seat; this
  is only the first one, and the qualifying-day / buffer rules are not modelled.""")
