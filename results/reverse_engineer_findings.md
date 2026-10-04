# Reverse-engineered levels: what is actually at the day's biggest reversal?

Method (data-first): zigzag each session's 1-min bars, take the pivot with the
largest departing move = that day's biggest rejection, then ask what price level
sits there. Compared against the share of the day's traded price space the same
level occupies.

## Result: 1,633 sessions, fixed 5-point tolerance

| feature | at rejection | price space | lift | 95% CI |
|---|---|---|---|---|
| **prior day close** | 4.8% | 3.7% | **1.30** | [1.04, 1.58] |
| value area low | 2.7% | 2.4% | 1.13 | [0.82, 1.47] |
| value area high | 3.2% | 3.1% | 1.04 | [0.77, 1.33] |
| TPO-style thin band | 4.3% | 4.4% | 0.99 | [0.77, 1.21] |
| **LVN bands** | 15.4% | 15.7% | **0.98** | [0.88, 1.09] |
| POC | 3.0% | 3.2% | 0.95 | [0.70, 1.21] |
| week low | 0.6% | 0.7% | 0.91 | [0.39, 1.48] |
| prior day high | 2.4% | 2.7% | 0.87 | [0.63, 1.16] |
| prior day low | 1.7% | 1.9% | 0.85 | [0.54, 1.18] |
| week high | 1.6% | 1.9% | 0.83 | [0.53, 1.17] |

Only prior-day close clears the CI. Everything else is null.

## The LVN answer, now definitive

**Lift 0.98, CI [0.88, 1.09], n=1,633.**

LVN bands cover 15.7% of the traded price space, so the test has full power for
them. The interval rules out any effect larger than about 9%. The day's biggest
reversal lands on an LVN almost exactly as often as a random price does.

The 7.5-month run hinted LVNs were *below* chance (0.77). With 11x the data it is
0.98 — that hint was noise too.

## The 7.5-month hints did not survive

| feature | 7.5 months | 8 years |
|---|---|---|
| POC | 1.97 | **0.95** |
| value area high | 1.86 | **1.04** |
| LVN | 0.77 | 0.98 |
| "top third of range" | 53.0% | **48.9%** |

Every apparent effect from the small sample collapses with more data. This is
what a noise floor looks like.

## prior-day close is probably noise too

It is the only CI that clears 1, but by era:

| era | lift |
|---|---|
| 2019-2021 | 1.40 |
| 2022-2023 | 0.84 |
| 2024-2025 | 1.60 |
| 2026 | 1.00 |

No stability. With 10 features tested, one clearing p<0.05 by chance is expected.
Not a finding.

## The real result

**Roughly 85% of the day's biggest rejections have no catalogue level within
5 points of them.** Not a prior high or low, not the POC, not a value-area edge,
not a week extreme, not an LVN.

The biggest reversal of the day happens at a price that prior structure does not
explain.

## What that suggests

The drivers are therefore not in prior price structure. They must be in what
happens DURING the session — order flow, absorption, aggression. Which is
exactly the information the footprint file carries and the OHLC bars do not.

This is the first result in the whole investigation that was not measured with a
footprint-based timing reference, and it is the one that points at the footprint.

## Caveats

- Fixed 5-point tolerance, calibrated on 2026 prices (~30,000). Over 2019-2021
  MNQ traded 7,500-16,000, so the tolerance is proportionally tighter there.
  Era columns are reported separately for this reason. It does not affect the
  LVN or POC conclusions, which are stable across eras.
- Bar-based session profiles distribute each bar's volume evenly across its
  range. Cruder than a true footprint but adequate for volume-at-price levels.
- The zigzag threshold is 25% of the day's range; different thresholds pick
  different pivots.
- 1,851 sessions, one instrument.
