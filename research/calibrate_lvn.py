"""
Calibrate the LVN definition against the user's hand-marked zones.

The user marked 5 LVN bands on 2026-09-23 from that session's own profile:
    30645.25-30659.50
    30827.25-30843.75
    30882.50-30905.25
    30972.00-30990.25
    31060.25-31091.00   <- unreproducible: 47 pts above the RTH high (ETH-derived)

We score candidate detection rules by how well they reproduce the other four,
measured as precision / recall / F1 over the price levels in the span the user
actually marked (30,645.25 .. 30,990.25).

Rules tested:
  A. global quantile   : level volume <= q x (that session's per-level distribution)
  B. relative to POC   : level volume <= f x POC volume
  C. local pinch       : level volume <= f x (median volume of a +/-W point window)
"""
import numpy as np
import pandas as pd

FP = '/tmp/newfp/full_fp.parquet'
DATE = '2026-09-23'

USER_ZONES = [(30645.25, 30659.50), (30827.25, 30843.75),
              (30882.50, 30905.25), (30972.00, 30990.25)]
Z5 = (31060.25, 31091.00)


def session_profile(date_str):
    fp = pd.read_parquet(FP)
    fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
    fp['date'] = fp.dt.dt.date
    fp['volume'] = fp.bid_volume + fp.ask_volume
    fp = fp[(fp.volume > 0) & (fp.date == pd.Timestamp(date_str).date())]
    return fp.groupby('price').volume.sum().sort_index()


def bands_from_mask(prices, mask, min_height=0.0):
    out, start, prev = [], None, None
    for p, m in zip(prices, mask):
        if m and start is None:
            start = p
        elif not m and start is not None:
            if prev - start >= min_height:
                out.append((start, prev))
            start = None
        prev = p
    if start is not None and prev - start >= min_height:
        out.append((start, prev))
    return out


def mask_from_bands(prices, bands):
    m = np.zeros(len(prices), bool)
    for lo, hi in bands:
        m |= (prices >= lo) & (prices <= hi)
    return m


def score(v, bands):
    """Precision/recall/F1 over the span the user marked."""
    lo_all = min(z[0] for z in USER_ZONES)
    hi_all = max(z[1] for z in USER_ZONES)
    sel = (v.index.values >= lo_all) & (v.index.values <= hi_all)
    prices = v.index.values[sel]

    mine = mask_from_bands(prices, bands)
    theirs = mask_from_bands(prices, USER_ZONES)

    tp = (mine & theirs).sum()
    prec = tp / mine.sum() if mine.sum() else 0.0
    rec = tp / theirs.sum() if theirs.sum() else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1, len(prices), int(mine.sum())


def main():
    v = session_profile(DATE)
    prices = v.index.values
    print(f"{DATE} profile: {len(v)} levels, {v.index.min():,.2f} .. {v.index.max():,.2f}")
    print(f"POC {v.idxmax():,.2f} at {v.max():,.0f}   median level {v.median():,.0f}")
    print(f"\nuser zones span {min(z[0] for z in USER_ZONES):,.2f} .. "
          f"{max(z[1] for z in USER_ZONES):,.2f}")
    print(f"z5 {Z5[0]:,.2f}..{Z5[1]:,.2f} excluded (ETH-derived, above RTH high)\n")

    results = []

    print('=== A. global quantile threshold ===')
    print(f"{'q':>6} | {'bands':>6} | {'prec':>7} | {'recall':>7} | {'F1':>7}")
    for q in [0.15, 0.20, 0.25, 0.30, 0.33, 0.35, 0.40, 0.45, 0.50, 0.60]:
        bands = bands_from_mask(prices, v.values <= v.quantile(q))
        prec, rec, f1, n, nb = score(v, bands)
        print(f"{q:>6.2f} | {len(bands):>6} | {prec:>7.1%} | {rec:>7.1%} | {f1:>7.3f}")
        results.append(('global_q', q, None, prec, rec, f1, len(bands)))

    print('\n=== B. threshold relative to POC volume ===')
    print(f"{'f':>6} | {'bands':>6} | {'prec':>7} | {'recall':>7} | {'F1':>7}")
    for f in [0.02, 0.05, 0.10, 0.15, 0.20, 0.30]:
        bands = bands_from_mask(prices, v.values <= f * v.max())
        prec, rec, f1, n, nb = score(v, bands)
        print(f"{f:>6.2f} | {len(bands):>6} | {prec:>7.1%} | {rec:>7.1%} | {f1:>7.3f}")
        results.append(('poc_frac', f, None, prec, rec, f1, len(bands)))

    print('\n=== C. local pinch: level <= f x median of a +/-W point window ===')
    print(f"{'W':>5} {'f':>5} | {'bands':>6} | {'prec':>7} | {'recall':>7} | {'F1':>7}")
    for W in [10, 20, 30, 50]:
        med = pd.Series(v.values).rolling(2 * int(W / 0.25) + 1, center=True,
                                          min_periods=int(W / 0.25)).median().values
        for f in [0.3, 0.5, 0.7, 0.9]:
            mask = v.values <= f * med
            mask &= ~np.isnan(med)
            bands = bands_from_mask(prices, mask)
            prec, rec, f1, n, nb = score(v, bands)
            print(f"{W:>5} {f:>5.1f} | {len(bands):>6} | {prec:>7.1%} | {rec:>7.1%} | {f1:>7.3f}")
            results.append(('local', f, W, prec, rec, f1, len(bands)))

    df = pd.DataFrame(results, columns=['rule', 'param', 'window', 'prec', 'recall', 'f1', 'n_bands'])
    df = df.sort_values('f1', ascending=False)
    print('\n=== best definitions by F1 ===')
    print(df.head(12).to_string(index=False, float_format=lambda x: f'{x:.3f}'))
    df.to_csv('results/lvn_calibration.csv', index=False)
    print('\nwrote results/lvn_calibration.csv')


if __name__ == '__main__':
    main()
