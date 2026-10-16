"""
04_build_dataset.py — build the master 1-min dataset + structural liquidity levels.

Sources (all MNQ / NQ, identical price grid, tick 0.25):
  A) mnq_flow.csv.gz       2019-07 -> 2026-09   24h OHLC, order flow RTH-only
  B) mnq_bidask.csv        2026-06-24 -> 10-02  24h OHLC, volume RTH-only
  C) footprint (2).zip     2026-02-16 -> 10-02  RTH per-price bid/ask (the good stuff)

Design decisions that matter:
  * RTH bars inside the footprint window are rebuilt FROM the footprint, so
    bar OHLC and per-price order flow are guaranteed consistent with each other.
  * Overnight levels (Asia / London / ON) come from the 24h OHLC sources, since
    the footprint file has no overnight data at all.
  * Everything is timestamped in ET and snapped to a *trading day* that opens at
    18:00 ET the previous calendar evening.

Output: /tmp/mnq/derived/bars.parquet + levels.parquet
"""
import gzip
import json
import os
import zipfile

import numpy as np
import pandas as pd

ET = "America/New_York"
OUT = "/tmp/mnq/derived"
os.makedirs(OUT, exist_ok=True)

FLOW = "/home/user/ss/mnq_flow.csv.gz"
BIDASK = "/home/user/ss/mnq_bidask.csv"
FP_ZIP = "/home/user/ss/mnq_bidask.footprint (2).zip"
FP_CSV = "/tmp/mnq/footprint_full.csv"

TICK = 0.25


def load_footprint_bars():
    """Rebuild 1-min RTH bars from per-price footprint rows."""
    print("  reading footprint (5.2M rows)...")
    df = pd.read_csv(
        FP_CSV,
        dtype={"timestamp": "int64", "price": "float32",
               "bid_volume": "int32", "ask_volume": "int32",
               "delta": "int32", "trades": "int32"},
    )
    g = df.groupby("timestamp", sort=True)
    bars = g.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        bid_volume=("bid_volume", "sum"),
        ask_volume=("ask_volume", "sum"),
        ntrades=("trades", "sum"),
    ).reset_index()
    bars["volume"] = bars["bid_volume"] + bars["ask_volume"]
    bars["delta"] = bars["ask_volume"] - bars["bid_volume"]
    bars["src"] = "footprint"
    print(f"  footprint bars: {len(bars):,} over {bars['timestamp'].nunique():,} minutes")
    return bars, df


def load_24h_bars():
    """Union of the two 24h OHLC sources, deduped, footprint window excluded later."""
    print("  reading long history...")
    a = pd.read_csv(gzip.open(FLOW, "rt"), on_bad_lines="skip")
    for c in ["open", "high", "low", "close", "volume", "bid_volume",
              "ask_volume", "delta", "trades", "ask_trades", "bid_trades"]:
        a[c] = pd.to_numeric(a[c], errors="coerce").fillna(0)
    a = a[["timestamp", "open", "high", "low", "close", "volume",
           "bid_volume", "ask_volume"]]
    a["src"] = "flow"

    print("  reading mnq_bidask.csv...")
    b = pd.read_csv(BIDASK)
    b["delta"] = b["ask_volume"] - b["bid_volume"]
    b = b[["timestamp", "open", "high", "low", "close", "volume",
           "bid_volume", "ask_volume"]]
    b["src"] = "bidask"

    allb = pd.concat([a, b], ignore_index=True)
    # same minute in both 24h sources: prefer the denser recent one (bidask)
    allb["prio"] = (allb["src"] == "bidask").astype(int)
    allb = allb.sort_values(["timestamp", "prio"]).drop_duplicates(
        "timestamp", keep="last").drop(columns="prio")
    allb = allb.sort_values("timestamp").reset_index(drop=True)
    print(f"  24h bars: {len(allb):,}")
    return allb


def to_et(df):
    dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(ET)
    df = df.copy()
    df["dt"] = dt
    df["date"] = dt.dt.date
    df["hm"] = dt.dt.hour * 60 + dt.dt.minute          # minutes past midnight ET
    return df


# ---------------------------------------------------------------- trading day
def trading_day(hm_date, hm):
    """
    Trading day = session that opens 18:00 ET the prior evening.
    Anything at/after 18:00 belongs to the NEXT calendar day's trading session.
    """
    shift = (hm >= 18 * 60).astype(int)
    return pd.to_datetime(hm_date) + pd.to_timedelta(shift, unit="D")


def main():
    bars_fp, fp = load_footprint_bars()
    bars_fp = to_et(bars_fp)
    fp["dt"] = pd.to_datetime(fp["timestamp"], unit="ms", utc=True).dt.tz_convert(ET)
    fp["date"] = fp["dt"].dt.date
    fp["hm"] = fp["dt"].dt.hour * 60 + fp["dt"].dt.minute

    bars24 = load_24h_bars()
    bars24 = to_et(bars24)

    # sanity: does footprint-derived OHLC agree with the 24h source where both exist?
    m = bars24.merge(bars_fp[["timestamp", "open", "high", "low", "close"]],
                     on="timestamp", suffixes=("_24", "_fp"))
    m = m[(m["hm"] >= 9 * 60 + 30) & (m["hm"] < 16 * 60)]
    if len(m):
        agree = ((m["high_24"] - m["high_fp"]).abs() < 0.26).mean()
        print(f"\n  cross-check on {len(m):,} overlapping RTH minutes: "
              f"high agrees within 1 tick on {agree*100:.2f}%")

    # RTH bars from footprint win inside its window
    fp_lo, fp_hi = bars_fp["timestamp"].min(), bars_fp["timestamp"].max()
    keep24 = bars24[(bars24["timestamp"] < fp_lo) | (bars24["timestamp"] > fp_hi)].copy()
    bars = pd.concat([keep24, bars_fp], ignore_index=True)
    bars = bars.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    bars["dt"] = pd.to_datetime(bars["timestamp"], unit="ms", utc=True).dt.tz_convert(ET)
    bars["date"] = bars["dt"].dt.date
    bars["hm"] = bars["dt"].dt.hour * 60 + bars["dt"].dt.minute
    bars["td"] = trading_day(bars["date"], bars["hm"])

    # ---------------- session flags ----------------
    hm = bars["hm"]
    bars["is_rth"] = (hm >= 9 * 60 + 30) & (hm < 16 * 60)
    bars["is_asia"] = (hm >= 18 * 60) | (hm < 3 * 60)
    bars["is_london"] = (hm >= 3 * 60) & (hm < 9 * 60 + 30)
    bars["is_ib"] = (hm >= 9 * 60 + 30) & (hm < 10 * 60 + 30)

    print(f"\n  total bars {len(bars):,}")
    print(f"  RTH bars {bars['is_rth'].sum():,}  days(td) {bars['td'].nunique():,}")

    # ---------------- structural levels per trading day ----------------
    rth = bars[bars["is_rth"]]
    g = rth.groupby("td")
    rth_hl = g.agg(rth_high=("high", "max"), rth_low=("low", "min"),
                   rth_open=("open", "first"), rth_close=("close", "last"))
    ib = bars[bars["is_ib"]].groupby("td").agg(ib_high=("high", "max"),
                                               ib_low=("low", "min"))
    on = bars[bars["is_asia"] | bars["is_london"]].groupby("td").agg(
        on_high=("high", "max"), on_low=("low", "min"))
    asia = bars[bars["is_asia"]].groupby("td").agg(
        asia_high=("high", "max"), asia_low=("low", "min"))
    lon = bars[bars["is_london"]].groupby("td").agg(
        lon_high=("high", "max"), lon_low=("low", "min"))

    lv = pd.concat([rth_hl, ib, on, asia, lon], axis=1).reset_index()
    lv = lv.sort_values("td").reset_index(drop=True)
    # prior trading day RTH reference (the classic PDH/PDL/PDC)
    lv["pdh"] = lv["rth_high"].shift(1)
    lv["pdl"] = lv["rth_low"].shift(1)
    lv["pdc"] = lv["rth_close"].shift(1)
    lv["pd_range"] = lv["pdh"] - lv["pdl"]
    print(f"  level table: {len(lv):,} trading days, "
          f"{lv['pdh'].notna().sum():,} with prior-day reference")

    lv.to_parquet(f"{OUT}/levels.parquet")
    bars.to_parquet(f"{OUT}/bars.parquet")

    # per-price volumes for profile features (footprint window only)
    print("  saving per-price volume profile table...")
    fp["td"] = trading_day(fp["date"], fp["hm"])
    prof = fp.groupby(["td", "price"], as_index=False)[["bid_volume", "ask_volume"]].sum()
    prof["volume"] = prof["bid_volume"] + prof["ask_volume"]
    prof.to_parquet(f"{OUT}/price_volume.parquet")
    print(f"  profile rows: {len(prof):,}")

    meta = {
        "tick": TICK,
        "fp_window": [int(fp_lo), int(fp_hi)],
        "days": int(bars["td"].nunique()),
        "rth_bars": int(bars["is_rth"].sum()),
        "fp_days": int(prof["td"].nunique()),
    }
    with open(f"{OUT}/meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print("\n" + json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
