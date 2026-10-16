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

### 1. Create the project

**Option A — use the included project file**

```bash
cd atas/src/SsDrawOnLiquidity
dotnet build SsDrawOnLiquidity.csproj
```

If your ATAS is not in `C:\Program Files (x86)\ATAS Platform`, pass it:

```bash
dotnet build SsDrawOnLiquidity.csproj -p:AtasPlatformDir="D:\ATAS Platform"
```

**Option B — Visual Studio**

1. **File → New → Project → Class Library** (C#), targeting **.NET 8** or
   **.NET 10** — see the framework note below.
2. Delete the generated `Class1.cs`, then add:
   - `src/SsDrawOnLiquidity/SsDrawOnLiquidity.cs`
   - `src/SsDrawOnLiquidity/PotTable.cs`
3. Right-click **Dependencies → Add Project Reference → Browse** and pick these
   from your ATAS install folder:
   - `ATAS.Indicators.dll` (required)
   - `OFT.Rendering.dll`, `OFT.Localization.dll`, `OFT.Attributes.dll`
   - `Utils.Common.dll` (only needed if you add logging)
   - Set **Copy Local = False** on all of them — ATAS loads these itself.
4. **Build → Build Solution** (Ctrl+Shift+B). Expect `1 succeeded` and one
   warning we deliberately allow (see Troubleshooting).

### 2. Framework version — the one thing that trips people up

The target framework must match the .NET runtime your ATAS runs on (.NET 8 or
.NET 10), and **the version number in the bottom-right corner of the platform
does not tell you which**. The reliable way:

> Open `OFT.Platform.runtimeconfig.json` in your ATAS install folder and read the
> `tfm` value — e.g. `net10.0` means target .NET 10.

The included `.csproj` sidesteps this by building for both at once, so you can
just install whichever output matches.

### 3. Install the DLL

**Recommended:** in ATAS, open the indicators window, click **Add custom
indicator** (bottom-left), and select your built `.dll`. It loads immediately —
no restart.

**Manual copy:** put the DLL in the indicators folder:

| ATAS version | Folder |
|---|---|
| Current (8.x and newer) | `%APPDATA%\ATAS\Indicators` — e.g. `C:\Users\<you>\AppData\Roaming\ATAS\Indicators` |
| Older (up to 7.0.7) | `Documents\ATAS\Indicators` |

Then press Ctrl+I on a chart and look for **SS Draw On Liquidity** under the
**SS** category.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `The type or namespace name 'RenderPen' does not exist` | Add `using OFT.Rendering.Tools;` |
| Errors about `Color` / colour pickers | Keep `<UseWPF>true</UseWPF>` in the csproj |
| Indicator missing from the list after copying a DLL | Wrong folder for your version — use the table above, or the **Add custom indicator** button |
| Builds but nothing draws | Chart must be inside RTH (see the setting), and you need enough history on the chart for prior-day levels to exist |
| Runtime exception | Check the log files in `…\ATAS\Logs` next to the indicators folder |
| `MSB3277` / framework mismatch | You targeted the wrong .NET — check `tfm` as described above |

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
