import dataclasses
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.cli.commands import alpha_investor_days as cmd
from agentic_trader.research.apriori.idday_parser import PARSER_VERSION, EventTable, IddayEvent, save_event_table
from agentic_trader.research.investor_days import InvestorDayStore, IssuerRecord, content_sha256
from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.events import LoadedCalendar
from agentic_trader.research.pooled.runner import cube_cache_path
from tests.research.test_investor_days import Router, tickers


UA = "Test Co test@example.com"


def _cohort(path):
    path.write_text("{}")


def _patch(monkeypatch, router, symbols):
    monkeypatch.setattr(cmd, "_client", lambda user_agent: router.client())
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    def load(path):
        cohort = SimpleNamespace(symbols=symbols[path.name])
        return SimpleNamespace(cohort=cohort)

    monkeypatch.setattr(cmd, "load_cohort", load)

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "edgar_pacer", lambda: no_pace)


def test_user_agent_never_written(tmp_path, monkeypatch):
    router = Router(tickers(("AAA", 1), ("BBB", 2)), {1: 1, 2: 1})
    _patch(monkeypatch, router, {"a.json": ["AAA"], "b.json": ["BBB", "AAA"]})
    monkeypatch.setenv("EDGAR_USER_AGENT", UA)
    for name in ("a.json", "b.json"):
        _cohort(tmp_path / name)
    args = ["--cache", str(tmp_path / "cache"), "--output", str(tmp_path / "out")]
    for name in ("a.json", "b.json"):
        args += ["--cohort", str(tmp_path / name)]
    result = CliRunner().invoke(cmd.investor_days_group, ["fetch", *args])
    assert result.exit_code == 0, result.output
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["user_agent_set"] is True and manifest["issuers"] == 2
    for root in (tmp_path / "cache", tmp_path / "out"):
        for file in root.rglob("*"):
            if file.is_file():
                assert b"test@example.com" not in file.read_bytes()
    assert "test@example.com" not in result.output
    again = CliRunner().invoke(cmd.investor_days_group, ["fetch", *args])
    assert again.exit_code == 1 and "exists" in again.output


def test_refuses_without_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    _cohort(tmp_path / "a.json")
    result = CliRunner().invoke(
        cmd.investor_days_group,
        [
            "fetch",
            "--cohort",
            str(tmp_path / "a.json"),
            "--cache",
            str(tmp_path / "c"),
            "--output",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code == 1 and "EDGAR_USER_AGENT is required" in result.output


def test_fetch_prints_progress_to_stderr_without_the_user_agent(tmp_path, monkeypatch):
    pairs = [(f"S{n:03d}", n) for n in range(1, 61)]
    router = Router(tickers(*pairs), dict.fromkeys(range(1, 61), 0))
    _patch(monkeypatch, router, {"a.json": [symbol for symbol, _ in pairs]})
    monkeypatch.setenv("EDGAR_USER_AGENT", UA)
    _cohort(tmp_path / "a.json")
    args = [
        "fetch",
        "--cohort",
        str(tmp_path / "a.json"),
        "--cache",
        str(tmp_path / "c"),
        "--output",
        str(tmp_path / "o"),
    ]
    result = CliRunner().invoke(cmd.investor_days_group, args)
    assert result.exit_code == 0, result.output
    lines = result.stderr.splitlines()
    assert [line.split(" issuers")[0] for line in lines] == ["investor days: 50/60", "investor days: 60/60"]
    assert "investor days:" not in result.stdout
    assert "test@example.com" not in result.stderr and "Test Co" not in result.stderr


# --- sample -----------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
V3_PROTOCOL = "config/research/pooled/cohort3-events-v1.json"
XU_PROTOCOL = "config/research/pooled/cohort-xu-insider-v1.json"
COHORT_SYMBOLS = {"cohort-v3.json": ("AAA", "BBB", "CCC"), "cohort-xu.json": ("INTU",)}
CUBE_V3 = "b" * 64
ENTRY_FIXTURE = REPO / "tests/research/apriori/fixtures/idday-v1-test.json"


def _weekdays(first, last):
    days, d = [], first
    while d <= last:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


def _small_cohorts(monkeypatch, symbols):
    """The real cohort files and digests, with their symbols replaced by ``symbols[file name]``."""
    real = cmd.load_cohort

    def load(path):
        return dataclasses.replace(real(path), cohort=SimpleNamespace(symbols=symbols[path.name]))

    monkeypatch.setattr(cmd, "load_cohort", load)


def _sample_setup(tmp_path, monkeypatch, *, pages_sha256=None, unmapped=(), cohort_symbols=None):
    protocol = load_campaign_protocol(REPO / V3_PROTOCOL).protocol
    cohort = load_cohort(REPO / protocol.cohort)
    spec = protocol.cube_spec()
    _small_cohorts(monkeypatch, cohort_symbols or COHORT_SYMBOLS)
    document = json.loads(ENTRY_FIXTURE.read_text())
    document["cohorts"][0]["pooled_protocol_sha256"] = hashlib.sha256((REPO / V3_PROTOCOL).read_bytes()).hexdigest()
    document["cohorts"][1]["pooled_protocol_sha256"] = hashlib.sha256((REPO / XU_PROTOCOL).read_bytes()).hexdigest()
    document["parser_check"]["sample_size"] = 2
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps(document))

    days = _weekdays(date(2024, 1, 2), date(2024, 12, 31))
    pooled = tmp_path / "pooled"
    pooled.mkdir()
    cube_cache_path(pooled, cohort, spec).write_bytes(b"")  # load_cube is patched; the file must exist
    cube = SimpleNamespace(sessions=days, spec_identity=spec.identity, cohort_sha256=cohort.sha256, sha256=CUBE_V3)
    monkeypatch.setattr(cmd, "load_cube", lambda path: cube)

    report = CalendarRow("BBB", days[100], EarningsTiming.UNSPECIFIED, None, None, None, None, None)
    digest = protocol.event_inputs.earnings.pages_sha256 if pages_sha256 is None else pages_sha256
    loaded = LoadedCalendar(rows_by_symbol={"BBB": (report,)}, pages_sha256=digest, pages=1, duplicates=0)
    monkeypatch.setattr(cmd, "load_calendar_pages", lambda directory, start, end: loaded)
    (tmp_path / "pages").mkdir()

    store = InvestorDayStore(tmp_path / "cache")
    names = {1: ("AAA",), 2: ("BBB",), 3: ("CCC",), 4: ("INTU",)}
    store.write_hits([IssuerRecord(cik, symbols, True, False, ()) for cik, symbols in names.items()], list(unmapped))
    events = []
    for cik, symbols in names.items():
        filed, held = days[50 + cik], days[65 + cik]
        events.append(
            IddayEvent(
                event_id=f"{cik}:{filed}:{held}",
                cik=cik,
                symbols=symbols,
                status="announced",
                filed=filed,
                event_date=held,
                n_announcing=1,
                event_day_material=False,
                keyword="investor day",
                sentence="Example Corp will host its investor day.",
                accession=f"0000000000-24-00000{cik}",
                url="https://example.invalid/doc.htm",
                item_2_02=False,
            )
        )
    # BBB reports inside its own hold, so only AAA and CCC remain once INTU (excluded) is left out.
    events[1] = dataclasses.replace(
        events[1], filed=days[95], event_date=days[110], event_id=f"2:{days[95]}:{days[110]}"
    )
    table = EventTable(PARSER_VERSION, content_sha256(store), tuple(events))
    save_event_table(table, tmp_path / "events.json")
    args = [
        "sample",
        "--cache",
        str(tmp_path / "cache"),
        "--events",
        str(tmp_path / "events.json"),
        "--repo-root",
        str(REPO),
        "--entry",
        str(entry),
        "--earnings-pages",
        str(tmp_path / "pages"),
        "--pooled-cache",
        str(pooled),
        "--output",
        str(tmp_path / "out" / "parser_sample.json"),
    ]
    return table, args


def test_sample_writes_the_seeded_parser_sample(tmp_path, monkeypatch):
    table, args = _sample_setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(cmd.investor_days_group, args)
    assert result.exit_code == 0, result.output
    written = json.loads((tmp_path / "out" / "parser_sample.json").read_text())
    assert written["events_sha256"] == table.events_sha256
    assert (written["seed"], written["sample_size"], written["population"]) == (20261119, 2, 2)
    assert [row["symbols"] for row in written["sample"]] == [["AAA"], ["CCC"]]
    assert written["candidate_counts"]["reasons"] == {"earnings_in_hold": 1}
    assert written["sessions"]["source"] == "cube"
    assert written["cube_sha256_v3"] == CUBE_V3
    again = CliRunner().invoke(cmd.investor_days_group, args)
    assert again.exit_code == 1 and "exists" in again.output


def test_sample_refuses_a_pages_digest_mismatch(tmp_path, monkeypatch):
    _, args = _sample_setup(tmp_path, monkeypatch, pages_sha256="0" * 64)
    result = CliRunner().invoke(cmd.investor_days_group, args)
    assert result.exit_code == 1 and "pages_sha256" in result.output
    assert not (tmp_path / "out" / "parser_sample.json").exists()


@pytest.mark.parametrize(
    ("unmapped", "cohort_symbols", "message"),
    [
        (("ZZZ",), None, r"1 extra \['ZZZ'\]"),
        ((), {**COHORT_SYMBOLS, "cohort-xu.json": ("INTU", "KMI")}, r"1 missing \['KMI'\]"),
    ],
)
def test_sample_refuses_a_store_whose_coverage_differs_from_both_cohorts(
    tmp_path, monkeypatch, unmapped, cohort_symbols, message
):
    _, args = _sample_setup(tmp_path, monkeypatch, unmapped=unmapped, cohort_symbols=cohort_symbols)
    result = CliRunner().invoke(cmd.investor_days_group, args)
    assert result.exit_code == 1 and "does not cover exactly" in result.output
    assert re.search(message, result.output)
    assert not (tmp_path / "out" / "parser_sample.json").exists()
