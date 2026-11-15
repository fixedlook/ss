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
        "  Zero-gamma  - the price where the book's gamma dollars cancel out (every " +
        "contract re-priced at that spot). Above it dealers dampen moves, below it " +
        "they amplify them.\n" +
        "  Call walls  - the two strikes with the largest call gamma above spot " +
        "(resistance / pinning above).\n" +
        "  Put walls   - the two strikes with the largest put gamma below spot " +
        "(support / acceleration below).\n" +
        "  IV walls    - the two most liquid strikes carrying the highest open-interest " +
        "weighted implied vol " +
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
        private string _lastMapLog = "";
        private string _lastBlockedLog = "";
        private string _lastAuditLog;
        private string _lastIncompleteSource;
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
        private decimal _gammaFlipBandPct = 12m;
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

        [Display(Name = "Source in use (read only)", GroupName = "Data", Order = 9,
            Description = "The chain the indicator is actually resolving to right now. With auto-detect on, " +
                          "this is what gets used and the manual field below is ignored.")]
        public string SourceInUse
        {
            get
            {
                try
                {
                    return ResolveSource();
                }
                catch
                {
                    return "";
                }
            }

            // computed - a no-op setter keeps the settings serializer happy
            set { }
        }

        [Display(Name = "Source symbol (manual) - IGNORED while auto-detect is on", GroupName = "Data", Order = 11,
            Description = "CBOE symbol: _SPX, _NDX, _RUT, _VIX, SPY, QQQ ... Only used after " +
                          "'Auto-detect source from chart' is switched OFF.")]
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

        [Display(Name = "Gamma flip search band (%)", GroupName = "Data", Order = 16,
            Description = "How far either side of spot the flip scan looks. The flip is the price where " +
                          "re-priced net gamma changes sign; a crossing further out than this is wing noise.")]
        public decimal GammaFlipBandPct
        {
            get => _gammaFlipBandPct;
            set
            {
                if (_gammaFlipBandPct == value)
                    return;
                // 0 would disable the scan entirely, which reads as a broken level
                _gammaFlipBandPct = value <= 0m ? 12m : Math.Min(value, 50m);
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

            var source = CanonicalSource(ResolveSource());
            if (string.IsNullOrEmpty(source))
            {
                if (!_loggedNoSource)
                {
                    _loggedNoSource = true;
                    Log("no CBOE source configured - set 'Source symbol' under Data");
                }

                return;
            }

            // A half-typed symbol ('_', '_N') only 404s once per keystroke and
            // buries the real failures. Wait until there is something fetchable.
            if (source.Length < 3)
            {
                if (!string.Equals(source, _lastIncompleteSource, StringComparison.OrdinalIgnoreCase))
                {
                    _lastIncompleteSource = source;
                    Log("source '" + source + "' is incomplete - waiting for a full CBOE symbol");
                }

                return;
            }

            _lastIncompleteSource = null;

            if (!string.Equals(source, _sourceUsed, StringComparison.OrdinalIgnoreCase))
            {
                // Do not blank the chart here. The previous levels stay visible
                // until the new chain's first snapshot arrives, and ApplyLevels
                // repaints the history at that point.
                if (!string.IsNullOrEmpty(_sourceUsed))
                    Log("source changed: " + _sourceUsed + " -> " + source);

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
                        var error = CboeChainProvider.LastErrorFor(source);
                        Log("chain unavailable for " + source + " (" + CboeChainProvider.UrlFor(source) + ")" +
                            (string.IsNullOrEmpty(error) ? "" : " - " + error) +
                            " - keeping the previous levels");
                        return;
                    }

                    _lastChain = chain;

                    var stale = CboeChainProvider.LastErrorFor(source);
                    if (!string.IsNullOrEmpty(stale))
                        Log("latest download failed (" + stale + ") - recomputing from the last good chain");

                    snapshot = GexMath.Compute(chain, BuildLevelOptions(), DateTime.UtcNow);
                }

                if (snapshot == null || !snapshot.HasAnyLevel)
                {
                    Log("no levels computed - check symbol, DTE window and distance filter");
                    return;
                }

                if (_dataSourceMode == DataSourceMode.CboeDelayed)
                {
                    LocalLevelsFile.Save(snapshot);
                    LocalLevelsFile.SaveAudit(snapshot, BuildLevelOptions());
                }

                LogAudit(snapshot);

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
                GammaFlipBandPct = _gammaFlipBandPct,
                VolumeProxyForToday = _volumeProxyForToday
            };
        }

        private string ResolveSource()
        {
            if (!_autoSource)
                return CanonicalSource(_sourceSymbol);

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

            return CanonicalSource(_sourceSymbol);
        }

        /// <summary>
        /// The manual field is typed by a human, so NQ, _NQ, mnq and MNQZ26 all
        /// have to end up at the same CBOE symbol. Unknown values pass through
        /// untouched - CBOE has plenty of symbols this table does not know.
        /// </summary>
        private static string CanonicalSource(string raw)
        {
            var text = (raw ?? "").Trim().ToUpperInvariant();
            if (text.Length == 0)
                return "";

            if (Aliases.TryGetValue(text, out var direct))
                return direct;

            // futures-style root: MNQZ26 -> MNQZ -> MNQ
            var end = 0;
            while (end < text.Length && char.IsLetter(text[end]))
                end++;

            if (end > 0)
            {
                var letters = text.Substring(0, end);
                if (Aliases.TryGetValue(letters, out var byRoot))
                    return byRoot;

                if (letters.Length > 3 &&
                    Aliases.TryGetValue(letters.Substring(0, letters.Length - 1), out var byMonth))
                    return byMonth;
            }

            return text;
        }

        private static readonly Dictionary<string, string> Aliases =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "NQ", "_NDX" }, { "MNQ", "_NDX" }, { "_NQ", "_NDX" }, { "NDX", "_NDX" }, { "_NDX", "_NDX" },
                { "ES", "_SPX" }, { "MES", "_SPX" }, { "_ES", "_SPX" }, { "SPX", "_SPX" }, { "SPXW", "_SPX" }, { "_SPX", "_SPX" },
                { "RTY", "_RUT" }, { "M2K", "_RUT" }, { "RUT", "_RUT" }, { "_RUT", "_RUT" },
                { "YM", "_DJX" }, { "MYM", "_DJX" }, { "DJI", "_DJX" }, { "DJX", "_DJX" }, { "_DJX", "_DJX" },
                { "VIX", "_VIX" }, { "_VIX", "_VIX" }, { "_XSP", "_XSP" },
                { "SPY", "SPY" }, { "QQQ", "QQQ" }, { "IWM", "IWM" }, { "GLD", "GLD" }, { "TLT", "TLT" }
            };

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

            // A different underlying means the stored steps are in the wrong price
            // space: drop them and repaint the whole history with the new chain.
            if (!string.Equals(_stepsSource, snapshot.Source, StringComparison.OrdinalIgnoreCase))
            {
                _steps.Clear();
                _stepsSource = snapshot.Source;
                _stepsVersion++;
            }

            var values = MapLevels(snapshot);

            if (values == null)
            {
                // No usable reference price yet (cache file without a spot, or no
                // candle). Previously the offset silently stayed 0 here, which drew
                // raw chain strikes - every level under the price on an NQ chart.
                return;
            }

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

            // Both non-Direct modes anchor the chain to the chart price. Without
            // that anchor the levels are raw strikes in the chain's own price
            // space, which is never what anyone wants to see on a futures chart.
            if (mapping != PriceMapping.Direct && (chartPrice <= 0m || snapshot.Spot <= 0m))
            {
                LogBlocked(mapping, snapshot, chartPrice);
                return null;
            }

            switch (mapping)
            {
                case PriceMapping.Normalize:
                    scale = chartPrice / snapshot.Spot;
                    break;

                case PriceMapping.AlignToChart:
                    offset = chartPrice - snapshot.Spot;
                    break;

                default:
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

            LogMapping(mapping, snapshot, chartPrice, scale, offset, values);

            return values;

            decimal? Map(decimal? level)
            {
                return level.HasValue ? RoundToTick(level.Value * scale + offset) : (decimal?)null;
            }
        }

        private void LogMapping(
            PriceMapping mapping, LevelsSnapshot snapshot, decimal chartPrice,
            decimal scale, decimal offset, decimal?[] values)
        {
            try
            {
                var age = (DateTime.UtcNow - snapshot.UpdatedUtc).TotalMinutes;
                var line = string.Format(
                    "map {0}: source={1}{2} spot={3:0.##} chart={4:0.##} scale={5:0.#####} offset={6:0.##} " +
                    "age={7:0.#}m contracts={8} | zero={9} call1={10} call2={11} put1={12} put2={13} " +
                    "iv1={14} iv2={15} mp={16} emH={17} emL={18}",
                    mapping,
                    string.IsNullOrEmpty(snapshot.Source) ? "?" : snapshot.Source,
                    _autoSource ? " auto" : " manual",
                    snapshot.Spot,
                    chartPrice,
                    scale,
                    offset,
                    age,
                    snapshot.ContractsUsed,
                    Show(values, Slot.ZeroGamma), Show(values, Slot.CallWall1), Show(values, Slot.CallWall2),
                    Show(values, Slot.PutWall1), Show(values, Slot.PutWall2), Show(values, Slot.IvWall1),
                    Show(values, Slot.IvWall2), Show(values, Slot.MaxPain), Show(values, Slot.EmHigh),
                    Show(values, Slot.EmLow));

                if (line == _lastMapLog)
                    return;

                _lastMapLog = line;
                Log(line);

                // A call wall sits at or above spot by construction, so after
                // anchoring it must be at or above the chart price. If it is not,
                // the two price spaces did not line up.
                var call1 = values[(int)Slot.CallWall1];
                if (mapping != PriceMapping.Direct && call1.HasValue && call1.Value < chartPrice)
                {
                    Log(string.Format(
                        "WARNING call wall {0:0.##} mapped below chart price {1:0.##} - spot and chart are in " +
                        "different price spaces (spot={2:0.##})", call1.Value, chartPrice, snapshot.Spot));
                }
            }
            catch
            {
                // diagnostics must never break the drawing
            }
        }

        private static string Show(decimal?[] values, Slot slot)
        {
            var value = values[(int)slot];
            return value.HasValue ? value.Value.ToString("0.##") : "-";
        }

        private void LogBlocked(PriceMapping mapping, LevelsSnapshot snapshot, decimal chartPrice)
        {
            try
            {
                var line = string.Format(
                    "cannot map {0}: spot={1:0.##} chart={2:0.##} - waiting for a reference price",
                    mapping, snapshot.Spot, chartPrice);

                if (line == _lastBlockedLog)
                    return;

                _lastBlockedLog = line;
                Log(line);
            }
            catch
            {
                // ignore
            }
        }

        /// <summary>
        /// One line per new snapshot describing what the source actually contained:
        /// how many contracts survived each filter and which expiries they came
        /// from. A level that looks wrong is usually a filter or an expiry mix,
        /// and this says which.
        /// </summary>
        private void LogAudit(LevelsSnapshot snapshot)
        {
            try
            {
                var audit = snapshot?.Audit;
                if (audit == null)
                    return;

                var builder = new System.Text.StringBuilder();
                builder.Append("chain ").Append(audit.Source);
                builder.Append(": ").Append(audit.ContractsTotal).Append(" contracts, used ").Append(audit.ContractsUsed);
                builder.Append(" (expired ").Append(audit.ExcludedExpired);
                builder.Append(", dte ").Append(audit.ExcludedDte);
                builder.Append(", strike-distance ").Append(audit.ExcludedStrike);
                builder.Append(", no oi/gamma ").Append(audit.ExcludedNoOiOrGamma);
                builder.Append(", no iv ").Append(audit.ExcludedNoIv).Append(")");
                builder.Append(" | iv units ").Append(audit.IvUnits);
                builder.Append(" | expiries ").Append(audit.Expiries.Count);

                var shown = 0;
                foreach (var expiry in audit.Expiries)
                {
                    if (shown++ == 4)
                        break;

                    builder.Append(" ").Append(expiry.Expiry.ToString("MM-dd"))
                        .Append(":dte").Append(expiry.Dte)
                        .Append("/oi").Append(decimal.Round(expiry.OpenInterest, 0));
                }

                builder.Append(" | flip ")
                    .Append(audit.FlipValue.HasValue ? audit.FlipValue.Value.ToString("0.##") : "none")
                    .Append(" (").Append(audit.FlipCrossings).Append(" crossings in ")
                    .Append(audit.FlipBandLow.ToString("0")).Append("..")
                    .Append(audit.FlipBandHigh.ToString("0")).Append(")");

                var line = builder.ToString();

                if (line == _lastAuditLog)
                    return;

                _lastAuditLog = line;
                Log(line);

                // Cross-check the source itself: the gamma column CBOE ships and
                // Black-Scholes gamma from the iv column must agree at spot, or
                // one of them is stale for this row set.
                var fromChain = Math.Abs(audit.ChainGexAtSpot);
                var fromIv = Math.Abs(audit.BsGexAtSpot);
                if (fromChain > 0m && fromIv > 0m)
                {
                    var ratio = fromIv / fromChain;
                    if (ratio > 1.6m || ratio < 0.6m)
                    {
                        Log(string.Format(
                            "WARNING source check: gex at spot from the chain's gamma column is {0:0.00e+0} " +
                            "but {1:0.00e+0} from Black-Scholes on the quoted iv (ratio {2:0.00}) - the chain " +
                            "greeks and its iv disagree for this snapshot",
                            audit.ChainGexAtSpot, audit.BsGexAtSpot, ratio));
                    }
                }
            }
            catch
            {
                // diagnostics must never break the drawing
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
            GexLog.Write(message + " [src=" + (_sourceUsed ?? "") + "]");
        }

        #endregion
    }
}
