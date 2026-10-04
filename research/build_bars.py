"""
Aggregate the MNQ bid/ask footprint export into 1-minute bars.

Input : data/mnq_bidask.footprint.csv   (extracted from mnq_bidask.footprint.zip)
Output: data/mnq_1min.parquet

NOTE ON LIMITATIONS
-------------------
The footprint export lists, for each 1-minute timestamp, every traded price
level with its bid volume, ask volume, delta and trade count.  The rows are
written in *price order* (verified: 100% of sampled minutes are strictly
ascending), NOT in time order.  Therefore the true intrabar path is lost and
we can only recover, per minute:

    high, low, total volume, bid volume, ask volume, delta, trades,
    VWAP, POC (highest-volume price of the minute), number of traded levels

Open/close prices per bar are NOT recoverable from this file.  Any study or
backtest that needs bar open/close must take it from a second price file.
"""

from __future__ import annotations

import os
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
ZIP = os.path.join(ROOT, "mnq_bidask.footprint.zip")
CSV = os.path.join(DATA, "mnq_bidask.footprint.csv")
OUT = os.path.join(DATA, "mnq_1min.parquet")

COLS = ["timestamp", "price", "bid_volume", "ask_volume", "delta", "trades"]


def load_footprint() -> pd.DataFrame:
    os.makedirs(DATA, exist_ok=True)
    if not os.path.exists(CSV):
        with zipfile.ZipFile(ZIP) as z:
            z.extract("mnq_bidask.footprint.csv", DATA)
    df = pd.read_csv(
        CSV,
        header=0,
        names=COLS,
        dtype={
            "timestamp": "int64",
            "price": "float64",
            "bid_volume": "int64",
            "ask_volume": "int64",
            "delta": "int64",
            "trades": "int64",
        },
    )
    return df


def build_bars(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["volume"] = df["bid_volume"] + df["ask_volume"]
    traded = df[df["volume"] > 0].copy()

    traded["pv"] = traded["price"] * traded["volume"]
    g = traded.groupby("timestamp", sort=True)

    bars = pd.DataFrame(
        {
            "high": g["price"].max(),
            "low": g["price"].min(),
            "volume": g["volume"].sum(),
            "bid_volume": g["bid_volume"].sum(),
            "ask_volume": g["ask_volume"].sum(),
            "delta": g["delta"].sum(),
            "trades": g["trades"].sum(),
            "levels": g["price"].size(),
            "pv": g["pv"].sum(),
        }
    )
    bars["vwap"] = bars["pv"] / bars["volume"]

    # Point of control: highest-volume price of the minute.
    idx = traded.groupby("timestamp")["volume"].idxmax()
    bars["poc"] = traded.loc[idx].set_index("timestamp")["price"]

    # Zero-volume levels present in the raw file (sanity metric).
    bars["zero_vol_levels"] = df.groupby("timestamp").size() - bars["levels"]

    bars = bars.drop(columns=["pv"]).reset_index()
    bars["dt_utc"] = pd.to_datetime(bars["timestamp"], unit="ms", utc=True)
    bars["dt_et"] = bars["dt_utc"].dt.tz_convert("America/New_York")
    bars["session_date"] = bars["dt_et"].dt.date
    bars["minute_of_day"] = bars["dt_et"].dt.hour * 60 + bars["dt_et"].dt.minute
    bars["signed_vol"] = bars["delta"]
    return bars


def report(bars: pd.DataFrame) -> None:
    print(f"minutes: {len(bars):,}   days: {bars['session_date'].nunique():,}")
    print(f"range : {bars['dt_utc'].min()}  ->  {bars['dt_utc'].max()}")
    print(
        f"price : {bars['low'].min():.2f} low .. {bars['high'].max():.2f} high "
        f"(last-minute close proxy: {bars['vwap'].iloc[-1]:.2f} VWAP)"
    )
    print(f"volume: {bars['volume'].sum():,} contracts   trades: {bars['trades'].sum():,}")
    print(f"delta : {bars['delta'].sum():,} cumulative")
    print(f"session minutes/day: median {bars.groupby('session_date').size().median():.0f}")
    print("\nper-day volume (first 5 / last 5):")
    vol = bars.groupby("session_date")["volume"].sum()
    print(vol.head().to_string())
    print("...")
    print(vol.tail().to_string())
    print("\nminute-of-day coverage (ET):")
    mod = bars.groupby("minute_of_day").size()
    print(f"  earliest {bars['minute_of_day'].min()} ({bars['minute_of_day'].min() // 60:02d}:{bars['minute_of_day'].min() % 60:02d} ET)"
          f"  latest {bars['minute_of_day'].max()} ({bars['minute_of_day'].max() // 60:02d}:{bars['minute_of_day'].max() % 60:02d} ET)")
    print(f"  minutes present in >90% of days: {(mod > 0.9 * bars['session_date'].nunique()).sum()}")
    print("\nzero-volume levels per minute (rows present but nothing traded):")
    print(bars["zero_vol_levels"].describe().to_string())


def main() -> None:
    df = load_footprint()
    print(f"raw rows: {len(df):,}")
    bars = build_bars(df)
    bars.to_parquet(OUT, index=False)
    print(f"wrote {OUT}")
    print()
    report(bars)


if __name__ == "__main__":
    main()
