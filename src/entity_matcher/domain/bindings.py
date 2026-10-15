"""Lossless canonical owner conversion; unsupported identities are never rewritten."""

from pydantic import ValidationError

from ..shared.cited.models import ProductPointer
from ..shared.match.models import FieldEvidenceIdentity, FieldId, PairIdentity, RawProduct
from ..shared.rulings.models import PairGeneration, PairSubject


class CanonicalIdentityUnsupported(ValueError):
    def __init__(self):
        super().__init__("canonical_identity_unsupported")


def ruling_binding(pair: PairIdentity) -> tuple[PairSubject, PairGeneration]:
    pair = PairIdentity.model_validate(pair)
    try:
        subject = PairSubject(
            kind="pair",
            pair_key=pair.pair_key,
            refs=tuple(ref.model_dump() for ref in pair.refs),
        )
        generation = PairGeneration(
            kind="pair",
            profile="product-raw-fields-v1",
            pair_generation=pair.pair_generation,
            raw_fingerprints=pair.raw_fingerprints,
        )
    except ValidationError:
        raise CanonicalIdentityUnsupported() from None
    return subject, generation


def product_pointer(dataset_id: str, raw: RawProduct, field_id: FieldId) -> ProductPointer | None:
    raw = RawProduct.model_validate(raw)
    evidence = FieldEvidenceIdentity.from_raw(dataset_id, raw, field_id)
    if evidence is None:
        return None
    try:
        return ProductPointer(
            kind="row:id",
            source_type="product_catalog",
            locator_subtype="record_field",
            source_id=dataset_id,
            source_sha256=evidence.source_sha256,
            source_revision=evidence.source_revision,
            evidence_artifact_sha256=evidence.evidence_artifact_sha256,
            locator={
                "dataset_id": dataset_id,
                "record_id": raw.id,
                "field_id": field_id,
                "quote": getattr(raw, field_id),
                "excerpt_sha256": evidence.excerpt_sha256,
            },
        )
    except ValidationError:
        raise CanonicalIdentityUnsupported() from None
