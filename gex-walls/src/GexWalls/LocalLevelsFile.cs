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
