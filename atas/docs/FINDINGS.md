# Draw-on-liquidity targeting: what the MNQ data actually says

Everything below was measured on your own exports. Numbers are reproduced by the
scripts in `atas/research/`, run in order. Nothing here is taken from a
trading-education source.

## 0. The question, stated so it can be measured

> "I am in a trade at price E, with my stop S points away. Which un-swept
> liquidity level is price most likely to reach before my stop?"

That is a first-passage race, so it has a clean label:

**PoT = P(target touched before the stop is touched)**.

Labelling rule used throughout: walk forward one bar at a time. If a single
1-minute bar spans *both* the target and the stop, the bar is counted as a
**stop-out**. This biases every number downward, so displayed probabilities are
mildly conservative rather than flattering.

## 1. The data

| Source | Span | Content | Days |
|---|---|---|---|
| `mnq_flow.csv.gz` | 2019-07 → 2026-09 | 24h OHLC, order flow **RTH only** | 1,762 |
| `mnq_bidask.csv` | 2026-06 → 2026-10 | 24h OHLC, volume RTH only | ~70 |
| `mnq_bidask.footprint (2).zip` | 2026-02 → 2026-10 | per-minute, per-price bid/ask | 164 |

Tick = 0.25 points. Median RTH range = 367 pts.

**Integrity check:** RTH bars rebuilt from the footprint were cross-checked
against the independent 24h OHLC source — highs agree within one tick on
**99.86%** of 62,955 overlapping minutes. The two datasets are consistent.

**A finding that changed the plan:** order flow exists only inside the regular
session (68% of RTH bars on average, 0% overnight, and effectively absent during
2025). So any model needing delta/limit-order data can only ever be fitted on a
subset of history — which turned out not to matter, see §3.

## 2. Base rates, before any model

Unconditional probability that a candidate target is reached before the stop,
over 91,976 setups:

| Stop | P(target first) |
|---|---|
| 20 pt | 20.5% |
| 40 pt | 29.5% |
| 60 pt | 35.4% |

## 3. Order flow added nothing — this is the most important result

275,928 rows, split **by trading day** (114 days train / 49 test), never
randomly, so no path information leaks across the split.

| Model | AUC | Brier |
|---|---|---|
| Structure only (gradient boosting) | **0.8179** | **0.1456** |
| Structure + order flow | 0.8131 | 0.1484 |
| Structure only (logistic) | 0.8069 | 0.1501 |
| Structure + order flow (logistic) | 0.8074 | 0.1499 |

Adding order flow made the model *slightly worse*. Feature importance confirms
why — the flow features sit at the bottom:

| Feature | AUC drop when shuffled |
|---|---|
| `rr` (reward:risk) | **+0.1552** |
| `dist_atr` | +0.0411 |
| `vol_rank` (prior-day volume at that price) | +0.0243 |
| `direction` | +0.0219 |
| `untested` | +0.0197 |
| `b_vol`, `a_vol`, `cum_delta`, `delta5` | ≈ 0.004–0.006 |

`rr` alone matters four times more than anything else. The reachable conclusion:
**touch probability is dominated by how far away the level is**, and the
order-flow state adds essentially no information about it at this horizon.

This is the honest answer to "can't you use resting limit orders etc.": the
dataset's own evidence says those inputs do not improve the forecast. So the
indicator does not use them, and the probability table needs no footprint data
at all — which is why it could be fitted on the full 1,460 days.

## 4. Sophisticated target *selection* is unnecessary

If the model's job is to pick the best of the available levels, it loses to a
one-line rule. Tested with a minimum R:R floor, identical setups and candidates:

| R:R floor | setups | nearest qualifying | model's pick | best achievable |
|---|---|---|---|---|
| 1.0× | 30,155 | **25.9%** | 24.5% | 25.9% |
| 1.5× | 27,633 | **20.0%** | 19.1% | 20.0% |
| 2.0× | 24,830 | **16.3%** | 15.6% | 16.3% |
| 3.0× | 19,153 | **11.7%** | 11.3% | 11.7% |

"Best achievable" equals "nearest qualifying" at **every** floor, meaning the
highest-probability qualifying target simply *is* the nearest one. There are only
~2.4 candidates meeting any floor at a given moment, so ranking is nearly a
non-problem. The model picks slightly farther levels (103 vs 97 pts) and is
therefore marginally worse.

**Therefore the indicator ranks by distance. No ML model is shipped.**

## 5. Are the ICT PD arrays any good? Yes — but not for the reason it first looks

A first pass showed swings and FVGs at 88% versus 29% for session levels. That
was an artefact: the two families were sampled at wildly different distances.

| Level | median distance |
|---|---|
| FVG | 4.0 pts |
| Swing high | 6.5 pts |
| PDH | 105.5 pts |
| IBH | 85.0 pts |

Held at **identical distance**, and with strict "never traded through since it
formed" definitions, the advantage survives at the distances that matter:

| R:R bucket | session levels | swing | fvg |
|---|---|---|---|
| 1.5–2 | 29.9% | 34.3% | 32.1% |
| 2–3 | 19.9% | **26.8%** | 25.3% |
| 3–4 | 12.4% | **20.9%** | 17.5% |
| 4–6 | 6.3% | **14.1%** | 15.0% |

Roughly 1.3–2.2× at tradable distances. So swings and unfilled FVGs are kept,
as you asked — the data supports them. Note they are *always close by*
(nothing beyond ~100 pts), so they serve as nearby targets, not distant ones.

Also worth recording: strictly-untouched session levels beat loosely-defined
ones (0.5–1 R:R: 51.5% → 55.6%). Untouched levels are genuinely stronger
magnets, which is why the indicator refuses levels price has already swept.

## 6. Two biases found and removed

**Look-ahead in the initial balance.** The first fit used the *completed* IB
high/low even when standing inside the IB window, so it "knew" a range that had
not finished forming. Symptom: IBH/IBL showed 33% when their realised rate was
80%. Fixed by admitting IB levels only after the window closes; the table lost
76,239 contaminated observations (4,269,846 → 4,193,607).

**Swing candidacy.** The fit counts a swing only when the centre bar is a strict
maximum from the centre onward. The indicator now applies the same test, so an
equal-high cluster is never reported with a probability that never measured it.

## 7. The shipped table, and its calibration

`Pot[group, stop, rrBucket, timeBucket]` — 5 groups × 3 stops × 8 R:R buckets ×
3 time buckets = 360 cells, fitted on 1,460 days. Cells with n < 120 fall back to
the time-pooled rate, then the R:R-pooled rate, so no lookup returns a number
fitted on a handful of cases.

QA (`13_verify_table.py`): 360/360 cells present, 0 out-of-band values, 0
recomputation mismatches, PoT monotonically falls as R:R rises and rises as the
stop widens.

**End-to-end replay of the indicator's own logic** (`14_validate_indicator.py`),
19,527 displayed targets across 1,347 days:

| displayed PoT | realised | n |
|---|---|---|
| 9.6% | 10.8% | 6,602 |
| 25.7% | 26.1% | 4,937 |
| 33.4% | 31.7% | 7,988 |
| **overall 23.4%** | **23.2%** | **19,527** |

By family: fvg 29.4% → 29.9%, swing 31.5% → 29.3%, session 14.8% → 16.0%,
PDC 16.2% → 19.6%. The number on the box is the number you get.

## 8. Honest limitations

- Fitted on **MNQ only**. The shape of the result (distance dominates) should
  transfer, but the percentages are MNQ-specific and should not be read onto
  other instruments without refitting.
- Calibrated on **RTH only**, which is why targets hide outside it by default.
- The label uses 1-minute first-touch. Sub-minute sequencing cannot be recovered
  from this data, hence the deliberately conservative same-bar rule.
- ~1.5% of the 360 cells are thin (n < 120) and rely on the pooled fallback.
- The indicator cannot read your position — the ATAS *indicator* SDK does not
  expose positions, orders or P&L. Direction, entry and stop are therefore inputs
  (or the Auto rule reads the side of the session open). Reading a live position
  would require the strategy/algo API, a different project.
