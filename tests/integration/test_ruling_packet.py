"""Closed packet/draft controls; imported claims never establish human authority."""

import csv
import io
import json
from pathlib import Path
from uuid import uuid4

import pytest

from entity_matcher.pipeline.ruling_packet import (
    ProductRulingDraft,
    ProductRulingPacket,
    RulingPacketError,
    decode_draft,
    decode_packet,
    encode_draft,
    encode_packet,
    make_ruling_packet,
    packet_digest,
)
from entity_matcher.pipeline.workspace import create_workspace
from entity_matcher.shared.rulings.store import JournalStore


def catalog(ids=("one",), *, description="plain", name="Berry Soap"):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    for identifier in ids:
        writer.writerow((identifier, name, "Orchard", "500", "mL", "2.00", "Soap", description))
    return text.getvalue().encode()


def setup(tmp_path, *, left=None, right=None):
    left = catalog() if left is None else left
    right = catalog(("two",)) if right is None else right
    workspace = create_workspace(left, right)
    store = JournalStore(tmp_path)
    packet = make_ruling_packet(workspace, store.replay(), namespace="local-products")
    return workspace, store, packet


def draft_wire(packet, *, indexes=(0,), verdict="positive"):
    return {
        "schema_version": 1,
        "kind": "product_ruling_draft",
        "packet_id": packet.packet_id,
        "operations": [
            {
                "operation_id": str(uuid4()),
                "pair_key": packet.entries[index].pair_key,
                "observed_head_event_id": packet.entries[index].observed_head_event_id,
                "action": "decide",
                "verdict": verdict,
                "at_utc": "2026-10-15T00:00:00Z",
                "reason": "literal =formula, <script> and\r\nquoted text",
            }
            for index in indexes
        ],
    }


def draft(packet, **options):
    return decode_draft(json.dumps(draft_wire(packet, **options)).encode())


def test_real_packet_and_draft_roundtrip_keeps_raw_and_owner_bindings(tmp_path):
    raw = catalog(description='literal "quoted", line\r\n=SUM(1,2)')
    workspace, store, packet = setup(tmp_path, left=raw)
    assert decode_packet(encode_packet(packet)) == packet
    assert packet.page.items[0].records[0].raw.description == 'literal "quoted", line\r\n=SUM(1,2)'
    assert (
        packet.entries[0].generation.raw_fingerprints == packet.page.items[0].pair.raw_fingerprints
    )
    assert packet.entries[0].observed_head_event_id is None
    assert packet.entries[0].retractable_event_id is None
    proposal = draft(packet)
    assert decode_draft(encode_draft(proposal)) == proposal
    assert proposal.operations[0].reason.startswith("literal =formula")
    assert not store.path.exists() and raw == catalog(
        description='literal "quoted", line\r\n=SUM(1,2)'
    )
    assert workspace.catalogs[0].accepted


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"authority": "human"},
        {"path": "../canary"},
        {"namespace": ""},
        {"packet_id": "rp1:" + "f" * 64},
    ],
)
def test_packet_tampering_rejected(tmp_path, change):
    _, _, packet = setup(tmp_path)
    wire = packet.model_dump(mode="json") | change
    with pytest.raises(RulingPacketError):
        decode_packet(json.dumps(wire).encode())


def test_recomputed_hash_does_not_hide_foreign_pair_binding(tmp_path):
    _, _, packet = setup(tmp_path)
    payload = packet.model_dump(mode="json", exclude={"packet_id"})
    payload["entries"][0]["pair_key"] = "pk1:" + "f" * 64
    wire = payload | {"packet_id": packet_digest(payload)}
    with pytest.raises(RulingPacketError):
        decode_packet(json.dumps(wire).encode())


@pytest.mark.parametrize(
    "change",
    [
        {"actor_provenance": "human"},
        {"actor_provenance_ref": "claimed"},
        {"actor_label": "Roy"},
        {"sequence": 1},
        {"namespace": "elsewhere"},
        {"path": "../CANARY"},
        {"operation_id": "00000000-0000-1000-8000-000000000000"},
        {"operation_id": "FFFFFFFF-FFFF-4FFF-8FFF-FFFFFFFFFFFF"},
        {"at_utc": "2026-10-15T00:00:00"},
        {"at_utc": "2026-10-15T01:00:00+01:00"},
        {"at_utc": "2026-99-15T00:00:00Z"},
        {"at_utc": "2026-10-15 00:00:00Z"},
        {"reason": "é" * 2049},
        {"verdict": "merge"},
        {"target_event_id": "not-a-decide-field"},
    ],
)
def test_closed_operation_rejects_claims_or_invalid_shape(tmp_path, change):
    _, _, packet = setup(tmp_path)
    wire = draft_wire(packet)
    wire["operations"][0].update(change)
    with pytest.raises(RulingPacketError):
        decode_draft(json.dumps(wire).encode())


def test_duplicate_operations_and_excess_count_reject(tmp_path):
    _, _, packet = setup(tmp_path)
    wire = draft_wire(packet)
    wire["operations"] *= 2
    with pytest.raises(RulingPacketError):
        decode_draft(json.dumps(wire).encode())
    wire["operations"] *= 51
    with pytest.raises(RulingPacketError):
        decode_draft(json.dumps(wire).encode())


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"[" * 33 + b"]" * 33,
    ],
)
def test_bounded_strict_json_decoder_rejects_before_transport_acceptance(data):
    for decoder in (decode_packet, decode_draft):
        with pytest.raises(RulingPacketError):
            decoder(data)


def test_input_byte_and_structure_limits_reject(monkeypatch):
    from entity_matcher.pipeline import ruling_packet as module

    monkeypatch.setattr(module, "MAX_PACKET_BYTES", 64)
    with pytest.raises(RulingPacketError, match="input_bound"):
        decode_draft(b" " * 65)
    monkeypatch.setattr(module, "MAX_PACKET_BYTES", 20 * 1024 * 1024)
    with pytest.raises(RulingPacketError):
        decode_draft(b"[" + b"0," * 200001 + b"0]")


def test_unsupported_identity_and_damaged_journal_are_visible_read_only(tmp_path):
    _, store, packet = setup(tmp_path, left=catalog(("é" * 513,)))
    assert packet.entries[0].issue_code == "canonical_identity_unsupported"
    assert packet.entries[0].subject is packet.entries[0].generation is None
    assert not store.path.exists()
    store.path.write_bytes(b'{"torn":')
    workspace = create_workspace(catalog(), catalog(("two",)))
    damaged = make_ruling_packet(workspace, store.replay(), namespace="local-products")
    assert damaged.journal_status == "repair_required"
    assert damaged.entries[0].issue_code == "journal_read_only"
    assert store.path.read_bytes() == b'{"torn":'


def test_schema_files_match_actual_models():
    root = Path(__file__).resolve().parents[2]
    for name, model in (
        ("ruling-packet", ProductRulingPacket),
        ("ruling-draft", ProductRulingDraft),
    ):
        assert (
            json.loads((root / f"schemas/review/{name}.schema.json").read_bytes())
            == model.model_json_schema()
        )
