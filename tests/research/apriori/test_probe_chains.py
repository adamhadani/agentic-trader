"""End-to-end chains of the PEAD catalog probe, with real components and no network.

1. Card: real enrolment -> registry snapshot -> ``EarningsDriftService.live_version`` ->
   ``candidate`` -> the real ``RiskEvaluator`` levels -> ``record_signal`` -> catalog
   admission (``_alpha_entry_rejection``) accepts it; its bracket is the study's
   ``levels_for`` rounded outward and its risk stays under the probe cap.
2. Measurement: ``event_document`` -> the journal's JSON -> ``decision_frame`` -> the
   study's ``label_events`` -> ``summarize_probe``, exactly as ``cards outcomes`` runs it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.config import AppConfig
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.strategy import entry_limit, execution_policy_from_dict
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import EVENT_COLUMNS
from agentic_trader.research.apriori.pead_live import event_document
from agentic_trader.research.apriori.pead_study import label_events, levels_for
from agentic_trader.research.apriori.probe import load_catalog_probe
from agentic_trader.research.apriori.probe_outcomes import decision_frame, summarize_probe
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.earnings_drift import DriftPreparation, EarningsDriftService
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import SignalRecord
from tests.research.probe_fixtures import paper_database
from tests.research.test_apriori_catalog_probe import ENTRY, study


TICK = 0.01


def _event_row(symbol: str, session: date, *, atr: float, z: float = 2.3) -> dict:
    """One ``build_events`` row as the study produces it: numpy scalars, a NaN reported surprise."""
    row = dict.fromkeys(EVENT_COLUMNS)
    row.update(
        symbol=symbol,
        report_date=session - timedelta(days=2),
        session=session,
        decision_at=pd.Timestamp(datetime.combine(session, time(10, 35), tzinfo=ET_TZ)).tz_convert("UTC"),
        z=np.float64(z),
        surprise_pct=np.float64(12.5),
        surprise_pct_reported=float("nan"),
        atr=np.float64(atr),
        median_dollar_volume=np.float64(5e7),
        reference=np.float64(1e7),
        leg="LONG",
        surprise_long=np.bool_(True),
        surprise_short=np.bool_(False),
        reaction_long=np.bool_(True),
        reaction_short=np.bool_(False),
    )
    return row


async def _admission(database, signal_id: int) -> str | None:
    async with database.session_factory() as session, session.begin():
        record = await session.get(SignalRecord, signal_id)
        return await database.workflows._alpha_entry_rejection(session, record)


async def test_a_live_drift_event_becomes_an_admissible_capped_card(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    try:
        repository = AlphaRepository(database.workflows)
        definition = load_catalog_probe(ENTRY, study(tmp_path), "long")
        await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
        service = EarningsDriftService(ENTRY, earnings=None, calendar=None, bars=None, static_symbols=())
        version = service.live_version(await repository.snapshot())
        assert version == definition

        today = datetime.now(ET_TZ).date()
        event = event_document(_event_row("WINR", today, atr=1.737))
        assert json.loads(json.dumps(event, allow_nan=False)) == event  # journal- and provenance-safe
        prep = DriftPreparation(
            "ok", today, version_id=version["version_id"], policy=version["execution"], events=(event,)
        )
        daily_index = pd.date_range(end=datetime.combine(today, time(0), tzinfo=UTC), periods=250, freq="D")
        daily = pd.DataFrame(
            {"Open": 50.0, "High": 51.0, "Low": 49.0, "Close": np.linspace(40, 50, 250), "Volume": 1e6},
            index=daily_index,
        )
        # Today's regular-session hourly bars through the 10:30 New York bar.
        hourly_index = pd.DatetimeIndex(
            [datetime.combine(today, time(hh, 30), tzinfo=ET_TZ).astimezone(UTC) for hh in (9, 10)]
        )
        hourly = pd.DataFrame(
            {"Open": 50.0, "High": 50.5, "Low": 49.5, "Close": 50.123, "Volume": 1e5}, index=hourly_index
        )

        candidate = service.candidate(event, SimpleNamespace(hourly=hourly, four_hour=hourly, daily=daily), prep)

        assert isinstance(candidate, ScreenerCandidate)
        assert (candidate.strategy, candidate.probe, candidate.catalog_event) == ("pead_long", True, event)
        config = AppConfig(execution_mode="alpaca", alpaca_paper=True)  # paper_database's own settings
        levels = RiskEvaluator(config).calculate_levels_deterministic(candidate, current_equity=100_000)
        entry_price = entry_limit(candidate.current_price, execution_policy_from_dict(candidate.alpha_policy))
        assert entry_price == 50.12
        frozen = levels_for("LONG", entry_price, event["atr"], load_pead_entry(ENTRY).entry)
        # The study's exact 2xATR stop / 3R target, rounded outward to the tick.
        assert 0 <= frozen.stop - levels.stop_loss < TICK + 1e-9
        assert 0 <= levels.take_profit - frozen.target < 3 * TICK + 1e-9
        assert 0 < levels.risk_dollars <= config.alpha_pipeline.probe_risk_dollars

        async def record(provenance_event: dict) -> int:
            return await database.record_signal(
                contract=candidate.contract,
                strategy=candidate.strategy,
                direction="LONG",
                entry_price=entry_price,
                stop_loss=levels.stop_loss,
                take_profit=levels.take_profit,
                risk_dollars=levels.risk_dollars,
                asset_class="EQUITY",
                quantity=levels.quantity,
                timeframe=candidate.timeframe,
                alpha_version=candidate.alpha_version,
                alpha_policy=candidate.alpha_policy,
                decision_provenance={"pead_event": provenance_event, "paper_probe": True},
            )

        assert await _admission(database, await record(candidate.catalog_event)) is None
        # The same card naming another session's event is refused: the chain is live, not vacuous.
        stale = {**event, "session": (today - timedelta(days=1)).isoformat()}
        assert await _admission(database, await record(stale)) == (
            "Catalog signal differs from its immutable strategy contract."
        )
    finally:
        await database.engine.dispose()


def _rising_hourly(session: date, sessions: int = 40) -> pd.DataFrame:
    """Regular-session hourly bars (09:30..15:30 New York) from three days before ``session``."""
    stamps, day = [], session - timedelta(days=3)
    while len(stamps) < sessions * 7:
        if day.weekday() < 5:
            stamps.extend(datetime.combine(day, time(hh, 30), tzinfo=ET_TZ).astimezone(UTC) for hh in range(9, 16))
        day += timedelta(days=1)
    price = np.linspace(100, 110, len(stamps))
    return pd.DataFrame(
        {"Open": price, "High": price + 0.2, "Low": price - 0.2, "Close": price, "Volume": 1000},
        index=pd.DatetimeIndex(stamps),
    )


def test_journaled_events_label_and_summarize_through_the_outcomes_chain():
    entry = load_pead_entry(ENTRY).entry
    session = date(2026, 6, 3)
    events = [
        {**event_document(_event_row("WINR", session, atr=1.0)), "outcome": "sent"},
        {**event_document(_event_row("OWND", session, atr=1.0, z=2.1)), "outcome": "skipped: open position"},
    ]
    # Exactly what `_journal_drift` writes and the journal reads back.
    payload = json.loads(json.dumps({"scan_id": "scan-1", "events": events}, allow_nan=False))

    decisions = decision_frame([payload])

    assert sorted(decisions["symbol"]) == ["OWND", "WINR"]
    assert set(decisions["session"]) == {session}
    hourly = {symbol: _rising_hourly(session) for symbol in ("WINR", "OWND")}
    labels, _ = label_events(decisions[list(EVENT_COLUMNS)], hourly, entry)
    labels = labels[(labels["direction"] == "LONG") & labels["is_leg"]]
    assert sorted(labels["symbol"]) == ["OWND", "WINR"]
    # +10 over 40 sessions never reaches the 3R (+6) target inside the 20-session hold.
    assert set(labels["hit"]) == {"timeout"} and set(labels["holding_sessions"]) == {20}
    assert (labels["r_cost"] > 0).all()

    summary = summarize_probe(decisions, labels, [], None, 0.149)

    assert (summary["events"], summary["carded"], summary["executed"], summary["closed"]) == (2, 1, 0, 0)
    assert summary["counterfactual"]["mature"] == 2
    assert summary["counterfactual"]["mean_r_cost"] == float(labels["r_cost"].mean())
    assert summary["study_mean_r"] == 0.149
