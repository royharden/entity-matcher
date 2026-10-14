"""Product-domain exact raw-field evidence, without generic Pointer dispatch or I/O."""

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from .models import FieldEvidenceIdentity, FieldId, RawProduct
from .normalize import NormalizedRecord


@dataclass(frozen=True)
class RawFieldResolution:
    status: Literal["valid", "stale", "missing", "invalid"]
    quote: str | None
    issue_code: str | None = None


def field_evidence(
    record: NormalizedRecord, fields: tuple[FieldId, ...]
) -> tuple[FieldEvidenceIdentity, ...]:
    """Nonblank fields only. Unknown/model-selected path strings reject before any lookup.

    These are producer-domain metadata, not installed generic Pointer instances.
    """
    raw = RawProduct.model_validate(record.raw)
    if record.ref.record_id != raw.id or record.ref.content_hash != raw.fingerprint:
        raise ValueError("Stale normalized record reference")
    if len(set(fields)) != len(fields):
        raise ValueError("Duplicate requested evidence field")
    result = []
    for field in fields:
        evidence = FieldEvidenceIdentity.from_raw(record.ref.dataset_id, raw, field)
        if evidence is not None:
            result.append(evidence)
    return tuple(result)


def resolve_raw_field(
    evidence: FieldEvidenceIdentity,
    *,
    dataset_id: str,
    raw: RawProduct | None,
) -> RawFieldResolution:
    """Check retained raw data without fetching, path access or retargeting current bytes."""
    try:
        accepted = FieldEvidenceIdentity.model_validate(evidence)
    except ValidationError:
        return RawFieldResolution("invalid", None, "invalid_evidence_metadata")
    if accepted.dataset_id != dataset_id:
        return RawFieldResolution("invalid", None, "wrong_evidence_namespace")
    if raw is None:
        return RawFieldResolution("missing", None, "missing_raw_record")
    try:
        record = RawProduct.model_validate(raw)
    except ValidationError:
        return RawFieldResolution("invalid", None, "invalid_raw_record")
    if record.id != accepted.record_id:
        return RawFieldResolution("missing", None, "missing_raw_identity")
    if record.fingerprint != accepted.source_sha256:
        return RawFieldResolution("stale", None, "raw_generation_changed")
    expected = FieldEvidenceIdentity.from_raw(dataset_id, record, accepted.field_id)
    if expected is None or expected != accepted:
        return RawFieldResolution("invalid", None, "field_artifact_or_excerpt_disagrees")
    return RawFieldResolution("valid", record.model_dump()[accepted.field_id])
