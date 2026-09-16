"""Observation-only application service; no broker or notifier dependency."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np

from agentic_trader.market.bars import BAR_DURATIONS, FIXED_BAR_LAYOUT, completed_fixed_bars, fixed_bar_closes
from agentic_trader.research.alpha.forecasts import AlphaForecast, ForecastCalibration, combine_forecasts
from agentic_trader.research.alpha.strategy import TIMEFRAME_FIELDS, alpha_scores, entry_directions
from agentic_trader.storage.workflow import encode


def observe_definition(definition, data, as_of):
    symbol = data.symbol or data.contract
    if definition.eligible_symbols and symbol not in definition.eligible_symbols:
        return None
    source = getattr(data, TIMEFRAME_FIELDS[definition.timeframe])
    payload = {
        "version_id": definition.version_id,
        "symbol": symbol,
        "timeframe": definition.timeframe,
        "observed_at": as_of.isoformat(),
        "valid": False,
        "bar_layout": source.attrs.get("bar_layout", FIXED_BAR_LAYOUT),
    }
    try:
        frame = completed_fixed_bars(source, definition.timeframe, as_of=as_of)
        if frame.empty:
            return {**payload, "reason": "missing_timeframe"}
        timestamp = frame.index[-1]
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        completed_at = fixed_bar_closes(frame, definition.timeframe)[-1]
        payload.update(candle_timestamp=timestamp.isoformat(), completed_at=completed_at.isoformat())
        if frame.attrs.get("feed") != definition.data_feed or frame.attrs.get("adjustment") != definition.adjustment:
            raise ValueError("deployment_data_contract_mismatch")
        scores = alpha_scores(definition, frame)
        score = float(scores.iloc[-1])
        if not np.isfinite(score):
            raise ValueError("missing_score_or_warmup")
        if as_of - completed_at > BAR_DURATIONS[definition.timeframe]:
            raise ValueError("stale_closed_bar")
        payload.update(
            valid=True,
            score=score,
            decision=int(entry_directions(scores, definition).iloc[-1]),
            close=float(frame.iloc[-1]["Close"] if "Close" in frame else frame.iloc[-1]["close"]),
        )
    except (TypeError, ValueError) as exc:
        payload["reason"] = str(exc)
    return payload


class AlphaShadowService:
    def __init__(self, repository):
        self.repository = repository

    async def observe(self, snapshot, data, *, as_of=None):
        now = as_of or datetime.now(UTC)
        # CPU work stays off the event loop; writes remain short transactions.
        observations = await asyncio.to_thread(
            lambda: [observe_definition(d, data, now) for d in (*snapshot.active, *snapshot.shadow)]
        )
        calibrated: dict[str, list[AlphaForecast]] = {}
        for payload in observations:
            if payload is None:
                continue
            key = hashlib.sha256(
                encode({k: payload.get(k) for k in ("version_id", "symbol", "candle_timestamp", "reason")}).encode()
            ).hexdigest()
            await self.repository.record_forecast(key, {**payload, "registry_generation": snapshot.generation})
            if payload.get("valid"):
                decision = await self.repository.get(f"qualification/{payload['version_id']}")
                calibration = decision.get("calibration") if decision else None
                if calibration:
                    model = ForecastCalibration(**calibration)
                    forecast = AlphaForecast(
                        payload["version_id"],
                        payload["symbol"],
                        payload["timeframe"],
                        datetime.fromisoformat(payload["completed_at"]),
                        model.predict(payload["score"]),
                        model.return_volatility,
                        1,
                    )
                    calibrated.setdefault(payload["timeframe"], []).append(forecast)
        for forecasts in calibrated.values():
            combined = combine_forecasts(forecasts, as_of=now)
            for result in combined:
                payload = {
                    "valid": False,
                    "reason": "combined_shadow_forecast",
                    **asdict(result),
                    "registry_generation": snapshot.generation,
                }
                key = hashlib.sha256(encode(payload).encode()).hexdigest()
                await self.repository.record_forecast(key, payload)
        return [p for p in observations if p is not None]
