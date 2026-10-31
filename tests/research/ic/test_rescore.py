import dataclasses
import gc
import hashlib
import inspect
import json
import math
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from agentic_trader.cli.commands import alpha_ic
from agentic_trader.cli.commands.alpha import _pooled_protocol
from agentic_trader.cli.commands.alpha_ic import ic_group
from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.ic import rescore as rescore_module, statistic as statistic_module
from agentic_trader.research.ic.arrays import build_ic_arrays, truncate_bars
from agentic_trader.research.ic.rescore import (
    LoadedRescore,
    RescoreBatch,
    batch_verdict,
    execute_rescore,
    load_rescore,
    read_pairs,
    rehearse_batch,
)
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.ic.statistic import ic_inference, rank_ic_series
from agentic_trader.research.pooled.campaign import CampaignProtocol, DiscoveryEvaluator, load_campaign_protocol
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.research.pooled.events import EVENT_FIELDS, EventCoverage, EventFrames
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook, score_book
from agentic_trader.research.pooled.short_fields import SHORT_FIELDS, ShortCoverage, ShortFrames
from agentic_trader.storage.db import SignalDatabase
from tests.research.ic.world import (
    HOURLY_DAYS,
    LONG_DAYS,
    World,
    as_builder,
    environment,
    first_hour_world,
    long_world,
    poisoned,
    poisoned_world,
    world_sectors,
)


SHIPPED = REPO_ROOT / "config" / "research" / "ic" / "rescore-v1.json"
COMPOSITE = REPO_ROOT / "config" / "research" / "ic" / "composite-ic-v1.json"
POOLED = REPO_ROOT / "config" / "research" / "pooled"
SECTOR_MAP_SHA256 = "56bc70fb52ce541aa2e4292673e81ebbc42df18bcf53484e6907cb52d4689574"
DAILY_FAMILIES = ("reversal_5", "signed_volume", "abnormal_volume")
EXPECTED_BATCHES = {
    "campaign-v4": (
        "a72f2e65378f2f49e9c6d7b6c38079fbbed1f673017ebcbbd074874a96270f0d",
        "pooled-campaign-v4-20261007",
        "136dc76125be875eb48d2d340d350b52769a4fdd8d935f891187afa51b7d65b2",
        "daily",
        0,
    ),
    "intraday-v1": (
        "5b4c3fdf23aa180039aecd97cb4d067e22ffb0302e858aaebb905a54a22a7d5d",
        "pooled-campaign-intraday-v1-20261014",
        "0cb341b7215f32799bf27c37bbbedb8de63e423ad620556fce9ab8d77f1bb725",
        "hourly",
        1,
    ),
    "events-v1": (
        "4e156fd8dcb45064a042505433260bd59aa628357106abaad145ccd24988d73b",
        "pooled-campaign-events-v1-20261015",
        "8ea4a5eeadca95d2f01d44efc56dd3c211182712f2de78c05b043a40808fe05c",
        "daily",
        0,
    ),
    "short-v1": (
        "4be5bf3a584d73343b7e9c145dfe13c1fc3d866141c87b91e6deb668ae88e587",
        "pooled-campaign-short-v1-20261020",
        "34a352d27b7ae6b97e5219159975b0fa28af3bd9d045fac8d4d80607ff6c106c",
        "daily",
        0,
    ),
    "cohort3-daily-v1": (
        "002b0ac27e2912105d8020495aabdf3e879c6bbc8bfa77665de45e297f290574",
        "pooled-campaign-cohort3-daily-v1-20261016",
        "c8afd90c5da9b0a62262f2441d83e860289fa25908c3a82672706bc827aedebb",
        "daily",
        0,
    ),
    "cohort3-intraday-v1": (
        "e6cda73e2c157a184b9c17beb00a0432d36aa0fac3fdbb2d6130c8a2ef14f124",
        "pooled-campaign-cohort3-intraday-v1-20261016",
        "089ef40a6941cc99197db94db9126df9aec3a3d30994d6e09ae8e2e98a235fe0",
        "hourly",
        1,
    ),
    "cohort3-events-v1": (
        "b692d7f9a1ec1d797542f710a69aa2f7556e72873e29f571f4bf946a37243843",
        "pooled-campaign-cohort3-events-v1-20261016",
        "bb8805b6332adb3ec2ca39627e33bf4e4535886a8673baf5e963a6ea42c548cb",
        "daily",
        0,
    ),
    "cohort3-short-v1": (
        "817076247520a85ff7944857e6060c41bcad28ad63a95fa2618f10910ee7a633",
        "pooled-campaign-cohort3-short-v1-20261020",
        "d8cd33fb0cd9d76cba9f05abe7846fa2f5a112b89ff769865e770eb437a7a065",
        "daily",
        0,
    ),
}


def labelled_world() -> World:
    return long_world(labels_seed=5)


def _stages(days: tuple[date, ...]) -> dict:
    """Discovery, selection and confirmation over the later part of a test calendar, as a protocol's window."""
    cuts = (260, 400, 401, 500, 510, 540) if days is LONG_DAYS else (100, 200, 201, 300, 310, 330)
    d = [days[cut].isoformat() for cut in cuts]
    return {"discovery": d[0:2], "selection": d[2:4], "confirmation": d[4:6]}


def _protocol_document(base: str, families: tuple[str, ...], days: tuple[date, ...]) -> dict:
    """A shipped fixed set, narrowed to a few formulas on a test calendar; its class and its validators are the real ones."""
    document = json.loads((POOLED / f"{base}.json").read_text())
    document["families"] = [family for family in document["families"] if family["id"] in families]
    document["formula_budget"] = len(document["families"])
    document["bars_from"] = days[0].isoformat()
    document["bars_through"] = days[-1].isoformat()
    document["windows"] = _stages(days)
    document["bootstrap"]["discovery_draws"] = 100
    return document


def _record(protocol: CampaignProtocol, built: CubeBuild, book: ScoreBook, family) -> dict:
    expression = canonical_expression(family.seeds[0])
    formula = book.formula(expression)
    edge = DiscoveryEvaluator(built.cube.window(*protocol.windows.discovery), protocol).score(formula).row["edge"]
    return {
        "edge_mean": edge["mean"],
        "edge_t": edge["t"],
        "expression": expression,
        "family": family.id,
        "formula_id": formula.formula_id,
        "n_sessions": int(edge["n_sessions"]),
        "status": "evaluated",
    }


def campaign_records(protocol: CampaignProtocol, built: CubeBuild) -> list[dict]:
    """What a campaign wrote: ``DiscoveryEvaluator``'s own rows, on a build that was never truncated."""
    book = score_book(protocol, built)
    return [_record(protocol, built, book, family) for family in protocol.families]


@lru_cache(maxsize=2)
def _daily_records() -> tuple[str, ...]:
    document = _protocol_document("cohort3-daily-v1", DAILY_FAMILIES, LONG_DAYS)
    protocol = CampaignProtocol.model_validate(document)
    return tuple(json.dumps(row) for row in campaign_records(protocol, labelled_world().build))


@dataclass
class Spec:
    id: str = "daily"
    base: str = "cohort3-daily-v1"
    families: tuple[str, ...] = DAILY_FAMILIES
    world: World | None = None
    score: str = "daily"
    entry_delay: int = 0
    tamper: Callable[[list[dict]], None] | None = None


@dataclass
class Scene:
    root: Path
    path: Path
    loaded: LoadedRescore
    sectors: LoadedSectors
    builders: dict


def _write_batch(root: Path, spec: Spec) -> dict:
    """The batch's protocol and campaign files under ``root``, and its row for the frozen file."""
    world = spec.world or labelled_world()
    days = tuple(world.build.trading_days)
    protocol_path = root / "protocols" / f"{spec.id}.json"
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_text(json.dumps(_protocol_document(spec.base, spec.families, days)))
    pinned = load_campaign_protocol(protocol_path)
    if spec.base == "cohort3-daily-v1" and spec.families == DAILY_FAMILIES and world is labelled_world():
        rows = [json.loads(row) for row in _daily_records()]
    else:
        rows = campaign_records(pinned.protocol, world.build)
    if spec.tamper is not None:
        spec.tamper(rows)
    campaign = root / f"campaign-{spec.id}"
    campaign.mkdir()
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    (campaign / "formulas.jsonl").write_bytes(body)
    return {
        "id": spec.id,
        "protocol": str(protocol_path),
        "protocol_sha256": pinned.sha256,
        "campaign_dir": campaign.name,
        "formulas_sha256": hashlib.sha256(body).hexdigest(),
        "score": spec.score,
        "entry_delay": spec.entry_delay,
    }


def _scene(root: Path, specs: list[Spec] | None = None, *, write_sectors: bool = False) -> Scene:
    specs = specs if specs is not None else [Spec()]
    worlds = {id(spec.world or labelled_world()): spec.world or labelled_world() for spec in specs}
    sectors = world_sectors(*worlds.values())
    root.mkdir(parents=True, exist_ok=True)
    sectors_path = "config/research/ic/sectors-v1.json"
    if write_sectors:
        target = root / "sectors.json"
        target.write_text(json.dumps(sectors.document))
        sectors = LoadedSectors(document=sectors.document, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        sectors_path = str(target)
    document = {
        "id": "ic-rescore",
        "version": 1,
        "rationale": "test",
        "sectors_path": sectors_path,
        "sectors_sha256": sectors.sha256,
        "horizon": 20,
        "min_names": 30,
        "bootstrap": {"draws": 100, "seed": 3},
        "edge_statistic_reference": {"t_mean": -0.06, "t_sd": 1.25},
        "known_families": ["reversal_5"],
        "batches": [_write_batch(root, spec) for spec in specs],
    }
    path = root / "rescore.json"
    path.write_text(json.dumps(document))
    builders = {spec.id: as_builder(spec.world or labelled_world()) for spec in specs}
    return Scene(root, path, load_rescore(path), sectors, builders)


async def _run(scene: Scene, output: Path, **overrides) -> dict:
    arguments = {
        "research_root": scene.root,
        "builders": scene.builders,
        "sectors": scene.sectors,
        "environment": environment(),
        "progress": None,
    }
    return await execute_rescore(scene.loaded, output, **(arguments | overrides))


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _pairs(output: Path) -> list[dict]:
    return [json.loads(line) for line in (output / "pairs.jsonl").read_text().splitlines()]


def _batch(output: Path, batch_id: str) -> dict:
    return next(entry for entry in _json(output / "batches.json")["batches"] if entry["id"] == batch_id)


def _pair(discovery: float, selection: float) -> dict:
    def window(mean: float) -> dict:
        return {"mean": mean, "n_sessions": 0 if math.isnan(mean) else 100}

    return {"discovery": window(discovery), "selection": window(selection)}


def _never(*_args, **_kwargs):
    raise AssertionError("a refused or failed rehearsal reads no IC")


def _forbid_ic(monkeypatch) -> None:
    for module, name in (
        (rescore_module, "rank_ic_series"),
        (rescore_module, "ic_inference"),
        (rescore_module, "tear_sheet"),
        (rescore_module, "build_ic_arrays"),
        (statistic_module, "rank_ic_series"),
    ):
        monkeypatch.setattr(module, name, _never)


def test_shipped_rescore_file_has_eight_batches_and_entry_delay_follows_score():
    loaded = load_rescore(SHIPPED)
    protocol = loaded.protocol
    assert loaded.sha256 == hashlib.sha256(SHIPPED.read_bytes()).hexdigest()
    assert {
        batch.id: (batch.protocol_sha256, batch.campaign_dir, batch.formulas_sha256, batch.score, batch.entry_delay)
        for batch in protocol.batches
    } == EXPECTED_BATCHES
    assert [batch.id for batch in protocol.batches] == list(EXPECTED_BATCHES)
    assert protocol.sectors_sha256 == SECTOR_MAP_SHA256 == _json(COMPOSITE)["sectors"]["sha256"]
    assert hashlib.sha256((REPO_ROOT / protocol.sectors_path).read_bytes()).hexdigest() == SECTOR_MAP_SHA256
    assert protocol.horizon == 20
    assert protocol.min_names == _json(COMPOSITE)["min_names"]
    assert protocol.bootstrap.draws == _json(COMPOSITE)["bootstrap"]["draws"]
    assert {"shorts_retreat", "quiet_momentum"} <= set(protocol.known_families)
    budget = 0
    for batch in protocol.batches:
        pooled = load_campaign_protocol(REPO_ROOT / batch.protocol)
        assert pooled.sha256 == batch.protocol_sha256
        assert pooled.protocol.score_inputs == batch.score
        assert batch.entry_delay == (1 if batch.score == "hourly" else 0)
        budget += pooled.protocol.formula_budget
    assert budget == 64

    inputs = {batch.id: batch.inputs for batch in protocol.batches}
    v2_actions = "corporate-actions-cohort-v2-20261014/actions.json"
    v3_actions = "corporate-actions-cohort-v3-20261015/actions.json"
    assert inputs["events-v1"].earnings_pages == inputs["cohort3-events-v1"].earnings_pages
    assert inputs["events-v1"].earnings_pages == "apriori-pead-v1-cache/nasdaq"
    assert inputs["short-v1"].short_volume == inputs["cohort3-short-v1"].short_volume
    assert inputs["short-v1"].short_volume == "short-volume-cache/short-volume"
    assert inputs["events-v1"].corporate_actions == inputs["short-v1"].corporate_actions == v2_actions
    assert inputs["cohort3-events-v1"].corporate_actions == inputs["cohort3-short-v1"].corporate_actions == v3_actions
    for plain in ("campaign-v4", "intraday-v1", "cohort3-daily-v1", "cohort3-intraday-v1"):
        assert inputs[plain].model_dump() == {"earnings_pages": None, "corporate_actions": None, "short_volume": None}


@pytest.mark.parametrize(("score", "entry_delay"), [("hourly", 0), ("daily", 1)])
def test_a_batch_whose_entry_delay_does_not_follow_its_score_is_invalid(score, entry_delay):
    with pytest.raises(ValidationError, match="entry_delay"):
        RescoreBatch(
            id="x",
            protocol="p.json",
            protocol_sha256="a" * 64,
            campaign_dir="c",
            formulas_sha256="b" * 64,
            score=score,
            entry_delay=entry_delay,
        )


async def test_a_changed_formulas_file_is_refused_by_digest(tmp_path, monkeypatch):
    scene = _scene(tmp_path / "scene")
    target = scene.root / "campaign-daily" / "formulas.jsonl"
    target.write_text(target.read_text() + "\n")
    _forbid_ic(monkeypatch)

    async def no_build():
        raise AssertionError("a refused run builds nothing")

    output = tmp_path / "out"
    result = await _run(scene, output, builders={"daily": no_build})
    assert result["status"] == "refused"
    assert "digest" in result["reason"]
    assert _json(output / "result.json")["status"] == "refused"
    assert (output / "manifest.json").exists()
    assert not (output / "pairs.jsonl").exists()


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ("protocol", "protocol"),
        ("sectors", "sector"),
        ("revision", "revision"),
        ("builder", "builder"),
        ("score", "score"),
    ],
)
async def test_a_broken_pin_or_a_dirty_revision_is_refused_before_any_build(tmp_path, monkeypatch, change, needle):
    scene = _scene(tmp_path / "scene")
    overrides: dict = {}
    if change == "protocol":
        target = Path(scene.loaded.protocol.batches[0].protocol)
        target.write_text(target.read_text() + "\n")
    elif change == "sectors":
        overrides["sectors"] = LoadedSectors(document=scene.sectors.document, sha256="0" * 64)
    elif change == "revision":
        overrides["environment"] = environment("abc123-dirty")
    elif change == "builder":
        overrides["builders"] = {}
    else:
        document = _json(scene.path)
        document["batches"][0].update(score="hourly", entry_delay=1)
        scene.path.write_text(json.dumps(document))
        scene.loaded = load_rescore(scene.path)
    _forbid_ic(monkeypatch)
    result = await _run(scene, tmp_path / "out", **overrides)
    assert result["status"] == "refused"
    assert needle in result["reason"]


async def test_a_missing_campaign_directory_is_refused_by_type_name(tmp_path, monkeypatch):
    scene = _scene(tmp_path / "scene")
    target = scene.root / "campaign-daily" / "formulas.jsonl"
    target.unlink()
    _forbid_ic(monkeypatch)
    result = await _run(scene, tmp_path / "out")
    assert result["status"] == "refused"
    assert "FileNotFoundError" in result["reason"]
    assert str(tmp_path) not in result["reason"]


@pytest.mark.parametrize(
    ("tamper", "needle"),
    [
        (lambda rows: rows.pop(), "budget"),
        (lambda rows: rows[0].update(formula_id="0" * 16), "formula_id"),
        (lambda rows: rows[0].update(status="error"), "evaluated"),
        (lambda rows: rows[0].update(expression="nonexistent_field +"), "formula_id"),
    ],
)
async def test_a_formula_list_off_budget_or_with_a_wrong_id_is_refused(tmp_path, monkeypatch, tamper, needle):
    scene = _scene(tmp_path / "scene", [Spec(tamper=tamper)])
    _forbid_ic(monkeypatch)
    result = await _run(scene, tmp_path / "out")
    assert result["status"] == "refused"
    assert needle in result["reason"]


def _rehearsal(tamper: Callable[[list[dict]], None] | None = None) -> dict:
    world = labelled_world()
    protocol = CampaignProtocol.model_validate(_protocol_document("cohort3-daily-v1", DAILY_FAMILIES, LONG_DAYS))
    rows = [json.loads(row) for row in _daily_records()]
    if tamper is not None:
        tamper(rows)
    book = score_book(protocol, world.build)
    return rehearse_batch(_batch_row(), world.build, book, rows, protocol)


def _batch_row() -> RescoreBatch:
    return RescoreBatch(
        id="daily",
        protocol="p.json",
        protocol_sha256="a" * 64,
        campaign_dir="c",
        formulas_sha256="b" * 64,
        score="daily",
        entry_delay=0,
    )


def test_rehearsal_reproduces_a_recorded_edge():
    outcome = _rehearsal()
    assert outcome["status"] == "reproduced"
    assert outcome["mismatches"] == []
    # A real, non-degenerate edge: the rehearsal has something to compare.
    assert len({row["edge_mean"] for row in map(json.loads, _daily_records())}) == len(DAILY_FAMILIES)
    assert all(row["edge_mean"] != 0.0 for row in map(json.loads, _daily_records()))


def test_rehearsal_flags_edge_beyond_1e_9_and_any_session_count_difference():
    def within(rows):
        rows[0]["edge_mean"] += 5e-10

    assert _rehearsal(within)["status"] == "reproduced"

    def edge_moved(rows):
        rows[0]["edge_mean"] += 2e-9

    outcome = _rehearsal(edge_moved)
    assert outcome["status"] == "not_reproduced"
    assert [(m["formula_id"], m["field"]) for m in outcome["mismatches"]] == [
        (json.loads(_daily_records()[0])["formula_id"], "edge_mean")
    ]

    def one_session_more(rows):
        rows[1]["n_sessions"] += 1

    outcome = _rehearsal(one_session_more)
    assert outcome["status"] == "not_reproduced"
    assert [m["field"] for m in outcome["mismatches"]] == ["n_sessions"]


def test_rehearsal_records_a_formula_it_cannot_score_by_type_name():
    def broken(rows):
        rows[0]["expression"] = "nonexistent_field_for_this_test"

    outcome = _rehearsal(broken)
    assert outcome["status"] == "not_reproduced"
    assert outcome["mismatches"][0]["field"] == "error"
    assert outcome["mismatches"][0]["error"].isidentifier()


def _drifted(rows: list[dict]) -> None:
    rows[0]["edge_mean"] += 1e-6


async def test_a_not_reproduced_batch_gets_no_ic_and_no_verdict_and_others_continue(tmp_path):
    scene = _scene(tmp_path / "scene", [Spec(id="drifted", tamper=_drifted), Spec(id="steady")])
    output = tmp_path / "out"
    result = await _run(scene, output)
    assert result["status"] == "partial"
    assert result["batches_not_reproduced"] == ["drifted"]
    assert result["batches_reproduced"] == ["steady"]
    drifted = _batch(output, "drifted")
    assert drifted["status"] == "not_reproduced"
    assert drifted["mismatches"][0]["field"] == "edge_mean"
    assert "verdict" not in drifted
    steady = _batch(output, "steady")
    assert steady["status"] == "reproduced"
    assert steady["verdict"]["n_formulas"] == len(DAILY_FAMILIES)
    rows = _pairs(output)
    assert {row["batch"] for row in rows} == {"steady"}
    assert len(rows) == len(DAILY_FAMILIES)
    assert {row["batch"] for row in map(json.loads, (output / "tear_sheets.jsonl").read_text().splitlines())} == {
        "steady"
    }


def _rescore_args(scene: Scene, output: Path) -> list[str]:
    return [
        "rescore",
        "--rescore",
        str(scene.path),
        "--research-root",
        str(scene.root),
        "--cache",
        str(scene.root / "cache"),
        "--output",
        str(output),
    ]


class _Clients:
    bars = calendar = pace = None
    static_symbols = ()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture
def command(monkeypatch):
    monkeypatch.setattr(alpha_ic, "_apriori_clients", _Clients)
    monkeypatch.setattr(alpha_ic, "research_environment", lambda: environment())
    return monkeypatch


def test_the_command_exits_non_zero_when_a_batch_did_not_reproduce(tmp_path, command):
    scene = _scene(tmp_path / "scene", [Spec(id="drifted", tamper=_drifted)], write_sectors=True)
    command.setattr(alpha_ic, "_rescore_builders", lambda *_args: scene.builders)
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, _rescore_args(scene, output))
    assert result.exit_code != 0
    assert "partial" in result.output
    assert "drifted" in result.output
    assert _json(output / "result.json")["status"] == "partial"


def test_the_command_exits_zero_on_a_complete_run_with_no_batch_carrying(tmp_path, command):
    scene = _scene(tmp_path / "scene", write_sectors=True)
    seen = {}

    async def complete(loaded, output, **kwargs):
        seen.update(loaded=loaded, output=output, **kwargs)
        return {"status": "completed", "batches_carrying": [], "authorizes_promotion": False}

    command.setattr(alpha_ic, "execute_rescore", complete)
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, _rescore_args(scene, output))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["batches_carrying"] == []
    assert seen["research_root"] == scene.root
    assert seen["sectors"].sha256 == scene.loaded.protocol.sectors_sha256
    assert set(seen["builders"]) == {"daily"}


def test_the_command_is_registered_and_refuses_an_existing_output_or_a_broken_pin(tmp_path, command):
    assert "rescore" in cli.commands["alpha"].commands["ic"].commands
    scene = _scene(tmp_path / "scene", write_sectors=True)
    output = tmp_path / "out"
    output.mkdir()
    result = CliRunner().invoke(ic_group, _rescore_args(scene, output))
    assert result.exit_code != 0
    assert "already exists" in result.output

    # The sector map on disk is no longer the one the frozen file pins.
    target = scene.root / "sectors.json"
    target.write_text(target.read_text() + "\n")
    fresh = tmp_path / "fresh"
    result = CliRunner().invoke(ic_group, _rescore_args(scene, fresh))
    assert result.exit_code != 0
    assert "digest" in result.output
    assert not fresh.exists()


async def test_the_command_builds_each_batch_from_its_own_inputs(tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_pooled_build(clients, cohort, spec, cache, *, require_cached, load_hourly, events, short):
        calls.append(
            {
                "clients": clients,
                "cache": cache,
                "cached": require_cached,
                "hourly": load_hourly,
                "e": events,
                "s": short,
            }
        )

        async def build():
            return "built"

        return build

    monkeypatch.setattr(alpha_ic, "_pooled_build", fake_pooled_build)
    monkeypatch.setattr(alpha_ic, "load_spy", lambda built, cache: f"spy of {built} in {cache}")
    loaded = load_rescore(SHIPPED)
    pooled = {batch.id: await _pooled_protocol(REPO_ROOT / batch.protocol) for batch in loaded.protocol.batches}
    root, cache = tmp_path / "root", tmp_path / "cache"
    builders = alpha_ic._rescore_builders("clients", loaded, pooled, root, cache)
    assert list(builders) == list(EXPECTED_BATCHES)
    for build in builders.values():
        assert await build() == ("built", f"spy of built in {cache}")
    by_id = dict(zip(builders, calls, strict=True))
    assert all(call["clients"] == "clients" and call["cache"] == cache and call["cached"] for call in calls)
    assert {key for key, call in by_id.items() if call["hourly"]} == {"intraday-v1", "cohort3-intraday-v1"}
    assert {key for key, call in by_id.items() if call["e"] is not None} == {"events-v1", "cohort3-events-v1"}
    assert {key for key, call in by_id.items() if call["s"] is not None} == {"short-v1", "cohort3-short-v1"}
    events = by_id["cohort3-events-v1"]["e"][1]
    assert events.earnings_pages == root / "apriori-pead-v1-cache/nasdaq"
    assert events.actions_file == root / "corporate-actions-cohort-v3-20261015/actions.json"
    short = by_id["short-v1"]["s"][1]
    assert short.short_volume_dir == root / "short-volume-cache/short-volume"
    assert short.actions_file == root / "corporate-actions-cohort-v2-20261014/actions.json"


async def test_ic_is_not_computed_before_rehearsal(tmp_path, monkeypatch):
    scene = _scene(tmp_path / "scene", [Spec(id="drifted", tamper=_drifted)])
    _forbid_ic(monkeypatch)
    result = await _run(scene, tmp_path / "failed")
    assert result["status"] == "partial"
    assert result["batches_not_reproduced"] == ["drifted"]
    monkeypatch.undo()

    # And on a batch that does reproduce, the rehearsal comes first.
    scene = _scene(tmp_path / "scene2")
    order: list[str] = []
    rehearse, arrays = rescore_module.rehearse_batch, rescore_module.build_ic_arrays

    def rehearse_spy(*args, **kwargs):
        order.append("rehearse")
        return rehearse(*args, **kwargs)

    def arrays_spy(*args, **kwargs):
        order.append("arrays")
        return arrays(*args, **kwargs)

    monkeypatch.setattr(rescore_module, "rehearse_batch", rehearse_spy)
    monkeypatch.setattr(rescore_module, "build_ic_arrays", arrays_spy)
    result = await _run(scene, tmp_path / "read")
    assert result["status"] == "completed"
    assert order == ["rehearse", "arrays"]


async def test_discovery_and_selection_each_read_their_own_sessions(tmp_path):
    scene = _scene(tmp_path / "scene")
    output = tmp_path / "out"
    assert (await _run(scene, output))["status"] == "completed"
    world = labelled_world()
    protocol = load_campaign_protocol(Path(scene.loaded.protocol.batches[0].protocol)).protocol
    start, end = protocol.windows.discovery[0], protocol.windows.selection[1]
    groups = scene.sectors.groups(world.build.cube.symbols)
    arrays = build_ic_arrays(world.build, world.spy, groups, (start, end), horizons=(20,))
    book = score_book(protocol, world.build)
    row = _pairs(output)[0]
    scores = book.panel(row["expression"])[arrays.offset : arrays.offset + len(arrays.sessions)]
    ic = rank_ic_series(scores, arrays.residuals[20], arrays.eligible)
    for name, (low, high) in (("discovery", protocol.windows.discovery), ("selection", protocol.windows.selection)):
        keep = np.array([low <= day <= high for day in arrays.sessions])
        assert 0 < keep.sum() < keep.size
        expected = ic_inference(ic[keep], 20, draws=100, seed=3)
        assert row[name]["mean"] == pytest.approx(expected["mean"], abs=1e-12)
        assert row[name]["t"] == pytest.approx(expected["t"], abs=1e-12)
        assert row[name]["n_sessions"] == expected["n_sessions"]
        assert row[name]["n_unavailable"] == expected["n_unavailable"]
    assert row["family"] in DAILY_FAMILIES
    assert row["rehearsal"]["edge_mean"] == pytest.approx(row["rehearsal"]["recorded_edge_mean"], abs=1e-9)
    assert row["rehearsal"]["n_sessions"] == row["rehearsal"]["recorded_n_sessions"]


async def test_a_price_after_the_selection_end_changes_nothing(tmp_path):
    world = labelled_world()
    protocol = CampaignProtocol.model_validate(_protocol_document("cohort3-daily-v1", DAILY_FAMILIES, LONG_DAYS))
    first_reserve = next(day for day in LONG_DAYS if day > protocol.windows.selection[1])
    scene = _scene(tmp_path / "scene")
    plain = await _run(scene, tmp_path / "plain")
    bad = await _run(scene, tmp_path / "poisoned", builders={"daily": as_builder(poisoned_world(world, first_reserve))})
    assert plain["status"] == bad["status"] == "completed"
    assert (tmp_path / "plain" / "pairs.jsonl").read_bytes() == (tmp_path / "poisoned" / "pairs.jsonl").read_bytes()


def _hourly_spec(world: World) -> Spec:
    return Spec(
        id="hourly",
        base="intraday-v1",
        families=("first_hour_momentum",),
        world=world,
        score="hourly",
        entry_delay=1,
    )


async def test_hourly_batch_target_starts_at_the_next_open(tmp_path):
    world = first_hour_world()
    scene = _scene(tmp_path / "scene", [_hourly_spec(world)])
    output = tmp_path / "out"
    result = await _run(scene, output)
    assert result["status"] == "completed", result
    row = _pairs(output)[0]
    assert row["expression"] == "oc_spread"
    assert abs(row["discovery"]["mean"]) < 0.04
    assert abs(row["selection"]["mean"]) < 0.04

    # The same score against a target that starts at D's own open reads its own first hour.
    batch = scene.loaded.protocol.batches[0]
    protocol = load_campaign_protocol(Path(batch.protocol)).protocol
    records = [
        json.loads(line) for line in (scene.root / "campaign-hourly" / "formulas.jsonl").read_text().splitlines()
    ]
    book = score_book(protocol, world.build)
    groups = scene.sectors.groups(world.build.cube.symbols)
    leaked, _sheets = read_pairs(
        scene.loaded.protocol, protocol, world.build, world.spy, book, records, groups, entry_delay=0
    )
    assert leaked[0]["discovery"]["mean"] > 0.15
    assert leaked[0]["selection"]["mean"] > 0.15


def test_truncation_keeps_the_last_sessions_hourly_bars():
    world = first_hour_world()
    last = HOURLY_DAYS[300]
    next_session = HOURLY_DAYS[301]
    frames = truncate_bars(world.build.hourly, last)
    for name, frame in world.build.hourly.items():
        kept = pd.DatetimeIndex(frames[name].index).tz_convert("America/New_York")
        assert (kept.date == last).sum() == 7
        assert kept.max().date() == last
        assert (kept.date == next_session).sum() == 0
        assert len(frames[name]) == 7 * 301 < len(frame)
    pooled = load_campaign_protocol(POOLED / "intraday-v1.json").protocol
    build = dataclasses.replace(world.build, hourly=frames)
    position = world.build.cube.sessions.index(last)
    truncated = score_book(pooled, build).panel("oc_spread")[position]
    whole = score_book(pooled, world.build).panel("oc_spread")[position]
    assert np.isfinite(truncated).all()
    np.testing.assert_array_equal(truncated, whole)


async def test_the_score_book_never_holds_a_bar_after_the_selection_end(tmp_path, monkeypatch):
    """Scores read at ``D-1`` cannot show it in an output, so this pins what the book is handed."""
    world = first_hour_world()
    scene = _scene(tmp_path / "scene", [_hourly_spec(world)])
    protocol = load_campaign_protocol(Path(scene.loaded.protocol.batches[0].protocol)).protocol
    assert protocol.windows.selection[1] < HOURLY_DAYS[-1]
    latest: list[date] = []
    real = rescore_module.score_book

    def spy(pooled, built):
        latest.extend(
            max(frame.index.max() for frame in frames.values()).tz_convert("America/New_York").date()
            for frames in (built.adjusted, built.hourly)
        )
        return real(pooled, built)

    monkeypatch.setattr(rescore_module, "score_book", spy)
    assert (await _run(scene, tmp_path / "out"))["status"] == "completed"
    assert latest == [protocol.windows.selection[1]] * 2


async def test_an_hourly_price_after_the_selection_end_changes_nothing(tmp_path):
    world = first_hour_world()
    scene = _scene(tmp_path / "scene", [_hourly_spec(world)])
    await _run(scene, tmp_path / "plain")
    protocol = load_campaign_protocol(Path(scene.loaded.protocol.batches[0].protocol)).protocol
    first_reserve = next(day for day in HOURLY_DAYS if day > protocol.windows.selection[1])
    build = dataclasses.replace(
        world.build,
        adjusted={name: poisoned(frame, first_reserve) for name, frame in world.build.adjusted.items()},
        hourly={name: poisoned(frame, first_reserve) for name, frame in world.build.hourly.items()},
    )

    async def poisoned_build():
        return build, poisoned(world.spy, first_reserve)

    result = await _run(scene, tmp_path / "poisoned", builders={"hourly": poisoned_build})
    assert result["status"] == "completed"
    assert (tmp_path / "plain" / "pairs.jsonl").read_bytes() == (tmp_path / "poisoned" / "pairs.jsonl").read_bytes()


def _field_frames(fields: tuple[str, ...], seed: int) -> dict[str, pd.DataFrame]:
    world = labelled_world()
    rng = np.random.default_rng(seed)
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in LONG_DAYS])
    return {
        name: pd.DataFrame(rng.normal(0.0, 1.0, (len(index), len(fields))), index=index, columns=list(fields))
        for name in world.build.cube.symbols
    }


def _events_world() -> World:
    world = labelled_world()
    coverage = EventCoverage(
        symbols_without_reports=(),
        symbols_without_actions=(),
        non_session_reports=0,
        unpriced_dividends=0,
        unscaled_dividends=0,
        symbols_without_raw=(),
        identity_breaks={},
        first_finite={},
    )
    events = EventFrames(frames=_field_frames(tuple(EVENT_FIELDS), 31), coverage=coverage)
    return World(dataclasses.replace(world.build, events=events), world.spy, world.groups, world.window)


def _short_world() -> World:
    world = labelled_world()
    coverage = ShortCoverage(
        symbols_without_rows=(),
        unscored_rows=0,
        thin_days=0,
        offex_clipped=0,
        identity_breaks={},
        first_finite={},
    )
    short = ShortFrames(frames=_field_frames(tuple(SHORT_FIELDS), 32), coverage=coverage)
    return World(dataclasses.replace(world.build, short=short), world.spy, world.groups, world.window)


@pytest.mark.parametrize(
    ("base", "family", "make"),
    [("events-v1", "dividend_yield", _events_world), ("short-v1", "shorts_retreat", _short_world)],
)
async def test_an_extra_field_batch_scores_from_the_frames_its_build_carries(tmp_path, base, family, make):
    world = make()
    scene = _scene(tmp_path / "scene", [Spec(id="fields", base=base, families=(family,), world=world)])
    output = tmp_path / "out"
    result = await _run(scene, output)
    assert result["status"] == "completed", result
    row = _pairs(output)[0]
    assert row["family"] == family
    assert row["discovery"]["n_sessions"] > 0
    assert row["selection"]["n_sessions"] > 0


def test_batch_carries_only_if_both_means_are_positive():
    rows = [_pair(0.01, 0.02), _pair(0.03, 0.04)]
    verdict = batch_verdict(rows)
    assert verdict == {
        "discovery_mean": pytest.approx(0.02),
        "selection_mean": pytest.approx(0.03),
        "n_formulas": 2,
        "n_without_sessions": 0,
        "carries": True,
    }
    assert batch_verdict([_pair(0.01, -0.02), _pair(0.01, 0.01)])["carries"] is False
    assert batch_verdict([_pair(-0.01, 0.02), _pair(0.0, 0.02)])["carries"] is False
    assert batch_verdict([_pair(0.0, 0.01)])["carries"] is False
    # A member's own read does not rescue a batch whose mean is not positive.
    assert batch_verdict([_pair(0.5, 0.5), _pair(-0.6, -0.6)])["carries"] is False


def test_formula_with_no_evaluated_session_is_left_out_of_the_mean_and_counted():
    nan = float("nan")
    verdict = batch_verdict([_pair(0.02, 0.02), _pair(nan, nan), _pair(nan, 0.04)])
    assert verdict["n_formulas"] == 3
    assert verdict["n_without_sessions"] == 2
    assert verdict["discovery_mean"] == pytest.approx(0.02)
    assert verdict["selection_mean"] == pytest.approx(0.03)
    assert verdict["carries"] is True
    # None is how a saved row holds it.
    saved = {"mean": None, "n_sessions": 0}
    assert batch_verdict([{"discovery": saved, "selection": saved}, _pair(0.01, 0.01)])["n_without_sessions"] == 1

    empty = batch_verdict([_pair(nan, nan)])
    assert math.isnan(empty["discovery_mean"]) and math.isnan(empty["selection_mean"])
    assert empty["carries"] is False


async def test_nothing_touches_a_database(tmp_path, monkeypatch):
    assert "repository" not in inspect.signature(execute_rescore).parameters

    def no_database(*_args, **_kwargs):
        raise AssertionError("the re-score opens no database")

    monkeypatch.setattr(SignalDatabase, "__init__", no_database)
    scene = _scene(tmp_path / "scene")
    assert (await _run(scene, tmp_path / "out"))["status"] == "completed"


async def test_result_never_authorizes_promotion(tmp_path):
    scene = _scene(tmp_path / "scene", [Spec(id="drifted", tamper=_drifted), Spec(id="steady")])
    partial = tmp_path / "partial"
    await _run(scene, partial)
    refused = tmp_path / "refused"
    await _run(scene, refused, environment=environment("abc-dirty"))
    whole = tmp_path / "whole"
    await _run(_scene(tmp_path / "scene2"), whole)
    for output, status in ((whole, "completed"), (partial, "partial"), (refused, "refused")):
        manifest, result = _json(output / "manifest.json"), _json(output / "result.json")
        assert result["status"] == status
        assert manifest["check"] == "ic_rescore"
        assert manifest["authorizes_promotion"] is False
        assert result["authorizes_promotion"] is False
        assert "started_at" in manifest
        assert "started_at" not in result


async def test_result_is_byte_for_byte_repeatable(tmp_path):
    scene = _scene(tmp_path / "scene")
    await _run(scene, tmp_path / "a")
    await _run(scene, tmp_path / "b")
    for name in ("result.json", "batches.json", "pairs.jsonl", "tear_sheets.jsonl"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


async def test_summary_reports_t_mean_and_sd_over_covered_pairs(tmp_path):
    scene = _scene(tmp_path / "scene", [Spec(id="drifted", tamper=_drifted), Spec(id="steady")])
    output = tmp_path / "out"
    result = await _run(scene, output)
    summary = result["summary"]
    rows = _pairs(output)
    assert summary["n_pairs"] == 2 * len(DAILY_FAMILIES)
    assert summary["n_covered"] == len(DAILY_FAMILIES) == len(rows)
    for window in ("discovery", "selection"):
        ts = np.array([row[window]["t"] for row in rows])
        assert summary[window]["n_t"] == ts.size
        assert summary[window]["t_mean"] == pytest.approx(ts.mean())
        assert summary[window]["t_sd"] == pytest.approx(ts.std(ddof=1))
    assert summary["edge_statistic_reference"] == {"t_mean": -0.06, "t_sd": 1.25}
    known = summary["known_families"]
    assert [entry["family"] for entry in known] == ["reversal_5"]
    assert known[0]["batch"] == "steady"
    assert set(known[0]) >= {"batch", "family", "expression", "discovery_mean", "discovery_t", "selection_mean"}
    assert known[0]["batch_carries"] is _batch(output, "steady")["verdict"]["carries"]


async def test_a_failed_build_is_recorded_by_type_name_and_the_others_continue(tmp_path):
    scene = _scene(tmp_path / "scene", [Spec(id="broken"), Spec(id="steady")])

    async def broken():
        raise RuntimeError("provider-secret-text")

    messages: list[str] = []
    output = tmp_path / "out"
    result = await _run(scene, output, builders={**scene.builders, "broken": broken}, progress=messages.append)
    assert result["status"] == "partial"
    assert result["batches_failed"] == ["broken"]
    assert result["batches_reproduced"] == ["steady"]
    entry = _batch(output, "broken")
    assert entry["status"] == "failed"
    assert entry["reason"] == "RuntimeError"
    assert "verdict" not in entry
    for name in ("result.json", "batches.json", "pairs.jsonl", "manifest.json"):
        assert "provider-secret-text" not in (output / name).read_text()
    assert any("provider-secret-text" in message for message in messages)


async def test_each_batchs_frames_are_freed_before_the_next_build(tmp_path):
    scene = _scene(tmp_path / "scene", [Spec(id="first"), Spec(id="second")])
    world = labelled_world()
    first_build: list[weakref.ref] = []
    seen_at_second_build: list[tuple[bool, int]] = []

    async def first():
        built = dataclasses.replace(world.build)
        first_build.append(weakref.ref(built))
        return built, world.spy

    async def second():
        books = sum(1 for item in gc.get_objects() if isinstance(item, ScoreBook))
        seen_at_second_build.append((first_build[0]() is None, books))
        return dataclasses.replace(world.build), world.spy

    result = await _run(scene, tmp_path / "out", builders={"first": first, "second": second})
    assert result["status"] == "completed"
    assert seen_at_second_build == [(True, 0)]
