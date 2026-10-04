# Valid reversals — defined the user's way. Nothing predicts them.

## The corrected definition

User: *"market was bearish and then it turned bullish by going up like 100pts.
100pts is the minimum for the rej to be valid."*

A reversal = direction changes AND both the incoming leg and the outgoing leg are
**at least 100 points absolute**. Not 25% of range, not the biggest candle.

**3,612 such reversals across 853 sessions = 4.23 per session.**

| property | value |
|---|---|
| median incoming leg | 161 pts |
| median outgoing leg | 159 pts |
| top-out vs bottom-out | 50.6% / 49.4% |
| **IS the session high or low** | **21.4%** |
| **intermediate (tradeable in principle)** | **78.6%** |

The user's definition identifies genuinely tradeable events: 79% are NOT the
day's final extreme, unlike the earlier "biggest rejection" definition where 54%
were terminal and therefore unknowable in advance.

Distribution through the day: 33.8% in the first hour, decaying smoothly to 3.7%
in the last. Position in range: essentially flat across all five fifths
(24.0 / 15.7 / 21.3 / 17.2 / 21.8 %). No positional bias.

## What is at these reversals: nothing

| feature | at reversal | price space | lift | 95% CI |
|---|---|---|---|---|
| prior_high | 0.9% | 1.6% | **0.59** | [0.36, 0.86] |
| va_high | 1.2% | 1.8% | **0.66** | [0.43, 0.94] |
| lvn_bands | 10.4% | 12.2% | **0.85** | [0.74, 0.97] |
| prior_low | 1.2% | 1.6% | 0.77 | [0.52, 1.03] |
| prior_close | 1.9% | 2.3% | 0.84 | [0.62, 1.09] |
| va_low | 1.5% | 1.8% | 0.88 | [0.59, 1.20] |
| poc | 1.9% | 2.0% | 0.94 | [0.60, 1.30] |

Three features are **significantly BELOW chance** — reversals happen *less* often
at these levels than at random prices. This is the opposite of the original
hypothesis. A 100-point reversal needs room to move; levels are where price is
congested.

## The volume lead — and why it dies

Raw measurement looked like a major finding:

| measure | value |
|---|---|
| volume of the turning bar, percentile within its own minute of day | **93.5%** |
| above the 90th percentile | 60.3% of reversals |
| above the 75th percentile | 82.9% of reversals |
| share of swing points in the top 10% of their minute | 32.2% (vs 10% expected, 3.2x) |

And it survives controlling for bar range. On a fine (25-point) zigzag,
P(a >=100pt reversal departs) rises monotonically with volume:

| volume percentile at the pivot | n | median move | P(>=100pt) |
|---|---|---|---|
| 0-50 | 11,629 | 44p | 6.1% |
| 50-75 | 17,340 | 43p | 5.7% |
| 75-90 | 20,571 | 44p | 6.5% |
| 90-95 | 10,087 | 46p | 8.2% |
| **95-100** | **13,486** | **51p** | **11.9%** |

Nearly doubles: 6.1% -> 11.9%.

### But the control kills it

High volume means high volatility, so big moves follow high-volume bars
ANYWHERE, not only at turning points. Tested at random bars:

| volume | at a swing point | at a random bar |
|---|---|---|
| below median | 6.2% | 0.4% |
| 50-90th | 6.2% | 1.4% |
| 90-95th | 8.2% | 6.8% |
| **top 5%** | **11.9%** | **13.0%** |

**A random high-volume bar beats a swing-point high-volume bar** (13.0% vs
11.9%). Median following move: 49p vs 51p — identical.

Volume does not mark reversals. Volume marks volatility. The apparent signal was
entirely the well-known fact that volatile periods are volatile.

## And the LVN box does not help either

The user's actual thesis: *"the boxes should only be a help to know when to
trust the big trades and not just use it blindly."*

P(>=100pt reversal) at a pivot with top-5% volume:

| | n | P(>=100pt) |
|---|---|---|
| **at a prior-session LVN** | 1,274 | **10.1%** |
| **elsewhere** | 12,212 | **12.1%** |

The box makes it **-2.0pp worse** (~2.2 SE). Not a filter — a slight negative.

## Conclusion

A valid reversal, as the user defines it, is a real and identifiable event:
4.2 per session, 79% of them intermediate rather than terminal. But **nothing
measurable predicts it**:

- Prior levels: below chance, three significantly so
- LVN boxes: below chance
- Volume at the turning bar: real but is pure volatility, and a random
  high-volume bar predicts a bigger move better
- Time of day: tracks the same U-shape everything else does

## What remains untested

Order flow **direction** — buy volume vs sell volume, aggression, trade size.
Bar volume is direction-agnostic: a 3x bar is a 3x bar whether it was all buyers
or all sellers. The footprint file carries `bid_volume`/`ask_volume` at price
level, which is the one input never used in this study.

## Caveats

- 100-point absolute threshold set by the user; MNQ prices ranged 7,500 to 31,000
  over the sample, so early years have proportionally larger moves.
- Bar-estimated session profiles (volume spread evenly across each bar's range),
  not true footprint, for the LVN construction.
- 5-point tolerance for "at a level".
- RTH only, one instrument.
