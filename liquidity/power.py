"""
Power analysis for the untested hypotheses (H3, H4, H6, H7, H9) and for
strengthening H8.

Motivation: the H1 result. The raw H1 test returned a 1.51x lift at p=5e-08 and
the controlled test returned 0.86. The expensive mistake is not failing to test
a hypothesis - it is running an underpowered or uncontrolled test, getting a
confirming number, and buying a data subscription on the strength of it.

So before specifying any data purchase, compute what each test could actually
detect. Minimum detectable effect (MDE) is reported as a RELATIVE lift over
base rate, at 80% power, for two alphas:

  alpha = 0.05   nominal
  alpha = 0.005  Bonferroni-corrected for the 10 hypotheses in section 10

All inputs are explicit and overridable - they are estimates of what a register
would contain, not measurements. Where a number comes from the framework
document it is cited in the comment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def power_two_prop(p0: float, p1: float, n0: int, n1: int, alpha: float) -> float:
    """Power of a two-sided two-proportion z-test."""
    if min(n0, n1) < 1 or not (0 < p0 < 1) or not (0 < p1 < 1):
        return 0.0
    p_bar = (p0 * n0 + p1 * n1) / (n0 + n1)
    se_null = np.sqrt(p_bar * (1 - p_bar) * (1 / n0 + 1 / n1))
    se_alt = np.sqrt(p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1)
    if se_alt <= 0:
        return 0.0
    z = stats.norm.ppf(1 - alpha / 2)
    return float(
        stats.norm.sf((z * se_null - abs(p1 - p0)) / se_alt)
        + stats.norm.cdf((-z * se_null - abs(p1 - p0)) / se_alt)
    )


def mde_relative_lift(p0: float, n0: int, n1: int, alpha: float = 0.05,
                      target_power: float = 0.80) -> float:
    """Smallest relative lift (p1/p0) detectable at `target_power`."""
    lo, hi = 1.0, 50.0
    if power_two_prop(p0, min(p0 * hi, 0.999), n0, n1, alpha) < target_power:
        return np.inf
    for _ in range(80):
        mid = (lo + hi) / 2
        if power_two_prop(p0, min(p0 * mid, 0.999), n0, n1, alpha) < target_power:
            lo = mid
        else:
            hi = mid
    return hi


# ---------------------------------------------------------------------------
# Scenario inputs.
#
# These are ESTIMATES of what a properly built register would contain, not
# measurements. Each carries its basis. Override them and re-run before
# committing to any data spend.
# ---------------------------------------------------------------------------

SCENARIOS = [
    dict(
        hypothesis="H3 pre-IPO conjunction -> DRHP within 24m",
        track="A",
        # Universe: private companies large enough to be plausible IPO
        # candidates (revenue > ~Rs 100 cr). ~20k, observed over 10 years.
        n_universe_entities=20_000,
        years=10,
        # DRHP filings 2015-2025: mainboard ~100/yr + SME ~150/yr, and only a
        # subset come from the >Rs 100 cr private universe.
        n_events=1_500,
        # Share of entity-years where all three markers fired within 12 months
        # of each other. The conjunction is the whole hypothesis and it is rare.
        treated_fraction=0.02,
        note="Conjunction is rare by design; that is what limits power.",
    ),
    dict(
        hypothesis="H4 lead-investor fund vintage 8+ yrs -> Track C hazard",
        track="C",
        # Indian startups with institutional funding and a traceable cap table.
        n_universe_entities=6_000,
        years=10,
        # Disclosed founder/early-investor secondaries. Framework calls Track C
        # "highest-volume and most under-covered" - much is undisclosed.
        n_events=1_200,
        # Fraction of entity-years where the lead investor's fund is 8-11 yrs old
        treated_fraction=0.18,
        note="Vintage is cleanly observable; the label is the weak link.",
    ),
    dict(
        hypothesis="H6 margin uplift 4-6 quarters -> Track B sale",
        track="B",
        n_universe_entities=20_000,
        years=10,
        # Framework, section 8.6: "you may have only 60-80 clean Track-B events"
        n_events=80,
        treated_fraction=0.10,
        note="Framework itself flags small-N. This is the binding constraint.",
    ),
    dict(
        hypothesis="H7 ESOP buyback -> founder secondary within 6m",
        track="C",
        # Startups that have ever run an ESOP buyback are the natural universe
        n_universe_entities=6_000,
        years=5,
        n_events=600,
        # ESOP buyback announcements: ~60/yr well-covered in trade press
        treated_fraction=0.01,
        note="Treated group is tiny AND the framework notes the two are often "
             "bundled, so causal direction is unidentified even if powered.",
    ),
    dict(
        hypothesis="H9 promoter age 58+ w/o next-gen -> Track B/E",
        track="B/E",
        n_universe_entities=3_000,
        years=10,
        n_events=300,
        # A large share of Indian promoters are 58+; this is not a rare treatment
        treated_fraction=0.35,
        note="Well-powered on paper; the problem is confounding, not N.",
    ),
    dict(
        hypothesis="H8 prior liquidity history -> repeat event (MEASURED)",
        track="D",
        # Measured directly from the built panel, not estimated: the panel is
        # entity-MONTHS, so these are supplied explicitly rather than derived
        # from an entities x years product.
        explicit=dict(p0=0.148414, n0=53_829, n1=46_801),
        n_events=18_347,
        note="Already run on real data: lift 1.49, p=2.8e-195. Listed only.",
    ),
]


def run(scenarios=None) -> pd.DataFrame:
    rows = []
    for s in scenarios or SCENARIOS:
        if "explicit" in s:
            p0 = s["explicit"]["p0"]
            n0, n1 = s["explicit"]["n0"], s["explicit"]["n1"]
            n_panel = n0 + n1
        else:
            n_panel = s["n_universe_entities"] * s["years"]
            p0 = s["n_events"] / n_panel
            n1 = int(n_panel * s["treated_fraction"])
            n0 = n_panel - n1
        rows.append(
            dict(
                hypothesis=s["hypothesis"],
                track=s["track"],
                panel_rows=n_panel,
                events=s["n_events"],
                base_rate=p0,
                n_treated=n1,
                mde_lift_a05=mde_relative_lift(p0, n0, n1, 0.05),
                mde_lift_bonferroni=mde_relative_lift(p0, n0, n1, 0.005),
                note=s["note"],
            )
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = run()
    pd.set_option("display.width", 200)
    print(df.drop(columns="note").to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print()
    for _, r in df.iterrows():
        print(f"- {r['hypothesis']}\n    {r['note']}")
