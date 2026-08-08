"""
Liquidity Event Prediction Model - full build + backtest.

Run: python3 run_all.py

Scope, stated up front (see reports/RESULTS.md for the full version):
This build covers TRACK D (listed promoter/insider sell-down) end to end on
real NSE data, plus the deterministic parts of TRACK A stage 3 (post-listing
lock-in calendar). Tracks A stage 1-2, B, C, E and F require MCA21,
private-market and rating-agency data that is not accessible from this
environment; those code paths are specified but NOT backtested, and the
hypotheses that depend on them are reported as NOT_TESTABLE rather than
guessed at.
"""

from __future__ import annotations

import json
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from liquidity.config import BACKTEST, EVENT_DEF, REPORT_DIR, WALLET
from liquidity import l1_load, leakage
from liquidity.backtest import score_baselines_walk_forward, walk_forward
from liquidity.baselines import baseline_scores
from liquidity.hypotheses import (
    NOT_TESTABLE, test_H1_lockin, test_H2_trading_plans, test_H8_mechanism, test_H8_repeat,
)
from liquidity.l3_events import (
    build_event_register, build_promoter_registry, label_deals, lockin_calendar,
)
from liquidity.l3_features import FAMILY_COVERAGE, TIER_FEATURES, attach_labels, build_panel, month_index
from liquidity.l5_wallet import size_event
from liquidity.metrics import calibration_table, decile_table, lead_time_distribution
from liquidity.t3_rules import run_t3

OUT = {}
os.makedirs(REPORT_DIR, exist_ok=True)


def section(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


def main():
    # ---------------------------------------------------------------- L1/L2
    section("L1/L2  Load + point-in-time audit")
    store, F = l1_load.build_store()
    audit = store.audit()
    print(audit.to_string(index=False))
    audit.to_csv(f"{REPORT_DIR}/pit_audit.csv", index=False)
    OUT["pit_audit"] = audit.to_dict("records")

    deals, shp, ipos, eq = F["deals"], F["shp"], F["ipos"], F["equity"]
    ann = F.get("announcements")
    pledge = F.get("pledge")
    pit = F.get("insider_pit")
    print(f"\ndeals {len(deals):,} | shp {len(shp):,} | ipos {len(ipos):,} | universe {len(eq):,}")
    if ann is not None:
        print(f"announcements {len(ann):,} (trading plans: {int(ann['is_trading_plan'].sum()):,})")

    # ---------------------------------------------------------------- L0
    section("L0  Entity resolution + promoter registry")
    reg = build_promoter_registry(F.get("promoter_names", pd.DataFrame()), pit)
    print(f"insider/promoter identities resolved: {reg.size():,} across {len(reg._by_symbol):,} symbols")
    if pit is not None and len(pit):
        print(f"  of which from PIT Reg 7(2) (point-in-time, incl. directors/KMP): "
              f"{int(pit['is_insider_cat'].sum()):,}")

    ld = label_deals(deals, reg)
    print(f"deals tagged: {int(ld['is_insider'].sum()):,} insider-matched of {len(ld):,}")
    print("\ncounterparty type mix:")
    print(ld["holder_type"].value_counts().to_string())

    # ---------------------------------------------------------------- L3 events
    section("L3  Event register (cash-dated)")
    events = build_event_register(ld, shp, pit)
    events.to_csv(f"{REPORT_DIR}/event_register.csv", index=False)
    print(events.groupby("event_source").agg(
        n=("cash_date", "size"),
        first=("cash_date", "min"),
        last=("cash_date", "max"),
        gross_cr=("gross_proceeds_cr", "sum"),
    ).to_string())

    dt = events[events["event_source"] == "deal_tape"]
    print(f"\ndeal-tape events: {len(dt):,}  |  total gross Rs {dt['gross_proceeds_cr'].sum():,.0f} cr")
    print(f"median event size: Rs {dt['gross_proceeds_cr'].median():.1f} cr   "
          f"p90: Rs {dt['gross_proceeds_cr'].quantile(0.9):.1f} cr")
    print(f"distinct beneficiaries: {dt['beneficiary_key'].nunique():,}")

    cal = lockin_calendar(ipos)
    print(f"\nlock-in calendar: {len(cal):,} dated expiries for {cal['symbol'].nunique():,} companies")

    # ---------------------------------------------------------------- L3 panel
    section("L3  PIT panel construction")
    # Panel starts once promoter identity is knowable and shareholding coverage
    # is broad; both bind at 2021H2.
    pn = F.get("promoter_names", pd.DataFrame())
    pstart = pd.Timestamp("2021-07-01")
    if len(pn):
        q = pn["known_from"].quantile(0.5)
        print(f"promoter-identity median known_from: {q:%Y-%m-%d}")
    months = month_index(pstart.strftime("%Y-%m-%d"), "2026-07-01")
    print(f"panel window: {months[0]:%Y-%m} .. {months[-1]:%Y-%m}  ({len(months)} months)")

    uni = eq[["symbol", "listing_date"]]
    panel = build_panel(uni, months, events, ld, shp, cal, ann, pledge)
    panel = attach_labels(panel, events, horizons=(3, 6, 12))
    print(f"\npanel: {len(panel):,} entity-months, {panel['symbol'].nunique():,} entities")
    for h in (3, 6, 12):
        print(f"  base rate y_{h}m: {panel[f'y_{h}m'].mean()*100:.2f}%  "
              f"({int(panel[f'y_{h}m'].sum()):,} positive entity-months)")
    panel.to_parquet(f"{REPORT_DIR}/panel.parquet", index=False)

    OUT["panel"] = dict(
        rows=int(len(panel)), entities=int(panel["symbol"].nunique()),
        window=f"{months[0]:%Y-%m}..{months[-1]:%Y-%m}",
        base_rate_3m=float(panel["y_3m"].mean()),
        base_rate_6m=float(panel["y_6m"].mean()),
        base_rate_12m=float(panel["y_12m"].mean()),
    )

    # ---------------------------------------------------------------- hypotheses
    section("Section 10  Hypothesis tests on real data")
    h1 = test_H1_lockin(panel, "y_3m")
    print("\nH1 - lock-in expiry raises P(sell within 90 days):")
    print(h1[["test", "n_treated", "rate_treated", "ci_treated", "rate_control", "lift", "p_value"]].to_string(index=False))
    h1.to_csv(f"{REPORT_DIR}/H1_lockin.csv", index=False)

    h2 = test_H2_trading_plans(ann, events, ld, pit)
    print("\nH2 - disclosed trading plan is followed by actual selling:")
    for k, v in h2.items():
        if k != "detail":
            print(f"  {k}: {v}")
    if "detail" in h2:
        h2["detail"].to_csv(f"{REPORT_DIR}/H2_trading_plans.csv", index=False)

    h8 = test_H8_repeat(panel, "y_12m")
    print("\nH8 - prior liquidity history predicts repeat events:")
    print(h8[["test", "n_treated", "rate_treated", "ci_treated", "rate_control", "lift", "p_value"]].to_string(index=False))
    h8.to_csv(f"{REPORT_DIR}/H8_repeat.csv", index=False)

    h8m = test_H8_mechanism(panel, events, "y_6m")
    print("\nH8 mechanism - dribble-out vs clustering vs persistent type:")
    print(f"  never-sold baseline y_6m: {h8m['never_sold_baseline']:.4f}")
    print(h8m["hazard_by_recency"].round(4).to_string(index=False))
    print(f"\n  repeat person-company sellers: {h8m.get('n_repeat_seller_pairs', 0):,}")
    print(f"  inter-event gap: median {h8m.get('gap_days_median', float('nan')):.0f}d "
          f"IQR {h8m.get('gap_days_iqr', '-')}  CV={h8m.get('gap_cv', float('nan')):.2f}")
    print(f"  -> {h8m.get('gap_interpretation', '')}")
    print("\n  conditioned on remaining promoter stake:")
    print(h8m["conditioned_on_remaining_stake"].round(4).to_string(index=False))
    h8m["hazard_by_recency"].to_csv(f"{REPORT_DIR}/H8_hazard_by_recency.csv", index=False)
    OUT["H8_mechanism"] = {
        k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in h8m.items()
    }

    print("\nNOT TESTABLE with available data:")
    for k, v in NOT_TESTABLE.items():
        print(f"  - {k}\n      {v}")

    OUT["H1"] = h1.to_dict("records")
    OUT["H2"] = {k: v for k, v in h2.items() if k != "detail"}
    OUT["H8"] = h8.to_dict("records")

    # ---------------------------------------------------------------- backtest
    section("Section 8.3  Walk-forward backtest (expanding window, 12m embargo)")
    results = {}
    scored_store = {}
    for horizon in (3, 6, 12):
        for tier in ("T1", "T2", "T3"):
            feats = [f for f in TIER_FEATURES[tier] if f in panel.columns]
            print(f"\n--- {tier} / horizon {horizon}m / {len(feats)} features")
            folds, scored, models = walk_forward(
                panel, feats, f"y_{horizon}m", horizon,
                embargo_months=BACKTEST["embargo_months"],
                min_train_months=24, step_months=6, test_window_months=6,
                wallet_col=f"wallet_{horizon}m_cr",
            )
            if folds.empty:
                print("    (no valid folds - insufficient history for this horizon)")
                continue
            key = f"{tier}_{horizon}m"
            results[key] = folds
            scored_store[key] = scored
            m = folds.mean(numeric_only=True)
            print(f"    MEAN over {len(folds)} folds: p@25={m.get('p@25',np.nan):.3f} "
                  f"p@50={m.get('p@50',np.nan):.3f} p@100={m.get('p@100',np.nan):.3f} "
                  f"lift@50={m.get('lift@50',np.nan):.2f} auc={m.get('auc',np.nan):.3f} "
                  f"cal_err={m.get('cal_error',np.nan):.4f} "
                  f"wallet_p@50={m.get('wallet_p@50',np.nan):.3f}")
            if models and tier == "T2" and horizon == 6:
                coefs = models[-1].coefficients()
                coefs.to_csv(f"{REPORT_DIR}/coefficients_T2_6m.csv", index=False)
                print("\n    top coefficients (last fold, standardised):")
                print(coefs.head(12).to_string(index=False))

    summary = []
    for k, f in results.items():
        m = f.mean(numeric_only=True).to_dict()
        m["model"] = k
        m["n_folds"] = len(f)
        summary.append(m)
    summ = pd.DataFrame(summary)
    summ.to_csv(f"{REPORT_DIR}/backtest_summary.csv", index=False)
    OUT["backtest"] = summ.to_dict("records")

    # ---------------------------------------------------------------- baselines
    section("Section 8.5  Baselines the model must beat")
    prim = "T2_6m" if "T2_6m" in scored_store else list(scored_store)[0]
    si = scored_store[prim]
    bl = score_baselines_walk_forward(panel, "y_6m", baseline_scores, si, f"wallet_6m_cr")
    mrow = results[prim].mean(numeric_only=True).to_dict()
    mrow["model"] = f"MODEL {prim}"
    comp = pd.concat([bl, pd.DataFrame([mrow])], ignore_index=True)
    cols = ["model", "base_rate", "p@25", "p@50", "p@100", "lift@50", "wallet_p@50", "auc"]
    comp = comp[[c for c in cols if c in comp.columns]].sort_values("p@50", ascending=False)
    print(comp.to_string(index=False))
    comp.to_csv(f"{REPORT_DIR}/baselines.csv", index=False)
    OUT["baselines"] = comp.to_dict("records")

    # ---------------------------------------------------------------- lead time
    section("Section 8.4  Lead-time distribution (the metric the business cares about)")
    lt_rows = []
    for thr_name, q in (("top decile", 0.90), ("top 5%", 0.95), ("top 1%", 0.99)):
        thr = np.quantile(si["score"], q)
        lt = lead_time_distribution(si, events[events["cash_date"] >= si["month"].min()], thr)
        if len(lt):
            lt_rows.append(dict(
                threshold=thr_name, n_events_with_prior_flag=len(lt),
                median_lead_months=float(lt["lead_months"].median()),
                p25=float(lt["lead_months"].quantile(0.25)),
                p75=float(lt["lead_months"].quantile(0.75)),
            ))
    ltdf = pd.DataFrame(lt_rows)
    print(ltdf.to_string(index=False) if len(ltdf) else "  (no flagged events)")
    ltdf.to_csv(f"{REPORT_DIR}/lead_time.csv", index=False)
    OUT["lead_time"] = ltdf.to_dict("records")

    # ---------------------------------------------------------------- calibration
    section("Section 8.4  Calibration + decile lift")
    ct = calibration_table(si["y_6m"].values, si["score"].values)
    print("\ncalibration (predicted vs realised):")
    print(ct.to_string(index=False))
    ct.to_csv(f"{REPORT_DIR}/calibration.csv", index=False)
    dt_ = decile_table(si["y_6m"].values, si["score"].values, si["wallet_6m_cr"].values)
    print("\ndecile lift:")
    print(dt_.to_string(index=False))
    dt_.to_csv(f"{REPORT_DIR}/deciles.csv", index=False)
    OUT["calibration"] = ct.to_dict("records")
    OUT["deciles"] = dt_.to_dict("records")

    # ---------------------------------------------------------------- leakage
    section("Section 8.1  Leakage demonstration: PIT vs naive join")
    naive_shp = leakage.make_naive_shp(shp)
    npanel = build_panel(uni, months, events, ld, naive_shp, cal, ann, pledge, verbose=False)
    npanel = attach_labels(npanel, events, horizons=(6,))
    feats = [f for f in TIER_FEATURES["T2"] if f in npanel.columns]
    nfolds, _, _ = walk_forward(npanel, feats, "y_6m", 6, embargo_months=BACKTEST["embargo_months"],
                               min_train_months=24, step_months=6, test_window_months=6,
                               wallet_col="wallet_6m_cr", verbose=False)
    pit_m = results["T2_6m"].mean(numeric_only=True)
    nv_m = nfolds.mean(numeric_only=True) if len(nfolds) else pd.Series(dtype=float)
    cmp = pd.DataFrame([
        {"join": "PIT (known_from = broadcast date)", **{k: pit_m.get(k) for k in ["p@50", "lift@50", "auc"]}},
        {"join": "NAIVE (known_from = quarter end)", **{k: nv_m.get(k) for k in ["p@50", "lift@50", "auc"]}},
    ])
    print(cmp.to_string(index=False))
    cmp.to_csv(f"{REPORT_DIR}/leakage_comparison.csv", index=False)
    OUT["leakage"] = cmp.to_dict("records")

    # Backtest scores are a WEAK instrument for detecting leakage. Measure the
    # leak directly at the feature level as well.
    exp = leakage.pit_exposure(panel, npanel)
    rev = leakage.revision_exposure(shp)
    print("\ndirect leakage exposure (what a naive join actually hands the model):")
    for k, v in exp.items():
        print(f"  {k}: {v:,.3f}" if isinstance(v, float) else f"  {k}: {v}")
    print("\nrestatement exposure in the shareholding feed:")
    for k, v in rev.items():
        print(f"  {k}: {v:,.2f}" if isinstance(v, float) else f"  {k}: {v}")
    OUT["pit_exposure"] = exp
    OUT["revision_exposure"] = rev

    # embargo sensitivity
    print("\nembargo sensitivity (T2 / 6m):")
    emb_rows = []
    for e in (0, 6, 12):
        f2, _, _ = walk_forward(panel, [f for f in TIER_FEATURES["T2"] if f in panel.columns],
                                "y_6m", 6, embargo_months=e, min_train_months=24,
                                step_months=6, test_window_months=6,
                                wallet_col="wallet_6m_cr", verbose=False)
        if len(f2):
            mm = f2.mean(numeric_only=True)
            emb_rows.append(dict(embargo_months=e, folds=len(f2), **{k: float(mm.get(k, np.nan)) for k in ["p@50", "lift@50", "auc"]}))
    edf = pd.DataFrame(emb_rows)
    print(edf.to_string(index=False))
    edf.to_csv(f"{REPORT_DIR}/embargo_sensitivity.csv", index=False)
    OUT["embargo"] = edf.to_dict("records")

    # ---------------------------------------------------------------- wallet
    section("L5  Wallet sizing")
    ex = [10, 50, 100, 500]
    ws = pd.DataFrame([size_event(g, cost_basis_fraction=0.10) for g in ex])
    ws.insert(0, "gross_cr_input", ex)
    print(ws.round(3).to_string(index=False))
    ws.to_csv(f"{REPORT_DIR}/wallet_examples.csv", index=False)
    # ---- L6: does ranking on expected proceeds fix wallet capture? ----
    section("L6  Ranking objective: probability vs expected proceeds")
    from liquidity.l6_ranking import attach_expected_size, rank_scores
    from liquidity.metrics import evaluate as _eval

    ranked = rank_scores(attach_expected_size(si, events))
    rows = []
    for obj in ("rank_prob", "rank_expected_proceeds", "rank_sqrt_size"):
        per_month = []
        for mth, g in ranked.groupby("month"):
            per_month.append(
                _eval(g["y_6m"].values, g[obj].values, g["wallet_6m_cr"].values, label=str(mth))
            )
        r = pd.DataFrame(per_month).drop(columns=["label"]).mean(numeric_only=True).to_dict()
        r["objective"] = obj
        rows.append(r)
    rk = pd.DataFrame(rows)[["objective", "p@25", "p@50", "p@100", "wallet_p@25", "wallet_p@50", "wallet_p@100"]]
    print(rk.to_string(index=False))
    rk.to_csv(f"{REPORT_DIR}/ranking_objectives.csv", index=False)
    OUT["ranking_objectives"] = rk.to_dict("records")

    realised = dt["gross_proceeds_cr"]
    print(f"\nrealised deal-tape event sizes: n={len(realised):,} "
          f"median Rs {realised.median():.1f} cr, mean Rs {realised.mean():.1f} cr, "
          f"total Rs {realised.sum():,.0f} cr")
    print(f"propensity_to_externalise assumed at {WALLET['propensity_to_externalise']:.0%} "
          f"- NOT fitted; must be calibrated from the firm's own conversions.")
    OUT["wallet_examples"] = ws.to_dict("records")

    # ---------------------------------------------------------------- T3
    section("T3  Rules engine - live alert list")
    as_of = pd.Timestamp("2026-08-07")
    alerts = run_t3(as_of, ann, cal, shp)
    print(f"alerts as of {as_of:%Y-%m-%d}: {len(alerts):,}")
    if len(alerts):
        print(alerts["rule"].value_counts().to_string())
        print("\nhighest-confidence alerts:")
        print(alerts.head(15).to_string(index=False))
        alerts.to_csv(f"{REPORT_DIR}/t3_alerts.csv", index=False)
    OUT["t3_alerts_n"] = int(len(alerts))

    # ---------------------------------------------------------------- coverage
    section("Feature family coverage (section 6)")
    for k, v in FAMILY_COVERAGE.items():
        print(f"  {k:26s} {v}")
    OUT["family_coverage"] = FAMILY_COVERAGE

    with open(f"{REPORT_DIR}/results.json", "w") as f:
        json.dump(OUT, f, indent=2, default=str)
    print(f"\nwrote {REPORT_DIR}/results.json")


if __name__ == "__main__":
    main()
