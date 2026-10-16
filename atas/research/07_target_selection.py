"""
07_target_selection.py — the decisive test: constrained target choice.

The naive top-1 test is misleading because a level 2 ticks away is almost always
touched but is a useless target. A trader always has a minimum acceptable reward.
So: require the target to be at least k x the stop distance away, then ask which
selection rule maximises P(target before stop).

Rules compared, all on identical setups and identical candidate sets:
  nearest_qualifying : closest level meeting the R:R floor
  model_score        : model's highest-PoT level meeting the floor
  and, as the decision that matters, the best PoT reached by any rule.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

OUT = "/tmp/mnq/derived"
SEED = 7

df = pd.read_parquet(f"{OUT}/design_fp.parquet")
types = sorted(df["type"].unique())
for t in types:
    df[f"t_{t}"] = (df["type"] == t).astype(np.int8)
df["dir_"] = (df["direction"] == 1).astype(np.int8)

STRUCT = (["dist_atr", "dist_pts", "rr", "time_left", "time_in", "range_pos",
           "range_atr", "vwap_dist", "dist_sess_hi", "dist_sess_lo",
           "untested", "tests_today", "vol_share", "vol_rank", "dir_"]
          + [f"t_{t}" for t in types])
FLOW = ["cum_delta_signed", "delta5_signed", "a_vol", "b_vol"]

days = sorted(df["td"].unique())
cut = int(len(days) * 0.7)
tr, te = df[df["td"].isin(set(days[:cut]))], df[df["td"].isin(set(days[cut:]))]

cols = STRUCT
Xtr = tr[cols].to_numpy(np.float64)
mu, sd = Xtr.mean(0), Xtr.std(0); sd[sd == 0] = 1
m = HistGradientBoostingClassifier(max_iter=300, random_state=SEED)
m.fit(Xtr, tr["y"].to_numpy())
te = te.copy()
te["p"] = m.predict_proba(te[cols].to_numpy(np.float64))[:, 1]

key = ["td", "direction", "stop", "time_in"]
print("R:R floor forced by dropping any candidate closer than k x stop\n")
print(f"{'floor':>6} {'setups':>8} {'nearest_qual':>13} {'model_top':>10} {'best_avail':>11} {'n_cand':>7}")
rows = []
for k in [1.0, 1.5, 2.0, 3.0]:
    sub = te[te["rr"] >= k]
    if not len(sub):
        continue
    g = sub.groupby(key)
    nearest = g.apply(lambda x: x.loc[x["dist_pts"].idxmin(), "y"], include_groups=False)
    modelp = g.apply(lambda x: x.loc[x["p"].idxmax(), "y"], include_groups=False)
    bestav = g.apply(lambda x: x["y"].max(), include_groups=False)
    ncand = g.size().mean()
    print(f"{k:>6.1f} {len(nearest):>8,} {nearest.mean()*100:>12.1f}% "
          f"{modelp.mean()*100:>9.1f}% {bestav.mean()*100:>10.1f}% {ncand:>7.1f}")
    rows.append(dict(floor=k, setups=len(nearest), nearest=nearest.mean(),
                     model=modelp.mean(), best_available=bestav.mean()))

res = pd.DataFrame(rows)
res.to_csv(f"{OUT}/target_selection.csv", index=False)

print("\n--- how good is the model's PoT number at the top of its own ranking? ---")
top = te.loc[te.groupby(key)["p"].idxmax()]
top = top.assign(bucket=pd.cut(top["p"], [0, .2, .3, .4, .5, .6, 1.0]))
agg = top.groupby("bucket", observed=True).agg(pred=("p", "mean"), actual=("y", "mean"),
                                               n=("y", "size"))
print("   claimed PoT    realised    n")
for i, r in agg.iterrows():
    print(f"   {r.pred*100:6.1f}%      {r.actual*100:6.1f}%  {int(r.n):,}")

print("\n--- mean distance (points) of the chosen target, per rule ---")
for k in [1.0, 2.0]:
    sub = te[te["rr"] >= k]
    g = sub.groupby(key)
    dn = g.apply(lambda x: x.loc[x["dist_pts"].idxmin(), "dist_pts"], include_groups=False).mean()
    dm = g.apply(lambda x: x.loc[x["p"].idxmax(), "dist_pts"], include_groups=False).mean()
    print(f"   floor {k}x: nearest picks {dn:.1f} pts, model picks {dm:.1f} pts")

# ---- the actual deliverable table: PoT by type x distance, R:R-floor style ----
print("\n--- interpretable PoT surface (all footprint-window rows, n>=200) ---")
d2 = df.copy()
d2["dist_bucket"] = pd.cut(d2["dist_pts"], [0, 20, 40, 60, 100, 150, 250],
                           labels=["<20", "20-40", "40-60", "60-100", "100-150", "150-250"])
d2["time_bucket"] = pd.cut(d2["time_in"], [-1, 90, 210, 330],
                           labels=["first_90m", "90-210m", "last_90m"])
piv = d2.groupby(["type", "dist_bucket", "time_bucket", "stop"], observed=True).agg(
    pot=("y", "mean"), n=("y", "size")).reset_index()
piv = piv[piv["n"] >= 200]
piv.to_csv(f"{OUT}/pot_surface.csv", index=False)
print(f"wrote {len(piv)} cells -> {OUT}/pot_surface.csv")
best = piv[piv["stop"] == 40].sort_values("pot", ascending=False).head(12)
print("\n  highest-PoT cells (40pt stop):")
print(best.to_string(index=False))
