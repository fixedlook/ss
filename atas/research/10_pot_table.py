"""
10_pot_table.py — final empirical PoT table for the ATAS indicator.

Changed premise: order flow added no predictive power (see FINDINGS.md), and the
one thing that dominates is reward:risk. That means the probability table does
NOT need the per-price footprint, so it can be calibrated on the full 1,469
trading days instead of the 164-day footprint window. Far more trustworthy.

Final table index:
    type_group  x  rr_bucket  x  stop  x  time_bucket   ->  PoT, n

Also answers two design questions the indicator depends on:
  Q1  At a fixed R:R, does the absolute stop size change PoT?
  Q2  With distance controlled, is the FVG/swing advantage still there?
"""
import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
TICK = 0.25
STOP_GRID = (20, 40, 60)
SUBSAMPLE = 3
MIN_TIME_LEFT = 30
MAX_DIST = 300.0
K = 3

LEVEL_COLS = {
    "PDH": ("pdh", "session_hl"), "PDL": ("pdl", "session_hl"),
    "ONH": ("on_high", "session_hl"), "ONL": ("on_low", "session_hl"),
    "ASIAH": ("asia_high", "session_hl"), "ASIAL": ("asia_low", "session_hl"),
    "LONH": ("lon_high", "session_hl"), "LONL": ("lon_low", "session_hl"),
    "IBH": ("ib_high", "session_hl"), "IBL": ("ib_low", "session_hl"),
    "PDC": ("pdc", "pdc"),
}
GROUP = {k: v[1] for k, v in LEVEL_COLS.items()}
GROUP["SESSH"] = "sess_extreme"
GROUP["SESSL"] = "sess_extreme"
GROUP["SWINGH"] = "swing"
GROUP["SWINGL"] = "swing"
GROUP["FVGUP"] = "fvg"
GROUP["FVGDN"] = "fvg"

RR_BINS = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 100]
RR_LBL = ["<0.5", "0.5-1", "1-1.5", "1.5-2", "2-3", "3-4", "4-6", "6+"]
T_LBL = ["first_90m", "90-210m", "last_90m"]


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    lv = pd.read_parquet(f"{OUT}/levels.parquet").set_index("td")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    tds = [t for t in sorted(rth["td"].unique()) if t in lv.index]
    print(f"{len(tds)} trading days")

    recs = []
    for di, td in enumerate(tds):
        if di % 150 == 0:
            print(f"  day {di}/{len(tds)} rows={len(recs):,}")
        d = rth[rth["td"] == td]
        n = len(d)
        if n < 60:
            continue
        row = lv.loc[td]
        if pd.isna(row.get("pdh")):
            continue
        H = d["high"].to_numpy(np.float64)
        L = d["low"].to_numpy(np.float64)
        C = d["close"].to_numpy(np.float64)

        pcs = np.concatenate([[H[0]], C[:-1]])
        tr = np.maximum(H - L, np.maximum(np.abs(H - pcs), np.abs(L - pcs)))
        vu = pd.Series(tr).rolling(70, min_periods=20).mean().to_numpy()

        static = {k: float(row[c]) for k, (c, _) in LEVEL_COLS.items()
                  if pd.notna(row.get(c))}

        run_high = np.maximum.accumulate(H)
        run_low = np.minimum.accumulate(L)
        rh_prev = np.concatenate([[H[0]], run_high[:-1]])
        rl_prev = np.concatenate([[L[0]], run_low[:-1]])

        # PD arrays, causal
        s_hi = [(j + K, H[j]) for j in range(K, n - K) if H[j] == H[j - K:j + K + 1].max()]
        s_lo = [(j + K, L[j]) for j in range(K, n - K) if L[j] == L[j - K:j + K + 1].min()]
        s_hi_ka = np.array([b for b, _ in s_hi], dtype=np.int64)
        s_hi_p = np.array([p for _, p in s_hi], dtype=np.float64)
        s_lo_ka = np.array([b for b, _ in s_lo], dtype=np.int64)
        s_lo_p = np.array([p for _, p in s_lo], dtype=np.float64)
        fv = [(i + 1, H[i - 1], L[i + 1], +1) for i in range(1, n - 1) if L[i + 1] > H[i - 1]]
        fv += [(i + 1, H[i + 1], L[i - 1], -1) for i in range(1, n - 1) if H[i + 1] < L[i - 1]]
        if fv:
            f_ka = np.array([b for b, *_ in fv], dtype=np.int64)
            f_lo = np.array([l for _, l, _, _ in fv], dtype=np.float64)
            f_hi = np.array([h for _, _, h, _ in fv], dtype=np.float64)
            f_d = np.array([dd for *_, dd in fv], dtype=np.int8)
        else:
            f_ka = f_lo = f_hi = np.zeros(0); f_d = np.zeros(0, dtype=np.int8)

        for t in range(n):
            time_left = n - t
            if time_left < MIN_TIME_LEFT or t % SUBSAMPLE:
                continue
            if not np.isfinite(vu[t]) or vu[t] <= 0:
                continue
            E = C[t]
            cands = dict(static)
            if rh_prev[t] > E + TICK:
                cands["SESSH"] = float(rh_prev[t])
            if rl_prev[t] < E - TICK:
                cands["SESSL"] = float(rl_prev[t])
            m = (s_hi_ka <= t) & (s_hi_p > E + TICK)
            if m.any():
                cands["SWINGH"] = float(s_hi_p[m].min())
            m = (s_lo_ka <= t) & (s_lo_p < E - TICK)
            if m.any():
                cands["SWINGL"] = float(s_lo_p[m].max())
            m = (f_ka <= t) & (f_d == +1) & (f_hi < E - TICK)
            if m.any():
                cands["FVGDN"] = float(f_hi[m].max())
            m = (f_ka <= t) & (f_d == -1) & (f_lo > E + TICK)
            if m.any():
                cands["FVGUP"] = float(f_lo[m].min())

            fut_h, fut_l = H[t + 1:], L[t + 1:]
            cmh = np.maximum.accumulate(fut_h)
            cml = -np.minimum.accumulate(fut_l)
            tb = T_LBL[0] if t < 90 else (T_LBL[1] if t < 210 else T_LBL[2])

            for direction in (1, -1):
                for nm, T in cands.items():
                    if direction == 1 and not T > E + TICK:
                        continue
                    if direction == -1 and not T < E - TICK:
                        continue
                    dist = abs(T - E)
                    if dist > MAX_DIST or dist < 2 * TICK:
                        continue
                    idxT = min(np.searchsorted(cmh if direction == 1 else cml,
                                               T if direction == 1 else -T, "left"),
                               len(fut_h))
                    for sp in STOP_GRID:
                        S = E - direction * sp
                        idxS = min(np.searchsorted(cml if direction == 1 else cmh,
                                                   -S if direction == 1 else S, "left"),
                                   len(fut_h))
                        recs.append((GROUP[nm], sp, tb, dist / sp, dist, dist / vu[t],
                                     int(idxT < idxS)))

    df = pd.DataFrame(recs, columns=["grp", "stop", "tb", "rr", "dist", "datr", "y"])
    df.to_parquet(f"{OUT}/pot_raw.parquet", index=False)
    print(f"\nraw rows: {len(df):,}")

    df["rr_b"] = pd.cut(df["rr"], RR_BINS, labels=RR_LBL)
    tab = df.groupby(["grp", "rr_b", "stop", "tb"], observed=True).agg(
        pot=("y", "mean"), n=("y", "size")).reset_index()
    tab = tab[tab["n"] >= 150]
    tab.to_csv(f"{OUT}/pot_final_table.csv", index=False)
    print(f"final table cells: {len(tab):,}")

    # ---------------------------------------------------------------- Q1
    print("\n=== Q1: at a fixed R:R, does stop size change PoT? (40pt vs 60pt) ===")
    q = df[df["stop"].isin([40, 60])].groupby(["rr_b", "stop"], observed=True)["y"].agg(["mean", "size"])
    q = q.unstack()
    print((q["mean"] * 100).round(1).to_string())
    print("\n  difference (60pt minus 40pt), percentage points:")
    diff = ((q["mean"][60] - q["mean"][40]) * 100).round(1)
    print(diff.to_string())

    # ---------------------------------------------------------------- Q2
    print("\n=== Q2: with R:R controlled, does FVG/swing still beat session levels? ===")
    sub = df[df["stop"] == 40]
    piv = sub.groupby(["rr_b", "grp"], observed=True)["y"].agg(["mean", "size"]).reset_index()
    piv = piv[piv["size"] >= 150]
    print((piv.pivot(index="rr_b", columns="grp", values="mean") * 100).round(1).to_string())
    print("\n  sample sizes:")
    print(piv.pivot(index="rr_b", columns="grp", values="size").fillna(0).astype(int).to_string())
    eq = (piv.pivot(index="rr_b", columns="grp", values="mean") * 100).mean().sort_values(ascending=False)
    print("\n  equal-weighted mean PoT across R:R buckets:")
    for k, v in eq.items():
        print(f"    {k:14s} {v:5.1f}%")

    # ------------------------------------------------ recommend default groups
    print("\n=== what 'nearest qualifying liquidity' actually resolves to ===")
    print(sub.groupby("grp")["y"].agg(["mean", "size"]).round(3).to_string())


if __name__ == "__main__":
    main()
