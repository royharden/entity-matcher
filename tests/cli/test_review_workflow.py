"""Real CLI file packet/apply/replay/tombstone/CSV flow over temporary microcases."""

import csv
import io
import json
import socket
import subprocess
import sys

import pytest

from entity_matcher.cli.main import main
from entity_matcher.pipeline.ruling_packet import decode_packet
from entity_matcher.shared.rulings.store import JournalStore
from tests.integration.test_ruling_packet import catalog, draft_wire


def files(tmp_path):
    a, b, journal = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "journal"
    a.write_bytes(catalog(("=one",)))
    b.write_bytes(catalog(("two",)))
    journal.mkdir()
    return a, b, journal


def arguments(command, a, b, journal, *more):
    return [
        command,
        str(a),
        str(b),
        "--journal-dir",
        str(journal),
        "--namespace",
        "local-products",
        *map(str, more),
    ]


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-m", "entity_matcher", *args], capture_output=True, timeout=20
    )


def test_real_module_packet_apply_retry_tombstone_export_roundtrip(tmp_path):
    a, b, journal = files(tmp_path)
    originals = a.read_bytes(), b.read_bytes()
    packet_path, draft_path = tmp_path / "packet.json", tmp_path / "draft.json"
    first = invoke(*arguments("packet", a, b, journal))
    assert first.returncode == 0, first.stderr
    packet_path.write_bytes(first.stdout)
    packet = decode_packet(first.stdout)
    wire = draft_wire(packet)
    draft_path.write_text(json.dumps(wire), encoding="utf-8")
    command = arguments(
        "apply-rulings", a, b, journal, "--packet", packet_path, "--draft", draft_path
    )
    appended = invoke(*command)
    assert appended.returncode == 0, appended.stderr
    result = json.loads(appended.stdout)
    assert result["status"] == "complete" and not result["human_admission_available"]
    assert result["receipts"][0]["outcome"] == "appended"
    before = (journal / "rulings.jsonl").read_bytes()
    retried = invoke(*command)
    assert retried.returncode == 0
    assert json.loads(retried.stdout)["receipts"][0]["historical"]
    assert (journal / "rulings.jsonl").read_bytes() == before
    exported = invoke(*arguments("export", a, b, journal))
    assert exported.returncode == 0
    pairs = list(csv.DictReader(io.StringIO(exported.stdout.decode(), newline="")))
    assert len(pairs) == 1 and pairs[0]["human_evidence_checked"] == "'false"
    assert "agent_provisional_not_human" in pairs[0]["issues"]
    components = invoke(*arguments("export", a, b, journal, "--view", "components"))
    rows = list(csv.DictReader(io.StringIO(components.stdout.decode(), newline="")))
    assert len(rows) == 2 and all(row["component_state"] == "'single_record" for row in rows)
    assert any(row["record_id"] == "'=one" for row in rows)
    next_packet = invoke(*arguments("packet", a, b, journal))
    assert next_packet.returncode == 0
    packet = decode_packet(next_packet.stdout)
    packet_path.write_bytes(next_packet.stdout)
    undo = draft_wire(packet)
    undo["operations"][0].pop("verdict")
    undo["operations"][0].update(
        action="retract", target_event_id=wire["operations"][0]["operation_id"]
    )
    draft_path.write_text(json.dumps(undo), encoding="utf-8")
    undone = invoke(*command)
    assert undone.returncode == 0
    snapshot = JournalStore(journal).replay()
    assert len(snapshot.events) == 2 and snapshot.events[1].payload.action == "retract"
    assert all(event.actor_provenance == "agent_provisional" for event in snapshot.events)
    assert (a.read_bytes(), b.read_bytes()) == originals


def test_direct_commands_need_no_network_and_skip_creates_no_journal(tmp_path, capsys, monkeypatch):
    a, b, journal = files(tmp_path)

    def denied(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    assert main(arguments("packet", a, b, journal)) == 0
    encoded = capsys.readouterr().out.encode()
    packet = decode_packet(encoded)
    packet_path, draft_path = tmp_path / "packet.json", tmp_path / "draft.json"
    packet_path.write_bytes(encoded)
    draft_path.write_text(json.dumps(draft_wire(packet, indexes=())), encoding="utf-8")
    assert (
        main(
            arguments(
                "apply-rulings", a, b, journal, "--packet", packet_path, "--draft", draft_path
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["receipts"] == []
    assert not (journal / "rulings.jsonl").exists()


@pytest.mark.parametrize("case", ["stale", "namespace", "human_claim", "malformed"])
def test_invalid_apply_has_named_failure_and_no_journal_bytes(tmp_path, capsys, case):
    a, b, journal = files(tmp_path)
    assert main(arguments("packet", a, b, journal)) == 0
    encoded = capsys.readouterr().out.encode()
    packet = decode_packet(encoded)
    wire = draft_wire(packet)
    packet_path, draft_path = tmp_path / "packet.json", tmp_path / "draft.json"
    packet_path.write_bytes(encoded)
    if case == "stale":
        a.write_bytes(catalog(("=one",), description="changed"))
    if case == "human_claim":
        wire["operations"][0]["actor_provenance"] = "human"
    draft_path.write_text("{}" if case == "malformed" else json.dumps(wire), encoding="utf-8")
    args = arguments("apply-rulings", a, b, journal, "--packet", packet_path, "--draft", draft_path)
    if case == "namespace":
        args[args.index("--namespace") + 1] = "another-owner"
    assert main(args) == 2
    error = json.loads(capsys.readouterr().out)
    assert (
        error["issue_code"]
        == {
            "stale": "packet_source_changed",
            "namespace": "trusted_journal_namespace_mismatch",
            "human_claim": "invalid_ruling_transport",
            "malformed": "invalid_ruling_transport",
        }[case]
    )
    assert not (journal / "rulings.jsonl").exists()
    assert "local-products" not in json.dumps(error)


@pytest.mark.parametrize("name", [".creds", ".thinking", ".chatexports", ".env-review"])
def test_protected_journal_directory_refuses_before_creating_lock(tmp_path, capsys, name):
    a, b, _ = files(tmp_path)
    protected = tmp_path / name
    protected.mkdir()
    assert main(arguments("packet", a, b, protected)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["issue_code"] == "protected_journal_path"
    assert list(protected.iterdir()) == []


def test_missing_root_is_not_automatically_created(tmp_path, capsys):
    a, b, _ = files(tmp_path)
    missing = tmp_path / "absent"
    assert main(arguments("packet", a, b, missing)) == 2
    assert json.loads(capsys.readouterr().out)["issue_code"] == "journal_directory_unavailable"
    assert not missing.exists()


def test_partial_real_journal_race_has_exit3_and_preserves_acknowledged_effect(
    tmp_path, capsys, monkeypatch
):
    from entity_matcher.pipeline.apply_draft import prepare_intents
    from entity_matcher.pipeline.ruling_packet import decode_draft

    a, b, journal = files(tmp_path)
    a.write_bytes(catalog(("one", "other")))
    assert main(arguments("packet", a, b, journal)) == 0
    encoded = capsys.readouterr().out.encode()
    packet = decode_packet(encoded)
    packet_path, draft_path = tmp_path / "packet.json", tmp_path / "draft.json"
    packet_path.write_bytes(encoded)
    submitted = draft_wire(packet, indexes=(0, 1))
    draft_path.write_text(json.dumps(submitted), encoding="utf-8")
    competing = decode_draft(
        json.dumps(draft_wire(packet, indexes=(1,), verdict="negative")).encode()
    )
    _, competition = prepare_intents(packet, competing)
    original = JournalStore.append
    once = False

    def race(self, intent, validator):
        nonlocal once
        result = original(self, intent, validator)
        if not once:
            once = True
            original(self, competition[0], validator)
        return result

    monkeypatch.setattr(JournalStore, "append", race)
    command = arguments(
        "apply-rulings", a, b, journal, "--packet", packet_path, "--draft", draft_path
    )
    assert main(command) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "stopped" and len(result["receipts"]) == 1
    assert result["remaining_operation_ids"] == [submitted["operations"][1]["operation_id"]]
    assert len(JournalStore(journal).replay().events) == 2


def test_damaged_journal_cannot_emit_silent_prefix_csv(tmp_path, capsys):
    a, b, journal = files(tmp_path)
    raw = b'{"torn":'
    (journal / "rulings.jsonl").write_bytes(raw)
    assert main(arguments("export", a, b, journal)) == 2
    assert json.loads(capsys.readouterr().out)["issue_code"] == "journal_read_only"
    assert (journal / "rulings.jsonl").read_bytes() == raw
