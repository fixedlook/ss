# GEX / IV walls on an ATAS chart, for free

**Short answer: yes.** CBOE serves the full option chain — every strike, every
expiry, with open interest, implied vol and greeks — as public JSON with no key
and no login:

```
https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json    (index: leading _)
https://cdn.cboe.com/api/global/delayed_quotes/options/QQQ.json
```

Everything here is built on that one endpoint. The indicator downloads the
chain itself, aggregates it into dealer gamma (GEX) and IV walls, and draws the
levels on the price chart. It re-polls on a timer, so when the book moves the
lines step to their new price and the old value stays visible on the bars where
it was in force.

What you give up versus a paid GEX feed: **15 minutes of delay**, and open
interest that is the previous session's until the overnight file posts. What you
get: the same underlying data the paid vendors start from, locally, for nothing.

---

## What's in here

| Path | What it is |
| --- | --- |
| `src/GexWalls/` | The ATAS indicator (C# class library → `GexWalls.dll`) |
| `src/GexWalls/GexMath.cs` | The aggregation maths (GEX, walls, flip, max pain, expected move) |
| `src/GexWalls/CboeChainProvider.cs` | Download + parse of the CBOE chain, with a per-symbol cache |
| `src/GexWalls/GexWallsIndicator.cs` | Chart drawing, refresh timer, settings, price mapping |
| `tools/gex_levels.py` | Same maths in Python — verify the numbers before trusting the chart, or run it as an external poller |
| `tools/test_gex_math.py` | Offline test: plants walls in a synthetic chain and asserts the levels come back out |

The Python version is the reference implementation: `test_gex_math.py` covers it
(26 checks), and `GexMath.cs` is a straight port. Change a formula in one, change
it in the other.

---

## The levels

All levels come from the same chain and are recomputed together:

| Level | Definition |
| --- | --- |
| **Zero-gamma flip** | Strike where cumulative net dealer gamma changes sign, linearly interpolated between strikes. Above it dealers dampen moves; below it they amplify them. |
| **Call walls (2)** | The two strikes with the largest call gamma × OI **at or above spot** — resistance / pinning. |
| **Put walls (2)** | The two strikes with the largest put gamma × OI **at or below spot** — support / acceleration. |
| **IV walls (2)** | The two strikes carrying the largest `IV × OI` mass, i.e. where the most implied vol is being paid. These are not support/resistance; they mark where a vol repricing matters most. |
| **Max pain** | Nearest-expiry strike with the smallest total option payout. Off by default. |
| **Expected move** | Nearest-expiry ATM straddle mid, drawn as a band around spot. Off by default. |

GEX per contract:

```
gex = gamma × open_interest × 100 × spot² × 0.01        (call: +, put: −)
```

That is dollars of dealer delta that must be re-hedged per 1% move — the same
convention the public GEX providers use. Calls are positive dealer gamma, puts
negative (dealers long call gamma / short put gamma against customer flow).

Filters that matter:

- **DTE window** (default 0–45 days). Gamma is a near-dated phenomenon; a LEAPS
  book is mostly delta and will drown the signal if you let it in.
- **Strike distance** (default ±20% of spot). Far-OTM strikes have big OI and
  tiny gamma, and you would never see them on a zoomed-in chart anyway.
- **Volume as OI for 0DTE.** CBOE's `open_interest` is T-1 until the overnight
  file posts, so today's expiry looks dead all session. Turn this on to use
  volume as an OI stand-in for contracts expiring today.

---

## Getting the price onto your chart

Your MNQ chart is not the SPX or the NDX — it is a future with a basis, and the
levels come from the cash index. So every level is shifted before it is drawn:

| Mapping | Formula | Use for |
| --- | --- | --- |
| **Auto** (default) | index chain → shift by basis; ETF chain → rescale | normal use |
| **AlignToChart** | `level + (chart price − chain spot)` | `_NDX` levels on NQ/MNQ, `_SPX` on ES/MES |
| **Normalize** | `level × (chart price ÷ chain spot)` | QQQ levels on NQ, GLD levels on GC, `_DJX` on YM |
| **Direct** | `level × scale + offset` | you want raw strikes, or a fixed fudge factor |

Auto picks by chain: an `_INDEX` symbol is shifted by the basis (same price
scale, different instrument), an ETF is rescaled (different price scale
entirely — QQQ ≈ NDX/41). Values are snapped to the instrument's tick size.

With auto-detect on, source selection is: NQ/MNQ → `_NDX`, ES/MES → `_SPX`,
RTY/M2K → `_RUT`, YM/MYM → `_DJX`, QQQ/SPY/IWM → their own chain, GC/MGC → `GLD`.
Anything else falls back to the manual source symbol.

---

## Build

Needs the .NET SDK matching your ATAS build (ATAS X is .NET 10 — if your
`ATAS.Indicators.dll` is older, change `TargetFramework` to `net8.0-windows`).

```bash
cd src/GexWalls
dotnet build -c Release -p:AtasDir="C:\Program Files\ATAS X"
```

The project references three assemblies straight out of the ATAS install folder:

- `ATAS.Indicators.dll` — the indicator base class, data series, `RedrawChart`
- `OFT.Rendering.dll` — `LineDashStyle`
- `Avalonia.Base.dll` — `Dispatcher`, for marshalling the refresh onto the UI thread

Nothing else. JSON parsing is `System.Text.Json` from the BCL, the HTTP client is
`System.Net.Http`, and the logger writes its own file — so there are no NuGet
packages to restore.

Install: drop `GexWalls.dll` into `%APPDATA%\ATAS X\Indicators\` (classic ATAS:
`%APPDATA%\ATAS\Indicators\`), reload custom indicators in ATAS, then add
**GEX / IV Walls (CBOE)** from the Custom group.

> Honest note: this was written without an ATAS install to compile against, so
> the first `dotnet build` is the real test. The API surface used is the
> documented one (`Indicator`, `ValueDataSeries`, `LineSeries`-style levels,
> `RedrawChart(new RedrawArg(Container.Region))`, `Dispatcher.UIThread`) and it
> matches what shipping ATAS X indicators do, but expect the possibility of one
> or two namespace nits. If `Series.Color = color.Convert()` complains, your
> build wants `System.Windows.Media.Color` there instead of `System.Drawing.Color`
> — it is the only line in the drawing code that touches that conversion.

---

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| Auto-detect source from chart | on | pick the chain from the instrument name |
| Source symbol (manual) | `_SPX` | used when auto-detect is off |
| Data source | CboeDelayed | or `LocalJsonFile` — read the level file a poller writes |
| Refresh interval | 120 s | CBOE data is 15 min delayed; faster polls buy nothing |
| Min / Max days to expiry | 0 / 45 | gamma-relevant slice |
| Max strike distance | 20 % | drop far-OTM strikes |
| Volume as OI for 0DTE | off | stale-OI workaround for today's expiry |
| Price mapping | Auto | see the table above |
| Show zero-gamma / call walls / put walls / IV walls | on | per-group visibility |
| Show max pain / expected move | off | per-level visibility |
| Six colour settings | gold/green/red/blue/gray/purple | per-group colour |

Rendering notes: the levels are `ValueDataSeries` with `ScaleIt = false`, so
they never squeeze the candle scale, and a value of 0 means "no level on this
bar" — that is how a line ends when a wall disappears.

The last computed snapshot is cached to
`%APPDATA%\ATAS X\GexWalls\levels_<source>.json`, so a restart or an ATAS
reload shows the previous levels instead of an empty chart. Fetch problems are
logged to `%APPDATA%\ATAS X\GexWalls\log.txt`. If the chart is empty, read that
file first — a 404 means the symbol is wrong, and a timeout means your network
is blocking `cdn.cboe.com`.

---

## Verify before you trust it

```bash
cd tools
python3 test_gex_math.py                 # offline, deterministic, no network
python3 gex_levels.py --symbol _NDX      # live numbers, printed as a table
python3 gex_levels.py --symbol QQQ --normalize-spot 29800
python3 gex_levels.py --symbol _SPX --watch 300 --write-cache
```

`--write-cache` writes exactly the file the indicator reads in `LocalJsonFile`
mode, so the Python tool can be the data engine and ATAS just the renderer. That
path is also the escape hatch for plugging in a different feed later
(Polygon, Tradier, your own greeks) without touching the indicator: produce the
same JSON, keep the same ten keys.

---

## Free option-chain sources, and what each is worth

| Source | Cost | Delay | Notes |
| --- | --- | --- | --- |
| **CBOE delayed quotes** (`cdn.cboe.com`) | free, no key | 15 min | Full chain with OI, IV, greeks. What this project uses. Undocumented, so it can change without notice — hence the cache and the stale-fallback. |
| Yahoo Finance options | free | 15 min | Now needs a cookie + crumb handshake; breaks often. No greeks. |
| Nasdaq option chain API | free | 15 min | Needs browser-ish headers, rate-limits hard, no greeks. |
| Tradier sandbox | free with account | ~15 min | Real API with greeks; good fallback if CBOE blocks you. |
| Deribit | free | real-time | Crypto only (BTC/ETH), but genuinely real-time with greeks. |
| gexmonitor.com public API | free, no auth | real-time-ish | Crypto GEX levels already aggregated; used by other ATAS indicators. |
| SpotGamma / GexBot / Convex / Unusual Whales | $50–200+/mo | real-time | Real-time OI, dealer positioning, intraday GEX. This is the only way to get true intraday OI. |

An NQ trader should think in `_NDX` (or QQQ, rescaled) — MNQ has no options of
its own, so the walls that matter for MNQ are the NDX complex's.

---

## Limitations worth knowing

- **15-minute delay** on everything. The walls are structural levels, not a
  scalping signal; a wall that mattered 15 minutes ago usually still matters.
- **OI is T-1 intraday.** Strikes appear and disappear overnight, not tick by
  tick. The walls therefore mostly move because *spot* moves (gamma is
  spot-dependent) and because time decays, not because OI changed. If you need
  intraday OI, that is the paid tier.
- **Greeks are CBOE's model output**, not yours. Fair value, but if you have a
  strong opinion on the vol surface you may want to recompute greeks from `iv`
  yourself (the Python tool is the place to do it).
- **Poll politely.** One download per symbol per 2 minutes is fine; several
  charts on the same symbol share a single cached download.
- **Index vs future basis** is handled by a shift, which is exact for the level
  *location* but does not model strike-liquidity differences between NQ options
  and NDX options — there are no NQ options.

Obvious next steps if this earns its place on your chart: an alert when price
crosses the zero-gamma flip, a GEX-bars profile drawn to the right of the last
bar (the per-strike numbers are already computed), and a heatmap of how each
wall migrated through the session (the step history for that already exists).
