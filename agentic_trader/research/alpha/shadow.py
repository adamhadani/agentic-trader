"""Observation-only application service; no broker or notifier dependency."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np

from agentic_trader.market.bars import FIXED_BAR_LAYOUT
from agentic_trader.research.alpha.clock import AlphaClockRejection, closed_alpha_bars
from agentic_trader.research.alpha.forecasts import (
    AlphaForecast,
    ForecastCalibration,
    ForecastContract,
    combine_forecasts,
    forecast_family,
)
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
        "bar_layout": definition.clock.bar_layout
        if definition.clock
        else source.attrs.get("bar_layout", FIXED_BAR_LAYOUT),
    }
    if definition.clock is not None and (snapshot := data.session_bars.get(definition.timeframe)) is not None:
        payload.update(requested_at=snapshot.requested_at.isoformat(), received_at=snapshot.received_at.isoformat())
    try:
        frame, completed_at = closed_alpha_bars(definition, data, as_of=as_of, require_verified=True)
        timestamp = frame.index[-1]
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        payload.update(candle_timestamp=timestamp.isoformat(), completed_at=completed_at.isoformat())
        scores = alpha_scores(definition, frame)
        score = float(scores.iloc[-1])
        if not np.isfinite(score):
            raise ValueError("missing_score_or_warmup")
        payload.update(
            valid=True,
            score=score,
            decision=int(entry_directions(scores, definition).iloc[-1]),
            close=float(frame.iloc[-1]["Close"] if "Close" in frame else frame.iloc[-1]["close"]),
        )
        if definition.clock is not None:
            # Scores are diagnostic until the session acquisition/decision worker
            # and actual execution evidence meet the roadmap's acceptance gates.
            payload.update(valid=False, reason="session_clock_diagnostic")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AlphaClockRejection):
            payload.update(exc.observation)
        payload["reason"] = str(exc)
    return payload


class AlphaShadowService:
    def __init__(self, repository):
        self.repository = repository

    async def observe(self, snapshot, data, *, as_of=None):
        now = as_of or datetime.now(UTC)
        # CPU work stays off the event loop; writes remain short transactions.
        observations = await asyncio.to_thread(
            lambda: [
                observe_definition(d, data, now)
                for d in (*snapshot.active, *snapshot.shadow)
                if d.clock is None or d.timeframe in data.session_bars
            ]
        )
        definitions = {d.version_id: d for d in (*snapshot.active, *snapshot.shadow)}
        calibrated: dict[ForecastContract, list[AlphaForecast]] = {}
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
                    try:
                        model = ForecastCalibration.from_document(calibration)
                        definition = definitions[payload["version_id"]]
                        observed_at = datetime.fromisoformat(payload["completed_at"])
                        if (
                            model.contract.feed != definition.data_feed
                            or model.contract.adjustment != definition.adjustment
                            or model.contract.target.timeframe != definition.timeframe
                            or model.contract.bar_layout != payload["bar_layout"]
                            or datetime.fromisoformat(model.trained_until) > observed_at
                        ):
                            raise ValueError("Calibration contract/cutoff does not match observation")
                        forecast = AlphaForecast(
                            payload["version_id"],
                            payload["symbol"],
                            model.contract,
                            observed_at,
                            model.predict(payload["score"]),
                            model.standard_error(payload["score"]),
                            1,
                            forecast_family(definition.expression, definition.normalization_window),
                            model.calibration_id,
                        )
                        calibrated.setdefault(model.contract, []).append(forecast)
                    except (ValueError, TypeError, KeyError) as exc:
                        await self._record(
                            {
                                **payload,
                                "valid": False,
                                "reason": "calibration_unavailable",
                                "detail": str(exc),
                                "registry_generation": snapshot.generation,
                            }
                        )
        for forecasts in calibrated.values():
            try:
                combined = combine_forecasts(forecasts, as_of=now)
            except ValueError as exc:
                await self._record(
                    {
                        "symbol": data.symbol or data.contract,
                        "observed_at": now.isoformat(),
                        "valid": False,
                        "reason": "combined_forecast_unavailable",
                        "detail": str(exc),
                        "registry_generation": snapshot.generation,
                    }
                )
                continue
            for result in combined:
                await self._record(
                    {
                        "valid": False,
                        "reason": "combined_shadow_forecast",
                        **asdict(result),
                        "timeframe": result.contract.target.timeframe,
                        "registry_generation": snapshot.generation,
                    }
                )
        return [p for p in observations if p is not None]

    async def _record(self, payload):
        key = hashlib.sha256(encode(payload).encode()).hexdigest()
        await self.repository.record_forecast(key, payload)
