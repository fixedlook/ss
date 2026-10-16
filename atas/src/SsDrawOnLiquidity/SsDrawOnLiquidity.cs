/*
 * SsDrawOnLiquidity - ATAS custom indicator
 * ---------------------------------------------------------------------------
 * Shows the highest-probability draw-on-liquidity target for the trade you are
 * currently in, with an empirically calibrated probability of touch (PoT).
 *
 * WHERE THE NUMBERS COME FROM
 *   The PoT table in PotTable.cs is not a guess. It was fitted on 1,460 MNQ
 *   trading days (2019-2026, ~4.3M candidate-level observations) using the
 *   first-passage labelling described in docs/FINDINGS.md. Each cell is
 *   p(target touched before your stop), with same-bar ambiguity counted as a
 *   LOSS, so the displayed probability is mildly conservative.
 *
 * HOW A TARGET IS CHOSEN
 *   The backtest was decisive on this point: at any moment only ~2.4 levels
 *   meet a realistic minimum R:R, and the level with the highest probability is
 *   simply the NEAREST qualifying one. A machine-learned ranking added nothing
 *   and lost to this rule. So the rule here is: nearest unswept liquidity that
 *   meets MinRR, with the level family's calibrated PoT shown alongside.
 *
 * Level families, all strictly "unswept" (price has not traded through them
 * since they formed):
 *   session_hl    prior-day H/L, overnight H/L, Asia H/L, London H/L, IB H/L
 *   sess_extreme  running session high/low
 *   pdc           prior-day close
 *   swing         confirmed 3-bar fractal highs/lows
 *   fvg           3-bar fair value gaps, unfilled
 *
 * IMPORTANT - session times
 *   Session boundaries are read from your chart's own bar timestamps. If your
 *   chart is not in US Eastern time, set the session parameters below to match
 *   your chart's clock or the overnight/Asia/London levels will be wrong.
 */

namespace ATAS.Indicators.Technical;

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Drawing;
using System.Linq;

using OFT.Attributes;
using OFT.Rendering.Context;
using OFT.Rendering.Tools;

using Color = System.Drawing.Color;

[DisplayName("SS Draw On Liquidity")]
[Description("Highest-probability draw-on-liquidity target with calibrated PoT.")]
[Category("SS")]
public class SsDrawOnLiquidity : Indicator
{
	#region Nested types

	public enum TradeDirectionMode
	{
		[Display(Name = "Auto (use last close)")]
		Auto = 0,

		[Display(Name = "Long")]
		Long = 1,

		[Display(Name = "Short")]
		Short = 2
	}

	/// <summary>A liquidity level that can act as a draw on price.</summary>
	private sealed class Level
	{
		public string Name = "";
		public string Group = "";

		/// <summary>The price at which this level is first touched.</summary>
		public decimal Price;

		/// <summary>Far edge, used only to draw a gap zone.</summary>
		public decimal Edge;

		/// <summary>
		/// True when this level sits ABOVE the market and draws price up.
		/// Stays valid for as long as the level is unswept, because the only way
		/// price can change side is by trading through it - which sweeps it.
		/// </summary>
		public bool IsUpper;

		public int BornBar;

		/// <summary>True once price has traded through it.</summary>
		public bool Swept;
	}

	#endregion

	#region Fields

	private int _lastCalculatedBar = -1;
	private bool _lastBarInRth;

	// --- session tracking ---------------------------------------------------
	private DateTime _currentDay = DateTime.MinValue;
	private DateTime _rthStartTime = DateTime.MinValue;
	private int _rthStartBar = -1;

	private decimal _rthHigh = decimal.MinValue, _rthLow = decimal.MaxValue;
	private decimal _rthOpen, _rthClose;
	private decimal _onHigh = decimal.MinValue, _onLow = decimal.MaxValue;
	private decimal _asiaHigh = decimal.MinValue, _asiaLow = decimal.MaxValue;
	private decimal _lonHigh = decimal.MinValue, _lonLow = decimal.MaxValue;
	private decimal _ibHigh = decimal.MinValue, _ibLow = decimal.MaxValue;

	private decimal _prevDayRthHigh = decimal.MinValue, _prevDayRthLow = decimal.MaxValue;
	private decimal _prevDayRthClose = decimal.MinValue;

	// --- running session extreme -------------------------------------------
	private decimal _sessHigh = decimal.MinValue, _sessLow = decimal.MaxValue;
	private decimal _sessHighPrev = decimal.MinValue, _sessLowPrev = decimal.MaxValue;

	// --- level store --------------------------------------------------------
	private readonly List<Level> _swings = new();
	private readonly List<Level> _fvgs = new();

	// --- current selection (rebuilt every calculation) ----------------------
	private readonly List<Level> _active = new();

	// --- rendering objects --------------------------------------------------
	private readonly RenderFont _font = new("Consolas", 10);
	private readonly RenderFont _fontSmall = new("Consolas", 9);

	private readonly RenderStringFormat _left = new()
	{
		Alignment = StringAlignment.Near,
		LineAlignment = StringAlignment.Center,
		FormatFlags = StringFormatFlags.NoWrap
	};

	#endregion

	#region Settings

	private TradeDirectionMode _directionMode = TradeDirectionMode.Auto;
	private decimal _stopPoints = 40m;
	private decimal _minRr = 1.5m;
	private int _maxTargets = 3;
	private decimal _zoneTicks = 4m;
	private int _swingStrength = 3;
	private int _maxLevels = 40;

	private TimeSpan _rthStart = new(9, 30, 0);
	private TimeSpan _rthEnd = new(16, 0, 0);
	private TimeSpan _asiaStart = new(18, 0, 0);
	private TimeSpan _londonStart = new(3, 0, 0);
	private int _ibMinutes = 60;
	private bool _showOnlyInRth = true;
	private decimal _sessionOffsetHours;
	private bool _showDiagnostics;

	private Color _targetColor = Color.FromArgb(120, 255, 90);
	private Color _zoneColor = Color.FromArgb(38, 120, 255, 90);
	private Color _textColor = Color.FromArgb(235, 235, 235);
	private Color _boxColor = Color.FromArgb(235, 16, 22, 18);

	[Display(Name = "Trade direction", GroupName = "Trade", Order = 10,
		Description = "Which side of the trade you are in. Auto uses the last close.")]
	public TradeDirectionMode DirectionMode
	{
		get => _directionMode;
		set { _directionMode = value; RecalculateValues(); }
	}

	[Display(Name = "Stop distance (points)", GroupName = "Trade", Order = 20,
		Description = "Distance from your entry to your stop. Drives the R:R and the PoT lookup.")]
	[Range(1, 100000)]
	public decimal StopPoints
	{
		get => _stopPoints;
		set { _stopPoints = value; RecalculateValues(); }
	}

	[Display(Name = "Minimum R:R", GroupName = "Trade", Order = 30,
		Description = "Targets closer than this multiple of your stop are ignored.")]
	[Range(0.1, 50)]
	public decimal MinRr
	{
		get => _minRr;
		set { _minRr = value; RecalculateValues(); }
	}

	[Display(Name = "Targets shown", GroupName = "Trade", Order = 40,
		Description = "How many ranked targets to display. 1 shows only the best.")]
	[Range(1, 5)]
	public int MaxTargets
	{
		get => _maxTargets;
		set { _maxTargets = value; RecalculateValues(); }
	}

	[Display(Name = "Zone half-width (ticks)", GroupName = "Display", Order = 110,
		Description = "Thickness of the shaded band drawn around each target level.")]
	[Range(0, 100)]
	public decimal ZoneTicks
	{
		get => _zoneTicks;
		set { _zoneTicks = value; RecalculateValues(); }
	}

	[Display(Name = "Target colour", GroupName = "Display", Order = 120)]
	public Color TargetColor
	{
		get => _targetColor;
		set { _targetColor = value; RecalculateValues(); }
	}

	[Display(Name = "Zone colour", GroupName = "Display", Order = 130)]
	public Color ZoneColor
	{
		get => _zoneColor;
		set { _zoneColor = value; RecalculateValues(); }
	}

	[Display(Name = "Text colour", GroupName = "Display", Order = 140)]
	public Color TextColor
	{
		get => _textColor;
		set { _textColor = value; RecalculateValues(); }
	}

	[Display(Name = "Swing strength", GroupName = "Detection", Order = 210,
		Description = "Bars either side required to confirm a swing high/low.")]
	[Range(1, 20)]
	public int SwingStrength
	{
		get => _swingStrength;
		set { _swingStrength = Math.Max(1, value); RecalculateValues(); }
	}

	[Display(Name = "Max stored levels", GroupName = "Detection", Order = 220,
		Description = "Cap on stored swings and gaps, for performance.")]
	[Range(5, 500)]
	public int MaxLevels
	{
		get => _maxLevels;
		set { _maxLevels = Math.Max(5, value); RecalculateValues(); }
	}

	[Display(Name = "RTH start", GroupName = "Sessions", Order = 310,
		Description = "Regular session open, in your chart's timezone.")]
	public TimeSpan RthStart
	{
		get => _rthStart;
		set { _rthStart = value; RecalculateValues(); }
	}

	[Display(Name = "RTH end", GroupName = "Sessions", Order = 320,
		Description = "Regular session close, in your chart's timezone.")]
	public TimeSpan RthEnd
	{
		get => _rthEnd;
		set { _rthEnd = value; RecalculateValues(); }
	}

	[Display(Name = "Asia start", GroupName = "Sessions", Order = 330,
		Description = "Start of the Asian session, in your chart's timezone.")]
	public TimeSpan AsiaStart
	{
		get => _asiaStart;
		set { _asiaStart = value; RecalculateValues(); }
	}

	[Display(Name = "London start", GroupName = "Sessions", Order = 340,
		Description = "Start of the London session, in your chart's timezone.")]
	public TimeSpan LondonStart
	{
		get => _londonStart;
		set { _londonStart = value; RecalculateValues(); }
	}

	[Display(Name = "Initial balance (minutes)", GroupName = "Sessions", Order = 350,
		Description = "Length of the initial balance from the RTH open.")]
	[Range(1, 600)]
	public int IbMinutes
	{
		get => _ibMinutes;
		set { _ibMinutes = Math.Max(1, value); RecalculateValues(); }
	}

	[Display(Name = "Only show during RTH", GroupName = "Sessions", Order = 360,
		Description = "The probability table was fitted on regular-hours data only. " +
			"Outside RTH the displayed probability would not be calibrated, so targets " +
			"are hidden by default. While hidden, the chart explains why.")]
	public bool ShowOnlyInRth
	{
		get => _showOnlyInRth;
		set { _showOnlyInRth = value; RecalculateValues(); }
	}

	[Display(Name = "Session offset from New York (hours)", GroupName = "Sessions", Order = 370,
		Description = "Session times above are New York times. If your chart is not on New York " +
			"time, set the difference here: London +5, Berlin/Paris/Rome +6, Dubai +8, " +
			"Singapore +12 (adjust by 1 in winter when US daylight saving differs). " +
			"Leave 0 if your chart runs on New York time.")]
	[Range(-12, 14)]
	public decimal SessionOffsetHours
	{
		get => _sessionOffsetHours;
		set { _sessionOffsetHours = value; RecalculateValues(); }
	}

	[Display(Name = "Show diagnostics", GroupName = "Sessions", Order = 380,
		Description = "Prints the detected session, level count and chosen target on the chart. " +
			"Useful when nothing appears.")]
	public bool ShowDiagnostics
	{
		get => _showDiagnostics;
		set { _showDiagnostics = value; RecalculateValues(); }
	}

	#endregion

	#region ctor

	public SsDrawOnLiquidity()
		: base(true)
	{
		DenyToChangePanel = true;
		DataSeries[0].IsHidden = true;
		((ValueDataSeries)DataSeries[0]).VisualType = VisualMode.Hide;
	}

	#endregion

	#region Calculation

	protected override void OnCalculate(int bar, decimal value)
	{
		if (bar == 0)
			ResetState();

		if (CurrentBar < 3)
			return;

		_lastCalculatedBar = bar;

		var candle = GetCandle(bar);
		var tod = candle.Time.TimeOfDay;
		var td = TradingDay(candle.Time, tod);

		if (td != _currentDay)
			RollDay(td);

		var tick = TickSize;

		// ---------------- session classification ----------------
		// session settings are New York times; shift them onto the chart clock
		var asiaStart = Shift(_asiaStart);
		var londonStart = Shift(_londonStart);
		var rthStart = Shift(_rthStart);
		var rthEnd = Shift(_rthEnd);

		var isAsia = tod >= asiaStart || tod < londonStart;
		var isLondon = tod >= londonStart && tod < rthStart;
		var isRth = tod >= rthStart && tod < rthEnd;
		// measured in wall-clock minutes so the indicator behaves identically on
		// any chart timeframe, matching how the probabilities were fitted
		var isIb = isRth && _rthStartTime != DateTime.MinValue
			&& candle.Time < _rthStartTime.AddMinutes(Math.Max(1, _ibMinutes));

		if (isRth && _rthStartBar < 0)
		{
			_rthStartBar = bar;
			_rthStartTime = candle.Time;
			_rthOpen = candle.Open;
			_sessHigh = candle.High;
			_sessLow = candle.Low;
		}

		// ---------------- accumulate session extremes ----------------
		if (isRth)
		{
			if (candle.High > _rthHigh) _rthHigh = candle.High;
			if (candle.Low < _rthLow) _rthLow = candle.Low;
			_rthClose = candle.Close;
			if (isIb)
			{
				if (candle.High > _ibHigh) _ibHigh = candle.High;
				if (candle.Low < _ibLow) _ibLow = candle.Low;
			}
		}

		if (isAsia)
		{
			if (candle.High > _asiaHigh) _asiaHigh = candle.High;
			if (candle.Low < _asiaLow) _asiaLow = candle.Low;
		}

		if (isLondon)
		{
			if (candle.High > _lonHigh) _lonHigh = candle.High;
			if (candle.Low < _lonLow) _lonLow = candle.Low;
		}

		if (isAsia || isLondon)
		{
			if (candle.High > _onHigh) _onHigh = candle.High;
			if (candle.Low < _onLow) _onLow = candle.Low;
		}

		// ---------------- maintain swing / gap stores ----------------
		UpdateSwings(bar);
		UpdateGaps(bar);

		// session extreme BEFORE this bar is the candidate level
		_sessHighPrev = _sessHigh;
		_sessLowPrev = _sessLow;
		if (isRth)
		{
			if (candle.High > _sessHigh) _sessHigh = candle.High;
			if (candle.Low < _sessLow) _sessLow = candle.Low;
		}

		_lastBarInRth = isRth;
		this[bar] = 0m;

		SelectTargets();
	}

	private DateTime TradingDay(DateTime time, TimeSpan tod)
		=> tod >= Shift(_asiaStart) ? time.Date.AddDays(1) : time.Date;

	private void RollDay(DateTime td)
	{
		// the day that just ended becomes "prior day" reference
		if (_currentDay != DateTime.MinValue && _rthHigh > decimal.MinValue)
		{
			_prevDayRthHigh = _rthHigh;
			_prevDayRthLow = _rthLow;
			_prevDayRthClose = _rthClose;
		}

		_currentDay = td;
		_rthHigh = decimal.MinValue; _rthLow = decimal.MaxValue;
		_rthOpen = 0m; _rthClose = 0m;
		_onHigh = decimal.MinValue; _onLow = decimal.MaxValue;
		_asiaHigh = decimal.MinValue; _asiaLow = decimal.MaxValue;
		_lonHigh = decimal.MinValue; _lonLow = decimal.MaxValue;
		_ibHigh = decimal.MinValue; _ibLow = decimal.MaxValue;
		_sessHigh = decimal.MinValue; _sessLow = decimal.MaxValue;
		_sessHighPrev = decimal.MinValue; _sessLowPrev = decimal.MaxValue;
		_rthStartBar = -1;
		_rthStartTime = DateTime.MinValue;
		_swings.Clear();
		_fvgs.Clear();
	}

	/// <summary>Confirms a fractal K bars back and marks swept levels.</summary>
	private void UpdateSwings(int bar)
	{
		var k = _swingStrength;
		var c = GetCandle(bar);

		// a level that price has traded through is no longer a draw
		foreach (var l in _swings)
		{
			if (l.Swept || l.BornBar >= bar)
				continue;
			if (l.IsUpper ? c.High >= l.Price : c.Low <= l.Price)
				l.Swept = true;
		}

		if (bar < 2 * k)
			return;

		var center = bar - k;
		var cc = GetCandle(center);

		var hi = decimal.MinValue;
		var lo = decimal.MaxValue;
		var rightHi = decimal.MinValue;
		var rightLo = decimal.MaxValue;

		for (var i = center - k; i <= bar; i++)
		{
			var b = GetCandle(i);
			if (b.High > hi) hi = b.High;
			if (b.Low < lo) lo = b.Low;

			if (i > center)
			{
				if (b.High > rightHi) rightHi = b.High;
				if (b.Low < rightLo) rightLo = b.Low;
			}
		}

		// the centre bar must be the extreme of its own +/- k window (only
		// knowable now that k further bars have closed) AND strictly beyond the
		// right half, which is what the fitted probabilities assume
		if (cc.High >= hi && cc.High > rightHi)
			AddLevel(new Level
			{
				Name = "SwingH", Group = "swing", Price = cc.High,
				IsUpper = true, BornBar = bar
			});

		if (cc.Low <= lo && cc.Low < rightLo)
			AddLevel(new Level
			{
				Name = "SwingL", Group = "swing", Price = cc.Low,
				IsUpper = false, BornBar = bar
			});
	}

	private void UpdateGaps(int bar)
	{
		var c = GetCandle(bar);

		// a gap is consumed as a draw once price returns to its near edge
		foreach (var g in _fvgs)
		{
			if (g.Swept || g.BornBar >= bar)
				continue;
			if (g.IsUpper ? c.High >= g.Price : c.Low <= g.Price)
				g.Swept = true;
		}

		if (bar < 2)
			return;

		var a = GetCandle(bar - 2);

		// bullish imbalance: low of the newest bar sits above the high of the
		// oldest. Near edge is the upper edge - that is what price meets first
		// on the way back down.
		if (c.Low > a.High)
			AddGap(new Level
			{
				Name = "FVG", Group = "fvg", Price = c.Low, Edge = a.High,
				IsUpper = false, BornBar = bar
			});

		// bearish imbalance: near edge is the lower edge
		if (c.High < a.Low)
			AddGap(new Level
			{
				Name = "FVG", Group = "fvg", Price = c.High, Edge = a.Low,
				IsUpper = true, BornBar = bar
			});
	}

	private void AddLevel(Level l)
	{
		_swings.Add(l);
		while (_swings.Count > _maxLevels)
			_swings.RemoveAt(0);
	}

	private void AddGap(Level l)
	{
		_fvgs.Add(l);
		while (_fvgs.Count > _maxLevels)
			_fvgs.RemoveAt(0);
	}

	private void ResetState()
	{
		_currentDay = DateTime.MinValue;
		_rthHigh = decimal.MinValue; _rthLow = decimal.MaxValue;
		_onHigh = decimal.MinValue; _onLow = decimal.MaxValue;
		_asiaHigh = decimal.MinValue; _asiaLow = decimal.MaxValue;
		_lonHigh = decimal.MinValue; _lonLow = decimal.MaxValue;
		_ibHigh = decimal.MinValue; _ibLow = decimal.MaxValue;
		_prevDayRthHigh = decimal.MinValue; _prevDayRthLow = decimal.MaxValue;
		_prevDayRthClose = decimal.MinValue;
		_sessHigh = decimal.MinValue; _sessLow = decimal.MaxValue;
		_sessHighPrev = decimal.MinValue; _sessLowPrev = decimal.MaxValue;
		_rthStartBar = -1; _rthStartTime = DateTime.MinValue;
		_swings.Clear();
		_fvgs.Clear();
		_active.Clear();
	}

	#endregion

	#region Target selection

	/// <summary>
	/// Builds the candidate list and keeps the best targets.
	///
	/// The research was unambiguous: among levels that clear the R:R floor, the
	/// NEAREST one has the highest probability of being reached. Ranking by a
	/// model instead of by distance tested strictly worse, so distance is the
	/// ranking key here.
	///
	/// Static levels are only admitted when they are still STRICTLY untouched
	/// this session. The fitted probabilities assume exactly that: a level that
	/// price has already traded through is not the same object, and admitting it
	/// would display a number the backtest never measured.
	/// </summary>
	private void SelectTargets()
	{
		_active.Clear();

		if (_lastCalculatedBar < 3 || _rthStartBar < 0)
			return;

		// the fitted numbers describe regular-hours behaviour; outside that
		// window they are not calibrated
		if (_showOnlyInRth && !_lastBarInRth)
			return;

		var tick = TickSize;
		var e = GetCandle(_lastCalculatedBar).Close;
		var stop = Math.Max(_stopPoints, tick);

		var dir = _directionMode switch
		{
			TradeDirectionMode.Long => 1,
			TradeDirectionMode.Short => -1,
			// an indicator cannot read your position, so Auto reads the side of
			// the session open; set Long/Short explicitly when in a trade
			_ => _rthOpen > 0 && e >= _rthOpen ? 1 : -1
		};

		// running RTH extremes INCLUDING the current bar: a level inside the
		// current bar's range has already been touched
		var hiSoFar = _sessHigh;
		var loSoFar = _sessLow;

		var all = new List<Level>();

		// an upper level must sit above every high printed this session
		void Upper(string name, string group, decimal price)
		{
			if (!IsReal(price))
				return;
			if (price > hiSoFar)
				all.Add(new Level { Name = name, Group = group, Price = price, IsUpper = true });
		}

		void Lower(string name, string group, decimal price)
		{
			if (!IsReal(price))
				return;
			if (price < loSoFar)
				all.Add(new Level { Name = name, Group = group, Price = price, IsUpper = false });
		}

		Upper("PDH", "session_hl", _prevDayRthHigh);
		Lower("PDL", "session_hl", _prevDayRthLow);
		Upper("ONH", "session_hl", _onHigh);
		Lower("ONL", "session_hl", _onLow);
		Upper("AsiaH", "session_hl", _asiaHigh);
		Lower("AsiaL", "session_hl", _asiaLow);
		Upper("LondonH", "session_hl", _lonHigh);
		Lower("LondonL", "session_hl", _lonLow);
		// the initial balance is not a known level until its window has closed;
		// exposing it earlier would use a range that is still forming
		if (_rthStartTime != DateTime.MinValue
			&& GetCandle(_lastCalculatedBar).Time
				>= _rthStartTime.AddMinutes(Math.Max(1, _ibMinutes)))
		{
			Upper("IBH", "session_hl", _ibHigh);
			Lower("IBL", "session_hl", _ibLow);
		}

		// running session extreme, excluding the current bar
		if (_sessHighPrev > hiSoFar)
			all.Add(new Level
			{
				Name = "SessionH", Group = "sess_extreme",
				Price = _sessHighPrev, IsUpper = true
			});
		if (_sessLowPrev < loSoFar)
			all.Add(new Level
			{
				Name = "SessionL", Group = "sess_extreme",
				Price = _sessLowPrev, IsUpper = false
			});

		// prior-day close sits on whichever side it happens to be
		if (IsReal(_prevDayRthClose))
		{
			if (_prevDayRthClose > hiSoFar)
				all.Add(new Level
				{
					Name = "PDC", Group = "pdc",
					Price = _prevDayRthClose, IsUpper = true
				});
			else if (_prevDayRthClose < loSoFar)
				all.Add(new Level
				{
					Name = "PDC", Group = "pdc",
					Price = _prevDayRthClose, IsUpper = false
				});
		}

		foreach (var l in _swings)
			if (!l.Swept && l.BornBar < _lastCalculatedBar)
				all.Add(l);

		foreach (var g in _fvgs)
			if (!g.Swept && g.BornBar < _lastCalculatedBar)
				all.Add(g);

		// ---------------- keep the nearest qualifying levels ----------------
		var cands = all
			.Where(l => dir > 0 ? l.Price > e + tick : l.Price < e - tick)
			.Where(l => Math.Abs(l.Price - e) / stop >= _minRr)
			.OrderBy(l => Math.Abs(l.Price - e))
			.Take(_maxTargets);

		foreach (var l in cands)
			_active.Add(l);
	}

	/// <summary>Probability of touch for a target, read from the fitted table.</summary>
	private float Pot(Level level, decimal dist)
	{
		var gi = PotTable.GroupIndex(level.Group);
		if (gi < 0)
			return 0.25f;

		var si = PotTable.StopIndex(_stopPoints);
		var ri = PotTable.RrIndex(dist / Math.Max(_stopPoints, 0.25m));
		var ti = TimeBucket();

		return PotTable.Pot[gi, si, ri, ti];
	}

	/// <summary>
	/// Which third of the RTH session are we in? Measured in wall-clock minutes
	/// from the session open, exactly as the table was fitted, so the lookup
	/// stays valid on 1-minute, 5-minute or 60-minute charts alike.
	/// </summary>
	private int TimeBucket()
	{
		if (_rthStartTime == DateTime.MinValue || _lastCalculatedBar < 0)
			return 0;

		var mins = (GetCandle(_lastCalculatedBar).Time - _rthStartTime).TotalMinutes;
		if (mins < 90) return 0;
		if (mins < 210) return 1;
		return 2;
	}

	#endregion

	#region Rendering

	protected override void OnRender(RenderContext context, DrawingLayouts layout)
	{
		if (ChartInfo is null)
			return;

		// Never leave the trader staring at a blank chart with no explanation.
		if (_active.Count == 0)
		{
			DrawStatus(context, StatusMessage(), false);
			return;
		}

		var tick = TickSize;
		var region = Container.Region;
		var right = region.Right;
		var high = ChartInfo.PriceChartContainer.High;
		var low = ChartInfo.PriceChartContainer.Low;
		var e = GetCandle(_lastCalculatedBar).Close;

		var startBar = Math.Max(0, _lastCalculatedBar - 120);
		var x1 = ChartInfo.GetXByBar(startBar, false);

		// right-edge projection line, as in the reference layout
		DrawDashed(context, new RenderPen(Color.FromArgb(70, _targetColor), 1),
			right - 1, region.Y, right - 1, region.Bottom, 4, 4);

		for (var i = 0; i < _active.Count; i++)
		{
			var l = _active[i];
			if (l.Price < low || l.Price > high)
				continue;

			var primary = i == 0;
			var y = ChartInfo.GetYByPrice(l.Price, false);
			var col = primary ? _targetColor : Color.FromArgb(100, _targetColor);

			// shaded band around the level
			var half = tick * _zoneTicks;
			var yTop = ChartInfo.GetYByPrice(l.Price + half, false);
			var yBot = ChartInfo.GetYByPrice(l.Price - half, false);
			var zone = new Rectangle(region.X, Math.Min(yTop, yBot),
				right - region.X, Math.Abs(yBot - yTop));

			if (zone.Height > 0)
			{
				context.FillRectangle(primary ? _zoneColor : Color.FromArgb(12, _zoneColor), zone);

				if (primary && _zoneTicks > 0)
				{
					var edge = new RenderPen(Color.FromArgb(60, _targetColor), 1);
					DrawDashed(context, edge, x1, zone.Top, right, zone.Top, 2, 5);
					DrawDashed(context, edge, x1, zone.Bottom, right, zone.Bottom, 2, 5);
				}
			}

			// the target line itself
			DrawDashed(context, new RenderPen(col, primary ? 2 : 1),
				x1, y, right, y, 9, 5);

			var dist = Math.Abs(l.Price - e);
			DrawCallout(context, right, y, l, i + 1, Pot(l, dist), primary);
		}

		if (_showDiagnostics)
			DrawStatus(context, DiagnosticMessage(), true);
	}

	/// <summary>
	/// Plain-language explanation of why nothing (or something) is being drawn.
	/// Every early-return path in this indicator sets a state that shows up here,
	/// so a blank chart always has a stated reason.
	/// </summary>
	private string StatusMessage()
	{
		if (_lastCalculatedBar < 0 || InstrumentInfo is null)
			return "SS Draw On Liquidity: waiting for bars...";

		var chartTime = GetCandle(_lastCalculatedBar).Time;

		if (_rthStartBar < 0)
		{
			var from = Clock(Shift(_rthStart));
			var to = Clock(Shift(_rthEnd));
			return "SS Draw On Liquidity: no regular-session bars in view yet (chart clock "
				+ Clock(chartTime) + "). RTH is set to " + from + "-" + to
				+ " on this chart clock. If your chart is not on New York time, set "
				+ "'Session offset from New York'.";
		}

		if (_showOnlyInRth && !_lastBarInRth)
			return "SS Draw On Liquidity: outside regular trading hours (chart clock "
				+ Clock(chartTime) + "). Targets are hidden because the probabilities are "
				+ "fitted on RTH only - untick 'Only show during RTH' to display them anyway.";

		var needed = _minRr * _stopPoints;
		return "SS Draw On Liquidity: in session, but no unswept level clears your minimum R:R "
			+ "(needs " + _minRr.ToString("0.##") + " x " + _stopPoints.ToString("0.#")
			+ " = " + needed.ToString("0.#") + " points). Lower 'Minimum R:R' or widen the stop.";
	}

	/// <summary>Diagnostic overlay: exactly what the indicator currently believes.</summary>
	private string DiagnosticMessage()
	{
		var candle = GetCandle(_lastCalculatedBar);
		var e = candle.Close;

		var dir = "auto";
		if (_directionMode == TradeDirectionMode.Short)
			dir = "short";
		else if (_directionMode == TradeDirectionMode.Long)
			dir = "long";

		var best = "no target";
		if (_active.Count > 0)
		{
			var pot = Pot(_active[0], Math.Abs(_active[0].Price - e));
			best = _active[0].Name + " @ " + _active[0].Price.ToString("0.##")
				+ " (" + (pot * 100f).ToString("F1") + "%)";
		}

		return "chart " + Clock(candle.Time)
			+ " | rth " + (_lastBarInRth ? "in" : "out")
			+ " | dir " + dir
			+ " | stop " + _stopPoints.ToString("0.#")
			+ " | minRR " + _minRr.ToString("0.##")
			+ " | levels " + _active.Count.ToString()
			+ " | swings " + _swings.Count.ToString()
			+ " | gaps " + _fvgs.Count.ToString()
			+ " | best " + best;
	}

	/// <summary>hh:mm on the chart clock, without relying on a format specifier.</summary>
	private static string Clock(TimeSpan t)
		=> t.Hours.ToString("00") + ":" + t.Minutes.ToString("00");

	private static string Clock(DateTime t)
		=> t.Hour.ToString("00") + ":" + t.Minute.ToString("00");

	/// <summary>
	/// Dashes drawn as explicit segments. Deliberately avoids any dash-style API
	/// on the pen so the indicator does not depend on a signature that varies
	/// between platform versions.
	/// </summary>
	private static void DrawDashed(RenderContext context, RenderPen pen,
		int x1, int y1, int x2, int y2, int dash, int gap)
	{
		if (y1 == y2)
		{
			for (var x = x1; x < x2; x += dash + gap)
				context.DrawLine(pen, x, y1, Math.Min(x + dash, x2), y1);
			return;
		}

		if (x1 == x2)
		{
			for (var y = y1; y < y2; y += dash + gap)
				context.DrawLine(pen, x1, y, x1, Math.Min(y + dash, y2));
			return;
		}

		context.DrawLine(pen, x1, y1, x2, y2);
	}

	/// <summary>Dark box at the right edge: "Target: X [n]" over "PoT: n.n%".</summary>
	private void DrawCallout(RenderContext context, int right, int y, Level l,
		int rank, float pot, bool primary)
	{
		var line1 = $"Target: {l.Name} [{rank}]";
		var line2 = $"PoT: {pot * 100:F1}%";

		var font = primary ? _font : _fontSmall;
		var s1 = context.MeasureString(line1, font);
		var s2 = context.MeasureString(line2, font);

		// MeasureString does not hand back ints, and Rectangle takes ints
		var pad = 7;
		var line1H = (int)s1.Height;
		var line2H = (int)s2.Height;
		var w = Math.Max((int)s1.Width, (int)s2.Width) + pad * 2;
		var h = line1H + line2H + pad * 2;

		var box = new Rectangle(right - w - 6, y - h / 2, w, h);

		context.FillRectangle(primary ? _boxColor : Color.FromArgb(190, _boxColor), box);
		context.DrawRectangle(new RenderPen(_targetColor, 1), box);

		context.DrawString(line1, font, _targetColor,
			new Rectangle(box.X + pad, box.Y + pad, w - pad * 2, line1H), _left);
		context.DrawString(line2, font, _textColor,
			new Rectangle(box.X + pad, box.Y + pad + line1H, w - pad * 2, line2H), _left);
	}

	#endregion

	#region Helpers

	/// <summary>Instrument tick, without assuming the property's numeric type.</summary>
	private decimal TickSize
		=> InstrumentInfo is null ? 0.25m : (decimal)InstrumentInfo.TickSize;

	/// <summary>Rejects the MinValue/MaxValue sentinels used for "no data yet".</summary>
	private static bool IsReal(decimal price)
		=> price > 0m && price != decimal.MinValue && price != decimal.MaxValue;

	/// <summary>
	/// Converts a New York session time into the chart's own clock.
	/// Session settings are entered as New York times because that is how the
	/// fitted data and every published session definition are expressed. A chart
	/// running on another timezone must shift them, or the session levels are
	/// computed from the wrong bars entirely.
	/// </summary>
	private TimeSpan Shift(TimeSpan nyTime)
	{
		if (_sessionOffsetHours == 0m)
			return nyTime;

		var mins = nyTime.TotalMinutes + (double)_sessionOffsetHours * 60.0;
		mins = ((mins % 1440.0) + 1440.0) % 1440.0;
		return TimeSpan.FromMinutes(mins);
	}

	#endregion
}
