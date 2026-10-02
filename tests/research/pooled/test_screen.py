import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, load_cohort
from agentic_trader.research.pooled.screen import (
    LoadedScreenRule,
    cohort_document,
    config_group_symbols,
    exclusion_reason,
    load_screen_rule,
    screen_table,
    snapshot_candidates,
    table_csv,
)


REPO = Path(__file__).resolve().parents[3]
LOADED = load_screen_rule(REPO / "config/research/pooled/screen-v2.json")
RULE = LOADED.rule
SESSIONS = (date(2023, 12, 22), date(2023, 12, 26), date(2023, 12, 27), date(2023, 12, 28), date(2023, 12, 29))
SMALL = RULE.model_copy(update={"window_sessions": 5, "min_sessions_with_bars": 4, "top": 2})


def member(symbol, name, source="otherlisted", classification="listed_non_etf_equity_candidate"):
    return {"symbol": symbol, "classification": classification, "directory": {"Security Name": name, "source": source}}


def daily(closes, volumes, days=SESSIONS):
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in days]).tz_convert("UTC")
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=index)


def test_the_frozen_rule_pins_the_snapshot_and_the_pre_confirmation_window():
    assert RULE.snapshot.snapshot_id == "bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f"
    assert RULE.snapshot.sha256 == "41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c"
    assert RULE.snapshot.classification == "listed_non_etf_equity_candidate"
    assert RULE.window_end == date(2023, 12, 29)  # the last session before the confirmation window
    assert (RULE.window_sessions, RULE.min_sessions_with_bars, RULE.min_price, RULE.top) == (60, 55, 10.0, 400)
    assert RULE.cohort_version == 2 and RULE.config_groups == ("mega_caps", "research_cohort")
    assert RULE.excluded_fifth_letters == ("P", "Q", "R", "U", "V", "W", "Z")


@pytest.mark.parametrize(
    ("symbol", "source", "name", "reason"),
    [
        ("CMCSA", "nasdaqlisted", "Comcast Corporation - Class A Common Stock", None),
        ("GOOGL", "nasdaqlisted", "Alphabet Inc. - Class A Common Stock", None),
        ("ET", "otherlisted", "Energy Transfer LP Common Units ", None),
        (
            "AMX",
            "otherlisted",
            "America Movil, S.A.B. de C.V. American Depositary Shares (each representing the right to receive twenty)",
            None,
        ),
        (
            "BWIV.U",
            "otherlisted",
            "Blue Water Acquisition Corp. IV Units, each consisting of one Class A ordinary share",
            "unsupported symbol form",
        ),
        ("XTERW", "nasdaqlisted", "Karman Line Acquisition Corp. - Warrant", "nasdaq fifth letter W"),
        ("MNSBP", "nasdaqlisted", "MainStreet Bancshares, Inc. - Depositary Shares", "nasdaq fifth letter P"),
        (
            "ZKPW",
            "nasdaqlisted",
            "Lafayette Digital Acquisition Corp. I - Warrant",
            r"security name matches \bwarrants?\b",
        ),
        (
            "ZKPU",
            "nasdaqlisted",
            "Lafayette Digital Acquisition Corp. I - Unit",
            r"security name matches -\s*units?\.?\s*$",
        ),
        (
            "DUKU",
            "otherlisted",
            "Duke Energy Corporation Corporate Units",
            r"security name matches \bcorporate units?\b",
        ),
        (
            "GLV",
            "otherlisted",
            "Clough Global Dividend and Income Fund Common Shares of beneficial interest",
            r"security name matches \bfund\b",
        ),
        (
            "CMSD",
            "otherlisted",
            "CMS Energy Corporation 5.875% Junior Subordinated Notes due 2079",
            "security name matches %",
        ),
        (
            "PFO",
            "otherlisted",
            "Flaherty & Crumrine Preferred and Income Opportunity Fund Incorporated",
            r"security name matches \bpreferred\b",
        ),
        (
            "BHFAL",
            "nasdaqlisted",
            "Brighthouse Financial, Inc. - Junior Subordinated Debentures due 2058",
            r"security name matches \bdebentures?\b",
        ),
    ],
)
def test_exclusion_rules_drop_non_common_instruments_and_keep_units_adrs_and_classes(symbol, source, name, reason):
    assert exclusion_reason(member(symbol, name, source), RULE) == reason


def test_snapshot_candidates_keep_only_the_rule_classification_and_record_every_exclusion():
    snapshot = {
        "snapshot_id": RULE.snapshot.snapshot_id,
        "members": [
            member("MSFT", "Microsoft Corporation - Common Stock", "nasdaqlisted"),
            member("XTERW", "Karman Line Acquisition Corp. - Warrant", "nasdaqlisted"),
            member("AAPL", "Apple Inc. - Common Stock", "nasdaqlisted"),
            member("SPY", "SPDR S&P 500 ETF Trust", classification="excluded"),
        ],
    }
    kept, excluded = snapshot_candidates(snapshot, RULE)
    assert kept == ["AAPL", "MSFT"]
    assert excluded == {"XTERW": "nasdaq fifth letter W"}


def test_snapshot_candidates_refuse_another_snapshot():
    with pytest.raises(ValueError, match="snapshot_id"):
        snapshot_candidates({"snapshot_id": "0" * 64, "members": []}, RULE)


def test_screen_table_ranks_by_median_dollar_volume_and_breaks_ties_by_symbol():
    frames = {
        "AAA": daily([20.0] * 5, [1e6] * 5),
        "BBB": daily([50.0] * 5, [1e6] * 5),
        "CCC": daily([20.0] * 5, [1e6] * 5),
    }
    table = screen_table(["AAA", "BBB", "CCC"], {}, frames, SESSIONS, SMALL)
    assert list(table["symbol"]) == ["BBB", "AAA", "CCC"]
    assert list(table["rank"]) == [1, 2, 3]
    assert list(table["selected"]) == [True, True, False]
    assert table.loc[0, "median_dollar_volume"] == pytest.approx(5.0e7)


def test_screen_table_reads_only_the_window_and_records_each_exclusion():
    later = (date(2024, 1, 2),)
    frames = {
        "OUT": daily([30.0] * 6, [1e6] * 5 + [1e12], days=SESSIONS + later),  # the post-window spike is ignored
        "FEW": daily([30.0] * 3, [1e6] * 3, days=SESSIONS[:3]),
        "GAP": daily([30.0] * 4, [1e6] * 4, days=SESSIONS[:4]),
        "LOW": daily([9.0] * 5, [1e9] * 5),
    }
    table = screen_table(
        ["FEW", "GAP", "LOW", "NONE", "OUT"], {"XTERW": "nasdaq fifth letter W"}, frames, SESSIONS, SMALL
    ).set_index("symbol")
    assert table.loc["OUT", "median_dollar_volume"] == pytest.approx(3.0e7)
    assert table.loc["OUT", "rank"] == 1 and table.loc["OUT", "exclusion"] == ""
    assert table.loc["FEW", "exclusion"] == "bars on fewer than 4 sessions"
    assert table.loc["GAP", "exclusion"] == "no bar on 2023-12-29"
    assert table.loc["LOW", "exclusion"] == "close below 10"
    assert table.loc["NONE", "exclusion"] == "no bars in the screen window"
    assert table.loc["XTERW", "exclusion"] == "nasdaq fifth letter W"
    others = table.drop(index="OUT")
    assert (others["rank"] == 0).all() and not others["selected"].any()


def test_the_window_must_end_on_the_rule_date():
    with pytest.raises(ValueError, match="sessions ending 2023-12-29"):
        screen_table([], {}, {}, (*SESSIONS[:-1], date(2023, 12, 30)), SMALL)


def test_the_csv_is_byte_identical_for_the_same_table():
    frames = {"AAA": daily([20.0] * 5, [1e6] * 5)}
    first = table_csv(screen_table(["AAA"], {}, frames, SESSIONS, SMALL))
    assert first == table_csv(screen_table(["AAA"], {}, frames, SESSIONS, SMALL))
    assert first.startswith(b"symbol,exclusion,sessions_with_bars,median_dollar_volume,last_close,rank,selected\n")


def test_cohort_document_unions_the_scan_equities_and_the_screened_names():
    frames = {s: daily([20.0 + i] * 5, [1e6] * 5) for i, s in enumerate(["AAA", "BBB", "CCC"])}
    table = screen_table(["AAA", "BBB", "CCC"], {"XTERW": "nasdaq fifth letter W"}, frames, SESSIONS, SMALL)
    csv = table_csv(table)
    loaded = LoadedScreenRule(rule=SMALL, sha256="a" * 64, path=Path("screen-v2.json"))
    doc = cohort_document(
        table,
        loaded,
        rule_path="config/research/pooled/screen-v2.json",
        config_symbols=["AAPL", "BBB", "BRK.B"],
        screen_sha256=hashlib.sha256(csv).hexdigest(),
        snapshot_sha256=RULE.snapshot.sha256,
    )
    cohort = Cohort.model_validate(doc)
    assert cohort.version == 2 and cohort.survivorship == RULE.survivorship
    assert cohort.symbols == ("AAPL", "BBB", "CCC")
    assert cohort.excluded == {"BRK.B": "unsupported symbol form"}
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert screened.symbols == ("BBB", "CCC")  # top 2 by median dollar volume: CCC (22) and BBB (21)
    assert screened.identity == hashlib.sha256(csv).hexdigest()
    assert screened.screen.rule_sha256 == "a" * 64
    assert screened.screen.rule == "config/research/pooled/screen-v2.json"
    assert (screened.screen.candidates, screened.screen.excluded, screened.screen.ranked) == (4, 1, 3)
    assert cohort.source_of("BBB") == ("config_groups", "liquidity_screen")


def test_config_group_symbols_are_the_sorted_union_of_the_named_groups(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "universe:\n  groups:\n"
        "    mega_caps:\n      - {symbol: MSFT}\n      - {symbol: AAPL}\n"
        "    research_cohort:\n      - {symbol: AAPL}\n      - {symbol: ZZZ}\n"
        "    other:\n      - {symbol: QQQ}\n"
    )
    assert config_group_symbols(config, ("mega_caps", "research_cohort")) == ["AAPL", "MSFT", "ZZZ"]


def test_only_a_liquidity_screen_source_carries_screen_provenance():
    provenance = {
        "rule": "r",
        "rule_sha256": "a" * 64,
        "snapshot_id": "s",
        "snapshot_sha256": "b" * 64,
        "window_end": "2023-12-29",
        "top": 1,
        "candidates": 1,
        "excluded": 0,
        "ranked": 1,
    }
    with pytest.raises(ValidationError, match="screen provenance"):
        CohortSource(kind="liquidity_screen", description="d", identity="i", symbols=("A",))
    with pytest.raises(ValidationError, match="screen provenance"):
        CohortSource(kind="config_groups", description="d", identity="i", symbols=("A",), screen=provenance)
    source = CohortSource(kind="liquidity_screen", description="d", identity="i", symbols=("A",), screen=provenance)
    assert source.screen.top == 1


def test_cohort_v1_still_loads_with_its_frozen_hash():
    loaded = load_cohort(REPO / "config/research/pooled/cohort-v1.json")
    assert loaded.sha256 == "966b67c53ad617d698089dd5b5786b05d4d5248f8844e3326d7addbe8d4ac5a9"
    assert all(source.screen is None for source in loaded.cohort.sources)
