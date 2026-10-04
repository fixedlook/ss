"""
Order-flow concepts as reversal triggers at LVN / single-print boxes.

Second stage of the analysis: stage 1 (reversal_concepts.py) built prior-only
zones, found every entry and labelled the outcome. Here we restrict to the
boxes the user cares about (LVN and single prints) and ask, for each order-flow
condition, whether it raises the reversal rate above the zone base rate.

Inference is clustered by session (bootstrap), because many entries share a
session and a zone.
"""
import numpy as np
import pandas as pd

EVENTS = '/tmp/zone_events.parquet'
OUT = 'results/concept_comparison_boxes.csv'

ev = pd.read_parquet(EVENTS)
ev['lvn'] = ev['lvn'].astype(bool)
ev['sp'] = ev['sp'].astype(bool)
ev['hvn'] = ev['hvn'].astype(bool)
ev['date'] = ev['date'].astype(str)

print(f"all zone-entry events : {len(ev):,}")
print("zone composition      :", ev.groupby(['lvn', 'sp', 'hvn']).size().to_dict())

BOX = ev[ev['lvn'] | ev['sp']].reset_index(drop=True)
RV = BOX['reversed'].to_numpy().astype(bool)
SESS = BOX['date'].to_numpy()
USESS = np.unique(SESS)
GROUPS = {x: np.where(SESS == x)[0] for x in USESS}
print(f"\nBOX events            : {len(BOX):,}   base reversal rate {RV.mean():.2%}")
print("non-zone baseline     : 50.00%")


def concept(df, name):
    rev = df['rev'].to_numpy()
    if name == 'delta_flip_rev':
        return np.where(rev > 0, df['delta_flip_up'], df['delta_flip_dn']).astype(bool)
    if name == 'delta_with_rev':
        return (df['delta'].to_numpy() * rev) > 0
    if name == 'delta_against_rev':
        return (df['delta'].to_numpy() * rev) < 0
    if name == 'delta_flip_strong':
        return (np.where(rev > 0, df['delta_flip_up'], df['delta_flip_dn']).astype(bool)
                & (df['absdelta_ratio'].to_numpy() > 1.0))
    if name == 'absorption':
        return df['big_delta_small_range'].to_numpy().astype(bool)
    if name == 'two_way_tape':
        return df['two_way'].to_numpy().astype(bool)
    if name == 'volume_climax':
        return df['vol_ratio'].to_numpy() > 2.0
    if name == 'volume_dry_up':
        return df['vol_ratio'].to_numpy() < 0.5
    if name == 'rejection_close':
        cp = df['close_pos'].to_numpy()
        return np.where(rev > 0, cp > 0.66, cp < 0.34)
    if name == 'delta_divergence':
        return np.where(rev > 0,
                        df['new_low'].to_numpy().astype(bool) & df['delta_less_neg'].to_numpy().astype(bool),
                        df['new_high'].to_numpy().astype(bool) & df['delta_less_pos'].to_numpy().astype(bool))
    raise ValueError(name)


CONCEPTS = ['delta_flip_rev', 'delta_flip_strong', 'delta_with_rev', 'delta_against_rev',
            'absorption', 'two_way_tape', 'volume_climax', 'volume_dry_up',
            'rejection_close', 'delta_divergence']

print(f"\n{'concept':>20} | {'n':>5} | {'with':>7} | {'without':>8} | {'diff':>7} | {'95% CI (clustered)':>22}")
print("-" * 84)
rows = []
for c in CONCEPTS:
    m = np.asarray(concept(BOX, c), dtype=bool)
    n_with = int(m.sum())
    if n_with < 25 or (len(m) - n_with) < 25:
        print(f"{c:>20} | {n_with:>5} |  too few events")
        continue
    rng = np.random.default_rng(11)
    diffs = []
    for _ in range(1000):
        pick = rng.choice(USESS, size=len(USESS), replace=True)
        take = np.concatenate([GROUPS[x] for x in pick])
        wm = m[take]
        nw = int(wm.sum())
        if nw < 8 or (len(wm) - nw) < 8:
            continue
        r = RV[take]
        diffs.append(r[wm].mean() - r[~wm].mean())
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    diff = RV[m].mean() - RV[~m].mean()
    sig = 'SIG' if (lo > 0 or hi < 0) else ''
    print(f"{c:>20} | {n_with:>5} | {RV[m].mean():>7.2%} | {RV[~m].mean():>8.2%} | "
          f"{diff:>+7.2%} | [{lo:>+6.2%},{hi:>+6.2%}] {sig}")
    rows.append(dict(concept=c, n=n_with, rate_with=RV[m].mean(), rate_without=RV[~m].mean(),
                     diff=diff, ci_lo=lo, ci_hi=hi, vs_base=RV[m].mean() - 0.50))

print("\n=== box subtypes, no trigger ===")
for lab, sel in [('LVN only', BOX['lvn'].to_numpy() & ~BOX['sp'].to_numpy()),
                 ('Single print only', BOX['sp'].to_numpy() & ~BOX['lvn'].to_numpy()),
                 ('LVN + SP both', BOX['lvn'].to_numpy() & BOX['sp'].to_numpy())]:
    sub = RV[sel]
    if len(sub) < 25:
        continue
    se = np.sqrt(0.25 / len(sub))
    print(f"  {lab:>19}: n={len(sub):>5}  reversal {sub.mean():.2%}   ({(sub.mean()-0.5)/se:+.2f} SE from 50%)")

print("\n=== in/out of sample (BX boxes) ===")
mid = len(USESS) // 2
IS, OOS = set(USESS[:mid]), set(USESS[mid:])
is_m, oos_m = np.isin(SESS, list(IS)), np.isin(SESS, list(OOS))
print(f"  IS  ({USESS[0]} .. {USESS[mid-1]}): {is_m.sum()} events")
print(f"  OOS ({USESS[mid]} .. {USESS[-1]}): {oos_m.sum()} events")
print(f"  {'concept':>20} | {'IS diff':>9} | {'OOS diff':>9} | {'consistent':>10}")
print("-" * 60)
for c in CONCEPTS:
    m = np.asarray(concept(BOX, c), dtype=bool)
    def dd(mask_rows):
        mm, rr = m[mask_rows], RV[mask_rows]
        if mm.sum() < 15 or (~mm).sum() < 15:
            return np.nan
        return rr[mm].mean() - rr[~mm].mean()
    a, b = dd(is_m), dd(oos_m)
    ok = '' if (np.isnan(a) or np.isnan(b)) else ('yes' if np.sign(a) == np.sign(b) else 'NO')
    print(f"  {c:>20} | {a:>+9.2%} | {b:>+9.2%} | {ok:>10}")

print("\n=== multiple-testing note ===")
print(f"  {len(CONCEPTS)} concepts tested at alpha=0.05 -> ~{0.05*len(CONCEPTS):.1f} false positives expected by chance")
print("  Bonferroni-corrected threshold: alpha = 0.005 (CI must exclude 0 by a wide margin)")
pd.DataFrame(rows).to_csv(OUT, index=False)
print(f"\nwrote {OUT}")
