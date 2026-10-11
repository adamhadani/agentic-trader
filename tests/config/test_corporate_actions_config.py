import pytest
from pydantic import ValidationError

from agentic_trader.config import AppConfig, CorporateActionsConfig


def test_defaults_ship_enabled_in_preview():
    policy = AppConfig().corporate_actions
    assert policy.enabled is True and policy.entry_block == "preview"
    assert (policy.pre_days, policy.post_days, policy.notes_horizon_days) == (7, 5, 10)
    assert (policy.lookback_days, policy.lookahead_days) == (45, 120)
    assert (policy.max_symbols, policy.chunk_size, policy.max_actions) == (600, 100, 5000)
    assert (policy.poll_seconds, policy.max_age_seconds, policy.max_requests_per_minute) == (300, 345600, 60)
    assert policy.extra_symbols == []


def test_yaml_off_spelling():
    assert CorporateActionsConfig(entry_block=False).entry_block == "off"
    with pytest.raises(ValidationError):
        CorporateActionsConfig(entry_block=True)


@pytest.mark.parametrize("symbols", [["AAPL", "aapl"], ["BAD SYMBOL"], ["BTC/USD"], [""]])
def test_extra_symbols_must_be_distinct_upper_case_equity_symbols(symbols):
    with pytest.raises(ValidationError):
        CorporateActionsConfig(extra_symbols=symbols)


def test_dotted_symbols_and_ordering_rules():
    assert CorporateActionsConfig(extra_symbols=["BRK.B", "BF.B"]).extra_symbols == ["BRK.B", "BF.B"]
    with pytest.raises(ValidationError, match="max_age_seconds must exceed poll_seconds"):
        CorporateActionsConfig(poll_seconds=600, max_age_seconds=600)
    with pytest.raises(ValidationError):
        CorporateActionsConfig(unknown=1)
