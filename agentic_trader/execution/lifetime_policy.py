"""Pure elapsed-time contract shared by research and broker lifecycle services."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


MAX_TRADE_LIFETIME_SECONDS = int(timedelta(days=31).total_seconds())
TRADE_LIFETIME_VERSION = "elapsed_utc_v1"
TRADE_LIFETIME_VERSION_INDEPENDENT = "elapsed_utc_v2"

# One regular trading session. Research stamps a daily order at its entry bar's label, and
# validate_sampling guarantees consecutive daily labels are at least 24 h apart, so label + 16 h
# always expires before the next bar's fill check. Live stamps the order at submission inside
# 09:30-16:00 New York, so + 16 h expires before the next open. 86,400 would give a live order
# two partial sessions; anything under 23,400 would not survive its own session.
DAILY_ENTRY_LIFETIME_SECONDS = 57_600


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Lifetime clocks require timezone-aware datetimes")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TradeLifetimePolicy:
    resting_seconds: int | None
    holding_seconds: int | None
    version: str = TRADE_LIFETIME_VERSION

    def __post_init__(self):
        if self.version not in (TRADE_LIFETIME_VERSION, TRADE_LIFETIME_VERSION_INDEPENDENT):
            raise ValueError("Unsupported trade lifetime version")
        if self.version == TRADE_LIFETIME_VERSION and (self.resting_seconds is None or self.holding_seconds is None):
            raise ValueError("elapsed_utc_v1 requires both entry and holding lifetimes")
        if (
            self.version == TRADE_LIFETIME_VERSION_INDEPENDENT
            and self.resting_seconds is None
            and self.holding_seconds is None
        ):
            raise ValueError("At least one independent trade lifetime is required")
        for value in (self.resting_seconds, self.holding_seconds):
            if value is not None and (type(value) is not int or not 1 <= value <= MAX_TRADE_LIFETIME_SECONDS):
                raise ValueError("Trade lifetimes require bounded positive integer seconds")

    def entry_deadline(self, submitted_at: datetime) -> datetime | None:
        if self.resting_seconds is None:
            return None
        return aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)

    def holding_deadline(self, filled_at: datetime) -> datetime | None:
        if self.holding_seconds is None:
            return None
        return aware_utc(filled_at) + timedelta(seconds=self.holding_seconds)


def daily_entry_lifetime() -> TradeLifetimePolicy:
    """The only lifetime a native-daily (semantics version 5) alpha may declare."""
    return TradeLifetimePolicy(
        resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
        holding_seconds=None,
        version=TRADE_LIFETIME_VERSION_INDEPENDENT,
    )
