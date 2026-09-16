"""Versioned research coverage; current membership is not survivorship-free history."""

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ResearchUniverse:
    name: str
    as_of: str
    cohorts: tuple[tuple[str, tuple[str, ...]], ...]
    point_in_time_membership: bool = False

    @property
    def symbols(self):
        return tuple(sorted(symbol for _, symbols in self.cohorts for symbol in symbols))

    @property
    def version_id(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


ETF_RESEARCH_UNIVERSE = ResearchUniverse(
    "etf32",
    "2026-09-16",
    (
        ("broad_equity", ("SPY", "QQQ", "IWM", "DIA", "VTI")),
        ("sector", ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")),
        ("international", ("EFA", "EEM", "EWJ", "EWG", "EWU", "EWC")),
        ("rates_credit", ("SHY", "IEF", "TLT", "TIP", "LQD", "HYG", "BIL", "AGG")),
        ("metals", ("GLD", "SLV")),
    ),
)
