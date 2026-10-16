"""
12_final_table.py — the table the indicator actually ships with.

Strict "unswept" definitions for every level family (see 11_strict_unswept.py):
a level only counts if price has never traded through it since it formed.

Dimensions, chosen so the C# side can index it with plain integers:
    group (5) x stop (20/40/60) x rr_bucket (8) x time_bucket (3)

Outputs:
    pot_table.csv         human-readable
    pot_table.json        compact, consumed by the C# generator
    pot_table_cs.txt      C# array literal ready to paste
"""
import json

import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
TICK = 0.25
STOP_GRID = (20, 40, 60)
SUBSAMPLE = 3
MIN_TIME_LEFT = 30
MAX_DIST = 300.0
K = 3
IB_MINUTES = 60      # initial balance window, in minutes

GROUPS = ["session_hl", "sess_extreme", "pdc", "swing", "fvg"]
RR_BINS = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 100]
RR_LBL = ["<0.5", "0.5-1", "1-1.5", "1.5-2", "2-3", "3-4", "4-6", "6+"]
T_LBL = ["first_90m", "mid", "last_90m"]

LEVEL_COLS = {
    "PDH": ("pdh", "session_hl"), "PDL": ("pdl", "session_hl"),
    "ONH": ("on_high", "session_hl"), "ONL": ("on_low", "session_hl"),
    "ASIAH": ("asia_high", "session_hl"), "ASIAL": ("asia_low", "session_hl"),
    "LONH": ("lon_high", "session_hl"), "LONL": ("lon_low", "session_hl"),
    "IBH": ("ib_high", "session_hl"), "IBL": ("ib_low", "session_hl"),
    "PDC": ("pdc", "pdc"),
}
GROUP = {k: v[1] for k, v in LEVEL_COLS.items()}
GROUP.update({"SESSH": "sess_extreme", "SESSL": "sess_extreme",
              "SWINGH": "swing", "SWINGL": "swing",
              "FVGUP": "fvg", "FVGDN": "fvg"})


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    lv = pd.read_parquet(f"{OUT}/levels.parquet").set_index("td")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    tds = [t for t in sorted(rth["td"].unique()) if t in lv.index]
    print(f"{len(tds)} trading days")

    recs = []
    for di, td in enumerate(tds):
        if di % 300 == 0:
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
        static = {k: float(row[c]) for k, (c, _) in LEVEL_COLS.items()
                  if pd.notna(row.get(c)) and not k.startswith("IB")}
        static_ib = {k: float(row[c]) for k, (c, _) in LEVEL_COLS.items()
                     if k.startswith("IB") and pd.notna(row.get(c))}
        rh = np.maximum.accumulate(H)
        rl = np.minimum.accumulate(L)
        # session extreme BEFORE the current bar: strictly never exceeded by
        # construction, so it is inherently "untouched"
        rh_prev = np.concatenate([[H[0]], rh[:-1]])
        rl_prev = np.concatenate([[L[0]], rl[:-1]])

        sw_hi = [(j + K, H[j], j) for j in range(K, n - K)
                 if H[j] == H[j - K:j + K + 1].max()]
        sw_lo = [(j + K, L[j], j) for j in range(K, n - K)
                 if L[j] == L[j - K:j + K + 1].min()]
        fv = [(i + 1, H[i - 1], L[i + 1], +1, i) for i in range(1, n - 1)
              if L[i + 1] > H[i - 1]]
        fv += [(i + 1, H[i + 1], L[i - 1], -1, i) for i in range(1, n - 1)
               if H[i + 1] < L[i - 1]]

        for t in range(n):
            if n - t < MIN_TIME_LEFT or t % SUBSAMPLE:
                continue
            E = C[t]
            succ_h, succ_l = H[t + 1:], L[t + 1:]
            if len(succ_h) == 0:
                continue
            cmh = np.maximum.accumulate(succ_h)
            cml = -np.minimum.accumulate(succ_l)
            tb = 0 if t < 90 else (1 if t < 210 else 2)

            cands = {}
            for nm, T in static.items():
                if T > E + TICK and T > rh[t]:
                    cands[nm] = T
                elif T < E - TICK and T < rl[t]:
                    cands[nm] = T

            # The initial balance is only a *known* level once its window has
            # closed. Admitting it earlier leaks the rest of the window into the
            # decision and inflates the measured hit rate.
            if t >= IB_MINUTES:
                for nm, T in static_ib.items():
                    if T > E + TICK and T > rh[t]:
                        cands[nm] = T
                    elif T < E - TICK and T < rl[t]:
                        cands[nm] = T
            if rh_prev[t] > E + TICK:
                cands["SESSH"] = float(rh_prev[t])
            if rl_prev[t] < E - TICK:
                cands["SESSL"] = float(rl_prev[t])
            b = None
            for ka, p, j in sw_hi:
                if ka <= t and p > E + TICK and p > H[j + 1:t + 1].max(initial=-np.inf):
                    if b is None or p < b:
                        b = p
            if b is not None:
                cands["SWINGH"] = b
            b = None
            for ka, p, j in sw_lo:
                if ka <= t and p < E - TICK and p < L[j + 1:t + 1].min(initial=np.inf):
                    if b is None or p > b:
                        b = p
            if b is not None:
                cands["SWINGL"] = b
            b = None
            for ka, lo_, hi_, dr, i0 in fv:
                if dr == +1 and ka <= t and hi_ < E - TICK \
                        and L[i0 + 2:t + 1].min(initial=np.inf) > hi_:
                    if b is None or hi_ > b:
                        b = hi_
            if b is not None:
                cands["FVGDN"] = b
            b = None
            for ka, lo_, hi_, dr, i0 in fv:
                if dr == -1 and ka <= t and lo_ > E + TICK \
                        and H[i0 + 2:t + 1].max(initial=-np.inf) < lo_:
                    if b is None or lo_ < b:
                        b = lo_
            if b is not None:
                cands["FVGUP"] = b

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
                        recs.append((GROUP[nm], sp, tb, dist / sp, int(idxT < idxS)))

    df = pd.DataFrame(recs, columns=["grp", "stop", "tb", "rr", "y"])
    df.to_parquet(f"{OUT}/pot_strict_final.parquet", index=False)
    print(f"raw rows: {len(df):,}")

    df["rr_b"] = pd.cut(df["rr"], RR_BINS, labels=False)
    g = df.groupby(["grp", "stop", "rr_b", "tb"]).agg(pot=("y", "mean"), n=("y", "size"))
    tab = g.reset_index()
    tab["rr_label"] = tab["rr_b"].map(dict(enumerate(RR_LBL)))
    tab["time_label"] = tab["tb"].map(dict(enumerate(T_LBL)))
    tab.to_csv(f"{OUT}/pot_table.csv", index=False)

    MIN_N = 120
    ok = tab[tab["n"] >= MIN_N]
    full = tab[(tab["n"] < MIN_N) & (tab["n"] > 0)]
    print(f"cells with n>={MIN_N}: {len(ok)}   thin/empty cells: {len(full)} of {len(tab)}")

    # ---- fallback: pooled across time, then across rr, then grand mean ----
    fb_time = df.groupby(["grp", "stop", "rr_b"])["y"].mean().to_dict()
    fb_rr = df.groupby(["grp", "stop"])["y"].mean().to_dict()
    fb_all = df.groupby("grp")["y"].mean().to_dict()

    table = {}
    for _, r in tab.iterrows():
        key = f"{r.grp}|{int(r.stop)}|{int(r.rr_b)}|{int(r.tb)}"
        if r.n >= MIN_N:
            table[key] = round(float(r.pot), 4)
        elif (r.grp, r.stop, r.rr_b) in fb_time:
            table[key] = round(float(fb_time[(r.grp, r.stop, r.rr_b)]), 4)
    # ensure every cell exists via coarser fallback
    for gi, grp in enumerate(GROUPS):
        for sp in STOP_GRID:
            for rb in range(len(RR_LBL)):
                for tb in range(3):
                    key = f"{grp}|{sp}|{rb}|{tb}"
                    if key in table:
                        continue
                    if (grp, sp) in fb_rr:
                        table[key] = round(float(fb_rr[(grp, sp)]), 4)
                    elif grp in fb_all:
                        table[key] = round(float(fb_all[grp]), 4)

    with open(f"{OUT}/pot_table.json", "w") as f:
        json.dump({"groups": GROUPS, "rr_labels": RR_LBL, "time_labels": T_LBL,
                   "stops": list(STOP_GRID), "min_n": MIN_N, "table": table}, f, indent=1)

    # ---- C# literal: float[group, stopIdx, rrIdx, timeIdx] ----
    lines = ["public static readonly float[,,,] Pot = new float[,,,]",
             "{"]
    for gi, grp in enumerate(GROUPS):
        lines.append(f"\t{{ // {grp}")
        for si, sp in enumerate(STOP_GRID):
            lines.append(f"\t\t{{ // stop {sp}")
            for rb in range(len(RR_LBL)):
                vals = ", ".join(
                    f"{table.get(f'{grp}|{sp}|{rb}|{tb}', 0.25):.4f}f" for tb in range(3))
                lines.append(f"\t\t\t{{ {vals} }}, // rr {RR_LBL[rb]}")
            lines.append("\t\t},")
        lines.append("\t},")
    lines += ["};"]
    with open(f"{OUT}/pot_table_cs.txt", "w") as f:
        f.write("\n".join(lines))

    print("\n--- final table, 40pt stop, mid session (rr x group) ---")
    p = tab[(tab["stop"] == 40) & (tab["tb"] == 1)]
    piv = p.pivot(index="rr_b", columns="grp", values="pot") * 100
    print(piv[GROUPS].round(1).to_string())
    print("\n--- sample counts ---")
    print(p.pivot(index="rr_b", columns="grp", values="n")[GROUPS].fillna(0).astype(int).to_string())
    print(f"\nwrote pot_table.csv / .json / _cs.txt to {OUT}")


if __name__ == "__main__":
    main()
