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
            // IV walls rank the open-interest weighted MEAN iv per strike. Ranking
            // by sum(iv * OI) instead reproduces the put wall exactly, which is the
            // bug this pair guards.
            Check("iv wall 1 = 30100 (vol peak)", result.IvWall1 == 30100m, Show(result.IvWall1));
            Check("iv wall 2 = 29600", result.IvWall2 == 29600m, Show(result.IvWall2));
            Check("iv wall is not a copy of the put wall", result.IvWall1 != result.PutWall1,
                Show(result.IvWall1) + " vs " + Show(result.PutWall1));
            Check("max pain = 29800", result.MaxPain == 29800m, Show(result.MaxPain));
            Check("expected move high = 29860", result.EmHigh == 29860m, Show(result.EmHigh));
            Check("expected move low = 29740", result.EmLow == 29740m, Show(result.EmLow));
            Check("dte + distance filters applied", result.ContractsUsed == 18, result.ContractsUsed.ToString());
            // The flip is where re-priced gamma nets to zero, and it has to sit
            // inside the scan band - the old cumulative-by-strike proxy drifted
            // into the wings (it answered -20% for SPX).
            Check("zero gamma inside the flip band",
                result.ZeroGamma.HasValue &&
                Math.Abs(result.ZeroGamma.Value - 29800m) < 29800m * 0.12m,
                Show(result.ZeroGamma));
            Check("zero gamma near the re-priced sign change (~29545)",
                result.ZeroGamma.HasValue && Math.Abs(result.ZeroGamma.Value - 29545.49m) < 3m,
                Show(result.ZeroGamma));
            Check("flip scan recorded its band",
                result.Audit != null && result.Audit.FlipBandLow < 29800m && result.Audit.FlipBandHigh > 29800m,
                result.Audit == null ? "no audit" : result.Audit.FlipBandLow + ".." + result.Audit.FlipBandHigh);
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

            // ---- the maths primitives -------------------------------------
            // Black-Scholes gamma, hand-computed: S=K=100, T=1, vol=0.2
            // -> d1 = 0.1, pdf = 0.396953, gamma = pdf / (100 * 0.2) = 0.0198477
            var atmGamma = GexMath.BlackScholesGamma(100m, 100m, 1m, 0.2m);
            Check("black-scholes gamma matches the closed form",
                Math.Abs(atmGamma - 0.0198477m) < 0.0000005m,
                atmGamma.ToString("0.0000000"));

            // gamma depends on ln(S/K) through a symmetric pdf, so an equal
            // percentage either side of the money must give the same value
            var above = GexMath.BlackScholesGamma(100m, 110m, 0.25m, 0.3m);
            var below = GexMath.BlackScholesGamma(100m, 90.9090909m, 0.25m, 0.3m);
            Check("gamma is symmetric around the money",
                Math.Abs(above - below) < 0.000000001m,
                above.ToString("0.00000000") + " vs " + below.ToString("0.00000000"));

            Check("gamma is safe at zero everything",
                GexMath.BlackScholesGamma(0m, 100m, 1m, 0.2m) == 0m &&
                GexMath.BlackScholesGamma(100m, 100m, 0m, 0.2m) == 0m &&
                GexMath.BlackScholesGamma(100m, 100m, 1m, 0m) == 0m,
                "");

            // A one-sided book has no sign change, so there is no flip to draw.
            var callsOnly = new List<GexMath.FlipInput>
            {
                new GexMath.FlipInput { Strike = 100m, Years = 0.1m, Vol = 0.2m, Oi = 500m, Sign = 1m }
            };
            var noFlip = GexMath.ComputeFlip(callsOnly, 100m, 100m, new LevelOptions());
            Check("no flip when the book is one-sided", noFlip.Value == null && noFlip.Crossings == 0,
                Show(noFlip.Value) + " crossings " + noFlip.Crossings);

            var putsOnly = new List<GexMath.FlipInput>
            {
                new GexMath.FlipInput { Strike = 100m, Years = 0.1m, Vol = 0.2m, Oi = 500m, Sign = -1m }
            };
            Check("no flip when the book is all puts",
                GexMath.ComputeFlip(putsOnly, 100m, 100m, new LevelOptions()).Value == null, "");

            // value -> strike with the larger OI wins, and a symmetric pair
            // crosses at the money
            var straddle = new List<GexMath.FlipInput>
            {
                new GexMath.FlipInput { Strike = 90m, Years = 0.08m, Vol = 0.25m, Oi = 400m, Sign = 1m },
                new GexMath.FlipInput { Strike = 110m, Years = 0.08m, Vol = 0.25m, Oi = 400m, Sign = -1m }
            };
            var straddleFlip = GexMath.ComputeFlip(straddle, 100m, 100m, new LevelOptions());
            Check("a call/put straddle flips at the money",
                straddleFlip.Value.HasValue && Math.Abs(straddleFlip.Value.Value - 100m) < 1.5m,
                Show(straddleFlip.Value));

            // ---- iv units --------------------------------------------------
            var percentRows = new List<OptionContract>
            {
                new OptionContract { Iv = 15.2m }, new OptionContract { Iv = 18.5m }
            };
            var fractionRows = new List<OptionContract>
            {
                new OptionContract { Iv = 0.152m }, new OptionContract { Iv = 0.185m }
            };
            Check("percent-quoted iv is detected",
                GexMath.DetectIvScale(percentRows) == 0.01m, GexMath.DetectIvScale(percentRows).ToString());
            Check("fraction-quoted iv is left alone",
                GexMath.DetectIvScale(fractionRows) == 1m, GexMath.DetectIvScale(fractionRows).ToString());

            // ---- the audit accounting must add up --------------------------
            var audit = result.Audit;
            Check("audit exists", audit != null, audit == null ? "null" : audit.Source);
            if (audit != null)
            {
                var accounted = audit.ContractsUsed + audit.ExcludedExpired + audit.ExcludedInvalidStrike +
                                audit.ExcludedDte + audit.ExcludedStrike + audit.ExcludedNoOiOrGamma;

                Check("every contract is accounted for",
                    accounted == audit.ContractsTotal,
                    accounted + " of " + audit.ContractsTotal + " (used " + audit.ContractsUsed + ")");
                Check("audit records the per-strike table",
                    audit.Strikes.Count > 0 && audit.Strikes.Exists(r => r.MeanIv > 0m),
                    audit.Strikes.Count + " strikes");
                Check("audit records the expiry mix", audit.Expiries.Count > 0,
                    audit.Expiries.Count + " expiries");
                Check("audit compares chain gamma against black-scholes",
                    audit.BsGexAtSpot != 0m, audit.BsGexAtSpot.ToString("0"));
            }

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
