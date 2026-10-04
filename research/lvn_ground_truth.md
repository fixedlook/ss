# LVN definition — ground truth from user's hand-marked zones

Definition under test:
```
LVN = level volume <= 0.7 * median volume of a +/-50pt window
      contiguous qualifying levels merged, min band height 4 pts
      marked from a completed session's own profile, used on later sessions
```

## Test 1 — 2026-09-23 (5 zones given by user)

User-marked zones (from 09-23's own profile):

| # | low | high | height | status |
|---|---|---|---|---|
| 1 | 30,645.25 | 30,659.50 | 14.25 | partially matched (10-35% of levels thin) |
| 2 | 30,827.25 | 30,843.75 | 16.50 | matched (~54-64% of levels thin) |
| 3 | 30,882.50 | 30,905.25 | 22.75 | matched (69-90%) |
| 4 | 30,972.00 | 30,990.25 | 18.25 | perfect (100%) |
| 5 | 31,060.25 | 31,091.00 | 30.75 | **UNREPRODUCIBLE** |

Zone 5 starts 47.25 pts ABOVE 09-23's RTH high (31,013.00). Not traded in RTH on
any session except 09-22 (24 levels only) and 10-02. Proof that the user's chart
volume profile contains overnight/ETH data that the CSV export does not.
The export is 100% RTH: 0 of 5,187,981 rows outside 09:30-16:59 ET.

## Test 2 — 2026-04-14 (blind test, 6 bands reported to user)

Detected by the rule above:

| # | low | high | height | user verdict |
|---|---|---|---|---|
| 1 | 25,664.75 | 25,669.50 | 4.75 | **REJECTED** — "theres quite a bit of volume" |
| 2 | 25,760.25 | 25,768.25 | 8.00 | confirmed |
| 3 | 25,827.50 | 25,832.25 | 4.75 | confirmed |
| 4 | 25,864.75 | 25,873.75 | 9.00 | confirmed |
| 5 | 25,934.75 | 25,942.00 | 7.25 | confirmed |
| 6 | 25,996.25 | 26,005.25 | 9.00 | confirmed |

Missed by the rule, user would mark it: **25,690.50 – 25,702.50** (12 pts)

### Score: precision 5/6 = 83%, recall 5/6 = 83%

### The unresolved conflict

| | rejected 25,664.75-25,669.50 | wanted 25,690.50-25,702.50 |
|---|---|---|
| median volume/level | **24** | **138** |
| % of day's median level | 3% | 17% |
| total volume | 456 | 7,207 |

The band the user wants is **5.6x heavier** than the band they reject.

Verified at every window width — the rejected band is always the thinner one:

| window | rejected | wanted |
|---|---|---|
| ±15pt | 0.74 | 1.01 |
| ±25pt | 0.63 | 0.91 |
| ±50pt | 0.39 | 0.77 |
| ±100pt | 0.17 | 0.59 |
| ±150pt | 0.11 | 0.30 |

**No volume-based rule can reject A while accepting B.** The two judgments are
incompatible under any "thin = LVN" criterion.

Both zones sit in the bottom tail: below 25,700 is 1.7% of the day's volume but
13% of its range.

### Leading explanation

User's chart VP shows the rejected zone as HEAVIER than their own CSV export does.
Same data-source mismatch as zone 5 on 09-23: the on-chart volume profile almost
certainly includes overnight/ETH volume, which concentrates near the session
extremes and inflates the low-of-day region without touching mid-range levels.

User's own words: "idk maybe your data is right and my visual vp isnt 100% accurate"

### Consequence

Exact replication of the user's on-chart marking is impossible with an RTH-only
export. Expect ~17% disagreement concentrated at the profile's tails.
