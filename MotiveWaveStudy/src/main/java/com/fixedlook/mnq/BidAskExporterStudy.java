package com.fixedlook.mnq;

import java.awt.Point;
import java.io.BufferedWriter;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.DataSeries;
import com.motivewave.platform.sdk.common.Defaults;
import com.motivewave.platform.sdk.common.DrawContext;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Tick;
import com.motivewave.platform.sdk.common.TickOperation;
import com.motivewave.platform.sdk.common.desc.BooleanDescriptor;
import com.motivewave.platform.sdk.common.desc.IntegerDescriptor;
import com.motivewave.platform.sdk.common.desc.LabelDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingGroup;
import com.motivewave.platform.sdk.common.desc.SettingTab;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.common.desc.StringDescriptor;
import com.motivewave.platform.sdk.common.menu.MenuDescriptor;
import com.motivewave.platform.sdk.common.menu.MenuItem;
import com.motivewave.platform.sdk.common.menu.MenuSeparator;
import com.motivewave.platform.sdk.study.RuntimeDescriptor;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

/**
 * Bid/Ask Exporter - streaming rewrite.
 *
 * <p>Same job as the previous exporter (aggregate bid vs ask volume from the
 * instrument's tick history), but built so it cannot run out of memory:
 *
 * <ul>
 *   <li><b>Chunked tick reads.</b> Instead of one {@code forEachTick} call over
 *       the whole range with everything accumulated in RAM, the range is read a
 *       few thousand bars at a time. Each chunk is written to disk and then
 *       dropped, so peak memory is one chunk, not one decade.</li>
 *   <li><b>Streaming writes.</b> Rows go to the PrintWriter as they are produced.
 *       Nothing is buffered until the end, so a crash can no longer lose the
 *       whole export.</li>
 *   <li><b>Session-aligned chunks.</b> A chunk is extended to the end of its
 *       session, so a session is never split across two chunks (which would
 *       otherwise duplicate its profile rows).</li>
 *   <li><b>Compact profile file.</b> The per-minute x per-price footprint is the
 *       reason the old export reached ~1.9 GB for seven years. It is still
 *       available but OFF by default; what the research actually needs is the
 *       per-session price profile (about 1,600 rows per session) and the
 *       per-minute flow (about 440 rows per session).</li>
 * </ul>
 *
 * <p>Output files, all derived from the configured CSV path:
 * <pre>
 *   &lt;base&gt;.csv            per-minute: OHLC + bid/ask volume, delta, trade counts
 *   &lt;base&gt;.profile.csv    per-session, per-price: bid volume, ask volume, trades
 *   &lt;base&gt;.footprint.csv  per-minute, per-price (optional, large)
 * </pre>
 *
 * <p>MotiveWave does not expose bid/ask volume on a Bar, so the flow comes from
 * {@code Instrument.forEachTick}, exactly as in the previous version. Due to
 * this the export blocks the UI thread while it runs; the output files grow as
 * it goes, so progress can be watched on disk.
 */
@StudyHeader(
    namespace = "com.fixedlook",
    id = "MNQ_BIDASK_EXPORT",
    name = "Bid/Ask Exporter (streaming)",
    label = "BidAsk Export",
    desc = "Exports bid/ask volume from the tick history to CSV in chunks, so memory stays flat "
         + "and long ranges (years) can be exported without crashing.",
    menu = "Order Flow",
    overlay = true,
    studyOverlay = true,
    requiresVolume = true,
    requiresBidAskHistory = true)
public class BidAskExporterStudy extends Study {

  // ---------------- setting keys ----------------
  static final String EXPORT_FILE    = "exportFile";
  static final String WRITE_BARS     = "writeBars";
  static final String WRITE_PROFILE  = "writeProfile";
  static final String WRITE_FOOTPRINT = "writeFootprint";
  static final String MAX_BARS       = "maxBars";
  static final String RTH_ONLY       = "rthOnly";
  static final String CHUNK_BARS     = "chunkBars";

  /** Last status message, shown as the study's popup text. */
  private volatile String status = "";

  @Override
  public void initialize(Defaults defaults) {
    SettingsDescriptor sd = createSD();

    SettingTab tab = sd.addTab("Export");

    SettingGroup out = tab.addGroup("Output");
    out.addRow(new StringDescriptor(EXPORT_FILE, "CSV Path",
        "C:\\mnq\\mnq_bidask.csv"));
    out.addRow(new BooleanDescriptor(WRITE_BARS, "Write bar/minute file", Boolean.TRUE));
    out.addRow(new BooleanDescriptor(WRITE_PROFILE, "Write session price profile", Boolean.TRUE));
    out.addRow(new BooleanDescriptor(WRITE_FOOTPRINT,
        "Write full minute x price footprint (large)", Boolean.FALSE));

    SettingGroup rng = tab.addGroup("Range");
    rng.addRow(new IntegerDescriptor(MAX_BARS, "Max bars (0 = all)", 0, 0, 20000000, 10000));
    rng.addRow(new BooleanDescriptor(RTH_ONLY, "Regular trading hours only", Boolean.TRUE));

    SettingGroup mem = tab.addGroup("Memory");
    mem.addRow(new IntegerDescriptor(CHUNK_BARS, "Bars per tick read", 5000, 200, 200000, 500));
    mem.addRow(new LabelDescriptor("Ticks are read in chunks and written straight to disk."));
    mem.addRow(new LabelDescriptor("Peak memory is one chunk, not the whole range."));
    mem.addRow(new LabelDescriptor("The chart will not respond while an export runs."));

    RuntimeDescriptor desc = createRD();
    desc.setLabelSettings(EXPORT_FILE, MAX_BARS, RTH_ONLY, CHUNK_BARS);
  }

  @Override
  public void clearState() {
    super.clearState();
    status = "";
  }

  @Override
  public String getPopupMessage(double x, double y, DrawContext ctx) {
    return status;
  }

  @Override
  public MenuDescriptor onMenu(String action, Point point, DrawContext ctx) {
    List<MenuItem> items = new ArrayList<>();
    items.add(new MenuSeparator());
    items.add(new MenuItem("Export bid/ask CSV (streaming)", new Runnable() {
      @Override
      public void run() {
        doExport();
      }
    }));
    return new MenuDescriptor(items, true);
  }

  // ------------------------------------------------------------------
  // Session helper
  // ------------------------------------------------------------------

  /** True if the bar at {@code i} starts a new trading session. */
  private static boolean isNewSession(Instrument inst, DataSeries series, int i, boolean rthOnly) {
    if (i < 1) return true;
    long prev = series.getStartTime(i - 1);
    long cur = series.getStartTime(i);
    if (cur - prev > 12L * 60L * 60L * 1000L) return true;   // overnight / weekend gap
    return inst.getStartOfDay(prev, rthOnly) != inst.getStartOfDay(cur, rthOnly);
  }

  private static PrintWriter writer(String file) throws Exception {
    return new PrintWriter(new BufferedWriter(
        new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8)));
  }

  private static void closeQuietly(PrintWriter w) {
    if (w != null) {
      try {
        w.close();
      } catch (Exception ignore) {
        // nothing useful to do here
      }
    }
  }

  // ------------------------------------------------------------------
  // The tick accumulator: bounded memory, writes as it goes
  // ------------------------------------------------------------------

  private final class Acc implements TickOperation {
    private final DataSeries s;
    private final Instrument inst;
    private final int from;
    private final int to;
    private final double tick;
    private final boolean rth;
    private final PrintWriter foot;
    private final PrintWriter prof;

    private final long[] askV;
    private final long[] bidV;
    private final long[] askT;
    private final long[] bidT;

    private int bi;
    private long curSession = Long.MIN_VALUE;
    private final TreeMap<Long, long[]> sess;    // price -> {bid, ask, trades} for the session
    private final TreeMap<Long, long[]> barFp;   // price -> {bid, ask, trades} for the current bar

    long ticks = 0;
    long unmatched = 0;

    Acc(DataSeries s, Instrument inst, int from, int to, double tick, boolean rth,
        PrintWriter foot, PrintWriter prof) {
      this.s = s;
      this.inst = inst;
      this.from = from;
      this.to = to;
      this.tick = tick;
      this.rth = rth;
      this.foot = foot;
      this.prof = prof;
      int m = to - from;
      this.askV = new long[m];
      this.bidV = new long[m];
      this.askT = new long[m];
      this.bidT = new long[m];
      this.bi = from;
      this.sess = (prof != null) ? new TreeMap<Long, long[]>() : null;
      this.barFp = (foot != null) ? new TreeMap<Long, long[]>() : null;
    }

    private long key(double price) {
      return Math.round(price / tick);
    }

    private void flushBarFoot() {
      if (foot == null || barFp == null || barFp.isEmpty()) return;
      long ts = s.getStartTime(bi);
      for (Map.Entry<Long, long[]> e : barFp.entrySet()) {
        long[] v = e.getValue();
        foot.printf("%d,%.4f,%d,%d,%d,%d\n", ts, e.getKey() * tick, v[0], v[1], v[1] - v[0], v[2]);
      }
      barFp.clear();
    }

    private void flushSession() {
      if (prof == null || sess == null || sess.isEmpty()) return;
      for (Map.Entry<Long, long[]> e : sess.entrySet()) {
        long[] v = e.getValue();
        prof.printf("%d,%.4f,%d,%d,%d\n", curSession, e.getKey() * tick, v[0], v[1], v[2]);
      }
      sess.clear();
    }

    /** Flush whatever is still buffered at the end of a chunk. */
    void finish() {
      if (foot != null) flushBarFoot();
      flushSession();
    }

    @Override
    public void onTick(Tick t) {
      ticks++;
      long tt = t.getTime();
      if (tt < s.getStartTime(bi)) {
        unmatched++;
        return;
      }
      while (bi < to - 1 && tt >= s.getStartTime(bi + 1)) {
        if (foot != null) flushBarFoot();
        bi++;
      }
      if (tt >= s.getEndTime(bi)) {
        unmatched++;
        return;
      }

      int k = bi - from;
      int vol = t.getVolume();
      long pk = key(t.getPrice());
      boolean ask = t.isAskTick();

      if (ask) {
        askV[k] += vol;
        askT[k]++;
      } else {
        bidV[k] += vol;
        bidT[k]++;
      }

      if (foot != null && barFp != null) {
        long[] v = barFp.get(pk);
        if (v == null) {
          v = new long[3];
          barFp.put(pk, v);
        }
        if (ask) v[1] += vol; else v[0] += vol;
        v[2]++;
      }

      if (prof != null && sess != null) {
        long ses = inst.getStartOfDay(tt, rth);
        if (ses != curSession) {
          flushSession();
          curSession = ses;
        }
        long[] v = sess.get(pk);
        if (v == null) {
          v = new long[3];
          sess.put(pk, v);
        }
        if (ask) v[1] += vol; else v[0] += vol;
        v[2]++;
      }
    }
  }

  // ------------------------------------------------------------------
  // The export
  // ------------------------------------------------------------------

  private void doExport() {
    PrintWriter wb = null;
    PrintWriter wp = null;
    PrintWriter wf = null;
    try {
      DataContext ctx = getDataContext();
      if (ctx == null) {
        error("No data context.");
        return;
      }
      DataSeries series = ctx.getDataSeries();
      if (series == null) {
        error("No data series.");
        return;
      }
      Instrument inst = series.getInstrument();
      if (inst == null) {
        error("No instrument.");
        return;
      }

      String raw = getSettings().getString(EXPORT_FILE, "");
      if (raw == null || raw.trim().isEmpty()) {
        error("Set a CSV Path in the study settings first (Export tab).");
        return;
      }
      String base = raw.trim().replaceAll("\\.csv$", "");

      int maxBars = getSettings().getInteger(MAX_BARS, 0);
      boolean rth = getSettings().getBoolean(RTH_ONLY, true);
      int chunk = Math.max(200, getSettings().getInteger(CHUNK_BARS, 5000));
      boolean wBars = getSettings().getBoolean(WRITE_BARS, true);
      boolean wProf = getSettings().getBoolean(WRITE_PROFILE, true);
      boolean wFoot = getSettings().getBoolean(WRITE_FOOTPRINT, false);

      int n = series.size();
      if (n <= 0) {
        error("Chart has no bars.");
        return;
      }
      int from = (maxBars > 0 && n > maxBars) ? n - maxBars : 0;

      double ts = inst.getTickSize();
      if (!(ts > 0)) ts = 0.25;
      final double tick = ts;

      if (wBars) {
        wb = writer(base + ".csv");
        wb.println("timestamp,open,high,low,close,volume,bid_volume,ask_volume,"
                 + "delta,trades,ask_trades,bid_trades");
      }
      if (wProf) {
        wp = writer(base + ".profile.csv");
        wp.println("session_start,price,bid_volume,ask_volume,trades");
      }
      if (wFoot) {
        wf = writer(base + ".footprint.csv");
        wf.println("timestamp,price,bid_volume,ask_volume,delta,trades");
      }

      long ticks = 0;
      long unmatched = 0;
      long barsWritten = 0;
      long barsWithFlow = 0;

      status = String.format("Exporting %d bars to %s - MotiveWave will not respond until it finishes.",
          n - from, base);
      info(status);

      int c = from;
      while (c < n) {
        int cEnd = Math.min(c + chunk, n);
        // never split a session across two chunks
        while (cEnd < n && !isNewSession(inst, series, cEnd, rth)) {
          cEnd++;
        }
        long t0 = series.getStartTime(c);
        long t1 = series.getEndTime(cEnd - 1);

        Acc acc = new Acc(series, inst, c, cEnd, tick, rth, wf, wp);
        try {
          inst.forEachTick(t0, t1, rth, acc);
        } catch (Exception ex) {
          info("Tick read failed for bars " + c + ".." + cEnd + ": " + ex.getMessage());
        }
        acc.finish();
        ticks += acc.ticks;
        unmatched += acc.unmatched;

        if (wb != null) {
          for (int i = c; i < cEnd; i++) {
            int k = i - c;
            long bv = acc.bidV[k];
            long av = acc.askV[k];
            long bt = acc.bidT[k];
            long at = acc.askT[k];
            if (av + bv > 0) barsWithFlow++;
            barsWritten++;
            wb.printf("%d,%.4f,%.4f,%.4f,%.4f,%d,%d,%d,%d,%d,%d,%d\n",
                series.getStartTime(i), series.getOpen(i), series.getHigh(i),
                series.getLow(i), series.getClose(i), series.getVolume(i),
                bv, av, av - bv, at + bt, at, bt);
          }
        } else {
          barsWritten += (cEnd - c);
        }

        c = cEnd;
        status = String.format("Exported %d / %d bars...", barsWritten, n - from);
        // progress goes to the log, not to info(): info() may be a modal dialog
        // and this runs once per chunk.
        System.out.println("[BidAskExporter] " + status);
      }

      if (ticks == 0) {
        status = "No ticks returned for this range. Your data feed does not provide "
               + "historical tick data for it - the export cannot work without it.";
        error(status);
      } else {
        StringBuilder failed = new StringBuilder();
        if (wb != null && wb.checkError()) failed.append("bar file ");
        if (wp != null && wp.checkError()) failed.append("profile file ");
        if (wf != null && wf.checkError()) failed.append("footprint file ");
        if (failed.length() > 0) {
          status = "Write failed for the " + failed
                 + "- check the CSV Path and that the file is not open in another program.";
          error(status);
        } else {
          status = String.format(
              "Wrote %d bars to %s. Ticks read %d, unmatched %d. Real bid/ask on %d of %d bars (%.0f%%).",
              barsWritten, base, ticks, unmatched, barsWithFlow, barsWritten,
              barsWritten == 0 ? 0.0 : 100.0 * barsWithFlow / barsWritten);
          info(status);
        }
      }
    } catch (Exception e) {
      status = "Export failed: " + e;
      error(status);
      e.printStackTrace();
    } finally {
      closeQuietly(wb);
      closeQuietly(wp);
      closeQuietly(wf);
    }
  }
}
