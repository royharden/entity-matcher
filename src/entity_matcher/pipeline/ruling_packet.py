"""Bounded provisional review transport; packet hashes never grant journal authority."""

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ..domain.bindings import CanonicalIdentityUnsupported, ruling_binding
from ..shared.match.identity import canonical_bytes
from ..shared.match.models import FrozenContract
from ..shared.rulings.models import PairDecision, PairGeneration, PairSubject
from ..shared.rulings.store import JournalSnapshot
from .review_page import ProductReviewPage, make_review_page
from .workspace import ComparisonWorkspace

MAX_PACKET_BYTES = 20 * 1024 * 1024
PacketId = Annotated[str, Field(pattern=r"^rp1:[0-9a-f]{64}$")]
PairKey = Annotated[str, Field(pattern=r"^pk1:[0-9a-f]{64}$")]
Opaque = Annotated[str, Field(min_length=1, max_length=512)]
Version = Annotated[int, Field(strict=True, ge=1, le=1)]


class RulingPacketError(ValueError):
    """Closed codes only; parser errors and source text are not user error messages."""


class PacketEntry(FrozenContract):
    pair_key: PairKey
    subject: PairSubject | None
    generation: PairGeneration | None
    observed_head_event_id: Opaque | None
    retractable_event_id: Opaque | None
    issue_code: Literal["canonical_identity_unsupported", "journal_read_only"] | None

    @model_validator(mode="after")
    def branch(self):
        unsupported = self.issue_code == "canonical_identity_unsupported"
        if unsupported != (self.subject is None and self.generation is None):
            raise ValueError("unsupported_pair_branch")
        if not unsupported and (self.subject is None or self.generation is None):
            raise ValueError("incomplete_pair_binding")
        if self.retractable_event_id is not None and (
            self.issue_code is not None or self.retractable_event_id != self.observed_head_event_id
        ):
            raise ValueError("retraction_requires_current_head")
        return self


class ProductRulingPacket(FrozenContract):
    schema_version: Version = 1
    kind: Literal["product_ruling_packet"] = "product_ruling_packet"
    authority: Literal["provisional_review_only"] = "provisional_review_only"
    packet_id: PacketId
    namespace: Opaque
    page: ProductReviewPage
    journal_status: Literal["clean", "repair_required", "limit_exceeded"]
    entries: Annotated[tuple[PacketEntry, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def alignment_and_digest(self):
        if len(self.entries) != len(self.page.items):
            raise ValueError("packet_item_count")
        for item, entry in zip(self.page.items, self.entries, strict=True):
            if entry.pair_key != item.pair.pair_key:
                raise ValueError("packet_pair_order")
            try:
                subject, generation = ruling_binding(item.pair)
            except CanonicalIdentityUnsupported:
                if entry.issue_code != "canonical_identity_unsupported":
                    raise ValueError("unsupported_pair_not_disclosed") from None
            else:
                if (entry.subject, entry.generation) != (subject, generation):
                    raise ValueError("packet_pair_binding")
                expected_issue = None if self.journal_status == "clean" else "journal_read_only"
                if entry.issue_code != expected_issue:
                    raise ValueError("journal_status_branch")
        payload = self.model_dump(mode="json", exclude={"packet_id"})
        if self.packet_id != packet_digest(payload):
            raise ValueError("packet_digest_mismatch")
        return self


class OperationFields(FrozenContract):
    operation_id: Annotated[str, Field(min_length=36, max_length=36)]
    pair_key: PairKey
    observed_head_event_id: Opaque | None
    at_utc: Annotated[str, Field(max_length=40)]
    reason: Annotated[str, Field(max_length=4096)] = ""

    @field_validator("operation_id")
    @classmethod
    def exact_uuid4(cls, value):
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("canonical_uuid4_required")
        return value

    @field_validator("at_utc")
    @classmethod
    def explicit_utc(cls, value):
        if (
            re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value)
            is None
        ):
            raise ValueError("explicit_utc_required")
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
            raise ValueError("explicit_utc_required")
        return value

    @field_validator("reason")
    @classmethod
    def reason_bytes(cls, value):
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("reason_byte_bound")
        return value


class DecideOperation(OperationFields):
    action: Literal["decide"]
    verdict: Literal["positive", "negative", "unsure"]


class RetractOperation(OperationFields):
    action: Literal["retract"]
    target_event_id: Opaque


class ProductRulingDraft(FrozenContract):
    schema_version: Version = 1
    kind: Literal["product_ruling_draft"] = "product_ruling_draft"
    packet_id: PacketId
    operations: Annotated[
        tuple[Annotated[DecideOperation | RetractOperation, Field(discriminator="action")], ...],
        Field(max_length=100),
    ]

    @model_validator(mode="after")
    def unique_operations(self):
        for values in (
            [item.operation_id for item in self.operations],
            [item.pair_key for item in self.operations],
        ):
            if len(set(values)) != len(values):
                raise ValueError("duplicate_operation_or_pair")
        return self


def _bounded_encode(value):
    output = bytearray()
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("utf-8")
        if len(output) + len(encoded) > MAX_PACKET_BYTES:
            raise RulingPacketError("ruling_transport_byte_bound")
        output.extend(encoded)
    return bytes(output)


def packet_digest(payload):
    _bounded_encode(payload)
    return (
        "rp1:"
        + hashlib.sha256(
            canonical_bytes({"domain": "product-ruling-packet-v1", "payload": payload})
        ).hexdigest()
    )


def _decode(data, model):
    if type(data) is not bytes or len(data) > MAX_PACKET_BYTES:
        raise RulingPacketError("ruling_transport_input_bound")
    try:
        text = data.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError("bom")
        depth = separators = 0
        quoted = escaped = False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > 32:
                    raise ValueError("depth")
            elif char in "]}":
                depth -= 1
            elif char in ",:":
                separators += 1
                if separators > 200000:
                    raise ValueError("structure")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = value
            return result

        def nonfinite(value):
            raise ValueError("nonfinite")

        json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
        return model.model_validate_json(data)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise RulingPacketError("invalid_ruling_transport") from None


def decode_packet(data: bytes) -> ProductRulingPacket:
    return _decode(data, ProductRulingPacket)


def decode_draft(data: bytes) -> ProductRulingDraft:
    return _decode(data, ProductRulingDraft)


def encode_packet(packet: ProductRulingPacket) -> bytes:
    return _bounded_encode(
        decode_packet(_bounded_encode(packet.model_dump(mode="json"))).model_dump(mode="json")
    )


def encode_draft(draft: ProductRulingDraft) -> bytes:
    return _bounded_encode(
        decode_draft(_bounded_encode(draft.model_dump(mode="json"))).model_dump(mode="json")
    )


def make_ruling_packet(
    workspace: ComparisonWorkspace,
    snapshot: JournalSnapshot,
    *,
    namespace: str,
    method="rules",
    offset=0,
    limit=50,
) -> ProductRulingPacket:
    if type(snapshot) is not JournalSnapshot:
        raise RulingPacketError("actual_journal_snapshot_required")
    page = make_review_page(workspace, method=method, offset=offset, limit=limit)
    entries = []
    for item in page.items:
        try:
            subject, generation = ruling_binding(item.pair)
        except CanonicalIdentityUnsupported:
            entries.append(
                PacketEntry(
                    pair_key=item.pair.pair_key,
                    subject=None,
                    generation=None,
                    observed_head_event_id=None,
                    retractable_event_id=None,
                    issue_code="canonical_identity_unsupported",
                )
            )
            continue
        head = snapshot.stable_head(namespace, subject)
        can_retract = (
            head is not None
            and isinstance(head.payload, PairDecision)
            and (head.generation_identity == generation and snapshot.can_append)
        )
        entries.append(
            PacketEntry(
                pair_key=item.pair.pair_key,
                subject=subject,
                generation=generation,
                observed_head_event_id=head.event_id if head else None,
                retractable_event_id=head.event_id if can_retract else None,
                issue_code=None if snapshot.can_append else "journal_read_only",
            )
        )
    payload = {
        "schema_version": 1,
        "kind": "product_ruling_packet",
        "authority": "provisional_review_only",
        "namespace": namespace,
        "page": page.model_dump(mode="json"),
        "journal_status": snapshot.status,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    packet = ProductRulingPacket.model_validate_json(
        _bounded_encode(payload | {"packet_id": packet_digest(payload)})
    )
    encode_packet(packet)
    return packet
