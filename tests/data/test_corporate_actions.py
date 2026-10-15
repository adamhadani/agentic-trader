"""Typed corporate actions: normalisation of every Alpaca shape, queries, the source and the lookup."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from agentic_trader.data.corporate_actions import (
    AlpacaCorporateActionsSource,
    CorporateAction,
    CorporateActionKind,
    CorporateActionsLookup,
    actions_for,
    between,
    normalize_actions,
    position_note,
)
from agentic_trader.risk import blocking_corporate_action, relative_days
from agentic_trader.transport.alpaca import BoundedCorporateActionsClient


# Public market data observed through the Alpaca data API on 2026-10-11 (ids shortened).
RAW = {
    "cash_dividends": [
        {
            "id": "div-aapl-1",
            "corporate_action_type": "cash_dividends",
            "symbol": "AAPL",
            "cusip": "037833100",
            "rate": 0.24,
            "special": False,
            "foreign": False,
            "process_date": "2024-02-15",
            "ex_date": "2024-02-09",
            "record_date": "2024-02-12",
            "payable_date": "2024-02-15",
            "due_bill_on_date": None,
            "due_bill_off_date": None,
        }
    ],
    "forward_splits": [
        {
            "id": "split-nvda",
            "corporate_action_type": "forward_splits",
            "symbol": "NVDA",
            "cusip": "67066G104",
            "new_rate": 10.0,
            "old_rate": 1.0,
            "process_date": "2024-06-10",
            "ex_date": "2024-06-10",
            "record_date": "2024-06-07",
            "payable_date": "2024-06-10",
            "due_bill_redemption_date": "2024-06-10",
        }
    ],
    "cash_mergers": [
        {
            "id": "merger-vzio",
            "corporate_action_type": "cash_mergers",
            "acquirer_symbol": "WMT",
            "acquirer_cusip": "931142103",
            "acquiree_symbol": "VZIO",
            "acquiree_cusip": "92858V101",
            "rate": 11.5,
            "process_date": "2024-12-03",
            "effective_date": "2024-12-03",
            "payable_date": None,
        }
    ],
    "stock_mergers": [
        {
            "id": "merger-pxd",
            "corporate_action_type": "stock_mergers",
            "acquirer_symbol": "XOM",
            "acquirer_cusip": "30231G102",
            "acquirer_rate": 2.3234,
            "acquiree_symbol": "PXD",
            "acquiree_cusip": "723787107",
            "acquiree_rate": 1.0,
            "process_date": "2024-05-03",
            "effective_date": "2024-05-03",
            "payable_date": "2024-05-03",
        }
    ],
}

SYNTHETIC = {
    "reverse_splits": [
        {
            "id": "rs",
            "symbol": "TINY",
            "old_cusip": "1",
            "new_cusip": "2",
            "new_rate": 1.0,
            "old_rate": 10.0,
            "process_date": "2026-01-05",
            "ex_date": "2026-01-05",
        }
    ],
    "unit_splits": [
        {
            "id": "us",
            "old_symbol": "SPACU",
            "old_cusip": "1",
            "old_rate": 1.0,
            "new_symbol": "SPAC",
            "new_cusip": "2",
            "new_rate": 1.0,
            "alternate_symbol": "SPACW",
            "alternate_cusip": "3",
            "alternate_rate": 0.5,
            "process_date": "2026-01-06",
            "effective_date": "2026-01-07",
        }
    ],
    "stock_dividends": [
        {
            "id": "sd",
            "symbol": "SDIV",
            "cusip": "1",
            "rate": 0.05,
            "process_date": "2026-01-08",
            "ex_date": "2026-01-08",
        }
    ],
    "spin_offs": [
        {
            "id": "so",
            "source_symbol": "PARENT",
            "source_cusip": "1",
            "source_rate": 1.0,
            "new_symbol": "CHILD",
            "new_cusip": "2",
            "new_rate": 0.25,
            "process_date": "2026-01-09",
            "ex_date": "2026-01-09",
        }
    ],
    "stock_and_cash_mergers": [
        {
            "id": "scm",
            "acquirer_symbol": "BIG",
            "acquirer_cusip": "1",
            "acquirer_rate": 0.5,
            "acquiree_symbol": "SMALL",
            "acquiree_cusip": "2",
            "acquiree_rate": 1.0,
            "cash_rate": 3.25,
            "process_date": "2026-01-10",
            "effective_date": "2026-01-12",
        }
    ],
    "redemptions": [{"id": "rd", "symbol": "PFD", "cusip": "1", "rate": 25.0, "process_date": "2026-01-13"}],
    "name_changes": [
        {
            "id": "nc",
            "old_symbol": "FB",
            "old_cusip": "1",
            "new_symbol": "META",
            "new_cusip": "2",
            "process_date": "2026-01-14",
        }
    ],
    "worthless_removals": [{"id": "wr", "symbol": "GONE", "cusip": "1", "process_date": "2026-01-15"}],
    "rights_distributions": [
        {
            "id": "rt",
            "source_symbol": "BRK.B",
            "source_cusip": "1",
            "new_symbol": "BRK.R",
            "new_cusip": "2",
            "rate": 0.1,
            "process_date": "2026-01-16",
            "ex_date": "2026-01-16",
            "payable_date": "2026-02-01",
        }
    ],
}


def test_normalises_observed_shapes_sorted_by_effective_date():
    actions = normalize_actions(RAW)
    assert [a.id for a in actions] == ["div-aapl-1", "merger-pxd", "split-nvda", "merger-vzio"]
    div, pxd, nvda, vzio = actions
    assert (div.kind, div.symbol, div.effective_on, div.rate, div.payable_date) == (
        CorporateActionKind.CASH_DIVIDEND,
        "AAPL",
        date(2024, 2, 9),
        0.24,
        date(2024, 2, 15),
    )
    assert (nvda.kind, nvda.effective_on, nvda.old_rate, nvda.new_rate) == (
        CorporateActionKind.FORWARD_SPLIT,
        date(2024, 6, 10),
        1.0,
        10.0,
    )
    assert nvda.price_factor() == pytest.approx(0.1)
    assert (vzio.symbol, vzio.related_symbol, vzio.cash_rate, vzio.effective_on) == (
        "VZIO",
        "WMT",
        11.5,
        date(2024, 12, 3),
    )
    assert (pxd.symbol, pxd.related_symbol, pxd.old_rate, pxd.new_rate) == ("PXD", "XOM", 1.0, 2.3234)
    assert not div.is_structural and nvda.is_structural and vzio.is_structural and pxd.is_structural


def test_normalises_every_other_shape_and_describes_it():
    by_kind = {a.kind: a for a in normalize_actions(SYNTHETIC)}
    assert len(by_kind) == 9
    assert by_kind[CorporateActionKind.REVERSE_SPLIT].describe() == "reverse split 1-for-10"
    assert by_kind[CorporateActionKind.REVERSE_SPLIT].price_factor() == pytest.approx(10.0)
    assert (by_kind[CorporateActionKind.UNIT_SPLIT].symbol, by_kind[CorporateActionKind.UNIT_SPLIT].effective_on) == (
        "SPACU",
        date(2026, 1, 7),
    )
    assert by_kind[CorporateActionKind.STOCK_DIVIDEND].price_factor() == pytest.approx(1 / 1.05)
    assert by_kind[CorporateActionKind.STOCK_DIVIDEND].describe() == "stock dividend 0.05/share"
    assert by_kind[CorporateActionKind.SPIN_OFF].describe() == "spin-off of CHILD (0.25 per 1)"
    assert by_kind[CorporateActionKind.STOCK_AND_CASH_MERGER].describe() == (
        "stock-and-cash merger into BIG (0.5 per 1 plus $3.25)"
    )
    assert by_kind[CorporateActionKind.REDEMPTION].describe() == "redemption at $25.00"
    assert by_kind[CorporateActionKind.NAME_CHANGE].describe() == "name change to META"
    assert by_kind[CorporateActionKind.NAME_CHANGE].effective_on == date(2026, 1, 14)
    assert by_kind[CorporateActionKind.WORTHLESS_REMOVAL].describe() == "worthless removal"
    assert by_kind[CorporateActionKind.RIGHTS_DISTRIBUTION].describe() == "rights distribution of BRK.R (0.1 per share)"
    assert normalize_actions(RAW)[0].describe() == "cash dividend $0.24/share"
    assert normalize_actions(RAW)[2].describe() == "forward split 10-for-1"


def test_dotted_symbols_round_trip():
    action = normalize_actions(SYNTHETIC)[-1]
    assert action.symbol == "BRK.B" and action.related_symbol == "BRK.R"
    assert actions_for(normalize_actions(SYNTHETIC), "brk.b") == (action,)


def test_accepts_date_objects_and_rejects_bad_input():
    typed = {
        "cash_dividends": [{**RAW["cash_dividends"][0], "ex_date": date(2024, 2, 9), "process_date": date(2024, 2, 15)}]
    }
    assert normalize_actions(typed)[0].effective_on == date(2024, 2, 9)
    with pytest.raises(ValueError, match="unknown corporate-action type 'buybacks'"):
        normalize_actions({"buybacks": [{"id": "x", "symbol": "A", "process_date": "2026-01-01"}]})
    with pytest.raises(ValueError, match="forward_split.*ex_date"):
        normalize_actions({"forward_splits": [{**RAW["forward_splits"][0], "ex_date": None}]})
    with pytest.raises(ValueError, match="cash_dividend.*rate"):
        normalize_actions({"cash_dividends": [{k: v for k, v in RAW["cash_dividends"][0].items() if k != "rate"}]})
    with pytest.raises(ValueError, match="cash_dividend.*id"):
        normalize_actions({"cash_dividends": [{k: v for k, v in RAW["cash_dividends"][0].items() if k != "id"}]})
    assert normalize_actions({}) == ()


def test_malformed_dates_name_kind_and_field_and_duplicates_collapse_by_id():
    with pytest.raises(ValueError, match="forward_split: malformed ex_date") as excinfo:
        normalize_actions({"forward_splits": [{**RAW["forward_splits"][0], "ex_date": "2024-13-99"}]})
    assert "2024-13-99" not in str(excinfo.value)
    with pytest.raises(ValueError, match="cash_dividend: malformed payable_date"):
        normalize_actions({"cash_dividends": [{**RAW["cash_dividends"][0], "payable_date": "soon"}]})
    twice = {"stock_mergers": [RAW["stock_mergers"][0], {**RAW["stock_mergers"][0]}]}
    assert [a.id for a in normalize_actions(twice)] == ["merger-pxd"]


def _action(kind, symbol, effective, **extra):
    base = {"id": f"{kind}-{symbol}-{effective}", "kind": kind, "symbol": symbol, "effective_on": effective}
    if kind in (CorporateActionKind.FORWARD_SPLIT, CorporateActionKind.REVERSE_SPLIT):
        base.update(old_rate=1.0, new_rate=2.0)
    if kind == CorporateActionKind.CASH_DIVIDEND:
        base.update(rate=0.5)
    return CorporateAction.model_validate({**base, **extra})


TODAY = date(2026, 10, 12)


def test_queries_between_and_blocking():
    split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY + timedelta(days=3))
    old_split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY - timedelta(days=6))
    dividend = _action(CorporateActionKind.CASH_DIVIDEND, "NVDA", TODAY + timedelta(days=1))
    other = _action(CorporateActionKind.FORWARD_SPLIT, "AAPL", TODAY)
    actions = (old_split, dividend, split, other)
    assert between(actions, "NVDA", TODAY, TODAY + timedelta(days=7)) == (dividend, split)
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=7, post_days=5) == split
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=2, post_days=5) is None  # dividend never blocks
    assert blocking_corporate_action(actions, "NVDA", TODAY, pre_days=2, post_days=6) == old_split
    assert blocking_corporate_action(actions, "AAPL", TODAY, pre_days=0, post_days=0) == other
    assert blocking_corporate_action(actions, "MSFT", TODAY, pre_days=30, post_days=30) is None


def test_relative_days_and_position_notes():
    assert relative_days(TODAY, TODAY) == "today"
    assert relative_days(TODAY + timedelta(days=1), TODAY) == "in 1 day"
    assert relative_days(TODAY - timedelta(days=3), TODAY) == "3 days ago"
    dividend = _action(
        CorporateActionKind.CASH_DIVIDEND,
        "XOM",
        TODAY + timedelta(days=2),
        rate=0.99,
        payable_date=TODAY + timedelta(days=30),
    )
    assert (
        position_note(dividend, TODAY) == "XOM: cash dividend $0.99/share, ex 2026-10-14 (in 2 days), pays 2026-11-11."
    )
    split = _action(CorporateActionKind.FORWARD_SPLIT, "NVDA", TODAY - timedelta(days=3), old_rate=1.0, new_rate=10.0)
    assert position_note(split, TODAY) == (
        "NVDA: forward split 10-for-1 effective 2026-10-09 (3 days ago); tracked quantity, stop and target predate it until reconciled."
    )


class FakeClient:
    """A raw-data corporate-actions client: records requests, replays canned pages."""

    def __init__(self, pages_by_chunk, *, fail_on=None):
        self.pages_by_chunk, self.fail_on, self.requests = pages_by_chunk, fail_on, []
        self.observer = None

    @contextmanager
    def observe_responses(self, observer):
        self.observer = observer
        try:
            yield
        finally:
            self.observer = None

    def get_corporate_actions(self, request):
        self.requests.append(request)
        key = tuple(request.symbols)
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("provider down")
        page = self.pages_by_chunk[key]
        self.observer(SimpleNamespace(response={"corporate_actions": page, "next_page_token": None}))
        return page


def test_source_chunks_symbols_digests_pages_and_merges():
    client = FakeClient(
        {
            ("AAPL", "NVDA"): {"cash_dividends": RAW["cash_dividends"], "forward_splits": RAW["forward_splits"]},
            ("PXD",): {"stock_mergers": RAW["stock_mergers"]},
        }
    )
    source = AlpacaCorporateActionsSource(client, chunk_size=2)
    page = source.fetch(["nvda", "AAPL", "PXD", "AAPL"], date(2024, 1, 1), date(2024, 12, 31))
    assert [tuple(r.symbols) for r in client.requests] == [("AAPL", "NVDA"), ("PXD",)]
    assert (client.requests[0].start, client.requests[0].end) == (date(2024, 1, 1), date(2024, 12, 31))
    assert client.requests[0].limit is None  # any limit caps the whole chunk in alpaca-py
    assert [a.id for a in page.actions] == ["div-aapl-1", "merger-pxd", "split-nvda"]
    assert page.pages == 2 and len(page.page_sha256) == 2 and len(set(page.page_sha256)) == 2
    assert page.symbols == 3 and page.window == (date(2024, 1, 1), date(2024, 12, 31))
    assert page.requested_at <= page.received_at


def test_source_follows_every_page_of_a_chunk_through_the_real_sdk(monkeypatch):
    """alpaca-py caps the whole aggregated result at ``limit``; with none it follows ``next_page_token``.

    The real bounded client runs end to end (SDK pagination, the response observer); only the HTTP
    session is replaced, so nothing reaches a socket.
    """
    template = RAW["cash_dividends"][0]
    rows = [{**template, "id": f"div-{n}", "symbol": f"S{n:04d}"} for n in range(2500)]
    pages = [(rows[:1000], "t1"), (rows[1000:2000], "t2"), (rows[2000:], None)]
    client = BoundedCorporateActionsClient(api_key="k", secret_key="s", request_timeout=1, raw_data=True)
    calls: list[tuple[str, str, dict]] = []
    seen: list = []
    sdk_get_corporate_actions = client.get_corporate_actions

    def get_corporate_actions(request):
        seen.append(request)
        return sdk_get_corporate_actions(request)

    def session_request(method, url, **opts):
        calls.append((method, urlsplit(url).path, dict(opts["params"])))
        chunk, token = pages[len(calls) - 1]
        body = {"corporate_actions": {"cash_dividends": chunk}, "next_page_token": token}
        return SimpleNamespace(raise_for_status=lambda: None, status_code=200, text=json.dumps(body), json=lambda: body)

    monkeypatch.setattr(client, "get_corporate_actions", get_corporate_actions)
    monkeypatch.setattr(client._session, "request", session_request)
    source = AlpacaCorporateActionsSource(client)
    page = source.fetch(["aapl"], date(2024, 1, 1), date(2024, 12, 31))
    assert len(seen) == 1 and seen[0].limit is None and seen[0].symbols == ["AAPL"]
    assert [(method, path) for method, path, _ in calls] == [("GET", "/v1/corporate-actions")] * 3
    assert [params["page_token"] for _, _, params in calls] == [None, "t1", "t2"]
    assert {params["limit"] for _, _, params in calls} == {1000}  # the SDK's page size, not a total cap
    assert len(page.actions) == 2500 and {a.id for a in page.actions} == {row["id"] for row in rows}
    assert page.pages == 3 and len(set(page.page_sha256)) == 3


def test_source_fails_whole_fetch_on_a_failed_chunk_and_skips_empty_requests():
    client = FakeClient({("AAPL",): {}}, fail_on="PXD")
    source = AlpacaCorporateActionsSource(client, chunk_size=1)
    with pytest.raises(RuntimeError):
        source.fetch(["AAPL", "PXD"], date(2024, 1, 1), date(2024, 1, 31))
    empty = source.fetch([], date(2024, 1, 1), date(2024, 1, 31))
    assert empty.actions == () and empty.pages == 0 and len(client.requests) == 2


def test_lookup_freshness():
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    actions = [a.model_dump(mode="json") for a in normalize_actions(RAW)]
    capture = {"content_sha256": "abc", "as_of": "2026-10-12", "actions": actions, "symbols": ["AAPL", "nvda"]}
    check = {"checked_at": (now - timedelta(hours=1)).isoformat(), "as_of": "2026-10-12", "content_sha256": "abc"}
    fresh = CorporateActionsLookup.build(capture, check, now=now, max_age_seconds=3600 * 4)
    assert fresh.status == "ok" and fresh.as_of == date(2026, 10, 12) and len(fresh.actions) == 4
    assert fresh.symbols == frozenset({"AAPL", "NVDA"})
    assert fresh.covers("aapl") is True and fresh.covers(" NVDA ") is True and fresh.covers("PXD") is False
    without_symbols = {key: value for key, value in capture.items() if key != "symbols"}
    legacy = CorporateActionsLookup.build(without_symbols, check, now=now, max_age_seconds=3600 * 4)
    assert legacy.status == "ok" and legacy.symbols == frozenset() and legacy.covers("AAPL") is False
    stale = CorporateActionsLookup.build(
        capture, {**check, "checked_at": (now - timedelta(days=5)).isoformat()}, now=now, max_age_seconds=3600
    )
    assert stale.status == "stale" and stale.as_of == date(2026, 10, 12) and stale.actions == ()
    assert stale.symbols == frozenset()
    assert CorporateActionsLookup("ok", None, ()).symbols == frozenset()  # three positional fields still construct
    assert CorporateActionsLookup.build(None, None, now=now, max_age_seconds=3600).status == "unavailable"
    assert (
        CorporateActionsLookup.build(
            capture, {**check, "content_sha256": "other"}, now=now, max_age_seconds=3600
        ).status
        == "unavailable"
    )
    future = {**check, "checked_at": (now + timedelta(hours=1)).isoformat()}
    assert CorporateActionsLookup.build(capture, future, now=now, max_age_seconds=3600).status == "stale"


REORG = {
    "id": "reorg-aaa",
    "symbol": "aaa",
    "cusip": "813921103",
    "cash_rate": 3.57426787,
    "effective_date": "2026-09-01",
    "payable_date": "2026-09-08",
    "process_date": "2026-09-08",
    "stock_movements": [{"cusip": "36168Q104", "new_rate": 0.3356, "source_rate": 1, "symbol": "bbb"}],
}


def test_reorganization_uses_first_stock_movement():
    (action,) = normalize_actions({"reorganizations": [REORG]})
    assert action.kind == CorporateActionKind.REORGANIZATION
    assert (action.symbol, action.related_symbol) == ("AAA", "BBB")
    assert action.effective_on == date(2026, 9, 1)
    assert action.payable_date == date(2026, 9, 8) and action.process_date == date(2026, 9, 8)
    assert (action.cash_rate, action.old_rate, action.new_rate) == (3.57426787, 1.0, 0.3356)
    assert action.is_structural and action.price_factor() is None
    assert "reorganization" in action.describe() and "BBB" in action.describe()


def test_reorganization_optional_cash_and_movements():
    stock_only = {k: v for k, v in REORG.items() if k != "cash_rate"}
    (action,) = normalize_actions({"reorganizations": [stock_only]})
    assert action.cash_rate is None and action.new_rate == 0.3356
    bare = {k: v for k, v in REORG.items() if k not in ("cash_rate", "stock_movements")}
    (action,) = normalize_actions({"reorganizations": [bare]})
    assert action.related_symbol is None and action.old_rate is None and action.new_rate is None
    assert action.describe() == "reorganization"
    no_symbol = {**REORG, "stock_movements": [{"new_rate": 0.3356, "source_rate": 1}]}
    (action,) = normalize_actions({"reorganizations": [no_symbol]})
    assert "?" not in action.describe() and "per 1" not in action.describe()


def test_reorganization_with_several_movements_is_refused():
    movements = [*REORG["stock_movements"], {"symbol": "CCC", "new_rate": 1, "source_rate": 1}]
    with pytest.raises(ValueError, match="reorg-aaa"):
        normalize_actions({"reorganizations": [{**REORG, "stock_movements": movements}]})
    with pytest.raises(ValueError, match="reorg-aaa.*new_rate"):
        normalize_actions({"reorganizations": [{**REORG, "stock_movements": [{"symbol": "BBB", "source_rate": 1}]}]})


def test_reorganization_with_malformed_movements_is_refused():
    with pytest.raises(ValueError, match="reorg-aaa.*not a list"):
        normalize_actions({"reorganizations": [{**REORG, "stock_movements": {"symbol": "BBB"}}]})
    with pytest.raises(ValueError, match="reorg-aaa.*not a mapping"):
        normalize_actions({"reorganizations": [{**REORG, "stock_movements": ["BBB"]}]})
