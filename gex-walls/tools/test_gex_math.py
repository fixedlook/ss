#!/usr/bin/env python3
"""
Offline checks for the GEX/IV wall math in gex_levels.py.

Builds a synthetic but realistically shaped CBOE payload, runs it through the
same parse + compute path the ATAS indicator uses, and asserts the levels come
out where they were deliberately planted. No network access needed:

    python test_gex_math.py
"""

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gex_levels as g  # noqa: E402

SPOT = 29800.0
TODAY = date(2026, 11, 15)

# Gamma decays with distance from the money, roughly like a real chain.
GAMMA_BY_DISTANCE = {0: 0.0016, 1: 0.0012, 2: 0.0008, 3: 0.0004, 4: 0.00015}


def osi(root, expiry, is_call, strike):
    return "{}{}{}{:08d}".format(root, expiry.strftime("%y%m%d"), "C" if is_call else "P", int(round(strike * 1000)))


def contract(root, expiry, is_call, strike, oi, iv, volume=0, gamma=None, bid=1.0, ask=1.2):
    dist = int(abs(strike - SPOT) / 100)
    if gamma is None:
        gamma = GAMMA_BY_DISTANCE.get(dist, 0.00005)
    return {
        "option": osi(root, expiry, is_call, strike),
        "bid": bid,
        "ask": ask,
        "last_trade_price": (bid + ask) / 2,
        "volume": volume,
        "open_interest": oi,
        "iv": iv,
        "delta": 0.5,
        "gamma": gamma,
        "theta": -1.0,
        "vega": 10.0,
        "rho": 0.1,
    }


def build_payload():
    near = TODAY + timedelta(days=1)     # inside the nearest-expiry window
    far = TODAY + timedelta(days=120)    # outside the GEX window (max 45)
    rows = []
    strikes = [29400.0, 29500.0, 29600.0, 29700.0, 29800.0, 29900.0, 30000.0, 30100.0, 31000.0]

    # Planted structure:
    #   biggest call gamma*OI above spot -> 30000 (call wall 1), 29900 (wall 2)
    #   biggest put  gamma*OI below spot -> 29600 (put wall 1),  29700 (wall 2)
    #   highest IV * OI                  -> 29600 (IV wall 1),   30000 (IV wall 2)
    oi_calls = {29400: 10, 29500: 20, 29600: 300, 29700: 100, 29800: 400,
                29900: 1500, 30000: 3000, 30100: 1200, 31000: 50}
    oi_puts = {29400: 30, 29500: 400, 29600: 2500, 29700: 1400, 29800: 600,
               29900: 200, 30000: 150, 30100: 40, 31000: 5}
    iv_calls = {k: 0.22 for k in oi_calls}
    iv_puts = {k: 0.24 for k in oi_puts}
    iv_calls[30100] = 0.55     # rich IV wall on the call side
    iv_puts[29600] = 0.52      # and on the put side

    for k in strikes:
        rows.append(contract("SPXW", near, True, k, oi_calls[k], iv_calls[k]))
        rows.append(contract("SPXW", near, False, k, oi_puts[k], iv_puts[k]))

    # ATM straddle mid: call 30.0/32.0, put 28.0/30.0 -> 31 + 29 = 60 points.
    for row in rows:
        if row["option"].endswith("C29800000"):
            row["bid"], row["ask"] = 30.0, 32.0
        if row["option"].endswith("P29800000"):
            row["bid"], row["ask"] = 28.0, 30.0

    # Distant expiry: must be ignored by the 45-DTE filter even though its OI
    # dwarfs everything near-dated.
    rows.append(contract("SPXW", far, True, 30000.0, 99_000, 0.30))
    rows.append(contract("SPXW", far, False, 30000.0, 99_000, 0.30))

    # Absurd far-OTM strike: must be dropped by the 20% distance filter.
    rows.append(contract("SPXW", near, True, 45000.0, 50_000, 0.90, gamma=0.5))

    return {
        "timestamp": "2026-11-15 14:30:00",
        "symbol": "_SPX",
        "data": {"symbol": "_SPX", "current_price": SPOT, "close": SPOT, "iv30": 0.185, "options": rows},
    }


def approx(actual, expected, tol=0.01):
    return actual is not None and abs(actual - expected) <= tol


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name + ("   " + detail if detail else ""))
    return bool(condition)


def main():
    payload = build_payload()
    spot, iv30, contracts = g.parse_chain(payload, "_SPX")
    ok = True

    # --- symbol parsing ---------------------------------------------------
    parsed = g.parse_symbol("SPXW261120C02900000")
    ok &= check("parse index symbol", parsed == ("SPXW", date(2026, 11, 20), True, 2900.0), str(parsed))
    parsed = g.parse_symbol("SPY260116P00450000")
    ok &= check("parse equity symbol", parsed == ("SPY", date(2026, 1, 16), False, 450.0), str(parsed))
    ok &= check("reject junk symbol", g.parse_symbol("not-an-option") is None)
    ok &= check("chain spot parsed", approx(spot, SPOT, 1e-6), str(spot))
    ok &= check("chain rows parsed", len(contracts) == 21, str(len(contracts)))

    # --- levels -----------------------------------------------------------
    res = g.compute(contracts, spot, min_dte=0, max_dte=45, max_dist_pct=20.0,
                    nearest_expiry_max_dte=10, today=TODAY)
    lv = res["levels"]

    ok &= check("call wall 1 = 30000", lv["call_wall_1"] == 30000.0, str(lv["call_wall_1"]))
    ok &= check("call wall 2 = 29900", lv["call_wall_2"] == 29900.0, str(lv["call_wall_2"]))
    ok &= check("put wall 1 = 29600", lv["put_wall_1"] == 29600.0, str(lv["put_wall_1"]))
    ok &= check("put wall 2 = 29700", lv["put_wall_2"] == 29700.0, str(lv["put_wall_2"]))
    # IV walls rank the open-interest weighted MEAN iv per strike. Ranking by
    # sum(iv * OI) just re-ranks open interest and returns the put wall again -
    # that is the bug this pair guards.
    ok &= check("iv wall 1 = 30100 (vol peak)", lv["iv_wall_1"] == 30100.0, str(lv["iv_wall_1"]))
    ok &= check("iv wall 2 = 29600", lv["iv_wall_2"] == 29600.0, str(lv["iv_wall_2"]))
    ok &= check("iv wall is not a copy of the put wall", lv["iv_wall_1"] != lv["put_wall_1"],
                "{} vs {}".format(lv["iv_wall_1"], lv["put_wall_1"]))

    # max pain: calls piled at 30000, puts at 29600 -> payout is flat in
    # between, so the tie must resolve to the strike nearest spot.
    ok &= check("max pain = 29800", lv["max_pain"] == 29800.0, str(lv["max_pain"]))
    ok &= check("expected move high = 29860", approx(lv["em_high"], SPOT + 60.0), str(lv["em_high"]))
    ok &= check("expected move low = 29740", approx(lv["em_low"], SPOT - 60.0), str(lv["em_low"]))

    # The distant expiry (99k OI) and the 45000 strike must not be counted.
    ok &= check("dte + distance filters applied", res["contracts_used"] == 18, str(res["contracts_used"]))

    # Zero gamma is the price where re-priced net gamma changes sign, and it has
    # to land inside the scan band. The old cumulative-by-strike proxy drifted
    # into the wings - it answered -20% from spot for a real SPX chain.
    z = lv["zero_gamma"]
    flip = res["gamma_flip"]
    ok &= check("zero gamma inside the flip band",
                z is not None and abs(z - SPOT) < SPOT * 0.12, str(z))
    ok &= check("zero gamma near the re-priced sign change (~29545.49)",
                z is not None and abs(z - 29545.49) < 3.0, str(z))
    ok &= check("flip scan found exactly one crossing", flip["crossings"] == 1, str(flip["crossings"]))
    ok &= check("flip band brackets spot",
                flip["band"][0] < SPOT < flip["band"][1], str(flip["band"]))
    ok &= check("iv units detected as fractions", flip["iv_units"] == "fraction", flip["iv_units"])

    ok &= check("total gex positive with the big call wall", res["total_gex"] > 0, str(res["total_gex"]))
    ok &= check("put/call OI ratio computed", res["put_call_oi_ratio"] not in (None, 0.0),
                str(res["put_call_oi_ratio"]))

    # --- filters behave ---------------------------------------------------
    tight = g.compute(contracts, spot, max_dte=0, today=TODAY)
    ok &= check("max_dte=0 keeps only 0DTE (none here)",
                tight["contracts_used"] == 0 and tight["levels"]["call_wall_1"] is None)

    wide = g.compute(contracts, spot, max_dte=200, max_dist_pct=0, today=TODAY)
    ok &= check("no distance filter picks up the 45000 strike",
                wide["levels"]["call_wall_1"] == 45000.0, str(wide["levels"]["call_wall_1"]))
    ok &= check("no dte cap pulls in the far expiry",
                wide["contracts_used"] == 21, str(wide["contracts_used"]))

    proxy = g.compute(contracts, spot, max_dte=45, vol_proxy_today=True, today=TODAY)
    ok &= check("volume proxy is a no-op when volume is 0",
                proxy["levels"]["call_wall_1"] == 30000.0)

    # --- maths primitives -------------------------------------------------
    # S=K=100, T=1, vol=0.2 -> d1 = 0.1, pdf = 0.396953, gamma = pdf / 20
    atm = g.bs_gamma(100.0, 100.0, 1.0, 0.2)
    ok &= check("black-scholes gamma matches the closed form", approx(atm, 0.0198477, 1e-6), str(atm))

    ok &= check("gamma is safe at zero everything",
                g.bs_gamma(0.0, 100.0, 1.0, 0.2) == 0.0
                and g.bs_gamma(100.0, 100.0, 0.0, 0.2) == 0.0
                and g.bs_gamma(100.0, 100.0, 1.0, 0.0) == 0.0)

    # A one-sided book never crosses zero, so there is no flip to draw.
    calls_only = [(100.0, 0.1, 0.2, 500.0, 1.0)]
    value, crossings, _, _, _ = g.zero_gamma_flip(calls_only, 100.0, 100.0)
    ok &= check("no flip when the book is one-sided", value is None and crossings == 0, str(value))

    puts_only = [(100.0, 0.1, 0.2, 500.0, -1.0)]
    value, _, _, _, _ = g.zero_gamma_flip(puts_only, 100.0, 100.0)
    ok &= check("no flip when the book is all puts", value is None, str(value))

    # A call/put straddle of equal size is gamma-neutral at the money.
    straddle = [(90.0, 0.08, 0.25, 400.0, 1.0), (110.0, 0.08, 0.25, 400.0, -1.0)]
    value, _, _, _, _ = g.zero_gamma_flip(straddle, 100.0, 100.0)
    ok &= check("a call/put straddle flips at the money",
                value is not None and abs(value - 100.0) < 1.5, str(value))

    ok &= check("percent-quoted iv is detected",
                g.iv_scale_of([{"iv": 15.2}, {"iv": 18.5}]) == 0.01)
    ok &= check("fraction-quoted iv is left alone",
                g.iv_scale_of([{"iv": 0.152}, {"iv": 0.185}]) == 1.0)

    # degenerate input must not raise
    empty = g.compute([], 0.0, today=TODAY)
    ok &= check("empty chain is safe", empty["levels"]["zero_gamma"] is None)

    # --- end to end via the CLI code path --------------------------------
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixture.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    try:
        result = g.build_result("_SPX", file=path)
        ok &= check("build_result via file", result["levels"]["call_wall_1"] == 30000.0,
                    str(result["levels"]["call_wall_1"]))
        ok &= check("iv30 reported in percent", approx(result["iv30"], 18.5), str(result["iv30"]))
        ok &= check("describe() renders", "GEX zero-gamma flip" in g.describe(result))
        # JSON round-trip, the way the ATAS indicator reads it back
        blob = json.dumps(result)
        back = json.loads(blob)
        ok &= check("json round-trip keeps levels",
                    back["levels"]["put_wall_1"] == 29600.0 and back["spot"] == SPOT)
    finally:
        if os.path.exists(path):
            os.remove(path)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
