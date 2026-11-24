"""A missing decision input is an explicit ``UNKNOWN`` stress state; a missing VIX is ``RegimeUnavailable``.

The macro engine is patched only through its ``yahoo_fetch`` and ``fred_client`` parameters: fakes built
from one ``{name: value | None | exception}`` map, with no module globals and no network.
"""

from __future__ import annotations

import copy
import logging
import pickle
import random
from collections import Counter
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, call, patch

import pandas as pd
import pytest

from agentic_trader.agent.macro import (
    CONTEXT_INPUTS,
    DECISION_INPUTS,
    MACRO_INPUT_SOURCES,
    FredDataClient,
    MacroInput,
    MacroIntelligenceEngine,
    MacroStressAssessment,
    MacroStressLevel,
    _yahoo_input_from_history,
    unknown_macro_stress,
)
from agentic_trader.agent.regime import (
    REGIME_BLOCK_KEYS,
    RegimeDetector,
    RegimeSnapshot,
    RegimeUnavailable,
    regime_journal_block,
    unavailable_regime_block,
)
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import VolatilityRegime


DECISION_OK = {"vix": 17.2, "3m": 3.95, "2y": 4.35, "10y": 4.95, "hy_oas": 3.70}  # MODERATE: credit ELEVATED
CONTEXT_OK = {"5y": 4.65, "30y": 5.25, "dxy": 103.4, "t10yie": 2.37, "t5yie": 2.40}
OBSERVED = date(2026, 10, 20)


def _fake_input(values: dict, name: str) -> MacroInput:
    value = values[name]
    if isinstance(value, BaseException):
        raise value
    return MacroInput(
        name=name,
        value=value,
        source=MACRO_INPUT_SOURCES[name],
        observed_on=OBSERVED if value is not None else None,
        decision=name in DECISION_INPUTS,
        error=None if value is not None else "no_observation",
    )


class FakeYahoo:
    """``yahoo_fetch``: reads the shared value map, counting calls."""

    def __init__(self, values: dict):
        self.values = values
        self.calls: list[str] = []

    def __call__(self, name: str, ticker: str, *, decision: bool) -> MacroInput:
        self.calls.append(name)
        assert MACRO_INPUT_SOURCES[name] == f"yfinance:{ticker}"
        assert decision is (name in DECISION_INPUTS)
        return _fake_input(self.values, name)


class FakeFred(FredDataClient):
    """``fred_client``: no HTTP; reads the shared value map, counting calls."""

    def __init__(self, values: dict):
        super().__init__()
        self.values = values
        self.calls: list[str] = []

    async def fetch_input(
        self, name: str, series_id: str, *, decision: bool, force_refresh: bool = False
    ) -> MacroInput:
        self.calls.append(name)
        assert MACRO_INPUT_SOURCES[name] == f"fred:{series_id}"
        return _fake_input(self.values, name)


def engine_with(values: dict, config: RegimeConfig | None = None) -> MacroIntelligenceEngine:
    return MacroIntelligenceEngine(
        config=config or RegimeConfig(),
        fred_client=FakeFred(values),
        yahoo_fetch=FakeYahoo(values),
        vix_retry_delay_seconds=0,
    )


def detector_with(values: dict, config: RegimeConfig | None = None) -> RegimeDetector:
    config = config or RegimeConfig()
    return RegimeDetector(config, macro_engine=engine_with(values, config))


def test_input_tuples_and_sources_are_the_declared_ten():
    assert DECISION_INPUTS == ("vix", "3m", "2y", "10y", "hy_oas")
    assert CONTEXT_INPUTS == ("5y", "30y", "dxy", "t10yie", "t5yie")
    assert dict(MACRO_INPUT_SOURCES) == {
        "vix": "yfinance:^VIX",
        "3m": "yfinance:^IRX",
        "2y": "fred:DGS2",
        "10y": "yfinance:^TNX",
        "hy_oas": "fred:BAMLH0A0HYM2",
        "5y": "yfinance:^FVX",
        "30y": "yfinance:^TYX",
        "dxy": "yfinance:DX-Y.NYB",
        "t10yie": "fred:T10YIE",
        "t5yie": "fred:T5YIE",
    }


def test_unknown_macro_stress_is_the_configured_policy():
    stress = unknown_macro_stress(RegimeConfig(), ["2y", "hy_oas"], vix=17.2, hy_oas=None)
    assert stress == MacroStressAssessment(
        level=MacroStressLevel.UNKNOWN,
        risk_multiplier=0.5,
        squeeze_breakout_allowed=False,
        min_rr_threshold=2.0,
        key_drivers=["Missing decision inputs: 2y, hy_oas"],
    )
    configured = unknown_macro_stress(
        RegimeConfig(unknown_risk_multiplier=0.3, unknown_breakout_allowed=True), ["3m"], vix=17.2, hy_oas=3.7
    )
    assert (configured.risk_multiplier, configured.squeeze_breakout_allowed) == (0.3, True)


def test_the_present_vix_and_hy_oas_are_required_so_no_caller_skips_the_extreme_cap():
    with pytest.raises(TypeError, match="vix"):
        unknown_macro_stress(RegimeConfig(), ["2y"])


async def test_complete_evaluation_has_every_input_and_a_report():
    evaluation = await engine_with({**DECISION_OK, **CONTEXT_OK}).evaluate()
    assert tuple(evaluation.inputs) == (*DECISION_INPUTS, *CONTEXT_INPUTS)
    assert evaluation.missing == () and evaluation.missing_decision == ()
    assert evaluation.report is not None and evaluation.report.stress == evaluation.stress
    assert evaluation.stress.level is MacroStressLevel.MODERATE
    assert evaluation.report.observation_dates == {
        "DGS2": "2026-10-20",
        "BAMLH0A0HYM2": "2026-10-20",
        "T10YIE": "2026-10-20",
        "T5YIE": "2026-10-20",
    }


async def test_context_inputs_never_move_the_stress():
    baseline = (await engine_with({**DECISION_OK, **CONTEXT_OK}).evaluate()).stress
    rng = random.Random(20261021)
    for _ in range(200):
        context = {n: rng.choice([None, rng.uniform(-1.0, 8.0)]) for n in CONTEXT_INPUTS}
        context["dxy"] = rng.choice([None, rng.uniform(80.0, 120.0)])
        assert (await engine_with({**DECISION_OK, **context}).evaluate(force_refresh=True)).stress == baseline


@pytest.mark.parametrize("name", CONTEXT_INPUTS)
async def test_one_missing_context_input_keeps_the_report_and_stress(name):
    baseline = (await engine_with({**DECISION_OK, **CONTEXT_OK}).evaluate()).stress
    evaluation = await engine_with({**DECISION_OK, **CONTEXT_OK, name: None}).evaluate()
    assert evaluation.report is not None
    assert evaluation.missing == (name,) and evaluation.missing_decision == ()
    assert evaluation.stress == baseline
    assert evaluation.inputs[name].error == "no_observation" and evaluation.inputs[name].decision is False


async def test_a_missing_context_input_leaves_its_report_fields_none():
    values = {**DECISION_OK, "5y": None, "30y": None, "dxy": None, "t10yie": 2.37, "t5yie": None}
    report = (await engine_with(values).evaluate()).report
    assert report is not None
    assert (report.yields.yield_5y, report.yields.yield_30y, report.spreads.curvature_butterfly_bps) == (None,) * 3
    assert (report.dxy, report.inflation) == (None, None)
    assert "T5YIE" not in report.observation_dates and report.observation_dates["T10YIE"] == "2026-10-20"


@pytest.mark.parametrize("name", ["3m", "2y", "10y", "hy_oas"])
@pytest.mark.parametrize(("vix", "min_rr"), [(17.2, 2.0), (25.0, 2.2)])
async def test_each_missing_decision_input_gives_unknown_at_half_with_breakouts_off(name, vix, min_rr):
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, "vix": vix, name: None}).get_regime(force_refresh=True)
    assert snapshot.macro_stress.level is MacroStressLevel.UNKNOWN and snapshot.macro_report is None
    assert (snapshot.risk_multiplier, snapshot.breakout_allowed, snapshot.min_rr_threshold) == (0.5, False, min_rr)
    assert f"Macro: UNKNOWN (missing {name}; ×0.50, breakouts suppressed)" in snapshot.summary_text
    assert snapshot.macro_unavailable_reason == f"Missing decision inputs: {name}"
    block = regime_journal_block(snapshot)
    assert (block["macro_stress"], block["missing"], block["missing_decision"]) == ("UNKNOWN", [name], [name])


async def _measured_extreme(values: dict) -> MacroStressAssessment:
    """EXTREME's limits as the full measurement of ``values`` sets them."""
    stress = (await engine_with({**DECISION_OK, **CONTEXT_OK, **values}).evaluate()).stress
    assert stress.level is MacroStressLevel.EXTREME
    return stress


@pytest.mark.parametrize(
    ("present", "missing"),
    [({"vix": 45.0}, "hy_oas"), ({"hy_oas": 5.6}, "2y")],
    ids=["vix-above-extreme", "hy-oas-critical"],
)
async def test_unknown_never_sizes_above_extreme_when_the_present_inputs_force_extreme(present, missing):
    extreme = await _measured_extreme(present)
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, **present, missing: None}).get_regime()
    stress = snapshot.macro_stress
    assert stress.level is MacroStressLevel.UNKNOWN and snapshot.macro_report is None
    assert stress.key_drivers == [f"Missing decision inputs: {missing}"]
    assert stress.risk_multiplier == snapshot.risk_multiplier == extreme.risk_multiplier < 0.5
    assert stress.min_rr_threshold == snapshot.min_rr_threshold == extreme.min_rr_threshold
    assert (stress.squeeze_breakout_allowed, snapshot.breakout_allowed) == (False, False)


async def test_unknown_at_a_normal_vix_with_hy_oas_missing_keeps_the_configured_policy():
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, "hy_oas": None}).get_regime()
    assert snapshot.macro_stress.level is MacroStressLevel.UNKNOWN
    assert (snapshot.risk_multiplier, snapshot.breakout_allowed, snapshot.min_rr_threshold) == (0.5, False, 2.0)


def test_the_extreme_cap_keeps_a_smaller_configured_multiplier_and_turns_breakouts_off():
    config = RegimeConfig(unknown_risk_multiplier=0.1, unknown_breakout_allowed=True)
    stress = unknown_macro_stress(config, ["hy_oas"], vix=45.0, hy_oas=None)
    assert (stress.level, stress.risk_multiplier, stress.squeeze_breakout_allowed, stress.min_rr_threshold) == (
        MacroStressLevel.UNKNOWN,
        0.1,
        False,
        config.extreme_min_rr,
    )
    calm = unknown_macro_stress(config, ["2y"], vix=17.2, hy_oas=3.7)
    assert (calm.risk_multiplier, calm.squeeze_breakout_allowed, calm.min_rr_threshold) == (0.1, True, 2.0)


async def test_two_missing_decision_inputs_are_named_in_canonical_order():
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, "hy_oas": None, "2y": None}).get_regime()
    assert "Macro: UNKNOWN (missing 2y, hy_oas; ×0.50, breakouts suppressed)" in snapshot.summary_text
    assert snapshot.macro_stress.key_drivers == ["Missing decision inputs: 2y, hy_oas"]


async def test_an_exception_from_a_source_is_its_type_name():
    evaluation = await engine_with(
        {**DECISION_OK, **CONTEXT_OK, "2y": TimeoutError("slow"), "dxy": KeyError("x")}
    ).evaluate()
    assert evaluation.inputs["2y"].error == "TimeoutError" and evaluation.inputs["2y"].value is None
    assert evaluation.inputs["dxy"].error == "KeyError" and evaluation.inputs["dxy"].source == "yfinance:DX-Y.NYB"
    assert evaluation.missing == ("2y", "dxy") and evaluation.missing_decision == ("2y",)
    assert evaluation.stress.level is MacroStressLevel.UNKNOWN


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
async def test_a_non_finite_value_is_an_unexpected_payload(bad):
    evaluation = await engine_with({**DECISION_OK, **CONTEXT_OK, "3m": bad}).evaluate()
    assert (evaluation.inputs["3m"].value, evaluation.inputs["3m"].error) == (None, "unexpected_payload")
    assert evaluation.report is None and evaluation.missing_decision == ("3m",)


async def test_missing_vix_raises_regime_unavailable_with_its_inputs():
    with pytest.raises(RegimeUnavailable, match=r"^Market regime unavailable: missing vix$") as caught:
        await detector_with({**DECISION_OK, **CONTEXT_OK, "vix": None}).get_regime(force_refresh=True)
    assert caught.value.inputs["vix"].error == "no_observation" and caught.value.missing == ("vix",)


async def test_missing_vix_with_other_inputs_names_them_decision_first():
    values = {**DECISION_OK, **CONTEXT_OK, "vix": None, "dxy": None, "hy_oas": None}
    with pytest.raises(RegimeUnavailable, match=r"^Market regime unavailable: missing vix, hy_oas, dxy$"):
        await detector_with(values).get_regime(force_refresh=True)


async def test_missing_vix_with_the_yield_curve_disabled_is_unavailable_too():
    with pytest.raises(RegimeUnavailable) as caught:
        await detector_with(
            {**DECISION_OK, **CONTEXT_OK, "vix": None}, RegimeConfig(yield_curve_enabled=False)
        ).get_regime()
    assert set(caught.value.inputs) == {"vix", "10y", "dxy"} and caught.value.missing == ("vix",)


async def test_disabled_yield_curve_is_vix_only_at_full_size():
    detector = detector_with({**DECISION_OK, **CONTEXT_OK}, RegimeConfig(yield_curve_enabled=False))
    snapshot = await detector.get_regime(force_refresh=True)
    block = regime_journal_block(snapshot)
    assert (snapshot.risk_multiplier, block["macro_enabled"], block["macro_stress"]) == (1.0, False, None)
    assert set(snapshot.macro_inputs) == {"vix", "10y", "dxy"}
    assert (snapshot.tnx, snapshot.dxy) == (4.95, 103.4)
    assert snapshot.macro_unavailable_reason == "Yield-curve enrichment is disabled in configuration"
    assert detector.macro_engine.fred_client.calls == []


async def test_snapshot_and_unavailable_error_are_plain_copyable_values():
    snapshot = await detector_with({**DECISION_OK, **CONTEXT_OK, "2y": None}).get_regime()
    assert copy.deepcopy(snapshot) == snapshot
    with pytest.raises(RegimeUnavailable) as caught:
        await detector_with({**DECISION_OK, **CONTEXT_OK, "vix": None, "hy_oas": None}).get_regime()
    original = caught.value
    for restored in (copy.copy(original), copy.deepcopy(original), pickle.loads(pickle.dumps(original))):
        assert type(restored) is RegimeUnavailable and restored is not original
        assert restored.missing == original.missing == ("vix", "hy_oas")
        assert str(restored) == str(original) == "Market regime unavailable: missing vix, hy_oas"
        assert dict(restored.inputs) == dict(original.inputs)


async def test_unknown_snapshot_is_cached_and_force_refresh_refetches():
    detector = detector_with({**DECISION_OK, **CONTEXT_OK, "2y": None})
    yahoo = detector.macro_engine.yahoo_fetch
    first = await detector.get_regime(force_refresh=True)
    reads = len(yahoo.calls)
    assert first.macro_stress.level is MacroStressLevel.UNKNOWN and reads == 6
    assert await detector.get_regime() is first
    assert len(yahoo.calls) == reads
    refreshed = await detector.get_regime(force_refresh=True)
    assert refreshed is not first and len(yahoo.calls) == 2 * reads


class FlakyVix(FakeYahoo):
    """``yahoo_fetch`` whose VIX reads return ``vix_reads`` in order (a value, None or an exception to raise)."""

    def __init__(self, values: dict, vix_reads: list):
        super().__init__(values)
        self.vix_reads = list(vix_reads)

    def __call__(self, name: str, ticker: str, *, decision: bool) -> MacroInput:
        if name == "vix":
            self.values = {**self.values, "vix": self.vix_reads.pop(0)}
        return super().__call__(name, ticker, decision=decision)


def flaky_vix_detector(vix_reads: list, config: RegimeConfig | None = None) -> RegimeDetector:
    config = config or RegimeConfig()
    values = {**DECISION_OK, **CONTEXT_OK}
    engine = MacroIntelligenceEngine(
        config=config, fred_client=FakeFred(values), yahoo_fetch=FlakyVix(values, vix_reads), vix_retry_delay_seconds=0
    )
    return RegimeDetector(config, macro_engine=engine)


@pytest.mark.parametrize(
    ("vix_reads", "sleeps"),
    [([TimeoutError("slow"), 18.4], [call(2.0)]), ([18.4], [])],
    ids=["first-read-fails", "first-read-succeeds"],
)
async def test_only_a_failed_vix_read_waits_two_seconds_before_its_one_retry(monkeypatch, vix_reads, sleeps):
    sleep = AsyncMock()
    monkeypatch.setattr("agentic_trader.agent.macro.asyncio.sleep", sleep)
    values = {**DECISION_OK, **CONTEXT_OK}
    config = RegimeConfig()
    engine = MacroIntelligenceEngine(  # the default delay
        config=config, fred_client=FakeFred(values), yahoo_fetch=FlakyVix(values, vix_reads)
    )

    snapshot = await RegimeDetector(config, macro_engine=engine).get_regime(force_refresh=True)

    assert snapshot.vix == 18.4
    assert sleep.await_args_list == sleeps


async def test_a_failed_vix_read_is_retried_once_and_the_fresh_second_read_is_measured(caplog):
    detector = flaky_vix_detector([TimeoutError("slow"), 18.4])

    with caplog.at_level(logging.WARNING, logger="agentic_trader.agent.macro"):
        snapshot = await detector.get_regime(force_refresh=True)

    assert snapshot.vix == 18.4 and snapshot.macro_report is not None
    assert snapshot.macro_inputs["vix"].error is None
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "vix_read_retry"]
    assert (record.levelno, record.error, record.attempt) == (logging.WARNING, "TimeoutError", 1)
    assert "slow" not in record.getMessage()


@pytest.mark.parametrize("yield_curve_enabled", [True, False], ids=["macro", "vix-only"])
async def test_two_failed_vix_reads_raise_regime_unavailable_and_no_other_input_is_retried(yield_curve_enabled):
    detector = flaky_vix_detector([None, TimeoutError("slow")], RegimeConfig(yield_curve_enabled=yield_curve_enabled))

    with pytest.raises(RegimeUnavailable) as caught:
        await detector.get_regime(force_refresh=True)

    assert caught.value.inputs["vix"].error == "TimeoutError"  # the second, fresh read's outcome
    engine = detector.macro_engine
    reads = Counter([*engine.yahoo_fetch.calls, *engine.fred_client.calls])
    assert reads.pop("vix") == 2
    assert set(reads.values()) == {1}
    assert set(reads) == ({*MACRO_INPUT_SOURCES} if yield_curve_enabled else {"10y", "dxy"}) - {"vix"}


async def test_a_partial_evaluation_is_not_cached_and_recovery_is_measured():
    values = {**DECISION_OK, **CONTEXT_OK, "2y": None}
    engine = engine_with(values)
    assert (await engine.evaluate()).stress.level is MacroStressLevel.UNKNOWN
    values["2y"] = 4.35
    recovered = await engine.evaluate()
    assert recovered.report is not None and recovered.stress.level is MacroStressLevel.MODERATE
    reads = len(engine.yahoo_fetch.calls)
    assert await engine.evaluate() is recovered  # a complete evaluation is cached
    assert len(engine.yahoo_fetch.calls) == reads


async def test_a_missing_context_input_is_not_cached_either():
    values = {**DECISION_OK, **CONTEXT_OK, "dxy": None}
    engine = engine_with(values)
    first = await engine.evaluate()
    assert first.missing == ("dxy",)
    values["dxy"] = 103.4
    assert (await engine.evaluate()).missing == ()


async def test_fred_html_200_is_unexpected_payload():
    client = FredDataClient()
    with patch.object(
        client, "_fetch_csv_text", new=AsyncMock(return_value="<html><title>Site Maintenance</title></html>")
    ):
        result = await client.fetch_input("2y", "DGS2", decision=True)
    assert (result.value, result.error, result.source) == (None, "unexpected_payload", "fred:DGS2")


@pytest.mark.parametrize("header", ["DATE,DGS2", "observation_date,DGS2", "Observation_Date,DGS2"])
async def test_fred_csv_gives_value_and_observation_date(header):
    client = FredDataClient()
    body = f"\n{header}\n2026-10-19,4.31\n2026-10-20,4.35\n2026-10-21,.\n"
    with patch.object(client, "_fetch_csv_text", new=AsyncMock(return_value=body)):
        result = await client.fetch_input("2y", "DGS2", decision=True)
    assert result == MacroInput("2y", 4.35, "fred:DGS2", date(2026, 10, 20), True, None)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("DATE,DGS2,EXTRA\n2026-10-20,4.35,1\n", "unexpected_payload"),
        ("value\n4.35\n", "unexpected_payload"),
        ("series,DGS2\n2026-10-20,4.35\n", "unexpected_payload"),
        ("  \n\n", "unexpected_payload"),
        ("DATE,DGS2\n", "no_observation"),
        ("DATE,DGS2\n2026-10-20,.\n", "no_observation"),
        ("DATE,DGS2\n2026-10-20,abc\nnot-a-date,4.1\n2026-10-21,nan\n", "no_observation"),
    ],
)
async def test_fred_body_without_a_usable_observation(body, error):
    client = FredDataClient()
    with patch.object(client, "_fetch_csv_text", new=AsyncMock(return_value=body)):
        result = await client.fetch_input("2y", "DGS2", decision=True)
    assert (result.value, result.error, result.observed_on) == (None, error, None)


async def test_fred_transport_failure_is_its_type_name_and_is_not_cached():
    client = FredDataClient()
    with patch.object(client, "_fetch_csv_text", new=AsyncMock(side_effect=TimeoutError("read timed out"))):
        failed = await client.fetch_input("hy_oas", "BAMLH0A0HYM2", decision=True)
    assert (failed.value, failed.error) == (None, "TimeoutError")
    ok = AsyncMock(return_value="DATE,BAMLH0A0HYM2\n2026-10-20,3.70\n")
    with patch.object(client, "_fetch_csv_text", new=ok):
        assert (await client.fetch_input("hy_oas", "BAMLH0A0HYM2", decision=True)).value == 3.70
        assert (await client.fetch_input("hy_oas", "BAMLH0A0HYM2", decision=True)).value == 3.70
        assert ok.await_count == 1  # a success is cached
        await client.fetch_input("hy_oas", "BAMLH0A0HYM2", decision=True, force_refresh=True)
        assert ok.await_count == 2


def test_yahoo_history_gives_the_last_non_null_close_and_its_date():
    index = pd.DatetimeIndex(
        [
            datetime(2026, 10, 16, 4, tzinfo=UTC),
            datetime(2026, 10, 19, 4, tzinfo=UTC),
            datetime(2026, 10, 20, 4, tzinfo=UTC),
        ]
    ).tz_convert("America/New_York")
    history = pd.DataFrame({"Close": [17.01234, 18.98765, float("nan")]}, index=index)
    result = _yahoo_input_from_history("vix", "^VIX", history, decision=True)
    assert result == MacroInput("vix", 18.988, "yfinance:^VIX", date(2026, 10, 19), True, None)


@pytest.mark.parametrize(
    "history",
    [pd.DataFrame(), pd.DataFrame({"Open": [1.0]}), pd.DataFrame({"Close": [float("nan")]})],
)
def test_yahoo_history_without_a_close_is_no_observation(history):
    result = _yahoo_input_from_history("dxy", "DX-Y.NYB", history, decision=False)
    assert result == MacroInput("dxy", None, "yfinance:DX-Y.NYB", None, False, "no_observation")


def _snapshot(timestamp: datetime, **overrides) -> RegimeSnapshot:
    inputs = {
        name: MacroInput(name, value, MACRO_INPUT_SOURCES[name], OBSERVED, name in DECISION_INPUTS, None)
        for name, value in {**DECISION_OK, **CONTEXT_OK}.items()
    }
    inputs["2y"] = MacroInput("2y", None, "fred:DGS2", None, True, "TimeoutError")
    inputs["dxy"] = MacroInput("dxy", None, "yfinance:DX-Y.NYB", None, False, "no_observation")
    fields = {
        "vix": 17.2,
        "vix_regime": VolatilityRegime.NORMAL,
        "tnx": 4.95,
        "dxy": None,
        "breakout_allowed": False,
        "min_rr_threshold": 2.0,
        "timestamp": timestamp,
        "summary_text": "test",
        "risk_multiplier": 0.5,
        "macro_stress": unknown_macro_stress(RegimeConfig(), ["2y"], vix=17.2, hy_oas=3.7),
        "macro_inputs": inputs,
    }
    return RegimeSnapshot(**{**fields, **overrides})


def test_regime_block_carries_inputs_missing_and_clock_regime():
    block = regime_journal_block(_snapshot(datetime(2026, 10, 21, 14, 35, tzinfo=UTC)))
    assert len(REGIME_BLOCK_KEYS) == 12 and tuple(block) == REGIME_BLOCK_KEYS
    assert REGIME_BLOCK_KEYS[:7] == (
        "vix",
        "vix_regime",
        "breakout_allowed",
        "risk_multiplier",
        "min_rr_threshold",
        "macro_stress",
        "observed_at",
    )
    assert block["macro_enabled"] is True and block["macro_stress"] == "UNKNOWN"
    assert tuple(block["inputs"]) == (*DECISION_INPUTS, *CONTEXT_INPUTS)
    assert block["inputs"]["2y"] == {
        "value": None,
        "source": "fred:DGS2",
        "observed_on": None,
        "decision": True,
        "error": "TimeoutError",
    }
    assert block["inputs"]["hy_oas"] == {
        "value": 3.70,
        "source": "fred:BAMLH0A0HYM2",
        "observed_on": "2026-10-20",
        "decision": True,
        "error": None,
    }
    assert block["inputs"]["t5yie"]["decision"] is False
    assert (block["missing"], block["missing_decision"]) == (["2y", "dxy"], ["2y"])
    assert block["clock_regime"] == "EDT"
    assert regime_journal_block(_snapshot(datetime(2026, 11, 3, 15, 0, tzinfo=UTC)))["clock_regime"] == "EST"


def test_regime_block_without_macro_stress_is_not_macro_enabled():
    block = regime_journal_block(_snapshot(datetime(2026, 10, 21, 14, 35, tzinfo=UTC), macro_stress=None))
    assert (block["macro_enabled"], block["macro_stress"]) == (False, None)


def test_unavailable_regime_block_has_null_snapshot_fields():
    at = datetime(2026, 10, 21, 14, 35, tzinfo=UTC)
    inputs = {
        "vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout"),
        "10y": MacroInput("10y", 4.95, "yfinance:^TNX", OBSERVED, True, None),
    }
    block = unavailable_regime_block(RegimeUnavailable(inputs), at=at, macro_enabled=True)
    assert tuple(block) == REGIME_BLOCK_KEYS
    for key in ("vix", "vix_regime", "breakout_allowed", "risk_multiplier", "min_rr_threshold", "macro_stress"):
        assert block[key] is None
    assert block["observed_at"] == "2026-10-21T14:35:00+00:00" and block["clock_regime"] == "EDT"
    assert block["macro_enabled"] is True
    assert block["inputs"]["vix"]["error"] == "ReadTimeout" and block["inputs"]["10y"]["value"] == 4.95
    assert (block["missing"], block["missing_decision"]) == (["vix"], ["vix"])

    other = unavailable_regime_block(TimeoutError("slow"), at=at, macro_enabled=False)
    assert tuple(other) == REGIME_BLOCK_KEYS
    assert (other["inputs"], other["missing"], other["missing_decision"], other["macro_enabled"]) == ({}, [], [], False)
    assert other["vix"] is None and other["macro_stress"] is None
