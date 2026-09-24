"""Table-driven tests for the pure card-freshness decision function."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.config import CardFreshnessConfig
from agentic_trader.execution.freshness import (
    CardOutcome,
    assess_card,
    card_is_stale,
    card_session_over,
    meets_min_reward_risk,
    parse_valid_until,
    reprice_quantity,
    valid_until_from_provenance,
)


POLICY = CardFreshnessConfig()  # enabled=True, fresh_seconds=1800, fresh_max_r=0.25, reprice_min_risk_fraction=0.5
MIN_RR = 2.0

# LONG fixture: entry 100, stop 95 (risk 5), target far away so reward:risk never
# binds while exercising the freshness/stop/too-close-to-stop boundaries.
LONG_ENTRY, LONG_STOP, LONG_TARGET = 100.0, 95.0, 200.0
# SHORT fixture: entry 100, stop 105 (risk 5), target far away, same reasoning.
SHORT_ENTRY, SHORT_STOP, SHORT_TARGET = 100.0, 105.0, 1.0

ISSUED_AT = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)  # 10:00 ET
VALID_UNTIL = datetime(2026, 9, 23, 20, 0, 0, tzinfo=UTC)  # 16:00 ET, same session close


def _assess(
    *,
    direction="LONG",
    entry=LONG_ENTRY,
    stop=LONG_STOP,
    target=LONG_TARGET,
    issued_at=ISSUED_AT,
    valid_until=VALID_UNTIL,
    now=None,
    price=LONG_ENTRY,
    session_is_rth=True,
    gate_reason=None,
    min_reward_risk=MIN_RR,
    policy=POLICY,
):
    return assess_card(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        issued_at=issued_at,
        valid_until=valid_until,
        now=now if now is not None else issued_at,
        price=price,
        session_is_rth=session_is_rth,
        gate_reason=gate_reason,
        min_reward_risk=min_reward_risk,
        policy=policy,
    )


# --- EXECUTE: fresh boundaries -----------------------------------------------------


def test_execute_at_entry_price_with_no_age():
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY)
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == 0.0
    assert result.age_seconds == 0.0
    assert result.price == LONG_ENTRY


def test_execute_at_exactly_fresh_seconds_boundary():
    now = ISSUED_AT + timedelta(seconds=POLICY.fresh_seconds)
    result = _assess(now=now, price=LONG_ENTRY)
    assert result.outcome == CardOutcome.EXECUTE


def test_reprice_just_past_fresh_seconds_boundary():
    now = ISSUED_AT + timedelta(seconds=POLICY.fresh_seconds + 1)
    result = _assess(now=now, price=LONG_ENTRY)
    assert result.outcome == CardOutcome.REPRICE


def test_execute_at_exactly_fresh_max_r_boundary_long():
    price = LONG_ENTRY + POLICY.fresh_max_r * (LONG_ENTRY - LONG_STOP)  # r_consumed == fresh_max_r exactly
    result = _assess(now=ISSUED_AT, price=price)
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == pytest.approx(POLICY.fresh_max_r)


def test_reprice_just_past_fresh_max_r_boundary_long():
    price = LONG_ENTRY + (POLICY.fresh_max_r + 0.01) * (LONG_ENTRY - LONG_STOP)
    result = _assess(now=ISSUED_AT, price=price)
    assert result.outcome == CardOutcome.REPRICE
    assert "R since the card" in result.reason


def test_execute_at_exactly_fresh_max_r_boundary_short():
    price = SHORT_ENTRY - POLICY.fresh_max_r * (SHORT_STOP - SHORT_ENTRY)
    result = _assess(
        direction="SHORT", entry=SHORT_ENTRY, stop=SHORT_STOP, target=SHORT_TARGET, now=ISSUED_AT, price=price
    )
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == pytest.approx(POLICY.fresh_max_r)


# --- MISSED: geometry breaks (LONG) -------------------------------------------------


def test_missed_long_price_at_stop_exactly():
    result = _assess(price=LONG_STOP)
    assert result.outcome == CardOutcome.MISSED
    assert "stop" in result.reason.lower()
    assert result.r_consumed == pytest.approx(-1.0)


def test_missed_long_price_through_stop():
    result = _assess(price=LONG_STOP - 1)
    assert result.outcome == CardOutcome.MISSED
    assert "stop" in result.reason.lower()


def test_missed_long_price_at_target_exactly():
    result = _assess(price=LONG_TARGET)
    assert result.outcome == CardOutcome.MISSED
    assert "target" in result.reason.lower()


def test_missed_long_price_through_target():
    result = _assess(price=LONG_TARGET + 1)
    assert result.outcome == CardOutcome.MISSED
    assert "target" in result.reason.lower()


def test_missed_long_too_close_to_stop_just_below_boundary():
    # remaining risk fraction boundary: reprice_min_risk_fraction * risk = 0.5 * 5 = 2.5
    # remaining = price - stop; just below 2.5 -> price just below 97.5
    price = LONG_STOP + POLICY.reprice_min_risk_fraction * (LONG_ENTRY - LONG_STOP) - 0.1
    result = _assess(price=price)
    assert result.outcome == CardOutcome.MISSED
    assert "close to the stop" in result.reason.lower()


def test_not_missed_at_exactly_reprice_min_risk_fraction_boundary():
    price = LONG_STOP + POLICY.reprice_min_risk_fraction * (LONG_ENTRY - LONG_STOP)  # remaining == 2.5 exactly
    result = _assess(now=ISSUED_AT + timedelta(hours=1), price=price)
    assert result.outcome != CardOutcome.MISSED


def test_missed_long_reward_risk_just_below_minimum():
    # entry 100, stop 90 (risk 10), target 150: the boundary price solving
    # (target-price)/(price-stop) == 2.0 is exactly 110 (clean floats); one dollar
    # past it drops the ratio just below the 2.0 minimum.
    price = 111.0
    result = _assess(entry=100.0, stop=90.0, target=150.0, price=price)
    assert result.outcome == CardOutcome.MISSED
    assert "reward:risk" in result.reason
    assert "< 2.0" in result.reason


def test_not_missed_at_exactly_minimum_reward_risk_boundary():
    price = 110.0  # exact solution to (150-price)/(price-90) == 2.0, with clean floats
    result = _assess(entry=100.0, stop=90.0, target=150.0, now=ISSUED_AT + timedelta(hours=1), price=price)
    assert result.outcome != CardOutcome.MISSED


# --- MISSED: geometry breaks (SHORT) ------------------------------------------------


def _short(**overrides):
    kwargs = {"direction": "SHORT", "entry": SHORT_ENTRY, "stop": SHORT_STOP, "target": SHORT_TARGET}
    kwargs.update(overrides)
    return _assess(**kwargs)


def test_missed_short_price_at_stop_exactly():
    result = _short(price=SHORT_STOP)
    assert result.outcome == CardOutcome.MISSED
    assert "stop" in result.reason.lower()
    assert result.r_consumed == pytest.approx(-1.0)


def test_missed_short_price_through_stop():
    result = _short(price=SHORT_STOP + 1)
    assert result.outcome == CardOutcome.MISSED
    assert "stop" in result.reason.lower()


def test_missed_short_price_at_target_exactly():
    result = _short(price=SHORT_TARGET)
    assert result.outcome == CardOutcome.MISSED
    assert "target" in result.reason.lower()


def test_missed_short_too_close_to_stop():
    # remaining = stop - price; threshold 2.5; just below -> price just above 102.5
    price = SHORT_STOP - POLICY.reprice_min_risk_fraction * (SHORT_STOP - SHORT_ENTRY) + 0.1
    result = _short(price=price)
    assert result.outcome == CardOutcome.MISSED
    assert "close to the stop" in result.reason.lower()


def test_execute_short_fresh():
    result = _short(now=ISSUED_AT, price=SHORT_ENTRY)
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == 0.0


def test_reprice_short_stale():
    now = ISSUED_AT + timedelta(hours=1)
    result = _short(now=now, price=SHORT_ENTRY - 1)  # favorable move, still R within bound but stale age
    assert result.outcome == CardOutcome.REPRICE


# --- EXPIRED: session boundaries -----------------------------------------------------


def test_expired_when_now_reaches_valid_until():
    result = _assess(now=VALID_UNTIL, price=LONG_ENTRY)
    assert result.outcome == CardOutcome.EXPIRED
    assert result.reason == "Card expired: its session has ended."
    assert result.r_consumed is None
    assert result.price is None


def test_expired_when_now_past_valid_until():
    now = VALID_UNTIL + timedelta(seconds=1)
    result = _assess(now=now, price=LONG_ENTRY)
    assert result.outcome == CardOutcome.EXPIRED


def test_not_expired_just_before_valid_until():
    now = VALID_UNTIL - timedelta(seconds=1)
    result = _assess(now=now, price=LONG_ENTRY)
    assert result.outcome != CardOutcome.EXPIRED


def test_expired_when_not_session_is_rth():
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY, session_is_rth=False)
    assert result.outcome == CardOutcome.EXPIRED


def test_legacy_valid_until_none_same_ny_date_not_expired():
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)  # 10:00 ET
    now = datetime(2026, 9, 23, 15, 0, 0, tzinfo=UTC)  # 11:00 ET, same NY date
    result = assess_card(
        direction="LONG",
        entry=LONG_ENTRY,
        stop=LONG_STOP,
        target=LONG_TARGET,
        issued_at=issued_at,
        valid_until=None,
        now=now,
        price=LONG_ENTRY,
        session_is_rth=True,
        gate_reason=None,
        min_reward_risk=MIN_RR,
        policy=POLICY,
    )
    assert result.outcome != CardOutcome.EXPIRED
    # age is 3600s > fresh_seconds(1800), so falls through to REPRICE.
    assert result.outcome == CardOutcome.REPRICE


def test_legacy_valid_until_none_next_ny_date_is_expired():
    issued_at = datetime(2026, 9, 23, 23, 30, 0, tzinfo=UTC)  # 19:30 ET Sept 23
    now = datetime(2026, 9, 24, 5, 0, 0, tzinfo=UTC)  # 01:00 ET Sept 24
    result = assess_card(
        direction="LONG",
        entry=LONG_ENTRY,
        stop=LONG_STOP,
        target=LONG_TARGET,
        issued_at=issued_at,
        valid_until=None,
        now=now,
        price=LONG_ENTRY,
        session_is_rth=True,
        gate_reason=None,
        min_reward_risk=MIN_RR,
        policy=POLICY,
    )
    assert result.outcome == CardOutcome.EXPIRED


def test_naive_datetime_raises():
    with pytest.raises(ValueError):
        assess_card(
            direction="LONG",
            entry=LONG_ENTRY,
            stop=LONG_STOP,
            target=LONG_TARGET,
            issued_at=datetime(2026, 9, 23, 14, 0, 0),  # noqa: DTZ001 - intentionally naive, asserting rejection
            valid_until=None,
            now=datetime(2026, 9, 23, 15, 0, 0),  # noqa: DTZ001 - intentionally naive, asserting rejection
            price=LONG_ENTRY,
            session_is_rth=True,
            gate_reason=None,
            min_reward_risk=MIN_RR,
            policy=POLICY,
        )


# --- Gate reason beats EXECUTE -------------------------------------------------------


def test_failing_gate_gives_missed_even_when_fresh():
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY, gate_reason="Earnings blackout: report due before close.")
    assert result.outcome == CardOutcome.MISSED
    assert result.reason == "Earnings blackout: report due before close."
    assert result.r_consumed == 0.0


def test_no_gate_reason_allows_execute():
    result = _assess(now=ISSUED_AT, price=LONG_ENTRY, gate_reason=None)
    assert result.outcome == CardOutcome.EXECUTE


# --- Incident 2026-09-24 #19: current-price reward:risk must not gate a fresh tap ---------
# Card #19: LONG entry 240.31, stop 229.90, target 261.13 (approved R:R exactly 2.0).
# Tapped 20s later at 240.40 (+0.009R). The current-price reward:risk there is
# (261.13-240.40)/(240.40-229.90) = 1.974... < 2.0 -- but the order rests at the
# original limit (240.31) when EXECUTE fires, so this must never gate a fresh tap.

CARD19_ENTRY, CARD19_STOP, CARD19_TARGET = 240.31, 229.90, 261.13
CARD19_TAPPED_PRICE = 240.40  # +0.009R uptick; current-price R:R there is ~1.974 < 2.0


def test_incident_card_19_fresh_uptick_executes_despite_low_current_price_rr():
    now = ISSUED_AT + timedelta(seconds=20)
    result = _assess(entry=CARD19_ENTRY, stop=CARD19_STOP, target=CARD19_TARGET, now=now, price=CARD19_TAPPED_PRICE)
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == pytest.approx(0.00864, abs=1e-4)


def test_incident_card_19_fresh_downtick_still_executes():
    now = ISSUED_AT + timedelta(seconds=20)
    price = CARD19_ENTRY - 0.09  # favorable-direction-agnostic small downtick, still within fresh bounds
    result = _assess(entry=CARD19_ENTRY, stop=CARD19_STOP, target=CARD19_TARGET, now=now, price=price)
    assert result.outcome == CardOutcome.EXECUTE


def test_incident_card_19_stale_uptick_is_missed_with_two_decimal_message():
    # Same levels/tap price, but now stale (past fresh_seconds): the current-price
    # reward:risk floor applies to the would-be re-price and this card fails it.
    now = ISSUED_AT + timedelta(seconds=POLICY.fresh_seconds + 1)
    result = _assess(entry=CARD19_ENTRY, stop=CARD19_STOP, target=CARD19_TARGET, now=now, price=CARD19_TAPPED_PRICE)
    assert result.outcome == CardOutcome.MISSED
    assert result.reason == "Missed: reward:risk at 240.40 would be 1.97 < 2.00."


def test_incident_card_19_stale_downtick_is_repriced():
    now = ISSUED_AT + timedelta(seconds=POLICY.fresh_seconds + 1)
    price = CARD19_ENTRY - 0.09
    result = _assess(entry=CARD19_ENTRY, stop=CARD19_STOP, target=CARD19_TARGET, now=now, price=price)
    assert result.outcome == CardOutcome.REPRICE


def test_incident_card_19_short_mirror_fresh_downtick_executes_despite_low_current_price_rr():
    # SHORT mirror of card #19: entry 240.31, stop 250.72 (risk 10.41), target 219.49
    # (approved R:R ~2.0). A small favorable downtick to 240.22 (+0.009R) drops the
    # current-price reward:risk to ~1.974 < 2.0, but the tap is fresh so it EXECUTEs.
    entry, stop, target = 240.31, 250.72, 219.49
    now = ISSUED_AT + timedelta(seconds=20)
    price = entry - 0.09
    result = _assess(direction="SHORT", entry=entry, stop=stop, target=target, now=now, price=price)
    assert result.outcome == CardOutcome.EXECUTE
    assert result.r_consumed == pytest.approx(0.00864, abs=1e-4)


def test_gate_reason_still_refuses_a_fresh_tap_after_reordering():
    now = ISSUED_AT + timedelta(seconds=20)
    result = _assess(
        entry=CARD19_ENTRY,
        stop=CARD19_STOP,
        target=CARD19_TARGET,
        now=now,
        price=CARD19_TAPPED_PRICE,
        gate_reason="Current macro/volatility policy requires a higher reward/risk ratio.",
    )
    assert result.outcome == CardOutcome.MISSED
    assert result.reason == "Current macro/volatility policy requires a higher reward/risk ratio."


# --- meets_min_reward_risk: the shared two-decimal comparison helper ----------------------


def test_meets_min_reward_risk_false_for_nonpositive_risk():
    assert meets_min_reward_risk(10.0, 0.0, 2.0) is False
    assert meets_min_reward_risk(10.0, -1.0, 2.0) is False


def test_meets_min_reward_risk_true_at_float_noise_boundary():
    # (261.58-240.46)/(240.46-229.90) == 1.9999999999999973 in raw floats (card #20).
    assert meets_min_reward_risk(1.9999999999999973, 1.0, 2.0) is True


def test_meets_min_reward_risk_rounding_boundary():
    # round(1.994, 2) == 1.99 < 2.0; round(1.995, 2) == 2.0 because the float 1.995 is stored just
    # above the halfway point. A ratio within about 0.005 below the minimum passes, as it does at approval.
    assert meets_min_reward_risk(1.994, 1.0, 2.0) is False
    assert meets_min_reward_risk(1.995, 1.0, 2.0) is True


# --- reprice_quantity -----------------------------------------------------------------


def test_reprice_quantity_keeps_original_risk_dollars():
    # original: 10 units, entry 100, stop 95 -> risk $5/unit -> $50 total risk.
    # new_entry 102, stop unchanged 95 -> risk $7/unit -> quantity = 50/7 = 7.142857
    quantity = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=102.0, multiplier=1.0, whole_units=False
    )
    assert quantity == pytest.approx(50.0 / 7.0)


def test_reprice_quantity_notional_cap():
    # original notional = 10 * 100 = 1000. New entry very close to stop -> risk-preserving
    # quantity would blow up the notional; cap keeps new_quantity*new_entry <= 1000.
    quantity = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=95.5, multiplier=1.0, whole_units=False
    )
    assert quantity * 95.5 <= 1000.0 + 1e-9
    assert quantity == pytest.approx(1000.0 / 95.5)


def test_reprice_quantity_floors_to_whole_units():
    quantity = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=102.0, multiplier=1.0, whole_units=True
    )
    assert quantity == 7.0


def test_reprice_quantity_zero_when_floored_below_one():
    # risk-preserving quantity is small, e.g. new risk per unit is huge relative to original.
    quantity = reprice_quantity(
        original_quantity=1, entry=100.0, stop=95.0, new_entry=200.0, multiplier=1.0, whole_units=True
    )
    assert quantity == 0.0


def test_reprice_quantity_zero_when_new_risk_is_zero():
    quantity = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=95.0, multiplier=1.0, whole_units=False
    )
    assert quantity == 0.0


def test_reprice_quantity_multiplier_cancels_in_ratio():
    plain = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=102.0, multiplier=1.0, whole_units=False
    )
    scaled = reprice_quantity(
        original_quantity=10, entry=100.0, stop=95.0, new_entry=102.0, multiplier=50.0, whole_units=False
    )
    assert plain == pytest.approx(scaled)


def test_degenerate_levels_are_missed_not_a_crash():
    result = _assess(entry=100.0, stop=100.0, target=120.0, price=100.0)
    assert result.outcome == CardOutcome.MISSED
    assert result.r_consumed is None


@pytest.mark.parametrize("field", ["issued_at", "valid_until", "now"])
def test_naive_datetimes_are_rejected_on_every_path(field):
    naive = {"issued_at": ISSUED_AT, "valid_until": VALID_UNTIL, "now": ISSUED_AT}
    naive[field] = naive[field].replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        _assess(**naive)


# --- parse_valid_until: the shared provenance parser -----------------------------------


def test_parse_valid_until_accepts_an_offset_iso_string():
    value = VALID_UNTIL.isoformat()
    assert parse_valid_until(value) == VALID_UNTIL


@pytest.mark.parametrize("raw", [None, "", "not-a-timestamp", "2026-09-23T20:00:00"])  # last: offset-naive
def test_parse_valid_until_falls_back_to_none(raw):
    assert parse_valid_until(raw) is None


# --- valid_until_from_provenance: the single extraction point shared by tap and sweep --


def test_valid_until_from_provenance_extracts_and_parses_the_key():
    provenance = {"valid_until": VALID_UNTIL.isoformat(), "setup_quality": 0.9}
    assert valid_until_from_provenance(provenance) == VALID_UNTIL


@pytest.mark.parametrize(
    "provenance", [None, {}, {"valid_until": None}, {"valid_until": "garbage"}, "not-a-dict", 42, ["valid_until"]]
)
def test_valid_until_from_provenance_falls_back_to_none(provenance):
    assert valid_until_from_provenance(provenance) is None


# --- card_session_over: the shared expiry rule behind assess_card and the sweep --------


def test_card_session_over_true_at_and_past_valid_until():
    assert card_session_over(ISSUED_AT, VALID_UNTIL, VALID_UNTIL) is True
    assert card_session_over(ISSUED_AT, VALID_UNTIL, VALID_UNTIL + timedelta(seconds=1)) is True


def test_card_session_over_false_before_valid_until():
    assert card_session_over(ISSUED_AT, VALID_UNTIL, VALID_UNTIL - timedelta(seconds=1)) is False


def test_card_session_over_legacy_same_ny_date_is_not_over():
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)  # 10:00 ET
    now = datetime(2026, 9, 23, 15, 0, 0, tzinfo=UTC)  # 11:00 ET, same NY date
    assert card_session_over(issued_at, None, now) is False


def test_card_session_over_legacy_next_ny_date_is_over():
    issued_at = datetime(2026, 9, 23, 23, 30, 0, tzinfo=UTC)  # 19:30 ET Sept 23
    now = datetime(2026, 9, 24, 5, 0, 0, tzinfo=UTC)  # 01:00 ET Sept 24
    assert card_session_over(issued_at, None, now) is True


# --- card_is_stale: the sweep rule, exercised through raw provenance ------------------


def test_card_is_stale_true_once_now_reaches_valid_until():
    provenance = {"valid_until": VALID_UNTIL.isoformat()}
    assert card_is_stale(issued_at=ISSUED_AT, decision_provenance=provenance, now=VALID_UNTIL) is True
    assert (
        card_is_stale(issued_at=ISSUED_AT, decision_provenance=provenance, now=VALID_UNTIL - timedelta(seconds=1))
        is False
    )


def test_card_is_stale_legacy_previous_ny_date_is_stale():
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)  # 10:00 ET Sept 23
    now = datetime(2026, 9, 24, 15, 0, 0, tzinfo=UTC)  # 11:00 ET Sept 24
    assert card_is_stale(issued_at=issued_at, decision_provenance=None, now=now) is True


def test_card_is_stale_legacy_same_ny_date_is_not_stale():
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)  # 10:00 ET Sept 23
    now = datetime(2026, 9, 23, 20, 0, 0, tzinfo=UTC)  # 16:00 ET Sept 23, still same NY date
    assert card_is_stale(issued_at=issued_at, decision_provenance=None, now=now) is False


def test_card_is_stale_legacy_utc_evening_timestamp_still_same_ny_date():
    # Crosses UTC midnight but stays within the same New York calendar date (Sept 23,
    # EDT = UTC-4): a naive UTC-date comparison would wrongly call this stale.
    issued_at = datetime(2026, 9, 23, 23, 30, 0, tzinfo=UTC)  # 19:30 ET Sept 23
    now = datetime(2026, 9, 24, 2, 0, 0, tzinfo=UTC)  # 22:00 ET Sept 23, still Sept 23 in NY
    assert card_is_stale(issued_at=issued_at, decision_provenance=None, now=now) is False


@pytest.mark.parametrize("raw_valid_until", ["not-a-timestamp", "2026-09-23T20:00:00", ""])  # unparseable or naive
def test_card_is_stale_unparseable_or_naive_valid_until_falls_back_to_legacy_rule(raw_valid_until):
    provenance = {"valid_until": raw_valid_until}
    # Legacy rule: same NY date as issue -> not stale, even though valid_until is unusable.
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)
    same_date_now = datetime(2026, 9, 23, 20, 0, 0, tzinfo=UTC)
    assert card_is_stale(issued_at=issued_at, decision_provenance=provenance, now=same_date_now) is False
    next_date_now = datetime(2026, 9, 24, 15, 0, 0, tzinfo=UTC)
    assert card_is_stale(issued_at=issued_at, decision_provenance=provenance, now=next_date_now) is True


def test_card_is_stale_missing_valid_until_key_falls_back_to_legacy_rule():
    issued_at = datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)
    assert (
        card_is_stale(issued_at=issued_at, decision_provenance={}, now=datetime(2026, 9, 23, 20, 0, tzinfo=UTC))
        is False
    )
