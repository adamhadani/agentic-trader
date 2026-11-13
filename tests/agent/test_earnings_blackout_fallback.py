"""The earnings blackout at the scan and at the tap: live, then the dated capture, then the unknown policy."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.earnings import EarningsTiming
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.regime import RegimeDetector, RegimeSnapshot
from agentic_trader.constants import VolatilityRegime
from agentic_trader.data.earnings_capture import CapturedReports, EarningsCaptureStore
from agentic_trader.risk import RiskRule
from tests.agent.test_evaluator import FakeEarningsCalendar, create_candidate, create_equity_candidate
from tests.calendar_doubles import clear_check, union_double


UNAVAILABLE = "earnings calendar unavailable"


class FakeCaptures:
    def __init__(self, report_in_days: int | None = None):
        self.report_in_days = report_in_days

    async def reports_between(self, start: date, end: date, *, today: date) -> CapturedReports | None:
        day = today + timedelta(days=self.report_in_days) if self.report_in_days is not None else None
        rows = {start: ()}
        if day is not None:
            rows[day] = (("AAPL", EarningsTiming.AFTER_HOURS),)
        return CapturedReports(capture_date=today, rows=rows)


def _now():
    return datetime.now(UTC)


def _config(config, policy):
    return config.model_copy(update={"risk": config.risk.model_copy(update={"earnings_calendar_unknown": policy})})


@pytest.fixture
def desk(app_config, temp_db):
    app_config.copilot_chat_enabled = False
    return TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=MagicMock(),
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )


async def _scan(config, policy, captures):
    cfg = _config(config, policy)
    detector = RegimeDetector(cfg.regime)
    detector.get_regime = AsyncMock(
        return_value=RegimeSnapshot(
            vix=18.0,
            vix_regime=VolatilityRegime.NORMAL,
            tnx=4.2,
            dxy=103.0,
            breakout_allowed=True,
            min_rr_threshold=2.0,
            timestamp=datetime.now(UTC),
            summary_text="Test context",
            risk_multiplier=1.0,
        )
    )
    evaluator = RiskEvaluator(
        cfg,
        regime_detector=detector,
        calendar=union_double(clear_check()),
        earnings_calendar=FakeEarningsCalendar(exc=RuntimeError("403")),
        earnings_captures=captures,
    )
    return await evaluator.evaluate_candidate(create_equity_candidate(), use_llm=False)


async def test_scan_refuses_when_neither_source_vouches_under_closed(config):
    res = await _scan(config, "closed", None)
    assert res.approved is False and res.rejection_rule == RiskRule.EARNINGS_BLACKOUT
    assert UNAVAILABLE in res.rejection_reason


async def test_scan_stays_open_by_policy_with_unverified_note(config):
    res = await _scan(config, "open", None)
    assert res.approved is True
    assert res.earnings_note == "Unverified — earnings calendar unavailable"


async def test_scan_uses_the_capture_when_live_fails(config):
    res = await _scan(config, "closed", FakeCaptures(report_in_days=2))
    assert res.approved is False and res.rejection_rule == RiskRule.EARNINGS_BLACKOUT
    assert "Earnings Blackout" in res.rejection_reason

    clear = await _scan(config, "closed", FakeCaptures())
    assert clear.approved is True and "calendar capture" in clear.earnings_note


async def test_tap_matches_the_scan(desk, config):
    now = _now()
    for policy, captures in (("closed", None), ("closed", FakeCaptures(2)), ("open", None)):
        scan = await _scan(config, policy, captures)
        desk.config = _config(desk.config, policy)
        desk.earnings_calendar = FakeEarningsCalendar(exc=RuntimeError("403"))
        desk.earnings_captures = captures
        tap = await desk._earnings_blackout_reason("AAPL", now)
        assert tap == (None if scan.approved else scan.rejection_reason)


async def test_tap_refusal_names_the_unavailable_calendar(desk):
    desk.config = _config(desk.config, "closed")
    desk.earnings_calendar = FakeEarningsCalendar(exc=RuntimeError("403"))
    desk.earnings_captures = None
    reason = await desk._earnings_blackout_reason("AAPL", _now())
    assert reason is not None and reason.startswith("Earnings Blackout: ") and UNAVAILABLE in reason


def test_one_capture_store_feeds_the_evaluator_and_the_release_exit(desk, temp_db):
    store = desk.earnings_captures
    assert isinstance(store, EarningsCaptureStore) and store.workflows is temp_db.workflows
    assert desk.release_exit.captures is store
    assert isinstance(desk.evaluator, RiskEvaluator) and desk.evaluator.earnings_captures is store


async def _scan_with(config, *, days=7, calendar="raise", candidate=None):
    cfg = _config(config, "closed")
    cfg = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"earnings_blackout_days": days})})
    detector = RegimeDetector(cfg.regime)
    detector.get_regime = AsyncMock(
        return_value=RegimeSnapshot(
            vix=18.0,
            vix_regime=VolatilityRegime.NORMAL,
            tnx=4.2,
            dxy=103.0,
            breakout_allowed=True,
            min_rr_threshold=2.0,
            timestamp=datetime.now(UTC),
            summary_text="Test context",
            risk_multiplier=1.0,
        )
    )
    fake = FakeEarningsCalendar(exc=RuntimeError("403")) if calendar == "raise" else None
    evaluator = RiskEvaluator(
        cfg, regime_detector=detector, calendar=union_double(clear_check()), earnings_calendar=fake
    )
    return await evaluator.evaluate_candidate(candidate or create_equity_candidate(), use_llm=False), fake


async def test_closed_policy_is_inert_when_the_blackout_is_zero_days(config, desk):
    res, fake = await _scan_with(config, days=0)
    assert res.approved is True and not fake.calls and res.earnings_note is None
    desk.config = _config(desk.config, "closed")
    desk.config = desk.config.model_copy(
        update={"risk": desk.config.risk.model_copy(update={"earnings_blackout_days": 0})}
    )
    desk.earnings_calendar = FakeEarningsCalendar(exc=RuntimeError("403"))
    assert await desk._earnings_blackout_reason("AAPL", _now()) is None


async def test_closed_policy_is_inert_without_a_live_calendar(config, desk):
    res, _ = await _scan_with(config, calendar=None)
    assert res.approved is True and res.earnings_note is None
    desk.config = _config(desk.config, "closed")
    desk.earnings_calendar = None
    assert await desk._earnings_blackout_reason("AAPL", _now()) is None


async def test_closed_policy_never_evaluates_a_non_equity(config):
    res, fake = await _scan_with(config, candidate=create_candidate())
    assert not fake.calls
    assert res.rejection_rule != RiskRule.EARNINGS_BLACKOUT
