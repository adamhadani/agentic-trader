"""The shipped config's Phase 0 keys reach ``AppConfig`` through ``load_config``.

Guards the 2026-10-13 regression class: ``load_config`` once silently ignored a whole YAML block
(``corporate_actions:``), so a shipped setting never took effect.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentic_trader.config import AppConfig, ExecutionConfig, LLMGateConfig, load_config
from agentic_trader.execution.auto_entry import auto_entry_active


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}
SHIPPED = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def test_the_shipped_calendar_regime_scan_and_scheduler_keys_reach_app_config():
    pinned = yaml.safe_load(SHIPPED.read_text())["risk"]["macro_calendar_sha256"]

    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert len(pinned) == 64 and config.risk.macro_calendar_sha256 == pinned
    assert config.risk.macro_calendar_unknown == "open"
    assert config.regime.unknown_risk_multiplier == 0.5
    assert config.regime.unknown_breakout_allowed is False
    assert len(config.scan.broad_index_etfs) == 23
    assert config.scheduler.intraday_scan_enabled is False
    # Several shipped values equal their code defaults; the fields set prove the YAML block was read.
    assert {"macro_calendar_sha256", "macro_calendar_unknown"} <= config.risk.model_fields_set
    assert {"unknown_risk_multiplier", "unknown_breakout_allowed"} <= config.regime.model_fields_set
    assert "broad_index_etfs" in config.scan.model_fields_set
    assert "intraday_scan_enabled" in config.scheduler.model_fields_set


def test_shipped_asset_classes():
    config = load_config(str(SHIPPED), environ=PRODUCTION)
    classes = config.universe.asset_classes()

    assert classes["XLK"] == "etf_equity" and classes["SPY"] == "etf_equity"
    assert classes["TLT"] == "rates_credit"
    assert classes["GLD"] == "metals"
    assert classes["AAPL"] == "single_stock"
    assert set(classes) == set(config.universe.symbols)
    for removed in ("BIL", "SHY"):
        assert removed not in classes and removed not in config.universe.symbols


def test_outcome_asset_classes_fall_back_to_research_etf_cohorts():
    config = load_config(str(SHIPPED), environ=PRODUCTION)
    classes = config.universe.outcome_asset_classes()

    assert classes["BIL"] == "rates_credit" and classes["SHY"] == "rates_credit"
    assert classes["TLT"] == "rates_credit" and classes["AAPL"] == "single_stock"
    assert classes["GLD"] == "metals" and classes["EFA"] == "etf_equity"
    assert "ZZZZ" not in classes


def test_shipped_config_bounds_native_entries():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.execution.native_entry_lifetime == "session"
    assert "native_entry_lifetime" in config.execution.model_fields_set


def test_native_entry_lifetime_defaults_off():
    assert ExecutionConfig().native_entry_lifetime == "off"


def test_invalid_native_entry_lifetime_refused():

    with pytest.raises(ValidationError):
        ExecutionConfig(native_entry_lifetime="day")


def test_the_shipped_insider_watch_block_reaches_app_config():
    config = load_config(str(SHIPPED), environ=PRODUCTION)
    watch = config.insider_watch

    assert watch.enabled is True  # the code default is False: the YAML block was read
    assert watch.max_cards_per_session == 2 and watch.max_open_positions == 4
    assert watch.max_risk_per_trade_usd == 100
    assert watch.archive_dir == Path("~/agentic-trader-research/insider-filings-cache").expanduser()
    assert watch.backfill_from.isoformat() == "2026-10-01"
    assert watch.pooled_cache_dir == Path("~/agentic-trader-research/pooled-cache-v1").expanduser()
    assert {"enabled", "archive_dir", "backfill_from", "pooled_cache_dir"} <= watch.model_fields_set


def test_the_shipped_llm_gate_block_reaches_app_config():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.llm_gate.timeout_seconds == 60
    # The shipped value equals the code default; the field being set proves the YAML block was read.
    assert "timeout_seconds" in config.llm_gate.model_fields_set


def test_llm_gate_defaults_and_bounds():
    assert LLMGateConfig().timeout_seconds == 60.0
    assert AppConfig().llm_gate == LLMGateConfig()
    assert LLMGateConfig(timeout_seconds=180).timeout_seconds == 180
    for bad in ({"timeout_seconds": 0}, {"timeout_seconds": 180.5}, {"timeout_seconds": float("inf")}, {"other": 1}):
        with pytest.raises(ValidationError):
            LLMGateConfig(**bad)


def test_shipped_config_enables_paper_auto_entry():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.execution.auto_entry == "paper"
    assert "auto_entry" in config.execution.model_fields_set


def test_shipped_auto_entry_is_inactive_under_the_simulator():
    config = load_config(str(SHIPPED), environ={**PRODUCTION, "EXECUTION_MODE": "paper"})

    assert config.execution.auto_entry == "paper"
    assert auto_entry_active(config) is False


def test_auto_entry_defaults_off():
    assert ExecutionConfig().auto_entry == "off"
    with pytest.raises(ValidationError):
        ExecutionConfig(auto_entry="live")


def _auto_entry_yaml(tmp_path, lifetime: str) -> str:
    path = tmp_path / "config.yaml"
    document = yaml.safe_load(SHIPPED.read_text())
    document["execution"]["auto_entry"] = "paper"
    document["execution"]["native_entry_lifetime"] = lifetime
    path.write_text(yaml.safe_dump(document))
    return str(path)


def test_load_config_refuses_auto_entry_on_live_alpaca(tmp_path):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "false"}

    with pytest.raises(ValueError, match="execution.auto_entry"):
        load_config(_auto_entry_yaml(tmp_path, "session"), environ=environ)


def test_load_config_refuses_auto_entry_without_session_lifetime(tmp_path):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "true"}

    with pytest.raises(ValueError, match="execution.auto_entry"):
        load_config(_auto_entry_yaml(tmp_path, "off"), environ=environ)


def test_shipped_config_lifetime_evidence_threshold():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.execution.lifetime_evidence_review_after_failures == 5
    assert ExecutionConfig().lifetime_evidence_review_after_failures == 5


@pytest.mark.parametrize("value", [0, 61])
def test_lifetime_evidence_threshold_bounds(value):
    with pytest.raises(ValueError, match="lifetime_evidence_review_after_failures"):
        ExecutionConfig(lifetime_evidence_review_after_failures=value)
