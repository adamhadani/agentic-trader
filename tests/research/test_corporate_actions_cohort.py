import json

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.research.corporate_actions import build_corporate_actions_source, cohort_symbols


def test_cohort_symbols_reads_sectors_or_symbols(tmp_path):
    sectors = tmp_path / "sectors.json"
    sectors.write_text(json.dumps({"sectors": {"energy": ["XOM", "CVX"], "tech": ["MSFT"]}}))
    assert cohort_symbols(sectors) == ["XOM", "CVX", "MSFT"]
    symbols = tmp_path / "symbols.json"
    symbols.write_text(json.dumps({"symbols": ["AAPL"]}))
    assert cohort_symbols(symbols) == ["AAPL"]


def test_cohort_symbols_rejects_an_unknown_shape(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"names": ["AAPL"]}))
    with pytest.raises(ValueError, match="sectors"):
        cohort_symbols(other)


def test_build_source_requires_credentials():
    with pytest.raises(ValueError, match="credentials"):
        build_corporate_actions_source(AppConfig())
