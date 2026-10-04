"""
Recon of the first real per-minute bid/ask export (mnq_flow.csv.gz).

Answers, in order:
  1. what date range / how many bars
  2. which bars carry actual tick flow (bid/ask > 0) and which are zero-flow overnight bars
  3. monthly coverage of *flow* bars  -> does the 14-month hole show up here too?
  4. sanity: is delta == ask - bid, do RTH bars start at 09:30 ET, are prices sane
  5. how many sessions and how many usable RTH minutes we actually have

Run:  python research/flow_recon.py /tmp/flow/f.csv
"""
import sys
import pandas as pd
import numpy as np

PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/flow/f.csv"

COLS = ["timestamp", "open", "high", "low", "close", "volume",
        "bid_volume", "ask_volume", "delta", "trades", "ask_trades", "bid_trades"]

print("reading ...")
raw = pd.read_csv(PATH, header=0, usecols=COLS, dtype={"timestamp": "int64"})
print("raw rows            :", len(raw))

# torn rows: everything the interleaved-writer glitch produced
df = raw.rename(columns={"timestamp": "ts"}).dropna(subset=["ts", "close", "bid_volume", "ask_volume"])
print("rows after dropna   :", len(df), f"(dropped {len(raw)-len(df)})")

# a torn row usually shows up as an absurd volume/price; clamp obviously bad values
bad = (df["bid_volume"] < 0) | (df["ask_volume"] < 0) | (df["close"] <= 0)
print("impossible values   :", int(bad.sum()))
df = df[~bad].copy()

df["dt_utc"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df["ny"] = df["dt_utc"].dt.tz_convert("America/New_York")
df["date"] = df["ny"].dt.date
df["year"] = df["ny"].dt.year
df["month"] = df["ny"].dt.strftime("%Y-%m")
df["tod"] = df["ny"].dt.strftime("%H:%M")

df["flow"] = df["bid_volume"] + df["ask_volume"]
df["has_flow"] = df["flow"] > 0

print()
print("=== 1. range ===")
print("first bar (NY):", df["ny"].min(), "  last bar (NY):", df["ny"].max())
print("bars total     :", len(df))

print()
print("=== 2. flow vs zero-flow bars ===")
print("bars with flow     :", int(df["has_flow"].sum()))
print("bars without flow  :", int((~df["has_flow"]).sum()))
print("first bar WITH flow:", df.loc[df["has_flow"], "ny"].min())
print("last  bar WITH flow:", df.loc[df["has_flow"], "ny"].max())

print()
print("=== 3. sessions with flow, by year ===")
sess = df[df["has_flow"]].groupby("date").agg(
    bars=("ts", "size"), minutes_flow=("has_flow", "sum"),
    vol=("flow", "sum"), first=("tod", "min"), last=("tod", "max"))
sess["year"] = pd.to_datetime(sess.index).year
print(sess.groupby("year").agg(sessions=("bars", "size"),
                               med_bars_per_session=("bars", "median"),
                               med_first=("first", "min"),
                               med_last=("last", "max")).to_string())

print()
print("=== 3b. by month (sessions with flow) ===")
m = sess.groupby(sess.index.astype(str).str[:7]).size()
print(m.to_string())

print()
print("=== 4. sanity checks ===")
sub = df[df["has_flow"]]
d = (sub["ask_volume"] - sub["bid_volume"]) - sub["delta"]
print("delta == ask-bid exact:", bool((d == 0).all()), " mismatches:", int((d != 0).sum()))
print("delta recomputed above is what we will use (no reliance on the file's own column)")

# where do flow bars sit in the clock?
tod_counts = sub.groupby("tod").size().sort_index()
print()
print("flow bars by time-of-day (first/last 8 slots):")
print(tod_counts.head(8).to_string(), "\n...\n", tod_counts.tail(8).to_string())

# price sanity
print()
print("close range:", float(df["close"].min()), "->", float(df["close"].max()))

print()
print("=== 5. usable universe ===")
print("sessions with flow      :", sess.shape[0])
print("median flow-bars/session:", float(sess['bars'].median()))
print("total flow bars         :", int(df['has_flow'].sum()))
sess2 = sess.reset_index().rename(columns={"ny": "date"})
print()
print(sess2.groupby("year")["bars"].agg(["size", "median", "min", "max"]).to_string())
