# Retraction — the delta-divergence result was a measurement artifact

## What I told you

At an LVN box, when the entry minute's delta pointed opposite to the prior
10-minute move, reversal was **67.35% vs 46.28% = +21.07pp**, CI [+10.29,+30.82],
in-sample +13.5 / out-of-sample +27.6. I recommended building on it.

## What is actually true

**−0.10pp.** The effect is zero.

| measured correctly | n | reversal |
|---|---|---|
| divergence present | 958 | 49.90% |
| divergence absent | 1,957 | 50.00% |
| **difference** | | **−0.10pp**, CI [−4.49, +4.55] |

## The error

The barrier race used each entry minute's **VWAP** as the reference price from
which the two exit barriers were measured. A minute's VWAP is not the price at
the end of that minute, and the gap depends on the delta sign:

| minute type | close minus VWAP |
|---|---|
| delta > 0 (buying) | **+2.69 pts** (close ends above VWAP) |
| delta < 0 (selling) | **−2.47 pts** (close ends below VWAP) |

Buying pushes price up through the minute, so the volume-weighted average sits
below where the minute ends.

So on a buying minute the reference sat 2.69 points **below** the real price.
With a barrier of about 5 points, the "up" barrier was effectively
`5.0 − 2.69 = 2.3` points away while the "down" barrier was
`5.0 + 2.69 = 7.7` points away — a better-than-3:1 head start.

`delta_divergence` selects minutes where price has been falling but delta is
positive. That is, it selects buying minutes. It predicted "price goes up".
The up barrier was mechanically three times closer. **The signal was measuring
its own definition.**

## The fix

Reference the barriers on an **observed price** — the minute's true close from
your 1-minute OHLC bars — so both barriers are equidistant from a real traded
price:

| reference | divergence | with trend | difference |
|---|---|---|---|
| VWAP (biased) | 60.13% | 40.45% | **+19.68pp** |
| true close (clean) | 50.87% | 51.00% | **−0.13pp** |

Decisive. 139,712 triggers, the effect vanishes completely.

## What survives, measured correctly

**The boxes themselves carry no edge at any barrier width:**

| barrier | zones | random prices | difference |
|---|---|---|---|
| 4 pts | 49.42% | 50.67% | −1.26% |
| 6 pts | 48.90% | 50.73% | −1.82% |
| 8 pts | 49.97% | 50.64% | −0.68% |
| 12 pts | 48.54% | 50.58% | −2.04% |
| 16 pts | 48.20% | 50.69% | −2.50% |
| 25 pts | 48.74% | 50.38% | −1.64% |

**Every concept is null** (D=8, n=2,918 zone arrivals):

| concept | n | with | without | diff | 95% CI |
|---|---|---|---|---|---|
| delta_divergence | 958 | 49.90% | 50.00% | −0.10% | [−4.49, +4.55] |
| delta_with_trend | 1,957 | 50.03% | 49.84% | +0.18% | [−4.32, +4.75] |
| volume_climax | 1,742 | 48.56% | 52.04% | −3.48% | [−7.70, +1.22] |
| big_range | 1,770 | 49.72% | 50.35% | −0.63% | [−4.92, +3.72] |
| low_abs_delta | 310 | 50.65% | 49.88% | +0.76% | [−6.43, +7.77] |
| high_abs_delta | 1,769 | 49.41% | 50.83% | −1.42% | [−6.43, +3.65] |
| fast_approach | 1,459 | 49.28% | 50.65% | −1.37% | [−5.93, +2.85] |
| slow_approach | 1,459 | 50.65% | 49.28% | +1.37% | [−3.45, +5.99] |

**Zone age makes no difference** (never-retired rule):

| age at entry | n | reversal |
|---|---|---|
| 1 session | 176 | 53.98% |
| 2–3 | 243 | 40.74% |
| 4–10 | 527 | 53.51% |
| 11–30 | 910 | 50.77% |
| 31+ | 1,062 | 48.96% |

Non-monotonic, no pattern. Older zones are not better.

## Which earlier results were also affected

Everything measured with a VWAP reference or an unobserved trigger moment:

- `concept_comparison_lvn_v4.csv` — the +21.07pp headline. VOID.
- `concept_comparison_neverretire.csv` — the +25.92pp. VOID.
- `random_price_control.csv` — the +19.69pp "works everywhere". VOID.
- `lvn_findings.md` — the whole conclusions section. SUPERSEDED.
- `concept_findings.md` (the earlier 12-concept study) — used a ±4-tick zone
  definition that was never valid, plus VWAP-style timing. VOID.

## Which results stand

- The **LVN zone definition** itself — calibrated against your hand-marked
  zones, 83% agreement. Unaffected.
- The **coverage math** — 3.9 zones per session, ~9% of the chart with the
  never-traded-through filter, ~57% without any retirement. Unaffected.
- The **ETH data gap** — your chart profile includes overnight, the export
  doesn't. Unaffected.
- The **power math** — unaffected.

## Bottom line

Measured correctly, on 2,918 zone arrivals across 164 sessions:

> **No order-flow concept tested predicts a reversal at an LVN box. The boxes
> do not predict reversals either. Every measured effect is within about
> ±1.5pp of zero, and no confidence interval excludes zero.**

This is a negative result. I am sorry I gave you the opposite one and
recommended you build on it.
