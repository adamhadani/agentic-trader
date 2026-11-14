"""The shipped config's Phase 0 keys reach ``AppConfig`` through ``load_config``.

Guards the 2026-10-13 regression class: ``load_config`` once silently ignored a whole YAML block
(``corporate_actions:``), so a shipped setting never took effect.
"""

import logging
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from agentic_trader.config import AppConfig, ExecutionConfig, LLMGateConfig, load_config
from agentic_trader.constants import DEFAULT_POSTGRES_DB_URL
from agentic_trader.data.edgar_filings import FilingCategory
from agentic_trader.execution.auto_entry import auto_entry_active, auto_entry_marker
from agentic_trader.execution.durable import ScanTrigger
from agentic_trader.execution.lifetime_policy import entry_lifetime_of
from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy
from agentic_trader.research.apriori.pead_base_rate import load_pead_base_rate
from agentic_trader.research.apriori.watch_contract import frozen_watch_contract
from agentic_trader.research.setups.card_prior import load_prior


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}
SHIPPED = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def test_the_shipped_calendar_regime_scan_and_scheduler_keys_reach_app_config():
    pinned = yaml.safe_load(SHIPPED.read_text())["risk"]["macro_calendar_sha256"]

    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert len(pinned) == 64 and config.risk.macro_calendar_sha256 == pinned
    assert config.risk.macro_calendar_unknown == "open"
    assert config.risk.earnings_calendar_unknown == "closed"
    assert config.regime.unknown_risk_multiplier == 0.5
    assert config.regime.unknown_breakout_allowed is False
    assert len(config.scan.broad_index_etfs) == 23
    assert config.scheduler.intraday_scan_enabled is False
    # Several shipped values equal their code defaults; the fields set prove the YAML block was read.
    assert {
        "macro_calendar_sha256",
        "macro_calendar_unknown",
        "earnings_calendar_unknown",
    } <= config.risk.model_fields_set
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


def _catalog_yaml(tmp_path, *, auto_entry: str, catalog) -> str:
    path = tmp_path / "config.yaml"
    document = yaml.safe_load(SHIPPED.read_text())
    document["execution"]["auto_entry"] = auto_entry
    document["execution"]["native_entry_lifetime"] = "session"
    document["execution"]["auto_entry_catalog"] = catalog
    path.write_text(yaml.safe_dump(document))
    return str(path)


def test_auto_entry_catalog_defaults_to_no_source_and_validates_its_ids():
    assert ExecutionConfig().auto_entry_catalog == ()
    assert ExecutionConfig(auto_entry_catalog=["pead", "insider_watch"]).auto_entry_catalog == ("pead", "insider_watch")
    with pytest.raises(ValidationError):
        ExecutionConfig(auto_entry_catalog=["exdiv"])


def test_load_config_accepts_a_catalog_list_on_alpaca_paper(tmp_path):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "true"}

    config = load_config(_catalog_yaml(tmp_path, auto_entry="paper", catalog=["pead"]), environ=environ)

    assert config.execution.auto_entry_catalog == ("pead",)


def test_load_config_treats_a_catalog_list_with_auto_entry_off_as_inert_and_warns(tmp_path, caplog):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "true"}

    with caplog.at_level(logging.WARNING, logger="copilot.config"):
        config = load_config(_catalog_yaml(tmp_path, auto_entry="off", catalog=["pead"]), environ=environ)

    assert config.execution.auto_entry == "off"
    assert config.execution.auto_entry_catalog == ("pead",)
    assert auto_entry_active(config) is False
    warnings = [record.getMessage() for record in caplog.records if "auto_entry_catalog_inert" in record.getMessage()]
    assert len(warnings) == 1 and "mode=off" in warnings[0]


def test_load_config_does_not_warn_when_the_catalog_list_is_active_or_empty(tmp_path, caplog):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "true"}

    with caplog.at_level(logging.WARNING, logger="copilot.config"):
        load_config(_catalog_yaml(tmp_path, auto_entry="paper", catalog=["pead"]), environ=environ)
        load_config(_catalog_yaml(tmp_path, auto_entry="off", catalog=[]), environ=environ)

    assert not [record for record in caplog.records if "auto_entry_catalog_inert" in record.getMessage()]


def test_load_config_refuses_a_catalog_list_on_live_alpaca(tmp_path):
    environ = {**PRODUCTION, "EXECUTION_MODE": "alpaca", "ALPACA_PAPER": "false"}

    with pytest.raises(ValueError, match="execution.auto_entry"):
        load_config(_catalog_yaml(tmp_path, auto_entry="paper", catalog=["pead", "insider_watch"]), environ=environ)


def test_shipped_config_lifetime_evidence_threshold():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.execution.lifetime_evidence_review_after_failures == 5
    assert ExecutionConfig().lifetime_evidence_review_after_failures == 5


@pytest.mark.parametrize("value", [0, 61])
def test_lifetime_evidence_threshold_bounds(value):
    with pytest.raises(ValueError, match="lifetime_evidence_review_after_failures"):
        ExecutionConfig(lifetime_evidence_review_after_failures=value)


def test_load_config_reads_lifetime_evidence_threshold(tmp_path):
    path = tmp_path / "config.yaml"
    document = yaml.safe_load(SHIPPED.read_text())
    document["execution"]["lifetime_evidence_review_after_failures"] = 3
    path.write_text(yaml.safe_dump(document))

    assert load_config(str(path), environ=PRODUCTION).execution.lifetime_evidence_review_after_failures == 3


def test_entry_trade_max_age_default_shipped_value_and_bounds():
    assert ExecutionConfig().entry_trade_max_age_seconds == 300
    assert load_config(str(SHIPPED), environ=PRODUCTION).execution.entry_trade_max_age_seconds == 300.0
    for value in (0, 901):
        with pytest.raises(ValueError, match="entry_trade_max_age_seconds"):
            ExecutionConfig(entry_trade_max_age_seconds=value)


def test_shipped_prior_matches_config():
    policy = load_config(str(SHIPPED), environ=PRODUCTION).card_policy

    prior = load_prior(policy.prior_path, policy.prior_sha256)

    assert prior.version == "card-stats-prior-v2"
    assert prior.is_replay("TREND_PULLBACK", "4h", "LONG")
    assert "prior_sha256" in policy.model_fields_set


def test_shipped_pead_base_rate_matches_config():
    apriori = load_config(str(SHIPPED), environ=PRODUCTION).apriori

    assert apriori.pead_base_rate_path.is_absolute()
    assert apriori.pead_base_rate_path.as_posix().endswith("config/research/pead-base-rate-v1.json")
    assert re.fullmatch(r"[0-9a-f]{64}", apriori.pead_base_rate_sha256)
    rate = load_pead_base_rate(apriori.pead_base_rate_path, apriori.pead_base_rate_sha256)
    assert rate.version == "pead-base-rate-v1"


def test_shipped_quiet_hours_reach_app_config():
    quiet = load_config(str(SHIPPED), environ=PRODUCTION).operations.quiet_hours

    assert quiet.enabled is True
    assert "enabled" in quiet.model_fields_set


def test_the_shipped_filing_veto_and_release_exit_blocks_reach_app_config():
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    # The code defaults are off; the fields set prove the YAML blocks were read.
    assert config.filing_veto.capture is True
    assert config.filing_veto.entry_block == "enforce"
    assert config.filing_veto.enforce_categories == (FilingCategory.LATE_FILING, FilingCategory.ADVERSE_8K)
    assert config.filing_veto.pre_scan_minutes == 30
    assert {"capture", "entry_block", "enforce_categories", "pre_scan_minutes"} <= config.filing_veto.model_fields_set
    assert config.release_exit.mode == "enforce"
    assert config.release_exit.minutes_before_close == 30
    assert {"mode", "minutes_before_close"} <= config.release_exit.model_fields_set


def test_neither_the_shipped_database_url_nor_the_production_default_names_a_user():
    """The repository is public: the database user comes from PGUSER or the OS account, or DATABASE_URL."""
    shipped = yaml.safe_load(SHIPPED.read_text())["database"]["url"]
    for url in (shipped, DEFAULT_POSTGRES_DB_URL):
        parsed = make_url(url)
        assert parsed.username is None and parsed.password is None, url
        assert (parsed.host, parsed.port, parsed.database) == ("localhost", 5432, "agentic_trader")
    loaded = load_config(str(SHIPPED), environ=PRODUCTION)
    assert loaded.database.url is not None and make_url(loaded.database.url).username is None


def test_the_shipped_config_scans_on_delayed_sip_while_quotes_stay_iex():
    """2026-11-07: IEX bars hid about two-thirds of the setups the SIP-measured evidence describes."""
    config = load_config(str(SHIPPED), environ={**PRODUCTION, "ALPACA_DATA_FEED": "iex"})

    assert config.market_data.scan_bars == "sip_delayed"
    assert config.market_data.sip_delay_minutes == 16
    # The shipped delay equals the code default; the field being set proves the YAML block was read.
    assert {"scan_bars", "sip_delay_minutes"} <= config.market_data.model_fields_set
    assert (config.market_data.bars_feed, config.alpaca_data_feed) == ("sip", "iex")


def test_the_shipped_config_auto_enters_the_catalog_sources_and_caps_non_native_positions():
    """Operator approval 2026-11-14: PEAD and insider-watch cards auto-enter on paper; O3 ships at 5 of 8 slots."""
    config = load_config(str(SHIPPED), environ=PRODUCTION)

    assert config.execution.auto_entry_catalog == ("pead", "insider_watch")
    assert config.portfolio.max_non_native_positions == 5
    assert config.portfolio.max_non_native_positions < config.portfolio.max_concurrent_positions
    # The shipped values differ from the code defaults ((), None); the fields set prove the YAML was read.
    assert "auto_entry_catalog" in config.execution.model_fields_set
    assert "max_non_native_positions" in config.portfolio.model_fields_set


def test_the_shipped_catalog_auto_entry_is_inactive_under_the_simulator():
    config = load_config(str(SHIPPED), environ={**PRODUCTION, "EXECUTION_MODE": "paper"})

    assert config.execution.auto_entry_catalog == ("pead", "insider_watch")
    assert auto_entry_active(config) is False


@pytest.mark.parametrize("execution_mode", ["alpaca", "paper"])
def test_the_shipped_config_with_auto_entry_off_loads_and_marks_no_catalog_card(tmp_path, execution_mode):
    """The runbook's off-switch: ``auto_entry: "off"`` alone, with the shipped catalog list left in place."""
    path = tmp_path / "config.yaml"
    document = yaml.safe_load(SHIPPED.read_text())
    document["execution"]["auto_entry"] = "off"
    path.write_text(yaml.safe_dump(document))
    environ = {**PRODUCTION, "EXECUTION_MODE": execution_mode, "ALPACA_PAPER": "true"}

    config = load_config(str(path), environ=environ)

    assert config.execution.auto_entry == "off"
    assert config.execution.auto_entry_catalog == ("pead", "insider_watch")
    assert auto_entry_active(config) is False
    for source in ("pead", "insider_watch"):
        assert (
            auto_entry_marker(
                config,
                trigger=ScanTrigger.SUGGESTION_SCAN,
                is_catalog=True,
                alpha=False,
                lifetime_marker=None,
                llm_verdict=None,
                catalog_source=source,
            )
            is None
        )


def test_the_shipped_scorecard_block_reaches_app_config():
    config = load_config(str(SHIPPED), environ=PRODUCTION)
    card = config.scorecard

    assert card.enabled is True
    assert (card.due_weekday, card.due_time_et, card.activity_days, card.record_days) == (5, "09:00", 7, 90)
    # The shipped block equals the code defaults; the fields set prove the YAML block was read.
    assert {"enabled", "due_weekday", "due_time_et", "activity_days", "record_days"} <= card.model_fields_set


def test_the_real_catalog_policies_each_resolve_an_entry_lifetime_as_stored_on_the_signal():
    """An enabled catalog card is auto-entered only if its stored `alpha_policy` resolves a lifetime."""
    config = load_config(str(SHIPPED), environ=PRODUCTION)
    pead = pead_execution_policy(load_pead_entry(Path(config.apriori.pead_entry_path)).entry)
    watch = frozen_watch_contract().policy

    for stored in (pead.to_dict(), watch):
        lifetime = entry_lifetime_of({"alpha_policy": stored})
        assert lifetime is not None
        # One regular session of resting entry (DAILY_ENTRY_LIFETIME_SECONDS) and the 20-session 15:45 ET exit.
        assert (lifetime.resting_seconds, lifetime.holding_sessions, lifetime.close_time_et) == (57_600, 20, "15:45")
