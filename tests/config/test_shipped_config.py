"""The shipped config's Phase 0 keys reach ``AppConfig`` through ``load_config``.

Guards the 2026-10-13 regression class: ``load_config`` once silently ignored a whole YAML block
(``corporate_actions:``), so a shipped setting never took effect.
"""

from pathlib import Path

import yaml

from agentic_trader.config import load_config


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
