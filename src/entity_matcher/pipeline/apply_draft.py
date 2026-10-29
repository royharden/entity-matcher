"""Local controller with optional trusted-host admission; no built-in human issuer."""

from dataclasses import dataclass
from typing import Protocol

from ..domain.generation import ProductGenerationValidator
from ..shared.rulings.identity import intent_bytes
from ..shared.rulings.models import AppendReceipt, PairDecision, RulingIntent
from ..shared.rulings.store import AppendUncertain, JournalError, JournalStore
from .review_page import check_page_sources
from .ruling_packet import (
    DecideOperation,
    ProductRulingDraft,
    ProductRulingPacket,
    RetractOperation,
    decode_draft,
    decode_packet,
    encode_draft,
    encode_packet,
)
from .workspace import ComparisonWorkspace


class DraftApplyError(ValueError):
    """A prevalidation failure has no append effects from this call."""


class HumanAdmissionIssuer(Protocol):
    """Trusted host capability, never constructed from imported draft/request fields.

    Admission must authenticate the reviewer and bind their approval to the complete intent.
    Only actor attribution may change. Verification must check that complete final intent
    against retained evidence, also on replay/export. Repeated admission of the same intent
    must retain the same attribution so exact journal retries stay idempotent.
    """

    def admit_intent(self, *, packet_id: str, intent: RulingIntent) -> RulingIntent: ...

    def validate_human(self, event: RulingIntent) -> None: ...


class DenyHumanAdmission:
    def issue_evidence(self, *, packet_id: str, operation_id: str) -> str:
        raise DraftApplyError("human_admission_unavailable")

    def admit_intent(self, *, packet_id: str, intent: RulingIntent) -> RulingIntent:
        raise DraftApplyError("human_admission_unavailable")

    def validate_human(self, event: RulingIntent) -> None:
        raise DraftApplyError("human_admission_unavailable")


@dataclass(frozen=True)
class ApplyResult:
    receipts: tuple[AppendReceipt, ...]
    remaining_operation_ids: tuple[str, ...]
    issue_code: str | None
    uncertain_operation_id: str | None = None
    human_admission_available: bool = False


def prepare_intents(packet: ProductRulingPacket, draft: ProductRulingDraft):
    # JSON revalidation rejects unchecked nested model_copy mutations as well as imported wires.
    packet, draft = decode_packet(encode_packet(packet)), decode_draft(encode_draft(draft))
    if draft.packet_id != packet.packet_id:
        raise DraftApplyError("draft_packet_mismatch")
    entries = {entry.pair_key: entry for entry in packet.entries}
    intents = []
    for operation in draft.operations:
        entry = entries.get(operation.pair_key)
        if entry is None or entry.issue_code is not None or packet.journal_status != "clean":
            raise DraftApplyError("packet_pair_not_writeable")
        if operation.observed_head_event_id != entry.observed_head_event_id:
            raise DraftApplyError("draft_observed_head_mismatch")
        if isinstance(operation, RetractOperation) and (
            operation.target_event_id != entry.retractable_event_id
        ):
            raise DraftApplyError("packet_retraction_not_available")
        payload = (
            {"action": "decide", "verdict": operation.verdict}
            if isinstance(operation, DecideOperation)
            else {"action": "retract", "target_event_id": operation.target_event_id}
        )
        intents.append(
            RulingIntent(
                event_id=operation.operation_id,
                namespace=packet.namespace,
                subject=entry.subject,
                generation_identity=entry.generation,
                event_kind="pair_decision",
                payload=payload,
                expected_source_revision=entry.generation.pair_generation,
                expected_evidence_revision=None,
                expected_prior_event_id=entry.observed_head_event_id,
                actor_label="local-file-review",
                actor_provenance="agent_provisional",
                actor_provenance_ref=None,
                at_utc=operation.at_utc,
                reason=operation.reason,
                context_refs=(packet.packet_id,),
            )
        )
    return packet, tuple(intents)


def apply_draft(
    workspace: ComparisonWorkspace,
    packet: ProductRulingPacket,
    draft: ProductRulingDraft,
    store: JournalStore,
    *,
    namespace: str,
    human_admission: HumanAdmissionIssuer | None = None,
) -> ApplyResult:
    if type(store) is not JournalStore:
        raise DraftApplyError("actual_owner_journal_required")
    packet, intents = prepare_intents(packet, draft)
    if type(namespace) is not str or packet.namespace != namespace:
        raise DraftApplyError("trusted_journal_namespace_mismatch")
    if human_admission is not None:
        intents = tuple(_admit_intent(human_admission, packet.packet_id, i) for i in intents)
    snapshot = store.replay()
    if not snapshot.can_append:
        raise DraftApplyError("journal_read_only")
    by_id = {event.event_id: event for event in snapshot.events}
    validator = ProductGenerationValidator(lambda: workspace.inventory)
    for intent in intents:
        existing = by_id.get(intent.event_id)
        if existing is not None:
            if intent_bytes(existing) != intent_bytes(intent):
                raise DraftApplyError("event_id_intent_conflict")
            # Owner exact retries are historical and do not regrant current-source authority.
            continue
        if not check_page_sources(packet.page, workspace).current:
            raise DraftApplyError("packet_source_changed")
        head = snapshot.stable_head(packet.namespace, intent.subject)
        if head is not None and head.actor_provenance == "human" and human_admission is None:
            raise DraftApplyError("human_admission_required")
        if intent.expected_prior_event_id != (head.event_id if head else None):
            raise DraftApplyError("stable_head_conflict")
        try:
            validator.validate(intent.subject, intent.generation_identity)
        except ValueError:
            raise DraftApplyError("current_generation_conflict") from None
        if isinstance(intent.payload, PairDecision):
            continue
        target = by_id.get(intent.payload.target_event_id)
        if (
            target is None
            or head != target
            or not isinstance(target.payload, PairDecision)
            or (target.generation_identity != intent.generation_identity)
        ):
            raise DraftApplyError("invalid_retraction_target")
    receipts = []
    for index, intent in enumerate(intents):
        try:
            append_validator = (
                _HumanGenerationValidator(validator, human_admission, intent)
                if human_admission is not None
                else validator
            )
            receipts.append(store.append(intent, append_validator))
        except AppendUncertain:
            return ApplyResult(
                tuple(receipts),
                tuple(i.event_id for i in intents[index:]),
                "journal_append_uncertain",
                intent.event_id,
                human_admission_available=human_admission is not None,
            )
        except (JournalError, ValueError):
            return ApplyResult(
                tuple(receipts),
                tuple(i.event_id for i in intents[index:]),
                "journal_write_stopped",
                human_admission_available=human_admission is not None,
            )
    return ApplyResult(
        tuple(receipts), (), None, human_admission_available=human_admission is not None
    )


def _admit_intent(
    authority: HumanAdmissionIssuer, packet_id: str, provisional: RulingIntent
) -> RulingIntent:
    try:
        attribution = {"actor_label", "actor_provenance", "actor_provenance_ref"}
        expected = provisional.model_dump(exclude=attribution)
        admitted = authority.admit_intent(packet_id=packet_id, intent=provisional)
        # Revalidate unchecked model_copy mutations and reject noncanonical return objects.
        if type(admitted) is not RulingIntent:
            raise ValueError("canonical intent required")
        admitted = RulingIntent.model_validate_json(admitted.model_dump_json())
        if (
            admitted.actor_provenance != "human"
            or admitted.model_dump(exclude=attribution) != expected
            or authority.validate_human(admitted) is not None
        ):
            raise ValueError("unverified or altered human intent")
        return admitted
    except Exception:
        # Host errors/evidence contents must not leak through the public controller surface.
        raise DraftApplyError("human_admission_refused") from None


@dataclass(frozen=True)
class _HumanGenerationValidator:
    source: ProductGenerationValidator
    authority: HumanAdmissionIssuer
    intent: RulingIntent

    def validate(self, subject, generation) -> None:
        self.source.validate(subject, generation)
        # The journal invokes this under its append lock, after its current-head check.
        # A host revocation between batch admission and append must still refuse the write.
        if self.authority.validate_human(self.intent) is not None:
            raise DraftApplyError("human_admission_refused")
