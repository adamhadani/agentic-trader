import json
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_short_volume as cmd
from agentic_trader.market.session import MarketCalendarDay


HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
LABOR_DAY = date(2026, 9, 7)


def _file(stamp: str) -> bytes:
    return (
        (HEADER + "".join(f"{stamp}|S{i:04d}|{i}|0|{2 * i + 1}|B,Q,N\n" for i in range(1000)) + "1000\n")
        .replace("\n", "\r\n")
        .encode()
    )


class _FakeCalendar:
    """Alpaca's exchange calendar, faked: weekdays trade except the ``closed`` dates."""

    def __init__(self, closed: set[date], requests: list[tuple[date, date]]):
        self.closed = closed
        self.requests = requests

    async def get_calendar_range(self, start: date, end: date) -> list[MarketCalendarDay]:
        self.requests.append((start, end))
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        return [
            MarketCalendarDay(
                date=day, is_trading_day=day.weekday() < 5 and day not in self.closed, is_early_close=False
            )
            for day in days
        ]


def _calendar(monkeypatch, *, closed=frozenset({LABOR_DAY}), key="k"):
    """Fake ``load_config``, the trading client and the Alpaca calendar; returns what the command touched."""
    touched = SimpleNamespace(clients=[], closed=[], requests=[])
    config = SimpleNamespace(
        alpaca_api_key=key,
        alpaca_api_secret="s",
        alpaca_paper=True,
        market_data=SimpleNamespace(timeout_seconds=5.0),
    )

    def client(*args, **kwargs):
        touched.clients.append((args, kwargs))
        return SimpleNamespace(_session=SimpleNamespace(close=lambda: touched.closed.append(True)))

    def provider(*, trading_client):
        assert trading_client is not None
        return _FakeCalendar(set(closed), touched.requests)

    monkeypatch.setattr(cmd, "load_config", lambda: config)
    monkeypatch.setattr(cmd, "BoundedTradingClient", client)
    monkeypatch.setattr(cmd, "AlpacaCalendarProvider", provider)
    return touched


def _patch(monkeypatch, seen, *, status: int = 200, closed=frozenset({LABOR_DAY})):
    def handler(request):
        stamp = request.url.path.rsplit("CNMSshvol", 1)[1].removesuffix(".txt")
        seen.append(stamp)
        return httpx.Response(200, content=_file(stamp)) if status == 200 else httpx.Response(status)

    real = httpx.AsyncClient
    monkeypatch.setattr(cmd.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "_pacer", lambda: no_pace)
    return _calendar(monkeypatch, closed=closed)


def _args(tmp_path, *extra):
    return [
        "--from",
        "2026-09-03",
        "--through",
        "2026-09-09",
        "--cache",
        str(tmp_path / "cache"),
        "--output",
        str(tmp_path / "out"),
        *extra,
    ]


def test_command_requests_alpaca_sessions_only_and_writes_manifest(tmp_path, monkeypatch):
    seen: list[str] = []
    touched = _patch(monkeypatch, seen)
    argv = ["copilot", "alpha", "short-volume", *_args(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code == 0, result.output
    # Thu 3, Fri 4, (weekend), (Mon 7 = Labor Day), Tue 8, Wed 9
    assert sorted(seen) == ["20260903", "20260904", "20260908", "20260909"]
    assert touched.requests == [(date(2026, 9, 3), date(2026, 9, 9))]
    assert len(touched.clients) == 1 and touched.closed == [True]
    assert touched.clients[0][1] == {"paper": True, "request_timeout": 5.0}
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["files_sha256"] and manifest["sessions_present"] == 4 and manifest["environment"] == {"python": "x"}
    assert manifest["allow_absent"] == [] and manifest["command"] == argv and manifest["calendar"] == "alpaca"
    assert oct((tmp_path / "out" / "manifest.json").stat().st_mode & 0o777) == "0o600"


def test_an_ad_hoc_closure_on_the_exchange_calendar_is_not_a_session(tmp_path, monkeypatch):
    """A day the offline calendar keeps (a national day of mourning) is never requested nor absent."""
    seen: list[str] = []
    _patch(monkeypatch, seen, closed=frozenset({LABOR_DAY, date(2026, 9, 8)}))
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code == 0, result.output
    assert sorted(seen) == ["20260903", "20260904", "20260909"]
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["sessions_requested"] == 3 and manifest["sessions_absent"] == []
    assert not (tmp_path / "cache" / "2026-09-08.meta.json").exists()


def test_command_refuses_without_alpaca_credentials_before_any_client_or_request(tmp_path, monkeypatch):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    touched = _calendar(monkeypatch, key="")
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code != 0 and "Alpaca credentials are required" in result.output
    assert touched.clients == [] and seen == [] and not (tmp_path / "out").exists()


def test_command_writes_the_files_directly_into_the_cache_directory(tmp_path, monkeypatch):
    _patch(monkeypatch, [])
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code == 0, result.output
    cache = tmp_path / "cache"
    assert (cache / "2026-09-03.txt").exists() and (cache / "2026-09-03.meta.json").exists()
    assert sorted(path.name for path in cache.iterdir() if path.is_dir()) == []


@pytest.mark.parametrize("day", ["2026-09-07", "2026-09-10", "2026-09-02"])
def test_command_refuses_an_allowed_absence_that_is_not_a_session_in_the_window(tmp_path, monkeypatch, day):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path, "--allow-absent", day))
    assert result.exit_code != 0
    assert f"--allow-absent {day} is not an NYSE session in" in result.output
    assert seen == [] and not (tmp_path / "out").exists()


def test_command_refuses_existing_output(tmp_path, monkeypatch):
    _patch(monkeypatch, [])
    (tmp_path / "out").mkdir()
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code != 0 and "exists" in result.output


def test_command_fails_on_absent_session_unless_allowed(tmp_path, monkeypatch):
    _patch(monkeypatch, [], status=403)
    args = ["--from", "2026-09-08", "--through", "2026-09-08", "--cache", str(tmp_path / "c"), "--output"]
    bad = CliRunner().invoke(cmd.short_volume_cmd, [*args, str(tmp_path / "o1")])
    assert bad.exit_code != 0 and "absent" in bad.output
    ok = CliRunner().invoke(cmd.short_volume_cmd, [*args, str(tmp_path / "o2"), "--allow-absent", "2026-09-08"])
    assert ok.exit_code == 0, ok.output
    manifest = json.loads((tmp_path / "o2" / "manifest.json").read_text())
    assert manifest["sessions_absent"] == ["2026-09-08"] and manifest["allow_absent"] == ["2026-09-08"]
    assert not (tmp_path / "c" / "2026-09-08.txt").exists() and (tmp_path / "c" / "2026-09-08.meta.json").exists()
