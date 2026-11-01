import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.ic.protocol import load_composite


PATH = Path(__file__).resolve().parents[3] / "config" / "research" / "ic" / "composite-ic-v1.json"


def _variant(tmp_path, mutate):
    document = json.loads(PATH.read_text())
    mutate(document)
    out = tmp_path / "composite.json"
    out.write_text(json.dumps(document))
    return out


def test_the_shipped_composite_loads_and_every_expression_compiles():
    loaded = load_composite(PATH)
    assert len(loaded.sha256) == 64
    assert loaded.path == PATH
    for member in loaded.protocol.members:
        compile_expression(member.expression)


def test_members_match_the_spec_table():
    members = [(m.id, m.expression, m.sign) for m in load_composite(PATH).protocol.members]
    assert members == [
        ("ret_3_1", "roc(delay(close, 21), 42)", 1),
        ("ret_6_1", "roc(delay(close, 21), 105)", 1),
        ("ret_9_1", "roc(delay(close, 21), 168)", 1),
        ("ret_12_1", "roc(delay(close, 21), 231)", 1),
        ("ret_1_0", "roc(close, 21)", -1),
        ("ivol_capm_252d", "ts_resid_std(returns, mkt_ret, 252)", -1),
    ]
    protocol = load_composite(PATH).protocol
    assert [t.theme for t in protocol.themes] == ["momentum", "short_term_reversal", "low_risk"]
    assert protocol.sectors.sha256 == "56bc70fb52ce541aa2e4292673e81ebbc42df18bcf53484e6907cb52d4689574"
    assert protocol.horizon == 20


def test_unknown_key_is_refused(tmp_path):
    with pytest.raises(ValidationError):
        load_composite(_variant(tmp_path, lambda d: d.update(surprise=1)))
    with pytest.raises(ValidationError):
        load_composite(_variant(tmp_path, lambda d: d["gate"].update(extra=1)))


@pytest.mark.parametrize("sign", [0, 2, -2])
def test_sign_must_be_plus_or_minus_one(tmp_path, sign):
    with pytest.raises(ValidationError):
        load_composite(_variant(tmp_path, lambda d: d["themes"][0]["members"][0].update(sign=sign)))


def test_window_must_end_on_or_before_2023_12_29(tmp_path):
    load_composite(_variant(tmp_path, lambda d: d.update(window=["2016-08-01", "2023-12-28"])))
    with pytest.raises(ValidationError):
        load_composite(_variant(tmp_path, lambda d: d.update(window=["2016-08-01", "2024-01-02"])))


def test_an_uncompilable_expression_is_refused(tmp_path):
    with pytest.raises(ValidationError):
        load_composite(_variant(tmp_path, lambda d: d["themes"][0]["members"][0].update(expression="nope(close)")))


def test_the_pinned_files_hash_to_the_shipped_digests():
    protocol = load_composite(PATH).protocol
    root = PATH.parents[3]
    for relative, digest in (
        (protocol.sectors.path, protocol.sectors.sha256),
        (protocol.decisive.protocol, protocol.decisive.sha256),
        (protocol.secondary.protocol, protocol.secondary.sha256),
    ):
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative


def test_a_ic_bases_are_not_c_ic_replicates():
    """Replicates seed at ``seed + replicate``, so overlapping ranges would make A-IC reuse C-IC's permutations."""
    checks = load_composite(PATH).protocol.checks
    assert checks.a_ic.seed == 20271102
    c_ic_longest = max(checks.c_ic.replicates, checks.c_ic.session_shuffle_replicates)
    c_ic_seeds = set(range(checks.c_ic.seed, checks.c_ic.seed + c_ic_longest))
    assert not c_ic_seeds & set(range(checks.a_ic.seed, checks.a_ic.seed + checks.a_ic.replicates))
