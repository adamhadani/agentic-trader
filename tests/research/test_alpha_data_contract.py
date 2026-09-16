import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.research.alpha.data import completed_bars, load_dataset, save_dataset, save_json_report
from agentic_trader.research.alpha.validation import frame_digest


@pytest.mark.parametrize(("timeframe", "periods", "expected"), [("15m", 5, 4), ("1h", 5, 1), ("4h", 5, 0)])
def test_forming_bar_is_excluded(timeframe, periods, expected):
    frame = pd.DataFrame(
        {"close": range(periods)}, index=pd.date_range("2026-09-16T10:00Z", periods=periods, freq="15min")
    )
    assert len(completed_bars(frame, timeframe, as_of=datetime(2026, 9, 16, 11, tzinfo=UTC))) == expected


def test_dataset_round_trip_retains_exact_observations(tmp_path):
    frame = pd.DataFrame(
        {"close": [100.123456789012345, 101.2345678912345], "volume": [123, 567]},
        index=pd.date_range("2020-01-01", periods=2, tz="UTC"),
    )
    frame.attrs["timeframe"] = "1d"
    digest = frame_digest(frame)
    saved = save_dataset(frame, tmp_path, digest)
    restored = load_dataset(saved)
    assert frame_digest(restored) == digest
    assert restored.attrs == frame.attrs


def test_diagnostic_report_is_private_and_never_overwrites_evidence(tmp_path):
    path = tmp_path / "research" / "report.json"
    save_json_report({"result": "original"}, path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        save_json_report({"result": "replacement"}, path)
    assert json.loads(path.read_text()) == {"result": "original"}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_invalid_report_is_not_published(tmp_path, invalid):
    path = tmp_path / "research" / "report.json"
    with pytest.raises((TypeError, ValueError)):
        save_json_report({"value": invalid}, path)
    assert not path.parent.exists()
