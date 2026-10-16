"""
06_model.py — does order flow beat structure alone?

Design rules that keep this honest:
  * Split by TRADING DAY, chronologically (first 70% train, last 30% test).
    Never randomly: rows from the same day share a path and would leak.
  * Two models with an identical split:
        STRUCT  = level geometry, distance, time, session context, prior profile
        FLOW    = STRUCT + book imbalance, 5-min delta, last-bar bid/ask volume
    The A/B difference is the value of order flow.
  * Report AUC, Brier, calibration by decile, and the metric that actually
    matters: top-1 hit rate, i.e. if you take the model's best target, how often
    does it get hit before your stop — vs. the "nearest unswept level" heuristic.
  * A logistic model is included separately because it is what we can port to C#.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, brier_score_loss

OUT = "/tmp/mnq/derived"
SEED = 7

df = pd.read_parquet(f"{OUT}/design_fp.parquet")
print(f"rows={len(df):,}  days={df['td'].nunique()}")
print(df.groupby("type")["y"].agg(["size", "mean"]).sort_values("size", ascending=False).to_string())

# level type -> one-hot (kept explicit so coefficients can be ported to C#)
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
train_days, test_days = set(days[:cut]), set(days[cut:])
tr = df[df["td"].isin(train_days)]
te = df[df["td"].isin(test_days)]
print(f"\ntrain {len(tr):,} rows over {len(train_days)} days  ({days[0]} .. {days[cut-1]})")
print(f"test  {len(te):,} rows over {len(test_days)} days  ({days[cut]} .. {days[-1]})")

y_tr = tr["y"].to_numpy()
y_te = te["y"].to_numpy()
print(f"base rate train {y_tr.mean():.3f}  test {y_te.mean():.3f}")


def evaluate(name, cols, model):
    Xtr = tr[cols].to_numpy(np.float64)
    Xte = te[cols].to_numpy(np.float64)
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1
    if isinstance(model, LogisticRegression):
        Xtr, Xte = (Xtr - mu) / sd, (Xte - mu) / sd
    model.fit(Xtr, y_tr)
    p = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(y_te, p)
    brier = brier_score_loss(y_te, p)
    print(f"  {name:34s} AUC={auc:.4f}  Brier={brier:.4f}")
    return p, auc


print("\n--- model comparison (test = last 30% of days) ---")
res = {}
res["struct_gbm"], _ = evaluate("STRUCT  (grad boosting)", STRUCT,
                                HistGradientBoostingClassifier(max_iter=250, random_state=SEED))
res["flow_gbm"], _ = evaluate("FLOW    (grad boosting)", STRUCT + FLOW,
                              HistGradientBoostingClassifier(max_iter=250, random_state=SEED))
res["struct_lr"], _ = evaluate("STRUCT  (logistic, ported to C#)", STRUCT,
                               LogisticRegression(max_iter=2000, C=0.5))
res["flow_lr"], _ = evaluate("FLOW    (logistic, ported to C#)", STRUCT + FLOW,
                             LogisticRegression(max_iter=2000, C=0.5))

# ---------------------------------------------------------------- calibration
best = res["flow_gbm"]
te2 = te.copy()
te2["p"] = best
te2["decile"] = pd.qcut(te2["p"], 10, labels=False, duplicates="drop")
cal = te2.groupby("decile").agg(pred=("p", "mean"), actual=("y", "mean"), n=("y", "size"))
print("\n--- calibration, FLOW grad-boosting, test set ---")
print("  decile   predicted  actual   n")
for i, r in cal.iterrows():
    print(f"    {i:>2}      {r.pred*100:6.1f}%  {r.actual*100:6.1f}%  {int(r.n):,}")

# ------------------------------------------------- top-1 target selection test
print("\n--- TOP-1 test: is the model's best target better than simple heuristics? ---")
key = ["td", "direction", "stop", "time_in"]
idx_te = te.index
te3 = te.copy()
te3["p_flow"] = res["flow_gbm"]
te3["p_struct"] = res["struct_gbm"]

def top1_stats(col):
    pick = te3.loc[te3.groupby(key)[col].idxmax()]
    return pick["y"].mean(), len(pick)

print(f"  {'strategy':38s} {'hit rate':>9s}  {'setups':>8s}")
for label, col in [("model best target (flow)", "p_flow"),
                   ("model best target (struct)", "p_struct")]:
    hr, n = top1_stats(col)
    print(f"  {label:38s} {hr*100:8.1f}%  {n:8,}")

# heuristics for comparison, on the same setups
g = te3.groupby(key)
heur = pd.DataFrame({
    "nearest": g.apply(lambda x: x.loc[x["dist_pts"].idxmin(), "y"], include_groups=False),
    "untested_nearest": g.apply(
        lambda x: x.loc[(x["untested"] == 1).reindex(x.index).fillna(0).astype(bool)].pipe(
            lambda z: z.loc[z["dist_pts"].idxmin(), "y"] if len(z) else x.loc[x["dist_pts"].idxmin(), "y"]
        ), include_groups=False),
    "highest_vol_nearest": g.apply(lambda x: x.loc[x["vol_rank"].idxmax(), "y"], include_groups=False),
})
for c in heur.columns:
    print(f"  heuristic: {c:27s} {heur[c].mean()*100:8.1f}%  {len(heur):8,}")
print(f"  random candidate                     {te3['y'].mean()*100:8.1f}%")

# --------------------------------------------------- which features matter
print("\n--- top struct/flow features, gradient boosting (permutation-free, gain proxy) ---")
from sklearn.inspection import permutation_importance
m = HistGradientBoostingClassifier(max_iter=250, random_state=SEED)
cols = STRUCT + FLOW
m.fit(tr[cols].to_numpy(np.float64), y_tr)
sample = te.sample(min(8000, len(te)), random_state=SEED)
pi = permutation_importance(m, sample[cols].to_numpy(np.float64),
                            sample["y"].to_numpy(), n_repeats=4,
                            random_state=SEED, scoring="roc_auc")
order = np.argsort(-pi.importances_mean)[:15]
print("  feature                          AUC drop when shuffled")
for i in order:
    print(f"    {cols[i]:28s} {pi.importances_mean[i]:+.4f}")

# ------------------------------------------- export logistic coefficients for C#
lr = LogisticRegression(max_iter=4000, C=0.5)
cols = STRUCT + FLOW
X = df[cols].to_numpy(np.float64)
mu, sd = X.mean(0), X.std(0)
sd[sd == 0] = 1
lr.fit((X - mu) / sd, df["y"].to_numpy())
coef = pd.DataFrame({
    "feature": ["__intercept__"] + cols,
    "mean": np.concatenate([[0.0], mu]),
    "std": np.concatenate([[1.0], sd]),
    "weight": np.concatenate([[lr.intercept_[0]], lr.coef_[0]]),
})
coef.to_csv(f"{OUT}/logistic_coefficients.csv", index=False)
print(f"\nwrote logistic coefficients ({len(coef)} rows) -> {OUT}/logistic_coefficients.csv")

# per-type empirical PoT on the full design set, for the indicator's prior table
tab = df.groupby(["type", "stop"]).agg(pot=("y", "mean"), n=("y", "size")).reset_index()
tab.to_csv(f"{OUT}/pot_by_type.csv", index=False)
print(f"wrote per-type PoT table -> {OUT}/pot_by_type.csv")

# distance-bucketed PoT (interpretable rule table the indicator can fall back on)
df["dist_bucket"] = pd.cut(df["dist_atr"], [0, 1, 2, 3, 5, 8, 100])
df["time_bucket"] = pd.cut(df["time_in"], [-1, 60, 150, 240, 600],
                           labels=["early", "mid", "late", "verylate"])
grid = df.groupby(["type", "dist_bucket", "time_bucket", "stop"], observed=True).agg(
    pot=("y", "mean"), n=("y", "size")).reset_index()
grid = grid[grid["n"] >= 30]
grid.to_csv(f"{OUT}/pot_lookup_grid.csv", index=False)
print(f"wrote interpretable PoT lookup grid ({len(grid)} cells) -> {OUT}/pot_lookup_grid.csv")
