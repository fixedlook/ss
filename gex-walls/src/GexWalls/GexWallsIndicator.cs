using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Threading;
using System.Threading.Tasks;
using System.Drawing;

using ATAS.Indicators;
using Avalonia.Threading;
using OFT.Rendering.Settings;

namespace GexWalls
{
    /// <summary>
    /// Draws option-derived GEX levels and IV walls on the price chart from
    /// CBOE's free delayed option chain, and keeps them up to date.
    ///
    /// The levels are recomputed on a timer, so when the book shifts the line
    /// steps to its new price and the previous value stays visible on the bars
    /// where it was in force - you see how a wall migrated rather than just
    /// where it is now.
    ///
    /// Data: https://cdn.cboe.com/api/global/delayed_quotes/options/&lt;SYMBOL&gt;.json
    /// 15 minute delay; open interest is the previous session's until the
    /// overnight file posts (turn on "volume as OI for 0DTE" to work around it).
    /// </summary>
    [DisplayName("GEX / IV Walls (CBOE)")]
    [Description(
        "Gamma exposure levels and implied-volatility walls computed from CBOE's " +
        "free delayed option chain, drawn on the price chart and refreshed while " +
        "the platform runs.\n\n" +
        "Levels:\n" +
        "  Zero-gamma  - strike where cumulative dealer gamma changes sign. Above it " +
        "dealers dampen moves, below it they amplify them.\n" +
        "  Call walls  - the two strikes with the largest call gamma above spot " +
        "(resistance / pinning above).\n" +
        "  Put walls   - the two strikes with the largest put gamma below spot " +
        "(support / acceleration below).\n" +
        "  IV walls    - the two strikes carrying the largest implied-vol x open-interest " +
        "mass, i.e. where the market is paying the most vol.\n" +
        "  Max pain    - nearest-expiry strike with the smallest total option payout.\n" +
        "  Expected move - nearest-expiry ATM straddle, drawn as a band.\n\n" +
        "Source symbols are CBOE's: _SPX, _NDX, _RUT, _VIX for indices (underscore), " +
        "SPY, QQQ, IWM for ETFs. With auto-detect on, an NQ/MNQ chart uses _NDX, an " +
        "ES/MES chart uses _SPX.\n\n" +
        "Because index options price a different instrument than the future you " +
        "trade, every level is shifted onto your chart by (chart price - chain spot), " +
        "which absorbs the futures basis. ETF chains are rescaled proportionally " +
        "instead. Choose the mapping manually under Mapping if you want raw strikes.")]
    public class GexWallsIndicator : Indicator
    {
        #region types

        private enum Slot
        {
            ZeroGamma = 0,
            CallWall1 = 1,
            CallWall2 = 2,
            PutWall1 = 3,
            PutWall2 = 4,
            IvWall1 = 5,
            IvWall2 = 6,
            MaxPain = 7,
            EmHigh = 8,
            EmLow = 9
        }

        private sealed class LevelSlot
        {
            public Slot Kind;
            public ValueDataSeries Series;
        }

        /// <summary>A level set that became valid at <see cref="Bar"/> and stayed valid until the next step.</summary>
        private sealed class Step
        {
            public int Bar;
            public decimal?[] Values;
        }

        #endregion

        #region fields

        private const int MaxSteps = 2000;

        private readonly List<LevelSlot> _slots = new List<LevelSlot>();
        private readonly List<Step> _steps = new List<Step>();

        private LevelsSnapshot _levels;
        private DispatcherTimer _timer;
        private bool _fetching;
        private DateTime _lastRequestUtc = DateTime.MinValue;
        private int _barCount = -1;
        private string _sourceUsed = "";
        private ChainSnapshot _lastChain;
        private bool _recomputePending;
        private bool _disposed;
        private bool _loggedNoSource;
        private string _stepsSource = "";
        private int _stepsVersion;
        private int _lastWrittenBar = -1;
        private int _lastWrittenVersion = -1;

        private Color _zeroGammaColor = Color.Gold;
        private Color _callWallColor = Color.LimeGreen;
        private Color _putWallColor = Color.OrangeRed;
        private Color _ivWallColor = Color.DodgerBlue;
        private Color _maxPainColor = Color.Gray;
        private Color _expectedMoveColor = Color.MediumPurple;

        private bool _showZeroGamma = true;
        private bool _showCallWalls = true;
        private bool _showPutWalls = true;
        private bool _showIvWalls = true;
        private bool _showMaxPain;
        private bool _showExpectedMove;
        private bool _autoSource = true;
        private string _sourceSymbol = "_SPX";
        private DataSourceMode _dataSourceMode = DataSourceMode.CboeDelayed;
        private int _refreshSeconds = 120;
        private int _minDte;
        private int _maxDte = 45;
        private decimal _maxStrikeDistancePct = 20m;
        private bool _volumeProxyForToday;
        private PriceMapping _priceMapping = PriceMapping.Auto;
        private decimal _scaleFactor = 1m;
        private decimal _priceOffset;

        #endregion

        #region ctor

        public GexWallsIndicator() : base(true)
        {
            DenyToChangePanel = true;
            DataSeries[0].IsHidden = true;

            AddSlot(Slot.ZeroGamma, "GEX zero-gamma", LineDashStyle.Solid, 2);
            AddSlot(Slot.CallWall1, "Call wall 1", LineDashStyle.Solid, 1);
            AddSlot(Slot.CallWall2, "Call wall 2", LineDashStyle.Dash, 1);
            AddSlot(Slot.PutWall1, "Put wall 1", LineDashStyle.Solid, 1);
            AddSlot(Slot.PutWall2, "Put wall 2", LineDashStyle.Dash, 1);
            AddSlot(Slot.IvWall1, "IV wall 1", LineDashStyle.Dot, 1);
            AddSlot(Slot.IvWall2, "IV wall 2", LineDashStyle.Dot, 1);
            AddSlot(Slot.MaxPain, "Max pain", LineDashStyle.Dot, 1);
            AddSlot(Slot.EmHigh, "Expected move high", LineDashStyle.Dash, 1);
            AddSlot(Slot.EmLow, "Expected move low", LineDashStyle.Dash, 1);

            ApplyColors();
        }

        private void AddSlot(Slot kind, string name, LineDashStyle dash, int width)
        {
            var series = new ValueDataSeries(name, name)
            {
                Width = width,
                LineDashStyle = dash,
                // never let a level compress the price scale of the candles
                ScaleIt = false,
                ShowZeroValue = false,
                ShowCurrentValue = false
            };

            DataSeries.Add(series);
            _slots.Add(new LevelSlot { Kind = kind, Series = series });
        }

        private void ApplyColors()
        {
            foreach (var slot in _slots)
            {
                Color color;
                switch (slot.Kind)
                {
                    case Slot.ZeroGamma:
                        color = _zeroGammaColor;
                        break;
                    case Slot.CallWall1:
                    case Slot.CallWall2:
                        color = _callWallColor;
                        break;
                    case Slot.PutWall1:
                    case Slot.PutWall2:
                        color = _putWallColor;
                        break;
                    case Slot.IvWall1:
                    case Slot.IvWall2:
                        color = _ivWallColor;
                        break;
                    case Slot.MaxPain:
                        color = _maxPainColor;
                        break;
                    default:
                        color = _expectedMoveColor;
                        break;
                }

                slot.Series.Color = color;
            }
        }

        #endregion

        #region settings

        [Display(Name = "Auto-detect source from chart", GroupName = "Data", Order = 10,
            Description = "NQ/MNQ -> _NDX, ES/MES -> _SPX, RTY/M2K -> _RUT, YM/MYM -> _DJX, QQQ/SPY -> own chain.")]
        public bool AutoSource
        {
            get => _autoSource;
            set
            {
                if (_autoSource == value)
                    return;
                _autoSource = value;
                OnSettingChanged(refetch: true, recompute: false);
            }
        }

        [Display(Name = "Source symbol (manual)", GroupName = "Data", Order = 11,
            Description = "CBOE symbol: _SPX, _NDX, _RUT, _VIX, SPY, QQQ ... used when auto-detect is off.")]
        public string SourceSymbol
        {
            get => _sourceSymbol;
            set
            {
                var text = (value ?? "").Trim();
                if (_sourceSymbol == text)
                    return;
                _sourceSymbol = text;
                OnSettingChanged(refetch: true, recompute: false);
            }
        }

        [Display(Name = "Data source", GroupName = "Data", Order = 12,
            Description = "CboeDelayed downloads the chain itself. LocalJsonFile reads the same level file " +
                          "that tools/gex_levels.py writes, for a custom poller.")]
        public DataSourceMode SourceMode
        {
            get => _dataSourceMode;
            set
            {
                if (_dataSourceMode == value)
                    return;
                _dataSourceMode = value;
                OnSettingChanged(refetch: true, recompute: false);
            }
        }

        [Display(Name = "Refresh interval (seconds)", GroupName = "Data", Order = 13,
            Description = "CBOE data is 15 minutes delayed, so polling faster than ~60s buys nothing.")]
        public int RefreshSeconds
        {
            get => _refreshSeconds;

            // Clamping here made the dialog show a value the user never typed,
            // which reads as "my setting did not stick". The fetch path already
            // enforces the 30 s floor.
            set => _refreshSeconds = value < 0 ? 0 : value;
        }

        [Display(Name = "Min days to expiry", GroupName = "Data", Order = 14)]
        public int MinDte
        {
            get => _minDte;
            set
            {
                if (_minDte == value)
                    return;
                _minDte = value < 0 ? 0 : value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Max days to expiry", GroupName = "Data", Order = 15,
            Description = "Gamma-relevant slice of the book. 45 keeps 0DTE through the next six weeks.")]
        public int MaxDte
        {
            get => _maxDte;
            set
            {
                if (_maxDte == value)
                    return;
                _maxDte = value < 0 ? 0 : value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Max strike distance from spot (%)", GroupName = "Data", Order = 16,
            Description = "Ignore strikes further away than this; 0 disables the filter.")]
        public decimal MaxStrikeDistancePct
        {
            get => _maxStrikeDistancePct;
            set
            {
                if (_maxStrikeDistancePct == value)
                    return;
                _maxStrikeDistancePct = value < 0m ? 0m : value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Volume as OI for 0DTE", GroupName = "Data", Order = 17,
            Description = "Open interest is T-1 until the overnight file posts, which makes today's expiry " +
                          "look empty. Uses volume as an OI stand-in for contracts expiring today.")]
        public bool VolumeProxyForToday
        {
            get => _volumeProxyForToday;
            set
            {
                if (_volumeProxyForToday == value)
                    return;
                _volumeProxyForToday = value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Price mapping", GroupName = "Mapping", Order = 20,
            Description = "Auto: shift index levels by the futures basis, rescale ETF levels. " +
                          "AlignToChart: shift only. Normalize: rescale only. Direct: raw strikes x scale + offset.")]
        public PriceMapping Mapping
        {
            get => _priceMapping;
            set
            {
                if (_priceMapping == value)
                    return;
                _priceMapping = value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Scale factor (Direct mode)", GroupName = "Mapping", Order = 21)]
        public decimal ScaleFactor
        {
            get => _scaleFactor;
            set
            {
                if (_scaleFactor == value)
                    return;
                _scaleFactor = value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Price offset (Direct mode)", GroupName = "Mapping", Order = 22)]
        public decimal PriceOffset
        {
            get => _priceOffset;
            set
            {
                if (_priceOffset == value)
                    return;
                _priceOffset = value;
                OnSettingChanged(refetch: false, recompute: true);
            }
        }

        [Display(Name = "Show zero-gamma flip", GroupName = "Levels", Order = 30)]
        public bool ShowZeroGamma
        {
            get => _showZeroGamma;
            set => SetLevelVisibility(ref _showZeroGamma, value);
        }

        [Display(Name = "Show call walls", GroupName = "Levels", Order = 31)]
        public bool ShowCallWalls
        {
            get => _showCallWalls;
            set => SetLevelVisibility(ref _showCallWalls, value);
        }

        [Display(Name = "Show put walls", GroupName = "Levels", Order = 32)]
        public bool ShowPutWalls
        {
            get => _showPutWalls;
            set => SetLevelVisibility(ref _showPutWalls, value);
        }

        [Display(Name = "Show IV walls", GroupName = "Levels", Order = 33)]
        public bool ShowIvWalls
        {
            get => _showIvWalls;
            set => SetLevelVisibility(ref _showIvWalls, value);
        }

        [Display(Name = "Show max pain", GroupName = "Levels", Order = 34)]
        public bool ShowMaxPain
        {
            get => _showMaxPain;
            set => SetLevelVisibility(ref _showMaxPain, value);
        }

        [Display(Name = "Show expected move band", GroupName = "Levels", Order = 35)]
        public bool ShowExpectedMove
        {
            get => _showExpectedMove;
            set => SetLevelVisibility(ref _showExpectedMove, value);
        }

        [Display(Name = "Zero-gamma color", GroupName = "Colors", Order = 40)]
        public Color ZeroGammaColor
        {
            get => _zeroGammaColor;
            set => SetColor(ref _zeroGammaColor, value);
        }

        [Display(Name = "Call wall color", GroupName = "Colors", Order = 41)]
        public Color CallWallColor
        {
            get => _callWallColor;
            set => SetColor(ref _callWallColor, value);
        }

        [Display(Name = "Put wall color", GroupName = "Colors", Order = 42)]
        public Color PutWallColor
        {
            get => _putWallColor;
            set => SetColor(ref _putWallColor, value);
        }

        [Display(Name = "IV wall color", GroupName = "Colors", Order = 43)]
        public Color IvWallColor
        {
            get => _ivWallColor;
            set => SetColor(ref _ivWallColor, value);
        }

        [Display(Name = "Max pain color", GroupName = "Colors", Order = 44)]
        public Color MaxPainColor
        {
            get => _maxPainColor;
            set => SetColor(ref _maxPainColor, value);
        }

        [Display(Name = "Expected move color (unused if hidden)", GroupName = "Colors", Order = 45)]
        public Color ExpectedMoveColor
        {
            get => _expectedMoveColor;
            set => SetColor(ref _expectedMoveColor, value);
        }

        private void SetColor(ref Color field, Color value)
        {
            try
            {
                field = value;
                ApplyColors();

                // A colour belongs to the whole series: no bar values changed.
                TryRedraw();
            }
            catch (Exception exception)
            {
                Log("applying a colour failed: " + exception.Message);
            }
        }

        private void SetLevelVisibility(ref bool field, bool value)
        {
            if (field == value)
                return;

            try
            {
                field = value;

                foreach (var bar in VisibleBars())
                    WriteBar(bar);

                TryRedraw();
            }
            catch (Exception exception)
            {
                Log("toggling a level failed: " + exception.Message);
            }
        }

        #endregion

        #region calculation

        protected override void OnCalculate(int bar, decimal value)
        {
            if (bar == 0)
            {
                _barCount = 0;
                _lastWrittenBar = -1;
                _lastWrittenVersion = -1;

                if (_levels == null && _steps.Count == 0)
                {
                    // last session's levels, so a restart is not an empty chart
                    try
                    {
                        var cached = LocalLevelsFile.Load(ResolveSource());
                        if (cached != null)
                            ApplyLevels(cached);
                    }
                    catch
                    {
                        // cache is a convenience only
                    }
                }

                StartTimer();
            }

            if (bar > _barCount)
                _barCount = bar;

            if (_recomputePending)
                RecomputeLocally();

            // A snapshot can arrive before the first bar exists; paint it as soon
            // as there is somewhere to put it.
            if (_steps.Count == 0 && _levels != null)
                ApplyLevels(_levels);

            // The forming bar only needs rewriting when its step changed - this
            // runs on every tick, so the check earns its keep.
            if (bar != _lastWrittenBar || _stepsVersion != _lastWrittenVersion)
            {
                try
                {
                    WriteBar(bar);
                    _lastWrittenBar = bar;
                    _lastWrittenVersion = _stepsVersion;
                }
                catch (Exception exception)
                {
                    Log("writing a level failed: " + exception.Message);
                }
            }

            if (bar == _barCount)
                MaybeRequestFetch();
        }

        protected override void OnDispose()
        {
            _disposed = true;

            if (_timer != null)
            {
                try
                {
                    _timer.Stop();
                }
                catch
                {
                    // nothing to do
                }

                _timer = null;
            }

            base.OnDispose();
        }

        private IEnumerable<int> VisibleBars()
        {
            for (var bar = 0; bar <= _barCount; bar++)
                yield return bar;
        }

        private void WriteBar(int bar)
        {
            if (bar < 0 || _steps.Count == 0)
                return;

            var step = FindStep(bar);

            for (var i = 0; i < _slots.Count; i++)
            {
                var slot = _slots[i];
                var enabled = IsEnabled(slot.Kind);
                var value = step == null ? null : step.Values[i];

                slot.Series[bar] = enabled && value.HasValue ? value.Value : 0m;
            }
        }

        private Step FindStep(int bar)
        {
            var low = 0;
            var high = _steps.Count - 1;
            Step found = null;

            while (low <= high)
            {
                var mid = (low + high) / 2;

                if (_steps[mid].Bar <= bar)
                {
                    found = _steps[mid];
                    low = mid + 1;
                }
                else
                {
                    high = mid - 1;
                }
            }

            return found;
        }

        private bool IsEnabled(Slot kind)
        {
            switch (kind)
            {
                case Slot.ZeroGamma:
                    return _showZeroGamma;
                case Slot.CallWall1:
                case Slot.CallWall2:
                    return _showCallWalls;
                case Slot.PutWall1:
                case Slot.PutWall2:
                    return _showPutWalls;
                case Slot.IvWall1:
                case Slot.IvWall2:
                    return _showIvWalls;
                case Slot.MaxPain:
                    return _showMaxPain;
                default:
                    return _showExpectedMove;
            }
        }

        #endregion

        #region data

        private void StartTimer()
        {
            if (_timer != null)
                return;

            try
            {
                // Ticks often; MaybeRequestFetch applies the real refresh interval.
                _timer = new DispatcherTimer(TimeSpan.FromSeconds(15), DispatcherPriority.Background,
                    (sender, args) => MaybeRequestFetch());
                _timer.Start();
            }
            catch
            {
                // OnCalculate still refreshes on incoming ticks without the timer.
            }
        }

        private void MaybeRequestFetch()
        {
            if (_fetching)
                return;

            // Called on every tick of the forming bar, so bail out on the
            // cheapest possible check before touching strings.
            var now = DateTime.UtcNow;
            if ((now - _lastRequestUtc).TotalSeconds < Math.Max(30, _refreshSeconds))
                return;

            var source = ResolveSource();
            if (string.IsNullOrEmpty(source))
            {
                if (!_loggedNoSource)
                {
                    _loggedNoSource = true;
                    Log("no CBOE source configured - set 'Source symbol' under Data");
                }

                return;
            }

            if (!string.Equals(source, _sourceUsed, StringComparison.OrdinalIgnoreCase))
            {
                // Do not blank the chart here. The previous levels stay visible
                // until the new chain's first snapshot arrives, and ApplyLevels
                // repaints the history at that point.
                _sourceUsed = source;
            }

            _lastRequestUtc = now;
            _ = FetchAsync(source);
        }

        private async Task FetchAsync(string source)
        {
            _fetching = true;

            try
            {
                LevelsSnapshot snapshot;

                if (_dataSourceMode == DataSourceMode.LocalJsonFile)
                {
                    snapshot = LocalLevelsFile.Load(source);
                }
                else
                {
                    var chain = await CboeChainProvider
                        .GetChainAsync(source, _refreshSeconds, CancellationToken.None)
                        .ConfigureAwait(false);

                    if (chain == null)
                    {
                        Log("chain unavailable - keeping the previous levels");
                        return;
                    }

                    _lastChain = chain;
                    snapshot = GexMath.Compute(chain, BuildLevelOptions(), DateTime.UtcNow);
                }

                if (snapshot == null || !snapshot.HasAnyLevel)
                {
                    Log("no levels computed - check symbol, DTE window and distance filter");
                    return;
                }

                if (_dataSourceMode == DataSourceMode.CboeDelayed)
                    LocalLevelsFile.Save(snapshot);

                if (_disposed)
                    return;

                await Dispatcher.UIThread.InvokeAsync(() => ApplyLevels(snapshot));
            }
            catch (Exception exception)
            {
                Log("GEX walls refresh failed: " + exception.Message);
            }
            finally
            {
                _fetching = false;
            }
        }

        /// <summary>
        /// Called by every settings setter. It must never throw: when the settings
        /// dialog is cancelled, ATAS restores the previous values through these
        /// same setters, and an exception in the middle of that pass aborts the
        /// restore - which is what made settings impossible to revert.
        /// </summary>
        private void OnSettingChanged(bool refetch, bool recompute)
        {
            try
            {
                if (refetch)
                {
                    // Let the next check pull fresh data; do not do network work
                    // inside a setter.
                    _lastRequestUtc = DateTime.MinValue;
                }

                if (recompute)
                {
                    _recomputePending = true;

                    // The cached chain is enough for a filter or mapping change,
                    // so apply it now instead of waiting for a download.
                    if (_lastChain != null || _levels != null)
                        RecomputeLocally();
                }

                if (refetch)
                {
                    // Re-run the refresh check once the dialog has settled, so a
                    // source change takes effect without waiting for a tick.
                    Dispatcher.UIThread.Post(() => TryFetch());
                }
            }
            catch (Exception exception)
            {
                Log("applying a setting failed: " + exception.Message);
            }
        }

        private void TryFetch()
        {
            try
            {
                MaybeRequestFetch();
            }
            catch (Exception exception)
            {
                Log("scheduled refresh failed: " + exception.Message);
            }
        }

        private void RecomputeLocally()
        {
            _recomputePending = false;

            if (_lastChain != null)
            {
                var fresh = GexMath.Compute(_lastChain, BuildLevelOptions(), DateTime.UtcNow);
                if (fresh != null && fresh.HasAnyLevel)
                {
                    ApplyLevels(fresh);
                    return;
                }
            }

            if (_levels != null)
                ApplyLevels(_levels);
        }

        private LevelOptions BuildLevelOptions()
        {
            return new LevelOptions
            {
                MinDte = _minDte,
                MaxDte = _maxDte,
                MaxStrikeDistancePct = _maxStrikeDistancePct,
                VolumeProxyForToday = _volumeProxyForToday
            };
        }

        private string ResolveSource()
        {
            if (!_autoSource)
                return (_sourceSymbol ?? "").Trim();

            var instrument = "";
            try
            {
                // Indicator.Instrument is obsolete in ATAS X
                instrument = (InstrumentInfo.Instrument ?? "").ToUpperInvariant();
            }
            catch
            {
                // not available yet (during construction, or no instrument bound)
            }

            if (instrument.Contains("NQ"))
                return "_NDX";
            if (instrument.Contains("ES"))
                return "_SPX";
            if (instrument.Contains("RTY") || instrument.Contains("M2K"))
                return "_RUT";
            if (instrument.Contains("YM") || instrument.Contains("DJ"))
                return "_DJX";
            if (instrument.Contains("QQQ"))
                return "QQQ";
            if (instrument.Contains("SPY"))
                return "SPY";
            if (instrument.Contains("IWM"))
                return "IWM";
            if (instrument.Contains("GC") || instrument.Contains("MGC"))
                return "GLD";

            return (_sourceSymbol ?? "").Trim();
        }

        private PriceMapping EffectiveMapping(string source)
        {
            if (_priceMapping != PriceMapping.Auto)
                return _priceMapping;

            var symbol = (source ?? "").ToUpperInvariant();

            if (symbol.StartsWith("_", StringComparison.Ordinal))
            {
                // DJX quotes a hundredth of the index, NQX a tenth of the NDX
                if (symbol == "_DJX" || symbol == "_NQX" || symbol == "_XSP")
                    return PriceMapping.Normalize;

                return PriceMapping.AlignToChart;
            }

            // ETF chains (SPY, QQQ, IWM, GLD...) trade in different price space
            return PriceMapping.Normalize;
        }

        #endregion

        #region drawing

        private void ApplyLevels(LevelsSnapshot snapshot)
        {
            if (snapshot == null || !snapshot.HasAnyLevel || _disposed)
                return;

            // Keep the snapshot even if there are no bars yet - the next
            // OnCalculate will paint it. Dropping it here left the chart empty
            // until the following download.
            _levels = snapshot;

            if (_barCount < 0)
                return;

            // Levels are in the chain's price space. Without a reference price on
            // the chart the mapping would be wrong, so wait for one.
            if (EffectiveMapping(snapshot.Source) != PriceMapping.Direct && ChartPrice() <= 0m)
                return;

            // A different underlying means the stored steps are in the wrong price
            // space: drop them and repaint the whole history with the new chain.
            if (!string.Equals(_stepsSource, snapshot.Source, StringComparison.OrdinalIgnoreCase))
            {
                _steps.Clear();
                _stepsSource = snapshot.Source;
                _stepsVersion++;
            }

            var values = MapLevels(snapshot);

            if (_steps.Count == 0)
            {
                // First level set: it is the best guess for the whole visible
                // history, so paint every existing bar with it.
                InsertStep(0, values);

                foreach (var bar in VisibleBars())
                    WriteBar(bar);
            }
            else
            {
                if (SameValues(_steps[_steps.Count - 1].Values, values))
                    return; // nothing moved, nothing to redraw

                InsertStep(_barCount, values);
                WriteBar(_barCount);
            }

            _lastWrittenBar = _barCount;
            _lastWrittenVersion = _stepsVersion;
            TryRedraw();
        }

        private void InsertStep(int bar, decimal?[] values)
        {
            var last = _steps.Count > 0 ? _steps[_steps.Count - 1] : null;

            if (last != null && bar < last.Bar)
                bar = last.Bar;

            if (last != null && last.Bar == bar)
                _steps[_steps.Count - 1] = new Step { Bar = bar, Values = values };
            else
                _steps.Add(new Step { Bar = bar, Values = values });

            if (_steps.Count > MaxSteps)
                _steps.RemoveRange(0, _steps.Count - MaxSteps);

            _stepsVersion++;
        }

        private static bool SameValues(decimal?[] left, decimal?[] right)
        {
            if (left == null || right == null || left.Length != right.Length)
                return false;

            for (var i = 0; i < left.Length; i++)
            {
                if (left[i].HasValue != right[i].HasValue)
                    return false;

                if (left[i].HasValue && left[i].Value != right[i].Value)
                    return false;
            }

            return true;
        }

        /// <summary>Moves the chain's price scale onto the chart's own price scale.</summary>
        private decimal?[] MapLevels(LevelsSnapshot snapshot)
        {
            var mapping = EffectiveMapping(snapshot.Source);
            var chartPrice = ChartPrice();

            var scale = 1m;
            var offset = 0m;

            switch (mapping)
            {
                case PriceMapping.Normalize:
                    if (snapshot.Spot > 0m && chartPrice > 0m)
                        scale = chartPrice / snapshot.Spot;
                    break;

                case PriceMapping.AlignToChart:
                    if (snapshot.Spot > 0m && chartPrice > 0m)
                        offset = chartPrice - snapshot.Spot;
                    break;

                case PriceMapping.Direct:
                    scale = _scaleFactor;
                    offset = _priceOffset;
                    break;
            }

            var values = new decimal?[_slots.Count];
            values[(int)Slot.ZeroGamma] = Map(snapshot.ZeroGamma);
            values[(int)Slot.CallWall1] = Map(snapshot.CallWall1);
            values[(int)Slot.CallWall2] = Map(snapshot.CallWall2);
            values[(int)Slot.PutWall1] = Map(snapshot.PutWall1);
            values[(int)Slot.PutWall2] = Map(snapshot.PutWall2);
            values[(int)Slot.IvWall1] = Map(snapshot.IvWall1);
            values[(int)Slot.IvWall2] = Map(snapshot.IvWall2);
            values[(int)Slot.MaxPain] = Map(snapshot.MaxPain);
            values[(int)Slot.EmHigh] = Map(snapshot.EmHigh);
            values[(int)Slot.EmLow] = Map(snapshot.EmLow);

            return values;

            decimal? Map(decimal? level)
            {
                return level.HasValue ? RoundToTick(level.Value * scale + offset) : (decimal?)null;
            }
        }

        private decimal ChartPrice()
        {
            try
            {
                if (_barCount >= 0)
                    return GetCandle(_barCount).Close;
            }
            catch
            {
                // bar not available yet
            }

            return 0m;
        }

        private decimal RoundToTick(decimal price)
        {
            var tick = 0m;

            try
            {
                tick = InstrumentInfo.TickSize;
            }
            catch
            {
                // instrument info not ready
            }

            if (tick <= 0m)
                return Math.Round(price, 2);

            return Math.Round(price / tick) * tick;
        }

        private void TryRedraw()
        {
            try
            {
                RedrawChart(new RedrawArg(Container.Region));
            }
            catch
            {
                // the next chart refresh will pick the levels up
            }
        }

        private void Log(string message)
        {
            GexLog.Write(message + " [" + (_sourceUsed ?? "") + "]");
        }

        #endregion
    }
}
