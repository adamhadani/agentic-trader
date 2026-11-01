"""The ``composite-ic`` score: the frozen members put through the fixed construction, nothing fitted (research only).

Per session, member by member, over eligible names with a known sector: multiply by the sign, z-score across
names and clip; z-score within sector; regress out ``beta_60`` and ``log_adv`` with an intercept; z-score the
residual across names. Then per name: require the minimum share of finite members, average the available
members within each theme and average across the themes that have one.

Member panels are read at ``arrays.offset`` from the book, so the score for ``D`` reads completed bars at ``D-1``.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.panel import cross_sectional_regress_out, cross_sectional_zscore, group_zscore
from agentic_trader.research.ic.arrays import ICArrays
from agentic_trader.research.ic.protocol import CompositeProtocol, Member, Theme


__all__ = [
    "LOW_RISK_THEME",
    "combine_members",
    "composite_panel",
    "ex_low_risk_panel",
    "member_panel",
    "member_panels",
    "theme_panel",
]

LOW_RISK_THEME = "low_risk"


def _frame(values: np.ndarray, symbols: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(np.where(np.isfinite(values), values, np.nan), columns=list(symbols))


def member_panel(member: Member, protocol: CompositeProtocol, arrays: ICArrays) -> np.ndarray:
    """One member after the four per-session steps, ``[S, N]``; NaN where the construction gives no value."""
    construction = protocol.construction
    limit = construction.winsorize_z
    stop = arrays.offset + len(arrays.sessions)
    raw = arrays.book.panel(member.expression)[arrays.offset : stop]
    signed = _frame(np.where(arrays.eligible, member.sign * raw, np.nan), arrays.symbols)
    clipped = cross_sectional_zscore(signed).clip(-limit, limit)
    groups = pd.Series(arrays.groups, index=clipped.columns)
    within_sector = group_zscore(clipped, groups, min_names=construction.min_sector_names)
    residual = cross_sectional_regress_out(
        within_sector,
        [_frame(arrays.beta_60, arrays.symbols), _frame(arrays.log_adv, arrays.symbols)],
        min_rows=construction.min_regression_rows,
    )
    return cross_sectional_zscore(residual).to_numpy(dtype=float)


def _mean_of_finite(block: np.ndarray) -> np.ndarray:
    """The mean along the first axis over finite entries; NaN where none is finite."""
    finite = np.isfinite(block)
    count = finite.sum(axis=0)
    total = np.where(finite, block, 0.0).sum(axis=0)
    return np.divide(total, count, out=np.full(total.shape, np.nan), where=count > 0)


def combine_members(protocol: CompositeProtocol, panels: Mapping[str, np.ndarray]) -> np.ndarray:
    """Mean of the available members within each theme, then across themes; a theme of four weighs as one of one."""
    members = np.stack([panels[member.id] for member in protocol.members])
    share = np.isfinite(members).sum(axis=0) / len(protocol.members)
    themes = np.stack(
        [_mean_of_finite(np.stack([panels[member.id] for member in theme.members])) for theme in protocol.themes]
    )
    return np.where(share >= protocol.construction.min_member_fraction, _mean_of_finite(themes), np.nan)


def member_panels(protocol: CompositeProtocol, arrays: ICArrays) -> dict[str, np.ndarray]:
    return {member.id: member_panel(member, protocol, arrays) for member in protocol.members}


def composite_panel(protocol: CompositeProtocol, arrays: ICArrays) -> np.ndarray:
    """``[S, N]`` composite scores."""
    return combine_members(protocol, member_panels(protocol, arrays))


def _combine_themes(
    protocol: CompositeProtocol, themes: tuple[Theme, ...], panels: Mapping[str, np.ndarray]
) -> np.ndarray:
    """``combine_members`` over these themes alone, so the minimum share counts only their members."""
    return combine_members(protocol.model_copy(update={"themes": themes}), panels)


def theme_panel(protocol: CompositeProtocol, theme: Theme, panels: Mapping[str, np.ndarray]) -> np.ndarray:
    return _combine_themes(protocol, (theme,), panels)


def ex_low_risk_panel(protocol: CompositeProtocol, panels: Mapping[str, np.ndarray]) -> np.ndarray:
    """The composite recombined without the low-risk theme; its member's panel is never read."""
    kept = tuple(theme for theme in protocol.themes if theme.theme != LOW_RISK_THEME)
    if len(kept) == len(protocol.themes):
        raise ValueError(f"the composite has no {LOW_RISK_THEME} theme to leave out")
    return _combine_themes(protocol, kept, panels)
