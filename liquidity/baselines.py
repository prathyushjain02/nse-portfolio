"""
Section 8.5 - the baselines you must beat. "If you can't beat these, stop and
rethink."

  1. Random selection from the universe
  2. Largest 100 by size
  3. "Raised a Series C+ in the last 24 months"   <- private-market rule; the
     nearest listed-universe analogue is "listed in the last 24 months", which
     is what we score. Flagged as a SUBSTITUTION, not the original rule.
  4. "Filed a DRHP"  <- for Track D the confirmatory analogue is a disclosed
     trading plan / OFS announcement. T3 must beat this on LEAD TIME, not
     precision.
  5. "Lock-in expiring in the next 90 days"  <- the explicit bar for Track D.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def baseline_scores(panel: pd.DataFrame, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(panel)
    out = {}

    out["1_random"] = rng.random(n)

    # 2. largest by size proxy (trailing traded value)
    out["2_largest"] = panel["size_proxy"].fillna(-1).values

    # 3. SUBSTITUTION for "Series C+ in last 24m": recently listed
    out["3_recent_listing"] = (
        panel["is_recent_ipo"].fillna(0).values + rng.random(n) * 1e-6
    )

    # 4. confirmatory disclosure (the "filed a DRHP" analogue for Track D)
    conf = panel.get("trading_plan_active", pd.Series(np.zeros(n))).fillna(0).values
    ofs = panel.get("ofs_announced_3m", pd.Series(np.zeros(n))).fillna(0).values
    out["4_confirmatory_disclosure"] = conf + ofs + rng.random(n) * 1e-6

    # 5. the Track-D bar
    out["5_lockin_90d"] = (
        panel["lockin_90d"].fillna(0).values + rng.random(n) * 1e-6
    )

    # 5b. promoter-class lock-in only (tighter version of the same rule)
    out["5b_lockin_promoter_90d"] = (
        panel["lockin_promoter_90d"].fillna(0).values + rng.random(n) * 1e-6
    )

    # 6. simple momentum rule: promoter stake already falling
    out["6_promoter_declining"] = (
        panel["promoter_declining"].fillna(0).values
        + panel["prior_event_count"].fillna(0).values * 1e-3
        + rng.random(n) * 1e-6
    )
    return out
