"""Actual owner registry/journal attachment controls; authored tiny sources are not Gold."""

import hashlib

import pytest

from entity_matcher.domain.bindings import (
    CanonicalIdentityUnsupported,
    product_pointer,
    ruling_binding,
)
from entity_matcher.domain.generation import ProductGenerationValidator
from entity_matcher.domain.product_rows import (
    CurrentProduct,
    ProductRowResolver,
    install_product_rows,
)
from entity_matcher.shared.cited import POINTER_ADAPTER, ArtifactRef, Registry, ResolutionContext
from entity_matcher.shared.match.identity import canonical_bytes
from entity_matcher.shared.match.models import PairIdentity, RawProduct, RecordRef
from entity_matcher.shared.rulings.models import RulingIntent
from entity_matcher.shared.rulings.store import GenerationConflict, JournalStore


def raw(**changes):
    return RawProduct(
        **{
            "id": "one",
            "name": " é Exact NAME\r\n",
            "brand": "Acme",
            "size": "100",
            "unit": "g",
            "price": "1.00",
            "category": "Tea",
            "description": "",
            **changes,
        }
    )


class Access:
    reference = ArtifactRef(artifact_id="access", artifact_sha256="a" * 64, profile_id="local/1")

    def __init__(self, answer="allow"):
        self.answer, self.calls = answer, 0

    def decision(self, *args):
        self.calls += 1
        return self.answer


class Generations:
    reference = ArtifactRef(artifact_id="generation", artifact_sha256="b" * 64, profile_id="raw/1")

    def __init__(self, row):
        self.value = CurrentProduct("current", row)
        self.calls = 0

    def current(self, *args):
        self.calls += 1
        return self.value


def setup(row=None, *, retained=True):
    row = row or raw()
    access, generation = Access(), Generations(row)
    resolver = ProductRowResolver(
        "review", [("A", row)] if retained else [], access=access, generations=generation
    )
    registry = Registry()
    install_product_rows(registry, resolver)
    context = ResolutionContext(
        namespace_id="review",
        access_policy_ref=access.reference,
        generation_policy_ref=generation.reference,
        required_scope="text",
    )
    return registry, resolver, context, access, generation


def test_actual_owner_registry_checks_exact_raw_field_bytes():
    row = raw()
    registry, _, context, _, _ = setup(row)
    pointer = product_pointer("A", row, "name")
    resolved = registry.resolve(pointer, context)
    assert resolved.status == "resolved" and resolved.scope == "text"
    assert resolved.excerpt == row.name
    assert resolved.excerpt_sha256 == hashlib.sha256(row.name.encode()).hexdigest()
    assert resolved.checked_source_sha256 == row.fingerprint
    assert resolved.checked_artifact_sha256 == pointer.evidence_artifact_sha256
    assert resolved.reason == "exact_raw_field_checked"


def test_blank_raw_field_never_becomes_invented_pointer():
    assert product_pointer("A", raw(), "description") is None


@pytest.mark.parametrize(
    "identifier", ["x" * 257, "https://example.invalid/one", "../one", "a\\b", "a\none"]
)
def test_pointer_identity_bounds_fail_closed_without_rewriting_raw_id(identifier):
    row = raw(id=identifier)
    before = row.fingerprint
    with pytest.raises(CanonicalIdentityUnsupported, match="canonical_identity_unsupported"):
        product_pointer("A", row, "name")
    assert row.id == identifier and row.fingerprint == before


def test_ruling_identity_bound_uses_actual_owner_codec_not_truncated_replacement():
    row = raw(id="x" * 513)
    pair = PairIdentity.from_refs(RecordRef.from_raw("A", row), RecordRef.from_raw("B", raw()))
    with pytest.raises(CanonicalIdentityUnsupported):
        ruling_binding(pair)
    assert pair.refs[0].record_id == row.id


@pytest.mark.parametrize("answer", ["deny", "unknown", None, True])
def test_denied_access_precedes_generation_or_retained_source_inspection(answer):
    registry, _, context, access, generation = setup()
    access.answer = answer
    result = registry.resolve(product_pointer("A", raw(), "name"), context)
    assert result.status == "unsupported" and result.excerpt is None
    assert generation.calls == 0


@pytest.mark.parametrize("status,expected", [("unknown", "unsupported"), ("tombstoned", "missing")])
def test_current_unknown_and_tombstoned_are_distinct(status, expected):
    registry, _, context, _, generation = setup()
    generation.value = CurrentProduct(status)
    assert registry.resolve(product_pointer("A", raw(), "name"), context).status == expected


def test_missing_retained_source_and_changed_raw_generation():
    registry, _, context, _, generation = setup(retained=False)
    pointer = product_pointer("A", raw(), "name")
    assert registry.resolve(pointer, context).status == "missing"
    generation.value = CurrentProduct("current", raw(price="1.01"))
    assert registry.resolve(pointer, context).status == "stale"


@pytest.mark.parametrize("change", ["namespace", "access_ref", "scope"])
def test_policy_context_cannot_be_borrowed(change):
    registry, _, context, access, _ = setup()
    if change == "namespace":
        context = context.model_copy(update={"namespace_id": "other"})
    elif change == "access_ref":
        context = context.model_copy(
            update={
                "access_policy_ref": ArtifactRef(
                    artifact_id="other", artifact_sha256="a" * 64, profile_id="local/1"
                )
            }
        )
    else:
        context = context.model_copy(update={"required_scope": "spatial"})
    result = registry.resolve(product_pointer("A", raw(), "name"), context)
    assert result.status == "unsupported" and access.calls == 0


def test_structurally_valid_but_false_raw_quote_does_not_resolve():
    row = raw()
    registry, _, context, _, _ = setup(row)
    pointer = product_pointer("A", row, "name").model_dump()
    pointer["locator"].update(quote="other", excerpt_sha256=hashlib.sha256(b"other").hexdigest())
    preimage = {
        "algorithm": "product-record-field-evidence-v1",
        "dataset_id": "A",
        "record_id": row.id,
        "source_sha256": row.fingerprint,
        "source_revision": row.generation,
        "field_id": "name",
        "raw_value": "other",
    }
    pointer["evidence_artifact_sha256"] = hashlib.sha256(canonical_bytes(preimage)).hexdigest()
    assert POINTER_ADAPTER.validate_python(pointer)
    assert registry.resolve(pointer, context).status == "invalid"


@pytest.mark.parametrize("mutation", ["access", "generation", "retained"])
def test_revocation_or_mutation_between_checks_discloses_no_excerpt(mutation):
    registry, resolver, context, access, generation = setup()
    original = generation.current

    def changing(*args):
        result = original(*args)
        if generation.calls == 1:
            if mutation == "access":
                access.answer = "deny"
            elif mutation == "generation":
                generation.value = CurrentProduct("current", raw(price="2"))
            else:
                object.__setattr__(resolver._rows[("A", "one")][0], "name", "changed")
        return result

    generation.current = changing
    result = registry.resolve(product_pointer("A", raw(), "name"), context)
    assert result.status != "resolved" and result.excerpt is None


def test_bounded_constructor_refuses_duplicates_and_oversize_without_partial_registry_install():
    row = raw()
    for rows, bounds in [
        ([("A", row), ("A", row)], {}),
        ([("A", row)], {"max_bytes": 1}),
        ([("A", row)], {"max_records": True}),
    ]:
        with pytest.raises(ValueError):
            ProductRowResolver(
                "review", rows, access=Access(), generations=Generations(row), **bounds
            )


def intent(pair, event_id="event", prior=None):
    subject, generation = ruling_binding(pair)
    return RulingIntent(
        subject=subject,
        generation_identity=generation,
        event_id=event_id,
        namespace="review",
        event_kind="pair_decision",
        payload={"action": "decide", "verdict": "positive"},
        expected_source_revision=generation.pair_generation,
        expected_evidence_revision=None,
        expected_prior_event_id=prior,
        actor_label="fixture",
        actor_provenance="agent_provisional",
        actor_provenance_ref=None,
        at_utc="2026-10-15T00:00:00Z",
        reason="synthetic adapter control",
        context_refs=(),
    )


def test_actual_journal_raw_generation_validation_no_stale_or_tombstoned_write(tmp_path):
    a, b = raw(), raw(id="two")
    inventory = {("A", a.id): a, ("B", b.id): b}
    pair = PairIdentity.from_refs(RecordRef.from_raw("A", a), RecordRef.from_raw("B", b))
    event = intent(pair)
    store = JournalStore(tmp_path)
    validator = ProductGenerationValidator(lambda: inventory)
    assert store.append(event, validator).outcome == "appended"
    before = store.path.read_bytes()
    inventory[("A", a.id)] = raw(price="2")
    with pytest.raises(GenerationConflict):
        store.append(intent(pair, "next", "event"), validator)
    inventory.pop(("A", a.id))
    with pytest.raises(GenerationConflict):
        store.append(intent(pair, "next", "event"), validator)
    assert store.path.read_bytes() == before
    assert store.append(event, validator).outcome == "exact_retry"
    assert not store.append(event, validator).current_generation_authority


def test_canonical_raw_generation_survives_processing_change_but_not_raw_edit():
    a, b = raw(), raw(id="two")
    pair = PairIdentity.from_refs(RecordRef.from_raw("A", a), RecordRef.from_raw("B", b))
    subject, generation = ruling_binding(pair)
    validator = ProductGenerationValidator(lambda: {("A", a.id): a, ("B", b.id): b})
    assert validator.validate(subject, generation) is None
    # Normalizer/model/calibration configs are absent from the raw-authority envelope.
    with pytest.raises(ValueError, match="unsupported_raw_profile"):
        validator.validate(subject, generation.model_copy(update={"profile": "normalized-v2"}))
