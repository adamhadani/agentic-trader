"""Source row accounting, independent from whether market coverage is complete."""

from collections.abc import Sequence
from dataclasses import dataclass


SOURCE_QUALITY_ATTR = "source_quality"
SOURCE_QUALITY_VERSION = "bar_source_quality_v1"


@dataclass(frozen=True)
class BarSourceQuality:
    raw_rows: int | None
    parsed_rows: int
    normalized_rows: int

    def __post_init__(self):
        counts: tuple[int, ...] = (self.parsed_rows, self.normalized_rows)
        if self.raw_rows is not None:
            counts = (self.raw_rows, *counts)
        if (
            any(type(value) is not int or value < 0 for value in counts)
            or tuple(sorted(counts, reverse=True)) != counts
        ):
            raise ValueError("Consistent nonnegative source row counts required")

    def document(self) -> dict:
        return {
            "version": SOURCE_QUALITY_VERSION,
            "raw_rows": self.raw_rows,
            "parsed_rows": self.parsed_rows,
            "normalized_rows": self.normalized_rows,
            "sdk_omitted_rows": None if self.raw_rows is None else self.raw_rows - self.parsed_rows,
            "normalization_dropped_rows": self.parsed_rows - self.normalized_rows,
        }

    @classmethod
    def from_document(cls, document: dict):
        if not isinstance(document, dict):
            raise TypeError("Explicit source quality document required")
        try:
            quality = cls(*(document[name] for name in ("raw_rows", "parsed_rows", "normalized_rows")))
        except KeyError as exc:
            raise ValueError("Complete source quality row counts required") from exc
        canonical = quality.document()
        if canonical != document or any(type(document[key]) is not type(value) for key, value in canonical.items()):
            raise ValueError("Exact versioned source quality document required")
        return quality

    @classmethod
    def combine(cls, qualities: Sequence[BarSourceQuality]):
        if not qualities:
            raise ValueError("At least one acquisition quality summary required")
        raw_rows = (
            sum(q.raw_rows for q in qualities if q.raw_rows is not None)
            if all(q.raw_rows is not None for q in qualities)
            else None
        )
        if raw_rows is None and any(q.raw_rows is not None and q.raw_rows > q.parsed_rows for q in qualities):
            raise ValueError("Unknown raw counts cannot erase known SDK omissions")
        return cls(raw_rows, sum(q.parsed_rows for q in qualities), sum(q.normalized_rows for q in qualities))

    def require_lossless(self, *, frame_rows: int) -> None:
        if frame_rows != self.normalized_rows:
            raise ValueError("Source quality does not match normalized frame rows")
        if self.normalized_rows != self.parsed_rows or (
            self.raw_rows is not None and self.raw_rows != self.parsed_rows
        ):
            raise ValueError("Source rows were discarded during SDK parsing or normalization")
