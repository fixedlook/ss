"""
Rebuild /tmp/newfp/full_fp.parquet from the footprint CSV.

The zip holds a headerless-looking CSV that actually HAS a header row:
    timestamp,price,bid_volume,ask_volume,delta,trades
Timestamps are epoch milliseconds, ET session (RTH only).

/tmp does not survive a sandbox recycle, so this must be re-runnable.
Run:  python research/build_parquet.py
"""
import os
import pandas as pd

SRC = '/tmp/newfp/mnq_bidask.footprint.csv'
DST = '/tmp/newfp/full_fp.parquet'

if not os.path.exists(SRC):
    raise SystemExit(f'{SRC} missing - unzip mnq_bidask.footprint.zip into /tmp/newfp first')

df = pd.read_csv(SRC, header=0)
print(f'rows {len(df):,}')

df['dt_et'] = pd.to_datetime(df.timestamp, unit='ms', utc=True).dt.tz_convert('America/New_York')
df['date'] = df.dt_et.dt.date

# sanity checks that matter for the studies
assert (df.delta == df.ask_volume - df.bid_volume).mean() > 0.999, 'delta mismatch'
mins = df.dt_et.dt.hour * 60 + df.dt_et.dt.minute
print(f'ET minute range {mins.min()}..{mins.max()} (570..1019 = 09:30-16:59 RTH)')
print(f'sessions {df.date.nunique()}  {df.date.min()} .. {df.date.max()}')
print(f'price range {df.price.min():,.2f} .. {df.price.max():,.2f}')

df.to_parquet(DST, index=False)
print('wrote', DST)
