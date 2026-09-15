from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agentic_trader.research.alpha.models import (
    AlphaCandidate,
    AlphaDefinition,
    AlphaStatus,
    PromotedAlphaRecord,
)


logger = logging.getLogger(__name__)

DEFAULT_PROMOTED_ALPHAS_PATH = Path("config/promoted_alphas.yaml")


class AlphaPromotionManager:
    """
    Manages the lifecycle, audit logging, and configuration persistence
    for production-promoted formulaic alpha trading strategies.
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path or DEFAULT_PROMOTED_ALPHAS_PATH)

    def load_records(self) -> list[PromotedAlphaRecord]:
        """Load all promoted alpha records from the YAML storage file."""
        if not self.config_path.exists():
            return []

        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            raw_alphas = data.get("promoted_alphas", [])
            return [PromotedAlphaRecord.from_dict(item) for item in raw_alphas if isinstance(item, dict)]
        except Exception as e:
            logger.error("Failed loading promoted alphas from %s: %s", self.config_path, e)
            return []

    def get_record(self, alpha_id: str) -> PromotedAlphaRecord | None:
        """Retrieve a promoted alpha record by ID (case-insensitive)."""
        target = alpha_id.lower()
        for rec in self.load_records():
            if rec.alpha_id.lower() == target:
                return rec
        return None

    def list_active_alphas(self) -> list[PromotedAlphaRecord]:
        """Return all currently active promoted alphas (status == PROMOTED)."""
        return [r for r in self.load_records() if r.status == AlphaStatus.PROMOTED]

    def promote(
        self,
        alpha: AlphaCandidate | AlphaDefinition,
        promoted_by: str = "cli_operator",
        allocation_weight: float = 0.10,
        notes: str = "",
    ) -> PromotedAlphaRecord:
        """
        Promote an alpha candidate or definition to production status.
        Persists record atomically to config/promoted_alphas.yaml.
        """
        if isinstance(alpha, AlphaCandidate):
            definition = alpha.definition
            metrics = alpha.metrics
        else:
            definition = alpha
            metrics = None

        alpha_id = definition.alpha_id.lower()
        now_iso = datetime.now(UTC).isoformat()

        record = PromotedAlphaRecord(
            alpha_id=alpha_id,
            definition=definition,
            metrics=metrics,
            promoted_at=now_iso,
            promoted_by=promoted_by,
            allocation_weight=max(0.01, min(1.0, allocation_weight)),
            status=AlphaStatus.PROMOTED,
            notes=notes,
        )

        existing_records = self.load_records()
        updated_records: list[PromotedAlphaRecord] = []
        replaced = False

        for r in existing_records:
            if r.alpha_id.lower() == alpha_id:
                updated_records.append(record)
                replaced = True
            else:
                updated_records.append(r)

        if not replaced:
            updated_records.append(record)

        self._save_records(updated_records)
        logger.info(
            "Promoted alpha '%s' to production by %s (weight: %.2f)",
            alpha_id,
            promoted_by,
            allocation_weight,
            extra={"alpha_id": alpha_id, "action": "promote", "promoted_by": promoted_by},
        )
        return record

    def demote(
        self,
        alpha_id: str,
        reason: str = "",
        demoted_by: str = "cli_operator",
        notes: str = "",
    ) -> PromotedAlphaRecord | None:
        """
        Demote/deprecate an active alpha, removing it from production scans.
        """
        target = alpha_id.lower()
        existing_records = self.load_records()
        demoted_rec: PromotedAlphaRecord | None = None
        updated_records: list[PromotedAlphaRecord] = []

        for r in existing_records:
            if r.alpha_id.lower() == target:
                r.status = AlphaStatus.DEMOTED
                details = reason or notes
                if details:
                    r.notes = f"{r.notes} [Demoted: {details}]".strip()
                updated_records.append(r)
                demoted_rec = r
            else:
                updated_records.append(r)

        if demoted_rec:
            self._save_records(updated_records)
            logger.info(
                "Demoted alpha '%s' from production by %s. Reason: %s",
                target,
                demoted_by,
                reason or notes or "manual demotion",
            )
            return demoted_rec
        return None

    def _save_records(self, records: list[PromotedAlphaRecord]) -> None:
        """Atomically persist records to YAML."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "last_updated": datetime.now(UTC).isoformat(),
            "promoted_alphas": [r.to_dict() for r in records],
        }

        temp_file = self.config_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
        temp_file.replace(self.config_path)
