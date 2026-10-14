"""Synthetic microcases for declared identity bytes, not an accuracy gold set."""

import hashlib

import pytest

from entity_matcher.shared.match import identity as ident


def raw_record(**changes: str) -> dict[str, str]:
    record = dict(
        zip(ident.RAW_FIELDS, ("1", "Bottle", "Brand", "500", "ml", "1.00", "", ""), strict=True)
    )
    return record | changes


def ref(dataset: str, record: str = "1") -> dict[str, str]:
    return {"dataset_id": dataset, "record_id": record}


def test_raw_preimage_exact_profile_bytes() -> None:
    """Catches wrapper/spacing/key-order changes or numeric price coercion."""
    expected = (
        b'{"algorithm":"product-raw-fields-v1","fields":{"brand":"Brand","category":"",'
        b'"description":"","id":"1","name":"Bottle","price":"1.00","size":"500","unit":"ml"}}'
    )
    assert ident.raw_record_preimage(raw_record()) == expected
    assert ident.raw_record_fingerprint(raw_record()) == hashlib.sha256(expected).hexdigest()


def test_stable_pair_key_exact_bytes() -> None:
    """Catches accidental fingerprints or delimiter-concatenation in a stable key."""
    expected = (
        b'{"algorithm_version":1,"kind":"pair","refs":['
        b'{"dataset_id":"a","record_id":"1"},{"dataset_id":"b","record_id":"1"}]}'
    )
    assert ident.pair_key_preimage(ref("b"), ref("a")) == expected
    assert ident.pair_key(ref("a"), ref("b")) == "pk1:" + hashlib.sha256(expected).hexdigest()


def test_content_ref_projection_does_not_change_stable_key() -> None:
    """Catches hashing a full RecordRef instead of its two-field identity projection."""
    old = {**ref("a"), "content_hash": ident.raw_record_fingerprint(raw_record())}
    new = {**ref("a"), "content_hash": ident.raw_record_fingerprint(raw_record(price="1.0"))}
    assert old["content_hash"] != new["content_hash"]
    assert ident.pair_key(ident.identity_from_ref(old), ref("b")) == ident.pair_key(
        ident.identity_from_ref(new), ref("b")
    )


def test_generation_swap_aligns_fingerprints() -> None:
    """Catches sorting refs without carrying each fingerprint to its matching side."""
    a, b = (
        ident.raw_record_fingerprint(raw_record()),
        ident.raw_record_fingerprint(raw_record(id="2")),
    )
    assert ident.pair_generation(ref("a"), a, ref("b", "2"), b) == ident.pair_generation(
        ref("b", "2"), b, ref("a"), a
    )
    assert ident.pair_generation(ref("a"), a, ref("b", "2"), b) != ident.pair_generation(
        ref("a"), b, ref("b", "2"), a
    )


def test_raw_mutation_changes_generation_only() -> None:
    """Catches unchanged generation after raw price/description changes."""
    old, new = raw_record(), raw_record(price="1.0", description="raw change")
    assert ident.raw_record_generation(old) != ident.raw_record_generation(new)
    b = ident.raw_record_fingerprint(raw_record(id="2"))
    assert ident.pair_generation(ref("a"), ident.raw_record_fingerprint(old), ref("b", "2"), b) != (
        ident.pair_generation(ref("a"), ident.raw_record_fingerprint(new), ref("b", "2"), b)
    )
    assert ident.pair_key(ref("a"), ref("b", "2")) == ident.pair_key(ref("b", "2"), ref("a"))


@pytest.mark.parametrize(
    "left,right",
    [
        (ref("ab", "c"), ref("a", "bc")),
        (ref("a:b", "c|d"), ref("a", "b:c|d")),
        (ref("a", "é"), ref("a", "e\u0301")),
        (ref("a", 'x"\\y'), ref("a", "x|y")),
    ],
)
def test_distinct_opaque_identity_bytes(left: dict[str, str], right: dict[str, str]) -> None:
    """Catches namespace boundaries/Unicode normalization collapsing accepted IDs."""
    assert ident.canonical_bytes(ident.record_identity(left)) != ident.canonical_bytes(
        ident.record_identity(right)
    )


def test_raw_mapping_order_is_irrelevant_without_normalizing_values() -> None:
    """Catches dictionary insertion order leaking into canonical bytes."""
    raw = raw_record(name=" Café ")
    assert ident.raw_record_preimage(raw) == ident.raw_record_preimage(
        dict(reversed(list(raw.items())))
    )
    assert ident.raw_record_fingerprint(raw) != ident.raw_record_fingerprint(
        raw_record(name="Café")
    )


def test_field_artifact_and_excerpt_exact_bytes() -> None:
    """Catches artifact hashing from a normalized field or a different dataset."""
    raw = raw_record(name=" Raw café ")
    fp = ident.raw_record_fingerprint(raw)
    expected = (
        '{"algorithm":"product-record-field-evidence-v1","dataset_id":"a","field_id":"name",'
        '"raw_value":" Raw café ","record_id":"1","source_revision":"rg1:'
        + fp
        + '","source_sha256":"'
        + fp
        + '"}'
    ).encode("utf-8")
    assert ident.field_evidence_preimage(ref("a"), raw, "name") == expected
    assert (
        ident.field_evidence_digest(ref("a"), raw, "name") == hashlib.sha256(expected).hexdigest()
    )
    assert ident.field_evidence_digest(ref("a"), raw, "name") != ident.field_evidence_digest(
        ref("b"), raw, "name"
    )
    assert (
        ident.field_excerpt_digest(raw, "name") == hashlib.sha256(" Raw café ".encode()).hexdigest()
    )


def test_blank_field_has_no_substantive_artifact() -> None:
    """Catches fabricated empty excerpts while raw blanks still affect raw identity."""
    assert ident.field_evidence_preimage(ref("a"), raw_record(), "description") is None
    assert ident.field_evidence_digest(ref("a"), raw_record(), "description") is None
    assert ident.field_excerpt_digest(raw_record(), "description") is None


@pytest.mark.parametrize("bad", [None, True, 1, [], {"id": "1"}, raw_record() | {"extra": "x"}])
def test_invalid_raw_shape_rejected(bad: object) -> None:
    """Catches coercion/missing/extra identity material."""
    with pytest.raises(ident.IdentityError):
        ident.raw_record_fingerprint(bad)


@pytest.mark.parametrize("value", [True, 1, None, "\ud800", "é" * 4097])
def test_invalid_raw_value_rejected(value: object) -> None:
    """Catches nonstring, invalid UTF8 and byte-limit bypass."""
    with pytest.raises(ident.IdentityError):
        ident.raw_record_fingerprint(raw_record() | {"price": value})


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, True, None])
def test_invalid_digest_rejected(bad: object) -> None:
    """Catches truncated/noncanonical/cast fingerprints."""
    with pytest.raises(ident.IdentityError):
        ident.pair_generation(ref("a"), bad, ref("b"), "b" * 64)


def test_extra_fingerprint_in_identity_is_rejected_without_explicit_projection() -> None:
    """Catches implicit whole-ref hashing or silently dropped arbitrary extra fields."""
    with pytest.raises(ident.IdentityError):
        ident.pair_key(ref("a") | {"content_hash": "a" * 64}, ref("b"))
    with pytest.raises(ident.IdentityError):
        ident.pair_key(ref("a"), ref("a"))


def test_field_generation_cannot_retarget_record_or_unknown_field() -> None:
    """Catches cross-record evidence and model-selected paths masquerading as field IDs."""
    with pytest.raises(ident.IdentityError):
        ident.field_evidence_digest(ref("a", "2"), raw_record(), "name")
    with pytest.raises(ident.IdentityError):
        ident.field_evidence_digest(ref("a"), raw_record(), "../../private")
