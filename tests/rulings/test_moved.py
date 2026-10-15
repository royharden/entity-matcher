"""Adapted S2 public store/projection oracles; synthetic attribution only."""

import hashlib
import os
import socket

import pytest

from entity_matcher.shared.rulings.identity import (
    canonical_bytes,
    canonical_pair,
    pair_generation,
    pair_key,
)
from entity_matcher.shared.rulings.models import RulingEvent, RulingIntent
from entity_matcher.shared.rulings.projection import export_labels, project
from entity_matcher.shared.rulings.store import (
    AppendUncertain,
    GenerationConflict,
    IntentConflict,
    JournalError,
    JournalReadOnly,
    JournalStore,
    PriorConflict,
    subject_key,
)


def scalar(
    event_id="event-1",
    *,
    prior=None,
    raw="raw-1",
    evidence="artifact-1",
    item="opaque-item",
    field="label",
    action="correct",
    old=False,
    new=True,
    namespace="fixture",
    actor="agent_provisional",
):
    return RulingIntent.model_validate(
        {
            "event_id": event_id,
            "namespace": namespace,
            "subject": {"kind": "field", "item_id": item, "field_id": field},
            "generation_identity": {
                "kind": "scalar",
                "profile": "fixture/1",
                "source_sha256": "a" * 64,
                "raw_source_revision": raw,
                "evidence_revision": evidence,
            },
            "event_kind": "scalar_decision",
            "payload": {
                "action": action,
                "old_value": old,
                "new_value": new,
                "issue_dispositions": (),
            },
            "expected_source_revision": raw,
            "expected_evidence_revision": evidence,
            "expected_prior_event_id": prior,
            "actor_label": "synthetic-actor",
            "actor_provenance": actor,
            "actor_provenance_ref": "synthetic-not-real-human" if actor == "human" else None,
            "at_utc": "2026-01-01T00:00:00Z",
            "reason": "authored synthetic contract",
            "context_refs": (),
        }
    )


def pair(
    event_id="event-1",
    *,
    prior=None,
    verdict="positive",
    target=None,
    second_hash="b" * 64,
    namespace="fixture",
):
    refs = canonical_pair(
        ({"dataset_id": "left", "record_id": "123"}, {"dataset_id": "right", "record_id": "123"})
    )
    key = pair_key(refs)
    generation = pair_generation(key, ("a" * 64, second_hash))
    values = scalar(event_id, prior=prior, namespace=namespace).model_dump() | {
        "subject": {"kind": "pair", "pair_key": key, "refs": refs},
        "generation_identity": {
            "kind": "pair",
            "profile": "product-raw-fields-v1",
            "pair_generation": generation,
            "raw_fingerprints": ("a" * 64, second_hash),
        },
        "event_kind": "pair_decision",
        "expected_source_revision": generation,
        "expected_evidence_revision": None,
        "payload": {"action": "retract", "target_event_id": target}
        if target
        else {"action": "decide", "verdict": verdict},
    }
    return RulingIntent.model_validate(values)


class CurrentGeneration:
    def __init__(self, generation):
        self.generation = generation
        self.calls = 0

    def validate(self, subject, generation):
        self.calls += 1
        if generation != self.generation:
            raise ValueError("PRIVATE synthetic source mismatch must not leak")


def test_real_append_restart_replay_and_exact_canonical_utf8_lf(tmp_path):
    store = JournalStore(tmp_path)
    intent = scalar(item="café  Ω", field="é")
    receipt = store.append(intent, CurrentGeneration(intent.generation_identity))
    assert receipt.sequence == 1 and receipt.outcome == "appended"
    data = store.path.read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    assert "café  Ω".encode() in data
    restarted = JournalStore(tmp_path).replay()
    assert restarted.status == "clean" and len(restarted.events) == 1
    assert data == canonical_bytes(restarted.events[0].model_dump(mode="json")) + b"\n"


def test_retry_before_stale_head_and_changed_current_generation_is_historical(tmp_path):
    store = JournalStore(tmp_path)
    first = scalar()
    validator = CurrentGeneration(first.generation_identity)
    store.append(first, validator)
    next_intent = scalar("event-2", prior="event-1", raw="raw-2", old=True, new=False)
    validator.generation = next_intent.generation_identity
    store.append(next_intent, validator)
    before = store.path.read_bytes()
    calls = validator.calls
    retry = store.append(first, validator)
    assert (
        retry.sequence,
        retry.outcome,
        retry.historical,
        retry.current_generation_authority,
    ) == (1, "exact_retry", True, False)
    assert validator.calls == calls and store.path.read_bytes() == before
    assert store.replay().stable_head(first.namespace, first.subject).event_id == "event-2"


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "changed"),
        ("actor_label", "other"),
        ("context_refs", ("other",)),
        ("at_utc", "2026-01-02T00:00:00Z"),
        ("expected_prior_event_id", "changed-head"),
    ],
)
def test_event_id_reuse_with_changed_complete_intent_refuses_without_bytes(field, value, tmp_path):
    store = JournalStore(tmp_path)
    original = scalar()
    store.append(original, CurrentGeneration(original.generation_identity))
    before = store.path.read_bytes()
    changed = RulingIntent.model_validate(original.model_dump() | {field: value})
    with pytest.raises(IntentConflict):
        store.append(changed, CurrentGeneration(changed.generation_identity))
    assert store.path.read_bytes() == before


def test_stale_head_source_or_evidence_refuses_before_any_event_bytes(tmp_path):
    store = JournalStore(tmp_path)
    first = scalar()
    current = CurrentGeneration(first.generation_identity)
    store.append(first, current)
    before = store.path.read_bytes()
    with pytest.raises(PriorConflict):
        store.append(scalar("event-2"), current)
    current.generation = scalar(raw="raw-2").generation_identity
    with pytest.raises(GenerationConflict) as error:
        store.append(scalar("event-2", prior="event-1"), current)
    assert str(error.value) == "current_generation_conflict" and "PRIVATE" not in str(error.value)
    current.generation = scalar(evidence="artifact-2").generation_identity
    with pytest.raises(GenerationConflict):
        store.append(scalar("event-2", prior="event-1"), current)
    assert store.path.read_bytes() == before


def test_fsync_failure_has_no_receipt_or_projection_then_unknown_retry_flushes_once(
    tmp_path, monkeypatch
):
    store = JournalStore(tmp_path)
    first = scalar()
    calls = []
    receipt = store._receipt
    monkeypatch.setattr(
        store, "_receipt", lambda *a, **kw: calls.append("receipt") or receipt(*a, **kw)
    )

    def fail(fd):
        raise OSError("synthetic disk fault")

    monkeypatch.setattr(store, "_sync", fail)
    with pytest.raises(AppendUncertain):
        store.append(first, CurrentGeneration(first.generation_identity))
    assert calls == [] and store.path.read_bytes().endswith(b"\n")
    with pytest.raises(AppendUncertain):
        store.replay()
    ordered = []

    def sync(fd):
        os.fsync(fd)
        ordered.append("synced")

    monkeypatch.setattr(store, "_sync", sync)
    monkeypatch.setattr(
        store, "_receipt", lambda *a, **kw: ordered.append("receipt") or receipt(*a, **kw)
    )
    retry = store.append(first, CurrentGeneration(scalar(raw="changed").generation_identity))
    assert ordered == ["synced", "receipt"] and retry.outcome == "exact_retry"
    assert len(store.replay().events) == 1


@pytest.mark.parametrize(
    "tail",
    [b'{"torn":', b"bad JSON\n", b"\n", b"x" * 65537],
    ids=["torn-json", "malformed", "empty-line", "oversized-line"],
)
def test_damaged_tail_prefix_replay_readonly_preserves_all_bytes_and_digest(tmp_path, tail):
    store = JournalStore(tmp_path)
    first = scalar()
    store.append(first, CurrentGeneration(first.generation_identity))
    prefix = store.path.read_bytes()
    store.path.write_bytes(prefix + tail)
    snapshot = store.replay()
    assert snapshot.status == "repair_required" and (not snapshot.can_append)
    assert snapshot.valid_prefix_bytes == len(prefix) and len(snapshot.events) == 1
    assert snapshot.damaged_tail_sha256 == hashlib.sha256(tail).hexdigest()
    assert snapshot.damaged_tail_bytes == len(tail)
    with pytest.raises(JournalReadOnly):
        store.append(
            scalar("event-2", prior="event-1"), CurrentGeneration(first.generation_identity)
        )
    assert store.path.read_bytes() == prefix + tail


def test_duplicate_sequence_or_impossible_historical_predecessor_is_preserved_not_repaired(
    tmp_path,
):
    store = JournalStore(tmp_path)
    first = scalar()
    store.append(first, CurrentGeneration(first.generation_identity))
    prefix = store.path.read_bytes()
    for event in (
        RulingEvent.model_validate(first.model_dump() | {"sequence": 2}),
        RulingEvent.model_validate(scalar("event-2").model_dump() | {"sequence": 2}),
    ):
        bad = canonical_bytes(event.model_dump(mode="json")) + b"\n"
        store.path.write_bytes(prefix + bad)
        assert JournalStore(tmp_path).replay().status == "repair_required"
        assert store.path.read_bytes() == prefix + bad


def test_partial_write_failure_keeps_torn_bytes_and_blocks_future_appends(tmp_path, monkeypatch):
    store = JournalStore(tmp_path)
    first = scalar()
    store.append(first, CurrentGeneration(first.generation_identity))
    real = os.write
    writes = []

    def partial(fd, value):
        if not writes:
            writes.append(1)
            return real(fd, value[:17])
        raise OSError("synthetic partial write")

    monkeypatch.setattr(os, "write", partial)
    with pytest.raises(AppendUncertain):
        store.append(
            scalar("event-2", prior="event-1"), CurrentGeneration(first.generation_identity)
        )
    broken = store.path.read_bytes()
    monkeypatch.setattr(os, "write", real)
    assert store.replay().status == "repair_required"
    assert store.path.read_bytes() == broken


def test_event_cap_before_append_and_journal_bounds_preserve_existing_bytes(tmp_path):
    store = JournalStore(tmp_path, max_events=1)
    first = scalar()
    huge = RulingIntent.model_validate(first.model_dump() | {"reason": "Ω" * 32768})
    with pytest.raises(ValueError):
        store.append(huge, CurrentGeneration(first.generation_identity))
    assert not store.path.exists()
    store.append(first, CurrentGeneration(first.generation_identity))
    before = store.path.read_bytes()
    with pytest.raises(JournalReadOnly):
        store.append(
            scalar("event-2", prior="event-1"), CurrentGeneration(first.generation_identity)
        )
    limited = JournalStore(tmp_path, max_journal_bytes=len(before) - 1).replay()
    assert limited.status == "limit_exceeded" and store.path.read_bytes() == before


def test_imported_sequence_and_reentrant_validation_never_self_deadlock(tmp_path):
    store = JournalStore(tmp_path, lock_deadline_s=0.1)
    intent = scalar()
    event = RulingEvent.model_validate(intent.model_dump() | {"sequence": 99})
    with pytest.raises(ValueError):
        store.append(event, CurrentGeneration(intent.generation_identity))

    class Reentrant:
        def validate(self, subject, generation):
            store.replay()

    with pytest.raises(JournalError, match="reentrant_journal_access"):
        store.append(intent, Reentrant())
    assert not store.path.exists()


def test_namespace_field_and_row_identity_head_separation_and_pair_target_rules(tmp_path):
    store = JournalStore(tmp_path)
    intents = (
        scalar("n1", namespace="a:b", item="c"),
        scalar("n2", namespace="a", item="b:c"),
        pair("p1"),
    )
    for intent in intents:
        store.append(intent, CurrentGeneration(intent.generation_identity))
    snapshot = store.replay()
    assert len({subject_key(i.namespace, i.subject) for i in intents}) == 3
    assert snapshot.stable_head(intents[0].namespace, intents[0].subject).event_id == "n1"
    for target in ("missing", "n1"):
        with pytest.raises(IntentConflict):
            intent = pair("p2", prior="p1", target=target)
            store.append(intent, CurrentGeneration(intent.generation_identity))
    assert len(store.replay().events) == 3


def test_same_client_id_new_generation_or_typed_payload_conflicts_not_retargeted(tmp_path):
    store = JournalStore(tmp_path)
    original = scalar()
    store.append(original, CurrentGeneration(original.generation_identity))
    before = store.path.read_bytes()
    for changed in (scalar(raw="raw-2"), scalar(new=False), scalar(actor="human")):
        with pytest.raises(IntentConflict):
            store.append(changed, CurrentGeneration(changed.generation_identity))
    assert store.path.read_bytes() == before


def test_historical_pair_retraction_wrong_generation_is_invalid_with_no_bytes(tmp_path):
    store = JournalStore(tmp_path)
    first = pair("p1")
    current = pair("p2", prior="p1", second_hash="c" * 64, verdict="negative")
    for intent in (first, current):
        store.append(intent, CurrentGeneration(intent.generation_identity))
    before = store.path.read_bytes()
    retraction = pair("p3", prior="p2", second_hash="c" * 64, target="p1")
    with pytest.raises(IntentConflict):
        store.append(retraction, CurrentGeneration(current.generation_identity))
    assert store.path.read_bytes() == before
    assert store.replay().stable_head(first.namespace, first.subject).event_id == "p2"


def test_noncanonical_and_duplicate_key_packets_never_enter_valid_prefix(tmp_path):
    store = JournalStore(tmp_path)
    intent = scalar()
    event = RulingEvent.model_validate(intent.model_dump() | {"sequence": 1})
    valid = canonical_bytes(event.model_dump(mode="json"))
    packets = (b" " + valid + b"\n", b'{"sequence":999,' + valid[1:] + b"\n")
    for packet in packets:
        store.path.write_bytes(packet)
        snapshot = store.replay()
        assert snapshot.status == "repair_required" and snapshot.events == ()
        assert store.path.read_bytes() == packet


def append(store, intent):
    store.append(intent, CurrentGeneration(intent.generation_identity))


def test_scalar_correct_confirm_reopen_never_revives_prior_value(tmp_path):
    store = JournalStore(tmp_path)
    intents = (
        scalar(new=False),
        scalar("event-2", prior="event-1", action="confirm", old=False, new=False),
        scalar("event-3", prior="event-2", action="reopen", old=False, new=None),
    )
    for intent in intents:
        append(store, intent)
    key = subject_key(intents[0].namespace, intents[0].subject)
    view = project(store.replay(), {key: intents[0].generation_identity})[key]
    assert view.state == "needs_review" and view.value is None and (view.effective_event_id is None)
    assert view.head_event_id == "event-3" and view.reason_code == "reopened"


def test_scalar_false_null_and_actual_human_evidence_gate_are_separate(tmp_path):
    store = JournalStore(tmp_path)
    first = scalar(new=False, actor="human")
    append(store, first)
    key = subject_key(first.namespace, first.subject)
    snapshot = store.replay()
    current = {key: first.generation_identity}
    unverified = export_labels(snapshot, current)[0]
    assert unverified.value is False and unverified.presence == "present"
    assert unverified.label_source == "unverified_human"

    class FixtureEvidence:
        def validate_human(self, event):
            assert event.actor_provenance_ref == "synthetic-not-real-human"

    assert (
        export_labels(snapshot, current, human_evidence=FixtureEvidence())[0].label_source
        == "human"
    )
    absent = scalar("event-2", prior="event-1", action="waive_absent", old=False, new=None)
    append(store, absent)
    label = export_labels(store.replay(), current)[0]
    assert (
        label.presence == "absent"
        and label.value is None
        and (label.label_source == "agent_provisional")
    )


def test_raw_evidence_change_or_tombstone_preserves_stable_head_but_no_old_correction(tmp_path):
    store = JournalStore(tmp_path)
    first = scalar()
    append(store, first)
    key = subject_key(first.namespace, first.subject)
    for current in (
        scalar(raw="raw-2").generation_identity,
        scalar(evidence="artifact-2").generation_identity,
        None,
    ):
        view = project(store.replay(), {key: current})[key]
        assert (
            view.state == "needs_review"
            and view.effective_event_id is None
            and (view.head_event_id == "event-1")
        )
    newer = scalar("event-2", prior="event-1", raw="raw-2", new=False)
    append(store, newer)
    view = project(store.replay(), {key: newer.generation_identity})[key]
    assert view.value is False and view.effective_event_id == "event-2"


def test_effective_pair_retraction_barrier_and_non_effective_history_annotation(tmp_path):
    store = JournalStore(tmp_path)
    positive = pair("p1")
    negative = pair("p2", prior="p1", verdict="negative")
    retract = pair("p3", prior="p2", target="p2")
    for intent in (positive, negative, retract):
        append(store, intent)
    key = subject_key(positive.namespace, positive.subject)
    current = {key: positive.generation_identity}
    view = project(store.replay(), current)[key]
    assert view.state == "needs_review" and view.value is None and (view.effective_event_id is None)
    newer = pair("p4", prior="p3", verdict="negative")
    history = pair("p5", prior="p4", target="p1")
    for intent in (newer, history):
        append(store, intent)
    view = project(store.replay(), current)[key]
    assert (
        view.value == "negative"
        and view.effective_event_id == "p4"
        and (view.head_event_id == "p5")
    )
    assert export_labels(store.replay(), current) == ()


def test_dirty_prefix_or_agent_provenance_never_becomes_real_human_gold(tmp_path):
    store = JournalStore(tmp_path)
    first = scalar()
    append(store, first)
    key = subject_key(first.namespace, first.subject)
    current = {key: first.generation_identity}
    assert export_labels(store.replay(), current)[0].label_source == "agent_provisional"
    store.path.write_bytes(store.path.read_bytes() + b"torn")
    label = export_labels(store.replay(), current)[0]
    assert label.presence == "unlabelled" and label.label_source == "unlabelled"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):

    def denied(*args, **kwargs):
        raise AssertionError("Network is disabled for journal tests")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_copied_schema_matches_actual_namespace():
    import json
    from pathlib import Path

    from entity_matcher.shared.rulings import models

    artifact = json.loads(Path(models.__file__).with_name("schema.json").read_text())
    assert artifact["schemas"] == {
        model.__name__: model.model_json_schema() for model in models.WIRE_MODELS
    }


def test_detached_pair_journal_real_io_and_owner_network_canaries(tmp_path):
    import json
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from entity_matcher.shared.rulings import models

    shutil.copytree(
        Path(models.__file__).parent,
        tmp_path / "moved_rulings",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    intent = pair()
    (tmp_path / "intent.json").write_text(intent.model_dump_json(), encoding="utf-8")
    (tmp_path / "private").mkdir()
    script = """
import importlib, importlib.abc, json, socket, sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.split('.')[0] in {'rfr','entity_matcher','openai','fastapi'}:
            raise RuntimeError('original owner/application disabled')
sys.meta_path.insert(0,Guard())
def denied(*a,**kw): raise RuntimeError('network disabled')
socket.create_connection=denied
socket.socket.connect=denied
from moved_rulings.models import RulingIntent
from moved_rulings.store import JournalStore,GenerationConflict,subject_key
from moved_rulings.projection import project
intent=RulingIntent.model_validate_json(Path('intent.json').read_bytes())
class Current:
    def validate(self,subject,generation): assert generation==intent.generation_identity
store=JournalStore(Path('private'))
receipt=store.append(intent,Current())
snapshot=JournalStore(Path('private')).replay()
key=subject_key(intent.namespace,intent.subject)
assert project(snapshot,{key:intent.generation_identity})[key].value=='positive'
assert project(snapshot,{key:None})[key].state=='needs_review'
assert store.append(intent,Current()).outcome=='exact_retry'
class Stale:
    def validate(self,*a): raise ValueError('private underlying error')
changed=RulingIntent.model_validate(intent.model_dump()|{
 'event_id':'next','expected_prior_event_id':intent.event_id})
before=store.path.read_bytes()
try: store.append(changed,Stale())
except GenerationConflict: pass
else: raise AssertionError('stale generation accepted')
assert before==store.path.read_bytes()
for name in ('rfr','entity_matcher','openai'):
    try: importlib.import_module(name)
    except RuntimeError: pass
    else: raise AssertionError('original import canary did not fail')
try: socket.create_connection(('example.invalid',443))
except RuntimeError: pass
else: raise AssertionError('network canary did not fail')
assert not any(n=='rfr' or n.startswith('rfr.') for n in sys.modules)
print(json.dumps({'events':len(snapshot.events),'platform':sys.platform,
 'owner_imports':'blocked','network':'blocked','io':'actual temp journal'}))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    run = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    receipt = json.loads(run.stdout)
    assert receipt["events"] == 1
    assert receipt["owner_imports"] == receipt["network"] == "blocked"
