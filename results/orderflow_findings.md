# Order flow — first use of the bid/ask split

> ## RETRACTED: the CVD-divergence result in this file is an artifact
>
> See `results/lvn_cvd_findings.md`. This study sampled **every 10th minute** of
> each session (i = 40, 50, 60, …). Re-run on **every minute**, the identical
> divergence flag gives **+0.21%, t = 0.12** — not +3.09%.
>
> The residue-class breakdown in that file shows exactly why: on `i ≡ 0 (mod 10)`
> the effect is +3.09%, and across the other nine residue classes it ranges from
> −1.70% to +2.75%. **The 10-minute grid is the luckiest of ten possible
> samples**, and this study drew precisely it. 200 random 10% subsamples average
> +0.10% (sd 2.34%); only 11% of them reach +3.09%.
>
> What still stands here: the base rates, the row-order bug, the boxes null, the
> power arithmetic, and every *negative* result. What does not stand: the
> divergence signal, the "independent repeat" of it, and the absorption cell.

Every previous test used total volume, which is direction-agnostic. This is the
first that uses **who was buying and who was selling**.

Footprint columns: `bid_volume` (trades at the bid = seller aggression),
`ask_volume` (trades at the ask = buyer aggression), `delta = ask − bid`.

## A bug found and fixed before reporting

The footprint file stores rows **sorted by price** within each minute, so
`.last()` returns the minute's **HIGH**, not its close. Racing symmetric barriers
from the high mechanically favours the downside — visible as P(up) = 42% when it
should be 50%.

**Fix:** take price from the 1-minute OHLC bar file (true closes), order flow from
the footprint, merge on date+minute. Base rates after the fix: **50.05% reversal,
48.91% P(up)**. Clean.

This matters: with the buggy reference, "delta opposes prior move" showed **+3.61%,
CI [+0.50, +6.78], significant**. With the corrected reference it is **−2.06%**.
The bug alone would have produced a false finding.

## Result 1 — order flow carries a small, repeating signal

3,836 events, every 10th minute, 164 sessions. Symmetric ±100pt race.

| feature | with | without | diff | n | 95% CI | IS / OOS |
|---|---|---|---|---|---|---|
| **CVD divergence** | 52.75% | 49.66% | **+3.09%** | 491 | [−2.12, +7.90] | **+2.83 / +3.41 ✓** |
| absorption | 56.82% | 49.97% | +6.84% | 44 | [−7.46, +20.42] | +8.81 / +4.52 |
| extreme buy aggression | 50.00% | 50.06% | −0.06% | 482 | [−4.73, +4.54] | sign flips |
| extreme sell aggression | 48.58% | 50.23% | −1.66% | 422 | [−7.41, +4.12] | sign flips |
| big abs delta | 49.39% | 50.15% | −0.76% | 492 | [−5.29, +3.50] | — |
| volume spike | 44.55% | 50.20% | −5.65% | 101 | [−14.73, +4.08] | −2.15 / −10.77 |
| high trade count | 46.45% | 50.26% | −3.82% | 211 | [−10.27, +3.36] | −0.03 / −9.29 |

**CVD divergence** = cumulative delta moving opposite to price over the last 10
minutes. Price falling while net aggression is positive, or vice versa.

~~It showed +3.09% here and +2.66% in the independent at-box run, with both
halves positive in both runs.~~ **DEAD — see the retraction at the top.** Both
runs used the same 10-minute sampling grid; the "independent repeat" was the same
artifact drawn twice from overlapping minutes. On every minute the effect is
+0.21%.

Directional check: CVD divergence gives P(up) 46.44% vs 49.27% — it does not
cleanly predict direction either.

## Result 2 — the boxes still add nothing

| | n | reversal |
|---|---|---|
| at a box | 174 | **49.43%** |
| elsewhere | 3,601 | **50.15%** |

−0.72pp. Boxes are neutral, the fifth independent confirmation.

## Result 3 — the interaction is UNTESTABLE with this data

The user's actual thesis is flow **at** a box. Those cells are nearly empty:

| flow condition | flow + box | flow alone |
|---|---|---|
| CVD divergence | **n = 6** | 463 |
| absorption | **n = 3** | 42 |
| extreme buy aggression | n = 28 | 459 |
| extreme sell aggression | n = 32 | 393 |

Box entries occur **~1.06 per session**. Requiring a flow condition on top leaves
single-digit samples. **No conclusion about flow-at-a-box is possible from 164
sessions.**

The two suggestive cells, for completeness (too small to use, and the CIs are
enormous):

- extreme buy aggression at a box: 35.71% reversal, n=28, CI [16.67, 54.55] —
  suggests price **continues** through, not reverses
- extreme sell aggression at a box: 40.62% reversal, n=32, CI [20.83, 58.63]

Both point the same way as the earlier finding that the fastest approaches
*continue* — the liquidity-vacuum story rather than rejection. Not usable at this
sample size.

## What is needed

For a 5pp effect at a box, 80% power, two groups: **784 events per group**.

Box entries at 1.06/session gives 174 in 164 sessions. To reach 784 with a flow
filter that retains ~10% of them, the estimate is:

**~10,000 sessions — roughly 40 years of RTH data.**

That is not a reason to give up; it is a reason to **relax the test**. Two options
that keep it honest:

1. Drop the box requirement and test order flow alone (where the samples exist:
   491 CVD-divergence events here). The result would be about order flow, not
   about boxes.
2. Widen what counts as a box: use a looser LVN threshold, or test "in the lower
   half of local volume" rather than a strict LVN. More events, weaker contrast.

## Honest summary

- **Order flow is the right place to look** and this is the first test of it. It
  produced the only sign-consistent candidate in the project (CVD divergence,
  ~+3pp).
- **The boxes remain neutral** — now confirmed on footprint-built boxes, which is
  the highest quality zone source available.
- **The user's specific thesis — flow at a box — cannot be tested with the data
  on hand.** Not a negative result; an untested one.

## Caveats

- 164 sessions, one instrument, RTH only.
- Barriers ±100 points, symmetric, referenced to true bar closes.
- CVD divergence is 1 of ~9 features with multiple cuts; about one false positive
  is expected at this level of testing, and its CI includes zero.
- Absorption at n=42 (+6.84%) is suggestive but far too small to use.
