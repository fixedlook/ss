"""
Two final tests.

TEST 1 - is the elevated pivot-bar volume a real signature or just selection?
  If heavy bars cause big reversals, volume should rise monotonically with the
  size of the departing move. If it only shows up for the single biggest one,
  it is selection.

TEST 2 - are the rejections sitting on INTRADAY levels rather than prior-session
  ones?  Every catalogue test used levels drawn BEFORE the session. But price
  can form a level during the session and return to it. Test whether the day's
  biggest rejection is a re-test of a price already traded earlier that day.
"""
import numpy as np
import pandas as pd

BARS = '/tmp/bars/MNQZ26 - 1 min - RTH.csv'
ZIG = 0.25
TOL = 5.0
rng = np.random.default_rng(13)

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

piv_vol = bars.groupby('min_od').volume.median()


def zigzag(H, Lo, thr):
    piv = []
    dirn = 0
    ext_i, ext_p = 0, H[0]
    rh_i, rh_p = 0, H[0]
    rl_i, rl_p = 0, Lo[0]
    for i in range(len(H)):
        if dirn == 0:
            if H[i] >= rl_p + thr and rl_i < i:
                dirn = 1; ext_i, ext_p = i, H[i]; rh_i, rh_p = i, H[i]
                piv.append((rl_i, rl_p, 'L'))
            elif Lo[i] <= rh_p - thr and rh_i < i:
                dirn = -1; ext_i, ext_p = i, Lo[i]; rl_i, rl_p = i, Lo[i]
                piv.append((rh_i, rh_p, 'H'))
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


print("\n" + "=" * 74)
print("TEST 1. does pivot-bar volume scale with the size of the reversal?")
print("=" * 74)
rank_rows = []
retest_rows = []
for d in days:
    b = gday[d]
    H, Lo, V = b.high.values, b.low.values, b.volume.values
    n = len(b)
    rng_ = H.max() - Lo.min()
    if rng_ <= 0:
        continue
    piv = zigzag(H, Lo, ZIG * rng_)
    if len(piv) < 5:
        continue
    # departing move for each pivot, ranked
    mv = []
    for k in range(len(piv) - 1):
        mv.append((abs(piv[k + 1][1] - piv[k][1]), piv[k]))
    mv.sort(key=lambda x: -x[0])
    for rank, (size, (pi, pp, kind)) in enumerate(mv[:5]):
        mod = int(b.min_od.values[pi])
        if mod not in piv_vol.index:
            continue
        base = piv_vol.loc[mod]
        rank_rows.append(dict(date=str(d), rank=rank, kind=kind,
                              move_frac=size / rng_,
                              vol_ratio=float(V[pi] / base) if base else np.nan))

    # ---- TEST 2: was this price traded earlier in the same session? ----
    pi, pp, kind = mv[0][1]
    if pi < 3:
        continue
    earlier = b.iloc[:pi]
    # exclude the immediately preceding leg (the approach itself)
    leg = max(0, pi - 30)
    before_leg = b.iloc[:leg]
    hit_all = bool(((earlier.high >= pp - TOL) & (earlier.low <= pp + TOL)).any())
    hit_before = bool(((before_leg.high >= pp - TOL) & (before_leg.low <= pp + TOL)).any()) \
        if len(before_leg) else False
    # control: a random bar's midpoint earlier in the session
    ctrl_hit = []
    for j in rng.choice(np.arange(3, n - 1), size=min(8, n - 4), replace=False):
        mp = (H[j] + Lo[j]) / 2
        e2 = b.iloc[:j]
        ctrl_hit.append(bool(((e2.high >= mp - TOL) & (e2.low <= mp + TOL)).any()))
    retest_rows.append(dict(date=str(d), hit_all=hit_all, hit_before_leg=hit_before,
                            ctrl=np.mean(ctrl_hit) if ctrl_hit else np.nan,
                            mod=int(b.min_od.values[pi]),
                            move_frac=mv[0][0] / rng_))

r = pd.DataFrame(rank_rows)
print(f"{'rank':>6} | {'n':>6} | {'median departing move':>22} | {'vol ratio to clock':>19}")
for k in range(5):
    s = r[r['rank'] == k]
    print(f"{k+1:>6} | {len(s):>6} | {s.move_frac.median():>21.1%} | {s.vol_ratio.median():>19.2f}")
print("  -> if vol ratio is flat across ranks, the volume signal is SELECTION")

t = pd.DataFrame(retest_rows)
print("\n" + "=" * 74)
print("TEST 2. is the biggest rejection a RE-TEST of an intraday price?")
print("=" * 74)
print(f"  sessions analysed                 : {len(t):,}")
print(f"  price was already traded earlier  : {t.hit_all.mean():.1%}")
print(f"  ...excluding the approach leg     : {t.hit_before_leg.mean():.1%}")
print(f"  random bar, same test             : {t.ctrl.mean():.1%}")
print(f"  lift vs random                    : {t.hit_before_leg.mean()/t.ctrl.mean():.2f}x")
t.to_csv('results/retest_intraday.csv', index=False)
r.to_csv('results/pivot_volume_rank.csv', index=False)
print("\nwrote results/retest_intraday.csv + results/pivot_volume_rank.csv")
