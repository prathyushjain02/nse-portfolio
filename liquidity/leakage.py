"""
Section 8.1 demonstration: what a time machine does to a backtest.

"If you join on event date, you have given the model a time machine and your
backtest will be spectacular and completely fake."

This module builds the SAME model twice:
  PIT      - shareholding facts visible only from their broadcast date
  NAIVE    - shareholding facts visible from their quarter-end date

and reports the difference. The gap is the size of the lie a careless join
tells you. In this dataset the naive join is worth up to 8 years of foresight
on individual records (revisions are broadcast long after the quarter they
restate), so the effect is not subtle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_naive_shp(shp: pd.DataFrame) -> pd.DataFrame:
    """The classic mistake: treat the fact as known when it became TRUE."""
    n = shp.copy()
    n["known_from"] = n["quarter_end"]
    return n


def pit_exposure(panel_pit: pd.DataFrame, panel_naive: pd.DataFrame, col: str = "promoter_pct") -> dict:
    """How much knowledge a naive join actually imports.

    Backtest scores are a weak instrument for detecting leakage: if the leaked
    feature happens to carry little weight, a leaky backtest can look identical
    to a clean one, and you will conclude - wrongly - that PIT discipline is
    optional. So we also measure the leak DIRECTLY, at the feature level:
    on what share of entity-months does the naive join hand the model a
    different (future-informed) value, and by how much?
    """
    a = panel_pit[["symbol", "month", col]].rename(columns={col: "pit"})
    b = panel_naive[["symbol", "month", col]].rename(columns={col: "naive"})
    m = a.merge(b, on=["symbol", "month"], how="inner")
    both = m.dropna(subset=["pit", "naive"])
    differs = (both["pit"] - both["naive"]).abs() > 1e-9
    only_naive = m["pit"].isna() & m["naive"].notna()
    return {
        "rows_compared": int(len(m)),
        "pct_rows_value_differs": float(differs.mean() * 100) if len(both) else np.nan,
        "pct_rows_known_only_to_naive": float(only_naive.mean() * 100),
        "mean_abs_diff_pp": float((both.loc[differs, "pit"] - both.loc[differs, "naive"]).abs().mean())
        if differs.any() else 0.0,
    }


def revision_exposure(shp: pd.DataFrame) -> dict:
    """Restatements are the sharpest form of the trap: a revision broadcast
    years later describes a quarter long past. Joining on quarter end makes
    the restated figure visible from the original quarter."""
    r = shp[shp["is_revision"]]
    lag = (r["known_from"] - r["quarter_end"]).dt.days
    return {
        "n_records": int(len(shp)),
        "n_revisions": int(len(r)),
        "pct_revisions": float(len(r) / len(shp) * 100) if len(shp) else np.nan,
        "revision_lag_days_p50": float(lag.median()) if len(r) else np.nan,
        "revision_lag_days_p90": float(lag.quantile(0.9)) if len(r) else np.nan,
        "revision_lag_days_max": float(lag.max()) if len(r) else np.nan,
    }


def lag_sensitivity_shp(shp: pd.DataFrame, assumed_lag_days: int) -> pd.DataFrame:
    """Section 8.1: 'for any source without a reliable ingestion timestamp,
    apply a conservative assumed lag and sensitivity-test the lag assumption.'

    Here we pretend we did NOT have broadcast timestamps and had to assume a
    flat lag, so we can measure what that assumption costs.
    """
    n = shp.copy()
    n["known_from"] = n["quarter_end"] + pd.Timedelta(days=assumed_lag_days)
    return n
