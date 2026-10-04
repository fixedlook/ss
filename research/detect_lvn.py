"""
Detect LVN bands on a given session using the calibrated definition:

    LVN  =  level volume <= 0.7 x median volume of a +/-50 point window
    merge contiguous qualifying levels into one band
    drop bands shorter than MIN_HEIGHT points

Also renders the profile with the detected bands so the boundaries can be
eyeballed against a chart.
"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FP = '/tmp/newfp/full_fp.parquet'
W_PTS = 50.0
F = 0.7
MIN_HEIGHT = 4.0
TICK = 0.25


def profile(date_str):
    fp = pd.read_parquet(FP)
    fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
    fp['date'] = fp.dt.dt.date
    fp['volume'] = fp.bid_volume + fp.ask_volume
    fp = fp[(fp.volume > 0) & (fp.date == pd.Timestamp(date_str).date())]
    return fp.groupby('price').volume.sum().sort_index()


def detect(v, f=F, w=W_PTS, min_height=MIN_HEIGHT):
    win = int(round(w / TICK / 2)) * 2 + 1
    med = pd.Series(v.values).rolling(win, center=True, min_periods=win // 3).median().values
    ratio = v.values / med                       # <1 => thinner than surroundings
    mask = (ratio <= f) & ~np.isnan(med)

    bands, start, prev = [], None, None
    for p, m in zip(v.index.values, mask):
        if m and start is None:
            start = p
        elif not m and start is not None:
            if prev - start >= min_height:
                bands.append((start, prev))
            start = None
        prev = p
    if start is not None and prev - start >= min_height:
        bands.append((start, prev))
    return sorted(bands), ratio


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else '2026-04-14'
    v = profile(date_str)
    bands, ratio = detect(v)
    rng = v.index.max() - v.index.min()

    print(f"session {date_str}   range {v.index.min():,.2f} .. {v.index.max():,.2f} "
          f"({rng:.2f} pts)   POC {v.idxmax():,.2f}")
    print(f"definition: volume <= {F:.1f} x median of +/-{W_PTS:.0f}pt window,  "
          f"min band height {MIN_HEIGHT:.2f} pts\n")
    print(f"{len(bands)} LVN bands detected:\n")
    print(f"{'#':>3}  {'low':>10}  {'high':>10}  {'height':>7}  {'% of range':>10}")
    for i, (lo, hi) in enumerate(bands, 1):
        print(f"{i:>3}  {lo:>10,.2f}  {hi:>10,.2f}  {hi-lo:>7.2f}  {(hi-lo)/rng:>9.1%}")
    tot = sum(h - l for l, h in bands)
    print(f"\ntotal zone coverage: {tot:.1f} pts = {tot/rng:.1%} of the session range")

    fig, ax = plt.subplots(figsize=(9, 15))
    ax.barh(v.index.values, v.values / v.max(), height=0.25, color='black', align='center')
    for lo, hi in bands:
        ax.axhspan(lo, hi, color='#1f77b4', alpha=0.30, zorder=0)
    ax.axhline(v.idxmax(), color='red', ls='--', lw=1.2)
    ax.text(0.02, v.idxmax(), ' POC', color='red', fontsize=11, va='bottom')
    for lo, hi in bands:
        ax.text(1.01, (lo + hi) / 2, f'{lo:,.2f} – {hi:,.2f}', fontsize=8.5,
                va='center', color='#1f4e79')
    ax.set_title(f'{date_str} MNQ volume profile — detected LVN bands (blue)\n'
                 f'volume <= {F:.1f}x median of +/-{W_PTS:.0f}pt window, min height {MIN_HEIGHT:.0f}pts',
                 fontsize=13)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel('volume (relative to POC)')
    ax.grid(alpha=0.2, axis='y')
    fig.tight_layout(rect=[0, 0, 0.93, 1])
    out = f'results/lvn_{date_str.replace("-","")}.png'
    fig.savefig(out, dpi=100)
    print(f'\nwrote {out}')


if __name__ == '__main__':
    main()
