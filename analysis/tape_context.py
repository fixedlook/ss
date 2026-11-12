#!/usr/bin/env python3
"""
Given a (timestamp, price) where you took a trade, print everything the tape and profile
say about that moment — the observable facts a signal could have been built from.

Usage:
    python3 analysis/tape_context.py 2026-09-25T14:32 30742.25
    python3 analysis/tape_context.py --file signals.csv      # one "time,price" per line

Data used:
    mnq_bidask.csv          1-min RTH bars with volume + delta
    mnq_bidask.footprint.csv  1-min x price-level bid/ask volume (for POC / value area / HVN)
"""
import csv, sys, gzip, statistics, io, zipfile, argparse
from datetime import datetime, timezone, timedelta

BARS = "mnq_bidask.csv"
FOOT = "mnq_bidask.footprint.csv"

def load_bars():
    rows = []
    with open(BARS) as f:
        for r in csv.DictReader(f):
            ts = int(r["timestamp"])
            rows.append({"ts": ts, "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"]),
                         "v": float(r["volume"]), "bv": float(r["bid_volume"]),
                         "av": float(r["ask_volume"]), "d": float(r["delta"]),
                         "n": float(r["trades"])})
    rows.sort(key=lambda x: x["ts"])
    return rows

def load_foot_for_day(day_ms, path=FOOT):
    """price-level rows for one UTC day from the footprint csv"""
    out = []
    lo, hi = day_ms, day_ms + 86_400_000
    with open(path) as f:
        for r in csv.reader(f):
            if r[0] == "timestamp":
                continue
            ts = int(r[0])
            if ts < lo:
                continue
            if ts >= hi:
                break
            out.append((ts, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    return out

def session_key(ts):
    """RTH sessions straddle UTC midnight; group by the 18:00 UTC boundary."""
    return (ts - 18 * 3600_000) // 86_400_000

def profile(foot_rows):
    """volume profile: poc, value area (70%), hvn/lvn"""
    vol = {}
    for ts, p, bv, av, d in foot_rows:
        vol[p] = vol.get(p, 0) + bv + av
    if not vol:
        return None
    total = sum(vol.values())
    poc = max(vol, key=vol.get)
    prices = sorted(vol)
    # expand from POC until 70% of volume is captured
    i = prices.index(poc)
    lo = hi = i
    acc = vol[poc]
    while acc < total * 0.70 and (lo > 0 or hi < len(prices) - 1):
        down = vol[prices[lo - 1]] if lo > 0 else -1
        up = vol[prices[hi + 1]] if hi < len(prices) - 1 else -1
        if up >= down:
            hi += 1; acc += up
        else:
            lo -= 1; acc += down
    return {"poc": poc, "val": prices[lo], "vah": prices[hi],
            "vol": vol, "total": total, "median_level": statistics.median(vol.values())}

def nearest_levels(price, prof, tol=1.0):
    """is price at a high-volume node or in a low-volume gap?"""
    if not prof:
        return {}
    vol, med = prof["vol"], prof["median_level"]
    here = vol.get(price)
    if here is None:
        near = min(vol, key=lambda p: abs(p - price))
        here = vol[near]
    ratio = here / med if med else 0
    return {"level_volume": here, "vs_median_level": round(ratio, 2),
            "kind": "HVN" if ratio > 1.6 else ("LVN" if ratio < 0.5 else "normal")}

def analyse(ts_ms, price, bars, bar_index):
    ts_ms = int(ts_ms)
    day = ts_ms - (ts_ms % 86_400_000)
    i = bar_index.get(ts_ms // 60_000 * 60_000)
    if i is None:
        # nearest bar within 2 minutes
        cand = [k for k in bar_index if abs(k - ts_ms) < 120_000]
        if not cand:
            return {"error": "no bar within 2 min of that time"}
        i = bar_index[min(cand, key=lambda k: abs(k - ts_ms))]
    bar = bars[i]

    # session = bars sharing the same session key
    sk = session_key(bar["ts"])
    sess = [b for b in bars[max(0, i - 900): i + 900] if session_key(b["ts"]) == sk]
    vols = [b["v"] for b in sess if b["v"] > 0]
    med_v = statistics.median(vols) if vols else 0
    prior = [b for b in sess if b["ts"] < bar["ts"]]
    post = [b for b in sess if b["ts"] > bar["ts"]]

    # session extremes *before* the signal (what you could have seen at the time)
    sess_hi = max((b["h"] for b in prior), default=bar["h"])
    sess_lo = min((b["l"] for b in prior), default=bar["l"])

    # running VWAP up to the bar
    num = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in prior)
    den = sum(b["v"] for b in prior)
    vwap = num / den if den else bar["c"]

    # previous session close
    prev = [b for b in bars[max(0, i - 2000): i] if session_key(b["ts"]) < sk]
    pd_close = prev[-1]["c"] if prev else None

    prof = profile(load_foot_for_day(day, FOOT)) if price else None
    ctx = {
        "bar_time": datetime.fromtimestamp(bar["ts"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bar": {k: bar[k] for k in ("o", "h", "l", "c", "v", "d")},
        "volume_x_session_median": round(bar["v"] / med_v, 2) if med_v else None,
        "delta": bar["d"],
        "delta_sign": "buyers" if bar["d"] > 0 else ("sellers" if bar["d"] < 0 else "flat"),
        "price_move_ticks": round((bar["c"] - bar["o"]) / 0.25),
        "absorption": bool(med_v and bar["v"] > med_v * 1.8 and abs(bar["c"] - bar["o"]) <= 1.0 and abs(bar["d"]) > bar["v"] * 0.15),
        "session_high_before": sess_hi,
        "session_low_before": sess_lo,
        "dist_from_hi": round((sess_hi - bar["c"]), 2),
        "dist_from_lo": round((bar["c"] - sess_lo), 2),
        "vwap": round(vwap, 2),
        "dist_from_vwap": round(bar["c"] - vwap, 2),
        "pd_close": pd_close,
        "at_session_extreme": bool(abs(sess_hi - bar["c"]) <= 2 or abs(bar["c"] - sess_lo) <= 2),
    }
    if price:
        ctx["given_price"] = price
        ctx["price_vs_bar"] = round(price - bar["c"], 2)
        ctx.update(nearest_levels(price, prof))
        if prof:
            ctx["profile"] = {"poc": prof["poc"], "val": prof["val"], "vah": prof["vah"]}
            ctx["price_vs_poc"] = round(price - prof["poc"], 2)
            ctx["price_vs_vah"] = round(price - prof["vah"], 2)
            ctx["price_vs_val"] = round(price - prof["val"], 2)
    # what happened next 5 / 15 minutes
    if post:
        ctx["next_5m_move"] = round(post[min(4, len(post) - 1)]["c"] - bar["c"], 2)
        ctx["next_15m_move"] = round(post[min(14, len(post) - 1)]["c"] - bar["c"], 2)
        ctx["next_15m_max_favourable_up"] = round(max(b["h"] for b in post[:15]) - bar["c"], 2)
        ctx["next_15m_max_favourable_down"] = round(min(b["l"] for b in post[:15]) - bar["c"], 2)
    return ctx

def parse_time(s):
    s = s.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            pass
    raise SystemExit(f"can't parse time: {s!r} (use 2026-09-25T14:32, UTC)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("time", nargs="?")
    ap.add_argument("price", nargs="?", type=float)
    ap.add_argument("--file", help="csv of time,price[,direction]")
    a = ap.parse_args()
    bars = load_bars()
    idx = {b["ts"]: k for k, b in enumerate(bars)}
    jobs = []
    if a.file:
        with open(a.file) as f:
            for row in csv.reader(f):
                if not row or row[0].startswith("#"):
                    continue
                if row[0].strip().lower() in ("time", "timestamp", "date"):
                    continue  # header row
                jobs.append((parse_time(row[0]), float(row[1]), row[2].strip().upper() if len(row) > 2 else ""))
    elif a.time:
        jobs.append((parse_time(a.time), a.price, ""))
    else:
        raise SystemExit("give a time and price, or --file signals.csv")
    out = []
    for ts, price, direction in jobs:
        ctx = analyse(ts, price, bars, idx)
        if direction:
            ctx["direction"] = direction
        out.append(ctx)
    import json
    print(json.dumps(out, indent=2, default=str))

if __name__ == "__main__":
    main()
