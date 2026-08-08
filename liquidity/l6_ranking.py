"""
L6 Ranking & triage: score x wallet x coverage-fit.

Motivated by a result that falls straight out of the backtest: on
wallet-weighted precision, ranking by probability alone LOSES to the trivial
"largest 100 companies" baseline. The model finds more events; the size
baseline finds bigger ones. Section 8.4 is explicit that catching one Rs 500 cr
event is worth twenty Rs 10 cr ones, so probability-only ranking is the wrong
objective function for the business.

This module ranks on EXPECTED PROCEEDS = P(event) x E[size | event], which is
what section 9 says the list should be sorted on.

E[size | event] is estimated point-in-time from the entity's own realised
history where it has one, else from a size-decile prior, else the panel median.
All three are computed from data known before the scoring month.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def expected_size_table(events: pd.DataFrame, as_of: pd.Timestamp) -> tuple[pd.Series, float]:
    """Per-symbol expected event size using only events KNOWN before `as_of`."""
    e = events[(events["known_from"] < as_of) & events["gross_proceeds_cr"].notna()]
    if not len(e):
        return pd.Series(dtype=float), np.nan
    per = e.groupby("symbol")["gross_proceeds_cr"].median()
    return per, float(e["gross_proceeds_cr"].median())


def attach_expected_size(scored: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time expected proceeds per (symbol, month).

    Computed month by month so a symbol's expected size never reflects deals
    that had not yet been reported at that month.
    """
    out = []
    for m, g in scored.groupby("month"):
        per, med = expected_size_table(events, pd.Timestamp(m))
        g = g.copy()
        g["expected_gross_cr"] = g["symbol"].map(per)
        # fall back to a size-decile prior, then the panel-wide median
        if "size_decile" in g:
            dec_prior = g.groupby("size_decile")["expected_gross_cr"].transform("median")
            g["expected_gross_cr"] = g["expected_gross_cr"].fillna(dec_prior)
        g["expected_gross_cr"] = g["expected_gross_cr"].fillna(med if np.isfinite(med) else 0.0)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def rank_scores(scored: pd.DataFrame, prob_col: str = "score") -> pd.DataFrame:
    """Three candidate ranking objectives, for like-for-like comparison."""
    s = scored.copy()
    s["rank_prob"] = s[prob_col]
    s["rank_expected_proceeds"] = s[prob_col] * s["expected_gross_cr"].clip(lower=0)
    # A compromise: dampen size so one giant name cannot dominate the list.
    s["rank_sqrt_size"] = s[prob_col] * np.sqrt(s["expected_gross_cr"].clip(lower=0))
    return s
