"""Drive the shipped CLI: stage, catalog validation, and a provisional journal apply."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = (
    "id,name,brand,size,unit,price,category,description\n"
    "one,Berry Soap,Orchard,500,mL,2.00,Soap,plain\n"
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, "-m", "entity_matcher", *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def write_catalog(path: Path, record_id: str) -> None:
    path.write_text(CSV.replace("one,", f"{record_id},", 1), encoding="utf-8", newline="\n")


def test_startup_check_uses_the_real_stage_command():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "factory_startup_check.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FACTORY_STARTUP_OK"


def test_validate_accepts_a_synthetic_catalog(tmp_path: Path):
    catalog = tmp_path / "catalog.csv"
    write_catalog(catalog, "one")
    result = run_cli("validate", str(catalog), "--dataset", "local-products")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["stage"] == "validation"
    assert payload["accepted"] is True
    assert payload["accepted_record_count"] == 1
    assert payload["dataset_id"] == "local-products"


def test_packet_and_apply_persist_a_provisional_decision(tmp_path: Path):
    left = tmp_path / "a.csv"
    right = tmp_path / "b.csv"
    journal = tmp_path / "journal"
    journal.mkdir()
    write_catalog(left, "one")
    write_catalog(right, "two")
    packet_run = run_cli(
        "packet",
        str(left),
        str(right),
        "--journal-dir",
        str(journal),
        "--namespace",
        "local-products",
    )
    assert packet_run.returncode == 0, packet_run.stderr
    packet = json.loads(packet_run.stdout)
    assert packet["kind"] == "product_ruling_packet"
    entry = packet["entries"][0]
    assert entry["issue_code"] is None
    draft = {
        "schema_version": 1,
        "kind": "product_ruling_draft",
        "packet_id": packet["packet_id"],
        "operations": [
            {
                "operation_id": str(uuid.uuid4()),
                "pair_key": entry["pair_key"],
                "observed_head_event_id": entry["observed_head_event_id"],
                "action": "decide",
                "verdict": "positive",
                "at_utc": "2026-11-13T00:00:00Z",
                "reason": "same synthetic soap in both catalogs",
            }
        ],
    }
    packet_path = tmp_path / "packet.json"
    draft_path = tmp_path / "draft.json"
    packet_path.write_text(packet_run.stdout, encoding="utf-8")
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    operation_id = draft["operations"][0]["operation_id"]
    before = sorted(path.name for path in journal.iterdir())
    applied = run_cli(
        "apply-rulings",
        str(left),
        str(right),
        "--journal-dir",
        str(journal),
        "--namespace",
        "local-products",
        "--packet",
        str(packet_path),
        "--draft",
        str(draft_path),
    )
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    assert receipt["stage"] == "provisional_ruling_apply"
    assert receipt["status"] == "complete"
    assert receipt["issue_code"] is None
    assert receipt["receipts"][0]["outcome"] == "appended"
    after = sorted(path.name for path in journal.iterdir())
    assert after != before
    replay = run_cli(
        "apply-rulings",
        str(left),
        str(right),
        "--journal-dir",
        str(journal),
        "--namespace",
        "local-products",
        "--packet",
        str(packet_path),
        "--draft",
        str(draft_path),
    )
    assert replay.returncode == 0, replay.stderr
    replayed = json.loads(replay.stdout)
    assert replayed["receipts"][0]["outcome"] == "exact_retry"
    exported = run_cli(
        "export",
        str(left),
        str(right),
        "--journal-dir",
        str(journal),
        "--namespace",
        "local-products",
        "--view",
        "pairs",
    )
    assert exported.returncode == 0, exported.stderr
    assert operation_id in exported.stdout
    assert "agent_provisional_not_human" in exported.stdout
