"""
Per-company model output.

Produces two things the backtest summary does not:

  1. BACKTEST OUTCOMES BY COMPANY - for every company the model actually scored
     in a held-out test month: how often it was ranked top-50, whether a cash
     event followed, and what it was worth. The "was the model right about THIS
     name" view.

  2. LIVE RANKING as of the latest data - model refit on everything available
     and scored forward. This is the origination list.

Both are point-in-time honest: the live model is trained only on months whose
labels have fully resolved, so nothing in the training set depends on an
outcome that has not happened yet.

Run: python3 score_companies.py
"""

from __future__ import annotations

import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from liquidity import l1_load
from liquidity.config import REPORT_DIR
from liquidity.l3_events import (
    build_event_register, build_promoter_registry, label_deals, lockin_calendar,
)
from liquidity.l3_features import TIER_FEATURES
from liquidity.l4_hazard import DiscreteHazard
from liquidity.l5_wallet import size_event
from liquidity.l6_ranking import attach_expected_size, rank_scores
from liquidity.t3_rules import run_t3

HORIZON = 6
TIER = "T2"


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    print("loading...", flush=True)
    store, F = l1_load.build_store()
    deals, shp, ipos, eq = F["deals"], F["shp"], F["ipos"], F["equity"]
    ann, pledge, pit = F.get("announcements"), F.get("pledge"), F.get("insider_pit")

    reg = build_promoter_registry(F.get("promoter_names", pd.DataFrame()), pit)
    ld = label_deals(deals, reg)
    events = build_event_register(ld, shp, pit)
    cal = lockin_calendar(ipos)

    panel = pd.read_parquet(f"{REPORT_DIR}/panel.parquet")
    names = dict(zip(eq["symbol"], eq["company"]))
    feats = [f for f in TIER_FEATURES[TIER] if f in panel.columns]
    label = f"y_{HORIZON}m"
    last_month = panel["month"].max()

    # ---------------------------------------------------------------- 1. backtest by company
    print("scoring backtest folds by company...", flush=True)
    from liquidity.backtest import walk_forward

    folds, scored, _ = walk_forward(
        panel, feats, label, HORIZON, embargo_months=12, min_train_months=24,
        step_months=6, test_window_months=6, wallet_col=f"wallet_{HORIZON}m_cr",
        verbose=False,
    )
    scored = attach_expected_size(scored, events)
    scored = rank_scores(scored)

    # rank within each test month, on both objectives
    scored["rank_in_month"] = scored.groupby("month")["score"].rank(ascending=False, method="first")
    scored["rank_in_month_wallet"] = scored.groupby("month")["rank_sqrt_size"].rank(
        ascending=False, method="first"
    )

    by_co = scored.groupby("symbol").agg(
        months_scored=("score", "size"),
        mean_score=("score", "mean"),
        max_score=("score", "max"),
        best_rank=("rank_in_month", "min"),
        times_top50=("rank_in_month", lambda s: int((s <= 50).sum())),
        times_top50_wallet=("rank_in_month_wallet", lambda s: int((s <= 50).sum())),
        events_followed=(label, "sum"),
        months_with_event=(label, "sum"),
        realised_wallet_cr=(f"wallet_{HORIZON}m_cr", "max"),
    ).reset_index()
    by_co["company"] = by_co["symbol"].map(names)
    by_co["hit_rate_when_scored"] = by_co["months_with_event"] / by_co["months_scored"]
    # was the model right when it actually surfaced this name?
    top = scored[scored["rank_in_month"] <= 50]
    t = top.groupby("symbol").agg(
        times_surfaced=("score", "size"),
        surfaced_hits=(label, "sum"),
        surfaced_wallet_cr=(f"wallet_{HORIZON}m_cr", "sum"),
    ).reset_index()
    by_co = by_co.merge(t, on="symbol", how="left")
    by_co["precision_when_surfaced"] = by_co["surfaced_hits"] / by_co["times_surfaced"]
    by_co = by_co.sort_values(["times_top50", "max_score"], ascending=False)
    by_co.to_csv(f"{REPORT_DIR}/company_backtest_results.csv", index=False)
    print(f"  wrote company_backtest_results.csv ({len(by_co):,} companies)")

    scored_out = scored[[
        "symbol", "month", "score", "rank_in_month", "rank_sqrt_size",
        "expected_gross_cr", label, f"wallet_{HORIZON}m_cr",
        "promoter_pct", "months_since_last_event", "prior_event_count",
        "lockin_expired_90d", "trading_plan_active",
    ]].copy()
    scored_out["company"] = scored_out["symbol"].map(names)
    scored_out.to_csv(f"{REPORT_DIR}/company_scores_backtest.csv", index=False)

    # ---------------------------------------------------------------- 2. live ranking
    print("fitting live model...", flush=True)
    # Train only where the label has fully resolved.
    train = panel[panel["month"] + pd.DateOffset(months=HORIZON) <= last_month]
    live_month = last_month
    live = panel[panel["month"] == live_month].copy()

    m = DiscreteHazard(feats).fit(train, train[label])
    live["score"] = m.predict_proba(live)
    live = attach_expected_size(live, events)
    live = rank_scores(live)
    live["company"] = live["symbol"].map(names)

    # wallet arithmetic on the expected size
    sized = [size_event(g, cost_basis_fraction=0.10) for g in live["expected_gross_cr"].fillna(0)]
    s = pd.DataFrame(sized, index=live.index)
    live["net_if_event_cr"] = s["net_cr"]
    live["addressable_aum_if_event_cr"] = s["addressable_aum_cr"]
    live["expected_addressable_aum_cr"] = live["score"] * s["addressable_aum_cr"]

    # join the deterministic T3 alerts
    alerts = run_t3(pd.Timestamp("2026-08-07"), ann, cal, shp)
    if len(alerts):
        a = alerts.groupby("symbol").agg(
            t3_rules=("rule", lambda x: "; ".join(sorted(set(x))[:3])),
            t3_best_confidence=("confidence", "first"),
            t3_days_to_eligible=("days_to_eligible", "min"),
        ).reset_index()
        live = live.merge(a, on="symbol", how="left")

    live["rank_prob_pos"] = live["score"].rank(ascending=False, method="first")
    live["rank_wallet_pos"] = live["rank_sqrt_size"].rank(ascending=False, method="first")
    live = live.sort_values("rank_sqrt_size", ascending=False)

    cols = [
        "rank_wallet_pos", "rank_prob_pos", "symbol", "company", "score",
        "expected_gross_cr", "expected_addressable_aum_cr",
        "promoter_pct", "months_since_last_event", "prior_event_count",
        "lockin_expired_90d", "lockin_90d", "trading_plan_active",
        "t3_rules", "t3_best_confidence", "t3_days_to_eligible",
    ]
    cols = [c for c in cols if c in live.columns]
    live[cols].to_csv(f"{REPORT_DIR}/company_live_ranking.csv", index=False)
    print(f"  wrote company_live_ranking.csv (as of {live_month:%Y-%m}, {len(live):,} companies)")

    print("\nTOP 20 BY EXPECTED WALLET (live, as of "
          f"{live_month:%Y-%m}):")
    show = live.head(20)[["symbol", "company", "score", "expected_gross_cr",
                          "expected_addressable_aum_cr", "months_since_last_event"]]
    print(show.round(3).to_string(index=False))

    print(f"\nmodel: {TIER}, horizon {HORIZON}m, trained on {len(train):,} entity-months "
          f"({int(train[label].sum()):,} events) through {train['month'].max():%Y-%m}")
    return by_co, live, scored


if __name__ == "__main__":
    main()
