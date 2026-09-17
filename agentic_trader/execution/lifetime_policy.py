"""Pure elapsed-time contract shared by research and broker lifecycle services."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


MAX_TRADE_LIFETIME_SECONDS = int(timedelta(days=31).total_seconds())
TRADE_LIFETIME_VERSION = "elapsed_utc_v1"


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Lifetime clocks require timezone-aware datetimes")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TradeLifetimePolicy:
    resting_seconds: int
    holding_seconds: int
    version: str = TRADE_LIFETIME_VERSION

    def __post_init__(self):
        if self.version != TRADE_LIFETIME_VERSION:
            raise ValueError("Unsupported trade lifetime version")
        for value in (self.resting_seconds, self.holding_seconds):
            if type(value) is not int or not 1 <= value <= MAX_TRADE_LIFETIME_SECONDS:
                raise ValueError("Trade lifetimes require bounded positive integer seconds")

    def entry_deadline(self, submitted_at: datetime) -> datetime:
        return aware_utc(submitted_at) + timedelta(seconds=self.resting_seconds)

    def holding_deadline(self, filled_at: datetime) -> datetime:
        return aware_utc(filled_at) + timedelta(seconds=self.holding_seconds)
