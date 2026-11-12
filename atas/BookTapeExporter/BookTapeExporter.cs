using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using ATAS.Indicators;
using Utils.Common.Logging;
using MarketByOrder = ATAS.DataFeedsCore.MarketByOrder;
using MboUpdateType = ATAS.DataFeedsCore.MarketByOrderUpdateTypes;
using MboSide = ATAS.DataFeedsCore.MarketDataType;

namespace Atas.Indicators.BookTape
{
	/// <summary>
	/// Book &amp; Tape Exporter.
	///
	/// Dumps every order-event stream the platform exposes to daily CSV files, so a signal can be studied
	/// against engine-level data instead of a chart picture. Streams:
	///
	///   trades    - every print, with the ids of the aggressing and the resting order
	///   sweeps    - each print paired with the resting order it consumed
	///   mbo       - every add / change / delete of an individual resting order
	///   dom       - depth: every best-bid/ask change plus throttled book snapshots
	///   structure - one row per bar: OHLCV, bid/ask split, delta, delta extrema, OI, POC
	///
	/// Trades reach back a month or more: one platform request may not exceed 7 days, so requests are
	/// chained until the whole window has been walked. MBO and depth are live-only - the platform keeps
	/// no history for them - which is stated in the log and in _status.csv rather than silently skipped.
	/// </summary>
	[DisplayName("Book & Tape Exporter")]
	[Category("Order Flow")]
	public class BookTapeExporter : Indicator
	{
		/// <summary>One request may not exceed 7 days; 6.5 leaves room for clock skew at the boundary.</summary>
		private static readonly TimeSpan MaxChunk = TimeSpan.FromDays(6.5);

		private const string TradesHeader =
			"ts_utc,ts_local,sym,price,size,dir,o_agg,o_rest,oi";

		private const string SweepsHeader =
			"ts_utc,ts_local,sym,aggr_side,rest_side,price_rest,size,o_agg,o_rest";

		private const string MboHeader =
			"ts_utc,ts_local,src_ns,lat_ms,sym,type,side,price,size,prio,oid,reason,flags,snapshot";

		private const string DepthHeader =
			"ts_utc,ts_local,sym,best_bid,bid_sz,best_ask,ask_sz,spread_ticks," +
			"tot_bid_sz,tot_ask_sz,lvl_price,lvl_side,lvl_size,is_bbo";

		private const string StructureHeader =
			"ts_utc,ts_local,sym,bar,open,high,low,close,vol,bid_vol,ask_vol,between,delta," +
			"max_delta,min_delta,ticks,oi,poc,poc_vol";

		private const string StatusHeader =
			"ts_utc,stage,detail,trades,sweeps,mbo,dom,structure,queued,dropped,write_errors," +
			"backfill_start,backfill_end,backfill_cursor,backfill_chunks,backfill_rows";

		#region Fields

		private CsvSink? _tradesSink;
		private CsvSink? _sweepsSink;
		private CsvSink? _mboSink;
		private CsvSink? _domSink;
		private CsvSink? _structureSink;

		private string _activeFolder = string.Empty;
		private string _symbol = "?";
		private TimeZoneInfo? _platformZone;
		private string _platformZoneId = string.Empty;

		// Resting orders last seen, used to name the order a print consumed.
		private readonly Dictionary<long, RestingOrder> _resting = new();
		private const int MaxRestingTracked = 200_000;

		// Live tape bookkeeping: backfill must never rewrite what the live path already wrote.
		private DateTime _lastLiveTradeUtc = DateTime.MinValue;

		// Bar-level dedupe across recalculation.
		private DateTime _lastStructureTime = DateTime.MinValue;
		private DateTime _lastLiveStructureTime = DateTime.MinValue;

		// Depth state.
		private DateTime _lastDomWriteUtc = DateTime.MinValue;
		private DateTime _lastStatusUtc = DateTime.MinValue;
		private decimal _bestBidPrice;
		private decimal _bestBidSize;
		private decimal _bestAskPrice;
		private decimal _bestAskSize;

		// MBO state.
		private bool _mboRequested;
		private bool _mboSubscribed;
		private long _mboRows;

		// Backfill state machine.
		private bool _bfStarted;
		private bool _bfActive;
		private bool _bfAwaiting;
		private DateTime _bfEndUtc;
		private DateTime _bfCursorUtc;
		private DateTime _bfWindowStartUtc;
		private int _bfChunksIssued;
		private int _bfChunksTotal;
		private long _bfRows;
		private CumulativeTradesRequest? _bfRequest;

		private readonly HashSet<string> _recentKeys = new();
		private readonly Queue<string> _recentOrder = new();

		private readonly struct RestingOrder
		{
			public RestingOrder(decimal price, bool isBid)
			{
				Price = price;
				IsBid = isBid;
			}

			public decimal Price { get; }

			public bool IsBid { get; }
		}

		#endregion

		#region Parameters

		[Display(Name = "Output folder", GroupName = "Output", Order = 10)]
		[Description("Directory for the CSV files, created if missing. Empty = Documents\\BookTapeExporter.")]
		public string OutputFolder { get; set; } = string.Empty;

		[Display(Name = "Session time zone", GroupName = "Output", Order = 20)]
		[Description("Windows or IANA zone id for ts_local and for splitting files by session (Central Standard Time / America/Chicago).")]
		public string SessionTimeZoneId { get; set; } = "Central Standard Time";

		[Display(Name = "Platform time zone", GroupName = "Output", Order = 25)]
		[Description("Zone the platform displays times in, used to convert them to UTC. Empty = this computer's local zone, which is correct for a normal install. Windows or IANA id.")]
		public string PlatformTimeZoneId { get; set; } = string.Empty;

		[Display(Name = "Session roll hour", GroupName = "Output", Order = 30)]
		[Description("Local hour at which a new trading day starts, so a Globex session is not split at midnight. 17 for the 17:00 open.")]
		public int SessionRollHour { get; set; } = 17;

		[Display(Name = "Max rows per file", GroupName = "Output", Order = 40)]
		[Description("A stream exceeding this many rows in one session continues in _p1, _p2... so a month of ticks stays openable.")]
		public int MaxRowsPerFile { get; set; } = 4_000_000;

		[Display(Name = "Write trades", GroupName = "Streams", Order = 50)]
		public bool WriteTrades { get; set; } = true;

		[Display(Name = "Write sweeps", GroupName = "Streams", Order = 60)]
		[Description("Print joined to the resting order it hit. Needs an MBO feed; empty otherwise.")]
		public bool WriteSweeps { get; set; } = true;

		[Display(Name = "Write MBO", GroupName = "Streams", Order = 70)]
		[Description("Every order add/change/delete. Live only: the platform keeps no MBO history.")]
		public bool WriteMbo { get; set; } = true;

		[Display(Name = "Write depth", GroupName = "Streams", Order = 80)]
		[Description("Best bid/ask changes and book snapshots. Live only.")]
		public bool WriteDepth { get; set; } = true;

		[Display(Name = "Write bar structure", GroupName = "Streams", Order = 90)]
		[Description("One row per bar: OHLCV, bid/ask volume, delta, delta extrema, OI, POC.")]
		public bool WriteStructure { get; set; } = true;

		[Display(Name = "Depth snapshot interval (ms)", GroupName = "Depth", Order = 100)]
		[Description("Minimum gap between book snapshots. Best bid/ask changes are always written. 0 = snapshot every change.")]
		public int DepthIntervalMs { get; set; } = 250;

		[Display(Name = "Backfill trades (days)", GroupName = "Backfill", Order = 110)]
		[Description("How far back to pull the trade tape on start. Chained in <=6.5-day requests, so 30 / 45 / 90 all work. 0 disables.")]
		public int BackfillDays { get; set; } = 45;

		#endregion

		public BookTapeExporter()
		{
			DenyToChangePanel = true;
			DataSeries[0].IsHidden = true;
		}

		#region Lifecycle

		protected override void OnInitialize()
		{
			base.OnInitialize();

			var folder = ResolveFolder();
			_symbol = InstrumentInfo?.Instrument ?? "?";
			_platformZone = ResolvePlatformZone();

			if (!string.Equals(folder, _activeFolder, StringComparison.Ordinal))
			{
				DisposeSinks();
				_activeFolder = folder;

				if (WriteTrades)
					_tradesSink = MakeSink("trades", TradesHeader, folder);

				if (WriteSweeps)
					_sweepsSink = MakeSink("sweeps", SweepsHeader, folder);

				if (WriteMbo)
					_mboSink = MakeSink("mbo", MboHeader, folder);

				if (WriteDepth)
					_domSink = MakeSink("dom", DepthHeader, folder);

				if (WriteStructure)
					_structureSink = MakeSink("structure", StructureHeader, folder);
			}

			this.LogInfo("Book & Tape Exporter: {0} -> {1}", _symbol, folder);

			if (WriteMbo && !_mboRequested)
			{
				_mboRequested = true;
				_ = SubscribeMboAsync();
			}
		}

		private CsvSink MakeSink(string name, string header, string folder)
		{
			return new CsvSink(folder, name, header, SessionTimeZoneId, SessionRollHour, MaxRowsPerFile);
		}

		private TimeZoneInfo? ResolvePlatformZone()
		{
			_platformZoneId = string.IsNullOrWhiteSpace(PlatformTimeZoneId) ? string.Empty : PlatformTimeZoneId.Trim();

			if (_platformZoneId.Length == 0)
				return TimeZoneInfo.Local;

			try
			{
				return TimeZoneInfo.FindSystemTimeZoneById(_platformZoneId);
			}
			catch (Exception)
			{
				this.LogWarn("Book & Tape Exporter: platform time zone '{0}' is not known on this machine; " +
					"falling back to the local zone. Times may be offset.", _platformZoneId);

				return TimeZoneInfo.Local;
			}
		}

		private string ResolveFolder()
		{
			if (!string.IsNullOrWhiteSpace(OutputFolder))
				return Environment.ExpandEnvironmentVariables(OutputFolder.Trim().Trim('"'));

			var docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
			return Path.Combine(docs, "BookTapeExporter");
		}

		private async System.Threading.Tasks.Task SubscribeMboAsync()
		{
			try
			{
				await SubscribeMarketByOrderData();

				_mboSubscribed = true;

				var snapshotCount = MarketByOrders?.Count() ?? 0;
				this.LogInfo("Book & Tape Exporter: MBO subscription active, {0} resting orders in the snapshot.", snapshotCount);
				WriteStatus("mbo", "subscribed");
			}
			catch (Exception e)
			{
				this.LogError("Book & Tape Exporter: MBO subscription failed. This feed most likely publishes no " +
					"order-level data, so mbo.csv and sweeps.csv stay empty; trades, dom and structure are unaffected.", e);

				WriteStatus("mbo", "subscribe-failed");
			}
		}

		private void DisposeSinks()
		{
			_tradesSink?.Dispose();
			_sweepsSink?.Dispose();
			_mboSink?.Dispose();
			_domSink?.Dispose();
			_structureSink?.Dispose();

			_tradesSink = null;
			_sweepsSink = null;
			_mboSink = null;
			_domSink = null;
			_structureSink = null;
		}

		#endregion

		#region Live tape

		protected override void OnNewTrade(MarketDataArg arg)
		{
			if (arg is null)
				return;

			_lastLiveTradeUtc = MaxUtc(_lastLiveTradeUtc, ToUtc(arg.Time));
			WriteTradeRow(arg, isBackfill: false);
		}

		protected override void OnCumulativeTrade(CumulativeTrade trade)
		{
			WriteCumulativeTicks(trade);
		}

		protected override void OnUpdateCumulativeTrade(CumulativeTrade trade)
		{
			// A growing group re-reports all of its ticks; the dedupe filter keeps only the new ones.
			WriteCumulativeTicks(trade);
		}

		private void WriteCumulativeTicks(CumulativeTrade trade)
		{
			if (trade is null)
				return;

			if (trade.Ticks is null || trade.Ticks.Count == 0)
			{
				WriteTradeRow(
					new MarketDataArg
					{
						Price = trade.Lastprice,
						Volume = trade.Volume,
						Time = trade.Time,
						Direction = trade.Direction
					},
					isBackfill: false);

				return;
			}

			foreach (var tick in trade.Ticks)
			{
				if (tick is not null)
					WriteTradeRow(tick, isBackfill: false);
			}
		}

		private bool Dedupe(DateTime utc, decimal price, decimal volume)
		{
			var key = utc.Ticks.ToString(CultureInfo.InvariantCulture) + '|'
				+ price.ToString(CultureInfo.InvariantCulture) + '|'
				+ volume.ToString(CultureInfo.InvariantCulture);

			if (_recentKeys.Contains(key))
				return false;

			_recentKeys.Add(key);
			_recentOrder.Enqueue(key);

			while (_recentOrder.Count > 200_000)
				_recentKeys.Remove(_recentOrder.Dequeue());

			return true;
		}

		private void WriteTradeRow(MarketDataArg arg, bool isBackfill)
		{
			var sink = _tradesSink;

			if (sink is null)
				return;

			var utc = ToUtc(arg.Time);

			if (isBackfill)
			{
				// Backfill never rewrites what the live tape has already written.
				if (utc >= _lastLiveTradeUtc || utc >= _bfEndUtc)
					return;
			}
			else if (!Dedupe(utc, arg.Price, arg.Volume))
			{
				return;
			}

			var (session, dayKey) = sink.Localise(utc);

			if (isBackfill)
				_bfRows++;

			var sb = new StringBuilder(96);
			sb.Append(CsvSink.Fmt(utc)).Append(',')
				.Append(CsvSink.Fmt(session)).Append(',')
				.Append(_symbol).Append(',')
				.Append(CsvSink.Fmt(arg.Price)).Append(',')
				.Append(CsvSink.Fmt(arg.Volume)).Append(',')
				.Append(DirectionCode(arg.Direction)).Append(',')
				.Append(IdText(arg.AggressorExchangeOrderId)).Append(',')
				.Append(IdText(arg.ExchangeOrderId)).Append(',')
				.Append(CsvSink.Fmt(arg.OpenInterest));

			sink.Enqueue(dayKey, sb.ToString());

			if (!isBackfill && WriteSweeps && arg.ExchangeOrderId is long restingId)
				EmitSweep(arg, restingId);
		}

		private static string IdText(long? id)
		{
			return id?.ToString(CultureInfo.InvariantCulture) ?? string.Empty;
		}

		/// <summary>
		/// A sweep row pairs a print with the resting order it consumed. That pairing separates "one
		/// aggressor ate one large resting order and price held" from "the same volume was spread over
		/// many orders" - the distinction absorption arguments actually rest on.
		/// </summary>
		private void EmitSweep(MarketDataArg arg, long restingId)
		{
			var sink = _sweepsSink;

			if (sink is null)
				return;

			_resting.TryGetValue(restingId, out var resting);

			var utc = ToUtc(arg.Time);
			var (session, dayKey) = sink.Localise(utc);

			var sb = new StringBuilder(96);
			sb.Append(CsvSink.Fmt(utc)).Append(',')
				.Append(CsvSink.Fmt(session)).Append(',')
				.Append(_symbol).Append(',')
				.Append(DirectionCode(arg.Direction)).Append(',')
				.Append(resting.IsBid ? "B" : "A").Append(',')
				.Append(resting.Price == 0 ? string.Empty : CsvSink.Fmt(resting.Price)).Append(',')
				.Append(CsvSink.Fmt(arg.Volume)).Append(',')
				.Append(IdText(arg.AggressorExchangeOrderId)).Append(',')
				.Append(restingId.ToString(CultureInfo.InvariantCulture));

			sink.Enqueue(dayKey, sb.ToString());
		}

		private static string DirectionCode(TradeDirection direction)
		{
			return direction switch
			{
				TradeDirection.Buy => "B",
				TradeDirection.Sell => "S",
				_ => "?"
			};
		}

		#endregion

		#region MBO

		protected override void OnMarketByOrdersChanged(IEnumerable<MarketByOrder> values)
		{
			if (values is null)
				return;

			foreach (var order in values)
			{
				if (order is null)
					continue;

				TrackRestingOrder(order);

				var sink = _mboSink;

				if (sink is null)
					continue;

				var hasSourceTime = order.SourceTimeNs > 0;

				var utc = hasSourceTime
					? DateTime.UnixEpoch.AddTicks(order.SourceTimeNs / 100)
					: ToUtc(order.Time);

				var (session, dayKey) = sink.Localise(utc);

				var latencyMs = double.NaN;

				if (hasSourceTime && order.ReceivedTime != default)
					latencyMs = (ToUtc(order.ReceivedTime) - utc).TotalMilliseconds;

				var sb = new StringBuilder(128);
				sb.Append(CsvSink.Fmt(utc)).Append(',')
					.Append(CsvSink.Fmt(session)).Append(',')
					.Append(order.SourceTimeNs.ToString(CultureInfo.InvariantCulture)).Append(',')
					.Append(double.IsNaN(latencyMs) ? string.Empty : latencyMs.ToString("0.#", CultureInfo.InvariantCulture)).Append(',')
					.Append(_symbol).Append(',')
					.Append(UpdateTypeCode(order.Type)).Append(',')
					.Append(order.Side == MboSide.Bid ? "B" : "A").Append(',')
					.Append(CsvSink.Fmt(order.Price)).Append(',')
					.Append(CsvSink.Fmt(order.Volume)).Append(',')
					.Append(order.Priority.ToString(CultureInfo.InvariantCulture)).Append(',')
					.Append(order.ExchangeOrderId.ToString(CultureInfo.InvariantCulture)).Append(',')
					.Append(order.Reason.ToString()).Append(',')
					.Append(((int)order.Flags).ToString(CultureInfo.InvariantCulture)).Append(',')
					.Append(order.Type == MboUpdateType.Snapshot ? "1" : "0");

				sink.Enqueue(dayKey, sb.ToString());
				_mboRows++;
			}
		}

		private void TrackRestingOrder(MarketByOrder order)
		{
			switch (order.Type)
			{
				case MboUpdateType.Delete:
					_resting.Remove(order.ExchangeOrderId);
					break;

				case MboUpdateType.New:
				case MboUpdateType.Change:
				case MboUpdateType.Snapshot:
					if (_resting.Count > MaxRestingTracked)
						_resting.Clear();

					_resting[order.ExchangeOrderId] = new RestingOrder(
						order.Price,
						order.Side == MboSide.Bid);
					break;
			}
		}

		private static string UpdateTypeCode(MboUpdateType type)
		{
			return type switch
			{
				MboUpdateType.Snapshot => "S",
				MboUpdateType.New => "N",
				MboUpdateType.Change => "C",
				MboUpdateType.Delete => "D",
				_ => "?"
			};
		}

		#endregion

		#region Depth

		protected override void MarketDepthChanged(MarketDataArg depth)
		{
			var sink = _domSink;

			if (sink is null || depth is null)
				return;

			var utc = ToUtc(depth.Time);

			if (DepthIntervalMs > 0 && (utc - _lastDomWriteUtc).TotalMilliseconds < DepthIntervalMs)
				return;

			_lastDomWriteUtc = utc;
			RefreshBook();

			WriteDepthRow(
				sink,
				utc,
				isBbo: false,
				levelPrice: depth.Price,
				levelSide: depth.IsBid ? "B" : "A",
				levelSize: depth.Volume);
		}

		protected override void OnBestBidAskChanged(MarketDataArg depth)
		{
			var sink = _domSink;

			if (sink is null || depth is null)
				return;

			// A BBO change is a discrete event, not a re-sample, so it is never throttled away.
			if (depth.IsBid)
			{
				_bestBidPrice = depth.Price;
				_bestBidSize = depth.Volume;
			}
			else
			{
				_bestAskPrice = depth.Price;
				_bestAskSize = depth.Volume;
			}

			WriteDepthRow(
				sink,
				ToUtc(depth.Time),
				isBbo: true,
				levelPrice: depth.Price,
				levelSide: depth.IsBid ? "B" : "A",
				levelSize: depth.Volume);
		}

		/// <summary>
		/// Derives best bid/ask from the DOM snapshot. The provider exposes cumulative side totals and the
		/// levels themselves, not a ready-made BBO, so the extremes are found by scanning the levels.
		/// </summary>
		private void RefreshBook()
		{
			var info = MarketDepthInfo;

			if (info is null)
				return;

			var bestBid = 0m;
			var bestBidSize = 0m;
			var bestAsk = 0m;
			var bestAskSize = 0m;

			try
			{
				foreach (var level in info.GetMarketDepthSnapshot())
				{
					if (level is null || level.Volume <= 0)
						continue;

					if (level.IsBid)
					{
						if (level.Price > bestBid)
						{
							bestBid = level.Price;
							bestBidSize = level.Volume;
						}
					}
					else if (bestAsk == 0 || level.Price < bestAsk)
					{
						bestAsk = level.Price;
						bestAskSize = level.Volume;
					}
				}
			}
			catch (Exception)
			{
				return;
			}

			if (bestBid > 0)
			{
				_bestBidPrice = bestBid;
				_bestBidSize = bestBidSize;
			}

			if (bestAsk > 0)
			{
				_bestAskPrice = bestAsk;
				_bestAskSize = bestAskSize;
			}
		}

		private void WriteDepthRow(
			CsvSink sink,
			DateTime utc,
			bool isBbo,
			decimal levelPrice,
			string levelSide,
			decimal levelSize)
		{
			var tick = InstrumentInfo?.TickSize ?? 0m;
			var spreadTicks = _bestBidPrice > 0 && _bestAskPrice > 0 && tick > 0
				? (_bestAskPrice - _bestBidPrice) / tick
				: 0m;

			var totalBid = 0m;
			var totalAsk = 0m;

			try
			{
				var info = MarketDepthInfo;

				if (info is not null)
				{
					totalBid = info.CumulativeDomBids;
					totalAsk = info.CumulativeDomAsks;
				}
			}
			catch (Exception)
			{
				// Leave the totals at zero rather than losing the row.
			}

			var (session, dayKey) = sink.Localise(utc);

			var sb = new StringBuilder(128);
			sb.Append(CsvSink.Fmt(utc)).Append(',')
				.Append(CsvSink.Fmt(session)).Append(',')
				.Append(_symbol).Append(',')
				.Append(CsvSink.Fmt(_bestBidPrice)).Append(',')
				.Append(CsvSink.Fmt(_bestBidSize)).Append(',')
				.Append(CsvSink.Fmt(_bestAskPrice)).Append(',')
				.Append(CsvSink.Fmt(_bestAskSize)).Append(',')
				.Append(CsvSink.Fmt(spreadTicks)).Append(',')
				.Append(CsvSink.Fmt(totalBid)).Append(',')
				.Append(CsvSink.Fmt(totalAsk)).Append(',')
				.Append(CsvSink.Fmt(levelPrice)).Append(',')
				.Append(levelSide).Append(',')
				.Append(CsvSink.Fmt(levelSize)).Append(',')
				.Append(isBbo ? "1" : "0");

			sink.Enqueue(dayKey, sb.ToString());
		}

		#endregion

		#region Bar structure

		protected override void OnCalculate(int bar, decimal value)
		{
			PumpBackfill();
			WriteStructureRow(bar);
			WriteStatusIfDue();
		}

		private void WriteStructureRow(int bar)
		{
			var sink = _structureSink;

			if (sink is null || bar < 0 || bar >= CurrentBar)
				return;

			var candle = GetCandle(bar);
			var isLive = bar == CurrentBar - 1;

			// Historical bars are written once; the live bar at most once per second.
			if (isLive)
			{
				if (candle.LastTime == _lastLiveStructureTime)
					return;

				_lastLiveStructureTime = candle.LastTime;
			}
			else
			{
				if (candle.LastTime <= _lastStructureTime)
					return;

				_lastStructureTime = candle.LastTime;
			}

			var poc = candle.MaxVolumePriceInfo;
			var candleUtc = ToUtc(candle.LastTime);
			var (session, dayKey) = sink.Localise(candleUtc);

			var sb = new StringBuilder(176);
			sb.Append(CsvSink.Fmt(candleUtc)).Append(',')
				.Append(CsvSink.Fmt(session)).Append(',')
				.Append(_symbol).Append(',')
				.Append(bar.ToString(CultureInfo.InvariantCulture)).Append(',')
				.Append(CsvSink.Fmt(candle.Open)).Append(',')
				.Append(CsvSink.Fmt(candle.High)).Append(',')
				.Append(CsvSink.Fmt(candle.Low)).Append(',')
				.Append(CsvSink.Fmt(candle.Close)).Append(',')
				.Append(CsvSink.Fmt(candle.Volume)).Append(',')
				.Append(CsvSink.Fmt(candle.Bid)).Append(',')
				.Append(CsvSink.Fmt(candle.Ask)).Append(',')
				.Append(CsvSink.Fmt(candle.Betweens)).Append(',')
				.Append(CsvSink.Fmt(candle.Delta)).Append(',')
				.Append(CsvSink.Fmt(candle.MaxDelta)).Append(',')
				.Append(CsvSink.Fmt(candle.MinDelta)).Append(',')
				.Append(CsvSink.Fmt(candle.Ticks)).Append(',')
				.Append(CsvSink.Fmt(candle.OI)).Append(',')
				.Append(poc is null ? string.Empty : CsvSink.Fmt(poc.Price)).Append(',')
				.Append(poc is null ? string.Empty : CsvSink.Fmt(poc.Volume));

			sink.Enqueue(dayKey, sb.ToString());
		}

		#endregion

		#region Backfill

		/// <summary>
		/// Walks the window in &lt;=7-day chunks: the platform refuses a longer period and allows only one
		/// outstanding request, so the loop is request -> wait -> advance cursor -> repeat. That chaining
		/// is what makes 30, 45 or 90 days possible; a single request cannot do it.
		/// </summary>
		protected override void OnFinishRecalculate()
		{
			if (_bfStarted || BackfillDays <= 0 || CurrentBar <= 0 || _tradesSink is null)
				return;

			_bfStarted = true;

			var lastCandle = GetCandle(CurrentBar - 1);

			_bfEndUtc = ToUtc(lastCandle.LastTime);
			_bfWindowStartUtc = _bfEndUtc.AddDays(-BackfillDays);
			_bfCursorUtc = _bfWindowStartUtc;
			_bfActive = true;
			_bfChunksTotal = Math.Max(1, (int)Math.Ceiling(BackfillDays / MaxChunk.TotalDays));

			this.LogInfo("Book & Tape Exporter: backfilling {0} days of trades in {1} chained requests ({2:yyyy-MM-dd} to {3:yyyy-MM-dd}).",
				BackfillDays, _bfChunksTotal, _bfWindowStartUtc, _bfEndUtc);

			WriteStatus("backfill", "started");
		}

		private void PumpBackfill()
		{
			if (!_bfActive || _bfAwaiting)
				return;

			if (_bfCursorUtc >= _bfEndUtc)
			{
				_bfActive = false;
				this.LogInfo("Book & Tape Exporter: backfill finished, {0:N0} trade rows over {1} days.",
					_bfRows, BackfillDays);
				WriteStatus("backfill", "done");
				return;
			}

			var chunkEnd = _bfCursorUtc.Add(MaxChunk);

			if (chunkEnd > _bfEndUtc)
				chunkEnd = _bfEndUtc;

			try
			{
				_bfRequest = new CumulativeTradesRequest(_bfCursorUtc, chunkEnd, 0, 0);
				_bfAwaiting = true;
				_bfChunksIssued++;

				RequestForCumulativeTrades(_bfRequest);

				WriteStatus("backfill",
					$"chunk {_bfChunksIssued}/{_bfChunksTotal} {_bfCursorUtc:yyyy-MM-dd}");
			}
			catch (Exception e)
			{
				_bfAwaiting = false;
				_bfActive = false;

				this.LogError("Book & Tape Exporter: backfill request failed at " +
					$"{_bfCursorUtc:yyyy-MM-dd HH:mm}. Backfill stops here; live capture continues.", e);

				WriteStatus("backfill", "failed");
			}
		}

		protected override void OnCumulativeTradesResponse(
			CumulativeTradesRequest request,
			IEnumerable<CumulativeTrade> cumulativeTrades)
		{
			if (_bfRequest is null || !ReferenceEquals(request, _bfRequest))
				return;

			var chunkStart = _bfRequest.BeginTime;
			var chunkEnd = _bfRequest.EndTime;
			var rows = 0L;

			try
			{
				foreach (var trade in cumulativeTrades)
				{
					if (trade?.Ticks is null)
						continue;

					foreach (var tick in trade.Ticks)
					{
						if (tick is null)
							continue;

						var utc = ToUtc(tick.Time);

						// Half-open interval: the chunk end belongs to the next chunk.
						if (utc < chunkStart || utc >= chunkEnd)
							continue;

						WriteTradeRow(tick, isBackfill: true);
						rows++;
					}
				}
			}
			catch (Exception e)
			{
				this.LogError($"Book & Tape Exporter: backfill rows for {chunkStart:yyyy-MM-dd} failed.", e);
			}

			_bfCursorUtc = chunkEnd;
			_bfAwaiting = false;
			_bfRequest = null;

			// The cursor only moves forward, which guarantees the chain terminates even if the provider
			// returns an empty chunk.
			if (_bfCursorUtc <= chunkStart)
				_bfCursorUtc = chunkStart.AddTicks(1);

			this.LogInfo("Book & Tape Exporter: chunk done at {0:yyyy-MM-dd}: {1:N0} rows (total {2:N0}).",
				chunkEnd, rows, _bfRows);
		}

		#endregion

		#region Status

		private void WriteStatusIfDue()
		{
			var now = DateTime.UtcNow;

			if ((now - _lastStatusUtc).TotalMilliseconds < 2_000)
				return;

			_lastStatusUtc = now;

			var stage = _bfActive ? "backfill" : "live";

			var detail = _bfActive
				? $"{_bfChunksIssued}/{_bfChunksTotal} {_bfCursorUtc:yyyy-MM-dd}"
				: _mboSubscribed ? "mbo-ok" : "no-mbo";

			WriteStatus(stage, detail);
		}

		private void WriteStatus(string stage, string detail)
		{
			try
			{
				if (string.IsNullOrEmpty(_activeFolder))
					return;

				Directory.CreateDirectory(_activeFolder);

				var sb = new StringBuilder(256);
				sb.Append(CsvSink.Fmt(DateTime.UtcNow)).Append(',')
					.Append(stage).Append(',')
					.Append(detail.Replace(',', ';')).Append(',')
					.Append(_tradesSink?.WrittenRows ?? 0).Append(',')
					.Append(_sweepsSink?.WrittenRows ?? 0).Append(',')
					.Append(_mboRows).Append(',')
					.Append(_domSink?.WrittenRows ?? 0).Append(',')
					.Append(_structureSink?.WrittenRows ?? 0).Append(',')
					.Append(QueueDepth()).Append(',')
					.Append(DroppedRows()).Append(',')
					.Append(WriteErrors()).Append(',')
					.Append(_bfWindowStartUtc == DateTime.MinValue ? string.Empty : _bfWindowStartUtc.ToString("yyyy-MM-dd")).Append(',')
					.Append(_bfEndUtc == DateTime.MinValue ? string.Empty : _bfEndUtc.ToString("yyyy-MM-dd")).Append(',')
					.Append(_bfCursorUtc == DateTime.MinValue ? string.Empty : _bfCursorUtc.ToString("yyyy-MM-dd")).Append(',')
					.Append(_bfChunksIssued).Append(',')
					.Append(_bfRows);

				var path = Path.Combine(_activeFolder, "_status.csv");
				var isNew = !File.Exists(path);

				using var writer = new StreamWriter(path, append: true);

				if (isNew)
					writer.WriteLine(StatusHeader);

				writer.WriteLine(sb.ToString());
			}
			catch (Exception)
			{
				// A status write must never take the exporter down.
			}
		}

		private long QueueDepth()
		{
			return (_tradesSink?.QueuedRows ?? 0)
				+ (_sweepsSink?.QueuedRows ?? 0)
				+ (_mboSink?.QueuedRows ?? 0)
				+ (_domSink?.QueuedRows ?? 0)
				+ (_structureSink?.QueuedRows ?? 0);
		}

		private long DroppedRows()
		{
			return (_tradesSink?.DroppedRows ?? 0)
				+ (_sweepsSink?.DroppedRows ?? 0)
				+ (_mboSink?.DroppedRows ?? 0)
				+ (_domSink?.DroppedRows ?? 0)
				+ (_structureSink?.DroppedRows ?? 0);
		}

		private long WriteErrors()
		{
			return (_tradesSink?.WriteErrors ?? 0)
				+ (_sweepsSink?.WriteErrors ?? 0)
				+ (_mboSink?.WriteErrors ?? 0)
				+ (_domSink?.WriteErrors ?? 0)
				+ (_structureSink?.WriteErrors ?? 0);
		}

		#endregion

		#region Time helpers

		/// <summary>
		/// Converts a platform timestamp to UTC.
		/// </summary>
		/// <remarks>
		/// The platform hands over wall-clock time with Kind=Unspecified, in whatever zone the user has the
		/// platform set to. Two things are deliberately not done here:
		///
		/// - It does not assume the clock is UTC. That would shift every row by the broker offset and put
		///   the whole dataset hours away from the chart times the signals are quoted in.
		/// - It does not use a fixed offset. A fixed -6 would be wrong for the whole of daylight time, so the
		///   offset is resolved per timestamp through the time zone database, which applies DST correctly.
		/// </remarks>
		private DateTime ToUtc(DateTime time)
		{
			if (time.Kind == DateTimeKind.Utc)
				return time;

			var zone = _platformZone ?? TimeZoneInfo.Local;
			var unspecified = DateTime.SpecifyKind(time, DateTimeKind.Unspecified);

			try
			{
				return DateTime.SpecifyKind(unspecified - zone.GetUtcOffset(unspecified), DateTimeKind.Utc);
			}
			catch (Exception)
			{
				// A zone database failure must not stop the capture; fall back to UTC rather than dropping rows.
				return DateTime.SpecifyKind(unspecified, DateTimeKind.Utc);
			}
		}

		private static DateTime MaxUtc(DateTime a, DateTime b)
		{
			return a > b ? a : b;
		}

		#endregion
	}
}
