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

Three steps. The first is a one-time setup; after that it's one double-click.

### Step 1 — install the .NET SDK (once)

You need the **SDK**, not just the runtime. The runtime runs programs; the SDK is
what compiles them. If you already have the runtime, `dotnet --list-sdks` returns
nothing — that's the giveaway.

Open **Command Prompt** or **PowerShell** and run:

```bat
winget install Microsoft.DotNet.SDK.8
```

> If your ATAS is newer and runs .NET 10, use `Microsoft.DotNet.SDK.10` instead.
> The build script detects which one you need and tells you if it's wrong.
> No `winget`? Download from <https://dotnet.microsoft.com/download>.

**Then close and reopen your terminal** — Windows only picks up the new PATH in a
fresh window. Confirm with:

```bat
dotnet --list-sdks
```

You should see a version line. If that errors, the SDK isn't installed or the
terminal wasn't restarted.

### Step 2 — download the code

Download and extract this ZIP (no Git needed):

**<https://github.com/fixedlook/ss/archive/refs/heads/arena/9aa27356-ss.zip>**

You'll get a folder like `ss-arena-9aa27356-ss\atas\`. Extract it somewhere easy,
such as `C:\Users\<you>\ss\`.

### Step 3 — build and install

Open the `atas` folder and **double-click `build.bat`**.

It finds your ATAS install, works out your platform's .NET version, builds the
indicator, and copies the DLL into your ATAS indicators folder — printing what it
did at each step.

If it can't find ATAS, it tells you how to pass the path:

```bat
build.bat -AtasPlatformDir "D:\ATAS Platform"
```

**Prefer to do it by hand?**

```bat
cd C:\Users\<you>\ss\atas\src\SsDrawOnLiquidity
dotnet build -c Release -p:AtasPlatformDir="C:\Program Files (x86)\ATAS Platform"
```

Then install the DLL:

| ATAS version | Folder |
|---|---|
| Current (8.x and newer) | `%APPDATA%\ATAS\Indicators` |
| Older (up to 7.0.7) | `Documents\ATAS\Indicators` |

…or, on any version, use ATAS's own **Add custom indicator** button (bottom-left
of the indicators window) and pick the built DLL. That installs immediately with
no restart.

Then press **Ctrl+I** on a chart and look for **SS Draw On Liquidity** under the
**SS** category.

### Using Visual Studio instead

If you'd rather not use the command line: **New → Class Library (C#)**, delete
`Class1.cs`, add `SsDrawOnLiquidity.cs` and `PotTable.cs`, then
**Dependencies → Add Project Reference → Browse** and select from your ATAS
folder:

- `ATAS.Indicators.dll` (required)
- `OFT.Rendering.dll` (required)
- `OFT.Attributes.dll`, `OFT.Localization.dll` (both ship with ATAS)

Set **Copy Local = False** on each. Build, then install the DLL as above.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No .NET SDKs were found` | Only the runtime is installed — do Step 1, then restart the terminal |
| `The system cannot find the path specified` | The repo isn't on your PC yet — do Step 2 |
| `ATAS.Indicators.dll was not found in ...` | Wrong folder; pass `-AtasPlatformDir` pointing at the folder that *contains* that file |
| `The type or namespace name 'RenderPen' does not exist` | Add `using OFT.Rendering.Tools;` at the top of `SsDrawOnLiquidity.cs` |
| `MSB3277` / framework mismatch | Wrong .NET target — read `tfm` from `OFT.Platform.runtimeconfig.json` in your ATAS folder and build with `-p:AtasTfm=net10.0-windows` (or `net8.0-windows`) |
| Builds, but nothing draws | Chart must be inside RTH (see the setting), and it needs enough history for prior-day levels to exist |
| Indicator missing from the list | Wrong folder for your version — use the table above, or the **Add custom indicator** button |
| Runtime exception | Check logs in `…\ATAS\Logs` next to the indicators folder |

> **There is no built-in ATAS code editor.** Unlike TradingView's Pine Script, ATAS
> requires a compiled DLL — ATAS users are actively requesting an in-platform
> editor precisely because this is the only route. So the steps above are the
> normal path, not a workaround.

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
