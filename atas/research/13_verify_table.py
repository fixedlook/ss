"""
13_verify_table.py — QA on the generated PoT table before it ships in the indicator.

Recomputes nothing heavy: loads the raw strict-design rows and re-derives the
summary, then checks the generated artefacts for the kinds of failure that would
silently produce wrong percentages inside ATAS:

  1. every (group, stop, rr, time) cell present in the JSON
  2. monotonicity: PoT must FALL as R:R rises, at fixed stop
  3. monotonicity: PoT must RISE with stop size at fixed R:R
  4. plausibility band: all values in (0, 1), none above 0.99
  5. why sess_extreme vanished from one slice
"""
import json
import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
GROUPS = ["session_hl", "sess_extreme", "pdc", "swing", "fvg"]
RR_LBL = ["<0.5", "0.5-1", "1-1.5", "1.5-2", "2-3", "3-4", "4-6", "6+"]
STOP_GRID = (20, 40, 60)
MIN_N = 120

df = pd.read_parquet(f"{OUT}/pot_strict_final.parquet")
df["rr_b"] = pd.cut(df["rr"], [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 100], labels=False)
print(f"raw rows {len(df):,}")

tab = df.groupby(["grp", "stop", "rr_b", "tb"]).agg(pot=("y", "mean"), n=("y", "size")).reset_index()

# ---- 5: why did sess_extreme disappear from stop=40, tb=1 ? ----
sub = tab[(tab["stop"] == 40) & (tab["tb"] == 1)]
print("\ngroups present at stop=40, tb=1 (mid session):")
print(sub.groupby("grp")["n"].sum().to_string())
print("\nsess_extreme cells in the WHOLE table, by tb:")
print(tab[tab["grp"] == "sess_extreme"].groupby("tb").agg(
    cells=("n", "size"), rows=("n", "sum")).to_string())

# ---- 2 & 3: monotonicity ----
print("\n--- monotonicity check: PoT vs R:R, stop=40, all groups, pooled time ---")
piv = (df[df["stop"] == 40].groupby(["rr_b", "grp"])["y"].mean().unstack() * 100)
print(piv[GROUPS].round(1).to_string())
viol = []
for g in GROUPS:
    if g not in piv:
        continue
    v = piv[g].dropna().values
    if len(v) > 1 and np.any(np.diff(v) > 0.6):
        viol.append(g)
print(f"  groups violating 'PoT falls as R:R rises': {viol if viol else 'none'}")

print("\n--- monotonicity check: PoT vs stop size, pooled ---")
p2 = (df.groupby(["stop", "grp"])["y"].mean().unstack() * 100)
print(p2[GROUPS].round(1).to_string())
for g in GROUPS:
    if g in p2:
        v = p2[g].values
        if np.any(np.diff(v) < 0):
            print(f"  !! {g}: PoT does not increase with stop size: {v}")

# ---- 1 & 4: artefact integrity ----
with open(f"{OUT}/pot_table.json") as f:
    art = json.load(f)
tbl = art["table"]
print(f"\nJSON cells: {len(tbl)}  (expected {len(GROUPS)*3*8*3})")
missing = [f"{g}|{sp}|{rb}|{tb}" for g in GROUPS for sp in STOP_GRID
           for rb in range(8) for tb in range(3) if f"{g}|{sp}|{rb}|{tb}" not in tbl]
print(f"missing cells: {len(missing)}")
vals = np.array(list(tbl.values()))
print(f"value range: {vals.min():.4f} .. {vals.max():.4f}   "
      f"out-of-band: {int(((vals <= 0) | (vals >= 0.99)).sum())}")

# spot-check three cells against recomputation from raw
print("\n--- spot check: JSON vs recomputed from raw rows ---")
import itertools
bad = 0
for g, sp, rb, tb in itertools.product(GROUPS, STOP_GRID, range(8), range(3)):
    s = df[(df["grp"] == g) & (df["stop"] == sp) & (df["rr_b"] == rb) & (df["tb"] == tb)]
    if len(s) >= MIN_N:
        direct = s["y"].mean()
        got = tbl[f"{g}|{sp}|{rb}|{tb}"]
        if abs(direct - got) > 1e-4:
            bad += 1
            if bad <= 5:
                print(f"  MISMATCH {g} stop{sp} rr{RR_LBL[rb]} tb{tb}: "
                      f"direct={direct:.4f} json={got:.4f} (n={len(s)})")
print(f"  mismatches on cells with n>={MIN_N}: {bad}")

print("\n--- published surface (stop 40, mid session) ---")
pv = (tab[(tab["stop"] == 40) & (tab["tb"] == 1)]
      .pivot(index="rr_b", columns="grp", values="pot") * 100).reindex(columns=GROUPS)
print(pv.round(1).to_string())
print("\n--- n per cell (stop 40, mid) ---")
pn = (tab[(tab["stop"] == 40) & (tab["tb"] == 1)]
      .pivot(index="rr_b", columns="grp", values="n")).reindex(columns=GROUPS)
print(pn.fillna(0).astype(int).to_string())

print("\n--- best tradeable cells overall (n>=300, rr>1) ---")
good = tab[(tab["n"] >= 300) & (tab["rr_b"] >= 2)].copy()
good["rr_label"] = good["rr_b"].map(dict(enumerate(RR_LBL)))
good["grp_tag"] = good["grp"]
top = good.sort_values("pot", ascending=False).head(12)
print(top[["grp", "stop", "rr_label", "tb", "pot", "n"]].to_string(index=False))
