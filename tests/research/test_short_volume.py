from datetime import UTC, date, datetime

import httpx
import pytest

from agentic_trader.research.short_volume import (
    MAX_FILE_BYTES,
    ShortVolumeStore,
    acquire_short_volume,
    files_sha256,
    parse_file,
)


NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"


def _file(day="20260916", rows=1000, extra="", trailer=True):
    return (
        HEADER
        + "".join(f"{day}|S{i:04d}|{i}|0|{2 * i + 1}|B,Q,N\n" for i in range(rows))
        + extra
        + (f"{rows + extra.count(chr(10))}\n" if trailer else "")
    )


def test_parse_file_maps_share_classes_and_keeps_fractional_volumes():
    rows = parse_file(
        _file(extra="20260916|BF/B|593898.756091|2493|918845.996289|B,Q,N\n20260916|BFHpA|1|0|2|Q\n"), date(2026, 9, 16)
    )
    by = {r.symbol: r for r in rows}
    assert by["BF.B"].short_volume == pytest.approx(593898.756091) and by["BF.B"].total_volume == pytest.approx(
        918845.996289
    )
    assert by["BF.B"].short_exempt_volume == 2493 and by["BF.B"].market == "B,Q,N"
    assert "BFHpA" in by  # kept as served; the field builder never matches it to a cohort name


def test_parse_file_rejects_rows_dated_another_day():
    with pytest.raises(ValueError, match="dated"):
        parse_file(_file(extra="20260915|AAPL|1|0|2|Q\n"), date(2026, 9, 16))


@pytest.mark.parametrize(
    "bad",
    [
        "Date|Symbol|ShortVolume\n",
        _file(rows=999),
        _file(extra="20260916|X|-1|0|2|Q\n"),
        _file(extra="20260916|X|abc|0|2|Q\n"),
        _file(extra="20260916|X|1|0\n"),
    ],
)
def test_parse_file_rejects_bad_header_short_files_and_bad_volumes(bad):
    with pytest.raises(ValueError):
        parse_file(bad, date(2026, 9, 16))


def test_store_round_trips_and_refuses_a_corrupt_file(tmp_path):
    store = ShortVolumeStore(tmp_path)
    store.save(date(2026, 9, 16), b"body", requested_at=NOW, received_at=NOW, status="present", rows=3)
    assert store.load(date(2026, 9, 16)) == "body" and store.meta(date(2026, 9, 16))["rows"] == 3
    assert store.load(date(2026, 9, 17)) is None and store.meta(date(2026, 9, 17)) is None
    store.paths(date(2026, 9, 16))[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(date(2026, 9, 16))


async def _noop():
    return None


def _client(files: dict[str, bytes | int], seen: list[str]):
    def handler(request):
        stamp = request.url.path.rsplit("CNMSshvol", 1)[1].removesuffix(".txt")
        seen.append(stamp)
        body = files.get(stamp, 403)
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


MON, TUE = date(2026, 9, 14), date(2026, 9, 15)


async def test_acquisition_requests_sessions_only_reuses_the_cache_and_digests(tmp_path):
    files = {"20260914": _file("20260914").encode(), "20260915": _file("20260915").encode()}
    seen: list[str] = []
    store = ShortVolumeStore(tmp_path)
    async with _client(files, seen) as client:
        manifest = await acquire_short_volume(client, (MON, TUE), store, pace=_noop)
        assert manifest["sessions_present"] == 2 and manifest["sessions_absent"] == []
        assert manifest["files_sha256"] == files_sha256(store, (MON, TUE))
        assert manifest["files"] == sorted(manifest["files"]) and len(manifest["files"]) == 2
        assert manifest["bytes_total"] == sum(len(b) for b in files.values())
        assert manifest["window"] == ["2026-09-14", "2026-09-15"]
        again = await acquire_short_volume(client, (MON, TUE), store, pace=_noop)
    assert again["sessions_reused"] == 2 and len(seen) == 2 and again["files_sha256"] == manifest["files_sha256"]
    assert sorted(seen) == ["20260914", "20260915"]


async def test_absent_session_fails_unless_allowed_and_is_recorded(tmp_path):
    files = {"20260914": _file("20260914").encode()}
    store = ShortVolumeStore(tmp_path)
    async with _client(files, []) as client:
        with pytest.raises(ValueError, match="absent"):
            await acquire_short_volume(client, (MON, TUE), store, pace=_noop)
        manifest = await acquire_short_volume(client, (MON, TUE), store, allow_absent=[TUE], pace=_noop)
    assert manifest["sessions_absent"] == ["2026-09-15"] and store.meta(TUE)["status"] == "absent"
    assert manifest["sessions_present"] == 1 and manifest["files"] == [f"2026-09-14:{store.meta(MON)['sha256']}"]
    assert files_sha256(store, (MON, TUE)) == manifest["files_sha256"]


async def test_invalid_file_is_kept_on_disk_and_fails(tmp_path):
    store = ShortVolumeStore(tmp_path)
    files = {"20260914": _file("20260915").encode()}
    async with _client(files, []) as client:
        with pytest.raises(ValueError, match="dated"):
            await acquire_short_volume(client, (MON,), store, pace=_noop)
    assert store.paths(MON)[0].exists() and store.meta(MON)["status"] == "invalid"


async def test_byte_budget_fails_closed(tmp_path):
    store = ShortVolumeStore(tmp_path)
    files = {"20260914": b"x" * (MAX_FILE_BYTES + 1)}
    async with _client(files, []) as client:
        with pytest.raises(ValueError, match="byte budget"):
            await acquire_short_volume(client, (MON,), store, pace=_noop)
    assert store.meta(MON) is None


async def test_server_error_raises_and_transport_error_is_retried_once(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=_file("20260914").encode())

    store = ShortVolumeStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manifest = await acquire_short_volume(client, (MON,), store, pace=_noop)
        assert manifest["sessions_present"] == 1 and len(calls) == 2
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as failing:
        with pytest.raises(httpx.HTTPStatusError):
            await acquire_short_volume(failing, (TUE,), store, pace=_noop)


def test_files_sha256_requires_meta(tmp_path):
    with pytest.raises(ValueError, match="no meta"):
        files_sha256(ShortVolumeStore(tmp_path), (MON,))


def test_parse_file_handles_crlf_and_a_matching_trailer_that_is_not_a_row():
    text = _file().replace("\n", "\r\n")
    assert text.endswith("1000\r\n")
    rows = parse_file(text, date(2026, 9, 16))
    assert len(rows) == 1000 and rows[-1].symbol == "S0999"


def test_parse_file_rejects_a_mismatching_trailer_and_accepts_no_trailer():
    bad = _file(trailer=False).replace("\n", "\r\n") + "1001\r\n"
    with pytest.raises(ValueError, match=r"trailer row count 1001 does not match 1000 data rows"):
        parse_file(bad, date(2026, 9, 16))
    assert len(parse_file(_file(trailer=False), date(2026, 9, 16))) == 1000


@pytest.mark.parametrize("content", ["not json", '{"sha256": "x"}', "[]"])
def test_store_meta_raises_value_error_on_corrupt_or_incomplete_meta(tmp_path, content):
    store = ShortVolumeStore(tmp_path)
    store.paths(MON)[1].write_text(content)
    with pytest.raises(ValueError, match="meta"):
        store.meta(MON)
