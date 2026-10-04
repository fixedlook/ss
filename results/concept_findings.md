# Which order-flow condition precedes a reversal at LVN / single-print boxes?

Data: 159 clean sessions, 2026-02-17 .. 2026-10-02 (MNQ 1-min RTH footprint).
Zones built prior-only from prior day + prior week volume profiles.
Outcome: symmetric barrier, +0.75xATR in the reversal direction before
-0.75xATR in the continuation direction, within 30 bars, from the close of the
entry bar (no look-ahead).

## Headline

- Box entries (LVN / single print): **18,995 zone events**, of which **2,486** are LVN or single print.
- Base reversal rate at boxes: **50.64%** vs **50.00%** for all non-zone minutes.
  That is +0.64pp on n=2,486 (SE ~1.0pp) - **the boxes alone carry no measurable reversal edge.**
- No concept survives Bonferroni correction (10 concepts, alpha=0.05 -> ~0.5 false positives expected).

## Concept table (boxes only)

| concept | n | rate_with | rate_without | diff | 95% CI (session-clustered) |
|---|---|---|---|---|---|
| absorption (big delta, small range) | 62 | 64.52% | 50.29% | +14.23% | [+0.97, +27.72] |
| volume_dry_up | 87 | 57.47% | 50.40% | +7.08% | [-3.39, +17.30] |
| volume_climax | 282 | 55.67% | 50.00% | +5.67% | [-4.09, +15.55] |
| rejection_close (entered and closed back out) | 463 | 54.43% | 49.78% | +4.65% | [-3.07, +12.07] |
| delta_divergence | 403 | 53.85% | 50.02% | +3.82% | [-0.99, +9.68] |
| two_way_tape | 1646 | 51.70% | 48.57% | +3.13% | [-1.66, +8.47] |
| delta_flip_rev | 317 | 51.10% | 50.58% | +0.53% | [-6.66, +7.33] |
| delta_flip_strong | 96 | 51.04% | 50.63% | +0.41% | [-12.71, +12.84] |
| delta_against_rev | 2065 | 50.65% | 50.59% | +0.06% | [-7.52, +6.78] |
| delta_with_rev | 420 | 50.48% | 50.68% | -0.20% | [-6.91, +7.30] |

## In-sample / out-of-sample sign consistency

IS = first half of sessions (1291 events), OOS = second half (1195 events).

| concept | IS | OOS | consistent |
|---|---|---|---|
| absorption | +2.87% | +21.42% | yes |
| volume_dry_up | +1.87% | +14.90% | yes |
| volume_climax | +7.00% | +4.74% | yes |
| rejection_close | +4.62% | +4.60% | yes |
| two_way_tape | +2.49% | +3.67% | yes |
| delta_divergence | +7.05% | +0.25% | yes (OOS ~0) |
| delta_flip_rev | +2.57% | -1.69% | NO |
| delta_flip_strong | +3.60% | -3.62% | NO |
| delta_with_rev | +0.98% | -1.43% | NO |
| delta_against_rev | -0.98% | +1.14% | NO |

## What this says

1. **Pure delta direction does nothing.** Every delta-sign concept is within
   +/-0.5pp of zero and flips sign between halves. Buying the bounce because
   "delta flipped positive" is not supported.
2. **Structure beats direction.** The concepts with consistent signs describe
   how price and volume behaved *at the box* (absorption, climax, rejection,
   dry-up) rather than which side was aggressive.
3. **The winner has too few events to trust.** `absorption` (+14.2pp) has
   n=62; its CI lower bound is +0.97pp, so it would not survive a
   multiple-testing correction and needs ~13x more data to confirm.
4. Nothing here justifies drawing boxes yet.

## Sample sizes needed (5pp effect, 80% power, alpha=0.05)

n = 7.84 * 0.25 / 0.05^2 = 784 events per group.

| concept | n now | sessions now | sessions needed | approx months |
|---|---|---|---|---|
| two_way_tape | 1646 | 159 | ~380 | 18 |
| rejection_close | 463 | 159 | ~270 | 13 |
| volume_climax | 282 | 159 | ~445 | 21 |
| absorption | 62 | 159 | ~2000 | 95 |
