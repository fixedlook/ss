"""
Backtest for the CVD divergence signal engine.

Runs the same rule set as the MotiveWave study (see cvd_divergence.py) over the
MNQ 1-minute RTH data and reports trade-by-trade P&L net of commission and
slippage, plus robustness checks.

Usage:
    python research/backtest.py                # default parameters
    python research/backtest.py --sweep        # parameter sweep + IS/OOS split

Outputs (results/):
    trades_<tag>.csv      trade list
    summary.md            headline statistics
    equity_<tag>.png      equity curve + drawdown
    byhour_<tag>.png      P&L by time of day
    sweep_<tag>.csv       parameter sweep grid (with --sweep)
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cvd_divergence import DivergenceParams, compute_signals, load_bars  # noqa: E402

# ----------------------------------------------------------------------
# execution model
# ----------------------------------------------------------------------

POINT_VALUE = 2.0        # MNQ: $2 per index point
TICK_SIZE = 0.25         # MNQ tick
TICK_VALUE = POINT_VALUE * TICK_SIZE   # $0.50


@dataclass
class ExecParams:
    stop_atr: float = 1.5
    target_atr: float = 2.5
    atr_period: int = 14
    max_bars: int = 30
    commission_rt: float = 1.00     # $ per contract, round turn
    slippage_ticks: int = 1         # per side, applied to market orders
    contracts: int = 1
    allow_long: bool = True
    allow_short: bool = True
    exit_at_session_end: bool = True


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def run_backtest(bars: pd.DataFrame, sig: pd.DataFrame, ex: ExecParams) -> pd.DataFrame:
    """Bar-by-bar simulation. Signals act on the NEXT bar's open."""
    o = bars["open"].values
    h = bars["high"].values
    l = bars["low"].values
    c = bars["close"].values
    t = bars["dt_et"].values
    sess = bars["session"].values
    a = atr(bars, ex.atr_period).values
    bull = sig["bull_signal"].values
    bear = sig["bear_signal"].values

    trades: list[dict] = []
    n = len(bars)
    i = 0
    slip = ex.slippage_ticks * TICK_SIZE

    while i < n - 1:
        long_sig = bull[i] and ex.allow_long
        short_sig = bear[i] and ex.allow_short
        if not (long_sig or short_sig) or np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue

        side = 1 if long_sig else -1
        entry_i = i + 1
        entry_px = o[entry_i] + side * slip          # market order slippage
        atrv = a[i]
        stop = entry_px - side * ex.stop_atr * atrv
        target = entry_px + side * ex.target_atr * atrv

        exit_i, exit_px, reason = None, None, None
        last_bar_of_session = entry_i
        for j in range(entry_i, min(entry_i + ex.max_bars, n)):
            last_bar_of_session = j
            if sess[j] != sess[entry_i]:
                break
            hit_stop = (l[j] <= stop) if side == 1 else (h[j] >= stop)
            hit_target = (h[j] >= target) if side == 1 else (l[j] <= target)
            if hit_stop:                     # conservative: stop wins ties
                exit_i, exit_px, reason = j, stop - side * slip, "stop"
                break
            if hit_target:
                exit_i, exit_px, reason = j, target, "target"
                break
            if j - entry_i >= ex.max_bars - 1:
                exit_i, exit_px, reason = j, c[j] - side * slip, "time"
                break
        if exit_i is None:
            # session rollover or end of data
            j = min(entry_i + ex.max_bars - 1, n - 1)
            if ex.exit_at_session_end and sess[j] != sess[entry_i]:
                j = max(entry_i, j - 1)
            exit_i, exit_px, reason = j, c[j] - side * slip, "session"

        gross_pts = side * (exit_px - entry_px)
        net_usd = gross_pts * POINT_VALUE * ex.contracts - ex.commission_rt * ex.contracts
        trades.append({
            "side": "long" if side == 1 else "short",
            "signal_type": "bull" if side == 1 else "bear",
            "entry_time": pd.Timestamp(t[entry_i]), "entry_px": entry_px,
            "exit_time": pd.Timestamp(t[exit_i]), "exit_px": exit_px,
            "exit_reason": reason, "bars_held": exit_i - entry_i + 1,
            "gross_points": gross_pts, "net_usd": net_usd,
            "atr_at_entry": atrv,
            "session": int(sess[entry_i]),
        })
        i = exit_i + 1                          # flat until the exit bar

    tr = pd.DataFrame(trades)
    if not tr.empty:
        tr["entry_hour"] = pd.to_datetime(tr["entry_time"]).dt.hour
        tr["entry_date"] = pd.to_datetime(tr["entry_time"]).dt.date
    return tr


# ----------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------

def stats(tr: pd.DataFrame, label: str = "") -> dict:
    if tr.empty:
        return {"label": label, "trades": 0}
    pnl = tr["net_usd"].values
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    daily = tr.groupby("entry_date")["net_usd"].sum()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 1 and daily.std() > 0 else 0.0
    return {
        "label": label,
        "trades": int(len(tr)),
        "net_usd": float(pnl.sum()),
        "net_points": float(tr["gross_points"].sum()),
        "expectancy_usd": float(pnl.mean()),
        "win_rate": float(len(wins) / len(pnl)),
        "avg_win_usd": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_usd": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float("inf"),
        "max_drawdown_usd": float(dd.min()),
        "sharpe_daily": float(sharpe),
        "avg_bars_held": float(tr["bars_held"].mean()),
        "long_trades": int((tr["side"] == "long").sum()),
        "short_trades": int((tr["side"] == "short").sum()),
        "long_net_usd": float(tr.loc[tr["side"] == "long", "net_usd"].sum()),
        "short_net_usd": float(tr.loc[tr["side"] == "short", "net_usd"].sum()),
        "exit_target": int((tr["exit_reason"] == "target").sum()),
        "exit_stop": int((tr["exit_reason"] == "stop").sum()),
        "exit_time": int((tr["exit_reason"] == "time").sum()),
        "exit_session": int((tr["exit_reason"] == "session").sum()),
    }


def fmt(s: dict) -> str:
    if s.get("trades", 0) == 0:
        return f"{s.get('label','')}: no trades"
    return (
        f"{s['label']:28s} n={s['trades']:5d}  net=${s['net_usd']:>9.0f}  "
        f"exp=${s['expectancy_usd']:>6.1f}  win={s['win_rate']:5.1%}  "
        f"PF={s['profit_factor']:4.2f}  maxDD=${s['max_drawdown_usd']:>8.0f}  sharpe={s['sharpe_daily']:5.2f}"
    )


# ----------------------------------------------------------------------
# charts
# ----------------------------------------------------------------------

def make_charts(tr: pd.DataFrame, out_dir: str, tag: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pnl = tr["net_usd"].values
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    x = pd.to_datetime(tr["exit_time"])
    ax1.plot(x, equity, lw=1.4, color="#1f77b4")
    ax1.axhline(0, color="grey", lw=0.8, ls="--")
    ax1.set_title(f"CVD divergence — equity curve (1 MNQ, net of ${tr.attrs.get('cost', 0)}/RT costs)")
    ax1.set_ylabel("cumulative net USD")
    ax1.grid(alpha=0.25)
    ax2.fill_between(x, dd, 0, color="#d62728", alpha=0.5)
    ax2.set_ylabel("drawdown USD")
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"equity_{tag}.png"), dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    byhour = tr.groupby("entry_hour")["net_usd"].agg(["sum", "count"])
    ax.bar(byhour.index, byhour["sum"], color="#2ca02c")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xlabel("entry hour (ET)")
    ax.set_ylabel("net USD")
    ax.set_title("P&L by entry hour")
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"byhour_{tag}.png"), dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--bars-csv", default="mnq_bidask.csv")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    bars = load_bars(args.bars_csv)
    print(f"bars: {len(bars):,}  sessions: {bars['session'].nunique():,}  "
          f"{bars['dt_et'].min():%Y-%m-%d} .. {bars['dt_et'].max():%Y-%m-%d}")

    base_params = DivergenceParams()
    ex = ExecParams()

    sig = compute_signals(bars, base_params)
    tr = run_backtest(bars, sig, ex)
    tr.attrs["cost"] = ex.commission_rt
    tag = "base"
    tr.to_csv(os.path.join(args.out, f"trades_{tag}.csv"), index=False)

    s = stats(tr, "base parameters")
    print("\n" + fmt(s))
    signals_per_day = (sig["bull_signal"].sum() + sig["bear_signal"].sum()) / bars["session"].nunique()
    print(f"signals: {sig['bull_signal'].sum()} bullish / {sig['bear_signal'].sum()} bearish "
          f"({signals_per_day:.1f} per session)")

    # ---------- robustness ----------
    print("\n=== robustness ===")
    checks = {}

    # 1. costs off (is the edge big enough to survive friction?)
    ex0 = replace(ex, commission_rt=0.0, slippage_ticks=0)
    s_nocost = stats(run_backtest(bars, sig, ex0), "no costs (frictionless)")
    print(fmt(s_nocost)); checks["no_costs"] = s_nocost

    # 2. double slippage
    ex2 = replace(ex, slippage_ticks=2)
    s_slip2 = stats(run_backtest(bars, sig, ex2), "2-tick slippage/side")
    print(fmt(s_slip2)); checks["slippage_2"] = s_slip2

    # 3. flipped (same timing, opposite direction) -> should be clearly negative
    exf = replace(ex, allow_long=True, allow_short=True)
    flipped = tr.copy()
    flipped["net_usd"] = -tr["gross_points"] * POINT_VALUE - ex.commission_rt
    s_flip = stats(flipped, "direction flipped")
    print(fmt(s_flip)); checks["flipped"] = s_flip

    # 4. no stop / no target (pure directional hold to session end, max 30 bars)
    exn = replace(ex, stop_atr=100.0, target_atr=100.0)
    s_hold = stats(run_backtest(bars, sig, exn), "no stop/target (30-bar hold)")
    print(fmt(s_hold)); checks["hold_only"] = s_hold

    # 5. long only / short only
    print(fmt(stats(run_backtest(bars, sig, replace(ex, allow_short=False)), "long only")))
    print(fmt(stats(run_backtest(bars, sig, replace(ex, allow_long=False)), "short only")))

    # 6. in-sample / out-of-sample split (first 60% of sessions / last 40%)
    mid = bars["session"].quantile(0.6)
    is_bars = bars[bars["session"] <= mid]
    oos_bars = bars[bars["session"] > mid]
    s_is = stats(run_backtest(is_bars, sig.loc[is_bars.index], ex), "in-sample (60%)")
    s_oos = stats(run_backtest(oos_bars, sig.loc[oos_bars.index], ex), "out-of-sample (40%)")
    print(fmt(s_is)); print(fmt(s_oos))
    checks["in_sample"], checks["out_of_sample"] = s_is, s_oos

    make_charts(tr, args.out, tag)

    # ---------- parameter sweep ----------
    if args.sweep:
        print("\n=== parameter sweep ===")
        rows = []
        grid = itertools.product(
            [2, 3, 5],                 # pivot strength
            [0.0, 250.0, 500.0],       # min CVD divergence
            [(1.0, 2.0), (1.5, 2.5), (2.0, 3.0)],  # (stop ATR, target ATR)
            [1, 30, 60],               # max bars  (1 ~ quick scalp out)
        )
        for strength, min_cvd, (stop, tgt), maxb in grid:
            p = replace(base_params, pivot_strength=strength, min_cvd_div=min_cvd)
            sg = compute_signals(bars, p)
            e = replace(ex, stop_atr=stop, target_atr=tgt, max_bars=maxb)
            tt = run_backtest(bars, sg, e)
            st = stats(tt)
            rows.append({
                "pivot_strength": strength, "min_cvd_div": min_cvd,
                "stop_atr": stop, "target_atr": tgt, "max_bars": maxb,
                **{k: v for k, v in st.items() if k != "label"},
            })
        sweep = pd.DataFrame(rows).sort_values("net_usd", ascending=False)
        sweep.to_csv(os.path.join(args.out, f"sweep_{tag}.csv"), index=False)
        print(sweep.head(12).to_string(index=False))
        checks["sweep_top"] = sweep.head(5).to_dict("records")
        checks["sweep_median_net"] = float(sweep["net_usd"].median())
        checks["sweep_profitable_share"] = float((sweep["net_usd"] > 0).mean())

    with open(os.path.join(args.out, f"summary_{tag}.json"), "w") as f:
        json.dump({"base": s, "signals_per_session": float(signals_per_day), "checks": checks}, f, indent=2, default=str)
    print(f"\nwrote {args.out}/summary_{tag}.json")


if __name__ == "__main__":
    main()
