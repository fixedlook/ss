"""
09_compare_types.py — is the PD-array advantage real, or a distance artefact?

The PD run produced PoT ~88% for FVGs at a 40pt stop, versus ~29% for session
levels in the main study. That is far too large to be a genuine edge, and there
is an obvious confound: the two studies did NOT sample the same distances.

A "nearest unswept swing" can sit 2 ticks from price (a fresh 3-bar fractal),
while PDH can be 200 points away. Touch probability is dominated by distance,
so comparing the two overall averages is meaningless.

The only honest comparison holds distance fixed. This script does that.
"""
import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"

sess = pd.read_parquet(f"{OUT}/design_fp.parquet")[["type", "stop", "dist_pts", "y"]]
pdarr = pd.read_parquet(f"{OUT}/design_pd.parquet")[["type", "stop", "dist_pts", "y"]]

print("--- distance distribution (points) by level type ---")
for name, d in [("SESSION LEVELS", sess), ("PD ARRAYS", pdarr)]:
    print(f"\n{name}")
    print(d.groupby("type")["dist_pts"].describe()[["count", "25%", "50%", "75%"]].to_string())

print("\n--- overall PoT (the misleading comparison) ---")
allx = pd.concat([sess, pdarr])
print(allx.groupby(["type", "stop"])["y"].mean().unstack().round(3).to_string())

# ------------------------------------------------------------------ matched test
BINS = [0, 10, 20, 40, 60, 100, 150, 250]
LBL = ["0-10", "10-20", "20-40", "40-60", "60-100", "100-150", "150-250"]
allx["dist_bucket"] = pd.cut(allx["dist_pts"], BINS, labels=LBL)

print("\n--- PoT at 40pt stop, WITHIN IDENTICAL DISTANCE BUCKETS ---")
print("(this is the only apples-to-apples comparison)\n")
piv = allx[allx["stop"] == 40].groupby(["dist_bucket", "type"], observed=True).agg(
    pot=("y", "mean"), n=("y", "size")).reset_index()
piv = piv[piv["n"] >= 150]
tab = piv.pivot(index="dist_bucket", columns="type", values="pot") * 100
cnt = piv.pivot(index="dist_bucket", columns="type", values="n")
order = [c for c in ["PDH", "PDL", "PDC", "IBH", "IBL", "SESSH", "SESSL",
                     "SWINGH", "SWINGL", "FVGUP", "FVGDN"] if c in tab.columns]
print(tab[order].round(1).to_string())

print("\n--- same table, sample sizes ---")
print(cnt[order].fillna(0).astype(int).to_string())

# ------------------------------------------------------------------ verdict calc
print("\n--- mean PoT across distance buckets, per type (equal-weighted) ---")
eq = tab[order].mean().sort_values(ascending=False)
for k, v in eq.items():
    grp = "SESSION" if k in {"PDH", "PDL", "PDC", "IBH", "IBL", "SESSH", "SESSL"} else "PD-ARRAY"
    print(f"   {k:8s} {v:6.1f}%   [{grp}]")

sess_m = eq[[c for c in eq.index if c in
             {"PDH", "PDL", "PDC", "IBH", "IBL", "SESSH", "SESSL"}]].mean()
pd_m = eq[[c for c in eq.index if c in {"SWINGH", "SWINGL", "FVGUP", "FVGDN"}]].mean()
print(f"\n  session-level average : {sess_m:.1f}%")
print(f"  PD-array average      : {pd_m:.1f}%")
print(f"  difference            : {pd_m - sess_m:+.1f} points")

# ------------------------------------------------------------------ distance matched pairs
print("\n--- paired comparison: nearest candidate of EACH type, same minute ---")
key = ["td", "direction", "stop", "time_in"] if "td" in sess.columns else None
print("  (design files were written without minute keys; bucket table above is the fair test)")

# ------------------------------------------------------------------ how far is "nearest"?
print("\n--- how close are 'nearest unswept' levels in practice? ---")
print(sess.groupby("type")["dist_pts"].median().sort_values().round(1).to_string())
