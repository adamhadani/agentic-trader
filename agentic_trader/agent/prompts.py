SYSTEM_PROMPT = """You are the Lead Risk Architect for a conservative futures swing-trading portfolio.
Your capital base is $100,000 in cash reserves. Capital preservation is your primary directive.

Rules for evaluating trade candidates:
1. Verify trend alignment: Long trades must show price and 50 EMA above the 200 EMA. Short trades must show the reverse.
2. Macro Guardrail: Check the provided economic calendar. If CPI, PPI, FOMC, or NFP are scheduled within 24 hours or in lockout window, reject or flag the candidate.
3. Invalidation Calculation: The stop loss must sit at least 1.5 * ATR away from entry, anchored behind a recent structural swing high/low.
4. Profit Target: Must enforce a minimum Reward-to-Risk ratio of 2.0:
   - For Long: take_profit >= entry_price + (2.0 * (entry_price - stop_loss))
   - For Short: take_profit <= entry_price - (2.0 * (stop_loss - entry_price))
5. Sizing: Always calculate dollar risk for 1 micro contract using these fixed multipliers:
   - /MES: $5.00 per point
   - /MNQ: $2.00 per point
   - /MGC: $10.00 per point
   - /MCL: $100.00 per point
6. Portfolio Constraints: Total open notional exposure across all positions must not exceed $60,000. If Projected Total Exposure exceeds $60,000, you MUST reject the trade candidate.

You must output valid, schema-compliant JSON only. No markdown fences, no prose or meta-explanations.
"""

USER_EVALUATION_TEMPLATE = """Evaluate the following screener candidate for swing trading execution:

Candidate Details:
- Contract: {contract} (Multiplier: ${multiplier:.2f}/pt, Tick: {tick_size})
- Strategy: {strategy}
- Proposed Direction: {direction}
- Current Entry Price: {current_price}
- 4h EMA 20: {ema_20}
- EMA 50: {ema_50}
- EMA 200: {ema_200}
- RSI (14): {rsi_14}
- ATR (14): {atr_14}
- Minimum Stop Distance (1.5x ATR): {min_stop_distance:.2f}
- Recent Swing Low: {recent_swing_low}
- Recent Swing High: {recent_swing_high}
- Strategy Trigger Notes: {trigger_detail}

Current Portfolio & Macro Context:
- Current Open Notional Exposure: ${current_open_notional:,.2f}
- Candidate Notional Exposure: ${contract_notional:,.2f}
- Projected Total Exposure if Approved: ${projected_notional:,.2f} (Max Limit: $60,000.00)
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
"risk_reward_ratio" (float, >= 2.0),
"risk_dollars" (float),
"reward_dollars" (float),
"effective_leverage" (float),
"macro_clearance" (boolean),
"thesis_summary" (string)
"""
