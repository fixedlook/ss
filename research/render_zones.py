"""
Render candidate LVN / HVN bands for a session profile so a human can pick the
threshold that matches what they would draw by eye.

Usage:  python research/render_zones.py 2026-09-22
Writes: results/zones_<date>.png  (4 candidate thresholds side by side)
        results/zone_levels_<date>.csv  (the band boundaries at each threshold)
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FP = '/tmp/newfp/full_fp.parquet'


def profile_for(date_str: str) -> pd.Series:
    fp = pd.read_parquet(FP)
    fp['dt'] = pd.to_datetime(fp.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
    fp['date'] = fp.dt.dt.date
    fp['volume'] = fp.bid_volume + fp.ask_volume
    fp = fp[(fp.volume > 0) & (fp.date == pd.Timestamp(date_str).date())]
    return fp.groupby('price').volume.sum().sort_index()


def bands_from_mask(prices: np.ndarray, mask: np.ndarray):
    """Contiguous runs of True -> list of (low, high) price bands."""
    out, start = [], None
    for p, m in zip(prices, mask):
        if m and start is None:
            start = p
        elif not m and start is not None:
            out.append((start, prev))
            start = None
        prev = p
    if start is not None:
        out.append((start, prev))
    return out


def lvn_bands(v: pd.Series, frac: float, min_height_pts: float):
    """Levels whose volume is below `frac` x the profile median."""
    mask = (v.values < frac * v.median())
    return [(lo, hi) for lo, hi in bands_from_mask(v.index.values, mask)
            if hi - lo >= min_height_pts]


def hvn_bands(v: pd.Series, frac: float, min_height_pts: float):
    """Levels whose volume is above `frac` x the profile median."""
    mask = (v.values > frac * v.median())
    return [(lo, hi) for lo, hi in bands_from_mask(v.index.values, mask)
            if hi - lo >= min_height_pts]


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else '2026-09-22'
    v = profile_for(date_str)
    grid = v.index.values
    med = v.median()

    thresholds = [0.10, 0.20, 0.30, 0.45]
    fig, axes = plt.subplots(1, len(thresholds), figsize=(6 * len(thresholds), 13), sharey=True)

    rows = []
    for ax, frac in zip(axes, thresholds):
        widths = v.values / v.max() * 1.0
        ax.barh(grid, widths, height=0.25, color='black', align='center')

        lv = lvn_bands(v, frac, 2.0)
        hv = hvn_bands(v, 1.0 / frac if frac else 2.0, 2.0)

        for lo, hi in lv:
            ax.axhspan(lo, hi, color='#1f77b4', alpha=0.25, zorder=0)
        for lo, hi in hv:
            ax.axhspan(lo, hi, color='#2ca02c', alpha=0.18, zorder=0)
            rows.append(dict(threshold=frac, kind='HVN', low=lo, high=hi, height=hi - lo))
        for lo, hi in lv:
            rows.append(dict(threshold=frac, kind='LVN', low=lo, high=hi, height=hi - lo))

        ax.set_title(f'LVN < {frac:.0%} of median level\n'
                     f'{len(lv)} LVN bands / {len(hv)} HVN bands', fontsize=13)
        ax.set_xlim(0, 1.05)
        ax.grid(alpha=0.2, axis='y')
        ax.set_xlabel('volume (relative)')

    ax = axes[0]
    ax.axhline(v.idxmax(), color='red', lw=1.2, ls='--')
    ax.text(0.02, v.idxmax(), ' POC', color='red', fontsize=11, va='bottom')
    fig.suptitle(f'{date_str} MNQ session volume profile — candidate zones '
                 f'(blue = LVN, green = HVN). Median level volume = {med:,.0f}',
                 fontsize=15, y=0.995)

    os.makedirs('results', exist_ok=True)
    out = f'results/zones_{date_str.replace("-","")}.png'
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out, dpi=110)
    print('wrote', out)

    df = pd.DataFrame(rows)
    df.to_csv(f'results/zone_levels_{date_str.replace("-","")}.csv', index=False)
    print(f'\nbands per threshold for {date_str}:')
    print(df.groupby(['threshold', 'kind']).size().to_string())
    print(f'\nPOC {v.idxmax():,.2f}   median level volume {med:,.0f}   '
          f'range {v.index.min():,.2f}..{v.index.max():,.2f}')


if __name__ == '__main__':
    main()
