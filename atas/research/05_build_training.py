"""
05_build_training.py — candidate targets, path labels, and features.  (fast version)

Two passes, deliberately separated by cost:

  PASS A — structural base rates over ALL 1,469 trading days (2019-2026).
           Label: was the level touched before the session ended?
           Cheap and vectorised: one first-touch index per level per day.

  PASS B — full path-race design matrix over the footprint window only (164 days),
           because that is the only period with per-price order flow.

THE QUESTION, precisely:
  "I am already in a trade at E with direction D and a stop S points away.
   For liquidity level T, what is P(price reaches T before it reaches my stop)?"

Race labelling is conservative (adverse-excursion bias):
  if a single 1-min bar spans BOTH the target and the stop, the stop counts as
  hit first. Every probability this produces is therefore a slight UNDER-estimate,
  which is the safe direction for a targeting tool.

Outputs (in /tmp/mnq/derived):
  design_fp.parquet   feature matrix + y20/y40/y60 labels
  base_levels.parquet one row per (trading day, level type) with touch outcomes
"""
import json
import os

import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
TICK = 0.25
STOP_GRID = (20, 40, 60)
SUBSAMPLE = 3
MIN_TIME_LEFT = 30
MAX_DIST = 250.0          # ignore absurdly distant candidates
MIN_DIST_TICKS = 2

LEVEL_COLS = {
    "PDH": "pdh", "PDL": "pdl", "PDC": "pdc",
    "ONH": "on_high", "ONL": "on_low",
    "ASIAH": "asia_high", "ASIAL": "asia_low",
    "LONH": "lon_high", "LONL": "lon_low",
    "IBH": "ib_high", "IBL": "ib_low",
}


def first_touch_idx(path_high, path_low, level):
    """Index of first bar touching `level`, or len(path) if never. Vectorised."""
    hits = (path_high >= level) | (path_low <= level)
    idx = np.flatnonzero(hits)
    return int(idx[0]) if len(idx) else len(path_high)


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    lv = pd.read_parquet(f"{OUT}/levels.parquet")
    prof = pd.read_parquet(f"{OUT}/price_volume.parquet")
    meta = json.load(open(f"{OUT}/meta.json"))

    fp_lo = pd.Timestamp(meta["fp_window"][0], unit="ms", tz="UTC").tz_convert("America/New_York")
    fp_hi = pd.Timestamp(meta["fp_window"][1], unit="ms", tz="UTC").tz_convert("America/New_York")
    fp_lo_ms, fp_hi_ms = int(fp_lo.value // 10**6), int(fp_hi.value // 10**6)
    print(f"footprint window: {fp_lo} .. {fp_hi}")

    # per-day prior-session volume map (price -> volume), for resting-liquidity proxies
    prof["pq"] = np.rint(prof["price"].values / TICK).astype(np.int64)
    vol_by_day, rank_by_day = {}, {}
    for td, g in prof.groupby("td"):
        vol_by_day[td] = dict(zip(g["pq"].values, g["volume"].values))
        rank_by_day[td] = np.sort(g["volume"].values)

    lvi = lv.set_index("td")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    # volatility unit from TR(1m) averaged over ~70 min, whole history
    pc = rth["close"].shift(1)
    tr = np.maximum(rth["high"] - rth["low"],
                    np.maximum((rth["high"] - pc).abs(), (rth["low"] - pc).abs()))
    rth = rth.assign(vu=tr.rolling(70, min_periods=20).mean())
    vu_by_ts = dict(zip(rth["timestamp"].values, rth["vu"].values))

    tds = [t for t in sorted(bars["td"].unique()) if t in lvi.index]
    print(f"{len(tds)} trading days")

    base_rows, design_rows = [], []

    for di, td in enumerate(tds):
        if di % 100 == 0:
            print(f"  day {di}/{len(tds)}  base={len(base_rows):,} design={len(design_rows):,}")
        d = rth[rth["td"] == td]
        if len(d) < 60:
            continue
        row = lvi.loc[td]
        if pd.isna(row.get("pdh")):
            continue

        H = d["high"].to_numpy(np.float64)
        L = d["low"].to_numpy(np.float64)
        C = d["close"].to_numpy(np.float64)
        V = d["volume"].to_numpy(np.float64)
        TS = d["timestamp"].to_numpy(np.int64)
        n = len(d)
        in_fp = TS.min() >= fp_lo_ms and TS.max() <= fp_hi_ms

        static = {}
        for k, c in LEVEL_COLS.items():
            v = row.get(c)
            if pd.notna(v):
                static[k] = float(v)

        # Running session extreme at each minute (for SESSH/SESSL candidates)
        run_high = np.maximum.accumulate(H)
        run_low = np.minimum.accumulate(L)
        # shift by one so "session extreme so far" excludes the current bar
        rh_prev = np.concatenate([[H[0]], run_high[:-1]])
        rl_prev = np.concatenate([[L[0]], run_low[:-1]])

        # ---------- PASS A: structural first-touch per level per day ----------
        for name, T in static.items():
            idx = first_touch_idx(H, L, T)
            rth_open = float(d["open"].iloc[0])
            base_rows.append(dict(
                td=td, type=name, level=T, hit=int(idx < n),
                touch_min=idx if idx < n else -1,
                dist_from_open=(T - rth_open),
                above_open=int(T > rth_open),
                range_ratio=(run_high[-1] - run_low[-1]) / max(vu_by_ts.get(TS[0], np.nan), 1e-9)
                if np.isfinite(vu_by_ts.get(TS[0], np.nan)) else np.nan,
                in_fp=in_fp,
            ))
        # session extremes: always "hit" by construction, used as reference
        base_rows.append(dict(td=td, type="SESSH", level=run_high[-1], hit=1,
                              touch_min=int(np.argmax(H == run_high[-1])),
                              dist_from_open=run_high[-1] - float(d["open"].iloc[0]),
                              above_open=1, range_ratio=np.nan, in_fp=in_fp))
        base_rows.append(dict(td=td, type="SESSL", level=run_low[-1], hit=1,
                              touch_min=int(np.argmax(L == run_low[-1])),
                              dist_from_open=run_low[-1] - float(d["open"].iloc[0]),
                              above_open=0, range_ratio=np.nan, in_fp=in_fp))

        if not in_fp:
            continue

        # ---------- PASS B: path-race design matrix ----------
        vu = np.array([vu_by_ts.get(t, np.nan) for t in TS])
        cum_pv = np.cumsum((H + L + C) / 3 * V)
        cum_v = np.cumsum(V)
        vwap = cum_pv / np.maximum(cum_v, 1)
        DL = d["delta"].to_numpy(np.float64) if "delta" in d.columns else np.zeros(n)
        cum_delta = np.cumsum(DL)

        prev_td = tds[di - 1] if di else None
        vmap = vol_by_day.get(prev_td, {})
        vranks = rank_by_day.get(prev_td)
        tot_prev = float(sum(vmap.values())) if vmap else 0.0

        touch = {k: 0 for k in static}

        for t in range(n):
            for k in static:
                if L[t] <= static[k] <= H[t]:
                    touch[k] += 1
            time_left = n - t
            if time_left < MIN_TIME_LEFT or t % SUBSAMPLE:
                continue
            if not np.isfinite(vu[t]) or vu[t] <= 0:
                continue

            E = C[t]
            rng_hi, rng_lo = rh_prev[t], rl_prev[t]
            rng = max(rng_hi - rng_lo, TICK)

            cands = dict(static)
            if rng_hi > E + TICK:
                cands["SESSH"] = float(rng_hi)
            if rng_lo < E - TICK:
                cands["SESSL"] = float(rng_lo)

            # forward paths computed ONCE per minute, reused for every level+stop
            fut_h, fut_l = H[t + 1:], L[t + 1:]
            if len(fut_h) == 0:
                continue
            cmh = np.maximum.accumulate(fut_h)      # running max ahead
            cml = -np.minimum.accumulate(fut_l)     # running negated min ahead

            names = list(cands)
            Tvals = np.array([cands[k] for k in names], dtype=np.float64)
            dist_all = np.abs(Tvals - E)

            keep = (dist_all >= MIN_DIST_TICKS * TICK) & (dist_all <= MAX_DIST)
            if not keep.any():
                continue
            names_k = [names[i] for i in np.flatnonzero(keep)]
            T_k = Tvals[keep]
            dist_k = dist_all[keep]

            delta_n = cum_delta[t] / max(cum_v[t], 1)
            k5 = max(0, t - 4)
            d5 = DL[k5:t + 1].sum() / max(V[k5:t + 1].sum(), 1)

            for direction in (1, -1):
                if direction == 1:
                    ok = T_k > E + TICK
                    idxT = np.searchsorted(cmh, T_k, side="left")
                else:
                    ok = T_k < E - TICK
                    idxT = np.searchsorted(cml, -T_k, side="left")
                if not ok.any():
                    continue

                sub_names = [names_k[i] for i in np.flatnonzero(ok)]
                sub_T = T_k[ok]
                sub_dist = dist_k[ok]
                sub_idxT = np.minimum(idxT[ok], len(fut_h))

                # stop-before-target index, vectorised across levels & stop sizes
                for sp in STOP_GRID:
                    S = E - direction * sp
                    if direction == 1:
                        idxS = np.minimum(np.searchsorted(cml, -S, side="left"), len(fut_h))
                    else:
                        idxS = np.minimum(np.searchsorted(cmh, S, side="left"), len(fut_h))
                    lab = (sub_idxT < idxS).astype(np.int8)
                    for j, nm in enumerate(sub_names):
                        pq = int(round(sub_T[j] / TICK))
                        vT = vmap.get(pq, 0)
                        design_rows.append(dict(
                            td=str(td), type=nm, direction=direction, stop=sp,
                            y=int(lab[j]),
                            dist_pts=sub_dist[j],
                            dist_atr=sub_dist[j] / vu[t],
                            rr=sub_dist[j] / sp,
                            time_left=time_left, time_in=t,
                            range_pos=(E - rng_lo) / rng,
                            range_atr=rng / vu[t],
                            vwap_dist=(E - vwap[t]) / vu[t] * direction,
                            cum_delta_signed=delta_n * direction,
                            delta5_signed=d5 * direction,
                            vol_share=(vT / tot_prev * 1e4) if tot_prev else 0.0,
                            vol_rank=(np.searchsorted(vranks, vT, side="left") / len(vranks))
                            if (vranks is not None and len(vranks) and vT) else 0.0,
                            dist_sess_hi=(rng_hi - E) / vu[t],
                            dist_sess_lo=(E - rng_lo) / vu[t],
                            untested=int(touch.get(nm, 0) == 0),
                            tests_today=min(touch.get(nm, 0), 3),
                            a_vol=d["ask_volume"].to_numpy()[t] if "ask_volume" in d else 0,
                            b_vol=d["bid_volume"].to_numpy()[t] if "bid_volume" in d else 0,
                            vol_unit=vu[t],
                        ))

    base = pd.DataFrame(base_rows)
    base.to_parquet(f"{OUT}/base_levels.parquet", index=False)
    print(f"\nPASS A rows: {base.shape}")

    df = pd.DataFrame(design_rows)
    df.to_parquet(f"{OUT}/design_fp.parquet", index=False)
    print(f"PASS B rows: {df.shape}")
    print("\nbase rate P(target before stop), by stop size:")
    for sp in STOP_GRID:
        s = df[df["stop"] == sp]
        print(f"  {sp}pt stop: {s['y'].mean()*100:.1f}%   (n={len(s):,})")


if __name__ == "__main__":
    main()
