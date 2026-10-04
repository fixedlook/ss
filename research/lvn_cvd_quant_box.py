"""
Quant upgrades to the LVN x CVD box, tested head to head against the original.

Five additions, each applied to the SAME entry machinery so the comparison is clean:

  V0  base        arrival at a level + boolean 10-min divergence  (the current box)
  V1  residual-z  arrival + the 10-min price move exceeds what the 10-min net flow
                  justifies by >= 1 prior-session sigma.  Replaces "signs disagree"
                  with a continuous, scale-free price-impact residual:
                      resid = dprice - beta * dCVD,   beta/sigma from PRIOR sessions
                  No lookahead: beta and sigma are pooled over sessions before today.
  V2  base + HF   base and flow in the top 30% of the trailing hour
  V3  base + LF   base and flow in the bottom 30% (the opposite hypothesis)
  V4  residual-z anywhere (no level) -> does the level add anything to V1?
  V5  ensemble    arrival + divergence agreeing on the 5, 10 and 20-minute windows
  V6  composite   walk-forward logistic on [div10, z, flow rank, vol ratio, arrival],
                  fitted on sessions before 2023-06 and evaluated only after it

Outcome priced two ways, both with the entry at the signal close and the session
close as the fallback exit:
  * the +/-100 race (the definition the whole project uses)
  * a 50 SL / 100 TP structure (the best cell of the SL/TP grid)

Run:  /tmp/venv/bin/python research/lvn_cvd_quant_box.py
"""
import time

import numpy as np
import pandas as pd

PROF = '/tmp/prof/clean.csv'
FLOW = '/tmp/flow/flow_good.parquet'
F_LVN, W_PTS, MIN_H, TICK, TOL = 0.7, 50.0, 4.0, 0.25, 5.0
N_PRIOR = 5
COST = 1.0
B = 100.0
SPLIT = '2023-06-01'
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
df['delta'] = df.ask_volume - df.bid_volume
df['flow'] = df.bid_volume + df.ask_volume
df['dstr'] = df['ny'].dt.strftime('%Y-%m-%d')
flowday = {d: g[g.flow > 0].reset_index(drop=True) for d, g in df.groupby('dstr')}
flowday = {d: g for d, g in flowday.items() if len(g) >= 100}
trade_days = sorted(flowday)
print(f"flow sessions {len(trade_days):,}")

# ------------------------------------------------- running price-impact model
Sxx = Sxy = S2 = 0.0
Nobs = 0
sig_hist = []                                    # trailing 5-session vol level
rows = []
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
    SV = b.delta.values.astype(float)
    V = b.flow.values.astype(float)
    n = len(b)
    if n < 60:
        continue
    cvd = np.cumsum(SV)
    inb = np.zeros(n, bool)
    for lo, hi in bands:
        inb |= (C >= lo - TOL) & (C <= hi + TOL)
    arr = inb & ~np.r_[False, inb[:-1]]

    # --- causal rolling features
    rr = np.diff(C)                                        # n-1 returns
    sw = np.lib.stride_tricks.sliding_window_view(V, 61)    # rows end at i=60..
    fr = (sw[:, :-1] < sw[:, [-1]]).mean(1)                 # flow rank in trailing hour
    sig30 = np.full(n, np.nan)
    s30 = np.lib.stride_tricks.sliding_window_view(rr, 30).std(1)
    sig30[30:] = s30
    vol_med_prior = np.median(sig_hist[-N_PRIOR:]) if len(sig_hist) >= N_PRIOR else np.nan
    vol_ratio = sig30 / vol_med_prior if vol_med_prior and np.isfinite(vol_med_prior) else np.full(n, np.nan)

    # --- price-impact residual (beta/sigma from BEFORE today)
    if Nobs > 5000:
        beta = Sxy / Sxx
        sd_r = np.sqrt(max(S2 / Nobs, 1e-12))
    else:
        beta, sd_r = 0.0, np.nan

    dp10 = np.full(n, np.nan)
    dc10 = np.full(n, np.nan)
    dp10[10:] = C[10:] - C[:-10]
    dc10[10:] = cvd[10:] - cvd[:-10]
    resid = dp10 - beta * dc10
    z = resid / sd_r if np.isfinite(sd_r) and sd_r > 0 else np.full(n, np.nan)
    div10 = np.zeros(n, bool)
    ok = np.isfinite(dp10) & (dp10 != 0) & (dc10 != 0)
    div10[ok] = np.sign(dc10[ok]) != np.sign(dp10[ok])
    rev_imp = np.isfinite(z) & np.isfinite(dp10) & (np.sign(resid) == np.sign(dp10))
    zrev = np.where(rev_imp, np.abs(z), -np.abs(z))

    div5 = np.zeros(n, bool)
    div20 = np.zeros(n, bool)
    for w, out in ((5, div5), (20, div20)):
        dpw = np.full(n, np.nan)
        dcw = np.full(n, np.nan)
        dpw[w:] = C[w:] - C[:-w]
        dcw[w:] = cvd[w:] - cvd[:-w]
        okw = np.isfinite(dpw) & (dpw != 0) & (dcw != 0)
        out[okw] = np.sign(dcw[okw]) != np.sign(dpw[okw])

    for i in range(40, n - 1):
        entry = C[i]
        ps = C[i] - C[i - 10]
        if ps == 0:
            continue
        dirn = -1.0 if ps > 0 else 1.0
        h, l = H[i + 1:], L[i + 1:]
        uh, dh = h >= entry + B, l <= entry - B
        a = int(np.argmax(uh)) if uh.any() else 10 ** 9
        bb = int(np.argmax(dh)) if dh.any() else 10 ** 9
        res = int(a != bb)
        if res:
            win = int((a < bb) != (ps > 0))
            pnl100 = B if win else -B
            bars = min(a, bb) + 1
        else:
            win, pnl100, bars = np.nan, dirn * (C[-1] - entry), n - 1 - i
        # 50 SL / 100 TP structure
        t_tp = int(np.argmax(h >= entry + B)) if (h >= entry + B).any() else 10 ** 9
        t_sl = int(np.argmax(l <= entry - B / 2)) if (l <= entry - B / 2).any() else 10 ** 9
        if t_tp < t_sl:
            pnl50 = B
        elif t_sl < t_tp:
            pnl50 = -B / 2
        elif t_tp == t_sl and t_tp < 10 ** 9:
            pnl50 = -B / 2
        else:
            pnl50 = dirn * (C[-1] - entry)
        rows.append((d, i, float(arr[i]), float(inb[i]), float(div10[i]),
                     float(zrev[i]) if np.isfinite(zrev[i]) else np.nan,
                     float(fr[i - 60]) if i >= 60 else np.nan,
                     float(vol_ratio[i]) if np.isfinite(vol_ratio[i]) else np.nan,
                     float(div5[i]), float(div20[i]),
                     res, win, pnl100, pnl50, bars, i / n))

    # --- update the impact model with this session, then the vol history
    m = np.isfinite(dp10) & np.isfinite(dc10)
    if m.sum() > 20:
        x, y = dc10[m], dp10[m]
        Sxx += float(x @ x)
        Sxy += float(x @ y)
        r = y - (Sxy / Sxx) * x
        S2 += float(r @ r)
        Nobs += len(x)
    sig_hist.append(float(np.nanmedian(sig30)))
    if (si + 1) % 200 == 0:
        print(f"  {si+1}/{len(trade_days)} sessions {time.time()-t0:.0f}s", flush=True)

E = pd.DataFrame(rows, columns=['date', 'i', 'arr', 'inb', 'div10', 'zrev', 'fr', 'vr',
                                'div5', 'div20', 'res', 'win', 'pnl100', 'pnl50', 'bars', 'tod'])
print(f"\nminutes {len(E):,}  sessions {E.date.nunique():,}   ({time.time()-t0:.0f}s)")
E['year'] = E.date.str[:4]
E.to_parquet('/tmp/flow/quant_box.parquet', index=False)

hi_f = E.fr >= 0.70
lo_f = E.fr <= 0.30
V = {
    'V0 base   (arrival + div10)': E.arr.astype(bool) & E.div10.astype(bool),
    'V1 residual-z >= 1 at level': E.arr.astype(bool) & (E.zrev >= 1.0),
    'V2 base + high flow': E.arr.astype(bool) & E.div10.astype(bool) & hi_f,
    'V3 base + low flow': E.arr.astype(bool) & E.div10.astype(bool) & lo_f,
    'V4 residual-z >= 1 anywhere': (E.zrev >= 1.0),
    'V5 div 5 & 10 & 20 agree': E.arr.astype(bool) & E.div5.astype(bool) & E.div10.astype(bool) & E.div20.astype(bool),
    'V5b div 10 & 20 agree': E.arr.astype(bool) & E.div10.astype(bool) & E.div20.astype(bool),
    '-- control: every minute': pd.Series(True, index=E.index),
}

sess_ids = {d: k for k, d in enumerate(sorted(E.date.unique()))}
SID = E.date.map(sess_ids).values
K = len(sess_ids)
rng = np.random.default_rng(4)
W = rng.multinomial(K, np.full(K, 1.0 / K), size=N_BOOT).astype(float)


def stats(mask, col='pnl100'):
    v = E[col].values[mask]
    s = SID[mask]
    N = np.bincount(s, minlength=K).astype(float)
    S = np.bincount(s, weights=v, minlength=K)
    with np.errstate(invalid='ignore', divide='ignore'):
        m = (W @ S) / (W @ N)
    m = m[np.isfinite(m)]
    lo, hi = np.percentile(m, [2.5, 97.5])
    r = E.res.values[mask]
    w = E.win.values[mask]
    res_n = int(np.nansum(r))
    return dict(n=int(mask.sum()), res=float(r.mean()) if len(r) else np.nan,
                winres=float(np.nanmean(w[r == 1])) if res_n else np.nan,
                gross=float(v.mean()), lo=lo, hi=hi)


print("\n" + "=" * 122)
print("THE BOX, UPGRADED - all on the +/-100 race, entry at the signal close, 1 pt cost")
print("=" * 122)
hdr = (f"{'variant':<30} | {'n':>7} {'res%':>6} {'win|res':>8} | {'gross':>7} {'net':>7} {'$ /MNQ':>8} | "
       f"{'95% CI (net)':>17} | {'50/100 net':>10}")
print(hdr)
print("-" * len(hdr))
base_mask = V['V0 base   (arrival + div10)']
base_stat = None
for name, mask in V.items():
    st = stats(mask)
    st2 = stats(mask, 'pnl50')
    if base_stat is None:
        base_stat = st
    print(f"{name:<30} | {st['n']:>7,} {st['res']:>5.0%} {st['winres']:>7.1%} | "
          f"{st['gross']:>+6.2f} {st['gross']-COST:>+6.2f} {2*(st['gross']-COST):>+7.2f} | "
          f"[{st['lo']-COST:>+5.2f},{st['hi']-COST:>+5.2f}] | {st2['gross']-COST:>+9.2f}")

print("\n" + "=" * 122)
print("IS THE UPGRADE BETTER THAN THE BASE BOX?  paired session bootstrap of (variant - V0)")
print("=" * 122)
for name, mask in V.items():
    if name.startswith(('V0', '--')):
        continue
    Nb = np.bincount(SID[base_mask], minlength=K).astype(float)
    Sb = np.bincount(SID[base_mask], weights=E.pnl100.values[base_mask], minlength=K)
    Nv = np.bincount(SID[mask], minlength=K).astype(float)
    Sv = np.bincount(SID[mask], weights=E.pnl100.values[mask], minlength=K)
    with np.errstate(invalid='ignore', divide='ignore'):
        mb = (W @ Sb) / (W @ Nb)
        mv = (W @ Sv) / (W @ Nv)
        d = mv - mb
    d = d[np.isfinite(d)]
    lo, hi = np.percentile(d, [2.5, 97.5])
    print(f"  {name:<30} {d.mean():>+7.2f} pts   CI [{lo:>+6.2f},{hi:>+6.2f}]   "
          f"({d.mean()*2:>+6.2f} $/MNQ)")

print("\n" + "=" * 122)
print("ERA STABILITY (net points per trade, +/-100 race)")
print("=" * 122)
eras = [('2021-2022', E.year.isin(['2021', '2022'])), ('2023-2024', E.year.isin(['2023', '2024'])),
        ('2026', E.year == '2026')]
print(f"{'variant':<30} |" + "".join(f"{lab:>18}" for lab, _ in eras))
for name, mask in V.items():
    row = ""
    for lab, m in eras:
        mm = mask & m
        row += (f"{E.pnl100.values[mm].mean()-COST:>+13.2f} n={int(mm.sum()):<4}" if mm.sum() > 5
                else f"{'-':>18}")
    print(f"{name:<30} |" + row)

print("\n" + "=" * 122)
print("DOSE-RESPONSE of the residual z at arrivals (V1 family) - deciles of zrev")
print("=" * 122)
a = E.arr.astype(bool) & E.zrev.notna()
q = E.zrev[a].quantile(np.linspace(0, 1, 11)).values
for k in range(10):
    m = a & (E.zrev >= q[k]) & (E.zrev <= q[k + 1] if k == 9 else E.zrev < q[k + 1])
    if m.sum() < 20:
        continue
    st = stats(m)
    print(f"  decile {k:>2}  z in [{q[k]:>+6.2f},{q[k+1]:>+6.2f}]  n={st['n']:>6,}  "
          f"win|res {st['winres']:>5.1%}  net {st['gross']-COST:>+6.2f} pts")

# ------------------------------------------------------------------ V6 walk-forward
print("\n" + "=" * 122)
print(f"V6 COMPOSITE - logistic regression, fitted before {SPLIT}, evaluated after")
print("=" * 122)
fit = E.dropna(subset=['zrev', 'fr', 'vr'])
fit = fit[fit.res == 1]
tr = fit[fit.date < SPLIT]
te = fit[fit.date >= SPLIT]
Xc = ['div10', 'zrev', 'fr', 'vr']
mu_, sd_ = tr[Xc].mean(), tr[Xc].std()
Xs = lambda f: ((f[Xc] - mu_) / sd_).values


def logistic(X, y, iters=600, lr=0.4):
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) / len(y)
        w -= lr * g
    return w


w = logistic(Xs(tr), tr.win.values.astype(float))
p_te = 1 / (1 + np.exp(-np.c_[np.ones(len(te)), Xs(te)] @ w))
print(f"  train {len(tr):,} resolved races (before {SPLIT})   test {len(te):,} after")
print("  coefficients: " + "  ".join(f"{c}={wi:+.3f}" for c, wi in zip(['int'] + Xc, w)))
thr = np.quantile(p_te, 0.90)
allm = E.date >= SPLIT
arr = E.arr.astype(bool) & allm
print(f"  test-set arrivals: {int(arr.sum()):,}")
for lab, m in [('all arrivals in test period', arr),
               ('top-decile model score', arr & (E.index.isin(te.index[p_te >= thr]))),
               ('base box (div10) in test period', arr & E.div10.astype(bool))]:
    if m.sum() < 10:
        print(f"  {lab:<34} too few ({int(m.sum())})")
        continue
    st = stats(m)
    print(f"  {lab:<34} n={st['n']:>6,}  res {st['res']:>5.0%}  win|res {st['winres']:>5.1%}  "
          f"gross {st['gross']:>+6.2f}  net {st['gross']-COST:>+6.2f} pts")
