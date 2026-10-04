# Supplementary findings: TPO overlap and POC geometry

**Note on provenance.** These two studies ran while the sandbox was being
recycled and their scripts (`research/poc_geometry.py`,
`research/lvn_tpo_overlap.py`) and output CSVs did not survive, because they had
not been committed. The numbers below are recorded from the run output so the
conclusions are not lost. Re-run if the underlying generators are needed again.

Both were exploratory and are superseded for the level-lift question by the
1,633-session study in `reverse_engineer_findings.md`.

## TPO overlap — is an LVN the same thing as a time-thin gap?

TPO gap defined as: a price traded in only ONE 30-minute bracket of the session.

| measure | value |
|---|---|
| volume-LVN levels that are also TPO gaps | **61.9%** |
| TPO-gap levels that are also volume-LVNs | 18.2% |
| Jaccard (both / either) | 16.4% |
| whole LVN zones >80% inside a TPO gap | 54.9% |
| LVN zones 20-80% overlap | 13.1% |
| LVN zones <20% overlap | **32.0%** |

**Conclusion:** related but not the same. About a third of LVN boxes are
volume-thin yet time-busy — a structure that has never been tested separately.

**Cost of requiring both:** the strict intersection keeps ~61% of zones,
taking arrivals from 2,918 to ~1,793 and the minimum detectable effect from
**3.67pp to 5.32pp**. Sharper signal, blurrier ruler - roughly a wash on this
sample size.

## POC geometry — does the point of control give direction?

POC is the price with the MOST volume; LVN is the price with the LEAST. Within a
single session they are opposite ends of the same distribution, so POC cannot be
a thinness filter. It can only indicate direction: is the box a waypoint on the
route to the prior session's POC?

| configuration | share | reversal rate |
|---|---|---|
| POC beyond the box (price heading toward it) | 49.0% | 50.32% |
| POC behind the box | 51.0% | 49.29% |

Coin flip both ways.

**Median distance from the arrival price to the prior session's POC: 285 points.**

The POC is usually nowhere near the price being traded - the market has moved a
full day's range away from it.

Exploratory split by distance (not pre-committed, below the detection threshold):

| distance to prior POC | n | reversal |
|---|---|---|
| < 25 pts | 182 | 50.0% |
| 25-50 | 158 | 52.5% |
| 50-100 | 342 | 53.8% |
| 100-200 | 626 | 52.6% |
| 200+ | 2,167 | 48.1% |

Pooled within 200 pts: 52.5% (n=1,308) vs 48.1% (n=2,167) = **+4.4pp, ~2.5 SE**.
Below the ~4.9pp detection threshold for a comparison of that size, and not
pre-committed. **Not a finding.** The 8-year run later put POC lift at 0.95,
which rules it out.

## Undetermined: does the POC work as an exit rather than a filter?

Every test so far has been an entry filter, and filters cost sample size. Using
the POC (or another heavy level) as a **target** - where a bounce might travel to -
does not reduce the entry sample at all. Never tested.
