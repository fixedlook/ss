# Approach speed — real-time test. The lead does not replicate.

## Why this test is the authoritative one

The earlier case-control study found "fast approach to a swing point" at 1.43-2.51x.
But it used a **zigzag**, which only confirms a pivot *after* price has moved
25 points away. That is hindsight, and it cannot be traded.

This version removes hindsight completely:

| element | how it is measured |
|---|---|
| event | price crosses an LVN box midpoint — observable at the bar close |
| speed | \|close[i] − close[i−10]\| / 10, from bars **before** the event |
| normalisation | percentile of that speed among all 10-min windows **so far this session** |
| race | ±100 pts from the event close; reversal = the side price came from |

Self-normalised speed removes the cross-session volatility confound that
survived the earlier controls.

**The harness validates itself: the overall reversal rate came out 49.91% on a
symmetric 100-point race.** Perfectly centred. No bias.

## Result: nothing

| | n | reversal rate | 95% CI |
|---|---|---|---|
| **LVN + fast** | 470 | **46.17%** | [41.29, 51.19] |
| LVN + normal | 1,185 | 51.98% | [49.04, 55.10] |
| elsewhere + fast | 228 | 48.25% | [42.38, 54.27] |
| elsewhere + normal | 3,503 | 49.81% | [48.31, 51.38] |

- box effect, given fast: **−2.08pp**
- speed effect, given box: **−5.81pp**
- speed effect, elsewhere: −1.57pp

**Fast approach at an LVN box reverses LESS than a slow approach** (46.2% vs
52.0%). Neither difference is significant, but the direction is the opposite of
the hypothesis. The speed lead is dead.

## Why the zigzag version looked positive

In a zigzag, "fast incoming leg" is a proxy for **the session being in a
rapid-swing regime** — the zigzag is producing pivots quickly. In that regime the
*outgoing* legs are also large, so the pivot gets classed as a "case" more often.

The developing-range control used earlier was computed over the whole session so
far, which is too coarse to catch this. Self-normalised real-time speed removes
it entirely, and the effect disappears.

This is the same class of error as the VWAP bug: the measurement contained the
thing it was trying to predict.

## One directional signal, stated as a lead not a finding

| speed percentile | n | reversal rate | 95% CI |
|---|---|---|---|
| bottom 50% | 2,703 | 50.65% | [48.72, 52.60] |
| 50-75th | 1,263 | 50.28% | [47.61, 53.00] |
| 75-90th | 722 | 49.45% | [45.72, 53.05] |
| 90-95th | 262 | 54.96% | [48.87, 61.11] |
| **top 5%** | 436 | **41.97%** | **[36.96, 47.23]** |

The fastest 5% of approaches reverse only 42% of the time — i.e. they
**continue 58% of the time**. That clears the clustered CI, and it is
mechanistically sensible: price sprinting into a thin area slices through it.
It is the liquidity-vacuum story rather than the rejection story.

**But it is non-monotonic** — 90-95th is 54.96%, top 5% is 41.97%. Neighbouring
buckets point opposite ways, which is what noise looks like. One significant
bucket out of five, in a non-monotonic pattern, is not a finding. Flagging it as
a hypothesis worth a pre-committed test, and nothing more.

## Trigger frequency

- LVN entries: **1.22 per session**
- of which fast (top decile): **0.35 per session**
- fast LVN entries across the whole 8-year sample: 470

Even if the effect were real, it would fire about once every three sessions.

## Conclusion

Approach speed does not predict reversals at LVN boxes, measured in real time
with no hindsight. The earlier positive result was an artifact of how the zigzag
identifies pivots.

## Limitations

- LVN boxes built from bar-estimated session profiles (volume spread evenly
  across each bar's range), not true footprint.
- 100-point symmetric barrier, user-specified.
- RTH only, one instrument, 1,361 sessions with events.
