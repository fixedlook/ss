using System;
using System.IO;
using System.Text.Json.Nodes;

namespace GexWalls
{
    /// <summary>
    /// The on-disk level file. Two jobs:
    ///
    /// 1. Cache - the last computed snapshot is saved so a restart or an ATAS
    ///    reload shows the levels immediately instead of an empty chart.
    /// 2. Bridge - "Local JSON file" data source mode reads this same file, so
    ///    tools/gex_levels.py (or your own poller, or a paid greeks feed) can
    ///    write it and the indicator just renders.
    ///
    /// Location: %APPDATA%\ATAS X\GexWalls\levels_&lt;source&gt;.json
    /// </summary>
    public static class LocalLevelsFile
    {
        public static readonly string[] LevelKeys =
        {
            "zero_gamma",
            "call_wall_1", "call_wall_2",
            "put_wall_1", "put_wall_2",
            "iv_wall_1", "iv_wall_2",
            "max_pain",
            "em_high", "em_low"
        };

        public static string DataDirectory
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "ATAS X",
                    "GexWalls");
            }
        }

        public static string FilePath(string source)
        {
            var name = (source ?? "").Trim().Replace("/", "_").Replace("\\", "_");
            return Path.Combine(DataDirectory, "levels_" + name + ".json");
        }

        public static void Save(LevelsSnapshot snapshot)
        {
            if (snapshot == null || string.IsNullOrEmpty(snapshot.Source))
                return;

            try
            {
                var levels = new JsonObject
                {
                    ["zero_gamma"] = Number(snapshot.ZeroGamma),
                    ["call_wall_1"] = Number(snapshot.CallWall1),
                    ["call_wall_2"] = Number(snapshot.CallWall2),
                    ["put_wall_1"] = Number(snapshot.PutWall1),
                    ["put_wall_2"] = Number(snapshot.PutWall2),
                    ["iv_wall_1"] = Number(snapshot.IvWall1),
                    ["iv_wall_2"] = Number(snapshot.IvWall2),
                    ["max_pain"] = Number(snapshot.MaxPain),
                    ["em_high"] = Number(snapshot.EmHigh),
                    ["em_low"] = Number(snapshot.EmLow)
                };

                var root = new JsonObject
                {
                    ["source"] = snapshot.Source,
                    ["spot"] = snapshot.Spot,
                    // stored in vol points (18.5), the same convention as gex_levels.py
                    ["iv30"] = snapshot.Iv30 > 0m
                        ? Math.Round(snapshot.Iv30 < 3m ? snapshot.Iv30 * 100m : snapshot.Iv30, 2)
                        : (JsonNode)null,
                    ["updated_utc"] = snapshot.UpdatedUtc.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    ["contracts_used"] = snapshot.ContractsUsed,
                    ["total_gex"] = Math.Round(snapshot.TotalGex, 2),
                    ["put_call_oi_ratio"] = Number(snapshot.PutCallOiRatio),
                    ["levels"] = levels
                };

                Directory.CreateDirectory(DataDirectory);

                var path = FilePath(snapshot.Source);
                var temp = path + ".tmp";
                File.WriteAllText(temp, root.ToJsonString());
                if (File.Exists(path))
                    File.Delete(path);
                File.Move(temp, path);
            }
            catch
            {
                // A cache write must never take the chart down.
            }
        }

        /// <summary>
        /// Writes audit_&lt;source&gt;.json: the filter counts, the expiry breakdown
        /// and the full per-strike table behind the levels. This is the file to
        /// send someone when they say a level is wrong - every number the
        /// indicator used is in it.
        /// </summary>
        public static void SaveAudit(LevelsSnapshot snapshot, LevelOptions options)
        {
            var audit = snapshot?.Audit;
            if (audit == null)
                return;

            try
            {
                var strikes = new JsonArray();
                foreach (var row in audit.Strikes)
                {
                    strikes.Add(new JsonObject
                    {
                        ["strike"] = row.Strike,
                        ["call_oi"] = row.CallOi,
                        ["put_oi"] = row.PutOi,
                        ["call_gex"] = Math.Round(row.CallGex, 2),
                        ["put_gex"] = Math.Round(row.PutGex, 2),
                        ["net_gex"] = Math.Round(row.Net, 2),
                        ["cum_gex"] = Math.Round(row.Cumulative, 2),
                        ["mean_iv"] = Math.Round(row.MeanIv * 100m, 2)
                    });
                }

                var expiries = new JsonArray();
                foreach (var row in audit.Expiries)
                {
                    expiries.Add(new JsonObject
                    {
                        ["expiry"] = row.Expiry.ToString("yyyy-MM-dd"),
                        ["dte"] = row.Dte,
                        ["contracts"] = row.Contracts,
                        ["open_interest"] = row.OpenInterest,
                        ["gex"] = Math.Round(row.Gex, 2)
                    });
                }

                var crossings = new JsonArray();
                foreach (var crossing in audit.ChainCrossings)
                    crossings.Add(JsonValue.Create(Math.Round(crossing, 2)));

                var root = new JsonObject
                {
                    ["source"] = audit.Source,
                    ["spot"] = audit.Spot,
                    ["generated_utc"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    ["data_updated_utc"] = snapshot.UpdatedUtc.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    ["settings"] = new JsonObject
                    {
                        ["min_dte"] = options?.MinDte ?? 0,
                        ["max_dte"] = options?.MaxDte ?? 0,
                        ["max_strike_distance_pct"] = options?.MaxStrikeDistancePct ?? 0m,
                        ["volume_proxy_for_today"] = options?.VolumeProxyForToday ?? false,
                        ["gamma_flip_band_pct"] = options?.GammaFlipBandPct ?? 0m,
                        ["iv_wall_min_oi_fraction"] = options?.IvWallMinOiFraction ?? 0m
                    },
                    ["counts"] = new JsonObject
                    {
                        ["contracts_total"] = audit.ContractsTotal,
                        ["used"] = audit.ContractsUsed,
                        ["excluded_expired"] = audit.ExcludedExpired,
                        ["excluded_invalid_strike"] = audit.ExcludedInvalidStrike,
                        ["excluded_dte"] = audit.ExcludedDte,
                        ["excluded_strike_distance"] = audit.ExcludedStrike,
                        ["excluded_no_oi_or_gamma"] = audit.ExcludedNoOiOrGamma,
                        ["excluded_no_iv"] = audit.ExcludedNoIv
                    },
                    ["iv_units"] = audit.IvUnits,
                    ["gamma_flip"] = new JsonObject
                    {
                        ["value"] = Number(audit.FlipValue),
                        ["band_low"] = audit.FlipBandLow,
                        ["band_high"] = audit.FlipBandHigh,
                        ["grid_points"] = audit.FlipGridPoints,
                        ["crossings"] = audit.FlipCrossings,
                        ["note"] = audit.FlipNote,
                        ["gex_at_spot_blackscholes"] = Math.Round(audit.BsGexAtSpot, 2),
                        ["gex_at_spot_chain_gamma"] = Math.Round(audit.ChainGexAtSpot, 2)
                    },
                    // The old proxy, kept visible so the two can be compared.
                    ["cumulative_by_strike_crossings"] = crossings,
                    ["nearest_expiry"] = audit.NearestExpiry?.ToString("yyyy-MM-dd"),
                    ["levels"] = new JsonObject
                    {
                        ["zero_gamma"] = Number(snapshot.ZeroGamma),
                        ["call_wall_1"] = Number(snapshot.CallWall1),
                        ["call_wall_2"] = Number(snapshot.CallWall2),
                        ["put_wall_1"] = Number(snapshot.PutWall1),
                        ["put_wall_2"] = Number(snapshot.PutWall2),
                        ["iv_wall_1"] = Number(snapshot.IvWall1),
                        ["iv_wall_2"] = Number(snapshot.IvWall2),
                        ["max_pain"] = Number(snapshot.MaxPain),
                        ["em_high"] = Number(snapshot.EmHigh),
                        ["em_low"] = Number(snapshot.EmLow)
                    },
                    ["expiries"] = expiries,
                    ["strikes"] = strikes
                };

                Directory.CreateDirectory(DataDirectory);

                var path = Path.Combine(DataDirectory, "audit_" + FileNameSource(audit.Source) + ".json");
                var temp = path + ".tmp";
                File.WriteAllText(temp, root.ToJsonString());
                if (File.Exists(path))
                    File.Delete(path);
                File.Move(temp, path);
            }
            catch
            {
                // an audit write must never take the chart down
            }
        }

        public static string FileNameSource(string source)
        {
            return (source ?? "").Trim().Replace("/", "_").Replace("\\", "_");
        }

        public static LevelsSnapshot Load(string source)
        {
            try
            {
                var path = FilePath(source);
                if (!File.Exists(path))
                    return null;

                var root = JsonNode.Parse(File.ReadAllText(path)) as JsonObject;
                if (root == null)
                    return null;

                var levels = root["levels"] as JsonObject;
                if (levels == null)
                    return null;

                var snapshot = new LevelsSnapshot
                {
                    Source = (string)root["source"] ?? source,
                    Spot = Decimal(root["spot"]) ?? 0m,
                    ContractsUsed = (int)(Decimal(root["contracts_used"]) ?? 0m),
                    UpdatedUtc = ParseTime(root["updated_utc"]),
                    ZeroGamma = Decimal(levels["zero_gamma"]),
                    CallWall1 = Decimal(levels["call_wall_1"]),
                    CallWall2 = Decimal(levels["call_wall_2"]),
                    PutWall1 = Decimal(levels["put_wall_1"]),
                    PutWall2 = Decimal(levels["put_wall_2"]),
                    IvWall1 = Decimal(levels["iv_wall_1"]),
                    IvWall2 = Decimal(levels["iv_wall_2"]),
                    MaxPain = Decimal(levels["max_pain"]),
                    EmHigh = Decimal(levels["em_high"]),
                    EmLow = Decimal(levels["em_low"]),
                    Status = "file"
                };

                return snapshot.HasAnyLevel ? snapshot : null;
            }
            catch
            {
                return null;
            }
        }

        private static JsonNode Number(decimal? value)
        {
            return value.HasValue ? JsonValue.Create(value.Value) : null;
        }

        private static decimal? Decimal(JsonNode node)
        {
            if (node == null)
                return null;

            try
            {
                return node.GetValue<decimal>();
            }
            catch
            {
                try
                {
                    return decimal.TryParse(node.ToString(), out var parsed) ? parsed : (decimal?)null;
                }
                catch
                {
                    return null;
                }
            }
        }

        private static DateTime ParseTime(JsonNode node)
        {
            var text = node?.ToString();
            return DateTime.TryParse(text, out var parsed) ? parsed.ToUniversalTime() : DateTime.UtcNow;
        }
    }
}
