"""
08_pd_arrays.py — add the PD-array level types the reference indicator displays
(swing highs/lows, fair value gaps, breakers) and measure their PoT the same way.

Detection is deliberately simple and causal: everything is computed from bars
strictly up to the decision minute, so no forward information leaks into a level.

  swing high : H[j] is max of a +/- k window, confirmed k bars later
  swing low  : mirror
  FVG        : 3-bar imbalance; bullish gap = zone between c[i-1].high and c[i+1].low
  breaker    : a swing level that price traded through, now referenced from the
               other side (role flip) and still untested since the flip

Labels use the identical conservative race as the main study.
"""
import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
TICK = 0.25
STOP_GRID = (20, 40, 60)
SUBSAMPLE = 3
MIN_TIME_LEFT = 30
MAX_DIST = 250.0
K = 3          # fractal strength for swings


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    meta = pd.read_parquet(f"{OUT}/levels.parquet")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    tds = sorted(rth["td"].unique())

    rows = []
    for di, td in enumerate(tds):
        if di % 20 == 0:
            print(f"  day {di}/{len(tds)} rows={len(rows):,}")
        d = rth[rth["td"] == td]
        if len(d) < 60:
            continue
        H = d["high"].to_numpy(np.float64)
        L = d["low"].to_numpy(np.float64)
        C = d["close"].to_numpy(np.float64)
        V = d["volume"].to_numpy(np.float64)
        n = len(d)

        pcs = np.concatenate([[H[0]], C[:-1]])
        tr = np.maximum(H - L, np.maximum(np.abs(H - pcs), np.abs(L - pcs)))
        tr_s = pd.Series(tr)
        vu = tr_s.rolling(70, min_periods=20).mean().to_numpy()

        # ---- pre-compute confirmed levels with the bar index they become known at
        swings_hi, swings_lo = [], []
        for j in range(K, n - K):
            if H[j] == H[j - K:j + K + 1].max():
                swings_hi.append((j + K, H[j]))     # confirmed only at j+K
            if L[j] == L[j - K:j + K + 1].min():
                swings_lo.append((j + K, L[j]))
        s_hi_ka = np.array([b for b, _ in swings_hi], dtype=np.int64)
        s_hi_p = np.array([p for _, p in swings_hi], dtype=np.float64)
        s_lo_ka = np.array([b for b, _ in swings_lo], dtype=np.int64)
        s_lo_p = np.array([p for _, p in swings_lo], dtype=np.float64)

        fvgs = []       # (known_at, low, high, dir)  dir=+1 bullish gap (support below)
        for i in range(1, n - 1):
            if L[i + 1] > H[i - 1]:
                fvgs.append((i + 1, H[i - 1], L[i + 1], +1))
            if H[i + 1] < L[i - 1]:
                fvgs.append((i + 1, H[i + 1], L[i - 1], -1))
        if fvgs:
            f_ka = np.array([b for b, *_ in fvgs], dtype=np.int64)
            f_lo = np.array([l for _, l, _, _ in fvgs], dtype=np.float64)
            f_hi = np.array([h for _, _, h, _ in fvgs], dtype=np.float64)
            f_d = np.array([d for *_, d in fvgs], dtype=np.int8)
        else:
            f_ka = f_lo = f_hi = np.zeros(0)
            f_d = np.zeros(0, dtype=np.int8)

        for t in range(n):
            time_left = n - t
            if time_left < MIN_TIME_LEFT or t % SUBSAMPLE:
                continue
            if not np.isfinite(vu[t]) or vu[t] <= 0:
                continue
            E = C[t]

            cands = {}
            # nearest unswept swing high / low confirmed by now, on each side of price
            m = (s_hi_ka <= t) & (s_hi_p > E + TICK)
            if m.any():
                cands["SWINGH"] = float(s_hi_p[m].min())
            m = (s_lo_ka <= t) & (s_lo_p < E - TICK)
            if m.any():
                cands["SWINGL"] = float(s_lo_p[m].max())
            # nearest unfilled FVG on each side (bullish gap acts as downside draw)
            m = (f_ka <= t) & (f_d == +1) & (f_hi < E - TICK)
            if m.any():
                cands["FVGDN"] = float(f_hi[m].max())
            m = (f_ka <= t) & (f_d == -1) & (f_lo > E + TICK)
            if m.any():
                cands["FVGUP"] = float(f_lo[m].min())

            if not cands:
                continue

            fut_h, fut_l = H[t + 1:], L[t + 1:]
            cmh = np.maximum.accumulate(fut_h)
            cml = -np.minimum.accumulate(fut_l)

            for direction in (1, -1):
                for nm, T in cands.items():
                    if direction == 1 and not T > E + TICK:
                        continue
                    if direction == -1 and not T < E - TICK:
                        continue
                    dist = abs(T - E)
                    if dist > MAX_DIST or dist < 2 * TICK:
                        continue
                    idxT = (np.searchsorted(cmh, T, "left") if direction == 1
                            else np.searchsorted(cml, -T, "left"))
                    idxT = min(idxT, len(fut_h))
                    for sp in STOP_GRID:
                        S = E - direction * sp
                        idxS = (min(np.searchsorted(cml, -S, "left"), len(fut_h))
                                if direction == 1
                                else min(np.searchsorted(cmh, S, "left"), len(fut_h)))
                        rows.append(dict(
                            td=str(td), type=nm, direction=direction, stop=sp,
                            y=int(idxT < idxS), dist_pts=dist, dist_atr=dist / vu[t],
                            rr=dist / sp, time_left=time_left, time_in=t,
                        ))

    df = pd.DataFrame(rows)
    df.to_parquet(f"{OUT}/design_pd.parquet", index=False)
    print(f"\nPD-array design rows: {df.shape}")
    print("\nPoT by type and stop:")
    print(df.groupby(["type", "stop"])["y"].agg(["size", "mean"]).to_string())

    df["dist_bucket"] = pd.cut(df["dist_pts"], [0, 20, 40, 60, 100, 150, 250],
                               labels=["<20", "20-40", "40-60", "60-100", "100-150", "150-250"])
    df["time_bucket"] = pd.cut(df["time_in"], [-1, 90, 210, 330],
                               labels=["first_90m", "90-210m", "last_90m"])
    piv = df.groupby(["type", "dist_bucket", "time_bucket", "stop"], observed=True).agg(
        pot=("y", "mean"), n=("y", "size")).reset_index()
    piv = piv[piv["n"] >= 200]
    piv.to_csv(f"{OUT}/pot_surface_pd.csv", index=False)
    print(f"wrote {len(piv)} PD-array cells -> {OUT}/pot_surface_pd.csv")


if __name__ == "__main__":
    main()
