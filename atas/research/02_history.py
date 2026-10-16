"""
02_history.py — profile the long 1-min history files.

Purpose: the footprint file is RTH-only and only 164 days. The 1-min history
(mnq_flow.csv.gz, MNQZ26 RTH.csv) is ~7 years and is what we fit *structural*
base rates on (e.g. how often PDH gets taken). Need to know:
  - what timezone / session window the timestamps cover
  - from when bid/ask (delta) data is actually populated
"""
import gzip
import numpy as np
import pandas as pd

def profile(name, path, gz=False):
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    op = gzip.open if gz else open
    with op(path, "rt") as f:
        hdr = f.readline().strip()
    print("header:", hdr)
    df = pd.read_csv(op(path, "rt"), header=0, on_bad_lines="skip")
    print(f"rows={len(df):,}  (malformed lines skipped)")
    c0 = df.columns[0]
    t = pd.to_datetime(df[c0].astype("int64"), unit="ms", utc=True)
    print(f"range: {t.min()} .. {t.max()} UTC   ({t.dt.date.nunique()} distinct days)")
    hh = t.dt.hour
    print("\nrows per UTC hour:")
    vc = hh.value_counts().sort_index()
    mx = vc.max()
    for h, c in vc.items():
        print(f"   {h:02d}Z  {c:>9,} {'#'*int(c/mx*40)}")
    q = t.dt.minute.value_counts()
    print("\nfirst minute-of-day histogram (%d days):" % t.dt.date.nunique())
    firsts = t.groupby(t.dt.date).min().dt.strftime("%H:%MZ")
    print(firsts.value_counts().head(4).to_string())
    lasts = t.groupby(t.dt.date).max().dt.strftime("%H:%MZ")
    print("last minute-of-day:")
    print(lasts.value_counts().head(4).to_string())
    # when does bid/ask data become non-zero?
    if "bid_volume" in df.columns:
        nz = df[df["bid_volume"] + df["ask_volume"] > 0]
        print(f"\nrows with bid/ask>0: {len(nz):,}")
        if len(nz):
            print(f"   first such timestamp: {t.loc[nz.index[0]]}")
            print(f"   last  such timestamp: {t.loc[nz.index[-1]]}")
    return df

profile("mnq_flow.csv.gz (long 1-min history)", "/home/user/ss/mnq_flow.csv.gz", gz=True)
