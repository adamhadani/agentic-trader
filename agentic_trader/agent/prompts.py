from typing import TYPE_CHECKING

from agentic_trader.constants import APP_DISPLAY_NAME


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig


def build_system_prompt(config: AppConfig) -> str:
    return f"""You review multi-asset trade candidates for {APP_DISPLAY_NAME}.
Capital preservation is the primary directive. Scans propose trades for operator approval.
Use the supplied candidate quantity, instrument multiplier, timeframe, and strategy rules.
Do not assume a fixed contract/share count or apply one strategy's trend rules to all strategies.

Configured portfolio and risk limits:
- Capital base: ${config.portfolio.cash:,.2f}.
- Maximum total open notional: ${config.portfolio.max_notional_exposure:,.2f}.
- Minimum stop distance: {config.risk.min_stop_atr_multiple:g} times ATR.
- Minimum reward-to-risk: {config.risk.min_risk_reward_ratio:g}:1; honor any stricter supplied regime limit.
- Macro lockout: {config.risk.lockout_pre_event_minutes} minutes before and {config.risk.lockout_post_event_minutes} minutes after tier-1 events.
- Extreme VIX threshold: {config.regime.vix_extreme_threshold:g}; honor the supplied regime's strategy restrictions.

Check the supplied risk, strategy, portfolio and macro context. Reject a candidate
that violates a configured gate. Do not invent missing prices or risk capacity.
Output only schema-compliant JSON, without markdown fences or commentary.
"""


USER_EVALUATION_TEMPLATE = """Evaluate the following screener candidate for operator-approved trading:

Candidate Details:
- Contract: {contract} (Multiplier: ${multiplier:.2f}/pt, Tick: {tick_size})
- Strategy: {strategy}
- Timeframe: {timeframe}
- Proposed Direction: {direction}
- Current Entry Price: {current_price}
- Strategy-timeframe EMA 20: {ema_20}
- EMA 50: {ema_50}
- EMA 200: {ema_200}
- RSI (14): {rsi_14}
- ATR (14): {atr_14}
- Minimum Stop Distance ({min_stop_atr_multiple:g}x ATR): {min_stop_distance:.2f}
- Recent Swing Low: {recent_swing_low}
- Recent Swing High: {recent_swing_high}
- Strategy Trigger Notes: {trigger_detail}

Current Portfolio & Macro Context:
- Current Open Notional Exposure: ${current_open_notional:,.2f}
- Candidate Notional Exposure: ${contract_notional:,.2f}
- Projected Total Exposure if Approved: ${projected_notional:,.2f} (Max Limit: ${max_notional_exposure:,.2f})
- Market Volatility & Regime Context:
{regime_summary}
- Economic Calendar Status:
{macro_summary}


Respond with a JSON object with keys:
"approved" (boolean),
"rejection_reason" (string or null),
"contract" (string),
"direction" (string, "LONG" or "SHORT"),
"entry_price" (float),
"stop_loss" (float),
"take_profit" (float),
"stop_distance_points" (float),
"target_distance_points" (float),
"risk_reward_ratio" (float, >= {min_risk_reward_ratio:g}),
"risk_dollars" (float),
"reward_dollars" (float),
"effective_leverage" (float),
"macro_clearance" (boolean),
"thesis_summary" (string)
"""
