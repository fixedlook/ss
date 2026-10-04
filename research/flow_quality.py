"""
Quality check of mnq_flow.csv.gz before any statistics are run on it.

  A. is the file time-ordered?  where does it really start/end?
  B. which rows are corrupt (impossible prices)?  how many?
  C. per-session coverage: how many sessions have a *full* RTH read (>=300 flow bars)?
  D. when did the tick read stop vs when do the bars stop?
  E. does the flow column agree with the old footprint export (mnq_bidask.footprint)?

Run:  python research/flow_quality.py /tmp/flow/f.csv
"""
import sys
import pandas as pd
import numpy as np

PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/flow/f.csv"
COLS = ["timestamp", "open", "high", "low", "close", "volume",
        "bid_volume", "ask_volume", "delta", "trades", "ask_trades", "bid_trades"]

raw = pd.read_csv(PATH, header=0, usecols=COLS, dtype={"timestamp": "int64"})
n_raw = len(raw)
df = raw.dropna(subset=["timestamp", "close", "bid_volume", "ask_volume"]).copy()
print(f"raw rows {n_raw}   clean rows {len(df)}   torn/NaN rows {n_raw-len(df)}")

df["flow"] = df["bid_volume"] + df["ask_volume"]

print("\n=== A. ordering ===")
mono = (df["timestamp"].diff().dropna() >= 0).all()
print("timestamps non-decreasing:", bool(mono))
print("min ts:", df["timestamp"].min(), "  max ts:", df["timestamp"].max())
ny = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("America/New_York")
print("min time NY:", ny.min(), "  max time NY:", ny.max())
print("total bars  :", len(df))

# how many drops / regressions in time order
d = df["timestamp"].diff()
print("rows where time went backwards:", int((d < 0).sum()),
      "  rows where time repeated:", int((d == 0).sum()))

print("\n=== D. last flow vs last bar ===")
fl = df[df["flow"] > 0]
flny = pd.to_datetime(fl["timestamp"], unit="ms", utc=True).dt.tz_convert("America/New_York")
print("first flow bar:", flny.min())
print("last  flow bar:", flny.max())
print("last 10 bars in file:")
tail = df.tail(10).copy()
tail["ny"] = pd.to_datetime(tail["timestamp"], unit="ms", utc=True).dt.tz_convert("America/New_York")
print(tail[["ny", "close", "volume", "bid_volume", "ask_volume"]].to_string(index=False))

print("\n=== B. corrupt rows ===")
sus = df[(df["close"] > 100000) | (df["close"] < 1000) |
         (df["high"] > 100000) | (df["low"] < 1000) |
         (df["bid_volume"] > 1e7) | (df["ask_volume"] > 1e7)]
print("suspicious rows:", len(sus))
if len(sus):
    s2 = sus.copy()
    s2["ny"] = pd.to_datetime(s2["timestamp"], unit="ms", utc=True).dt.tz_convert("America/New_York")
    print(s2[["ny", "open", "high", "low", "close", "volume", "bid_volume", "ask_volume"]].head(40).to_string(index=False))

good = df[(df["close"].between(1000, 100000)) & (df["high"].between(1000, 100000)) &
          (df["low"].between(1000, 100000)) & (df["volume"] >= 0) &
          (df["bid_volume"] >= 0) & (df["ask_volume"] >= 0) &
          (df["bid_volume"] < 1e7) & (df["ask_volume"] < 1e7)].copy()
good["flow"] = good["bid_volume"] + good["ask_volume"]
good["ny"] = pd.to_datetime(good["timestamp"], unit="ms", utc=True).dt.tz_convert("America/New_York")
good["date"] = good["ny"].dt.date
print("\nafter outlier filter:", len(good))

print("\n=== C. per-session coverage (flow bars per session) ===")
sess = good[good["flow"] > 0].groupby("date").agg(
    bars=("timestamp", "size"), vol=("flow", "sum"),
    bid=("bid_volume", "sum"), ask=("ask_volume", "sum"))
sess["year"] = pd.to_datetime(sess.index).year
b = sess["bars"]
print("sessions with any flow :", len(sess))
print("  >=450 bars:", int((b >= 450).sum()))
print("  >=400 bars:", int((b >= 400).sum()))
print("  >=300 bars:", int((b >= 300).sum()))
print("  100-299   :", int(((b >= 100) & (b < 300)).sum()))
print("  <100 bars :", int((b < 100).sum()))
print()
print(sess.groupby("year").agg(
    any_flow=("bars", "size"),
    full_400=("bars", lambda x: int((x >= 400).sum())),
    median_bars=("bars", "median"),
    total_vol=("vol", "sum")).to_string())
print("\n(bars per session > 450 = flow seen outside the 09:30-17:00 window)")

print("\n=== E. spot totals vs old footprint (handled in a separate step) ===")
print("median bars/session on good sessions:", float(b[b >= 300].median()))

good.to_parquet("/tmp/flow/flow_good.parquet", index=False)
print("\nwrote /tmp/flow/flow_good.parquet")
