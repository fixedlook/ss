package com.fixedlook.mnq;

import java.awt.Color;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import com.motivewave.platform.sdk.common.Coordinate;
import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.DataSeries;
import com.motivewave.platform.sdk.common.Defaults;
import com.motivewave.platform.sdk.common.Enums;
import com.motivewave.platform.sdk.common.Inputs;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.LineInfo;
import com.motivewave.platform.sdk.common.MarkerInfo;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.Util;
import com.motivewave.platform.sdk.common.desc.BooleanDescriptor;
import com.motivewave.platform.sdk.common.desc.ColorDescriptor;
import com.motivewave.platform.sdk.common.desc.DoubleDescriptor;
import com.motivewave.platform.sdk.common.desc.IndicatorDescriptor;
import com.motivewave.platform.sdk.common.desc.IntegerDescriptor;
import com.motivewave.platform.sdk.common.desc.LabelDescriptor;
import com.motivewave.platform.sdk.common.desc.MarkerDescriptor;
import com.motivewave.platform.sdk.common.desc.PathDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingGroup;
import com.motivewave.platform.sdk.common.desc.SettingTab;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.common.desc.ValueDescriptor;
import com.motivewave.platform.sdk.draw.Marker;
import com.motivewave.platform.sdk.study.RuntimeDescriptor;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

/**
 * CVD Divergence - order flow study for MotiveWave.
 *
 * <p>Plots the session cumulative volume delta (CVD = running sum of
 * ask-volume minus bid-volume, per bar) and flags classic divergence between
 * price swings and the delta swing that produced them:
 *
 * <ul>
 *   <li><b>Bullish divergence</b>: price prints a lower low while CVD prints a
 *       higher low - sellers are pushing price down but are being absorbed.</li>
 *   <li><b>Bearish divergence</b>: price prints a higher high while CVD prints
 *       a lower high - buyers are pushing price up but are being absorbed.</li>
 * </ul>
 *
 * <p>How the delta is obtained: MotiveWave does not expose per-bar bid/ask
 * volume on {@code Bar}, so each bar's delta is aggregated from the instrument's
 * tick history ({@code forEachTick}) and cached by bar start time. The cache is
 * keyed on start time, so an updated (live) bar is simply re-aggregated.
 *
 * <p>Signal timing: a swing point is only confirmed {@code pivotStrength} bars
 * after it occurs, and the signal is raised on the confirmation bar. There is
 * therefore no look-ahead: the divergence is reported at the first bar at which
 * a trader could actually have known about it.
 *
 * <p>This study was developed together with a Python backtest of the identical
 * rule set (see /research in the repository) on MNQ 1-minute RTH data.
 */
@StudyHeader(
    namespace = "com.fixedlook",
    id = "MNQ_CVD_DIVERGENCE",
    name = "CVD Divergence (Order Flow)",
    label = "CVD Div",
    desc = "Session cumulative delta (ask volume - bid volume) with classic price/CVD divergence signals. "
         + "Bullish: price lower low, CVD higher low. Bearish: price higher high, CVD lower high.",
    menu = "Order Flow",
    overlay = false,
    studyOverlay = true,
    signals = true,
    requiresVolume = true)
public class CvdDivergenceStudy extends Study {
  /** Values stored per bar in the data series. */
  enum Values { DELTA, CVD }

  /** Signal keys (see RuntimeDescriptor.declareSignal). */
  protected enum Signals { BULL_DIV, BEAR_DIV }

  // ------------------------------------------------------------------
  // Setting keys
  // ------------------------------------------------------------------
  static final String SESSION_RTH     = "sessionRth";      // count RTH session only
  static final String RESET_SESSION   = "resetSession";    // reset CVD on session open
  static final String PIVOT_STRENGTH  = "pivotStrength";   // bars each side of a swing
  static final String MAX_LOOKBACK    = "maxLookback";     // bars back to look for prior swing
  static final String MIN_CVD_DIV     = "minCvdDiv";       // min CVD difference (contracts)
  static final String MIN_PRICE_TICKS = "minPriceTicks";   // min price difference (ticks)
  static final String MIN_BAR_VOLUME  = "minBarVolume";    // ignore signals on thin bars
  static final String SKIP_FIRST_MIN  = "skipFirstMin";    // ignore first N minutes of session
  static final String ALLOW_LONG      = "allowLong";
  static final String ALLOW_SHORT     = "allowShort";
  static final String BULL_COLOR      = "bullColor";
  static final String BEAR_COLOR      = "bearColor";
  static final String CVD_PATH        = "cvdPath";
  static final String CVD_IND         = "cvdInd";

  /** Per-bar delta cache, keyed by bar start time. */
  private final Map<Long, Long> deltaCache = new ConcurrentHashMap<>();

  @Override
  public void initialize(Defaults defaults) {
    SettingsDescriptor sd = createSD();

    // ---------------- General ----------------
    SettingTab tab = sd.addTab("General");
    SettingGroup gen = tab.addGroup("Session");
    gen.addRow(new BooleanDescriptor(SESSION_RTH, "Regular Trading Hours only", Boolean.TRUE));
    gen.addRow(new BooleanDescriptor(RESET_SESSION, "Reset CVD at session open", Boolean.TRUE));

    SettingGroup sig = tab.addGroup("Divergence");
    sig.addRow(new IntegerDescriptor(PIVOT_STRENGTH, "Pivot Strength (bars each side)", 3, 1, 50, 1));
    sig.addRow(new IntegerDescriptor(MAX_LOOKBACK, "Max Lookback (bars)", 60, 5, 500, 1));
    sig.addRow(new DoubleDescriptor(MIN_CVD_DIV, "Min CVD Divergence (contracts)", 250, 0, 100000, 25));
    sig.addRow(new IntegerDescriptor(MIN_PRICE_TICKS, "Min Price Divergence (ticks)", 2, 0, 100, 1));

    SettingGroup filt = tab.addGroup("Filters");
    filt.addRow(new IntegerDescriptor(MIN_BAR_VOLUME, "Min Bar Volume (0 = off)", 0, 0, 1000000, 100));
    filt.addRow(new IntegerDescriptor(SKIP_FIRST_MIN, "Skip First N Minutes of Session", 15, 0, 240, 5));
    filt.addRow(new BooleanDescriptor(ALLOW_LONG, "Enable Bullish Signals", Boolean.TRUE));
    filt.addRow(new BooleanDescriptor(ALLOW_SHORT, "Enable Bearish Signals", Boolean.TRUE));

    // ---------------- Display ----------------
    tab = sd.addTab("Display");
    SettingGroup disp = tab.addGroup("CVD");
    disp.addRow(new PathDescriptor(CVD_PATH, "CVD Path", defaults.getLineColor(), 1.5f, null, true, false, true));
    disp.addRow(new IndicatorDescriptor(CVD_IND, "CVD Indicator", defaults.getLineColor(), null, false, true, true));

    SettingGroup marks = tab.addGroup("Markers");
    marks.addRow(new ColorDescriptor(BULL_COLOR, "Bullish Color", new Color(0, 180, 120)));
    marks.addRow(new ColorDescriptor(BEAR_COLOR, "Bearish Color", new Color(225, 60, 60)));
    marks.addRow(new MarkerDescriptor(Inputs.UP_MARKER, "Bullish Marker", Enums.MarkerType.TRIANGLE,
        Enums.Size.SMALL, new Color(0, 180, 120), defaults.getLineColor(), true, true));
    marks.addRow(new MarkerDescriptor(Inputs.DOWN_MARKER, "Bearish Marker", Enums.MarkerType.TRIANGLE,
        Enums.Size.SMALL, new Color(225, 60, 60), defaults.getLineColor(), true, true));

    SettingGroup legend = tab.addGroup("Legend");
    legend.addRow(new LabelDescriptor("Bullish = price lower low + CVD higher low (sellers absorbed)"));
    legend.addRow(new LabelDescriptor("Bearish = price higher high + CVD lower high (buyers absorbed)"));
    legend.addRow(new LabelDescriptor("Signals are raised on the bar that confirms the swing (no look-ahead)."));

    // ---------------- Runtime ----------------
    RuntimeDescriptor desc = createRD();
    desc.setLabelSettings(SESSION_RTH, PIVOT_STRENGTH, MAX_LOOKBACK, MIN_CVD_DIV);
    desc.exportValue(new ValueDescriptor(Values.DELTA, "Bar Delta", new String[] { SESSION_RTH }));
    desc.exportValue(new ValueDescriptor(Values.CVD, "Cumulative Delta",
        new String[] { SESSION_RTH, RESET_SESSION }));
    desc.exportValue(new ValueDescriptor(Signals.BULL_DIV, Enums.ValueType.BOOLEAN, "Bullish Divergence", null));
    desc.exportValue(new ValueDescriptor(Signals.BEAR_DIV, Enums.ValueType.BOOLEAN, "Bearish Divergence", null));

    desc.declarePath(Values.CVD, CVD_PATH);
    desc.declareIndicator(Values.CVD, CVD_IND);
    desc.declareSignal(Signals.BULL_DIV, "Bullish Divergence");
    desc.declareSignal(Signals.BEAR_DIV, "Bearish Divergence");
    desc.setRangeKeys(Values.CVD);
    desc.addHorizontalLine(new LineInfo(0, defaults.getGrey(), 1.0f, new float[] { 3, 3 }));
  }

  @Override
  public void onLoad(Defaults defaults) {
    int strength = getSettings().getInteger(PIVOT_STRENGTH);
    int lookback = getSettings().getInteger(MAX_LOOKBACK);
    setMinBars(Math.max(2 * strength + 2, lookback + strength + 2));
  }

  @Override
  protected void calculate(int index, DataContext ctx) {
    DataSeries series = ctx.getDataSeries();
    Instrument inst = ctx.getInstrument();
    if (series == null || inst == null) return;
    if (index < 1) { series.setDouble(index, Values.CVD, 0.0); return; }

    // ---- 1. delta for this bar (cached by bar start time) ----
    long barStart = series.getStartTime(index);
    long barEnd = series.getEndTime(index);
    double delta = barDelta(inst, barStart, barEnd);
    series.setDouble(index, Values.DELTA, delta);

    // ---- 2. cumulative delta, session anchored ----
    boolean resetSession = getSettings().getBoolean(RESET_SESSION, true);
    boolean rthOnly = getSettings().getBoolean(SESSION_RTH, true);
    double cvd;
    if (resetSession && isNewSession(inst, series, index, rthOnly)) cvd = delta;
    else cvd = series.getDouble(index - 1, Values.CVD, 0.0) + delta;
    series.setDouble(index, Values.CVD, cvd);

    // ---- 3. divergence detection ----
    int strength = getSettings().getInteger(PIVOT_STRENGTH);
    int lookback = getSettings().getInteger(MAX_LOOKBACK);
    int minTicks = getSettings().getInteger(MIN_PRICE_TICKS);
    double minCvdDiv = getSettings().getDouble(MIN_CVD_DIV);
    int minVolume = getSettings().getInteger(MIN_BAR_VOLUME);
    int skipFirst = getSettings().getInteger(SKIP_FIRST_MIN);
    boolean allowLong = getSettings().getBoolean(ALLOW_LONG, true);
    boolean allowShort = getSettings().getBoolean(ALLOW_SHORT, true);

    int p = index - strength;                    // candidate swing bar, confirmed now
    if (p < strength) { series.setComplete(index); return; }

    if (minVolume > 0 && series.getVolume(p) < minVolume) { series.setComplete(index); return; }
    if (skipFirst > 0 && minutesIntoSession(inst, series, p, rthOnly) < skipFirst) {
      series.setComplete(index); return;
    }

    double tick = inst.getTickSize();
    boolean bull = false, bear = false;
    int prior = -1;

    if (isPivotLow(series, p, strength)) {
      prior = findPriorPivotLow(series, p, strength, lookback);
      if (prior >= 0) {
        double priceLowNow = series.getLow(p);
        double priceLowPrior = series.getLow(prior);
        double cvdNow = series.getDouble(p, Values.CVD, Double.NaN);
        double cvdPrior = series.getDouble(prior, Values.CVD, Double.NaN);
        if (!Double.isNaN(cvdNow) && !Double.isNaN(cvdPrior)) {
          bull = (priceLowNow < priceLowPrior - minTicks * tick)
              && (cvdNow > cvdPrior + minCvdDiv);
        }
      }
    } else if (isPivotHigh(series, p, strength)) {
      prior = findPriorPivotHigh(series, p, strength, lookback);
      if (prior >= 0) {
        double priceHighNow = series.getHigh(p);
        double priceHighPrior = series.getHigh(prior);
        double cvdNow = series.getDouble(p, Values.CVD, Double.NaN);
        double cvdPrior = series.getDouble(prior, Values.CVD, Double.NaN);
        if (!Double.isNaN(cvdNow) && !Double.isNaN(cvdPrior)) {
          bear = (priceHighNow > priceHighPrior + minTicks * tick)
              && (cvdNow < cvdPrior - minCvdDiv);
        }
      }
    }

    // only raise a signal once per bar (calculate may be re-run on recalculation)
    boolean raisedBull = series.getBoolean(index, Signals.BULL_DIV, false);
    boolean raisedBear = series.getBoolean(index, Signals.BEAR_DIV, false);

    if (bull && allowLong && !raisedBull) {
      series.setBoolean(index, Signals.BULL_DIV, Boolean.TRUE);
      double cvdAtPivot = series.getDouble(p, Values.CVD, 0.0);
      String msg = "Bullish CVD divergence: price low " + Util.round(series.getLow(p), 2)
          + " vs " + Util.round(series.getLow(prior), 2)
          + ", CVD " + Util.round(cvdAtPivot, 0) + " vs " + Util.round(series.getDouble(prior, Values.CVD, 0.0), 0);
      MarkerInfo marker = getSettings().getMarker(Inputs.UP_MARKER);
      if (marker != null && marker.isEnabled()) {
        addFigure(new Marker(new Coordinate(series.getStartTime(p), cvdAtPivot),
            Enums.Position.BOTTOM, marker, msg));
      }
      ctx.signal(index, Signals.BULL_DIV, msg, series.getLow(p));
    } else if (bull) {
      series.setBoolean(index, Signals.BULL_DIV, Boolean.TRUE);
    }

    if (bear && allowShort && !raisedBear) {
      series.setBoolean(index, Signals.BEAR_DIV, Boolean.TRUE);
      double cvdAtPivot = series.getDouble(p, Values.CVD, 0.0);
      String msg = "Bearish CVD divergence: price high " + Util.round(series.getHigh(p), 2)
          + " vs " + Util.round(series.getHigh(prior), 2)
          + ", CVD " + Util.round(cvdAtPivot, 0) + " vs " + Util.round(series.getDouble(prior, Values.CVD, 0.0), 0);
      MarkerInfo marker = getSettings().getMarker(Inputs.DOWN_MARKER);
      if (marker != null && marker.isEnabled()) {
        addFigure(new Marker(new Coordinate(series.getStartTime(p), cvdAtPivot),
            Enums.Position.TOP, marker, msg));
      }
      ctx.signal(index, Signals.BEAR_DIV, msg, series.getHigh(p));
    } else if (bear) {
      series.setBoolean(index, Signals.BEAR_DIV, Boolean.TRUE);
    }

    series.setComplete(index);
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  /**
   * Aggregates the delta (ask volume - bid volume) for the given time range from
   * the instrument's tick history. Results are cached by bar start time so that a
   * recalculation over thousands of bars does not re-read the tick store.
   */
  private long barDelta(Instrument inst, long start, long end) {
    Long cached = deltaCache.get(start);
    if (cached != null) return cached;
    final long[] acc = new long[] { 0L, 0L };   // [askVolume, bidVolume]
    try {
      inst.forEachTick(start, end, new com.motivewave.platform.sdk.common.TickOperation() {
        @Override
        public void onTick(Tick t) {
          if (t.isAskTick()) acc[0] += t.getVolume();
          else acc[1] += t.getVolume();
        }
      });
    } catch (Exception e) {
      return 0L;   // ticks not available (yet) - next pass will retry
    }
    long delta = acc[0] - acc[1];
    deltaCache.put(start, delta);
    return delta;
  }

  /** True if this bar starts a new trading session (CVD is reset here). */
  private boolean isNewSession(Instrument inst, DataSeries series, int index, boolean rthOnly) {
    if (index < 1) return true;
    long prev = series.getStartTime(index - 1);
    long cur = series.getStartTime(index);
    if (cur - prev > 12L * 60L * 60L * 1000L) return true;   // gap (overnight / weekend)
    return inst.getStartOfDay(prev, rthOnly) != inst.getStartOfDay(cur, rthOnly);
  }

  /** Minutes elapsed since the session open for the bar at the given index. */
  private long minutesIntoSession(Instrument inst, DataSeries series, int index, boolean rthOnly) {
    long dayStart = inst.getStartOfDay(series.getStartTime(index), rthOnly);
    return (series.getStartTime(index) - dayStart) / 60000L;
  }

  /** A bar is a pivot low if its low is the lowest of the surrounding 2*strength+1 bars. */
  private boolean isPivotLow(DataSeries series, int i, int strength) {
    double low = series.getLow(i);
    for (int k = i - strength; k <= i + strength; k++) {
      if (k == i) continue;
      if (k < 0 || k > series.size() - 1) return false;
      if (series.getLow(k) <= low) return false;
    }
    return true;
  }

  /** A bar is a pivot high if its high is the highest of the surrounding 2*strength+1 bars. */
  private boolean isPivotHigh(DataSeries series, int i, int strength) {
    double high = series.getHigh(i);
    for (int k = i - strength; k <= i + strength; k++) {
      if (k == i) continue;
      if (k < 0 || k > series.size() - 1) return false;
      if (series.getHigh(k) >= high) return false;
    }
    return true;
  }

  /** Most recent pivot low strictly before (i - strength), within the lookback window. */
  private int findPriorPivotLow(DataSeries series, int i, int strength, int lookback) {
    int from = Math.max(strength, i - lookback);
    for (int j = i - strength; j >= from; j--) {
      if (isPivotLow(series, j, strength)) return j;
    }
    return -1;
  }

  /** Most recent pivot high strictly before (i - strength), within the lookback window. */
  private int findPriorPivotHigh(DataSeries series, int i, int strength, int lookback) {
    int from = Math.max(strength, i - lookback);
    for (int j = i - strength; j >= from; j--) {
      if (isPivotHigh(series, j, strength)) return j;
    }
    return -1;
  }
}
