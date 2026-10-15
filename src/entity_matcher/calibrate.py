"""Explicit DEV-derived empirical artifacts; no fitting, label access or quality certification."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .shared.match.models import (
    CalibrationEvidence,
    Digest,
    FrozenContract,
    JudgmentProposal,
    UnitFloat,
)

Method = Literal["fuzzy", "rules", "jev", "llm"]


class CalibrationContext(FrozenContract):
    method: Method
    cohort_id: Annotated[str, Field(min_length=1, max_length=256)]
    dev_manifest_sha256: Digest
    config_sha256: Digest
    split: Literal["dev"] = "dev"


class EmpiricalBin(FrozenContract):
    lo: UnitFloat
    hi: UnitFloat
    count: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    positive_count: Annotated[int, Field(strict=True, ge=0, le=100_000)]

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if self.lo >= self.hi or self.positive_count > self.count:
            raise ValueError("Invalid empirical interval or count")
        return self


class CalibrationModel(FrozenContract):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    context: CalibrationContext
    min_bin_support: Annotated[int, Field(strict=True, ge=1, le=100_000)] = 20
    bins: Annotated[tuple[EmpiricalBin, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def sorted_nonoverlapping(self) -> Self:
        for left, right in zip(self.bins, self.bins[1:], strict=False):
            if left.hi > right.lo:
                raise ValueError("Empirical bins overlap or are out of order")
        return self


def attach_calibration(
    proposal: JudgmentProposal,
    model: CalibrationModel,
    *,
    expected_context: CalibrationContext,
) -> JudgmentProposal:
    """Attach only matching supported metadata; context mismatch is an explicit failure.

    Intervals are [lo, hi), except hi=1 includes exactly 1. Gaps and sparse bins stay unsupported.
    Serialized counts remain claims by the artifact producer, not proof of provenance or a floor.
    """
    proposal = JudgmentProposal.model_validate(proposal)
    model = CalibrationModel.model_validate(model)
    expected_context = CalibrationContext.model_validate(expected_context)
    if model.context != expected_context or proposal.method != expected_context.method:
        raise ValueError("Calibration method, cohort, manifest or config mismatch")
    if proposal.p_same is not None or proposal.calibration is not None:
        raise ValueError("Refuse replacing attached calibration")
    if proposal.status != "ok":
        return proposal
    score = proposal.relatedness
    assert score is not None
    for bucket in model.bins:
        if bucket.lo <= score and (score < bucket.hi or score == bucket.hi == 1.0):
            if bucket.count < model.min_bin_support:
                return proposal
            return JudgmentProposal(
                **proposal.model_dump(exclude={"p_same", "calibration"}),
                p_same=bucket.positive_count / bucket.count,
                calibration=CalibrationEvidence(
                    dev_manifest_sha256=model.context.dev_manifest_sha256,
                    config_sha256=model.context.config_sha256,
                    support_count=bucket.count,
                ),
            )
    return proposal
