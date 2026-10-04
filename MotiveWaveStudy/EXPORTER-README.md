# Bid/Ask Exporter (streaming) — install & run

## Why the old one crashed

The old exporter made **one** `forEachTick` call over the whole requested range
and accumulated everything in memory before writing anything:

- a `Map` with one entry per **(bar × price level)** — 31,634 entries per session,
  roughly **55 million** for seven years,
- plus per-bar arrays for every bar in range.

With MotiveWave's heap at ~1.3 GB (`756M of 1.3G` in your status bar) that is a
guaranteed death, and because the CSVs were written at the very end, a crash lost
**the entire export**.

## What this version does differently

| | old | new |
|---|---|---|
| tick reads | 1 call, whole range | chunked, default 5,000 bars |
| peak memory | grows with range | flat — one chunk |
| writing | all at the end | streams as it goes |
| crash cost | everything | only the current chunk |
| 7-year output | ~1.9 GB | ~130 MB (~29 MB zipped) |

Chunks are extended to the end of their session, so a session's profile is never
split across two chunks.

## Install

1. Download **`dist/MNQ-BidAskExporter.jar`** from this repository (browser →
   open the file → Download).
2. In MotiveWave: **Study → All Studies → Import**, pick the jar.
   (Or drop it into MotiveWave's `Extensions` folder and restart.)
3. Right-click the chart → remove the **old** "Bid/Ask Exporter" study, so you
   don't confuse the two menu items.
4. Add the new study: **Study → All Studies → Bid/Ask Exporter (streaming)**.

## Configure

Right-click the study → **Properties / Edit** → *Export* tab:

| setting | value | why |
|---|---|---|
| **CSV Path** | e.g. `C:\mnq\mnq_flow.csv` | base name for all outputs |
| Write bar/minute file | ✔ | per-minute bid/ask, delta, trade counts |
| Write session price profile | ✔ | the compact per-session price levels |
| Write full minute × price footprint | ✗ | **leave off** — this is the 1.9 GB file |
| Max bars | 0 | 0 = everything loaded in the chart |
| Regular trading hours only | ✔ | matches the existing export |
| Bars per tick read | 5000 | lower it if you still see trouble |

## Output files

| file | rows/session | contents |
|---|---|---|
| `<base>.csv` | ~440 | `timestamp,open,high,low,close,volume,bid_volume,ask_volume,delta,trades,ask_trades,bid_trades` |
| `<base>.profile.csv` | ~1,600 | `session_start,price,bid_volume,ask_volume,trades` |
| `<base>.footprint.csv` | ~31,600 | optional, off by default |

## ⚠️ The chart must actually contain the bars

The study can only export what MotiveWave has **loaded**. So before exporting,
scroll the chart back until the range you want is loaded — the same "Max bars"
limit applies as before:

| range | sessions | bars needed | chart Max bars |
|---|---|---|---|
| **2 years (2024-01 → 2026-02)** | ~500 | **~221,000** | ≥ 221,000 |
| 7 years (2019 → 2026) | ~1,750 | ~773,500 | ≥ 773,500 |

Your chart's Max bars is **225,000**, which is *just* enough for the 2-year
target. If it will not load that far back, do it in two passes (e.g.
2024-01 → 2025-02, then 2025-02 → 2026-02) with separate output files.

## Run

Right-click the study → **Export bid/ask CSV (streaming)**.

- MotiveWave **will not respond** while it runs — that is expected, not a crash.
  Watch the output file grow on disk to see progress.
- A line is printed to the log per chunk.
- If it is interrupted, **what was already written is kept**; re-run to get the
  rest (using a different path/range).

## What to send back

Zip and upload `<base>.csv` and `<base>.profile.csv` — **not** the footprint
file, and not both if the zip exceeds 25 MB.

Target: **2024-01 → 2026-02** (~500 sessions). That roughly triples the order-flow
history (164 sessions today) and is what the LVN × CVD-divergence test needs to
get from t≈1.2 to a real answer.
