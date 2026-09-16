"""Explicit timestamp × symbol operators; never overload single-series rank."""

import numpy as np
import pandas as pd


def validate_panel(panel: pd.DataFrame):
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing or not panel.columns.is_unique:
        raise ValueError("Panel must have unique sorted timestamps and unique symbols")
    if np.isinf(panel.to_numpy(dtype=float)).any():
        raise ValueError("Infinite panel observations")


def cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    validate_panel(panel)
    return panel.rank(axis=1, pct=True, na_option="keep")


def cross_sectional_zscore(panel: pd.DataFrame) -> pd.DataFrame:
    validate_panel(panel)
    return panel.sub(panel.mean(axis=1), axis=0).div(panel.std(axis=1).replace(0, np.nan), axis=0)


def group_neutralize(panel: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    validate_panel(panel)
    if not groups.index.equals(panel.columns) or groups.isna().any():
        raise ValueError("Group membership must align to every symbol")
    result = panel.astype(float).copy()
    for group in sorted(groups.unique()):
        columns = groups.index[groups == group]
        result[columns] = panel[columns].sub(panel[columns].mean(axis=1), axis=0)
    return result
