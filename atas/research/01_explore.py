"""
01_explore.py — structural reconnaissance of the MNQ footprint dataset.

Goal: understand session boundaries, tick size, timestamp ordering, and
what target levels are even representable before designing features.

Input : /tmp/mnq/footprint_full.csv   (extracted from mnq_bidask.footprint (2).zip)
Output: printed profile, no files written.
"""
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

SRC = "/tmp/mnq/footprint_full.csv"
ET = ZoneInfo("America/New_York")

dtypes = {
    "timestamp": "int64",
    "price": "float32",
    "bid_volume": "int32",
    "ask_volume": "int32",
    "delta": "int32",
    "trades": "int32",
}

print("loading...")
df = pd.read_csv(SRC, dtype=dtypes)
print(f"rows={len(df):,}  mem={df.memory_usage(deep=True).sum()/1e6:.0f} MB")

# --- timestamp sanity -------------------------------------------------------
ts = df["timestamp"].to_numpy()
print("sorted ascending:", bool(np.all(np.diff(ts) >= 0)))
print("distinct minutes:", df["timestamp"].nunique())

# --- tick size --------------------------------------------------------------
prices = np.sort(df["price"].unique())
diffs = np.diff(prices)
vals, cnts = np.unique(np.round(diffs, 4), return_counts=True)
print("\ntop price gaps (tick size candidates):")
for v, c in sorted(zip(vals, cnts), key=lambda x: -x[1])[:5]:
    print(f"   gap={v:<10} count={c:,}")
print(f"price range: {prices[0]} .. {prices[-1]}  ({len(prices):,} distinct)")

# --- session structure ------------------------------------------------------
df["dt_utc"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
df["dt_et"] = df["dt_utc"].dt.tz_convert(ET)
df["date_et"] = df["dt_et"].dt.date
tod = df["dt_et"].dt.hour + df["dt_et"].dt.minute / 60

print("\n--- rows per ET hour (aggregated over whole sample) ---")
byhour = df.groupby(df["dt_et"].dt.hour).size()
for h, c in byhour.items():
    print(f"   {h:02d}:00 ET  {c:>10,}  {'#' * int(c / byhour.max() * 50)}")

print("\n--- per-day row counts (first 5 / last 5 days) ---")
per_day = df.groupby("date_et").size()
print(per_day.head(5).to_string())
print("   ...")
print(per_day.tail(5).to_string())
print(f"\ndays={len(per_day)}  median rows/day={per_day.median():,.0f}")

# --- does the day start at 18:00 ET (Globex) or 09:30 (RTH)? ----------------
g = df.groupby("date_et")["dt_et"]
starts = g.min().dt.strftime("%H:%M")
ends = g.max().dt.strftime("%H:%M")
print("\nmost common first-minute-of-day (ET):")
print(starts.value_counts().head(5).to_string())
print("\nmost common last-minute-of-day (ET):")
print(ends.value_counts().head(5).to_string())

# --- RTH volume share -------------------------------------------------------
rth = df[(tod >= 9.5) & (tod < 16.0)]
print(f"\nRTH rows {len(rth):,} / {len(df):,} = {len(rth)/len(df)*100:.1f}%")
print(f"RTH volume {rth['bid_volume'].sum()+rth['ask_volume'].sum():,}")
print(f"ALL volume {df['bid_volume'].sum()+df['ask_volume'].sum():,}")

# --- typical daily range, for sanity-checking level distances ---------------
rth_bars = rth.groupby("timestamp").agg(h=("price", "max"), l=("price", "min"))
daily = rth_bars.groupby(rth_bars.index // 86400000).agg(hi=("h", "max"), lo=("l", "min"))
daily["range"] = daily["hi"] - daily["lo"]
print("\nRTH daily range (points):")
print(daily["range"].describe().to_string())
