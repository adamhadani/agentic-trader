"""One lockout rule at every site (spec §5.1 "The rule stays in `risk/`", §7.1 "Expired plus `closed`").

The whole-scan check, the evaluator gate, the tap gate and the preflight read one `CalendarCheck` and refuse
with the one text `risk.macro_lockout` formats.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass, CalendarStatus, StrategyType
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.risk import RiskRule
from tests.agent.test_risk_policy_integration import desk, offer, setup  # noqa: F401  (fixture)
from tests.calendar_doubles import lockout_check, union_double, unverified_check


NOW = datetime(2026, 10, 21, 18, 35, tzinfo=UTC)
CLOSED = {
    CalendarStatus.EXPIRED: (
        "Macro calendar unverified: the static tier-1 calendar is expired; new entries refused "
        "(risk.macro_calendar_unknown: closed)."
    ),
    CalendarStatus.INVALID: (
        "Macro calendar unverified: the static tier-1 calendar is invalid; new entries refused "
        "(risk.macro_calendar_unknown: closed)."
    ),
}
REQUEST = OrderRequest(
    symbol="SPY",
    asset_class=AssetClass.EQUITY,
    direction="LONG",
    quantity=10.0,
    entry_price=100.0,
    stop_loss=95.0,
    take_profit=120.0,
)
SIGNAL = {"strategy": StrategyType.TREND_PULLBACK}


def install(desk, check) -> None:  # noqa: F811
    """One calendar for the desk and the evaluator it built, as the constructor wires them."""
    desk.calendar = desk.evaluator.calendar = union_double(check)


async def site_texts(desk, calm_regime) -> dict[str, str | None]:  # noqa: F811
    desk.readiness = AsyncMock()
    offer(desk, setup("SPY"))
    scan = await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)
    [(ready, detail)] = [
        call.args[1:] for call in desk.readiness.observe.await_args_list if call.args[0] == HealthComponent.SCAN
    ]
    assert scan is None and ready is True
    evaluation = await desk.evaluator.evaluate_candidate(setup("SPY"), use_llm=False)
    assert evaluation.rejection_rule == RiskRule.MACRO_LOCKOUT == "macro_lockout"
    assert evaluation.macro_clearance is False
    return {
        "scan": detail,
        "evaluator": evaluation.rejection_reason,
        "tap": await desk._macro_gate(REQUEST, SIGNAL, calm_regime, now=NOW),
        "preflight": await desk._entry_macro_check(REQUEST, SIGNAL),
    }


async def test_closed_policy_refuses_with_one_text_at_all_four_sites(desk, app_config, calm_regime):  # noqa: F811
    app_config.risk.macro_calendar_unknown = "closed"
    for status, text in CLOSED.items():
        install(desk, unverified_check(status))
        assert await site_texts(desk, calm_regime) == {
            "scan": f"Macro gate checked: {text}; entry alerts paused",
            "evaluator": text,
            "tap": text,
            "preflight": text,
        }
        evaluation = await desk.evaluator.evaluate_candidate(setup("SPY"), use_llm=False)
        assert evaluation.thesis_summary == "Rejected: macro calendar unverified (policy closed)."


async def test_a_verified_lockout_refuses_with_one_text_at_all_four_sites(desk, app_config, calm_regime):  # noqa: F811
    app_config.risk.macro_calendar_unknown = "closed"  # a verified calendar never reaches the unverified refusal
    install(desk, lockout_check("CPI", datetime(2026, 10, 21, 12, 30, tzinfo=UTC)))
    text = "Macro event lockout: CPI at 12:30 UTC."
    assert await site_texts(desk, calm_regime) == {
        "scan": f"Macro gate checked: {text}; entry alerts paused",
        "evaluator": text,
        "tap": text,
        "preflight": text,
    }


async def test_open_policy_never_refuses_an_unverified_calendar(desk, app_config, calm_regime):  # noqa: F811
    assert app_config.risk.macro_calendar_unknown == "open"
    install(desk, unverified_check())
    assert await desk._macro_lockout_reason(now=NOW) is None
    assert await desk._entry_macro_check(REQUEST, SIGNAL) is None
    evaluation = await desk.evaluator.evaluate_candidate(setup("SPY"), use_llm=False)
    assert evaluation.approved is True
    assert evaluation.macro_note == "static tier-1 calendar expired; live feed ok"


async def test_every_site_asks_the_calendar_at_its_own_instant(desk, calm_regime):  # noqa: F811
    install(desk, unverified_check())
    await desk._macro_gate(REQUEST, SIGNAL, calm_regime, now=NOW)
    await desk._entry_macro_check(REQUEST, SIGNAL)
    tap, preflight = desk.calendar.check.await_args_list
    assert tap.args == (NOW, 60, 30)
    assert preflight.args[0].tzinfo is UTC and preflight.args[1:] == (60, 30)
