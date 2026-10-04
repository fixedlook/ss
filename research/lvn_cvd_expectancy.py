"""
Does the LVN x CVD signal actually make money?  Priced properly.

The 2x2 in lvn_cvd_everyminute.py is conditional on the +/-100pt race RESOLVING
inside the session. In live trading you take every signal and some are still open
at the bell, so that filter is optimistic. Here every signal is priced:

    entry   = close of the signal minute
    exit    = +100/-100 barrier if touched, else the 16:59 close (mark to market)
    bet     = reversal (against the approach);  P&L in points, then after costs

Same bands, same arrival definition, same divergence flag as the committed study.
No lookahead anywhere: bars come from the union of the last 5 completed sessions.

Run:  /tmp/venv/bin/python research/lvn_cvd_expectancy.py
"""
import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5
BARRIER = 100.0
COST_PTS = 1.0          # round turn, in index points ($2/pt on MNQ -> $2.00)
rng = np.random.default_rng(99)

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
df['flow'] = df.bid_volume + df.ask_volume
df['delta'] = df.ask_volume - df.bid_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)

rows = []
for d in trade_days:
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
    D = b.delta.values
    V = b.flow.values
    n = len(b)
    cvd = np.cumsum(D)
    inb = np.zeros(n, bool)
    for lo, hi in bands:
        inb |= (C >= lo - TOL) & (C <= hi + TOL)
    arr = inb & ~np.r_[False, inb[:-1]]
    if arr.sum() == 0:
        continue

    for i in range(40, n - 1):
        ref = C[i]
        pslope = C[i] - C[i - 10]
        if pslope == 0:
            continue
        approach_up = pslope > 0
        seg_h, seg_l = H[i + 1:], L[i + 1:]
        uh, dh = seg_h >= ref + BARRIER, seg_l <= ref - BARRIER
        a = np.argmax(uh) if uh.any() else 10 ** 9
        bb = np.argmax(dh) if dh.any() else 10 ** 9
        resolved = int(a != bb)
        if resolved:
            outcome_up = a < bb
            rev_wins = int(outcome_up != approach_up)
            pnl_rev = BARRIER if rev_wins else -BARRIER
            bars = int(min(a, bb)) + 1
        else:
            rev_wins = np.nan
            end = C[-1]
            pnl_rev = (ref - end) if approach_up else (end - ref)
            bars = n - 1 - i
        s10 = cvd[i] - cvd[i - 10]
        rows.append(dict(
            date=d, i=i, arrival=int(arr[i]),
            cvd_div=int(s10 != 0 and np.sign(s10) != np.sign(pslope)),
            delta_opposes=int(D[i] != 0 and np.sign(D[i]) != (1 if approach_up else -1)),
            resolved=resolved, rev_wins=rev_wins, pnl_rev=float(pnl_rev), bars=bars,
            vol_pct=float((V[:i] < V[i]).mean())))

e = pd.DataFrame(rows)
print(f"signals priced {len(e):,} | every minute with a defined approach | sessions {e.date.nunique()}")
print(f"resolution rate {e.resolved.mean():.1%}   "
      f"median bars to resolution {e.bars[e.resolved == 1].median():.0f} min")

arr = e.arrival == 1
dv = e.cvd_div == 1
cells = np.where(arr & dv, 0, np.where(arr & ~dv, 1, np.where(~arr & dv, 2, 3)))
names = ['LVN arrival + divergence', 'LVN arrival only', 'divergence only', 'neither']

cod, ucl = pd.factorize(e.date.values)
K = cod.max() + 1


def cl_mean(vals_full, mask, n_boot=1200, seed=5):
    """vals_full: full-length values; mask: full-length boolean selection."""
    r = np.random.default_rng(seed)
    N = np.bincount(cod[mask], minlength=K)
    S = np.bincount(cod[mask], weights=vals_full[mask], minlength=K)
    W = r.multinomial(K, np.full(K, 1.0 / K), size=n_boot).astype(float)
    with np.errstate(invalid='ignore', divide='ignore'):
        m = (W @ S) / (W @ N)
    m = m[np.isfinite(m)]
    return np.percentile(m, [2.5, 97.5])


print("\n" + "=" * 104)
print("P&L OF THE REVERSAL BET (points per trade). Entry at the signal close, exit at the")
print("+/-100 barrier, or marked at 16:59 if still open. NOT filtered on resolution.")
print("=" * 104)
print(f"{'cell':>26} | {'n':>7} | {'resolved':>8} | {'P&L gross':>10} | {'P&L -1pt':>9} | "
      f"{'$ /MNQ':>8} | {'beats base':>10} | {'95% CI (net)':>18}")
base_net = None
summary = []
net_all = e.pnl_rev.values - COST_PTS          # full length; cl_mean masks internally
for k, nm in enumerate(names):
    mm = cells == k
    pnl = e.pnl_rev.values[mm]
    net = pnl - COST_PTS
    g = pnl.mean()
    n = net.mean()
    if k == 3:
        base_net = n
    lo, hi = cl_mean(net_all, mm)
    print(f"{nm:>26} | {mm.sum():>7,} | {e.resolved.values[mm].mean():>7.1%} | {g:>+9.2f} | "
          f"{n:>+8.2f} | {2*n:>+7.2f} | {'':>10} | [{lo:>+6.2f},{hi:>+6.2f}]")
    summary.append(dict(cell=nm, n=int(mm.sum()), resolved=e.resolved.values[mm].mean(),
                        pnl_gross=g, pnl_net=n, ci_lo=lo, ci_hi=hi))

sig = (cells == 0)
print("\n" + "=" * 104)
print("THE EDGE THAT MATTERS: signal cell minus the do-nothing cells")
print("=" * 104)
d_net = e.pnl_rev.values[sig].mean() - e.pnl_rev.values[cells == 3].mean() - 0  # costs cancel
lo, hi = cl_mean(e.pnl_rev.values, sig, seed=11)
lo0, hi0 = cl_mean(e.pnl_rev.values, cells == 3, seed=12)
print(f"  LVN+divergence P&L {e.pnl_rev.values[sig].mean():+.2f} pts  CI [{lo:+.2f},{hi:+.2f}]")
print(f"  neither          P&L {e.pnl_rev.values[cells == 3].mean():+.2f} pts  "
      f"CI [{lo0:+.2f},{hi0:+.2f}]")
print(f"  difference       {d_net:+.2f} points per trade = ${2*d_net:+.2f} per MNQ contract")

print("\nresolved vs unresolved split of the signal cell:")
s = sig
print(f"  resolved  : n={int((s & (e.resolved == 1)).sum()):,}  "
      f"win rate {e.rev_wins.values[s & (e.resolved == 1)].mean():.2%}  "
      f"P&L {e.pnl_rev.values[s & (e.resolved == 1)].mean():+.2f} pts")
print(f"  unresolved: n={int((s & (e.resolved == 0)).sum()):,}  "
      f"mark {e.pnl_rev.values[s & (e.resolved == 0)].mean():+.2f} pts  "
      f"median bars open {np.median(e.bars.values[s & (e.resolved == 0)]):.0f}")

print("\ncost sensitivity of the signal cell (net points / $ per MNQ contract):")
for c in [0.0, 0.5, 1.0, 1.5, 2.0]:
    print(f"  {c:>4.1f} pt cost -> {e.pnl_rev.values[sig].mean()-c:+.2f} pts  "
          f"(${2*(e.pnl_rev.values[sig].mean()-c):+.2f})")

e.to_parquet('/tmp/flow/lvn_cvd_pnl.parquet', index=False)
pd.DataFrame(summary).to_csv('results/lvn_cvd_expectancy.csv', index=False)
print("\nwrote results/lvn_cvd_expectancy.csv")
