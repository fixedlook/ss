using System;
using System.Collections.Generic;
using System.IO;

namespace GexWalls.Tests
{
    /// <summary>
    /// Executes the shipped GEX maths - the same GexMath.cs and
    /// CboeChainProvider.cs the indicator uses - against the fixture in
    /// fixture_spx.json, which is the same chain tools/test_gex_math.py asserts
    /// on. If this passes, the DLL's numbers match the reference implementation.
    ///
    /// dotnet run --project gex-walls/tests
    /// </summary>
    public static class Program
    {
        private static readonly DateTime Today = new DateTime(2026, 11, 15, 0, 0, 0, DateTimeKind.Utc);

        private static int _failures;

        public static int Main(string[] args)
        {
            var fixture = Path.Combine(
                Path.GetDirectoryName(typeof(Program).Assembly.Location) ?? ".",
                "fixture_spx.json");

            // dotnet run sets the working directory to the project, so look there first
            if (!File.Exists(fixture))
                fixture = Path.Combine(AppContext.BaseDirectory, "fixture_spx.json");
            if (!File.Exists(fixture))
                fixture = Path.Combine("gex-walls", "tests", "fixture_spx.json");
            if (!File.Exists(fixture))
                fixture = "fixture_spx.json";

            if (!File.Exists(fixture))
            {
                Console.WriteLine("FAIL  fixture not found: " + fixture);
                return 1;
            }

            var json = File.ReadAllText(fixture);

            // ---- symbol parsing ------------------------------------------
            GexMath.TryParseSymbol("SPXW261120C02900000", out var root, out var expiry, out var isCall, out var strike);
            Check("parse index symbol",
                root == "SPXW" && expiry == new DateTime(2026, 11, 20) && isCall && strike == 2900m,
                root + " " + expiry.ToString("yyyy-MM-dd") + " " + isCall + " " + strike);

            GexMath.TryParseSymbol("SPY260116P00450000", out root, out expiry, out isCall, out strike);
            Check("parse equity symbol",
                root == "SPY" && expiry == new DateTime(2026, 1, 16) && !isCall && strike == 450m,
                root + " " + expiry.ToString("yyyy-MM-dd") + " " + isCall + " " + strike);

            Check("reject junk symbol", !GexMath.TryParseSymbol("not-an-option", out _, out _, out _, out _), "");

            // ---- chain parse ---------------------------------------------
            var chain = CboeChainProvider.Parse(json, "_SPX");
            Check("chain parsed", chain != null && chain.Contracts.Count == 21,
                chain == null ? "null" : chain.Contracts.Count + " contracts");
            Check("spot parsed", chain != null && chain.Spot == 29800m,
                chain == null ? "null" : chain.Spot.ToString("0.##"));

            // ---- levels ---------------------------------------------------
            var result = GexMath.Compute(chain, new LevelOptions { MinDte = 0, MaxDte = 45, MaxStrikeDistancePct = 20m }, Today);

            Check("call wall 1 = 30000", result.CallWall1 == 30000m, Show(result.CallWall1));
            Check("call wall 2 = 29900", result.CallWall2 == 29900m, Show(result.CallWall2));
            Check("put wall 1 = 29600", result.PutWall1 == 29600m, Show(result.PutWall1));
            Check("put wall 2 = 29700", result.PutWall2 == 29700m, Show(result.PutWall2));
            Check("iv wall 1 = 29600", result.IvWall1 == 29600m, Show(result.IvWall1));
            Check("iv wall 2 = 30000", result.IvWall2 == 30000m, Show(result.IvWall2));
            Check("max pain = 29800", result.MaxPain == 29800m, Show(result.MaxPain));
            Check("expected move high = 29860", result.EmHigh == 29860m, Show(result.EmHigh));
            Check("expected move low = 29740", result.EmLow == 29740m, Show(result.EmLow));
            Check("dte + distance filters applied", result.ContractsUsed == 18, result.ContractsUsed.ToString());
            Check("zero gamma between 29900 and 30000",
                result.ZeroGamma.HasValue && result.ZeroGamma.Value > 29900m && result.ZeroGamma.Value < 30000m,
                Show(result.ZeroGamma));
            Check("total gex positive", result.TotalGex > 0m, result.TotalGex.ToString("0"));
            Check("put/call OI ratio ~0.8093",
                result.PutCallOiRatio.HasValue && Math.Abs(result.PutCallOiRatio.Value - 0.8093m) < 0.001m,
                Show(result.PutCallOiRatio));

            // ---- filters --------------------------------------------------
            var noneToday = GexMath.Compute(chain, new LevelOptions { MaxDte = 0, MaxStrikeDistancePct = 20m }, Today);
            Check("maxDte=0 keeps only 0DTE (none in fixture)",
                noneToday.ContractsUsed == 0 && noneToday.CallWall1 == null,
                noneToday.ContractsUsed + " used, wall " + Show(noneToday.CallWall1));

            var wide = GexMath.Compute(chain, new LevelOptions { MaxDte = 200, MaxStrikeDistancePct = 0m }, Today);
            Check("no distance filter picks the 45000 strike", wide.CallWall1 == 45000m, Show(wide.CallWall1));
            Check("no dte cap pulls in the far expiry", wide.ContractsUsed == 21, wide.ContractsUsed.ToString());

            // ---- degenerate input must not throw --------------------------
            var empty = GexMath.Compute(new ChainSnapshot { Spot = 0m }, new LevelOptions(), Today);
            Check("empty chain is safe", empty.ZeroGamma == null && !empty.HasAnyLevel, "");

            var noChain = GexMath.Compute(null, new LevelOptions(), Today);
            Check("null chain is safe", noChain != null && noChain.Status == "no chain", noChain?.Status ?? "null");

            // ---- a level set must survive the indicator's price mapping ---
            // AlignToChart on a futures chart: shift by (chart price - spot)
            const decimal chartPrice = 30100m;
            var offset = chartPrice - chain.Spot;
            Check("basis alignment lands the call wall on the chart scale",
                result.CallWall1 + offset == 30300m,
                (result.CallWall1 + offset)?.ToString("0.##") ?? "null");

            Console.WriteLine();
            Console.WriteLine(_failures == 0
                ? "ALL CHECKS PASSED (" + Passed + ")"
                : _failures + " CHECK(S) FAILED");
            return _failures == 0 ? 0 : 1;
        }

        private static int Passed;

        private static string Show(decimal? value)
        {
            return value.HasValue ? value.Value.ToString("0.####") : "null";
        }

        private static void Check(string name, bool condition, string detail)
        {
            if (condition)
                Passed++;
            else
                _failures++;

            Console.WriteLine((condition ? "PASS  " : "FAIL  ") + name + (string.IsNullOrEmpty(detail) ? "" : "   " + detail));
        }
    }
}
