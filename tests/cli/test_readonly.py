"""Real bounded CLI flow over temporary microcases; no Gold/user data or write effects."""

import csv
import io
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from entity_matcher.cli.main import main, read_explicit_file
from entity_matcher.shared.match.identity import RAW_FIELDS


def catalog(path: Path, rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(RAW_FIELDS)
    writer.writerows(rows)
    data = stream.getvalue().encode()
    path.write_bytes(data)
    return data


def row(id: str, name: str = "Bottle") -> list[str]:
    return [id, name, "acme", "500", "ml", "1.00", "", ""]


def test_no_args_reports_only_actual_readonly_stage(capsys: pytest.CaptureFixture) -> None:
    """Catches a setup command overclaiming full comparison/ruling/review availability."""
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["read_only_commands"] == ["validate", "candidates", "review-page"]
    assert result["review_page_export_available"]
    assert not result["matching_available"] and not result["human_rulings_available"]


def test_module_entrypoint_returns_actual_stage_json() -> None:
    """Catches the public python -m command diverging from the tested CLI parser."""
    result = subprocess.run(
        [sys.executable, "-m", "entity_matcher"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["status"] == "local-provisional-review-source"
    assert state["provisional_rulings_available"]
    assert state["provisional_commands"] == ["packet", "apply-rulings", "export"]
    assert not state["human_rulings_available"] and not state["interactive_review_available"]


def test_validate_json_summary_keeps_data_private_and_source_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Catches CLI validation exposing source cells or changing its input bytes."""
    path = tmp_path / "a.csv"
    data = catalog(path, [row(" 1 ", "Café Bottle")])
    assert main(["validate", str(path), "--dataset", "catalog-a"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] and result["accepted_record_count"] == 1
    assert "records" not in result and path.read_bytes() == data


def test_invalid_data_exits_nonzero_without_partial_accepted_records(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Catches duplicate rows accepted or validator failure reported as success."""
    path = tmp_path / "a.csv"
    catalog(path, [row("1"), row("1")])
    assert main(["validate", str(path), "--dataset", "a"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["accepted"] and result["accepted_record_count"] == 0
    assert result["issues"][0]["code"] == "duplicate_id"


def test_candidate_inspection_real_core_bounded_preview_no_model_or_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches CLI preview treated as a match, hidden network dispatch, or source mutation."""
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    before_a, before_b = catalog(a, [row("same")]), catalog(b, [row("same"), row("2")])

    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only candidate inspection attempted network")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert main(["candidates", str(a), str(b), "--max-output", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["report"]["retained"] == 2 and result["preview_truncated"]
    assert len(result["candidate_preview"]) == 1
    assert not result["judgment_performed"] and not result["human_rulings_applied"]
    refs = result["candidate_preview"][0]["refs"]
    assert refs[0]["dataset_id"] == "a" and refs[1]["dataset_id"] == "b"
    assert a.read_bytes() == before_a and b.read_bytes() == before_b


@pytest.mark.parametrize(
    "options,code",
    [
        (["--dataset-a", "x", "--dataset-b", "x"], "dataset_namespaces_must_differ"),
        (["--per-a-cap", "51"], "blocking_policy_bound_invalid"),
        (["--global-cap", "0"], "blocking_policy_bound_invalid"),
        (["--max-output", "1001"], "candidate_preview_bound_invalid"),
    ],
)
def test_cli_bounds_reject_before_candidate_run(
    tmp_path: Path, capsys: pytest.CaptureFixture, options: list[str], code: str
) -> None:
    """Catches CLI knobs bypassing deterministic hard caps or namespace separation."""
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    catalog(a, [row("1")])
    catalog(b, [row("1")])
    assert main(["candidates", str(a), str(b), *options]) == 2
    assert json.loads(capsys.readouterr().out)["issue_code"] == code


def test_missing_file_and_invalid_pair_input_do_not_generate_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Catches unreadable input trace leakage and partial success hiding a rejected side."""
    assert main(["validate", str(tmp_path / "missing.csv"), "--dataset", "a"]) == 2
    assert json.loads(capsys.readouterr().out)["issue_code"] == "input_unreadable"
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    catalog(a, [row("1")])
    b.write_bytes(b"wrong,header\n")
    assert main(["candidates", str(a), str(b)]) == 2
    assert "candidate_preview" not in json.loads(capsys.readouterr().out)


def test_explicit_file_bound_and_protected_path_before_read(tmp_path: Path) -> None:
    """Catches direct reads escaping the file fence or accessing secret/agent-only locations."""
    path = tmp_path / "data.csv"
    path.write_bytes(b"123")
    with pytest.raises(ValueError, match="file_too_large"):
        read_explicit_file(path, 2)
    protected = tmp_path / ".creds"
    protected.mkdir()
    forbidden = protected / "empty.csv"
    forbidden.touch()
    with pytest.raises(ValueError, match="protected_input_path"):
        read_explicit_file(forbidden, 20)
