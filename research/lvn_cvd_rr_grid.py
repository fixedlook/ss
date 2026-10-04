"""
Asymmetric SL/TP grid on the LVN x CVD reversal setup.

The +/-100 race in lvn_cvd_expectancy.py answers "which way does it go first".
Traders don't trade that.  They trade:

    price arrives at an LVN, CVD divergence shows up -> go the reversal way
    with an SL of X points and a TP of Y points.

So this prices exactly that over a grid of X and Y, walking the 1-min bar path
forward from the entry close.  Arms, all through the identical machinery:

    A  arrival at a level + 10-min divergence   (the committed signal, n=2,532)
    B  divergence TURNS ON while price sits in a level ("hits LVN, then CVD")
    L  arrival at a level, NO divergence        (does the CVD part add anything?)
    M  A's entries taken the OTHER way (momentum, i.e. with the approach)
    C  every minute with a defined approach     (control)

Bar-by-bar rules
    entry  = close of the signal minute
    bet    = reversal direction (against the 10-min slope) unless stated
    TP/SL  = intrabar touch, whichever comes first
    ties   = both thresholds inside one bar -> counted as the LOSS (conservative);
             the optimistic twin is printed so the reader sees the error bar
    open   = neither touched by 16:59 -> marked at the close
No lookahead: bands from the union of the last 5 completed sessions, divergence
on the trailing 10 minutes only.

Run:  /tmp/venv/bin/python research/lvn_cvd_rr_grid.py
"""
import time
from collections import defaultdict

import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5
COST_PTS = 1.0
SL_GRID = [10, 15, 20, 25, 30, 40, 50]
TP_GRID = [10, 15, 20, 30, 40, 50, 60, 75, 100]
BIG = 10 ** 9
NB = 900
N_BOOT = 1200

# ---------------------------------------------------------------- bands
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
df['delta'] = df.ask_volume - df.bid_volume          # recomputed, per house rule
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)
print(f"flow sessions {len(trade_days):,}")

COMBOS = [(s, t) for s in SL_GRID for t in TP_GRID]
SLI = {s: k for k, s in enumerate(SL_GRID)}
TPI = {t: k for k, t in enumerate(TP_GRID)}
SLa = np.array(SL_GRID, float)
TPa = np.array(TP_GRID, float)
HEAD = [(30, 60), (10, 50), (20, 40), (50, 100), (15, 30), (25, 50), (30, 30), (50, 50)]
HEADI = {k: j for j, k in enumerate(HEAD)}
ARMS = list('ABLMC')
NSESS = len(trade_days)


def new_stat():
    return dict(n=0, win=0, lose=0, amb=0, open=0, gross=0.0, opt=0.0,
                bars=np.zeros(NB, np.int64), y_sum=defaultdict(float),
                y_n=defaultdict(int))


STAT = {a: {k: new_stat() for k in COMBOS} for a in ARMS}
BS = {a: {k: [np.zeros(NSESS), np.zeros(NSESS)] for k in HEAD} for a in ARMS}
NPT = defaultdict(int)
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
    b = flowday[d]
    H, L, C = b.high.values, b.low.values, b.close.values
    n = len(b)
    cvd = np.cumsum(b.delta.values)
    inb = np.zeros(n, bool)
    for lo, hi in bands:
        inb |= (C >= lo - TOL) & (C <= hi + TOL)
    arr = inb & ~np.r_[False, inb[:-1]]
    valid = np.zeros(n, bool)
    divf = np.zeros(n, bool)
    for i in range(40, n - 1):
        ps = C[i] - C[i - 10]
        if ps == 0:
            continue
        s10 = cvd[i] - cvd[i - 10]
        valid[i] = True
        divf[i] = (s10 != 0) and (np.sign(s10) != np.sign(ps))

    for i in range(40, n - 1):
        if not valid[i]:
            continue
        ps = C[i] - C[i - 10]
        dirn = -1.0 if ps > 0 else 1.0                 # the reversal bet
        entry = C[i]
        year = d[:4]
        tags = ['C']
        if arr[i] and divf[i]:
            tags += ['A', 'M']                         # M = same entry, other side
        if arr[i] and not divf[i]:
            tags.append('L')
        if inb[i] and divf[i] and not divf[i - 1]:
            tags.append('B')
        for tg in tags:
            NPT[tg] += 1

        h, l = H[i + 1:], L[i + 1:]
        for tg in tags:
            dir_use = -dirn if tg == 'M' else dirn
            if dir_use > 0:
                fav = np.maximum(h - entry, 0.0)
                adv = np.maximum(entry - l, 0.0)
            else:
                fav = np.maximum(entry - l, 0.0)
                adv = np.maximum(h - entry, 0.0)
            fav = np.maximum.accumulate(fav)
            adv = np.maximum.accumulate(adv)
            m_tp = fav[:, None] >= TPa[None, :]
            m_sl = adv[:, None] >= SLa[None, :]
            t_tp = np.where(m_tp.any(0), np.argmax(m_tp, 0), BIG)
            t_sl = np.where(m_sl.any(0), np.argmax(m_sl, 0), BIG)
            TT, SS = t_tp[None, :], t_sl[:, None]
            win = TT < SS
            lose = SS < TT
            amb = (TT == SS) & (TT < BIG)
            opn = (TT == BIG) & (SS == BIG)
            mark = dir_use * (C[-1] - entry)
            pnl_p = np.where(win, TPa[None, :], np.where(lose, -SLa[:, None],
                             np.where(amb, -SLa[:, None], mark)))
            pnl_o = np.where(win, TPa[None, :], np.where(lose, -SLa[:, None],
                             np.where(amb, TPa[None, :], mark)))
            bars = np.where(opn, n - 1 - i, np.minimum(np.minimum(TT, SS), BIG) + 1)
            bars = np.clip(bars, 1, NB - 1)
            for s, t in COMBOS:
                r, c = SLI[s], TPI[t]
                st = STAT[tg][(s, t)]
                st['n'] += 1
                st['win'] += int(win[r, c])
                st['lose'] += int(lose[r, c])
                st['amb'] += int(amb[r, c])
                st['open'] += int(opn[r, c])
                st['gross'] += float(pnl_p[r, c])
                st['opt'] += float(pnl_o[r, c])
                st['bars'][int(bars[r, c])] += 1
                st['y_sum'][year] += float(pnl_p[r, c])
                st['y_n'][year] += 1
                if (s, t) in HEADI:
                    z = BS[tg][(s, t)]
                    z[0][si] += 1.0
                    z[1][si] += float(pnl_p[r, c])
    if (si + 1) % 250 == 0:
        print(f"  {si+1}/{len(trade_days)} sessions  {time.time()-t0:.0f}s", flush=True)

print(f"\nsignals: " + "  ".join(f"{a} {NPT[a]:,}" for a in ARMS) + f"   ({time.time()-t0:.0f}s)")

rng = np.random.default_rng(21)
WM = rng.multinomial(NSESS, np.full(NSESS, 1.0 / NSESS), size=N_BOOT).astype(float)


def boot_ci(arm, combo):
    N, S = BS[arm][combo]
    with np.errstate(invalid='ignore', divide='ignore'):
        m = (WM @ S) / (WM @ N)
    m = m[np.isfinite(m)]
    return np.percentile(m, 2.5) - COST_PTS, np.percentile(m, 97.5) - COST_PTS


def med_bars(st):
    c = st['bars']
    return int(np.searchsorted(np.cumsum(c), c.sum() / 2.0)) if c.sum() else 0


def cell(arm, s, t, key):
    st = STAT[arm][(s, t)]
    if st['n'] == 0:
        return np.nan
    if key == 'net':
        return st['gross'] / st['n'] - COST_PTS
    if key == 'net_opt':
        return st['opt'] / st['n'] - COST_PTS
    if key == 'gross':
        return st['gross'] / st['n']
    if key == 'win':
        return st['win'] / st['n']
    if key == 'amb':
        return st['amb'] / st['n']
    if key == 'open':
        return st['open'] / st['n']


def matrix(arm, key, title, fmt='{:+.2f}'):
    print("\n" + "=" * 112)
    print(title)
    print("=" * 112)
    print("      TP ->" + "".join(f"{t:>9}" for t in TP_GRID))
    for s in SL_GRID:
        row = ""
        for t in TP_GRID:
            v = cell(arm, s, t, key)
            row += (fmt.format(v).rjust(9) if np.isfinite(v) else "      -  ")
        print(f"SL {s:>3}   " + row)


print(f"\nCOST MODEL: {COST_PTS:.1f} pt round turn on every trade (deducted below)")
matrix('A', 'net', "NET P&L PER TRADE (points) - A: LVN arrival + 10-min divergence")
matrix('M', 'net', "NET P&L PER TRADE (points) - M: same entries, MOMENTUM side (with the approach)")
matrix('L', 'net', "NET P&L PER TRADE (points) - L: LVN arrival, no divergence")
matrix('C', 'net', "NET P&L PER TRADE (points) - C: CONTROL, every minute, reversal side")
matrix('A', 'win', "WIN RATE - A (ties counted as losses)", fmt='{:.1%}')
matrix('A', 'open', "STILL OPEN AT 16:59 - A (marked to the close)", fmt='{:.1%}')
matrix('A', 'amb', "SAME-BAR TIE RATE - A", fmt='{:.1%}')

for other, nm in [('C', 'CONTROL C'), ('L', 'LVN arrival without divergence L'), ('M', 'momentum side M')]:
    print("\n" + "=" * 112)
    print(f"EDGE OF A OVER {nm} - net points per trade (same SL/TP)")
    print("=" * 112)
    print("      TP ->" + "".join(f"{t:>9}" for t in TP_GRID))
    for s in SL_GRID:
        row = ""
        for t in TP_GRID:
            e = cell('A', s, t, 'net') - cell(other, s, t, 'net')
            row += (f"{e:>+9.2f}" if np.isfinite(e) else "      -  ")
        print(f"SL {s:>3}   " + row)


def detail(arm, label):
    print("\n" + "=" * 116)
    print(f"DETAIL - {label}")
    print("=" * 116)
    hdr = (f"{'SL/TP':>8} {'R:R':>5} | {'n':>7} {'win%':>6} {'null%':>6} {'loss%':>6} {'tie%':>5} "
           f"{'open%':>6} | {'gross':>7} {'net':>7} {'$ /MNQ':>7} {'net/SL':>7} {'bars':>5} | "
           f"{'ctrl':>6} {'edgeC':>6} | {'95% CI (net)':>18}")
    print(hdr)
    print("-" * len(hdr))
    for s, t in HEAD:
        st = STAT[arm][(s, t)]
        n = st['n']
        net = st['gross'] / n - COST_PTS
        cnet = cell('C', s, t, 'net')
        need = (s + COST_PTS) / (s + t)
        null = s / (s + t)
        lo, hi = boot_ci(arm, (s, t))
        print(f"{s:>4}/{t:<3} {t/s:>5.2f} | {n:>7,} {st['win']/n:>5.1%} {null:>5.1%} "
              f"{st['lose']/n:>5.1%} {st['amb']/n:>4.1%} {st['open']/n:>5.1%} | "
              f"{st['gross']/n:>+6.2f} {net:>+6.2f} {2*net:>+6.2f} {net/s:>+6.2f}R {med_bars(st):>5} | "
              f"{cnet:>+6.2f} {net-cnet:>+5.2f} | [{lo:>+5.2f},{hi:>+5.2f}]")


detail('A', "A: LVN arrival + 10-min divergence  (win% must beat null% to pay for the asymmetry)")
detail('L', "L: LVN arrival, no divergence")
detail('M', "M: same entries as A, MOMENTUM side")
detail('B', "B: divergence turns on while price sits in the level")

print("\n" + "=" * 112)
print("BEST CELLS OF A BY NET P&L (out of 63 combos)")
print("=" * 112)
best = sorted(COMBOS, key=lambda k: -cell('A', k[0], k[1], 'net'))[:6]
print(f"{'SL/TP':>8} | {'n':>7} {'net':>7} {'ci_lo':>7} {'ci_hi':>7} | {'ctrl':>6} {'edge':>6} | {'win%':>6} {'open%':>6}")
for s, t in best:
    st = STAT['A'][(s, t)]
    n = st['n']
    net = st['gross'] / n - COST_PTS
    lo, hi = boot_ci('A', (s, t)) if (s, t) in HEADI else (np.nan, np.nan)
    cnet = cell('C', s, t, 'net')
    print(f"{s:>4}/{t:<3} | {n:>7,} {net:>+6.2f} {lo:>+6.2f} {hi:>+6.2f} | {cnet:>+6.2f} "
          f"{net-cnet:>+5.2f} | {st['win']/n:>5.1%} {st['open']/n:>5.1%}")

print("\n" + "=" * 112)
print("ERA STABILITY - net points per trade, A")
print("=" * 112)
years = sorted({y for k in COMBOS for y in STAT['A'][k]['y_sum']})
print(f"{'SL/TP':>8} |" + "".join(f"{y:>10}" for y in years))
for s, t in HEAD:
    st = STAT['A'][(s, t)]
    row = ""
    for y in years:
        nn = st['y_n'].get(y, 0)
        row += f"{st['y_sum'][y]/nn:>+10.2f}" if nn else "        -  "
    print(f"{s:>4}/{t:<3} |" + row)
print(f"{'n':>8} |" + "".join(f"{STAT['A'][HEAD[0]]['y_n'].get(y,0):>10,}" for y in years))

rows = []
for arm in ARMS:
    for s, t in COMBOS:
        st = STAT[arm][(s, t)]
        if st['n'] == 0:
            continue
        n = st['n']
        rows.append(dict(set=arm, sl=s, tp=t, rr=t / s, n=n, win=st['win'] / n,
                         null_win=s / (s + t), breakeven_win=(s + COST_PTS) / (s + t),
                         lose=st['lose'] / n, tie=st['amb'] / n, open=st['open'] / n,
                         gross_pts=st['gross'] / n, net_pts=st['gross'] / n - COST_PTS,
                         net_opt_pts=st['opt'] / n - COST_PTS,
                         usd=2 * (st['gross'] / n - COST_PTS),
                         net_per_sl=(st['gross'] / n - COST_PTS) / s, med_bars=med_bars(st)))
out = pd.DataFrame(rows)
out.to_csv('results/lvn_cvd_rr_grid.csv', index=False)
print("\nwrote results/lvn_cvd_rr_grid.csv")
