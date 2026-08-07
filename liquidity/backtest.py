"""
Section 8.3 - walk-forward, expanding window, with an embargo gap.

Two separate leakage controls are applied, and the distinction matters:

  LABEL-WINDOW GAP (horizon)  A training row at month M carries a label that
      resolves at M+H. If the test period starts before M+H, the model is
      trained on outcomes that overlap the test period. So training must stop
      at test_start - H.

  EMBARGO (section 8.3)       A further 12-month gap "to prevent leakage
      through slow-moving features". Applied on top of the horizon gap.

Ranking is done WITHIN each test month, because precision@50 means "the 50
names an RM team can actually work this month" (section 8.4).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from liquidity.l4_hazard import DiscreteHazard
from liquidity.metrics import evaluate


def walk_forward(
    panel: pd.DataFrame,
    features: list[str],
    label_col: str,
    horizon_months: int,
    embargo_months: int = 12,
    min_train_months: int = 24,
    step_months: int = 6,
    test_window_months: int = 6,
    model: str = "logit",
    wallet_col: str | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """Returns (fold_metrics, scored_panel, fitted_models)."""
    months = np.sort(panel["month"].unique())
    if len(months) == 0:
        return pd.DataFrame(), pd.DataFrame(), []

    first = pd.Timestamp(months[0])
    last = pd.Timestamp(months[-1])

    folds, scored, models = [], [], []

    # earliest possible test start: enough train + horizon + embargo
    test_start = first + pd.DateOffset(months=min_train_months + horizon_months + embargo_months)

    while test_start <= last:
        test_end = test_start + pd.DateOffset(months=test_window_months)
        train_end = test_start - pd.DateOffset(months=horizon_months + embargo_months)

        tr = panel[panel["month"] < train_end]
        te = panel[(panel["month"] >= test_start) & (panel["month"] < test_end)]

        # a test row's label must be fully resolvable inside the data window
        te = te[te["month"] + pd.DateOffset(months=horizon_months) <= last]

        if len(tr) < 500 or len(te) < 100 or tr[label_col].sum() < 30 or te[label_col].sum() < 5:
            test_start += pd.DateOffset(months=step_months)
            continue

        m = DiscreteHazard(features, model=model).fit(tr, tr[label_col])
        s = m.predict_proba(te)

        t = te.copy()
        t["score"] = s
        t["fold_test_start"] = test_start
        scored.append(t)
        models.append(m)

        # per-month ranking, then average across the months in the fold
        per_month = []
        for mth, g in t.groupby("month"):
            w = g[wallet_col].values if wallet_col else None
            per_month.append(evaluate(g[label_col].values, g["score"].values, w, label=str(mth)))
        pm = pd.DataFrame(per_month)
        agg = pm.drop(columns=["label"]).mean(numeric_only=True).to_dict()
        agg.update(
            fold_test_start=test_start,
            train_end=train_end,
            n_train=len(tr),
            n_test=len(te),
            train_events=int(tr[label_col].sum()),
            test_events=int(te[label_col].sum()),
        )
        folds.append(agg)
        if verbose:
            print(
                f"    fold test {test_start:%Y-%m} | train<{train_end:%Y-%m} "
                f"n={len(tr):,}/{len(te):,} ev={int(tr[label_col].sum())}/{int(te[label_col].sum())} "
                f"p@50={agg.get('p@50', np.nan):.3f} lift@50={agg.get('lift@50', np.nan):.2f} "
                f"auc={agg.get('auc', np.nan):.3f}",
                flush=True,
            )
        test_start += pd.DateOffset(months=step_months)

    fold_df = pd.DataFrame(folds)
    scored_df = pd.concat(scored, ignore_index=True) if scored else pd.DataFrame()
    return fold_df, scored_df, models


def score_baselines_walk_forward(
    panel: pd.DataFrame,
    label_col: str,
    baseline_fn,
    scored_index: pd.DataFrame,
    wallet_col: str | None = None,
) -> pd.DataFrame:
    """Evaluate baselines on exactly the same test rows the model saw, so the
    comparison is like-for-like."""
    if scored_index.empty:
        return pd.DataFrame()
    rows = []
    bl = baseline_fn(scored_index)
    for name, sc in bl.items():
        t = scored_index.copy()
        t["score"] = sc
        per_month = []
        for mth, g in t.groupby("month"):
            w = g[wallet_col].values if wallet_col else None
            per_month.append(evaluate(g[label_col].values, g["score"].values, w, label=str(mth)))
        pm = pd.DataFrame(per_month).drop(columns=["label"]).mean(numeric_only=True).to_dict()
        pm["model"] = name
        rows.append(pm)
    return pd.DataFrame(rows)
