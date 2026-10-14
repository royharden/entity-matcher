"""Positive controls for the initial publication boundary, not matching accuracy."""

from pathlib import Path

import pytest

from scripts.check_public_tree import scan_files


def test_provider_key_positive_control_is_detected_without_value_in_findings(
    tmp_path: Path,
) -> None:
    """Catches a scanner that returns no hits because its patterns see no files."""
    canary = "sk-" + "synthetic" * 4
    (tmp_path / "config.txt").write_text(canary)
    findings = scan_files(tmp_path, ["config.txt"])
    assert [item.code for item in findings] == ["provider_key"]
    assert canary not in repr(findings)


def test_private_key_positive_control(tmp_path: Path) -> None:
    """Catches missed PEM credentials without exposing a private-key fixture."""
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    (tmp_path / "key.txt").write_text(marker)
    assert [item.code for item in scan_files(tmp_path, ["key.txt"])] == ["private_key"]


def test_credential_assignment_positive_control(tmp_path: Path) -> None:
    """Catches credential assignments that lack an OpenAI-style prefix."""
    (tmp_path / "config.txt").write_text('api_key="' + "synthetic" * 4 + '"')
    assert [item.code for item in scan_files(tmp_path, ["config.txt"])] == ["credential_assignment"]


def test_utf16_key_is_detected(tmp_path: Path) -> None:
    """Catches keys hidden from a scanner that assumes every file is UTF8."""
    (tmp_path / "config.txt").write_text("sk-" + "synthetic" * 4, encoding="utf-16")
    assert [item.code for item in scan_files(tmp_path, ["config.txt"])] == ["provider_key"]


@pytest.mark.parametrize(
    "name",
    [".env", ".ENV", ".Env.local", ".creds/key.txt", ".CREDS/key.txt", ".chatexports/chat.md"],
)
def test_private_path_refused_before_read(tmp_path: Path, name: str) -> None:
    """Catches force-added private files; nonexistent input proves content is not opened."""
    assert [item.code for item in scan_files(tmp_path, [name])] == ["private_runtime_file"]


def test_path_escape_is_not_read(tmp_path: Path) -> None:
    """Catches directory escape without inspecting a sibling's content."""
    assert [item.code for item in scan_files(tmp_path, ["../key.txt"])] == ["path_escape"]


def test_empty_example_is_allowed(tmp_path: Path) -> None:
    """Catches overbroad gates that reject safe empty server-key examples."""
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\nTYPESAFE_API_KEY=\n")
    assert scan_files(tmp_path, [".env.example"]) == []
