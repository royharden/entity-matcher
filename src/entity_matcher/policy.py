"""Calibrated machine proposals only; no human events, merges or journal writes."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from .calibrate import CalibrationContext, CalibrationModel, attach_calibration
from .shared.match.models import (
    FrozenContract,
    JudgmentProposal,
    PairIdentity,
    ReviewProposal,
    UnitFloat,
)


class ProposalPolicy(FrozenContract):
    merge_at: UnitFloat = 0.99
    unlinked_at: UnitFloat = 0.1

    @model_validator(mode="after")
    def separate_bands(self) -> Self:
        if self.unlinked_at >= self.merge_at:
            raise ValueError("Proposal bands must be strictly separated")
        return self


def propose_review(
    pair: PairIdentity,
    raw_judgment: JudgmentProposal,
    calibration: CalibrationModel,
    *,
    expected_context: CalibrationContext,
    policy: ProposalPolicy | None = None,
) -> ReviewProposal:
    """Any named raw issue or unsupported probability requires human review.

    Recompute probability through the supplied bound artifact rather than trusting p_same input.
    Threshold defaults are untuned; applying this function does not demonstrate accuracy.
    """
    pair = PairIdentity.model_validate(pair)
    policy = ProposalPolicy() if policy is None else ProposalPolicy.model_validate(policy)
    judgment = attach_calibration(raw_judgment, calibration, expected_context=expected_context)
    outcome = "review"
    reasons = judgment.issue_codes
    if judgment.status != "ok":
        reasons = (*reasons, "judgment_unavailable")[:32]
    elif judgment.p_same is None:
        reasons = (*reasons, "calibration_unsupported")[:32]
    elif not reasons:
        if judgment.p_same >= policy.merge_at:
            outcome = "merge"
        elif judgment.p_same <= policy.unlinked_at:
            outcome = "unlinked"
        else:
            reasons = ("probability_review_band",)
    return ReviewProposal(
        pair=pair, judgment=judgment, proposed_outcome=outcome, reason_codes=reasons
    )
