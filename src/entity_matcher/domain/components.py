"""Rebuildable, human-evidence-gated components; never consolidate source catalogs."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from ..shared.cited.models import LogicalID
from ..shared.match.identity import canonical_bytes
from ..shared.match.models import PairIdentity, RawProduct, RecordIdentity, RecordRef
from ..shared.rulings.models import PairSubject, RulingEvent
from ..shared.rulings.models import RecordIdentity as CanonicalRulingIdentity
from ..shared.rulings.projection import HumanEvidenceValidator, project
from ..shared.rulings.store import JournalSnapshot, subject_key
from .bindings import CanonicalIdentityUnsupported, ruling_binding


@dataclass(frozen=True)
class UnsupportedRecord:
    identity: RecordIdentity
    capabilities: tuple[str, ...]
    issue_code: str = "canonical_identity_unsupported"


@dataclass(frozen=True)
class PairView:
    pair_key: str
    head_event_id: str
    effective_event_id: str | None
    state: Literal["effective", "needs_review"]
    outcome: Literal["positive", "negative"] | None
    human_evidence_verified: bool
    queue_state: Literal["present", "outside", "unknown"]
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class Component:
    members: tuple[RecordIdentity, ...]
    contested: bool
    internal_negative_pairs: tuple[str, ...]


@dataclass(frozen=True)
class ProductProjection:
    components: tuple[Component, ...]
    pairs: tuple[PairView, ...]
    unsupported_records: tuple[UnsupportedRecord, ...]
    journal_status: str


def project_components(
    snapshot: JournalSnapshot,
    inventory: Mapping[tuple[str, str], RawProduct],
    *,
    namespace: str,
    human_evidence: HumanEvidenceValidator | None = None,
    candidate_pair_keys: frozenset[str] | None = None,
) -> ProductProjection:
    """Project a real journal replay against an explicit current source snapshot.

    Provisional/unknown human attribution forms a review barrier, not a graph edge. Injected
    evidence checks are a capability boundary; a fixture validator is not real authentication.
    Candidate membership is diagnostic only: raw-current human authority survives blocker changes.
    """
    if len(inventory) > 20000 or len(snapshot.events) > 10000:
        raise ValueError("product_projection_bound")
    refs = {}
    unsupported = []
    logical = TypeAdapter(LogicalID, config={"strict": True})
    for key, raw in inventory.items():
        if type(key) is not tuple or len(key) != 2:
            raise ValueError("inventory_identity_invalid")
        dataset, record = key
        raw = RawProduct.model_validate(raw)
        ref = RecordRef.from_raw(dataset, raw)
        if raw.id != record:
            raise ValueError("inventory_source_identity_mismatch")
        refs[key] = ref
        blocked = []
        try:
            CanonicalRulingIdentity.model_validate(ref.identity_fields())
        except ValidationError:
            blocked.append("ruling")
        try:
            logical.validate_python(dataset)
            logical.validate_python(record)
        except ValidationError:
            blocked.append("pointer")
        if blocked:
            unsupported.append(UnsupportedRecord(ref.stable_identity, tuple(blocked)))

    events: tuple[RulingEvent, ...] = tuple(
        event
        for event in snapshot.events
        if event.namespace == namespace and isinstance(event.subject, PairSubject)
    )
    by_id = {event.event_id: event for event in events}
    current, endpoints = {}, {}
    for event in events:
        key = subject_key(namespace, event.subject)
        identities = tuple((ref.dataset_id, ref.record_id) for ref in event.subject.refs)
        endpoints[event.subject.pair_key] = identities
        if all(identity in refs for identity in identities):
            try:
                _, generation = ruling_binding(
                    PairIdentity.from_refs(*(refs[i] for i in identities))
                )
                current[key] = generation
            except CanonicalIdentityUnsupported:
                current[key] = None
        else:
            current[key] = None
    views = project(snapshot, current)
    pairs = []
    positive, negative = [], []
    for view in views.values():
        head = by_id.get(view.head_event_id)
        if head is None:
            continue
        queue: Literal["present", "outside", "unknown"] = (
            "unknown"
            if candidate_pair_keys is None
            else "present"
            if head.subject.pair_key in candidate_pair_keys
            else "outside"
        )
        issues = (view.reason_code,) if view.reason_code else ()
        verified = False
        event = by_id.get(view.effective_event_id)
        if view.state == "effective" and event is not None:
            if event.actor_provenance != "human":
                issues = (*issues, "agent_provisional_not_human")
            elif human_evidence is None or event.actor_provenance_ref is None:
                issues = (*issues, "human_evidence_unverified")
            else:
                try:
                    verified = human_evidence.validate_human(event) is None
                except Exception:
                    verified = False
                if not verified:
                    issues = (*issues, "human_evidence_unverified")
        outcome = view.value if verified and view.value in ("positive", "negative") else None
        if outcome == "positive":
            positive.append(head.subject.pair_key)
        elif outcome == "negative":
            negative.append(head.subject.pair_key)
        pairs.append(
            PairView(
                head.subject.pair_key,
                view.head_event_id,
                view.effective_event_id,
                "effective" if outcome is not None else "needs_review",
                outcome,
                verified,
                queue,
                issues,
            )
        )

    # Rebuild from the effective graph on every call: retraction/deletion can split components.
    parents = {key: key for key in refs}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for pair_key in positive:
        a, b = endpoints[pair_key]
        left, right = root(a), root(b)
        if left != right:
            parents[right] = left
    members = {}
    for key, ref in refs.items():
        members.setdefault(root(key), []).append(ref.stable_identity)
    conflicts = {}
    for pair_key in negative:
        a, b = endpoints[pair_key]
        if root(a) == root(b):
            conflicts.setdefault(root(a), []).append(pair_key)
    components = []
    for leader, identities in members.items():
        ordered = tuple(sorted(identities, key=lambda ref: canonical_bytes(ref.identity_fields())))
        internal = tuple(sorted(conflicts.get(leader, ())))
        components.append(Component(ordered, bool(internal), internal))
    components.sort(key=lambda component: canonical_bytes(component.members[0].identity_fields()))
    pairs.sort(key=lambda pair: pair.pair_key)
    unsupported.sort(key=lambda item: canonical_bytes(item.identity.identity_fields()))
    return ProductProjection(tuple(components), tuple(pairs), tuple(unsupported), snapshot.status)
