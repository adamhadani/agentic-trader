from pathlib import Path

import numpy as np

from agentic_trader.research.pooled.campaign import Selection, load_campaign_protocol
from agentic_trader.research.pooled.cube import CubeView


REPO = Path(__file__).resolve().parents[3]
V2 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v2.json").protocol
V3 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v3.json").protocol


def test_campaign_v3_changes_only_the_selection_from_v2():
    assert V3.version == 3 and V3.k is None
    assert V3.selection_rule == Selection(rule="top_fraction", fraction=0.10)
    keep = set(type(V3).model_fields) - {"version", "title", "k", "selection"}
    assert {key: getattr(V3, key) for key in keep} == {key: getattr(V2, key) for key in keep}
    V3.require_campaign_ready()
    assert V3.cube_spec().identity == V2.cube_spec().identity  # the cached v2 cube is reused


def _view(n_sessions: int, n_names: int) -> CubeView:
    shape = (n_sessions, n_names)
    return CubeView(
        sessions=tuple(range(n_sessions)),
        symbols=tuple(f"S{j}" for j in range(n_names)),
        offset=0,
        eligible=np.ones(shape, bool),
        labelled=np.ones(shape, bool),
        r_gross=np.zeros(shape),
        r_cost=np.zeros(shape),
        holding=np.full(shape, 3, np.int16),
        hit=np.ones(shape, np.int8),
        tiebreak=np.tile(np.arange(n_names, dtype=np.uint64), (n_sessions, 1)),
        dollar_volume=np.ones(shape),
    )


def test_select_dispatches_on_the_rule():
    view = _view(2, 20)
    scores = np.tile(np.arange(20, dtype=float), (2, 1))
    allowed = np.ones_like(scores, bool)
    top_k = V2.select(scores, allowed, view)  # top 3 with hold-skipping: new names on day 2
    decile = V3.select(scores, allowed, view)  # top 2 of 20 each day, the same names
    assert top_k.symbol_idx.tolist() == [19, 18, 17, 16, 15, 14]
    assert decile.symbol_idx.tolist() == [19, 18, 19, 18]
