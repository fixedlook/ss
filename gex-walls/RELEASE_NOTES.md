Drop-in ATAS custom indicator built from `gex-walls/` by CI.

**What it does:** pulls CBOE's free delayed option chain, computes dealer gamma
walls, the zero-gamma flip, IV walls, max pain and the expected-move band, and
draws them on the price chart. Levels refresh on a timer, and the previous value
stays visible on the bars where it was in force.

**Install:** copy `GexWalls.dll` into `%APPDATA%\ATAS X\Indicators\`, reload
custom indicators in ATAS, then add **GEX / IV Walls (CBOE)** from the Custom
group.

**If ATAS rejects it:** the DLL is compiled against the reference assemblies
pinned in the workflow, which may not match your ATAS build. Rebuild locally
against your own install folder:

```
dotnet build -c Release -p:AtasDir="C:\Program Files\ATAS X"
```

**Changed in this build (v0.1.3) — level accuracy:**

* **Zero-gamma flip is now a real gamma flip.** It was a cumulative-by-strike
  crossing, which drifts into the wings (it answered −20% from spot on SPX).
  Every contract is now re-priced with Black-Scholes gamma across a ±12% spot
  grid and the sign change nearest spot is reported. New `Gamma flip search
  band` setting.
* **IV walls are IV walls again.** They were ranked by `IV × OI`, which just
  re-ranks open interest and returned the put wall a second time (both SPX and
  NDX files showed `iv_wall_1 == put_wall_1`). Now the highest OI-weighted mean
  IV among strikes holding ≥2% of the largest per-strike OI.
* **IMPLIED VOL UNITS.** CBOE ships IV either as a fraction or as percent
  points; the chain is now detected instead of assumed, so the flip scan cannot
  run on a 15.0 that means 15%.
* **Source symbols are canonicalised**: `NQ`, `_NQ`, `MNQ`, `MNQZ26` all resolve
  to `_NDX`; a half-typed symbol no longer fires a 404 per keystroke.
* **Failures now say why**: the log carries the URL, the HTTP status and the
  exception, instead of `chain unavailable`.
* **New `audit_<source>.json`** next to the level cache: filter counts, expiry
  mix, flip grid and the per-strike table (strike, call/put OI, call/put GEX,
  cumulative, mean IV) the levels were read off. Plus a startup cross-check that
  warns when the chain's gamma column disagrees with Black-Scholes gamma from
  its own IV column.

Logs and cached levels: `%APPDATA%\ATAS X\GexWalls\`.
