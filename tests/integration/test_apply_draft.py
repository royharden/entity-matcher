"""Actual owner journal effects, historical retries and partial/uncertain failures."""

import json

import pytest

from entity_matcher.domain.components import project_components
from entity_matcher.domain.generation import ProductGenerationValidator
from entity_matcher.pipeline.apply_draft import (
    DenyHumanAdmission,
    DraftApplyError,
    apply_draft,
    prepare_intents,
)
from entity_matcher.pipeline.ruling_packet import decode_draft, make_ruling_packet
from entity_matcher.pipeline.workspace import create_workspace
from entity_matcher.shared.rulings.store import JournalStore
from tests.integration.test_ruling_packet import catalog, draft, draft_wire, setup


def test_actual_apply_replay_exact_retry_remains_provisional_without_human_edges(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    first = apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert first.issue_code is None and len(first.receipts) == 1
    assert first.receipts[0].outcome == "appended" and not first.receipts[0].historical
    before = store.path.read_bytes()
    replay = store.replay()
    assert replay.events[0].actor_provenance == "agent_provisional"
    assert replay.events[0].actor_provenance_ref is None
    projection = project_components(replay, workspace.inventory, namespace=packet.namespace)
    assert all(len(component.members) == 1 for component in projection.components)
    assert not projection.pairs[0].human_evidence_verified
    retry = apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert retry.receipts[0].outcome == "exact_retry" and retry.receipts[0].historical
    assert not retry.receipts[0].current_generation_authority
    assert store.path.read_bytes() == before
    changed = create_workspace(catalog(description="changed"), catalog(("two",)))
    historical = apply_draft(changed, packet, submitted, store, namespace="local-products")
    assert historical.receipts[0].historical and store.path.read_bytes() == before
    with pytest.raises(DraftApplyError, match="human_admission_unavailable"):
        DenyHumanAdmission().issue_evidence(packet_id=packet.packet_id, operation_id="anything")


def test_real_retraction_sets_barrier_and_new_packet_cannot_retract_tombstone(tmp_path):
    workspace, store, original = setup(tmp_path)
    submitted = draft(original)
    apply_draft(workspace, original, submitted, store, namespace="local-products")
    packet = make_ruling_packet(workspace, store.replay(), namespace=original.namespace)
    target = submitted.operations[0].operation_id
    assert packet.entries[0].retractable_event_id == target
    wire = draft_wire(packet)
    wire["operations"][0].pop("verdict")
    wire["operations"][0].update(action="retract", target_event_id=target)
    retract = decode_draft(json.dumps(wire).encode())
    result = apply_draft(workspace, packet, retract, store, namespace="local-products")
    assert result.issue_code is None
    after = make_ruling_packet(workspace, store.replay(), namespace=original.namespace)
    assert after.entries[0].observed_head_event_id == retract.operations[0].operation_id
    assert after.entries[0].retractable_event_id is None
    projected = project_components(store.replay(), workspace.inventory, namespace=packet.namespace)
    assert projected.pairs[0].state == "needs_review"
    assert all(len(component.members) == 1 for component in projected.components)
    assert (
        apply_draft(workspace, packet, retract, store, namespace="local-products")
        .receipts[0]
        .historical
    )
    fresh = apply_draft(
        workspace, after, draft(after, verdict="unsure"), store, namespace="local-products"
    )
    assert fresh.issue_code is None and len(store.replay().events) == 3


def test_current_raw_mutation_and_packet_or_head_tampering_refuse_without_bytes(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    changed = create_workspace(catalog(description="mutated"), catalog(("two",)))
    with pytest.raises(DraftApplyError, match="packet_source_changed"):
        apply_draft(changed, packet, submitted, store, namespace="local-products")
    assert not store.path.exists()
    altered = submitted.model_copy(update={"packet_id": "rp1:" + "f" * 64})
    with pytest.raises(DraftApplyError, match="draft_packet_mismatch"):
        apply_draft(workspace, packet, altered, store, namespace="local-products")
    operation = submitted.operations[0].model_copy(update={"observed_head_event_id": "fabricated"})
    with pytest.raises(DraftApplyError, match="draft_observed_head_mismatch"):
        apply_draft(
            workspace,
            packet,
            submitted.model_copy(update={"operations": (operation,)}),
            store,
            namespace="local-products",
        )
    assert not store.path.exists()


def test_all_new_operations_prevalidate_before_any_append(tmp_path):
    workspace, store, packet = setup(tmp_path, left=catalog(("one", "other")))
    submitted = draft(packet, indexes=(0, 1))
    competing = draft(packet, indexes=(1,), verdict="negative")
    apply_draft(workspace, packet, competing, store, namespace="local-products")
    before = store.path.read_bytes()
    with pytest.raises(DraftApplyError, match="stable_head_conflict"):
        apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert store.path.read_bytes() == before and len(store.replay().events) == 1


def test_actual_mid_batch_race_reports_first_ack_and_remaining_without_rollback(
    tmp_path, monkeypatch
):
    workspace, store, packet = setup(tmp_path, left=catalog(("one", "other")))
    submitted = draft(packet, indexes=(0, 1))
    _, competition = prepare_intents(packet, draft(packet, indexes=(1,), verdict="negative"))
    original_append = JournalStore.append
    once = False

    def racing_append(self, intent, validator):
        nonlocal once
        receipt = original_append(self, intent, validator)
        if not once:
            once = True
            original_append(self, competition[0], validator)
        return receipt

    monkeypatch.setattr(JournalStore, "append", racing_append)
    result = apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert result.issue_code == "journal_write_stopped" and len(result.receipts) == 1
    assert result.remaining_operation_ids == (submitted.operations[1].operation_id,)
    assert len(store.replay().events) == 2
    assert store.replay().events[0].event_id == submitted.operations[0].operation_id


def test_actual_write_then_sync_failure_is_uncertain_and_same_intent_retry_reconciles(
    tmp_path, monkeypatch
):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    original_sync = JournalStore._sync

    def fail_sync(self, fd):
        raise OSError("synthetic fsync failure after actual append")

    monkeypatch.setattr(JournalStore, "_sync", fail_sync)
    result = apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert result.issue_code == "journal_append_uncertain" and result.receipts == ()
    assert result.uncertain_operation_id == submitted.operations[0].operation_id
    assert store.path.read_bytes().endswith(b"\n")
    monkeypatch.setattr(JournalStore, "_sync", original_sync)
    assert len(store.replay().events) == 1
    retry = apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert retry.receipts[0].historical and len(store.replay().events) == 1


def test_conflicting_retry_and_damaged_tail_preserve_existing_bytes(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    apply_draft(workspace, packet, submitted, store, namespace="local-products")
    operation = submitted.operations[0].model_copy(update={"reason": "different intent"})
    changed = submitted.model_copy(update={"operations": (operation,)})
    before = store.path.read_bytes()
    with pytest.raises(DraftApplyError, match="event_id_intent_conflict"):
        apply_draft(workspace, packet, changed, store, namespace="local-products")
    assert store.path.read_bytes() == before
    store.path.write_bytes(before + b'{"torn":')
    with pytest.raises(DraftApplyError, match="journal_read_only"):
        apply_draft(workspace, packet, submitted, store, namespace="local-products")
    assert store.path.read_bytes() == before + b'{"torn":'


def test_unsupported_packet_pair_never_appends(tmp_path):
    workspace, store, packet = setup(tmp_path, left=catalog(("x" * 513,)))
    with pytest.raises(DraftApplyError, match="packet_pair_not_writeable"):
        apply_draft(workspace, packet, draft(packet), store, namespace="local-products")
    assert not store.path.exists()
    with pytest.raises(DraftApplyError, match="actual_owner_journal_required"):
        apply_draft(workspace, packet, draft(packet), object(), namespace="local-products")


def test_empty_skip_draft_creates_no_event_and_actual_validator_is_reused(tmp_path):
    workspace, store, packet = setup(tmp_path)
    empty = draft(packet, indexes=())
    assert apply_draft(workspace, packet, empty, store, namespace="local-products").receipts == ()
    assert not store.path.exists()
    _, intents = prepare_intents(packet, draft(packet))
    validator = ProductGenerationValidator(lambda: workspace.inventory)
    assert validator.validate(intents[0].subject, intents[0].generation_identity) is None


def test_imported_packet_namespace_cannot_select_another_owner_namespace(tmp_path):
    workspace, store, packet = setup(tmp_path)
    with pytest.raises(DraftApplyError, match="trusted_journal_namespace_mismatch"):
        apply_draft(workspace, packet, draft(packet), store, namespace="another-configured-owner")
    assert not store.path.exists()


def test_nested_unchecked_packet_mutation_is_revalidated_before_journal(tmp_path):
    from entity_matcher.pipeline.ruling_packet import RulingPacketError

    workspace, store, packet = setup(tmp_path)
    generation = packet.entries[0].generation.model_copy(update={"profile": "forged"})
    entry = packet.entries[0].model_copy(update={"generation": generation})
    unchecked = packet.model_copy(update={"entries": (entry,)})
    with pytest.raises(RulingPacketError):
        apply_draft(workspace, unchecked, draft(packet), store, namespace="local-products")
    assert not store.path.exists()
