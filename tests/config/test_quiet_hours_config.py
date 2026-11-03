from datetime import time

import pytest
import yaml
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


BROKER_WORK = ["entry_recovery", "entry_cancellation", "close_recovery"]


def test_yaml_strings_parse():
    ops = OperationsConfig(
        quiet_hours={"enabled": True, "alert_start_et": "09:00", "exempt_components": [*BROKER_WORK, "trading_halt"]}
    )
    assert ops.quiet_hours.alert_start_et == time(9, 0)


def test_unquoted_yaml_times_fail_load():
    """YAML 1.1 reads unquoted 10:00 as the integer 600, which would otherwise become 00:10 UTC."""
    unquoted = yaml.safe_load("alert_start_et: 10:00\nalert_end_et: 16:00\n")
    assert unquoted == {"alert_start_et": 600, "alert_end_et": 960}
    with pytest.raises(ValidationError, match='quote the time, e.g. "09:15"'):
        QuietHoursConfig.model_validate(unquoted)
    with pytest.raises(ValidationError, match='quote the time, e.g. "09:15"'):
        QuietHoursConfig.model_validate(yaml.safe_load('alert_start_et: "09:15"\nalert_end_et: 16:00\n'))
    for value in (555.0, True, None, [9, 15]):
        with pytest.raises(ValidationError, match='quote the time, e.g. "09:15"'):
            QuietHoursConfig(alert_start_et=value)


def test_times_with_an_offset_fail_load():
    with pytest.raises(ValidationError, match='quote the time, e.g. "09:15"'):
        QuietHoursConfig(alert_start_et="09:15+00:00")
    with pytest.raises(ValidationError, match='quote the time, e.g. "09:15"'):
        QuietHoursConfig(alert_end_et="16:30-05:00")


def test_default_exempt_components_include_the_checks_that_hide_broker_work():
    assert QuietHoursConfig().exempt_components == [*BROKER_WORK, "endpoint", "storage"]


@pytest.mark.parametrize("missing", BROKER_WORK)
def test_exempt_components_must_keep_every_broker_work_check(missing):
    exempt = [name for name in BROKER_WORK if name != missing] + ["endpoint", "storage"]
    with pytest.raises(ValidationError, match=f"broker-work checks always page.*{missing}"):
        QuietHoursConfig(exempt_components=exempt)


def test_endpoint_and_storage_may_be_removed_deliberately():
    assert QuietHoursConfig(exempt_components=BROKER_WORK).exempt_components == BROKER_WORK


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
