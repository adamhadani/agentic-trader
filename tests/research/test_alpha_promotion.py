from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentic_trader.research.alpha.models import (
    AlphaCandidate,
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaStatus,
)
from agentic_trader.research.alpha.promotion import AlphaPromotionManager


@pytest.fixture
def temp_promo_yaml() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        path = Path(tf.name)
    yield path
    if path.exists():
        path.unlink()


def test_promotion_manager_lifecycle(temp_promo_yaml: Path):
    mgr = AlphaPromotionManager(config_path=temp_promo_yaml)
    assert mgr.load_records() == []
    assert mgr.list_active_alphas() == []

    defn = AlphaDefinition(
        alpha_id="alpha_test_001",
        name="Test Momentum Alpha",
        expression="delta(close, 5)",
        description="Testing delta close",
        origin="unit_test",
        direction="long",
        entry_threshold=1.5,
        exit_threshold=0.0,
        timeframe="4h",
    )
    metrics = AlphaEvaluationMetrics(
        rank_ic_mean=0.065,
        rank_ic_std=0.02,
        rank_ic_ir=3.25,
        sharpe_is=2.1,
        sharpe_oos=1.85,
        dsr=0.96,
        win_rate=0.58,
        profit_factor=1.92,
        max_drawdown_pct=6.5,
        annualized_return_pct=18.4,
        total_trades=42,
    )
    candidate = AlphaCandidate(definition=defn, metrics=metrics)

    # 1. Promote
    rec = mgr.promote(
        alpha=candidate,
        promoted_by="test_suite",
        allocation_weight=0.15,
        notes="High DSR alpha",
    )
    assert rec.alpha_id == "alpha_test_001"
    assert rec.status == AlphaStatus.PROMOTED
    assert rec.allocation_weight == 0.15

    # Verify persistent reload
    reloaded = mgr.load_records()
    assert len(reloaded) == 1
    assert reloaded[0].alpha_id == "alpha_test_001"
    assert reloaded[0].metrics is not None
    assert reloaded[0].metrics.sharpe_oos == 1.85
    assert reloaded[0].metrics.dsr == 0.96

    # 2. Duplicate promotion updates record in place
    rec2 = mgr.promote(
        alpha=candidate,
        promoted_by="re_promoter",
        allocation_weight=0.25,
        notes="Increased allocation",
    )
    assert rec2.allocation_weight == 0.25
    reloaded2 = mgr.load_records()
    assert len(reloaded2) == 1
    assert reloaded2[0].allocation_weight == 0.25
    assert reloaded2[0].promoted_by == "re_promoter"

    # 3. Demote
    demoted = mgr.demote(alpha_id="alpha_test_001", demoted_by="test_suite", reason="Retired")
    assert demoted is not None
    assert demoted.status == AlphaStatus.DEMOTED

    reloaded_active = mgr.list_active_alphas()
    assert len(reloaded_active) == 0

    all_recs = mgr.load_records()
    assert len(all_recs) == 1
    assert all_recs[0].status == AlphaStatus.DEMOTED
