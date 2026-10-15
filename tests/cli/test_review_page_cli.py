"""Actual readonly CLI-to-page controls over authored temporary sources only."""

import csv
import io
import json
import socket
import subprocess
import sys

import pytest

from entity_matcher.cli.main import main
from entity_matcher.pipeline.review_page import ReviewPageError, decode_review_page


def catalog(path, identifier, brand):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    writer.writerow((identifier, "Café Tea", brand, "100", "g", "1", "Drinks", ""))
    path.write_bytes(stream.getvalue().encode())
    return path.read_bytes()


def test_actual_module_emits_utf8_page_without_catalog_or_journal_write(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    before = catalog(a, "one", "Acme"), catalog(b, "two", "Other")
    result = subprocess.run(
        [sys.executable, "-m", "entity_matcher", "review-page", str(a), str(b)],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    page = decode_review_page(result.stdout)
    assert len(page.items) == 1 and page.items[0].records[0].raw.name == "Café Tea"
    assert page.items[0].proposal.judgment.relatedness == 0.0
    assert page.items[0].proposal.proposed_outcome == "review"
    assert page.authority == "machine_proposals_only"
    assert (a.read_bytes(), b.read_bytes()) == before
    assert not list(tmp_path.glob("*.jsonl"))


def test_direct_command_uses_no_network_and_emits_existing_schema(tmp_path, capsys, monkeypatch):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    catalog(a, "one", "Acme"), catalog(b, "two", "Other")

    def denied(*args, **kwargs):
        raise AssertionError("network disabled")

    monkeypatch.setattr(socket, "create_connection", denied)
    assert main(["review-page", str(a), str(b)]) == 0
    page = decode_review_page(capsys.readouterr().out.encode())
    assert page.report.retained == 1 and page.unsupported_records_on_page == 0


@pytest.mark.parametrize(
    "flags,issue",
    [
        (["--limit", "0"], "workspace_page_bound"),
        (["--limit", "101"], "workspace_page_bound"),
        (["--offset", "-1"], "workspace_page_bound"),
        (["--global-cap", "0"], "blocking_policy_bound_invalid"),
        (["--dataset-a", "same", "--dataset-b", "same"], "dataset_namespaces_must_differ"),
    ],
)
def test_bad_flags_reject_before_source_read(tmp_path, capsys, flags, issue):
    assert (
        main(["review-page", str(tmp_path / "missing-a"), str(tmp_path / "missing-b"), *flags]) == 2
    )
    assert json.loads(capsys.readouterr().out)["issue_code"] == issue


def test_protected_input_refuses_before_data_read(tmp_path, capsys):
    protected = tmp_path / ".creds"
    protected.mkdir()
    file = protected / "empty.csv"
    file.touch()
    assert main(["review-page", str(file), str(file)]) == 2
    assert json.loads(capsys.readouterr().out)["issue_code"] == "protected_input_path"


def test_whole_output_error_emits_no_partial_page(tmp_path, capsys, monkeypatch):
    import entity_matcher.cli.main as module

    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    catalog(a, "one", "Acme"), catalog(b, "two", "Other")

    def refuse(*args, **kwargs):
        raise ReviewPageError("review_page_byte_limit")

    monkeypatch.setattr(module, "encode_review_page", refuse)
    assert main(["review-page", str(a), str(b)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["issue_code"] == "review_page_byte_limit" and "items" not in output


def test_uninstalled_method_cannot_silently_fallback(capsys):
    with pytest.raises(SystemExit) as error:
        main(["review-page", "missing-a", "missing-b", "--method", "jev"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
