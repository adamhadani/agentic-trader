from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.pooled.campaign import ShortInputs
from agentic_trader.research.pooled.formula import require_dimensionless
from agentic_trader.research.pooled.short_fields import (
    SHORT_FIELDS,
    LoadedShortVolume,
    build_short_frames,
    load_short_volume,
    verify_short_sources,
)
from agentic_trader.research.short_volume import ShortVolumeRow, ShortVolumeStore, files_sha256
from tests.research.pooled.test_formula import daily, weekdays


DAYS = weekdays(date(2021, 1, 4), 60)
INPUTS = ShortInputs.model_validate(
    {
        "fields_version": "short_fields_v1",
        "short_volume": {
            "source": "finra-regsho-cnms",
            "files_sha256": "a" * 64,
            "files_from": "2021-01-04",
            "files_through": "2021-03-26",
            "z_window": 20,
            "min_total_volume": 10000,
            "max_offex_share": 1.5,
        },
        "corporate_actions": {
            "source": "alpaca",
            "content_sha256": "b" * 64,
            "window": ["2021-01-01", "2021-12-31"],
            "trailing_sessions": 252,
        },
    }
)
SHORT, TOTAL = 20_000.0, 50_000.0  # ratio 0.4


def _row(symbol: str, day: date, short: float, total: float) -> ShortVolumeRow:
    return ShortVolumeRow(day, symbol, short, 0.0, total, "Q")


def _rows(symbol: str, by_day: dict[date, tuple[float, float]]) -> dict[date, ShortVolumeRow]:
    return {day: _row(symbol, day, s, t) for day, (s, t) in by_day.items()}


def _loaded(by_symbol: dict[str, dict[date, ShortVolumeRow]], unscored_rows: int = 0) -> LoadedShortVolume:
    return LoadedShortVolume(
        by_symbol=by_symbol,
        files_sha256="a" * 64,
        sessions=tuple(DAYS),
        absent=(),
        rows=sum(len(v) for v in by_symbol.values()),
        unscored_rows=unscored_rows,
    )


def _build(by_symbol, symbols=("AAA",), actions=(), raw=None, unscored_rows=0):
    if raw is None:
        raw = {s: daily(DAYS, [10.0] * len(DAYS)) for s in symbols}  # Volume 1e6
    return build_short_frames(INPUTS, _loaded(by_symbol, unscored_rows), actions, raw, DAYS, list(symbols))


def _series(shorts, total=TOTAL):
    return {DAYS[i]: (s, total) for i, s in enumerate(shorts)}


def _rename(old: str, new: str, on: date) -> CorporateAction:
    return CorporateAction(
        id=f"{old}-{new}", kind="name_change", symbol=old, related_symbol=new, effective_on=on, process_date=on
    )


def _split(symbol: str, kind: str, old_rate: float, new_rate: float, on: date) -> CorporateAction:
    return CorporateAction(
        id=f"{symbol}-{kind}", kind=kind, symbol=symbol, effective_on=on, old_rate=old_rate, new_rate=new_rate
    )


def test_short_ratio_is_the_file_ratio_and_nan_on_thin_days():
    by_day = _series([SHORT] * 60)
    by_day[DAYS[5]] = (4000.0, 9_999.0)  # one share under the floor
    built = _build({"AAA": _rows("AAA", by_day)})
    f = built.frames["AAA"]
    assert list(f.columns) == list(SHORT_FIELDS)
    assert f["short_ratio"].iloc[0] == pytest.approx(0.4) and f["short_ratio"].iloc[59] == pytest.approx(0.4)
    assert np.isnan(f["short_ratio"].iloc[5])
    assert built.coverage.thin_days == 1


def test_short_ratio_z_uses_only_prior_sessions_and_needs_twenty():
    flat = _build({"AAA": _rows("AAA", _series([SHORT] * 21 + [30_000.0] + [SHORT] * 38))}).frames["AAA"]
    assert flat["short_ratio_z"].iloc[:20].isna().all()  # days 1..20: fewer than 20 prior values
    assert np.isnan(flat["short_ratio_z"].iloc[20])  # constant baseline: std 0
    assert np.isnan(flat["short_ratio_z"].iloc[21])  # baseline days 2..21 constant again
    shorts = [20_000.0 + 1000.0 * (i % 5) for i in range(60)]
    f = _build({"AAA": _rows("AAA", _series(shorts))}).frames["AAA"]
    ratios = np.array(shorts) / TOTAL
    base = ratios[1:21]  # day 22 (index 21): days 2..21 only
    expected = (ratios[21] - base.mean()) / base.std(ddof=1)
    assert f["short_ratio_z"].iloc[21] == pytest.approx(expected)
    assert f["short_ratio_z"].iloc[:20].isna().all() and np.isfinite(f["short_ratio_z"].iloc[20])


def test_short_volume_rel_is_short_over_the_prior_mean():
    f = _build({"AAA": _rows("AAA", _series([1000.0] * 20 + [1500.0] * 40))}).frames["AAA"]
    assert f["short_volume_rel"].iloc[:20].isna().all()
    assert f["short_volume_rel"].iloc[20] == pytest.approx(1.5)
    assert f["short_volume_rel"].iloc[21] == pytest.approx(1500.0 / 1025.0)  # (19 * 1000 + 1500) / 20
    zero = _build({"AAA": _rows("AAA", _series([0.0] * 20 + [1500.0] * 40))}).frames["AAA"]
    assert np.isnan(zero["short_volume_rel"].iloc[20])


def test_missing_row_is_nan_and_the_baseline_skips_it():
    shorts = [20_000.0 + 1000.0 * (i % 5) for i in range(60)]
    by_day = _series(shorts)
    del by_day[DAYS[9]]  # day 10
    f = _build({"AAA": _rows("AAA", by_day)}).frames["AAA"]
    assert np.isnan(f["short_ratio"].iloc[9]) and np.isnan(f["short_volume_rel"].iloc[9])
    assert np.isnan(f["short_ratio_z"].iloc[9])
    assert np.isnan(f["short_ratio_z"].iloc[20])  # days 1-9 and 11-20: 19 finite values
    assert np.isnan(f["short_volume_rel"].iloc[20])
    ratios = np.array(shorts) / TOTAL
    base = np.delete(ratios[:21], 9)  # day 22 (index 21): days 1-9 and 11-21
    assert len(base) == 20
    assert f["short_ratio_z"].iloc[21] == pytest.approx((ratios[21] - base.mean()) / base.std(ddof=1))
    assert f["short_volume_rel"].iloc[21] == pytest.approx(shorts[21] / np.delete(np.array(shorts[:21]), 9).mean())


def test_a_thin_day_is_skipped_by_the_ratio_baseline_but_not_the_volume_baseline():
    shorts = [20_000.0 + 1000.0 * (i % 5) for i in range(60)]
    by_day = _series(shorts)
    by_day[DAYS[9]] = (shorts[9], 5_000.0)
    f = _build({"AAA": _rows("AAA", by_day)}).frames["AAA"]
    assert np.isnan(f["short_ratio_z"].iloc[20]) and np.isfinite(f["short_ratio_z"].iloc[21])
    assert np.isfinite(f["short_volume_rel"].iloc[20])  # the thin day's short volume is still an input


def test_sparse_symbol_stays_nan_when_fewer_than_twenty_finite_values_lie_in_forty_sessions():
    by_day = {
        DAYS[i]: (20_000.0 + 1000.0 * (i % 5), TOTAL) for i in range(0, 60, 3)
    }  # a row every third day: 13 finite values per 40 sessions
    f = _build({"AAA": _rows("AAA", by_day)}).frames["AAA"]
    assert f["short_ratio_z"].isna().all() and f["short_volume_rel"].isna().all()
    assert f["short_ratio"].notna().sum() == 20


def test_the_twenty_finite_values_may_span_exactly_forty_sessions_but_not_forty_one():
    baseline = [0, *range(21, 40)]  # 20 rows; for T=40 the oldest is T-40, for T=41 it is T-41

    def frame(t: int) -> pd.DataFrame:
        by_day = {DAYS[i]: (20_000.0 + 1000.0 * (i % 5), TOTAL) for i in [*baseline, t]}
        return _build({"AAA": _rows("AAA", by_day)}).frames["AAA"]

    for name in ("short_ratio_z", "short_volume_rel"):
        assert np.isfinite(frame(40)[name].iloc[40]), name
        assert np.isnan(frame(41)[name].iloc[41]), name


def test_offex_share_above_the_cap_is_nan_and_counted():
    raw = {"AAA": daily(DAYS, [10.0] * 60)}
    raw["AAA"]["Volume"] = 100_000.0
    raw["AAA"].loc[raw["AAA"].index[2], "Volume"] = 0.0
    by_day = {DAYS[0]: (1000.0, 150_000.0), DAYS[1]: (1000.0, 160_000.0), DAYS[2]: (1000.0, 50_000.0)}
    built = _build({"AAA": _rows("AAA", by_day)}, raw=raw)
    f = built.frames["AAA"]["offex_share"]
    assert f.iloc[0] == pytest.approx(1.5)
    assert np.isnan(f.iloc[1])
    assert np.isnan(f.iloc[2])  # raw volume 0
    assert built.coverage.offex_clipped == 1
    no_raw = _build({"AAA": _rows("AAA", by_day)}, raw={})
    assert no_raw.frames["AAA"]["offex_share"].isna().all()


def test_identity_break_blanks_fields_before_the_rename_and_drops_pre_break_rows():
    shorts = [20_000.0 + 1000.0 * (i % 5) for i in range(60)]
    by_day = _rows("META", _series(shorts))
    built = _build({"META": by_day}, symbols=("META",), actions=[_rename("FB", "META", DAYS[30])])
    f = built.frames["META"]
    assert f.iloc[:30].isna().all().all()
    assert np.isfinite(f["short_ratio"].iloc[30]) and np.isfinite(f["offex_share"].iloc[30])
    # The baseline restarts at the break: 20 post-break prior values first exist at index 50.
    assert np.isnan(f["short_ratio_z"].iloc[49]) and np.isfinite(f["short_ratio_z"].iloc[50])
    ratios = np.array(shorts) / TOTAL
    expected = (ratios[50] - ratios[30:50].mean()) / ratios[30:50].std(ddof=1)
    assert f["short_ratio_z"].iloc[50] == pytest.approx(expected)
    assert built.coverage.identity_breaks == {"META": DAYS[30].isoformat()}
    assert built.coverage.first_finite["META"]["short_ratio"] == DAYS[30].isoformat()
    assert built.coverage.first_finite["META"]["short_ratio_z"] == DAYS[50].isoformat()


def test_symbol_without_rows_is_all_nan_and_reported_and_unscored_rows_are_counted():
    by_symbol = {
        "AAA": _rows("AAA", _series([SHORT] * 60)),
        "ZZZZ": _rows("ZZZZ", _series([SHORT] * 60)),
        "BFHpA": _rows("BFHpA", _series([SHORT] * 60)),
    }
    built = _build(by_symbol, symbols=("AAA", "BBB"), unscored_rows=7)
    assert built.frames["BBB"].isna().all().all()
    assert built.coverage.symbols_without_rows == ("BBB",)
    # The loader's dropped rows plus the loaded rows of names the builder does not score.
    assert built.coverage.unscored_rows == 7 + 120
    assert built.coverage.first_finite["BBB"] == dict.fromkeys(SHORT_FIELDS)


@pytest.mark.parametrize(("kind", "old_rate", "new_rate"), [("reverse_split", 10.0, 1.0), ("forward_split", 1.0, 4.0)])
def test_a_split_restarts_the_short_volume_baseline_but_not_the_ratio_baseline(kind, old_rate, new_rate):
    factor = old_rate / new_rate  # shares after the split = shares before / factor
    shorts = [(200_000.0 + 10_000.0 * (i % 5)) / (factor if i >= 30 else 1.0) for i in range(60)]
    totals = [500_000.0 / (factor if i >= 30 else 1.0) for i in range(60)]
    by_day = {DAYS[i]: (shorts[i], totals[i]) for i in range(60)}
    f = _build({"AAA": _rows("AAA", by_day)}, actions=[_split("AAA", kind, old_rate, new_rate, DAYS[30])]).frames["AAA"]
    rel = f["short_volume_rel"]
    assert np.isfinite(rel.iloc[20:30]).all()  # the pre-split baseline is untouched
    assert rel.iloc[30:50].isna().all()  # the split day and the 19 sessions after it: < 20 post-split values
    assert rel.iloc[50] == pytest.approx(shorts[50] / np.mean(shorts[30:50]))
    assert rel.iloc[50:].between(0.9, 1.1).all()
    # The ratio is scale-free: its baseline still spans the split.
    assert np.isfinite(f["short_ratio_z"].iloc[30:50]).all()
    other = _build({"AAA": _rows("AAA", by_day)}, actions=[_split("BBB", kind, old_rate, new_rate, DAYS[30])])
    assert np.isfinite(other.frames["AAA"]["short_volume_rel"].iloc[30:50]).all()  # another name's split


def test_frames_align_with_prepare_daily_index():
    built = _build({"AAA": _rows("AAA", _series([SHORT] * 60))})
    expected = pd.DatetimeIndex([pd.Timestamp(day) for day in DAYS])
    assert built.frames["AAA"].index.equals(expected)


def _write(store: ShortVolumeStore, day: date, status: str, pad: int = 1000) -> None:
    stamp = day.strftime("%Y%m%d")
    lines = ["Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"]
    lines += [f"{stamp}|S{i:04d}|10|0|100|Q" for i in range(pad)]
    lines.append(str(pad))
    body = ("\n".join(lines) + "\n").encode() if status == "present" else b""
    now = datetime(2021, 1, 5, tzinfo=UTC)
    store.save(day, body, requested_at=now, received_at=now, status=status, rows=pad if status == "present" else 0)


def _store(tmp_path, statuses: dict[date, str]) -> ShortVolumeStore:
    store = ShortVolumeStore(tmp_path / "files")
    for day, status in statuses.items():
        _write(store, day, status)
    return store


COHORT = ("S0000", "S0001", "NOROWS")


def test_load_short_volume_is_offline_skips_absent_keeps_the_symbols_and_digests(tmp_path):
    statuses = {DAYS[0]: "present", DAYS[1]: "present", DAYS[2]: "absent", DAYS[3]: "present"}
    store = _store(tmp_path, statuses)
    loaded = load_short_volume(tmp_path / "files", INPUTS.short_volume, DAYS[:4], COHORT)
    assert loaded.sessions == (DAYS[0], DAYS[1], DAYS[3]) and loaded.absent == (DAYS[2],)
    # The digest binds the absent session too.
    assert loaded.files_sha256 == files_sha256(store, DAYS[:4])
    assert loaded.files_sha256 != files_sha256(store, [DAYS[0], DAYS[1], DAYS[3]])
    assert set(loaded.by_symbol) == {"S0000", "S0001"}
    assert loaded.rows == 6 and loaded.unscored_rows == 3000 - 6
    assert sorted(loaded.by_symbol["S0000"]) == [DAYS[0], DAYS[1], DAYS[3]]


def test_load_short_volume_fails_closed_on_a_missing_session(tmp_path):
    _store(tmp_path, {DAYS[0]: "present"})
    with pytest.raises(ValueError, match=f"short-volume file for {DAYS[1].isoformat()} is missing"):
        load_short_volume(tmp_path / "files", INPUTS.short_volume, DAYS[:2], COHORT)


def test_load_short_volume_fails_closed_on_an_invalid_session(tmp_path):
    store = _store(tmp_path, {DAYS[0]: "present"})
    now = datetime(2021, 1, 5, tzinfo=UTC)
    store.save(DAYS[1], b"junk", requested_at=now, received_at=now, status="invalid", rows=0)
    with pytest.raises(ValueError, match="invalid"):
        load_short_volume(tmp_path / "files", INPUTS.short_volume, DAYS[:2], COHORT)


def test_load_short_volume_refuses_a_session_outside_the_files_window(tmp_path):
    before = date(2020, 12, 31)
    _store(tmp_path, {before: "present", DAYS[0]: "present"})
    with pytest.raises(ValueError, match="outside the protocol's files window"):
        load_short_volume(tmp_path / "files", INPUTS.short_volume, [before, DAYS[0]], COHORT)


def test_verify_short_sources_names_the_mismatching_digest():
    verify_short_sources(INPUTS, _loaded({}), "b" * 64)
    stale = LoadedShortVolume(by_symbol={}, files_sha256="c" * 64, sessions=(), absent=(), rows=0, unscored_rows=0)
    with pytest.raises(ValueError, match=r"short_inputs\.short_volume\.files_sha256"):
        verify_short_sources(INPUTS, stale, "b" * 64)
    with pytest.raises(ValueError, match=r"short_inputs\.corporate_actions\.content_sha256"):
        verify_short_sources(INPUTS, _loaded({}), "d" * 64)


def test_short_fields_are_dimensionless_dsl_fields_that_fail_closed_when_absent():
    require_dimensionless("-short_ratio_z / (ts_sum(returns, 5) > 0)")
    index = pd.date_range("2021-01-04", periods=8, freq="B")
    frame = pd.DataFrame({"Close": np.linspace(10, 11, 8)}, index=index)
    with pytest.raises(ValueError, match="Missing required fields"):
        AlphaExpressionEvaluator().evaluate("-short_ratio_z / (ts_sum(returns, 5) > 0)", frame)
