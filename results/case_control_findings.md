# Case-control: what distinguishes a swing point that becomes a 100pt reversal?

**Method.** 73,353 fine (25-point) zigzag pivots across 1,796 sessions.
- **Cases** = pivots from which price travelled >= 100 points away: 5,503 (7.50%)
- **Controls** = pivots from which it did not

This is the sharpest test run so far. Instead of "is a level there", it asks:
*given a swing point, what predicts that THIS one becomes a valid reversal?*

## Results

### Idea 1 — PRIOR LEVELS: consistently negative

| feature | case rate if YES | if NO | lift |
|---|---|---|---|
| at any prior-session level | 6.4% | 7.6% | **0.84x** |
| at a round 100 | 8.0% | 7.4% | 1.08x |
| at a round 50 | 7.8% | 7.4% | 1.05x |
| at the developing range extreme | 8.4% | 7.2% | 1.17x |

Cases also sit **further** from every prior level (prior high 240 vs 186 pts,
prior POC 173 vs 147) — reconfirming that big reversals happen where structure
is absent, not where it is.

Round numbers: nothing (1.05-1.08x).

### Idea 2 — TIME OF DAY: real but small, and it is just activity

| window | n | case rate | lift |
|---|---|---|---|
| 09:30-10:00 | 12,944 | **10.3%** | **1.37x** |
| 10:00-10:30 | 8,794 | 8.1% | 1.07x |
| 10:30-11:30 | 12,846 | 6.8% | 0.91x |
| 11:30-13:30 | 16,847 | 6.6% | 0.88x |
| 13:30-15:00 | 11,631 | 6.5% | 0.86x |
| 15:00-16:00 | 8,579 | 6.5% | 0.87x |
| 16:00+ | 1,705 | 9.1% | 1.22x |

Stable across both eras. The "10am hypothesis" is half right — the effect is at
the **open** (1.37x) and the **close** (1.22x), i.e. the standard U-shape of
activity. It is not an independent signal; it is "the market moves more then".

### Idea 3 — RETEST OF AN EARLIER REVERSAL: FAILS

| era | retest | no retest | lift |
|---|---|---|---|
| 2019-2023 | 6.3% | 6.2% | **1.01x** |
| 2024-2026 | 10.6% | 8.4% | 1.26x |

Not stable — zero in the earlier half. **Discarded.** This was the
"self-defining level" idea (price where a reversal already happened), and it does
not hold up.

### Idea 4 — INCOMING SPEED: the one real signal, but smaller than it first looked

How fast price arrives at the swing point (incoming leg points / minutes).

First pass, global threshold: **2.51x**, CI [2.20, 2.84], stable across all four
eras, and holding inside every developing-range quartile *and* every
incoming-leg-size quartile:

| same leg SIZE, varying SPEED | slow 50% | 75-90% | top 10% |
|---|---|---|---|
| leg size top 10% | 5.1% | 18.5% | **32.6%** |

That last table is striking — among arrivals covering the same distance, the fast
ones reverse 6x more often.

**But the clean volatility control shrinks it.** Using each session's own top 10%
as the threshold (the tradeable definition) and controlling with the first
30 minutes' range, which cannot be contaminated by the fast move itself:

| early-session volatility | fast | rest | lift |
|---|---|---|---|
| calm open | 4.9% | 4.9% | **1.00x** |
| normal | 8.9% | 6.3% | 1.42x |
| wild open | 17.3% | 10.2% | 1.69x |
| **overall** | **10.2%** | **7.2%** | **1.43x** |

**So the honest figure is ~1.4x, not 2.5x, and it does nothing on a calm day.**
The global-threshold version was inflated by drawing "fast" pivots mostly from
already-volatile sessions.

## What survives

| idea | verdict |
|---|---|
| **Fast approach to a swing point** | **~1.4x, conditional on an active market. Best lead found.** |
| Time of day (open/close) | 1.2-1.4x, but is just activity |
| Retest of an earlier reversal | fails era split |
| Round numbers | nothing |
| Prior-session levels | **below chance (0.84x)** |
| Developing range extreme | 1.17x, weak |
| Adjacent to any prior level | negative |

## Limitations

- **The zigzag uses hindsight.** A pivot is only confirmed once price moves 25
  points away. So "fast arrival at a swing point" is not directly tradeable as
  written — it needs a real-time proxy (e.g. approach speed into a prior
  session's extreme, or into an LVN box). Not yet built.
- ~25 features tested. At 95% about one false positive is expected; the speed
  finding clears that bar only because it is era-stable and survives four
  different controls.
- Approach speed is still partly volatility: it works on wild days, not calm ones.
- Bar data only. No direction information — cannot separate buying from selling.
