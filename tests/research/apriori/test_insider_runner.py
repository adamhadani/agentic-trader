from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.research.apriori import insider_runner
from agentic_trader.research.apriori.catalog import InsiderEntry, load_insider_entry
from agentic_trader.research.apriori.insider_runner import build_insider_leg_inputs
from agentic_trader.research.insider_filings import InsiderTransaction


REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / "config/research/apriori/insider-v1.json"
ENTRY = load_insider_entry(SHIPPED).entry
DAYS = tuple(
    date(2021, 1, 4) + timedelta(days=i) for i in range(60) if (date(2021, 1, 4) + timedelta(days=i)).weekday() < 5
)
SYMBOL = "AAPL"


def _entry(**update) -> InsiderEntry:
    return InsiderEntry.model_validate({**ENTRY.model_dump(mode="json"), **update})


def _raw():
    index = pd.DatetimeIndex([datetime.combine(d, time(5), tzinfo=UTC) for d in DAYS])
    return pd.DataFrame({"Close": 10.0, "Volume": 1e5}, index=index)  # DV = 1e6


def _tx(day, dollars):
    return InsiderTransaction(SYMBOL, day, day, "P", dollars, 1)


class _Store:
    def __init__(self, directory):
        pass

    def load_tickers(self):
        return {"0": {"ticker": SYMBOL, "cik_str": 320193}}


def _install(monkeypatch, *, cube_sha=None, calls=None):
    async def fake_build(cohort, spec, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        assert kwargs["require_cached"] is True
        cube = SimpleNamespace(sha256=cube_sha or ENTRY.cube_sha256)
        return SimpleNamespace(cube=cube, trading_days=DAYS, adjusted={}, raw={SYMBOL: _raw()})

    filings = SimpleNamespace(
        by_symbol={SYMBOL: (_tx(DAYS[30], 1e6), _tx(DAYS[31], 1e8))},
        files_sha256="f" * 64,
        drops={"rule": 2},
    )
    monkeypatch.setattr(insider_runner, "build_cube_inputs", fake_build)
    monkeypatch.setattr(insider_runner, "load_insider_sources", lambda inputs, sources, symbols: (filings, ()))
    monkeypatch.setattr(insider_runner, "InsiderFilingStore", _Store)
    monkeypatch.setattr(
        insider_runner,
        "load_cohort",
        lambda path: SimpleNamespace(sha256=insider_runner_cohort_sha(), cohort=SimpleNamespace(symbols=(SYMBOL,))),
    )


def insider_runner_cohort_sha():
    return insider_runner.load_campaign_protocol(REPO / ENTRY.pooled_protocol).protocol.cohort_sha256


def _build(entry=ENTRY, tmp_path=None):
    return build_insider_leg_inputs(
        entry,
        repo_root=REPO,
        bars=object(),
        calendar=object(),
        cache_dir=Path("cache"),
        insider_filings_dir=Path("filings"),
        actions_file=Path("actions.json"),
        pace=None,
    )


async def test_protocol_digest_mismatch_raises_before_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no I/O after a digest mismatch")

    monkeypatch.setattr(insider_runner, "build_cube_inputs", forbidden)
    monkeypatch.setattr(insider_runner, "load_insider_sources", forbidden)
    with pytest.raises(ValueError, match="pooled_protocol_sha256"):
        await _build(_entry(pooled_protocol_sha256="0" * 64))


async def test_cube_sha_mismatch_raises(monkeypatch):
    _install(monkeypatch, cube_sha="1" * 64)
    with pytest.raises(ValueError, match=f"{'1' * 64}.*{ENTRY.cube_sha256}"):
        await _build()


async def test_short_cooldown_refused_by_check_against(monkeypatch):
    _install(monkeypatch)
    event = {**ENTRY.model_dump(mode="json")["event"], "cooldown_sessions": 20}
    with pytest.raises(ValueError, match="cooldown_sessions"):
        await _build(_entry(event=event))


async def test_implausible_dropped_before_events(monkeypatch):
    calls: list = []
    _install(monkeypatch, calls=calls)
    inputs = await _build()
    assert [t.dollars for t in inputs.transactions[SYMBOL]] == [1e6]
    assert inputs.provenance["implausible_size"] == 1
    assert inputs.provenance["cube_sha256"] == ENTRY.cube_sha256
    assert inputs.provenance["loader_drops"] == {"rule": 2}
    assert inputs.decision_time == time(10, 35)
    assert inputs.issuer_of == {SYMBOL: 320193}
    assert calls[0]["static_symbols"] == ()
