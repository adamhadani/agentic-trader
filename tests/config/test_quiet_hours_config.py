from datetime import time

import pytest
from pydantic import ValidationError

from agentic_trader.config import READINESS_CHECK_NAMES, OperationsConfig, QuietHoursConfig
from agentic_trader.diagnostics.readiness import HealthComponent, ReadinessService
from agentic_trader.telemetry.collector import MetricsCollector


def test_window_must_start_before_it_ends():
    with pytest.raises(ValidationError):
        QuietHoursConfig(alert_start_et="16:30", alert_end_et="09:15")
    with pytest.raises(ValidationError):
        QuietHoursConfig(alert_start_et="09:15", alert_end_et="09:15")


def test_unknown_exempt_component_fails_load():
    with pytest.raises(ValidationError, match="entry_recovry"):
        QuietHoursConfig(exempt_components=["entry_recovry"])


def test_yaml_strings_parse():
    ops = OperationsConfig(
        quiet_hours={"enabled": True, "alert_start_et": "09:00", "exempt_components": ["endpoint", "trading_halt"]}
    )
    assert ops.quiet_hours.alert_start_et == time(9, 0)


async def test_check_names_cover_readiness(temp_db, app_config):
    assert {c.value for c in HealthComponent} <= READINESS_CHECK_NAMES

    async def registry():
        return {"ready": True}

    config = app_config.model_copy(deep=True)
    config.telegram_bot_token = "token"
    config.telegram_chat_id = "chat"
    for section in (
        config.alpha_pipeline.observations,
        config.alpha_pipeline.decisions,
        config.alpha_pipeline.daily_panel,
    ):
        section.enabled = True
    service = ReadinessService(
        temp_db.workflows,
        config,
        MetricsCollector(),
        accounting_enabled=True,
        card_stats_enabled=True,
        corporate_actions_enabled=True,
        insider_capture_enabled=True,
        alpha_registry_report=registry,
        stream_connected=lambda: True,
        macro_calendar=lambda now: {"ready": True},
    )
    assert set((await service.report())["checks"]) | {"endpoint", "storage"} == READINESS_CHECK_NAMES
