using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace GexWalls
{
    /// <summary>
    /// Downloads and parses CBOE's public delayed option chain.
    ///
    ///   https://cdn.cboe.com/api/global/delayed_quotes/options/&lt;SYMBOL&gt;.json
    ///
    /// No key, no login, 15 minute delay. Index symbols take an underscore
    /// (_SPX, _NDX, _RUT, _VIX); ETFs are plain (SPY, QQQ, IWM). The endpoint is
    /// not a documented product, so every failure mode here is non-fatal: the
    /// last good chain is reused and the chart keeps its previous levels.
    ///
    /// Chains are cached per source, so several charts open on the same
    /// underlying share one download.
    /// </summary>
    public static class CboeChainProvider
    {
        private const string BaseUrl = "https://cdn.cboe.com/api/global/delayed_quotes/options/";

        private static readonly HttpClient Http = CreateClient();
        private static readonly SemaphoreSlim Gate = new SemaphoreSlim(1, 1);

        private static readonly Dictionary<string, CacheEntry> Cache =
            new Dictionary<string, CacheEntry>(StringComparer.OrdinalIgnoreCase);

        private sealed class CacheEntry
        {
            public ChainSnapshot Chain;
            public DateTime FetchedUtc;
            public string Error = "";
        }

        private static HttpClient CreateClient()
        {
            var handler = new SocketsHttpHandler
            {
                AutomaticDecompression = DecompressionMethods.All,
                AllowAutoRedirect = true,
                MaxAutomaticRedirections = 5
            };

            var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(30) };
            client.DefaultRequestHeaders.UserAgent.ParseAdd("ATAS-GexWalls/1.0");
            client.DefaultRequestHeaders.Accept.ParseAdd("application/json");
            return client;
        }

        /// <summary>
        /// Returns the chain for <paramref name="source"/>, hitting the network at most
        /// once per <paramref name="minSeconds"/> across all indicator instances.
        /// Returns the stale copy when the download fails, or null on a cold failure.
        /// </summary>
        public static async Task<ChainSnapshot> GetChainAsync(
            string source, int minSeconds, CancellationToken token)
        {
            source = (source ?? "").Trim();
            if (source.Length == 0)
                return null;

            var minInterval = Math.Max(20, minSeconds);

            if (TryGetFresh(source, minInterval, out var fresh))
                return fresh;

            await Gate.WaitAsync(token).ConfigureAwait(false);
            try
            {
                if (TryGetFresh(source, minInterval, out fresh))
                    return fresh;

                var url = BaseUrl + Uri.EscapeDataString(source) + ".json";
                var json = await Http.GetStringAsync(url, token).ConfigureAwait(false);

                var chain = Parse(json, source);
                if (chain == null || chain.Contracts.Count == 0)
                    throw new InvalidOperationException("chain parsed empty");

                lock (Cache)
                    Cache[source] = new CacheEntry { Chain = chain, FetchedUtc = DateTime.UtcNow };

                return chain;
            }
            catch (Exception ex)
            {
                lock (Cache)
                {
                    if (Cache.TryGetValue(source, out var cached) && cached.Chain != null)
                    {
                        cached.Error = ex.Message;
                        return cached.Chain;
                    }

                    Cache[source] = new CacheEntry { Error = ex.Message, FetchedUtc = DateTime.UtcNow };
                }

                return null;
            }
            finally
            {
                Gate.Release();
            }
        }

        private static bool TryGetFresh(string source, int minInterval, out ChainSnapshot chain)
        {
            chain = null;
            lock (Cache)
            {
                if (!Cache.TryGetValue(source, out var entry) || entry.Chain == null)
                    return false;

                if ((DateTime.UtcNow - entry.FetchedUtc).TotalSeconds >= minInterval)
                    return false;

                chain = entry.Chain;
                return true;
            }
        }

        /// <summary>Parses the raw JSON body. Kept public so it can be exercised against saved payloads.</summary>
        public static ChainSnapshot Parse(string json, string source)
        {
            using (var document = JsonDocument.Parse(json))
            {
                var root = document.RootElement;
                if (!root.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Object)
                    return null;

                var chain = new ChainSnapshot
                {
                    Source = source,
                    FetchedUtc = DateTime.UtcNow,
                    Spot = ReadDecimal(data, "current_price") ?? ReadDecimal(data, "close") ?? 0m,
                    Iv30 = ReadDecimal(data, "iv30") ?? 0m
                };

                if (!data.TryGetProperty("options", out var options) || options.ValueKind != JsonValueKind.Array)
                    return chain;

                foreach (var row in options.EnumerateArray())
                {
                    if (row.ValueKind != JsonValueKind.Object)
                        continue;

                    var symbol = ReadString(row, "option");
                    if (!GexMath.TryParseSymbol(symbol, out var rootName, out var expiry, out var isCall, out var strike))
                        continue;

                    chain.Contracts.Add(new OptionContract
                    {
                        Symbol = symbol,
                        Root = rootName,
                        Expiry = expiry,
                        IsCall = isCall,
                        Strike = strike,
                        Bid = ReadDecimal(row, "bid") ?? 0m,
                        Ask = ReadDecimal(row, "ask") ?? 0m,
                        Last = ReadDecimal(row, "last_trade_price") ?? 0m,
                        Iv = ReadDecimal(row, "iv") ?? 0m,
                        Delta = ReadDecimal(row, "delta") ?? 0m,
                        Gamma = ReadDecimal(row, "gamma") ?? 0m,
                        Theta = ReadDecimal(row, "theta") ?? 0m,
                        Vega = ReadDecimal(row, "vega") ?? 0m,
                        OpenInterest = ReadDecimal(row, "open_interest") ?? 0m,
                        Volume = ReadDecimal(row, "volume") ?? 0m
                    });
                }

                return chain;
            }
        }

        private static decimal? ReadDecimal(JsonElement element, string name)
        {
            if (!element.TryGetProperty(name, out var value))
                return null;

            switch (value.ValueKind)
            {
                case JsonValueKind.Number:
                    return value.TryGetDecimal(out var number) ? number : (decimal?)null;
                case JsonValueKind.String:
                    return decimal.TryParse(value.GetString(), NumberStyles.Any,
                        CultureInfo.InvariantCulture, out var parsed)
                        ? parsed
                        : (decimal?)null;
                default:
                    return null;
            }
        }

        private static string ReadString(JsonElement element, string name)
        {
            return element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
                ? value.GetString()
                : null;
        }
    }
}
