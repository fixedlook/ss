# LVN × CVD divergence — and the retraction of the divergence signal

The question: *"wait so what if we use lvn with cvd divergence"*.

Answer: it is the only combination in this project that is not visibly dead — at
**+3.8%, t ≈ 1.2**. And getting to that answer killed the previous headline.

---

## 1. The previous headline was wrong

Last turn's write-up reported **CVD divergence +3.09%, IS +2.83 / OOS +3.41** as
"the only sign-consistent candidate in the project", with an "independent repeat"
of +2.66% in the at-box run.

Both runs sampled **every 10th minute of the session** (`i = 40, 50, 60, …`).
That grid always lands on the same minutes-of-session in every session. Re-run on
**every minute**, the identical flag gives:

| universe | n | div | no div | effect | cluster-robust |
|---|---|---|---|---|---|
| every minute | 37,707 | 50.58% | 50.37% | **+0.21%** | SE 1.69%, **t = 0.12**, CI [−3.10, +3.52] |

Split in half it **flips sign**: IS **−2.31%**, OOS **+2.95%**.

### Why — the residue classes

| i mod 10 | n div | div rev | no-div rev | effect |
|---|---|---|---|---|
| **0** | 491 | 52.75% | 49.66% | **+3.09%** ← v2's number exactly |
| 1 | 506 | 50.59% | 49.49% | +1.10% |
| 2 | 493 | 50.10% | 50.17% | −0.06% |
| 3 | 486 | 50.21% | 51.42% | −1.22% |
| 4 | 503 | 54.08% | 51.32% | +2.75% |
| 5 | 465 | 50.97% | 50.96% | +0.01% |
| 6 | 469 | 51.17% | 50.99% | +0.18% |
| 7 | 467 | 48.39% | 50.09% | −1.70% |
| 8 | 501 | 49.70% | 50.45% | −0.75% |
| 9 | 494 | 47.77% | 49.18% | −1.41% |

The effect ranges from −1.70% to +3.09% depending only on which minutes you look
at. **200 random 10% subsamples: mean +0.10%, sd 2.34%** — only **11%** reach
+3.09%. The 10-minute grid is the luckiest of ten systematic samples and v2 drew
exactly it. The "independent repeat" in the at-box run was **the same artifact
drawn twice from overlapping minutes**, not a replication.

**The sampling grid is how this kind of bug hides.** It is the third of the
family: the VWAP reference and the price-sorted footprint both produced confident
false positives too. A feature that only shows up on one systematic subsample is
not a feature.

---

## 2. Two bugs of mine, fixed before believing anything

1. **`divz` scale.** Normalised by 10 × (median |per-minute delta|), which has sd
   0.443 — denominator ~4× too large, so `|divz| ≥ 1` was the top 2% and,
   intersected with a 13% flag, fired **zero times**. The "strong divergence" arm
   tested nothing. Now normalised by the expanding median of the 10-minute CVD
   move.
2. **Arrival sampling.** Arrivals were detected on the 10-minute grid, so most
   band entries fell between samples — the first run found 93 arrivals, *fewer*
   than the old midpoint rule. Detected on every minute: **954 arrivals**
   (prior-day bands) / **2,593** (5-day union).

---

## 3. What survives: the combination

Universe: every minute, 164 sessions. Base reversal **50.40%**, base P(up)
**50.85%**. Bands: prior-day profile, and union of the previous 5 sessions'
profiles (the user's real workflow — LVNs stay valid for the week).

### 2×2, arrival at an LVN band × CVD divergence

| | n | reversal | 95% CI |
|---|---|---|---|
| **divergence + arrival** | **326** | **54.29%** | [47.64, 61.11] |
| divergence, no arrival | 4,549 | 50.32% | [46.63, 53.75] |
| no divergence + arrival | 2,267 | 50.46% | [47.64, 53.27] |
| no divergence, no arrival | 30,565 | 50.37% | [49.01, 51.77] |

- divergence **at** the level: **+3.83%**
- divergence **elsewhere**: **−0.05%**
- **interaction: +3.88%** — additive would predict +3.78% on top of 50.37%

Cluster-robust regression on all 37,707 minutes: `div` +0.0017 (t 0.10),
`arrival` +0.0010 (t 0.07), **interaction +0.0388 (t +1.14)**.
Direct: **+3.93%, SE 3.27%, t = 1.20, CI [−2.49, +10.34]**.

### The same 2×2 on prior-day bands only

| | n | reversal |
|---|---|---|
| divergence + arrival | 104 | 51.92% |
| divergence, no arrival | 4,771 | 50.56% |
| no divergence + arrival | 850 | 49.88% |
| no divergence, no arrival | 31,982 | 50.39% |

divergence at the level **+2.04%**, elsewhere +0.17%, **interaction +1.87%
(t = +0.32)**. Much weaker.

### The state version (in a band, not arriving) is weaker still

union: +0.76% (interaction t = +0.22); prior-day: +1.75% (t = +0.31).
**The effect is on arrival, not on sitting inside the band.**

### It is not a drift artifact

| side | cell | control | lift |
|---|---|---|---|
| price rose into the level (n=129) | 51.94% | 48.93% | +3.0% |
| price fell into the level (n=197) | 55.84% | 51.96% | +3.9% |

Both sides lift against their own control. Cell P(up) = 60.43%.

### But the mechanism does not show up where it should

Thinness terciles *inside* the cell (local volume ratio, <1 = thin):

| tercile | ratio | n | reversal |
|---|---|---|---|
| 1 (thinnest) | 0.01–0.04 | 111 | **47.75%** |
| 2 | 0.04–0.73 | 106 | 58.49% |
| 3 | 0.73–1.59 | 109 | 56.88% |

**Non-monotonic, and absent in the thinnest third.** A genuine "liquidity vacuum"
mechanism should be strongest where the book is thinnest. It is not. (Caveat:
thinness is measured on the prior-day profile, which is not exactly the quantity
that defined a union band.)

---

## 4. Zones alone are still neutral — seventh confirmation

| | n | reversal |
|---|---|---|
| arrival at a union LVN band, no divergence | 2,267 | 50.46% |
| everything else, no divergence | 30,565 | 50.37% |

**+0.09%.** Unchanged from the five earlier nulls.

---

## 5. How to read this

- **20-odd cells were examined.** The largest t is **1.20**. Under the null, the
  expected best of ~20 near-independent tests is |t| ≈ 2. A t of 1.2 is
  **unremarkable** — it is not evidence, it is permission to keep looking.
- The honest statement: *divergence alone does nothing; divergence while price
  arrives at a level is +3.8% with t ≈ 1.2, and that is not yet distinguishable
  from zero.*
- Random 50% splits of the cell: **92% positive**, mean +3.29%, sd 2.32% — so the
  *sign* is stable even though the level is not.
- **What would settle it:** SE is 3.27% on 164 sessions (159 clusters). To reach
  t = 2 on a +3.9% effect the SE must fall to 1.95% → **(3.27/1.95)² ≈ 2.8× the
  data → about 460 sessions, ~21 months of footprint.** The current export is
  7.5 months. This is a data-availability problem, not a modelling one.
- Trading caveat: the ±100pt race measures *which barrier is reached first*. It is
  a proxy for the user's "both legs ≥ 100 pts" definition of a valid reversal, not
  that definition itself.

### Recommendation

Do not build anything on this yet. If the idea is to be pursued, the cheapest
honest path is **more footprint data** — the same export extended backwards. Every
order-flow question in this project is currently power-limited, not
hypothesis-limited: this cell needs ~460 sessions to resolve, and the user's
original "flow at a box" thesis needs ~10,000.

---

## Files

| file | contents |
|---|---|
| `research/lvn_cvd.py` | arrival/state frames, both band sets, 2×2 + regressions |
| `research/lvn_cvd_full.py` | definitive full-grid 2×2 and interaction |
| `research/grid_check.py` | residue-class and random-subsample diagnostics |
| `results/lvn_cvd_full.csv` | 37,707 minute-level events with all flags |
| `results/lvn_cvd_state.csv` | 3,836 every-10th-minute events |
| `results/lvn_cvd_arrivals.csv` | 3,296 arrival events |
