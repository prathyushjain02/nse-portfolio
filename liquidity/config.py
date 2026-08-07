"""
Configuration: regulatory + tax parameters.

Per framework section 13 ("Verify before build"): every regulatory parameter
lives here as configuration, never as a hard-coded constant in model code.
Each entry carries the source it must be re-verified against.

VERIFICATION STATUS: values below are transcribed from the framework document
(dated FY2026-27 assumptions). They have NOT been independently re-verified
against primary sources in this build. Do that before any production use.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# SEBI ICDR 2018 (as amended 2021) - lock-in periods, in months from allotment
# --------------------------------------------------------------------------
LOCKIN = {
    # Minimum promoter contribution (20% of post-issue capital).
    # 18 months normally; 3 years (36m) where >50% of fresh issue funds capex.
    # The offer document discloses which applies. We cannot read offer docs
    # here, so we calendar BOTH and flag which is assumed.
    "mpc_standard_months": 18,
    "mpc_capex_months": 36,
    # Promoter holding in excess of the 20% MPC
    "promoter_excess_months": 6,
    # Non-promoter pre-issue capital
    "non_promoter_months": 6,
    # AIF/VCF held 6+ months pre-RHP
    "aif_vcf_months": 6,
    # Anchor investors
    "anchor_months": (1, 3),  # 30 / 90 days
}

# Which MPC assumption to use as the base case when the offer document is
# unavailable. Sensitivity-tested in the backtest.
LOCKIN_MPC_ASSUMPTION = "mpc_standard_months"

# --------------------------------------------------------------------------
# SEBI PIT Regulations - trading plans
# --------------------------------------------------------------------------
# PIT (Second Amendment) Regulations 2024, effective 24-Sep-2024:
# trading cannot commence until 120 calendar days after public disclosure.
TRADING_PLAN = {
    "cooling_off_days": 120,
    "regime_effective_from": "2024-09-24",
    # Pre-amendment regime: 6 months cooling off, min 12 month duration
    "pre_amendment_cooling_off_days": 180,
}

# --------------------------------------------------------------------------
# Capital gains tax (India, FY2026-27 assumptions - VERIFY AT BUILD TIME)
# --------------------------------------------------------------------------
TAX = {
    "listed_ltcg_rate": 0.125,        # >12m holding, no indexation
    "listed_ltcg_exemption": 125_000,  # Rs 1.25 lakh
    "listed_stcg_rate": 0.20,         # <12m holding
    "listed_ltcg_holding_months": 12,
    "unlisted_ltcg_rate": 0.125,      # >24m holding, no indexation, no exemption
    "unlisted_ltcg_exemption": 0,
    "unlisted_stcg_rate": 0.30,       # slab, assume top slab
    "unlisted_ltcg_holding_months": 24,
    "surcharge_cap": 0.15,            # surcharge on capital gains capped at 15%
    "cess": 0.04,
}


def effective_ltcg_rate(listed: bool = True) -> float:
    """All-in effective LTCG rate including capped surcharge and cess.

    Listed: 12.5% x 1.15 x 1.04 ~= 14.95%
    """
    base = TAX["listed_ltcg_rate"] if listed else TAX["unlisted_ltcg_rate"]
    return base * (1 + TAX["surcharge_cap"]) * (1 + TAX["cess"])


# --------------------------------------------------------------------------
# Wallet sizing (section 9)
# --------------------------------------------------------------------------
WALLET = {
    # EY study of Indian startup secondaries: ~85% price at par with the
    # concurrent primary; where a discount exists it averages ~19% (sd ~9%).
    "secondary_par_probability": 0.85,
    "secondary_discount_mean": 0.19,
    "secondary_discount_sd": 0.09,
    # Soft parameter - MUST be calibrated from own historical conversions.
    # Framework says start 30-50%.
    "propensity_to_externalise": 0.40,
}

# --------------------------------------------------------------------------
# Event / label definitions (section 8.6 "definition drift" - freeze & version)
# --------------------------------------------------------------------------
LABEL_DEF_VERSION = "trackD-v1.0"

EVENT_DEF = {
    # A Track-D promoter sell-down is recorded when promoter+promoter-group
    # holding falls by at least this many percentage points between
    # consecutive quarterly shareholding patterns.
    "promoter_selldown_pp_threshold": 0.50,
    # Minimum bulk/block deal value (Rs cr) to count as a standalone event
    "min_deal_value_cr": 1.0,
    # Horizon tiers (months)
    "T1_horizon": (12, 36),
    "T2_horizon": (6, 12),
    "T3_horizon": (0, 6),
}

# --------------------------------------------------------------------------
# Backtest protocol (section 8.3)
# --------------------------------------------------------------------------
BACKTEST = {
    "embargo_months": 12,       # gap between train end and test start
    "train_start": "2016-01",
    "min_train_months": 36,
    "refit_frequency_months": 12,
    "precision_at_k": [25, 50, 100],
    # Conservative assumed publication lag for any source lacking a reliable
    # ingestion timestamp. Sensitivity-tested.
    "assumed_lag_days": {
        "shareholding_pattern": 21,   # we have real submission dates; used as fallback
        "mca_financials": 210,        # AOC-4 for FY-end March filed Oct-Nov
    },
}

DATA_DIR = "data"
RAW_DIR = "data/raw"
CACHE_DIR = "data/cache"
REPORT_DIR = "reports"
