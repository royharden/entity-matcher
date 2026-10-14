"""Positive and rejection controls for model contracts, not matching accuracy."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from entity_matcher.shared.match.models import (
    CalibrationEvidence,
    FieldEvidenceIdentity,
    JudgmentProposal,
    PairIdentity,
    RawProduct,
    RecordIdentity,
    RecordRef,
    ReviewProposal,
)


def raw(**changes: object) -> RawProduct:
    return RawProduct(
        **{
            "id": "row:1",
            "name": " Café ",
            "brand": "",
            "size": "500",
            "unit": "ml",
            "price": "1.00",
            "category": "",
            "description": "",
        }
        | changes
    )


def pair() -> PairIdentity:
    return PairIdentity.from_refs(RecordRef.from_raw("a", raw()), RecordRef.from_raw("b", raw()))


def calibration() -> CalibrationEvidence:
    return CalibrationEvidence(
        dev_manifest_sha256="a" * 64, config_sha256="b" * 64, support_count=12
    )


def test_raw_roundtrip_preserves_decimal_unicode_and_blanks() -> None:
    """Catches normalization or coercion of raw fields at the model boundary."""
    product = raw()
    assert RawProduct.model_validate_json(product.model_dump_json()) == product
    assert product.price == "1.00" and product.name == " Café " and product.brand == ""
    assert product.generation == "rg1:" + product.fingerprint


@pytest.mark.parametrize(
    "changes",
    [
        {"price": 1},
        {"size": True},
        {"brand": None},
        {"id": ""},
        {"name": ""},
        {"description": "é" * 4097},
        {"description": "\ud800"},
        {"extra": "forbidden"},
    ],
)
def test_raw_rejects_coercion_empty_required_and_utf8_overflow(changes: dict) -> None:
    """Catches strict-string, closed-shape and byte-limit bypasses."""
    with pytest.raises(ValidationError):
        raw(**changes)


def test_raw_byte_limit_positive_boundary_and_frozen_fields() -> None:
    """Catches off-by-one byte bounds and mutation of accepted raw evidence."""
    product = raw(description="é" * 4096)
    assert len(product.description.encode("utf-8")) == 8192
    with pytest.raises(ValidationError):
        product.price = "1.0"
    with pytest.raises(ValidationError):
        RawProduct.model_validate(product.model_copy(update={"price": 1}))


@pytest.mark.parametrize(
    "data",
    [
        {"dataset_id": "", "record_id": "1"},
        {"dataset_id": "é" * 129, "record_id": "1"},
        {"dataset_id": "a", "record_id": 1},
        {"dataset_id": "a", "record_id": "1", "content_hash": "a" * 64},
    ],
)
def test_identity_rejects_invalid_or_content_bearing_wire(data: dict) -> None:
    """Catches overlong namespaces and implicit projection of content-bearing wire."""
    with pytest.raises(ValidationError):
        RecordIdentity.model_validate(data)


def test_ref_projection_and_raw_mutation_preserve_stable_pair_key() -> None:
    """Catches raw content leaking into stable refs or unchanged generation after mutation."""
    original, changed = RecordRef.from_raw("a", raw()), RecordRef.from_raw("a", raw(price="1.0"))
    other = RecordRef.from_raw("b", raw())
    before, after = PairIdentity.from_refs(original, other), PairIdentity.from_refs(changed, other)
    assert original.stable_identity == changed.stable_identity
    assert set(original.stable_identity.model_dump()) == {"dataset_id", "record_id"}
    assert before.pair_key == after.pair_key
    assert before.pair_generation != after.pair_generation
    assert PairIdentity.from_refs(other, original) == before
    assert PairIdentity.model_validate_json(before.model_dump_json()) == before


@pytest.mark.parametrize("case", ["key", "generation", "alignment", "orientation", "version"])
def test_pair_rejects_forged_or_noncanonical_wire(case: str) -> None:
    """Catches inconsistent keys, fingerprints, orientation and bool-as-version inputs."""
    data = pair().model_dump()
    if case == "key":
        data["pair_key"] = "pk1:" + "0" * 64
    elif case == "generation":
        data["pair_generation"] = "pg1:" + "0" * 64
    elif case == "alignment":
        data["raw_fingerprints"] = ("0" * 64, "1" * 64)
    elif case == "orientation":
        data["refs"] = tuple(reversed(data["refs"]))
    else:
        data["schema_version"] = True
    with pytest.raises(ValidationError):
        PairIdentity.model_validate(data)


def test_nested_pair_does_not_accept_content_ref_without_projection() -> None:
    """Catches trusted-subclass or unvalidated-copy bypasses of closed nested identity models."""
    data = pair().model_dump()
    data["refs"] = (
        RecordRef.from_raw("a", raw()),
        RecordIdentity(dataset_id="b", record_id="row:1"),
    )
    with pytest.raises(ValidationError):
        PairIdentity.model_validate(data)
    forged = pair().model_copy(update={"pair_key": "pk1:" + "0" * 64})
    with pytest.raises(ValidationError):
        PairIdentity.model_validate(forged)


def test_self_pair_rejected_and_nested_contract_frozen() -> None:
    """Catches self-pairs and mutation through a nested identity reference."""
    ref = RecordRef.from_raw("a", raw())
    with pytest.raises(ValueError):
        PairIdentity.from_refs(ref, ref)
    with pytest.raises(ValidationError):
        pair().refs[0].record_id = "other"


def test_field_evidence_exact_artifact_and_blank_none() -> None:
    """Catches detached, normalized or empty evidence replacing exact raw field provenance."""
    product = raw()
    evidence = FieldEvidenceIdentity.from_raw("a", product, "name")
    assert evidence is not None
    preimage = {
        "algorithm": "product-record-field-evidence-v1",
        "dataset_id": "a",
        "record_id": product.id,
        "source_sha256": product.fingerprint,
        "source_revision": product.generation,
        "field_id": "name",
        "raw_value": " Café ",
    }
    encoded = json.dumps(
        preimage, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert evidence.evidence_artifact_sha256 == hashlib.sha256(encoded).hexdigest()
    assert evidence.excerpt_sha256 == hashlib.sha256(" Café ".encode()).hexdigest()
    assert FieldEvidenceIdentity.from_raw("a", product, "brand") is None
    changed = FieldEvidenceIdentity.from_raw("b", product, "name")
    assert changed is not None
    assert changed.evidence_artifact_sha256 != evidence.evidence_artifact_sha256
    with pytest.raises(ValidationError):
        FieldEvidenceIdentity.model_validate(
            evidence.model_dump() | {"source_revision": "rg1:" + "0" * 64}
        )
    with pytest.raises(ValueError):
        FieldEvidenceIdentity.from_raw("a", product, "../../private")


@pytest.mark.parametrize("value", ["a" * 63, "A" * 64, "g" * 64, True])
def test_full_canonical_ref_digest_required(value: object) -> None:
    """Catches truncated hashes, uppercase variants and digest coercion."""
    with pytest.raises(ValidationError):
        RecordRef(dataset_id="a", record_id="1", content_hash=value)


def test_native_distribution_is_retained_without_correctness_probability() -> None:
    """Catches native confidence/distribution being promoted to calibrated authority."""
    judgment = JudgmentProposal(
        method="jev",
        status="ok",
        relatedness=0.8,
        native_confidence=0.9,
        native_probabilities=(0.05, 0.15, 0.8),
    )
    assert judgment.p_same is None and judgment.calibration is None
    ReviewProposal(pair=pair(), judgment=judgment, proposed_outcome="review")
    with pytest.raises(ValidationError):
        ReviewProposal(pair=pair(), judgment=judgment, proposed_outcome="merge")


@pytest.mark.parametrize(
    "changes",
    [
        {"p_same": 0.9},
        {"calibration": calibration()},
        {"relatedness": float("nan")},
        {"native_confidence": float("inf")},
        {"relatedness": "0.8"},
        {"relatedness": True},
        {"native_probabilities": (0.2, 0.2, 0.2)},
        {"native_probabilities": (1.0,)},
        {"native_probabilities": (-0.1, 1.1)},
        {"native_probabilities": tuple(0.1 for _ in range(11))},
        {"who": "human"},
        {"verdict": "positive"},
        {"event_id": "machine-event"},
    ],
)
def test_judgment_rejects_invalid_signals_and_human_event_fields(changes: dict) -> None:
    """Catches malformed native values, unsupported calibration and machine-to-ruling wire drift."""
    with pytest.raises(ValidationError):
        JudgmentProposal.model_validate(
            {"method": "rules", "status": "ok", "relatedness": 0.8} | changes
        )


def test_calibrated_proposal_is_frozen_metadata_not_an_accuracy_certificate() -> None:
    """Catches lost calibration provenance or machine proposal being serialized as a human event."""
    judgment = JudgmentProposal(
        method="rules", status="ok", relatedness=0.8, p_same=0.9, calibration=calibration()
    )
    proposal = ReviewProposal(pair=pair(), judgment=judgment, proposed_outcome="merge")
    assert proposal.kind == "review_proposal"
    assert ReviewProposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert not {"verdict", "actor", "event_id", "action", "expected_head"} & set(
        proposal.model_dump()
    )
    with pytest.raises(ValidationError):
        proposal.judgment.calibration.support_count = 0


@pytest.mark.parametrize("status", ["unavailable", "invalid", "error"])
def test_failed_judgment_requires_named_issue_and_review(status: str) -> None:
    """Catches anonymous errors and failed inference masquerading as usable match evidence."""
    with pytest.raises(ValidationError):
        JudgmentProposal(method="llm", status=status)
    judgment = JudgmentProposal(method="llm", status=status, issue_codes=("provider_unavailable",))
    ReviewProposal(pair=pair(), judgment=judgment, proposed_outcome="review")
    with pytest.raises(ValidationError):
        JudgmentProposal(
            method="llm", status=status, issue_codes=("provider_unavailable",), relatedness=0.8
        )
    with pytest.raises(ValidationError):
        ReviewProposal(pair=pair(), judgment=judgment, proposed_outcome="unlinked")


@pytest.mark.parametrize("support", [True, 0, -1, "12"])
def test_calibration_requires_positive_strict_support_count(support: object) -> None:
    """Catches unsupported or coerced sample counts hiding behind a calibration label."""
    with pytest.raises(ValidationError):
        CalibrationEvidence(
            dev_manifest_sha256="a" * 64, config_sha256="b" * 64, support_count=support
        )


def test_review_rejects_human_action_and_schema_version_drift() -> None:
    """Catches an export confusing a machine proposal with the separately owned ruling envelope."""
    data = {
        "pair": pair(),
        "judgment": JudgmentProposal(method="fuzzy", status="ok", relatedness=0.5),
        "proposed_outcome": "review",
    }
    for extra in (
        {"action": "decide"},
        {"verdict": "positive"},
        {"schema_version": True},
        {"kind": "ruling"},
    ):
        with pytest.raises(ValidationError):
            ReviewProposal.model_validate(data | extra)
