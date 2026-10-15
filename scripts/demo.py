"""Run a tiny provisional CLI workflow with tombstone and invalid-input refusals."""

import argparse
import csv
import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from entity_matcher.cli.main import PROTECTED_PARTS, main
from entity_matcher.pipeline.ruling_packet import decode_draft, decode_packet, encode_draft


def catalog(identifier, *, description="Literal demo text"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    writer.writerow((identifier, "Berry Soap", "Orchard", "500", "mL", "2.00", "Soap", description))
    return output.getvalue().encode("utf-8")


def run(output: Path):
    def protected(path):
        return any(
            part.casefold() in PROTECTED_PARTS or part.casefold().startswith(".env")
            for part in path.parts
        )

    if protected(output.absolute()) or protected(output.resolve()):
        raise ValueError("protected_demo_path")
    if output.exists():
        raise ValueError("demo_directory_already_exists")
    output.mkdir(parents=True, exist_ok=False)
    journal = output / "journal"
    journal.mkdir()
    a, b = output / "catalog-A.csv", output / "catalog-B.csv"
    left, right = catalog("soap-a"), catalog("soap-b")
    a.write_bytes(left)
    b.write_bytes(right)
    common = [str(a), str(b), "--journal-dir", str(journal), "--namespace", "local-demo"]
    steps = []

    def cli(command, arguments, filename, expected=0):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main([command, *arguments])
        payload = stream.getvalue().encode("utf-8")
        (output / filename).write_bytes(payload)
        steps.append({"command": command, "artifact": filename, "exit": code})
        if code != expected:
            raise ValueError("demo_step_failed")
        return payload

    def decision(packet, *, retract=False):
        entry = packet.entries[0]
        operation = {
            "operation_id": str(uuid4()),
            "pair_key": entry.pair_key,
            "observed_head_event_id": entry.observed_head_event_id,
            "at_utc": datetime.now(UTC).isoformat(),
            "reason": "Automated synthetic demonstration; not a person or quality label.",
        }
        operation.update(
            {"action": "retract", "target_event_id": entry.retractable_event_id}
            if retract
            else {"action": "decide", "verdict": "positive"}
        )
        return decode_draft(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "product_ruling_draft",
                    "packet_id": packet.packet_id,
                    "operations": [operation],
                }
            ).encode()
        )

    packet_path, draft_path = output / "packet.json", output / "decisions.json"
    packet = decode_packet(cli("packet", common, packet_path.name))
    signal = packet.page.items[0].proposal.judgment
    draft_path.write_bytes(encode_draft(decision(packet)))
    apply = [*common, "--packet", str(packet_path), "--draft", str(draft_path)]
    first = json.loads(cli("apply-rulings", apply, "append-receipt.json"))
    before_retry = (journal / "rulings.jsonl").read_bytes()
    retry = json.loads(cli("apply-rulings", apply, "retry-receipt.json"))
    assert retry["receipts"][0]["historical"]
    assert (journal / "rulings.jsonl").read_bytes() == before_retry
    undo_packet_path, undo_draft_path = output / "undo-packet.json", output / "undo-decisions.json"
    undo_packet = decode_packet(cli("packet", common, undo_packet_path.name))
    undo_draft_path.write_bytes(encode_draft(decision(undo_packet, retract=True)))
    cli(
        "apply-rulings",
        [*common, "--packet", str(undo_packet_path), "--draft", str(undo_draft_path)],
        "retraction-receipt.json",
    )
    after_retraction = (journal / "rulings.jsonl").read_bytes()
    old_retry = json.loads(cli("apply-rulings", apply, "retry-after-retraction.json"))
    assert old_retry["receipts"][0]["historical"]
    assert (journal / "rulings.jsonl").read_bytes() == after_retraction
    final_path = output / "current-packet.json"
    final = decode_packet(cli("packet", common, final_path.name))
    assert final.entries[0].observed_head_event_id is not None
    assert final.entries[0].retractable_event_id is None
    cli("export", common, "pairs.csv")
    cli("export", [*common, "--view", "components"], "components.csv")
    refused_draft = output / "refused-decisions.json"
    refused_draft.write_bytes(encode_draft(decision(final)))
    edited = output / "catalog-A-edited.csv"
    edited.write_bytes(catalog("soap-a", description="Edited after the review packet was made."))
    before_refusal = (journal / "rulings.jsonl").read_bytes()
    refused = json.loads(
        cli(
            "apply-rulings",
            [str(edited), *common[1:], "--packet", str(final_path), "--draft", str(refused_draft)],
            "refused-receipt.json",
            expected=2,
        )
    )
    assert refused["issue_code"] == "packet_source_changed"
    assert (journal / "rulings.jsonl").read_bytes() == before_refusal
    malformed = output / "catalog-A-malformed.csv"
    malformed_bytes = b'id,name,brand,size,unit,price,category,description\n"unterminated'
    malformed.write_bytes(malformed_bytes)
    validation = json.loads(
        cli("validate", [str(malformed), "--dataset", "a"], "malformed-validation.json", expected=2)
    )
    assert not validation["accepted"] and validation["accepted_record_count"] == 0
    assert any(issue["code"] == "malformed_csv" for issue in validation["issues"])
    malformed_refusal = json.loads(
        cli(
            "apply-rulings",
            [
                str(malformed),
                *common[1:],
                "--packet",
                str(final_path),
                "--draft",
                str(refused_draft),
            ],
            "malformed-apply-receipt.json",
            expected=2,
        )
    )
    assert malformed_refusal["issue_code"] == "catalog_validation_failed"
    assert (journal / "rulings.jsonl").read_bytes() == before_refusal
    assert malformed.read_bytes() == malformed_bytes
    assert (a.read_bytes(), b.read_bytes()) == (left, right)
    result = {
        "status": "provisional_demo_complete",
        "steps": steps,
        "input_rows": [1, 1],
        "automated_decisions": True,
        "human_admission": False,
        "normal": first["status"],
        "retry_historical": retry["receipts"][0]["historical"],
        "retry_after_retraction_historical": old_retry["receipts"][0]["historical"],
        "tombstone_preserved_after_retry": True,
        "refusal": refused["issue_code"],
        "refusal_preserved_journal_bytes": True,
        "malformed_refusal": malformed_refusal["issue_code"],
        "malformed_refusal_preserved_journal_bytes": True,
        "raw_sources_unchanged": True,
        "raw_signal_status": signal.status,
        "raw_signal_issues": list(signal.issue_codes),
        "probability": signal.p_same,
        "artifacts": {
            str(path.relative_to(output)): {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "rulings.jsonl.lock"
        },
        "limits": (
            "Tiny authored demonstration only. Every persisted decision is agent_provisional. "
            "No person, accuracy, calibrated probability, model/provider call, browser, "
            "API server or deployment acceptance."
        ),
    }
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="New demo-only directory; existing paths refuse"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.out), sort_keys=True))
    except (OSError, ValueError, AssertionError):
        print(json.dumps({"status": "demo_refused_or_failed", "partial_outputs_preserved": True}))
        raise SystemExit(2) from None
