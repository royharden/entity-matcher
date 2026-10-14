"""Check publishable files without printing matching credential material."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PRIVATE_PARTS = frozenset(
    {
        ".creds",
        ".local",
        ".chatexports",
        ".agent-registry",
        ".agent-work",
        ".thinking",
        ".venv",
        "node_modules",
        "runs",
        "ledgers",
        "data",
    }
)
PATTERNS = (
    ("provider_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*[\"'][A-Za-z0-9_+/-]{20,}[\"']"
        ),
    ),
)
MAX_FILE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    path: str
    code: str


def scan_files(root: Path, paths: list[str]) -> list[Finding]:
    """Fail before reading private/escaped files; findings never include matched values."""
    root = root.resolve()
    findings: list[Finding] = []
    for name in sorted(set(paths)):
        relative = Path(name)
        parts = {part.casefold() for part in relative.parts}
        if relative.is_absolute() or ".." in relative.parts:
            findings.append(Finding(name, "path_escape"))
            continue
        filename = relative.name.casefold()
        if parts & PRIVATE_PARTS or (filename.startswith(".env") and filename != ".env.example"):
            findings.append(Finding(name, "private_runtime_file"))
            continue
        candidate = root / relative
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            findings.append(Finding(name, "path_escape"))
            continue
        if not candidate.is_file():
            findings.append(Finding(name, "missing_file"))
            continue
        if candidate.stat().st_size > MAX_FILE_BYTES:
            findings.append(Finding(name, "scan_size_limit"))
            continue
        content = candidate.read_bytes()
        encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = content.decode(encoding)
        except UnicodeError:
            findings.append(Finding(name, "unscannable_public_file"))
            continue
        for code, pattern in PATTERNS:
            if pattern.search(text):
                findings.append(Finding(name, code))
    return findings


def public_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    paths = public_paths(args.repo)
    if not paths:
        print("Public-tree scan failed: no files selected")
        return 1
    findings = scan_files(args.repo, paths)
    for finding in findings:
        print(f"{finding.path}: {finding.code}")
    print(f"Public-tree scan: {len(paths)} files, {len(findings)} findings")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
