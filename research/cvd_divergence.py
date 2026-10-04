"""
CVD divergence signal engine — Python mirror of the MotiveWave study.

This module reproduces, bar for bar, the logic implemented in
MotiveWaveStudy/src/main/java/com/fixedlook/mnq/CvdDivergenceStudy.java so that
the study and the backtest can never drift apart.

Definitions
-----------
delta   : per-bar ask volume - bid volume (taken from the tick-derived export)
CVD     : running sum of delta, reset at the start of each session (RTH)
pivot   : bar i whose low (high) is strictly the lowest (highest) of the
          2 * strength + 1 bars centred on i. A pivot is therefore only known
          `strength` bars later - the signal is raised on that confirmation bar,
          which is what makes the backtest free of look-ahead.
divergence (bullish): price low  < previous pivot low  - min_ticks * tick
                      AND CVD at the pivot > CVD at previous pivot + min_cvd_div
divergence (bearish): price high > previous pivot high + min_ticks * tick
                      AND CVD at the pivot < CVD at previous pivot - min_cvd_div
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class DivergenceParams:
    """Study inputs (defaults match the MotiveWave study defaults)."""

    pivot_strength: int = 3        # bars each side of the swing
    max_lookback: int = 60         # bars back to look for the prior swing
    min_cvd_div: float = 250.0     # minimum CVD difference, contracts
    min_price_ticks: int = 2       # minimum price difference, ticks
    min_bar_volume: int = 0        # 0 = off
    skip_first_minutes: int = 15   # ignore signals taken in the first N minutes
    tick_size: float = 0.25        # MNQ tick
    session_reset: bool = True


def load_bars(csv_path: str = "mnq_bidask.csv") -> pd.DataFrame:
    """Load the 1-minute bar file (OHLC + bid/ask volume) and keep RTH bars."""
    df = pd.read_csv(csv_path)
    df = df[df["volume"] > 0].copy()              # RTH-only rows carry volume
    df["dt_utc"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.sort_values("dt_utc").reset_index(drop=True)

    # session = US trading date in New York; a new session starts when the day
    # changes or when there is a gap longer than 12 hours.
    day = df["dt_et"].dt.date
    gap = df["dt_utc"].diff().dt.total_seconds().fillna(0) > 12 * 3600
    df["session"] = (day != day.shift(1)) | gap
    df["session"] = df["session"].cumsum()
    df["minute_of_session"] = (
        df.groupby("session").cumcount()
    )
    return df


def add_cvd(df: pd.DataFrame, params: DivergenceParams) -> pd.DataFrame:
    """Add per-bar delta and session-anchored cumulative delta."""
    df = df.copy()
    delta = (df["ask_volume"] - df["bid_volume"]).astype(float)
    df["delta_calc"] = delta
    cvd = np.empty(len(df), dtype=float)
    running = 0.0
    prev_session = None
    for i, (sess, d) in enumerate(zip(df["session"].values, delta.values)):
        if params.session_reset and sess != prev_session:
            running = 0.0
        running += d
        cvd[i] = running
        prev_session = sess
    df["cvd"] = cvd
    return df


def find_pivots(df: pd.DataFrame, strength: int) -> pd.DataFrame:
    """Mark pivot lows/highs. A pivot is strictly extreme inside its window."""
    df = df.copy()
    low, high = df["low"].values, df["high"].values
    n = len(df)
    k = strength

    left_min = pd.Series(low).shift(1).rolling(k).min().values
    right_min = pd.Series(low).shift(-k).rolling(k).min().values
    left_max = pd.Series(high).shift(1).rolling(k).max().values
    right_max = pd.Series(high).shift(-k).rolling(k).max().values

    pivot_low = np.zeros(n, dtype=bool)
    pivot_high = np.zeros(n, dtype=bool)
    with np.errstate(invalid="ignore"):
        pivot_low = low < np.fmin(left_min, right_min)
        pivot_high = high > np.fmax(left_max, right_max)
    pivot_low[np.isnan(left_min) | np.isnan(right_min)] = False
    pivot_high[np.isnan(left_max) | np.isnan(right_max)] = False

    df["pivot_low"] = pivot_low
    df["pivot_high"] = pivot_high
    return df


def find_divergences(df: pd.DataFrame, params: DivergenceParams) -> pd.DataFrame:
    """Attach signal columns. Signals fire on the confirmation bar (pivot + strength)."""
    df = find_pivots(df, params.pivot_strength)
    n = len(df)
    k = params.pivot_strength
    look = params.max_lookback
    tick = params.tick_size

    low = df["low"].values
    high = df["high"].values
    cvd = df["cvd"].values
    vol = df["volume"].values
    sess = df["session"].values
    mos = df["minute_of_session"].values
    plow = df["pivot_low"].values
    phigh = df["pivot_high"].values

    bull_signal = np.zeros(n, dtype=bool)
    bear_signal = np.zeros(n, dtype=bool)
    bull_conf = np.full(n, -1, dtype=int)
    bear_conf = np.full(n, -1, dtype=int)

    # previous pivot low / high seen so far (most recent before p - strength)
    last_low_idx = -1
    last_high_idx = -1
    pivot_low_list: list[int] = []
    pivot_high_list: list[int] = []

    for p in range(n):
        if plow[p]:
            pivot_low_list.append(p)
        if phigh[p]:
            pivot_high_list.append(p)

        index = p + k                # confirmation bar for a pivot at p
        if index >= n:
            break

        # ---- bullish: pivot low at p, confirmed k bars later ----
        if plow[p]:
            prior = -1
            for j in reversed(pivot_low_list[:-1]):     # strictly earlier pivots
                if j <= p - k and j >= max(k, p - look):
                    prior = j
                    break
            if prior >= 0:
                if vol[p] < params.min_bar_volume:
                    prior = -1
                elif mos[p] < params.skip_first_minutes:
                    prior = -1
                elif not (low[p] < low[prior] - params.min_price_ticks * tick):
                    prior = -1
                elif not (cvd[p] > cvd[prior] + params.min_cvd_div):
                    prior = -1
            if prior >= 0:
                bull_signal[index] = True
                bull_conf[index] = prior

        # ---- bearish: pivot high at p, confirmed k bars later ----
        if phigh[p]:
            prior = -1
            for j in reversed(pivot_high_list[:-1]):
                if j <= p - k and j >= max(k, p - look):
                    prior = j
                    break
            if prior >= 0:
                if vol[p] < params.min_bar_volume:
                    prior = -1
                elif mos[p] < params.skip_first_minutes:
                    prior = -1
                elif not (high[p] > high[prior] + params.min_price_ticks * tick):
                    prior = -1
                elif not (cvd[p] < cvd[prior] - params.min_cvd_div):
                    prior = -1
            if prior >= 0:
                bear_signal[index] = True
                bear_conf[index] = prior

    df["bull_signal"] = bull_signal
    df["bear_signal"] = bear_signal
    df["bull_pivot"] = bull_signal  # convenience
    df["bull_prior"] = bull_conf
    df["bear_prior"] = bear_conf
    # bar at which the swing actually occurred (for charting / diagnostics)
    df["bull_pivot_bar"] = np.where(bull_signal, np.arange(n) - k, -1)
    df["bear_pivot_bar"] = np.where(bear_signal, np.arange(n) - k, -1)
    return df


def compute_signals(df: pd.DataFrame, params: DivergenceParams | None = None) -> pd.DataFrame:
    """Full pipeline: deltas -> CVD -> pivots -> divergence signals."""
    params = params or DivergenceParams()
    df = add_cvd(df, params)
    return find_divergences(df, params)
