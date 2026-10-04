# Quant framings applied to the LVN × CVD problem — 2026-10-04

Three pieces of standard mathematics, run on the same data, to answer "is there an
edge here at all, and where would it have to live". Scripts + raw output alongside:
`research/first_passage_fit.py` / `results/first_passage_fit.txt`,
`research/kyle_lambda_lvn.py` / `results/kyle_lambda_lvn.txt`,
`research/flow_persistence.py` / `results/flow_persistence.txt`.

## 1. The SL/TP grid is an empirical first-passage surface

For Brownian motion with drift-to-variance ratio `k = 2μ/σ²`, the probability of
touching +TP before −SL is

    p(SL,TP) = (1 − e^{−k·SL}) / (1 − e^{−k·(SL+TP)})

and at `k = 0` this is exactly the barrier ratio `SL/(SL+TP)` — the martingale null
that every cell of the grid has to beat. Fitted to all 63 cells per arm:

| arm | k (1/point) | characteristic distance 1/|k| |
|---|---|---|
| A  LVN arrival + 10-min divergence | −0.0015 | ~650 pts |
| L  LVN arrival, no divergence | −0.0022 | ~460 pts |
| M  same entries, momentum side | −0.0018 | ~570 pts |
| B  divergence turns on in the level | −0.0031 | ~330 pts |
| C  control, every minute | −0.0023 | ~430 pts |

Findings:

* **Every arm sits at the martingale value within noise, and every arm is the same
  number.** The 63-cell grid — 2,532 trades × 63 barrier choices — carries about one
  parameter's worth of information, and that parameter is ≈ 0. There is no geometry
  to optimise because there is no effect for the geometry to harvest.
* **1/|k| ≈ 400–650 points is the distance scale over which any drift becomes
  visible.** A 10–50 point stop lives entirely inside the coin-flip regime, which is
  the mathematical statement of why the tight-stop grid cannot work.
* k is slightly **negative** everywhere, including the every-minute control: entering
  at a bar close and betting against the last 10 minutes is a small edge *for the
  market*. Part of that is spread/adverse-selection, i.e. it overlaps with the 1 pt
  cost already charged — do not double count it.
* Censoring caveat: the session bell truncates wide-barrier races, which biases the
  resolved-only win share down (corr(open share, win−null) = −0.63 for arm A).
  Refitting on low-censoring cells only moves k from −0.0015 to −0.0013, so the
  conclusion is unaffected. The P&L grid is not biased — it marks every open trade.

## 2. Kyle's lambda — is a thin zone actually thin?

`dp = λ · signed_volume + ε`, one regression per session, Fama–MacBeth across 1,007
sessions (points per 1,000 net contracts):

| sample | λ | t |
|---|---|---|
| all minutes | 47.5 | +9.8 |
| inside an LVN band | 44.8 | +5.9 |
| outside the bands | 47.9 | +9.5 |
| paired difference in/out | +7.2 | +1.2 |

**Flow moves price — that is one of the few genuinely significant effects in the
whole project (t = +9.8). But the LVN does not change the impact coefficient**
(paired t = +1.2; median ratio 1.01). Plain magnitudes agree: inside the band the
median 1-min move is 1.07× bigger *and* the median 1-min volume is 1.15× bigger
(t = +8.4 / +8.3) — so the zone is not a moment of thin tape, it is a moment of
*more* trading. The "slip zone" reading of the failed SL/TP grid is therefore not
supported by the tape; what the grid shows is simply that the entry has no edge.

## 3. Flow persistence (Cont–Kukanov–Stoikov)

If signed flow is autocorrelated and moves price, lagged flow should predict returns.

| test | value | t |
|---|---|---|
| autocorrelation of signed flow, lag 1 | +0.107 | +49.5 |
| autocorrelation, lag 5 | −0.013 | −6.6 |
| corr(imbalance_t , return t+1) | +0.0002 | +0.1 |
| corr(imbalance_t , return t+1..t+6) | −0.0096 | −6.5 |
| trade: top-third imbalance, 5-min hold, 1 pt cost | −1.06 pts net (−$2.12/MNQ) | −22.4 |
| same, before costs | −0.06 pts | ~0 |

**Flow is persistent, flow moves price, and lagged flow still has exactly zero
directional alpha.** The impact is impounded within the bar it happens in — and
slightly mean-reverts over the next five minutes. This closes off the last
"order flow as a directional signal" door, independently of the divergence framing
killed earlier.

## Where that leaves the project

* Direction: no. Not from levels (first-passage grid), not from divergence
  (every-minute 2×2), not from flow persistence (this note).
* Magnitude: yes, and it is the only robust thing found — order flow predicts how
  far the market will travel (top-decile volume minutes → 1.43× the next-30-minute
  range; P(≥100 pt move) 26.7% vs 11.6%), in every year and every hour.
* Mechanically, the level is not a barrier (λ unchanged) and not a liquidity hole
  (volume higher inside it). A rejection has to come from size resting against the
  move — i.e. from absorption — which is exactly what the Big Trades overlay is
  meant to detect, and which this data cannot see.
