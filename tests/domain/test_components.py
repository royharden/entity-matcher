"""Real temporary journal graph regressions; injected human checks are synthetic protocols only."""

import pytest

from entity_matcher.domain.bindings import ruling_binding
from entity_matcher.domain.components import project_components
from entity_matcher.domain.generation import ProductGenerationValidator
from entity_matcher.shared.match.models import PairIdentity, RawProduct, RecordRef
from entity_matcher.shared.rulings.models import RulingIntent
from entity_matcher.shared.rulings.store import JournalStore


def row(record_id, **changes):
    return RawProduct(
        **{
            "id": record_id,
            "name": "Tea",
            "brand": "Acme",
            "size": "100",
            "unit": "g",
            "price": "1",
            "category": "Drinks",
            "description": "",
            **changes,
        }
    )


class FixtureHumanEvidence:
    """A test capability, never evidence of a real person or published Gold."""

    def validate_human(self, event):
        if event.actor_provenance_ref != "synthetic-proof-only":
            raise ValueError("private-proof-detail-must-not-leak")


class Rig:
    def __init__(self, path):
        self.inventory = {
            ("A", "one"): row("one"),
            ("B", "two"): row("two"),
            ("A", "three"): row("three"),
        }
        self.store = JournalStore(path)
        self.validator = ProductGenerationValidator(lambda: self.inventory)
        self.counter = 0

    def append(
        self,
        left=("A", "one"),
        right=("B", "two"),
        *,
        verdict="positive",
        target=None,
        provenance="human",
        proof="synthetic-proof-only",
        namespace="review",
    ):
        pair = PairIdentity.from_refs(
            *(RecordRef.from_raw(key[0], self.inventory[key]) for key in (left, right))
        )
        subject, generation = ruling_binding(pair)
        prior = self.store.stable_head(namespace, subject)
        self.counter += 1
        event_id = f"event-{self.counter}"
        intent = RulingIntent(
            event_id=event_id,
            namespace=namespace,
            subject=subject,
            generation_identity=generation,
            event_kind="pair_decision",
            payload={"action": "retract", "target_event_id": target}
            if target
            else {"action": "decide", "verdict": verdict},
            expected_source_revision=generation.pair_generation,
            expected_evidence_revision=None,
            expected_prior_event_id=prior.event_id if prior else None,
            actor_label="fixture",
            actor_provenance=provenance,
            actor_provenance_ref=proof,
            at_utc="2026-10-15T00:00:00Z",
            reason="authored graph control",
            context_refs=(),
        )
        self.store.append(intent, self.validator)
        return event_id, subject.pair_key

    def projection(self, **kwargs):
        return project_components(self.store.replay(), self.inventory, namespace="review", **kwargs)


def sizes(result):
    return sorted(len(component.members) for component in result.components)


def test_human_strings_alone_or_provisional_events_never_form_edges(tmp_path):
    rig = Rig(tmp_path)
    rig.append()
    assert sizes(rig.projection()) == [1, 1, 1]
    assert rig.projection().pairs[0].issue_codes == ("human_evidence_unverified",)
    rig.append(provenance="agent_provisional", proof=None)
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert result.pairs[0].issue_codes == ("agent_provisional_not_human",)


def test_transitive_positive_component_is_contested_by_internal_negative(tmp_path):
    rig = Rig(tmp_path)
    rig.append()
    rig.append(("A", "three"), ("B", "two"))
    _, negative = rig.append(("A", "one"), ("A", "three"), verdict="negative")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [3]
    assert result.components[0].contested
    assert result.components[0].internal_negative_pairs == (negative,)
    assert all(pair.human_evidence_verified for pair in result.pairs)


def test_negative_between_separate_components_does_not_invent_positive_merge(tmp_path):
    rig = Rig(tmp_path)
    rig.append(verdict="negative")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert not any(component.contested for component in result.components)


def test_retraction_and_unsure_are_barriers_without_historical_positive_revival(tmp_path):
    rig = Rig(tmp_path)
    first, _ = rig.append()
    rig.append(target=first)
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert result.pairs[0].issue_codes == ("effective_decision_retracted",)
    rig.append()
    rig.append(verdict="unsure")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert result.pairs[0].issue_codes == ("human_unsure",)


def test_history_retraction_does_not_withdraw_newer_decision(tmp_path):
    rig = Rig(tmp_path)
    first, _ = rig.append()
    latest, _ = rig.append()
    head, _ = rig.append(target=first)
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 2]
    assert result.pairs[0].effective_event_id == latest
    assert result.pairs[0].head_event_id == head


def test_removed_or_edited_middle_record_splits_existing_component(tmp_path):
    rig = Rig(tmp_path)
    rig.append()
    rig.append(("A", "three"), ("B", "two"))
    assert sizes(rig.projection(human_evidence=FixtureHumanEvidence())) == [3]
    rig.inventory[("B", "two")] = row("two", price="2")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert all(pair.state == "needs_review" for pair in result.pairs)
    del rig.inventory[("B", "two")]
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1]
    assert all(pair.issue_codes == ("source_missing_or_tombstoned",) for pair in result.pairs)


def test_unchanged_raw_ruling_outside_new_candidate_queue_remains_effective(tmp_path):
    rig = Rig(tmp_path)
    rig.append()
    result = rig.projection(human_evidence=FixtureHumanEvidence(), candidate_pair_keys=frozenset())
    assert sizes(result) == [1, 2]
    assert result.pairs[0].state == "effective" and result.pairs[0].queue_state == "outside"


def test_foreign_namespace_and_unverified_new_head_cannot_supply_edges(tmp_path):
    rig = Rig(tmp_path)
    rig.append(namespace="foreign")
    assert sizes(rig.projection(human_evidence=FixtureHumanEvidence())) == [1, 1, 1]
    rig.append()
    rig.append(proof="unverified-claim")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1]
    assert result.pairs[0].issue_codes == ("human_evidence_unverified",)
    assert "private-proof-detail" not in repr(result)


def test_dirty_journal_preserves_visible_records_but_all_edges_require_review(tmp_path):
    rig = Rig(tmp_path)
    rig.append()
    rig.store.path.write_bytes(rig.store.path.read_bytes() + b"torn")
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert result.journal_status == "repair_required" and sizes(result) == [1, 1, 1]
    assert result.pairs[0].issue_codes == ("journal_not_clean",)


def test_unrepresentable_canonical_identity_stays_visible_and_singleton(tmp_path):
    rig = Rig(tmp_path)
    long_id = "x" * 513
    rig.inventory[("A", long_id)] = row(long_id)
    result = rig.projection(human_evidence=FixtureHumanEvidence())
    assert sizes(result) == [1, 1, 1, 1]
    assert result.unsupported_records[0].identity.record_id == long_id
    assert result.unsupported_records[0].capabilities == ("ruling", "pointer")


def test_inventory_key_does_not_retarget_different_raw_identity(tmp_path):
    rig = Rig(tmp_path)
    rig.inventory[("A", "one")] = row("different")
    with pytest.raises(ValueError, match="inventory_source_identity_mismatch"):
        rig.projection(human_evidence=FixtureHumanEvidence())
