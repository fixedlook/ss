# History Exporter — ATAS / ATAS X

Dumps the bars and the footprint that are **already loaded on the chart** to CSV. One pass, no live
capture, no waiting for the market.

Add the indicator to a chart → it exports automatically → two files appear. Nothing else to do.

## Why this is enough

The platform keeps the whole chart range in memory — it is the data you are already looking at. Walking
the bars and reading each candle's price levels is a single loop, so the export covers exactly what the
chart covers, including the past. Order-by-order data is a different story (see *Limits*), but bar and
footprint history needs no capture at all.

## Output

Everything lands in `Documents\AtasHistoryExport` unless you set another folder.

**`bars.csv`** — one row per bar:

```
ts_utc,ts_local,sym,open,high,low,close,vol,bid_vol,ask_vol,between,delta,max_delta,min_delta,ticks,oi,poc,poc_vol
```

**`footprint.csv`** — one row per bar per price (this is the one that matters for signal work):

```
ts_utc,ts_local,sym,price,bid_vol,ask_vol,delta,ticks
```

`bid_vol` is volume that traded at the bid (a seller hit it), `ask_vol` volume that traded at the ask
(a buyer lifted it), and `delta = ask_vol − bid_vol`. That per-price split is what lets a signal be
checked against what actually happened in the book at that price, which a 1-minute OHLC bar cannot show.

**`_status.csv`** — what was written, and the **real bid/ask coverage percentage**. Read that number
before trusting any delta.

`ts_utc` is derived from your local clock using the time-zone database (so daylight time is correct), and
`ts_local` is that same instant in local time. One of them will line up with your signal times.

## Two things to check after the first run

1. **Coverage % in `_status.csv`.** If it is low, the platform does not have genuine bid/ask for that
   range and the delta column is inferred from price movement rather than real aggressor data. That is
   the difference between evidence and decoration.
2. **Row count vs your chart.** `bars` should match the bars on screen; `levels` should be in the
   millions for a few months of a liquid future.

## Settings

| setting | meaning |
|---|---|
| Output folder | empty = `Documents\AtasHistoryExport` |
| Export footprint | off = bars only (fast). On = one row per bar per price (big, and the useful one) |
| Bars to export | `0` = every bar on the chart. Set e.g. `50000` to cap it |
| Export on start | run once automatically after the first calculation |
| Export now | set `true` to re-run after changing settings |

The export runs on a background thread, so the chart stays usable. Progress goes to the ATAS log.

**Make the chart cover the period you want first.** Widen the date range / increase the history bars in
the chart's data settings, let it load, then export. The indicator can only dump what the chart holds.

## Limits — stated plainly

- **MBO (individual orders) cannot be exported for the past.** The platform does not retain order-level
  history; it only streams it live. Footprint bid/ask is the finest granularity that exists historically.
- **Sub-second precision is not in the CSV** because the platform's stored bars are 1-minute (or whatever
  your chart period is). Set a smaller chart period before exporting if you need finer bars.
- One chart period per export. Want 1-minute and 5-minute bars? Export twice, using two chart periods.

## Build

Requires the .NET 10 SDK.

```bash
dotnet build -c Release                                          # ATAS X, Windows
dotnet build -c Release -p:AtasBase="C:\Program Files (x86)\ATAS Platform"   # classic ATAS
dotnet build -c Release -p:AtasBase="/Applications/ATAS X.app/Contents/MonoBundle"  # macOS
```

Copy `bin/Release/net10.0-windows/HistoryExporter.dll` to `%APPDATA%\ATAS X\Extensions`
(or `%APPDATA%\ATAS\Indicators` for classic). The folder is watched — ATAS X reloads by itself, classic
ATAS needs the reload button in the status bar. No restart.

## Then

Give me the `footprint.csv` (and the price/time list) and I can check every signal against the real
per-price bid/ask at that minute — volume, absorption, where the prints landed relative to the session
profile, and whether the claimed direction survived the next 15 minutes.
