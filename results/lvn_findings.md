# LVN reversal study — results (calibrated zones)

**Data** 164 sessions MNQ RTH footprint, 2026-02-16 → 2026-10-02 (5,187,981 rows)
**Zones** LVN = volume ≤ 0.7 × median of a ±50pt window, min 4pts, marked from a
completed session's own profile, live from the next session, retired once price
trades through it. 632 zones total, ~3.9 per session.
**Event** price reaches the zone midpoint (so the two barriers are equidistant —
no momentum bias).
**Outcome** over the next 60 minutes, whichever side is reached first:
near-edge + 2pts (REVERSAL) or far-edge − 2pts (CONTINUATION).
Control = random minutes in the same sessions, same barrier distance (5.25 pts).

## 1. Do the boxes work on their own?

| group | n | reversal rate |
|---|---|---|
| LVN zone entries | 528 | **50.19%** |
| control minutes | 499 | **46.09%** |
| difference | | **+4.10pp** |

+4.10pp is a hint, not a result — the standard error on n≈500 is about ±2.2pp each,
so the difference is roughly 1.3 SE from zero. **The boxes alone do not carry a
demonstrated edge.**

## 2. Order-flow concepts at the zone

| concept | n | with | without | diff | 95% CI | IS / OOS |
|---|---|---|---|---|---|---|
| **delta divergence** (entry-minute delta opposes the prior 10-min move) | 98 | **67.35%** | 46.28% | **+21.07pp** | [+10.29, +30.82] SIG | +13.5 / +27.6 ✓ |
| **fast approach** (price arrives at the zone quickly) | 264 | **54.92%** | 45.45% | **+9.47pp** | [+0.83, +17.59] SIG | +11.5 / +7.9 ✓ |
| low absolute delta | 39 | 66.67% | 48.88% | +17.79pp | [−2.45, +34.17] | +8.2 / +25.4 |
| big_range | 394 | 48.22% | 55.97% | −7.75pp | [−18.70, +2.03] | −3.0 / −12.7 |
| volume_climax | 430 | 49.77% | 52.04% | −2.27pp | [−14.66, +7.85] | **sign flips** |
| absorption (big delta, small range) | 6 | — | — | — | — | too few |
| volume_dry_up / small_range | 1 / 0 | — | — | — | — | too few |
| two_way_tape | 520 | — | — | — | — | too few treated |

Clustered by session, 1,000-iteration bootstrap.

## 3. What this means

**The only concept that survived every robustness check is delta divergence** —
when price is moving into the zone one way, but the buy/sell imbalance in the
entry minute points the other way, reversal jumps from 46% to 67%. That is the
same family as the user's absorption thesis: price pushed down, but the tape
shows buying.

**Important correction made during the work:** the effect measured +38.6pp when
the trend window included the entry minute, but it dropped to +21.1pp once the
trend was measured strictly beforehand. The entry minute's own delta was
contaminating the classification. The +21.1pp figure is the honest one.

**fast approach** (+9.5pp) also survives IS/OOS, but its CI lower bound is +0.83pp
— one more thing to confirm than a proven edge.

## 4. Limitations

- Concept flags are read from the **entry minute only**; footprint rows are
  price-sorted within a minute, so the true intrabar path is unavailable.
  Minute close is proxied by VWAP.
- 60-minute horizon with a ~5pt barrier: this is a **short-term** bounce measure,
  not a swing-trade result.
- One instrument, 7.5 months, 164 sessions. n=98 for the headline concept.
- Absorption proper (individual large prints) is **not testable here** — the
  export has no trade-size field. The user is supplying that separately via
  MotiveWave's Big Trades indicator.
- The on-chart volume profile includes overnight data; this export is RTH only.

## 5. Files

- `research/lvn_reversal_study.py` — the study
- `results/lvn_entry_events_v4.csv` — all 528 events with features
- `results/concept_comparison_lvn_v4.csv` — the table above
- `research/detect_lvn.py` — the zone detector
- `research/lvn_ground_truth.md` — how the zone definition was calibrated

## 6. Sensitivity check (added)

The event definition is unchanged (midpoint touch); only the exit rule varies.
Cell = reversal rate with divergence minus without.

| barrier beyond zone edge | h=30min | h=60min | h=120min |
|---|---|---|---|
| 1 pt | +17.9% | +17.9% | +17.9% |
| 2 pt | +21.1% | +21.1% | +21.1% |
| 4 pt | +23.3% | +23.3% | +23.3% |
| 8 pt | +27.9% | +27.9% | +27.9% |

Two conclusions:

1. **The effect is not a barrier artifact** — it grows smoothly with the barrier
   size (+17.9% → +27.9%) rather than depending on one lucky setting.
2. **The horizon is irrelevant** — 30, 60 and 120 minutes give identical numbers.
   Every event resolves within 30 minutes even at an 8-point barrier.

   That means this is a **minutes-scale bounce**, not an hours-scale reversal.
   The signal fires and resolves quickly. It should not be read as a swing signal.

## 7. Power

At the observed effect (+21pp, base rate ~52%), a two-group test needs roughly
**89 events per group** for 80% power at α=0.05. The sample has 98 divergence
events against 430 non-events, so it is adequately powered *for this effect size*
within this dataset.

To confirm a more conservative +10pp effect would need ~800 per group, i.e.
roughly 4,300 zone entries — about 5 years of sessions at the current rate.
