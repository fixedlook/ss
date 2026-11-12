#!/usr/bin/env python3
"""
Turn a list of (time, price, label) signals into an evidence table.

For every signal it prints what the tape and the profile actually showed at that minute —
volume against the session median, delta, where price sat versus VWAP / session extremes /
the day's profile — plus what happened next at four horizons. Then it repeats the whole
measurement on randomly chosen minutes from the same sessions, so "these signals cluster at
reversal points" becomes a claim that can be tested instead of asserted.

Usage:
    python3 analysis/study.py --signals signals.csv
    python3 analysis/study.py --signals signals.csv --tz Europe/Rome
    python3 analysis/study.py --selftest          # exercises the whole path on real bars

signal file: one row per line, "YYYY-MM-DDTHH:MM,price[,label]". Rows starting with # are
ignored. Times are read in --tz (default Europe/Rome, matching the platform the list came from).

Sources, chosen per signal date:
    mnq_bidask.csv         2026-06-24 .. 2026-10-02, 1-min, bid/ask + delta populated
    mnq_flow.csv.gz        2019-07-21 .. 2026-09-23, 1-min, 24h, bid/ask coverage varies
    mnq_bidask.footprint.csv   price-level bid/ask, 2026-09-22 .. 2026-10-02
"""
import argparse
import csv
import gzip
import io
import statistics
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BAR_FILES = ["bars.csv", "mnq_bidask.csv", "mnq_flow.csv.gz"]
ARCHIVE = "MNQZ26 - 1 min - RTH.zip"

# RTH in New York time; everything else is overnight.
RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60 + 15


# ----------------------------------------------------------------------------- loading


def _ts_to_dt(ts):
    ts = int(ts)
    if ts > 1e11:          # milliseconds
        ts //= 1000
    return datetime.fromtimestamp(ts, timezone.utc)


def load_bars():
    """Return {utc_minute_datetime: bar dict} merged from both extracts, plus coverage info."""
    bars = {}
    coverage = {}

    for path in BAR_FILES:
        opener = gzip.open if path.endswith(".gz") else open
        n = 0
        real = 0
        bad = 0
        try:
            with opener(path, "rt", errors="replace") as f:
                for row in csv.DictReader(f):
                    try:
                        if "ts_utc" in row:
                            ts_field = row["ts_utc"]
                        else:
                            ts_field = row["timestamp"]
                        # The ATAS export and the older extracts name the same columns differently.
                        def pick(*names, default=0.0):
                            for name in names:
                                if name in row and row[name] not in (None, ""):
                                    return float(row[name])
                            return default

                        bar = {
                            "o": pick("open"), "h": pick("high"),
                            "l": pick("low"), "c": pick("close"),
                            "v": pick("volume", "vol"),
                            "bv": pick("bid_volume", "bid_vol"),
                            "av": pick("ask_volume", "ask_vol"),
                            "d": pick("delta"),
                            "n": pick("trades", "ticks"),
                            "src": path,
                        }
                        if "ts_utc" in row:
                            dt = datetime.fromisoformat(ts_field).replace(tzinfo=timezone.utc).replace(second=0, microsecond=0)
                        else:
                            dt = _ts_to_dt(ts_field).replace(second=0, microsecond=0)
                    except (KeyError, ValueError, TypeError):
                        # Both extracts contain a handful of truncated rows; skipping them is
                        # correct, failing the whole load over 8 rows out of 1.9M is not.
                        bad += 1
                        continue
                    # Later files never overwrite earlier ones: mnq_bidask is listed first
                    # because its bid/ask columns are actually populated.
                    if dt not in bars:
                        bars[dt] = bar
                    n += 1
                    if bar["bv"] or bar["av"]:
                        real += 1
        except FileNotFoundError:
            continue
        coverage[path] = {"rows": n, "real_bidask": real, "bad": bad,
                          "pct": (100.0 * real / n) if n else 0.0}
    return bars, coverage


def load_footprint(path, wanted=None):
    """Load per-price bid/ask, in either format. Returns {session_day: [(dt, price, bid, ask, ticks)]}.

    Accepts:
      ATAS History Exporter  ts_utc,ts_local,sym,price,bid_vol,ask_vol,delta,ticks
      MotiveWave extract     timestamp,price,bid_volume,ask_volume,delta,trades
    """
    by_day = {}
    malformed = 0

    opener = gzip.open if str(path).endswith(".gz") else open

    try:
        with opener(path, "rt", newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []

            for row in reader:
                try:
                    if "ts_utc" in fields:
                        dt = datetime.fromisoformat(row["ts_utc"]).replace(tzinfo=timezone.utc)
                        price = float(row["price"])
                        bid = float(row["bid_vol"] or 0)
                        ask = float(row["ask_vol"] or 0)
                        ticks = float(row["ticks"] or 0)
                    else:
                        dt = _ts_to_dt(row["timestamp"])
                        price = float(row["price"])
                        bid = float(row["bid_volume"] or 0)
                        ask = float(row["ask_volume"] or 0)
                        ticks = float(row["trades"] or 0)
                except (KeyError, ValueError, TypeError):
                    malformed += 1
                    continue

                key = session_of(dt, None)
                if wanted is not None and key not in wanted:
                    continue
                by_day.setdefault(key, []).append((dt, price, bid, ask, ticks))
    except FileNotFoundError:
        return None, 0

    for key in by_day:
        by_day[key].sort()

    return by_day, malformed


def level_context(rows_for_session, dt_utc, price, tick=0.25, band_ticks=8, window_min=3):
    """What the book did at and around this price, in a short window after the signal.

    This is the part a 1-minute OHLC bar cannot give: how much traded *at this price*, whether the
    buyers or the sellers were the aggressors there, and whether the price was a volume shelf that
    the market had to chew through.
    """
    if not rows_for_session:
        return {}

    end = dt_utc + timedelta(minutes=window_min)
    window = [r for r in rows_for_session if dt_utc <= r[0] <= end]
    if not window:
        return {}

    lo = price - band_ticks * tick
    hi = price + band_ticks * tick

    band_bid = band_ask = band_ticks_total = 0.0
    at_bid = at_ask = 0.0
    session_vol = {}
    band_vol = 0.0

    for dt, p, bid, ask, ticks in window:
        session_vol[p] = session_vol.get(p, 0.0) + bid + ask
        if lo <= p <= hi:
            band_bid += bid
            band_ask += ask
            band_ticks_total += ticks
            band_vol += bid + ask

    for dt, p, bid, ask, ticks in window:
        if abs(p - price) < tick / 2:
            at_bid += bid
            at_ask += ask

    total = sum(session_vol.values())
    ranked = sorted(session_vol.items(), key=lambda kv: -kv[1])
    rank = next((i for i, (p, _) in enumerate(ranked) if abs(p - price) < tick / 2), None)

    imbalance = (band_ask - band_bid) / (band_ask + band_bid) if (band_ask + band_bid) else 0.0
    at_imbalance = (at_ask - at_bid) / (at_ask + at_bid) if (at_ask + at_bid) else 0.0

    return {
        "band_vol": band_vol,
        "band_bid": band_bid,
        "band_ask": band_ask,
        "band_delta": band_ask - band_bid,
        "band_imbalance": round(imbalance, 3),
        "at_price_bid": at_bid,
        "at_price_ask": at_ask,
        "at_price_imbalance": round(at_imbalance, 3),
        "band_share_of_session": round(band_vol / total, 3) if total else None,
        "level_rank": None if rank is None else rank,
        "levels_traded": len(ranked),
        "top_level_price": ranked[0][0] if ranked else None,
        "top_level_vol": ranked[0][1] if ranked else None,
        "window_bars": len(window),
    }


# ----------------------------------------------------------------------------- features


def session_of(dt_utc, tz):
    """Session key: the New York trading day, so an overnight run is one session."""
    local = dt_utc.astimezone(ZoneInfo("America/New_York"))
    key = local.date() if local.hour >= 17 else (local - timedelta(days=1)).date() if local.hour < 9 else local.date()
    return key


def is_rth(dt_utc):
    ny = dt_utc.astimezone(ZoneInfo("America/New_York"))
    m = ny.hour * 60 + ny.minute
    return RTH_OPEN_MIN <= m <= RTH_CLOSE_MIN


def build_sessions(bars):
    """Group bars into sessions once. Doing this per lookup is O(n) each time and unusable on 1.9M bars."""
    sessions = {}
    for dt, bar in bars.items():
        sessions.setdefault(session_of(dt, None), []).append((dt, bar))
    for key in sessions:
        sessions[key].sort(key=lambda pair: pair[0])
    medians = {}
    for key, pairs in sessions.items():
        vols = [b["v"] for _, b in pairs if b["v"] > 0]
        medians[key] = statistics.median(vols) if vols else 0.0
    return sessions, medians


def percentile(values, x):
    if not values:
        return None
    below = sum(1 for v in values if v < x)
    return 100.0 * below / len(values)


def swing_kind(bars_sorted, idx, k):
    """Is this bar a local extreme at scale k? That is what 'inflection' should mean."""
    lo = max(0, idx - k)
    hi = min(len(bars_sorted), idx + k + 1)
    window = bars_sorted[lo:hi]
    if len(window) < 3:
        return ""
    c = window[idx - lo]["c"]
    if c >= max(w["h"] for w in window) - 1e-9:
        return "high"
    if c <= min(w["l"] for w in window) + 1e-9:
        return "low"
    return ""


def forward_return(bars_sorted, idx, minutes, price):
    j = idx + minutes
    if j >= len(bars_sorted):
        return None
    return round(bars_sorted[j]["c"] - price, 2)


def mfe_mae(bars_sorted, idx, minutes, price, direction):
    window = bars_sorted[idx + 1: idx + 1 + minutes]
    if not window:
        return None, None
    hi = max(w["h"] for w in window) - price
    lo = min(w["l"] for w in window) - price
    if direction == "bullish":
        return round(hi, 2), round(lo, 2)
    return round(-lo, 2), round(-hi, 2)


def describe(session_pairs, idx, price, dt_utc, label="", med=0.0):
    """Features for one bar, given its session's bars (already sorted) and that session's median volume."""
    bar = session_pairs[idx][1]
    bars_sorted = [b for _, b in session_pairs]

    vols = [b["v"] for b in bars_sorted if b["v"] > 0]
    deltas = [b["d"] for b in bars_sorted]
    ahead = bars_sorted[: idx + 1]
    sess_open = ahead[0]["c"] if ahead else price
    hi_so_far = max(b["h"] for b in ahead)
    lo_so_far = min(b["l"] for b in ahead)

    pv = sum(((b["h"] + b["l"] + b["c"]) / 3.0) * b["v"] for b in ahead)
    vsum = sum(b["v"] for b in ahead)
    vwap = pv / vsum if vsum else price

    def ret(back):
        j = idx - back
        if j < 0:
            return None
        return round(price - bars_sorted[j]["c"], 2)

    return {
        "utc": dt_utc.strftime("%Y-%m-%d %H:%M"),
        "ny": dt_utc.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
        "phase": "RTH" if is_rth(dt_utc) else "overnight",
        "bar_o": bar["o"], "bar_h": bar["h"], "bar_l": bar["l"], "bar_c": bar["c"],
        "vol": bar["v"],
        "vol_x_median": round(bar["v"] / med, 2) if med else None,
        "vol_pctile": None if not vols else round(percentile(vols, bar["v"]), 0),
        "delta": bar["d"],
        "delta_pctile": None if not deltas else round(percentile(deltas, bar["d"]), 0),
        "bid_vol": bar["bv"], "ask_vol": bar["av"],
        "real_bidask": bool(bar["bv"] or bar["av"]),
        "trades": bar["n"],
        "tick_from_session_open": round(price - sess_open, 2),
        "tick_from_vwap": round(price - vwap, 2),
        "tick_from_session_high": round(price - hi_so_far, 2),
        "tick_from_session_low": round(price - lo_so_far, 2),
        "swing_5": swing_kind(bars_sorted, idx, 5),
        "swing_15": swing_kind(bars_sorted, idx, 15),
        "swing_45": swing_kind(bars_sorted, idx, 45),
        "runup_5": ret(5), "runup_15": ret(15), "runup_30": ret(30),
        "fwd_5": forward_return(bars_sorted, idx, 5, price),
        "fwd_15": forward_return(bars_sorted, idx, 15, price),
        "fwd_30": forward_return(bars_sorted, idx, 30, price),
        "fwd_60": forward_return(bars_sorted, idx, 60, price),
        "label": label,
    }





# ----------------------------------------------------------------------------- study


def evidence(res, direction):
    """Score one row from -2 (contradicts the claimed direction) to +2 (supports it)."""
    score = 0
    notes = []

    vx = res["vol_x_median"]
    if vx is not None and vx >= 2.0:
        score += 1
        notes.append(f"volume {vx}x median")

    if res["real_bidask"]:
        contrarian = (direction == "bullish" and res["delta"] < 0) or \
                     (direction == "bearish" and res["delta"] > 0)
        if contrarian:
            score += 1
            notes.append("delta opposes the move (absorption shape)")
        elif (direction == "bullish" and res["delta"] > 0) or \
             (direction == "bearish" and res["delta"] < 0):
            notes.append("delta agrees with the move (momentum shape)")

    want = "low" if direction == "bullish" else "high"
    if res["swing_15"] == want:
        score += 1
        notes.append(f"local {res['swing_15']} at 15-bar scale")

    f = res["fwd_15"]
    if f is not None:
        moved = (f > 0) if direction == "bullish" else (f < 0)
        score += 1 if moved else -1
        notes.append(f"15m follow-through {f:+.2f} ({'confirmed' if moved else 'failed'})")

    return score, notes


def summarise(rows, baselines):
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    if not rows:
        print("no rows")
        return

    scored = [evidence(r, r["direction"]) for r in rows if r.get("direction")]
    if scored:
        avg = sum(s for s, _ in scored) / len(scored)
        print(f"signals scored            : {len(scored)}")
        print(f"mean evidence score       : {avg:+.2f}  (range -2..+2)")

    for horizon in ("fwd_5", "fwd_15", "fwd_30"):
        vals = [r[horizon] for r in rows if r[horizon] is not None]
        if not vals:
            continue
        print(f"{horizon:25} mean {statistics.mean(vals):+8.2f}   "
              f"median {statistics.median(vals):+8.2f}   n={len(vals)}")

    # The honest test: do the chosen minutes differ from arbitrary minutes?
    print("\nselection test — signals vs random minutes from the same sessions")
    for key in ("vol_x_median", "tick_from_vwap", "delta", "fwd_15"):
        a = [r[key] for r in rows if r.get(key) is not None]
        b = [r[key] for r in baselines if r.get(key) is not None]
        if len(a) < 3 or len(b) < 3:
            continue
        ma, mb = statistics.mean(a), statistics.mean(b)
        sd = statistics.pstdev(b) or 1e-9
        z = (ma - mb) / (sd / (len(a) ** 0.5))
        flag = "  <-- distinct" if abs(z) >= 2 else ""
        print(f"  {key:18} signals {ma:+9.2f}   random {mb:+9.2f}   z={z:+5.2f}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", help="csv of time,price[,label]")
    ap.add_argument("--tz", default="Europe/Rome", help="zone the signal times are written in")
    ap.add_argument("--footprint", help="footprint.csv from the ATAS History Exporter (or the older extract)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the whole pipeline on swing points found in the data")
    args = ap.parse_args()

    bars, coverage = load_bars()
    if not bars:
        sys.exit("no bar files found")

    print("bars loaded per source:")
    for path, info in coverage.items():
        bad = f", {info['bad']} malformed skipped" if info.get("bad") else ""
        print(f"  {path:24} {info['rows']:>9,} rows, real bid/ask on {info['pct']:5.1f}%{bad}")

    days = sorted({dt.date() for dt in bars})
    print(f"  coverage: {days[0]} -> {days[-1]}  ({len(days)} dates)")

    sessions, medians = build_sessions(bars)
    zone = ZoneInfo(args.tz)

    foot = None
    if args.footprint:
        wanted = None
        if args.signals:
            wanted = set()
            with open(args.signals) as sf:
                for line in sf:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    head = line.split(",")[0]
                    try:
                        local = datetime.fromisoformat(head).replace(tzinfo=zone)
                    except ValueError:
                        continue
                    wanted.add(session_of(local.astimezone(timezone.utc), None))
            print(f"  footprint: keeping only {len(wanted)} session(s) that the signals fall in")

        foot, foot_bad = load_footprint(args.footprint, wanted)
        if foot is None:
            sys.exit(f"footprint file not found: {args.footprint}")
        fdays = sorted(foot)
        levels = sum(len(v) for v in foot.values())
        print(f"  {args.footprint:24} {levels:>9,} price rows over {len(fdays)} sessions"
              + (f", {foot_bad} malformed skipped" if foot_bad else ""))
        print(f"  footprint sessions: {fdays[0]} -> {fdays[-1]}")
    signals = []

    if args.signals:
        with open(args.signals) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2 or parts[0].lower() in ("time", "timestamp"):
                    continue
                try:
                    local = datetime.fromisoformat(parts[0]).replace(tzinfo=zone)
                    price = float(parts[1])
                except ValueError:
                    print(f"  ! skipped: {line}")
                    continue
                label = parts[2].lower() if len(parts) > 2 else ""
                signals.append((local, price, label))

    if args.selftest:
        ordered = sorted(bars.items())
        step = max(1, len(ordered) // 25)
        for dt, _ in ordered[::step][:25]:
            sessions.setdefault(session_of(dt, None), [])
            sess = sessions[session_of(dt, None)]
            keys = [d for d, _ in sess]
            if dt not in keys:
                continue
            i = keys.index(dt)
            k = swing_kind([b for _, b in sess], i, 15)
            label = "bullish" if k == "low" else "bearish"
            signals.append((dt.astimezone(zone), sess[i][1]["c"], label))
        print(f"\nselftest: judging {len(signals)} arbitrary swing points as if they were signals")

    if not signals:
        sys.exit("nothing to study: pass --signals or --selftest")

    print(f"reading signal times as {args.tz}")
    print("=" * 84)
    print(f"{'signal (local)':17} {'utc':17} {'phase':10} {'vol x':>6} {'delta':>7} "
          f"{'vwap':>8} {'fwd15':>8} {'swing15':>8}")
    print("-" * 84)

    rows = []
    missing = 0

    for local, price, label in signals:
        dt = local.astimezone(timezone.utc).replace(second=0, microsecond=0)
        sess = session_of(dt, None)
        pairs = sessions.get(sess)

        if not pairs or dt not in bars:
            missing += 1
            continue

        keys = [d for d, _ in pairs]
        idx = keys.index(dt)
        med = medians.get(sess, 0.0)

        res = describe(pairs, idx, price, dt, label, med)

        if foot is not None:
            res["levels"] = level_context(foot.get(sess), dt, price)
        low = label.lower()
        res["direction"] = "bullish" if ("bull" in low or low in ("u", "up", "▲")) else \
                           "bearish" if ("bear" in low or low in ("d", "down", "▼")) else ""
        rows.append(res)

        fwd = res["fwd_15"]
        print(f"{local.strftime('%Y-%m-%d %H:%M'):17} {res['utc']:17} {res['phase']:10} "
              f"{(res['vol_x_median'] or 0):6.2f} {res['delta']:+7.0f} "
              f"{res['tick_from_vwap']:+8.2f} "
              f"{(f'{fwd:+.2f}' if fwd is not None else 'n/a'):>8} "
              f"{res['swing_15']:>8}")

    if missing:
        print(f"\n!! {missing} of {len(signals)} had no bar in the covered range")

    # Matched baseline: arbitrary minutes from the very same sessions.
    import random
    random.seed(7)
    baselines = []
    for sess in {session_of(datetime.fromisoformat(r["utc"]).replace(tzinfo=timezone.utc), None) for r in rows}:
        pairs = sessions.get(sess)
        if not pairs:
            continue
        for _ in range(min(30, len(pairs))):
            i = random.randrange(len(pairs))
            b = describe(pairs, i, pairs[i][1]["c"], pairs[i][0], med=medians.get(sess, 0.0))
            b["direction"] = ""
            baselines.append(b)

    if rows:
        for r in rows:
            if r["direction"]:
                r["evidence_score"], r["evidence_notes"] = evidence(r, r["direction"])

        summarise(rows, baselines)

        print("\n" + "=" * 84)
        print("PER-SIGNAL EVIDENCE")
        print("=" * 84)
        for r in rows:
            if not r["direction"]:
                continue
            print(f"\n{r['utc']} UTC  {r['direction']:8} score {r.get('evidence_score', 0):+d}")
            print(f"  bar  O{r['bar_o']} H{r['bar_h']} L{r['bar_l']} C{r['bar_c']}  "
                  f"vol {r['vol']:.0f} ({r['vol_x_median']}x median, {r['vol_pctile']:.0f}th pct)")
            print(f"  delta {r['delta']:+.0f}   bid {r['bid_vol']:.0f} / ask {r['ask_vol']:.0f}"
                  f"{'' if r['real_bidask'] else '   [NO REAL BID/ASK - inferred delta]'}")
            print(f"  vs session: open {r['tick_from_session_open']:+.2f}  vwap {r['tick_from_vwap']:+.2f}"
                  f"  high {r['tick_from_session_high']:+.2f}  low {r['tick_from_session_low']:+.2f}")
            print(f"  swing: 5-bar {r['swing_5'] or '-'}  15-bar {r['swing_15'] or '-'}  45-bar {r['swing_45'] or '-'}")
            print(f"  before: 5m {r['runup_5']}  15m {r['runup_15']}  30m {r['runup_30']}")
            print(f"  after:  5m {r['fwd_5']}  15m {r['fwd_15']}  30m {r['fwd_30']}  60m {r['fwd_60']}")
            lv = r.get("levels") or {}
            if lv:
                print(f"  book @price: traded {lv['at_price_bid']:.0f} bid / {lv['at_price_ask']:.0f} ask"
                      f"  imbalance {lv['at_price_imbalance']:+.2f}")
                print(f"  book +-8 ticks (first {lv['window_bars']} bars): "
                      f"{lv['band_bid']:.0f} bid / {lv['band_ask']:.0f} ask  "
                      f"delta {lv['band_delta']:+.0f}  imbalance {lv['band_imbalance']:+.2f}")
                if lv["level_rank"] is not None:
                    print(f"  level rank: {lv['level_rank'] + 1} of {lv['levels_traded']} traded levels"
                          f"  (busiest {lv['top_level_price']} with {lv['top_level_vol']:.0f})")

    print("\n" + "-" * 84)
    print("Trust the z-scores, not the story: |z| < 2 means these minutes are indistinguishable from")
    print("arbitrary minutes on the same sessions.")


if __name__ == "__main__":
    main()
