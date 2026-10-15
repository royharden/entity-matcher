"""Toy arithmetic and fail-closed context controls; no independent benchmark labels."""

import pytest
from pydantic import ValidationError

from entity_matcher.calibrate import (
    CalibrationContext,
    CalibrationModel,
    EmpiricalBin,
    attach_calibration,
)
from entity_matcher.policy import ProposalPolicy, propose_review
from entity_matcher.shared.match.models import JudgmentProposal, PairIdentity, RecordRef


def context(**changes):
    return CalibrationContext(
        **{
            "method": "fuzzy",
            "cohort_id": "toy",
            "dev_manifest_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            **changes,
        }
    )


def artifact(*, positives=20, count=20, lo=0.0, hi=1.0):
    return CalibrationModel(
        context=context(),
        bins=(
            EmpiricalBin(
                lo=lo,
                hi=hi,
                count=count,
                positive_count=positives,
            ),
        ),
    )


def raw(score=0.9, **changes):
    return JudgmentProposal(method="fuzzy", status="ok", relatedness=score, **changes)


def pair():
    return PairIdentity.from_refs(
        RecordRef(dataset_id="A", record_id="one", content_hash="c" * 64),
        RecordRef(dataset_id="B", record_id="two", content_hash="d" * 64),
    )


@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
def test_probability_is_empirical_not_raw_or_native(score):
    result = attach_calibration(
        raw(score, native_confidence=0.99), artifact(positives=5), expected_context=context()
    )
    assert result.p_same == 0.25
    assert result.calibration.support_count == 20
    assert result.relatedness == score


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "rules"},
        {"cohort_id": "other"},
        {"dev_manifest_sha256": "e" * 64},
        {"config_sha256": "f" * 64},
    ],
)
def test_context_mismatch_is_not_attached(changes):
    with pytest.raises(ValueError, match="mismatch"):
        attach_calibration(raw(), artifact(), expected_context=context(**changes))


def test_heldout_token_rejected():
    with pytest.raises(ValidationError):
        context(split="heldout")


@pytest.mark.parametrize(
    "values",
    [
        {"lo": 0.5, "hi": 0.5},
        {"lo": 0.7, "hi": 0.6},
        {"lo": float("nan")},
        {"hi": float("inf")},
        {"count": True},
        {"count": 0},
        {"positive_count": 21},
        {"positive_count": -1},
        {"count": "20"},
    ],
)
def test_bin_malformed_values_rejected(values):
    with pytest.raises(ValidationError):
        EmpiricalBin(**{"lo": 0.0, "hi": 1.0, "count": 20, "positive_count": 20, **values})


def test_bin_overlap_and_order_rejected():
    a = EmpiricalBin(lo=0.0, hi=0.6, count=20, positive_count=0)
    b = EmpiricalBin(lo=0.5, hi=1.0, count=20, positive_count=20)
    for bins in [(a, b), (b, a)]:
        with pytest.raises(ValidationError):
            CalibrationModel(context=context(), bins=bins)


def test_shared_boundary_belongs_to_right_bucket():
    model = CalibrationModel(
        context=context(),
        bins=(
            EmpiricalBin(lo=0.0, hi=0.5, count=20, positive_count=0),
            EmpiricalBin(lo=0.5, hi=1.0, count=20, positive_count=20),
        ),
    )
    assert attach_calibration(raw(0.5), model, expected_context=context()).p_same == 1.0


@pytest.mark.parametrize("model", [artifact(count=19, positives=19), artifact(lo=0.95)])
def test_unsupported_sparse_or_gap_requires_review(model):
    result = propose_review(pair(), raw(), model, expected_context=context())
    assert result.proposed_outcome == "review"
    assert result.judgment.p_same is None


@pytest.mark.parametrize("positive_count,outcome", [(20, "merge"), (0, "unlinked"), (10, "review")])
def test_untuned_bands_are_proposals(positive_count, outcome):
    result = propose_review(
        pair(), raw(), artifact(positives=positive_count), expected_context=context()
    )
    assert result.proposed_outcome == outcome
    assert result.kind == "review_proposal"
    assert "event_id" not in result.model_dump()


@pytest.mark.parametrize("issue", ["quantity_unknown", "brand_conflict", "evidence_missing"])
def test_named_issue_never_automatically_decides(issue):
    result = propose_review(
        pair(), raw(issue_codes=(issue,)), artifact(), expected_context=context()
    )
    assert result.proposed_outcome == "review"


def test_failed_judgment_stays_unusable():
    failure = JudgmentProposal(method="fuzzy", status="unavailable", issue_codes=("missing",))
    result = propose_review(pair(), failure, artifact(), expected_context=context())
    assert result.proposed_outcome == "review"
    assert result.judgment.p_same is None


def test_already_attached_or_wrong_method_not_recalibrated():
    attached = attach_calibration(raw(), artifact(), expected_context=context())
    with pytest.raises(ValueError, match="replacing"):
        attach_calibration(attached, artifact(), expected_context=context())
    with pytest.raises(ValueError, match="mismatch"):
        attach_calibration(
            JudgmentProposal(method="rules", status="ok", relatedness=1.0),
            artifact(),
            expected_context=context(),
        )


@pytest.mark.parametrize(
    "values",
    [{"merge_at": 0.1}, {"unlinked_at": 0.99}, {"merge_at": float("nan")}, {"merge_at": 2.0}],
)
def test_invalid_policy_rejected(values):
    with pytest.raises(ValidationError):
        ProposalPolicy(**values)
