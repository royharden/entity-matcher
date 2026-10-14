"""Exact domain field checks, with no generic resolver or source I/O claims."""

from dataclasses import replace

import pytest

from entity_matcher.shared.match.evidence import field_evidence, resolve_raw_field
from entity_matcher.shared.match.models import RawProduct
from entity_matcher.shared.match.normalize import normalize_record


def product(**changes: str) -> RawProduct:
    return RawProduct(
        **{
            "id": "1",
            "name": " Raw café ",
            "brand": "",
            "size": "500",
            "unit": "ml",
            "price": "1.00",
            "category": "",
            "description": "",
        }
        | changes
    )


def test_exact_quote_resolves_and_blank_field_has_no_artifact() -> None:
    """Catches normalized quotes or fabricated artifacts for blank optional fields."""
    raw = product()
    items = field_evidence(normalize_record(raw, "a"), ("name", "brand"))
    assert len(items) == 1
    result = resolve_raw_field(items[0], dataset_id="a", raw=raw)
    assert result.status == "valid" and result.quote == " Raw café "


def test_mutated_raw_is_stale_missing_id_not_retargeted_wrong_namespace_invalid() -> None:
    """Catches changed raw data or another identity/namespace substituting for old evidence."""
    evidence = field_evidence(normalize_record(product(), "a"), ("name",))[0]
    assert resolve_raw_field(evidence, dataset_id="a", raw=product(price="1.0")).status == "stale"
    assert resolve_raw_field(evidence, dataset_id="a", raw=None).status == "missing"
    assert resolve_raw_field(evidence, dataset_id="a", raw=product(id="2")).status == "missing"
    assert resolve_raw_field(evidence, dataset_id="b", raw=product()).status == "invalid"


@pytest.mark.parametrize("field", ["excerpt_sha256", "evidence_artifact_sha256"])
def test_wrong_quote_or_artifact_hash_is_invalid_without_quote(field: str) -> None:
    """Catches well-shaped metadata treated as proof of actual field bytes."""
    evidence = field_evidence(normalize_record(product(), "a"), ("name",))[0]
    bad = evidence.model_copy(update={field: "0" * 64})
    result = resolve_raw_field(bad, dataset_id="a", raw=product())
    assert result.status == "invalid" and result.quote is None


def test_unknown_path_field_and_duplicate_field_reject_before_resolution() -> None:
    """Catches model-selected URLs/paths entering lookup or duplicated quote evidence."""
    record = normalize_record(product(), "a")
    for field in ("../../private", "https://example.test/file"):
        with pytest.raises(ValueError):
            field_evidence(record, (field,))
    with pytest.raises(ValueError):
        field_evidence(record, ("name", "name"))
    bad_ref = record.ref.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(ValueError):
        field_evidence(replace(record, ref=bad_ref), ("name",))
