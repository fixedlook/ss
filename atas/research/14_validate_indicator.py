"""
14_validate_indicator.py — end-to-end validation of the SHIPPED logic.

The earlier scripts validated the *research* pipeline. This one re-implements the
indicator's own decision path, step for step, and replays it over the full
history:

    level detection  ->  strict unswept filter  ->  direction resolution
    ->  nearest level clearing MinRr  ->  PoT lookup from pot_table.json

Then it checks the thing that actually matters to a trader: when the indicator
says "PoT 44%", does the target get hit about 44% of the time?

This is a faithful port, not a re-derivation. If the C# and this diverge, the
C# is what ships and this is what caught it.
"""
import json
import numpy as np
import pandas as pd

OUT = "/tmp/mnq/derived"
TICK = 0.25
STOP = 40.0
MIN_RR = 1.5
K = 3
IB_MINUTES = 60
SUBSAMPLE = 15          # every 15th minute is plenty for calibration checking

art = json.load(open(f"{OUT}/pot_table.json"))
TBL = art["table"]
GROUPS = art["groups"]
RR_EDGES = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]


def group_index(g):
    return GROUPS.index(g)


def stop_index(sp):
    stops = [20, 40, 60]
    return min(range(3), key=lambda i: abs(stops[i] - sp))


def rr_index(rr):
    for i, e in enumerate(RR_EDGES):
        if rr < e:
            return i
    return len(RR_EDGES)


def pot(group, rr, tb):
    return TBL[f"{group}|{int(STOP)}|{rr_index(rr)}|{tb}"]


def main():
    bars = pd.read_parquet(f"{OUT}/bars.parquet")
    lv = pd.read_parquet(f"{OUT}/levels.parquet").set_index("td")
    rth = bars[bars["is_rth"]].sort_values("timestamp")
    tds = [t for t in sorted(rth["td"].unique()) if t in lv.index]

    recs = []
    for td in tds:
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
        O = d["open"].to_numpy(np.float64)
        rth_open = O[0]

        run_high = np.maximum.accumulate(H)
        run_low = np.minimum.accumulate(L)
        rh_prev = np.concatenate([[H[0]], run_high[:-1]])
        rl_prev = np.concatenate([[L[0]], run_low[:-1]])

        # ---- levels exactly as the indicator builds them ----
        static_up = {}
        static_dn = {}
        for name, col in [("PDH", "pdh"), ("ONH", "on_high"), ("AsiaH", "asia_high"),
                          ("LondonH", "lon_high")]:
            v = row.get(col)
            if pd.notna(v):
                static_up[name] = float(v)
        for name, col in [("PDL", "pdl"), ("ONL", "on_low"), ("AsiaL", "asia_low"),
                          ("LondonL", "lon_low")]:
            v = row.get(col)
            if pd.notna(v):
                static_dn[name] = float(v)
        # initial balance: only usable once its window has closed
        ib_up = float(row["ib_high"]) if pd.notna(row.get("ib_high")) else None
        ib_dn = float(row["ib_low"]) if pd.notna(row.get("ib_low")) else None
        pdc = float(row["pdc"]) if pd.notna(row.get("pdc")) else None

        # swings / gaps, causal
        sw_hi = [(j + K, H[j]) for j in range(K, n - K) if H[j] == H[j - K:j + K + 1].max()]
        sw_lo = [(j + K, L[j]) for j in range(K, n - K) if L[j] == L[j - K:j + K + 1].min()]
        fv = [(i + 1, H[i - 1], L[i + 1], +1) for i in range(1, n - 1) if L[i + 1] > H[i - 1]]
        fv += [(i + 1, H[i + 1], L[i - 1], -1) for i in range(1, n - 1) if H[i + 1] < L[i - 1]]

        for t in range(3, n):
            if t % SUBSAMPLE:
                continue
            e = C[t]
            hi_so_far, lo_so_far = run_high[t], run_low[t]

            dirn = 1 if (rth_open > 0 and e >= rth_open) else -1
            tb = 0 if t < 90 else (1 if t < 210 else 2)

            cands = []

            def add(name, group, price, upper):
                if price is None or not np.isfinite(price) or price <= 0:
                    return
                if upper and price > hi_so_far:
                    cands.append((name, group, float(price)))
                elif not upper and price < lo_so_far:
                    cands.append((name, group, float(price)))

            for nm, v in static_up.items():
                add(nm, "session_hl", v, True)
            for nm, v in static_dn.items():
                add(nm, "session_hl", v, False)
            if pdc is not None:
                add("PDC", "pdc", pdc, pdc > hi_so_far)
            if rh_prev[t] > hi_so_far:
                add("SessionH", "sess_extreme", rh_prev[t], True)
            if rl_prev[t] < lo_so_far:
                add("SessionL", "sess_extreme", rl_prev[t], False)

            # a level survives only if no bar AFTER its birth bar touched it,
            # which is exactly the condition the indicator maintains
            for ka, p in sw_hi:
                if ka <= t and H[ka + 1:t + 1].max(initial=-np.inf) < p:
                    cands.append(("SwingH", "swing", float(p)))
            for ka, p in sw_lo:
                if ka <= t and L[ka + 1:t + 1].min(initial=np.inf) > p:
                    cands.append(("SwingL", "swing", float(p)))
            for ka, lo_, hi_, dr in fv:
                if dr == +1 and ka <= t and L[ka + 1:t + 1].min(initial=np.inf) > hi_:
                    cands.append(("FVG", "fvg", float(hi_)))
                if dr == -1 and ka <= t and H[ka + 1:t + 1].max(initial=-np.inf) < lo_:
                    cands.append(("FVG", "fvg", float(lo_)))

            if t >= IB_MINUTES:
                add("IBH", "session_hl", ib_up, True)
                add("IBL", "session_hl", ib_dn, False)

            # ---- nearest that clears the R:R floor ----
            qual = [(nm, g, p) for nm, g, p in cands
                    if (p > e + TICK if dirn > 0 else p < e - TICK)
                    and abs(p - e) / STOP >= MIN_RR]
            if not qual:
                continue
            nm, g, p = min(qual, key=lambda x: abs(x[2] - e))
            dist = abs(p - e)

            # ---- outcome: target before stop (conservative) ----
            fut_h, fut_l = H[t + 1:], L[t + 1:]
            if len(fut_h) == 0:
                continue
            cmh = np.maximum.accumulate(fut_h)
            cml = -np.minimum.accumulate(fut_l)
            if dirn > 0:
                iT = np.searchsorted(cmh, p, "left")
                iS = np.searchsorted(cml, -(e - STOP), "left")
            else:
                iT = np.searchsorted(cml, -p, "left")
                iS = np.searchsorted(cmh, e + STOP, "left")
            hit = int(min(iT, len(fut_h)) < min(iS, len(fut_h)))

            recs.append(dict(td=str(td), level=nm, group=g, dirn=dirn,
                             dist=dist, rr=dist / STOP, tb=tb,
                             pot=pot(g, dist / STOP, tb), hit=hit))

    df = pd.DataFrame(recs)
    df.to_parquet(f"{OUT}/indicator_replay.parquet", index=False)
    print(f"replayed {len(df):,} displayed targets over {df['td'].nunique()} days\n")

    print("=== CALIBRATION: does the displayed PoT match reality? ===")
    df["bucket"] = pd.cut(df["pot"], [0, .2, .3, .4, .5, .6, .7, 1.0])
    agg = df.groupby("bucket", observed=True).agg(shown=("pot", "mean"),
                                                  actual=("hit", "mean"), n=("hit", "size"))
    print("   displayed   realised    n")
    for i, r in agg.iterrows():
        if r.n < 30:
            continue
        flag = "  <-- overstates" if r.actual < r.shown - 0.05 else ""
        print(f"    {r.shown*100:5.1f}%     {r.actual*100:5.1f}%   {int(r.n):,}{flag}")

    print(f"\n  overall: displayed {df['pot'].mean()*100:.1f}%  "
          f"actual {df['hit'].mean()*100:.1f}%   (n={len(df):,})")

    print("\n=== which level type actually gets picked, and how it does ===")
    s = df.groupby("level").agg(picked=("hit", "size"), shown=("pot", "mean"),
                                actual=("hit", "mean"), med_dist=("dist", "median"))
    s = s.sort_values("picked", ascending=False)
    s["shown"] *= 100
    s["actual"] *= 100
    print(s.round(1).to_string())

    print("\n=== per level group ===")
    g2 = df.groupby("group").agg(picked=("hit", "size"), shown=("pot", "mean"),
                                 actual=("hit", "mean"))
    g2["shown"] *= 100
    g2["actual"] *= 100
    print(g2.round(1).to_string())

    print("\n=== split by direction ===")
    d2 = df.groupby("dirn").agg(n=("hit", "size"), shown=("pot", "mean"), actual=("hit", "mean"))
    d2.index = ["short", "long"] if list(d2.index) == [-1, 1] else d2.index
    d2["shown"] *= 100
    d2["actual"] *= 100
    print(d2.round(1).to_string())


if __name__ == "__main__":
    main()
