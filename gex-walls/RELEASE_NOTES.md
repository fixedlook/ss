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

Logs and cached levels: `%APPDATA%\ATAS X\GexWalls\`.
