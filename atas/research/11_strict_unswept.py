"""
11_strict_unswept.py — the control that decides whether the swing/FVG edge is real.

Weak condition used so far: "level is above current price".
Strict condition used here: "price has never traded through the level since it
was created". These are very different sets, and the weak one admits levels that
price has already tested and rejected — which are far more likely to be retouched,
inflating PoT.

Strict definitions applied consistently to every level family:
  static session level above price : level > running_max(high[0..t])   (never touched today)
  swing high P formed at j         : P > running_max(high[j+1..t])    (never exceeded since forming)
  bullish FVG below price          : min(low[i+2..t]) > top_edge       (zone never entered)
Everything else identical to 10_pot_table.py.
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
GROUP.update({"SWINGH": "swing", "SWINGL": "swing", "FVGUP": "fvg", "FVGDN": "fvg"})
RR_BINS = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 100]
RR_LBL = ["<0.5", "0.5-1", "1-1.5", "1.5-2", "2-3", "3-4", "4-6", "6+"]


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    lv = pd.read_parquet(f"{OUT}/levels.parquet").set_index("td")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    tds = [t for t in sorted(rth["td"].unique()) if t in lv.index]

    weak, strict = [], []
    for di, td in enumerate(tds):
        if di % 300 == 0:
            print(f"  day {di}/{len(tds)}")
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
        static = {k: float(row[c]) for k, (c, _) in LEVEL_COLS.items()
                  if pd.notna(row.get(c))}

        rh = np.maximum.accumulate(H)      # running high INCLUDING current bar
        rl = np.minimum.accumulate(L)

        # swings with the running max/min from their formation bar onward
        sw_hi, sw_lo = [], []
        for j in range(K, n - K):
            if H[j] == H[j - K:j + K + 1].max():
                sw_hi.append((j + K, H[j], j))
            if L[j] == L[j - K:j + K + 1].min():
                sw_lo.append((j + K, L[j], j))
        # FVGs with formation bar
        fv = [(i + 1, H[i - 1], L[i + 1], +1, i) for i in range(1, n - 1)
              if L[i + 1] > H[i - 1]]
        fv += [(i + 1, H[i + 1], L[i - 1], -1, i) for i in range(1, n - 1)
               if H[i + 1] < L[i - 1]]

        for t in range(n):
            if n - t < MIN_TIME_LEFT or t % SUBSAMPLE:
                continue
            E = C[t]
            succ_h = H[t + 1:]
            succ_l = L[t + 1:]
            if len(succ_h) == 0:
                continue
            cmh = np.maximum.accumulate(succ_h)
            cml = -np.minimum.accumulate(succ_l)

            cands_w, cands_s = {}, {}

            # ---- static session levels ----
            for nm, T in static.items():
                if T > E + TICK:
                    cands_w[nm] = T
                    if T > rh[t]:            # never touched today
                        cands_s[nm] = T
                elif T < E - TICK:
                    cands_w[nm] = T
                    if T < rl[t]:
                        cands_s[nm] = T

            # ---- swings ----
            best_w_hi = best_s_hi = None
            for ka, p, j in sw_hi:
                if ka > t or p <= E + TICK:
                    continue
                if best_w_hi is None or p < best_w_hi:
                    best_w_hi = p
                # never exceeded since the swing bar closed
                if p > H[j + 1:t + 1].max(initial=-np.inf):
                    if best_s_hi is None or p < best_s_hi:
                        best_s_hi = p
            if best_w_hi is not None:
                cands_w["SWINGH"] = best_w_hi
            if best_s_hi is not None:
                cands_s["SWINGH"] = best_s_hi

            best_w_lo = best_s_lo = None
            for ka, p, j in sw_lo:
                if ka > t or p >= E - TICK:
                    continue
                if best_w_lo is None or p > best_w_lo:
                    best_w_lo = p
                if p < L[j + 1:t + 1].min(initial=np.inf):
                    if best_s_lo is None or p > best_s_lo:
                        best_s_lo = p
            if best_w_lo is not None:
                cands_w["SWINGL"] = best_w_lo
            if best_s_lo is not None:
                cands_s["SWINGL"] = best_s_lo

            # ---- fair value gaps: near edge = first point of contact ----
            bw, bs = None, None
            for ka, lo_, hi_, dr, i0 in fv:
                if dr != +1 or ka > t or hi_ >= E - TICK:
                    continue
                filled = L[i0 + 2:t + 1].min(initial=np.inf) <= hi_
                if not filled:
                    if bw is None or hi_ > bw:
                        bw = hi_
                    if bs is None or hi_ > bs:
                        bs = hi_
            if bw is not None:
                cands_w["FVGDN"] = bw
            if bs is not None:
                cands_s["FVGDN"] = bs
            bw, bs = None, None
            for ka, lo_, hi_, dr, i0 in fv:
                if dr != -1 or ka > t or lo_ <= E + TICK:
                    continue
                filled = H[i0 + 2:t + 1].max(initial=-np.inf) >= lo_
                if not filled:
                    if bw is None or lo_ < bw:
                        bw = lo_
                    if bs is None or lo_ < bs:
                        bs = lo_
            if bw is not None:
                cands_w["FVGUP"] = bw
            if bs is not None:
                cands_s["FVGUP"] = bs

            for tag, cands, sink in (("weak", cands_w, weak), ("strict", cands_s, strict)):
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
                                   len(succ_h))
                        for sp in STOP_GRID:
                            S = E - direction * sp
                            idxS = min(np.searchsorted(cml if direction == 1 else cmh,
                                                       -S if direction == 1 else S, "left"),
                                       len(succ_h))
                            sink.append((GROUP[nm], sp, dist / sp, int(idxT < idxS), tag))

    for tag, sink in (("weak", weak), ("strict", strict)):
        df = pd.DataFrame(sink, columns=["grp", "stop", "rr", "y", "tag"])
        df["rr_b"] = pd.cut(df["rr"], RR_BINS, labels=RR_LBL)
        sub = df[df["stop"] == 40]
        piv = sub.groupby(["rr_b", "grp"], observed=True)["y"].agg(["mean", "size"]).reset_index()
        piv = piv[piv["size"] >= 150]
        print(f"\n{'='*72}\n{tag.upper()} definition — PoT at 40pt stop, by R:R bucket\n{'='*72}")
        print((piv.pivot(index="rr_b", columns="grp", values="mean") * 100).round(1).to_string())
        print("\nsample sizes:")
        print(piv.pivot(index="rr_b", columns="grp", values="size").fillna(0).astype(int).to_string())
        df.to_parquet(f"{OUT}/pot_raw_{tag}.parquet", index=False)


if __name__ == "__main__":
    main()
