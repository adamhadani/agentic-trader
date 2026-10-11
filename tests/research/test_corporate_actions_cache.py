"""Research acquisition: one hash-named cache file per request, digest-checked on reuse, no provider on a hit."""

import json
from datetime import UTC, date, datetime

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.research.corporate_actions import fetch_corporate_actions_cached
from tests.data.test_corporate_actions import RAW


class CountingSource:
    def __init__(self):
        self.calls = 0

    def fetch(self, symbols, start, end):
        self.calls += 1
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(RAW), now, now, len(symbols), (start, end), 1, ("p",))


async def _pace():
    return None


async def test_fetch_writes_a_hash_named_file_and_reuses_it(tmp_path):
    source = CountingSource()
    args = (source, ["NVDA", "aapl", "AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path)
    actions, manifest = await fetch_corporate_actions_cached(*args, pace=_pace)
    assert len(actions) == 4 and source.calls == 1
    files = list((tmp_path / "corporate-actions").glob("*.json"))
    assert len(files) == 1 and files[0].stem == manifest["request_sha256"]
    assert manifest["count"] == 4 and manifest["counts_by_kind"]["cash_dividend"] == 1
    assert manifest["symbols"] == ["AAPL", "NVDA"] and manifest["cache_file"] == str(files[0])
    again, manifest_again = await fetch_corporate_actions_cached(*args, pace=_pace)
    assert again == actions and source.calls == 1 and manifest_again["content_sha256"] == manifest["content_sha256"]
    assert manifest_again["cached"] is True and manifest["cached"] is False


async def test_a_different_request_is_a_different_file_and_a_corrupt_cache_is_refused(tmp_path):
    source = CountingSource()
    await fetch_corporate_actions_cached(source, ["AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path, pace=_pace)
    await fetch_corporate_actions_cached(source, ["AAPL"], date(2024, 1, 1), date(2025, 12, 31), tmp_path, pace=_pace)
    files = sorted((tmp_path / "corporate-actions").glob("*.json"))
    assert len(files) == 2 and source.calls == 2
    document = json.loads(files[0].read_text())
    document["actions"][0]["rate"] = 9.99
    files[0].write_text(json.dumps(document))
    with pytest.raises(ValueError, match="does not match its recorded SHA-256"):
        await fetch_corporate_actions_cached(
            source, ["AAPL"], date(2024, 1, 1), date(2024, 12, 31), tmp_path, pace=_pace
        )
