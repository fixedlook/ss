"""
03_flow_coverage.py — when is order-flow (bid/ask) data actually trustworthy?

mnq_flow.csv.gz spans 2019-07..2026-09 with 24h coverage, but only ~433k rows
carry non-zero bid/ask volume. The structural model can use all 1,762 days;
the order-flow model can only use the period where delta is dense.

Also establishes the session grid: Asia / London / NY in ET terms.
"""
import gzip
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

df = pd.read_csv(
    gzip.open("/home/user/ss/mnq_flow.csv.gz", "rt"),
    on_bad_lines="skip",
)
# the raw export has a few blank/non-numeric cells; coerce rather than assume
for c in ["open", "high", "low", "close", "volume", "bid_volume", "ask_volume",
          "delta", "trades", "ask_trades", "bid_trades"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
df["volume"] = df["volume"].astype("int64")
for c in ["bid_volume", "ask_volume", "delta", "trades", "ask_trades", "bid_trades"]:
    df[c] = df[c].astype("int32")
t = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(ET)
df["dt"] = t
df["ym"] = t.dt.strftime("%Y-%m")
df["has_flow"] = (df["bid_volume"] + df["ask_volume"]) > 0

print("--- monthly order-flow coverage (share of bars with bid/ask data) ---")
cov = df.groupby("ym").agg(bars=("has_flow", "size"), with_flow=("has_flow", "sum"))
cov["pct"] = (cov["with_flow"] / cov["bars"] * 100).round(1)
# print compactly: 6 months per line
items = list(cov.iterrows())
for i in range(0, len(items), 6):
    chunk = items[i:i+6]
    print("  " + "   ".join(f"{m}:{r.pct:5.1f}%" for m, r in chunk))

solid = cov[cov["pct"] > 90]
if len(solid):
    print(f"\nfirst month with >90% flow coverage: {solid.index[0]}")

# --- session grid in ET -----------------------------------------------------
hour = t.dt.hour + t.dt.minute / 60
def label(h):
    if 18 <= h or h < 3:  return "Asia"
    if 3 <= h < 9.5:      return "London"
    if 9.5 <= h < 16:     return "NY_RTH"
    return "PM"

df["session"] = hour.map(label)
print("\n--- volume share by session (whole history) ---")
vol = df.groupby("session")["volume"].sum()
print((vol / vol.sum() * 100).round(1).to_string())

print("\n--- volume share by session, 2025+ only ---")
recent = df[t >= "2025-01-01"]
volr = recent.groupby("session")["volume"].sum()
print((volr / volr.sum() * 100).round(1).to_string())

# --- per-session extreme times ---------------------------------------------
print("\n--- typical NY_RTH window check ---")
rth = df[df["session"] == "NY_RTH"]
print(f"  bars {len(rth):,}  days {rth['dt'].dt.date.nunique():,}")
print(f"  mean bars/day {len(rth)/rth['dt'].dt.date.nunique():.0f}  (expect ~390)")
