"""Pure fuzzy and dimensional-rule signals; no correctness probability or human event."""

from typing import Protocol

from pydantic import ValidationError

from ..models import JudgmentProposal, RawProduct, RecordRef
from ..normalize import PROFILE, NormalizedRecord
from ..quantity import quantities_equal


class Judge(Protocol):
    def judge(self, left: NormalizedRecord, right: NormalizedRecord) -> JudgmentProposal: ...


def _input_issue(left: NormalizedRecord, right: NormalizedRecord) -> str | None:
    for record in (left, right):
        if not isinstance(record, NormalizedRecord) or record.profile != PROFILE:
            return "unsupported_normalization_profile"
        try:
            raw, ref = RawProduct.model_validate(record.raw), RecordRef.model_validate(record.ref)
        except ValidationError:
            return "invalid_source_record"
        if raw.id != ref.record_id or raw.fingerprint != ref.content_hash:
            return "stale_normalized_reference"
        if not record.name:
            return "missing_normalized_name"
    return None


def _token_ratio(left: NormalizedRecord, right: NormalizedRecord) -> float | None:
    # The approved pure baseline is RapidFuzz token_sort_ratio; no substitute scorer.
    try:
        from rapidfuzz.fuzz import token_sort_ratio
    except ImportError:
        return None
    a, b = f"{left.brand} {left.name}".strip(), f"{right.brand} {right.name}".strip()
    return float(token_sort_ratio(a, b)) / 100


def judge_fuzzy(left: NormalizedRecord, right: NormalizedRecord) -> JudgmentProposal:
    issue = _input_issue(left, right)
    if issue is not None:
        return JudgmentProposal(method="fuzzy", status="invalid", issue_codes=(issue,))
    score = _token_ratio(left, right)
    if score is None:
        return JudgmentProposal(
            method="fuzzy", status="unavailable", issue_codes=("fuzzy_dependency_unavailable",)
        )
    return JudgmentProposal(method="fuzzy", status="ok", relatedness=score)


def judge_rules(left: NormalizedRecord, right: NormalizedRecord) -> JudgmentProposal:
    issue = _input_issue(left, right)
    if issue is not None:
        return JudgmentProposal(method="rules", status="invalid", issue_codes=(issue,))
    reasons = []
    quantity = quantities_equal(left.quantity, right.quantity)
    if quantity is False:
        reasons.append("quantity_or_pack_conflict")
    if left.brand and right.brand and left.brand != right.brand:
        reasons.append("brand_conflict")
    if reasons:
        return JudgmentProposal(
            method="rules", status="ok", relatedness=0.0, issue_codes=tuple(reasons)
        )
    if quantity is None:
        reasons.append("unknown_quantity")
    if not left.brand or not right.brand:
        reasons.append("unknown_brand")
    score = _token_ratio(left, right)
    if score is None:
        return JudgmentProposal(
            method="rules", status="unavailable", issue_codes=("fuzzy_dependency_unavailable",)
        )
    return JudgmentProposal(
        method="rules", status="ok", relatedness=score, issue_codes=tuple(reasons)
    )


class FuzzyJudge:
    judge = staticmethod(judge_fuzzy)


class RulesJudge:
    judge = staticmethod(judge_rules)
