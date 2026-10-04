"""
Cross-validation: the NEW per-minute export (mnq_flow.csv.gz, run 2026-10-04 20:54)
against the OLD price-level footprint (mnq_bidask.footprint.csv, run 2026-10-04 ~11:49).

They are two independent runs of the same study over an overlapping window, so their
per-minute bid/ask totals must agree if the new file is trustworthy.

Run:  /tmp/venv/bin/python research/flow_vs_footprint.py
"""
import pandas as pd
import numpy as np

new = pd.read_parquet("/tmp/flow/flow_good.parquet")[
    ["timestamp", "ny", "date", "bid_volume", "ask_volume", "trades"]].copy()
new["date"] = new["date"].astype(str)

fp = pd.read_parquet("/tmp/newfp/full_fp.parquet")[
    ["timestamp", "bid_volume", "ask_volume", "trades", "dt_et"]].copy()
fp["date"] = fp["dt_et"].dt.strftime("%Y-%m-%d")

print("new file minutes :", len(new), " dates", new['date'].min(), "->", new['date'].max())
print("old footprint mins (rows aggregated):", len(fp), " dates", fp['date'].min(), "->", fp['date'].max())

old_min = fp.groupby("timestamp").agg(bid_old=("bid_volume", "sum"),
                                      ask_old=("ask_volume", "sum"),
                                      trades_old=("trades", "sum")).reset_index()

m = new.merge(old_min, on="timestamp", how="inner")
print("\noverlapping minutes:", len(m),
      " dates", m['date'].min(), "->", m['date'].max(),
      " sessions", m['date'].nunique())

if len(m) == 0:
    raise SystemExit("no overlap - check timestamp conventions")

m["db"] = m["bid_volume"] - m["bid_old"]
m["da"] = m["ask_volume"] - m["ask_old"]
m["dt_"] = m["trades"] - m["trades_old"]

tot_bid_new, tot_bid_old = m["bid_volume"].sum(), m["bid_old"].sum()
tot_ask_new, tot_ask_old = m["ask_volume"].sum(), m["ask_old"].sum()

print("\n=== totals over overlapping minutes ===")
print(f"bid  new {tot_bid_new:>12,}   old {tot_bid_old:>12,}   ratio {tot_bid_new/max(tot_bid_old,1):.6f}")
print(f"ask  new {tot_ask_new:>12,}   old {tot_ask_old:>12,}   ratio {tot_ask_new/max(tot_ask_old,1):.6f}")

exact = ((m["db"] == 0) & (m["da"] == 0)).sum()
print(f"\nminutes with bid AND ask identical: {exact:,} / {len(m):,}  ({100*exact/len(m):.2f}%)")
print("minutes with any difference       :", int(((m['db'] != 0) | (m['da'] != 0)).sum()))
print("  |diff| <= 1 contract            :", int((m['db'].abs().le(1) & m['da'].abs().le(1)).sum()))
print("  |diff| > 1 contract             :", int((m['db'].abs().gt(1) | m['da'].abs().gt(1)).sum()))

print("\n=== distribution of differences (new - old) ===")
print("bid diff:", m['db'].describe(percentiles=[.01, .5, .99]).to_string())
print("ask diff:", m['da'].describe(percentiles=[.01, .5, .99]).to_string())

print("\n=== per-session check ===")
s = m.groupby("date").agg(
    mins=("timestamp", "size"),
    bid_new=("bid_volume", "sum"), bid_old=("bid_old", "sum"),
    ask_new=("ask_volume", "sum"), ask_old=("ask_old", "sum"))
s["bid_ratio"] = s["bid_new"] / s["bid_old"].replace(0, np.nan)
s["ask_ratio"] = s["ask_new"] / s["ask_old"].replace(0, np.nan)
s["bid_eq"] = (s["bid_new"] == s["bid_old"])
s["ask_eq"] = (s["ask_new"] == s["ask_old"])
print("sessions:", len(s),
      " bid identical:", int(s['bid_eq'].sum()),
      " ask identical:", int(s['ask_eq'].sum()))
print("sessions where BOTH identical:", int((s['bid_eq'] & s['ask_eq']).sum()))
print()
print(s.sort_values("bid_ratio").head(8).round(5).to_string())
print()
print(s.sort_values("bid_ratio").tail(8).round(5).to_string())

print("\n=== old footprint final 5 sessions (not covered by the new file) ===")
covered = set(m["date"].unique())
last = sorted(set(fp["date"].unique()) - covered)[-5:]
print(last)

m.to_parquet("/tmp/flow/flow_vs_fp.parquet", index=False)
print("\nwrote /tmp/flow/flow_vs_fp.parquet")
