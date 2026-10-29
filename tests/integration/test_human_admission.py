"""Synthetic host capability controls, never actual person/authentication evidence."""

import json

import pytest

from entity_matcher.domain.components import project_components
from entity_matcher.export.csv_views import component_csv
from entity_matcher.pipeline.apply_draft import (
    DenyHumanAdmission,
    DraftApplyError,
    apply_draft,
    prepare_intents,
)
from entity_matcher.pipeline.ruling_packet import decode_draft, make_ruling_packet
from entity_matcher.pipeline.workspace import create_workspace
from entity_matcher.shared.rulings.identity import intent_bytes
from tests.integration.test_ruling_packet import catalog, draft, draft_wire, setup


class SyntheticHost:
    """Only preapproved exact test intents; no real authentication or production issuer."""

    def __init__(self, packet, submitted):
        _, intents = prepare_intents(packet, submitted)
        self.packet_id = packet.packet_id
        self.pending = {i.event_id: intent_bytes(i) for i in intents}
        self.approved = {
            i.event_id: i.model_copy(
                update={
                    "actor_label": "synthetic-authenticated-subject",
                    "actor_provenance": "human",
                    "actor_provenance_ref": "synthetic-proof:" + i.event_id,
                }
            )
            for i in intents
        }

    def admit_intent(self, *, packet_id, intent):
        if packet_id != self.packet_id or self.pending.get(intent.event_id) != intent_bytes(intent):
            raise ValueError("not approved by synthetic test host")
        return self.approved[intent.event_id]

    def validate_human(self, event):
        approved = self.approved.get(event.event_id)
        if approved is None or intent_bytes(event) != intent_bytes(approved):
            raise ValueError("synthetic evidence absent or mismatched")


def apply(workspace, store, packet, submitted, host):
    return apply_draft(
        workspace, packet, submitted, store, namespace=packet.namespace, human_admission=host
    )


def project(workspace, store, packet, host):
    return project_components(
        store.replay(), workspace.inventory, namespace=packet.namespace, human_evidence=host
    )


def test_synthetic_admission_actual_journal_retry_and_verified_export(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    result = apply(workspace, store, packet, submitted, host)
    assert result.issue_code is None and result.human_admission_available
    before = store.path.read_bytes()
    event = store.replay().events[0]
    assert event.actor_label == "synthetic-authenticated-subject"
    assert event.actor_provenance == "human"
    verified = project(workspace, store, packet, host)
    assert len(verified.components) == 1 and len(verified.components[0].members) == 2
    assert verified.pairs[0].human_evidence_verified
    assert b"human_component" in component_csv(verified).data
    unverified = project(workspace, store, packet, None)
    assert all(len(c.members) == 1 for c in unverified.components)
    assert b"human_component" not in component_csv(unverified).data
    assert apply(workspace, store, packet, submitted, host).receipts[0].historical
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("authority", [None, DenyHumanAdmission()])
def test_absent_authority_never_creates_human_evidence(tmp_path, authority):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    if authority is None:
        assert apply(workspace, store, packet, submitted, authority).issue_code is None
        assert store.replay().events[0].actor_provenance == "agent_provisional"
        assert all(len(c.members) == 1 for c in project(workspace, store, packet, None).components)
    else:
        with pytest.raises(DraftApplyError, match="human_admission_refused"):
            apply(workspace, store, packet, submitted, authority)
        assert not store.path.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"reason": "changed after approval"},
        {"actor_provenance_ref": "forged-proof"},
        {"actor_label": "forged-user"},
        {"actor_provenance": "agent_provisional"},
        {"actor_provenance_ref": None},
        {"context_refs": ("another-packet",)},
    ],
)
def test_altered_host_return_or_forged_evidence_refuses_before_append(tmp_path, change):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    original = host.admit_intent
    host.admit_intent = lambda **kw: original(**kw).model_copy(update=change)
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, submitted, host)
    assert not store.path.exists()


def test_even_host_verified_changed_action_is_not_the_submitted_intent(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    identifier = submitted.operations[0].operation_id
    host.approved[identifier] = host.approved[identifier].model_copy(update={"reason": "other"})
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, submitted, host)
    assert not store.path.exists()


def test_unapproved_user_or_changed_draft_and_false_validator_refuse(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    host.approved.clear()
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, submitted, host)
    host = SyntheticHost(packet, submitted)
    changed = submitted.model_copy(
        update={"operations": (submitted.operations[0].model_copy(update={"reason": "forged"}),)}
    )
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, changed, host)
    host.validate_human = lambda event: False
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, submitted, host)
    assert not store.path.exists()


def test_current_source_and_head_checks_remain_required_with_host(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    changed = create_workspace(catalog(description="changed"), catalog(("two",)))
    with pytest.raises(DraftApplyError, match="packet_source_changed"):
        apply(changed, store, packet, submitted, host)
    competitor = draft(packet, verdict="negative")
    apply_draft(workspace, packet, competitor, store, namespace=packet.namespace)
    before = store.path.read_bytes()
    with pytest.raises(DraftApplyError, match="stable_head_conflict"):
        apply(workspace, store, packet, submitted, host)
    assert store.path.read_bytes() == before


def test_provisional_cannot_overwrite_human_head_and_verified_retract_splits_export(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    apply(workspace, store, packet, submitted, host)
    before = store.path.read_bytes()
    current = make_ruling_packet(workspace, store.replay(), namespace=packet.namespace)
    wire = draft_wire(current)
    wire["operations"][0].pop("verdict")
    wire["operations"][0].update(
        action="retract", target_event_id=submitted.operations[0].operation_id
    )
    retract = decode_draft(json.dumps(wire).encode())
    with pytest.raises(DraftApplyError, match="human_admission_required"):
        apply(workspace, store, current, retract, None)
    assert store.path.read_bytes() == before
    retract_host = SyntheticHost(current, retract)
    assert apply(workspace, store, current, retract, retract_host).issue_code is None
    retracted_bytes = store.path.read_bytes()
    assert apply(workspace, store, packet, submitted, host).receipts[0].historical
    assert store.path.read_bytes() == retracted_bytes
    result = project(workspace, store, packet, host)
    assert all(len(c.members) == 1 for c in result.components)
    assert b"human_component" not in component_csv(result).data


def test_revoked_proof_stale_and_orphan_sources_never_export_approved_cluster(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    apply(workspace, store, packet, submitted, host)
    for changed in (
        create_workspace(catalog(description="changed"), catalog(("two",))),
        create_workspace(catalog(("new-id",)), catalog(("two",))),
    ):
        assert all(len(c.members) == 1 for c in project(changed, store, packet, host).components)
    host.approved.clear()
    assert all(len(c.members) == 1 for c in project(workspace, store, packet, host).components)


def test_revocation_after_prevalidation_is_rechecked_inside_actual_journal_append(tmp_path):
    workspace, store, packet = setup(tmp_path)
    submitted = draft(packet)
    host = SyntheticHost(packet, submitted)
    original = host.validate_human
    calls = 0

    def revoke(event):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ValueError("synthetic revocation before append")
        return original(event)

    host.validate_human = revoke
    result = apply(workspace, store, packet, submitted, host)
    assert result.issue_code == "journal_write_stopped" and not result.receipts
    assert result.remaining_operation_ids == (submitted.operations[0].operation_id,)
    assert store.replay().events == ()


def test_later_unapproved_operation_refuses_entire_batch_before_first_append(tmp_path):
    workspace, store, packet = setup(tmp_path, left=catalog(("one", "other")))
    submitted = draft(packet, indexes=(0, 1))
    host = SyntheticHost(packet, submitted)
    del host.approved[submitted.operations[1].operation_id]
    with pytest.raises(DraftApplyError, match="human_admission_refused"):
        apply(workspace, store, packet, submitted, host)
    assert not store.path.exists()
