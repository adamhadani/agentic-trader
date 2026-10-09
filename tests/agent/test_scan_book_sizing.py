"""Book-aware sizing at the L3 seam of ``run_scan``: decision, provenance, notification, journal."""

import math
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.agent.position_sizing import SizingTier
from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass
from agentic_trader.execution.book_sizing import BookSizer
from agentic_trader.execution.durable import EventKind, RankedOutcome, WorkKind
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture used by shadow_desk)
from tests.agent.test_scan_shadow_ranker import daily_frame, shadow_desk  # noqa: F401  (fixture)


def correlated_book_frame(source: pd.DataFrame, seed: int = 99) -> pd.DataFrame:
    """A frame whose returns are the source's plus a little noise: strongly correlated, same dates."""
    rets = np.diff(np.log(source["Close"].to_numpy()), prepend=np.log(source["Close"].iloc[0]))
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rets + rng.normal(0.0, 0.002, len(rets))))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Volume": 1e6}, index=source.index)


def sized_evaluation(cand, quantity: float = 100.0) -> LLMTradeEvaluation:
    """A deliverable equity evaluation with three tiers; entry 100, stop 98, so $2 risk per share."""

    def tier(tier_id: str, qty: float, default: bool = False) -> dict:
        return SizingTier(
            tier_id=tier_id,
            label=tier_id,
            quantity=qty,
            risk_dollars=2.0 * qty,
            reward_dollars=4.0 * qty,
            notional_dollars=100.0 * qty,
            effective_leverage=0.1,
            is_default=default,
        ).model_dump()

    return LLMTradeEvaluation(
        approved=True,
        contract=cand.contract,
        direction=cand.direction,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        stop_distance_points=2.0,
        target_distance_points=4.0,
        risk_reward_ratio=2.0,
        risk_dollars=2.0 * quantity,
        reward_dollars=4.0 * quantity,
        notional_value=100.0 * quantity,
        effective_leverage=0.1,
        macro_clearance=True,
        thesis_summary="fixture thesis",
        quantity=quantity,
        asset_class=AssetClass.EQUITY,
        sizing_tiers=[tier("half", quantity / 2), tier("base", quantity, True), tier("max", quantity * 1.5)],
    )


async def _plant_book_position(db, symbol="ZZZ", notional=20_000.0):
    async with db.session_factory() as session, session.begin():
        session.add(
            SignalRecord(
                contract=symbol,
                strategy="TREND_PULLBACK",
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=400.0,
                notional_value=notional,
                asset_class="EQUITY",
                quantity=notional / 100.0,
                status="EXECUTED",
                environment=db.environment,
                execution_mode=db.execution_mode,
            )
        )


@pytest.fixture
def book_desk(shadow_desk, temp_db, app_config):  # noqa: F811
    app_config.portfolio.correlation_groups = {}
    app_config.scan.max_cards_per_scan = 2
    app_config.scan.max_cards_per_session = 3  # the planted EXECUTED row spends one session card
    app_config.book_sizing.mode = "preview"
    app_config.book_sizing.max_portfolio_daily_vol_pct = 0.0022
    app_config.book_sizing.shadow_optimizer = False
    shadow_desk.quantities = {}
    shadow_desk.monitor_positions = AsyncMock()  # the planted EXECUTED row has no broker behind it
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kwargs: sized_evaluation(cand, shadow_desk.quantities.get(cand.contract, 100.0))
    )
    # The book holds ZZZ (outside the scan), correlated with the rank-1 candidate DDD.
    source = daily_frame(3)
    shadow_desk.bar_source = MagicMock(fetch_daily_many=MagicMock(return_value={"ZZZ": correlated_book_frame(source)}))

    def install_sizer() -> None:
        shadow_desk.book_sizer = BookSizer(
            app_config.book_sizing,
            shadow_desk.bar_source,
            portfolio_cash=app_config.portfolio.cash,
            min_units=1.0,
        )

    shadow_desk.install_sizer = install_sizer
    install_sizer()
    return shadow_desk


async def _scan(desk, **kwargs):
    await desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, **kwargs)


async def _signals(db):
    rows = await db.get_recent_signals(limit=10)
    return {row["contract"]: row for row in rows}


async def _ranked_candidates(db):
    event = [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED][-1]
    return {c["contract"]: c for c in event["payload"]["candidates"]}


async def _clear_cards(db):
    """Forget the scan's recorded cards (keep the planted book position)."""
    async with db.session_factory() as session, session.begin():
        await session.execute(delete(SignalRecord).where(SignalRecord.contract != "ZZZ"))


async def _setup(desk, db):
    await _plant_book_position(db)
    desk.install_sizer()


async def test_preview_leaves_eval_res_identical(book_desk, temp_db, app_config):
    await _setup(book_desk, temp_db)
    app_config.book_sizing.mode = "off"
    book_desk.install_sizer()
    await _scan(book_desk)
    off = (await _signals(temp_db))["DDD"]
    await _clear_cards(temp_db)
    app_config.book_sizing.mode = "preview"
    book_desk.install_sizer()
    await _scan(book_desk)
    preview = (await _signals(temp_db))["DDD"]

    assert preview["quantity"] == off["quantity"] == 100.0
    assert preview["raw_response"] == off["raw_response"]
    block = preview["decision_provenance"]["book_sizing"]
    assert block["status"] == "unchanged" and block["would_scale"] is True and block["factor"] < 1.0


async def test_enforce_scales_the_card_and_journals_applied(book_desk, temp_db, app_config):
    app_config.book_sizing.mode = "enforce"
    await _setup(book_desk, temp_db)
    await _scan(book_desk)

    signal = (await _signals(temp_db))["DDD"]
    block = signal["decision_provenance"]["book_sizing"]
    assert block["status"] == "applied" and block["factor"] < 1.0
    assert signal["quantity"] == float(math.floor(100.0 * block["factor"]))
    assert block["quantity_before"] == 100.0 and block["quantity_after"] == signal["quantity"]

    [item] = [
        i
        for i in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if i.payload["arguments"]["eval_res"]["contract"] == "DDD"
    ]
    assert item.payload["arguments"]["book_sizing"]["status"] == "applied"
    assert item.payload["arguments"]["eval_res"]["quantity"] == signal["quantity"]

    journal = (await _ranked_candidates(temp_db))["DDD"]["book_sizing"]
    assert journal["status"] == "applied" and journal["quantity_after"] < journal["quantity_before"]


async def test_enforce_blocks_and_falls_through(book_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 1
    app_config.scan.max_cards_per_session = 2
    await _setup(book_desk, temp_db)
    await _scan(book_desk)  # preview: read the book's own vol to set a budget just above it
    base = (await _signals(temp_db))["DDD"]["decision_provenance"]["book_sizing"]["vol_before_pct"]
    await _clear_cards(temp_db)
    app_config.book_sizing.mode = "enforce"
    app_config.book_sizing.max_portfolio_daily_vol_pct = base * 1.001
    book_desk.install_sizer()
    book_desk.quantities = {"DDD": 2.0}  # a correlated add of this size floors below one share
    book_desk.evaluator.evaluate_candidate.reset_mock()

    await _scan(book_desk)

    signals = await _signals(temp_db)
    assert "DDD" not in signals and "CCC" in signals
    candidates = await _ranked_candidates(temp_db)
    assert candidates["DDD"]["outcome"] == RankedOutcome.BOOK_SIZING_BLOCKED
    assert candidates["DDD"]["book_sizing"]["status"] == "blocked"
    assert candidates["CCC"]["outcome"] == "sent"
    # the per-scan and per-session card budgets were charged once, by CCC only
    assert sum(1 for o in candidates.values() if o["outcome"] == "sent") == 1
    assert any(r["contract"] == "DDD" for r in book_desk.last_scan_summary["runners_up"])


async def test_later_card_sees_scaled_book(book_desk, temp_db, app_config):
    app_config.book_sizing.mode = "enforce"
    await _setup(book_desk, temp_db)
    await _scan(book_desk)

    candidates = await _ranked_candidates(temp_db)
    first, second = candidates["DDD"]["book_sizing"], candidates["CCC"]["book_sizing"]
    assert first["status"] == "applied"
    signals = await _signals(temp_db)
    first_after = signals["DDD"]["decision_provenance"]["book_sizing"]["vol_after_scaled_pct"]
    # The recorded card adds its whole-unit notional (not exactly factor x notional): one share of
    # rounding moves the book vol by ~1e-6 of capital, far below this bound and far above the ~2e-4
    # gap a no-op book update would leave.
    assert second["vol_before_pct"] == pytest.approx(first_after, abs=2e-5)
    assert second["vol_before_pct"] > first["vol_before_pct"]


async def test_unavailable_never_blocks(book_desk, temp_db, app_config):
    app_config.book_sizing.mode = "enforce"
    book_desk.bar_source.fetch_daily_many.side_effect = RuntimeError("provider down")
    await _setup(book_desk, temp_db)
    await _scan(book_desk)

    signals = await _signals(temp_db)
    assert {"DDD", "CCC"} <= set(signals)
    for contract in ("DDD", "CCC"):
        block = signals[contract]["decision_provenance"]["book_sizing"]
        assert block["status"] == "unavailable" and block["reason"] == "RuntimeError"
        assert signals[contract]["quantity"] == 100.0


async def test_dry_run_is_not_applicable(book_desk, temp_db, app_config, capsys):
    app_config.book_sizing.mode = "enforce"
    await _setup(book_desk, temp_db)
    book_desk.book_sizer.prepare = AsyncMock()
    await book_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL)

    book_desk.book_sizer.prepare.assert_not_awaited()
    book_desk.bar_source.fetch_daily_many.assert_not_called()
    out = capsys.readouterr().out
    assert "Book:" not in out and "📋 SETUP:" in out


async def test_off_mode_adds_no_keys_but_null_block(book_desk, temp_db, app_config):
    app_config.book_sizing.mode = "off"
    await _setup(book_desk, temp_db)
    await _scan(book_desk)

    signal = (await _signals(temp_db))["DDD"]
    assert signal["quantity"] == 100.0
    assert signal["decision_provenance"]["book_sizing"]["status"] == "not_applicable"
    book_desk.bar_source.fetch_daily_many.assert_not_called()
    journal = (await _ranked_candidates(temp_db))["DDD"]["book_sizing"]
    assert journal["mode"] == "off" and journal["status"] == "not_applicable"
