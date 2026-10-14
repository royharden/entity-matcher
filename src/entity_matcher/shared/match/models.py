"""Strict immutable matching contracts; validation does not certify a match or ruling."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import identity

Cell = Annotated[str, Field(max_length=8192, json_schema_extra={"x-maxUtf8Bytes": 8192})]
OpaqueId = Annotated[
    str, Field(min_length=1, max_length=8192, json_schema_extra={"x-maxUtf8Bytes": 8192})
]
DatasetId = Annotated[
    str, Field(min_length=1, max_length=256, json_schema_extra={"x-maxUtf8Bytes": 256})
]
Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
UnitFloat = Annotated[float, Field(ge=0, le=1)]
Code = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]*$")]
FieldId = Literal["id", "name", "brand", "size", "unit", "price", "category", "description"]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class RawProduct(FrozenContract):
    """Eight decoded raw strings; no price, size or unit parsing occurs here."""

    id: OpaqueId
    name: Annotated[Cell, Field(min_length=1)]
    brand: Cell
    size: Cell
    unit: Cell
    price: Cell
    category: Cell
    description: Cell

    @model_validator(mode="after")
    def raw_bounds(self) -> Self:
        identity.raw_fields(self.model_dump())
        return self

    @property
    def fingerprint(self) -> str:
        return identity.raw_record_fingerprint(self.model_dump())

    @property
    def generation(self) -> str:
        return "rg1:" + self.fingerprint


class RecordIdentity(FrozenContract):
    dataset_id: DatasetId
    record_id: OpaqueId

    @model_validator(mode="after")
    def identity_bounds(self) -> Self:
        identity.record_identity(self.identity_fields())
        return self

    def identity_fields(self) -> dict[str, str]:
        return {"dataset_id": self.dataset_id, "record_id": self.record_id}


class RecordRef(RecordIdentity):
    content_hash: Digest

    @property
    def stable_identity(self) -> RecordIdentity:
        return RecordIdentity(**self.identity_fields())

    @classmethod
    def from_raw(cls, dataset_id: str, raw: RawProduct) -> Self:
        return cls(dataset_id=dataset_id, record_id=raw.id, content_hash=raw.fingerprint)


class PairIdentity(FrozenContract):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    refs: tuple[RecordIdentity, RecordIdentity]
    raw_fingerprints: tuple[Digest, Digest]
    pair_key: Annotated[str, Field(pattern=r"^pk1:[0-9a-f]{64}$", min_length=68, max_length=68)]
    pair_generation: Annotated[
        str, Field(pattern=r"^pg1:[0-9a-f]{64}$", min_length=68, max_length=68)
    ]

    @model_validator(mode="after")
    def identities_recompute(self) -> Self:
        left, right = (ref.identity_fields() for ref in self.refs)
        if identity.canonical_bytes(left) >= identity.canonical_bytes(right):
            raise ValueError("Pair refs require distinct canonical orientation")
        if self.pair_key != identity.pair_key(left, right):
            raise ValueError("Stable pair key disagrees with clear identity refs")
        if self.pair_generation != identity.pair_generation(
            left, self.raw_fingerprints[0], right, self.raw_fingerprints[1]
        ):
            raise ValueError("Pair generation disagrees with aligned fingerprints")
        return self

    @classmethod
    def from_refs(cls, left: RecordRef, right: RecordRef) -> Self:
        ordered = sorted(
            (left, right), key=lambda ref: identity.canonical_bytes(ref.identity_fields())
        )
        a, b = ordered
        return cls(
            refs=(a.stable_identity, b.stable_identity),
            raw_fingerprints=(a.content_hash, b.content_hash),
            pair_key=identity.pair_key(a.identity_fields(), b.identity_fields()),
            pair_generation=identity.pair_generation(
                a.identity_fields(), a.content_hash, b.identity_fields(), b.content_hash
            ),
        )


class FieldEvidenceIdentity(RecordIdentity):
    """Domain digest metadata only, not a generic Pointer or a resolver."""

    field_id: FieldId
    source_sha256: Digest
    source_revision: Annotated[
        str, Field(pattern=r"^rg1:[0-9a-f]{64}$", min_length=68, max_length=68)
    ]
    evidence_artifact_sha256: Digest
    excerpt_sha256: Digest

    @model_validator(mode="after")
    def generation_agrees(self) -> Self:
        if self.source_revision != "rg1:" + self.source_sha256:
            raise ValueError("Raw field generation disagrees with source fingerprint")
        return self

    @classmethod
    def from_raw(cls, dataset_id: str, raw: RawProduct, field_id: FieldId) -> Self | None:
        ref = {"dataset_id": dataset_id, "record_id": raw.id}
        values = raw.model_dump()
        artifact = identity.field_evidence_digest(ref, values, field_id)
        excerpt = identity.field_excerpt_digest(values, field_id)
        if artifact is None or excerpt is None:
            return None
        return cls(
            **ref,
            field_id=field_id,
            source_sha256=raw.fingerprint,
            source_revision=raw.generation,
            evidence_artifact_sha256=artifact,
            excerpt_sha256=excerpt,
        )


class CalibrationEvidence(FrozenContract):
    dev_manifest_sha256: Digest
    config_sha256: Digest
    support_count: Annotated[int, Field(strict=True, ge=1)]


class JudgmentProposal(FrozenContract):
    """Raw signals plus explicit calibration provenance, never a human event."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    kind: Literal["judgment_proposal"] = "judgment_proposal"
    method: Literal["fuzzy", "rules", "jev", "llm"]
    status: Literal["ok", "unavailable", "invalid", "error"]
    relatedness: UnitFloat | None = None
    native_confidence: UnitFloat | None = None
    native_probabilities: (
        Annotated[tuple[UnitFloat, ...], Field(min_length=2, max_length=10)] | None
    ) = None
    p_same: UnitFloat | None = None
    calibration: CalibrationEvidence | None = None
    issue_codes: Annotated[tuple[Code, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def signals_are_not_authority(self) -> Self:
        if self.native_probabilities is not None and abs(
            math.fsum(self.native_probabilities) - 1
        ) > (1e-6):
            raise ValueError("Native probability distribution must sum to one")
        if (self.p_same is None) != (self.calibration is None):
            raise ValueError("Calibrated probability requires explicit supported provenance")
        if self.status == "ok" and self.relatedness is None:
            raise ValueError("Successful proposal requires raw relatedness")
        if self.status != "ok":
            if not self.issue_codes:
                raise ValueError("Failed proposal requires a named issue")
            if any(
                signal is not None
                for signal in (
                    self.relatedness,
                    self.native_confidence,
                    self.native_probabilities,
                    self.p_same,
                    self.calibration,
                )
            ):
                raise ValueError("Failed proposal cannot expose usable judgment signals")
        return self


class ReviewProposal(FrozenContract):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    kind: Literal["review_proposal"] = "review_proposal"
    pair: PairIdentity
    judgment: JudgmentProposal
    proposed_outcome: Literal["unlinked", "review", "merge"]
    reason_codes: Annotated[tuple[Code, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def unresolved_signals_require_review(self) -> Self:
        if self.judgment.p_same is None and self.proposed_outcome != "review":
            raise ValueError("Unsupported or failed calibration requires review")
        return self


SCHEMA_MODELS = (
    RawProduct,
    RecordIdentity,
    RecordRef,
    PairIdentity,
    FieldEvidenceIdentity,
    CalibrationEvidence,
    JudgmentProposal,
    ReviewProposal,
)
