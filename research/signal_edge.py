"""
Signal edge analysis: does a CVD divergence predict the next N bars?

This deliberately avoids stop/target assumptions - it measures the raw
information content of the signal:

    fwd_return(h) = (close[t+h] - close[t]) / ATR(t), signed by signal direction

and compares it with (a) all bars and (b) the same bars with the direction
flipped. Also reports the effect on the *price extreme* rather than the close.

Run:  python research/signal_edge.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cvd_divergence import DivergenceParams, compute_signals, load_bars  # noqa: E402
from backtest import atr  # noqa: E402

HORIZONS = [1, 3, 5, 10, 15, 30, 60]


def forward_table(bars: pd.DataFrame, mask: np.ndarray, side: int, a: np.ndarray) -> pd.DataFrame:
    """Signed forward returns (in ATR units) for entries where `mask` is true."""
    c = bars["close"].values
    h = bars["high"].values
    l = bars["low"].values
    n = len(bars)
    idx = np.where(mask)[0]
    idx = idx[(idx + 1 < n) & ~np.isnan(a[idx]) & (a[idx] > 0)]
    rows = {}
    for horizon in HORIZONS:
        # enter at the open of the next bar, measure to the close h bars later
        entry = bars["open"].values[idx + 1]
        end = np.minimum(idx + horizon, n - 1)
        ret = (c[end] - entry) / a[idx]
        mfe = ((h[np.minimum(idx + horizon, n - 1)] - entry) if side > 0
               else (entry - l[np.minimum(idx + horizon, n - 1)])) / a[idx]
        mae = ((l[np.minimum(idx + horizon, n - 1)] - entry) if side > 0
               else (entry - h[np.minimum(idx + horizon, n - 1)])) / a[idx]
        rows[horizon] = pd.DataFrame({
            "signed_ret": side * ret,
            "mfe_atr": mfe / 1.0 if side > 0 else mfe / 1.0,
            "mae_atr": mae if side > 0 else mae,
        })
    out = []
    for horizon, df in rows.items():
        x = df["signed_ret"].dropna()
        if len(x) < 5:
            continue
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std(ddof=1) > 0 else 0.0
        out.append({
            "horizon": horizon, "n": len(x),
            "mean_signed_ret_atr": x.mean(),
            "median": x.median(),
            "t_stat": t,
            "win_rate": (x > 0).mean(),
            "mfe_atr": df["mfe_atr"].mean(),
            "mae_atr": df["mae_atr"].mean(),
        })
    return pd.DataFrame(out)


def main() -> None:
    bars = load_bars("mnq_bidask.csv")
    a = atr(bars, 14).values
    params = DivergenceParams()
    sig = compute_signals(bars, params)

    print(f"bars {len(bars):,}   bullish signals {sig['bull_signal'].sum()}   "
          f"bearish signals {sig['bear_signal'].sum()}")

    print("\n=== BULLISH divergence (fade: long) ===")
    bull = forward_table(bars, sig["bull_signal"].values, +1, a)
    print(bull.round(3).to_string(index=False))
    print("\n=== BULLISH divergence (continuation: short) ===")
    print(forward_table(bars, sig["bull_signal"].values, -1, a).round(3).to_string(index=False))

    print("\n=== BEARISH divergence (fade: short) ===")
    bear = forward_table(bars, sig["bear_signal"].values, -1, a)
    print(bear.round(3).to_string(index=False))
    print("\n=== BEARISH divergence (continuation: long) ===")
    print(forward_table(bars, sig["bear_signal"].values, +1, a).round(3).to_string(index=False))

    # baseline: every bar in the same direction (long) and short
    allbars = np.ones(len(bars), dtype=bool)
    print("\n=== BASELINE: every bar, long ===")
    print(forward_table(bars, allbars, +1, a).round(3).to_string(index=False))
    print("\n=== BASELINE: every bar, short ===")
    print(forward_table(bars, allbars, -1, a).round(3).to_string(index=False))

    os.makedirs("results", exist_ok=True)
    both = pd.concat([
        bull.assign(kind="bull_fade_long"),
        forward_table(bars, sig["bull_signal"].values, -1, a).assign(kind="bull_cont_short"),
        bear.assign(kind="bear_fade_short"),
        forward_table(bars, sig["bear_signal"].values, +1, a).assign(kind="bear_cont_long"),
    ])
    both.to_csv("results/signal_edge.csv", index=False)
    print("\nwrote results/signal_edge.csv")


if __name__ == "__main__":
    main()
