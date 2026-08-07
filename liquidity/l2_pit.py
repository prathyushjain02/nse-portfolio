"""
L2 Point-in-time (bitemporal) store.

Section 8.1: "MCA filings have a filing date and an event date, often 30-180
days apart. If you join on event date, you have given the model a time machine
and your backtest will be spectacular and completely fake."

Every fact table registered here must declare:
    valid_from  - when the fact became true in the world (e.g. quarter end)
    known_from  - when WE could first have known it (broadcast/publication)

All feature construction goes through `as_of(known_date)`, which filters on
known_from only. `valid_from` is never allowed to leak into visibility.

The NSE shareholding-pattern feed is a real-world demonstration of why this
matters: it contains REVISIONS broadcast years after the quarter they describe.
A naive join on quarter-end date silently imports 2025 knowledge into 2019.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FactTable:
    name: str
    df: pd.DataFrame
    entity_col: str
    valid_from_col: str
    known_from_col: str
    value_cols: list[str] = field(default_factory=list)

    def __post_init__(self):
        d = self.df
        for c in (self.valid_from_col, self.known_from_col):
            if not np.issubdtype(d[c].dtype, np.datetime64):
                d[c] = pd.to_datetime(d[c], errors="coerce")
        # A fact whose known_from precedes its valid_from is either a data
        # error or genuine forward-looking disclosure. Flag loudly.
        bad = (d[self.known_from_col] < d[self.valid_from_col]).sum()
        self.n_known_before_valid = int(bad)
        # Facts with no known_from are unusable point-in-time.
        self.n_missing_known = int(d[self.known_from_col].isna().sum())
        self.df = d.sort_values(self.known_from_col).reset_index(drop=True)

    @property
    def publication_lag_days(self) -> pd.Series:
        return (self.df[self.known_from_col] - self.df[self.valid_from_col]).dt.days


class PITStore:
    """Bitemporal fact store with as-of-date visibility."""

    def __init__(self):
        self.tables: dict[str, FactTable] = {}

    def register(
        self,
        name: str,
        df: pd.DataFrame,
        entity_col: str,
        valid_from_col: str,
        known_from_col: str,
        value_cols: list[str] | None = None,
    ) -> FactTable:
        ft = FactTable(
            name=name,
            df=df.copy(),
            entity_col=entity_col,
            valid_from_col=valid_from_col,
            known_from_col=known_from_col,
            value_cols=value_cols or [],
        )
        self.tables[name] = ft
        return ft

    # ---------------- visibility ----------------

    def as_of(self, name: str, as_of_date, drop_missing_known: bool = True) -> pd.DataFrame:
        """All facts KNOWN as of `as_of_date`. The only sanctioned read path."""
        ft = self.tables[name]
        d = ft.df
        as_of_date = pd.Timestamp(as_of_date)
        m = d[ft.known_from_col] <= as_of_date
        if drop_missing_known:
            m &= d[ft.known_from_col].notna()
        return d.loc[m]

    def latest_as_of(
        self,
        name: str,
        as_of_date,
        by: list[str] | None = None,
    ) -> pd.DataFrame:
        """Most recent VALID fact per entity, among those already KNOWN.

        Handles revisions correctly: if a revision for an old quarter is not
        yet broadcast, the original stands; once broadcast, the revision wins.
        """
        ft = self.tables[name]
        d = self.as_of(name, as_of_date)
        if d.empty:
            return d
        by = by or [ft.entity_col]
        # order by validity then by knowledge, so a later-known revision of the
        # same valid_from supersedes the original
        d = d.sort_values([ft.valid_from_col, ft.known_from_col])
        return d.groupby(by, as_index=False, sort=False).tail(1)

    def history_as_of(self, name: str, as_of_date, by: list[str] | None = None) -> pd.DataFrame:
        """Full known history, de-duplicated to one row per (entity, valid_from),
        taking the latest-known version of each. Used for trend features."""
        ft = self.tables[name]
        d = self.as_of(name, as_of_date)
        if d.empty:
            return d
        keys = (by or [ft.entity_col]) + [ft.valid_from_col]
        d = d.sort_values([ft.known_from_col])
        return d.groupby(keys, as_index=False, sort=False).tail(1)

    # ---------------- diagnostics ----------------

    def audit(self) -> pd.DataFrame:
        rows = []
        for n, ft in self.tables.items():
            lag = ft.publication_lag_days
            rows.append(
                dict(
                    table=n,
                    rows=len(ft.df),
                    entities=ft.df[ft.entity_col].nunique(),
                    valid_from_min=ft.df[ft.valid_from_col].min(),
                    valid_from_max=ft.df[ft.valid_from_col].max(),
                    missing_known_from=ft.n_missing_known,
                    known_before_valid=ft.n_known_before_valid,
                    lag_p50=float(np.nanpercentile(lag.dropna(), 50)) if lag.notna().any() else np.nan,
                    lag_p90=float(np.nanpercentile(lag.dropna(), 90)) if lag.notna().any() else np.nan,
                    lag_max=float(lag.max()) if lag.notna().any() else np.nan,
                )
            )
        return pd.DataFrame(rows)


def assert_pit_safe(feature_frame: pd.DataFrame, as_of_col: str, known_cols: list[str]):
    """Guard: no feature may be built from a fact known after its as-of date."""
    for c in known_cols:
        if c not in feature_frame:
            continue
        viol = (feature_frame[c] > feature_frame[as_of_col]).sum()
        if viol:
            raise AssertionError(f"PIT violation: {viol} rows where {c} > {as_of_col}")
