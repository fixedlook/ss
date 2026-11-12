using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using ATAS.Indicators;
using Utils.Common.Logging;

namespace Atas.Indicators.HistoryExport
{
	/// <summary>
	/// History Exporter: writes the bars and the footprint that are <b>already loaded on the chart</b> to CSV.
	///
	/// This is a history dump, not a capture. The platform has the whole range in memory - the same data
	/// you are looking at - so one pass over the bars produces the file. Nothing has to be recorded live,
	/// and the result covers whatever the chart covers.
	///
	/// Add it to any chart, set the folder (or leave the default), press "Export now" in the settings,
	/// wait for the log line, done. It also export once automatically at the end of the first calculation.
	///
	/// Output:
	///   bars.csv       one row per bar: OHLCV, bid/ask volume, delta, delta extrema, OI, POC
	///   footprint.csv  one row per bar per price: bid volume, ask volume, delta, ticks
	///   _status.csv    what was written and any limitation hit
	/// </summary>
	[DisplayName("History Exporter")]
	[Category("Order Flow")]
	public class HistoryExport : Indicator
	{
		private const string BarsHeader =
			"ts_utc,ts_local,sym,open,high,low,close,vol,bid_vol,ask_vol,between,delta," +
			"max_delta,min_delta,ticks,oi,poc,poc_vol";

		private const string FootHeader =
			"ts_utc,ts_local,sym,price,bid_vol,ask_vol,delta,ticks";

		private bool _autoDone;
		private int _exportRunning;
		private long _barRows;
		private long _footRows;
		private decimal _realBidAskBars;
		private decimal _barsSeen;
		private readonly PriceVolumeInfo _level = new();

		#region Settings

		[Display(Name = "Output folder", GroupName = "Export", Order = 10)]
		[Description("Where the CSVs go. Empty = Documents\\AtasHistoryExport.")]
		public string OutputFolder { get; set; } = string.Empty;

		[Display(Name = "Export footprint (price levels)", GroupName = "Export", Order = 20)]
		[Description("Off writes bars.csv only, which is fast. On adds one row per bar per price - much bigger.")]
		public bool ExportFootprint { get; set; } = true;

		[Display(Name = "Bars to export", GroupName = "Export", Order = 30)]
		[Description("How many of the most recent bars to dump. 0 = every bar loaded on the chart.")]
		public int MaxBars { get; set; }

		[Display(Name = "Export on start", GroupName = "Export", Order = 40)]
		[Description("Run the export automatically once the first calculation finishes.")]
		public bool ExportOnStart { get; set; } = true;

		[Display(Name = "Export now", GroupName = "Export", Order = 50)]
		[Description("Set to true to start an export. Resets itself when finished.")]
		public bool ExportNow
		{
			get => false;
			set
			{
				if (value)
					StartExport();
			}
		}

		#endregion

		public HistoryExport()
		{
			DenyToChangePanel = true;
			DataSeries[0].IsHidden = true;
		}

		protected override void OnCalculate(int bar, decimal value)
		{
			PumpPending();
		}

		protected override void OnFinishRecalculate()
		{
			if (!_autoDone && ExportOnStart && CurrentBar > 0)
			{
				_autoDone = true;
				StartExport();
			}
		}

		private void StartExport()
		{
			if (Interlocked.CompareExchange(ref _exportRunning, 1, 0) != 0)
				return;

			// Reading candles is cheap but writing a year of footprints is not, so the work goes to a
			// background thread; the platform's calculation thread is released immediately.
			var thread = new Thread(ExportLoop)
			{
				IsBackground = true,
				Name = "HistoryExport",
				Priority = ThreadPriority.BelowNormal
			};
			thread.Start();
		}

		private void PumpPending()
		{
			// Kept as a hook: with the background-thread approach there is nothing to pump here, and this
			// stays empty on purpose rather than silently dropping work.
		}

		private void ExportLoop()
		{
			try
			{
				var folder = ResolveFolder();
				Directory.CreateDirectory(folder);

				var barCount = CurrentBar;

				if (barCount <= 0)
				{
					this.LogWarn("History Exporter: no bars loaded, nothing to export.");
					return;
				}

				var first = MaxBars > 0 ? Math.Max(0, barCount - MaxBars) : 0;
				var symbol = InstrumentInfo?.Instrument ?? "?";
				var zone = ResolveZone();

				this.LogInfo("History Exporter: exporting bars {0}..{1} ({2:N0}) to {3}",
					first, barCount - 1, barCount - first, folder);

				_barRows = 0;
				_footRows = 0;
				_realBidAskBars = 0;
				_barsSeen = 0;

				using (var barsFile = new StreamWriter(Path.Combine(folder, "bars.csv"), false, new UTF8Encoding(false)))
				using (var footFile = ExportFootprint
					? new StreamWriter(Path.Combine(folder, "footprint.csv"), false, new UTF8Encoding(false))
					: null)
				{
					barsFile.WriteLine(BarsHeader);
					footFile?.WriteLine(FootHeader);

					var bars = new StringBuilder(256);
					var foot = new StringBuilder(256);

					for (var bar = first; bar < barCount; bar++)
					{
						IndicatorCandle candle;

						try
						{
							candle = GetCandle(bar);
						}
						catch (Exception)
						{
							// An index can go stale while the chart scrolls; skip rather than abort the dump.
							continue;
						}

						if (candle is null)
							continue;

						_barsSeen++;

						var local = TimeZoneInfo.ConvertTimeFromUtc(
							DateTime.SpecifyKind(ToUtc(candle.LastTime, zone), DateTimeKind.Utc), zone);

						var tsUtc = Fmt(ToUtc(candle.LastTime, zone));
						var tsLocal = Fmt(local);

						var poc = candle.MaxVolumePriceInfo;

						bars.Clear();
						bars.Append(tsUtc).Append(',')
							.Append(tsLocal).Append(',')
							.Append(symbol).Append(',')
							.Append(Fmt(candle.Open)).Append(',')
							.Append(Fmt(candle.High)).Append(',')
							.Append(Fmt(candle.Low)).Append(',')
							.Append(Fmt(candle.Close)).Append(',')
							.Append(Fmt(candle.Volume)).Append(',')
							.Append(Fmt(candle.Bid)).Append(',')
							.Append(Fmt(candle.Ask)).Append(',')
							.Append(Fmt(candle.Betweens)).Append(',')
							.Append(Fmt(candle.Delta)).Append(',')
							.Append(Fmt(candle.MaxDelta)).Append(',')
							.Append(Fmt(candle.MinDelta)).Append(',')
							.Append(Fmt(candle.Ticks)).Append(',')
							.Append(Fmt(candle.OI)).Append(',')
							.Append(poc is null ? string.Empty : Fmt(poc.Price)).Append(',')
							.Append(poc is null ? string.Empty : Fmt(poc.Volume));

						barsFile.WriteLine(bars);
						_barRows++;

						if (candle.Bid > 0 || candle.Ask > 0)
							_realBidAskBars++;

						if (footFile is null)
							continue;

						// The overload writes each level into the reused instance, so no allocation per level.
						foreach (var level in candle.GetAllPriceLevels(_level))
						{
							if (level is null || level.Volume <= 0)
								continue;

							foot.Clear();
							foot.Append(tsUtc).Append(',')
								.Append(tsLocal).Append(',')
								.Append(symbol).Append(',')
								.Append(Fmt(level.Price)).Append(',')
								.Append(Fmt(level.Bid)).Append(',')
								.Append(Fmt(level.Ask)).Append(',')
								.Append(Fmt(level.Ask - level.Bid)).Append(',')
								.Append(Fmt(level.Ticks));

							footFile.WriteLine(foot);
							_footRows++;
						}
					}
				}

				var pct = _barsSeen > 0 ? 100.0 * (double)_realBidAskBars / (double)_barsSeen : 0.0;

				this.LogInfo("History Exporter: done. {0:N0} bars, {1:N0} price levels. Real bid/ask on {2:0.0}% of bars.",
					_barRows, _footRows, pct);

				WriteStatus(folder, "done", $"bars={_barRows}; levels={_footRows}; bidask={pct:0.0}%");
			}
			catch (Exception e)
			{
				this.LogError("History Exporter: export failed.", e);
				WriteStatus(ResolveFolderSafe(), "failed", e.Message);
			}
			finally
			{
				Interlocked.Exchange(ref _exportRunning, 0);
			}
		}

		private void WriteStatus(string folder, string stage, string detail)
		{
			try
			{
				var path = Path.Combine(folder, "_status.csv");

				if (!File.Exists(path))
					File.WriteAllText(path, "ts_utc,symbol,stage,detail,bars,levels\n");

				File.AppendAllText(path,
					$"{Fmt(DateTime.UtcNow)},{InstrumentInfo?.Instrument ?? "?"},{stage}," +
					$"\"{detail.Replace('"', '\'')}\",{_barRows},{_footRows}\n");
			}
			catch (Exception)
			{
				// Status is a convenience; never let it break the export.
			}
		}

		private string ResolveFolderSafe()
		{
			try
			{
				return ResolveFolder();
			}
			catch (Exception)
			{
				return Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
			}
		}

		private string ResolveFolder()
		{
			if (!string.IsNullOrWhiteSpace(OutputFolder))
				return Environment.ExpandEnvironmentVariables(OutputFolder.Trim().Trim('"'));

			return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "AtasHistoryExport");
		}

		private TimeZoneInfo ResolveZone()
		{
			return TimeZoneInfo.Local;
		}

		private static DateTime ToUtc(DateTime platformTime, TimeZoneInfo zone)
		{
			if (platformTime.Kind == DateTimeKind.Utc)
				return platformTime;

			var unspecified = DateTime.SpecifyKind(platformTime, DateTimeKind.Unspecified);

			try
			{
				return DateTime.SpecifyKind(unspecified - zone.GetUtcOffset(unspecified), DateTimeKind.Utc);
			}
			catch (Exception)
			{
				return DateTime.SpecifyKind(unspecified, DateTimeKind.Utc);
			}
		}

		private static string Fmt(DateTime value)
		{
			return value.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
		}

		private static string Fmt(decimal value)
		{
			return value.ToString("0.####", CultureInfo.InvariantCulture);
		}
	}
}
