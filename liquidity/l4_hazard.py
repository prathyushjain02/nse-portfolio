"""
L4 Discrete-time competing-risks hazard model (section 7.1).

Panel: one row per entity x month. For entity i, month t, event type
k in {promoter_selldown, ..., none}:

    h_k(i,t) = P(T_i = t, K_i = k | T_i >= t, X_{i,t})

Multinomial logit with "no event" as base:

    log( h_k(i,t) / h_0(i,t) ) = alpha_k(t) + beta_k' X_{i,t}

alpha_k(t) is a piecewise-constant baseline in tenure (months since listing),
which is the right clock for Track D because lock-in structure is anchored to
listing date.

Section 7.1 is explicit: "Start with the multinomial logit... Do not start
with the fancy model." So the primary model here is a regularised multinomial
logit. A gradient-boosted comparator is included ONLY to check whether the
logit is leaving signal on the table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


# tenure bins for the piecewise-constant baseline alpha_k(t)
TENURE_BINS = [0, 6, 12, 18, 24, 36, 60, 120, 10_000]


def add_baseline_dummies(X: pd.DataFrame, tenure_col: str = "months_since_listing") -> pd.DataFrame:
    """Piecewise-constant baseline hazard in tenure."""
    if tenure_col not in X:
        return X
    b = pd.cut(X[tenure_col], bins=TENURE_BINS, labels=False, include_lowest=True)
    d = pd.get_dummies(b.astype("Int64"), prefix="tenure", dummy_na=True)
    # drop one level to avoid collinearity with the intercept
    if d.shape[1] > 1:
        d = d.iloc[:, 1:]
    return pd.concat([X.reset_index(drop=True), d.reset_index(drop=True).astype(float)], axis=1)


class DiscreteHazard:
    """Binary or multinomial discrete-time hazard with calibrated probabilities."""

    def __init__(self, features: list[str], C: float = 1.0, model: str = "logit", seed: int = 0):
        self.features = features
        self.model_kind = model
        self.C = C
        self.seed = seed
        self.pipe = None
        self.cols_ = None

    def _design(self, panel: pd.DataFrame) -> pd.DataFrame:
        X = panel[[c for c in self.features if c in panel.columns]].copy()
        X = add_baseline_dummies(X)
        X = X.replace([np.inf, -np.inf], np.nan)
        return X

    def fit(self, panel: pd.DataFrame, y: pd.Series):
        X = self._design(panel)
        self.cols_ = list(X.columns)
        self.medians_ = X.median(numeric_only=True)
        X = X.fillna(self.medians_).fillna(0)
        if self.model_kind == "logit":
            self.pipe = Pipeline(
                [
                    ("sc", StandardScaler()),
                    (
                        "lr",
                        LogisticRegression(
                            C=self.C,
                            max_iter=2000,
                            solver="lbfgs",
                            class_weight=None,  # keep probabilities calibrated
                            random_state=self.seed,
                        ),
                    ),
                ]
            )
        else:
            self.pipe = Pipeline(
                [
                    (
                        "gb",
                        HistGradientBoostingClassifier(
                            max_iter=250,
                            learning_rate=0.06,
                            max_leaf_nodes=15,
                            min_samples_leaf=200,
                            l2_regularization=1.0,
                            random_state=self.seed,
                        ),
                    )
                ]
            )
        self.pipe.fit(X.values, y.values)
        return self

    def predict_proba(self, panel: pd.DataFrame) -> np.ndarray:
        X = self._design(panel)
        for c in self.cols_:
            if c not in X:
                X[c] = np.nan
        X = X[self.cols_].fillna(self.medians_).fillna(0)
        return self.pipe.predict_proba(X.values)[:, 1]

    def coefficients(self) -> pd.DataFrame:
        if self.model_kind != "logit":
            return pd.DataFrame()
        lr = self.pipe.named_steps["lr"]
        return (
            pd.DataFrame({"feature": self.cols_, "coef": lr.coef_[0]})
            .assign(odds_ratio=lambda d: np.exp(d["coef"]))
            .sort_values("coef", key=np.abs, ascending=False)
            .reset_index(drop=True)
        )


class CompetingRisks:
    """Competing-risks wrapper: one hazard per event type, sharing the design.

    Section 8.6: with small N per track, pool the baseline hazard across tracks
    and let only beta differ. Here each risk gets its own beta; the shared
    tenure baseline is the pooling device.
    """

    def __init__(self, features: list[str], risks: list[str], **kw):
        self.risks = risks
        self.models = {r: DiscreteHazard(features, **kw) for r in risks}

    def fit(self, panel: pd.DataFrame, y_by_risk: dict[str, pd.Series]):
        for r in self.risks:
            y = y_by_risk[r]
            if y.sum() < 25:  # too few events to identify beta
                self.models[r] = None
                continue
            self.models[r].fit(panel, y)
        return self

    def hazards(self, panel: pd.DataFrame) -> pd.DataFrame:
        out = {}
        for r, m in self.models.items():
            out[r] = m.predict_proba(panel) if m is not None else np.zeros(len(panel))
        return pd.DataFrame(out, index=panel.index)

    def any_event_prob(self, panel: pd.DataFrame) -> np.ndarray:
        """P(any event) = 1 - prod_k (1 - h_k), assuming conditional independence
        across risks given X - the standard discrete-time approximation."""
        h = self.hazards(panel)
        return 1 - (1 - h).prod(axis=1).values


def cumulative_probability(monthly_hazard: np.ndarray, horizon: int) -> np.ndarray:
    """P(event within H) = 1 - prod_{s=t}^{t+H} (1 - h(i,s))

    Applied with a constant hazard over the horizon; used only for translating a
    fitted monthly hazard into a horizon probability for presentation.
    """
    return 1 - (1 - monthly_hazard) ** horizon
