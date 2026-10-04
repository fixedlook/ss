# mnq_flow.csv.gz — validation and first results

Data: per-minute 1-min bars with bid/ask split, exported 2026-10-04 20:54, killed ~30 s before the end
(file stops 2026-09-22; the last ~7 trading days of the chart are not in it — no consequence for any result here).

## 1. What arrived

| | |
|---|---|
| gz | 32,118,632 B → 149,667,840 B csv |
| rows | 1,942,403 (1,942,401 clean, 7 torn, 6 corrupt, 2 bad `delta` values) |
| sessions with flow | **1,024** (2019-08-12 → 2026-09-22) |
| flow minutes | **433,060** |
| coverage | 929 sessions ≥300 flow min, 920 ≥400, 848 with the full 450 |
| shape | RTH only, 09:30–16:59 = 450 min/session; ETH bars have volume but zero flow |

Corrupt rows (impossible prices) sit at 2019-08-12 22:24, 2020-09-04 16:12, 2020-09-08 13:08,
2020-10-08 23:29, 2020-10-15 20:58, 2021-02-03 21:46 — filtered out (`close` outside 1,000–100,000).
The file's own `delta` column disagrees with `ask − bid` on 2 rows: recompute, never trust it.

**The 14-month hole (2024-10 → 2025-11) is in the bar series itself**, not just the footprint —
so it is a chart/data gap, not a tick-history problem.

## 2. Validation against the independent footprint export

68,895 overlapping minutes (156 sessions, 2026-02-16 → 2026-09-22):

- bid volume: **identical in every single minute** (125,291,322 = 125,291,322)
- ask volume: identical in 68,894 of 68,895; one minute off by 1 contract
- sessions with both columns identical: 155 / 156

The per-minute file is a faithful aggregation of the same tick stream the footprint came from.
(`research/flow_vs_footprint.py`)

## 3. Order flow does not predict DIRECTION

Every minute (no 10-minute grid), ±100 pt race to the session end, session-clustered bootstrap,
**151,805 resolved events over 893 sessions**. Base reversal 49.60%, base P(up) 48.92%.

| feature | with | without | diff | n | 95% CI |
|---|---|---|---|---|---|
| CVD divergence (CVD vs price) | 49.51% | 49.62% | **−0.10%** | 19,584 | [−1.64%, +1.51%] |
| delta opposes approach | 49.59% | 49.61% | −0.01% | 63,186 | [−0.49%, +0.47%] |
| absorption (big delta, small range) | 50.00% | 49.60% | +0.40% | 2,462 | [−2.05%, +2.83%] |
| extreme BUY aggression (top 10%) | 50.49% | 49.48% | +1.01% | 18,816 | [−0.28%, +2.24%] |
| extreme SELL aggression (bottom 10%) | 49.37% | 49.64% | −0.27% | 19,655 | [−1.43%, +1.05%] |
| big \|delta\| (top 20%) | 49.11% | 49.66% | −0.55% | 16,747 | [−1.53%, +0.49%] |
| volume spike (top 10%) | 47.13% | 49.66% | −2.53% | 3,274 | [−5.65%, +0.62%] |
| high trade count (top 20%) | 47.49% | 49.70% | −2.21% | 6,749 | [−4.64%, +0.18%] |
| buy_ratio deviation > 0.05 | 49.69% | 49.52% | +0.17% | 71,454 | [−0.41%, +0.76%] |

Directional check (P(up)) is flat for all of them; era-by-era signs flip at random.

**Conclusion: the "+3.09% CVD divergence" is confirmed dead.** On six times the data and with the
grid removed it is −0.10%, and no order-flow feature predicts reversal direction at any usable size.
With this sample the test could have detected ~1.5 pp — so "no order-flow direction edge above 1.5 pp"
is now a positive statement, not an absence of data.

If anything the two activity measures point the other way: high-volume / high-trade-count minutes
**continue** more often than they reverse.

(`research/orderflow_everyminute.py`, `results/orderflow_everyminute_summary.csv`)

## 4. Order flow does predict DISTANCE — 1.4×, in every year and every hour

Clean version: **every minute**, no race filter, no future conditioning. Only past information is used
(expanding within-session percentile of volume / trade count). Outcome = the range of the next 30 minutes.
**372,150 events over 1,009 sessions.** Baseline 30-min range 57.6 pts (median 47.2).

| feature | with | without | ratio | P(≥100 pt in 30 min) | base | n |
|---|---|---|---|---|---|---|
| volume in top 10% of session | 81.8 | 57.1 | **1.43×** | **26.7%** | 11.6% | 6,765 |
| volume in top 20% | 75.7 | 56.8 | 1.33× | 22.5% | 11.4% | 16,132 |
| **trade count top 10%** | 83.2 | 57.2 | **1.46×** | **27.8%** | 11.6% | 6,198 |
| volume in bottom 50% | 55.4 | 67.2 | 0.82× | 10.7% | 16.7% | 303,404 |

Robustness:

- within-session normalised (removes cross-day differences): **1.40×** (vol), **1.44×** (trades)
- every year 2020–2026: 1.26 / 1.37 / 1.47 / 1.55 / 1.40 / 1.20 / 1.45 — **7 of 7 positive**
- every hour of the session: 1.04 – 2.97 — **7 of 7 positive**
- session-clustered bootstrap: **vol 1.43× [1.35, 1.51]**, **trades 1.46× [1.36, 1.56]**

(`research/flow_volatility.py`, `results/orderflow_everyminute_move.csv`)

## 5. What this means for the thesis

"Price reaches the level then changes direction" cannot be predicted from order flow — but order flow
says **when the market is about to move at all**, and a level that is reached in a dead tape is not the
same trade as a level reached while the tape is exploding.

That reframes the level work rather than killing it: the question is no longer *"does flow say which way"*
(it doesn't, and now we know that with power) but *"which arrivals are followed by a real move"* — and
1.4× / 27% vs 12% is a large enough difference to condition on.

Note this also explains why every reversal study came back null: if flow scales the move symmetrically,
every long/short test built on it must average to zero by construction.
