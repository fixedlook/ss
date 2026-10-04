# What the day's biggest rejections have in common

The user's question: 85% of the biggest rejections had no prior-session level
within 5 points. Do they share anything else?

Method: take the 1,637 biggest rejections across 1,851 sessions (zigzag pivots,
largest departing move) and characterise the rejections THEMSELVES — their shape,
timing, position, and what preceded them. Every statistic against a null.

## Finding 1: over half are simply the day's high or low

| | |
|---|---|
| biggest rejection IS the session high or low | **53.7%** |
| at the high | 28.5% |
| at the low | 25.2% |
| a random bar is either | 0.5% |

Terminal pivots are ~25% of all zigzag pivots but win the "biggest departing
move" contest 54% of the time — roughly 2x over-represented.

**Consequence: half the time the day's biggest reversal is the day's final
extreme, which is unknowable in advance. Those are not tradeable.** The
tradeable subset is the 46% of intermediate pivots.

## Finding 2: the timing is NOT a discovery

| | |
|---|---|
| biggest rejection within the first hour | 61.3% |
| day's high or low within the first hour | **77.0%** |

The rejections are LESS early than the day's extremes themselves. So the time
concentration carries no information beyond the well-known fact that the
session's extremes usually form early. Discarded.

## Finding 3: the volume signature does NOT survive

Raw numbers looked impressive:

| measure | value |
|---|---|
| pivot bar volume / day median | 3.12x |
| ordinary local extreme / day median | 1.09x |
| pivot bar range / day median range | 2.08x |

But controlling for time of day (comparing each bar against other bars at the
SAME minute of day, which removes the U-shaped intraday volume curve):

| group | volume vs same-minute median |
|---|---|
| biggest rejection | **1.34x** |
| ordinary local extreme | 1.05x |
| any random bar | 1.00x |

So the real figure is 1.34x, not 3.12x — the rest was the time-of-day shape.
1.34x is the 69th percentile of its own minute of day. A third of all bars are
heavier.

**And the decisive test — does volume scale with the size of the reversal?**

| rank of departing move | median move | volume ratio to clock |
|---|---|---|
| 1st (biggest) | 80.5% of range | **1.37** |
| 2nd | 61.2% | 1.46 |
| 3rd | 51.3% | 1.48 |
| 4th | 44.2% | 1.50 |
| 5th | 40.6% | 1.49 |

Flat, and the biggest reversal has the LOWEST ratio of the five. **Volume does
not identify the biggest reversal.** Heavy bars come with large swings in
general, not with the largest one specifically. No monotonic relationship.

## Finding 4: intraday re-tests — inconclusive as measured

| | |
|---|---|
| rejection price already traded earlier that session | 77.7% |
| ...excluding the approach leg | 40.5% |
| random bar, same test | 99.6% |

The control is unfair: random bars sit in the middle of the range where every
price gets visited, while rejection pivots sit at the extremes. The lift figure
(0.41x) should not be read as meaningful. The only solid number is that 40.5% of
the biggest rejections are prices the session visited earlier and came back to.

## Conclusion

The things the day's biggest rejections share are **definitional, not
informative**:

1. They are at the extremes of the day's range — that is what "biggest reversal"
   means.
2. They mostly happen early — because the day's extremes form early.
3. Their bars are marginally heavier (1.34x, 69th percentile) — and the heaviness
   does not distinguish them from other swings.

**Nothing found here is usable as a filter.** No volume signature, no timing
edge, no shape, no path. Combined with the 85% result, the day's biggest
reversal is not predictable from price, time, range, volume, or prior structure.

## Limitations

- Zigzag threshold 25% of the day's range; different thresholds select different
  pivots.
- Bar volume only. Order-flow splits (buy vs sell, aggression, trade size) are
  not in this data.
- "Terminal" defined as within 0.25 points of the session extreme.
- One instrument, 1,851 sessions, RTH only.
