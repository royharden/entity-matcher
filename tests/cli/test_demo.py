"""Actual executable micro-demo; not a genuine-person or match-quality evaluation."""

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

from entity_matcher.shared.rulings.store import JournalStore

ROOT = Path(__file__).resolve().parents[2]


def demo(output):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/demo.py"), "--out", str(output)],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )


def test_actual_demo_normal_retry_tombstone_export_and_stale_refusal(tmp_path):
    output = tmp_path / "demo"
    result = demo(output)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["normal"] == "complete" and report["retry_historical"]
    assert (
        report["refusal"] == "packet_source_changed" and report["refusal_preserved_journal_bytes"]
    )
    assert report["raw_sources_unchanged"] and not report["human_admission"]
    assert report["probability"] is None
    assert [step["exit"] for step in report["steps"]] == [0] * 8 + [2]
    assert json.loads((output / "RESULT.json").read_bytes()) == report
    events = JournalStore(output / "journal").replay().events
    assert len(events) == 2 and events[1].payload.action == "retract"
    assert all(event.actor_provenance == "agent_provisional" for event in events)
    rows = list(csv.DictReader(io.StringIO((output / "components.csv").read_text(), newline="")))
    assert len(rows) == 2 and all(row["component_state"] == "'single_record" for row in rows)


def test_demo_refuses_existing_directory_without_overwriting(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "KEEP.txt"
    sentinel.write_bytes(b"preserve unrelated content")
    result = demo(output)
    assert result.returncode == 2
    assert sentinel.read_bytes() == b"preserve unrelated content"
    assert list(output.iterdir()) == [sentinel]


def test_demo_refuses_protected_target_without_creating_it(tmp_path):
    protected = tmp_path / ".creds" / "demo"
    result = demo(protected)
    assert result.returncode == 2 and not protected.parent.exists()
    assert b".creds" not in result.stdout + result.stderr
