# Book & Tape Exporter — ATAS / ATAS X

A chart indicator that writes every order‑event stream the platform exposes to plain CSV, so a signal
can be studied against engine‑level data instead of a chart picture.

This exists because the MNQ extracts in this repo stop on **2026‑10‑02**, and the signals in question are
from 2026‑11‑09/10. Aggregate 1‑minute bars cannot answer "what did the tape do a second before this
print", so the data has to be captured at source.

## What it writes

One file per stream per session day, e.g. `trades_20261110.csv`. Every row carries **both** `ts_utc`
and `ts_local`, so the file is machine‑readable and readable in market time without arithmetic.

| stream | one row per | columns | history |
|---|---|---|---|
| `trades` | print | price, size, dir, aggressor order id, resting order id, OI | **backfillable** (see below) |
| `sweeps` | print joined to the resting order it ate | aggressor side, resting side, resting price, size, both order ids | live + backfill |
| `mbo` | order add / change / delete | source time (ns), feed latency, side, price, size, queue priority, order id, reason, flags | **live only** |
| `dom` | best‑bid/ask change, plus throttled book snapshots | BBO + sizes, spread in ticks, cumulative bid/ask size, the changing level | **live only** |
| `structure` | bar | OHLCV, bid/ask/between volume, delta, max/min delta, ticks, OI, POC | derived from bars |

`_status.csv` is appended every ~2 s with row counts, queue depth, dropped rows, write errors and
backfill progress. **If a session is missing, `_status.csv` says why** — that is the first file to look at.

## Backfill: a month or more, not 7 days

The platform refuses a single cumulative‑trades request longer than 7 days and allows only one
outstanding request at a time. This indicator chains them: it walks the window in 6.5‑day slices,
request → wait for response → advance cursor → repeat, until the whole window is covered.

`Backfill trades (days)` is therefore a free parameter — 30, 45, 90 all work. At 90 days it is ~60
chained requests; expect it to take a while and to produce several GB. Start at 30.

Two hard limits worth knowing before you plan around this:

- **MBO and depth have no history.** The platform only subscribes from the moment the indicator starts.
  Order‑level data for a signal that already happened cannot be recovered — it has to be captured going
  forward. This is the single biggest constraint on the whole exercise.
- **Backfill stops where the live tape begins.** The two paths never write the same instant twice, so
  the files are directly concatenable.

## Install

Requires the **.NET 10 SDK** and a reference to the ATAS DLLs, which ship with the platform.

```bash
# ATAS X on Windows (default)
dotnet build -c Release

# classic ATAS (x86 install path)
dotnet build -c Release -p:AtasBase="C:\Program Files (x86)\ATAS Platform"

# macOS
dotnet build -c Release -p:AtasBase="/Applications/ATAS X.app/Contents/MonoBundle"
```

Then copy `bin/Release/net10.0-windows/BookTapeExporter.dll` into:

- classic ATAS: `%APPDATA%\ATAS\Indicators`
- ATAS X: `%APPDATA%\ATAS X\Extensions`
- macOS: `~/Library/Application Support/ATAS/Extensions`

or use **Add custom indicator** in the indicators window. The platform watches the folder, so no restart
is needed — classic ATAS shows a reload button in the status bar, ATAS X reloads on its own.

If the platform is on .NET 8, build with
`-p:TargetFramework=net8.0-windows` instead; the `tfm` in `OFT.Platform.runtimeconfig.json` next to the
platform executable tells you which.

## Settings that matter

- **Platform time zone** — leave empty to use this computer's zone, which is right for a normal install.
  Set it (Windows or IANA id) if the platform displays times in a zone other than the machine's. The
  conversion to UTC resolves the offset **per timestamp**, so daylight time is handled correctly.
- **Session time zone** — `Central Standard Time` (Windows) or `America/Chicago` (IANA). Both work; the
  id is resolved at runtime.
- **Session roll hour** — `17`, so a Globex session is one file instead of being cut at midnight. The
  session day is `(local time − roll hours).Date`.
- **Max rows per file** — `4,000,000`. Beyond this the day continues in `_p1`, `_p2`. A month of MBO on a
  liquid future will hit this repeatedly, which is intended: one openable file beats one 8 GB file.
- **Depth snapshot interval** — `250 ms`. Best‑bid/ask changes are always written regardless, because a
  BBO change is an event and throttling it would destroy the signal.

## How MBO actually behaves on CME

`MarketByOrder` is only populated when the connector and the data feed publish order‑level data *and*
the account's tariff allows it. Many CME‑oriented feeds publish aggregated depth only.

The indicator does not pretend otherwise: if `SubscribeMarketByOrderData()` fails the reason goes to the
ATAS log (`BookTapeExporter.cs` catch block) and to `_status.csv` as `no-mbo`, and `mbo.csv` /
`sweeps.csv` stay empty while trades, dom and structure keep filling. **Check `_status.csv` after the
first minute of a session** — if it says `no-mbo`, the order‑level analysis is not available on that
feed and the fallback is trade + depth + bar structure.

## Reading the output

Point the analysis tooling at the exported `trades` / `structure` CSVs instead of the old 1‑minute
extract — they share the same `ts_utc` first column, so the join is trivial. `tape_context.py` currently
reads the historical extract only; wiring the exported trade tape into it is the next step, not something
this DLL does.

Columns are additive only — new fields are appended, never inserted, so an existing parser keeps working.

## Known limitation of a long backfill

A chunk can come back **empty** even though the request was accepted: the broker's data server may not
retain ticks that far back, independently of the platform's 7‑day rule. The chain still advances (the
cursor never moves backwards, so it always terminates), which means a 90‑day request can silently yield
only the last few weeks of actual trades. `_status.csv` reports rows per chunk in the log line and the
running total, so **compare `backfill_rows` against the day count before trusting the window**. Start
with 30 days, confirm the oldest file has rows, then extend.
