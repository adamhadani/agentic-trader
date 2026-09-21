from agentic_trader.constants import AssetClass
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.registry import ConflictResolver, StrategyRegistry


def definition(alpha_id, symbol="AAPL"):
    return AlphaDefinition(alpha_id, alpha_id, "delta(close,3)", timeframe="1d", eligible_symbols=(symbol,))


def candidate(strategy, *, probe):
    return ScreenerCandidate(
        contract="AAPL",
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        timeframe="1d",
        strategy=strategy,
        direction="LONG",
        current_price=100.0,
        ema_20=1.0,
        ema_50=1.0,
        ema_200=1.0,
        rsi_14=50.0,
        atr_14=1.0,
        candle_timestamp="2026-09-21T00:00:00+00:00",
        recent_swing_low=0.0,
        recent_swing_high=0.0,
        trigger_detail="t",
        alpha_version="v-" + strategy,
        probe=probe,
    )


def test_install_marks_probe_strategies_and_replaces_the_previous_snapshot():
    registry = StrategyRegistry()
    registry.install_alphas((definition("alpha_live"),), probes=(definition("imported_without_prefix", "MSFT"),))
    live, probe = registry.get("alpha_live"), registry.get("imported_without_prefix")
    assert isinstance(live, FormulaicAlphaStrategy) and live.probe is False
    assert isinstance(probe, FormulaicAlphaStrategy) and probe.probe is True
    registry.install_alphas(())
    assert registry.get("alpha_live") is None and registry.get("imported_without_prefix") is None


def test_parallel_pickup_uses_type_not_the_alpha_prefix(app_config):
    registry = StrategyRegistry()
    registry.install_alphas((), probes=(definition("imported_without_prefix"),))
    picked = [s.strategy_id for s in registry.get_active_strategies(app_config, override_mode="parallel")]
    assert "imported_without_prefix" in picked


def test_active_candidate_beats_probe_candidate_for_one_instrument(app_config):
    winner = ConflictResolver.resolve([candidate("alpha_a_probe", probe=True), candidate("alpha_z_live", probe=False)])
    assert [c.strategy for c in winner] == ["alpha_z_live"]
