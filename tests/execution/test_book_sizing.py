import dataclasses
import logging
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from agentic_trader.config import BookSizingConfig
from agentic_trader.constants import AssetClass
from agentic_trader.execution import book_sizing
from agentic_trader.execution.book_sizing import BookContext, BookSizer, BookSizingStatus
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer
from tests.execution.test_position_sizing import _eval_with_tiers


AS_OF = date(2027, 1, 1)


def frame(seed: int, sessions: int = 200, base: np.ndarray | None = None) -> pd.DataFrame:
    index = pd.bdate_range(start="2026-01-02", periods=sessions)
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.01, sessions) if base is None else base + rng.normal(0.0, 0.001, sessions)
    close = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Volume": 1e6}, index=index)


def correlated_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    a = frame(1)
    rets = np.diff(np.log(a["Close"].to_numpy()), prepend=np.log(100.0))
    return a, frame(2, base=rets)


def config(**kw) -> BookSizingConfig:
    return BookSizingConfig(**{"mode": "preview", "max_portfolio_daily_vol_pct": 0.001, **kw})


def sizer(cfg, fetched=None, bar_source="default") -> BookSizer:
    if bar_source == "default":
        bar_source = MagicMock(fetch_daily_many=MagicMock(return_value=fetched or {}))
    return BookSizer(cfg, bar_source, portfolio_cash=100_000.0, min_units=1.0)


def row(symbol="BBB", direction="LONG", notional=20_000.0, asset_class="EQUITY"):
    return {
        "contract": symbol,
        "direction": direction,
        "asset_class": asset_class,
        "notional_value": notional,
        "risk_dollars": 300.0,
        "status": "EXECUTED",
    }


def candidate(**kw):
    base = {
        "contract": "AAA",
        "asset_class": AssetClass.EQUITY,
        "catalog_event": None,
        "alpha_version": None,
        "alpha_policy": None,
        "probe": False,
    }
    return SimpleNamespace(**{**base, **kw})


def datasets(**frames):
    return {s: SimpleNamespace(daily=f) for s, f in frames.items()}


async def ready(cfg=None, **kw):
    a, b = correlated_pair()
    s = sizer(cfg or config(**kw))
    ctx = await s.prepare([row()], datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "ready", ctx.reason
    return s, ctx


async def test_off_mode_is_not_applicable_and_never_fetches():
    s = sizer(config(mode="off"))
    ctx = await s.prepare([row()], {}, as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "off"
    s._bar_source.fetch_daily_many.assert_not_called()
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.status == BookSizingStatus.NOT_APPLICABLE
    assert out is ev


async def test_prepare_uses_scan_datasets_and_fetches_only_missing_book_symbols():
    a, b = correlated_pair()
    s = sizer(config(), {"BBB": b})
    ctx = await s.prepare([row()], datasets(AAA=a), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "ready"
    call = s._bar_source.fetch_daily_many.call_args
    assert call.args[0] == ["BBB"]
    assert call.kwargs == {"adjustment": "raw"}
    assert ctx.weights == {"BBB": 20_000.0}
    assert ctx.book_symbols == ("BBB",)


async def test_no_fetch_when_nothing_missing():
    a, b = correlated_pair()
    s = sizer(config())
    await s.prepare([row()], datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    s._bar_source.fetch_daily_many.assert_not_called()


async def test_missing_book_symbol_is_unavailable_not_dropped():
    a, _ = correlated_pair()
    s = sizer(config(), {})
    ctx = await s.prepare([row()], datasets(AAA=a), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "unavailable"
    assert ctx.missing_symbols == ("BBB",)
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.status == BookSizingStatus.UNAVAILABLE
    assert decision.factor is None
    assert out is ev


async def test_no_bar_source_is_unavailable():
    a, _ = correlated_pair()
    s = sizer(config(), bar_source=None)
    ctx = await s.prepare([row()], datasets(AAA=a), as_of=AS_OF, candidates=["AAA"])
    assert (ctx.status, ctx.reason) == ("unavailable", "no_bar_source")


async def test_invalid_book_row_is_unavailable():
    a, b = correlated_pair()
    s = sizer(config())
    ctx = await s.prepare([row(notional=None)], datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert (ctx.status, ctx.reason) == ("unavailable", "invalid_book_row")


async def test_non_equity_rows_are_not_in_the_book_vector():
    a, b = correlated_pair()
    s = sizer(config())
    rows = [row(), row("/MES", asset_class="FUTURES")]
    ctx = await s.prepare(rows, datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "ready"
    assert ctx.book_symbols == ("BBB",)


async def test_short_rows_are_signed_negative():
    a, b = correlated_pair()
    s = sizer(config())
    ctx = await s.prepare([row(direction="SHORT")], datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert ctx.weights == {"BBB": -20_000.0}


async def test_fetch_timeout_is_unavailable_with_type_name_only(monkeypatch):
    a, _ = correlated_pair()

    def slow(*args, **kwargs):
        time.sleep(0.05)
        return {}

    monkeypatch.setattr(book_sizing, "BOOK_SIZING_FETCH_TIMEOUT_SECONDS", 0.01)
    s = sizer(config(), bar_source=MagicMock(fetch_daily_many=slow))
    ctx = await s.prepare([row()], datasets(AAA=a), as_of=AS_OF, candidates=["AAA"])
    assert (ctx.status, ctx.reason) == ("unavailable", "TimeoutError")


async def test_adjusted_frame_counts_as_missing():
    a, b = correlated_pair()
    b.attrs["adjustment"] = "all"
    s = sizer(config(), {})
    ctx = await s.prepare([row()], datasets(AAA=a, BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "unavailable"
    assert ctx.missing_symbols == ("BBB",)


async def test_book_symbol_with_too_few_observations_is_unavailable():
    a, b = correlated_pair()
    s = sizer(config())
    ctx = await s.prepare([row()], datasets(AAA=a, BBB=b.iloc[:30]), as_of=AS_OF, candidates=["AAA"])
    assert (ctx.status, ctx.reason) == ("unavailable", "insufficient_observations")


async def test_candidate_with_too_few_observations_only_affects_itself():
    a, b = correlated_pair()
    s = sizer(config())
    ctx = await s.prepare([row()], datasets(AAA=a.iloc[:30], BBB=b), as_of=AS_OF, candidates=["AAA"])
    assert ctx.status == "ready"
    decision, _ = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert (decision.status, decision.reason) == (BookSizingStatus.UNAVAILABLE, "candidate_missing")


async def test_preview_records_would_scale_and_leaves_eval_res_identical():
    s, ctx = await ready()
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.factor is not None
    assert decision.factor < 1
    assert decision.would_scale is True
    assert decision.status == BookSizingStatus.UNCHANGED
    assert out is ev
    assert decision.quantity_before == decision.quantity_after == 40.0


async def test_enforce_scales_every_tier_and_reports_applied():
    s, ctx = await ready(mode="enforce", max_portfolio_daily_vol_pct=0.0021)
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.status == BookSizingStatus.APPLIED, decision
    assert out is not ev
    assert out.quantity == decision.quantity_after < decision.quantity_before
    assert all(t["quantity"] < o["quantity"] for t, o in zip(out.sizing_tiers, ev.sizing_tiers, strict=False))


async def test_enforce_blocks_when_default_tier_vanishes():
    s, ctx = await ready(mode="enforce")
    decision, out = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert decision.status == BookSizingStatus.BLOCKED
    assert out is None
    assert decision.quantity_after == 0
    assert decision.reason.startswith("book sizing: portfolio vol ")


async def test_enforce_factor_one_is_unchanged():
    s, ctx = await ready(mode="enforce", max_portfolio_daily_vol_pct=0.1)
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.status == BookSizingStatus.UNCHANGED
    assert decision.factor == 1.0
    assert out is ev


async def test_non_equity_candidate_and_dry_run_are_not_applicable():
    s, ctx = await ready()
    fut = _eval_with_tiers().model_copy(update={"asset_class": AssetClass.FUTURES})
    d1, o1 = s.decide(ctx, candidate=candidate(), eval_res=fut, risk_capital=100_000.0, dry_run=False)
    assert (d1.status, d1.reason) == (BookSizingStatus.NOT_APPLICABLE, "non_equity")
    assert o1 is fut
    d2, _ = s.decide(ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=True)
    assert (d2.status, d2.reason) == (BookSizingStatus.NOT_APPLICABLE, "dry_run")


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"catalog_event": object()}, "drift"),
        ({"alpha_version": "v1"}, "policy_locked"),
        ({"alpha_policy": "p"}, "policy_locked"),
        ({"probe": True}, "policy_locked"),
    ],
)
async def test_policy_locked_and_drift_candidates_are_not_applicable(kw, reason):
    s, ctx = await ready(mode="enforce")
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(**kw), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert (decision.status, decision.reason) == (BookSizingStatus.NOT_APPLICABLE, reason)
    assert out is ev


def test_no_context_is_not_applicable():
    s = sizer(config())
    decision, _ = s.decide(
        None, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert (decision.status, decision.reason) == (BookSizingStatus.NOT_APPLICABLE, "no_context")


async def test_shadow_optimizer_block_matches_closed_form():
    s, ctx = await ready(shadow_optimizer=True)
    decision, _ = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert decision.shadow is None  # decide never runs the solver (event loop)
    decision = await s.attach_shadow(ctx, decision)
    assert decision.shadow["status"] == "ok", decision.shadow
    assert abs(decision.shadow["vol_after_scaled_pct"] - decision.vol_after_scaled_pct) < 1e-6
    assert decision.shadow["abs_diff_pct"] < 1e-6


async def test_shadow_mismatch_logs_warning(monkeypatch, caplog):
    s, ctx = await ready()
    real = ConvexAlphaPortfolioOptimizer.optimize

    def skewed(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        return dataclasses.replace(result, portfolio_variance=result.portfolio_variance * 4)

    monkeypatch.setattr(ConvexAlphaPortfolioOptimizer, "optimize", skewed)
    with caplog.at_level(logging.WARNING):
        decision, _ = s.decide(
            ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
        )
        decision = await s.attach_shadow(ctx, decision)
    assert decision.shadow["abs_diff_pct"] > 1e-6
    assert any(r.__dict__.get("event") == "book_sizing_shadow_mismatch" for r in caplog.records)


async def test_shadow_optimizer_failure_never_changes_the_decision(monkeypatch):
    s, ctx = await ready(shadow_optimizer=True)
    off_sizer, off_ctx = await ready(shadow_optimizer=False)
    base, _ = off_sizer.decide(
        off_ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert base.shadow is None

    def boom(self, *args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(ConvexAlphaPortfolioOptimizer, "optimize", boom)
    decision, _ = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    decision = await s.attach_shadow(ctx, decision)
    assert decision.shadow == {"status": "failed", "reason": "RuntimeError"}
    assert decision.model_copy(update={"shadow": None}) == base


async def test_journal_block_shape():
    s, ctx = await ready()
    decision, _ = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    keys = {
        "mode",
        "status",
        "factor",
        "would_scale",
        "vol_before_pct",
        "vol_after_full_pct",
        "quantity_before",
        "quantity_after",
    }
    block = s.journal_block(decision)
    assert set(block) == keys
    assert block["status"] == "unchanged"
    assert block["mode"] == "preview"
    empty = s.journal_block(None)
    assert set(empty) == keys
    assert {k: v for k, v in empty.items() if k != "mode"} == dict.fromkeys(keys - {"mode"})
    assert empty["mode"] == "preview"


def test_context_is_frozen():
    ctx = BookContext(
        status="off",
        reason=None,
        covariance=None,
        shrinkage=None,
        n_observations=None,
        weights={},
        book_symbols=(),
        missing_symbols=(),
    )
    with pytest.raises(ValueError, match="frozen"):
        ctx.status = "ready"


async def test_shadow_check_includes_an_existing_holding_in_the_candidate_name():
    """The closed form adds the scaled card to the book's own weight in that symbol; the shadow
    must pin the same total, not replace the holding with the card."""
    s, ctx = await ready(shadow_optimizer=True)
    held = ctx.model_copy(update={"weights": {**ctx.weights, "AAA": 5_000.0}})
    decision, _ = s.decide(
        held, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    decision = await s.attach_shadow(held, decision)
    assert decision.shadow["status"] == "ok", decision.shadow
    assert decision.shadow["abs_diff_pct"] < 1e-6


async def test_attach_shadow_is_a_no_op_when_off_or_not_decided():
    s, ctx = await ready(shadow_optimizer=False)
    decision, _ = s.decide(
        ctx, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert await s.attach_shadow(ctx, decision) is decision
    s2, _ = await ready(shadow_optimizer=True)
    exempt, _ = s2.decide(
        None, candidate=candidate(), eval_res=_eval_with_tiers(), risk_capital=100_000.0, dry_run=False
    )
    assert await s2.attach_shadow(None, exempt) is exempt


async def test_unavailable_never_blocks_under_enforce():
    s, ctx = await ready(mode="enforce")
    broken = ctx.model_copy(update={"status": "unavailable", "reason": "missing_bars", "covariance": None})
    ev = _eval_with_tiers()
    decision, out = s.decide(broken, candidate=candidate(), eval_res=ev, risk_capital=100_000.0, dry_run=False)
    assert decision.status == BookSizingStatus.UNAVAILABLE
    assert out is ev


async def test_short_candidate_against_a_long_book_is_scaled_less_than_a_long_one():
    """Sign handling end to end: a short against a correlated long book lowers portfolio vol."""
    s, ctx = await ready()
    long_eval = _eval_with_tiers()
    short_eval = long_eval.model_copy(update={"direction": "SHORT"})
    long_decision, _ = s.decide(ctx, candidate=candidate(), eval_res=long_eval, risk_capital=100_000.0, dry_run=False)
    short_decision, _ = s.decide(ctx, candidate=candidate(), eval_res=short_eval, risk_capital=100_000.0, dry_run=False)
    assert short_decision.vol_after_full_pct < long_decision.vol_after_full_pct
    assert short_decision.factor >= long_decision.factor
    assert short_decision.scaled_weight <= 0.0 <= long_decision.scaled_weight


async def test_non_positive_risk_capital_is_unavailable():
    s, ctx = await ready()
    ev = _eval_with_tiers()
    decision, out = s.decide(ctx, candidate=candidate(), eval_res=ev, risk_capital=0.0, dry_run=False)
    assert decision.status == BookSizingStatus.UNAVAILABLE
    assert decision.reason == "invalid_risk_capital"
    assert out is ev


async def test_with_card_adds_signed_notional_and_never_mutates():
    _, ctx = await ready()
    before = dict(ctx.weights)
    longer = ctx.with_card(symbol="aaa", direction="LONG", notional=2_000.0, asset_class=AssetClass.EQUITY)
    assert longer.weights["AAA"] == before.get("AAA", 0.0) + 2_000.0
    assert ctx.weights == before  # frozen context untouched
    shorter = longer.with_card(symbol="AAA", direction="SHORT", notional=500.0, asset_class=AssetClass.EQUITY)
    assert shorter.weights["AAA"] == pytest.approx(before.get("AAA", 0.0) + 1_500.0)


async def test_with_card_leaves_unavailable_contexts_and_non_equity_cards_alone():
    _, ctx = await ready()
    broken = ctx.model_copy(update={"status": "unavailable", "reason": "missing_bars", "covariance": None})
    assert broken.with_card(symbol="AAA", direction="LONG", notional=1.0, asset_class=AssetClass.EQUITY) is broken
    assert ctx.with_card(symbol="AAA", direction="LONG", notional=1.0, asset_class=AssetClass.FUTURES) is ctx
    assert ctx.with_card(symbol="ZZZ", direction="LONG", notional=1.0, asset_class=AssetClass.EQUITY) is ctx
