#!/usr/bin/env python3
"""
Free GEX / IV walls from CBOE's public delayed-quote option chain.

Reference implementation of the exact math used by the ATAS indicator in
../src/GexWalls. Run this to sanity-check the numbers before trusting the
chart, or use it as a standalone poller that writes the JSON file the
indicator can read in "Local JSON file" mode.

Data source (free, no key, no login):
    https://cdn.cboe.com/api/global/delayed_quotes/options/<SYMBOL>.json
    index symbols are prefixed with an underscore: _SPX, _NDX, _RUT, _VIX
    15-minute delayed, greeks computed by CBOE, open interest is T-1.

Usage:
    python gex_levels.py --symbol _NDX
    python gex_levels.py --symbol QQQ --normalize-spot 29800
    python gex_levels.py --symbol _SPX --watch 300 --write-cache
    python gex_levels.py --file chain.json --symbol _SPX

No third-party packages required.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import math
from datetime import datetime, timezone

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
USER_AGENT = "gex-walls/1.0 (+https://github.com/fixedlook/ss)"
CONTRACT_MULTIPLIER = 100

# ^([root])(yymmdd)(C|P)(strike in 1/1000 dollars)$
# e.g. SPXW261120C02900000 -> root SPXW, 2026-11-20, call, 2900.00
_OPT_RE = re.compile(r"^(?P<root>.+?)(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8}|[1-9]\d*)$")


def parse_symbol(symbol):
    """Split an OSI option symbol into (root, expiry date, is_call, strike)."""
    if not symbol:
        return None
    m = _OPT_RE.match(symbol.strip().upper())
    if not m:
        return None
    try:
        expiry = datetime.strptime("20" + m.group("exp"), "%Y%m%d").date()
    except ValueError:
        return None
    strike = int(m.group("strike")) / 1000.0
    return m.group("root"), expiry, m.group("cp") == "C", strike


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_chain(payload, source=""):
    """Turn the CBOE JSON body into (spot, iv30, contracts)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return 0.0, 0.0, []

    spot = _num(data.get("current_price")) or _num(data.get("close")) or 0.0
    iv30 = _num(data.get("iv30")) or 0.0

    contracts = []
    for row in data.get("options") or []:
        if not isinstance(row, dict):
            continue
        parsed = parse_symbol(row.get("option"))
        if not parsed:
            continue
        root, expiry, is_call, strike = parsed
        contracts.append(
            {
                "symbol": row.get("option"),
                "root": root,
                "expiry": expiry,
                "is_call": is_call,
                "strike": strike,
                "bid": _num(row.get("bid")) or 0.0,
                "ask": _num(row.get("ask")) or 0.0,
                "last": _num(row.get("last_trade_price")) or 0.0,
                "iv": _num(row.get("iv")) or 0.0,
                "delta": _num(row.get("delta")) or 0.0,
                "gamma": _num(row.get("gamma")) or 0.0,
                "vega": _num(row.get("vega")) or 0.0,
                "oi": _num(row.get("open_interest")) or 0.0,
                "volume": _num(row.get("volume")) or 0.0,
            }
        )
    return spot, iv30, contracts


def iv_scale_of(contracts):
    """0.01 when the chain quotes iv in percent points (18.5), else 1.

    Median based, so a couple of junk rows cannot flip the whole chain.
    """
    values = sorted(c["iv"] for c in contracts if c.get("iv", 0) > 0)
    if not values:
        return 1.0
    return 0.01 if values[len(values) // 2] > 1.0 else 1.0


def bs_gamma(spot, strike, years, vol):
    """Black-Scholes gamma, r = 0. Vol is a fraction (0.15 = 15%)."""
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return 0.0
    denom = vol * math.sqrt(years)
    if denom <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * years) / denom
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * denom)


def zero_gamma_flip(rows, spot, multiplier, band_pct=12.0, steps=80):
    """The price where net gamma dollars cancel, by re-pricing every contract.

    `rows` are (strike, years, vol_fraction, oi, sign) with sign +1 for calls
    and -1 for puts. Returns (value, crossings, gex_at_spot, band_low, band_high).

    This is not the cumulative-by-strike crossing: that proxy drifts into the
    wings and answered -20% from spot for SPX.
    """
    low = spot * (1 - band_pct / 100.0)
    high = spot * (1 + band_pct / 100.0)
    step = (high - low) / steps

    def net_gex(candidate):
        total = 0.0
        for strike, years, vol, oi, sign in rows:
            g = bs_gamma(candidate, strike, years, vol)
            if g > 0:
                total += sign * g * oi * multiplier * candidate * candidate * 0.01
        return total

    crossings = []
    prev_gex = None
    prev_spot = None
    best = None
    for i in range(steps + 1):
        candidate = low + step * i
        gex = net_gex(candidate)
        if prev_gex is not None and prev_gex != gex:
            if (prev_gex < 0 <= gex) or (prev_gex > 0 >= gex):
                crossing = prev_spot + (candidate - prev_spot) * (-prev_gex) / (gex - prev_gex)
                crossings.append(crossing)
        prev_gex, prev_spot = gex, candidate

    if crossings:
        best = min(crossings, key=lambda x: abs(x - spot))
    return best, len(crossings), net_gex(spot), low, high


def _wall(strikes_to_values, take):
    """Highest `take` strikes by value, descending."""
    ranked = sorted(strikes_to_values.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in ranked[:take]]


def compute(contracts, spot, min_dte=0, max_dte=45, max_dist_pct=20.0,
            multiplier=CONTRACT_MULTIPLIER, vol_proxy_today=False,
            nearest_expiry_max_dte=10, today=None, flip_band_pct=12.0,
            iv_wall_min_oi_fraction=0.02):
    """
    Aggregate the chain into the level set the indicator draws.

    GEX per contract is gamma * OI * multiplier * spot^2 * 0.01, i.e. dollars of
    dealer delta that must be hedged per 1% move in the underlying. Calls are
    treated as positive dealer gamma, puts as negative (the usual convention:
    dealers are long call gamma and short put gamma against customer flow).
    """
    today = today or datetime.now(timezone.utc).date()
    levels = {
        "zero_gamma": None,
        "call_wall_1": None,
        "call_wall_2": None,
        "put_wall_1": None,
        "put_wall_2": None,
        "iv_wall_1": None,
        "iv_wall_2": None,
        "max_pain": None,
        "em_high": None,
        "em_low": None,
    }
    out = {
        "spot": round(spot, 4),
        "contracts_used": 0,
        "total_gex": 0.0,
        "put_call_oi_ratio": None,
        "levels": levels,
    }
    if spot <= 0 or not contracts:
        return out

    lo = spot * (1 - max_dist_pct / 100.0) if max_dist_pct > 0 else 0.0
    hi = spot * (1 + max_dist_pct / 100.0) if max_dist_pct > 0 else float("inf")

    call_gex, put_gex = {}, {}
    iv_oi, iv_weighted = {}, {}
    call_oi = put_oi = 0.0
    used = 0
    expiries = {}
    flip_rows = []
    iv_scale = iv_scale_of(contracts)

    for c in contracts:
        dte = (c["expiry"] - today).days
        if dte < min_dte or dte > max_dte:
            continue
        # Nearest-expiry slice for max pain / expected move is built from the
        # unfiltered-by-distance set, so keep collecting it separately.
        if 0 <= dte <= nearest_expiry_max_dte:
            expiries.setdefault(c["expiry"], []).append(c)

        if not (lo <= c["strike"] <= hi):
            continue
        oi = c["oi"]
        if vol_proxy_today and dte == 0:
            oi = max(oi, c["volume"])
        if oi <= 0 or c["gamma"] <= 0:
            continue

        used += 1
        gex = c["gamma"] * oi * multiplier * spot * spot * 0.01
        if c["is_call"]:
            call_gex[c["strike"]] = call_gex.get(c["strike"], 0.0) + gex
            call_oi += oi
        else:
            put_gex[c["strike"]] = put_gex.get(c["strike"], 0.0) + gex
            put_oi += oi
        iv = c["iv"] * iv_scale
        if iv > 0:
            iv_oi[c["strike"]] = iv_oi.get(c["strike"], 0.0) + oi
            iv_weighted[c["strike"]] = iv_weighted.get(c["strike"], 0.0) + iv * oi
            flip_rows.append((c["strike"], max(dte, 0.5) / 365.0, iv, oi, 1.0 if c["is_call"] else -1.0))

    out["contracts_used"] = used
    if call_oi + put_oi > 0:
        out["put_call_oi_ratio"] = round(put_oi / call_oi, 4) if call_oi > 0 else None

    # --- walls -------------------------------------------------------------
    # Call walls are resistance: only strikes at or above spot can be one.
    # Put walls are support: only strikes at or below spot. Fall back to the
    # full side when the filter leaves nothing (deep ITM-only books).
    call_side = {k: v for k, v in call_gex.items() if k >= spot} or call_gex
    put_side = {k: v for k, v in put_gex.items() if k <= spot} or put_gex
    calls = _wall(call_side, 2)
    puts = _wall(put_side, 2)
    while len(calls) < 2:
        calls.append(None)
    while len(puts) < 2:
        puts.append(None)
    levels["call_wall_1"], levels["call_wall_2"] = calls[0], calls[1]
    levels["put_wall_1"], levels["put_wall_2"] = puts[0], puts[1]

    # IV walls: the vol peak among liquid strikes, not sum(iv * OI) - that just
    # re-ranks open interest and returns the put wall a second time.
    iv_peaks = {}
    if iv_oi:
        floor = max(iv_oi.values()) * iv_wall_min_oi_fraction
        for strike, contracts_oi in iv_oi.items():
            if contracts_oi <= 0 or contracts_oi < floor:
                continue
            iv_peaks[strike] = iv_weighted[strike] / contracts_oi

    ivs = _wall(iv_peaks, 2)
    while len(ivs) < 2:
        ivs.append(None)
    levels["iv_wall_1"], levels["iv_wall_2"] = ivs[0], ivs[1]

    # --- total and zero-gamma flip ----------------------------------------
    strikes = sorted(set(call_gex) | set(put_gex))
    net = [call_gex.get(k, 0.0) - put_gex.get(k, 0.0) for k in strikes]
    out["total_gex"] = round(sum(net), 2)

    zero, flip_crossings, gex_at_spot, band_low, band_high = zero_gamma_flip(
        flip_rows, spot, multiplier, band_pct=flip_band_pct)
    levels["zero_gamma"] = round(zero, 2) if zero is not None else None
    out["gamma_flip"] = {
        "value": round(zero, 2) if zero is not None else None,
        "crossings": flip_crossings,
        "band": [round(band_low, 2), round(band_high, 2)],
        "gex_at_spot_blackscholes": round(gex_at_spot, 2),
        "gex_at_spot_chain_gamma": out["total_gex"],
        "iv_units": "percent" if iv_scale < 1 else "fraction",
    }

    # --- max pain / expected move on the nearest expiry -------------------
    if expiries:
        nearest = min(expiries)
        rows = expiries[nearest]
        if not nearest_is_stale(nearest, today, nearest_expiry_max_dte):
            levels["max_pain"] = max_pain(rows, spot)
            em_low, em_high = expected_move(rows, spot)
            levels["em_low"], levels["em_high"] = em_low, em_high

    return out


def nearest_is_stale(expiry, today, max_dte):
    return not (0 <= (expiry - today).days <= max_dte)


def max_pain(rows, spot=0.0):
    """Strike where option holders collectively lose the most at expiry.

    Payout is often flat over a wide band, so ties resolve to the strike
    closest to spot rather than to the lowest strike.
    """
    strikes = sorted({c["strike"] for c in rows})
    if not strikes:
        return None
    best_key, best = None, None
    for settle in strikes:
        payout = 0.0
        for c in rows:
            if c["is_call"]:
                payout += c["oi"] * max(0.0, settle - c["strike"])
            else:
                payout += c["oi"] * max(0.0, c["strike"] - settle)
        key = (round(payout, 6), abs(settle - spot))
        if best_key is None or key < best_key:
            best_key, best = key, settle
    return best


def expected_move(rows, spot):
    """1-sigma expected move from the at-the-money straddle mid of one expiry."""
    if not rows or spot <= 0:
        return None, None
    atm = min(rows, key=lambda c: abs(c["strike"] - spot))["strike"]
    call = next((c for c in rows if c["is_call"] and c["strike"] == atm), None)
    put = next((c for c in rows if not c["is_call"] and c["strike"] == atm), None)

    def mid(c):
        if c is None:
            return None
        if c["bid"] > 0 and c["ask"] > 0:
            return (c["bid"] + c["ask"]) / 2.0
        return c["last"] if c["last"] > 0 else (c["ask"] or c["bid"] or None)

    cm, pm = mid(call), mid(put)
    if not cm or not pm:
        return None, None
    move = cm + pm
    return round(spot - move, 2), round(spot + move, 2)


def fetch_cboe(symbol, timeout=25):
    url = CBOE_URL.format(symbol=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def cache_path(source):
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "ATAS X", "GexWalls", "levels_" + source.replace("/", "_") + ".json")


def build_result(symbol, payload=None, file=None, min_dte=0, max_dte=45,
                 max_dist_pct=20.0, vol_proxy_today=False, normalize_spot=0.0):
    if payload is None:
        if file:
            with open(file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        else:
            payload = fetch_cboe(symbol)

    spot, iv30, contracts = parse_chain(payload, symbol)
    # QQQ/SPY -> futures price space: pre-scale the spot so the levels come out
    # in the units of the instrument you actually trade.
    if normalize_spot > 0 and spot > 0:
        spot = normalize_spot

    result = compute(
        contracts,
        spot,
        min_dte=min_dte,
        max_dte=max_dte,
        max_dist_pct=max_dist_pct,
        vol_proxy_today=vol_proxy_today,
    )
    result["source"] = symbol
    result["iv30"] = round(iv30 * 100, 2) if iv30 and iv30 < 3 else round(iv30, 2)
    result["updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def describe(result):
    levels = result["levels"]
    spot = result["spot"]
    lines = [
        "source {}   spot {:.2f}   {} contracts in window   iv30 {}".format(
            result["source"], spot, result["contracts_used"], result.get("iv30")
        ),
    ]
    order = [
        ("zero_gamma", "GEX zero-gamma flip"),
        ("call_wall_1", "call wall 1"),
        ("call_wall_2", "call wall 2"),
        ("put_wall_1", "put wall 1"),
        ("put_wall_2", "put wall 2"),
        ("iv_wall_1", "IV wall 1"),
        ("iv_wall_2", "IV wall 2"),
        ("max_pain", "max pain"),
        ("em_high", "expected move high"),
        ("em_low", "expected move low"),
    ]
    for key, label in order:
        value = levels.get(key)
        if value is None:
            lines.append("  {:<22} -".format(label))
            continue
        lines.append("  {:<22} {:>10.2f}   ({:+.2f}%)".format(label, value, (value - spot) / spot * 100))
    lines.append("  total gex           {:>10.0f}  $ per 1% move".format(result["total_gex"]))
    if result.get("put_call_oi_ratio") is not None:
        lines.append("  put/call OI ratio   {:>10.2f}".format(result["put_call_oi_ratio"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compute GEX / IV walls from CBOE's free delayed option chain.")
    ap.add_argument("--symbol", default="_SPX", help="_SPX, _NDX, _RUT, QQQ, SPY ...")
    ap.add_argument("--file", help="read a saved CBOE JSON body instead of downloading")
    ap.add_argument("--json", help="write the result to this path")
    ap.add_argument("--write-cache", action="store_true",
                    help="also write the ATAS cache file (levels_<symbol>.json)")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="refresh forever every SECONDS")
    ap.add_argument("--min-dte", type=int, default=0)
    ap.add_argument("--max-dte", type=int, default=45)
    ap.add_argument("--dist", type=float, default=20.0, help="max strike distance from spot, %%")
    ap.add_argument("--vol-proxy", action="store_true",
                    help="use volume as OI for today's expiry (stale-OI workaround)")
    ap.add_argument("--normalize-spot", type=float, default=0.0,
                    help="rescale every level so spot equals this price (e.g. QQQ -> NQ)")
    args = ap.parse_args(argv)

    def once():
        try:
            result = build_result(
                args.symbol,
                payload=None,
                file=args.file,
                min_dte=args.min_dte,
                max_dte=args.max_dte,
                max_dist_pct=args.dist,
                vol_proxy_today=args.vol_proxy,
                normalize_spot=args.normalize_spot,
            )
        except urllib.error.HTTPError as exc:
            print("HTTP {} for {} - symbol not on CBOE delayed quotes?".format(exc.code, args.symbol),
                  file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - CLI
            print("error: {}".format(exc), file=sys.stderr)
            return 1

        print(describe(result))
        body = json.dumps(result, indent=2)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                fh.write(body)
        if args.write_cache:
            path = cache_path(args.symbol)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, path)
            print("wrote {}".format(path))
        return 0

    if args.watch <= 0:
        return once()

    while True:
        print("--- {}".format(datetime.now().strftime("%H:%M:%S")))
        once()
        time.sleep(max(30, args.watch))


if __name__ == "__main__":
    sys.exit(main())
