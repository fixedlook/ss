# SS Draw On Liquidity — ATAS indicator

Shows the most probable draw-on-liquidity target for the trade you are in, with a
probability of touch that was **fitted and then verified** on 1,460 MNQ trading
days.

```
┌──────────────────────────────────────────────┐
│        ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒    │  ← translucent zone
│ ═══════════════════════════════════════════  │  ┌───────────────────┐
│        ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒    │  │ Target: PDH [1]   │
│                                              │  │ PoT: 23.4%        │
└──────────────────────────────────────────────┘  └───────────────────┘
```

## What it does

While you hold a trade it finds the nearest **unswept** liquidity level that
clears your minimum R:R, and labels it with the measured probability that price
reaches it before your stop.

Level families, all strictly unswept (price has not traded through them since
they formed):

| Family | Levels |
|---|---|
| `session_hl` | prior-day H/L, overnight H/L, Asia H/L, London H/L, initial-balance H/L |
| `sess_extreme` | running session high/low |
| `pdc` | prior-day close |
| `swing` | confirmed 3-bar fractal highs/lows |
| `fvg` | unfilled 3-bar fair value gaps |

## Why it picks what it picks

Two results from the research (`docs/FINDINGS.md`) dictated the design:

1. **Distance dominates everything else.** Reward:risk alone carries ~4× the
   predictive weight of any other feature. Adding order flow (delta, bid/ask,
   resting volume) made the model slightly *worse*, so no order-flow inputs are
   used.
2. **Ranking is unnecessary.** Among levels clearing an R:R floor, the nearest
   one is also the highest-probability one. A learned ranking lost to that
   one-line rule, so the indicator ranks by distance.

## Install

1. In Visual Studio, create a **Class Library** project (or open your existing
   ATAS indicators solution).
2. Add both source files:
   - `src/SsDrawOnLiquidity/SsDrawOnLiquidity.cs`
   - `src/SsDrawOnLiquidity/PotTable.cs`
3. Add references to the ATAS assemblies, from your ATAS install folder
   (typically `C:\Program Files (x86)\ATAS Platform\`):
   - `ATAS.Indicators.dll`
   - `OFT.Rendering.dll`, `OFT.Localization.dll`, `OFT.Attributes.dll`
   - plus whatever your ATAS version's own sample indicators reference
4. Build, then copy the resulting `.dll` to
   `Documents\ATAS\Indicators\`.
5. Restart ATAS. The indicator appears as **SS Draw On Liquidity**.

`SSDrawOnLiquidity.csproj` is included as a starting point — point
`AtasPlatformDir` at your install folder, or just copy the two `.cs` files into
your own solution.

## Settings

**Trade**

| Setting | Default | Notes |
|---|---|---|
| Trade direction | Auto | **Set this to Long/Short when you are in a trade.** Auto reads the side of the session open, because an indicator cannot read your position. |
| Stop distance (points) | 40 | Drives R:R and the PoT lookup. |
| Minimum R:R | 1.5 | Closer targets are ignored. |
| Targets shown | 3 | 1 shows only the best. |

**Display** — target colour, zone colour, zone half-width in ticks, text colour.

**Detection** — swing strength (fractal K), max stored levels.

**Sessions** — RTH start/end, Asia start, London start, IB length (minutes).

> ⚠️ **Session times are read from your chart's clock.** If your chart is not in
> US Eastern time, set these to match it, or the overnight/Asia/London levels
> will be wrong.

> **"Only show during RTH" is on by default.** The probability table was fitted
> on regular-hours data, so outside RTH the displayed percentage would not be
> calibrated.

## Reading it

- The bright diagonal-dashed line is your best target; the shaded band is the
  zone around it.
- The box shows `PoT` — the chance price reaches that level **before your stop**,
  at your configured stop distance.
- Secondary targets are drawn dimmer.

PoT is deliberately conservative: when a single 1-minute bar spans both the
target and the stop, it is counted as a stop-out.

## Verification summary

| Check | Result |
|---|---|
| Fit sample | 1,460 trading days, 4,193,607 observations |
| Data integrity | footprint-derived highs match an independent source within 1 tick on 99.86% of minutes |
| Table QA | 360/360 cells, monotonic in R:R and stop size, 0 mismatches |
| End-to-end replay | 19,527 displayed targets over 1,347 days |
| **Displayed PoT** | **23.4%** |
| **Realised hit rate** | **23.2%** |

The number on the box is the number you get. Full detail, including the two
biases found and removed, is in [`docs/FINDINGS.md`](docs/FINDINGS.md).

## Rebuilding the probability table

`data/pot_table.csv` and `data/pot_table.json` are the fitted table; `PotTable.cs`
is generated from the JSON. To refit on different data or an instrument:

```bash
python3 research/04_build_dataset.py      # master bars + structural levels
python3 research/05_build_training.py     # design matrix + base rates
python3 research/12_final_table.py        # the shipped table
python3 research/14_validate_indicator.py # calibration check
```

`research/13_verify_table.py` runs QA on the generated table. Scripts 01–11
document the investigation, including the dead ends.

## Scope

MNQ, RTH, 1-minute-label fitted. The *shape* of the finding (distance dominates)
should transfer to other instruments, but the percentages should be refitted
before being trusted elsewhere.
