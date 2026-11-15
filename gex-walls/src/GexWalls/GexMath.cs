using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace GexWalls
{
    /// <summary>
    /// Aggregates an option chain into GEX / IV wall levels.
    ///
    /// This is a direct port of tools/gex_levels.py, which is covered by
    /// tools/test_gex_math.py - keep the two in sync if you change a formula.
    ///
    /// GEX per contract = gamma * OI * multiplier * spot^2 * 0.01, i.e. the
    /// dollars of dealer delta that has to be re-hedged per 1% move of the
    /// underlying. Calls count as positive dealer gamma, puts as negative: the
    /// standard convention (dealers long call gamma / short put gamma against
    /// customer flow).
    ///
    /// Two definitions matter when comparing against a paid feed, and each is
    /// computed one way and one way only:
    ///
    ///   * walls      = the strike carrying the most gamma dollars on that side
    ///                  (call walls only at/above spot, put walls only below),
    ///                  aggregated over the whole DTE window.
    ///   * zero gamma = the price at which the book's gamma dollars cancel, i.e.
    ///                  every contract's gamma is re-priced with Black-Scholes at
    ///                  a hypothetical spot and the net is scanned for a sign
    ///                  change. It is NOT the cumulative-by-strike crossing: that
    ///                  proxy drifts into the wings (it answered -20% for SPX).
    ///   * iv wall    = the strike with the highest open-interest weighted implied
    ///                  vol. Ranking by sum(iv * OI) instead just re-ranks open
    ///                  interest and returns the put wall a second time.
    /// </summary>
    public static class GexMath
    {
        // SPXW261120C02900000 -> root SPXW, 2026-11-20, call, strike 2900.000
        private static readonly Regex OptionSymbol = new Regex(
            @"^(?<root>.+?)(?<exp>[0-9]{6})(?<type>[CP])(?<strike>[0-9]+)$",
            RegexOptions.Compiled | RegexOptions.CultureInvariant);

        public static bool TryParseSymbol(
            string symbol, out string root, out DateTime expiry, out bool isCall, out decimal strike)
        {
            root = "";
            expiry = default(DateTime);
            isCall = false;
            strike = 0m;

            if (string.IsNullOrWhiteSpace(symbol))
                return false;

            var match = OptionSymbol.Match(symbol.Trim().ToUpperInvariant());
            if (!match.Success)
                return false;

            if (!DateTime.TryParseExact(
                    "20" + match.Groups["exp"].Value,
                    "yyyyMMdd",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.None,
                    out expiry))
                return false;

            if (!decimal.TryParse(match.Groups["strike"].Value, NumberStyles.Integer,
                    CultureInfo.InvariantCulture, out strike))
                return false;

            strike /= 1000m;
            isCall = match.Groups["type"].Value == "C";
            root = match.Groups["root"].Value;
            return strike > 0m;
        }

        public static LevelsSnapshot Compute(ChainSnapshot chain, LevelOptions options, DateTime nowUtc)
        {
            var result = new LevelsSnapshot();
            var audit = new ChainAudit();
            result.Audit = audit;

            if (chain == null)
            {
                result.Status = "no chain";
                return result;
            }

            result.Source = chain.Source;
            result.Spot = chain.Spot;
            result.Iv30 = chain.Iv30;
            result.UpdatedUtc = chain.FetchedUtc;

            audit.Source = chain.Source;
            audit.Spot = chain.Spot;
            audit.ContractsTotal = chain.Contracts.Count;

            if (chain.Spot <= 0m || chain.Contracts.Count == 0)
            {
                result.Status = "empty chain";
                return result;
            }

            options = options ?? new LevelOptions();
            var spot = chain.Spot;
            var multiplier = options.ContractMultiplier <= 0m ? 100m : options.ContractMultiplier;
            var today = nowUtc.Date;

            // CBOE ships iv either as a fraction (0.185) or as percent points
            // (18.5). Detect it once for the whole chain instead of guessing
            // per row: a wrong guess turns a 15% IV into 1500% and poisons the
            // gamma re-pricing used for the flip.
            var ivScale = DetectIvScale(chain.Contracts);
            audit.IvUnits = ivScale < 1m ? "percent" : "fraction";

            var lowStrike = options.MaxStrikeDistancePct > 0m
                ? spot * (1m - options.MaxStrikeDistancePct / 100m)
                : 0m;
            var highStrike = options.MaxStrikeDistancePct > 0m
                ? spot * (1m + options.MaxStrikeDistancePct / 100m)
                : decimal.MaxValue;

            var callGex = new SortedDictionary<decimal, decimal>();
            var putGex = new SortedDictionary<decimal, decimal>();
            var callOi = new SortedDictionary<decimal, decimal>();
            var putOi = new SortedDictionary<decimal, decimal>();
            var ivWeighted = new SortedDictionary<decimal, decimal>();
            var ivOi = new SortedDictionary<decimal, decimal>();
            var rowsByExpiry = new Dictionary<DateTime, List<OptionContract>>();
            var flipInputs = new List<FlipInput>();
            var expiryAudit = new SortedDictionary<DateTime, ExpiryAudit>();

            decimal totalCallOi = 0m, totalPutOi = 0m;
            var used = 0;

            foreach (var contract in chain.Contracts)
            {
                if (contract.Strike <= 0m)
                {
                    audit.ExcludedInvalidStrike++;
                    continue;
                }

                if (contract.Expiry.Date < today)
                {
                    audit.ExcludedExpired++;
                    continue;
                }

                var dte = contract.DaysToExpiry(today);

                if (dte <= options.NearestExpiryMaxDte)
                {
                    if (!rowsByExpiry.TryGetValue(contract.Expiry.Date, out var rows))
                        rowsByExpiry[contract.Expiry.Date] = rows = new List<OptionContract>();
                    rows.Add(contract);
                }

                if (dte < options.MinDte || dte > options.MaxDte)
                {
                    audit.ExcludedDte++;
                    continue;
                }

                if (contract.Strike < lowStrike || contract.Strike > highStrike)
                {
                    audit.ExcludedStrike++;
                    continue;
                }

                var oi = contract.OpenInterest;
                if (options.VolumeProxyForToday && dte == 0)
                    oi = Math.Max(oi, contract.Volume);

                if (oi <= 0m || contract.Gamma <= 0m)
                {
                    audit.ExcludedNoOiOrGamma++;
                    continue;
                }

                used++;

                var gex = contract.Gamma * oi * multiplier * spot * spot * 0.01m;
                var iv = contract.Iv * ivScale;

                if (contract.IsCall)
                {
                    Add(callGex, contract.Strike, gex);
                    Add(callOi, contract.Strike, oi);
                    totalCallOi += oi;
                }
                else
                {
                    Add(putGex, contract.Strike, gex);
                    Add(putOi, contract.Strike, oi);
                    totalPutOi += oi;
                }

                if (iv > 0m)
                {
                    Add(ivWeighted, contract.Strike, iv * oi);
                    Add(ivOi, contract.Strike, oi);
                }
                else
                {
                    audit.ExcludedNoIv++;
                }

                if (iv > 0m && contract.Strike > 0m)
                {
                    flipInputs.Add(new FlipInput
                    {
                        Strike = contract.Strike,
                        // T in years, floored so a same-day expiry cannot divide by zero.
                        Years = Math.Max(dte, 0.5m) / 365m,
                        Vol = iv,
                        Oi = oi,
                        Sign = contract.IsCall ? 1m : -1m
                    });
                }

                if (!expiryAudit.TryGetValue(contract.Expiry.Date, out var expiryRow))
                    expiryAudit[contract.Expiry.Date] = expiryRow = new ExpiryAudit { Expiry = contract.Expiry.Date, Dte = dte };

                expiryRow.Contracts++;
                expiryRow.OpenInterest += oi;
                expiryRow.Gex += contract.IsCall ? gex : -gex;
            }

            audit.ContractsUsed = used;
            result.ContractsUsed = used;

            if (totalCallOi > 0m && totalPutOi > 0m)
                result.PutCallOiRatio = Math.Round(totalPutOi / totalCallOi, 4);

            // ---- walls -----------------------------------------------------
            // Call walls are resistance, so only strikes at or above spot count.
            // Put walls are support, so only strikes at or below spot count. Fall
            // back to the full side when the filter leaves nothing.
            var callSide = callGex.Where(kv => kv.Key >= spot).ToList();
            if (callSide.Count == 0)
                callSide = callGex.ToList();

            var putSide = putGex.Where(kv => kv.Key <= spot).ToList();
            if (putSide.Count == 0)
                putSide = putGex.ToList();

            result.CallWall1 = TopStrike(callSide, 0, spot);
            result.CallWall2 = TopStrike(callSide, 1, spot);
            result.PutWall1 = TopStrike(putSide, 0, spot);
            result.PutWall2 = TopStrike(putSide, 1, spot);

            // ---- iv walls --------------------------------------------------
            // OI-weighted MEAN iv per strike, then the vol peak among strikes
            // that carry meaningful open interest. See the class comment: ranking
            // by sum(iv * OI) reproduces the put wall instead of a vol peak.
            var ivPeaks = new List<KeyValuePair<decimal, decimal>>();
            if (ivOi.Count > 0)
            {
                var maxStrikeOi = ivOi.Values.Max();
                var floor = maxStrikeOi * (options.IvWallMinOiFraction > 0m ? options.IvWallMinOiFraction : 0.02m);

                foreach (var pair in ivOi)
                {
                    if (pair.Value < floor || pair.Value <= 0m)
                        continue;

                    ivWeighted.TryGetValue(pair.Key, out var weighted);
                    ivPeaks.Add(new KeyValuePair<decimal, decimal>(pair.Key, weighted / pair.Value));
                }
            }

            result.IvWall1 = TopStrike(ivPeaks, 0, spot);
            result.IvWall2 = TopStrike(ivPeaks, 1, spot);

            // ---- total gamma -----------------------------------------------
            var strikes = callGex.Keys.Union(putGex.Keys).OrderBy(k => k).ToList();
            var cum = 0m;
            var prevCum = (decimal?)null;
            var prevStrike = 0m;
            var chainCrossings = new List<decimal>();
            var strikeRows = new List<StrikeAudit>(strikes.Count);

            foreach (var strike in strikes)
            {
                var call = Get(callGex, strike);
                var put = Get(putGex, strike);
                var net = call - put;
                cum += net;
                result.TotalGex += net;

                if (prevCum.HasValue && ((prevCum.Value < 0m && cum >= 0m) || (prevCum.Value > 0m && cum <= 0m)) && prevCum.Value != cum)
                {
                    var a = prevCum.Value;
                    chainCrossings.Add(prevStrike + (strike - prevStrike) * (-a) / (cum - a));
                }

                prevCum = cum;
                prevStrike = strike;

                ivWeighted.TryGetValue(strike, out var ivSum);
                ivOi.TryGetValue(strike, out var ivContracts);

                strikeRows.Add(new StrikeAudit
                {
                    Strike = strike,
                    CallOi = Get(callOi, strike),
                    PutOi = Get(putOi, strike),
                    CallGex = call,
                    PutGex = put,
                    Net = net,
                    Cumulative = cum,
                    MeanIv = ivContracts > 0m ? ivSum / ivContracts : 0m
                });
            }

            audit.Strikes = strikeRows;
            audit.ChainCrossings = chainCrossings;
            audit.Expiries = expiryAudit.Values.OrderBy(e => e.Expiry).ToList();

            // ---- zero gamma: the real gamma flip ---------------------------
            // Re-price every contract's gamma at a hypothetical spot and find
            // where the net gamma dollars change sign. The cumulative-by-strike
            // number above is kept only as a diagnostic in the audit file.
            var flip = ComputeFlip(flipInputs, spot, multiplier, options);
            result.ZeroGamma = flip.Value;
            audit.FlipValue = flip.Value;
            audit.FlipCrossings = flip.Crossings;
            audit.FlipBandLow = flip.BandLow;
            audit.FlipBandHigh = flip.BandHigh;
            audit.FlipGridPoints = flip.GridPoints;
            audit.BsGexAtSpot = flip.GexAtSpot;
            audit.ChainGexAtSpot = result.TotalGex;
            audit.FlipNote = flip.Note;

            // ---- max pain / expected move on the nearest expiry -----------
            if (rowsByExpiry.Count > 0)
            {
                var nearest = rowsByExpiry.Keys.OrderBy(d => d).First();
                var rows = rowsByExpiry[nearest];
                audit.NearestExpiry = nearest;
                result.MaxPain = MaxPain(rows, spot);

                var low = ExpectedMoveLow(rows, spot);
                var high = ExpectedMoveHigh(rows, spot);
                result.EmLow = low;
                result.EmHigh = high;
            }

            return result;
        }

        public enum FlipStatus
        {
            Ok = 0,
            NoContracts = 1,
            NoCrossing = 2
        }

        /// <summary>Result of the gamma-flip scan, with the diagnostics the audit file records.</summary>
        public sealed class FlipResult
        {
            public decimal? Value;
            public decimal BandLow;
            public decimal BandHigh;
            public int GridPoints;
            public int Crossings;
            public decimal GexAtSpot;
            public string Note = "";
        }

        /// <summary>One frozen input row for the gamma-flip scan.</summary>
        public sealed class FlipInput
        {
            public decimal Strike;
            public decimal Years;
            public decimal Vol;
            public decimal Oi;
            /// <summary>+1 for a call, -1 for a put (dealer-side convention).</summary>
            public decimal Sign;
        }

        /// <summary>
        /// The price where the book's gamma dollars cancel out. Every eligible
        /// contract is re-priced with Black-Scholes gamma at each candidate spot,
        /// so this answers "at which price would dealers be flat gamma" rather
        /// than "which strike has the least cumulative OI mass".
        /// </summary>
        public static FlipResult ComputeFlip(
            IEnumerable<FlipInput> inputs, decimal spot, decimal multiplier, LevelOptions options)
        {
            options = options ?? new LevelOptions();

            var flip = new FlipResult();
            var band = options.GammaFlipBandPct > 0m ? options.GammaFlipBandPct : 12m;
            var steps = options.GammaFlipSteps >= 8 ? options.GammaFlipSteps : 80;

            flip.BandLow = Math.Round(spot * (1m - band / 100m), 2);
            flip.BandHigh = Math.Round(spot * (1m + band / 100m), 2);
            flip.GridPoints = steps + 1;

            var rows = new List<FlipInput>();
            foreach (var input in inputs ?? Enumerable.Empty<FlipInput>())
            {
                if (input == null || input.Oi <= 0m || input.Vol <= 0m || input.Strike <= 0m || input.Years <= 0m)
                    continue;

                // A chain quoting percent points (18.5) must be turned into a
                // fraction here; Compute already normalised, but guard anyway so
                // a caller passing raw contracts cannot produce garbage.
                if (input.Vol > 3m)
                    input.Vol /= 100m;

                rows.Add(input);
            }

            if (rows.Count == 0)
            {
                flip.Note = "no contracts with gamma and iv";
                return flip;
            }

            var low = flip.BandLow;
            var high = flip.BandHigh;
            var step = (high - low) / steps;

            decimal? previousGex = null;
            var previousSpot = 0m;
            decimal? best = null;
            var bestDistance = decimal.MaxValue;
            var crossings = 0;

            for (var i = 0; i <= steps; i++)
            {
                var candidate = low + step * i;
                var gex = NetGex(rows, candidate, multiplier);

                if (previousGex.HasValue && previousGex.Value != gex &&
                    ((previousGex.Value < 0m && gex >= 0m) || (previousGex.Value > 0m && gex <= 0m)))
                {
                    var a = previousGex.Value;
                    var crossing = previousSpot + (candidate - previousSpot) * (-a) / (gex - a);
                    crossings++;

                    var distance = Math.Abs(crossing - spot);
                    if (distance < bestDistance)
                    {
                        bestDistance = distance;
                        best = crossing;
                    }
                }

                previousGex = gex;
                previousSpot = candidate;
            }

            flip.GexAtSpot = NetGex(rows, spot, multiplier);
            flip.Crossings = crossings;
            flip.Value = best.HasValue ? Math.Round(best.Value, 2) : (decimal?)null;
            flip.Note = best.HasValue
                ? ""
                : "no sign change inside +/-" + band.ToString("0.#") + "% of spot";

            return flip;
        }

        private static decimal NetGex(List<FlipInput> rows, decimal spot, decimal multiplier)
        {
            var total = 0m;
            foreach (var row in rows)
            {
                var gamma = BlackScholesGamma(spot, row.Strike, row.Years, row.Vol);
                if (gamma <= 0m)
                    continue;

                total += row.Sign * gamma * row.Oi * multiplier * spot * spot * 0.01m;
            }

            return total;
        }

        /// <summary>Black-Scholes gamma, r = 0. Vol is a fraction (0.15 = 15%).</summary>
        public static decimal BlackScholesGamma(decimal spot, decimal strike, decimal years, decimal vol)
        {
            if (spot <= 0m || strike <= 0m || years <= 0m || vol <= 0m)
                return 0m;

            var s = (double)spot;
            var k = (double)strike;
            var t = (double)years;
            var v = (double)vol;
            var sqrtT = Math.Sqrt(t);
            var denominator = v * sqrtT;
            if (denominator <= 0.0)
                return 0m;

            var d1 = (Math.Log(s / k) + 0.5 * v * v * t) / denominator;
            var pdf = Math.Exp(-0.5 * d1 * d1) / Math.Sqrt(2.0 * Math.PI);

            return (decimal)(pdf / (s * denominator));
        }

        /// <summary>
        /// 0.01 when the chain quotes iv in percent points (15.2), 1 when it
        /// already quotes a fraction (0.152). Median-based, so a couple of junk
        /// rows cannot flip the whole chain.
        /// </summary>
        public static decimal DetectIvScale(IEnumerable<OptionContract> contracts)
        {
            var values = new List<decimal>();
            foreach (var contract in contracts)
            {
                if (contract.Iv > 0m)
                    values.Add(contract.Iv);
            }

            if (values.Count == 0)
                return 1m;

            values.Sort();
            var median = values[values.Count / 2];
            return median > 1m ? 0.01m : 1m;
        }

        private static decimal? TopStrike(List<KeyValuePair<decimal, decimal>> pairs, int rank, decimal spot)
        {
            var ranked = pairs
                .OrderByDescending(kv => kv.Value)
                .ThenBy(kv => Math.Abs(kv.Key - spot))
                .ThenBy(kv => kv.Key)
                .ToList();

            return rank < ranked.Count ? ranked[rank].Key : (decimal?)null;
        }

        /// <summary>
        /// Strike where option holders collectively lose the most at expiry.
        /// Payout is often flat over a wide band, so ties resolve to the strike
        /// closest to spot rather than to the lowest strike.
        /// </summary>
        public static decimal? MaxPain(IEnumerable<OptionContract> rows, decimal spot)
        {
            var list = rows as IList<OptionContract> ?? rows.ToList();
            if (list.Count == 0)
                return null;

            decimal? best = null;
            var bestPayout = 0m;
            var bestDistance = decimal.MaxValue;

            foreach (var settle in list.Select(c => c.Strike).Distinct().OrderBy(k => k))
            {
                var payout = 0m;
                foreach (var contract in list)
                {
                    payout += contract.IsCall
                        ? contract.OpenInterest * Math.Max(0m, settle - contract.Strike)
                        : contract.OpenInterest * Math.Max(0m, contract.Strike - settle);
                }

                payout = Math.Round(payout, 6);
                var distance = Math.Abs(settle - spot);

                if (!best.HasValue || payout < bestPayout || (payout == bestPayout && distance < bestDistance))
                {
                    best = settle;
                    bestPayout = payout;
                    bestDistance = distance;
                }
            }

            return best;
        }

        /// <summary>At-the-money straddle mid of a single expiry: half the implied 1-sigma move.</summary>
        public static decimal? ExpectedMove(IEnumerable<OptionContract> rows, decimal spot)
        {
            var list = rows as IList<OptionContract> ?? rows.ToList();
            if (list.Count == 0 || spot <= 0m)
                return null;

            var atm = list.OrderBy(c => Math.Abs(c.Strike - spot)).First().Strike;
            var call = list.FirstOrDefault(c => c.IsCall && c.Strike == atm);
            var put = list.FirstOrDefault(c => !c.IsCall && c.Strike == atm);

            var callMid = call?.Mid ?? 0m;
            var putMid = put?.Mid ?? 0m;
            if (callMid <= 0m || putMid <= 0m)
                return null;

            return callMid + putMid;
        }

        private static decimal? ExpectedMoveHigh(IEnumerable<OptionContract> rows, decimal spot)
        {
            var move = ExpectedMove(rows, spot);
            return move.HasValue ? Math.Round(spot + move.Value, 2) : (decimal?)null;
        }

        private static decimal? ExpectedMoveLow(IEnumerable<OptionContract> rows, decimal spot)
        {
            var move = ExpectedMove(rows, spot);
            return move.HasValue ? Math.Round(spot - move.Value, 2) : (decimal?)null;
        }

        private static void Add(SortedDictionary<decimal, decimal> map, decimal key, decimal value)
        {
            map.TryGetValue(key, out var current);
            map[key] = current + value;
        }

        private static decimal Get(SortedDictionary<decimal, decimal> map, decimal key)
        {
            return map.TryGetValue(key, out var value) ? value : 0m;
        }
    }
}
