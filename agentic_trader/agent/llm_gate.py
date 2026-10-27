"""The native-card LLM gate's reply classifier (docs/superpowers/specs/2026-10-27-llm-gate-hygiene-design.md §2.2).

A gate reply has exactly three outcomes. Only a reply that parses, validates and whose raw ``approved`` is
the JSON boolean ``true`` approves; the raw value is read before pydantic's lax bool coercion, so ``"true"``,
``"yes"`` and ``1`` never approve. A reply that parses as a JSON object but does not approve is a veto,
``malformed`` when it fails validation or its ``approved`` is not a JSON boolean. Anything else is an
error: an exception (the deadline's is ``TimeoutError``), a non-JSON or non-object reply, or a JSON-``true``
approval that fails validation. An error is named by its type only, never by exception text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator


__all__ = [
    "LLM_KEY_MISSING",
    "MALFORMED_VETO_REASON",
    "VETO_WITHOUT_REASON",
    "GateReply",
    "GateReplyFields",
    "classify_gate_reply",
]

# A veto's reason when the reply gives no non-blank string: a malformed reply's, and a well-formed one's.
MALFORMED_VETO_REASON = "malformed veto"
VETO_WITHOUT_REASON = "veto without a reason"
# The error type name of an LLM scan with no LLM API key configured (not an exception: nothing is called).
LLM_KEY_MISSING = "LLMKeyMissing"


class GateReplyFields(BaseModel):
    """The reply fields the evaluator reads, typed as ``LLMTradeEvaluation`` types them.

    Every other key in the reply (the bracket arithmetic, ``macro_clearance``, the desk's own blocks) is
    computed by the desk and never read from it.
    """

    approved: bool
    rejection_reason: str | None = None
    thesis_summary: str
    # Absent: the deterministic level. Present: a number, as the evaluator's bracket checks read it.
    stop_loss: float | None = None
    take_profit: float | None = None

    @field_validator("stop_loss", "take_profit", mode="before")
    @classmethod
    def present_levels_are_numbers(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("a proposed level must be a number")
        return value


@dataclass(frozen=True)
class GateReply:
    """One classified gate reply.

    ``data`` is None for an error. For an approval or a well-formed veto it holds the validated fields the
    reply set, a well-formed veto's ``rejection_reason`` always a non-blank string (``veto without a reason``
    when the reply gives none). For a malformed veto it holds only ``approved`` (false), ``rejection_reason``
    (``malformed veto`` when the reply gives none) and, when the reply's is a string, ``thesis_summary``: its
    levels are not read. ``error_type`` names the error's type; ``malformed`` is only ever true for a veto.
    """

    kind: Literal["approve", "veto", "error"]
    data: dict[str, Any] | None
    error_type: str | None
    malformed: bool


def _error(error_type: str) -> GateReply:
    return GateReply(kind="error", data=None, error_type=error_type, malformed=False)


def _veto_reason(payload: dict[str, Any], default: str) -> str:
    """The reply's reason when it is a non-blank string, else ``default``."""
    reason = payload.get("rejection_reason")
    return reason if isinstance(reason, str) and reason.strip() else default


def _malformed_veto(payload: dict[str, Any]) -> GateReply:
    data: dict[str, Any] = {"approved": False, "rejection_reason": _veto_reason(payload, MALFORMED_VETO_REASON)}
    if isinstance(thesis := payload.get("thesis_summary"), str):
        data["thesis_summary"] = thesis
    return GateReply(kind="veto", data=data, error_type=None, malformed=True)


def classify_gate_reply(content: str | None, *, error: BaseException | None = None) -> GateReply:
    """Classify one gate reply (pure). ``error`` is the exception the call raised, if any."""
    if error is not None:
        return _error(type(error).__name__)
    text = content.strip() if isinstance(content, str) else ""
    try:
        # Strip possible markdown wrapping, as the gate always has.
        payload = json.loads(re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip())
    except (ValueError, RecursionError) as exc:
        return _error(type(exc).__name__)
    if not isinstance(payload, dict):
        return _error(TypeError.__name__)
    approved = payload.get("approved")
    try:
        fields = GateReplyFields.model_validate(payload)
    except ValidationError as exc:
        return _error(type(exc).__name__) if approved is True else _malformed_veto(payload)
    if approved is True:
        return GateReply(kind="approve", data=fields.model_dump(exclude_unset=True), error_type=None, malformed=False)
    if approved is False:
        data = {**fields.model_dump(exclude_unset=True), "rejection_reason": _veto_reason(payload, VETO_WITHOUT_REASON)}
        return GateReply(kind="veto", data=data, error_type=None, malformed=False)
    return _malformed_veto(payload)
