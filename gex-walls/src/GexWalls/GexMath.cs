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
            if (chain == null)
            {
                result.Status = "no chain";
                return result;
            }

            result.Source = chain.Source;
            result.Spot = chain.Spot;
            result.Iv30 = chain.Iv30;
            result.UpdatedUtc = chain.FetchedUtc;

            if (chain.Spot <= 0m || chain.Contracts.Count == 0)
            {
                result.Status = "empty chain";
                return result;
            }

            options = options ?? new LevelOptions();
            var spot = chain.Spot;
            var multiplier = options.ContractMultiplier <= 0m ? 100m : options.ContractMultiplier;
            var today = nowUtc.Date;

            var lowStrike = options.MaxStrikeDistancePct > 0m
                ? spot * (1m - options.MaxStrikeDistancePct / 100m)
                : 0m;
            var highStrike = options.MaxStrikeDistancePct > 0m
                ? spot * (1m + options.MaxStrikeDistancePct / 100m)
                : decimal.MaxValue;

            var callGex = new SortedDictionary<decimal, decimal>();
            var putGex = new SortedDictionary<decimal, decimal>();
            var ivOi = new SortedDictionary<decimal, decimal>();
            var rowsByExpiry = new Dictionary<DateTime, List<OptionContract>>();

            decimal callOi = 0m, putOi = 0m;
            var used = 0;

            foreach (var contract in chain.Contracts)
            {
                if (contract.Strike <= 0m || contract.Expiry.Date < today)
                    continue;

                var dte = contract.DaysToExpiry(today);

                if (dte <= options.NearestExpiryMaxDte)
                {
                    if (!rowsByExpiry.TryGetValue(contract.Expiry.Date, out var rows))
                        rowsByExpiry[contract.Expiry.Date] = rows = new List<OptionContract>();
                    rows.Add(contract);
                }

                if (dte < options.MinDte || dte > options.MaxDte)
                    continue;

                if (contract.Strike < lowStrike || contract.Strike > highStrike)
                    continue;

                var oi = contract.OpenInterest;
                if (options.VolumeProxyForToday && dte == 0)
                    oi = Math.Max(oi, contract.Volume);

                if (oi <= 0m || contract.Gamma <= 0m)
                    continue;

                used++;

                var gex = contract.Gamma * oi * multiplier * spot * spot * 0.01m;

                if (contract.IsCall)
                {
                    Add(callGex, contract.Strike, gex);
                    callOi += oi;
                }
                else
                {
                    Add(putGex, contract.Strike, gex);
                    putOi += oi;
                }

                if (contract.Iv > 0m)
                    Add(ivOi, contract.Strike, contract.Iv * oi);
            }

            result.ContractsUsed = used;

            if (callOi > 0m && putOi > 0m)
                result.PutCallOiRatio = Math.Round(putOi / callOi, 4);

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

            result.CallWall1 = TopStrike(callSide, 0);
            result.CallWall2 = TopStrike(callSide, 1);
            result.PutWall1 = TopStrike(putSide, 0);
            result.PutWall2 = TopStrike(putSide, 1);
            result.IvWall1 = TopStrike(ivOi.ToList(), 0);
            result.IvWall2 = TopStrike(ivOi.ToList(), 1);

            // ---- total gamma and the zero-gamma flip ----------------------
            var strikes = callGex.Keys.Union(putGex.Keys).OrderBy(k => k).ToList();
            var cum = 0m;
            var prevCum = (decimal?)null;
            var prevStrike = 0m;
            var crossings = new List<decimal>();
            decimal weakestCum = 0m;
            var weakestStrike = (decimal?)null;

            foreach (var strike in strikes)
            {
                var net = Get(callGex, strike) - Get(putGex, strike);
                cum += net;
                result.TotalGex += net;

                if (!weakestStrike.HasValue || Math.Abs(cum) < Math.Abs(weakestCum))
                {
                    weakestCum = cum;
                    weakestStrike = strike;
                }

                if (prevCum.HasValue && prevCum.Value != cum)
                {
                    var a = prevCum.Value;
                    if ((a < 0m && cum >= 0m) || (a > 0m && cum <= 0m))
                    {
                        // linear interpolation of the zero crossing
                        crossings.Add(prevStrike + (strike - prevStrike) * (-a) / (cum - a));
                    }
                }

                prevCum = cum;
                prevStrike = strike;
            }

            if (crossings.Count > 0)
                result.ZeroGamma = Math.Round(crossings.OrderBy(c => Math.Abs(c - spot)).First(), 2);
            else if (weakestStrike.HasValue)
                // No sign change in the window: the flat-gamma pocket is the best proxy.
                result.ZeroGamma = Math.Round(weakestStrike.Value, 2);

            // ---- max pain / expected move on the nearest expiry -----------
            if (rowsByExpiry.Count > 0)
            {
                var nearest = rowsByExpiry.Keys.OrderBy(d => d).First();
                var rows = rowsByExpiry[nearest];
                result.MaxPain = MaxPain(rows, spot);

                var low = ExpectedMoveLow(rows, spot);
                var high = ExpectedMoveHigh(rows, spot);
                result.EmLow = low;
                result.EmHigh = high;
            }

            return result;
        }

        private static decimal? TopStrike(List<KeyValuePair<decimal, decimal>> pairs, int rank)
        {
            var ranked = pairs
                .OrderByDescending(kv => kv.Value)
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
            decimal bestPayout = 0m;
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
