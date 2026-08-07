"""
L5 Wallet sizing (section 9). "Score without size is a badly ordered list."

    Gross proceeds  = stake_sold_% x implied_equity_value x (1 - secondary_discount)
    Net proceeds    = Gross - capital_gains_tax - pledge/debt_repayment - reinvestment
    Addressable AUM = Net x propensity_to_externalise

For listed Track-D events we do not need the regression: the deal tape gives
quantity x price directly, so gross proceeds are ARITHMETIC, not an estimate.
The modelled parts are tax, and the propensity to externalise - which the
framework is explicit must be calibrated from a firm's own conversion history
and NOT assumed. We expose it as a parameter and refuse to pretend it is fitted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from liquidity.config import TAX, WALLET, effective_ltcg_rate


def gross_proceeds_cr(qty: float, price: float) -> float:
    return qty * price / 1e7


def capital_gains_tax_cr(
    gross_cr: float,
    cost_basis_fraction: float = 0.20,
    listed: bool = True,
    long_term: bool = True,
) -> float:
    """Tax on the gain, not the proceeds.

    cost_basis_fraction is the acquisition cost as a fraction of sale value.
    For a founder/promoter selling shares acquired at or near par decades ago,
    this is close to zero; 0.20 is a deliberately conservative default. This is
    the single largest source of error in net-proceeds estimates and should be
    replaced with actual cost basis wherever the bank can obtain it.
    """
    gain = max(0.0, gross_cr * (1 - cost_basis_fraction))
    if listed and long_term:
        rate = effective_ltcg_rate(listed=True)
        exempt = TAX["listed_ltcg_exemption"] / 1e7
        return max(0.0, (gain - exempt)) * rate
    if listed and not long_term:
        return gain * TAX["listed_stcg_rate"] * (1 + TAX["surcharge_cap"]) * (1 + TAX["cess"])
    if not listed and long_term:
        return gain * effective_ltcg_rate(listed=False)
    return gain * TAX["unlisted_stcg_rate"] * (1 + TAX["surcharge_cap"]) * (1 + TAX["cess"])


def size_event(
    gross_cr: float,
    cost_basis_fraction: float = 0.20,
    listed: bool = True,
    long_term: bool = True,
    debt_repayment_cr: float = 0.0,
    reinvestment_fraction: float = 0.0,
    propensity: float | None = None,
) -> dict:
    propensity = WALLET["propensity_to_externalise"] if propensity is None else propensity
    tax = capital_gains_tax_cr(gross_cr, cost_basis_fraction, listed, long_term)
    net = gross_cr - tax - debt_repayment_cr
    net -= max(0.0, net) * reinvestment_fraction
    return {
        "gross_cr": gross_cr,
        "tax_cr": tax,
        "effective_tax_rate_on_proceeds": tax / gross_cr if gross_cr else np.nan,
        "net_cr": net,
        "addressable_aum_cr": max(0.0, net) * propensity,
        "propensity_assumed": propensity,
    }


def size_expected_wallet(
    scored: pd.DataFrame,
    prob_col: str = "score",
    expected_gross_col: str = "expected_gross_cr",
    **kw,
) -> pd.DataFrame:
    """Expected addressable AUM = P(event) x addressable AUM if it happens.

    This is what the ranking in L6 should sort on, not probability alone.
    """
    out = scored.copy()
    sized = [size_event(g if np.isfinite(g) else 0.0, **kw) for g in out[expected_gross_col]]
    s = pd.DataFrame(sized, index=out.index)
    out["net_cr_if_event"] = s["net_cr"]
    out["addressable_aum_if_event_cr"] = s["addressable_aum_cr"]
    out["expected_addressable_aum_cr"] = out[prob_col] * s["addressable_aum_cr"]
    return out


def historical_gross_by_symbol(events: pd.DataFrame) -> pd.Series:
    """Empirical prior for deal size: median observed insider sell value per
    symbol, falling back to the panel-wide median."""
    e = events[events["event_source"] == "deal_tape"]
    per = e.groupby("symbol")["gross_proceeds_cr"].median()
    return per
